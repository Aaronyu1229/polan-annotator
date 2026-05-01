"""Phase 6 — admin-only 音源上傳 API 測試。

涵蓋：
- admin 上傳合法 .wav → 200，檔落地、AudioFile row 入庫、回傳 shape
- 同名重複上傳 → 409；加 ?replace=true → 200
- dev 模式預設 is_admin=True（PHASE6 決策），所以 dev 測試走 admin path；
  非 admin 行為留給 prod 模式 test_auth.py 的 OAuth flow 覆蓋
- 不合法檔名（.mp3 / 落到 fallback parser） → 400
- 空檔案 → 400

audio_dir 透過 monkeypatch `app.state.audio_dir` 隔離到 tmp_path，
避免污染真正的 data/audio/。
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from sqlmodel import Session, select

from src import main as main_module
from src.models import AudioFile


def _wav_bytes(payload_size: int = 1024) -> bytes:
    """產生最簡 .wav header + N bytes silence 給上傳測試用。

    不需要可被 librosa 真的 decode — 上傳路徑不解析 audio，只寫檔 + scan。
    """
    # RIFF + WAVE + fmt chunk + data chunk header（44 bytes 標準 PCM header）
    # 內容隨便填 0；總 size = 44 + payload_size
    header = bytearray(44)
    header[0:4] = b"RIFF"
    header[4:8] = (36 + payload_size).to_bytes(4, "little")
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    header[16:20] = (16).to_bytes(4, "little")     # fmt chunk size
    header[20:22] = (1).to_bytes(2, "little")      # PCM
    header[22:24] = (1).to_bytes(2, "little")      # mono
    header[24:28] = (44100).to_bytes(4, "little")  # sample rate
    header[28:32] = (88200).to_bytes(4, "little")  # byte rate
    header[32:34] = (2).to_bytes(2, "little")      # block align
    header[34:36] = (16).to_bytes(2, "little")     # bits per sample
    header[36:40] = b"data"
    header[40:44] = payload_size.to_bytes(4, "little")
    return bytes(header) + b"\x00" * payload_size


@pytest.fixture
def upload_audio_dir(tmp_path: Path):
    """把 app.state.audio_dir override 到 tmp_path，測試結束還原。"""
    audio_dir = tmp_path / "audio_upload"
    audio_dir.mkdir()
    prev = getattr(main_module.app.state, "audio_dir", None)
    main_module.app.state.audio_dir = audio_dir
    yield audio_dir
    if prev is None:
        # 移除屬性 — 還原沒設定的狀態，避免影響其他 test
        try:
            delattr(main_module.app.state, "audio_dir")
        except AttributeError:
            pass
    else:
        main_module.app.state.audio_dir = prev


def test_upload_admin_uploads_valid_wav(client, upload_audio_dir, in_memory_engine):
    payload = _wav_bytes(2048)
    files = {
        "file": ("Volcano Goddess_Base Game.wav", io.BytesIO(payload), "audio/wav"),
    }
    r = client.post("/api/audio/upload?annotator=amber", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "Volcano Goddess_Base Game.wav"
    assert body["game_name"] == "Volcano Goddess"
    assert body["game_stage"] == "Base Game"
    assert body["is_brand_theme"] is False
    assert body["size_bytes"] == len(payload)
    assert body["added"] is True
    assert body["replaced"] is False
    assert isinstance(body["audio_id"], str) and body["audio_id"]

    # 檔案實際落地
    target = upload_audio_dir / "Volcano Goddess_Base Game.wav"
    assert target.exists()
    assert target.read_bytes() == payload

    # AudioFile row 入庫
    with Session(in_memory_engine) as s:
        rows = s.exec(select(AudioFile)).all()
        assert len(rows) == 1
        assert rows[0].filename == "Volcano Goddess_Base Game.wav"


def test_upload_admin_uploads_brand_theme_three_segment(client, upload_audio_dir):
    payload = _wav_bytes(512)
    files = {
        "file": (
            "Game Brand Theme Music_TestBrand_AI Virtual Voice.wav",
            io.BytesIO(payload),
            "audio/wav",
        ),
    }
    r = client.post("/api/audio/upload?annotator=amber", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["game_name"] == "Game Brand Theme Music"
    assert body["game_stage"] == "TestBrand (AI Virtual Voice)"
    assert body["is_brand_theme"] is True


def test_upload_duplicate_returns_409(client, upload_audio_dir):
    payload = _wav_bytes(512)
    files = {"file": ("Dragon Empire_Base Game.wav", io.BytesIO(payload), "audio/wav")}
    r1 = client.post("/api/audio/upload?annotator=amber", files=files)
    assert r1.status_code == 200, r1.text

    files2 = {"file": ("Dragon Empire_Base Game.wav", io.BytesIO(payload), "audio/wav")}
    r2 = client.post("/api/audio/upload?annotator=amber", files=files2)
    assert r2.status_code == 409
    assert "已存在" in r2.json()["detail"]


def test_upload_replace_true_overwrites(client, upload_audio_dir, in_memory_engine):
    payload_v1 = _wav_bytes(512)
    payload_v2 = _wav_bytes(1024)
    fname = "Phoenix Rise_Free Game.wav"

    r1 = client.post(
        "/api/audio/upload?annotator=amber",
        files={"file": (fname, io.BytesIO(payload_v1), "audio/wav")},
    )
    assert r1.status_code == 200
    target = upload_audio_dir / fname
    assert target.read_bytes() == payload_v1

    # 帶 ?replace=true 第二次應成功，原檔被覆蓋
    r2 = client.post(
        "/api/audio/upload?annotator=amber&replace=true",
        files={"file": (fname, io.BytesIO(payload_v2), "audio/wav")},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["replaced"] is True
    assert body["added"] is False
    assert body["size_bytes"] == len(payload_v2)
    assert target.read_bytes() == payload_v2

    # DB 仍只有一筆（scanner idempotent）
    with Session(in_memory_engine) as s:
        rows = s.exec(select(AudioFile)).all()
        assert len(rows) == 1


def test_upload_rejects_non_wav_extension(client, upload_audio_dir):
    payload = _wav_bytes(512)
    files = {"file": ("not_audio.mp3", io.BytesIO(payload), "audio/mpeg")}
    r = client.post("/api/audio/upload?annotator=amber", files=files)
    assert r.status_code == 400
    assert ".wav" in r.json()["detail"]


def test_upload_rejects_bad_filename_falls_to_parser_fallback(client, upload_audio_dir):
    """檔名沒有已知 stage 結尾、也不是品牌主題曲 → parser fallback → 400 + hint。"""
    payload = _wav_bytes(512)
    files = {"file": ("randomthing.wav", io.BytesIO(payload), "audio/wav")}
    r = client.post("/api/audio/upload?annotator=amber", files=files)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "格式" in detail or "Stage" in detail


def test_upload_rejects_bad_filename_unknown_stage(client, upload_audio_dir):
    """兩段式但 stage 不在 KNOWN_STAGES → 400。"""
    payload = _wav_bytes(512)
    files = {"file": ("Some Game_Tutorial Mode.wav", io.BytesIO(payload), "audio/wav")}
    r = client.post("/api/audio/upload?annotator=amber", files=files)
    assert r.status_code == 400


def test_upload_rejects_empty_file(client, upload_audio_dir):
    files = {"file": ("Empty Game_Base Game.wav", io.BytesIO(b""), "audio/wav")}
    r = client.post("/api/audio/upload?annotator=amber", files=files)
    assert r.status_code == 400
    assert "空" in r.json()["detail"]


def test_upload_rejects_path_traversal_filename(client, upload_audio_dir):
    """前端理論上不會送，但後端要擋帶路徑成分的 filename。"""
    payload = _wav_bytes(512)
    # `_` 切分後 stage 是 "Base Game" 但檔名含路徑成分
    files = {"file": ("../escaped_Base Game.wav", io.BytesIO(payload), "audio/wav")}
    r = client.post("/api/audio/upload?annotator=amber", files=files)
    # FastAPI / Starlette 的 UploadFile.filename 會把 "../" 保留；handler 自己擋
    # 接受 400（檔名不合法）作為通過條件
    assert r.status_code == 400


def test_upload_brand_theme_missing_brand_returns_400(client, upload_audio_dir):
    """`Game Brand Theme Music_.wav` 缺品牌 → parser fallback 訊息。"""
    payload = _wav_bytes(512)
    files = {"file": ("Game Brand Theme Music_.wav", io.BytesIO(payload), "audio/wav")}
    r = client.post("/api/audio/upload?annotator=amber", files=files)
    assert r.status_code == 400


# ── 非 admin 行為 ──
# dev 模式（OAUTH_ENABLED=false）刻意把所有 dev user 都當 admin（PHASE6 決策），
# 因此 dev mode 下不會有「合法登入但非 admin」的情境。
# 真正的「non-admin 403」測試屬 prod / OAuth flow，已由 test_auth.py 的
# `test_prod_non_whitelisted_email_returns_403` 等 fixture 覆蓋。
# 這裡用 monkeypatch 把 require_auth override 成回 is_admin=False，
# 直接驗 endpoint 的 403 分支，保證 dev → prod 切換時 admin gate 仍生效。

def test_upload_non_admin_returns_403(client, upload_audio_dir):
    from src.middleware import require_auth

    def _fake_non_admin():
        return {
            "annotator_id": "intern",
            "email": "intern@example.com",
            "is_admin": False,
            "name": "Intern",
        }

    main_module.app.dependency_overrides[require_auth] = _fake_non_admin
    try:
        payload = _wav_bytes(512)
        files = {"file": ("Whatever_Base Game.wav", io.BytesIO(payload), "audio/wav")}
        r = client.post("/api/audio/upload", files=files)
        assert r.status_code == 403
        assert "admin" in r.json()["detail"]
    finally:
        main_module.app.dependency_overrides.pop(require_auth, None)
