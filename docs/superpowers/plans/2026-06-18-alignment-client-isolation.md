# Alignment 客戶隔離 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 alignment 標註頁能安全丟給外部客戶，客戶看不到內部工具、碰不到版權非我方的內部音檔。

**Architecture:** 對 `/alignment` + `/api/alignment/*` 開 Cloudflare Access Bypass，改用 app 層 token（存 hash）把關；token 綁定 role/session/audio，client link 鎖死 context、engineer link 自由。新增獨立音檔倉 `data/alignment_audio/` + `AlignmentAudio` 表 + 專屬 streaming 端點，客戶實體碰不到 `data/audio/`。內部路徑一律不 bypass，維持 CF Access OTP 保護。

**Tech Stack:** FastAPI、純 SQLAlchemy（alignment.db，**不用 SQLModel**）、Pydantic（API schema）、stdlib（`secrets`/`hashlib`/`shutil`/`uuid`）、vanilla JS + Tailwind CDN。**不新增任何套件。**

## Global Constraints

- Python 3.11+、4-space indent、所有 public function 加 type hints（`src/CLAUDE.md`）。
- alignment.db 的 model 用**純 SQLAlchemy declarative**（`AlignmentBase`），**絕不用 SQLModel** —— 否則破壞 annotations.db / alignment.db 的雙向檔案隔離（`src/alignment_db.py:7-13`）。
- API schema 用 Pydantic；DB 寫入經 alignment session。
- JS：2-space、**不加分號**、`const` 為主、`fetch`、vanilla、Tailwind CDN（`CLAUDE.md`）。
- 所有使用者可見文字用**繁體中文 sentence case**；code identifier 用英文；error 訊息要具體。
- **不新增 Python 套件**（token 用 stdlib `secrets`/`hashlib`）。
- Commit 格式比照本 branch 既有慣例：`feat:` / `test:` / `docs:` / `chore:`（非 `[Phase N]`）。
- 不碰內部任何既有路由的 auth；不改 `data/annotations.db` 既有資料。
- timezone-aware UTC：用 `src/alignment_db.py` 的 `_utcnow()` 慣例。

## 動到的檔案

| 檔案 | 責任 |
|---|---|
| `src/alignment_db.py`（改） | 新增 `AlignmentAudio`、`ClientLink` 兩個 model |
| `src/client_auth.py`（新） | token 產生/雜湊工具 + `AlignmentAccess` + `resolve_alignment_access` 依賴 |
| `src/alignment_publish.py`（新） | `publish_audio_link()` — 複製音檔進倉 + 建 AlignmentAudio + ClientLink |
| `src/routes/alignment.py`（改） | 全端點掛 gate、強制注入 client context、新增 `/context` 與 `/audio/{id}/stream` |
| `src/main.py`（改） | `/alignment` 頁面路由掛 gate（驗 token + 種 cookie） |
| `src/routes/admin.py`（改） | `POST /api/admin/alignment/publish`、`GET .../links`、`POST .../links/{id}/revoke` |
| `static/alignment.js`（改） | 改打 `/context` 取 context、player 指向新 stream 端點 |
| `static/dashboard.html` + `dashboard.js`（改） | admin-only「發佈客戶連結」widget：發佈表單 + 連結列表 + 撤銷 |
| `docs/ops/cloudflare-alignment-bypass.md`（新） | CF Access Bypass policy 確切設定 |

## 設計備註（plan 階段定案）

- **dev 模式放行**：`cloudflare_access_enabled` 與 `oauth_enabled` 皆 false 時（本機 / 測試），`resolve_alignment_access` 直接回一個信任的 engineer access（沿用 `src/middleware.py:_dev_mode_user` 哲學）。好處：本機開發不必帶 token、且既有 alignment 測試完全不用改。Production（CF 開）才強制 token。
- **`ClientLink.role`**：`"client"` 鎖死 `session_id`/`annotator_id`/`alignment_audio_id`；`"engineer"` 三者為 None，沿用 query string（工程師為信任內部）。
- **音檔 id 語意改變**：alignment 前端與端點改用 `AlignmentAudio.id`（指向 `data/alignment_audio/`），不再用 annotations.db 的 `AudioFile.id`。

---

### Task 1: alignment.db 新增 AlignmentAudio + ClientLink model

**Files:**
- Modify: `src/alignment_db.py`（在 `AlignmentSpec` 之後、`make_alignment_engine` 之前插入）
- Test: `tests/test_alignment_client_link_db.py`（新）

**Interfaces:**
- Produces:
  - `class AlignmentAudio(AlignmentBase)` 欄位：`id: str`(pk), `filename: str`, `orig_audio_id: Optional[str]`, `created_at: datetime`
  - `class ClientLink(AlignmentBase)` 欄位：`id: str`(pk), `token_hash: str`(unique index), `role: str`, `label: str`, `annotator_id: Optional[str]`, `session_id: Optional[str]`, `alignment_audio_id: Optional[str]`, `created_at: datetime`, `expires_at: Optional[datetime]`, `revoked: bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alignment_client_link_db.py
"""AlignmentAudio + ClientLink model 的 DB 層測試（in-memory alignment 庫）。"""
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.alignment_db import AlignmentAudio, AlignmentBase, ClientLink


def _mem_session() -> Session:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AlignmentBase.metadata.create_all(eng)
    return Session(eng)


def test_alignment_audio_insert_and_read():
    s = _mem_session()
    row = AlignmentAudio(id="aa1", filename="ref.wav", orig_audio_id="src1")
    s.add(row)
    s.commit()
    got = s.get(AlignmentAudio, "aa1")
    assert got.filename == "ref.wav"
    assert got.orig_audio_id == "src1"
    assert isinstance(got.created_at, datetime)


def test_client_link_insert_and_query_by_hash():
    s = _mem_session()
    s.add(ClientLink(
        id="cl1", token_hash="deadbeef", role="client", label="客戶A",
        annotator_id="cli1", session_id="s1", alignment_audio_id="aa1",
    ))
    s.commit()
    found = s.scalars(
        select(ClientLink).where(ClientLink.token_hash == "deadbeef")
    ).first()
    assert found.role == "client"
    assert found.revoked is False
    assert found.expires_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alignment_client_link_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'AlignmentAudio'`

- [ ] **Step 3: Add the models**

在 `src/alignment_db.py` 頂部 import 補上 `Boolean`：

```python
from sqlalchemy import create_engine, Index, String, Float, Integer, DateTime, Boolean
```

在 `AlignmentSpec` class 之後插入：

```python
def _uuid() -> str:
    import uuid
    return str(uuid.uuid4())


class AlignmentAudio(AlignmentBase):
    """客戶端音檔倉的一筆。實體檔放 data/alignment_audio/，與 data/audio/ 完全分離。

    orig_audio_id 留存出處（annotations.db 的 AudioFile.id，軟參照、僅供追溯），
    客戶端 streaming 只認本表 + 本目錄，碰不到內部音檔。
    """
    __tablename__ = "alignment_audio"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    filename: Mapped[str] = mapped_column(String)            # data/alignment_audio/ 下的檔名
    orig_audio_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ClientLink(AlignmentBase):
    """一條可分享的存取連結 = 一組 token。token 只存 SHA-256 hash，明文不落地。

    role="client"：鎖死 annotator_id / session_id / alignment_audio_id（客戶只能標自己那份）。
    role="engineer"：三者為 None，沿用 query string（工程師為信任內部）。
    """
    __tablename__ = "client_link"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    role: Mapped[str] = mapped_column(String)                # "client" | "engineer"
    label: Mapped[str] = mapped_column(String)               # 客戶名 / 批次標籤
    annotator_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    alignment_audio_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_alignment_client_link_db.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Verify existing alignment DB tests still pass（建表沒破壞隔離）**

Run: `pytest tests/test_alignment_db.py tests/test_alignment_api.py -q`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add src/alignment_db.py tests/test_alignment_client_link_db.py
git commit -m "feat: alignment.db 新增 AlignmentAudio + ClientLink model"
```

---

### Task 2: token 工具 + resolve_alignment_access 依賴

**Files:**
- Create: `src/client_auth.py`
- Test: `tests/test_client_auth.py`（新）

**Interfaces:**
- Consumes: `ClientLink`（Task 1）、`get_alignment_session`（`src/alignment_db.py:106`）、`_get_settings`（`src/middleware.py:43`）
- Produces:
  - `CLIENT_COOKIE: str = "polan_align"`
  - `generate_token() -> str`（urlsafe，≥32 bytes 熵）
  - `hash_token(token: str) -> str`（SHA-256 hex）
  - `verify_token_hash(token: str, expected_hash: str) -> bool`（constant-time）
  - `@dataclass(frozen=True) class AlignmentAccess`：欄位 `role: str`, `annotator_id: str | None`, `session_id: str | None`, `alignment_audio_id: str | None`
  - `resolve_alignment_access(request, response, token=Query(None), db=Depends(get_alignment_session)) -> AlignmentAccess`（FastAPI 依賴）

- [ ] **Step 1: Write the failing test (token helpers)**

```python
# tests/test_client_auth.py
"""token 工具 + resolve_alignment_access 依賴。"""
from datetime import datetime, timedelta, UTC

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.alignment_db import AlignmentBase, ClientLink, get_alignment_session
from src.client_auth import (
    CLIENT_COOKIE,
    AlignmentAccess,
    generate_token,
    hash_token,
    resolve_alignment_access,
    verify_token_hash,
)


def test_generate_token_is_high_entropy_and_unique():
    a, b = generate_token(), generate_token()
    assert a != b
    assert len(a) >= 32


def test_hash_and_verify_roundtrip():
    tok = generate_token()
    h = hash_token(tok)
    assert h != tok                       # 不存明文
    assert verify_token_hash(tok, h) is True
    assert verify_token_hash("wrong", h) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_client_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.client_auth'`

- [ ] **Step 3: Write src/client_auth.py**

```python
"""Alignment 客戶端存取把關（CF Access bypass 後的唯一鎖）。

token 只存 SHA-256 hash；明文僅在發佈當下回傳一次。dev 模式（CF + OAuth 皆關）
直接放行為信任 engineer，沿用 src/middleware.py 的 dev 哲學，讓本機/測試免帶 token。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, UTC

from fastapi import Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.alignment_db import ClientLink, get_alignment_session
from src.middleware import _get_settings

CLIENT_COOKIE = "polan_align"


def generate_token() -> str:
    """≥32 bytes 熵的 urlsafe token（明文，只在發佈當下用）。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex。DB 只存這個。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token_hash(token: str, expected_hash: str) -> bool:
    """constant-time 比對，避免 timing 洩漏。"""
    return hmac.compare_digest(hash_token(token), expected_hash)


@dataclass(frozen=True)
class AlignmentAccess:
    """gate 解析出的存取上下文。client 三欄被鎖死；engineer 三欄為 None。"""
    role: str
    annotator_id: str | None
    session_id: str | None
    alignment_audio_id: str | None


def _link_to_access(link: ClientLink) -> AlignmentAccess:
    return AlignmentAccess(
        role=link.role,
        annotator_id=link.annotator_id,
        session_id=link.session_id,
        alignment_audio_id=link.alignment_audio_id,
    )


def resolve_alignment_access(
    request: Request,
    response: Response,
    token: str | None = Query(default=None),
    db: Session = Depends(get_alignment_session),
) -> AlignmentAccess:
    """alignment 頁與所有 /api/alignment/* 的把關依賴。

    dev 模式（CF + OAuth 皆關）→ 信任 engineer，免 token。
    否則：token 取自 ?token= 或 cookie；驗 hash + 未撤銷 + 未過期；
    首次帶 query token 時種 cookie（後續 API / 音檔自動帶）。
    """
    settings = _get_settings(request)
    if not settings.cloudflare_access_enabled and not settings.oauth_enabled:
        return AlignmentAccess(role="engineer", annotator_id=None,
                               session_id=None, alignment_audio_id=None)

    from_query = token is not None and token.strip() != ""
    raw = token.strip() if from_query else request.cookies.get(CLIENT_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺存取 token，請使用發佈的連結進入")

    link = db.scalars(
        select(ClientLink).where(ClientLink.token_hash == hash_token(raw))
    ).first()
    if link is None or not verify_token_hash(raw, link.token_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 無效")
    if link.revoked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "此連結已被撤銷")
    if link.expires_at is not None and datetime.now(UTC) > link.expires_at.replace(tzinfo=UTC):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "此連結已過期")

    if from_query:
        response.set_cookie(
            key=CLIENT_COOKIE, value=raw, httponly=True, secure=True,
            samesite="lax", max_age=60 * 60 * 24 * 30,
        )
    return _link_to_access(link)
```

- [ ] **Step 4: Run helper tests to verify pass**

Run: `pytest tests/test_client_auth.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Add gate behaviour tests (dev passthrough + token enforcement)**

在 `tests/test_client_auth.py` 末尾追加：

```python
def _app_with_gate(eng, cf_enabled: bool):
    """最小 app：一個受 gate 保護的路由，回傳解析出的 access。"""
    app = FastAPI()

    class _S:
        cloudflare_access_enabled = cf_enabled
        oauth_enabled = False
    app.state.settings = _S()

    def _override():
        with Session(eng) as s:
            yield s
    app.dependency_overrides[get_alignment_session] = _override

    @app.get("/probe")
    def probe(acc: AlignmentAccess = Depends(resolve_alignment_access)):
        return {"role": acc.role, "session_id": acc.session_id}

    return app


@pytest.fixture
def gate_engine():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)
    return eng


def test_dev_mode_passes_through_as_engineer(gate_engine):
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=False))
    r = c.get("/probe")
    assert r.status_code == 200
    assert r.json()["role"] == "engineer"


def test_prod_mode_rejects_missing_token(gate_engine):
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=True))
    r = c.get("/probe")
    assert r.status_code == 401


def test_prod_mode_accepts_valid_client_token_and_sets_cookie(gate_engine):
    tok = generate_token()
    with Session(gate_engine) as s:
        s.add(ClientLink(id="cl1", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1"))
        s.commit()
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=True))
    r = c.get(f"/probe?token={tok}")
    assert r.status_code == 200
    assert r.json() == {"role": "client", "session_id": "s1"}
    assert CLIENT_COOKIE in r.cookies


def test_prod_mode_rejects_revoked_token(gate_engine):
    tok = generate_token()
    with Session(gate_engine) as s:
        s.add(ClientLink(id="cl2", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1", revoked=True))
        s.commit()
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=True))
    assert c.get(f"/probe?token={tok}").status_code == 403
```

- [ ] **Step 6: Run full file**

Run: `pytest tests/test_client_auth.py -v`
Expected: PASS（6 passed）

- [ ] **Step 7: Commit**

```bash
git add src/client_auth.py tests/test_client_auth.py
git commit -m "feat: alignment 客戶 token gate（hash 存、可撤銷/過期、dev 放行）"
```

---

### Task 3: 發佈服務 publish_audio_link（複製音檔進倉 + 建 link）

**Files:**
- Create: `src/alignment_publish.py`
- Test: `tests/test_alignment_publish.py`（新）

**Interfaces:**
- Consumes: `AlignmentAudio`/`ClientLink`（Task 1）、`generate_token`/`hash_token`（Task 2）、`AudioFile`（`src/models.py`）、`AUDIO_DIR`（`src/audio_analysis.py:22`）
- Produces:
  - `ALIGNMENT_AUDIO_DIR: Path = PROJECT_ROOT / "data" / "alignment_audio"`
  - `@dataclass(frozen=True) class PublishResult`：`token: str`, `link_id: str`, `alignment_audio_id: str`, `session_id: str`
  - `publish_audio_link(*, src_filename, label, role, annotator_id, session_id, expires_at, align_db, src_audio_dir=AUDIO_DIR, dst_audio_dir=ALIGNMENT_AUDIO_DIR, orig_audio_id=None) -> PublishResult`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alignment_publish.py
"""發佈服務：複製音檔進獨立倉 + 建 AlignmentAudio + ClientLink。"""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.alignment_db import AlignmentAudio, AlignmentBase, ClientLink
from src.alignment_publish import publish_audio_link
from src.client_auth import hash_token


def _align_session():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)
    return Session(eng)


def test_publish_copies_file_and_creates_rows(tmp_path):
    src_dir = tmp_path / "audio"
    dst_dir = tmp_path / "alignment_audio"
    src_dir.mkdir()
    (src_dir / "ref.wav").write_bytes(b"RIFF0000WAVE")

    db = _align_session()
    res = publish_audio_link(
        src_filename="ref.wav", label="客戶A", role="client",
        annotator_id="cli1", session_id="s1", expires_at=None,
        align_db=db, src_audio_dir=src_dir, dst_audio_dir=dst_dir,
        orig_audio_id="orig1",
    )

    # 檔案被複製到獨立倉，原檔還在
    assert (dst_dir / "ref.wav").read_bytes() == b"RIFF0000WAVE"
    assert (src_dir / "ref.wav").exists()

    aa = db.get(AlignmentAudio, res.alignment_audio_id)
    assert aa.filename == "ref.wav"
    assert aa.orig_audio_id == "orig1"

    link = db.scalars(
        select(ClientLink).where(ClientLink.id == res.link_id)
    ).first()
    assert link.role == "client"
    assert link.session_id == res.session_id
    assert link.alignment_audio_id == res.alignment_audio_id
    # DB 只存 hash，回傳明文 token 的 hash 要對得上
    assert link.token_hash == hash_token(res.token)


def test_publish_missing_source_raises(tmp_path):
    db = _align_session()
    try:
        publish_audio_link(
            src_filename="nope.wav", label="A", role="client",
            annotator_id="cli1", session_id="s1", expires_at=None,
            align_db=db, src_audio_dir=tmp_path, dst_audio_dir=tmp_path / "out",
        )
        assert False, "should have raised"
    except FileNotFoundError:
        pass
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_alignment_publish.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.alignment_publish'`

- [ ] **Step 3: Write src/alignment_publish.py**

```python
"""發佈服務：把一支內部 ref 音檔複製進獨立客戶倉，並產生一條存取 link。

實體隔離硬需求：客戶端只認 data/alignment_audio/ + alignment.db，
永遠碰不到 data/audio/ 的內部音檔。
"""
from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from src.alignment_db import AlignmentAudio, ClientLink
from src.audio_analysis import AUDIO_DIR
from src.client_auth import generate_token, hash_token

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALIGNMENT_AUDIO_DIR = PROJECT_ROOT / "data" / "alignment_audio"


@dataclass(frozen=True)
class PublishResult:
    token: str               # 明文，只此一次
    link_id: str
    alignment_audio_id: str
    session_id: str


def publish_audio_link(
    *,
    src_filename: str,
    label: str,
    role: str,
    annotator_id: str | None,
    session_id: str | None,
    expires_at: datetime | None,
    align_db: Session,
    src_audio_dir: Path = AUDIO_DIR,
    dst_audio_dir: Path = ALIGNMENT_AUDIO_DIR,
    orig_audio_id: str | None = None,
) -> PublishResult:
    """複製音檔 → 建 AlignmentAudio → 建 ClientLink。回傳明文 token（只此一次）。

    role="client" 時自動補 session_id（缺則生成），三欄會鎖進 link。
    """
    src_path = src_audio_dir / src_filename
    if not src_path.exists():
        raise FileNotFoundError(f"找不到來源音檔：{src_filename}")

    dst_audio_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_audio_dir / src_filename)

    audio = AlignmentAudio(filename=src_filename, orig_audio_id=orig_audio_id)
    align_db.add(audio)
    align_db.flush()  # 取 audio.id

    resolved_session = session_id or f"sess-{uuid.uuid4().hex[:8]}"
    token = generate_token()
    link = ClientLink(
        token_hash=hash_token(token),
        role=role,
        label=label,
        annotator_id=annotator_id if role == "client" else None,
        session_id=resolved_session if role == "client" else None,
        alignment_audio_id=audio.id if role == "client" else None,
        expires_at=expires_at,
    )
    align_db.add(link)
    align_db.commit()

    return PublishResult(
        token=token, link_id=link.id,
        alignment_audio_id=audio.id, session_id=resolved_session,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_alignment_publish.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add src/alignment_publish.py tests/test_alignment_publish.py
git commit -m "feat: alignment 發佈服務（複製音檔進獨立倉 + 建 link）"
```

---

### Task 4: alignment 路由掛 gate + /context + 獨立 stream 端點

**Files:**
- Modify: `src/routes/alignment.py`
- Modify: `src/alignment_db.py`（`create_alignment_db` 後不需改；建表已涵蓋新表）
- Test: `tests/test_alignment_isolation_api.py`（新）

**Interfaces:**
- Consumes: `resolve_alignment_access`/`AlignmentAccess`（Task 2）、`AlignmentAudio`（Task 1）、`ALIGNMENT_AUDIO_DIR`（Task 3）
- Produces：
  - `GET /api/alignment/context` → `{role, annotator_id, session_id, alignment_audio_id}`
  - `GET /api/alignment/audio/{alignment_audio_id}/stream` → `FileResponse`
  - router 全端點經 `resolve_alignment_access`；client 的 `session_id` 被強制覆蓋

- [ ] **Step 1: Write the failing test (context + stream isolation)**

```python
# tests/test_alignment_isolation_api.py
"""alignment 端點的存取隔離：context 回鎖定 ctx、音檔只在獨立倉解析。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src import main as main_module
from src.alignment_db import AlignmentAudio, AlignmentBase, ClientLink, get_alignment_session
from src.client_auth import generate_token, hash_token
import src.routes.alignment as align_routes


@pytest.fixture
def iso(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)

    # 獨立音檔倉指向 tmp
    audio_dir = tmp_path / "alignment_audio"
    audio_dir.mkdir()
    (audio_dir / "ref.wav").write_bytes(b"RIFF0000WAVE")
    monkeypatch.setattr(align_routes, "ALIGNMENT_AUDIO_DIR", audio_dir)

    tok = generate_token()
    with Session(eng) as s:
        s.add(AlignmentAudio(id="aa1", filename="ref.wav"))
        s.add(ClientLink(id="cl1", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1"))
        s.commit()

    def _override():
        with Session(eng) as s:
            yield s
    main_module.app.dependency_overrides[get_alignment_session] = _override

    # 啟用 prod gate（強制 token）
    class _S:
        cloudflare_access_enabled = True
        oauth_enabled = False
        cloudflare_access_team_domain = ""
        cloudflare_access_aud = ""
    monkeypatch.setattr(main_module.app.state, "settings", _S())

    yield TestClient(main_module.app), tok
    main_module.app.dependency_overrides.clear()


def test_context_returns_locked_ctx(iso):
    client, tok = iso
    r = client.get(f"/api/alignment/context?token={tok}")
    assert r.status_code == 200
    assert r.json() == {
        "role": "client", "annotator_id": "cli1",
        "session_id": "s1", "alignment_audio_id": "aa1",
    }


def test_stream_serves_from_isolated_store(iso):
    client, tok = iso
    r = client.get(f"/api/alignment/audio/aa1/stream?token={tok}")
    assert r.status_code == 200
    assert r.content == b"RIFF0000WAVE"


def test_stream_rejects_audio_outside_link(iso):
    client, tok = iso
    # 客戶要別支音檔 → 403（不在 token 綁定範圍）
    r = client.get(f"/api/alignment/audio/aa-other/stream?token={tok}")
    assert r.status_code == 403


def test_context_without_token_rejected(iso):
    client, _tok = iso
    assert client.get("/api/alignment/context").status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_alignment_isolation_api.py -v`
Expected: FAIL（`/api/alignment/context` 404，端點未建）

- [ ] **Step 3: Wire the gate + add endpoints**

在 `src/routes/alignment.py` 的 import 區補上：

```python
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from src.alignment_db import AlignmentAudio, AlignmentReading, AlignmentSpec, get_alignment_session
from src.alignment_publish import ALIGNMENT_AUDIO_DIR
from src.client_auth import AlignmentAccess, resolve_alignment_access
```

把 router 宣告改為整組掛 gate：

```python
router = APIRouter(
    prefix="/api/alignment",
    tags=["alignment"],
    dependencies=[Depends(resolve_alignment_access)],
)
```

新增 context 端點（放在 `bgm_dimensions` 之前）：

```python
@router.get("/context")
def alignment_context(
    access: AlignmentAccess = Depends(resolve_alignment_access),
) -> dict:
    """回前端要載入的上下文。client 為鎖定值；engineer 三欄為 None（前端改讀 query）。"""
    return {
        "role": access.role,
        "annotator_id": access.annotator_id,
        "session_id": access.session_id,
        "alignment_audio_id": access.alignment_audio_id,
    }


@router.get("/audio/{alignment_audio_id}/stream")
def stream_alignment_audio(
    alignment_audio_id: str,
    access: AlignmentAccess = Depends(resolve_alignment_access),
    db: Session = Depends(get_alignment_session),
) -> FileResponse:
    """只從 data/alignment_audio/ serve；client 只能串 token 綁定的那支。"""
    if access.role == "client" and access.alignment_audio_id != alignment_audio_id:
        raise HTTPException(403, "無權存取此音檔")
    audio = db.get(AlignmentAudio, alignment_audio_id)
    if audio is None:
        raise HTTPException(404, f"找不到音檔：{alignment_audio_id}")
    path = ALIGNMENT_AUDIO_DIR / audio.filename
    if not path.exists():
        raise HTTPException(404, f"音檔檔案不存在：{audio.filename}")
    media_type, _ = mimetypes.guess_type(audio.filename)
    if not media_type or not media_type.startswith("audio/"):
        media_type = "audio/wav"
    return FileResponse(path=path, media_type=media_type, filename=audio.filename)
```

強制注入 client session_id —— 在 `save_readings`、`list_readings`、`save_spec`、`list_specs` 各自加上 `access` 參數並覆蓋 session_id。範例（`list_readings`）：

```python
@router.get("/readings")
def list_readings(
    session_id: str = Query(default=""),
    access: AlignmentAccess = Depends(resolve_alignment_access),
    db: Session = Depends(get_alignment_session),
) -> dict:
    """回傳某 session 全部 reading；client 一律用 token 綁定的 session_id。"""
    sid = access.session_id or session_id
    rows = db.scalars(
        select(AlignmentReading).where(AlignmentReading.session_id == sid)
    ).all()
    # ...（其餘不變）
```

`save_readings` / `save_spec`：在 `_validate_*` 之後、寫入之前插入：

```python
    if access.session_id is not None:        # client：鎖死 session
        payload.session_id = access.session_id
    if access.annotator_id is not None:
        payload.annotator_id = access.annotator_id
        payload.annotator_role = "client"
```

`list_specs` 比照 `list_readings` 用 `access.session_id or session_id`。

- [ ] **Step 4: Run isolation tests to verify pass**

Run: `pytest tests/test_alignment_isolation_api.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Run existing alignment API tests (dev passthrough must keep them green)**

Run: `pytest tests/test_alignment_api.py tests/test_alignment_spec_api.py -q`
Expected: 全 PASS（dev 模式 gate 放行 → 既有測試不帶 token 仍可寫入）

- [ ] **Step 6: Commit**

```bash
git add src/routes/alignment.py tests/test_alignment_isolation_api.py
git commit -m "feat: alignment 端點掛 token gate + /context + 獨立音檔 stream"
```

---

### Task 5: 頁面路由 /alignment 掛 gate（驗 token + 種 cookie）

**Files:**
- Modify: `src/main.py:141-144`（`alignment_page`）
- Test: `tests/test_alignment_page_gate.py`（新）

**Interfaces:**
- Consumes: `resolve_alignment_access`（Task 2）
- Produces: `/alignment` 在 prod 模式下無有效 token → 401；dev 模式 → 照常 serve

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alignment_page_gate.py
"""/alignment 頁面路由的 token gate（prod 擋、dev 放行）。"""
import pytest
from fastapi.testclient import TestClient

from src import main as main_module


def test_alignment_page_dev_mode_serves():
    # conftest 預設 OAUTH=false、CF=false → dev 放行
    c = TestClient(main_module.app)
    r = c.get("/alignment")
    assert r.status_code == 200


def test_alignment_page_prod_mode_requires_token(monkeypatch):
    class _S:
        cloudflare_access_enabled = True
        oauth_enabled = False
    monkeypatch.setattr(main_module.app.state, "settings", _S())
    c = TestClient(main_module.app)
    r = c.get("/alignment")
    assert r.status_code == 401
    monkeypatch.undo()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_alignment_page_gate.py -v`
Expected: FAIL（prod test 拿到 200，gate 未掛）

- [ ] **Step 3: Gate the page route**

`src/main.py`：在既有 `from src.middleware import require_auth` 附近加：

```python
from src.client_auth import resolve_alignment_access  # noqa: E402
```

把 `alignment_page` 改為：

```python
@app.get("/alignment", include_in_schema=False)
def alignment_page(
    _access=Depends(resolve_alignment_access),
) -> FileResponse:
    """BGM 對齊標註頁。prod 須帶有效 token（gate 會種 cookie）；dev 放行。"""
    return FileResponse(STATIC_DIR / "alignment.html")
```

（`Depends` 已在 `src/main.py:173` import；`resolve_alignment_access` 內部自帶 `Response` 參數，FastAPI 會注入，cookie 能正確種到回應。）

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_alignment_page_gate.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add src/main.py tests/test_alignment_page_gate.py
git commit -m "feat: /alignment 頁面路由掛 token gate"
```

---

### Task 6: admin 發佈 / 列表 / 撤銷端點

**Files:**
- Modify: `src/routes/admin.py`
- Test: `tests/test_alignment_admin_api.py`（新）

**Interfaces:**
- Consumes: `require_auth`/`_require_admin`（`src/routes/admin.py`）、`publish_audio_link`/`PublishResult`（Task 3）、`ClientLink`（Task 1）、`get_alignment_session`
- Produces:
  - `POST /api/admin/alignment/publish` body `{filename, label, role?, annotator_id?, session_id?, expires_at?, orig_audio_id?}` → `{token, client_url, link_id, alignment_audio_id, session_id}`
  - `GET /api/admin/alignment/links` → `{links: [...]}`（不含 token）
  - `POST /api/admin/alignment/links/{link_id}/revoke` → `{revoked: true}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alignment_admin_api.py
"""admin 發佈 / 列表 / 撤銷 client link。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src import main as main_module
from src.alignment_db import AlignmentBase, ClientLink, get_alignment_session
import src.routes.admin as admin_routes


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)

    src_dir = tmp_path / "audio"
    src_dir.mkdir()
    (src_dir / "ref.wav").write_bytes(b"RIFF0000WAVE")
    # publish 端點用的來源/目的目錄都導到 tmp
    monkeypatch.setattr(admin_routes, "AUDIO_DIR", src_dir, raising=False)
    monkeypatch.setattr(admin_routes, "ALIGNMENT_AUDIO_DIR", tmp_path / "out", raising=False)

    def _override():
        with Session(eng) as s:
            yield s
    main_module.app.dependency_overrides[get_alignment_session] = _override
    yield TestClient(main_module.app), eng
    main_module.app.dependency_overrides.clear()


def test_publish_returns_url_and_token(admin_client):
    client, _ = admin_client
    r = client.post("/api/admin/alignment/publish", json={
        "filename": "ref.wav", "label": "客戶A", "annotator_id": "cli1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert "/alignment?token=" in body["client_url"]
    assert body["session_id"]


def test_links_list_excludes_token(admin_client):
    client, _ = admin_client
    client.post("/api/admin/alignment/publish", json={"filename": "ref.wav", "label": "A", "annotator_id": "c1"})
    r = client.get("/api/admin/alignment/links")
    assert r.status_code == 200
    links = r.json()["links"]
    assert len(links) == 1
    assert "token" not in links[0]
    assert "token_hash" not in links[0]


def test_revoke_marks_link(admin_client):
    client, eng = admin_client
    pub = client.post("/api/admin/alignment/publish", json={"filename": "ref.wav", "label": "A", "annotator_id": "c1"}).json()
    r = client.post(f"/api/admin/alignment/links/{pub['link_id']}/revoke")
    assert r.status_code == 200
    with Session(eng) as s:
        assert s.get(ClientLink, pub["link_id"]).revoked is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_alignment_admin_api.py -v`
Expected: FAIL（publish 端點 404）

- [ ] **Step 3: Add endpoints to admin.py**

`src/routes/admin.py` import 區補上：

```python
from datetime import datetime
from urllib.parse import quote

from fastapi import Request
from sqlalchemy import select as sa_select

from src.alignment_db import ClientLink, get_alignment_session
from src.alignment_publish import ALIGNMENT_AUDIO_DIR, publish_audio_link  # noqa: F401
from src.audio_analysis import AUDIO_DIR  # noqa: F401
```

加 Pydantic schema 與端點（檔案末尾）：

```python
class PublishLinkBody(BaseModel):
    filename: str
    label: str
    role: str = "client"
    annotator_id: str | None = None
    session_id: str | None = None
    expires_at: datetime | None = None
    orig_audio_id: str | None = None


@router.post("/alignment/publish")
def publish_alignment_link(
    body: PublishLinkBody,
    request: Request,
    current_user: dict[str, Any] = Depends(require_auth),
    align_db: Session = Depends(get_alignment_session),
) -> dict[str, Any]:
    """admin：把一支 ref 音檔發佈成客戶可標的連結。回明文 token（只此一次）。"""
    _require_admin(current_user)
    if body.role not in {"client", "engineer"}:
        raise HTTPException(400, f"role 必須是 client 或 engineer，收到 {body.role!r}")
    try:
        res = publish_audio_link(
            src_filename=body.filename, label=body.label, role=body.role,
            annotator_id=body.annotator_id, session_id=body.session_id,
            expires_at=body.expires_at, align_db=align_db,
            src_audio_dir=AUDIO_DIR, dst_audio_dir=ALIGNMENT_AUDIO_DIR,
            orig_audio_id=body.orig_audio_id,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    base = str(request.base_url).rstrip("/")
    return {
        "token": res.token,
        "client_url": f"{base}/alignment?token={quote(res.token)}",
        "link_id": res.link_id,
        "alignment_audio_id": res.alignment_audio_id,
        "session_id": res.session_id,
    }


@router.get("/alignment/links")
def list_alignment_links(
    current_user: dict[str, Any] = Depends(require_auth),
    align_db: Session = Depends(get_alignment_session),
) -> dict[str, Any]:
    """admin：列出所有 client link（不含 token / token_hash）。"""
    _require_admin(current_user)
    rows = align_db.scalars(sa_select(ClientLink)).all()
    return {"links": [
        {
            "id": r.id, "role": r.role, "label": r.label,
            "annotator_id": r.annotator_id, "session_id": r.session_id,
            "alignment_audio_id": r.alignment_audio_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "revoked": r.revoked,
        }
        for r in rows
    ]}


@router.post("/alignment/links/{link_id}/revoke")
def revoke_alignment_link(
    link_id: str,
    current_user: dict[str, Any] = Depends(require_auth),
    align_db: Session = Depends(get_alignment_session),
) -> dict[str, Any]:
    """admin：撤銷一條 link（立即失效）。"""
    _require_admin(current_user)
    link = align_db.get(ClientLink, link_id)
    if link is None:
        raise HTTPException(404, "找不到此連結")
    link.revoked = True
    align_db.commit()
    return {"revoked": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_alignment_admin_api.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/routes/admin.py tests/test_alignment_admin_api.py
git commit -m "feat: admin 發佈/列表/撤銷 alignment 客戶連結"
```

---

### Task 7: 前端 alignment.js 改用 /context + 新 stream 端點

**Files:**
- Modify: `static/alignment.js:6-15`（CTX 取得）、`:164-174`（init / player）

**Interfaces:**
- Consumes: `GET /api/alignment/context`（Task 4）、`/api/alignment/audio/{id}/stream`（Task 4）
- Produces: 無（純前端）

> 此 task 無自動化測試（vanilla JS，repo 無前端測試框架）。驗證走手動冒煙（Step 3）。

- [ ] **Step 1: Replace context acquisition**

把 `static/alignment.js:6-15` 的 `CTX` 區塊改為先讀 query（engineer fallback）、待 init 時用 `/context` 覆蓋：

```javascript
// ========== context ==========
// client：由 /context 回傳鎖定值。engineer：沿用 query string。
const qs = new URLSearchParams(window.location.search)
const CTX = {
  session_id: qs.get('session_id') || 's1',
  annotator_id: qs.get('annotator_id') || 'guest',
  annotator_role: qs.get('annotator_role') || 'client',
  alignment_audio_id: qs.get('audio_id') || '',
  audio_role: qs.get('audio_role') || 'ref',
  version: parseInt(qs.get('version') || '0', 10),
}
```

注意：`submit()` 內 `base.audio_id` 要改用 `CTX.alignment_audio_id`。找到 `src/alignment.js` 的 `base`（約 :140-144）改：

```javascript
  const base = {
    session_id: CTX.session_id, annotator_id: CTX.annotator_id,
    annotator_role: CTX.annotator_role, audio_id: CTX.alignment_audio_id,
    audio_role: CTX.audio_role, version: CTX.version,
  }
```

- [ ] **Step 2: Fetch /context and point player at the isolated stream**

把 `init()`（`static/alignment.js:165-174`）開頭改為：

```javascript
async function init() {
  try {
    const ctx = await fetchJson('/api/alignment/context')
    if (ctx.role === 'client') {
      CTX.session_id = ctx.session_id
      CTX.annotator_id = ctx.annotator_id
      CTX.annotator_role = 'client'
      CTX.alignment_audio_id = ctx.alignment_audio_id
    }
  } catch (err) {
    showBanner(`無法載入存取資訊：${err.message}`, false)
    return
  }

  $('context-line').textContent =
    `session ${CTX.session_id} ・ ${CTX.annotator_role} ${CTX.annotator_id} ・ ${CTX.audio_role} v${CTX.version}` +
    (CTX.alignment_audio_id ? ` ・ ${CTX.alignment_audio_id}` : '（未指定音檔）')

  if (CTX.alignment_audio_id) {
    const player = $('player')
    player.src = `/api/alignment/audio/${encodeURIComponent(CTX.alignment_audio_id)}/stream`
    player.classList.remove('hidden')
  }

  // ...（其餘 dimensions / style / chips 載入維持不變）
```

- [ ] **Step 3: Manual smoke verification**

啟動本機 server（dev 模式，gate 放行）：

Run: `uvicorn src.main:app --port 8000`
然後瀏覽器開 `http://localhost:8000/alignment?audio_id=<某個已發佈的 AlignmentAudio id>&session_id=s1`
Expected:
- context-line 正常顯示、無紅 banner。
- 若該 audio_id 已透過 publish 進倉，player 能播放。
- DevTools Network：音檔請求打的是 `/api/alignment/audio/.../stream`，**不是** `/api/audio/.../stream`。

- [ ] **Step 4: Commit**

```bash
git add static/alignment.js
git commit -m "feat: alignment 前端改用 /context + 獨立音檔 stream 端點"
```

---

### Task 8: Dashboard 發佈 widget（表單 + 連結列表 + 撤銷）

**Files:**
- Modify: `static/dashboard.html`（加 admin-only section 容器）
- Modify: `static/dashboard.js`（加 `loadAlignmentLinks()` + 發佈表單邏輯）

**Interfaces:**
- Consumes: `/api/audio`（既有，列音檔）、`/api/admin/alignment/publish`、`/api/admin/alignment/links`、`/api/admin/alignment/links/{id}/revoke`（Task 6）
- Produces: 無

> 此 task 無自動化測試（vanilla JS）。驗證走手動冒煙（Step 3）。
> 設計取捨：dashboard 現無逐列音檔表，故改用一個自包含 widget（音檔下拉 + label + 發佈鈕 + 連結列表），比硬塞逐列按鈕乾淨，符合既有 widget 模式。

- [ ] **Step 1: Add the section container to dashboard.html**

在 `static/dashboard.html` 的待校準 section（`id="pending-section"`）附近，加入一個預設隱藏的 admin section：

```html
<section id="alignment-links-section" class="hidden mt-6">
  <h2 class="text-lg font-semibold mb-2">發佈客戶對齊連結</h2>
  <div class="flex flex-wrap items-end gap-2 mb-3">
    <label class="text-sm">音檔
      <select id="al-audio" class="block border rounded px-2 py-1 text-sm"></select>
    </label>
    <label class="text-sm">客戶標籤
      <input id="al-label" type="text" placeholder="例：客戶A 第一批"
             class="block border rounded px-2 py-1 text-sm" />
    </label>
    <button id="al-publish" type="button"
            class="px-3 py-1.5 text-sm font-medium rounded bg-amber-600 text-white hover:bg-amber-700">
      發佈連結
    </button>
  </div>
  <div id="al-result" class="hidden text-sm mb-3"></div>
  <div id="al-links"></div>
</section>
```

- [ ] **Step 2: Add the widget logic to dashboard.js**

在 `static/dashboard.js` 的 `loadAll()` 的 `Promise.all([...])` 陣列尾端加入 `loadAlignmentLinks()`，並於檔案中新增：

```javascript
// 發佈客戶對齊連結（admin only；403 → 靜默隱藏整個 section）
async function loadAlignmentLinks() {
  const section = $('alignment-links-section')
  try {
    const res = await fetch('/api/admin/alignment/links')
    if (res.status === 403) { section.classList.add('hidden'); return }
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    section.classList.remove('hidden')
    await fillAudioOptions()
    renderAlignmentLinks((await res.json()).links)
    const btn = $('al-publish')
    if (!btn.dataset.bound) {
      btn.dataset.bound = '1'
      btn.addEventListener('click', publishAlignmentLink)
    }
  } catch {
    section.classList.add('hidden')
  }
}

async function fillAudioOptions() {
  const sel = $('al-audio')
  if (sel.dataset.filled) return
  try {
    const res = await fetch('/api/audio')
    if (!res.ok) return
    const items = await res.json()
    sel.innerHTML = items.map(a =>
      `<option value="${escapeAttr(a.filename)}">${escapeHtml(a.game_name)} – ${escapeHtml(a.game_stage)}</option>`
    ).join('')
    sel.dataset.filled = '1'
  } catch {
    // 靜默
  }
}

async function publishAlignmentLink() {
  const filename = $('al-audio').value
  const label = $('al-label').value.trim()
  if (!filename || !label) { alert('請選音檔並填客戶標籤'); return }
  const btn = $('al-publish')
  btn.disabled = true
  try {
    const res = await fetch('/api/admin/alignment/publish', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, label, role: 'client', annotator_id: label }),
    })
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      throw new Error(e.detail || `HTTP ${res.status}`)
    }
    const d = await res.json()
    const box = $('al-result')
    box.classList.remove('hidden')
    box.innerHTML =
      `連結（只顯示一次，請複製）：<input readonly value="${escapeAttr(d.client_url)}"
        class="w-full border rounded px-2 py-1 mt-1 font-mono text-xs" onclick="this.select()" />`
    $('al-label').value = ''
    await loadAlignmentLinks()
  } catch (err) {
    alert(`發佈失敗：${err.message}`)
  } finally {
    btn.disabled = false
  }
}

function renderAlignmentLinks(links) {
  const wrap = $('al-links')
  if (!links.length) { wrap.innerHTML = '<div class="text-sm text-slate-500">尚無連結</div>'; return }
  wrap.innerHTML = links.map(l => `
    <div class="flex items-center gap-3 p-2 border-t border-slate-200 dark:border-slate-700">
      <div class="flex-1 min-w-0">
        <span class="font-medium">${escapeHtml(l.label)}</span>
        <span class="text-xs text-slate-500">(${escapeHtml(l.role)}・session ${escapeHtml(l.session_id || '—')})</span>
        ${l.revoked ? '<span class="text-xs text-rose-600">已撤銷</span>' : ''}
      </div>
      ${l.revoked ? '' : `<button type="button" data-revoke="${escapeAttr(l.id)}"
        class="px-2 py-1 text-xs rounded bg-rose-600 text-white hover:bg-rose-700">撤銷</button>`}
    </div>
  `).join('')
  wrap.querySelectorAll('button[data-revoke]').forEach(b =>
    b.addEventListener('click', () => revokeAlignmentLink(b.dataset.revoke)))
}

async function revokeAlignmentLink(linkId) {
  if (!confirm('確定撤銷此連結？客戶將立即無法存取。')) return
  try {
    const res = await fetch(`/api/admin/alignment/links/${encodeURIComponent(linkId)}/revoke`, { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await loadAlignmentLinks()
  } catch (err) {
    alert(`撤銷失敗：${err.message}`)
  }
}
```

- [ ] **Step 3: Manual smoke verification**

Run: `uvicorn src.main:app --port 8000`（dev 模式：`/api/me` 回 is_admin=true）
瀏覽器開 `http://localhost:8000/dashboard`
Expected:
- 看到「發佈客戶對齊連結」section。
- 選一支音檔、填標籤、按「發佈連結」→ 出現可複製的 `…/alignment?token=…`。
- 連結列表出現該筆，按「撤銷」後標記為已撤銷。
- 開該連結（dev 模式）能進 alignment 頁。

- [ ] **Step 4: Commit**

```bash
git add static/dashboard.html static/dashboard.js
git commit -m "feat: dashboard 發佈客戶對齊連結 widget"
```

---

### Task 9: Cloudflare Access Bypass 操作文件

**Files:**
- Create: `docs/ops/cloudflare-alignment-bypass.md`

**Interfaces:** 無（純文件）

- [ ] **Step 1: Write the ops doc**

```markdown
# Cloudflare Access：alignment 客戶 bypass 設定

> 目標：讓外部客戶能用 token 連結進 `/alignment`，但內部工具維持 OTP 白名單保護。

## 要加的 Bypass policy（只對這兩個 path pattern）

在 Zero Trust → Access → Applications，對 `annotate.dolcenforte.com` 既有 application：

新增一條 **Bypass** policy（或一個獨立 application，path 限定）：
- Path: `/alignment`
- Path: `/api/alignment/*`

Action: **Bypass**（Everyone）。

## 絕對不要 bypass 的 path（維持 OTP）

- `/dashboard`、`/upload`、`/annotator/*`
- `/admin/*`
- `/api/export/*`  ← 資料集本體
- `/api/stats/*`、`/api/audio/*`、`/calibration*`
- 其餘所有 path

## 驗收

1. 無痕視窗開 `…/alignment?token=<有效>` → 進得去、能播音檔、能標。
2. 同視窗（已帶 alignment cookie）直打 `…/dashboard` → 被 CF Access OTP 擋（你不在白名單的角色）。
3. 同視窗直打 `…/api/export/dataset.json` → 被 CF Access OTP 擋。
4. 撤銷該 token 後重開連結 → app 回 403。

## 原理

bypass 後 `/alignment` + `/api/alignment/*` 由 app 層 token gate（`src/client_auth.py`）把關；
token 存 hash、可撤銷、可過期。其餘 path 仍在 CF Access 後，客戶從未被加進白名單，故進不去。
```

- [ ] **Step 2: Commit**

```bash
git add docs/ops/cloudflare-alignment-bypass.md
git commit -m "docs: Cloudflare Access alignment bypass 操作說明"
```

---

### Task 10: 全套件回歸 + 隔離驗收

**Files:** 無（驗證）

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: 全 PASS（含既有測試 + 本次新增 5 個測試檔）

- [ ] **Step 2: Grep 確認沒有殘留把客戶導向內部音檔端點**

Run: `grep -rn "api/audio" static/alignment.js`
Expected: 無輸出（alignment 前端只走 `/api/alignment/audio/...`）

- [ ] **Step 3: 確認成功標準（對照 spec §成功標準）**

逐條人工確認（dev 模式 + 一次 prod-like monkeypatch 已由各 task 測試覆蓋）：
1. ✅ Task 4/5 測試：valid token 能進頁、串到綁定音檔、存讀標註。
2. ✅ Task 9 文件 + CF 後台：cookie 直打內部端點被 OTP 擋（部署後人工驗）。
3. ✅ Task 2/4 測試：改 session_id / audio_id 只能存取自己綁定那份。
4. ✅ Task 3/4 測試：stream 只在 `data/alignment_audio/` 解析。
5. ✅ Task 2 測試：revoke 後立即 403。

- [ ] **Step 4: Final commit (if any cleanup)**

```bash
git status   # 應乾淨；如有 ruff 整理再 commit
```

---

## Self-Review

**Spec coverage：**
- A 存取隔離（token gate + CF bypass）→ Task 2（gate）、Task 5（頁面）、Task 9（CF 文件）✅
- ClientLink 表 + role/session/audio 綁定 → Task 1 + 強制注入 Task 4 ✅
- B 音檔分倉（新目錄 + AlignmentAudio + 新 stream 端點）→ Task 1 + Task 3 + Task 4 ✅
- C 前端改 /context + 新 stream → Task 7 ✅
- D 發佈動作（publish/list/revoke + dashboard 按鈕）→ Task 3 + Task 6 + Task 8 ✅
- 安全（hash 存、constant-time、可撤銷、可過期、admin gate）→ Task 2 + Task 6 ✅

**Placeholder scan：** 無 TODO / 「適當處理」/ 省略測試碼；每個 code step 都有完整內容。

**Type consistency：**
- `AlignmentAccess(role, annotator_id, session_id, alignment_audio_id)` 一致用於 Task 2/4/5。
- `publish_audio_link(...) -> PublishResult(token, link_id, alignment_audio_id, session_id)` 一致用於 Task 3/6。
- `CLIENT_COOKIE = "polan_align"`、`resolve_alignment_access`、`hash_token`/`generate_token` 命名跨 task 一致。
- `ClientLink` 欄位（`token_hash`/`role`/`label`/`annotator_id`/`session_id`/`alignment_audio_id`/`expires_at`/`revoked`）跨 Task 1/2/3/6 一致。

**已知部署注意：** `data/alignment_audio/` 為新目錄，首次 `publish` 會自動建（`dst_audio_dir.mkdir(parents=True)`）。VPS 上若用 Litestream/備份，記得納入此目錄（部署文件外，屬 ops 後續）。
