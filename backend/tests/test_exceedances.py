"""超标记录标注接口测试."""
from app.models import Exceedance


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
    assert body["requested"] == 2
    assert body["missing"] == []
    assert body["annotator"] == "李静"
    assert Exceedance.query.filter_by(status="ignored").count() == 2

    missing = client.post(
        "/api/exceedances/annotations",
        json={
            "ids": [9999],
            "status": "confirmed",
            "note": "不存在",
            "annotator": "李静",
            "mode": "partial",
        },
    )
    assert missing.get_json()["missing"] == [9999]


def test_batch_annotation_requires_annotator(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]
    response = client.post(
        "/api/exceedances/annotations",
        json={"ids": ids, "status": "confirmed", "note": "复核确认"},
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["fields"]["annotator"] == "required"


def test_batch_annotation_atomic_mode_rolls_back_whole_batch(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]

    response = client.post(
        "/api/exceedances/annotations",
        json={
            "ids": ids + [9999],
            "status": "confirmed",
            "note": "复核确认",
            "annotator": "王敏",
            "mode": "atomic",
        },
    )
    assert response.status_code == 422
    payload = response.get_json()
    assert "整批未生效" in payload["error"]["message"]
    assert payload["error"]["fields"]["missing"] == [9999]
    # 整批未生效: 所有记录保持待标注
    assert Exceedance.query.filter_by(status="pending").count() == 2


def test_batch_annotation_partial_mode_reports_per_item(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]

    response = client.post(
        "/api/exceedances/annotations",
        json={
            "ids": ids + [9999],
            "status": "confirmed",
            "note": "复核确认",
            "annotator": "王敏",
            "mode": "partial",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    # 勾选数量与实际处理数严格对账: requested == updated + failed
    assert body["requested"] == 3
    assert body["updated"] == 2
    assert body["requested"] == body["updated"] + len(body["failed"])
    assert body["failed"] == [{"id": 9999, "reason": "记录不存在或已被删除"}]
    per_item = {item["id"]: item["result"] for item in body["items"]}
    assert per_item[9999] == "missing"
    assert per_item[ids[0]] == "updated"
    assert Exceedance.query.filter_by(status="confirmed").count() == 2


def test_batch_annotation_is_idempotent_per_request_id(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]

    first = client.post(
        "/api/exceedances/annotations",
        json={
            "ids": ids,
            "status": "confirmed",
            "note": "首次提交",
            "annotator": "王敏",
            "request_id": "batch-0001",
        },
    )
    assert first.status_code == 200
    first_body = first.get_json()
    assert first_body["deduplicated"] is False

    # 同一批重复提交 (即使负载不同) 只产生一次处理结果
    second = client.post(
        "/api/exceedances/annotations",
        json={
            "ids": ids,
            "status": "ignored",
            "note": "重复提交不应生效",
            "annotator": "李静",
            "request_id": "batch-0001",
        },
    )
    assert second.status_code == 200
    second_body = second.get_json()
    assert second_body["deduplicated"] is True
    assert second_body["updated"] == first_body["updated"]
    assert second_body["note"] == "首次提交"
    record = Exceedance.query.order_by(Exceedance.id.asc()).first()
    assert record.status == "confirmed"
    assert record.note == "首次提交"
    assert record.annotator == "王敏"


def test_batch_annotation_reannotation_shows_before_after(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]
    client.post(
        "/api/exceedances/annotations",
        json={"ids": ids, "status": "confirmed", "note": "首次确认", "annotator": "王敏"},
    )

    response = client.post(
        "/api/exceedances/annotations",
        json={
            "ids": ids,
            "status": "ignored",
            "level": "light",
            "note": "复核后改为忽略",
            "annotator": "李静",
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    item = next(entry for entry in body["items"] if entry["id"] == ids[0])
    assert item["reannotated"] is True
    assert item["changed"] is True
    assert item["before"]["status"] == "confirmed"
    assert item["before"]["note"] == "首次确认"
    assert item["before"]["annotator"] == "王敏"
    assert item["after"]["status"] == "ignored"
    assert item["after"]["note"] == "复核后改为忽略"
    assert item["after"]["annotator"] == "李静"
    assert item["after"]["level"] == "light"


def test_annotation_batch_can_be_looked_up_by_request_id(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    ids = [item.id for item in Exceedance.query.all()]
    client.post(
        "/api/exceedances/annotations",
        json={
            "ids": ids,
            "status": "confirmed",
            "note": "复核确认",
            "annotator": "王敏",
            "request_id": "batch-lookup",
        },
    )

    stored = client.get("/api/exceedances/annotations/batch-lookup").get_json()
    assert stored["request_id"] == "batch-lookup"
    assert stored["annotator"] == "王敏"
    assert stored["updated"] == 2
    assert stored["recorded_at"] is not None

    missing = client.get("/api/exceedances/annotations/no-such-batch")
    assert missing.status_code == 404


def test_exceedance_ids_endpoint_supports_cross_page_select_all(client, station, entry_payload):
    for hour in range(10, 13):
        _make_exceedances(client, station, entry_payload, measured_at="2026-09-01 %d:00" % hour)
    total = Exceedance.query.count()
    assert total == 6

    body = client.get("/api/exceedances/ids").get_json()
    assert body["total"] == total
    assert len(body["ids"]) == total
    assert body["truncated"] is False

    only_so2 = client.get("/api/exceedances/ids?pollutant=SO2").get_json()
    assert only_so2["total"] == 3


def test_batch_annotation_refreshes_summary_counts(client, station, entry_payload):
    _make_exceedances(client, station, entry_payload)
    before = client.get("/api/exceedances/summary").get_json()
    assert before["pending"] == 2

    ids = [item.id for item in Exceedance.query.all()]
    client.post(
        "/api/exceedances/annotations",
        json={
            "ids": ids,
            "status": "confirmed",
            "level": "severe",
            "note": "复核确认",
            "annotator": "王敏",
        },
    )

    after = client.get("/api/exceedances/summary").get_json()
    assert after["pending"] == 0
    status_counts = {item["key"]: item["count"] for item in after["by_status"]}
    assert status_counts["confirmed"] == 2
    level_counts = {item["key"]: item["count"] for item in after["by_level"]}
    assert level_counts["severe"] == 2
    assert after["top_pollutants"][0]["count"] == 1


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
