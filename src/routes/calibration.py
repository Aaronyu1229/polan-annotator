"""Phase 3 校準模式 backend。

URL 設計：
    /calibration                       — HTML 校準首頁，列待校準音檔
    /calibration/{audio_id}            — HTML 校準標註頁（重用 annotate.html）
    /calibration/compare/{audio_id}    — HTML 比對結果頁
    /api/calibration/queue?annotator=  — 待校準清單（reference 已標、自己未標）
    /api/calibration/reference/{id}    — reference annotator 的某筆 annotation

reference annotator 暫時 hardcode 為 amber；未來改 config。
重用 annotate.html / annotate.js — 前端用 URL pathname 判斷模式。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from src.db import get_session
from src.models import Annotation, AudioFile

REFERENCE_ANNOTATOR_ID = "amber"  # TODO: 未來改 config

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "static"

api_router = APIRouter(prefix="/api/calibration", tags=["calibration"])
page_router = APIRouter(tags=["calibration-pages"])

log = logging.getLogger("polan.routes.calibration")


def _decode_list(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _annotation_to_dict(ann: Annotation) -> dict[str, Any]:
    """同 routes/audio._annotation_to_dict 的格式（前端統一處理）。"""
    return {
        "id": ann.id,
        "annotator_id": ann.annotator_id,
        "valence": ann.valence,
        "arousal": ann.arousal,
        "emotional_warmth": ann.emotional_warmth,
        "tension_direction": ann.tension_direction,
        "temporal_position": ann.temporal_position,
        "event_significance": ann.event_significance,
        "loop_capability": _decode_list(ann.loop_capability),
        "tonal_noise_ratio": ann.tonal_noise_ratio,
        "spectral_density": ann.spectral_density,
        "world_immersion": ann.world_immersion,
        "source_type": _decode_list(ann.source_type),
        "function_roles": _decode_list(ann.function_roles),
        "genre_tag": _decode_list(ann.genre_tag),
        "worldview_tag": ann.worldview_tag,
        "style_tag": _decode_list(ann.style_tag),
        "notes": ann.notes,
        "is_complete": ann.is_complete,
        "updated_at": ann.updated_at.isoformat() if ann.updated_at else None,
    }


# ─── API ──────────────────────────────────────────────────

@api_router.get("/queue")
def calibration_queue(
    annotator: str = Query(..., description="目前校準中的 annotator_id"),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """回 reference 已 is_complete 標過、{annotator} 還沒 is_complete 標的 audio 清單。

    依 (game_name, game_stage) 排序。若 annotator 就是 reference 自己，回空 list。
    """
    if annotator == REFERENCE_ANNOTATOR_ID:
        return []

    ref_audio_ids = set(
        session.exec(
            select(Annotation.audio_file_id).where(
                Annotation.annotator_id == REFERENCE_ANNOTATOR_ID,
                Annotation.is_complete == True,  # noqa: E712
            )
        ).all()
    )
    if not ref_audio_ids:
        return []

    self_audio_ids = set(
        session.exec(
            select(Annotation.audio_file_id).where(
                Annotation.annotator_id == annotator,
                Annotation.is_complete == True,  # noqa: E712
            )
        ).all()
    )
    pending_ids = ref_audio_ids - self_audio_ids
    if not pending_ids:
        return []

    audios = session.exec(
        select(AudioFile)
        .where(AudioFile.id.in_(pending_ids))  # type: ignore[attr-defined]
        .order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()
    return [
        {
            "id": a.id,
            "filename": a.filename,
            "game_name": a.game_name,
            "game_stage": a.game_stage,
            "is_brand_theme": a.is_brand_theme,
            "duration_sec": a.duration_sec,
        }
        for a in audios
    ]


@api_router.get("/reference/{audio_id}")
def get_reference_annotation(
    audio_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """回 reference annotator (amber) 對指定 audio 的 is_complete=True annotation。

    404 if reference 對此 audio 無 is_complete=True 紀錄。
    """
    ann = session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio_id,
            Annotation.annotator_id == REFERENCE_ANNOTATOR_ID,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).first()
    if ann is None:
        raise HTTPException(
            status_code=404,
            detail=f"{REFERENCE_ANNOTATOR_ID} 對音檔 {audio_id} 尚無完整標註",
        )
    return _annotation_to_dict(ann)


# ─── HTML 頁面 ────────────────────────────────────────────

@page_router.get("/calibration", include_in_schema=False)
def calibration_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "calibration.html")


@page_router.get("/calibration/{audio_id}", include_in_schema=False)
def calibration_annotate(audio_id: str) -> FileResponse:  # noqa: ARG001 — JS 從 path 取
    """重用 annotate.html — 前端用 URL pathname 判斷是 calibration mode。"""
    return FileResponse(STATIC_DIR / "annotate.html")


@page_router.get("/calibration/compare/{audio_id}", include_in_schema=False)
def calibration_compare(audio_id: str) -> FileResponse:  # noqa: ARG001 — JS 從 path 取
    return FileResponse(STATIC_DIR / "calibration-compare.html")
