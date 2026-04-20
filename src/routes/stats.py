"""GET /api/stats/progress — 單 annotator 進度快照。

前端 list.js 用 Intl.DateTimeFormat().resolvedOptions().timeZone 抓瀏覽器 TZ
當 ?tz= 帶入，後端用 zoneinfo 解析。無效 TZ 會 fallback UTC 並 log warning。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from src.db import get_session
from src.stats import compute_progress

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/progress")
def progress(
    annotator: str = Query(..., description="annotator_id"),
    tz: Optional[str] = Query(default=None, description="IANA TZ，例如 Asia/Taipei"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_progress(session, annotator, tz_name=tz).to_dict()
