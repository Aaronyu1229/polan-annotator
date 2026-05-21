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
# Phase 8 follow-up: list filter must match metadata gate (no ghost items)
#
# Bug 2026-05-21: list 給 pending_calibration 看到全部 1281 檔,點到非 calibration
# 檔 → /api/audio/{id} 403 → 前端「音樂無法載入」。修法:list 按 gate 同樣規則
# 過濾,避免「看得到卻載不到」的不一致狀態。
# ---------------------------------------------------------------------------

def test_pending_list_returns_only_amber_completed(
    client, in_memory_engine, tmp_annotators_config,
):
    """vvgosick(pending) GET /api/audio 只該回 Amber 已 is_complete 的音檔。"""
    a_in_cal = _make_audio(in_memory_engine, "A_Base Game.wav")
    _make_amber_completed_annotation(in_memory_engine, a_in_cal)
    _make_audio(in_memory_engine, "B_Base Game.wav")  # amber 沒標,非 calibration
    _make_audio(in_memory_engine, "C_Base Game.wav")  # 同上

    r = client.get("/api/audio?annotator=vvgosick")
    assert r.status_code == 200
    items = r.json()
    filenames = {it["filename"] for it in items}
    assert filenames == {"A_Base Game.wav"}, (
        f"pending 應該只看到 calibration set,實際看到:{filenames}"
    )


def test_archived_list_returns_empty(
    client, in_memory_engine, tmp_annotators_config,
):
    """archived 帳號 GET /api/audio 該回空陣列(任何檔都不該看到)。"""
    a = _make_audio(in_memory_engine, "A_Base Game.wav")
    _make_amber_completed_annotation(in_memory_engine, a)  # 即使在 calibration 也不開放

    r = client.get("/api/audio?annotator=ex_user")
    assert r.status_code == 200
    assert r.json() == []


def test_active_list_unfiltered_regression(
    client, in_memory_engine, tmp_annotators_config,
):
    """active 帳號(yyslin1024) list 行為不變 — 看到所有音檔。"""
    _make_audio(in_memory_engine, "A_Base Game.wav")
    _make_audio(in_memory_engine, "B_Base Game.wav")
    _make_audio(in_memory_engine, "C_Base Game.wav")

    r = client.get("/api/audio?annotator=yyslin1024")
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_unknown_annotator_list_unfiltered(
    client, in_memory_engine, tmp_annotators_config,
):
    """歷史 annotator_id(如 guest)不在 config — fail-open 看到全部。

    跟 enforce_annotator_access 的 backward compat 行為一致(見 src/middleware.py
    的「向後相容歷史 annotator_id」分支)。
    """
    _make_audio(in_memory_engine, "A_Base Game.wav")
    _make_audio(in_memory_engine, "B_Base Game.wav")

    r = client.get("/api/audio?annotator=guest")
    assert r.status_code == 200
    assert len(r.json()) == 2


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


# ---------------------------------------------------------------------------
# Phase 9: POST /api/annotations 對 pending 回 calibration_feedback
# ---------------------------------------------------------------------------

def test_post_returns_calibration_feedback_for_pending_on_calibration_audio(
    client, in_memory_engine, tmp_annotators_config,
):
    """vvgosick(pending) 標 amber 已 done 的音檔 → response 含 calibration_feedback。"""
    audio_id = _make_audio(in_memory_engine)
    _make_amber_completed_annotation(in_memory_engine, audio_id)  # amber 全標 0.5
    payload = _complete_payload(audio_id)  # vvgosick valence 0.7 (delta 0.2 yellow)
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "calibration_feedback" in data
    # 7 個 human 維度都該有 feedback
    expected_dims = {
        "valence", "arousal", "emotional_warmth", "tension_direction",
        "temporal_position", "event_significance", "world_immersion",
    }
    assert set(data["calibration_feedback"].keys()) == expected_dims
    # 每個值是合法 color
    for color in data["calibration_feedback"].values():
        assert color in {"green", "yellow", "red"}
    # ⚠️ 不該洩露 amber 的具體值
    assert "reference_values" not in data
    assert "amber_values" not in data


def test_post_no_calibration_feedback_for_active_annotator(
    client, in_memory_engine, tmp_annotators_config,
):
    """yyslin1024 active 就算標 calibration audio,response 也不該有 calibration_feedback。"""
    audio_id = _make_audio(in_memory_engine)
    _make_amber_completed_annotation(in_memory_engine, audio_id)
    payload = _complete_payload(audio_id, annotator_id="yyslin1024")
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200, r.text
    assert "calibration_feedback" not in r.json()


def test_post_no_calibration_feedback_for_unknown_annotator(
    client, in_memory_engine, tmp_annotators_config,
):
    """歷史 guest 等未在 config 的 id 也不該收到 feedback。"""
    audio_id = _make_audio(in_memory_engine)
    _make_amber_completed_annotation(in_memory_engine, audio_id)
    payload = _complete_payload(audio_id, annotator_id="guest")
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200, r.text
    assert "calibration_feedback" not in r.json()


# ---------------------------------------------------------------------------
# Phase 9: GET /api/calibration/report
# ---------------------------------------------------------------------------

def test_report_endpoint_returns_structured_data(client, in_memory_engine, tmp_annotators_config):
    """report endpoint 對 vvgosick 該回 dimensions / calibration_progress。"""
    audio_id = _make_audio(in_memory_engine)
    _make_amber_completed_annotation(in_memory_engine, audio_id)
    # vvgosick 標一筆
    client.post("/api/annotations", json=_complete_payload(audio_id))

    r = client.get("/api/calibration/report?annotator=vvgosick")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["annotator"] == "vvgosick"
    assert data["is_reference"] is False
    assert data["calibration_progress"] == "1/1"
    # dimensions 是 list，valence 在其中
    dim_names = [d["name"] for d in data["dimensions"]]
    assert "valence" in dim_names


def test_report_endpoint_for_reference_returns_is_reference(client, in_memory_engine, tmp_annotators_config):
    r = client.get("/api/calibration/report?annotator=amber")
    assert r.status_code == 200
    assert r.json()["is_reference"] is True


# ---------------------------------------------------------------------------
# Phase 10: AudioFile gold lock endpoints
# ---------------------------------------------------------------------------

def _make_two_complete_annotations(engine, audio_id: str, valence_a=0.5, valence_b=0.5):
    """模擬 2 位 annotator 都已 is_complete 標,給 gold prereq 通關。"""
    for ann_id, valence in [("amber", valence_a), ("yyslin1024", valence_b)]:
        with Session(engine) as s:
            s.add(Annotation(
                audio_file_id=audio_id, annotator_id=ann_id,
                valence=valence, arousal=0.5, emotional_warmth=0.5,
                tension_direction=0.5, temporal_position=0.5,
                event_significance=0.5, world_immersion=0.5,
                loop_capability=json.dumps([1.0]),
                source_type=json.dumps(["ambience"]),
                function_roles=json.dumps(["atmosphere"]),
                genre_tag=json.dumps([]), style_tag=json.dumps([]),
                is_complete=True,
            ))
            s.commit()


def test_lock_gold_success_when_prereq_met(client, in_memory_engine, tmp_annotators_config):
    audio_id = _make_audio(in_memory_engine)
    _make_two_complete_annotations(in_memory_engine, audio_id, 0.5, 0.55)  # spread 0.05 OK
    r = client.post(f"/api/admin/audio/{audio_id}/lock_gold?annotator=amber")
    assert r.status_code == 200, r.text
    assert r.json()["is_gold_locked"] is True
    assert r.json()["gold_locked_by"]


def test_lock_gold_409_when_only_one_annotator(client, in_memory_engine, tmp_annotators_config):
    audio_id = _make_audio(in_memory_engine)
    # 只有 amber 一個人標
    with Session(in_memory_engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id="amber",
            valence=0.5, arousal=0.5, emotional_warmth=0.5,
            tension_direction=0.5, temporal_position=0.5,
            event_significance=0.5, world_immersion=0.5,
            loop_capability=json.dumps([1.0]),
            source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]),
            genre_tag=json.dumps([]), style_tag=json.dumps([]),
            is_complete=True,
        )); s.commit()
    r = client.post(f"/api/admin/audio/{audio_id}/lock_gold?annotator=amber")
    assert r.status_code == 409
    body = r.json()
    assert "2 位" in str(body["detail"])


def test_lock_gold_409_when_spread_too_wide(client, in_memory_engine, tmp_annotators_config):
    audio_id = _make_audio(in_memory_engine)
    _make_two_complete_annotations(in_memory_engine, audio_id, 0.1, 0.9)  # spread 0.8 > 0.20
    r = client.post(f"/api/admin/audio/{audio_id}/lock_gold?annotator=amber")
    assert r.status_code == 409
    assert "spread" in str(r.json()["detail"]).lower()


def test_unlock_gold_reverts_state(client, in_memory_engine, tmp_annotators_config):
    audio_id = _make_audio(in_memory_engine)
    _make_two_complete_annotations(in_memory_engine, audio_id, 0.5, 0.55)
    client.post(f"/api/admin/audio/{audio_id}/lock_gold?annotator=amber")
    r = client.post(f"/api/admin/audio/{audio_id}/unlock_gold?annotator=amber")
    assert r.status_code == 200, r.text
    assert r.json()["is_gold_locked"] is False


def test_audio_status_summary_endpoint(client, in_memory_engine, tmp_annotators_config):
    """summary endpoint 該回 5 種狀態的 count + total。"""
    _make_audio(in_memory_engine, "A1_X.wav")  # untouched
    a2 = _make_audio(in_memory_engine, "A2_X.wav")
    with Session(in_memory_engine) as s:
        s.add(Annotation(
            audio_file_id=a2, annotator_id="amber",
            valence=0.5, arousal=0.5, emotional_warmth=0.5,
            tension_direction=0.5, temporal_position=0.5,
            event_significance=0.5, world_immersion=0.5,
            loop_capability=json.dumps([1.0]),
            source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]),
            genre_tag=json.dumps([]), style_tag=json.dumps([]),
            is_complete=True,
        )); s.commit()

    r = client.get("/api/admin/audio_status_summary?annotator=amber")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2
    assert data["untouched"] == 1
    assert data["draft"] == 1


# ---------------------------------------------------------------------------
# Phase 11: reconciliation endpoints
# ---------------------------------------------------------------------------

def test_reconcile_list_returns_only_cross_annotated(
    client, in_memory_engine, tmp_annotators_config,
):
    """reconcile list 只回 status=cross_annotated 的 audio,不該含 lockable / gold / draft / untouched。"""
    # 1 untouched
    _make_audio(in_memory_engine, "U_X.wav")
    # 1 draft
    a_draft = _make_audio(in_memory_engine, "D_X.wav")
    _make_amber_completed_annotation(in_memory_engine, a_draft)
    # 1 cross_annotated (wide spread)
    a_cross = _make_audio(in_memory_engine, "C_X.wav")
    _make_two_complete_annotations(in_memory_engine, a_cross, 0.1, 0.9)
    # 1 lockable (tight spread) — 不該出現
    a_lock = _make_audio(in_memory_engine, "L_X.wav")
    _make_two_complete_annotations(in_memory_engine, a_lock, 0.5, 0.55)

    r = client.get("/api/admin/reconcile/list?annotator=amber")
    assert r.status_code == 200, r.text
    items = r.json()
    filenames = [it["filename"] for it in items]
    assert filenames == ["C_X.wav"]  # 只 cross_annotated
    assert items[0]["max_spread_dim"] == "valence"
    assert items[0]["max_spread_value"] == pytest.approx(0.8)
    assert "amber" in items[0]["annotators"]
    assert items[0]["amber_already_annotated"] is True


def test_reconcile_list_sorts_by_max_spread_desc(
    client, in_memory_engine, tmp_annotators_config,
):
    """max spread 大的優先。"""
    # 3 個 cross_annotated,不同 spread
    a1 = _make_audio(in_memory_engine, "A1_X.wav")
    _make_two_complete_annotations(in_memory_engine, a1, 0.1, 0.4)  # spread 0.3
    a2 = _make_audio(in_memory_engine, "A2_X.wav")
    _make_two_complete_annotations(in_memory_engine, a2, 0.1, 0.9)  # spread 0.8
    a3 = _make_audio(in_memory_engine, "A3_X.wav")
    _make_two_complete_annotations(in_memory_engine, a3, 0.3, 0.6)  # spread 0.3

    r = client.get("/api/admin/reconcile/list?annotator=amber")
    items = r.json()
    spreads = [it["max_spread_value"] for it in items]
    assert spreads == sorted(spreads, reverse=True)
    assert items[0]["filename"] == "A2_X.wav"  # 最大 spread 在最前


def test_reconcile_detail_returns_all_annotations(
    client, in_memory_engine, tmp_annotators_config,
):
    audio_id = _make_audio(in_memory_engine, "R_X.wav")
    _make_two_complete_annotations(in_memory_engine, audio_id, 0.3, 0.7)
    r = client.get(f"/api/admin/reconcile/{audio_id}?annotator=amber")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["audio"]["filename"] == "R_X.wav"
    assert len(data["annotations"]) == 2
    annotator_ids = {a["annotator_id"] for a in data["annotations"]}
    assert annotator_ids == {"amber", "yyslin1024"}
    assert data["status"] == "cross_annotated"


def test_reconcile_detail_404_for_missing_audio(
    client, in_memory_engine, tmp_annotators_config,
):
    r = client.get("/api/admin/reconcile/non-existent-id?annotator=amber")
    assert r.status_code == 404


def test_reconcile_list_requires_admin(client, in_memory_engine, tmp_annotators_config):
    from src import main as main_module
    from src.middleware import require_auth

    def _non_admin_user():
        return {"annotator_id": "vvgosick", "email": "x", "is_admin": False, "name": None}

    main_module.app.dependency_overrides[require_auth] = _non_admin_user
    try:
        r = client.get("/api/admin/reconcile/list")
        assert r.status_code == 403
    finally:
        main_module.app.dependency_overrides.pop(require_auth, None)


def test_reconcile_save_via_annotations_post_updates_amber(
    client, in_memory_engine, tmp_annotators_config,
):
    """仲裁儲存走 POST /api/annotations(annotator_id=amber)— 該 path 已存在,只驗 flow。"""
    audio_id = _make_audio(in_memory_engine, "S_X.wav")
    _make_two_complete_annotations(in_memory_engine, audio_id, 0.2, 0.8)  # 仲裁前 spread 0.6

    # Amber 仲裁後寫一筆覆蓋
    payload = _complete_payload(audio_id, annotator_id="amber")
    payload["valence"] = 0.5  # Amber 決定 0.5
    r = client.post("/api/annotations", json=payload)
    assert r.status_code == 200, r.text

    # 驗 reconcile detail 該回 3 筆 annotation(amber + yyslin1024 + 上面 setup 的 amber 已被改)
    # 注意:_make_two_complete_annotations 已建 amber row,所以這 POST 是 update upsert
    r = client.get(f"/api/admin/reconcile/{audio_id}?annotator=amber")
    data = r.json()
    amber_ann = next(a for a in data["annotations"] if a["annotator_id"] == "amber")
    assert amber_ann["valence"] == 0.5


# ---------------------------------------------------------------------------
# Phase 12-A: lockable list endpoint
# ---------------------------------------------------------------------------

def test_lockable_list_returns_only_lockable(
    client, in_memory_engine, tmp_annotators_config,
):
    """只回 status=lockable,不該含 cross / gold / draft。"""
    # 1 lockable (tight spread)
    a_lock = _make_audio(in_memory_engine, "L_X.wav")
    _make_two_complete_annotations(in_memory_engine, a_lock, 0.5, 0.55)
    # 1 cross (wide spread) — 不該出現
    a_cross = _make_audio(in_memory_engine, "C_X.wav")
    _make_two_complete_annotations(in_memory_engine, a_cross, 0.1, 0.9)
    # 1 draft — 不該出現
    a_draft = _make_audio(in_memory_engine, "D_X.wav")
    _make_amber_completed_annotation(in_memory_engine, a_draft)

    r = client.get("/api/admin/lockable/list?annotator=amber")
    assert r.status_code == 200, r.text
    items = r.json()
    filenames = [it["filename"] for it in items]
    assert filenames == ["L_X.wav"]
    assert items[0]["max_spread_value"] is not None
    assert items[0]["max_spread_value"] <= 0.20  # gold threshold


def test_lockable_list_requires_admin(client, in_memory_engine, tmp_annotators_config):
    from src import main as main_module
    from src.middleware import require_auth
    main_module.app.dependency_overrides[require_auth] = lambda: {
        "annotator_id": "vvgosick", "email": "x", "is_admin": False, "name": None,
    }
    try:
        r = client.get("/api/admin/lockable/list")
        assert r.status_code == 403
    finally:
        main_module.app.dependency_overrides.pop(require_auth, None)


def test_lockable_then_lock_gold_full_flow(
    client, in_memory_engine, tmp_annotators_config,
):
    """全流程:lockable 清單看到 → POST lock_gold → 清單空。"""
    audio_id = _make_audio(in_memory_engine, "F_X.wav")
    _make_two_complete_annotations(in_memory_engine, audio_id, 0.5, 0.55)

    r = client.get("/api/admin/lockable/list?annotator=amber")
    assert len(r.json()) == 1
    r = client.post(f"/api/admin/audio/{audio_id}/lock_gold?annotator=amber")
    assert r.status_code == 200
    r = client.get("/api/admin/lockable/list?annotator=amber")
    assert len(r.json()) == 0  # 鎖完從 lockable 移到 gold


# ---------------------------------------------------------------------------
# Phase 13-B: admin HTML page auth gate
# ---------------------------------------------------------------------------

def test_admin_html_page_redirects_non_admin(client, in_memory_engine, tmp_annotators_config):
    """非 admin 開 /admin/* HTML 該 302 redirect 到 /。"""
    from src import main as main_module
    from src.middleware import require_auth

    def _non_admin():
        return {"annotator_id": "vvgosick", "email": "x", "is_admin": False, "name": None}

    main_module.app.dependency_overrides[require_auth] = _non_admin
    try:
        for path in ["/admin/lockable", "/admin/reconcile", "/admin/review-dimensions"]:
            r = client.get(path, follow_redirects=False)
            assert r.status_code == 302, f"{path} should redirect non-admin, got {r.status_code}"
            assert r.headers.get("location") == "/"
    finally:
        main_module.app.dependency_overrides.pop(require_auth, None)


def test_admin_html_page_serves_html_to_admin(client, in_memory_engine, tmp_annotators_config):
    """admin 開 /admin/* HTML 該回 200 HTML(dev mode 預設 is_admin=True)。"""
    for path in ["/admin/lockable", "/admin/reconcile", "/admin/review-dimensions"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} should serve HTML to admin"
        assert "html" in r.headers.get("content-type", "").lower()
