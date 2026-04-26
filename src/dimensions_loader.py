"""dimensions_config.json loader。

伺服器啟動時呼叫 load_dimensions() 讓 JSON 錯誤早期爆炸（fail-fast）。
修改 JSON 後需要重啟 uvicorn 才會生效 — 這是刻意設計，
讓 Amber 調整定義 → 重啟 → 立即看到新版，不用改 Python code。
"""
from functools import lru_cache
from pathlib import Path
import json
from typing import Any

_CONFIG_PATH = Path(__file__).resolve().parent / "dimensions_config.json"

_REQUIRED_FIELDS = frozenset({
    "label_zh", "category", "type", "definition",
    "low_anchor", "high_anchor", "amber_confirmed",
})
_VALID_CATEGORIES = frozenset({"emotion", "function", "acoustic"})
_VALID_TYPES = frozenset({"continuous", "discrete", "multi_discrete"})


class DimensionsConfigError(ValueError):
    """dimensions_config.json 格式不符預期。"""


def _validate(config: dict[str, Any]) -> None:
    if not config:
        raise DimensionsConfigError("dimensions_config.json 至少需要一個維度")
    for dim_id, spec in config.items():
        if not isinstance(spec, dict):
            raise DimensionsConfigError(f"維度 {dim_id!r} 定義必須是物件")
        missing = _REQUIRED_FIELDS - spec.keys()
        if missing:
            raise DimensionsConfigError(
                f"維度 {dim_id!r} 缺少必要欄位：{sorted(missing)}"
            )
        if spec["category"] not in _VALID_CATEGORIES:
            raise DimensionsConfigError(
                f"維度 {dim_id!r} 的 category={spec['category']!r} 不合法，"
                f"合法值：{sorted(_VALID_CATEGORIES)}"
            )
        if spec["type"] not in _VALID_TYPES:
            raise DimensionsConfigError(
                f"維度 {dim_id!r} 的 type={spec['type']!r} 不合法，"
                f"合法值：{sorted(_VALID_TYPES)}"
            )
        if spec["type"] == "continuous":
            rng = spec.get("range")
            if (
                not isinstance(rng, list)
                or len(rng) != 2
                or not all(isinstance(v, (int, float)) for v in rng)
                or rng[0] >= rng[1]
            ):
                raise DimensionsConfigError(
                    f"維度 {dim_id!r} (continuous) 需要 range=[low, high] 且 low < high"
                )
        else:  # discrete / multi_discrete
            options = spec.get("options")
            if not isinstance(options, list) or not options:
                raise DimensionsConfigError(
                    f"維度 {dim_id!r} ({spec['type']}) 需要 options 非空陣列"
                )


@lru_cache(maxsize=1)
def load_dimensions() -> dict[str, Any]:
    """讀取並驗證 dimensions_config.json，回傳整份 config dict。"""
    if not _CONFIG_PATH.exists():
        raise DimensionsConfigError(f"找不到 {_CONFIG_PATH}")
    try:
        config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DimensionsConfigError(f"{_CONFIG_PATH} 非合法 JSON：{e}") from e
    if not isinstance(config, dict):
        raise DimensionsConfigError("dimensions_config.json 根層必須是 object")
    _validate(config)
    return config


def get_dimension(dim_id: str) -> dict[str, Any]:
    """取單一維度 spec；未知 id 拋 KeyError。"""
    config = load_dimensions()
    if dim_id not in config:
        raise KeyError(dim_id)
    return config[dim_id]


def list_dimension_ids() -> list[str]:
    """依 JSON 中出現的順序回傳維度 id list。"""
    return list(load_dimensions().keys())
