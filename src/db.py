"""SQLite engine + session 工具。

DB 路徑寫死在 data/annotations.db，符合 Phase 1 單人使用情境。
SQLModel.metadata.create_all 會自動建表，所以不需要 Alembic migration。
"""
from pathlib import Path
from sqlmodel import SQLModel, Session, create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "annotations.db"

# check_same_thread=False 讓 FastAPI 的 thread pool 能共用同一個 engine
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


def create_db() -> None:
    """建立所有 SQLModel 表；idempotent，重複呼叫安全。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # import models 確保 metadata 註冊
    from src import models  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """FastAPI dependency 用的 session factory。"""
    with Session(engine) as session:
        yield session
