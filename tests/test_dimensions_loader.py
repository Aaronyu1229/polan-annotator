"""dimensions_loader：happy path + fail-fast 情境。"""
import json

import pytest

from src import dimensions_loader
from src.dimensions_loader import (
    DimensionsConfigError,
    get_dimension,
    list_dimension_ids,
    load_dimensions,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    load_dimensions.cache_clear()
    yield
    load_dimensions.cache_clear()


# ─── happy path ─────────────────────────────────────────────

def test_loads_all_ten_dimensions():
    config = load_dimensions()
    assert len(config) == 10


def test_dimension_ids_are_expected_set():
    expected = {
        "valence", "arousal", "emotional_warmth", "tension_direction",
        "temporal_position", "event_significance", "loop_capability",
        "tonal_noise_ratio", "spectral_density", "world_immersion",
    }
    assert set(list_dimension_ids()) == expected


def test_get_dimension_returns_valence_spec():
    spec = get_dimension("valence")
    assert spec["amber_confirmed"] is True
    assert spec["category"] == "emotion"
    assert spec["type"] == "continuous"
    assert spec["range"] == [0.0, 1.0]


def test_get_dimension_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        get_dimension("nonexistent_dimension")


def test_continuous_dimensions_have_valid_range():
    for dim_id in list_dimension_ids():
        spec = get_dimension(dim_id)
        if spec["type"] == "continuous":
            low, high = spec["range"]
            assert low < high, f"{dim_id}: range {spec['range']} not low<high"


def test_loop_capability_is_multi_discrete_three_options():
    spec = get_dimension("loop_capability")
    assert spec["type"] == "multi_discrete"
    assert spec["options"] == [0.0, 0.5, 1.0]


def test_temporal_position_has_filename_mapping():
    """Phase 2 filename auto-suggest 依賴這個 mapping。"""
    spec = get_dimension("temporal_position")
    assert spec["auto_suggest_from_filename"] is True
    assert "Base Game" in spec["filename_mapping"]
    assert "Winning Panel" in spec["filename_mapping"]


# ─── fail-fast validation ───────────────────────────────────

def _write_config(tmp_path, payload: str) -> None:
    """把字串寫成 config，並把 loader 的 _CONFIG_PATH 指過去。"""
    path = tmp_path / "dimensions_config.json"
    path.write_text(payload, encoding="utf-8")
    dimensions_loader._CONFIG_PATH = path  # 測試專用；每個 test 都會 reset cache


def test_empty_config_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dimensions_loader, "_CONFIG_PATH", tmp_path / "empty.json"
    )
    (tmp_path / "empty.json").write_text("{}", encoding="utf-8")
    load_dimensions.cache_clear()
    with pytest.raises(DimensionsConfigError, match="至少需要一個維度"):
        load_dimensions()


def test_invalid_json_raises(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(dimensions_loader, "_CONFIG_PATH", bad)
    load_dimensions.cache_clear()
    with pytest.raises(DimensionsConfigError, match="非合法 JSON"):
        load_dimensions()


def test_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dimensions_loader, "_CONFIG_PATH", tmp_path / "nope.json"
    )
    load_dimensions.cache_clear()
    with pytest.raises(DimensionsConfigError, match="找不到"):
        load_dimensions()


def test_missing_required_field_raises(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"x": {"label_zh": "x"}}), encoding="utf-8")
    monkeypatch.setattr(dimensions_loader, "_CONFIG_PATH", bad)
    load_dimensions.cache_clear()
    with pytest.raises(DimensionsConfigError, match="缺少必要欄位"):
        load_dimensions()


def test_invalid_category_raises(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "x": {
            "label_zh": "x", "category": "invalid", "type": "continuous",
            "definition": "d", "low_anchor": "l", "high_anchor": "h",
            "amber_confirmed": True, "range": [0, 1],
        }
    }), encoding="utf-8")
    monkeypatch.setattr(dimensions_loader, "_CONFIG_PATH", bad)
    load_dimensions.cache_clear()
    with pytest.raises(DimensionsConfigError, match="category"):
        load_dimensions()


def test_continuous_without_range_raises(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "x": {
            "label_zh": "x", "category": "emotion", "type": "continuous",
            "definition": "d", "low_anchor": "l", "high_anchor": "h",
            "amber_confirmed": True,  # 沒有 range
        }
    }), encoding="utf-8")
    monkeypatch.setattr(dimensions_loader, "_CONFIG_PATH", bad)
    load_dimensions.cache_clear()
    with pytest.raises(DimensionsConfigError, match="range"):
        load_dimensions()


def test_discrete_without_options_raises(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "x": {
            "label_zh": "x", "category": "function", "type": "discrete",
            "definition": "d", "low_anchor": "l", "high_anchor": "h",
            "amber_confirmed": True,  # 沒有 options
        }
    }), encoding="utf-8")
    monkeypatch.setattr(dimensions_loader, "_CONFIG_PATH", bad)
    load_dimensions.cache_clear()
    with pytest.raises(DimensionsConfigError, match="options"):
        load_dimensions()
