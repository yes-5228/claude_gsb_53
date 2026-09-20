"""超标标注的批次记录与单条标注历史 (含前后值对照)."""
import json
from datetime import datetime

from ..domain.constants import EXCEEDANCE_LEVEL_LABELS, EXCEEDANCE_STATUS_LABELS, label_of
from ..extensions import db
from .base import iso


class AnnotationBatch(db.Model):
    """一次批量标注操作: 幂等键 + 操作人/说明 + 处理结果快照."""

    __tablename__ = "annotation_batches"

    id = db.Column(db.Integer, primary_key=True)
    batch_key = db.Column(db.String(64), nullable=False, unique=True, index=True)
    status = db.Column(db.String(16), nullable=False)
    level = db.Column(db.String(16))
    note = db.Column(db.Text)
    annotator = db.Column(db.String(64))
    requested = db.Column(db.Integer, nullable=False, default=0)
    updated = db.Column(db.Integer, nullable=False, default=0)
    failed = db.Column(db.Integer, nullable=False, default=0)
    reannotated = db.Column(db.Integer, nullable=False, default=0)
    result = db.Column(db.Text)  # JSON 快照, 重复提交时原样返回
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    entries = db.relationship("ExceedanceAnnotation", back_populates="batch")

    def result_payload(self):
        return json.loads(self.result) if self.result else {}

    def to_dict(self):
        payload = self.result_payload()
        return {
            "id": self.id,
            "batch_key": self.batch_key,
            "status": self.status,
            "status_label": label_of(EXCEEDANCE_STATUS_LABELS, self.status),
            "level": self.level,
            "level_label": label_of(EXCEEDANCE_LEVEL_LABELS, self.level) if self.level else None,
            "note": self.note,
            "annotator": self.annotator,
            "requested": self.requested,
            "updated": self.updated,
            "failed": self.failed,
            "reannotated": self.reannotated,
            "failed_items": payload.get("failed", []),
            "created_at": iso(self.created_at),
        }


class ExceedanceAnnotation(db.Model):
    """单条超标记录的一次标注历史, 记录前后值以便对比两次标注的差别."""

    __tablename__ = "exceedance_annotations"

    id = db.Column(db.Integer, primary_key=True)
    exceedance_id = db.Column(
        db.Integer,
        db.ForeignKey("exceedances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id = db.Column(
        db.Integer,
        db.ForeignKey("annotation_batches.id", ondelete="SET NULL"),
        index=True,
    )
    source = db.Column(db.String(16), nullable=False, default="single")  # single | batch
    annotator = db.Column(db.String(64))
    note = db.Column(db.Text)
    prev_status = db.Column(db.String(16))
    prev_level = db.Column(db.String(16))
    prev_note = db.Column(db.Text)
    prev_annotator = db.Column(db.String(64))
    new_status = db.Column(db.String(16), nullable=False)
    new_level = db.Column(db.String(16), nullable=False)
    new_note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    batch = db.relationship("AnnotationBatch", back_populates="entries")
    exceedance = db.relationship("Exceedance")

    def to_dict(self):
        return {
            "id": self.id,
            "exceedance_id": self.exceedance_id,
            "source": self.source,
            "source_label": "批量标注" if self.source == "batch" else "单条标注",
            "batch_key": self.batch.batch_key if self.batch else None,
            "annotator": self.annotator,
            "note": self.note,
            "prev_status": self.prev_status,
            "prev_status_label": label_of(EXCEEDANCE_STATUS_LABELS, self.prev_status),
            "prev_level": self.prev_level,
            "prev_level_label": label_of(EXCEEDANCE_LEVEL_LABELS, self.prev_level),
            "prev_note": self.prev_note,
            "prev_annotator": self.prev_annotator,
            "new_status": self.new_status,
            "new_status_label": label_of(EXCEEDANCE_STATUS_LABELS, self.new_status),
            "new_level": self.new_level,
            "new_level_label": label_of(EXCEEDANCE_LEVEL_LABELS, self.new_level),
            "new_note": self.new_note,
            "created_at": iso(self.created_at),
        }
