# BGM 對齊 P1（後端地基）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 給 BGM 對齊後端補上 `level_id`（關卡）維度與比對②的收斂專屬端點，作為 P2 輸入校準格 / P3 四組比對頁的地基。

**Architecture:** alignment.db（純 SQLAlchemy，與 annotations.db 實體隔離）的兩張表各加一個 `level_id` 欄；所有 reading 定位與比對都多一個 `(session_id, level_id)` 範圍鍵；新增 `/compare/convergence` 端點，繞過「一次只變一軸」守門做 ref-target ↔ deliverable-perceived 的逐版差距。

**Tech Stack:** Python 3.11 / FastAPI / 純 SQLAlchemy（alignment 側）/ Pydantic / pytest。

## Global Constraints

- 不動 annotations.db / 既有三角校準 / ICC / export（CLAUDE.md 隔離原則）。
- 不引新框架；alignment 側維持純 SQLAlchemy（**不要**改成 SQLModel）。
- 不 auto-install 套件。
- Python 4-space indent；public function 加 type hints；exception 抓具體型別。
- 使用者可見文字繁中、code identifier 英文。
- SQLite ALTER 只能 ADD COLUMN（不能改既有 column）；migration 必須 idempotent。
- 所有 alignment 端點仍掛 `Depends(resolve_alignment_access)`，client 鎖定邏輯不可破。
- 權威 spec：`docs/superpowers/specs/2026-06-21-bgm-alignment-multiref-compare-design.md`。

---

### Task 1: `level_id` 欄位 + idempotent migration

**Files:**
- Modify: `src/alignment_db.py`（`AlignmentReading`、`AlignmentSpec` 加欄；新增 `apply_alignment_migrations`）
- Modify: `src/main.py:78`（`create_alignment_db()` 之後呼叫 migration）
- Test: `tests/test_alignment_db.py`

**Interfaces:**
- Produces:
  - `AlignmentReading.level_id: str`、`AlignmentSpec.level_id: str`（NOT NULL，default `""`）
  - `apply_alignment_migrations(eng: Engine = engine) -> list[str]`（回傳已套用的 `"table.column"` 清單；idempotent）

- [ ] **Step 1: 寫 failing test**

加到 `tests/test_alignment_db.py`：

```python
from sqlalchemy import text
from src.alignment_db import (
    AlignmentBase,
    make_alignment_engine,
    apply_alignment_migrations,
)


def _column_names(eng, table):
    with eng.begin() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").all()
    return {r[1] for r in rows}


def test_level_id_column_added_and_idempotent(tmp_path):
    db = tmp_path / "alignment.db"
    eng = make_alignment_engine(db)
    # 先建一個「沒有 level_id」的舊版表，模擬既有 alignment.db
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alignment_reading (id INTEGER PRIMARY KEY, session_id VARCHAR)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE alignment_spec (id INTEGER PRIMARY KEY, session_id VARCHAR)"
        )
    applied = apply_alignment_migrations(eng)
    assert "alignment_reading.level_id" in applied
    assert "alignment_spec.level_id" in applied
    assert "level_id" in _column_names(eng, "alignment_reading")
    assert "level_id" in _column_names(eng, "alignment_spec")
    # 再跑一次不應重複套用
    assert apply_alignment_migrations(eng) == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd /Users/aaron/Desktop/github/polan-annotator && python -m pytest tests/test_alignment_db.py::test_level_id_column_added_and_idempotent -v`
Expected: FAIL（`ImportError: cannot import name 'apply_alignment_migrations'`）

- [ ] **Step 3: 實作**

在 `src/alignment_db.py` 的 `AlignmentReading` 加（接在 `reading_type` 之後、`note` 之前皆可，欄序不影響）：

```python
    level_id: Mapped[str] = mapped_column(String, index=True, server_default="", default="")
```

在 `AlignmentSpec` 同樣加：

```python
    level_id: Mapped[str] = mapped_column(String, index=True, server_default="", default="")
```

在 `create_alignment_db` 之後新增：

```python
def _column_exists(conn, table: str, column: str) -> bool:
    """SQLite PRAGMA table_info 查 column 是否存在（純 SQLAlchemy connection）。"""
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").all()
    return any(r[1] == column for r in rows)


def apply_alignment_migrations(eng: Engine = engine) -> list[str]:
    """idempotent ALTER：補 level_id 欄到既有 alignment.db。

    與 src/migrations.py（SQLModel session.exec）分離 —— alignment 側是純
    SQLAlchemy，用 exec_driver_sql。SQLite ADD COLUMN 是 O(1) metadata op。
    """
    pending = [
        ("alignment_reading", "level_id", "VARCHAR NOT NULL DEFAULT ''"),
        ("alignment_spec", "level_id", "VARCHAR NOT NULL DEFAULT ''"),
    ]
    applied: list[str] = []
    with eng.begin() as conn:
        for table, column, col_def in pending:
            if not _column_exists(conn, table, column):
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                applied.append(f"{table}.{column}")
    return applied
```

在 `src/main.py` line 78 `create_alignment_db()` 之後加一行：

```python
    apply_alignment_migrations()  # 補 level_id 欄到既有 alignment.db（idempotent）
```

並在 main.py 既有的 `from src.alignment_db import create_alignment_db` 改成同時 import `apply_alignment_migrations`。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_alignment_db.py -v`
Expected: PASS（含既有 test_alignment_db 全綠）

- [ ] **Step 5: commit**

```bash
git add src/alignment_db.py src/main.py tests/test_alignment_db.py
git commit -m "[Phase 6] alignment: 加 level_id 欄 + idempotent migration"
```

---

### Task 2: `level_id` 進比對引擎 dataclasses + `differing_axes`

**Files:**
- Modify: `src/alignment_compare.py`（`Reading`、`SetIdentity` 加 `level_id`；`_identity_of`；`differing_axes`）
- Test: `tests/test_alignment_compare.py`

**Interfaces:**
- Consumes: Task 1 無直接依賴（純函數層）。
- Produces:
  - `Reading.level_id: str`、`SetIdentity.level_id: str`
  - `differing_axes` 在 `a.level_id != b.level_id` 時於回傳清單含 `"level"`。

- [ ] **Step 1: 寫 failing test**

加到 `tests/test_alignment_compare.py`：

```python
from src.alignment_compare import Reading, group_into_sets, differing_axes


def _r(level_id, audio_id, dim, val):
    return Reading(
        session_id="s1", annotator_id="amber", annotator_role="client",
        audio_id=audio_id, audio_role="ref", version=0,
        dimension=dim, value=val, reading_type="perceived", level_id=level_id,
    )


def test_differing_axes_flags_level_mismatch():
    a = group_into_sets([_r("L1", "refA", "valence", 0.9)])[0]
    b = group_into_sets([_r("L2", "refA", "valence", 0.9)])[0]
    assert "level" in differing_axes(a.identity, b.identity)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_alignment_compare.py::test_differing_axes_flags_level_mismatch -v`
Expected: FAIL（`Reading.__init__() got an unexpected keyword argument 'level_id'`）

- [ ] **Step 3: 實作**

`src/alignment_compare.py`：`Reading` dataclass 加欄（放在 `reading_type` 之後）：

```python
    level_id: str = ""
```

`SetIdentity` 加欄（放在 `session_id` 之後，保持與 reading 對應）：

```python
    level_id: str = ""
```

`_identity_of` 多帶 `level_id=r.level_id,`。

`differing_axes` 在 `session` 檢查之後加：

```python
    if a.level_id != b.level_id:
        axes.append("level")
```

> 註：frozen dataclass 加有預設值的欄位不破壞既有不帶 level_id 的呼叫（預設 `""`）。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_alignment_compare.py -v`
Expected: PASS（既有 compare 測試不帶 level_id 仍綠，因有預設值）

- [ ] **Step 5: commit**

```bash
git add src/alignment_compare.py tests/test_alignment_compare.py
git commit -m "[Phase 6] alignment: level_id 進 compare 引擎 + differing_axes"
```

---

### Task 3: `level_id` 進 API schema + 查詢 + 寫入

**Files:**
- Modify: `src/routes/alignment.py`（`Identity`、`VarianceRequest` 加欄；`_load_set` where 加條件；`_row_to_reading`；`save_readings`/`save_spec` 寫入；GET `readings`/`spec` 接收+篩選）
- Test: `tests/test_alignment_api.py`

**Interfaces:**
- Consumes: Task 1（`AlignmentReading.level_id`）、Task 2（`Reading.level_id`）。
- Produces:
  - `Identity.level_id: str = ""`、`VarianceRequest.level_id: str = ""`
  - `GET /api/alignment/readings?session_id=&level_id=` 以 `(session_id, level_id)` 篩（`level_id` 給空字串時不額外篩，向後相容）
  - `POST /api/alignment/readings`、`/spec` 寫入 `level_id`

- [ ] **Step 1: 寫 failing test**

加到 `tests/test_alignment_api.py`（該檔的 fixture 叫 **`align_client`**，自帶 in-memory alignment 庫並 `AlignmentBase.metadata.create_all` —— 因 Task 1 已把 level_id 加進 model，此 fixture 建表時就會含 level_id 欄）：

```python
def test_readings_scoped_by_level(align_client):
    base = dict(session_id="s1", annotator_id="amber", annotator_role="engineer",
                audio_role="ref", version=0, reading_type="perceived")
    # 同 session、不同 level 各存一筆
    align_client.post("/api/alignment/readings", json={
        **base, "level_id": "L1", "audio_id": "refA", "values": {"valence": 0.9}})
    align_client.post("/api/alignment/readings", json={
        **base, "level_id": "L2", "audio_id": "refB", "values": {"valence": 0.2}})
    r = align_client.get("/api/alignment/readings", params={"session_id": "s1", "level_id": "L1"})
    sets = r.json()["sets"]
    assert len(sets) == 1
    assert sets[0]["audio_id"] == "refA"
    assert sets[0]["level_id"] == "L1"
```

> 若既有 `list_readings` 回傳的 set dict 尚未含 `level_id`，本測試的 `sets[0]["level_id"]` 會逼你在 Step 3 補上。

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_alignment_api.py::test_readings_scoped_by_level -v`
Expected: FAIL（回傳 2 筆，或 KeyError `level_id`）

- [ ] **Step 3: 實作**

`src/routes/alignment.py`：

`Identity` 加欄（放在 `version` 之後）：

```python
    level_id: str = ""
```

`VarianceRequest` 加欄（放在 `session_id` 之後）：

```python
    level_id: str = ""
```

`_row_to_reading` 多帶 `level_id=row.level_id,`。

`_load_set` 的 `select(...).where(...)` 加一條件：

```python
            AlignmentReading.level_id == idt.level_id,
```

`save_readings`：在迴圈 `db.add(AlignmentReading(...))` 內加 `level_id=payload.level_id,`；刪舊 row 的 `select(...).where(...)` 也加 `AlignmentReading.level_id == payload.level_id,`。

`list_readings`：簽名加 `level_id: str = Query(default="")`；query 改為：

```python
    stmt = select(AlignmentReading).where(AlignmentReading.session_id == sid)
    if level_id:
        stmt = stmt.where(AlignmentReading.level_id == level_id)
    rows = db.scalars(stmt).all()
```

回傳每個 set dict 加 `"level_id": s.identity.level_id,`。

`save_spec`：`db.add(AlignmentSpec(...))` 加 `level_id=payload.level_id,`；刪舊 row where 加 `AlignmentSpec.level_id == payload.level_id,`。`SpecPayload` 加 `level_id: str = ""`。

`list_specs`：簽名加 `level_id: str = Query(default="")`；同 list_readings 的條件式篩法；回傳 spec dict 加 `"level_id": r.level_id,`。

`compare_variance_endpoint`：建 `Identity(...)` 時多帶 `level_id=req.level_id,`。

> ⚠️ `compare_pair_endpoint`：`PairRequest.a/b` 為 `Identity`，已自動帶 `level_id`，`_load_set` 已加條件 —— 不用再改。

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_alignment_api.py tests/test_alignment_compare.py -v`
Expected: PASS（既有 alignment API 測試若沒帶 level_id，因預設 `""` 仍應綠；若有少數因 set dict 多了 level_id key 而 assert 整個 dict 相等的測試壞掉，更新該 assert 加 level_id）

- [ ] **Step 5: commit**

```bash
git add src/routes/alignment.py tests/test_alignment_api.py
git commit -m "[Phase 6] alignment: level_id 進 API schema/查詢/寫入"
```

---

### Task 4: `/compare/convergence` 收斂端點（比對②）

**Files:**
- Modify: `src/routes/alignment.py`（新增 `ConvergenceRequest` + endpoint）
- Test: `tests/test_alignment_api.py`（用該檔的 `align_client` fixture —— 此 fixture 非共用 conftest，integration 測試必須放這檔）

**Interfaces:**
- Consumes: Task 3（`_load_set`、`Identity`、`level_id`）、`compare_pair`（`alignment_compare`）。
- Produces:
  - `POST /api/alignment/compare/convergence`
  - body: `{session_id, level_id, annotator_id, annotator_role, goal_audio_id, deliverable_audio_id, versions: list[int]}`
  - return: `{"goal": {dim: float}, "versions": [{"version": int, "values": {dim: float}, "diffs": {dim: float}}]}`

- [ ] **Step 1: 寫 failing test**

```python
def test_convergence_diffs_against_goal(align_client):
    # goal = 主 ref 的 target
    align_client.post("/api/alignment/readings", json={
        "session_id": "s1", "level_id": "L1", "annotator_id": "amber",
        "annotator_role": "client", "audio_id": "refA", "audio_role": "ref",
        "version": 0, "reading_type": "target", "values": {"valence": 0.90}})
    # v1 / v2 = 新曲 deliverable perceived
    align_client.post("/api/alignment/readings", json={
        "session_id": "s1", "level_id": "L1", "annotator_id": "amber",
        "annotator_role": "client", "audio_id": "song", "audio_role": "deliverable",
        "version": 1, "reading_type": "perceived", "values": {"valence": 0.80}})
    align_client.post("/api/alignment/readings", json={
        "session_id": "s1", "level_id": "L1", "annotator_id": "amber",
        "annotator_role": "client", "audio_id": "song", "audio_role": "deliverable",
        "version": 2, "reading_type": "perceived", "values": {"valence": 0.88}})
    r = align_client.post("/api/alignment/compare/convergence", json={
        "session_id": "s1", "level_id": "L1", "annotator_id": "amber",
        "annotator_role": "client", "goal_audio_id": "refA",
        "deliverable_audio_id": "song", "versions": [1, 2]})
    body = r.json()
    assert body["goal"]["valence"] == 0.90
    v1 = next(v for v in body["versions"] if v["version"] == 1)
    v2 = next(v for v in body["versions"] if v["version"] == 2)
    assert abs(v1["diffs"]["valence"] - 0.10) < 1e-9
    assert abs(v2["diffs"]["valence"] - 0.02) < 1e-9
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `python -m pytest tests/test_alignment_api.py::test_convergence_diffs_against_goal -v`
Expected: FAIL（404，端點不存在）

- [ ] **Step 3: 實作**

`src/routes/alignment.py` 加 schema（接其他 schema 後）：

```python
class ConvergenceRequest(BaseModel):
    session_id: str
    level_id: str = ""
    annotator_id: str
    annotator_role: str
    goal_audio_id: str          # 主 ref（取其 reading_type=target）
    deliverable_audio_id: str   # 新曲
    versions: list[int]
```

加端點（放在 `compare_variance_endpoint` 之後）：

```python
@router.post("/compare/convergence")
def compare_convergence_endpoint(
    req: ConvergenceRequest,
    access: AlignmentAccess = Depends(resolve_alignment_access),
    db: Session = Depends(get_alignment_session),
) -> dict:
    """比對②：主 ref 的 target 當目標，逐版算新曲 deliverable perceived 的差距。

    刻意繞過『一次只變一軸』守門 —— goal↔deliverable 本就是跨軸的目標比對。
    """
    if access.session_id is not None:
        req.session_id = access.session_id
    goal_idt = Identity(
        session_id=req.session_id, level_id=req.level_id,
        annotator_id=req.annotator_id, annotator_role=req.annotator_role,
        audio_id=req.goal_audio_id, audio_role="ref", version=0,
        reading_type="target",
    )
    goal = _load_set(db, goal_idt)
    if goal is None:
        raise HTTPException(404, f"查無主 ref 的 target：{req.goal_audio_id}")
    out_versions: list[dict] = []
    for v in req.versions:
        ver_idt = Identity(
            session_id=req.session_id, level_id=req.level_id,
            annotator_id=req.annotator_id, annotator_role=req.annotator_role,
            audio_id=req.deliverable_audio_id, audio_role="deliverable", version=v,
            reading_type="perceived",
        )
        ver = _load_set(db, ver_idt)
        if ver is None:
            continue
        out_versions.append({
            "version": v,
            "values": ver.values,
            "diffs": compare_pair(goal, ver),  # per-dim abs diff，不套守門
        })
    return {"goal": goal.values, "versions": out_versions}
```

確認檔頭 import 已含 `compare_pair`（既有 import 區已 import `pair_comparison`，需補 `compare_pair`）：

```python
from src.alignment_compare import (
    BGM_DIMENSIONS,
    PairResult,
    Reading,
    ReadingSet,
    compare_pair,       # 新增
    compute_variance,
    group_into_sets,
    pair_comparison,
)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `python -m pytest tests/test_alignment_api.py::test_convergence_diffs_against_goal -v`
Expected: PASS

- [ ] **Step 5: 全套 alignment 測試 + commit**

```bash
python -m pytest tests/ -k alignment -v
git add src/routes/alignment.py tests/
git commit -m "[Phase 6] alignment: /compare/convergence 收斂端點（比對②）"
```

---

## P1 完成定義（Definition of Done）
1. `python -m pytest tests/ -k alignment -v` 全綠。
2. 啟動 server（`uvicorn src.main:app` 或既有啟動方式）對既有 `data/alignment.db` 不報錯、`level_id` 欄已補。
3. annotations.db 未被觸碰。
4. 端點 `/compare/convergence` 可用；`/readings`、`/spec`、`/compare/variance` 都能依 `(session_id, level_id)` 範圍運作。

## Self-Review 結果
- **Spec 覆蓋**：spec §3.1/3.2（level_id+migration）→T1；§4.2（compare dataclass）→T2；§4.1（端點串 level_id）→T3；§4.3（convergence）→T4。§4.4 角色規則屬前端（P2）、§4.5 GET /level 標可選暫不做 —— 皆非 P1 後端硬需求，未漏。
- **Placeholder 掃描**：無 TBD/TODO；每個 code step 有完整程式。
- **型別一致**：`level_id: str` 全程一致；`apply_alignment_migrations` / `_column_exists` / `ConvergenceRequest` 欄位在 T1/T4 定義並於後續沿用一致。
