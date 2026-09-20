"""超标记录标注接口测试."""
from app.models import AnnotationBatch, Exceedance, ExceedanceAnnotation


def _make_exceedances(client, station, entry_payload, measured_at="2026-09-01 10:00"):
    return client.post(
        "/api/measurements/entries",
        json=entry_payload(
            station.id,
            measured_at=measured_at,
            entries=[
                {"pollutant": "SO2", "value": 600.0},
                {"pollutant": "NO2", "value": 300.0},
                {"pollutant": "PM25", "value": 40.0},
            ],
        ),
    ).get_json()


def test_exceedance_records_are_created_automatically(client, station, entry_payload):
    body = _make_exceedances(client, station, entry_payload)
    assert body["summary"]["exceeded_count"] == 2

    listed = client.get("/api/exceedances").get_json()
    assert listed["total"] == 2
    levels = {item["pollutant"]: item["level"] for item in listed["items"]}
    assert levels == {"SO2": "light", "NO2": "moderate"}
    assert listed["summary"]["pending"] == 2
    assert listed["summary"]["by_status"][0]["key"] == "pending"


def test_annotation_requires_note_when_not_pending(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    exceedance_id = Exceedance.query.first().id

    response = client.patch("/api/exceedances/%d" % exceedance_id, json={"status": "confirmed"})
    assert response.status_code == 422
    assert response.get_json()["error"]["fields"]["note"] == "required"


def test_single_annotation_persists_note_and_annotator(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    exceedance_id = Exceedance.query.order_by(Exceedance.id.asc()).first().id

    response = client.patch(
        "/api/exceedances/%d" % exceedance_id,
        json={
            "status": "confirmed",
            "level": "severe",
            "note": "复核确认超标, 已通知现场核查",
            "annotator": "王敏",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "confirmed"
    assert body["status_label"] == "已确认"
    assert body["level"] == "severe"
    assert body["note"] == "复核确认超标, 已通知现场核查"
    assert body["annotator"] == "王敏"
    assert body["annotated_at"] is not None


def test_batch_annotation_updates_selected_records(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]

    response = client.post(
        "/api/exceedances/annotations",
        json={"ids": ids, "status": "ignored", "note": "仪器校准异常值", "annotator": "李静"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["updated"] == 2
    assert body["missing"] == []
    assert Exceedance.query.filter_by(status="ignored").count() == 2

    missing = client.post(
        "/api/exceedances/annotations",
        json={"ids": [9999], "status": "confirmed", "note": "不存在"},
    )
    assert missing.get_json()["missing"] == [9999]


def test_batch_annotation_without_note_is_rejected(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]
    response = client.post(
        "/api/exceedances/annotations", json={"ids": ids, "status": "confirmed"}
    )
    assert response.status_code == 422


def test_exceedance_filters_and_summary(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    only_so2 = client.get("/api/exceedances?pollutant=SO2&level=light").get_json()
    assert only_so2["total"] == 1
    assert only_so2["items"][0]["pollutant"] == "SO2"
    assert only_so2["summary"]["total"] == 1

    annotated = client.get("/api/exceedances?annotated=false").get_json()
    assert annotated["total"] == 2

    detail = client.get("/api/exceedances/%d" % only_so2["items"][0]["id"]).get_json()
    assert detail["measurement"]["station"]["code"] == "TEST-001"


def test_exceedance_options_and_export(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    options = client.get("/api/exceedances/options").get_json()
    assert {item["value"] for item in options["statuses"]} == {"pending", "confirmed", "ignored"}

    csv_body = client.get("/api/exceedances/export").get_data(as_text=True)
    assert csv_body.startswith("\ufeff站点编码")
    assert "SO₂" not in csv_body  # 导出使用标准因子代码
    assert "SO2" in csv_body


def test_batch_result_reconciles_requested_with_per_record_reasons(client, station, entry_payload):
    """跨页勾选数量必须与实际处理数严格对得上, 失败记录逐条给出原因."""
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]
    selected = ids + [9999]

    response = client.post(
        "/api/exceedances/annotations",
        json={"ids": selected, "status": "confirmed", "note": "复核属实", "annotator": "王敏"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["requested"] == len(selected)
    assert body["updated"] == len(ids)
    assert body["requested"] == body["updated"] + len(body["failed"])
    assert body["failed"] == [{"id": 9999, "reason": "记录不存在或已被删除"}]
    assert body["missing"] == [9999]
    assert sorted(body["updated_ids"]) == sorted(ids)


def test_batch_annotation_rejects_non_integer_ids(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    response = client.post(
        "/api/exceedances/annotations",
        json={"ids": ["abc"], "status": "confirmed", "note": "复核属实"},
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["fields"]["ids"] == "invalid"


def test_batch_validation_failure_leaves_whole_batch_untouched(client, station, entry_payload):
    """参数不满足条件 (缺说明) 时整批不生效."""
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]

    response = client.post(
        "/api/exceedances/annotations", json={"ids": ids, "status": "confirmed"}
    )
    assert response.status_code == 422
    assert Exceedance.query.filter_by(status="pending").count() == len(ids)
    assert ExceedanceAnnotation.query.count() == 0


def test_batch_annotation_is_idempotent_with_batch_key(client, station, entry_payload):
    """同一批次重复提交只产生一次处理结果."""
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]
    payload = {
        "ids": ids,
        "status": "confirmed",
        "note": "复核属实",
        "annotator": "王敏",
        "batch_id": "batch-20260920-001",
    }

    first = client.post("/api/exceedances/annotations", json=payload)
    assert first.status_code == 200
    first_body = first.get_json()
    assert first_body["idempotent_replay"] is False
    assert first_body["updated"] == len(ids)

    second = client.post("/api/exceedances/annotations", json=payload)
    assert second.status_code == 200
    second_body = second.get_json()
    assert second_body["idempotent_replay"] is True
    assert second_body["updated"] == first_body["updated"]
    assert second_body["processed_at"] == first_body["processed_at"]

    # 只产生一次处理结果: 批次记录与标注历史均不重复
    assert AnnotationBatch.query.filter_by(batch_key="batch-20260920-001").count() == 1
    assert ExceedanceAnnotation.query.count() == len(ids)


def test_annotation_history_shows_diff_between_two_annotations(client, station, entry_payload):
    """已标注记录再次处理时, 历史中能看出前后两次的差别."""
    _make_exceedances(client, station, entry_payload)
    exceedance_id = Exceedance.query.order_by(Exceedance.id.asc()).first().id

    client.patch(
        "/api/exceedances/%d" % exceedance_id,
        json={"status": "confirmed", "note": "首次确认", "annotator": "王敏"},
    )
    client.post(
        "/api/exceedances/annotations",
        json={
            "ids": [exceedance_id],
            "status": "ignored",
            "note": "复核后为校准数据, 改判忽略",
            "annotator": "李静",
        },
    )

    history = client.get("/api/exceedances/%d/annotations" % exceedance_id).get_json()
    assert history["total"] == 2
    latest, previous = history["items"][0], history["items"][1]

    assert previous["source"] == "single"
    assert previous["prev_status"] == "pending"
    assert previous["new_status"] == "confirmed"
    assert previous["new_note"] == "首次确认"

    assert latest["source"] == "batch"
    assert latest["prev_status"] == "confirmed"
    assert latest["prev_note"] == "首次确认"
    assert latest["prev_annotator"] == "王敏"
    assert latest["new_status"] == "ignored"
    assert latest["new_note"] == "复核后为校准数据, 改判忽略"
    assert latest["annotator"] == "李静"


def test_batch_marks_reannotated_records(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]
    client.patch(
        "/api/exceedances/%d" % ids[0],
        json={"status": "confirmed", "note": "首次确认", "annotator": "王敏"},
    )

    body = client.post(
        "/api/exceedances/annotations",
        json={"ids": ids, "status": "ignored", "note": "整批复核为干扰数据"},
    ).get_json()
    assert body["reannotated"] == 1
    assert body["updated"] == len(ids)


def test_recent_batches_log_operator_and_note(client, station, entry_payload):
    """每次批量操作都留下操作人和说明."""
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]
    client.post(
        "/api/exceedances/annotations",
        json={
            "ids": ids,
            "status": "confirmed",
            "note": "月度复核通过",
            "annotator": "李静",
            "batch_id": "batch-log-001",
        },
    )

    batches = client.get("/api/exceedances/batches").get_json()
    assert batches["total"] == 1
    item = batches["items"][0]
    assert item["batch_key"] == "batch-log-001"
    assert item["annotator"] == "李静"
    assert item["note"] == "月度复核通过"
    assert item["requested"] == len(ids)
    assert item["updated"] == len(ids)
    assert item["failed"] == 0
    assert item["status_label"] == "已确认"
