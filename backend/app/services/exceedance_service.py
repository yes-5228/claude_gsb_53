"""超标记录查询与人工标注."""
import json
from datetime import datetime

from sqlalchemy import cast, func, or_
from sqlalchemy.exc import IntegrityError

from ..domain.constants import EXCEEDANCE_LEVEL_LABELS, EXCEEDANCE_STATUS_LABELS
from ..errors import NotFoundError, ValidationError
from ..extensions import db
from ..models import AnnotationBatch, Exceedance, ExceedanceAnnotation, Measurement, Station
from ..models.base import iso

STATUS_CHOICES = tuple(EXCEEDANCE_STATUS_LABELS.keys())
LEVEL_CHOICES = tuple(EXCEEDANCE_LEVEL_LABELS.keys())


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


def _snapshot(exceedance):
    """Capture the annotation fields before a change, for history diffing."""
    return {
        "status": exceedance.status,
        "level": exceedance.level,
        "note": exceedance.note,
        "annotator": exceedance.annotator,
    }


def _record_history(exceedance, prev, *, source, batch=None, note=None, annotator=None):
    entry = ExceedanceAnnotation(
        exceedance_id=exceedance.id,
        source=source,
        annotator=annotator,
        note=note,
        prev_status=prev["status"],
        prev_level=prev["level"],
        prev_note=prev["note"],
        prev_annotator=prev["annotator"],
        new_status=exceedance.status,
        new_level=exceedance.level,
        new_note=exceedance.note,
    )
    if batch is not None:
        entry.batch = batch
    db.session.add(entry)
    return entry


def annotate(exceedance, status=None, note=None, annotator=None, level=None):
    """Apply a manual annotation to an exceedance record."""
    prev = _snapshot(exceedance)
    note_text = (note or "").strip()
    annotator_text = (annotator or "").strip()

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

    if exceedance.status == "pending":
        exceedance.note = note_text or exceedance.note
        exceedance.annotator = annotator_text or exceedance.annotator
        exceedance.annotated_at = None if not note_text else datetime.now()
    else:
        if not note_text:
            reason = "确认" if exceedance.status == "confirmed" else "忽略"
            raise ValidationError(
                "标注为\"%s\"时必须填写%s原因" % (EXCEEDANCE_STATUS_LABELS[exceedance.status], reason),
                fields={"note": "required"},
            )
        exceedance.note = note_text
        exceedance.annotator = annotator_text or "未署名"
        exceedance.annotated_at = datetime.now()

    _record_history(
        exceedance,
        prev,
        source="single",
        note=note_text or None,
        annotator=annotator_text or None,
    )
    db.session.commit()
    return exceedance


def _replay_result(batch):
    """Stored result of a previously processed batch (idempotent replay)."""
    payload = batch.result_payload()
    payload["idempotent_replay"] = True
    return payload


def annotate_batch(ids, status, note=None, annotator=None, level=None, batch_key=None):
    """Batch annotation used by the exceedance work bench.

    - 参数不合法 (如缺少说明) -> 422, 整批不生效;
    - 单条记录不满足条件 (如不存在) -> 其余照常处理, 逐条返回失败原因;
    - 携带相同 batch_key 的重复提交 -> 直接返回首次处理结果, 不重复写入.
    """
    note_text = (note or "").strip()
    annotator_text = (annotator or "").strip()

    if status not in STATUS_CHOICES:
        raise ValidationError(
            "标注状态取值不合法, 可选: %s" % ", ".join(STATUS_CHOICES),
            fields={"status": "unknown"},
        )
    if level is not None and level not in LEVEL_CHOICES:
        raise ValidationError(
            "超标等级取值不合法, 可选: %s" % ", ".join(LEVEL_CHOICES),
            fields={"level": "unknown"},
        )
    if status != "pending" and not note_text:
        raise ValidationError(
            "批量标注为\"%s\"时必须填写标注说明" % EXCEEDANCE_STATUS_LABELS[status],
            fields={"note": "required"},
        )

    try:
        ids = list(dict.fromkeys(int(item) for item in ids))
    except (TypeError, ValueError):
        raise ValidationError("ids 必须是整数数组", fields={"ids": "invalid"})
    if not ids:
        raise ValidationError("请至少选择一条超标记录", fields={"ids": "empty"})

    if batch_key:
        existing = AnnotationBatch.query.filter_by(batch_key=batch_key).first()
        if existing is not None:
            return _replay_result(existing)

    records = Exceedance.query.filter(Exceedance.id.in_(ids)).all()
    found = {record.id: record for record in records}

    batch = None
    if batch_key:
        batch = AnnotationBatch(
            batch_key=batch_key,
            status=status,
            level=level,
            note=note_text or None,
            annotator=annotator_text or None,
        )
        db.session.add(batch)

    updated_ids = []
    failed = []
    reannotated = 0
    now = datetime.now()
    for item in ids:
        record = found.get(item)
        if record is None:
            failed.append({"id": item, "reason": "记录不存在或已被删除"})
            continue
        prev = _snapshot(record)
        if prev["status"] != "pending" or record.annotated_at is not None:
            reannotated += 1
        record.status = status
        if level is not None:
            record.level = level
        if note_text:
            record.note = note_text
        if status == "pending":
            record.annotated_at = None
        else:
            record.annotator = annotator_text or record.annotator or "未署名"
            record.annotated_at = now
        _record_history(
            record,
            prev,
            source="batch",
            batch=batch,
            note=note_text or None,
            annotator=annotator_text or None,
        )
        updated_ids.append(item)

    result = {
        "batch_id": batch_key,
        "idempotent_replay": False,
        "status": status,
        "requested": len(ids),
        "updated": len(updated_ids),
        "updated_ids": updated_ids,
        "failed": failed,
        "missing": [item["id"] for item in failed],
        "reannotated": reannotated,
        "annotator": annotator_text or None,
        "note": note_text or None,
        "processed_at": iso(now),
    }

    if batch is not None:
        batch.requested = result["requested"]
        batch.updated = result["updated"]
        batch.failed = len(failed)
        batch.reannotated = reannotated
        batch.result = json.dumps(
            {key: value for key, value in result.items() if key != "idempotent_replay"},
            ensure_ascii=False,
        )

    try:
        db.session.commit()
    except IntegrityError:
        # 并发下同一 batch_key 已被其他请求写入: 回滚后按幂等重放处理
        db.session.rollback()
        if batch_key:
            existing = AnnotationBatch.query.filter_by(batch_key=batch_key).first()
            if existing is not None:
                return _replay_result(existing)
        raise
    return result


def annotation_history(exceedance_id, limit=50):
    """单条记录的标注历史 (新→旧), 含每次标注的前后值对照."""
    get_exceedance(exceedance_id)
    entries = (
        ExceedanceAnnotation.query.filter_by(exceedance_id=exceedance_id)
        .order_by(ExceedanceAnnotation.created_at.desc(), ExceedanceAnnotation.id.desc())
        .limit(limit)
        .all()
    )
    return {"items": [entry.to_dict() for entry in entries], "total": len(entries)}


def recent_batches(limit=20):
    """最近的批量标注操作记录 (操作人/说明/结果), 供工作台留痕展示."""
    try:
        limit = min(max(int(limit or 20), 1), 100)
    except (TypeError, ValueError):
        limit = 20
    batches = (
        AnnotationBatch.query.order_by(AnnotationBatch.created_at.desc(), AnnotationBatch.id.desc())
        .limit(limit)
        .all()
    )
    return {"items": [batch.to_dict() for batch in batches], "total": len(batches)}


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
