"""POST /api/annotations 的完整度驗證與 upsert 邏輯測試。

覆蓋 acceptance 條款：
- function_roles=[] → 400
- dimension 超出 [0,1] → 400
- source_type 非法 → 400
- 完整 payload → 200 + is_complete=True
- 部分 payload（缺維度 / source）→ 200 + is_complete=False
- 同 (audio, annotator) 第二次 POST → upsert 而非新增
- next_audio_id 指向下一個未完成音檔
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select

from src.models import AudioFile, Annotation


def _make_audio(engine, filename: str = "Foo_Base Game.wav") -> str:
    """在 test DB 建一筆 AudioFile，回 id。"""
    with Session(engine) as s:
        audio = AudioFile(
            filename=filename,
            game_name=filename.split("_")[0],
            game_stage=filename.split("_")[1].removesuffix(".wav"),
        )
        s.add(audio)
        s.commit()
        s.refresh(audio)
        return audio.id


def _complete_payload(audio_id: str, annotator_id: str = "amber") -> dict:
    """產生一個 is_complete=True 的合法 payload。"""
    return {
        "audio_id": audio_id,
        "annotator_id": annotator_id,
        "valence": 0.7,
        "arousal": 0.6,
        "emotional_warmth": 0.5,
        "tension_direction": 0.3,
        "temporal_position": 0.25,
        "event_significance": 0.6,
        "loop_capability": [1.0],
        "tonal_noise_ratio": 0.8,
        "spectral_density": 0.5,
        "world_immersion": 0.7,
        "source_type": ["ambience"],
        "function_roles": ["atmosphere", "gameplay_core"],
        "genre_tag": ["博弈"],
        "worldview_tag": "asian_mythology",
        "style_tag": ["chinese_traditional"],
        "notes": "測試用",
    }


def test_post_complete_payload_marks_is_complete_true(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    r = client.post("/api/annotations", json=_complete_payload(audio_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_complete"] is True
    assert body["annotation_id"]


def test_post_empty_function_roles_returns_400(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["function_roles"] = []
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 400
    assert "function_roles" in r.json()["detail"]


def test_post_out_of_range_dimension_returns_400(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["valence"] = 1.5
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 400
    assert "valence" in r.json()["detail"]
    assert "1.5" in r.json()["detail"]


def test_post_invalid_source_type_returns_400(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["source_type"] = ["not_a_real_type"]
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 400
    assert "source_type" in r.json()["detail"]


def test_post_partial_payload_marks_is_complete_false(client, in_memory_engine):
    """source_type=[] 表示半成品；仍接受儲存，但 is_complete=False。"""
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["source_type"] = []  # 還沒選音源類型
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200
    assert r.json()["is_complete"] is False


def test_post_multi_source_type_accepted(client, in_memory_engine):
    """同時選 ambience + synthetic_designed → is_complete=True，DB 存 JSON list。"""
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["source_type"] = ["ambience", "synthetic_designed"]
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200
    assert r.json()["is_complete"] is True

    with Session(in_memory_engine) as s:
        ann = s.exec(select(Annotation).where(Annotation.audio_file_id == audio_id)).one()
        assert json.loads(ann.source_type) == ["ambience", "synthetic_designed"]


def test_post_partial_missing_dimension_marks_is_complete_false(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["arousal"] = None
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200
    assert r.json()["is_complete"] is False


def test_post_empty_loop_capability_marks_is_complete_false(client, in_memory_engine):
    """loop_capability=[] 是合法的草稿狀態，但 is_complete=False。"""
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["loop_capability"] = []
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200
    assert r.json()["is_complete"] is False


def test_post_invalid_loop_capability_value_returns_400(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["loop_capability"] = [0.7]  # 0.7 不在 {0, 0.5, 1}
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 400
    assert "loop_capability" in r.json()["detail"]


def test_post_multi_loop_capability_accepted(client, in_memory_engine):
    """同時選 0.5 + 1.0 → is_complete=True。"""
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["loop_capability"] = [0.5, 1.0]
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200
    assert r.json()["is_complete"] is True

    with Session(in_memory_engine) as s:
        ann = s.exec(select(Annotation).where(Annotation.audio_file_id == audio_id)).one()
        assert json.loads(ann.loop_capability) == [0.5, 1.0]


def test_post_stores_genre_tag_as_json_array(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    payload["genre_tag"] = ["博弈", "東方"]
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200

    with Session(in_memory_engine) as s:
        ann = s.exec(select(Annotation).where(Annotation.audio_file_id == audio_id)).one()
        assert json.loads(ann.genre_tag) == ["博弈", "東方"]


def test_post_upsert_updates_existing_row(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)

    r1 = client.post("/api/annotations", json=payload)
    assert r1.status_code == 200
    first_id = r1.json()["annotation_id"]

    payload["valence"] = 0.1
    r2 = client.post("/api/annotations", json=payload)
    assert r2.status_code == 200
    assert r2.json()["annotation_id"] == first_id

    with Session(in_memory_engine) as s:
        rows = s.exec(select(Annotation).where(Annotation.audio_file_id == audio_id)).all()
        assert len(rows) == 1
        assert rows[0].valence == 0.1


def test_post_stores_function_roles_as_json_array(client, in_memory_engine):
    audio_id = _make_audio(in_memory_engine)
    payload = _complete_payload(audio_id)
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200

    with Session(in_memory_engine) as s:
        ann = s.exec(select(Annotation).where(Annotation.audio_file_id == audio_id)).one()
        decoded = json.loads(ann.function_roles)
        assert decoded == ["atmosphere", "gameplay_core"]
        # 風格 tag 也應以 JSON array 儲存
        assert json.loads(ann.style_tag) == ["chinese_traditional"]


def test_next_audio_id_points_to_next_incomplete(client, in_memory_engine):
    # 三筆音檔：AAA、BBB、CCC（按 game_name 排序）
    a = _make_audio(in_memory_engine, "AAA_Base Game.wav")
    b = _make_audio(in_memory_engine, "BBB_Base Game.wav")
    c = _make_audio(in_memory_engine, "CCC_Base Game.wav")

    r = client.post("/api/annotations", json=_complete_payload(a))
    assert r.status_code == 200
    assert r.json()["next_audio_id"] == b

    r = client.post("/api/annotations", json=_complete_payload(b))
    assert r.json()["next_audio_id"] == c

    r = client.post("/api/annotations", json=_complete_payload(c))
    # 全部標完 → None
    assert r.json()["next_audio_id"] is None


def test_post_to_missing_audio_returns_404(client, in_memory_engine):
    r = client.post("/api/annotations", json=_complete_payload("no-such-audio-id"))
    assert r.status_code == 404


def test_annotators_list_returns_distinct_ids(client, in_memory_engine):
    a = _make_audio(in_memory_engine, "Foo_Base Game.wav")
    b = _make_audio(in_memory_engine, "Bar_Base Game.wav")
    p1 = _complete_payload(a, annotator_id="amber")
    p2 = _complete_payload(b, annotator_id="aaron")
    p3 = _complete_payload(a, annotator_id="aaron")  # dup annotator_id
    client.post("/api/annotations", json=p1)
    client.post("/api/annotations", json=p2)
    client.post("/api/annotations", json=p3)

    r = client.get("/api/annotations/annotators")
    assert r.status_code == 200
    assert r.json() == ["aaron", "amber"]


def test_annotators_list_empty_when_no_records(client):
    r = client.get("/api/annotations/annotators")
    assert r.status_code == 200
    assert r.json() == []
