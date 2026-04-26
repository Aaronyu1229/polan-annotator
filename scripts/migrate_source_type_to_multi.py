"""一次性 migration：把 annotation.source_type 從 VARCHAR 單值轉成 JSON list 字串。

背景：Phase 5 #5 把 source_type（音源類型）從單選改成多選。
DB column 仍叫 source_type，但值的型別從 VARCHAR 變成 TEXT（存 JSON）。

行為：
- 已是 JSON list 字串 → 跳過（idempotent）
- 是 None / NULL → 跳過
- 是合法 enum 字串（例如 "ambience"）→ 轉成 '["ambience"]'
- 是非 enum 字串 → 仍轉但印警告

執行前請先 `cp data/annotations.db data/annotations.db.bak.<時戳>`。

用法：
    uv run python scripts/migrate_source_type_to_multi.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "annotations.db"

LEGAL_VALUES = {
    "weapon", "explosion", "impact", "character_vocal", "dialogue_vo",
    "ambience", "environmental", "mechanical_vehicle", "creature_foley",
    "synthetic_designed",
}


def _looks_like_json_list(value: str) -> bool:
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

    cur.execute("SELECT id, source_type FROM annotation")
    rows = cur.fetchall()

    converted = 0
    skipped_null = 0
    skipped_already_json = 0
    warnings: list[str] = []

    for row in rows:
        ann_id = row["id"]
        raw = row["source_type"]

        if raw is None:
            skipped_null += 1
            continue

        if isinstance(raw, str) and _looks_like_json_list(raw):
            skipped_already_json += 1
            continue

        # 應為純字串例如 "ambience"
        value = str(raw)
        if value not in LEGAL_VALUES:
            warnings.append(
                f"annotation {ann_id} 的 source_type={value!r} 不在合法 enum 內（仍會 migrate）"
            )

        new_value = json.dumps([value])
        cur.execute(
            "UPDATE annotation SET source_type = ? WHERE id = ?",
            (new_value, ann_id),
        )
        converted += 1

    conn.commit()
    conn.close()

    print("✅ migration 完成")
    print(f"  converted VARCHAR → JSON list：{converted}")
    print(f"  skipped (NULL)：{skipped_null}")
    print(f"  skipped (已是 JSON)：{skipped_already_json}")
    if warnings:
        print(f"\n⚠️  {len(warnings)} 筆警告：")
        for w in warnings:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
