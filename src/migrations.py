"""Idempotent schema migrations 在 startup 跑。

設計理由:
- SQLModel.metadata.create_all 只建「不存在的表」,**不會加 column 到既有表**。
- 加新 column 要 ALTER TABLE,SQLite 支援 ADD COLUMN 但不支援 ALTER 既有 column。
- 每次 app 啟動跑這支,所有 migration 都檢查 column 已存在就 skip → idempotent。
- 跟 Phase 10 `scripts/migrate_add_gold_lock_fields.py` 同 pattern,但自動跑不用 ssh。

新 migration 加進 `_MIGRATIONS` list 即可。每筆 (table, column, sql_def) 三元組。
"""
from __future__ import annotations

import logging

from sqlalchemy import Engine, text
from sqlmodel import Session

log = logging.getLogger("polan.migrations")

# (table, column_name, column_definition) — column_definition 不含 "column_name" 本身
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # 2026-05-21: started_at 補足「平均單筆耗時」量測 — created_at - started_at 才是真實
    # 標註花費時間(原本算 updated_at - created_at 量到的是「事後 edit 延遲」,大量為 0)。
    ("annotation", "started_at", "DATETIME"),
)


def _column_exists(session: Session, table: str, column: str) -> bool:
    """SQLite PRAGMA table_info 查 column 是否存在。"""
    rows = session.exec(text(f"PRAGMA table_info({table})")).all()  # type: ignore[arg-type]
    return any(row[1] == column for row in rows)


def apply_pending_migrations(engine: Engine) -> list[str]:
    """跑所有未套用的 migration。回剛 apply 的 column 列表(給測試/log 用)。

    SQLite ALTER TABLE ADD COLUMN 是 O(1) metadata op,不掃 rows,大表也安全。
    """
    applied: list[str] = []
    with Session(engine) as session:
        for table, column, col_def in _MIGRATIONS:
            if _column_exists(session, table, column):
                continue
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"
            log.info("applying migration: %s", sql)
            session.exec(text(sql))  # type: ignore[arg-type]
            session.commit()
            applied.append(f"{table}.{column}")
    if applied:
        log.info("migrations applied: %s", applied)
    return applied
