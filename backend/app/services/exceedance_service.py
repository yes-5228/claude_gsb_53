"""超标记录查询与人工标注."""
import uuid
from datetime import datetime

from sqlalchemy import cast, func, or_
from sqlalchemy.exc import IntegrityError

from ..domain.constants import EXCEEDANCE_LEVEL_LABELS, EXCEEDANCE_STATUS_LABELS
from ..errors import NotFoundError, ValidationError
from ..extensions import db
from ..models import AnnotationBatch, Exceedance, Measurement, Station
from ..models.base import iso

STATUS_CHOICES = tuple(EXCEEDANCE_STATUS_LABELS.keys())
LEVEL_CHOICES = tuple(EXCEEDANCE_LEVEL_LABELS.keys())
BATCH_MODES = ("atomic", "partial")


def _split(value):
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _int_list(args, name):
    values = []
    for item in _split(args.get(name)):
        try:
            values.append(int(item))
        except ValueError:
            raise ValidationError("%s 参数必须为整数" % name, fields={name: "invalid_integer"})
    return values


def _date_arg(args, name, end_of_day=False):
    from datetime import time

    from ..utils.validation import parse_date

    raw = args.get(name)
    if raw in (None, ""):
        return None
    parsed = parse_date(raw, name)
    return datetime.combine(parsed, time.max if end_of_day else time.min)


def get_exceedance(exceedance_id):
    exceedance = db.session.get(Exceedance, exceedance_id)
    if exceedance is None:
        raise NotFoundError("超标记录不存在: id=%s" % exceedance_id)
    return exceedance


def exceedance_query(args):
    query = db.session.query(Exceedance).join(Station, Exceedance.station_id == Station.id)

    statuses = _split(args.get("status"))
    if statuses:
        query = query.filter(Exceedance.status.in_(statuses))
    levels = _split(args.get("level"))
    if levels:
        query = query.filter(Exceedance.level.in_(levels))
    pollutants = _split(args.get("pollutant"))
    if pollutants:
        query = query.filter(Exceedance.pollutant.in_([item.upper() for item in pollutants]))
    station_ids = _int_list(args, "station_id")
    if station_ids:
        query = query.filter(Exceedance.station_id.in_(station_ids))
    areas = _split(args.get("area"))
    if areas:
        query = query.filter(Station.area.in_(areas))
    keyword = (args.get("keyword") or "").strip()
    if keyword:
        like = "%" + keyword + "%"
        query = query.filter(
            or_(Station.name.like(like), Station.code.like(like), Exceedance.note.like(like))
        )
    date_from = _date_arg(args, "date_from")
    if date_from:
        query = query.filter(Exceedance.measured_at >= date_from)
    date_to = _date_arg(args, "date_to", end_of_day=True)
    if date_to:
        query = query.filter(Exceedance.measured_at <= date_to)
    min_ratio = args.get("min_ratio")
    if min_ratio not in (None, ""):
        try:
            query = query.filter(Exceedance.exceed_ratio >= float(min_ratio))
        except ValueError:
            raise ValidationError("min_ratio 必须为数字", fields={"min_ratio": "invalid_number"})
    if str(args.get("annotated", "")).strip().lower() in {"1", "true", "yes"}:
        query = query.filter(Exceedance.annotated_at.isnot(None))
    elif str(args.get("annotated", "")).strip().lower() in {"0", "false", "no"}:
        query = query.filter(Exceedance.annotated_at.is_(None))

    order = (args.get("order") or "desc").lower()
    sort_key = args.get("sort") or "measured_at"
    column = {
        "measured_at": Exceedance.measured_at,
        "exceed_ratio": Exceedance.exceed_ratio,
        "level": Exceedance.level,
        "updated_at": Exceedance.updated_at,
    }.get(sort_key, Exceedance.measured_at)
    primary = column.desc() if order == "desc" else column.asc()
    return query.order_by(primary, Exceedance.id.desc())


def annotate(exceedance, status=None, note=None, annotator=None, level=None):
    """Apply a manual annotation to an exceedance record."""
    if status is not None:
        if status not in STATUS_CHOICES:
            raise ValidationError(
                "标注状态取值不合法, 可选: %s" % ", ".join(STATUS_CHOICES),
                fields={"status": "unknown"},
            )
        exceedance.status = status
    if level is not None:
        if level not in LEVEL_CHOICES:
            raise ValidationError(
                "超标等级取值不合法, 可选: %s" % ", ".join(LEVEL_CHOICES),
                fields={"level": "unknown"},
            )
        exceedance.level = level

    note = (note or "").strip()
    if exceedance.status == "pending":
        exceedance.note = note or exceedance.note
        exceedance.annotator = annotator or exceedance.annotator
        exceedance.annotated_at = None if not note else datetime.now()
    else:
        if not note:
            reason = "确认" if exceedance.status == "confirmed" else "忽略"
            raise ValidationError(
                "标注为\"%s\"时必须填写%s原因" % (EXCEEDANCE_STATUS_LABELS[exceedance.status], reason),
                fields={"note": "required"},
            )
        exceedance.note = note
        exceedance.annotator = annotator or "未署名"
        exceedance.annotated_at = datetime.now()

    db.session.commit()
    return exceedance


def _snapshot(record):
    """Annotation-relevant fields, used for before/after comparison."""
    return {
        "status": record.status,
        "level": record.level,
        "note": record.note,
        "annotator": record.annotator,
        "annotated_at": iso(record.annotated_at),
    }


def _validate_batch_payload(status, note, annotator):
    """Every batch operation must record operator; confirm/ignore need a note."""
    if not (annotator or "").strip():
        raise ValidationError(
            "批量操作必须填写标注人, 便于追溯", fields={"annotator": "required"}
        )
    if status != "pending" and not (note or "").strip():
        raise ValidationError(
            "批量标注为\"%s\"时必须填写标注说明"
            % EXCEEDANCE_STATUS_LABELS.get(status, status),
            fields={"note": "required"},
        )


def annotate_batch(ids, status, note=None, annotator=None, level=None,
                   mode="atomic", request_id=None):
    """Batch annotation used by the exceedance work bench.

    - ``mode="atomic"``: any record that cannot be processed aborts the whole
      batch (nothing is written) and the error explains which ids failed.
    - ``mode="partial"``: process what can be processed; the response carries
      per-item results with reasons for every skipped record.
    - ``request_id``: idempotency key. Resubmitting the same key returns the
      first stored result (``deduplicated=True``) without touching the records.
    """
    if mode not in BATCH_MODES:
        raise ValidationError(
            "批量模式取值不合法, 可选: %s" % ", ".join(BATCH_MODES),
            fields={"mode": "unknown"},
        )

    request_id = (request_id or "").strip() or uuid.uuid4().hex
    existing = AnnotationBatch.query.filter_by(request_id=request_id).first()
    if existing is not None:
        result = existing.to_dict()
        result["deduplicated"] = True
        return result

    _validate_batch_payload(status, note, annotator)

    ids = list(dict.fromkeys(int(item) for item in ids))
    if not ids:
        raise ValidationError("请至少选择一条超标记录", fields={"ids": "empty"})

    records = Exceedance.query.filter(Exceedance.id.in_(ids)).all()
    found = {record.id for record in records}
    missing = [item for item in ids if item not in found]

    if missing and mode == "atomic":
        raise ValidationError(
            "有 %d 条记录不存在 (%s), 整批未生效, 请刷新后重新勾选"
            % (len(missing), ", ".join(str(item) for item in missing[:10])),
            fields={"ids": "missing", "missing": missing},
        )

    note = (note or "").strip()
    annotator = annotator.strip()
    now = datetime.now()

    items = []
    updated_ids = []
    for record in records:
        before = _snapshot(record)
        record.status = status
        if level is not None:
            record.level = level
        if note:
            record.note = note
        if status == "pending":
            record.annotated_at = None
        else:
            record.annotator = annotator
            record.annotated_at = now
        after = _snapshot(record)
        items.append({
            "id": record.id,
            "result": "updated",
            "changed": before != after,
            "reannotated": before["annotated_at"] is not None,
            "before": before,
            "after": after,
            "station_name": record.station.name if record.station else None,
            "pollutant": record.pollutant,
            "measured_at": iso(record.measured_at),
        })
        updated_ids.append(record.id)
    for item in missing:
        items.append({"id": item, "result": "missing", "reason": "记录不存在或已被删除"})

    result = {
        "request_id": request_id,
        "mode": mode,
        "deduplicated": False,
        "status": status,
        "level": level,
        "note": note or None,
        "annotator": annotator,
        "requested": len(ids),
        "updated": len(updated_ids),
        "updated_ids": updated_ids,
        "missing": missing,
        "failed": [{"id": item, "reason": "记录不存在或已被删除"} for item in missing],
        "items": items,
        "processed_at": iso(now),
    }

    batch = AnnotationBatch(
        request_id=request_id,
        mode=mode,
        status=status,
        level=level,
        note=note or None,
        annotator=annotator,
        requested=len(ids),
        updated=len(updated_ids),
        result=result,
    )
    db.session.add(batch)
    try:
        db.session.commit()
    except IntegrityError:
        # Concurrent duplicate submission: the first one won, replay its result.
        db.session.rollback()
        winner = AnnotationBatch.query.filter_by(request_id=request_id).first()
        if winner is None:
            raise
        result = winner.to_dict()
        result["deduplicated"] = True
        return result
    return result


def get_batch(request_id):
    batch = AnnotationBatch.query.filter_by(request_id=(request_id or "").strip()).first()
    if batch is None:
        raise NotFoundError("批量操作记录不存在: request_id=%s" % request_id)
    return batch.to_dict()


def matching_ids(args):
    """All exceedance ids matching the current filters (for cross-page select all)."""
    from flask import current_app

    limit = current_app.config["MAX_BATCH_SIZE"]
    query = exceedance_query(args).with_entities(Exceedance.id)
    total = query.count()
    ids = [row[0] for row in query.limit(limit).all()]
    return {"ids": ids, "total": total, "truncated": total > limit}


def summary(args):
    """Dashboard counters for the annotation work bench."""
    base = exceedance_query(args)
    subquery = base.with_entities(Exceedance.id, Exceedance.station_id,
                                  Exceedance.status, Exceedance.level,
                                  Exceedance.pollutant, Exceedance.exceed_ratio).subquery()

    by_status = {
        status: {"key": status, "label": label, "count": 0}
        for status, label in EXCEEDANCE_STATUS_LABELS.items()
    }
    for status, count in (
        db.session.query(subquery.c.status, func.count()).group_by(subquery.c.status).all()
    ):
        if status in by_status:
            by_status[status]["count"] = int(count)

    by_level = {
        level: {"key": level, "label": label, "count": 0}
        for level, label in EXCEEDANCE_LEVEL_LABELS.items()
    }
    for level, count in (
        db.session.query(subquery.c.level, func.count()).group_by(subquery.c.level).all()
    ):
        if level in by_level:
            by_level[level]["count"] = int(count)

    top_pollutants = [
        {"key": pollutant, "count": int(count), "avg_ratio": round(float(avg_ratio or 0), 3)}
        for pollutant, count, avg_ratio in (
            db.session.query(
                subquery.c.pollutant,
                func.count(),
                func.avg(subquery.c.exceed_ratio),
            )
            .group_by(subquery.c.pollutant)
            .order_by(func.count().desc())
            .all()
        )
    ]

    top_stations = [
        {"station_id": station_id, "station_name": name, "count": int(count)}
        for station_id, name, count in (
            db.session.query(
                subquery.c.station_id,
                Station.name,
                func.count(),
            )
            .join(Station, Station.id == subquery.c.station_id)
            .group_by(subquery.c.station_id, Station.name)
            .order_by(func.count().desc())
            .limit(5)
            .all()
        )
    ]

    totals = db.session.query(
        func.count(subquery.c.id),
        func.max(subquery.c.exceed_ratio),
        func.avg(subquery.c.exceed_ratio),
    ).one()

    return {
        "total": int(totals[0] or 0),
        "pending": by_status["pending"]["count"],
        "by_status": list(by_status.values()),
        "by_level": list(by_level.values()),
        "top_pollutants": top_pollutants,
        "top_stations": top_stations,
        "max_ratio": round(float(totals[1] or 0), 3),
        "avg_ratio": round(float(totals[2] or 0), 3),
        "generated_at": iso(datetime.now()),
    }
