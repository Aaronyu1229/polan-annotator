"""BGM 對齊模式專用資料庫（data/alignment.db）。

與 data/annotations.db **檔案層級完全分離** —— 負責人要求 BGM 客戶對齊資料實體隔離，
永不進入要賣給 AI 新創的訓練資料集 pipeline。spec:
docs/superpowers/specs/2026-06-18-bgm-alignment-mode-design.md

## 為什麼這裡用純 SQLAlchemy 而非 SQLModel（刻意偏離 CLAUDE.md「DB schema 用 SQLModel」）
SQLModel 共用單一全域 `SQLModel.metadata`，無法把單一張表綁到獨立 metadata
（`registry=` kwarg 會被 SQLModel metaclass 吃掉，表反而不會註冊到任何 metadata）。
若沿用 SQLModel，`SQLModel.metadata.create_all(annotations_engine)` 會把 alignment 表
一起建進 annotations.db、且 alignment engine 也會反向建出全部主表 → 隔離失敗。
故 alignment 模型改用純 SQLAlchemy declarative base + 專屬 metadata，保證雙向不洩漏。
此為實測後的決定，理由＝實體隔離硬需求 > 風格一致性。
"""
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, Index, String, Float, Integer, DateTime
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALIGNMENT_DB_PATH = PROJECT_ROOT / "data" / "alignment.db"


def _utcnow() -> datetime:
    """timezone-aware UTC now（對齊 src/models.py 的慣例）。"""
    return datetime.now(UTC)


class AlignmentBase(DeclarativeBase):
    """專屬 metadata —— 不與 SQLModel.metadata 共用，確保檔案隔離。"""


class AlignmentReading(AlignmentBase):
    """一筆 reading = 某標註者對某音源在某時點、針對某維度的單一值。

    雙值（perceived / target）靠多 row 達成，不在同一 row 塞兩欄。
    audio_id 是對另一個資料庫 AudioFile.id 的軟參照（跨 .db 檔無法做 FK）。
    """
    __tablename__ = "alignment_reading"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, index=True)   # 哪次合作 / 哪個關卡
    annotator_id: Mapped[str] = mapped_column(String, index=True)  # 誰
    annotator_role: Mapped[str] = mapped_column(String)            # "engineer" | "client"
    audio_id: Mapped[str] = mapped_column(String, index=True)      # 軟參照 AudioFile.id
    audio_role: Mapped[str] = mapped_column(String)               # "ref" | "deliverable"
    version: Mapped[int] = mapped_column(Integer, default=0)       # ref=0；新曲 1,2,3…
    dimension: Mapped[str] = mapped_column(String)                # valence … world_immersion
    value: Mapped[float] = mapped_column(Float)                   # 0.00–1.00（範圍由 API 層驗）
    reading_type: Mapped[str] = mapped_column(String)            # "perceived" | "target"
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_alignment_session_audio", "session_id", "audio_id"),
    )


def make_alignment_engine(path: Path = ALIGNMENT_DB_PATH) -> Engine:
    """建一個指向 alignment.db 的 engine。path 可覆寫（測試用）。"""
    return create_engine(
        f"sqlite:///{path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )


engine: Engine = make_alignment_engine()


def create_alignment_db(eng: Engine = engine) -> None:
    """建立 alignment 表；idempotent。預設建在 data/alignment.db。"""
    ALIGNMENT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    AlignmentBase.metadata.create_all(eng)


def get_alignment_session() -> Session:
    """FastAPI dependency 用的 session factory（綁預設 alignment engine）。"""
    with Session(engine) as session:
        yield session
