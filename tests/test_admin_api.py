"""Phase 8 — admin endpoints integration tests.

驗:
- GET  /api/admin/annotators/pending  (admin only,403 for non-admin)
- POST /api/admin/annotators/{id}/approve  (transition + 404 / 409 error paths)
- pending_calibration 在 POST /api/annotations 跟 GET /api/audio/{id} 被擋
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import Session

from src import annotators_loader, middleware
from src.models import Annotation, AudioFile


# ---------------------------------------------------------------------------
# fixtures: temp annotators config + override default path
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_annotators_config(tmp_path, monkeypatch):
    """建一個獨立 annotators_config.json 並 monkeypatch loader 的預設路徑。

    包含:amber (admin/active)、vvgosick (pending)、yyslin1024 (active)、
    archived_user (archived)。
    """
    payload = {
        "amber": {
            "name": "Amber",
            "email": "polanmusic2025@gmail.com",
            "annotator_profile": "music_professional",
            "status": "active",
            "is_admin": True,
            "joined_at": "2025-12-15",
        },
        "vvgosick": {
            "name": "老公",
            "email": "vvgosick@gmail.com",
            "annotator_profile": "general_audience",
            "status": "pending_calibration",
            "is_admin": False,
            "joined_at": "2026-05-12",
        },
        "yyslin1024": {
            "name": "養心",
            "email": "yyslin1024@gmail.com",
            "annotator_profile": "general_audience",
            "status": "active",
            "is_admin": False,
            "joined_at": "2026-02-01",
        },
        "ex_user": {
            "name": "Ex",
            "email": "ex@example.com",
            "annotator_profile": "general_audience",
            "status": "archived",
            "is_admin": False,
            "joined_at": "2026-01-01",
        },
    }
    path = tmp_path / "annotators_config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(annotators_loader, "_CONFIG_PATH", path)
    return path


def _make_audio(engine, filename: str = "X_Base Game.wav") -> str:
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


def _make_amber_completed_annotation(engine, audio_id: str) -> None:
    """模擬 Amber 已 is_complete 標好一首音檔 → 進入 calibration set。"""
    with Session(engine) as s:
        ann = Annotation(
            audio_file_id=audio_id,
            annotator_id="amber",
            valence=0.5, arousal=0.5, emotional_warmth=0.5,
            tension_direction=0.5, temporal_position=0.5,
            event_significance=0.5, world_immersion=0.5,
            loop_capability=json.dumps([1.0]),
            source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]),
            genre_tag=json.dumps(["博弈"]),
            style_tag=json.dumps([]),
            is_complete=True,
        )
        s.add(ann)
        s.commit()


def _complete_payload(audio_id: str, annotator_id: str = "vvgosick") -> dict:
    return {
        "audio_id": audio_id,
        "annotator_id": annotator_id,
        "valence": 0.7, "arousal": 0.6, "emotional_warmth": 0.5,
        "tension_direction": 0.3, "temporal_position": 0.25,
        "event_significance": 0.6, "world_immersion": 0.7,
        "loop_capability": [1.0],
        "source_type": ["ambience"],
        "function_roles": ["atmosphere"],
        "genre_tag": [], "worldview_tag": None,
        "style_tag": [], "notes": None,
    }


# ---------------------------------------------------------------------------
# admin endpoints
# ---------------------------------------------------------------------------

def test_list_pending_returns_only_pending(client, in_memory_engine, tmp_annotators_config):
    """admin 看 pending 清單 — 只該回 vvgosick(pending),不該包含 active / archived。"""
    r = client.get("/api/admin/annotators/pending?annotator=amber")
    assert r.status_code == 200, r.text
    items = r.json()
    ids = {it["id"] for it in items}
    assert ids == {"vvgosick"}
    entry = items[0]
    assert entry["status"] == "pending_calibration"
    assert "calibration_progress" in entry
    assert entry["calibration_progress"]["completed"] == 0  # 還沒標
    assert entry["calibration_progress"]["calibration_set_size"] == 0  # amber 還沒標


def test_approve_transitions_pending_to_active(client, in_memory_engine, tmp_annotators_config):
    r = client.post("/api/admin/annotators/vvgosick/approve?annotator=amber")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    # 驗 JSON 真的被寫回
    assert annotators_loader.get_annotator("vvgosick")["status"] == "active"


def test_approve_404_for_unknown(client, in_memory_engine, tmp_annotators_config):
    r = client.post("/api/admin/annotators/ghost/approve?annotator=amber")
    assert r.status_code == 404


def test_approve_409_when_already_active(client, in_memory_engine, tmp_annotators_config):
    r = client.post("/api/admin/annotators/yyslin1024/approve?annotator=amber")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# pending_calibration gate on POST /api/annotations & GET /api/audio/{id}
# ---------------------------------------------------------------------------

def test_pending_blocked_from_non_calibration_audio_post(
    client, in_memory_engine, tmp_annotators_config,
):
    """vvgosick(pending) 嘗試標一首 Amber 沒做的音檔 → 403。"""
    audio_id = _make_audio(in_memory_engine)  # amber 沒標過
    r = client.post("/api/annotations", json=_complete_payload(audio_id))
    assert r.status_code == 403
    assert "校準" in r.json()["detail"]


def test_pending_allowed_on_calibration_audio_post(
    client, in_memory_engine, tmp_annotators_config,
):
    """vvgosick(pending) 標 amber 已 is_complete 的音檔 → 通過。"""
    audio_id = _make_audio(in_memory_engine)
    _make_amber_completed_annotation(in_memory_engine, audio_id)
    r = client.post("/api/annotations", json=_complete_payload(audio_id))
    assert r.status_code == 200, r.text
    assert r.json()["is_complete"] is True


def test_archived_annotator_blocked_unconditionally(
    client, in_memory_engine, tmp_annotators_config,
):
    """archived 帳號:就算音檔在 calibration set 也 403。"""
    audio_id = _make_audio(in_memory_engine)
    _make_amber_completed_annotation(in_memory_engine, audio_id)
    r = client.post("/api/annotations", json=_complete_payload(audio_id, annotator_id="ex_user"))
    assert r.status_code == 403
    assert "封存" in r.json()["detail"]


def test_active_annotator_unaffected(client, in_memory_engine, tmp_annotators_config):
    """yyslin1024 active 對任何音檔都通行(向後相容既有 211 筆)。"""
    audio_id = _make_audio(in_memory_engine)
    r = client.post("/api/annotations", json=_complete_payload(audio_id, annotator_id="yyslin1024"))
    assert r.status_code == 200, r.text


def test_unknown_annotator_not_blocked(client, in_memory_engine, tmp_annotators_config):
    """未在 config 的 annotator_id(歷史 guest 等)— fail-open 不擋。

    刻意設計:避免突然 403 衝擊既有 33 筆 guest annotation。
    """
    audio_id = _make_audio(in_memory_engine)
    r = client.post("/api/annotations", json=_complete_payload(audio_id, annotator_id="guest"))
    assert r.status_code == 200, r.text


def test_pending_blocked_on_get_audio_detail(
    client, in_memory_engine, tmp_annotators_config,
):
    audio_id = _make_audio(in_memory_engine)
    # amber 沒標 → 非 calibration audio → vvgosick GET 應該 403
    r = client.get(f"/api/audio/{audio_id}?annotator=vvgosick")
    assert r.status_code == 403


def test_pending_allowed_on_get_audio_in_calibration_set(
    client, in_memory_engine, tmp_annotators_config,
):
    audio_id = _make_audio(in_memory_engine)
    _make_amber_completed_annotation(in_memory_engine, audio_id)
    r = client.get(f"/api/audio/{audio_id}?annotator=vvgosick")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Phase 8.5: dimension-review endpoint
# ---------------------------------------------------------------------------

def test_dimension_review_requires_admin(client, in_memory_engine, tmp_annotators_config):
    """非 admin 拿到 403。dev 模式預設 is_admin=True,測試需 override 模擬非 admin。"""
    from src import main as main_module
    from src.middleware import require_auth

    def _non_admin_user():
        return {
            "annotator_id": "vvgosick",
            "email": "vvgosick@gmail.com",
            "is_admin": False,
            "name": "老公",
        }

    main_module.app.dependency_overrides[require_auth] = _non_admin_user
    try:
        r = client.get("/api/admin/dimension-review")
        assert r.status_code == 403
    finally:
        main_module.app.dependency_overrides.pop(require_auth, None)


def test_dimension_review_returns_only_amber_confirmed_false(
    client, in_memory_engine, tmp_annotators_config,
):
    """只回 amber_confirmed:false 維度,且 amber 14 筆對齊。"""
    # 建 2 個音檔 + amber 對它們的 is_complete annotation
    audio_a = _make_audio(in_memory_engine, "A_Base Game.wav")
    audio_b = _make_audio(in_memory_engine, "B_Base Game.wav")
    _make_amber_completed_annotation(in_memory_engine, audio_a)
    _make_amber_completed_annotation(in_memory_engine, audio_b)

    r = client.get("/api/admin/dimension-review?annotator=amber")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["reference_annotator"] == "amber"
    assert data["total_amber_annotations"] == 2

    # dimensions_config.json 的 4 個 amber_confirmed:false 維度都該在
    dim_ids = {d["dim_id"] for d in data["dimensions"]}
    assert dim_ids == {
        "emotional_warmth", "tension_direction",
        "event_significance", "world_immersion",
    }

    # 每個維度都該有 2 筆 amber 的值,且從小到大
    for dim in data["dimensions"]:
        assert len(dim["items"]) == 2
        values = [it["value"] for it in dim["items"]]
        assert values == sorted(values), f"{dim['dim_id']} items 未從小到大排"
        assert dim["amber_confirmed"] is False
        assert dim["definition"], "definition 不該空"
        assert dim["low_anchor"], "low_anchor 不該空"
        assert dim["high_anchor"], "high_anchor 不該空"


def test_dimension_review_excludes_incomplete_annotations(
    client, in_memory_engine, tmp_annotators_config,
):
    """amber 的 is_complete=False annotation 不該出現在 review 列表(只看正式標)。"""
    audio_id = _make_audio(in_memory_engine)
    # 用低層 fixture 直接建一筆 is_complete=False
    import json as _json
    from src.models import Annotation
    with Session(in_memory_engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id="amber",
            valence=0.5, emotional_warmth=0.5,
            loop_capability=_json.dumps([1.0]),
            source_type=_json.dumps(["ambience"]),
            function_roles=_json.dumps(["atmosphere"]),
            genre_tag=_json.dumps([]), style_tag=_json.dumps([]),
            is_complete=False,
        ))
        s.commit()

    r = client.get("/api/admin/dimension-review?annotator=amber")
    assert r.status_code == 200
    data = r.json()
    assert data["total_amber_annotations"] == 0  # is_complete=False 不算
    for dim in data["dimensions"]:
        assert dim["items"] == []
