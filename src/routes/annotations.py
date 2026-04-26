"""Annotation 寫入 API：POST /api/annotations — upsert + 完整度驗證。

完整度規則（is_complete=True 才進 Phase 4 export）：
1. 10 個維度值全部在 [0, 1] 範圍內
2. source_type 在 SOURCE_TYPES 白名單
3. function_roles 是 list 且長度 >= 1
4. 任一條件不滿足 → is_complete=False（草稿半成品仍接受儲存）

回傳 next_audio_id（同 annotator 下一個未完成音檔的 id），方便前端自動導航。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from src.constants import FUNCTION_ROLES, SOURCE_TYPES
from src.db import get_session
from src.models import AudioFile, Annotation, TagSuggestion

router = APIRouter(prefix="/api", tags=["annotations"])
log = logging.getLogger("polan.routes.annotations")

CONTINUOUS_DIMENSION_FIELDS: tuple[str, ...] = (
    "valence",
    "arousal",
    "emotional_warmth",
    "tension_direction",
    "temporal_position",
    "event_significance",
    "tonal_noise_ratio",
    "spectral_density",
    "world_immersion",
)

# loop_capability 是 multi_discrete，存 list[float]，與其他連續維度分開驗證。
DIMENSION_FIELDS: tuple[str, ...] = CONTINUOUS_DIMENSION_FIELDS + ("loop_capability",)

_SOURCE_TYPE_KEYS: set[str] = {key for key, _ in SOURCE_TYPES}
_FUNCTION_ROLE_KEYS: set[str] = {key for key, _ in FUNCTION_ROLES}
_LOOP_CAPABILITY_VALUES: set[float] = {0.0, 0.5, 1.0}


class AnnotationPayload(BaseModel):
    """POST body。所有欄位 optional，方便前端在半成品狀態也能存草稿（is_complete=False）。

    draft 仍只存在 localStorage；這個 endpoint 只收正式 submit（但允許不完整）。
    """
    audio_id: str
    annotator_id: str
    valence: Optional[float] = None
    arousal: Optional[float] = None
    emotional_warmth: Optional[float] = None
    tension_direction: Optional[float] = None
    temporal_position: Optional[float] = None
    event_significance: Optional[float] = None
    # loop_capability 從單值 float 改成多選 list[float]（值限於 {0.0, 0.5, 1.0}）
    loop_capability: list[float] = Field(default_factory=list)
    tonal_noise_ratio: Optional[float] = None
    spectral_density: Optional[float] = None
    world_immersion: Optional[float] = None
    source_type: Optional[str] = None
    function_roles: list[str] = Field(default_factory=list)
    # genre_tag 從單字串改成多選 list[str]
    genre_tag: list[str] = Field(default_factory=list)
    worldview_tag: Optional[str] = None
    style_tag: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


def _check_completeness(payload: AnnotationPayload) -> tuple[bool, Optional[str]]:
    """驗證 payload — 回 (is_complete, error_msg_if_400)。

    error_msg 非 None 表示**硬性錯誤**（應回 400）：
    - 任一維度值超出 [0, 1]
    - source_type 非法 enum 值
    - function_roles 為空（必須至少選一項）

    軟性不完整（接受但 is_complete=False）：
    - 任一維度為 None
    - source_type 為 None
    """
    for field in CONTINUOUS_DIMENSION_FIELDS:
        value = getattr(payload, field)
        if value is not None and not (0.0 <= value <= 1.0):
            return False, f"維度 {field} 值 {value} 超出範圍 [0, 1]"

    # loop_capability 多選：每個值必須在合法 {0, 0.5, 1} 集合內
    invalid_loop = [v for v in payload.loop_capability if v not in _LOOP_CAPABILITY_VALUES]
    if invalid_loop:
        return False, (
            f"loop_capability 包含非法值：{invalid_loop}（合法：0.0 / 0.5 / 1.0）"
        )

    if payload.source_type is not None and payload.source_type not in _SOURCE_TYPE_KEYS:
        return False, f"source_type '{payload.source_type}' 不在合法清單"

    if len(payload.function_roles) < 1:
        return False, "function_roles 必須至少選一項"

    invalid_roles = [r for r in payload.function_roles if r not in _FUNCTION_ROLE_KEYS]
    if invalid_roles:
        return False, f"function_roles 包含非法值：{', '.join(invalid_roles)}"

    continuous_filled = all(
        getattr(payload, f) is not None for f in CONTINUOUS_DIMENSION_FIELDS
    )
    loop_filled = len(payload.loop_capability) >= 1
    has_source = payload.source_type is not None
    is_complete = continuous_filled and loop_filled and has_source
    return is_complete, None


def _touch_tag_suggestion(session: Session, field: str, value: str) -> None:
    """寫入或 +1 TagSuggestion；忽略空白字串。"""
    value = value.strip()
    if not value:
        return
    existing = session.exec(
        select(TagSuggestion).where(
            TagSuggestion.field == field,
            TagSuggestion.value == value,
        )
    ).first()
    if existing:
        existing.use_count += 1
        session.add(existing)
    else:
        session.add(TagSuggestion(field=field, value=value, use_count=1))


def _record_tag_suggestions(session: Session, payload: AnnotationPayload) -> None:
    for genre in payload.genre_tag:
        _touch_tag_suggestion(session, "genre", genre)
    if payload.worldview_tag:
        _touch_tag_suggestion(session, "worldview", payload.worldview_tag)
    for style in payload.style_tag:
        _touch_tag_suggestion(session, "style", style)


def _next_audio_id_for(session: Session, annotator_id: str, current_audio_id: str) -> Optional[str]:
    """找同 annotator 的下一個未完成音檔 id（按 game_name/game_stage 排序）。

    「未完成」= 沒有 annotation 或 is_complete=False。回 None 代表標完了。
    """
    completed_ids = set(
        session.exec(
            select(Annotation.audio_file_id).where(
                Annotation.annotator_id == annotator_id,
                Annotation.is_complete == True,  # noqa: E712
            )
        ).all()
    )
    audios = session.exec(
        select(AudioFile).order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()
    ids_in_order = [a.id for a in audios]
    if current_audio_id not in ids_in_order:
        # 退回從頭找
        for aid in ids_in_order:
            if aid not in completed_ids:
                return aid
        return None
    # 從 current 之後開始找，找不到就 wrap 到頭
    start = ids_in_order.index(current_audio_id) + 1
    ordered = ids_in_order[start:] + ids_in_order[:start]
    for aid in ordered:
        if aid == current_audio_id:
            continue
        if aid not in completed_ids:
            return aid
    return None


@router.get("/annotations/annotators")
def list_annotators(session: Session = Depends(get_session)) -> list[str]:
    """回傳 DB 中有出現過的 annotator_id 清單（去重 + 排序）。

    給列表頁 / 標註頁的「切換標註員」dropdown 用。當 DB 還空的時回空 list，
    前端會 fallback 顯示 ['amber'] 以免 select 空白。
    """
    rows = session.exec(
        select(Annotation.annotator_id).distinct().order_by(Annotation.annotator_id)
    ).all()
    return [r for r in rows if r]


@router.post("/annotations")
def upsert_annotation(
    payload: AnnotationPayload,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    audio = session.get(AudioFile, payload.audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔：{payload.audio_id}")

    is_complete, error = _check_completeness(payload)
    if error:
        raise HTTPException(status_code=400, detail=error)

    existing = session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == payload.audio_id,
            Annotation.annotator_id == payload.annotator_id,
        )
    ).first()

    from src.models import _utcnow  # noqa: PLC0415 — 避免循環 import
    now = _utcnow()

    if existing:
        for field in CONTINUOUS_DIMENSION_FIELDS:
            setattr(existing, field, getattr(payload, field))
        existing.loop_capability = json.dumps(payload.loop_capability)
        existing.source_type = payload.source_type
        existing.function_roles = json.dumps(payload.function_roles)
        existing.genre_tag = json.dumps(payload.genre_tag)
        existing.worldview_tag = payload.worldview_tag
        existing.style_tag = json.dumps(payload.style_tag)
        existing.notes = payload.notes
        existing.is_complete = is_complete
        existing.updated_at = now
        session.add(existing)
        annotation_id = existing.id
    else:
        ann = Annotation(
            audio_file_id=payload.audio_id,
            annotator_id=payload.annotator_id,
            valence=payload.valence,
            arousal=payload.arousal,
            emotional_warmth=payload.emotional_warmth,
            tension_direction=payload.tension_direction,
            temporal_position=payload.temporal_position,
            event_significance=payload.event_significance,
            loop_capability=json.dumps(payload.loop_capability),
            tonal_noise_ratio=payload.tonal_noise_ratio,
            spectral_density=payload.spectral_density,
            world_immersion=payload.world_immersion,
            source_type=payload.source_type,
            function_roles=json.dumps(payload.function_roles),
            genre_tag=json.dumps(payload.genre_tag),
            worldview_tag=payload.worldview_tag,
            style_tag=json.dumps(payload.style_tag),
            notes=payload.notes,
            is_complete=is_complete,
            created_at=now,
            updated_at=now,
        )
        session.add(ann)
        session.flush()  # 拿到 id
        annotation_id = ann.id

    _record_tag_suggestions(session, payload)
    session.commit()

    next_id = _next_audio_id_for(session, payload.annotator_id, payload.audio_id)

    return {
        "annotation_id": annotation_id,
        "is_complete": is_complete,
        "next_audio_id": next_id,
    }
