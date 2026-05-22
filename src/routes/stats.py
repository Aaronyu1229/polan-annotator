"""GET /api/stats/* — 進度快照、跨人 ICC、overlap 清單。

- /progress：單 annotator 進度（Phase 5 #2）
- /icc：跨標註員 ICC(2,1) per dimension（Phase 3）
- /overlap：被 ≥ 2 人 is_complete-標過的 audio 清單（Phase 3）

前端 list.js 用 Intl.DateTimeFormat().resolvedOptions().timeZone 抓瀏覽器 TZ
當 ?tz= 帶入，後端用 zoneinfo 解析。無效 TZ 會 fallback UTC 並 log warning。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from src.annotation_serialization import annotation_to_dict
from src.annotators_loader import AnnotatorsConfigError, get_annotator
from src.calibration_feedback import REFERENCE_ANNOTATOR, build_calibration_report
from src.db import get_session
from src.middleware import require_auth
from src.models import Annotation, AudioFile
from src.stats import compute_icc_per_dimension, compute_overlap_audios, compute_progress

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Feature-005: 每筆音檔「警示」判定 — 標註員值與 Amber 值偏差 >= 門檻。
# 門檻依負責人 spec:主觀 7 維 0.25、客觀 2 維 0.15。
# 註:world_immersion 在 dimensions_config 歸 acoustic 類,但 spec 明列為主觀維度
# (它是「世界沉浸感」主觀感受,非聲學量測),故門檻用 0.25,以負責人 spec 為準。
_WARNING_THRESHOLDS: dict[str, float] = {
    "valence": 0.25,
    "arousal": 0.25,
    "emotional_warmth": 0.25,
    "tension_direction": 0.25,
    "temporal_position": 0.25,
    "event_significance": 0.25,
    "world_immersion": 0.25,
    "tonal_noise_ratio": 0.15,
    "spectral_density": 0.15,
}


@router.get("/progress")
def progress(
    user: dict[str, Any] = Depends(require_auth),
    annotator: Optional[str] = Query(
        default=None,
        description="查指定標註員進度（需 admin）；省略則查登入者自己。dashboard 各人進度條用",
    ),
    tz: Optional[str] = Query(default=None, description="IANA TZ，例如 Asia/Taipei"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    target = (annotator or "").strip() or user["annotator_id"]
    if target != user["annotator_id"] and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="僅 admin 可查看其他標註員進度")
    return compute_progress(session, target, tz_name=tz).to_dict()


@router.get("/icc")
def icc(
    include_fixture: bool = Query(
        default=False,
        description="是否納入 fixture_ 前綴的假標註員（dashboard preview 用）",
    ),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_icc_per_dimension(session, include_fixture=include_fixture)


@router.get("/overlap")
def overlap(
    include_fixture: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return compute_overlap_audios(session, include_fixture=include_fixture)


@router.get("/annotator/{annotator_id}/detail")
def annotator_detail(
    annotator_id: str,
    user: dict[str, Any] = Depends(require_auth),
    tz: Optional[str] = Query(default=None, description="IANA TZ，傳給 compute_progress"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """單一標註員明細：progress + vs Amber 校準摘要 + 全部 is_complete 標註。

    權限 admin-or-self（沿用 /progress 寫法）。amber 或無重疊 → calibration=None。
    """
    target = (annotator_id or "").strip()
    if target != user["annotator_id"] and not user.get("is_admin"):
        raise HTTPException(
            status_code=403, detail="僅 admin 或本人可檢視此標註員明細"
        )

    progress = compute_progress(session, target, tz_name=tz).to_dict()

    report = build_calibration_report(session, target)
    calibration: Optional[dict[str, Any]] = None
    if not report.get("is_reference") and report.get("total_overlap", 0) > 0:
        dims = report.get("dimensions", {})
        maes = [d["mae"] for d in dims.values() if d.get("mae") is not None]
        overall_mae = round(sum(maes) / len(maes), 3) if maes else None
        worst_dim: Optional[str] = None
        worst_mae: Optional[float] = None
        for dim_key, d in dims.items():
            mae = d.get("mae")
            if mae is None:
                continue
            if worst_mae is None or mae > worst_mae:
                worst_mae = mae
                worst_dim = dim_key
        calibration = {
            "total_overlap": report["total_overlap"],
            "reference_total": report.get("reference_total"),
            "overall_mae": overall_mae,
            "worst_dim": worst_dim,
            "worst_mae": worst_mae,
            "report_url": f"/calibration/report?annotator={target}",
        }

    rows = session.exec(
        select(Annotation, AudioFile)
        .join(AudioFile, Annotation.audio_file_id == AudioFile.id)
        .where(
            Annotation.annotator_id == target,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    files: list[dict[str, Any]] = []
    for ann, audio in rows:
        item = annotation_to_dict(ann)
        item.update(
            {
                "audio_id": audio.id,
                "filename": audio.filename,
                "game_name": audio.game_name,
                "game_stage": audio.game_stage,
                "duration_sec": audio.duration_sec,
            }
        )
        files.append(item)
    files.sort(key=lambda f: f["updated_at"] or "", reverse=True)

    # Feature-005: 對每筆算「vs Amber 偏差警示」。amber 自己的明細不比對(無 self-reference)。
    _annotate_warning_dims(session, target, files)

    try:
        spec = get_annotator(target)
    except AnnotatorsConfigError:
        spec = None
    annotator_name = (spec or {}).get("name") or target

    return {
        "annotator_id": target,
        "annotator_name": annotator_name,
        "progress": progress,
        "calibration": calibration,
        "files": files,
    }


def _annotate_warning_dims(
    session: Session,
    target: str,
    files: list[dict[str, Any]],
) -> None:
    """就地為每筆 file 加 warning_dims / warning_count / max_deviation 欄位。

    警示 = 該筆某維度 |標註員值 - Amber值| >= 門檻(_WARNING_THRESHOLDS)。
    - amber 自己的明細:不比對,全部 warning_count=0、max_deviation=None。
    - Amber 沒標過該 audio 的:無從比對,warning_count=0、max_deviation=None。
    - 某維度任一方為 None(如 acoustic 兩維已改 librosa 自動,人類常為 None):跳過該維。
    """
    for f in files:
        f["warning_dims"] = []
        f["warning_count"] = 0
        f["max_deviation"] = None

    if target == REFERENCE_ANNOTATOR or not files:
        return

    audio_ids = [f["audio_id"] for f in files]
    amber_rows = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == REFERENCE_ANNOTATOR,
            Annotation.is_complete == True,  # noqa: E712
            Annotation.audio_file_id.in_(audio_ids),  # type: ignore[attr-defined]
        )
    ).all()
    amber_by_audio = {a.audio_file_id: a for a in amber_rows}

    for f in files:
        amber_ann = amber_by_audio.get(f["audio_id"])
        if amber_ann is None:
            continue
        warning_dims: list[dict[str, Any]] = []
        max_dev: Optional[float] = None
        for dim, threshold in _WARNING_THRESHOLDS.items():
            my_val = f.get(dim)
            amber_val = getattr(amber_ann, dim, None)
            if my_val is None or amber_val is None:
                continue
            diff = abs(float(my_val) - float(amber_val))
            max_dev = diff if max_dev is None else max(max_dev, diff)
            if diff >= threshold:
                warning_dims.append(
                    {"dim": dim, "diff": round(diff, 3), "threshold": threshold}
                )
        # 警示維度依差距由大到小,前端 expand 時最嚴重的排前面
        warning_dims.sort(key=lambda w: w["diff"], reverse=True)
        f["warning_dims"] = warning_dims
        f["warning_count"] = len(warning_dims)
        f["max_deviation"] = round(max_dev, 3) if max_dev is not None else None
