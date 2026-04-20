"""共用 pytest fixtures。

`client` fixture 不觸發 lifespan（避免 hit 真正的 data/audio/ 與 data/annotations.db），
改用 in-memory SQLite + dependency_overrides 隔離測試。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src import main as main_module
from src.db import get_session
from src import models  # noqa: F401 — 確保 SQLModel.metadata 註冊到 AudioFile/Annotation


@pytest.fixture
def in_memory_engine():
    # StaticPool 讓所有 thread 共用同一個 :memory: 連線；
    # FastAPI sync route 會在 threadpool 跑，預設的 SingletonThreadPool 會給每個 thread
    # 一個獨立的 in-memory DB，導致測試的 create_all 被 API 呼叫看不到。
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(in_memory_engine):
    """TestClient that bypasses lifespan and uses in-memory DB."""
    def _override_session():
        with Session(in_memory_engine) as s:
            yield s

    main_module.app.dependency_overrides[get_session] = _override_session
    # 不用 `with TestClient` → 不觸發 lifespan，測試環境下 server startup 副作用全跳過
    yield TestClient(main_module.app)
    main_module.app.dependency_overrides.clear()
