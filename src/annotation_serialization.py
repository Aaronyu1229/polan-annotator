"""單一 Annotation → dict 序列化（標註員詳細頁端點用）。

JSON 字串欄位（loop_capability / source_type / function_roles / genre_tag /
style_tag）decode 成 list；decode 失敗、非 list、或 None → []。
不含 audio metadata — 由呼叫端 join AudioFile 後合併。
"""
from __future__ import annotations

import json
from typing import Any

from src.models import Annotation


def _decode_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def decode_worldview_tag(raw: str | None) -> list[str]:
    """worldview_tag 向後相容解碼（單值 → 多選遷移）。

    舊資料把 worldview_tag 存成原始字串（如 "fantasy"），改多選後存 JSON list
    （如 '["fantasy"]'）。同一 column 兩種格式並存，故讀取時統一解成 list：
    - None / "" → []
    - 合法 JSON list（含空 list []）→ 該 list
    - 其餘（JSON decode 失敗，或 decode 出非 list）→ 視為單一舊值 [raw]

    與 _decode_list 的差異：_decode_list 對 decode 失敗回 []（會丟掉舊字串值），
    這個函式保留舊值，且能區分「合法空 list」與「decode 失敗」。
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [raw]
    return value if isinstance(value, list) else [raw]


def annotation_to_dict(ann: Annotation) -> dict[str, Any]:
    """回傳單筆 annotation 的標註欄位（不含 audio metadata）。"""
    return {
        "annotation_id": ann.id,
        "annotator_id": ann.annotator_id,
        "valence": ann.valence,
        "arousal": ann.arousal,
        "emotional_warmth": ann.emotional_warmth,
        "tension_direction": ann.tension_direction,
        "temporal_position": ann.temporal_position,
        "event_significance": ann.event_significance,
        "world_immersion": ann.world_immersion,
        "tonal_noise_ratio": ann.tonal_noise_ratio,
        "spectral_density": ann.spectral_density,
        "loop_capability": _decode_list(ann.loop_capability),
        "source_type": _decode_list(ann.source_type),
        "function_roles": _decode_list(ann.function_roles),
        "genre_tag": _decode_list(ann.genre_tag),
        "worldview_tag": decode_worldview_tag(ann.worldview_tag),
        "style_tag": _decode_list(ann.style_tag),
        "notes": ann.notes,
        "is_complete": ann.is_complete,
        "created_at": ann.created_at.isoformat() if ann.created_at else None,
        "updated_at": ann.updated_at.isoformat() if ann.updated_at else None,
    }
