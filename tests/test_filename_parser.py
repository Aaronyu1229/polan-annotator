"""parse_audio_filename 的行為測試。

覆蓋三種情境：兩段式、三段式品牌主題曲、game_name 含底線的 edge case。
"""
from src.constants import parse_audio_filename


# ── 兩段式 ──────────────────────────────────────────────

def test_two_segment_base_game():
    result = parse_audio_filename("Volcano Goddess_Base Game.wav")
    assert result == {
        "game_name": "Volcano Goddess",
        "game_stage": "Base Game",
        "is_brand_theme": False,
    }


def test_two_segment_free_game():
    result = parse_audio_filename("Treasure Dragon_Free Game.wav")
    assert result == {
        "game_name": "Treasure Dragon",
        "game_stage": "Free Game",
        "is_brand_theme": False,
    }


# ── 三段式品牌主題曲 ────────────────────────────────────

def test_brand_theme_jinxin():
    result = parse_audio_filename("Game Brand Theme Music_金鑫_AI Virtual Voice.wav")
    assert result == {
        "game_name": "Game Brand Theme Music",
        "game_stage": "金鑫 (AI Virtual Voice)",
        "is_brand_theme": True,
    }


def test_brand_theme_baolifa():
    result = parse_audio_filename("Game Brand Theme Music_寶利發_AI Virtual Voice.wav")
    assert result == {
        "game_name": "Game Brand Theme Music",
        "game_stage": "寶利發 (AI Virtual Voice)",
        "is_brand_theme": True,
    }


# ── Edge case：game_name 本身含底線 ─────────────────────

def test_game_name_contains_underscore():
    """`Wealth God_s Blessing_Free Game.wav` 要保留 game_name 內的底線。

    Phase 1 先保留原始底線（例如 "Wealth God_s Blessing"），後續 Amber 可
    考慮用另一個 display_name 欄位顯示。
    """
    result = parse_audio_filename("Wealth God_s Blessing_Free Game.wav")
    assert result == {
        "game_name": "Wealth God_s Blessing",
        "game_stage": "Free Game",
        "is_brand_theme": False,
    }


# ── Fallback：非預期格式 ────────────────────────────────

def test_fallback_unknown_stage():
    """stage 不在 KNOWN_STAGES：退回第一個底線切分。"""
    result = parse_audio_filename("Some Game_Unknown Stage.wav")
    assert result == {
        "game_name": "Some Game",
        "game_stage": "Unknown Stage",
        "is_brand_theme": False,
    }


def test_fallback_no_underscore():
    """完全沒有底線：整個 stem 當 game_name。"""
    result = parse_audio_filename("soloname.wav")
    assert result == {
        "game_name": "soloname",
        "game_stage": "",
        "is_brand_theme": False,
    }
