"""BGM 維度錨點變體測試。

驗 (1) 4 個感受維度有 bgm block；(2) get_bgm_view 回 BGM 版；(3) 無 bgm 維度 fallback；
(4) Amber 的 SFX 文字未被改（守 CLAUDE.md #8）；(5) bgm block 缺欄 fail-fast。
"""
import pytest

from src.alignment_compare import BGM_DIMENSIONS
from src.dimensions_loader import (
    DimensionsConfigError,
    _validate,
    get_bgm_view,
    get_dimension,
    load_dimensions,
)


def test_config_still_loads():
    assert load_dimensions()  # 不拋＝JSON + bgm 驗證都過


def test_four_feel_dimensions_have_bgm_block():
    for dim_id in BGM_DIMENSIONS:
        assert "bgm" in get_dimension(dim_id), f"{dim_id} 缺 bgm block"


def test_get_bgm_view_returns_bgm_variant():
    v = get_bgm_view("emotional_warmth")
    assert v["display_name"] == "柔烈度"
    assert v["mid_anchor"] == "柔中帶亮"
    assert "柔暖" in v["client_question"]


def test_get_bgm_view_falls_back_for_non_bgm_dim():
    v = get_bgm_view("arousal")  # arousal 在 BGM 模式隱藏、無 bgm block
    assert v["display_name"] == get_dimension("arousal")["label_zh"]
    assert v["mid_anchor"] is None
    assert v["client_question"] is None


def test_amber_sfx_anchors_unchanged():
    # 規則 #8：不得改 Amber 的 SFX 定義文字
    assert get_dimension("valence")["low_anchor"].startswith("0.3-0.4: Base Game")
    assert get_dimension("emotional_warmth")["definition"].startswith("情緒的「溫度」")


def test_incomplete_bgm_block_fails_fast():
    bad = {
        "d": {
            "label_zh": "x", "category": "emotion", "type": "continuous",
            "range": [0.0, 1.0], "definition": "x",
            "low_anchor": "a", "high_anchor": "b", "amber_confirmed": True,
            "bgm": {"display_name": "x"},  # 缺 mid_anchor 等
        }
    }
    with pytest.raises(DimensionsConfigError, match="bgm block 缺少"):
        _validate(bad)
