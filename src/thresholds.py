"""三角架構所有門檻的單一來源（取代散落各檔的 GOLD_MAX_SPREAD / GREEN_THRESHOLD 等）。

註：門檻皆為慣例，非統計驗證過的 cutoff（見 methodology-deep-review A3）。
未來若改 per-dimension SD 正規化，只動這裡。
"""
from __future__ import annotations

# creator-industry gap ≤ 此值 → fast 仲裁路徑；> 此值 → full（須 Notes）
ARBITRATION_GATE = 0.20
# creator-industry gap > 此值 → 標記「業界內部分歧」，觸發 industry 校準（Phase 5）
INDUSTRY_RECAL = 0.30
# industry-audience gap > 此值 → 「專業 vs 大眾分歧」= 商品特性，不修正（Phase 5）
PRODUCT_DIVERGENCE = 0.40

# 7 個 human 連續維（acoustic 兩維 librosa deterministic 不計）。放在此 leaf module
# 供 arbitration / role_gaps / audiofile_status 共用，打破循環 import。
HUMAN_CONTINUOUS_DIMS: tuple[str, ...] = (
    "valence", "arousal", "emotional_warmth", "tension_direction",
    "temporal_position", "event_significance", "world_immersion",
)
