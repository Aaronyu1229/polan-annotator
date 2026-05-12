"""Phase 10 — 把 AudioFile gold lock 三欄加進既有 production DB。

Idempotent:看 column 已存在就 skip。對 1311 個既有 row 都套 default(False / NULL)。

跑法:
    # 本地
    uv run python scripts/migrate_add_gold_lock_fields.py

    # VPS
    ssh root@152.42.226.237 'docker exec polan-annotator-app python scripts/migrate_add_gold_lock_fields.py'

跑前/後驗:
    sqlite3 data/annotations.db ".schema audiofile" | grep gold
"""
from __future__ import annotations

import sys

from sqlalchemy import text
from sqlmodel import Session

from src.db import engine


# 欄位定義 — 注意 SQLite ADD COLUMN 不支援 ALTER 既有 column,所以這個 script 只能 add 不能 modify
COLUMNS_TO_ADD = [
    ("is_gold_locked", "BOOLEAN NOT NULL DEFAULT 0"),
    ("gold_locked_at", "DATETIME"),
    ("gold_locked_by", "VARCHAR"),
]


def column_exists(session: Session, table: str, column: str) -> bool:
    """SQLite PRAGMA table_info 查 column 是否存在。"""
    rows = session.exec(text(f"PRAGMA table_info({table})")).all()  # type: ignore[arg-type]
    return any(row[1] == column for row in rows)


def main() -> int:
    with Session(engine) as session:
        added = []
        skipped = []
        for col_name, col_def in COLUMNS_TO_ADD:
            if column_exists(session, "audiofile", col_name):
                print(f"⏭  skip (already exists): audiofile.{col_name}")
                skipped.append(col_name)
                continue
            sql = f"ALTER TABLE audiofile ADD COLUMN {col_name} {col_def}"
            print(f"+  adding: {sql}")
            session.exec(text(sql))  # type: ignore[arg-type]
            session.commit()
            added.append(col_name)

        # 驗 column 都在
        rows = session.exec(text("PRAGMA table_info(audiofile)")).all()  # type: ignore[arg-type]
        final_cols = {row[1] for row in rows}
        missing = [c for c, _ in COLUMNS_TO_ADD if c not in final_cols]

        # 驗 row count 沒掉
        total = session.exec(text("SELECT COUNT(*) FROM audiofile")).one()  # type: ignore[arg-type]

        print()
        print(f"📊 Summary: 加 {len(added)} 欄、skip {len(skipped)} 欄、audiofile rows={total[0]}")
        if missing:
            print(f"❌ FAIL missing columns: {missing}", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
