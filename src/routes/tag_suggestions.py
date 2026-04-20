"""GET /api/tag-suggestions?field= — combobox autocomplete 資料源。

合併 (TagSuggestion DB rows sort by use_count desc) + 預設 constants 清單，去重後回傳。
寫入由 POST /api/annotations 觸發（見 annotations.py）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from src.constants import GENRE_PRESETS, STYLE_PRESETS, WORLDVIEW_PRESETS
from src.db import get_session
from src.models import TagSuggestion

router = APIRouter(prefix="/api", tags=["tag-suggestions"])

_FIELD_PRESETS: dict[str, list[str]] = {
    "genre": GENRE_PRESETS,
    "worldview": WORLDVIEW_PRESETS,
    "style": STYLE_PRESETS,
}


@router.get("/tag-suggestions")
def get_tag_suggestions(
    field: str = Query(..., description="genre / worldview / style"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if field not in _FIELD_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"field 必須是 genre / worldview / style，收到：{field}",
        )

    rows = session.exec(
        select(TagSuggestion)
        .where(TagSuggestion.field == field)
        .order_by(TagSuggestion.use_count.desc())
    ).all()
    history_values = [r.value for r in rows]

    # 歷史值在前、presets 在後；去重保留第一次出現順序
    seen: set[str] = set()
    merged: list[str] = []
    for value in history_values + _FIELD_PRESETS[field]:
        if value not in seen:
            seen.add(value)
            merged.append(value)

    return {"field": field, "suggestions": merged}
