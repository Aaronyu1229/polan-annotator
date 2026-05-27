"""Per-dimension 三向 pairwise gap 引擎（純函式，無 DB / 無副作用）。

只 creator_industry gap 是仲裁闘門；creator_audience / industry_audience 只觀察
（audience 偏離永不影響仲裁路徑 — 修「把視角分歧當缺陷」的 bug class）。

呼叫端負責用 annotators_loader.annotator_id_for_role 把 role → annotation 解好再傳進來。
"""
from __future__ import annotations

from typing import Optional

from src.models import Annotation
from src.thresholds import (
    ARBITRATION_GATE,
    HUMAN_CONTINUOUS_DIMS,
    INDUSTRY_RECAL,
    PRODUCT_DIVERGENCE,
)

GapsByDim = dict[str, dict[str, Optional[float]]]


def _abs_gap(a: Optional[Annotation], b: Optional[Annotation], dim: str) -> Optional[float]:
    if a is None or b is None:
        return None
    av, bv = getattr(a, dim, None), getattr(b, dim, None)
    if av is None or bv is None:
        return None
    return abs(av - bv)


def pairwise_gaps(by_role: dict[str, Optional[Annotation]]) -> GapsByDim:
    """每個 human 連續維 → {creator_industry, creator_audience, industry_audience}。
    任一側缺（None 或該維未標）→ 該 pair = None。"""
    creator = by_role.get("creator")
    industry = by_role.get("industry")
    audience = by_role.get("audience")
    out: GapsByDim = {}
    for dim in HUMAN_CONTINUOUS_DIMS:
        out[dim] = {
            "creator_industry": _abs_gap(creator, industry, dim),
            "creator_audience": _abs_gap(creator, audience, dim),
            "industry_audience": _abs_gap(industry, audience, dim),
        }
    return out


def needs_full_arbitration(gaps: GapsByDim) -> set[str]:
    """回 creator_industry_gap > ARBITRATION_GATE 的維度集合（需走 full 仲裁、寫 Notes）。
    None（industry 缺）不算超標 — 缺 industry 由 status 層歸到「等待 industry」。"""
    return {
        dim
        for dim, g in gaps.items()
        if g["creator_industry"] is not None and g["creator_industry"] > ARBITRATION_GATE
    }


def classify_dim_flags(gaps: GapsByDim) -> dict[str, set[str]]:
    """每個連續維 → 品質 flag 集合（Phase 5）。

    - industry_divergence：creator_industry_gap > INDUSTRY_RECAL（業界內部分歧，校準信號）
    - product_divergence：industry_audience_gap > PRODUCT_DIVERGENCE（專業vs大眾=商品）
    None gap（缺角色）不分類；creator_audience 純觀察不分類。
    """
    _eps = 1e-9  # IEEE 754 容忍：剛好 = 門檻不算超（如 0.5-0.8 = 0.30000000000000004）
    out: dict[str, set[str]] = {}
    for dim, g in gaps.items():
        flags: set[str] = set()
        ci = g["creator_industry"]
        ia = g["industry_audience"]
        if ci is not None and ci > INDUSTRY_RECAL + _eps:
            flags.add("industry_divergence")
        if ia is not None and ia > PRODUCT_DIVERGENCE + _eps:
            flags.add("product_divergence")
        out[dim] = flags
    return out
