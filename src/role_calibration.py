"""Phase 7 — per-role 校準指標。

creator 自我一致性（self-MAE，test-retest）/ industry 對齊（只上界）/ audience 內部一致性。
test-retest 用 AnnotationSnapshot（原始 Annotation = pass 1，retest 寫 snapshot）。
門檻見 thresholds.py；嚴謹 CI / CCC 留 Phase 8。
"""
from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from src.annotators_loader import annotator_id_for_role, get_role
from src.models import Annotation, AnnotationSnapshot
from src.thresholds import (
    AUDIENCE_INTRA_MIN,
    CALIB_MIN_N,
    HUMAN_CONTINUOUS_DIMS,
    INDUSTRY_ALIGN_MAX,
    SELF_MAE_MAX,
)


def _retest_deltas(session: Session, annotator_id: str) -> list[float]:
    """原始 Annotation 與 AnnotationSnapshot（retest）同 (audio, dim) 的 |Δ| 列表。"""
    originals = {
        a.audio_file_id: a
        for a in session.exec(
            select(Annotation).where(
                Annotation.annotator_id == annotator_id,
                Annotation.is_complete == True,  # noqa: E712
            )
        ).all()
    }
    snaps = session.exec(
        select(AnnotationSnapshot).where(AnnotationSnapshot.annotator_id == annotator_id)
    ).all()
    deltas: list[float] = []
    for snap in snaps:
        orig = originals.get(snap.audio_file_id)
        if orig is None:
            continue
        for dim in HUMAN_CONTINUOUS_DIMS:
            ov, sv = getattr(orig, dim, None), getattr(snap, dim, None)
            if ov is not None and sv is not None:
                deltas.append(abs(ov - sv))
    return deltas


def self_mae(session: Session, annotator_id: str) -> dict[str, Any]:
    """creator 自我一致性 self-MAE（越低越一致）。N = 比對的 (audio,dim) 數。"""
    deltas = _retest_deltas(session, annotator_id)
    n = len(deltas)
    if n < CALIB_MIN_N:
        return {"metric": "self_mae", "value": None, "n": n, "insufficient": True}
    value = round(sum(deltas) / n, 3)
    return {"metric": "self_mae", "value": value, "n": n,
            "insufficient": False, "pass": value < SELF_MAE_MAX}


def audience_intra_rater(session: Session, annotator_id: str) -> dict[str, Any]:
    """audience 內部一致性 = 1 - mean|Δ|（test-retest）。不以 vs-creator gating。"""
    deltas = _retest_deltas(session, annotator_id)
    n = len(deltas)
    if n < CALIB_MIN_N:
        return {"metric": "intra_rater", "value": None, "n": n, "insufficient": True}
    value = round(1.0 - sum(deltas) / n, 3)
    return {"metric": "intra_rater", "value": value, "n": n,
            "insufficient": False, "pass": value >= AUDIENCE_INTRA_MIN}


def industry_alignment(session: Session, annotator_id: str) -> dict[str, Any]:
    """industry vs creator 對齊 MAE（**只上界** INDUSTRY_ALIGN_MAX；拿掉 0.10 下界，低 MAE 不 fail）。"""
    creator_id = annotator_id_for_role("creator")
    creator = {
        a.audio_file_id: a
        for a in session.exec(
            select(Annotation).where(
                Annotation.annotator_id == creator_id,
                Annotation.is_complete == True,  # noqa: E712
            )
        ).all()
    } if creator_id else {}
    mine = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == annotator_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    deltas: list[float] = []
    for a in mine:
        ref = creator.get(a.audio_file_id)
        if ref is None:
            continue
        for dim in HUMAN_CONTINUOUS_DIMS:
            mv, rv = getattr(a, dim, None), getattr(ref, dim, None)
            if mv is not None and rv is not None:
                deltas.append(abs(mv - rv))
    n = len(deltas)
    if n == 0:
        return {"metric": "vs_creator_mae", "value": None, "n": 0, "insufficient": True}
    value = round(sum(deltas) / n, 3)
    return {"metric": "vs_creator_mae", "value": value, "n": n,
            "insufficient": False, "pass": value <= INDUSTRY_ALIGN_MAX}


def role_aware_calibration_status(session: Session, annotator_id: str) -> dict[str, Any]:
    """依 role 走對的 metric。creator→self_mae / industry→vs_creator_mae(只上界) /
    audience→intra_rater（不以分數對齊 gating）。未設 role → 回 None。"""
    role = get_role(annotator_id)
    if role == "creator":
        result = self_mae(session, annotator_id)
    elif role == "industry":
        result = industry_alignment(session, annotator_id)
    elif role == "audience":
        result = audience_intra_rater(session, annotator_id)
    else:
        return {"role": role, "metric": None, "value": None, "insufficient": True}
    return {"role": role, **result}
