# 標註員詳細頁（annotator detail view）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Amber（admin）/ 本人從 dashboard 進度列點標註員名字，進入該標註員詳細頁，看到 ta 全部完成標註（可排序、就地展開完整標註結果）+ 上方彙整統計（含 vs Amber 校準摘要）。

**Architecture:** 一支 composed API（`GET /api/stats/annotator/{id}/detail`）重用既有 `compute_progress` + `build_calibration_report`，外加一支 join 查詢序列化該人所有 `is_complete` 標註；一個純 `FileResponse` 頁面路由（權限由 API 把關，admin-or-self）；vanilla + Tailwind CDN 前端，inline 展開；dashboard 進度列名字依權限變連結。

**Tech Stack:** FastAPI + SQLModel（SQLite）、pytest、vanilla HTML/JS + Tailwind CDN。沿用 `docs/superpowers/specs/2026-05-19-annotator-detail-view-design.md`。

**Conventions（CLAUDE.md）:** Python 4-space + type hints + 具體 except；JS 2-space + 無分號 + ES module + `const` 為主；使用者可見文字繁中；commit 用近期實際慣例 `feat:`（spec 已註記文件落差）；無動畫。

---

## File Structure

| 檔案 | 動作 | 責任 |
|---|---|---|
| `src/annotation_serialization.py` | Create | `annotation_to_dict(ann)`：單筆 Annotation → dict，JSON 字串欄位 decode 成 list |
| `src/routes/stats.py` | Modify | 新增 `GET /api/stats/annotator/{annotator_id}/detail` |
| `src/main.py` | Modify | 新增頁面路由 `GET /annotator/{annotator_id}` |
| `static/annotator-detail.html` | Create | 詳細頁骨架 |
| `static/annotator-detail.js` | Create | 詳細頁邏輯（統計卡 + 可排序表 + inline 展開） |
| `static/dashboard.js` | Modify | `loadProgressForAll()` 名字依權限 render 成連結 |
| `tests/test_annotation_serialization.py` | Create | `annotation_to_dict` 單測 |
| `tests/test_annotator_detail_api.py` | Create | API 行為 + 頁面路由 200 |

不動：既有 3 處重複序列化（外科原則）、`compute_progress`/`build_calibration_report` 邏輯、`dashboard.html`、DB schema。

---

### Task 1: `annotation_to_dict` 序列化函式

**Files:**
- Create: `src/annotation_serialization.py`
- Test: `tests/test_annotation_serialization.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_annotation_serialization.py`:

```python
"""src/annotation_serialization.annotation_to_dict 單元測試。"""
from __future__ import annotations

from datetime import UTC, datetime

from src.annotation_serialization import annotation_to_dict
from src.models import Annotation


def test_full_annotation_serializes_all_fields():
    ann = Annotation(
        id="ann1",
        audio_file_id="aud1",
        annotator_id="yyslin1024",
        valence=0.7,
        arousal=0.5,
        emotional_warmth=0.6,
        tension_direction=0.4,
        temporal_position=0.5,
        event_significance=0.3,
        world_immersion=0.55,
        tonal_noise_ratio=0.8,
        spectral_density=0.6,
        loop_capability='[1.0]',
        source_type='["bgm"]',
        function_roles='["atmosphere", "tension"]',
        genre_tag='["epic"]',
        worldview_tag="fantasy",
        style_tag='["orchestral"]',
        notes="some note",
        is_complete=True,
        created_at=datetime(2026, 5, 12, 3, 11, tzinfo=UTC),
        updated_at=datetime(2026, 5, 12, 3, 14, tzinfo=UTC),
    )
    d = annotation_to_dict(ann)
    assert d["annotation_id"] == "ann1"
    assert d["annotator_id"] == "yyslin1024"
    assert d["valence"] == 0.7
    assert d["world_immersion"] == 0.55
    assert d["loop_capability"] == [1.0]
    assert d["source_type"] == ["bgm"]
    assert d["function_roles"] == ["atmosphere", "tension"]
    assert d["genre_tag"] == ["epic"]
    assert d["style_tag"] == ["orchestral"]
    assert d["worldview_tag"] == "fantasy"
    assert d["notes"] == "some note"
    assert d["is_complete"] is True
    assert d["created_at"] == "2026-05-12T03:11:00+00:00"
    assert d["updated_at"] == "2026-05-12T03:14:00+00:00"


def test_bad_json_list_field_becomes_empty_list():
    ann = Annotation(
        audio_file_id="a", annotator_id="x",
        source_type="not-json{", function_roles=None, style_tag="",
    )
    d = annotation_to_dict(ann)
    assert d["source_type"] == []
    assert d["function_roles"] == []
    assert d["style_tag"] == []


def test_non_list_json_becomes_empty_list():
    ann = Annotation(
        audio_file_id="a", annotator_id="x",
        genre_tag='{"k": "v"}',  # valid JSON but not a list
    )
    assert annotation_to_dict(ann)["genre_tag"] == []


def test_none_timestamps_serialize_to_none():
    ann = Annotation(audio_file_id="a", annotator_id="x")
    ann.created_at = None
    ann.updated_at = None
    d = annotation_to_dict(ann)
    assert d["created_at"] is None
    assert d["updated_at"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_annotation_serialization.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.annotation_serialization'`

- [ ] **Step 3: Write minimal implementation**

Create `src/annotation_serialization.py`:

```python
"""單一 Annotation → dict 序列化（標註員詳細頁端點用）。

JSON 字串欄位（loop_capability / source_type / function_roles / genre_tag /
style_tag）decode 成 list；decode 失敗、非 list、或 None → []。
不含 audio metadata — 由呼叫端 join AudioFile 後合併。
"""
from __future__ import annotations

import json
from typing import Any

from src.models import Annotation


def _decode_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def annotation_to_dict(ann: Annotation) -> dict[str, Any]:
    """回傳單筆 annotation 的標註欄位（不含 audio metadata）。"""
    return {
        "annotation_id": ann.id,
        "annotator_id": ann.annotator_id,
        "valence": ann.valence,
        "arousal": ann.arousal,
        "emotional_warmth": ann.emotional_warmth,
        "tension_direction": ann.tension_direction,
        "temporal_position": ann.temporal_position,
        "event_significance": ann.event_significance,
        "world_immersion": ann.world_immersion,
        "tonal_noise_ratio": ann.tonal_noise_ratio,
        "spectral_density": ann.spectral_density,
        "loop_capability": _decode_list(ann.loop_capability),
        "source_type": _decode_list(ann.source_type),
        "function_roles": _decode_list(ann.function_roles),
        "genre_tag": _decode_list(ann.genre_tag),
        "worldview_tag": ann.worldview_tag,
        "style_tag": _decode_list(ann.style_tag),
        "notes": ann.notes,
        "is_complete": ann.is_complete,
        "created_at": ann.created_at.isoformat() if ann.created_at else None,
        "updated_at": ann.updated_at.isoformat() if ann.updated_at else None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_annotation_serialization.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/annotation_serialization.py tests/test_annotation_serialization.py
git commit -m "feat: add annotation_to_dict serializer for annotator detail"
```

---

### Task 2: `GET /api/stats/annotator/{id}/detail` 端點

**Files:**
- Modify: `src/routes/stats.py`
- Test: `tests/test_annotator_detail_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_annotator_detail_api.py`:

```python
"""GET /api/stats/annotator/{id}/detail 整合測試 + 頁面路由。

沿用 test_stats.py 的 fixture/override 慣例（conftest 的 in_memory_engine / client，
dependency_overrides[require_auth] 模擬登入者）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from src.models import Annotation, AudioFile


def _add_audio(engine, filename: str) -> str:
    with Session(engine) as s:
        a = AudioFile(filename=filename, game_name=filename.split("_")[0],
                       game_stage="Base Game", duration_sec=9.2)
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def _add_annotation(engine, audio_id: str, annotator_id: str, *,
                     is_complete: bool = True, valence: float | None = 0.5,
                     created_at: datetime, updated_at: datetime | None = None,
                     source_type: str = '["bgm"]') -> None:
    if updated_at is None:
        updated_at = created_at + timedelta(minutes=5)
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=annotator_id,
            is_complete=is_complete, valence=valence,
            source_type=source_type, function_roles='["atmosphere"]',
            style_tag="[]", created_at=created_at, updated_at=updated_at,
        ))
        s.commit()


def _override_user(annotator_id: str, *, is_admin: bool):
    from src import main as main_module
    from src.middleware import require_auth
    main_module.app.dependency_overrides[require_auth] = lambda: {
        "annotator_id": annotator_id, "email": None,
        "is_admin": is_admin, "name": None,
    }


NOW = datetime(2026, 5, 12, 3, 0, tzinfo=UTC)


def test_admin_sees_other_annotator(client, in_memory_engine):
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_audio(in_memory_engine, "G2_x.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024", created_at=NOW)
    _override_user("amber", is_admin=True)

    r = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["annotator_id"] == "yyslin1024"
    assert body["progress"]["completed_count"] == 1
    assert body["progress"]["total_audio_files"] == 2
    assert len(body["files"]) == 1
    assert body["files"][0]["filename"] == "G1_x.wav"
    assert body["files"][0]["source_type"] == ["bgm"]
    assert body["files"][0]["game_name"] == "G1"


def test_self_sees_self(client, in_memory_engine):
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024", created_at=NOW)
    _override_user("yyslin1024", is_admin=False)

    r = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC")
    assert r.status_code == 200, r.text
    assert r.json()["annotator_id"] == "yyslin1024"


def test_non_admin_non_self_forbidden(client, in_memory_engine):
    _add_audio(in_memory_engine, "G1_x.wav")
    _override_user("yyslin1024", is_admin=False)

    r = client.get("/api/stats/annotator/amber/detail?tz=UTC")
    assert r.status_code == 403


def test_amber_has_no_calibration(client, in_memory_engine):
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "amber", created_at=NOW)
    _override_user("amber", is_admin=True)

    r = client.get("/api/stats/annotator/amber/detail?tz=UTC")
    assert r.status_code == 200, r.text
    assert r.json()["calibration"] is None


def test_no_overlap_has_no_calibration(client, in_memory_engine):
    # yyslin1024 標 a1，amber 標 a2 → 無重疊 → calibration None
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    a2 = _add_audio(in_memory_engine, "G2_x.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024", created_at=NOW)
    _add_annotation(in_memory_engine, a2, "amber", created_at=NOW)
    _override_user("amber", is_admin=True)

    r = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC")
    assert r.status_code == 200, r.text
    assert r.json()["calibration"] is None


def test_overlap_produces_calibration_summary(client, in_memory_engine):
    # 同一 audio amber valence=0.5 / yyslin1024 valence=0.8 → mae 0.3
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "amber", valence=0.5, created_at=NOW)
    _add_annotation(in_memory_engine, a1, "yyslin1024", valence=0.8, created_at=NOW)
    _override_user("amber", is_admin=True)

    r = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC")
    assert r.status_code == 200, r.text
    cal = r.json()["calibration"]
    assert cal is not None
    assert cal["total_overlap"] == 1
    assert abs(cal["overall_mae"] - 0.3) < 1e-6
    assert cal["worst_dim"] == "valence"
    assert cal["report_url"] == "/calibration/report?annotator=yyslin1024"


def test_files_only_complete_and_sorted_desc(client, in_memory_engine):
    a1 = _add_audio(in_memory_engine, "G1_a.wav")
    a2 = _add_audio(in_memory_engine, "G2_b.wav")
    a3 = _add_audio(in_memory_engine, "G3_c.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024",
                    created_at=NOW, updated_at=NOW + timedelta(hours=1))
    _add_annotation(in_memory_engine, a2, "yyslin1024",
                    created_at=NOW, updated_at=NOW + timedelta(hours=3))
    # a3 未完成 → 不應出現
    _add_annotation(in_memory_engine, a3, "yyslin1024", is_complete=False,
                    created_at=NOW, updated_at=NOW + timedelta(hours=9))
    _override_user("amber", is_admin=True)

    files = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC").json()["files"]
    assert [f["filename"] for f in files] == ["G2_b.wav", "G1_a.wav"]


def test_detail_page_route_serves_html(client):
    r = client.get("/annotator/yyslin1024")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "annotator-detail.js" in r.text
```

> 注意：`test_detail_page_route_serves_html` 依賴 Task 3 的 `static/annotator-detail.html` 與 Task 4 的頁面路由。本 Task 先讓其餘 API 測試通過，該頁面測試會在 Task 4 完成後轉綠（執行 Task 2 Step 4 時它預期 FAIL，屬正常）。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_annotator_detail_api.py -v`
Expected: API 測試 FAIL（404，路由不存在）；`test_detail_page_route_serves_html` 也 FAIL（預期，Task 4 才補）

- [ ] **Step 3: Write minimal implementation**

In `src/routes/stats.py`, replace the import block (lines 10-19) so it reads exactly:

```python
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from src.annotation_serialization import annotation_to_dict
from src.annotators_loader import AnnotatorsConfigError, get_annotator
from src.calibration_feedback import build_calibration_report
from src.db import get_session
from src.middleware import require_auth
from src.models import Annotation, AudioFile
from src.stats import compute_icc_per_dimension, compute_overlap_audios, compute_progress
```

Then append this endpoint to the end of `src/routes/stats.py`:

```python
@router.get("/annotator/{annotator_id}/detail")
def annotator_detail(
    annotator_id: str,
    user: dict[str, Any] = Depends(require_auth),
    tz: Optional[str] = Query(default=None, description="IANA TZ，傳給 compute_progress"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """單一標註員明細：progress + vs Amber 校準摘要 + 全部 is_complete 標註。

    權限 admin-or-self（沿用 /progress 寫法）。amber 或無重疊 → calibration=None。
    """
    target = (annotator_id or "").strip()
    if target != user["annotator_id"] and not user.get("is_admin"):
        raise HTTPException(
            status_code=403, detail="僅 admin 或本人可檢視此標註員明細"
        )

    progress = compute_progress(session, target, tz_name=tz).to_dict()

    report = build_calibration_report(session, target)
    calibration: Optional[dict[str, Any]] = None
    if not report.get("is_reference") and report.get("total_overlap", 0) > 0:
        dims = report.get("dimensions", {})
        maes = [d["mae"] for d in dims.values() if d.get("mae") is not None]
        overall_mae = round(sum(maes) / len(maes), 3) if maes else None
        worst_dim: Optional[str] = None
        worst_mae: Optional[float] = None
        for dim_key, d in dims.items():
            mae = d.get("mae")
            if mae is None:
                continue
            if worst_mae is None or mae > worst_mae:
                worst_mae = mae
                worst_dim = dim_key
        calibration = {
            "total_overlap": report["total_overlap"],
            "reference_total": report.get("reference_total"),
            "overall_mae": overall_mae,
            "worst_dim": worst_dim,
            "worst_mae": worst_mae,
            "report_url": f"/calibration/report?annotator={target}",
        }

    rows = session.exec(
        select(Annotation, AudioFile)
        .join(AudioFile, Annotation.audio_file_id == AudioFile.id)
        .where(
            Annotation.annotator_id == target,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    files: list[dict[str, Any]] = []
    for ann, audio in rows:
        item = annotation_to_dict(ann)
        item.update(
            {
                "audio_id": audio.id,
                "filename": audio.filename,
                "game_name": audio.game_name,
                "game_stage": audio.game_stage,
                "duration_sec": audio.duration_sec,
            }
        )
        files.append(item)
    files.sort(key=lambda f: f["updated_at"] or "", reverse=True)

    try:
        spec = get_annotator(target)
    except AnnotatorsConfigError:
        spec = None
    annotator_name = (spec or {}).get("name") or target

    return {
        "annotator_id": target,
        "annotator_name": annotator_name,
        "progress": progress,
        "calibration": calibration,
        "files": files,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_annotator_detail_api.py -v`
Expected: 所有 API 測試 PASS；`test_detail_page_route_serves_html` 仍 FAIL（Task 4 才補，預期）

- [ ] **Step 5: Commit**

```bash
git add src/routes/stats.py tests/test_annotator_detail_api.py
git commit -m "feat: add GET /api/stats/annotator/{id}/detail endpoint"
```

---

### Task 3: 前端詳細頁（HTML + JS）

**Files:**
- Create: `static/annotator-detail.html`
- Create: `static/annotator-detail.js`

無自動化測試（專案前端無 JS test infra，spec 已決定不引入）；以手動驗證 step 取代。

- [ ] **Step 1: Create `static/annotator-detail.html`**

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>標註員明細 — 珀瀾標註工具</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 text-slate-900 dark:bg-slate-900 dark:text-slate-100">
  <main class="max-w-5xl mx-auto p-6" id="content">
    <header class="mb-6 flex items-baseline justify-between">
      <h1 id="title" class="text-2xl font-semibold">標註員明細</h1>
      <a href="/dashboard" class="text-sm text-slate-500 hover:text-amber-500">← Dashboard</a>
    </header>

    <section id="stats" class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
      <div class="text-sm text-slate-500 dark:text-slate-400 col-span-4">載入中…</div>
    </section>

    <section class="bg-white dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div class="p-3 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <span id="meta" class="text-xs text-slate-500 dark:text-slate-400">載入中…</span>
        <label class="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1">
          排序
          <select id="sort" class="bg-transparent border border-slate-300 dark:border-slate-600 rounded px-1 py-0.5">
            <option value="time">標註時間 ↓</option>
            <option value="filename">檔名 A→Z</option>
          </select>
        </label>
      </div>
      <table class="w-full text-sm">
        <thead class="bg-slate-100 dark:bg-slate-900/70 text-xs text-slate-600 dark:text-slate-400 uppercase tracking-wide">
          <tr>
            <th class="text-left p-3 font-medium">檔名</th>
            <th class="text-left p-3 font-medium">遊戲・段落</th>
            <th class="text-right p-3 font-medium w-44">標註時間</th>
          </tr>
        </thead>
        <tbody id="tbody"></tbody>
      </table>
    </section>
  </main>

  <script type="module" src="/static/auth.js"></script>
  <script type="module" src="/static/annotator-detail.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `static/annotator-detail.js`**

```javascript
// 標註員詳細頁 — fetch /api/stats/annotator/{id}/detail + /api/dimensions。
// admin 看任何人 / 本人看自己；權限由後端把關，403 顯示無權限。
// dimensions_config 是維度 label / amber_confirmed 的唯一來源（CLAUDE.md）。

const ANNOTATOR_ID = decodeURIComponent(window.location.pathname.split('/').pop())
const $ = id => document.getElementById(id)

const TZ = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
})()

const CONTINUOUS_ORDER = [
  'valence', 'arousal', 'emotional_warmth', 'tension_direction',
  'temporal_position', 'event_significance', 'world_immersion',
  'tonal_noise_ratio', 'spectral_density',
]

const state = {
  files: [],
  dims: {},
  sortKey: 'time',
  expandedId: null,
}

load()

async function load() {
  try {
    const [detailRes, dimsRes] = await Promise.all([
      fetch(`/api/stats/annotator/${encodeURIComponent(ANNOTATOR_ID)}/detail?tz=${encodeURIComponent(TZ)}`),
      fetch('/api/dimensions'),
    ])
    if (detailRes.status === 403) {
      $('content').innerHTML = '<div class="p-6 text-sm text-slate-600 dark:text-slate-400">無權限檢視此標註員。</div>'
      return
    }
    if (!detailRes.ok) throw new Error(`HTTP ${detailRes.status}`)
    const data = await detailRes.json()
    state.dims = dimsRes.ok ? await dimsRes.json() : {}
    state.files = data.files || []
    $('title').textContent = `標註員明細 — ${data.annotator_name || data.annotator_id}`
    renderStats(data)
    bindSort()
    renderTable()
  } catch (err) {
    $('content').innerHTML = `<div class="p-6 text-sm text-red-600">載入失敗：${escapeHtml(err.message)}</div>`
  }
}

function fmtDuration(sec) {
  if (sec == null) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function fmtTime(iso) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 16)
}

function statCard(label, value, sub) {
  return `
    <div class="p-3 rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
      <div class="text-xs text-slate-500 dark:text-slate-400 mb-1">${escapeHtml(label)}</div>
      <div class="text-2xl font-semibold font-mono">${escapeHtml(value)}</div>
      <div class="text-xs text-slate-500 dark:text-slate-400">${escapeHtml(sub)}</div>
    </div>`
}

function renderStats(data) {
  const p = data.progress || {}
  const total = p.total_audio_files || 0
  const done = p.completed_count || 0
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  let html = statCard('完成筆數', `${done} / ${total}`, `${pct}%`)
  html += statCard('平均單筆耗時', fmtDuration(p.avg_duration_sec), '排除 ≥2h')
  html += statCard(
    '連續標註天數',
    p.current_streak_days == null ? '—' : String(p.current_streak_days),
    '',
  )
  if (data.calibration) {
    const c = data.calibration
    const worst = c.worst_dim ? (state.dims[c.worst_dim]?.label_zh || c.worst_dim) : '—'
    html += `
      <div class="p-3 rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
        <div class="text-xs text-slate-500 dark:text-slate-400 mb-1">vs Amber 校準</div>
        <div class="text-2xl font-semibold font-mono">${c.overall_mae == null ? '—' : Number(c.overall_mae).toFixed(3)}</div>
        <div class="text-xs text-slate-500 dark:text-slate-400">
          overall MAE · 最差：${escapeHtml(worst)} · 重疊 ${c.total_overlap} 筆 ·
          <a href="${escapeAttr(c.report_url)}" target="_blank" class="text-amber-600 dark:text-amber-400 hover:underline">看完整報告 ↗</a>
        </div>
      </div>`
  } else {
    const msg = data.annotator_id === 'amber'
      ? '此為 reference 標註員，無校準比對'
      : '與 Amber 無重疊檔案，無法比對'
    html += `<div class="p-3 rounded border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-xs text-slate-500 dark:text-slate-400 flex items-center">${msg}</div>`
  }
  $('stats').innerHTML = html
}

function sortedFiles() {
  const fs = state.files.slice()
  if (state.sortKey === 'filename') {
    fs.sort((a, b) => a.filename.localeCompare(b.filename))
  } else {
    fs.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
  }
  return fs
}

function dimLabel(key) {
  return state.dims[key]?.label_zh || key
}

function dimWarn(key) {
  return state.dims[key] && state.dims[key].amber_confirmed === false ? ' ⚠️' : ''
}

function chips(arr) {
  if (!arr || !arr.length) return '<span class="text-slate-400">—</span>'
  return arr.map(v =>
    `<span class="inline-block px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-900 text-xs mr-1 mb-1">${escapeHtml(String(v))}</span>`,
  ).join('')
}

function renderDetail(f) {
  const dimRows = CONTINUOUS_ORDER.map(k => {
    const v = f[k]
    return `<div class="flex justify-between py-0.5">
      <span class="text-slate-600 dark:text-slate-400">${escapeHtml(dimLabel(k))}${dimWarn(k)}</span>
      <span class="font-mono">${v == null ? '—' : Number(v).toFixed(2)}</span>
    </div>`
  }).join('')
  const loop = (f.loop_capability || []).map(x => Number(x).toFixed(2)).join(', ') || '—'
  const dash = '<span class="text-slate-400">—</span>'
  return `
    <div class="bg-slate-50 dark:bg-slate-900/40 p-4 grid md:grid-cols-2 gap-4 text-sm">
      <div>
        <div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">維度</div>
        ${dimRows}
        <div class="flex justify-between py-0.5">
          <span class="text-slate-600 dark:text-slate-400">${escapeHtml(dimLabel('loop_capability'))}${dimWarn('loop_capability')}</span>
          <span class="font-mono">${escapeHtml(loop)}</span>
        </div>
      </div>
      <div class="space-y-2">
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">音源類型</div>${chips(f.source_type)}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">功能角色</div>${chips(f.function_roles)}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">Genre</div>${chips(f.genre_tag)}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">Style</div>${chips(f.style_tag)}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">世界觀</div>${f.worldview_tag ? escapeHtml(f.worldview_tag) : dash}</div>
        <div><div class="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">備註</div>${f.notes ? escapeHtml(f.notes) : dash}</div>
      </div>
    </div>`
}

function renderTable() {
  const fs = sortedFiles()
  $('meta').textContent = `共 ${fs.length} 筆完成標註`
  if (!fs.length) {
    $('tbody').innerHTML = '<tr><td colspan="3" class="p-3 text-sm text-slate-500 dark:text-slate-400">尚無完成的標註</td></tr>'
    return
  }
  $('tbody').innerHTML = fs.map(f => {
    const expanded = state.expandedId === f.annotation_id
    const detail = expanded
      ? `<tr><td colspan="3" class="p-0">${renderDetail(f)}</td></tr>`
      : ''
    return `
      <tr class="border-t border-slate-200 dark:border-slate-700/60 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700/30"
          data-row="${escapeAttr(f.annotation_id)}">
        <td class="p-3"><div class="font-medium">${escapeHtml(f.filename)}</div></td>
        <td class="p-3 text-slate-600 dark:text-slate-400">${escapeHtml(f.game_name)} · ${escapeHtml(f.game_stage)}</td>
        <td class="p-3 text-right font-mono text-xs text-slate-500 dark:text-slate-400">${escapeHtml(fmtTime(f.updated_at))} ${expanded ? '▲' : '▼'}</td>
      </tr>
      ${detail}`
  }).join('')
  $('tbody').querySelectorAll('[data-row]').forEach(tr => {
    tr.addEventListener('click', () => {
      const id = tr.dataset.row
      state.expandedId = state.expandedId === id ? null : id
      renderTable()
    })
  })
}

function bindSort() {
  const sel = $('sort')
  if (!sel) return
  sel.addEventListener('change', e => {
    state.sortKey = e.target.value
    renderTable()
  })
}

// ─── helpers ──────────────────────────
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c])
}
function escapeAttr(s) { return escapeHtml(s).replace(/\n/g, '&#10;') }
```

- [ ] **Step 3: Commit**

```bash
git add static/annotator-detail.html static/annotator-detail.js
git commit -m "feat: annotator detail page (html + js)"
```

---

### Task 4: 頁面路由 `GET /annotator/{annotator_id}`

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_annotator_detail_api.py`（`test_detail_page_route_serves_html`，已於 Task 2 寫好）

- [ ] **Step 1: Confirm the failing test**

Run: `pytest tests/test_annotator_detail_api.py::test_detail_page_route_serves_html -v`
Expected: FAIL — 404（路由尚未加）

- [ ] **Step 2: Write minimal implementation**

In `src/main.py`, add this route immediately after the `dashboard_page` function (after its `return FileResponse(STATIC_DIR / "dashboard.html")` line, before `@app.get("/upload"...)`):

```python
@app.get("/annotator/{annotator_id}", include_in_schema=False)
def annotator_detail_page(annotator_id: str) -> FileResponse:  # noqa: ARG001 — JS 從 path 取
    """標註員詳細頁；權限由 /api/stats/annotator/{id}/detail 把關（admin-or-self）。

    比照 /calibration/report：頁面本身純 serve，真正 gate 在 API。
    """
    return FileResponse(STATIC_DIR / "annotator-detail.html")
```

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_annotator_detail_api.py::test_detail_page_route_serves_html -v`
Expected: PASS

- [ ] **Step 4: Run the full new-file test module**

Run: `pytest tests/test_annotator_detail_api.py tests/test_annotation_serialization.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/main.py
git commit -m "feat: add /annotator/{id} page route"
```

---

### Task 5: Dashboard 進度列名字依權限變連結

**Files:**
- Modify: `static/dashboard.js`

無 JS test infra；以手動驗證取代。改動限 `loadProgressForAll()` 與新增一個 `getMe()`，不動 `showAdminLinks()`（外科原則，避免改既有可運作邏輯）。

- [ ] **Step 1: Add `getMe()` helper**

In `static/dashboard.js`, immediately after the `includeFixtureBox.addEventListener('change', loadAll)` line and before `loadAll()`, add:

```javascript
// /api/me 快取一次（dashboard 進度列依此決定名字是否可點進詳細頁）
let _mePromise = null
function getMe() {
  if (!_mePromise) {
    _mePromise = fetch('/api/me')
      .then(r => (r.ok ? r.json() : null))
      .catch(() => null)
  }
  return _mePromise
}
```

- [ ] **Step 2: Make annotator names clickable for admin / self**

In `static/dashboard.js`, in `loadProgressForAll(annotators)`, change the function signature line from:

```javascript
async function loadProgressForAll(annotators) {
```

to:

```javascript
async function loadProgressForAll(annotators) {
  const me = await getMe()
```

Then replace this exact block:

```javascript
  list.innerHTML = annotators.map(a => `
    <div class="flex items-center gap-3" data-annotator="${escapeAttr(a)}">
      <div class="w-32 text-sm font-medium truncate">${escapeHtml(a)}</div>
      <div class="flex-1 h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
        <div class="h-full bg-amber-500" data-bar style="width: 0%"></div>
      </div>
      <div class="w-28 text-right text-sm text-slate-500 dark:text-slate-400 font-mono" data-text>—</div>
    </div>
  `).join('')
```

with:

```javascript
  list.innerHTML = annotators.map(a => {
    const clickable = me && (me.is_admin || a === me.annotator_id)
    const nameCell = clickable
      ? `<a href="/annotator/${encodeURIComponent(a)}" class="hover:underline hover:text-amber-600 dark:hover:text-amber-400">${escapeHtml(a)}</a>`
      : escapeHtml(a)
    return `
    <div class="flex items-center gap-3" data-annotator="${escapeAttr(a)}">
      <div class="w-32 text-sm font-medium truncate">${nameCell}</div>
      <div class="flex-1 h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
        <div class="h-full bg-amber-500" data-bar style="width: 0%"></div>
      </div>
      <div class="w-28 text-right text-sm text-slate-500 dark:text-slate-400 font-mono" data-text>—</div>
    </div>
  `
  }).join('')
```

(The early-return guard `if (!annotators.length) { ... return }` above this block stays unchanged.)

- [ ] **Step 3: Commit**

```bash
git add static/dashboard.js
git commit -m "feat: link annotator names to detail page on dashboard"
```

---

### Task 6: 全測試 + lint + 手動驗證 + 收尾

**Files:** 無新增；驗證既有改動。

- [ ] **Step 1: Run full test suite**

Run: `pytest -q`
Expected: 全綠（既有 236 + 本次新增；新增約 12 個 test 全 PASS，0 failed）。若有 fail，回對應 Task 修正後重跑。

- [ ] **Step 2: Lint**

Run: `ruff check src/ tests/`
Expected: `All checks passed!`（如有可自動修，跑 `ruff check --fix src/ tests/` 後重跑並 commit）

- [ ] **Step 3: Manual smoke (dev 模式)**

Run（背景啟動）：`uvicorn src.main:app --port 8000`
然後在瀏覽器：

1. 開 `http://localhost:8000/dashboard` → 「各標註員進度」每個名字是連結（dev 模式 require_auth 預設 amber/admin，全部可點）。
2. 點任一名字 → 進 `/annotator/{name}`：上方 4 張統計卡（完成筆數 / 平均單筆耗時 / 連續天數 / 校準摘要或「無校準比對」）。
3. 表格點任一列 → 就地展開 10 維 + 音源類型/功能角色/genre/style/世界觀/備註；再點收合；展開另一列時前一列自動收合。
4. 切換「排序」下拉 → 列順序依檔名 / 標註時間改變。
5. 開 `http://localhost:8000/annotator/amber` → 校準卡顯示「此為 reference 標註員，無校準比對」。

驗證後停掉 uvicorn。

- [ ] **Step 4: Verify branch state**

Run: `git status --porcelain` → expect empty (all committed)
Run: `git log --oneline origin/master..HEAD` → expect the spec commit + 5 feat commits, in order.

- [ ] **Step 5: Finish**

實作完成。依 superpowers:finishing-a-development-branch 決定 merge / PR / cleanup（交回使用者選擇，勿自行 push 或 merge）。

---

## Self-Review

**1. Spec coverage:**

| Spec 要求 | 對應 Task |
|---|---|
| API `GET /api/stats/annotator/{id}/detail`，admin-or-self gate | Task 2（+ 403 測試）|
| progress 重用 compute_progress（含 avg_duration_sec = 平均單筆耗時）| Task 2 |
| calibration：amber / 無重疊 → null；否則 overall_mae + worst_dim + report_url | Task 2（+ 3 個 calibration 測試）|
| files 只 is_complete、後端預設 updated_at desc、含 audio meta + decoded list | Task 1 + Task 2（+ sort/filter 測試）|
| `src/annotation_serialization.py` 新檔、不重構既有 3 處 | Task 1 |
| 頁面路由 `/annotator/{id}` 純 FileResponse、非 /admin/ | Task 4（+ route 測試）|
| 前端 html/js：統計卡 + 可排序表 + inline 展開 + ⚠️ + 403 訊息 | Task 3 |
| dashboard 名字依 admin/self 變連結 | Task 5 |
| 前端無測試框架（不引入）| Task 3/5 以手動驗證取代 |
| commit 用 `feat:` | 全 Task commit |
| 未知 annotator 不 404（compute_progress has_data:false）| Task 2（沿用 compute_progress 行為，不額外 404）|

無遺漏。

**2. Placeholder scan:** 無 TBD/TODO；每個 code step 均含完整可貼上的程式碼與確切指令/預期輸出。

**3. Type consistency:**
- `annotation_to_dict` 產出鍵（`annotation_id`/`loop_capability`/…）= Task 2 端點 `item.update(...)` 合併 audio 鍵後 = Task 3 前端讀取鍵（`f.annotation_id`/`f.filename`/`f.game_name`/`f.updated_at`/`f.loop_capability`/`f.source_type`/`f.function_roles`/`f.genre_tag`/`f.style_tag`/`f.worldview_tag`/`f.notes`）。一致。
- `calibration` 鍵（`overall_mae`/`worst_dim`/`total_overlap`/`report_url`）= 前端 `c.overall_mae`/`c.worst_dim`/`c.total_overlap`/`c.report_url`。一致。
- `progress` 鍵 = `ProgressStats.to_dict()`（`completed_count`/`total_audio_files`/`avg_duration_sec`/`current_streak_days`）= 前端讀取鍵。一致。
- `require_auth` 回傳 `{annotator_id,email,is_admin,name}`，端點用 `user["annotator_id"]` / `user.get("is_admin")`，與 `_override_user` 測試 stub 一致。
