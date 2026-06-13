"""單一 source of truth：某標註員可見/可標的音檔集合。

原本 pending_calibration / archived 的 gating 散在多處（list_audio inline、
enforce_annotator_access、且 _next_audio_id_for 與 compute_progress 根本沒套用），
導致同一個 pending 標註員在「清單」看到 100%、在「進度卡」看到全資料集分母的矛盾，
以及「儲存並下一個」會把他導向會被 403 的校準集外音檔。

把規則收斂到這裡，所有 annotator-facing 讀取路徑共用，行為一致：
    active / 未知 id（向後相容）→ None      = 不限制（全資料集）
    archived                      → set()    = 看不到任何音檔
    pending_calibration           → 校準集    = Amber 已 is_complete 的音檔 id
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from src.annotators_loader import AnnotatorsConfigError, get_annotator
from src.models import Annotation

# 校準黃金集的參照標註員（creator）。與 enforce_annotator_access 同一定義。
CALIBRATION_REFERENCE_ID = "amber"


def calibration_set_ids(session: Session) -> set[str]:
    """校準集 = 參照標註員（Amber）已 is_complete 標過的音檔 id。"""
    rows = session.exec(
        select(Annotation.audio_file_id).where(
            Annotation.annotator_id == CALIBRATION_REFERENCE_ID,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    return set(rows)


def accessible_audio_ids(
    session: Session, annotator_id: Optional[str]
) -> Optional[set[str]]:
    """回該標註員可存取的音檔 id 集合。

    None  → 不限制（active；或 annotator 為空 / 未知 id → 向後相容比照 gate fail-open）。
    set() → 僅限這些 id（archived = 空；pending_calibration = 校準集）。
    """
    if not annotator_id:
        return None
    try:
        spec = get_annotator(annotator_id)
    except AnnotatorsConfigError:
        return None  # config 壞 → fail-open，與 enforce_annotator_access 同 fallback
    if spec is None:
        return None  # 向後相容歷史 annotator_id（如 'guest'）
    status_value = spec.get("status")
    if status_value == "archived":
        return set()
    if status_value == "pending_calibration":
        return calibration_set_ids(session)
    return None  # active
