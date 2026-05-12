"""Phase 8 — annotators_config.json loader + state mutation.

設計理由（呼應 dimensions_loader.py 但更動態）：
- 標註團隊資訊（name / email / profile / status / is_admin）放在 `data/annotators_config.json`，
  因為 data/ 被 docker-compose bind mount，可在容器內寫入並持久到 host。
- Amber 在 Dashboard 點「認可校準通過」會呼叫 `set_status(id, "active")`，atomic 改 JSON。
- 修改 profile / 加新人時 Amber 編 JSON（git diff 看得到）。

狀態機（status 欄位合法值）：
    pending_calibration → active        ← Amber 認可後
    active              → archived      ← 離職 / 停用
    pending_calibration → archived      ← 不通過

annotator_profile 合法值：
    music_professional / general_audience / TBD_pending_amber_confirm

CLAUDE.md：不 over-engineer。沒做 admin UI 改 profile，沒做離職流程，沒做歷史審計表。
等真的有 5 人團隊再說。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("polan.annotators_loader")

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "annotators_config.json"

_REQUIRED_FIELDS = frozenset({
    "name", "email", "annotator_profile", "status", "is_admin", "joined_at",
})
_VALID_STATUS = frozenset({"pending_calibration", "active", "archived"})
_VALID_PROFILE = frozenset({
    "music_professional", "general_audience", "TBD_pending_amber_confirm",
})


class AnnotatorsConfigError(ValueError):
    """annotators_config.json 格式不符預期。"""


def _validate(config: dict[str, Any]) -> None:
    if not isinstance(config, dict) or not config:
        raise AnnotatorsConfigError("annotators_config.json 至少需要一個標註員")
    for ann_id, spec in config.items():
        if not isinstance(spec, dict):
            raise AnnotatorsConfigError(f"標註員 {ann_id!r} 定義必須是 object")
        missing = _REQUIRED_FIELDS - spec.keys()
        if missing:
            raise AnnotatorsConfigError(
                f"標註員 {ann_id!r} 缺必填欄位：{sorted(missing)}"
            )
        if spec["status"] not in _VALID_STATUS:
            raise AnnotatorsConfigError(
                f"標註員 {ann_id!r} status={spec['status']!r} 不合法，"
                f"合法值：{sorted(_VALID_STATUS)}"
            )
        if spec["annotator_profile"] not in _VALID_PROFILE:
            raise AnnotatorsConfigError(
                f"標註員 {ann_id!r} annotator_profile={spec['annotator_profile']!r} 不合法，"
                f"合法值：{sorted(_VALID_PROFILE)}"
            )


def _resolve_path(path: Path | None) -> Path:
    """path=None 時回 module-level `_CONFIG_PATH`，於呼叫時取值。

    刻意用 module attribute 查 — 讓 monkeypatch 在 test 內可改 `_CONFIG_PATH` 後生效。
    若用 `def f(path=_CONFIG_PATH)` 做預設值,default 在 import-time 就被定型,
    monkeypatch 無效;這個 bug 曾意外讓測試寫進 production 配置。
    """
    return path if path is not None else _CONFIG_PATH


def _read_config_uncached(path: Path | None = None) -> dict[str, Any]:
    """讀 + 驗 — 不 cache，每次呼叫都重新從磁碟讀。

    刻意不 cache：approve button 寫入後需立即看到新值。
    讀檔成本可忽略（3-5 個 entry，<1KB）。
    """
    path = _resolve_path(path)
    if not path.exists():
        raise AnnotatorsConfigError(
            f"找不到 {path}。Phase 8 初次部署時須確保 data/annotators_config.json 存在。"
        )
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AnnotatorsConfigError(f"{path} 非合法 JSON：{e}") from e
    _validate(config)
    return config


def load_annotators(path: Path | None = None) -> dict[str, Any]:
    """回傳完整 config（dict[annotator_id, spec]）。每次 fresh read。"""
    return _read_config_uncached(path)


def get_annotator(annotator_id: str, path: Path | None = None) -> dict[str, Any] | None:
    """取單一 spec；未知 id 回 None（呼叫端決定要 404 還是降級）。"""
    return load_annotators(path).get(annotator_id)


def list_pending_annotators(path: Path | None = None) -> list[dict[str, Any]]:
    """列所有 status=pending_calibration 的人，給 admin dashboard widget 用。"""
    config = load_annotators(path)
    return [
        {"id": ann_id, **spec}
        for ann_id, spec in config.items()
        if spec.get("status") == "pending_calibration"
    ]


def is_pending_calibration(annotator_id: str, path: Path | None = None) -> bool:
    """中介層快速查 — 未知 id 視為 False（讓 require_auth 處理鑑權，不在這層擋）。"""
    spec = get_annotator(annotator_id, path)
    return bool(spec and spec.get("status") == "pending_calibration")


def set_status(
    annotator_id: str,
    new_status: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """atomic 改某人的 status — temp file + os.replace 確保不會留 partial JSON。

    回更新後的整份 config（讓 caller 不必再 reload）。
    未知 id raise KeyError；非法 status raise ValueError。
    """
    if new_status not in _VALID_STATUS:
        raise ValueError(
            f"new_status={new_status!r} 不合法，合法值：{sorted(_VALID_STATUS)}"
        )

    path = _resolve_path(path)
    config = load_annotators(path)
    if annotator_id not in config:
        raise KeyError(annotator_id)

    config[annotator_id]["status"] = new_status

    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
    log.info("annotator %s status → %s", annotator_id, new_status)
    return config
