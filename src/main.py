"""FastAPI app entry point。

啟動流程（lifespan）：
1. 設定 logging
2. 建表（idempotent）
3. 掃描 data/audio/ 並 upsert 新檔案

執行：
    uvicorn src.main:app --reload --port 8000

Phase 6 加入：
- 從 env 載入 Settings → app.state.settings
- 若 OAUTH_ENABLED=true：掛 SessionMiddleware + 初始化 Authlib OAuth client
- 若 SENTRY_DSN 有值：在建立 app 之前 init Sentry
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from src.audio_scanner import scan_audio_directory
from src.alignment_db import create_alignment_db
from src.config import load_settings
from src.db import create_db, engine
from src.routes import (
    admin,
    alignment,
    annotations,
    audio,
    auth as auth_routes,
    calibration,
    dimensions,
    export,
    feedback,
    stats,
    tag_suggestions,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"

log = logging.getLogger("polan")

# Settings 在 module import 時 load 一次。fail-fast：若 OAUTH_ENABLED=true
# 但缺必填，server 起不來、不會有半開不開的狀態。
settings = load_settings()

# Sentry 必須在 app 建立前 init（FastAPI integration 會 patch）
if settings.sentry_dsn:
    try:
        import sentry_sdk  # type: ignore[import-not-found]
        from sentry_sdk.integrations.fastapi import FastApiIntegration  # type: ignore[import-not-found]

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            integrations=[FastApiIntegration()],
            traces_sample_rate=0.1,
        )
        log.info("Sentry initialized")
    except ImportError:
        log.warning(
            "SENTRY_DSN 已設置但 sentry-sdk 未安裝；跳過 Sentry init"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    create_db()
    create_alignment_db()  # BGM 對齊獨立庫 data/alignment.db（與 annotations.db 分離）
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
app.state.settings = settings

# OAuth + Session：只在 OAUTH_ENABLED=true 時掛載
if settings.oauth_enabled:
    from starlette.middleware.sessions import SessionMiddleware

    from src.auth import make_oauth

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        https_only=True,
        same_site="lax",
        session_cookie="polan_session",
    )
    app.state.oauth = make_oauth(settings)
    log.info("OAuth 已啟用 (allowed_emails=%d)", len(settings.allowed_emails))
else:
    app.state.oauth = None
    log.info("OAuth 停用 — dev 模式（query string annotator）")

app.include_router(auth_routes.router)
app.include_router(dimensions.router)
app.include_router(audio.router)
app.include_router(annotations.router)
app.include_router(tag_suggestions.router)
app.include_router(export.router)
app.include_router(stats.router)
app.include_router(feedback.router)
app.include_router(calibration.api_router)
app.include_router(calibration.page_router)
app.include_router(admin.router)
app.include_router(alignment.router)


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


@app.get("/alignment", include_in_schema=False)
def alignment_page() -> FileResponse:
    """BGM 對齊標註頁（context 由前端從 query string 解析）。"""
    return FileResponse(STATIC_DIR / "alignment.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page() -> FileResponse:
    """Phase 3：跨標註員 ICC 紅綠燈 + 重疊檔案清單。"""
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/annotator/{annotator_id}", include_in_schema=False)
def annotator_detail_page(annotator_id: str) -> FileResponse:  # noqa: ARG001 — JS 從 path 取
    """標註員詳細頁；權限由 /api/stats/annotator/{id}/detail 把關（admin-or-self）。

    比照 /calibration/report：頁面本身純 serve，真正 gate 在 API。
    """
    return FileResponse(STATIC_DIR / "annotator-detail.html")


@app.get("/upload", include_in_schema=False)
def upload_page() -> FileResponse:
    """Phase 6：admin-only 音源上傳頁。

    本 route 只 serve HTML，admin 判斷在前端 fetch /api/me 後做、
    上傳 API 後端再驗一次 — 雙閘門避免靠前端守。
    """
    return FileResponse(STATIC_DIR / "upload.html")


# Phase 13-B：admin-only HTML page 加 auth gate(非 admin → 302 redirect 到 /)
from fastapi import Depends  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402

from src.middleware import require_auth  # noqa: E402


def _ensure_admin_or_redirect(user: dict, static_file: str):
    """admin → serve HTML;非 admin → 302 redirect to /(不洩露 admin route 存在)。"""
    if not user.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(STATIC_DIR / static_file)


@app.get("/admin/review-dimensions", include_in_schema=False)
def review_dimensions_page(user: dict = Depends(require_auth)):
    """Phase 8.5(+13-B auth gate):admin-only 維度定義 review 頁。"""
    return _ensure_admin_or_redirect(user, "review-dimensions.html")


@app.get("/admin/reconcile", include_in_schema=False)
def reconcile_list_page(user: dict = Depends(require_auth)):
    """Phase 11(+13-B auth gate):admin-only 仲裁清單頁。"""
    return _ensure_admin_or_redirect(user, "reconcile-list.html")


@app.get("/admin/reconcile/{audio_id}", include_in_schema=False)
def reconcile_detail_page(audio_id: str, user: dict = Depends(require_auth)):  # noqa: ARG001
    """Phase 11(+13-B auth gate):admin-only 單筆仲裁頁。"""
    return _ensure_admin_or_redirect(user, "reconcile.html")


@app.get("/admin/quality", include_in_schema=False)
def quality_page(user: dict = Depends(require_auth)):
    """Phase 5:admin-only 品質信號頁（業界對齊 / 商品證據 / audience 守門）。"""
    return _ensure_admin_or_redirect(user, "quality.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
