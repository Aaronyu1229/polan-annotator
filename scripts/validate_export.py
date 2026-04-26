"""獨立驗證 export 的 JSON 是否符合 Pōlán 資料集 schema。

故意**不依賴** FastAPI / SQLModel / src.constants — 買方拿到 dataset.json 後
能自己跑這支 script 驗格式（他們不會有我們的 repo）。enum 清單硬編碼在本檔案。

用法：
    uv run python scripts/validate_export.py path/to/dataset.json

Exit codes:
    0 — valid
    1 — 有 errors（訊息印到 stdout，方便 CI 抓）
    2 — 參數錯誤

⚠️ Schema contract：
    EXPECTED_SOURCE_TYPES / EXPECTED_FUNCTION_ROLES / EXPECTED_DIMENSIONS
    必須與 src/constants.py / src/dimensions_config.json 對齊。任一邊變動
    都要同步更新另一邊。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_VERSION = "0.1.0"

EXPECTED_CONTINUOUS_DIMENSIONS: list[str] = [
    "valence", "arousal", "emotional_warmth", "tension_direction",
    "temporal_position", "event_significance",
    "tonal_noise_ratio", "spectral_density", "world_immersion",
]

# loop_capability 是 multi_discrete：值為 list[float]，元素限於 {0, 0.5, 1}
EXPECTED_MULTI_DISCRETE_DIMENSIONS: list[str] = ["loop_capability"]

EXPECTED_DIMENSIONS: list[str] = (
    EXPECTED_CONTINUOUS_DIMENSIONS + EXPECTED_MULTI_DISCRETE_DIMENSIONS
)

EXPECTED_SOURCE_TYPES: set[str] = {
    "weapon", "explosion", "impact", "character_vocal", "dialogue_vo",
    "ambience", "environmental", "mechanical_vehicle", "creature_foley",
    "synthetic_designed",
}

EXPECTED_FUNCTION_ROLES: set[str] = {
    "ui", "gameplay_core", "reward_feedback", "negative_feedback",
    "cinematic", "musical_sfx", "atmosphere", "hybrid",
}

EXPECTED_CONSENSUS_METHODS: set[str] = {"single_annotator", "mixed"}

# loop_capability 的合法離散值
LOOP_CAPABILITY_VALUES: set[float] = {0.0, 0.5, 1.0}


def _validate_dimensions(
    dims: dict,
    prefix: str,
    errors: list[str],
) -> None:
    """檢查 10 個維度 key 齊全：
    - 9 個連續維度：numeric，[0,1]
    - loop_capability：list[float]，每個值在 {0, 0.5, 1}，至少 1 個
    """
    for dim in EXPECTED_CONTINUOUS_DIMENSIONS:
        if dim not in dims:
            errors.append(f"{prefix}.{dim} missing")
            continue
        v = dims[dim]
        if v is None:
            errors.append(f"{prefix}.{dim} is null (completed annotation should have all 9 continuous dims)")
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            errors.append(f"{prefix}.{dim} not numeric: {v!r}")
            continue
        if not (0 <= v <= 1):
            errors.append(f"{prefix}.{dim} out of [0,1]: {v}")

    for dim in EXPECTED_MULTI_DISCRETE_DIMENSIONS:
        if dim not in dims:
            errors.append(f"{prefix}.{dim} missing")
            continue
        v = dims[dim]
        if not isinstance(v, list):
            errors.append(f"{prefix}.{dim} must be list, got {type(v).__name__}")
            continue
        if len(v) == 0:
            errors.append(f"{prefix}.{dim} empty (completed annotation needs >=1 selection)")
            continue
        for item in v:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                errors.append(f"{prefix}.{dim} contains non-numeric: {item!r}")
            elif item not in LOOP_CAPABILITY_VALUES:
                errors.append(
                    f"{prefix}.{dim} value {item} not in {{0.0, 0.5, 1.0}}"
                )


def _validate_function_roles(roles, prefix: str, errors: list[str]) -> None:
    if not isinstance(roles, list):
        errors.append(f"{prefix}.function_roles not list: {type(roles).__name__}")
        return
    if len(roles) == 0:
        errors.append(f"{prefix}.function_roles empty (must have >=1)")
    for r in roles:
        if r not in EXPECTED_FUNCTION_ROLES:
            errors.append(f"{prefix}.function_roles invalid value: {r!r}")


def _validate_source_type(value, prefix: str, errors: list[str]) -> None:
    """source_type 是 list[str]：completed annotation 至少 1 個值，每個值在 enum 裡。"""
    if not isinstance(value, list):
        errors.append(f"{prefix}.source_type not list: {type(value).__name__}")
        return
    if len(value) == 0:
        errors.append(f"{prefix}.source_type empty (must have >=1)")
    for s in value:
        if s not in EXPECTED_SOURCE_TYPES:
            errors.append(f"{prefix}.source_type invalid value: {s!r}")


def _validate_item(item: dict, i: int, errors: list[str]) -> None:
    prefix = f"items[{i}]"

    if "audio_file" not in item:
        errors.append(f"{prefix}.audio_file missing")

    consensus = item.get("consensus")
    if not isinstance(consensus, dict):
        errors.append(f"{prefix}.consensus missing or not object")
        return

    _validate_dimensions(
        consensus.get("dimensions", {}),
        f"{prefix}.consensus.dimensions",
        errors,
    )
    _validate_source_type(consensus.get("source_type"), f"{prefix}.consensus", errors)
    _validate_function_roles(consensus.get("function_roles"), f"{prefix}.consensus", errors)

    # consensus_method
    method = item.get("consensus_method")
    if method not in EXPECTED_CONSENSUS_METHODS:
        errors.append(
            f"{prefix}.consensus_method invalid: {method!r} "
            f"(expected one of {sorted(EXPECTED_CONSENSUS_METHODS)})"
        )

    # individual_annotations
    inds = item.get("individual_annotations")
    if not isinstance(inds, list) or len(inds) == 0:
        errors.append(f"{prefix}.individual_annotations missing or empty")
        return

    # single_annotator ↔ 1 筆、mixed ↔ ≥2 筆 — 互相檢查
    if method == "single_annotator" and len(inds) != 1:
        errors.append(
            f"{prefix}.consensus_method=single_annotator but individual_annotations has {len(inds)}"
        )
    if method == "mixed" and len(inds) < 2:
        errors.append(
            f"{prefix}.consensus_method=mixed but individual_annotations has {len(inds)}"
        )

    for j, ind in enumerate(inds):
        ind_prefix = f"{prefix}.individual_annotations[{j}]"
        if "annotator_id" not in ind:
            errors.append(f"{ind_prefix}.annotator_id missing")
        _validate_dimensions(
            ind.get("dimensions", {}),
            f"{ind_prefix}.dimensions",
            errors,
        )
        _validate_source_type(ind.get("source_type"), ind_prefix, errors)
        _validate_function_roles(ind.get("function_roles"), ind_prefix, errors)


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"File is not valid JSON: {e}"]
    except OSError as e:
        return [f"Cannot read file: {e}"]

    # 頂層必須欄位
    for key in (
        "schema_version", "generated_at", "generator", "dataset_name",
        "total_audio_files", "total_annotated", "total_annotations",
        "annotators", "dimension_schema", "items",
    ):
        if key not in data:
            errors.append(f"Missing top-level key: {key}")

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version mismatch: got {data.get('schema_version')!r}, "
            f"expected {SCHEMA_VERSION!r}"
        )

    if not isinstance(data.get("items"), list):
        errors.append("items must be a list")
        return errors

    for i, item in enumerate(data["items"]):
        _validate_item(item, i, errors)

    return errors


def _summarize(data: dict) -> str:
    return (
        f"{len(data.get('items', []))} items, "
        f"{data.get('total_annotations', '?')} annotations, "
        f"{len(data.get('annotators', []))} annotators"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: validate_export.py <path-to-dataset.json>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    errors = validate(path)

    if errors:
        print(f"❌ {len(errors)} validation error(s) in {path}:\n")
        for e in errors:
            print(f"  - {e}")
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"✅ Valid. {_summarize(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
