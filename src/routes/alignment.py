"""BGM 對齊模式 API（獨立於既有標註 API，寫入 data/alignment.db）。

端點：
- POST /api/alignment/readings        儲存一組維度值（雙值靠多次 POST，reading_type 區分）
- GET  /api/alignment/readings        取某 session 的所有 reading（已聚成 set）
- POST /api/alignment/compare/pair    比對 1/2/4：兩筆 set → 每維差距 + 一次只變一軸守門
- POST /api/alignment/compare/variance 比對 3：多首 ref → 每維 spread（穩定=保留項）

spec: docs/superpowers/specs/2026-06-18-bgm-alignment-mode-design.md
"""
from __future__ import annotations

import json
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
    compute_variance,
    group_into_sets,
    pair_comparison,
)
from src.alignment_db import AlignmentReading, AlignmentSpec, get_alignment_session
from src.dimensions_loader import get_bgm_view

router = APIRouter(prefix="/api/alignment", tags=["alignment"])
log = logging.getLogger("polan.routes.alignment")

ANNOTATOR_ROLES: set[str] = {"engineer", "client"}
AUDIO_ROLES: set[str] = {"ref", "deliverable"}
READING_TYPES: set[str] = {"perceived", "target"}
_DIMENSIONS: set[str] = set(BGM_DIMENSIONS)

# 規格區（spec §5）
LOOP_VALUES: set[str] = {"loop", "one_shot"}
LOOP_LENGTHS: set[int] = {15, 30, 60}
# 風格標籤白名單（spec §4）— 只能點選、不可自由輸入
STYLE_TAG_OPTIONS: tuple[str, ...] = (
    "electronic", "celtic", "orchestral", "modern_pop", "trap", "jazz", "lofi",
    "chinese_traditional", "epic", "ambient", "kpop", "world", "fantasy", "cute",
    "realistic", "asian_mythology", "horror", "cyberpunk", "western", "racing",
    "mystery", "japanese", "undersea", "festival",
)
_STYLE_TAG_SET: set[str] = set(STYLE_TAG_OPTIONS)


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


class SpecPayload(BaseModel):
    """規格區提交：循環/長度/風格（非感受值、不分 perceived/target）。"""
    session_id: str
    annotator_id: str
    annotator_role: str
    audio_id: str
    audio_role: str
    version: int = 0
    loop: str | None = None
    loop_length: int | None = None
    style_tags: list[str] = Field(default_factory=list)


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


def _validate_spec(payload: SpecPayload) -> None:
    if payload.annotator_role not in ANNOTATOR_ROLES:
        raise HTTPException(400, f"annotator_role 必須是 {sorted(ANNOTATOR_ROLES)}，收到 {payload.annotator_role!r}")
    if payload.audio_role not in AUDIO_ROLES:
        raise HTTPException(400, f"audio_role 必須是 {sorted(AUDIO_ROLES)}，收到 {payload.audio_role!r}")
    if payload.loop is not None and payload.loop not in LOOP_VALUES:
        raise HTTPException(400, f"loop 必須是 {sorted(LOOP_VALUES)}，收到 {payload.loop!r}")
    if payload.loop_length is not None and payload.loop_length not in LOOP_LENGTHS:
        raise HTTPException(400, f"loop_length 必須是 {sorted(LOOP_LENGTHS)}，收到 {payload.loop_length}")
    bad = [t for t in payload.style_tags if t not in _STYLE_TAG_SET]
    if bad:
        raise HTTPException(400, f"未知風格標籤 {bad}；只能從白名單點選，不可自由輸入")


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


@router.get("/dimensions")
def bgm_dimensions() -> dict:
    """BGM 模式四條感受維度的顯示 view（display_name + 三段錨點 + client_question），依序。"""
    return {"dimensions": [
        {"key": key, **get_bgm_view(key)} for key in BGM_DIMENSIONS
    ]}


@router.get("/style-options")
def style_options() -> dict:
    """風格標籤白名單（前端只能從這裡點選）。"""
    return {"style_tags": list(STYLE_TAG_OPTIONS)}


@router.post("/spec")
def save_spec(
    payload: SpecPayload,
    db: Session = Depends(get_alignment_session),
) -> dict:
    """Upsert 規格區：先刪同身分舊 row 再寫入（一身分一筆規格）。"""
    _validate_spec(payload)
    existing = db.scalars(
        select(AlignmentSpec).where(
            AlignmentSpec.session_id == payload.session_id,
            AlignmentSpec.annotator_id == payload.annotator_id,
            AlignmentSpec.annotator_role == payload.annotator_role,
            AlignmentSpec.audio_id == payload.audio_id,
            AlignmentSpec.audio_role == payload.audio_role,
            AlignmentSpec.version == payload.version,
        )
    ).all()
    for row in existing:
        db.delete(row)
    db.add(AlignmentSpec(
        session_id=payload.session_id,
        annotator_id=payload.annotator_id,
        annotator_role=payload.annotator_role,
        audio_id=payload.audio_id,
        audio_role=payload.audio_role,
        version=payload.version,
        loop=payload.loop,
        loop_length=payload.loop_length,
        style_tags=json.dumps(payload.style_tags, ensure_ascii=False),
    ))
    db.commit()
    return {"saved": True, "session_id": payload.session_id, "audio_id": payload.audio_id}


@router.get("/spec")
def list_specs(
    session_id: str = Query(...),
    db: Session = Depends(get_alignment_session),
) -> dict:
    """回傳某 session 的所有規格區資料。"""
    rows = db.scalars(
        select(AlignmentSpec).where(AlignmentSpec.session_id == session_id)
    ).all()
    return {"specs": [
        {
            "session_id": r.session_id,
            "annotator_id": r.annotator_id,
            "annotator_role": r.annotator_role,
            "audio_id": r.audio_id,
            "audio_role": r.audio_role,
            "version": r.version,
            "loop": r.loop,
            "loop_length": r.loop_length,
            "style_tags": json.loads(r.style_tags) if r.style_tags else [],
        }
        for r in rows
    ]}
