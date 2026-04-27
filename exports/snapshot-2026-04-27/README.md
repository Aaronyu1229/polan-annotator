# Pōlán 聲音標註資料集 — Snapshot 2026-04-27

## 內容

| 檔案 | 用途 |
|---|---|
| `dataset.json` | 主要交付物，含 consensus + individual_annotations |
| `calibration_set.json` | 只含創辦人 Amber 的標註，給新標註員校準用 |
| `individual_amber.json` | Amber 個人的全部 11 筆標註 |

## 統計

- `schema_version`: `0.1.0`
- `total_audio_files`: 33（資料集音檔總數）
- `total_annotated`: 11（已完整標記、可用）
- `total_annotations`: 11（個別標註筆數）
- `annotators`: `["amber"]`（單一標註員 — 創辦人 Amber）
- `consensus_method`: 全部 `single_annotator`（無 cross-annotator aggregation）

## 維度

10 個維度，分 3 大類：

- **emotion**（4 個，threshold 0.7）：valence、arousal、emotional_warmth、tension_direction
- **function**（3 個，threshold 0.7）：temporal_position、event_significance、loop_capability
- **acoustic**（3 個，threshold 0.85）：tonal_noise_ratio、spectral_density、world_immersion

連續維度 9 個（值 ∈ [0, 1] 浮點）；`loop_capability` 為 multi_discrete（值 ∈ list of {0.0, 0.5, 1.0}）。

## 驗證

```bash
python scripts/validate_export.py dataset.json
# 應回 ✅ Valid. 11 items, 11 annotations, 1 annotators
```

## 注意

- 4 個維度標記為 `amber_confirmed: false`：`emotional_warmth` / `tension_direction` /
  `event_significance` / `world_immersion`。定義為 Aaron 推敲版本，待 Amber 試標後驗收。
- 此 snapshot 為**早期 MVP** 版本（ICC 未計算 — 單人標註無 ICC）。後續加入第二位標註員後
  會 release 含 ICC 的 `v0.2.0` snapshot。

## Git tag

`v0.1.0-amber-11`
