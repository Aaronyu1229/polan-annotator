"""Phase 8 — annotators_loader unit tests.

關鍵覆蓋:
- 合法 JSON parse + validate 通過
- 缺欄位 / 不合法 status / 不合法 profile → AnnotatorsConfigError
- list_pending_annotators / is_pending_calibration query helpers
- set_status atomic write(tmp file + rename),失敗時不留 partial
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.annotators_loader import (
    AnnotatorsConfigError,
    get_annotator,
    is_pending_calibration,
    list_pending_annotators,
    load_annotators,
    set_status,
)


def _seed(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "annotators_config.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _valid_entry(**overrides) -> dict:
    base = {
        "name": "Test User",
        "email": "test@example.com",
        "annotator_profile": "music_professional",
        "status": "active",
        "is_admin": False,
        "joined_at": "2026-05-12",
    }
    base.update(overrides)
    return base


def test_load_valid_config(tmp_path):
    path = _seed(tmp_path, {"alice": _valid_entry()})
    config = load_annotators(path)
    assert "alice" in config
    assert config["alice"]["status"] == "active"


def test_load_empty_config_raises(tmp_path):
    path = _seed(tmp_path, {})
    with pytest.raises(AnnotatorsConfigError, match="至少需要一個"):
        load_annotators(path)


def test_load_missing_field_raises(tmp_path):
    entry = _valid_entry()
    del entry["email"]
    path = _seed(tmp_path, {"alice": entry})
    with pytest.raises(AnnotatorsConfigError, match="缺必填欄位"):
        load_annotators(path)


def test_load_invalid_status_raises(tmp_path):
    path = _seed(tmp_path, {"alice": _valid_entry(status="terminated")})
    with pytest.raises(AnnotatorsConfigError, match="status"):
        load_annotators(path)


def test_load_invalid_profile_raises(tmp_path):
    path = _seed(tmp_path, {"alice": _valid_entry(annotator_profile="expert")})
    with pytest.raises(AnnotatorsConfigError, match="annotator_profile"):
        load_annotators(path)


def test_load_missing_file_raises(tmp_path):
    path = tmp_path / "nonexistent.json"
    with pytest.raises(AnnotatorsConfigError, match="找不到"):
        load_annotators(path)


def test_load_malformed_json_raises(tmp_path):
    path = tmp_path / "annotators_config.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    with pytest.raises(AnnotatorsConfigError, match="非合法 JSON"):
        load_annotators(path)


def test_list_pending_annotators(tmp_path):
    path = _seed(tmp_path, {
        "alice": _valid_entry(status="active"),
        "bob":   _valid_entry(status="pending_calibration"),
        "carol": _valid_entry(status="archived"),
        "dave":  _valid_entry(status="pending_calibration"),
    })
    pending = list_pending_annotators(path)
    ids = {p["id"] for p in pending}
    assert ids == {"bob", "dave"}


def test_is_pending_calibration_true_false_unknown(tmp_path):
    path = _seed(tmp_path, {
        "alice": _valid_entry(status="active"),
        "bob": _valid_entry(status="pending_calibration"),
    })
    assert is_pending_calibration("bob", path) is True
    assert is_pending_calibration("alice", path) is False
    assert is_pending_calibration("ghost", path) is False  # 未知 id 視為 False


def test_get_annotator_returns_none_for_unknown(tmp_path):
    path = _seed(tmp_path, {"alice": _valid_entry()})
    assert get_annotator("ghost", path) is None
    assert get_annotator("alice", path)["status"] == "active"


def test_set_status_transitions_atomic(tmp_path):
    path = _seed(tmp_path, {
        "bob": _valid_entry(status="pending_calibration"),
    })
    config = set_status("bob", "active", path)
    assert config["bob"]["status"] == "active"
    # 再讀一次驗持久化
    assert load_annotators(path)["bob"]["status"] == "active"
    # tmp file 不應遺留
    assert not (path.with_name(path.name + ".tmp")).exists()


def test_set_status_unknown_annotator_raises(tmp_path):
    path = _seed(tmp_path, {"alice": _valid_entry()})
    with pytest.raises(KeyError):
        set_status("ghost", "active", path)


def test_set_status_invalid_status_raises(tmp_path):
    path = _seed(tmp_path, {"alice": _valid_entry()})
    with pytest.raises(ValueError, match="不合法"):
        set_status("alice", "deleted", path)
