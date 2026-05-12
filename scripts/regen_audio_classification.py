"""重新生成 data/audio_classification.txt。

分類規則（從現存 audio_classification.txt 反向推導）：
  - BGM：檔名含 bgm / BGM / Base Game / Free Game / Main Game / Bonus Game /
         Theme Music / Winning Panel / Transition / Win End 之一
  - 未知：檔名為純數字（id 編號類），例如 11005101.wav
  - SFX：其他

長度桶（用 librosa 拿 duration）：
  [<5s   ]  [5-20s ]  [>20s  ]  [?     ] (讀取失敗)

執行：
    python scripts/regen_audio_classification.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import librosa

AUDIO_DIR = Path(__file__).resolve().parents[1] / "data" / "audio"
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "audio_classification.txt"

AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}

BGM_KEYWORDS = (
    "bgm", "Base Game", "Free Game", "Main Game", "Bonus Game",
    "Theme Music", "Winning Panel", "Transition", "Win End", "Big Win",
    "Mega Win", "Super Win", "Max Win", "Ultra Win", "Epic Win",
)
NUMERIC_RE = re.compile(r"^\d+$")


def classify(name: str) -> str:
    stem = Path(name).stem
    lower = name.lower()
    if any(kw.lower() in lower for kw in BGM_KEYWORDS):
        return "BGM"
    if NUMERIC_RE.match(stem):
        return "未知"
    return "SFX"


def duration_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "?     "
    if seconds < 5:
        return "<5s   "
    if seconds <= 20:
        return "5-20s "
    return ">20s  "


def main() -> int:
    files = sorted(
        p for p in AUDIO_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )
    print(f"Scanning {len(files)} files...", file=sys.stderr)

    grouped: dict[str, list[tuple[str, str]]] = {"BGM": [], "SFX": [], "未知": []}
    for i, p in enumerate(files):
        if i % 100 == 0:
            print(f"  [{i}/{len(files)}]", file=sys.stderr)
        try:
            dur = librosa.get_duration(path=str(p))
        except Exception:
            dur = None
        cat = classify(p.name)
        grouped[cat].append((duration_bucket(dur), p.name))

    lines = [f"分類報告 — 共 {len(files)} 檔", "", ""]
    for cat in ("BGM", "SFX", "未知"):
        items = grouped[cat]
        lines.append(f"=== {cat} ({len(items)} 檔) ===")
        for bucket, name in items:
            lines.append(f"  [{bucket}] {name}")
        lines.append("")

    OUTPUT.write_text("\n".join(lines))
    print(f"\nWrote {OUTPUT} (BGM={len(grouped['BGM'])}, "
          f"SFX={len(grouped['SFX'])}, 未知={len(grouped['未知'])})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
