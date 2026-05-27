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
# 單一維度需 ≥ 此檔數出現 creator-industry > INDUSTRY_RECAL，才建議 industry 重新校準
# （避免單一 outlier 觸發；Phase 5）
RECAL_MIN_FILES = 3

# ─── Phase 7：per-role 校準 ───────────────────────────────────────
SELF_MAE_MAX = 0.10        # creator 自我一致性目標（test-retest）
INDUSTRY_ALIGN_MAX = 0.20  # industry vs-creator 對齊上界（**拿掉下界**，低 MAE 不再 fail）
AUDIENCE_INTRA_MIN = 0.6   # audience 內部一致性（1 - mean|Δ|）下界；不以 vs-creator gating
CALIB_MIN_N = 20           # self-MAE / intra-rater 最低樣本數，不足回「資料不足」
RETEST_WASHOUT_DAYS = 14   # test-retest 最小 wash-out（避免記憶污染）

# 7 個 human 連續維（acoustic 兩維 librosa deterministic 不計）。放在此 leaf module
# 供 arbitration / role_gaps / audiofile_status 共用，打破循環 import。
HUMAN_CONTINUOUS_DIMS: tuple[str, ...] = (
    "valence", "arousal", "emotional_warmth", "tension_direction",
    "temporal_position", "event_significance", "world_immersion",
)
