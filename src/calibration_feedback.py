"""Phase 9 — 校準訓練 feedback 計算邏輯。

設計原則(回應 PM 對 anchoring bias 的關切):
- 只回「方向類別」(green/yellow/red),不回 reference 的具體值
- 不告訴標註員自己的偏差方向(偏高 vs 偏低)— 避免訓練成 reference clone
- 報告層才揭露 systematic offset(MAE / Pearson r / 平均偏移),但仍不 per-item 揭路

色彩門檻(對 [0,1] 連續維度):
    🟢 green   |Δ| ≤ 0.15        接近
    🟡 yellow  0.15 < |Δ| ≤ 0.30  略偏
    🔴 red     |Δ| > 0.30         顯著偏離

multi_discrete(loop_capability)目前不參與 calibration feedback —
集合相等就綠、有交集但不等是黃、無交集就紅。實作簡單,放未來如有需要再加。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from src.models import Annotation

# 跟 src/routes/admin.py 對齊;未來 config 化時兩處同改
REFERENCE_ANNOTATOR = "amber"

# 連續維度色彩門檻
GREEN_THRESHOLD = 0.15
YELLOW_THRESHOLD = 0.30
# 浮點精度容忍:避免 0.5 - 0.35 = 0.15000000000000002 這種 IEEE 754 邊界把
# 「剛好等於門檻」誤判到下一級
_EPS = 1e-9

# Phase 7 起這 2 維由 librosa 算,不參與人類校準 feedback
LIBROSA_DIMS = frozenset({"tonal_noise_ratio", "spectral_density"})

# 7 個 human 維度(對齊 routes/annotations.HUMAN_CONTINUOUS_DIMENSION_FIELDS)
HUMAN_CONTINUOUS_DIMS = (
    "valence",
    "arousal",
    "emotional_warmth",
    "tension_direction",
    "temporal_position",
    "event_significance",
    "world_immersion",
)

# 報告層判定常數
WARNING_DIMS_THRESHOLD = 2

# 繁中固定建議文案（CLAUDE.md UI convention：sentence case，具體）
NEXT_ACTIONS = (
    "與 Amber 一對一過 Top 10 偏差最大的音檔（約 1 hr）",
    "重做校準集第二輪標註（全部校準題）",
    "重新計算 MAE，目標降至 ≤ 0.15 後由 Amber 認可通過",
)


def distance_category(my_value: float, ref_value: float) -> str:
    """連續維度比對 → green / yellow / red。門檻含浮點 epsilon 容忍。"""
    delta = abs(my_value - ref_value)
    if delta <= GREEN_THRESHOLD + _EPS:
        return "green"
    if delta <= YELLOW_THRESHOLD + _EPS:
        return "yellow"
    return "red"


def compute_calibration_feedback(
    my_annotation: Annotation,
    reference_annotation: Annotation,
) -> dict[str, str]:
    """對齊兩筆 annotation 同一 audio,每維度回 color。

    缺值處理:任一邊維度為 None 該維度跳過(不出現在結果),caller 該維度不顯示徽章。
    """
    feedback: dict[str, str] = {}
    for dim in HUMAN_CONTINUOUS_DIMS:
        my_val = getattr(my_annotation, dim, None)
        ref_val = getattr(reference_annotation, dim, None)
        if my_val is None or ref_val is None:
            continue
        feedback[dim] = distance_category(float(my_val), float(ref_val))
    return feedback


def get_reference_annotation(
    session: Session,
    audio_id: str,
) -> Optional[Annotation]:
    """取 reference(amber)對此 audio 的 is_complete annotation。

    這就是 calibration set 的定義:reference 已 is_complete 標過的 audio。
    """
    return session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio_id,
            Annotation.annotator_id == REFERENCE_ANNOTATOR,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).first()


def build_calibration_report(
    session: Session,
    annotator_id: str,
) -> dict[str, Any]:
    """產生 annotator 全部校準題完成後的 aggregated report。

    對所有 (my, reference) 配對的 is_complete annotation:
    - per-dim MAE(平均絕對誤差,signed delta 的 abs 平均)
    - per-dim Pearson r(N≥3 才算,否則 None)
    - per-dim mean_signed_offset(平均 signed delta,告訴 Amber 此人系統性偏高/偏低)
    - per-dim sample_size

    刻意不揭露 reference 具體值,即使在 report 也只給聚合統計。
    """
    if annotator_id == REFERENCE_ANNOTATOR:
        return {
            "annotator_id": annotator_id,
            "is_reference": True,
            "dimensions": {},
            "total_overlap": 0,
        }

    # reference 已 is_complete 的 audio set
    ref_audio_ids = set(session.exec(
        select(Annotation.audio_file_id).where(
            Annotation.annotator_id == REFERENCE_ANNOTATOR,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all())

    # annotator 已 is_complete 的 audio set
    my_anns = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == annotator_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    my_by_audio = {a.audio_file_id: a for a in my_anns}

    overlap = ref_audio_ids & set(my_by_audio.keys())
    if not overlap:
        return {
            "annotator_id": annotator_id,
            "is_reference": False,
            "dimensions": {},
            "total_overlap": 0,
            "reference_total": len(ref_audio_ids),
        }

    # 撈 reference 對 overlap 這些的 annotation
    ref_anns = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == REFERENCE_ANNOTATOR,
            Annotation.is_complete == True,  # noqa: E712
            Annotation.audio_file_id.in_(overlap),  # type: ignore[attr-defined]
        )
    ).all()
    ref_by_audio = {a.audio_file_id: a for a in ref_anns}

    per_dim: dict[str, dict[str, Any]] = {}
    for dim in HUMAN_CONTINUOUS_DIMS:
        my_vals: list[float] = []
        ref_vals: list[float] = []
        for audio_id in overlap:
            my_v = getattr(my_by_audio[audio_id], dim, None)
            ref_v = getattr(ref_by_audio[audio_id], dim, None)
            if my_v is None or ref_v is None:
                continue
            my_vals.append(float(my_v))
            ref_vals.append(float(ref_v))

        if not my_vals:
            per_dim[dim] = {
                "sample_size": 0,
                "mae": None,
                "pearson_r": None,
                "mean_signed_offset": None,
                "verdict": None,
            }
            continue

        deltas = [m - r for m, r in zip(my_vals, ref_vals)]
        abs_deltas = [abs(d) for d in deltas]
        mae = sum(abs_deltas) / len(abs_deltas)
        mean_signed = sum(deltas) / len(deltas)

        pearson_r: Optional[float] = None
        if len(my_vals) >= 3:
            pearson_r = _pearson(my_vals, ref_vals)

        if mae <= GREEN_THRESHOLD:
            verdict = "green"
        elif mae <= YELLOW_THRESHOLD:
            verdict = "yellow"
        else:
            verdict = "red"

        per_dim[dim] = {
            "sample_size": len(my_vals),
            "mae": round(mae, 3),
            "pearson_r": round(pearson_r, 3) if pearson_r is not None else None,
            "mean_signed_offset": round(mean_signed, 3),
            "verdict": verdict,
        }

    return {
        "annotator_id": annotator_id,
        "is_reference": False,
        "dimensions": per_dim,
        "total_overlap": len(overlap),
        "reference_total": len(ref_audio_ids),
        "completed_calibration": len(overlap) >= len(ref_audio_ids),
    }


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    """Pearson correlation。常數序列(無變異)回 None — Pearson 在這種 case undefined。"""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs)
    den_y = sum((y - mean_y) ** 2 for y in ys)
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y) ** 0.5


def _dim_display(dim_id: str) -> str:
    """維度中文顯示名 = dimensions_config 的 label_zh（唯一來源）。"""
    from src.dimensions_loader import load_dimensions  # noqa: PLC0415
    return load_dimensions().get(dim_id, {}).get("label_zh", dim_id)


def _objective_dim_ids() -> list[str]:
    """dimensions_config 內、不在 HUMAN_CONTINUOUS_DIMS 的維度（無人工 vs amber MAE）。"""
    from src.dimensions_loader import load_dimensions  # noqa: PLC0415
    return [d for d in load_dimensions() if d not in HUMAN_CONTINUOUS_DIMS]


def _recommendation(overall_mae: Optional[float], warning_count: int) -> str:
    if overall_mae is None:
        return "needs_training"
    if overall_mae <= GREEN_THRESHOLD and warning_count < WARNING_DIMS_THRESHOLD:
        return "approved"
    if overall_mae > YELLOW_THRESHOLD:
        return "not_recommended"
    return "needs_training"


def build_calibration_report_detailed(
    session: Session,
    annotator_id: str,
    include_reference_detail: bool,
) -> dict[str, Any]:
    """合伙人 spec 的完整校準報告。

    scatter_data / top_deviations 含 reference(amber) 逐題值 —
    只在 include_reference_detail=True（admin 視角）才加入。
    既有 build_calibration_report() 不動，本函式呼叫它取 per-dim 核心。
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from src.annotators_loader import get_annotator  # noqa: PLC0415

    core = build_calibration_report(session, annotator_id)
    spec = get_annotator(annotator_id) or {}
    generated = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    base = {
        "annotator": annotator_id,
        "annotator_name": spec.get("name") or annotator_id,
        "role": spec.get("annotator_profile"),
        "is_reference": bool(core.get("is_reference", False)),
        "report_generated_at": generated,
    }

    if core.get("is_reference") or core.get("total_overlap", 0) == 0:
        return {
            **base,
            "calibration_progress": (
                f"{core.get('total_overlap', 0)}/{core.get('reference_total', 0)}"
            ),
            "overall": None,
            "dimensions": [],
            "recommendations": None,
        }

    per_dim = core["dimensions"]
    dimensions: list[dict[str, Any]] = []
    maes: list[float] = []
    warning_count = 0

    for dim in HUMAN_CONTINUOUS_DIMS:
        d = per_dim.get(dim, {})
        mae = d.get("mae")
        size = d.get("sample_size", 0)
        if mae is None or size == 0:
            status = "no_data"
        elif mae > GREEN_THRESHOLD:
            status = "warning"
            warning_count += 1
        else:
            status = "ok"
        if mae is not None:
            maes.append(mae)
        dimensions.append({
            "name": dim,
            "display_name_zh": _dim_display(dim),
            "category": "subjective",
            "mae": mae,
            "threshold": GREEN_THRESHOLD,
            "status": status,
            "overlap_count": size,
        })

    for dim in _objective_dim_ids():
        dimensions.append({
            "name": dim,
            "display_name_zh": _dim_display(dim),
            "category": "objective",
            "mae": None,
            "threshold": GREEN_THRESHOLD,
            "status": "no_data",
            "overlap_count": 0,
        })

    overall_mae = round(sum(maes) / len(maes), 3) if maes else None
    overall = {
        "mae": overall_mae,
        "threshold": GREEN_THRESHOLD,
        "warning_dims_count": warning_count,
        "warning_dims_threshold": WARNING_DIMS_THRESHOLD,
        "recommendation": _recommendation(overall_mae, warning_count),
    }
    recommendations = {
        "dims_to_retrain": [
            d["name"] for d in dimensions
            if d["category"] == "subjective" and d["status"] == "warning"
        ],
        "dims_approved": [
            d["name"] for d in dimensions
            if d["category"] == "subjective" and d["status"] == "ok"
        ],
        "dims_no_data": [d["name"] for d in dimensions if d["status"] == "no_data"],
        "next_actions": list(NEXT_ACTIONS),
    }

    result = {
        **base,
        "calibration_progress": (
            f"{core['total_overlap']}/{core['reference_total']}"
        ),
        "overall": overall,
        "dimensions": dimensions,
        "recommendations": recommendations,
    }
    if include_reference_detail:
        scatter, top = _build_reference_detail(session, annotator_id)
        result["scatter_data"] = scatter
        result["top_deviations"] = top
    return result


def _build_reference_detail(
    session: Session,
    annotator_id: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """回 (scatter_data, top_deviations)。含 amber 逐題值 — 只在 admin 視角呼叫。"""
    from src.models import AudioFile  # noqa: PLC0415

    ref_rows = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == REFERENCE_ANNOTATOR,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    my_rows = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == annotator_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    ref_by = {a.audio_file_id: a for a in ref_rows}
    my_by = {a.audio_file_id: a for a in my_rows}
    overlap = set(ref_by) & set(my_by)

    audio_by_id: dict[str, Any] = {}
    if overlap:
        audio_by_id = {
            a.id: a
            for a in session.exec(
                select(AudioFile).where(
                    AudioFile.id.in_(overlap)  # type: ignore[attr-defined]
                )
            ).all()
        }

    scatter: dict[str, list[dict[str, Any]]] = {
        d: [] for d in HUMAN_CONTINUOUS_DIMS
    }
    deviations: list[dict[str, Any]] = []

    for aid in overlap:
        ref = ref_by[aid]
        mine = my_by[aid]
        audio = audio_by_id.get(aid)
        fname = audio.filename if audio else aid
        all_dims: dict[str, dict[str, float]] = {}
        worst_dim: Optional[str] = None
        worst_diff = -1.0
        for dim in HUMAN_CONTINUOUS_DIMS:
            rv = getattr(ref, dim, None)
            mv = getattr(mine, dim, None)
            if rv is None or mv is None:
                continue
            rv = float(rv)
            mv = float(mv)
            scatter[dim].append({"file": fname, "amber": rv, "annotator": mv})
            diff = round(abs(mv - rv), 3)
            all_dims[dim] = {"amber": rv, "annotator": mv, "diff": diff}
            if diff > worst_diff:
                worst_diff = diff
                worst_dim = dim
        if worst_dim is None:
            continue
        deviations.append({
            "file": fname,
            "game": audio.game_name if audio else "",
            "section": audio.game_stage if audio else "",
            "audio_url": f"/api/audio/{aid}/stream",
            "worst_dim": worst_dim,
            "worst_dim_display": _dim_display(worst_dim),
            "amber_value": all_dims[worst_dim]["amber"],
            "annotator_value": all_dims[worst_dim]["annotator"],
            "diff": all_dims[worst_dim]["diff"],
            "all_dims": all_dims,
        })

    deviations.sort(key=lambda d: d["diff"], reverse=True)
    return scatter, deviations[:10]
