"""Phase 8 — agreement 統計（純函式）。

pairwise 對齊用 Lin's CCC + Bland–Altman（取代 K=2 ICC，見 deep-review A3）；
三人整體用 ICC(2,1) 僅報告。所有函式：N < min_n → {insufficient: True}，不出 pass/fail。
門檻皆慣例，gate 用 CI 下界而非點估計。
"""
from __future__ import annotations

import random
import statistics
from typing import Any

from src.thresholds import AGREEMENT_MIN_N


def _ccc_value(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    vx = sum((x - mx) ** 2 for x in xs) / n
    vy = sum((y - my) ** 2 for y in ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    denom = vx + vy + (mx - my) ** 2
    return 2 * cov / denom if denom else 1.0


def ccc(xs: list[float], ys: list[float], *, min_n: int = AGREEMENT_MIN_N,
        n_boot: int = 1000, seed: int = 0) -> dict[str, Any]:
    """Lin's concordance correlation coefficient + bootstrap 95% CI。"""
    n = len(xs)
    if n < min_n:
        return {"metric": "ccc", "value": None, "n": n, "insufficient": True}
    value = _ccc_value(xs, ys)
    rng = random.Random(seed)
    boots = []
    idx = range(n)
    for _ in range(n_boot):
        sample = [rng.choice(idx) for _ in idx]
        bx = [xs[i] for i in sample]
        by = [ys[i] for i in sample]
        try:
            boots.append(_ccc_value(bx, by))
        except ZeroDivisionError:
            continue
    boots.sort()
    lo = boots[int(0.025 * len(boots))]
    hi = boots[int(0.975 * len(boots))]
    return {"metric": "ccc", "value": round(value, 3), "n": n, "insufficient": False,
            "ci_low": round(lo, 3), "ci_high": round(hi, 3)}


def bland_altman(xs: list[float], ys: list[float]) -> dict[str, Any]:
    """mean bias + 95% limits of agreement。"""
    diffs = [x - y for x, y in zip(xs, ys)]
    n = len(diffs)
    if n < 2:
        return {"mean_bias": None, "insufficient": True}
    bias = statistics.fmean(diffs)
    sd = statistics.stdev(diffs)
    return {"mean_bias": round(bias, 3), "sd_diff": round(sd, 3),
            "loa_low": round(bias - 1.96 * sd, 3), "loa_high": round(bias + 1.96 * sd, 3),
            "n": n, "insufficient": False}


def icc_2_1(matrix: list[list[float]]) -> dict[str, Any]:
    """ICC(2,1) two-way random, absolute agreement, single rater。matrix: subjects × raters。

    僅供三人整體『報告』用（不 gate）。N(subjects) < 2 或 raters < 2 → insufficient。
    """
    n = len(matrix)
    if n < 2 or not matrix or len(matrix[0]) < 2:
        return {"metric": "icc_2_1", "value": None, "insufficient": True}
    k = len(matrix[0])
    flat = [v for row in matrix for v in row]
    grand = statistics.fmean(flat)
    row_means = [statistics.fmean(r) for r in matrix]
    col_means = [statistics.fmean([matrix[i][j] for i in range(n)]) for j in range(k)]
    ss_total = sum((v - grand) ** 2 for v in flat)
    ss_row = k * sum((rm - grand) ** 2 for rm in row_means)
    ss_col = n * sum((cm - grand) ** 2 for cm in col_means)
    ss_err = ss_total - ss_row - ss_col
    msr = ss_row / (n - 1)
    msc = ss_col / (k - 1)
    mse = ss_err / ((n - 1) * (k - 1))
    denom = msr + (k - 1) * mse + (k / n) * (msc - mse)
    value = (msr - mse) / denom if denom else 1.0
    return {"metric": "icc_2_1", "value": round(value, 3), "n_subjects": n,
            "n_raters": k, "insufficient": False}


# ─── 集合層級分層（DB） ───────────────────────────────────────────

def compute_agreement_layers(session) -> dict[str, Any]:
    """三層 agreement：業界對齊(CCC creator×industry, gate CI下界) / 三人整體(ICC 僅報告) /
    audience within-category(source_type 分組 proxy)。修掉現行 yyslin×Vic 量錯對象。
    """
    import json as _json  # noqa: PLC0415

    from sqlmodel import select as _select  # noqa: PLC0415

    from src.annotators_loader import annotator_id_for_role  # noqa: PLC0415
    from src.models import Annotation as _Ann  # noqa: PLC0415
    from src.thresholds import (  # noqa: PLC0415
        AUDIENCE_WITHIN_CAT_MIN,
        HUMAN_CONTINUOUS_DIMS,
        INDUSTRY_CCC_MIN,
    )

    c_id = annotator_id_for_role("creator")
    i_id = annotator_id_for_role("industry")
    a_id = annotator_id_for_role("audience")

    rows = session.exec(
        _select(_Ann).where(_Ann.is_complete == True)  # noqa: E712
    ).all()
    by_audio: dict[str, dict[str, _Ann]] = {}
    for r in rows:
        by_audio.setdefault(r.audio_file_id, {})[r.annotator_id] = r

    industry_alignment: dict[str, Any] = {}
    overall_three_way: dict[str, Any] = {}
    for dim in HUMAN_CONTINUOUS_DIMS:
        cx, iy, matrix = [], [], []
        for anns in by_audio.values():
            c, i, a = anns.get(c_id), anns.get(i_id), anns.get(a_id)
            if c is not None and i is not None:
                cv, iv = getattr(c, dim, None), getattr(i, dim, None)
                if cv is not None and iv is not None:
                    cx.append(cv)
                    iy.append(iv)
            if c is not None and i is not None and a is not None:
                vals = [getattr(c, dim, None), getattr(i, dim, None), getattr(a, dim, None)]
                if all(v is not None for v in vals):
                    matrix.append(vals)
        cc = ccc(cx, iy)
        ba = bland_altman(cx, iy) if len(cx) >= 2 else {"insufficient": True}
        passed = (not cc["insufficient"]) and cc.get("ci_low", -1) >= INDUSTRY_CCC_MIN
        industry_alignment[dim] = {**cc, "bland_altman": ba, "pass": passed}
        overall_three_way[dim] = icc_2_1(matrix)

    # audience within-category（proxy：以 creator 的 source_type 主類別分組，組內 SD）
    groups: dict[str, list[_Ann]] = {}
    for anns in by_audio.values():
        c, a = anns.get(c_id), anns.get(a_id)
        if c is None or a is None:
            continue
        try:
            cats = _json.loads(c.source_type) if c.source_type else []
            cat = cats[0] if cats else "untagged"
        except (ValueError, TypeError):
            cat = "untagged"
        groups.setdefault(cat, []).append(a)
    import statistics as _st  # noqa: PLC0415
    audience_within_category: dict[str, Any] = {}
    for cat, anns in groups.items():
        if len(anns) < AUDIENCE_WITHIN_CAT_MIN:
            audience_within_category[cat] = {"insufficient": True, "n": len(anns)}
            continue
        sds = []
        for dim in HUMAN_CONTINUOUS_DIMS:
            vals = [v for x in anns if (v := getattr(x, dim, None)) is not None]
            if len(vals) >= 2:
                sds.append(_st.pstdev(vals))
        consistency = round(1.0 - (sum(sds) / len(sds)), 3) if sds else None
        audience_within_category[cat] = {
            "n": len(anns), "consistency_proxy": consistency, "insufficient": False,
            "note": "proxy（組內 SD）；嚴謹版需 test-retest（Phase 7 retest 資料）",
        }

    return {
        "industry_alignment": industry_alignment,   # gate CI 下界 ≥ 0.7
        "overall_three_way": overall_three_way,      # 僅報告，低=商品，不 gate
        "audience_within_category": audience_within_category,
        "mimicry_residual": {
            "enabled": False,
            "reason": "需第三獨立評分者才能算殘差相關；待補後啟用",
        },
    }
