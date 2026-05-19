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
from src.calibration_feedback import build_calibration_report
from src.db import get_session
from src.middleware import require_auth
from src.models import Annotation, AudioFile
from src.stats import compute_icc_per_dimension, compute_overlap_audios, compute_progress

router = APIRouter(prefix="/api/stats", tags=["stats"])


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
