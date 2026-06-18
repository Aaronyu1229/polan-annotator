"""BGM 對齊模式 API（獨立於既有標註 API，寫入 data/alignment.db）。

端點：
- POST /api/alignment/readings        儲存一組維度值（雙值靠多次 POST，reading_type 區分）
- GET  /api/alignment/readings        取某 session 的所有 reading（已聚成 set）
- POST /api/alignment/compare/pair    比對 1/2/4：兩筆 set → 每維差距 + 一次只變一軸守門
- POST /api/alignment/compare/variance 比對 3：多首 ref → 每維 spread（穩定=保留項）

spec: docs/superpowers/specs/2026-06-18-bgm-alignment-mode-design.md
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.alignment_compare import (
    BGM_DIMENSIONS,
    PairResult,
    Reading,
    ReadingSet,
    SetIdentity,
    compute_variance,
    group_into_sets,
    pair_comparison,
)
from src.alignment_db import AlignmentReading, get_alignment_session

router = APIRouter(prefix="/api/alignment", tags=["alignment"])
log = logging.getLogger("polan.routes.alignment")

ANNOTATOR_ROLES: set[str] = {"engineer", "client"}
AUDIO_ROLES: set[str] = {"ref", "deliverable"}
READING_TYPES: set[str] = {"perceived", "target"}
_DIMENSIONS: set[str] = set(BGM_DIMENSIONS)


# ── schemas ───────────────────────────────────────────────────────────────
class Identity(BaseModel):
    session_id: str
    annotator_id: str
    annotator_role: str
    audio_id: str
    audio_role: str
    version: int = 0
    reading_type: str


class ReadingSetPayload(Identity):
    values: dict[str, float] = Field(default_factory=dict)
    note: str | None = None


class PairRequest(BaseModel):
    a: Identity
    b: Identity


class VarianceRequest(BaseModel):
    session_id: str
    annotator_id: str
    annotator_role: str
    audio_role: str
    version: int = 0
    reading_type: str
    audio_ids: list[str]


# ── validation ────────────────────────────────────────────────────────────
def _validate_identity(idt: Identity) -> None:
    if idt.annotator_role not in ANNOTATOR_ROLES:
        raise HTTPException(400, f"annotator_role 必須是 {sorted(ANNOTATOR_ROLES)}，收到 {idt.annotator_role!r}")
    if idt.audio_role not in AUDIO_ROLES:
        raise HTTPException(400, f"audio_role 必須是 {sorted(AUDIO_ROLES)}，收到 {idt.audio_role!r}")
    if idt.reading_type not in READING_TYPES:
        raise HTTPException(400, f"reading_type 必須是 {sorted(READING_TYPES)}，收到 {idt.reading_type!r}")


def _validate_values(values: dict[str, float]) -> None:
    for dim, val in values.items():
        if dim not in _DIMENSIONS:
            raise HTTPException(400, f"未知維度 {dim!r}；BGM 模式只接受 {sorted(_DIMENSIONS)}")
        if not 0.0 <= val <= 1.0:
            raise HTTPException(400, f"維度 {dim} 值 {val} 超出範圍 0-1")


# ── db helpers ────────────────────────────────────────────────────────────
def _row_to_reading(row: AlignmentReading) -> Reading:
    return Reading(
        session_id=row.session_id,
        annotator_id=row.annotator_id,
        annotator_role=row.annotator_role,
        audio_id=row.audio_id,
        audio_role=row.audio_role,
        version=row.version,
        dimension=row.dimension,
        value=row.value,
        reading_type=row.reading_type,
    )


def _load_set(db: Session, idt: Identity) -> ReadingSet | None:
    rows = db.scalars(
        select(AlignmentReading).where(
            AlignmentReading.session_id == idt.session_id,
            AlignmentReading.annotator_id == idt.annotator_id,
            AlignmentReading.annotator_role == idt.annotator_role,
            AlignmentReading.audio_id == idt.audio_id,
            AlignmentReading.audio_role == idt.audio_role,
            AlignmentReading.version == idt.version,
            AlignmentReading.reading_type == idt.reading_type,
        )
    ).all()
    if not rows:
        return None
    return group_into_sets([_row_to_reading(r) for r in rows])[0]


# ── endpoints ─────────────────────────────────────────────────────────────
@router.post("/readings")
def save_readings(
    payload: ReadingSetPayload,
    db: Session = Depends(get_alignment_session),
) -> dict:
    """Upsert 一組維度值：先刪同身分的舊 row，再寫入新值（idempotent）。"""
    _validate_identity(payload)
    _validate_values(payload.values)

    existing = db.scalars(
        select(AlignmentReading).where(
            AlignmentReading.session_id == payload.session_id,
            AlignmentReading.annotator_id == payload.annotator_id,
            AlignmentReading.annotator_role == payload.annotator_role,
            AlignmentReading.audio_id == payload.audio_id,
            AlignmentReading.audio_role == payload.audio_role,
            AlignmentReading.version == payload.version,
            AlignmentReading.reading_type == payload.reading_type,
        )
    ).all()
    for row in existing:
        db.delete(row)

    for dim, val in payload.values.items():
        db.add(AlignmentReading(
            session_id=payload.session_id,
            annotator_id=payload.annotator_id,
            annotator_role=payload.annotator_role,
            audio_id=payload.audio_id,
            audio_role=payload.audio_role,
            version=payload.version,
            dimension=dim,
            value=val,
            reading_type=payload.reading_type,
            note=payload.note,
        ))
    db.commit()
    return {"saved": len(payload.values), "session_id": payload.session_id, "audio_id": payload.audio_id}


@router.get("/readings")
def list_readings(
    session_id: str = Query(...),
    db: Session = Depends(get_alignment_session),
) -> dict:
    """回傳某 session 全部 reading，已聚成 set（每身分一組維度→值）。"""
    rows = db.scalars(
        select(AlignmentReading).where(AlignmentReading.session_id == session_id)
    ).all()
    sets = group_into_sets([_row_to_reading(r) for r in rows])
    return {"sets": [
        {
            "session_id": s.identity.session_id,
            "annotator_id": s.identity.annotator_id,
            "annotator_role": s.identity.annotator_role,
            "audio_id": s.identity.audio_id,
            "audio_role": s.identity.audio_role,
            "version": s.identity.version,
            "reading_type": s.identity.reading_type,
            "values": s.values,
        }
        for s in sets
    ]}


@router.post("/compare/pair")
def compare_pair_endpoint(
    req: PairRequest,
    db: Session = Depends(get_alignment_session),
) -> dict:
    """比對 1/2/4：載入 A、B 兩筆 set，回每維差距 + 是否只變一軸。"""
    set_a = _load_set(db, req.a)
    set_b = _load_set(db, req.b)
    if set_a is None or set_b is None:
        missing = "A" if set_a is None else "B"
        raise HTTPException(404, f"比對對象 {missing} 查無 reading")
    result: PairResult = pair_comparison(set_a, set_b)
    return {
        "diffs": result.diffs,
        "differing_axes": result.differing_axes,
        "valid": result.valid,
    }


@router.post("/compare/variance")
def compare_variance_endpoint(
    req: VarianceRequest,
    db: Session = Depends(get_alignment_session),
) -> dict:
    """比對 3：載入同一人 / 同 reading_type 下多首 ref 的 set，回每維 spread。"""
    sets: list[ReadingSet] = []
    for audio_id in req.audio_ids:
        idt = Identity(
            session_id=req.session_id,
            annotator_id=req.annotator_id,
            annotator_role=req.annotator_role,
            audio_id=audio_id,
            audio_role=req.audio_role,
            version=req.version,
            reading_type=req.reading_type,
        )
        s = _load_set(db, idt)
        if s is not None:
            sets.append(s)
    return {"spread": compute_variance(sets), "n": len(sets)}
