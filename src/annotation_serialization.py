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
        "worldview_tag": ann.worldview_tag,
        "style_tag": _decode_list(ann.style_tag),
        "notes": ann.notes,
        "is_complete": ann.is_complete,
        "created_at": ann.created_at.isoformat() if ann.created_at else None,
        "updated_at": ann.updated_at.isoformat() if ann.updated_at else None,
    }
