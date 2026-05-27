"""Phase 5 — 集合層級品質信號（純聚合，無新表）。

回答三件事：業界對齊出問題了嗎（industry 校準信號）、哪些檔是商品（專業vs大眾分歧）、
audience 資料可信嗎（straight-lining 守門）。flag 定義見 role_gaps.classify_dim_flags。
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

from sqlmodel import Session, select

from src.audiofile_status import bulk_load_annotations_by_audio
from src.models import Annotation, AudioFile
from src.role_gaps import classify_dim_flags, pairwise_gaps
from src.thresholds import HUMAN_CONTINUOUS_DIMS, RECAL_MIN_FILES

# straight-lining 啟發式參數（Phase 7 會用 test-retest intra-rater 取代）
_LOW_SD = 0.05
_MIN_DISTINCT = 2
_SUSPECT_DIM_FRACTION = 0.5  # 過半維度低變異 → 疑似亂標
_STRAIGHT_LINING_MIN_N = 5   # 不足量不判定


def audience_straight_lining(audience_anns: list[Annotation]) -> dict[str, Any]:
    """audience 分數多樣性啟發式。多數維度 distinct ≤2 或 SD <0.05 → suspect。

    這是 Phase 5 輕量守門；Phase 7 用隱藏重複題 intra-rater 取代。
    """
    n = len(audience_anns)
    low_variance_dims: list[str] = []
    for dim in HUMAN_CONTINUOUS_DIMS:
        values = [v for a in audience_anns if (v := getattr(a, dim, None)) is not None]
        if len(values) < _STRAIGHT_LINING_MIN_N:
            continue
        distinct = len(set(values))
        sd = statistics.pstdev(values)
        if distinct <= _MIN_DISTINCT or sd < _LOW_SD:
            low_variance_dims.append(dim)
    enough = n >= _STRAIGHT_LINING_MIN_N
    suspect = enough and len(low_variance_dims) >= _SUSPECT_DIM_FRACTION * len(HUMAN_CONTINUOUS_DIMS)
    return {
        "suspect": suspect,
        "n_complete": n,
        "low_variance_dims": low_variance_dims,
        "insufficient": not enough,
    }


def aggregate_quality(
    session: Session,
    role_map: dict[str, Optional[str]],
) -> dict[str, Any]:
    """跑全部 creator+industry 皆完成的檔，聚合品質信號。"""
    creator_id = role_map.get("creator")
    industry_id = role_map.get("industry")
    audience_id = role_map.get("audience")

    audios = session.exec(select(AudioFile)).all()
    anns_by_audio = bulk_load_annotations_by_audio(session)

    industry_divergence_by_dim: dict[str, list[str]] = {d: [] for d in HUMAN_CONTINUOUS_DIMS}
    product_divergence_files: list[dict[str, Any]] = []
    audience_anns: list[Annotation] = []

    for audio in audios:
        anns = anns_by_audio.get(audio.id, [])
        by_id = {a.annotator_id: a for a in anns}
        if audience_id and audience_id in by_id:
            audience_anns.append(by_id[audience_id])
        if creator_id not in by_id or industry_id not in by_id:
            continue
        by_role = {
            "creator": by_id.get(creator_id),
            "industry": by_id.get(industry_id),
            "audience": by_id.get(audience_id),
        }
        flags = classify_dim_flags(pairwise_gaps(by_role))
        product_dims: list[str] = []
        for dim, fset in flags.items():
            if "industry_divergence" in fset:
                industry_divergence_by_dim[dim].append(audio.id)
            if "product_divergence" in fset:
                product_dims.append(dim)
        if product_dims:
            product_divergence_files.append({
                "audio_id": audio.id,
                "filename": audio.filename,
                "dims": sorted(product_dims),
            })

    recalibration_recommended_dims = sorted(
        dim for dim, ids in industry_divergence_by_dim.items() if len(ids) >= RECAL_MIN_FILES
    )
    return {
        "industry_divergence_by_dim": {
            dim: {"count": len(ids), "audio_ids": ids}
            for dim, ids in industry_divergence_by_dim.items()
        },
        "recalibration_recommended_dims": recalibration_recommended_dims,
        "recal_min_files": RECAL_MIN_FILES,
        "product_divergence_files": product_divergence_files,
        "audience_quality": audience_straight_lining(audience_anns),
    }
