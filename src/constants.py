"""靜態常數與檔名 parser。

這些 list 被 Phase 2 的 UI / Phase 4 的 validator 共用，統一在這裡定義。
不要把它們搬進 dimensions_config.json — 這些是「UI 選項」不是「維度定義」，本質不同。
"""
from pathlib import Path

# Layer 1：音源類型（單選）
SOURCE_TYPES: list[tuple[str, str]] = [
    ("weapon",             "武器動作 Weapon"),
    ("explosion",          "爆炸破壞 Explosion & Destruction"),
    ("impact",             "衝擊打擊 Impact & Hit"),
    ("character_vocal",    "角色發聲 Character Vocal"),
    ("dialogue_vo",        "台詞對白 Dialogue / VO"),
    ("ambience",           "環境氛圍 Ambience"),
    ("environmental",      "環境點綴 Environmental One-shot"),
    ("mechanical_vehicle", "機械載具 Mechanical & Vehicle"),
    ("creature_foley",     "生物擬音 Creature Foley"),
    ("synthetic_designed", "抽象合成 Synthetic / Designed"),
]

# Layer 2：功能角色（多選）
FUNCTION_ROLES: list[tuple[str, str]] = [
    ("ui",                "UI 介面"),
    ("gameplay_core",     "核心玩法"),
    ("reward_feedback",   "獎勵回饋"),
    ("negative_feedback", "失敗／負面回饋"),
    ("cinematic",         "過場／敘事"),
    ("musical_sfx",       "音樂化音效"),
    ("atmosphere",        "氛圍營造"),
    ("hybrid",            "混合型"),
]

# 附加離散 tag presets（Phase 2 的 combobox 起始選項）
GENRE_PRESETS: list[str] = [
    "博弈", "RPG", "恐怖", "動作", "解謎", "休閒", "競速", "策略",
]

WORLDVIEW_PRESETS: list[str] = [
    "fantasy", "scifi", "horror", "realistic", "cyberpunk",
    "asian_mythology", "casino", "racing", "cute",
]

STYLE_PRESETS: list[str] = [
    "kpop", "trap", "orchestral", "electronic",
    "chinese_traditional", "ambient", "rock", "jazz", "lofi",
]

# 已知合法的 game stage 集合，parser 判定兩段式時用
# 這 5 種是 spec 明示的全集，若資料集擴充需同步更新
KNOWN_STAGES: set[str] = {
    "Base Game",
    "Free Game",
    "Bonus Game",
    "Main Game",
    "Winning Panel",
}

_BRAND_THEME_PREFIX = "Game Brand Theme Music_"


def parse_audio_filename(filename: str) -> dict:
    """將音檔檔名拆成 game_name / game_stage / is_brand_theme。

    處理三種情境：
    1. 三段式品牌主題曲：`Game Brand Theme Music_{品牌}_AI Virtual Voice.wav`
    2. 兩段式 with 已知 stage：`{Game}_..._{Stage}.wav` — 用 rsplit + KNOWN_STAGES 判定，
       這能正確處理 game_name 內本身含底線的情況（例如 `Wealth God_s Blessing_Free Game.wav`）
    3. Fallback：若後段不在 KNOWN_STAGES，退回第一個底線切分並加 TODO 註記
    """
    stem = Path(filename).stem  # 去掉 .wav 副檔名

    # Case 1: 三段式品牌主題曲
    if stem.startswith(_BRAND_THEME_PREFIX):
        rest = stem[len(_BRAND_THEME_PREFIX):]  # e.g. "金鑫_AI Virtual Voice"
        parts = rest.split("_", 1)
        brand = parts[0] if parts and parts[0] else "Unknown"
        suffix = parts[1] if len(parts) > 1 else ""
        return {
            "game_name": "Game Brand Theme Music",
            "game_stage": f"{brand} ({suffix})" if suffix else brand,
            "is_brand_theme": True,
        }

    # Case 2: 兩段式 with 已知 stage
    # rsplit 從右邊切一次，處理 game_name 內含底線的 edge case
    if "_" in stem:
        head, tail = stem.rsplit("_", 1)
        if tail in KNOWN_STAGES:
            return {
                "game_name": head,
                "game_stage": tail,
                "is_brand_theme": False,
            }

    # Case 3: fallback — 不符合已知格式
    # TODO(aaron): 出現非預期檔名格式，parser 退回 split 並保留原檔名在 game_name
    if "_" in stem:
        head, tail = stem.split("_", 1)
        return {
            "game_name": head,
            "game_stage": tail,
            "is_brand_theme": False,
        }
    return {
        "game_name": stem,
        "game_stage": "",
        "is_brand_theme": False,
    }
