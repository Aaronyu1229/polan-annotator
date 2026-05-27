"""Arbitration 表的讀取 / 寫入 / 序列化 helper（單一資料來源，避免各處手刻 decode）。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import Session, select

from src.models import Arbitration
from src.thresholds import HUMAN_CONTINUOUS_DIMS

# 多選欄位（存 JSON list）；其餘 ARBITRATED_FIELDS 為連續維（存 float）
_LIST_STR_FIELDS = frozenset({"source_type", "function_roles", "genre_tag",
                              "worldview_tag", "style_tag"})
_LIST_FLOAT_FIELDS = frozenset({"loop_capability"})

# 7 連續維 + loop_capability + 5 tags = 13（acoustic 兩維 librosa deterministic，不仲裁）
ARBITRATED_FIELDS: tuple[str, ...] = (
    *HUMAN_CONTINUOUS_DIMS,
    "loop_capability",
    "source_type", "function_roles", "genre_tag", "worldview_tag", "style_tag",
)


def serialize_value(field: str, value: Any) -> tuple[str, str]:
    """回 (json_str, value_type)。"""
    if field in _LIST_FLOAT_FIELDS:
        return json.dumps([float(v) for v in value]), "list_float"
    if field in _LIST_STR_FIELDS:
        return json.dumps(list(value), ensure_ascii=False), "list_str"
    return json.dumps(float(value)), "float"


def deserialize_value(raw: str, value_type: str) -> Any:
    value = json.loads(raw)
    if value_type == "float":
        return float(value)
    if value_type == "list_float":
        return [float(v) for v in value]
    return list(value)  # list_str


def latest_by_audio_field(
    rows: list[Arbitration],
) -> dict[tuple[str, str], Arbitration]:
    """同 (audio_file_id, field) 取 arbitrated_at 最大者 = active 仲裁。"""
    latest: dict[tuple[str, str], Arbitration] = {}
    for r in rows:
        key = (r.audio_file_id, r.field)
        cur = latest.get(key)
        if cur is None or r.arbitrated_at > cur.arbitrated_at:
            latest[key] = r
    return latest


def bulk_load_arbitrations_by_audio(
    session: Session,
) -> dict[str, list[Arbitration]]:
    """一次撈全部 Arbitration，分組 by audio_id（避免 status 全量計算時 N+1）。"""
    rows = session.exec(select(Arbitration)).all()
    by_audio: dict[str, list[Arbitration]] = {}
    for r in rows:
        by_audio.setdefault(r.audio_file_id, []).append(r)
    return by_audio


# ─── Phase 4: 寫入 + 盲審 ──────────────────────────────────────────

def write_arbitration(
    session: Session,
    *,
    audio_id: str,
    fields_values: dict[str, Any],
    path: str,
    notes: str | None,
    arbitrated_by: str,
) -> list[Arbitration]:
    """為一個 audio 的多個 field 各寫一筆 Arbitration（append，不刪歷史）。

    fields_values: {field: raw_value}（float 或 list）。caller 負責 commit。
    """
    rows: list[Arbitration] = []
    for field, value in fields_values.items():
        raw, value_type = serialize_value(field, value)
        row = Arbitration(
            audio_file_id=audio_id, field=field,
            arbitrated_value=raw, value_type=value_type,
            path=path, notes=notes, arbitrated_by=arbitrated_by,
        )
        session.add(row)
        rows.append(row)
    return rows


def is_blind_audit(audio_id: str) -> bool:
    """確定性抽樣 ≈12.5%（A5 fast-path 盲審）：sha1(audio_id) 末 hex ∈ {0,1}。

    抽中的 fast_confirmable 檔不可批次快速確認，必須走 full（強制 Notes），
    讓獨立判斷紀律不只在校準失敗時才出現。確定性 → 同檔每次判定一致。
    """
    digest = hashlib.sha1(audio_id.encode("utf-8")).hexdigest()  # noqa: S324 — 非密碼學用途
    return int(digest[-1], 16) < 2
