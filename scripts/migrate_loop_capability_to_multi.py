"""一次性 migration：把 annotation.loop_capability 從 FLOAT 單值轉成 JSON list 字串。

背景：Phase 5 #4 把 loop_capability 從 discrete 單選改成 multi_discrete 多選。
DB column 仍叫 loop_capability，但值的型別從 FLOAT 變成 TEXT（存 JSON）。

行為：
- 已是 JSON list 字串 → 跳過（idempotent）
- 是 None / NULL → 跳過
- 是 FLOAT（0.0 / 0.5 / 1.0）→ 轉成 "[v]" 字串
- 是 FLOAT 但非合法值 → 報錯停下（fail loud）

執行前請先 `cp data/annotations.db data/annotations.db.bak.<時戳>`。

用法：
    uv run python scripts/migrate_loop_capability_to_multi.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "annotations.db"

LEGAL_VALUES = {0.0, 0.5, 1.0}


def _looks_like_json_list(value: str) -> bool:
    """已是 JSON list 字串 → True；其他（FLOAT 字面量 / 空字串）→ False。"""
    if not value or not value.strip().startswith("["):
        return False
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, list)


def main() -> int:
    if not DB_PATH.exists():
        print(f"❌ 找不到 DB：{DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, loop_capability FROM annotation")
    rows = cur.fetchall()

    converted = 0
    skipped_null = 0
    skipped_already_json = 0
    errors: list[str] = []

    for row in rows:
        ann_id = row["id"]
        raw = row["loop_capability"]

        if raw is None:
            skipped_null += 1
            continue

        if isinstance(raw, str) and _looks_like_json_list(raw):
            skipped_already_json += 1
            continue

        # 應為 FLOAT（SQLite 會以 number 回傳）或數字字串
        try:
            value = float(raw)
        except (TypeError, ValueError):
            errors.append(f"annotation {ann_id} 的 loop_capability={raw!r} 無法轉 float")
            continue

        if value not in LEGAL_VALUES:
            errors.append(
                f"annotation {ann_id} 的 loop_capability={value} 不在 {{0, 0.5, 1}}"
            )
            continue

        new_value = json.dumps([value])
        cur.execute(
            "UPDATE annotation SET loop_capability = ? WHERE id = ?",
            (new_value, ann_id),
        )
        converted += 1

    if errors:
        print(f"❌ 發現 {len(errors)} 筆無法 migrate 的資料：")
        for e in errors:
            print(f"  - {e}")
        conn.rollback()
        conn.close()
        return 2

    conn.commit()
    conn.close()

    print("✅ migration 完成")
    print(f"  converted FLOAT → JSON list：{converted}")
    print(f"  skipped (NULL)：{skipped_null}")
    print(f"  skipped (已是 JSON)：{skipped_already_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
