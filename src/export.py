"""Phase 4 資料集匯出 — aggregation 核心邏輯。

公開 API：
    build_dataset(session, annotator_filter=None) -> dict

歸約規則（aggregation rules）：
    連續維度 9 個                → mean，round 到 3 位小數
    loop_capability（multi_discrete）→ union，回 list[float]（值限於 0/0.5/1）
    source_type（單選）          → mode；平手 → None + warnings=["source_type_conflict"]
    function_roles（多選）       → union，dedupe 保留首現順序
    genre_tag（多選）            → union
    worldview_tag                → mode；平手 → None
    style_tag（多選）            → union
    notes                        → 不合併，只留在 individual_annotations

過濾：只取 is_complete=1 的 annotation。一檔全 incomplete → 整檔不進 items。
"""
from __future__ import annotations

import json
from collections import Counter, OrderedDict
from datetime import UTC, datetime
from typing import Any, Optional

from sqlmodel import Session, select

from src.dimensions_loader import list_dimension_ids, load_dimensions
from src.models import AudioFile, Annotation

SCHEMA_VERSION = "0.1.0"
GENERATOR = "polan-annotator/phase4"
DATASET_NAME = "polan_calibration_v0"

# 10 個維度 key 的標準順序；由 dimensions_config.json 決定，不在這裡硬編碼。
# 用函式而非 module-level 呼叫，避免 import-time side effect。
def _dimension_keys() -> list[str]:
    return list_dimension_ids()


# 目前 multi_discrete 只有 loop_capability；若未來新增，這段仍自動處理。
def _multi_discrete_dim_keys() -> set[str]:
    return {k for k, spec in load_dimensions().items() if spec.get("type") == "multi_discrete"}


class ExportError(ValueError):
    """export 階段資料格式異常（如 JSON decode 失敗）。"""


# ---------------------------------------------------------------------------
# 基礎歸約函式
# ---------------------------------------------------------------------------

def _mean_continuous(values: list[float]) -> Optional[float]:
    """round 到 3 位小數；空 list 回 None。

    精度選擇 3 位小數的理由：滑桿 step=0.05，任何 n 筆平均後最多 3 位有意義小數。
    更多位數是假精確，讀者會誤以為我們的量尺有次毫米刻度。
    """
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _mode_or_tie(
    values: list[Any],
    tie_value: Any = None,
) -> tuple[Any, bool]:
    """回 (mode_value, is_tie)。

    - 空 list → (None, False)
    - 單一頂票 → (that_value, False)
    - 多個並列頂票 → (tie_value, True)

    刻意不用 statistics.mode — 它在 tie 時回「第一個看到的」，會產生無意中的
    字母序偏向，這是 source_type conflict 判定要明確避免的行為。
    """
    if not values:
        return None, False
    counts = Counter(values)
    top_freq = max(counts.values())
    top_values = [v for v, c in counts.items() if c == top_freq]
    if len(top_values) == 1:
        return top_values[0], False
    return tie_value, True


def _union_ordered(lists: list[list]) -> list:
    """多選欄位 union，dedupe 保留首現順序。

    OrderedDict.fromkeys 的 trick 比 set 穩定：同輸入 → 同輸出順序，方便 diff 和測試。
    """
    seen: OrderedDict = OrderedDict()
    for lst in lists:
        for item in lst:
            seen.setdefault(item, None)
    return list(seen.keys())


def _decode_multi_field(
    raw: Optional[str],
    ann_id: str,
    field: str,
    *,
    cast: type = str,
) -> list:
    """DB 裡多選欄位（function_roles / style_tag / loop_capability / genre_tag）
    存 JSON string → decode 成 list。

    None / 空字串 → []。JSON decode 失敗 → raise ExportError，訊息包含 annotation.id
    和欄位名（fail loud，方便追到壞資料哪筆）。

    cast：list 元素的型別（loop_capability 用 float，其他用 str）。
    """
    if raw is None or raw == "":
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ExportError(
            f"annotation {ann_id!r} 的 {field} 欄位非合法 JSON：{e.msg}"
        ) from e
    if not isinstance(value, list):
        raise ExportError(
            f"annotation {ann_id!r} 的 {field} 欄位 decode 後不是 list：{type(value).__name__}"
        )
    return [cast(v) for v in value]


# ---------------------------------------------------------------------------
# 單筆 annotation / item 組裝
# ---------------------------------------------------------------------------

def _annotation_to_individual(ann: Annotation, dim_keys: list[str]) -> dict[str, Any]:
    """把單一 Annotation row 攤成 JSON 物件（放進 individual_annotations）。

    multi_discrete 欄位（loop_capability）在 DB 是 JSON-string，decode 成 list[float]。
    """
    dimensions: dict[str, Any] = {}
    for k in dim_keys:
        if k == "loop_capability":
            dimensions[k] = _decode_multi_field(ann.loop_capability, ann.id, k, cast=float)
        else:
            dimensions[k] = getattr(ann, k)
    return {
        "annotator_id": ann.annotator_id,
        # updated_at 語意：「最近一次確認這筆標註的時間」。見 README export 章節。
        "annotated_at": ann.updated_at.isoformat(),
        "dimensions": dimensions,
        "source_type": ann.source_type,
        "function_roles": _decode_multi_field(ann.function_roles, ann.id, "function_roles"),
        "genre_tag": _decode_multi_field(ann.genre_tag, ann.id, "genre_tag"),
        "worldview_tag": ann.worldview_tag,
        "style_tag": _decode_multi_field(ann.style_tag, ann.id, "style_tag"),
        "notes": ann.notes,
    }


def _aggregate_consensus(
    inds: list[dict[str, Any]],
    dim_keys: list[str],
    multi_discrete_keys: set[str],
) -> tuple[dict[str, Any], list[str], str]:
    """從 individual_annotations 推 consensus、warnings、consensus_method。

    回 (consensus_block, warnings_list, consensus_method)。
    """
    warnings: list[str] = []

    # dimensions：連續取 mean、multi_discrete 取 union
    dims: dict[str, Any] = {}
    for k in dim_keys:
        if k in multi_discrete_keys:
            # loop_capability：union 所有 annotator 的選項；空 list → []
            dims[k] = _union_ordered([ind["dimensions"][k] for ind in inds])
            continue
        vals = [ind["dimensions"][k] for ind in inds if ind["dimensions"][k] is not None]
        if not vals:
            dims[k] = None
            continue
        dims[k] = _mean_continuous(vals)

    # source_type：mode，tie → None + warning
    src_values = [ind["source_type"] for ind in inds if ind["source_type"] is not None]
    source_type, source_tie = _mode_or_tie(src_values, tie_value=None)
    if source_tie:
        warnings.append("source_type_conflict")

    # function_roles / genre_tag / style_tag：union
    function_roles = _union_ordered([ind["function_roles"] for ind in inds])
    genre_tag = _union_ordered([ind["genre_tag"] for ind in inds])
    style_tag = _union_ordered([ind["style_tag"] for ind in inds])

    # worldview_tag：mode，tie → None
    worldview_values = [ind["worldview_tag"] for ind in inds if ind["worldview_tag"]]
    worldview_tag, _ = _mode_or_tie(worldview_values, tie_value=None)

    consensus = {
        "dimensions": dims,
        "source_type": source_type,
        "function_roles": function_roles,
        "genre_tag": genre_tag,
        "worldview_tag": worldview_tag,
        "style_tag": style_tag,
    }

    # consensus_method 語意見 README：single_annotator vs mixed（連續 mean + 離散 mode + 多選 union）
    method = "single_annotator" if len(inds) == 1 else "mixed"

    return consensus, warnings, method


def _build_item(
    audio: AudioFile,
    anns: list[Annotation],
    dim_keys: list[str],
    multi_discrete_keys: set[str],
) -> dict[str, Any]:
    """組裝 items[] 的單一元素。`anns` 必須已過濾 is_complete=1 且非空。"""
    inds = [_annotation_to_individual(a, dim_keys) for a in anns]
    consensus, warnings, method = _aggregate_consensus(inds, dim_keys, multi_discrete_keys)

    item: dict[str, Any] = {
        "audio_file": audio.filename,
        "audio_metadata": {
            "game_name": audio.game_name,
            "game_stage": audio.game_stage,
            "is_brand_theme": audio.is_brand_theme,
            "duration_sec": audio.duration_sec,
            "bpm": audio.bpm,
            "sample_rate": audio.sample_rate,
        },
        "consensus": consensus,
        "consensus_method": method,
        "individual_annotations": inds,
        # auto_computed 是 librosa 建議值（AudioFile.*_auto），**不等於** consensus 裡
        # 人工標註的 tonal_noise_ratio / spectral_density。兩者並列方便買方比對。
        "auto_computed": {
            "tonal_noise_ratio_suggested": audio.tonal_noise_ratio_auto,
            "spectral_density_suggested": audio.spectral_density_auto,
        },
    }
    if warnings:
        item["warnings"] = warnings
    return item


# ---------------------------------------------------------------------------
# dimension_schema（給買方 parser 用的 metadata）
# ---------------------------------------------------------------------------

def _build_dimension_schema() -> dict[str, Any]:
    """從 dimensions_config.json 派生 type/range/category 的精簡版本。"""
    config = load_dimensions()
    out: dict[str, Any] = {}
    for dim_id, spec in config.items():
        entry: dict[str, Any] = {
            "type": spec["type"],
            "category": spec["category"],
        }
        if spec["type"] == "continuous":
            entry["range"] = spec.get("range")
        else:  # discrete / multi_discrete
            entry["options"] = spec.get("options")
        out[dim_id] = entry
    return out


# ---------------------------------------------------------------------------
# 頂層：build_dataset
# ---------------------------------------------------------------------------

def build_dataset(
    session: Session,
    annotator_filter: Optional[str] = None,
) -> dict[str, Any]:
    """產生一份完整 dataset JSON 結構（dict，call site 負責序列化）。

    annotator_filter:
        None  → 全員 annotation，items 裡每檔的 consensus 為多人共識。
        str   → 只保留 annotator_id == 該值的 annotation，consensus 直接 = 該 annotator 值。

    個別 endpoint 各自決定呼叫方式：
        /api/export/dataset.json          → build_dataset(session)
        /api/export/calibration_set.json  → build_dataset(session, "amber")
        /api/export/individual.json       → build_dataset(session, "<id>")（404 由 route 層判斷）
    """
    dim_keys = _dimension_keys()
    multi_discrete_keys = _multi_discrete_dim_keys()

    audios = session.exec(
        select(AudioFile).order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()

    ann_stmt = select(Annotation).where(Annotation.is_complete == True)  # noqa: E712
    if annotator_filter is not None:
        ann_stmt = ann_stmt.where(Annotation.annotator_id == annotator_filter)
    annotations = session.exec(ann_stmt).all()

    # group by audio_file_id
    by_audio: dict[str, list[Annotation]] = {}
    for a in annotations:
        by_audio.setdefault(a.audio_file_id, []).append(a)

    items: list[dict[str, Any]] = []
    for audio in audios:
        anns = by_audio.get(audio.id, [])
        if not anns:
            # 整檔沒任何 is_complete=1 的 annotation → 不進 items，但仍計入 total_audio_files
            continue
        items.append(_build_item(audio, anns, dim_keys, multi_discrete_keys))

    annotators_in_export = sorted({a.annotator_id for a in annotations})

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "generator": GENERATOR,
        "dataset_name": DATASET_NAME,
        "total_audio_files": len(audios),
        "total_annotated": len(items),
        "total_annotations": len(annotations),
        "annotators": annotators_in_export,
        "dimension_schema": _build_dimension_schema(),
        "items": items,
    }


def count_completed_for(session: Session, annotator_id: str) -> int:
    """給 route 層判斷「該 annotator 是否有 completed annotation」用的輕量 query。

    用於 /api/export/individual.json 的 404 判定：不存在 or 存在但全 incomplete
    都應該 404，不要回 200 帶空 items。
    """
    rows = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == annotator_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    return len(rows)
