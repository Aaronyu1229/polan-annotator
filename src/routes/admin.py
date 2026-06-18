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

from src.annotation_serialization import decode_worldview_tag
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
from src.auto_promote import auto_promote_all
from src.role_gaps import needs_full_arbitration, pairwise_gaps
from src.db import get_session
from src.dimensions_loader import load_dimensions
from src.middleware import require_auth
from src.models import Annotation, AudioFile
from pydantic import BaseModel, Field as PydField
from datetime import datetime
from urllib.parse import quote

from fastapi import Request
from sqlalchemy import select as sa_select

from src.alignment_db import ClientLink, get_alignment_session
from src.alignment_publish import ALIGNMENT_AUDIO_DIR, publish_audio_link  # noqa: F401
from src.audio_analysis import AUDIO_DIR  # noqa: F401

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


@router.get("/export_readiness")
def export_readiness_endpoint(
    current_user: dict[str, Any] = Depends(require_auth),  # noqa: ARG001 — 純 auth gate
    session: Session = Depends(get_session),
) -> dict[str, int]:
    """兩條出貨軌可出貨筆數：Dual-View(只需 yyslin) / Expert(creator_ready)。"""
    from src.export_editions import export_readiness_summary  # noqa: PLC0415
    return export_readiness_summary(session)


@router.get("/vic_credibility")
def vic_credibility_endpoint(
    current_user: dict[str, Any] = Depends(require_auth),  # noqa: ARG001 — 純 auth gate
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """audience(Vic)可信度：方差 / 極端共識探針 / test-retest 合成狀態（Dual-View 賣點）。"""
    from src.audience_credibility import vic_credibility  # noqa: PLC0415
    return vic_credibility(session)


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


@router.post("/arbitrate/auto-promote-all")
def arbitrate_auto_promote_all(
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, list[str]]:
    """補晉升：把所有已對齊（gap 全 ≤ GATE）且非盲審的檔一次寫 Arbitration(path=auto)。

    供既有「初標完+對齊但未確認」的舊檔一次性升 creator_ready。
    盲審抽中的對齊檔不在此晉升（須走完整仲裁），列在 skipped_blind_audit。
    """
    _require_admin(current_user)
    result = auto_promote_all(session, resolve_role_map())
    session.commit()
    log.info("auto-promote-all: promoted=%d skipped_blind=%d",
             len(result["promoted"]), len(result["skipped_blind_audit"]))
    return result


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


# ─── Phase 8：agreement 分層 ─────────────────────────────────────

@router.get("/agreement")
def agreement_layers(
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """三層 agreement：業界對齊(CCC) / 三人整體(ICC 僅報告) / audience within-category。"""
    _require_admin(current_user)
    from src.agreement import compute_agreement_layers  # noqa: PLC0415
    return compute_agreement_layers(session)


# ─── BGM alignment：admin 發佈 / 列表 / 撤銷 ──────────────────────


class PublishLinkBody(BaseModel):
    filename: str
    label: str
    role: str = "client"
    annotator_id: str | None = None
    session_id: str | None = None
    expires_at: datetime | None = None
    orig_audio_id: str | None = None


@router.post("/alignment/publish")
def publish_alignment_link(
    body: PublishLinkBody,
    request: Request,
    current_user: dict[str, Any] = Depends(require_auth),
    align_db: Session = Depends(get_alignment_session),
) -> dict[str, Any]:
    """admin：把一支 ref 音檔發佈成客戶可標的連結。回明文 token（只此一次）。"""
    _require_admin(current_user)
    if body.role not in {"client", "engineer"}:
        raise HTTPException(400, f"role 必須是 client 或 engineer，收到 {body.role!r}")
    try:
        res = publish_audio_link(
            src_filename=body.filename, label=body.label, role=body.role,
            annotator_id=body.annotator_id, session_id=body.session_id,
            expires_at=body.expires_at, align_db=align_db,
            src_audio_dir=AUDIO_DIR, dst_audio_dir=ALIGNMENT_AUDIO_DIR,
            orig_audio_id=body.orig_audio_id,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    base = str(request.base_url).rstrip("/")
    return {
        "token": res.token,
        "client_url": f"{base}/alignment?token={quote(res.token)}",
        "link_id": res.link_id,
        "alignment_audio_id": res.alignment_audio_id,
        "session_id": res.session_id,
    }


@router.get("/alignment/links")
def list_alignment_links(
    current_user: dict[str, Any] = Depends(require_auth),
    align_db: Session = Depends(get_alignment_session),
) -> dict[str, Any]:
    """admin：列出所有 client link（不含 token / token_hash）。"""
    _require_admin(current_user)
    rows = align_db.scalars(sa_select(ClientLink)).all()
    return {"links": [
        {
            "id": r.id, "role": r.role, "label": r.label,
            "annotator_id": r.annotator_id, "session_id": r.session_id,
            "alignment_audio_id": r.alignment_audio_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "revoked": r.revoked,
        }
        for r in rows
    ]}


@router.post("/alignment/links/{link_id}/revoke")
def revoke_alignment_link(
    link_id: str,
    current_user: dict[str, Any] = Depends(require_auth),
    align_db: Session = Depends(get_alignment_session),
) -> dict[str, Any]:
    """admin：撤銷一條 link（立即失效）。"""
    _require_admin(current_user)
    link = align_db.get(ClientLink, link_id)
    if link is None:
        raise HTTPException(404, "找不到此連結")
    link.revoked = True
    align_db.commit()
    return {"revoked": True}
