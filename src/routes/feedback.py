"""Phase 5 #3 — 維度反饋 3 個 endpoint：

- `POST /api/feedback/dimension` — upsert 單筆 feedback
- `GET  /api/feedback/dimension?annotator=&audio_file_id=` — 某音檔某 annotator 所有 feedback（前端 UI 用來決定 💬 or ✅）
- `GET  /api/feedback/summary?annotator=` — 聚合統計（Aaron 看哪個維度需要改定義）

Feedback 與 Annotation 是兩條獨立生命週期 — 給 feedback 不代表標註完成、反之亦然。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from src.db import get_session
from src.models import AudioFile, DimensionFeedback, _utcnow

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

_ALLOWED_FEEDBACK_TYPES: set[str] = {"clear", "vague", "misaligned", "note"}


class FeedbackPayload(BaseModel):
    """POST body。audio_file_id / annotator_id / dimension_key 三者組成 upsert 的 key。"""
    audio_file_id: str
    annotator_id: str
    dimension_key: str
    feedback_type: str
    note_text: Optional[str] = None


class FeedbackOut(BaseModel):
    """回傳給前端的 feedback row — 隱藏內部 id 以外的細節。"""
    dimension_key: str
    feedback_type: str
    note_text: Optional[str] = None
    updated_at: str

    @classmethod
    def from_model(cls, f: DimensionFeedback) -> "FeedbackOut":
        return cls(
            dimension_key=f.dimension_key,
            feedback_type=f.feedback_type,
            note_text=f.note_text,
            updated_at=f.updated_at.isoformat() if f.updated_at else "",
        )


@router.post("/dimension")
def upsert_feedback(
    payload: FeedbackPayload,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if payload.feedback_type not in _ALLOWED_FEEDBACK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"feedback_type '{payload.feedback_type}' 不合法；"
                f"合法值：{sorted(_ALLOWED_FEEDBACK_TYPES)}"
            ),
        )

    note_trimmed = (payload.note_text or "").strip()
    if payload.feedback_type == "note" and not note_trimmed:
        raise HTTPException(
            status_code=400,
            detail="feedback_type='note' 時 note_text 必填且非空",
        )

    audio = session.get(AudioFile, payload.audio_file_id)
    if audio is None:
        raise HTTPException(
            status_code=404,
            detail=f"找不到音檔：{payload.audio_file_id}",
        )

    # upsert：同 (audio, annotator, dim) 存在則覆蓋 updated_at，否則新建
    existing = session.exec(
        select(DimensionFeedback).where(
            DimensionFeedback.audio_file_id == payload.audio_file_id,
            DimensionFeedback.annotator_id == payload.annotator_id,
            DimensionFeedback.dimension_key == payload.dimension_key,
        )
    ).first()

    now = _utcnow()
    # note_text 只在 note 時保留；切到其他 type 要清掉，避免殘留誤導
    stored_note: Optional[str] = note_trimmed if payload.feedback_type == "note" else None

    if existing:
        existing.feedback_type = payload.feedback_type
        existing.note_text = stored_note
        existing.updated_at = now
        session.add(existing)
        feedback_id = existing.id
    else:
        new = DimensionFeedback(
            audio_file_id=payload.audio_file_id,
            annotator_id=payload.annotator_id,
            dimension_key=payload.dimension_key,
            feedback_type=payload.feedback_type,
            note_text=stored_note,
            created_at=now,
            updated_at=now,
        )
        session.add(new)
        session.flush()
        feedback_id = new.id

    session.commit()

    return {
        "feedback_id": feedback_id,
        "audio_file_id": payload.audio_file_id,
        "annotator_id": payload.annotator_id,
        "dimension_key": payload.dimension_key,
        "feedback_type": payload.feedback_type,
        "updated_at": now.isoformat(),
    }


@router.get("/dimension")
def list_feedback_for_audio(
    annotator: str = Query(..., description="annotator_id"),
    audio_file_id: str = Query(..., description="單一音檔 id"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """回某 annotator 對某音檔所有維度 feedback — 給前端 render 💬/✅ 狀態。"""
    rows = session.exec(
        select(DimensionFeedback).where(
            DimensionFeedback.audio_file_id == audio_file_id,
            DimensionFeedback.annotator_id == annotator,
        )
    ).all()
    return {
        "annotator_id": annotator,
        "audio_file_id": audio_file_id,
        "feedbacks": [FeedbackOut.from_model(r).model_dump() for r in rows],
    }


@router.get("/summary")
def feedback_summary(
    annotator: str = Query(..., description="annotator_id"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Aaron 用 — 看哪個維度的 feedback 分佈最值得調整定義。"""
    rows = session.exec(
        select(DimensionFeedback).where(
            DimensionFeedback.annotator_id == annotator,
        )
    ).all()

    # by_dimension: dim_key -> { feedback_type -> count, total -> int }
    by_dim: dict[str, dict[str, int]] = {}
    for r in rows:
        dim_bucket = by_dim.setdefault(
            r.dimension_key,
            {"clear": 0, "vague": 0, "misaligned": 0, "note": 0, "total": 0},
        )
        # 合法 type 已在寫入時驗過；這裡若 DB 有髒資料也只會多算 total
        if r.feedback_type in dim_bucket:
            dim_bucket[r.feedback_type] += 1
        dim_bucket["total"] += 1

    # recent_notes: 最近 10 筆 feedback_type="note" 的 note_text
    note_rows = sorted(
        (r for r in rows if r.feedback_type == "note" and r.note_text),
        key=lambda r: r.created_at or _utcnow(),
        reverse=True,
    )[:10]

    # 查 audio filename 給 recent_notes 一個人類可讀欄位
    audio_lookup: dict[str, str] = {}
    if note_rows:
        audio_ids = {r.audio_file_id for r in note_rows}
        audios = session.exec(
            select(AudioFile).where(AudioFile.id.in_(audio_ids))  # type: ignore[attr-defined]
        ).all()
        audio_lookup = {a.id: a.filename for a in audios}

    recent_notes = [
        {
            "dimension_key": r.dimension_key,
            "audio_file": audio_lookup.get(r.audio_file_id, r.audio_file_id),
            "note_text": r.note_text,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in note_rows
    ]

    return {
        "annotator_id": annotator,
        "by_dimension": by_dim,
        "recent_notes": recent_notes,
    }
