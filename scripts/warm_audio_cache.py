"""預先把所有 AudioFile 的 librosa metadata 算出來並 cache 進 DB。

動機：`audio_analysis.ensure_cached` 原本只在使用者開啟標註頁時 lazy 跑，
導致沒開過的檔案 duration_sec / bpm / sample_rate / *_auto 欄位是 null。
Phase 4 export 的 audio_metadata 會出現大量 null。

Amber 在試標 / 匯出前跑一次這個 script，把 33 檔都預熱，之後 export 的
audio_metadata 就齊全。

用法：
    uv run python scripts/warm_audio_cache.py

預計耗時：3-8 分鐘（依 CPU），每檔 5-15 秒。
某檔失敗不會中斷整批：印到 stderr 後繼續下一檔，結尾印 summary。
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from sqlmodel import Session, select

from src.audio_analysis import AUDIO_DIR, ensure_cached
from src.db import engine
from src.models import AudioFile

# ensure_cached 內部有 logging，關掉 librosa/audioread 的噪音讓 CLI 輸出乾淨
logging.basicConfig(level=logging.WARNING, format="%(message)s")


def main(argv: list[str]) -> int:
    audio_dir = AUDIO_DIR
    if not audio_dir.exists():
        print(f"❌ AUDIO_DIR 不存在：{audio_dir}", file=sys.stderr)
        return 2

    with Session(engine) as session:
        audios = session.exec(
            select(AudioFile).order_by(AudioFile.game_name, AudioFile.game_stage)
        ).all()

        total = len(audios)
        if total == 0:
            print("⚠️  DB 沒有任何 AudioFile，先啟動一次 uvicorn 讓掃描跑起來。")
            return 0

        ok, skipped, failed = 0, 0, 0
        failed_files: list[str] = []

        print(f"🎧 開始預熱 {total} 個音檔的 librosa cache…\n")
        for i, audio in enumerate(audios, 1):
            cached = (
                audio.duration_sec is not None
                and audio.tonal_noise_ratio_auto is not None
                and audio.spectral_density_auto is not None
            )
            if cached:
                print(f"[{i}/{total}] ⏭  skip (cached): {audio.filename}")
                skipped += 1
                continue

            t0 = time.perf_counter()
            try:
                ensure_cached(session, audio, audio_dir=audio_dir)
            except (FileNotFoundError, OSError, ValueError, RuntimeError) as e:
                # librosa 可能丟 RuntimeError（audioread 解不開）、PySoundFile fallback 失敗等
                # 不讓單檔失敗中斷整批；記到 stderr 讓 CI / Amber 事後回頭處理。
                elapsed = time.perf_counter() - t0
                print(
                    f"[{i}/{total}] ✗  FAIL  ({elapsed:.1f}s): {audio.filename} — {type(e).__name__}: {e}",
                    file=sys.stderr,
                )
                failed += 1
                failed_files.append(audio.filename)
                continue

            elapsed = time.perf_counter() - t0
            print(f"[{i}/{total}] ✓  {audio.filename}  ({elapsed:.1f}s)")
            ok += 1

        print()
        print(f"📊 Summary: {total} 檔中 {ok} 新分析、{skipped} 已快取跳過、{failed} 失敗")
        if failed_files:
            print("失敗清單（也印在 stderr 了）：")
            for f in failed_files:
                print(f"  - {f}")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
