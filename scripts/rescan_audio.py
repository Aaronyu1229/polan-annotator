"""CLI：手動重掃 data/audio/ 並 upsert 新檔案到 DB。

使用情境：
- 把新音檔丟進 data/audio/ 後不想重啟 server
- 確認某些檔案是否被正確 parse

執行：
    python scripts/rescan_audio.py
    python -m scripts.rescan_audio
"""
import logging
import sys

from sqlmodel import Session

from src.audio_scanner import scan_audio_directory
from src.db import create_db, engine


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log = logging.getLogger("rescan_audio")

    create_db()
    with Session(engine) as session:
        result = scan_audio_directory(session)

    log.info(
        "完成 — 磁碟上 %d 檔，新增 %d，已存在 %d",
        result.total_on_disk, len(result.added), len(result.skipped),
    )
    if result.added:
        log.info("新增明細：")
        for name in result.added:
            log.info("  + %s", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
