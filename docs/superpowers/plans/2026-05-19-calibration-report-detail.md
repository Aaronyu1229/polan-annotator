# 校準報告擴充（detailed calibration report）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把既有 `/api/calibration/report?annotator=<id>` 從聚合-only 擴充為含 overall / per-dim(含 objective no_data) / scatter / top-10 deviations / recommendations 的完整報告，scatter 與 top_deviations 僅 admin 視角回傳。

**Architecture:** 新增專責函式 `build_calibration_report_detailed()` 於 `src/calibration_feedback.py`，內部呼叫既有 `build_calibration_report()`（MAE/Pearson 單一來源、不動）。`/api/calibration/report` route 加 `require_auth`，以 `user["is_admin"]` 決定是否含敏感逐題資料。前端 `calibration-report.html/.js` 整段改寫渲染新 shape。

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / SQLite；pytest；前端 vanilla JS + Tailwind CDN（手刻 SVG，無 chart 套件）。

**Spec:** `docs/superpowers/specs/2026-05-19-calibration-report-detail-design.md`

---

## File Structure

| 檔案 | 動作 | 職責 |
|---|---|---|
| `src/calibration_feedback.py` | Modify | 新增 `WARNING_DIMS_THRESHOLD` / `NEXT_ACTIONS` 常數、`_dim_display` / `_objective_dim_ids` / `_recommendation` / `_build_reference_detail` helper、`build_calibration_report_detailed()`。既有 `build_calibration_report()` 不動。 |
| `src/routes/calibration.py` | Modify (`/report` route, ~line 175-186) | 加 `Depends(require_auth)`，改呼叫 detailed builder 並傳 admin flag |
| `static/calibration-report.html` | Modify (整檔改寫，66 行) | 加 overall 卡 / recommendations / scatter 區 / top-10 區的容器 |
| `static/calibration-report.js` | Modify (整檔改寫，144 行) | 渲染新 response shape（dimensions 改 list、overall、scatter SVG、top-10 inline audio） |
| `tests/test_calibration_feedback.py` | Modify (append) | detailed builder 單元測試 |
| `tests/test_calibration_report_api.py` | Create | route 層 admin / 非 admin gating 整合測試 |

**注意（向後相容）**：只有 `static/calibration-report.js` fetch `/api/calibration/report`；`dashboard.js`/`list.js` 僅連到頁面、`src/routes/stats.py:82` 直接呼叫**未改動的** `build_calibration_report()`。故 response shape 變更僅影響本頁，前端同 task 改寫即一致。

---

## Task 1: 後端 detailed builder 核心（無 scatter/top）

**Files:**
- Modify: `src/calibration_feedback.py`
- Test: `tests/test_calibration_feedback.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calibration_feedback.py`:

```python
# ---------------------------------------------------------------------------
# build_calibration_report_detailed — 核心(無 scatter/top)
# ---------------------------------------------------------------------------

from src.calibration_feedback import build_calibration_report_detailed


def test_detailed_reference_returns_minimal(in_memory_engine):
    with Session(in_memory_engine) as s:
        r = build_calibration_report_detailed(s, "amber", include_reference_detail=True)
    assert r["is_reference"] is True
    assert r["dimensions"] == []
    assert r["overall"] is None
    assert r["recommendations"] is None
    assert r["calibration_progress"] == "0/0"
    assert "scatter_data" not in r


def test_detailed_no_overlap_minimal(in_memory_engine):
    aid = _save_audio(in_memory_engine, "A_Base Game.wav")
    _save_annotation(in_memory_engine, aid, "amber")
    with Session(in_memory_engine) as s:
        r = build_calibration_report_detailed(s, "vvgosick", include_reference_detail=True)
    assert r["is_reference"] is False
    assert r["calibration_progress"] == "0/1"
    assert r["dimensions"] == []
    assert r["overall"] is None


def test_detailed_dimensions_list_has_subjective_and_objective(in_memory_engine):
    audios = [_save_audio(in_memory_engine, f"A{i}_Base Game.wav") for i in range(3)]
    for aid in audios:
        _save_annotation(in_memory_engine, aid, "amber", valence=0.5)
        _save_annotation(in_memory_engine, aid, "vvgosick", valence=0.7)
    with Session(in_memory_engine) as s:
        r = build_calibration_report_detailed(s, "vvgosick", include_reference_detail=False)

    names = {d["name"]: d for d in r["dimensions"]}
    # 7 subjective + 3 objective(loop_capability / tonal_noise_ratio / spectral_density)
    assert names["valence"]["category"] == "subjective"
    assert names["valence"]["overlap_count"] == 3
    assert names["valence"]["mae"] == 0.2
    assert names["valence"]["status"] == "warning"  # 0.2 > 0.15
    for objective in ("loop_capability", "tonal_noise_ratio", "spectral_density"):
        assert names[objective]["category"] == "objective"
        assert names[objective]["status"] == "no_data"
        assert names[objective]["mae"] is None
    assert names["valence"]["display_name_zh"]  # 取自 label_zh，非空


def test_detailed_overall_and_recommendation(in_memory_engine):
    audios = [_save_audio(in_memory_engine, f"A{i}_Base Game.wav") for i in range(3)]
    for aid in audios:
        _save_annotation(in_memory_engine, aid, "amber", valence=0.5, arousal=0.5)
        _save_annotation(in_memory_engine, aid, "vvgosick", valence=0.5, arousal=0.5)
    with Session(in_memory_engine) as s:
        r = build_calibration_report_detailed(s, "vvgosick", include_reference_detail=False)
    # 全對齊 → mae 0.0、無 warning → approved
    assert r["overall"]["mae"] == 0.0
    assert r["overall"]["warning_dims_count"] == 0
    assert r["overall"]["warning_dims_threshold"] == 2
    assert r["overall"]["recommendation"] == "approved"
    assert "valence" in r["recommendations"]["dims_approved"]
    assert len(r["recommendations"]["next_actions"]) == 3


def test_detailed_recommendation_not_recommended(in_memory_engine):
    audios = [_save_audio(in_memory_engine, f"A{i}_Base Game.wav") for i in range(3)]
    for aid in audios:
        _save_annotation(in_memory_engine, aid, "amber", valence=0.1, arousal=0.1)
        _save_annotation(in_memory_engine, aid, "vvgosick", valence=0.9, arousal=0.9)
    with Session(in_memory_engine) as s:
        r = build_calibration_report_detailed(s, "vvgosick", include_reference_detail=False)
    # mae 0.8 > 0.30 → not_recommended
    assert r["overall"]["recommendation"] == "not_recommended"
    assert "valence" in r["recommendations"]["dims_to_retrain"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_calibration_feedback.py -k detailed -q`
Expected: FAIL — `ImportError: cannot import name 'build_calibration_report_detailed'`

- [ ] **Step 3: Write minimal implementation**

In `src/calibration_feedback.py`, after the `HUMAN_CONTINUOUS_DIMS` tuple (after line 46) add constants:

```python
# 報告層判定常數
WARNING_DIMS_THRESHOLD = 2

# 繁中固定建議文案（CLAUDE.md UI convention：sentence case，具體）
NEXT_ACTIONS = (
    "與 Amber 一對一過 Top 10 偏差最大的音檔（約 1 hr）",
    "重做校準集第二輪標註（全部校準題）",
    "重新計算 MAE，目標降至 ≤ 0.15 後由 Amber 認可通過",
)
```

At the end of `src/calibration_feedback.py` (after `_pearson`) add:

```python
def _dim_display(dim_id: str) -> str:
    """維度中文顯示名 = dimensions_config 的 label_zh（唯一來源）。"""
    from src.dimensions_loader import load_dimensions  # noqa: PLC0415
    return load_dimensions().get(dim_id, {}).get("label_zh", dim_id)


def _objective_dim_ids() -> list[str]:
    """dimensions_config 內、不在 HUMAN_CONTINUOUS_DIMS 的維度（無人工 vs amber MAE）。"""
    from src.dimensions_loader import load_dimensions  # noqa: PLC0415
    return [d for d in load_dimensions() if d not in HUMAN_CONTINUOUS_DIMS]


def _recommendation(overall_mae: Optional[float], warning_count: int) -> str:
    if overall_mae is None:
        return "needs_training"
    if overall_mae <= GREEN_THRESHOLD and warning_count < WARNING_DIMS_THRESHOLD:
        return "approved"
    if overall_mae > YELLOW_THRESHOLD:
        return "not_recommended"
    return "needs_training"


def build_calibration_report_detailed(
    session: Session,
    annotator_id: str,
    include_reference_detail: bool,
) -> dict[str, Any]:
    """合伙人 spec 的完整校準報告。

    scatter_data / top_deviations 含 reference(amber) 逐題值 —
    只在 include_reference_detail=True（admin 視角）才加入。
    既有 build_calibration_report() 不動，本函式呼叫它取 per-dim 核心。
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from src.annotators_loader import get_annotator  # noqa: PLC0415

    core = build_calibration_report(session, annotator_id)
    spec = get_annotator(annotator_id) or {}
    generated = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    base = {
        "annotator": annotator_id,
        "annotator_name": spec.get("name") or annotator_id,
        "role": spec.get("annotator_profile"),
        "is_reference": bool(core.get("is_reference", False)),
        "report_generated_at": generated,
    }

    if core.get("is_reference") or core.get("total_overlap", 0) == 0:
        return {
            **base,
            "calibration_progress": (
                f"{core.get('total_overlap', 0)}/{core.get('reference_total', 0)}"
            ),
            "overall": None,
            "dimensions": [],
            "recommendations": None,
        }

    per_dim = core["dimensions"]
    dimensions: list[dict[str, Any]] = []
    maes: list[float] = []
    warning_count = 0

    for dim in HUMAN_CONTINUOUS_DIMS:
        d = per_dim.get(dim, {})
        mae = d.get("mae")
        size = d.get("sample_size", 0)
        if mae is None or size == 0:
            status = "no_data"
        elif mae > GREEN_THRESHOLD:
            status = "warning"
            warning_count += 1
        else:
            status = "ok"
        if mae is not None:
            maes.append(mae)
        dimensions.append({
            "name": dim,
            "display_name_zh": _dim_display(dim),
            "category": "subjective",
            "mae": mae,
            "threshold": GREEN_THRESHOLD,
            "status": status,
            "overlap_count": size,
        })

    for dim in _objective_dim_ids():
        dimensions.append({
            "name": dim,
            "display_name_zh": _dim_display(dim),
            "category": "objective",
            "mae": None,
            "threshold": GREEN_THRESHOLD,
            "status": "no_data",
            "overlap_count": 0,
        })

    overall_mae = round(sum(maes) / len(maes), 3) if maes else None
    overall = {
        "mae": overall_mae,
        "threshold": GREEN_THRESHOLD,
        "warning_dims_count": warning_count,
        "warning_dims_threshold": WARNING_DIMS_THRESHOLD,
        "recommendation": _recommendation(overall_mae, warning_count),
    }
    recommendations = {
        "dims_to_retrain": [
            d["name"] for d in dimensions
            if d["category"] == "subjective" and d["status"] == "warning"
        ],
        "dims_approved": [
            d["name"] for d in dimensions
            if d["category"] == "subjective" and d["status"] == "ok"
        ],
        "dims_no_data": [d["name"] for d in dimensions if d["status"] == "no_data"],
        "next_actions": list(NEXT_ACTIONS),
    }

    return {
        **base,
        "calibration_progress": (
            f"{core['total_overlap']}/{core['reference_total']}"
        ),
        "overall": overall,
        "dimensions": dimensions,
        "recommendations": recommendations,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_calibration_feedback.py -k detailed -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/calibration_feedback.py tests/test_calibration_feedback.py
git -c commit.gpgsign=false commit -m "feat: detailed calibration report builder core"
```

---

## Task 2: 後端 scatter_data + top_deviations（admin-gated）

**Files:**
- Modify: `src/calibration_feedback.py`
- Test: `tests/test_calibration_feedback.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_calibration_feedback.py`:

```python
def test_detailed_admin_includes_scatter_and_top(in_memory_engine):
    a0 = _save_audio(in_memory_engine, "GameX_Free Game.wav")
    a1 = _save_audio(in_memory_engine, "GameY_Base Game.wav")
    _save_annotation(in_memory_engine, a0, "amber", valence=0.9, arousal=0.5)
    _save_annotation(in_memory_engine, a0, "vvgosick", valence=0.2, arousal=0.5)  # diff 0.7
    _save_annotation(in_memory_engine, a1, "amber", valence=0.5, arousal=0.5)
    _save_annotation(in_memory_engine, a1, "vvgosick", valence=0.55, arousal=0.5)  # diff 0.05

    with Session(in_memory_engine) as s:
        r = build_calibration_report_detailed(s, "vvgosick", include_reference_detail=True)

    assert "scatter_data" in r
    assert "top_deviations" in r
    # scatter：每 subjective dim 一個 array，valence 有 2 點
    assert len(r["scatter_data"]["valence"]) == 2
    assert {"file", "amber", "annotator"} == set(r["scatter_data"]["valence"][0])
    # top_deviations：依跨維最大 diff 由大到小，diff 0.7 那筆排第一
    top = r["top_deviations"]
    assert len(top) == 2
    assert top[0]["file"] == "GameX_Free Game.wav"
    assert top[0]["worst_dim"] == "valence"
    assert top[0]["amber_value"] == 0.9
    assert top[0]["annotator_value"] == 0.2
    assert top[0]["diff"] == 0.7
    assert top[0]["game"] == "GameX"
    assert top[0]["section"] == "Free Game"
    assert top[0]["audio_url"] == f"/api/audio/{a0}/stream"
    assert "valence" in top[0]["all_dims"]
    assert top[0]["all_dims"]["valence"]["diff"] == 0.7


def test_detailed_non_admin_omits_sensitive(in_memory_engine):
    aid = _save_audio(in_memory_engine, "G_Base Game.wav")
    _save_annotation(in_memory_engine, aid, "amber", valence=0.5)
    _save_annotation(in_memory_engine, aid, "vvgosick", valence=0.7)
    with Session(in_memory_engine) as s:
        r = build_calibration_report_detailed(s, "vvgosick", include_reference_detail=False)
    assert "scatter_data" not in r
    assert "top_deviations" not in r
    # 聚合區塊仍完整
    assert r["overall"] is not None
    assert any(d["name"] == "valence" for d in r["dimensions"])


def test_detailed_top_deviations_capped_at_10(in_memory_engine):
    for i in range(13):
        aid = _save_audio(in_memory_engine, f"G{i}_Base Game.wav")
        _save_annotation(in_memory_engine, aid, "amber", valence=0.1)
        _save_annotation(in_memory_engine, aid, "vvgosick", valence=0.9)
    with Session(in_memory_engine) as s:
        r = build_calibration_report_detailed(s, "vvgosick", include_reference_detail=True)
    assert len(r["top_deviations"]) == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_calibration_feedback.py -k "admin_includes or non_admin_omits or capped_at_10" -q`
Expected: FAIL — `KeyError: 'scatter_data'` (still omitted)

- [ ] **Step 3: Write minimal implementation**

In `src/calibration_feedback.py`, add helper after `build_calibration_report_detailed`:

```python
def _build_reference_detail(
    session: Session,
    annotator_id: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """回 (scatter_data, top_deviations)。含 amber 逐題值 — 只在 admin 視角呼叫。"""
    from src.models import AudioFile  # noqa: PLC0415

    ref_rows = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == REFERENCE_ANNOTATOR,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    my_rows = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == annotator_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    ref_by = {a.audio_file_id: a for a in ref_rows}
    my_by = {a.audio_file_id: a for a in my_rows}
    overlap = set(ref_by) & set(my_by)

    audio_by_id: dict[str, Any] = {}
    if overlap:
        audio_by_id = {
            a.id: a
            for a in session.exec(
                select(AudioFile).where(
                    AudioFile.id.in_(overlap)  # type: ignore[attr-defined]
                )
            ).all()
        }

    scatter: dict[str, list[dict[str, Any]]] = {
        d: [] for d in HUMAN_CONTINUOUS_DIMS
    }
    deviations: list[dict[str, Any]] = []

    for aid in overlap:
        ref = ref_by[aid]
        mine = my_by[aid]
        audio = audio_by_id.get(aid)
        fname = audio.filename if audio else aid
        all_dims: dict[str, dict[str, float]] = {}
        worst_dim: Optional[str] = None
        worst_diff = -1.0
        for dim in HUMAN_CONTINUOUS_DIMS:
            rv = getattr(ref, dim, None)
            mv = getattr(mine, dim, None)
            if rv is None or mv is None:
                continue
            rv = float(rv)
            mv = float(mv)
            scatter[dim].append({"file": fname, "amber": rv, "annotator": mv})
            diff = round(abs(mv - rv), 3)
            all_dims[dim] = {"amber": rv, "annotator": mv, "diff": diff}
            if diff > worst_diff:
                worst_diff = diff
                worst_dim = dim
        if worst_dim is None:
            continue
        deviations.append({
            "file": fname,
            "game": audio.game_name if audio else "",
            "section": audio.game_stage if audio else "",
            "audio_url": f"/api/audio/{aid}/stream",
            "worst_dim": worst_dim,
            "worst_dim_display": _dim_display(worst_dim),
            "amber_value": all_dims[worst_dim]["amber"],
            "annotator_value": all_dims[worst_dim]["annotator"],
            "diff": all_dims[worst_dim]["diff"],
            "all_dims": all_dims,
        })

    deviations.sort(key=lambda d: d["diff"], reverse=True)
    return scatter, deviations[:10]
```

Then in `build_calibration_report_detailed`, replace the final `return { **base, ... "recommendations": recommendations }` block with:

```python
    result = {
        **base,
        "calibration_progress": (
            f"{core['total_overlap']}/{core['reference_total']}"
        ),
        "overall": overall,
        "dimensions": dimensions,
        "recommendations": recommendations,
    }
    if include_reference_detail:
        scatter, top = _build_reference_detail(session, annotator_id)
        result["scatter_data"] = scatter
        result["top_deviations"] = top
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_calibration_feedback.py -k detailed -q`
Expected: PASS (8 passed — Task 1 + Task 2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/calibration_feedback.py tests/test_calibration_feedback.py
git -c commit.gpgsign=false commit -m "feat: admin-gated scatter + top deviations in calibration report"
```

---

## Task 3: Route 接線（require_auth + admin flag）

**Files:**
- Modify: `src/routes/calibration.py` (the `/report` route, ~line 175-186)
- Test: Create `tests/test_calibration_report_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_calibration_report_api.py`:

```python
"""校準報告 API 層 — admin / 非 admin 揭露 gating。

dependency_overrides[require_auth] 模擬登入者（沿用 test_annotator_detail_api.py 模式）。
"""
from __future__ import annotations

import json

from sqlmodel import Session

import src.main as main_module
from src.models import Annotation, AudioFile


def _override_user(annotator_id: str, *, is_admin: bool):
    from src.middleware import require_auth
    main_module.app.dependency_overrides[require_auth] = lambda: {
        "annotator_id": annotator_id,
        "is_admin": is_admin,
        "name": None,
    }


def _seed(engine):
    with Session(engine) as s:
        a = AudioFile(filename="G_Free Game.wav", game_name="G", game_stage="Free Game")
        s.add(a)
        s.commit()
        s.refresh(a)
        aid = a.id
        common = dict(
            is_complete=True,
            loop_capability=json.dumps([1.0]),
            source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]),
            genre_tag=json.dumps([]),
            style_tag=json.dumps([]),
            arousal=0.5, emotional_warmth=0.5, tension_direction=0.5,
            temporal_position=0.5, event_significance=0.5, world_immersion=0.5,
        )
        s.add(Annotation(audio_file_id=aid, annotator_id="amber", valence=0.9, **common))
        s.add(Annotation(audio_file_id=aid, annotator_id="vvgosick", valence=0.2, **common))
        s.commit()


def test_admin_sees_scatter_and_top(client, in_memory_engine):
    _seed(in_memory_engine)
    _override_user("amber", is_admin=True)
    r = client.get("/api/calibration/report?annotator=vvgosick")
    main_module.app.dependency_overrides.pop(_get_require_auth(), None)
    assert r.status_code == 200
    body = r.json()
    assert "scatter_data" in body
    assert "top_deviations" in body
    assert body["overall"]["recommendation"] in {
        "approved", "needs_training", "not_recommended",
    }


def test_non_admin_no_scatter(client, in_memory_engine):
    _seed(in_memory_engine)
    _override_user("vvgosick", is_admin=False)
    r = client.get("/api/calibration/report?annotator=vvgosick")
    assert r.status_code == 200
    body = r.json()
    assert "scatter_data" not in body
    assert "top_deviations" not in body
    assert body["overall"] is not None


def test_amber_is_reference(client, in_memory_engine):
    _seed(in_memory_engine)
    _override_user("amber", is_admin=True)
    r = client.get("/api/calibration/report?annotator=amber")
    assert r.status_code == 200
    assert r.json()["is_reference"] is True


def _get_require_auth():
    from src.middleware import require_auth
    return require_auth
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_calibration_report_api.py -q`
Expected: FAIL — `test_non_admin_no_scatter` fails because current route ignores auth and still calls old aggregate builder (no `overall` key → `KeyError`/`AssertionError`).

- [ ] **Step 3: Write minimal implementation**

In `src/routes/calibration.py`, replace the `/report` api route (currently ~line 175-186):

```python
@api_router.get("/report")
def calibration_report(
    annotator: str,
    user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """annotator 的完整校準報告。

    scatter_data / top_deviations 含 reference(amber) 逐題值，
    只在 admin 視角回（保 multi-perspective 中立性）。
    """
    from src.calibration_feedback import (  # noqa: PLC0415
        build_calibration_report_detailed,
    )

    return build_calibration_report_detailed(
        session,
        annotator,
        include_reference_detail=bool(user.get("is_admin")),
    )
```

(`Depends`, `require_auth`, `Any`, `Session`, `get_session` already imported at top of file — verify lines 20, 24-26.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_calibration_report_api.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/routes/calibration.py tests/test_calibration_report_api.py
git -c commit.gpgsign=false commit -m "feat: wire calibration report route to detailed builder with admin gate"
```

---

## Task 4: 前端改寫（overall + dimensions list + recommendations + scatter + top-10）

**Files:**
- Modify: `static/calibration-report.html` (整檔覆寫)
- Modify: `static/calibration-report.js` (整檔覆寫)

> 本 repo 無 JS 測試框架（前端為 vanilla，沿用 Phase 9 / upload-preview / annotator-detail 慣例：spec + 手動驗證）。本 task 以手動載入驗證取代自動測試。

- [ ] **Step 1: 覆寫 `static/calibration-report.html`**

完整內容：

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>校準完成報告 — 珀瀾標註工具</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 text-slate-900 dark:bg-slate-900 dark:text-slate-100">
  <main class="max-w-3xl mx-auto p-6">
    <header class="mb-6">
      <div class="flex items-baseline justify-between mb-2">
        <h1 class="text-2xl font-semibold">📊 校準完成報告</h1>
        <a id="back-link" href="/" class="text-sm text-slate-500 hover:text-amber-500">← 主頁</a>
      </div>
      <p id="header-meta" class="text-sm text-slate-600 dark:text-slate-400">載入中…</p>
    </header>

    <section id="overall-panel" class="bg-white dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 p-4 mb-6"></section>

    <section class="bg-white dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 overflow-hidden mb-6">
      <div class="p-4 border-b border-slate-200 dark:border-slate-700">
        <h2 class="text-base font-semibold">維度對齊度</h2>
        <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">
          MAE = 你跟 Amber 的平均絕對差距，越小越接近。objective 維度由 librosa 計算，無人工校準資料。
        </p>
      </div>
      <table class="w-full text-sm">
        <thead class="bg-slate-100 dark:bg-slate-900/70 text-xs uppercase tracking-wide text-slate-600 dark:text-slate-400">
          <tr>
            <th class="text-left p-3 font-medium">維度</th>
            <th class="text-left p-3 font-medium">類別</th>
            <th class="text-right p-3 font-medium">N</th>
            <th class="text-right p-3 font-medium">MAE</th>
            <th class="text-center p-3 font-medium w-20">狀態</th>
          </tr>
        </thead>
        <tbody id="dims-tbody"></tbody>
      </table>
    </section>

    <section id="scatter-panel" class="mb-6 hidden">
      <h2 class="text-base font-semibold mb-2">逐題散點（Amber × 你）</h2>
      <p class="text-xs text-slate-500 dark:text-slate-400 mb-3">對角線 = 完美一致。離線越遠代表該題分歧越大。（僅 admin 可見）</p>
      <div id="scatter-grid" class="grid grid-cols-2 sm:grid-cols-3 gap-4"></div>
    </section>

    <section id="top-panel" class="mb-6 hidden">
      <h2 class="text-base font-semibold mb-2">Top 10 偏差最大音檔</h2>
      <div id="top-list" class="space-y-2"></div>
    </section>

    <footer class="mt-6 text-xs text-slate-500 dark:text-slate-500">
      <p>狀態：🟢 ok（MAE ≤ 0.15）/ 🟡 warning（&gt; 0.15）/ ⚪ 無資料</p>
      <p class="mt-1">Amber 看完此報告後會在 Dashboard 認可校準通過，你才會解鎖標全部音檔。</p>
    </footer>
  </main>

  <script type="module" src="/static/auth.js"></script>
  <script type="module" src="/static/calibration-report.js"></script>
</body>
</html>
```

- [ ] **Step 2: 覆寫 `static/calibration-report.js`**

完整內容：

```javascript
// 校準完成報告渲染（detailed）。
// fetch /api/calibration/report?annotator=X → 渲染 overall / 維度表 /
// scatter（admin）/ top-10（admin）。response shape 見
// docs/superpowers/specs/2026-05-19-calibration-report-detail-design.md

const $ = id => document.getElementById(id)
const params = new URLSearchParams(window.location.search)
const annotator = params.get('annotator') || ''

loadAll()

async function loadAll() {
  if (!annotator) {
    $('header-meta').textContent = '請從 URL 帶 ?annotator=xxx'
    return
  }
  $('back-link').href = `/?annotator=${encodeURIComponent(annotator)}`
  try {
    const res = await fetch(
      `/api/calibration/report?annotator=${encodeURIComponent(annotator)}`,
    )
    if (!res.ok) throw new Error(`報告 HTTP ${res.status}`)
    render(await res.json())
  } catch (err) {
    $('header-meta').textContent = `載入失敗：${err.message}`
  }
}

function render(r) {
  $('header-meta').textContent =
    `標註員：${r.annotator_name || r.annotator}` +
    (r.role ? `（${r.role}）` : '') +
    ` · 進度 ${r.calibration_progress}`

  if (r.is_reference) {
    $('overall-panel').innerHTML =
      `<p class="text-sm text-slate-600 dark:text-slate-400">` +
      `${escapeHtml(r.annotator_name || r.annotator)} 是 reference annotator，` +
      `不需要校準報告。</p>`
    $('dims-tbody').innerHTML = ''
    return
  }
  if (!r.overall) {
    $('overall-panel').innerHTML =
      `<p class="text-sm text-amber-700 dark:text-amber-300">尚未開始校準` +
      `（進度 ${escapeHtml(r.calibration_progress)}）。` +
      `<a class="underline" href="/calibration?annotator=` +
      `${encodeURIComponent(annotator)}">前往校準頁 →</a></p>`
    $('dims-tbody').innerHTML = ''
    return
  }

  renderOverall(r.overall, r.recommendations)
  renderDimensions(r.dimensions || [])
  if (r.scatter_data) renderScatter(r.scatter_data)
  if (r.top_deviations) renderTop(r.top_deviations)
}

function renderOverall(o, rec) {
  const recMap = {
    approved: ['🟢 建議認可', 'text-emerald-700 dark:text-emerald-300'],
    needs_training: ['🟡 需再訓練', 'text-yellow-700 dark:text-yellow-300'],
    not_recommended: ['🔴 不建議通過', 'text-rose-700 dark:text-rose-300'],
  }
  const [recLabel, recColor] = recMap[o.recommendation] || ['—', '']
  const retrain = (rec && rec.dims_to_retrain) || []
  $('overall-panel').innerHTML = `
    <div class="flex items-baseline justify-between mb-2">
      <div class="text-sm text-slate-500 dark:text-slate-400">整體 MAE</div>
      <div class="text-sm font-semibold ${recColor}">${recLabel}</div>
    </div>
    <div class="flex items-baseline gap-2">
      <span class="text-3xl font-semibold font-mono">${o.mae == null ? '—' : o.mae.toFixed(3)}</span>
      <span class="text-sm text-slate-500 dark:text-slate-400">門檻 ${o.threshold}</span>
    </div>
    <div class="text-sm text-slate-600 dark:text-slate-400 mt-2">
      警示維度 ${o.warning_dims_count} / ${o.warning_dims_threshold}
      ${retrain.length ? `· 需重訓：${retrain.map(escapeHtml).join('、')}` : ''}
    </div>
  `
}

function renderDimensions(dims) {
  const statusBadge = {
    ok: '<span class="text-emerald-600 dark:text-emerald-400 text-lg">🟢</span>',
    warning: '<span class="text-yellow-600 dark:text-yellow-400 text-lg">🟡</span>',
    no_data: '<span class="text-slate-400 text-lg">⚪</span>',
  }
  $('dims-tbody').innerHTML = dims.map(d => `
    <tr class="border-t border-slate-200 dark:border-slate-700${d.status === 'no_data' ? ' text-slate-400' : ''}">
      <td class="p-3 font-medium">${escapeHtml(d.display_name_zh)}</td>
      <td class="p-3">${d.category === 'subjective' ? '主觀' : '客觀'}</td>
      <td class="p-3 text-right font-mono">${d.overlap_count}</td>
      <td class="p-3 text-right font-mono">${d.mae == null ? '—' : d.mae.toFixed(3)}</td>
      <td class="p-3 text-center">${statusBadge[d.status] || '—'}</td>
    </tr>
  `).join('')
}

function renderScatter(scatter) {
  const grid = $('scatter-grid')
  const entries = Object.entries(scatter).filter(([, pts]) => pts.length)
  if (!entries.length) return
  $('scatter-panel').classList.remove('hidden')
  grid.innerHTML = entries.map(([dim, pts]) => `
    <figure class="bg-white dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 p-2">
      <figcaption class="text-xs text-slate-600 dark:text-slate-400 mb-1">${escapeHtml(dim)}</figcaption>
      ${scatterSvg(pts)}
    </figure>
  `).join('')
}

function scatterSvg(points) {
  const S = 140, P = 10, span = S - 2 * P
  const x = v => P + v * span
  const y = v => S - P - v * span  // SVG y 反向
  const dots = points.map(p =>
    `<circle cx="${x(p.amber).toFixed(1)}" cy="${y(p.annotator).toFixed(1)}" r="3" fill="#f59e0b" fill-opacity="0.6"/>`,
  ).join('')
  return `<svg viewBox="0 0 ${S} ${S}" class="w-full h-auto" role="img" aria-label="散點圖">
    <rect x="${P}" y="${P}" width="${span}" height="${span}" fill="none" stroke="#cbd5e1"/>
    <line x1="${P}" y1="${S - P}" x2="${S - P}" y2="${P}" stroke="#94a3b8" stroke-dasharray="3 3"/>
    ${dots}
  </svg>`
}

function renderTop(items) {
  if (!items.length) return
  $('top-panel').classList.remove('hidden')
  $('top-list').innerHTML = items.map(it => `
    <details class="bg-white dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 p-3 text-sm">
      <summary class="cursor-pointer flex items-center justify-between gap-3">
        <span class="font-medium">${escapeHtml(it.game)} · ${escapeHtml(it.section)}</span>
        <span class="font-mono text-rose-600 dark:text-rose-400">Δ${it.diff.toFixed(3)} ${escapeHtml(it.worst_dim_display)}</span>
      </summary>
      <audio controls preload="none" class="w-full mt-2" src="${it.audio_url}"></audio>
      <table class="w-full mt-2 text-xs">
        <tbody>
          ${Object.entries(it.all_dims).map(([dim, v]) => `
            <tr class="border-t border-slate-100 dark:border-slate-700/50">
              <td class="py-1">${escapeHtml(dim)}</td>
              <td class="py-1 text-right font-mono">Amber ${v.amber.toFixed(2)}</td>
              <td class="py-1 text-right font-mono">你 ${v.annotator.toFixed(2)}</td>
              <td class="py-1 text-right font-mono ${v.diff > 0.30 ? 'text-rose-600 dark:text-rose-400' : v.diff > 0.15 ? 'text-yellow-600 dark:text-yellow-400' : 'text-slate-500'}">Δ${v.diff.toFixed(2)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </details>
  `).join('')
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
```

- [ ] **Step 3: 手動驗證（本機）**

```bash
.venv/bin/uvicorn src.main:app --port 8000 &
sleep 3
```

開 `http://localhost:8000/calibration/report?annotator=vvgosick`（本機 dev `is_admin` 恆 True）。
逐項確認：
- header 顯示「標註員：Vic（general_audience）· 進度 N/M」
- Overall 卡有 MAE 數字 + recommendation 徽章
- 維度表 10 列：7 主觀有數值、3 客觀（loop_capability/tonal_noise_ratio/spectral_density）灰底 ⚪ 無資料
- 「逐題散點」區出現，每主觀維度一張 SVG，對角線虛線可見、點為琥珀色
- 「Top 10 偏差」區出現，展開某列可見 inline `<audio>` 可播放 + all_dims 表
- 開 `?annotator=amber` → 顯示「是 reference annotator，不需要校準報告」不報錯
- 瀏覽器 console 無 error

驗證後關閉：`kill %1`

- [ ] **Step 4: Commit**

```bash
git add static/calibration-report.html static/calibration-report.js
git -c commit.gpgsign=false commit -m "feat: render detailed calibration report (overall, scatter, top-10)"
```

---

## Task 5: 全套件驗證 + lint + 收尾

**Files:** 無新增（驗證 task）

- [ ] **Step 1: 全測試套件**

Run: `.venv/bin/pytest -q`
Expected: 全綠（既有 236+ 測試 + 本次新增 11 個，0 failed）

- [ ] **Step 2: 覆蓋率**

Run: `.venv/bin/pytest --cov=src/calibration_feedback --cov=src/routes/calibration --cov-report=term-missing -q`
Expected: `src/calibration_feedback.py` 與 `src/routes/calibration.py` 覆蓋率 ≥ 80%。若 < 80%，補對應 missing 行的測試後重跑。

- [ ] **Step 3: Lint**

Run: `.venv/bin/ruff check src/calibration_feedback.py src/routes/calibration.py tests/test_calibration_report_api.py`
Expected: `All checks passed!`（有問題就修，重跑至綠）

- [ ] **Step 4: 確認 stats.py 未受影響（向後相容）**

Run: `.venv/bin/pytest tests/test_annotator_detail_api.py -q`
Expected: 全綠（證明未改動的 `build_calibration_report()` 對 `stats.py` 消費者行為不變）

- [ ] **Step 5: Final commit（若 Step 2 有補測試）**

```bash
git add -A
git -c commit.gpgsign=false commit -m "test: coverage top-up for detailed calibration report" || echo "無新增，跳過"
```

---

## Self-Review

**1. Spec coverage：**
- §5.1 admin 完整 shape → Task 1（base/overall/dimensions/recommendations）+ Task 2（scatter/top）✓
- §5.2 非 admin 省略敏感 key → Task 2 `test_detailed_non_admin_omits_sensitive` + Task 3 `test_non_admin_no_scatter` ✓
- §5.3 amber / 無 overlap 精簡 → Task 1 `test_detailed_reference_returns_minimal` / `test_detailed_no_overlap_minimal` + Task 3 `test_amber_is_reference` ✓
- §3 修正①路徑（用既有 /api/calibration/report）→ Task 3 ✓ ②demo vvgosick → 測試與手動驗證皆用 vvgosick ✓ ③不 hardcode 數字 → `calibration_progress` 由 core 動態組 ✓ ④label_zh → `_dim_display` ✓ ⑤admin-only → Task 2/3 ✓
- §3 category 規則（HUMAN_CONTINUOUS_DIMS → subjective，餘 objective）→ Task 1 `_objective_dim_ids` + 測試 ✓
- §6 recommendation 三檔 → Task 1 `_recommendation` + `test_detailed_overall_and_recommendation` / `test_detailed_recommendation_not_recommended` ✓
- §8 前端（overall 卡 / 維度表 category+no_data / SVG scatter admin / top-10 inline audio）→ Task 4 ✓
- §10 測試（含/不含 reference detail、objective no_data、top-N 排序、recommendation 邊界、route admin gate）→ Task 1-3 ✓

**2. Placeholder scan：** 無 TBD/TODO；所有 code step 含完整可執行內容；前端無 JS 測試框架已明說以手動驗證取代（非 placeholder，是事實）。

**3. Type consistency：** `build_calibration_report_detailed(session, annotator_id, include_reference_detail)` 簽名於 Task 1 定義，Task 2/3 一致使用；回傳 key（`annotator`/`overall`/`dimensions` list/`scatter_data`/`top_deviations`）在 Task 1→2→3→4 全程一致；前端 `render()` 讀的 key 與後端輸出對齊（`annotator_name`/`role`/`calibration_progress`/`overall`/`dimensions[].display_name_zh`/`scatter_data`/`top_deviations[].all_dims`）。
