"""發佈服務：把一支內部 ref 音檔複製進獨立客戶倉，並產生一條存取 link。

實體隔離硬需求：客戶端只認 data/alignment_audio/ + alignment.db，
永遠碰不到 data/audio/ 的內部音檔。
"""
from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from src.alignment_db import AlignmentAudio, ClientLink
from src.audio_analysis import AUDIO_DIR
from src.client_auth import generate_token, hash_token

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALIGNMENT_AUDIO_DIR = PROJECT_ROOT / "data" / "alignment_audio"


@dataclass(frozen=True)
class PublishResult:
    token: str               # 明文，只此一次
    link_id: str
    alignment_audio_id: str
    session_id: str


def publish_audio_link(
    *,
    src_filename: str,
    label: str,
    role: str,
    annotator_id: str | None,
    session_id: str | None,
    expires_at: datetime | None,
    align_db: Session,
    src_audio_dir: Path = AUDIO_DIR,
    dst_audio_dir: Path = ALIGNMENT_AUDIO_DIR,
    orig_audio_id: str | None = None,
) -> PublishResult:
    """複製音檔 → 建 AlignmentAudio → 建 ClientLink。回傳明文 token（只此一次）。

    role="client" 時自動補 session_id（缺則生成），三欄會鎖進 link。
    """
    src_path = src_audio_dir / src_filename
    if not src_path.exists():
        raise FileNotFoundError(f"找不到來源音檔：{src_filename}")

    dst_audio_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_audio_dir / src_filename)

    audio = AlignmentAudio(filename=src_filename, orig_audio_id=orig_audio_id)
    align_db.add(audio)
    align_db.flush()  # 取 audio.id

    resolved_session = session_id or f"sess-{uuid.uuid4().hex[:8]}"
    token = generate_token()
    link = ClientLink(
        token_hash=hash_token(token),
        role=role,
        label=label,
        annotator_id=annotator_id if role == "client" else None,
        session_id=resolved_session if role == "client" else None,
        alignment_audio_id=audio.id if role == "client" else None,
        expires_at=expires_at,
    )
    align_db.add(link)
    align_db.commit()

    return PublishResult(
        token=token, link_id=link.id,
        alignment_audio_id=audio.id, session_id=resolved_session,
    )
