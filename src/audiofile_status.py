"""AudioFile 狀態機（三角架構）+ 資料集匯出 quality 過濾邏輯。

狀態（衍生，不存 DB）。只 creator-industry gap 當闘門；audience 偏離永不 gate
（修「把預期內視角分歧當缺陷」的 bug class，取代舊的三人 max-min spread 判定）：

    untouched         無任何 is_complete annotation
    draft             只有 audience/guest 等非 creator/industry 完成
    creator_draft     creator 完成、industry 未（audience 不論）
    industry_only     industry 完成、creator 未
    needs_arbitration creator+industry 齊，且至少一連續維 creator_industry_gap > GATE 且該維未 active 仲裁
    fast_confirmable  creator+industry 齊，所有連續維 gap ≤ GATE，但尚未全欄位仲裁
    creator_ready     所有 ARBITRATED_FIELDS 都有 active 仲裁（無 stale）→ Creator Edition 可出貨

仲裁紀錄由 Phase 4 才寫入，故 Phase 3 上線後不會出現 creator_ready（預期，非 regression）。

匯出過濾（min_status）：沿用 _STATUS_ORDER；保留舊狀態 key（gold/lockable/cross_annotated）
做向後相容映射，避免舊 min_status 請求 400。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from src.annotators_loader import annotator_id_for_role
from src.arbitration import ARBITRATED_FIELDS, latest_by_audio_field
from src.models import Annotation, Arbitration, AudioFile
from src.role_gaps import needs_full_arbitration, pairwise_gaps
from src.thresholds import HUMAN_CONTINUOUS_DIMS  # re-export：既有呼叫端仍可 from audiofile_status import

__all__ = [
    "HUMAN_CONTINUOUS_DIMS", "CANONICAL_STATES", "status_meets",
    "bulk_load_annotations_by_audio", "resolve_role_map",
    "compute_status_from_preload", "compute_audiofile_status", "status_summary",
]

# 衍生狀態（canonical，用於 status_summary 計數）
CANONICAL_STATES: tuple[str, ...] = (
    "untouched", "draft", "creator_draft", "industry_only",
    "needs_arbitration", "fast_confirmable", "creator_ready",
)

# 狀態排序（min_status 比對）。新狀態 + 舊 alias（export 向後相容）。
_STATUS_ORDER = {
    "untouched": 0,
    "draft": 1, "industry_only": 1, "creator_draft": 1,
    "cross_annotated": 2, "needs_arbitration": 2,
    "lockable": 3, "fast_confirmable": 3,
    "gold": 4, "creator_ready": 4,
}


def status_meets(actual: str, minimum: str) -> bool:
    """actual 是否 ≥ minimum（用於 export filter）。"""
    return _STATUS_ORDER.get(actual, -1) >= _STATUS_ORDER.get(minimum, -1)


def resolve_role_map() -> dict[str, Optional[str]]:
    """解析 {role: annotator_id}。bulk 操作頂端呼叫一次，往下傳，勿在 per-row 迴圈呼叫。"""
    return {r: annotator_id_for_role(r) for r in ("creator", "industry", "audience")}


def bulk_load_annotations_by_audio(
    session: Session,
) -> dict[str, list[Annotation]]:
    """一次撈所有 is_complete annotation，分組 by audio_id（避免 N+1）。"""
    rows = session.exec(
        select(Annotation).where(Annotation.is_complete == True)  # noqa: E712
    ).all()
    by_audio: dict[str, list[Annotation]] = {}
    for r in rows:
        by_audio.setdefault(r.audio_file_id, []).append(r)
    return by_audio


def compute_status_from_preload(
    audio: AudioFile,
    annotations: list[Annotation],
    arbitrations: list[Arbitration],
    role_map: dict[str, Optional[str]],
) -> str:
    """三角架構衍生狀態（純函式，預載資料，不查 DB）。

    role_map: {"creator": id, "industry": id, "audience": id}（呼叫端解析一次）。
    只 creator-industry gap 當闘門；audience 偏離永不 gate。
    """
    creator_id = role_map.get("creator")
    industry_id = role_map.get("industry")

    completed = [a for a in annotations if a.is_complete]
    by_id = {a.annotator_id: a for a in completed}
    has_creator = creator_id is not None and creator_id in by_id
    has_industry = industry_id is not None and industry_id in by_id

    if not completed:
        return "untouched"
    if has_creator and not has_industry:
        return "creator_draft"
    if has_industry and not has_creator:
        return "industry_only"
    if not has_creator and not has_industry:
        return "draft"  # 只有 audience/guest 等完成

    # creator + industry 齊
    by_role = {
        "creator": by_id.get(creator_id),
        "industry": by_id.get(industry_id),
        "audience": by_id.get(role_map.get("audience")),
    }
    gaps = pairwise_gaps(by_role)
    needs_full = needs_full_arbitration(gaps)

    active = latest_by_audio_field(
        [r for r in arbitrations if r.audio_file_id == audio.id]
    )
    creator_ann = by_role["creator"]

    def _arbitrated(field: str) -> bool:
        rec = active.get((audio.id, field))
        if rec is None:
            return False
        # stale：creator 在仲裁後又改 raw annotation → 失效
        return not (creator_ann.updated_at and creator_ann.updated_at > rec.arbitrated_at)

    if all(_arbitrated(f) for f in ARBITRATED_FIELDS):
        return "creator_ready"
    unresolved_full = {d for d in needs_full if not _arbitrated(d)}
    return "needs_arbitration" if unresolved_full else "fast_confirmable"


def compute_audiofile_status(audio: AudioFile, session: Session) -> str:
    """衍生狀態（查 DB 版）。語意同 compute_status_from_preload。"""
    completed = session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio.id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    arbitrations = session.exec(
        select(Arbitration).where(Arbitration.audio_file_id == audio.id)
    ).all()
    return compute_status_from_preload(
        audio, list(completed), list(arbitrations), resolve_role_map()
    )


def status_summary(session: Session) -> dict[str, Any]:
    """Dashboard 卡用 — 一次回所有 audio 的 status 分布（bulk pre-load 避免 N+1）。"""
    from src.arbitration import bulk_load_arbitrations_by_audio  # noqa: PLC0415

    audios = session.exec(select(AudioFile)).all()
    anns_by_audio = bulk_load_annotations_by_audio(session)
    arbs_by_audio = bulk_load_arbitrations_by_audio(session)
    role_map = resolve_role_map()
    counts = {k: 0 for k in CANONICAL_STATES}
    for a in audios:
        st = compute_status_from_preload(
            a, anns_by_audio.get(a.id, []), arbs_by_audio.get(a.id, []), role_map
        )
        counts[st] = counts.get(st, 0) + 1
    counts["total"] = len(audios)
    return counts
