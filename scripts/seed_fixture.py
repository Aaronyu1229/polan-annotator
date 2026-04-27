"""Optional 工具：生成 fixture 假標註，讓 dashboard 在沒第二位真實標註員時也能預覽。

用法：
    uv run python scripts/seed_fixture.py            # 寫入 fixture_bob + fixture_alice
    uv run python scripts/seed_fixture.py --remove   # 清除所有 fixture_ 前綴的 annotation

行為：
- 抓所有 amber 的 is_complete=1 annotation，對每筆生成 fixture_bob / fixture_alice 兩份
  「在 amber 值附近 ±0.15 jitter」的 annotation
- annotator_id 永遠以 fixture_ 前綴，方便 DELETE WHERE annotator_id LIKE 'fixture_%' 清除
- multi-select 欄位（function_roles / source_type / loop_capability / genre_tag / style_tag）
  完全複製 amber 的選擇（避免引入 schema 錯誤）

設計用意：dashboard 預設排除 fixture_ 前綴；勾「include fixture」才看到，
方便 Aaron 在 Bob 真正加入前展示 ICC + overlap 視覺。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import Session, delete, select  # noqa: E402

from src.db import engine  # noqa: E402
from src.models import Annotation  # noqa: E402

REFERENCE_ANNOTATOR_ID = "amber"
FIXTURE_ANNOTATORS: list[tuple[str, int]] = [
    ("fixture_bob", 42),       # seed 42 — bob 接近 amber
    ("fixture_alice", 7),      # seed 7  — alice 略歧見
]
JITTER_MAX = 0.15  # ± 範圍

CONTINUOUS_DIMS: tuple[str, ...] = (
    "valence", "arousal", "emotional_warmth", "tension_direction",
    "temporal_position", "event_significance",
    "tonal_noise_ratio", "spectral_density", "world_immersion",
)


def _jitter(value: float | None, rng: random.Random) -> float | None:
    if value is None:
        return None
    new_val = value + rng.uniform(-JITTER_MAX, JITTER_MAX)
    # clamp 到 [0, 1]
    return max(0.0, min(1.0, new_val))


def remove_fixtures(session: Session) -> int:
    """刪除所有 fixture_ 前綴 annotation，回 deleted count。"""
    rows = session.exec(
        select(Annotation).where(Annotation.annotator_id.like("fixture_%"))  # type: ignore[attr-defined]
    ).all()
    count = len(rows)
    for r in rows:
        session.delete(r)
    session.commit()
    return count


def seed_fixtures(session: Session) -> int:
    """為每位 fixture annotator 複製 amber 的 is_complete=1 annotation（加 jitter）。"""
    amber_rows = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == REFERENCE_ANNOTATOR_ID,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    if not amber_rows:
        print(f"⚠️  {REFERENCE_ANNOTATOR_ID} 沒有 is_complete=1 的 annotation，無資料可複製。")
        return 0

    inserted = 0
    for fixture_id, seed in FIXTURE_ANNOTATORS:
        rng = random.Random(seed)
        for amber_ann in amber_rows:
            # 已存在就跳過（idempotent，重跑不重複）
            existing = session.exec(
                select(Annotation).where(
                    Annotation.audio_file_id == amber_ann.audio_file_id,
                    Annotation.annotator_id == fixture_id,
                )
            ).first()
            if existing is not None:
                continue

            ann = Annotation(
                audio_file_id=amber_ann.audio_file_id,
                annotator_id=fixture_id,
                # 連續維度：jitter
                **{dim: _jitter(getattr(amber_ann, dim), rng) for dim in CONTINUOUS_DIMS},
                # multi-select 欄位：完全複製（避免引入 schema 錯）
                loop_capability=amber_ann.loop_capability,
                source_type=amber_ann.source_type,
                function_roles=amber_ann.function_roles,
                genre_tag=amber_ann.genre_tag,
                worldview_tag=amber_ann.worldview_tag,
                style_tag=amber_ann.style_tag,
                notes=f"[fixture seed={seed}]",
                is_complete=True,
            )
            session.add(ann)
            inserted += 1
    session.commit()
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="清除所有 fixture_ 前綴 annotation")
    args = parser.parse_args()

    with Session(engine) as session:
        if args.remove:
            n = remove_fixtures(session)
            print(f"✅ 已刪除 {n} 筆 fixture annotation")
        else:
            n = seed_fixtures(session)
            if n == 0:
                print("無新增（amber 無 is_complete annotation 或 fixture 已存在）")
            else:
                print(f"✅ 已新增 {n} 筆 fixture annotation（含 fixture_bob + fixture_alice）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
