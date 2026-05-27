# 三角架構仲裁 — Phase 1–3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉「把預期內視角分歧當缺陷」的 lockable bug — 用三角角色（creator/industry/audience）+ per-(audio×field) 仲裁模型取代 spread≤0.20 的舊判定。

**Architecture:** Phase 1 鋪資料基礎（config `role` 欄位、`Arbitration` 表、append-only `AnnotationSnapshot` 表、集中 thresholds）。Phase 2 寫純函式 gap 引擎（per-dim creator-industry/creator-audience/industry-audience gap）。Phase 3 重寫 audiofile status 衍生邏輯（收斂三處重複、is_gold_locked 退役、新狀態分類），只有 creator-industry gap 當仲裁闘門，audience 偏離永不 gate。

**Tech Stack:** FastAPI + SQLModel + SQLite，pytest。新表靠 `SQLModel.metadata.create_all` 自動建（既有 `tests/conftest.py::in_memory_engine` 與 startup 都會跑），**不需** migration 條目（migration 只用於既有表 ADD COLUMN）。

**設計依據：** [arbitration-triangular-lockable spec §10](../specs/2026-05-27-arbitration-triangular-lockable-design.md) + [methodology-deep-review](../specs/2026-05-27-methodology-deep-review.md)。

---

## File Structure

**Create:**
- `src/thresholds.py` — 所有門檻常數單一來源
- `src/arbitration.py` — Arbitration 讀取/序列化 helper（ARBITRATED_FIELDS、value (de)serialize、latest_by_audio_field、bulk loader）
- `src/role_gaps.py` — 純函式 gap 引擎
- `tests/test_thresholds.py`, `tests/test_arbitration.py`, `tests/test_role_gaps.py`, `tests/test_annotators_role.py`

**Modify:**
- `data/annotators_config.json` — 每人加 `role`
- `src/annotators_loader.py` — 驗證 `role` + `get_role` / `annotator_id_for_role`
- `src/models.py` — 新增 `Arbitration`、`AnnotationSnapshot` 兩個 table 類別
- `src/audiofile_status.py` — 新狀態分類、收斂、ARBITRATED_FIELDS 來源
- `src/routes/audio.py` — 刪除 `_compute_status_inline`，改走 `compute_status_from_preload`
- `src/routes/admin.py` — 停用 `lock_gold` / `unlock_gold`（回 410）
- 既有測試（`test_audiofile_status.py` 等）因語意改變需更新

---

# PHASE 1 — 資料基礎

## Task 1: Config `role` 欄位 + loader 驗證 + 反查 helper

**Files:**
- Modify: `data/annotators_config.json`
- Modify: `src/annotators_loader.py`
- Test: `tests/test_annotators_role.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_annotators_role.py
"""role 欄位驗證 + 反查。role 與 profile 解耦（獨立欄位）。"""
from __future__ import annotations

import json
import pytest

from src.annotators_loader import (
    AnnotatorsConfigError,
    annotator_id_for_role,
    get_role,
    load_annotators,
)


def _write_config(tmp_path, mapping):
    p = tmp_path / "annotators_config.json"
    p.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return p


def _spec(role):
    return {
        "name": "X", "email": "x@x.com", "annotator_profile": "general_audience",
        "status": "active", "is_admin": False, "joined_at": "2026-01-01", "role": role,
    }


def test_valid_roles_load(tmp_path):
    cfg = _write_config(tmp_path, {
        "amber": _spec("creator"), "y": _spec("industry"), "v": _spec("audience"),
    })
    loaded = load_annotators(cfg)
    assert loaded["amber"]["role"] == "creator"


def test_null_role_allowed(tmp_path):
    cfg = _write_config(tmp_path, {"guest": {**_spec("audience"), "role": None}})
    assert load_annotators(cfg)["guest"]["role"] is None


def test_missing_role_allowed_defaults_none(tmp_path):
    spec = _spec("creator")
    del spec["role"]
    cfg = _write_config(tmp_path, {"amber": spec})
    assert get_role("amber", cfg) is None


def test_invalid_role_raises(tmp_path):
    cfg = _write_config(tmp_path, {"x": _spec("expert")})  # not a valid role
    with pytest.raises(AnnotatorsConfigError):
        load_annotators(cfg)


def test_annotator_id_for_role(tmp_path):
    cfg = _write_config(tmp_path, {
        "amber": _spec("creator"), "y": _spec("industry"),
    })
    assert annotator_id_for_role("creator", cfg) == "amber"
    assert annotator_id_for_role("audience", cfg) is None  # nobody has it


def test_duplicate_role_raises(tmp_path):
    cfg = _write_config(tmp_path, {
        "a": _spec("industry"), "b": _spec("industry"),
    })
    with pytest.raises(AnnotatorsConfigError):
        annotator_id_for_role("industry", cfg)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_annotators_role.py -q`
Expected: FAIL (ImportError: cannot import `annotator_id_for_role` / `get_role`).

- [ ] **Step 3: Implement loader changes**

In `src/annotators_loader.py`, add `role` to validation (optional, default None) and two helpers. Add after `_VALID_PROFILE`:

```python
_VALID_ROLES = frozenset({"creator", "industry", "audience"})
```

In `_validate`, inside the per-annotator loop, after the profile check add:

```python
        role = spec.get("role")
        if role is not None and role not in _VALID_ROLES:
            raise AnnotatorsConfigError(
                f"標註員 {ann_id!r} role={role!r} 不合法，"
                f"合法值：{sorted(_VALID_ROLES)} 或 null"
            )
```

Append two module-level functions:

```python
def get_role(annotator_id: str, path: Path | None = None) -> str | None:
    """回該標註員的方法論角色（creator/industry/audience）；未設或未知 → None。"""
    spec = get_annotator(annotator_id, path)
    return spec.get("role") if spec else None


def annotator_id_for_role(role: str, path: Path | None = None) -> str | None:
    """反查扮演某 role 的 annotator_id。

    role≠profile：role 是內部架構角色。目前一 role 一人；解析到多人時 raise
    （把「一 role 多人」的未來情境變大聲，而非靜默取第一個）。無人擔任 → None。
    """
    config = load_annotators(path)
    matches = [aid for aid, spec in config.items() if spec.get("role") == role]
    if len(matches) > 1:
        raise AnnotatorsConfigError(
            f"role={role!r} 對應多位標註員 {matches}；目前模型假設一 role 一人。"
        )
    return matches[0] if matches else None
```

- [ ] **Step 4: Add `role` to production config**

Edit `data/annotators_config.json` — add `"role"` to each entry: `amber`→`"creator"`, `yyslin1024`→`"industry"`, `vvgosick`→`"audience"`, `guest`→`null`.

- [ ] **Step 5: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/test_annotators_role.py -q`
Expected: PASS (6 passed).

- [ ] **Step 6: Verify production config still loads**

Run: `.venv/bin/python -c "from src.annotators_loader import load_annotators, annotator_id_for_role; load_annotators(); print(annotator_id_for_role('creator'))"`
Expected: prints `amber`.

- [ ] **Step 7: Commit**

```bash
git add src/annotators_loader.py data/annotators_config.json tests/test_annotators_role.py
git commit -m "feat: annotators role 欄位 (creator/industry/audience) + 反查 helper"
```

---

## Task 2: `Arbitration` table

**Files:**
- Modify: `src/models.py`
- Test: `tests/test_arbitration.py` (create — table CRUD subset here; helpers in Task 5)

- [ ] **Step 1: Write failing test**

```python
# tests/test_arbitration.py
from __future__ import annotations

from sqlmodel import Session, select

from src.models import Arbitration


def test_arbitration_row_roundtrip(in_memory_engine):
    with Session(in_memory_engine) as s:
        s.add(Arbitration(
            audio_file_id="a1", field="valence", arbitrated_value="0.7",
            value_type="float", path="fast", arbitrated_by="amber",
        ))
        s.commit()
        row = s.exec(select(Arbitration).where(Arbitration.audio_file_id == "a1")).one()
        assert row.field == "valence"
        assert row.path == "fast"
        assert row.notes is None
        assert row.arbitrated_at is not None


def test_arbitration_history_multiple_rows_same_audio_field(in_memory_engine):
    # 同 (audio, field) 允許多筆（re-arbitration 歷史保留）
    with Session(in_memory_engine) as s:
        s.add(Arbitration(audio_file_id="a1", field="valence", arbitrated_value="0.5",
                          value_type="float", path="fast", arbitrated_by="amber"))
        s.add(Arbitration(audio_file_id="a1", field="valence", arbitrated_value="0.6",
                          value_type="float", path="full", notes="改判", arbitrated_by="amber"))
        s.commit()
        rows = s.exec(select(Arbitration).where(Arbitration.field == "valence")).all()
        assert len(rows) == 2
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_arbitration.py -q`
Expected: FAIL (ImportError: cannot import `Arbitration`).

- [ ] **Step 3: Add the model**

In `src/models.py`, after the `TagSuggestion` class, append:

```python
class Arbitration(SQLModel, table=True):
    """creator 對 (audio × field) 確認最終值的事件。per-(audio,field)，保留歷史。

    active 仲裁 = 同 (audio_file_id, field) 中 arbitrated_at 最大的那筆（不存 is_active）。
    arbitrated_value 以 JSON 字串存（float / list[str] / list[float]），由 value_type 標型別，
    decode 集中在 src/arbitration.py。
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    audio_file_id: str = Field(foreign_key="audiofile.id", index=True)
    field: str = Field(index=True)            # valence … world_immersion / loop_capability / *_tag
    arbitrated_value: str                     # JSON-serialized
    value_type: str                           # "float" | "list_str" | "list_float"
    path: str                                 # "fast" | "full"
    notes: Optional[str] = None               # full path 時 API 強制要求（Phase 4）
    arbitrated_by: str                        # = creator annotator_id
    arbitrated_at: datetime = Field(default_factory=_utcnow)

    __table_args__ = (
        sa.Index("ix_arbitration_audio_field_at", "audio_file_id", "field", "arbitrated_at"),
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/test_arbitration.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_arbitration.py
git commit -m "feat: Arbitration 表 (per audio×field, 保留歷史, value_type)"
```

---

## Task 3: `AnnotationSnapshot` table (append-only, 凍結欄位)

**Files:**
- Modify: `src/models.py`
- Test: `tests/test_arbitration.py` (append)

> 理由：creator self-MAE（Phase 7）與 audience intra-rater 一致性（A4 提前核心）需保留同一 (audio, annotator) 的 ≥2 次標註。現行 `Annotation` 是 upsert 覆寫且唯一鍵綁死，太多 reader 依賴一列。**本 phase 只建空表、凍結欄位，不寫入、不動 `Annotation`。**

- [ ] **Step 1: Write failing test**

Append to `tests/test_arbitration.py`:

```python
def test_annotation_snapshot_append_only_multiple_passes(in_memory_engine):
    from src.models import AnnotationSnapshot
    with Session(in_memory_engine) as s:
        s.add(AnnotationSnapshot(audio_file_id="a1", annotator_id="amber",
                                 pass_no=1, valence=0.5))
        s.add(AnnotationSnapshot(audio_file_id="a1", annotator_id="amber",
                                 pass_no=2, valence=0.55))
        s.commit()
        rows = s.exec(
            select(AnnotationSnapshot).where(AnnotationSnapshot.annotator_id == "amber")
        ).all()
        assert {r.pass_no for r in rows} == {1, 2}
```

(add `select` import already present; add the local import shown.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_arbitration.py::test_annotation_snapshot_append_only_multiple_passes -q`
Expected: FAIL (cannot import `AnnotationSnapshot`).

- [ ] **Step 3: Add the model**

In `src/models.py`, after `Arbitration`, append:

```python
class AnnotationSnapshot(SQLModel, table=True):
    """Append-only test-retest 紀錄（凍結欄位）。Phase 1 建空表；Phase 7 / audience-floor 才寫入。

    用途：creator self-MAE、audience 隱藏重複題 intra-rater 一致性。
    刻意不加唯一鍵 — 同 (audio, annotator) 可多次 pass。只存 7 個 human 連續維。
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    audio_file_id: str = Field(foreign_key="audiofile.id", index=True)
    annotator_id: str = Field(index=True)
    pass_no: int
    created_at: datetime = Field(default_factory=_utcnow)
    valence: Optional[float] = None
    arousal: Optional[float] = None
    emotional_warmth: Optional[float] = None
    tension_direction: Optional[float] = None
    temporal_position: Optional[float] = None
    event_significance: Optional[float] = None
    world_immersion: Optional[float] = None
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_arbitration.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_arbitration.py
git commit -m "feat: AnnotationSnapshot 表 (append-only test-retest, 凍結欄位, Phase1 建空表)"
```

---

## Task 4: `src/thresholds.py` 集中門檻

**Files:**
- Create: `src/thresholds.py`
- Test: `tests/test_thresholds.py` (create)

- [ ] **Step 1: Write failing test**

```python
# tests/test_thresholds.py
from src import thresholds


def test_thresholds_present_and_ordered():
    assert thresholds.ARBITRATION_GATE == 0.20
    assert thresholds.INDUSTRY_RECAL == 0.30
    assert thresholds.PRODUCT_DIVERGENCE == 0.40
    # 邏輯排序：仲裁 gate < industry 校準警示 < 商品分歧
    assert thresholds.ARBITRATION_GATE < thresholds.INDUSTRY_RECAL < thresholds.PRODUCT_DIVERGENCE
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_thresholds.py -q`
Expected: FAIL (ModuleNotFoundError: src.thresholds).

- [ ] **Step 3: Implement**

```python
# src/thresholds.py
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
```

> 註：`audiofile_status.py` 既有的 `HUMAN_CONTINUOUS_DIMS` 在 Task 7 改成 `from src.thresholds import HUMAN_CONTINUOUS_DIMS`（re-export，讓既有 `from src.audiofile_status import HUMAN_CONTINUOUS_DIMS` 的呼叫端不破）。

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_thresholds.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/thresholds.py tests/test_thresholds.py
git commit -m "feat: src/thresholds.py 集中三角架構門檻"
```

---

## Task 5: `src/arbitration.py` — ARBITRATED_FIELDS + 序列化 + latest reducer + bulk loader

**Files:**
- Create: `src/arbitration.py`
- Test: `tests/test_arbitration.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_arbitration.py`:

```python
def test_serialize_roundtrip_float_and_list():
    from src.arbitration import serialize_value, deserialize_value
    v, t = serialize_value("valence", 0.7)
    assert (v, t) == ("0.7", "float")
    assert deserialize_value(v, t) == 0.7
    v, t = serialize_value("genre_tag", ["博弈", "RPG"])
    assert t == "list_str"
    assert deserialize_value(v, t) == ["博弈", "RPG"]
    v, t = serialize_value("loop_capability", [0.5, 1.0])
    assert t == "list_float"
    assert deserialize_value(v, t) == [0.5, 1.0]


def test_arbitrated_fields_count():
    from src.arbitration import ARBITRATED_FIELDS
    # 7 連續維 + loop_capability + 5 tags = 13
    assert len(ARBITRATED_FIELDS) == 13
    assert "valence" in ARBITRATED_FIELDS
    assert "worldview_tag" in ARBITRATED_FIELDS
    assert "tonal_noise_ratio" not in ARBITRATED_FIELDS  # acoustic 不仲裁


def test_latest_by_audio_field_picks_newest(in_memory_engine):
    from datetime import datetime, UTC, timedelta
    from src.models import Arbitration
    from src.arbitration import latest_by_audio_field
    t0 = datetime(2026, 5, 1, tzinfo=UTC)
    rows = [
        Arbitration(audio_file_id="a", field="valence", arbitrated_value="0.5",
                    value_type="float", path="fast", arbitrated_by="amber", arbitrated_at=t0),
        Arbitration(audio_file_id="a", field="valence", arbitrated_value="0.6",
                    value_type="float", path="full", arbitrated_by="amber",
                    arbitrated_at=t0 + timedelta(days=1)),
    ]
    latest = latest_by_audio_field(rows)
    assert latest[("a", "valence")].arbitrated_value == "0.6"


def test_bulk_load_arbitrations_groups_by_audio(in_memory_engine):
    from src.models import Arbitration
    from src.arbitration import bulk_load_arbitrations_by_audio
    with Session(in_memory_engine) as s:
        s.add(Arbitration(audio_file_id="a", field="valence", arbitrated_value="0.5",
                          value_type="float", path="fast", arbitrated_by="amber"))
        s.add(Arbitration(audio_file_id="b", field="arousal", arbitrated_value="0.3",
                          value_type="float", path="fast", arbitrated_by="amber"))
        s.commit()
        by_audio = bulk_load_arbitrations_by_audio(s)
        assert set(by_audio.keys()) == {"a", "b"}
        assert by_audio["a"][0].field == "valence"
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_arbitration.py -q`
Expected: FAIL (ModuleNotFoundError: src.arbitration).

- [ ] **Step 3: Implement**

```python
# src/arbitration.py
"""Arbitration 表的讀取 / 序列化 helper（單一資料來源，避免 Phase 5/6 各自手刻 decode）。"""
from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from src.models import Arbitration
from src.thresholds import HUMAN_CONTINUOUS_DIMS

# 多選欄位（存 JSON list）；其餘 ARBITRATED_FIELDS 為連續維（存 float）
_LIST_STR_FIELDS = frozenset({"source_type", "function_roles", "genre_tag",
                              "worldview_tag", "style_tag"})
_LIST_FLOAT_FIELDS = frozenset({"loop_capability"})

# 7 連續維 + loop_capability + 5 tags = 13（acoustic 兩維 librosa deterministic，不仲裁）
ARBITRATED_FIELDS: tuple[str, ...] = (
    *HUMAN_CONTINUOUS_DIMS,
    "loop_capability",
    "source_type", "function_roles", "genre_tag", "worldview_tag", "style_tag",
)


def serialize_value(field: str, value: Any) -> tuple[str, str]:
    """回 (json_str, value_type)。"""
    if field in _LIST_FLOAT_FIELDS:
        return json.dumps([float(v) for v in value]), "list_float"
    if field in _LIST_STR_FIELDS:
        return json.dumps(list(value), ensure_ascii=False), "list_str"
    return json.dumps(float(value)), "float"


def deserialize_value(raw: str, value_type: str) -> Any:
    value = json.loads(raw)
    if value_type == "float":
        return float(value)
    if value_type == "list_float":
        return [float(v) for v in value]
    return list(value)  # list_str


def latest_by_audio_field(
    rows: list[Arbitration],
) -> dict[tuple[str, str], Arbitration]:
    """同 (audio_file_id, field) 取 arbitrated_at 最大者 = active 仲裁。"""
    latest: dict[tuple[str, str], Arbitration] = {}
    for r in rows:
        key = (r.audio_file_id, r.field)
        cur = latest.get(key)
        if cur is None or r.arbitrated_at > cur.arbitrated_at:
            latest[key] = r
    return latest


def bulk_load_arbitrations_by_audio(
    session: Session,
) -> dict[str, list[Arbitration]]:
    """一次撈全部 Arbitration，分組 by audio_id（避免 status 全量計算時 N+1）。"""
    rows = session.exec(select(Arbitration)).all()
    by_audio: dict[str, list[Arbitration]] = {}
    for r in rows:
        by_audio.setdefault(r.audio_file_id, []).append(r)
    return by_audio
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_arbitration.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/arbitration.py tests/test_arbitration.py
git commit -m "feat: src/arbitration.py — ARBITRATED_FIELDS + 序列化 + latest reducer + bulk loader"
```

---

# PHASE 2 — Gap 引擎

## Task 6: `src/role_gaps.py` 純函式 gap 引擎

**Files:**
- Create: `src/role_gaps.py`
- Test: `tests/test_role_gaps.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_role_gaps.py
from __future__ import annotations

from src.models import Annotation
from src.role_gaps import pairwise_gaps, needs_full_arbitration


def _ann(**dims) -> Annotation:
    return Annotation(audio_file_id="a", annotator_id="x", **dims)


def test_three_way_gaps_all_present():
    by_role = {
        "creator": _ann(valence=0.5),
        "industry": _ann(valence=0.6),
        "audience": _ann(valence=0.9),
    }
    g = pairwise_gaps(by_role)
    assert g["valence"]["creator_industry"] == pytest_approx(0.1)
    assert g["valence"]["creator_audience"] == pytest_approx(0.4)
    assert g["valence"]["industry_audience"] == pytest_approx(0.3)


def test_missing_side_yields_none():
    by_role = {"creator": _ann(valence=0.5), "industry": None, "audience": None}
    g = pairwise_gaps(by_role)
    assert g["valence"]["creator_industry"] is None
    assert g["valence"]["creator_audience"] is None


def test_needs_full_arbitration_only_creator_industry_over_gate():
    # creator-industry 0.25 > 0.20 → full；audience 偏離 0.6 不影響
    by_role = {
        "creator": _ann(valence=0.5, arousal=0.5),
        "industry": _ann(valence=0.75, arousal=0.55),
        "audience": _ann(valence=0.99, arousal=0.99),
    }
    g = pairwise_gaps(by_role)
    assert needs_full_arbitration(g) == {"valence"}  # arousal gap 0.05 ≤ 0.20；audience 不算


def test_boundary_equal_gate_is_fast():
    by_role = {"creator": _ann(valence=0.5), "industry": _ann(valence=0.7),
               "audience": None}
    g = pairwise_gaps(by_role)  # gap = 0.20 exactly
    assert needs_full_arbitration(g) == set()  # ≤ gate → fast
```

Add at top of file:
```python
import pytest
def pytest_approx(x): return pytest.approx(x, abs=1e-9)
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_role_gaps.py -q`
Expected: FAIL (ModuleNotFoundError: src.role_gaps).

- [ ] **Step 3: Implement**

```python
# src/role_gaps.py
"""Per-dimension 三向 pairwise gap 引擎（純函式，無 DB / 無副作用）。

只 creator_industry gap 是仲裁闘門；creator_audience / industry_audience 只觀察
（audience 偏離永不影響仲裁路徑 — 修「把視角分歧當缺陷」的 bug class）。

呼叫端負責用 annotators_loader.annotator_id_for_role 把 role → annotation 解好再傳進來。
"""
from __future__ import annotations

from typing import Optional

from src.models import Annotation
from src.thresholds import ARBITRATION_GATE, HUMAN_CONTINUOUS_DIMS

GapsByDim = dict[str, dict[str, Optional[float]]]


def _abs_gap(a: Optional[Annotation], b: Optional[Annotation], dim: str) -> Optional[float]:
    if a is None or b is None:
        return None
    av, bv = getattr(a, dim, None), getattr(b, dim, None)
    if av is None or bv is None:
        return None
    return abs(av - bv)


def pairwise_gaps(by_role: dict[str, Optional[Annotation]]) -> GapsByDim:
    """每個 human 連續維 → {creator_industry, creator_audience, industry_audience}。
    任一側缺（None 或該維未標）→ 該 pair = None。"""
    creator = by_role.get("creator")
    industry = by_role.get("industry")
    audience = by_role.get("audience")
    out: GapsByDim = {}
    for dim in HUMAN_CONTINUOUS_DIMS:
        out[dim] = {
            "creator_industry": _abs_gap(creator, industry, dim),
            "creator_audience": _abs_gap(creator, audience, dim),
            "industry_audience": _abs_gap(industry, audience, dim),
        }
    return out


def needs_full_arbitration(gaps: GapsByDim) -> set[str]:
    """回 creator_industry_gap > ARBITRATION_GATE 的維度集合（需走 full 仲裁、寫 Notes）。
    None（industry 缺）不算超標 — 缺 industry 由 status 層歸到「等待 industry」。"""
    return {
        dim
        for dim, g in gaps.items()
        if g["creator_industry"] is not None and g["creator_industry"] > ARBITRATION_GATE
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_role_gaps.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/role_gaps.py tests/test_role_gaps.py
git commit -m "feat: src/role_gaps.py — per-dim 三向 gap 引擎 (僅 creator-industry 為仲裁闘門)"
```

---

# PHASE 3 — Status 重寫

> 新狀態（audio-level，衍生不存 DB）。**只 creator-industry gap 當闘門；audience 永不 gate。** 仲裁紀錄由 Phase 4 才寫入，故 Phase 3 上線後不會出現 `creator_ready`（預期，非 regression）。

新狀態定義：
- `untouched` — 0 筆 is_complete
- `creator_draft` — creator is_complete、industry 未（audience 不論）
- `industry_only` — industry is_complete、creator 未
- `needs_arbitration` — creator+industry 齊，且至少一連續維 creator_industry_gap > GATE 且該維尚未 active 仲裁
- `fast_confirmable` — creator+industry 齊，所有連續維 gap ≤ GATE，但尚未全欄位仲裁
- `creator_ready` — 所有 ARBITRATED_FIELDS 都有 active 仲裁，且無 stale（`creator.updated_at > arbitrated_at` 視為失效）

## Task 7: 重寫 `compute_status_from_preload` + 新狀態 + 收斂 spread 邏輯

**Files:**
- Modify: `src/audiofile_status.py`
- Test: `tests/test_audiofile_status_v2.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_audiofile_status_v2.py
"""三角架構新狀態分類。audience 偏離永不 gate（回歸舊 spread bug）。"""
from __future__ import annotations

from src.models import Annotation, AudioFile
from src.audiofile_status import compute_status_from_preload


def _audio() -> AudioFile:
    return AudioFile(id="a", filename="A_Base Game.wav", game_name="A", game_stage="Base Game")


def _ann(role_id, **dims) -> Annotation:
    return Annotation(audio_file_id="a", annotator_id=role_id, is_complete=True, **dims)


ROLE_MAP = {"creator": "amber", "industry": "yyslin", "audience": "vic"}


def _call(anns, arbs=None):
    return compute_status_from_preload(_audio(), anns, arbs or [], ROLE_MAP)


def test_untouched():
    assert _call([]) == "untouched"


def test_creator_only_is_creator_draft():
    assert _call([_ann("amber", valence=0.5)]) == "creator_draft"


def test_industry_only():
    assert _call([_ann("yyslin", valence=0.5)]) == "industry_only"


def test_audience_divergence_does_not_block_fast_confirmable():
    # creator+industry 對齊 (gap 0.05)；audience 大幅偏離 (0.9) — 舊邏輯會卡 cross_annotated
    anns = [
        _ann("amber", valence=0.5), _ann("yyslin", valence=0.55),
        _ann("vic", valence=0.95),
    ]
    assert _call(anns) == "fast_confirmable"


def test_creator_industry_gap_over_gate_needs_arbitration():
    anns = [_ann("amber", valence=0.5), _ann("yyslin", valence=0.8)]  # gap 0.30 > 0.20
    assert _call(anns) == "needs_arbitration"


def test_creator_ready_when_all_fields_arbitrated():
    from datetime import datetime, UTC, timedelta
    from src.models import Arbitration
    from src.arbitration import ARBITRATED_FIELDS
    t = datetime(2026, 5, 20, tzinfo=UTC)
    creator = _ann("amber", valence=0.5); creator.updated_at = t
    anns = [creator, _ann("yyslin", valence=0.55)]
    arbs = [
        Arbitration(audio_file_id="a", field=f, arbitrated_value="0.5",
                    value_type="float", path="fast", arbitrated_by="amber",
                    arbitrated_at=t + timedelta(hours=1))
        for f in ARBITRATED_FIELDS
    ]
    assert _call(anns, arbs) == "creator_ready"


def test_stale_arbitration_demotes_from_creator_ready():
    from datetime import datetime, UTC, timedelta
    from src.models import Arbitration
    from src.arbitration import ARBITRATED_FIELDS
    t = datetime(2026, 5, 20, tzinfo=UTC)
    creator = _ann("amber", valence=0.5)
    creator.updated_at = t + timedelta(days=5)  # creator 改在仲裁之後 → stale
    anns = [creator, _ann("yyslin", valence=0.55)]
    arbs = [
        Arbitration(audio_file_id="a", field=f, arbitrated_value="0.5",
                    value_type="float", path="fast", arbitrated_by="amber", arbitrated_at=t)
        for f in ARBITRATED_FIELDS
    ]
    assert _call(anns, arbs) != "creator_ready"  # demoted（fast_confirmable）
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_audiofile_status_v2.py -q`
Expected: FAIL (`compute_status_from_preload` signature mismatch / new states not returned).

- [ ] **Step 3: Rewrite the derivation**

In `src/audiofile_status.py`: change the local `HUMAN_CONTINUOUS_DIMS = (...)` definition to a re-export, and add the new imports. `thresholds` / `arbitration` / `role_gaps` are all leaf-er than `audiofile_status`, so no cycle:

```python
from src.arbitration import ARBITRATED_FIELDS, latest_by_audio_field
from src.role_gaps import needs_full_arbitration, pairwise_gaps
from src.thresholds import HUMAN_CONTINUOUS_DIMS  # re-export: 既有呼叫端仍可 from audiofile_status import
```

Delete the old `HUMAN_CONTINUOUS_DIMS = (...)` tuple literal and the `GOLD_MAX_SPREAD` constant + `per_dim_spread` (grep first to confirm no remaining callers besides `gold_lock_prerequisites`, retired in Task 9). Then replace the `compute_status_from_preload` / `compute_audiofile_status` bodies.

新 `compute_status_from_preload`：

```python
def compute_status_from_preload(
    audio: AudioFile,
    annotations: list[Annotation],
    arbitrations: list[Arbitration],
    role_map: dict[str, str],
) -> str:
    """三角架構衍生狀態。role_map: {"creator": id, "industry": id, "audience": id}（呼叫端解析一次）。

    只 creator-industry gap 當闘門；audience 偏離永不 gate。
    """
    creator_id = role_map.get("creator")
    industry_id = role_map.get("industry")

    completed = [a for a in annotations if a.is_complete]
    by_id = {a.annotator_id: a for a in completed}
    has_creator = creator_id in by_id
    has_industry = industry_id in by_id

    if not completed:
        return "untouched"
    if not has_creator:
        return "industry_only" if has_industry else "creator_draft"
    if not has_industry:
        return "creator_draft"

    # creator + industry 齊
    by_role = {
        "creator": by_id.get(creator_id),
        "industry": by_id.get(industry_id),
        "audience": by_id.get(role_map.get("audience")),
    }
    gaps = pairwise_gaps(by_role)
    needs_full = needs_full_arbitration(gaps)

    active = latest_by_audio_field(
        [r for r in arbitrations if r.audio_file_id == audio.id]
    )
    creator_ann = by_role["creator"]

    def _arbitrated(field: str) -> bool:
        rec = active.get((audio.id, field))
        if rec is None:
            return False
        # stale：creator 在仲裁後又改 → 失效
        return not (creator_ann.updated_at and creator_ann.updated_at > rec.arbitrated_at)

    all_arbitrated = all(_arbitrated(f) for f in ARBITRATED_FIELDS)
    if all_arbitrated:
        return "creator_ready"
    # 尚未全仲裁：有任一連續維 gap 超標且未仲裁 → needs_arbitration，否則 fast_confirmable
    unresolved_full = {d for d in needs_full if not _arbitrated(d)}
    return "needs_arbitration" if unresolved_full else "fast_confirmable"
```

Update `compute_audiofile_status` (the DB-querying variant) to load arbitrations + role_map and delegate:

```python
def compute_audiofile_status(audio: AudioFile, session: Session) -> str:
    from src.annotators_loader import annotator_id_for_role  # noqa: PLC0415
    completed = session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio.id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    arbitrations = session.exec(
        select(Arbitration).where(Arbitration.audio_file_id == audio.id)
    ).all()
    role_map = {r: annotator_id_for_role(r) for r in ("creator", "industry", "audience")}
    return compute_status_from_preload(audio, list(completed), list(arbitrations), role_map)
```

Add imports for `Arbitration`, `select`, `Session` if not present. Remove `per_dim_spread` and `GOLD_MAX_SPREAD` only if no other callers remain (grep first — Task 9 handles `gold_lock_prerequisites`).

- [ ] **Step 4: Add `bulk_load_arbitrations_by_audio` import path + a status-order update**

In `audiofile_status.py`, update `_STATUS_ORDER` to include new states while **keeping** old keys for export back-compat:

```python
_STATUS_ORDER = {
    "untouched": 0,
    "industry_only": 1,
    "creator_draft": 1,
    "draft": 1,                 # legacy alias
    "cross_annotated": 2,       # legacy alias
    "needs_arbitration": 2,
    "fast_confirmable": 3,
    "lockable": 3,              # legacy alias
    "creator_ready": 4,
    "gold": 4,                  # legacy alias
}
```

- [ ] **Step 5: Run new tests to verify pass**

Run: `.venv/bin/python -m pytest tests/test_audiofile_status_v2.py -q`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
git add src/audiofile_status.py src/thresholds.py src/arbitration.py src/role_gaps.py tests/test_audiofile_status_v2.py
git commit -m "feat: 三角架構新狀態分類 (audience 不再 gate, creator-industry gap 闘門)"
```

---

## Task 8: 收斂 `audio.py` 的 inline status 重複

**Files:**
- Modify: `src/routes/audio.py` (remove `_compute_status_inline` at ~line 132)
- Test: existing `tests/test_audio_api.py` (run to confirm no regression)

- [ ] **Step 1: Locate the duplicate**

Run: `grep -n "_compute_status_inline\|def.*status\|compute_status" src/routes/audio.py`
Expected: shows `_compute_status_inline` definition + call site.

- [ ] **Step 2: Replace inline with shared call**

Delete the `_compute_status_inline` function. At its call site, build `role_map` once and call `compute_status_from_preload` (preload annotations + arbitrations for the listed audios, mirroring how the list endpoint already bulk-loads). Use `bulk_load_annotations_by_audio` + `bulk_load_arbitrations_by_audio` + `{r: annotator_id_for_role(r) for r in (...)}` resolved once.

```python
from src.annotators_loader import annotator_id_for_role
from src.arbitration import bulk_load_arbitrations_by_audio
from src.audiofile_status import (
    bulk_load_annotations_by_audio, compute_status_from_preload,
)
# ... inside the list handler, once:
role_map = {r: annotator_id_for_role(r) for r in ("creator", "industry", "audience")}
anns_by_audio = bulk_load_annotations_by_audio(session)
arbs_by_audio = bulk_load_arbitrations_by_audio(session)
# per audio:
status = compute_status_from_preload(
    audio, anns_by_audio.get(audio.id, []), arbs_by_audio.get(audio.id, []), role_map,
)
```

- [ ] **Step 3: Run audio API tests**

Run: `.venv/bin/python -m pytest tests/test_audio_api.py -q`
Expected: PASS (failures here mean the list endpoint relied on old status strings — fix the assertions to new vocabulary in Task 10).

- [ ] **Step 4: Commit**

```bash
git add src/routes/audio.py
git commit -m "refactor: 收斂 audio.py inline status 為 compute_status_from_preload"
```

---

## Task 9: `is_gold_locked` 退役 — 停用 lock/unlock 端點

**Files:**
- Modify: `src/routes/admin.py` (lock_gold / unlock_gold ~line 212-290)
- Modify: `src/audiofile_status.py` (`gold_lock_prerequisites` → 標記 deprecated 或刪呼叫)
- Test: `tests/test_admin_api.py` (update expectations)

- [ ] **Step 1: Write failing test**

Append to `tests/test_admin_api.py`:

```python
def test_lock_gold_endpoint_retired_returns_410(client, in_memory_engine, tmp_annotators_config):
    r = client.post("/api/admin/audio/some-id/lock_gold?annotator=amber")
    assert r.status_code == 410
```

(adjust route path to the actual one found via grep `lock_gold` in `src/routes/admin.py`.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_admin_api.py::test_lock_gold_endpoint_retired_returns_410 -q`
Expected: FAIL (currently 200/409, not 410).

- [ ] **Step 3: Retire the endpoints**

In `src/routes/admin.py`, replace the bodies of `lock_gold` / `unlock_gold` handlers with an HTTP 410:

```python
    raise HTTPException(
        status_code=410,
        detail="gold lock 已退役；改由 arbitration 衍生 creator_ready（見 Phase 3 spec）",
    )
```

Keep the route decorators (so old clients get 410, not 404). Remove now-dead prereq computation if it references removed `per_dim_spread`/`GOLD_MAX_SPREAD`.

- [ ] **Step 4: Run to verify pass + full admin suite**

Run: `.venv/bin/python -m pytest tests/test_admin_api.py -q`
Expected: PASS (update any test that asserted successful gold-lock — assert 410 instead).

- [ ] **Step 5: Commit**

```bash
git add src/routes/admin.py src/audiofile_status.py tests/test_admin_api.py
git commit -m "feat: is_gold_locked 退役 — lock/unlock 端點回 410, status 改由 arbitration 衍生"
```

---

## Task 10: 更新既有測試到新狀態詞彙 + 全綠

**Files:**
- Modify: `tests/test_audiofile_status.py` and any test asserting old status strings (`lockable`/`gold`/`cross_annotated`)

- [ ] **Step 1: Find broken assertions**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -30`
Expected: failures concentrated in old status tests + export `min_status` tests.

- [ ] **Step 2: Update assertions**

For each failing test asserting `"lockable"`/`"gold"`/`"cross_annotated"`, map to new vocabulary (`fast_confirmable`/`creator_ready`/`needs_arbitration`) using the role-aware fixtures (creator+industry as the two raters, not arbitrary annotators). Where a test specifically exercised the old spread bug (3-person spread incl. a divergent rater blocking lockable), rewrite it to assert the **new** correct behavior (audience divergence → still `fast_confirmable`) and reference it fixes the bug.

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -5`
Expected: ALL PASS.

- [ ] **Step 4: ruff**

Run: `.venv/bin/ruff check src/ tests/`
Expected: All checks passed (fix any unused-import orphans from removed spread logic).

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: 既有 status 測試更新到三角架構新狀態詞彙"
```

---

## Done criteria (Phase 1–3)

- New tables (`Arbitration`, `AnnotationSnapshot`) create cleanly; `role` validated in config.
- `compute_status_from_preload` returns the 6 new states; **audience divergence never blocks** `fast_confirmable`/`creator_ready` (the bug fix).
- Single status code path (no `_compute_status_inline`); gold lock endpoints return 410.
- `pytest` all green; `ruff` clean.
- No `creator_ready` will appear until Phase 4 writes arbitration records — expected, not a regression.
