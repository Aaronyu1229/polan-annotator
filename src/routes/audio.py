"""音檔相關 API：
- GET /api/audio                 列表（含 duration 與當前 annotator 的完成狀態）
- GET /api/audio/{id}            單筆詳細（含 auto_computed、existing_annotation）
- GET /api/audio/{id}/stream     串流 .wav 給 WaveSurfer

Phase 2 的擴充：list 回傳增加 `is_annotated_by_current_annotator` + `duration_sec`；
single 回傳增加 `auto_computed` dict 與 `existing_annotation`。
音訊分析結果會 cache 到 DB（首次開啟時算，之後直接讀）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from src.audio_analysis import AUDIO_DIR, ensure_cached
from src.db import get_session
from src.models import AudioFile, Annotation

router = APIRouter(prefix="/api", tags=["audio"])
log = logging.getLogger("polan.routes.audio")


def _annotation_to_dict(ann: Annotation) -> dict[str, Any]:
    """把 Annotation row 轉成前端 prefill 用的 dict。多選欄位 JSON-decode。"""
    def _decode_list(s: Optional[str]) -> list:
        if not s:
            return []
        try:
            value = json.loads(s)
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []

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
        "source_type": ann.source_type,
        "function_roles": _decode_list(ann.function_roles),
        "genre_tag": _decode_list(ann.genre_tag),
        "worldview_tag": ann.worldview_tag,
        "style_tag": _decode_list(ann.style_tag),
        "notes": ann.notes,
        "is_complete": ann.is_complete,
        "updated_at": ann.updated_at.isoformat() if ann.updated_at else None,
    }


@router.get("/audio")
def list_audio(
    annotator: Optional[str] = Query(default=None, description="當前標註員 id，未帶則全部顯示未標"),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """回傳所有音檔，含 duration 與 is_annotated_by_current_annotator 旗標。

    只有對應 annotator 已存在且 is_complete=True 的 record 才算「已標」 —
    半成品（is_complete=False）仍視為未標，使列表頁的 ✓ 旗標精準反映「完成」狀態。
    """
    audios = session.exec(
        select(AudioFile).order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()

    completed_audio_ids: set[str] = set()
    if annotator:
        completed = session.exec(
            select(Annotation.audio_file_id).where(
                Annotation.annotator_id == annotator,
                Annotation.is_complete == True,  # noqa: E712 — SQLModel 不允許 is True
            )
        ).all()
        completed_audio_ids = set(completed)

    return [
        {
            "id": a.id,
            "filename": a.filename,
            "game_name": a.game_name,
            "game_stage": a.game_stage,
            "is_brand_theme": a.is_brand_theme,
            "duration_sec": a.duration_sec,
            "is_annotated_by_current_annotator": a.id in completed_audio_ids,
        }
        for a in audios
    ]


@router.get("/audio/{audio_id}")
def get_audio(
    audio_id: str,
    annotator: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """回單一音檔詳細 — 含 auto_computed 建議值與當前 annotator 既有標註（if any）。

    首次開啟會觸發 librosa 分析並 cache 到 DB，之後讀 cache。
    """
    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔：{audio_id}")

    # 首次開啟時算 librosa 並 cache；失敗時 auto 欄位仍為 None，UI 顯示 N/A
    try:
        audio = ensure_cached(session, audio)
    except Exception as e:  # noqa: BLE001
        log.warning("ensure_cached 失敗（%s）：%s", audio.filename, e)

    existing_annotation: Optional[dict[str, Any]] = None
    if annotator:
        ann = session.exec(
            select(Annotation).where(
                Annotation.audio_file_id == audio_id,
                Annotation.annotator_id == annotator,
            )
        ).first()
        if ann is not None:
            existing_annotation = _annotation_to_dict(ann)

    return {
        "id": audio.id,
        "filename": audio.filename,
        "game_name": audio.game_name,
        "game_stage": audio.game_stage,
        "is_brand_theme": audio.is_brand_theme,
        "duration_sec": audio.duration_sec,
        "bpm": audio.bpm,
        "sample_rate": audio.sample_rate,
        "auto_computed": {
            "tonal_noise_ratio": audio.tonal_noise_ratio_auto,
            "spectral_density": audio.spectral_density_auto,
        },
        "existing_annotation": existing_annotation,
    }


@router.get("/audio/{audio_id}/stream")
def stream_audio(
    audio_id: str,
    session: Session = Depends(get_session),
) -> FileResponse:
    """串 .wav 給 WaveSurfer。不直接暴露 data/audio/ 目錄，經 id 查 filename 再 serve。"""
    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔：{audio_id}")

    file_path = AUDIO_DIR / audio.filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"音檔檔案不存在：{audio.filename}")

    return FileResponse(
        path=file_path,
        media_type="audio/wav",
        filename=audio.filename,
    )
