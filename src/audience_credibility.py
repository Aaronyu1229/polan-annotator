"""audience(Vic)可信度 — 把大眾分歧「驗證過、非雜訊」做成可呈現的狀態。

合成三個訊號（缺資料的訊號回 insufficient，不拖垮整體判定）：
  1. variance        每維有沒有方差（不是整排同值）          → quality_flags.audience_straight_lining
  2. extreme_consensus 極端共識探針：錨點(yyslin)給極端值的題，  → 本檔（新）
                       Vic 有沒有跨中線到相反半邊（亂掉）
  3. intra_rater     同檔標兩次自己穩不穩（test-retest）       → role_calibration.audience_intra_rater
                     需埋重複題才有資料；現階段多半 insufficient

整體狀態：
  trusted       兩個核心訊號(variance + extreme)都有資料且通過（intra 有就一併要求）
  watch         只有一個核心訊號有資料且通過（另一個資料不足）
  suspect       任一有資料的訊號 fail
  insufficient  兩個核心訊號都資料不足
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session

from src.audiofile_status import bulk_load_annotations_by_audio, resolve_role_map
from src.models import Annotation
from src.quality_flags import audience_straight_lining
from src.role_calibration import audience_intra_rater
from src.thresholds import (
    AUDIENCE_EXTREME_HIGH,
    AUDIENCE_EXTREME_LOW,
    AUDIENCE_EXTREME_MAX_VIOLATION,
    AUDIENCE_EXTREME_MIN_PROBES,
    HUMAN_CONTINUOUS_DIMS,
)

_MIDLINE = 0.5


def extreme_consensus_sanity(
    session: Session,
    role_map: dict[str, Optional[str]],
) -> dict[str, Any]:
    """錨點(industry=yyslin)給極端值的 (音檔,維度)，Vic 是否跨中線到相反半邊。

    探針：錨點 ≤ AUDIENCE_EXTREME_LOW 或 ≥ AUDIENCE_EXTREME_HIGH 的題。
    違反：錨點低極端但 Vic > 0.5，或錨點高極端但 Vic < 0.5（恰 0.5 不算）。
    合法分歧（Vic 在同半邊、只是沒那麼極端）**不算**違反。
    """
    industry_id = role_map.get("industry")
    audience_id = role_map.get("audience")
    if not industry_id or not audience_id:
        return {"checked": 0, "violations": 0, "violation_rate": None,
                "pass": None, "insufficient": True, "violation_audio_ids": []}

    anns_by_audio = bulk_load_annotations_by_audio(session)
    checked = 0
    violations = 0
    violation_audio_ids: list[str] = []
    for audio_id, anns in anns_by_audio.items():
        by_id = {a.annotator_id: a for a in anns}
        anchor = by_id.get(industry_id)
        vic = by_id.get(audience_id)
        if anchor is None or vic is None:
            continue
        file_violated = False
        for dim in HUMAN_CONTINUOUS_DIMS:
            av = getattr(anchor, dim, None)
            vv = getattr(vic, dim, None)
            if av is None or vv is None:
                continue
            if av <= AUDIENCE_EXTREME_LOW:
                checked += 1
                if vv > _MIDLINE:
                    violations += 1
                    file_violated = True
            elif av >= AUDIENCE_EXTREME_HIGH:
                checked += 1
                if vv < _MIDLINE:
                    violations += 1
                    file_violated = True
        if file_violated:
            violation_audio_ids.append(audio_id)

    insufficient = checked < AUDIENCE_EXTREME_MIN_PROBES
    rate = round(violations / checked, 3) if checked else None
    return {
        "checked": checked,
        "violations": violations,
        "violation_rate": rate,
        "pass": (None if insufficient else rate <= AUDIENCE_EXTREME_MAX_VIOLATION),
        "insufficient": insufficient,
        "violation_audio_ids": violation_audio_ids,
    }


def _overall_status(variance: dict, extreme: dict, intra: dict) -> str:
    def _failed(sig: dict, key: str) -> bool:
        # variance 用 suspect=True 表 fail；其餘用 pass=False
        if sig.get("insufficient"):
            return False
        return sig["suspect"] if key == "variance" else (sig.get("pass") is False)

    has_variance = not variance.get("insufficient")
    has_extreme = not extreme.get("insufficient")
    any_available = has_variance or has_extreme or (not intra.get("insufficient"))

    if not any_available:
        return "insufficient"
    if _failed(variance, "variance") or _failed(extreme, "extreme") or _failed(intra, "intra"):
        return "suspect"
    if has_variance and has_extreme:
        return "trusted"
    return "watch"


def vic_credibility(session: Session) -> dict[str, Any]:
    """Vic 可信度狀態 — dashboard + Dual-View 匯出共用。"""
    role_map = resolve_role_map()
    audience_id = role_map.get("audience")

    anns_by_audio = bulk_load_annotations_by_audio(session)
    audience_anns: list[Annotation] = []
    for anns in anns_by_audio.values():
        for a in anns:
            if a.annotator_id == audience_id:
                audience_anns.append(a)

    variance = audience_straight_lining(audience_anns)
    extreme = extreme_consensus_sanity(session, role_map)
    intra = (
        audience_intra_rater(session, audience_id)
        if audience_id
        else {"metric": "intra_rater", "value": None, "n": 0, "insufficient": True}
    )
    status = _overall_status(variance, extreme, intra)

    return {
        "annotator_id": audience_id,
        "status": status,
        "n_complete": len(audience_anns),
        "signals": {
            "variance": variance,
            "extreme_consensus": extreme,
            "intra_rater": intra,
        },
        "statement": _statement(status, variance, extreme, intra),
    }


def _statement(status: str, variance: dict, extreme: dict, intra: dict) -> str:
    """人話版賣點摘要（誠實標示哪些訊號還沒資料）。"""
    parts: list[str] = []
    if variance.get("insufficient"):
        parts.append("整排同值守門：資料不足")
    else:
        parts.append("整排同值守門：" + ("疑似亂標" if variance["suspect"] else "通過"))
    if extreme.get("insufficient"):
        parts.append(f"極端共識探針：僅 {extreme['checked']} 道，資料不足")
    else:
        verdict = "通過" if extreme["pass"] else "未過"
        parts.append(
            f"極端共識探針 {extreme['checked']} 道、違反率 "
            f"{extreme['violation_rate']:.0%}（{verdict}）"
        )
    if intra.get("insufficient"):
        parts.append("test-retest：待埋重複題")
    else:
        parts.append(f"test-retest 一致性 {intra['value']}（{'通過' if intra['pass'] else '未過'}）")
    return f"Vic 可信度：{status} — " + "；".join(parts) + "。"
