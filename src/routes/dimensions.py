"""GET /api/dimensions — 提供前端 / Phase 2 標註介面所需的維度定義。"""
from typing import Any

from fastapi import APIRouter

from src.dimensions_loader import load_dimensions

router = APIRouter(prefix="/api", tags=["dimensions"])


@router.get("/dimensions")
def get_dimensions() -> dict[str, Any]:
    return load_dimensions()
