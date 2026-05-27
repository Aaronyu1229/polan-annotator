"""role 欄位驗證 + 反查。role 與 profile 解耦（獨立欄位）。"""
from __future__ import annotations

import json
import pytest

from src.annotators_loader import (
    AnnotatorsConfigError,
    annotator_id_for_role,
    get_role,
    load_annotators,
)


def _write_config(tmp_path, mapping):
    p = tmp_path / "annotators_config.json"
    p.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return p


def _spec(role):
    return {
        "name": "X", "email": "x@x.com", "annotator_profile": "general_audience",
        "status": "active", "is_admin": False, "joined_at": "2026-01-01", "role": role,
    }


def test_valid_roles_load(tmp_path):
    cfg = _write_config(tmp_path, {
        "amber": _spec("creator"), "y": _spec("industry"), "v": _spec("audience"),
    })
    loaded = load_annotators(cfg)
    assert loaded["amber"]["role"] == "creator"


def test_null_role_allowed(tmp_path):
    cfg = _write_config(tmp_path, {"guest": {**_spec("audience"), "role": None}})
    assert load_annotators(cfg)["guest"]["role"] is None


def test_missing_role_allowed_defaults_none(tmp_path):
    spec = _spec("creator")
    del spec["role"]
    cfg = _write_config(tmp_path, {"amber": spec})
    assert get_role("amber", cfg) is None


def test_invalid_role_raises(tmp_path):
    cfg = _write_config(tmp_path, {"x": _spec("expert")})  # not a valid role
    with pytest.raises(AnnotatorsConfigError):
        load_annotators(cfg)


def test_annotator_id_for_role(tmp_path):
    cfg = _write_config(tmp_path, {
        "amber": _spec("creator"), "y": _spec("industry"),
    })
    assert annotator_id_for_role("creator", cfg) == "amber"
    assert annotator_id_for_role("audience", cfg) is None  # nobody has it


def test_duplicate_role_raises(tmp_path):
    cfg = _write_config(tmp_path, {
        "a": _spec("industry"), "b": _spec("industry"),
    })
    with pytest.raises(AnnotatorsConfigError):
        annotator_id_for_role("industry", cfg)
