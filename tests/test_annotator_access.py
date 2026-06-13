"""accessible_audio_ids — 單一 source of truth：某標註員可見/可標的音檔集合。

把原本散在 list_audio / enforce_annotator_access 的 pending_calibration / archived
gating 收斂成一個 helper，並驗證 compute_progress 與 _next_audio_id_for 都套用同一
過濾，避免「list 100% / 進度卡 71%」這種不一致再發生。
"""
from datetime import datetime, timezone
from pathlib import Path
import json

from sqlmodel import Session

from src import annotators_loader
from src.annotator_access import accessible_audio_ids, calibration_set_ids
from src.models import Annotation, AudioFile
from src.stats import compute_progress
from src.routes.annotations import _next_audio_id_for


def _entry(status: str, role: str | None = "audience") -> dict:
    return {
        "name": "X", "email": "x@example.com",
        "annotator_profile": "general_audience", "status": status,
        "is_admin": False, "joined_at": "2026-01-01", "role": role,
    }


def _seed_config(tmp_path: Path, monkeypatch) -> None:
    payload = {
        "amber": _entry("active", "creator"),
        "vic": _entry("pending_calibration", "audience"),
        "lin": _entry("active", "industry"),
        "ex": _entry("archived", None),
    }
    p = tmp_path / "annotators_config.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(annotators_loader, "_CONFIG_PATH", p)


def _add_audio(engine, fn: str) -> str:
    with Session(engine) as s:
        a = AudioFile(filename=fn, game_name="G", game_stage="S")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def _add_annotation(engine, audio_id: str, annotator_id: str, *, is_complete: bool) -> None:
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=annotator_id,
            is_complete=is_complete,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            function_roles='["atmosphere"]', style_tag="[]",
        ))
        s.commit()


# ── helper：可見集合 ──────────────────────────────────────────────────

def test_active_annotator_unrestricted(in_memory_engine, tmp_path, monkeypatch):
    _seed_config(tmp_path, monkeypatch)
    with Session(in_memory_engine) as s:
        assert accessible_audio_ids(s, "amber") is None
        assert accessible_audio_ids(s, "lin") is None


def test_unknown_annotator_unrestricted_for_backward_compat(in_memory_engine, tmp_path, monkeypatch):
    _seed_config(tmp_path, monkeypatch)
    with Session(in_memory_engine) as s:
        assert accessible_audio_ids(s, "guest") is None


def test_archived_sees_nothing(in_memory_engine, tmp_path, monkeypatch):
    _seed_config(tmp_path, monkeypatch)
    with Session(in_memory_engine) as s:
        assert accessible_audio_ids(s, "ex") == set()


def test_pending_restricted_to_amber_completed_set(in_memory_engine, tmp_path, monkeypatch):
    _seed_config(tmp_path, monkeypatch)
    a1 = _add_audio(in_memory_engine, "a1.wav")
    a2 = _add_audio(in_memory_engine, "a2.wav")
    _add_audio(in_memory_engine, "a3.wav")          # amber 沒標 → 不在集合
    _add_annotation(in_memory_engine, a1, "amber", is_complete=True)
    _add_annotation(in_memory_engine, a2, "amber", is_complete=True)
    with Session(in_memory_engine) as s:
        assert accessible_audio_ids(s, "vic") == {a1, a2}
        assert calibration_set_ids(s) == {a1, a2}


def test_pending_excludes_amber_incomplete(in_memory_engine, tmp_path, monkeypatch):
    _seed_config(tmp_path, monkeypatch)
    a1 = _add_audio(in_memory_engine, "a1.wav")
    _add_annotation(in_memory_engine, a1, "amber", is_complete=False)  # 草稿不算校準集
    with Session(in_memory_engine) as s:
        assert accessible_audio_ids(s, "vic") == set()


# ── compute_progress 套用同一過濾 ────────────────────────────────────

def test_progress_pending_scoped_to_calibration_set(in_memory_engine, tmp_path, monkeypatch):
    """pending 的進度分母 = 校準集大小，不是全資料集（修「891/1250 vs 55/55」矛盾）。"""
    _seed_config(tmp_path, monkeypatch)
    a1 = _add_audio(in_memory_engine, "a1.wav")
    a2 = _add_audio(in_memory_engine, "a2.wav")
    _add_audio(in_memory_engine, "a3.wav")          # 校準集外
    _add_annotation(in_memory_engine, a1, "amber", is_complete=True)
    _add_annotation(in_memory_engine, a2, "amber", is_complete=True)
    # vic 把校準集兩首都標完，外加一筆校準集外（理論上不該存在，但驗證不被計入）
    _add_annotation(in_memory_engine, a1, "vic", is_complete=True)
    _add_annotation(in_memory_engine, a2, "vic", is_complete=True)
    with Session(in_memory_engine) as s:
        r = compute_progress(s, "vic", tz_name="UTC")
    assert r.total_audio_files == 2          # 校準集大小，非 3
    assert r.completed_count == 2
    assert r.completion_rate == 1.0


def test_progress_active_unchanged_full_dataset(in_memory_engine, tmp_path, monkeypatch):
    _seed_config(tmp_path, monkeypatch)
    a1 = _add_audio(in_memory_engine, "a1.wav")
    _add_audio(in_memory_engine, "a2.wav")
    _add_annotation(in_memory_engine, a1, "lin", is_complete=True)
    with Session(in_memory_engine) as s:
        r = compute_progress(s, "lin", tz_name="UTC")
    assert r.total_audio_files == 2          # active → 全資料集
    assert r.completed_count == 1


# ── _next_audio_id_for 套用同一過濾 ──────────────────────────────────

def test_next_audio_pending_stays_in_calibration_set(in_memory_engine, tmp_path, monkeypatch):
    """pending 的「儲存並下一個」不可導向校準集外（否則開啟/儲存會 403）。"""
    _seed_config(tmp_path, monkeypatch)
    a1 = _add_audio(in_memory_engine, "a1.wav")
    a2 = _add_audio(in_memory_engine, "a2.wav")
    out = _add_audio(in_memory_engine, "a3.wav")    # 校準集外
    _add_annotation(in_memory_engine, a1, "amber", is_complete=True)
    _add_annotation(in_memory_engine, a2, "amber", is_complete=True)
    _add_annotation(in_memory_engine, a1, "vic", is_complete=True)  # a1 已標
    with Session(in_memory_engine) as s:
        nxt = _next_audio_id_for(s, "vic", a1)
    assert nxt == a2          # 下一個校準集內未標的
    assert nxt != out


def test_next_audio_pending_none_when_calibration_done(in_memory_engine, tmp_path, monkeypatch):
    _seed_config(tmp_path, monkeypatch)
    a1 = _add_audio(in_memory_engine, "a1.wav")
    _add_audio(in_memory_engine, "a2.wav")          # 校準集外，不該被導向
    _add_annotation(in_memory_engine, a1, "amber", is_complete=True)
    _add_annotation(in_memory_engine, a1, "vic", is_complete=True)
    with Session(in_memory_engine) as s:
        assert _next_audio_id_for(s, "vic", a1) is None
