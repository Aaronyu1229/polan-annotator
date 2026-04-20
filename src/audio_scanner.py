"""data/audio/ 掃描器：把 .wav 檔名 parse 後 upsert 到 AudioFile table。

設計原則：
- Idempotent：重複掃同一個目錄只新增未見過的檔案，已存在的 filename 跳過不改。
- Phase 1 不跑 librosa：duration_sec / bpm / sample_rate 在 insert 時留 None，
  Phase 2 的 audio_analysis 模組再補上。
- 不處理刪除 / rename：MVP 階段假設 data/audio/ 的 33 首種子集合不會縮水。
"""
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session, select

from src.constants import parse_audio_filename
from src.db import PROJECT_ROOT
from src.models import AudioFile

AUDIO_DIR = PROJECT_ROOT / "data" / "audio"


@dataclass(frozen=True)
class ScanResult:
    """scanner 執行後的摘要。"""
    total_on_disk: int
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def scan_audio_directory(
    session: Session,
    audio_dir: Path = AUDIO_DIR,
) -> ScanResult:
    """掃描 audio_dir，把新檔案 insert 到 AudioFile table。

    回傳 ScanResult 摘要。caller 負責處理 session commit（雖然這個函數內部已 commit）。
    """
    if not audio_dir.exists():
        raise FileNotFoundError(f"音檔目錄不存在：{audio_dir}")
    if not audio_dir.is_dir():
        raise NotADirectoryError(f"{audio_dir} 不是目錄")

    wav_paths = sorted(audio_dir.glob("*.wav"))

    # 一次撈出 DB 裡所有 filename，避免 N+1 query
    existing_names: set[str] = set(
        session.exec(select(AudioFile.filename)).all()
    )

    added: list[str] = []
    skipped: list[str] = []

    for wav_path in wav_paths:
        filename = wav_path.name
        if filename in existing_names:
            skipped.append(filename)
            continue
        parsed = parse_audio_filename(filename)
        audio = AudioFile(
            filename=filename,
            game_name=parsed["game_name"],
            game_stage=parsed["game_stage"],
            is_brand_theme=parsed["is_brand_theme"],
        )
        session.add(audio)
        added.append(filename)

    session.commit()

    return ScanResult(
        total_on_disk=len(wav_paths),
        added=added,
        skipped=skipped,
    )
