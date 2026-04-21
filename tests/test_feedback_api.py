"""Phase 5 #3 — DimensionFeedback API 測試。

覆蓋 spec 的 8 個 case：
1. 合法 upsert 4 種 feedback_type 都能存
2. feedback_type="note" + note_text 空白/None → 400
3. feedback_type 非合法 enum → 400
4. 同 (audio, annotator, dim) POST 兩次 → upsert 不重複建 row、updated_at 更新
5. 切換 type 從 "note" → "vague" 會清掉 note_text
6. GET /dimension?annotator=&audio_file_id= 回該 audio 該 annotator 全部 feedback
7. GET /summary 正確聚合 by_dimension counts + recent_notes
8. 不同 annotator 資料互不污染
"""
from __future__ import annotations

from sqlmodel import Session, select

from src.models import AudioFile, DimensionFeedback


def _make_audio(engine, filename: str = "a.wav") -> str:
    with Session(engine) as s:
        a = AudioFile(
            filename=filename, game_name=filename.split("_")[0], game_stage="Base Game",
        )
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def _base_payload(audio_id: str, annotator: str = "amber", dim: str = "emotional_warmth") -> dict:
    return {
        "audio_file_id": audio_id,
        "annotator_id": annotator,
        "dimension_key": dim,
        "feedback_type": "clear",
    }


def test_post_all_four_feedback_types_accepted(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    dims = ["valence", "arousal", "emotional_warmth", "tension_direction"]
    types = ["clear", "vague", "misaligned", "note"]
    for dim, ftype in zip(dims, types):
        payload = _base_payload(audio_id)
        payload["dimension_key"] = dim
        payload["feedback_type"] = ftype
        if ftype == "note":
            payload["note_text"] = "這個維度定義有點抽象"
        r = client.post("/api/feedback/dimension", json=payload)
        assert r.status_code == 200, f"type={ftype} 失敗：{r.text}"
    # DB 應有 4 row
    with Session(in_memory_engine) as s:
        rows = s.exec(select(DimensionFeedback)).all()
        assert len(rows) == 4


def test_post_note_type_without_note_text_rejected(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    # 情境 A: note_text=None
    p1 = _base_payload(audio_id)
    p1["feedback_type"] = "note"
    r1 = client.post("/api/feedback/dimension", json=p1)
    assert r1.status_code == 400
    assert "note_text" in r1.json()["detail"]
    # 情境 B: note_text="   "（只有空白）
    p2 = _base_payload(audio_id)
    p2["feedback_type"] = "note"
    p2["note_text"] = "   "
    r2 = client.post("/api/feedback/dimension", json=p2)
    assert r2.status_code == 400


def test_post_invalid_feedback_type_rejected(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    p = _base_payload(audio_id)
    p["feedback_type"] = "garbage_value"
    r = client.post("/api/feedback/dimension", json=p)
    assert r.status_code == 400
    assert "feedback_type" in r.json()["detail"]


def test_post_upsert_does_not_duplicate(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    p1 = _base_payload(audio_id)
    p1["feedback_type"] = "clear"
    r1 = client.post("/api/feedback/dimension", json=p1)
    assert r1.status_code == 200
    first_id = r1.json()["feedback_id"]

    # 同 key 再 POST 不同 type
    p2 = _base_payload(audio_id)
    p2["feedback_type"] = "vague"
    r2 = client.post("/api/feedback/dimension", json=p2)
    assert r2.status_code == 200
    assert r2.json()["feedback_id"] == first_id  # upsert：同一 row

    with Session(in_memory_engine) as s:
        rows = s.exec(select(DimensionFeedback)).all()
        assert len(rows) == 1
        assert rows[0].feedback_type == "vague"


def test_post_switching_from_note_clears_note_text(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    # 先存 note
    p1 = _base_payload(audio_id)
    p1["feedback_type"] = "note"
    p1["note_text"] = "我覺得這維度應該獨立於樂器選擇"
    r1 = client.post("/api/feedback/dimension", json=p1)
    assert r1.status_code == 200

    # 改成 vague — note_text 應清掉避免誤導
    p2 = _base_payload(audio_id)
    p2["feedback_type"] = "vague"
    r2 = client.post("/api/feedback/dimension", json=p2)
    assert r2.status_code == 200

    with Session(in_memory_engine) as s:
        row = s.exec(select(DimensionFeedback)).one()
        assert row.feedback_type == "vague"
        assert row.note_text is None


def test_get_feedback_for_audio_returns_all(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    for dim in ["valence", "arousal", "emotional_warmth"]:
        p = _base_payload(audio_id, dim=dim)
        p["feedback_type"] = "vague"
        client.post("/api/feedback/dimension", json=p)

    r = client.get(
        f"/api/feedback/dimension?annotator=amber&audio_file_id={audio_id}"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["annotator_id"] == "amber"
    assert body["audio_file_id"] == audio_id
    returned_dims = {f["dimension_key"] for f in body["feedbacks"]}
    assert returned_dims == {"valence", "arousal", "emotional_warmth"}


def test_summary_aggregates_correctly(client, in_memory_engine):
    a1 = _make_audio(in_memory_engine, "a.wav")
    a2 = _make_audio(in_memory_engine, "b.wav")

    # emotional_warmth: 1 vague (a1) + 1 note (a2)
    # valence: 1 clear (a1)
    client.post("/api/feedback/dimension", json={
        "audio_file_id": a1, "annotator_id": "amber",
        "dimension_key": "emotional_warmth", "feedback_type": "vague",
    })
    client.post("/api/feedback/dimension", json={
        "audio_file_id": a2, "annotator_id": "amber",
        "dimension_key": "emotional_warmth", "feedback_type": "note",
        "note_text": "定義跟我想的有出入",
    })
    client.post("/api/feedback/dimension", json={
        "audio_file_id": a1, "annotator_id": "amber",
        "dimension_key": "valence", "feedback_type": "clear",
    })

    r = client.get("/api/feedback/summary?annotator=amber")
    assert r.status_code == 200
    body = r.json()
    ew = body["by_dimension"]["emotional_warmth"]
    assert ew == {"clear": 0, "vague": 1, "misaligned": 0, "note": 1, "total": 2}
    val = body["by_dimension"]["valence"]
    assert val == {"clear": 1, "vague": 0, "misaligned": 0, "note": 0, "total": 1}
    assert len(body["recent_notes"]) == 1
    assert body["recent_notes"][0]["dimension_key"] == "emotional_warmth"
    assert body["recent_notes"][0]["note_text"] == "定義跟我想的有出入"
    assert body["recent_notes"][0]["audio_file"] == "b.wav"


def test_different_annotators_isolated(client, in_memory_engine):
    a = _make_audio(in_memory_engine)
    client.post("/api/feedback/dimension", json={
        "audio_file_id": a, "annotator_id": "amber",
        "dimension_key": "valence", "feedback_type": "clear",
    })
    client.post("/api/feedback/dimension", json={
        "audio_file_id": a, "annotator_id": "bob",
        "dimension_key": "valence", "feedback_type": "vague",
    })

    amber = client.get(f"/api/feedback/dimension?annotator=amber&audio_file_id={a}").json()
    bob = client.get(f"/api/feedback/dimension?annotator=bob&audio_file_id={a}").json()
    assert len(amber["feedbacks"]) == 1
    assert amber["feedbacks"][0]["feedback_type"] == "clear"
    assert len(bob["feedbacks"]) == 1
    assert bob["feedbacks"][0]["feedback_type"] == "vague"

    amber_summary = client.get("/api/feedback/summary?annotator=amber").json()
    bob_summary = client.get("/api/feedback/summary?annotator=bob").json()
    assert amber_summary["by_dimension"]["valence"]["clear"] == 1
    assert bob_summary["by_dimension"]["valence"]["vague"] == 1
