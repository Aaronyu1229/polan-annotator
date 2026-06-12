"""自動晉升：creator + industry 對齊（所有連續維 gap ≤ ARBITRATION_GATE）且非盲審
抽中的檔，於初標完成當下直接以 creator 初標值寫 Arbitration(path="auto") →
狀態變 creator_ready，免去手動「快速確認」一步。

盲審抽中的對齊檔**不**自動晉升（保留獨立判斷紀律，須走完整仲裁＋Notes）。

晉升來源以 Arbitration.path 區分，方便審計：
    auto  → 系統自動晉升（本模組）
    fast  → 手動一鍵快速確認（已退役，歷史紀錄保留）
    full  → 手動逐維仲裁
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from src.annotation_serialization import annotation_to_dict
from src.arbitration import ARBITRATED_FIELDS, is_blind_audit, write_arbitration
from src.audiofile_status import compute_status_from_preload
from src.models import Annotation, Arbitration, AudioFile

AUTO_PROMOTE_PATH = "auto"


def _completed_annotations(session: Session, audio_id: str) -> list[Annotation]:
    return list(session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all())


def _arbitrations(session: Session, audio_id: str) -> list[Arbitration]:
    return list(session.exec(
        select(Arbitration).where(Arbitration.audio_file_id == audio_id)
    ).all())


def maybe_auto_promote(
    session: Session,
    audio: AudioFile,
    role_map: dict[str, Optional[str]],
) -> bool:
    """若 audio 現為 fast_confirmable 且非盲審抽中 → 寫 Arbitration(path="auto")。

    以 creator 初標值寫入（與手動快速確認同值，差別只在 path 標記）。
    不 commit（caller 負責），回是否晉升。
    """
    if is_blind_audit(audio.id):
        return False

    anns = _completed_annotations(session, audio.id)
    arbs = _arbitrations(session, audio.id)
    if compute_status_from_preload(audio, anns, arbs, role_map) != "fast_confirmable":
        return False

    creator_id = role_map.get("creator")
    creator_ann = next((a for a in anns if a.annotator_id == creator_id), None)
    if creator_ann is None:
        return False

    decoded = annotation_to_dict(creator_ann)
    fields_values = {f: decoded[f] for f in ARBITRATED_FIELDS}
    write_arbitration(
        session, audio_id=audio.id, fields_values=fields_values,
        path=AUTO_PROMOTE_PATH, notes=None, arbitrated_by=creator_id or "amber",
    )
    return True


def auto_promote_all(
    session: Session,
    role_map: dict[str, Optional[str]],
) -> dict[str, list[str]]:
    """一次性補晉升：掃所有 audio，對齊且非盲審者全部晉升。

    回 {promoted, skipped_blind_audit}；skipped_blind_audit 只含「對齊但被盲審抽中」
    的檔（這些仍須走完整仲裁，不在此晉升）。不 commit（caller 負責）。
    """
    promoted: list[str] = []
    skipped_blind: list[str] = []
    for audio in session.exec(select(AudioFile)).all():
        anns = _completed_annotations(session, audio.id)
        arbs = _arbitrations(session, audio.id)
        if compute_status_from_preload(audio, anns, arbs, role_map) != "fast_confirmable":
            continue
        if is_blind_audit(audio.id):
            skipped_blind.append(audio.id)
            continue
        if maybe_auto_promote(session, audio, role_map):
            promoted.append(audio.id)
    return {"promoted": promoted, "skipped_blind_audit": skipped_blind}
