"""GET /api/stats/* — 進度快照、跨人 ICC、overlap 清單。

- /progress：單 annotator 進度（Phase 5 #2）
- /icc：跨標註員 ICC(2,1) per dimension（Phase 3）
- /overlap：被 ≥ 2 人 is_complete-標過的 audio 清單（Phase 3）

前端 list.js 用 Intl.DateTimeFormat().resolvedOptions().timeZone 抓瀏覽器 TZ
當 ?tz= 帶入，後端用 zoneinfo 解析。無效 TZ 會 fallback UTC 並 log warning。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from src.db import get_session
from src.middleware import require_auth
from src.stats import compute_icc_per_dimension, compute_overlap_audios, compute_progress

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/progress")
def progress(
    user: dict[str, Any] = Depends(require_auth),
    tz: Optional[str] = Query(default=None, description="IANA TZ，例如 Asia/Taipei"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_progress(session, user["annotator_id"], tz_name=tz).to_dict()


@router.get("/icc")
def icc(
    include_fixture: bool = Query(
        default=False,
        description="是否納入 fixture_ 前綴的假標註員（dashboard preview 用）",
    ),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_icc_per_dimension(session, include_fixture=include_fixture)


@router.get("/overlap")
def overlap(
    include_fixture: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return compute_overlap_audios(session, include_fixture=include_fixture)
