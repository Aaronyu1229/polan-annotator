"""Phase 8 — admin-only endpoints for annotator team management.

目前只有兩個 endpoint：
- GET  /api/admin/annotators/pending           列出 pending_calibration 的人 + 校準進度
- POST /api/admin/annotators/{id}/approve      把 status 改 active（解鎖標全部音檔）

設計理由：
- 沒做完整 CRUD（新增 / 刪除 / 改 profile）— Amber 改 JSON 即生效，
  不為了管理 3 個人就建一套 admin UI。
- 進度語意刻意對齊「reference annotator (amber) 已 is_complete 標過 N 首」=
  待校準者的「校準集合大小」。沒有 hardcode 校準清單，跟 calibration.py 同步演進。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from src.annotators_loader import (
    AnnotatorsConfigError,
    get_annotator,
    list_pending_annotators,
    set_status,
)
from src.audiofile_status import (
    compute_audiofile_status,
    gold_lock_prerequisites,
    status_summary,
)
from src.db import get_session
from src.dimensions_loader import load_dimensions
from src.middleware import require_auth
from src.models import Annotation, AudioFile

router = APIRouter(prefix="/api/admin", tags=["admin"])
log = logging.getLogger("polan.routes.admin")

REFERENCE_ANNOTATOR = "amber"  # 跟 calibration.py 對齊；未來 config 化時兩處同改


def _require_admin(current_user: dict[str, Any]) -> None:
    if not current_user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要 admin 權限",
        )


def _calibration_progress(
    annotator_id: str,
    session: Session,
) -> dict[str, int]:
    """回校準進度：完成 = 此人 is_complete=True 的 audio 在 reference 也 is_complete=True 的交集大小。

    回 {"completed": int, "calibration_set_size": int}。calibration_set_size = reference 已標數。
    """
    reference_audio_ids = set(session.exec(
        select(Annotation.audio_file_id).where(
            Annotation.annotator_id == REFERENCE_ANNOTATOR,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all())
    my_done_audio_ids = set(session.exec(
        select(Annotation.audio_file_id).where(
            Annotation.annotator_id == annotator_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all())
    intersection = reference_audio_ids & my_done_audio_ids
    return {
        "completed": len(intersection),
        "calibration_set_size": len(reference_audio_ids),
    }


@router.get("/annotators/pending")
def list_pending(
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """回 status=pending_calibration 的人 + 各自校準進度。"""
    _require_admin(current_user)
    try:
        pending = list_pending_annotators()
    except AnnotatorsConfigError as e:
        log.error("讀 annotators_config 失敗：%s", e)
        raise HTTPException(status_code=500, detail=f"annotators_config 讀取失敗：{e}") from e

    return [
        {
            **entry,
            "calibration_progress": _calibration_progress(entry["id"], session),
        }
        for entry in pending
    ]


@router.post("/annotators/{annotator_id}/approve")
def approve_calibration(
    annotator_id: str,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Amber 認可 vvgosick 校準通過 → status 改 active。

    僅允許從 pending_calibration → active 的 transition。
    其他 transition（active → archived 等）暫不開 API，Amber 改 JSON 即可。
    """
    _require_admin(current_user)

    spec = get_annotator(annotator_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"找不到標註員：{annotator_id}")
    if spec.get("status") != "pending_calibration":
        raise HTTPException(
            status_code=409,
            detail=f"標註員 {annotator_id} 當前狀態為 {spec.get('status')!r}，非 pending_calibration",
        )

    try:
        set_status(annotator_id, "active")
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    log.info(
        "admin %s approved calibration for %s",
        current_user.get("email") or current_user.get("annotator_id"),
        annotator_id,
    )
    return {
        "annotator_id": annotator_id,
        "status": "active",
        "approved_by": current_user.get("email") or current_user.get("annotator_id"),
    }


# ─── Phase 8.5：dimension definition review tool ───────────────────────────

@router.get("/dimension-review")
def dimension_review_data(
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """給 Amber 一站式檢視所有 amber_confirmed:false 維度 + 自己的標註值。

    用途:Amber 看自己 14 筆 × 4 個未確認維度,評估「定義文字 OK 嗎」「自己標的值 OK 嗎」,
    決定哪些維度的定義需要 refine。實際修改 dimensions_config.json 由 Amber 自己編
    (CLAUDE.md 規則 #8：唯一資料來源)。
    """
    _require_admin(current_user)

    dims = load_dimensions()
    # 只取 amber 還沒 confirm 的維度,這些需要 review
    unconfirmed_ids = [
        dim_id for dim_id, spec in dims.items()
        if spec.get("amber_confirmed", True) is False
    ]

    # 一次撈 amber 所有 is_complete annotation + 對應 AudioFile
    rows = session.exec(
        select(Annotation, AudioFile)
        .join(AudioFile, Annotation.audio_file_id == AudioFile.id)
        .where(
            Annotation.annotator_id == REFERENCE_ANNOTATOR,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()

    result = []
    for dim_id in unconfirmed_ids:
        spec = dims[dim_id]
        items = []
        for ann, audio in rows:
            value = getattr(ann, dim_id, None)
            if value is None:
                continue
            items.append({
                "audio_id": audio.id,
                "filename": audio.filename,
                "game_name": audio.game_name,
                "game_stage": audio.game_stage,
                "value": float(value),
            })
        # 從小到大排,Amber 直覺看「最低的真的最低嗎、最高的真的最高嗎、中段順序合理嗎」
        items.sort(key=lambda x: x["value"])
        result.append({
            "dim_id": dim_id,
            "label_zh": spec.get("label_zh", dim_id),
            "category": spec.get("category", ""),
            "definition": spec.get("definition", ""),
            "low_anchor": spec.get("low_anchor", ""),
            "high_anchor": spec.get("high_anchor", ""),
            "amber_confirmed": spec.get("amber_confirmed", False),
            "todo_amber": spec.get("todo_amber", ""),
            "items": items,
        })

    return {
        "reference_annotator": REFERENCE_ANNOTATOR,
        "total_amber_annotations": len(rows),
        "dimensions": result,
    }


# ─── Phase 10：AudioFile gold lock ─────────────────────────────────

@router.post("/audio/{audio_id}/lock_gold")
def lock_audio_gold(
    audio_id: str,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """admin 把某筆音檔標記為 gold(可商用)。

    驗 prereq:≥2 人標 + 每維 max-min spread ≤ GOLD_MAX_SPREAD。
    不符回 409 帶 reasons。
    """
    _require_admin(current_user)
    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔:{audio_id}")

    prereq = gold_lock_prerequisites(audio, session)
    if not prereq["eligible"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Gold lock prerequisites not met",
                "prereq": prereq,
            },
        )

    from src.models import _utcnow  # noqa: PLC0415 — 避免循環 import
    audio.is_gold_locked = True
    audio.gold_locked_at = _utcnow()
    audio.gold_locked_by = current_user.get("annotator_id") or current_user.get("email")
    session.add(audio)
    session.commit()
    session.refresh(audio)

    log.info(
        "admin %s gold-locked audio %s (%s)",
        audio.gold_locked_by, audio.id, audio.filename,
    )
    return {
        "audio_id": audio.id,
        "filename": audio.filename,
        "is_gold_locked": True,
        "gold_locked_at": audio.gold_locked_at.isoformat() if audio.gold_locked_at else None,
        "gold_locked_by": audio.gold_locked_by,
    }


@router.post("/audio/{audio_id}/unlock_gold")
def unlock_audio_gold(
    audio_id: str,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """admin 撤銷某筆音檔的 gold 認證(Amber 改變主意)。

    不驗 prereq — 撤銷永遠允許。
    """
    _require_admin(current_user)
    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔:{audio_id}")

    if not audio.is_gold_locked:
        raise HTTPException(status_code=409, detail="此音檔未鎖,無需 unlock")

    audio.is_gold_locked = False
    audio.gold_locked_at = None
    audio.gold_locked_by = None
    session.add(audio)
    session.commit()

    log.info(
        "admin %s gold-unlocked audio %s (%s)",
        current_user.get("annotator_id"), audio.id, audio.filename,
    )
    return {
        "audio_id": audio.id,
        "filename": audio.filename,
        "is_gold_locked": False,
    }


@router.get("/audio/{audio_id}/status")
def get_audio_status(
    audio_id: str,
    current_user: dict[str, Any] = Depends(require_auth),  # noqa: ARG001 — 純 auth gate
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """單筆音檔目前的 status + prereq 細節。給 admin UI 用。"""
    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔:{audio_id}")

    return {
        "audio_id": audio.id,
        "filename": audio.filename,
        "status": compute_audiofile_status(audio, session),
        "is_gold_locked": audio.is_gold_locked,
        "gold_locked_at": audio.gold_locked_at.isoformat() if audio.gold_locked_at else None,
        "gold_locked_by": audio.gold_locked_by,
        "prereq_check": gold_lock_prerequisites(audio, session),
    }


@router.get("/audio_status_summary")
def audio_status_summary_endpoint(
    current_user: dict[str, Any] = Depends(require_auth),  # noqa: ARG001 — 純 auth gate
    session: Session = Depends(get_session),
) -> dict[str, int]:
    """Dashboard 5 卡用:5 種狀態的計數 + total。

    刻意對所有 logged-in user 公開(不限 admin)— 團隊成員都該看到資料集品質分布。
    """
    return status_summary(session)
