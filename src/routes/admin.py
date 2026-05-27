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

from src.annotation_serialization import annotation_to_dict, decode_worldview_tag
from src.annotators_loader import (
    AnnotatorsConfigError,
    get_annotator,
    list_pending_annotators,
    set_status,
)
from src.arbitration import (
    ARBITRATED_FIELDS,
    bulk_load_arbitrations_by_audio,
    is_blind_audit,
    write_arbitration,
)
from src.audiofile_status import (
    bulk_load_annotations_by_audio,
    compute_audiofile_status,
    compute_status_from_preload,
    resolve_role_map,
    status_summary,
)
from src.role_gaps import needs_full_arbitration, pairwise_gaps
from src.db import get_session
from src.dimensions_loader import load_dimensions
from src.middleware import require_auth
from src.models import Annotation, Arbitration, AudioFile
from pydantic import BaseModel, Field as PydField

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

_GOLD_LOCK_RETIRED = (
    "gold lock 已退役；creator_ready 改由 arbitration 衍生（見 Phase 3 spec）。"
)


@router.post("/audio/{audio_id}/lock_gold")
def lock_audio_gold(
    audio_id: str,  # noqa: ARG001
    current_user: dict[str, Any] = Depends(require_auth),  # noqa: ARG001
    session: Session = Depends(get_session),  # noqa: ARG001
) -> dict[str, Any]:
    """已退役 — 三角架構用 arbitration 取代手動 gold lock。"""
    raise HTTPException(status_code=410, detail=_GOLD_LOCK_RETIRED)


@router.post("/audio/{audio_id}/unlock_gold")
def unlock_audio_gold(
    audio_id: str,  # noqa: ARG001
    current_user: dict[str, Any] = Depends(require_auth),  # noqa: ARG001
    session: Session = Depends(get_session),  # noqa: ARG001
) -> dict[str, Any]:
    """已退役 — 見 lock_gold。"""
    raise HTTPException(status_code=410, detail=_GOLD_LOCK_RETIRED)


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


# ─── Phase 12-A：lockable 清單(給 Amber 一鍵 lock gold)─────────────

def _max_creator_industry_gap(
    anns: list[Annotation], role_map: dict[str, Any],
) -> tuple[str | None, float | None]:
    """回 (dim, value)：該音檔 creator-industry gap 最大的維度。缺一方 → (None, None)。"""
    by_id = {a.annotator_id: a for a in anns}
    by_role = {
        "creator": by_id.get(role_map.get("creator")),
        "industry": by_id.get(role_map.get("industry")),
        "audience": by_id.get(role_map.get("audience")),
    }
    gaps = pairwise_gaps(by_role)
    ci = {d: g["creator_industry"] for d, g in gaps.items() if g["creator_industry"] is not None}
    if not ci:
        return None, None
    max_dim = max(ci, key=ci.get)
    return max_dim, ci[max_dim]


@router.get("/lockable/list")
def lockable_list(
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """列出 status=fast_confirmable 的音檔（creator-industry 已對齊，待 Amber 批次快速確認）。

    Sort by creator-industry gap asc — 最對齊的優先。
    （注：gold lock 已退役；此清單供 Phase 4 批次快速確認 UI 用。）
    """
    _require_admin(current_user)

    role_map = resolve_role_map()
    audios = session.exec(select(AudioFile)).all()
    by_audio = bulk_load_annotations_by_audio(session)
    arbs_by_audio = bulk_load_arbitrations_by_audio(session)
    items: list[dict[str, Any]] = []
    for audio in audios:
        anns = by_audio.get(audio.id, [])
        st = compute_status_from_preload(audio, anns, arbs_by_audio.get(audio.id, []), role_map)
        if st != "fast_confirmable":
            continue
        max_dim, max_val = _max_creator_industry_gap(anns, role_map)
        items.append({
            "audio_id": audio.id,
            "filename": audio.filename,
            "game_name": audio.game_name,
            "game_stage": audio.game_stage,
            "duration_sec": audio.duration_sec,
            "annotators": sorted({a.annotator_id for a in anns}),
            "max_gap_dim": max_dim,
            "max_gap_value": max_val,
            "blind_audit": is_blind_audit(audio.id),  # 抽中者不可快速確認，須走 full
        })
    items.sort(key=lambda x: (x["max_gap_value"] or 0))
    return items


# ─── Phase 11：仲裁(reconciliation)— Amber 看 cross_annotated 並更新自己 annotation ─

@router.get("/reconcile/list")
def reconcile_list(
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """列出 status=needs_arbitration 的音檔 + 每筆 creator-industry gap 最大維度。

    Sort by gap desc — 差距大的優先處理。
    """
    _require_admin(current_user)

    role_map = resolve_role_map()
    audios = session.exec(select(AudioFile)).all()
    by_audio = bulk_load_annotations_by_audio(session)
    arbs_by_audio = bulk_load_arbitrations_by_audio(session)
    items: list[dict[str, Any]] = []
    for audio in audios:
        anns = by_audio.get(audio.id, [])
        st = compute_status_from_preload(audio, anns, arbs_by_audio.get(audio.id, []), role_map)
        audit = is_blind_audit(audio.id)
        # needs_arbitration 必收；fast_confirmable 但被盲審抽中者也納入（強制走 full）
        if not (st == "needs_arbitration" or (st == "fast_confirmable" and audit)):
            continue
        max_dim, max_val = _max_creator_industry_gap(anns, role_map)
        items.append({
            "audio_id": audio.id,
            "filename": audio.filename,
            "game_name": audio.game_name,
            "game_stage": audio.game_stage,
            "duration_sec": audio.duration_sec,
            "annotators": sorted({a.annotator_id for a in anns}),
            "max_gap_dim": max_dim,
            "max_gap_value": max_val,
            "amber_already_annotated": any(a.annotator_id == "amber" for a in anns),
            "blind_audit": audit,
        })
    items.sort(key=lambda x: (x["max_gap_value"] or 0), reverse=True)
    return items


@router.get("/reconcile/{audio_id}")
def reconcile_detail(
    audio_id: str,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """單筆仲裁所需資料:audio metadata + 所有 annotation。

    儲存走 POST /api/annotations(annotator_id="amber") — 不另開 save endpoint。
    """
    _require_admin(current_user)

    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔:{audio_id}")

    anns = session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()

    def _decode(s):
        if not s:
            return []
        try:
            import json as _json  # noqa: PLC0415
            v = _json.loads(s)
            return v if isinstance(v, list) else []
        except Exception:  # noqa: BLE001
            return []

    annotations_data = [
        {
            "annotator_id": a.annotator_id,
            "valence": a.valence,
            "arousal": a.arousal,
            "emotional_warmth": a.emotional_warmth,
            "tension_direction": a.tension_direction,
            "temporal_position": a.temporal_position,
            "event_significance": a.event_significance,
            "world_immersion": a.world_immersion,
            "loop_capability": _decode(a.loop_capability),
            "source_type": _decode(a.source_type),
            "function_roles": _decode(a.function_roles),
            "genre_tag": _decode(a.genre_tag),
            "worldview_tag": decode_worldview_tag(a.worldview_tag),
            "style_tag": _decode(a.style_tag),
            "notes": a.notes,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        for a in anns
    ]

    # Phase 4：per-dim creator-industry gap + 是否需 full（Notes 強制）。
    role_map = resolve_role_map()
    by_id = {a.annotator_id: a for a in anns}
    by_role = {r: by_id.get(role_map.get(r)) for r in ("creator", "industry", "audience")}
    gaps = pairwise_gaps(by_role)
    creator_industry_gaps = {d: g["creator_industry"] for d, g in gaps.items()}
    needs_full = needs_full_arbitration(gaps)
    # blind-audit 抽中的對齊檔也要強制 Notes（A5）
    notes_required = bool(needs_full) or is_blind_audit(audio_id)

    return {
        "audio": {
            "id": audio.id,
            "filename": audio.filename,
            "game_name": audio.game_name,
            "game_stage": audio.game_stage,
            "duration_sec": audio.duration_sec,
            "is_brand_theme": audio.is_brand_theme,
            "tonal_noise_ratio_auto": audio.tonal_noise_ratio_auto,
            "spectral_density_auto": audio.spectral_density_auto,
        },
        "annotations": annotations_data,
        "status": compute_audiofile_status(audio, session),
        "creator_industry_gaps": creator_industry_gaps,
        "needs_full_dims": sorted(needs_full),
        "notes_required": notes_required,
        "blind_audit": is_blind_audit(audio_id),
    }


# ─── Phase 4：仲裁寫入（fast-confirm 批次 + full 完整）─────────────

def _completed_annotations(session: Session, audio_id: str) -> list[Annotation]:
    return list(session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all())


class FastConfirmPayload(BaseModel):
    audio_ids: list[str] = PydField(default_factory=list)
    notes: str | None = None


@router.post("/arbitrate/fast-confirm")
def arbitrate_fast_confirm(
    payload: FastConfirmPayload,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """批次快速確認：fast_confirmable 檔以 creator raw value 寫 Arbitration(path=fast)。

    重算 status 防 race；blind-audit 抽中者 skip（必須走 full）。
    """
    _require_admin(current_user)
    role_map = resolve_role_map()
    creator_id = role_map.get("creator")
    arbitrated_by = current_user.get("annotator_id") or creator_id or "amber"

    confirmed: list[str] = []
    skipped: list[dict[str, str]] = []
    for aid in payload.audio_ids:
        audio = session.get(AudioFile, aid)
        if audio is None:
            skipped.append({"audio_id": aid, "reason": "not_found"})
            continue
        if is_blind_audit(aid):
            skipped.append({"audio_id": aid, "reason": "blind_audit"})
            continue
        anns = _completed_annotations(session, aid)
        arbs = list(session.exec(
            select(Arbitration).where(Arbitration.audio_file_id == aid)
        ).all())
        if compute_status_from_preload(audio, anns, arbs, role_map) != "fast_confirmable":
            skipped.append({"audio_id": aid, "reason": "not_fast_confirmable"})
            continue
        creator_ann = next((a for a in anns if a.annotator_id == creator_id), None)
        if creator_ann is None:
            skipped.append({"audio_id": aid, "reason": "no_creator_annotation"})
            continue
        decoded = annotation_to_dict(creator_ann)
        fields_values = {f: decoded[f] for f in ARBITRATED_FIELDS}
        write_arbitration(
            session, audio_id=aid, fields_values=fields_values,
            path="fast", notes=payload.notes, arbitrated_by=arbitrated_by,
        )
        confirmed.append(aid)

    session.commit()
    log.info("fast-confirm by %s: confirmed=%d skipped=%d",
             arbitrated_by, len(confirmed), len(skipped))
    return {"confirmed": confirmed, "skipped": skipped}


class FullArbitrationPayload(BaseModel):
    values: dict[str, Any] = PydField(default_factory=dict)
    notes: str | None = None


@router.post("/arbitrate/{audio_id}/full")
def arbitrate_full(
    audio_id: str,
    payload: FullArbitrationPayload,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """完整仲裁：逐欄寫 Arbitration(path=full)。needs_full / blind-audit 檔 Notes 強制。

    不覆寫 creator raw annotation — 最終值只存 Arbitration。
    """
    _require_admin(current_user)
    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔:{audio_id}")

    role_map = resolve_role_map()
    anns = _completed_annotations(session, audio_id)
    by_id = {a.annotator_id: a for a in anns}
    if role_map.get("creator") not in by_id or role_map.get("industry") not in by_id:
        raise HTTPException(status_code=409, detail="creator 與 industry 尚未都完成，不可仲裁")

    by_role = {r: by_id.get(role_map.get(r)) for r in ("creator", "industry", "audience")}
    needs_full = needs_full_arbitration(pairwise_gaps(by_role))
    notes_required = bool(needs_full) or is_blind_audit(audio_id)
    if notes_required and not (payload.notes and payload.notes.strip()):
        raise HTTPException(status_code=400, detail="此檔需完整仲裁，Notes 必填")

    missing = [f for f in ARBITRATED_FIELDS if f not in payload.values]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺仲裁欄位:{missing}")

    arbitrated_by = current_user.get("annotator_id") or role_map.get("creator") or "amber"
    write_arbitration(
        session, audio_id=audio_id,
        fields_values={f: payload.values[f] for f in ARBITRATED_FIELDS},
        path="full", notes=payload.notes, arbitrated_by=arbitrated_by,
    )
    session.commit()
    log.info("full-arbitrate %s by %s", audio_id, arbitrated_by)
    return {"audio_id": audio_id, "status": compute_audiofile_status(audio, session)}


# ─── Phase 5：品質 flags 聚合 ─────────────────────────────────────

@router.get("/quality")
def quality_flags(
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """集合層級品質信號：industry 校準信號 / 商品證據 / audience 守門。"""
    _require_admin(current_user)
    from src.quality_flags import aggregate_quality  # noqa: PLC0415
    return aggregate_quality(session, resolve_role_map())


# ─── Phase 7：per-role 校準狀態 ──────────────────────────────────

@router.get("/calibration-status/{annotator_id}")
def calibration_status(
    annotator_id: str,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """role-aware 校準狀態（creator self-MAE / industry 對齊只上界 / audience intra-rater）。"""
    _require_admin(current_user)
    from src.role_calibration import role_aware_calibration_status  # noqa: PLC0415
    return role_aware_calibration_status(session, annotator_id)
