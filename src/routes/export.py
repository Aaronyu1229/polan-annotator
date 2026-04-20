"""Phase 4 dataset export endpoints — 薄 wrapper，真正的 aggregation 在 src/export.py。

3 個 endpoint 都回 application/json（FastAPI default）：
    GET /api/export/dataset.json           完整 dataset，多人共識
    GET /api/export/calibration_set.json   只含 amber 的 annotation
    GET /api/export/individual.json?annotator=<id>   特定標註員；未知 or 無完成 annotation 回 404

刻意不用 response_model（Pydantic）：schema 由 src/export.py 手寫 dict 組合，
validator 對照 prompt 的 schema 範例逐欄驗；Pydantic 會把 schema 綁死在 code 裡，
降低手寫 JSON 的可讀性。此處追求「讀 code 就能看出輸出長怎樣」優於型別校驗。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from src.db import get_session
from src.export import build_dataset, count_completed_for

router = APIRouter(prefix="/api/export", tags=["export"])

_CALIBRATION_ANNOTATOR = "amber"


@router.get("/dataset.json")
def export_dataset(session: Session = Depends(get_session)) -> dict:
    return build_dataset(session)


@router.get("/calibration_set.json")
def export_calibration_set(session: Session = Depends(get_session)) -> dict:
    # calibration set = Amber 的標註，給新標註員做校準訓練。
    # filter 寫死 "amber"，與 CLAUDE.md 的「Amber 是 ground truth」定位一致。
    return build_dataset(session, annotator_filter=_CALIBRATION_ANNOTATOR)


@router.get("/individual.json")
def export_individual(
    annotator: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
) -> dict:
    # 404 判定：annotator 不存在 or 存在但無任何 is_complete=1 的標註
    # → 兩種情況都不該回 200 帶空 items，避免買方誤以為「此人存在只是沒標」。
    if count_completed_for(session, annotator) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"annotator '{annotator}' has no completed annotations",
        )
    return build_dataset(session, annotator_filter=annotator)
