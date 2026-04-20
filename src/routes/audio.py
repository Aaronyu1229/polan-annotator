"""GET /api/audio — 列出已掃描入庫的所有音檔。

Phase 1 只回傳 metadata；Phase 2 會新增 /api/audio/{id}/file 串流 wav。
"""
from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from src.db import get_session
from src.models import AudioFile

router = APIRouter(prefix="/api", tags=["audio"])


@router.get("/audio")
def list_audio(session: Session = Depends(get_session)) -> list[AudioFile]:
    return session.exec(
        select(AudioFile).order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()
