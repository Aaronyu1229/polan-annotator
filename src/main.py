"""FastAPI app entry point。

啟動流程（lifespan）：
1. 設定 logging
2. 建表（idempotent）
3. 掃描 data/audio/ 並 upsert 新檔案

執行：
    uvicorn src.main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from src.audio_scanner import scan_audio_directory
from src.db import create_db, engine
from src.routes import annotations, audio, dimensions, export, tag_suggestions

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"

log = logging.getLogger("polan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    create_db()
    with Session(engine) as session:
        result = scan_audio_directory(session)
    log.info(
        "啟動掃描完成 — 磁碟上 %d 檔，新增 %d，跳過 %d",
        result.total_on_disk,
        len(result.added),
        len(result.skipped),
    )
    yield


app = FastAPI(title="珀瀾聲音標註工具", version="0.1.0", lifespan=lifespan)
app.include_router(dimensions.router)
app.include_router(audio.router)
app.include_router(annotations.router)
app.include_router(tag_suggestions.router)
app.include_router(export.router)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/annotate", include_in_schema=False)
def annotate_legacy() -> FileResponse:
    """向後相容 Phase 1 的 /annotate?audio_id=... 連結；Phase 2 主路由改 path param。"""
    return FileResponse(STATIC_DIR / "annotate.html")


@app.get("/annotate/{audio_id}", include_in_schema=False)
def annotate_page(audio_id: str) -> FileResponse:  # noqa: ARG001 — 路徑參數由前端 JS 解析
    return FileResponse(STATIC_DIR / "annotate.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
