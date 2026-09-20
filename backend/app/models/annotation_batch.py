"""批量标注操作记录 (审计 + 幂等)."""
from ..extensions import db
from .base import TimestampMixin, iso


class AnnotationBatch(TimestampMixin, db.Model):
    """One row per submitted batch annotation.

    ``request_id`` is the client supplied idempotency key: resubmitting the
    same key returns the stored result instead of processing the batch twice.
    ``result`` keeps the full per-item payload (before/after snapshots) so the
    operation can be audited and re-rendered later.
    """

    __tablename__ = "annotation_batches"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    mode = db.Column(db.String(16), nullable=False, default="atomic")
    status = db.Column(db.String(16), nullable=False)
    level = db.Column(db.String(16))
    note = db.Column(db.Text)
    annotator = db.Column(db.String(64), nullable=False)
    requested = db.Column(db.Integer, nullable=False, default=0)
    updated = db.Column(db.Integer, nullable=False, default=0)
    result = db.Column(db.JSON)

    def to_dict(self):
        payload = dict(self.result or {})
        payload.setdefault("request_id", self.request_id)
        payload.setdefault("mode", self.mode)
        payload["recorded_at"] = iso(self.created_at)
        return payload

    def __repr__(self):
        return "<AnnotationBatch %s %s>" % (self.request_id, self.status)
