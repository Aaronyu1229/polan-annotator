"""音檔相關 API：
- GET  /api/audio                 列表（含 duration 與當前 annotator 的完成狀態）
- GET  /api/audio/{id}            單筆詳細（含 auto_computed、existing_annotation）
- GET  /api/audio/{id}/stream     串流 .wav 給 WaveSurfer
- POST /api/audio/upload          admin-only 上傳新音檔（Phase 6）

Phase 2 的擴充：list 回傳增加 `is_annotated_by_current_annotator` + `duration_sec`；
single 回傳增加 `auto_computed` dict 與 `existing_annotation`。
音訊分析結果會 cache 到 DB（首次開啟時算，之後直接讀）。

Phase 6 的擴充：upload 端點 — admin（依 `is_admin` flag）才能上傳；
檔名必須符合 `parse_audio_filename` 的兩段式或三段式規則；
寫檔走 .tmp + rename 確保原子性；寫完後 rescan 把新檔 upsert 進 AudioFile table。
"""
from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from src.audio_analysis import AUDIO_DIR, ensure_cached
from src.audio_scanner import SUPPORTED_EXTS, scan_audio_directory
from src.constants import KNOWN_STAGES, parse_audio_filename
from src.db import get_session
from src.middleware import optional_annotator, require_auth
from src.models import Annotation, AudioFile, DimensionFeedback

router = APIRouter(prefix="/api", tags=["audio"])
log = logging.getLogger("polan.routes.audio")

# 100 MB — 與 nginx `client_max_body_size` 對齊
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# 上傳時為避免半寫入的破檔被列表掃到，先寫到 .tmp 再 rename
_TMP_SUFFIX = ".uploading.tmp"


def _annotation_to_dict(ann: Annotation) -> dict[str, Any]:
    """把 Annotation row 轉成前端 prefill 用的 dict。多選欄位 JSON-decode。"""
    def _decode_list(s: Optional[str]) -> list:
        if not s:
            return []
        try:
            value = json.loads(s)
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            return []

    return {
        "id": ann.id,
        "annotator_id": ann.annotator_id,
        "valence": ann.valence,
        "arousal": ann.arousal,
        "emotional_warmth": ann.emotional_warmth,
        "tension_direction": ann.tension_direction,
        "temporal_position": ann.temporal_position,
        "event_significance": ann.event_significance,
        "loop_capability": _decode_list(ann.loop_capability),
        "tonal_noise_ratio": ann.tonal_noise_ratio,
        "spectral_density": ann.spectral_density,
        "world_immersion": ann.world_immersion,
        "source_type": _decode_list(ann.source_type),
        "function_roles": _decode_list(ann.function_roles),
        "genre_tag": _decode_list(ann.genre_tag),
        "worldview_tag": ann.worldview_tag,
        "style_tag": _decode_list(ann.style_tag),
        "notes": ann.notes,
        "is_complete": ann.is_complete,
        "updated_at": ann.updated_at.isoformat() if ann.updated_at else None,
    }


@router.get("/audio")
def list_audio(
    annotator: Optional[str] = Depends(optional_annotator),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """回傳所有音檔，含 duration 與 is_annotated_by_current_annotator 旗標。

    只有對應 annotator 已存在且 is_complete=True 的 record 才算「已標」 —
    半成品（is_complete=False）仍視為未標，使列表頁的 ✓ 旗標精準反映「完成」狀態。
    """
    audios = session.exec(
        select(AudioFile).order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()

    completed_audio_ids: set[str] = set()
    if annotator:
        completed = session.exec(
            select(Annotation.audio_file_id).where(
                Annotation.annotator_id == annotator,
                Annotation.is_complete == True,  # noqa: E712 — SQLModel 不允許 is True
            )
        ).all()
        completed_audio_ids = set(completed)

    return [
        {
            "id": a.id,
            "filename": a.filename,
            "game_name": a.game_name,
            "game_stage": a.game_stage,
            "is_brand_theme": a.is_brand_theme,
            "duration_sec": a.duration_sec,
            "is_annotated_by_current_annotator": a.id in completed_audio_ids,
        }
        for a in audios
    ]


@router.get("/audio/{audio_id}")
def get_audio(
    audio_id: str,
    annotator: Optional[str] = Depends(optional_annotator),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """回單一音檔詳細 — 含 auto_computed 建議值與當前 annotator 既有標註（if any）。

    首次開啟會觸發 librosa 分析並 cache 到 DB，之後讀 cache。
    """
    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔：{audio_id}")

    # 首次開啟時算 librosa 並 cache；失敗時 auto 欄位仍為 None，UI 顯示 N/A
    try:
        audio = ensure_cached(session, audio)
    except Exception as e:  # noqa: BLE001
        log.warning("ensure_cached 失敗（%s）：%s", audio.filename, e)

    existing_annotation: Optional[dict[str, Any]] = None
    if annotator:
        ann = session.exec(
            select(Annotation).where(
                Annotation.audio_file_id == audio_id,
                Annotation.annotator_id == annotator,
            )
        ).first()
        if ann is not None:
            existing_annotation = _annotation_to_dict(ann)

    return {
        "id": audio.id,
        "filename": audio.filename,
        "game_name": audio.game_name,
        "game_stage": audio.game_stage,
        "is_brand_theme": audio.is_brand_theme,
        "duration_sec": audio.duration_sec,
        "bpm": audio.bpm,
        "sample_rate": audio.sample_rate,
        "auto_computed": {
            "tonal_noise_ratio": audio.tonal_noise_ratio_auto,
            "spectral_density": audio.spectral_density_auto,
        },
        "existing_annotation": existing_annotation,
    }


@router.get("/audio/{audio_id}/stream")
def stream_audio(
    audio_id: str,
    session: Session = Depends(get_session),
) -> FileResponse:
    """串 .wav 給 WaveSurfer。不直接暴露 data/audio/ 目錄，經 id 查 filename 再 serve。"""
    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔：{audio_id}")

    file_path = AUDIO_DIR / audio.filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"音檔檔案不存在：{audio.filename}")

    # Phase 6：依副檔名給正確 Content-Type（mp3 / ogg / m4a / flac），
    # 否則 WaveSurfer 在某些瀏覽器會拒絕 stream。
    media_type, _ = mimetypes.guess_type(audio.filename)
    if not media_type or not media_type.startswith("audio/"):
        media_type = "audio/wav"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=audio.filename,
    )


# ─── Phase 6：admin upload ──────────────────────────────────

def _validate_filename_format(filename: str) -> tuple[dict[str, Any], str | None]:
    """跑 parser 並回傳結果。

    Phase 6 後實際檔案命名規則已大幅放寬（音效類檔名如 `countDown_ai.mp3`、
    `crazyBus_2.mp3` 不符合原本 BGM 兩段式 / 三段式格式，但仍要能上傳）。

    parser 本身有 fallback 邏輯能處理任意檔名（case 3：split 後保留 head/tail），
    這裡只擋一個極端 case：三段式品牌主題曲前綴但缺品牌名 — 這明顯是手誤。
    """
    parsed = parse_audio_filename(filename)
    if parsed["is_brand_theme"] and parsed["game_stage"].startswith("Unknown"):
        return parsed, (
            "三段式品牌主題曲缺品牌名。"
            "格式：Game Brand Theme Music_{品牌}_AI Virtual Voice.{副檔名}"
        )
    return parsed, None


def _safe_target_path(audio_dir: Path, filename: str) -> Path:
    """確認 filename 不含路徑成分，避免 path traversal。"""
    if "/" in filename or "\\" in filename or filename in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="檔名不合法")
    target = audio_dir / filename
    # resolve 後仍須在 audio_dir 之下
    try:
        target.resolve().relative_to(audio_dir.resolve())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="檔名不合法") from e
    return target


@router.post("/audio/upload")
async def upload_audio(
    request: Request,
    file: UploadFile = File(...),
    replace: bool = False,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """admin-only 上傳新 .wav。

    流程：
    1. 確認 admin
    2. 驗 content-type / 副檔名 / 檔名 parser
    3. 驗大小（讀完串流時 enforce）
    4. 寫到 `<filename>.uploading.tmp` 然後 atomic rename
    5. 呼叫 scan_audio_directory upsert AudioFile row
    """
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要 admin 權限")

    # 取出 audio_dir：優先用 app.state.audio_dir（測試 override 用），否則走預設
    audio_dir: Path = getattr(request.app.state, "audio_dir", AUDIO_DIR)
    audio_dir.mkdir(parents=True, exist_ok=True)

    raw_name = file.filename or ""
    suffix = Path(raw_name).suffix.lower()
    if suffix not in SUPPORTED_EXTS:
        accepted = " / ".join(sorted(SUPPORTED_EXTS))
        raise HTTPException(
            status_code=400,
            detail=f"副檔名 {suffix or '(無)'} 不支援，僅接受：{accepted}",
        )

    content_type = (file.content_type or "").lower()
    # 部分瀏覽器把 .wav 標成 audio/x-wav / audio/wave / audio/vnd.wave；
    # 嚴格但寬容：開頭是 audio/ 即可，其餘交給 parser + 副檔名擋
    if content_type and not content_type.startswith("audio/"):
        raise HTTPException(
            status_code=400,
            detail=f"Content-Type 不合法（{content_type}），需為 audio/*",
        )

    parsed, fmt_error = _validate_filename_format(raw_name)
    if fmt_error is not None:
        raise HTTPException(status_code=400, detail=fmt_error)

    target_path = _safe_target_path(audio_dir, raw_name)
    file_existed = target_path.exists()
    if file_existed and not replace:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"檔案已存在：{raw_name}（加 ?replace=true 可覆蓋）",
        )

    tmp_path = target_path.with_name(target_path.name + _TMP_SUFFIX)
    bytes_written = 0
    chunk_size = 1024 * 1024  # 1 MB
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"檔案過大（已超過 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限）"
                        ),
                    )
                out.write(chunk)
        if bytes_written == 0:
            raise HTTPException(status_code=400, detail="檔案內容為空")
        # atomic rename — 同 filesystem 上 os.replace 會 atomic
        tmp_path.replace(target_path)
    except HTTPException:
        # 清理半寫入的 .tmp
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError as e:  # noqa: BLE001 — 清理失敗不應掩蓋原 error
                log.warning("清理 .tmp 失敗（%s）：%s", tmp_path, e)
        raise
    except OSError as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        log.exception("寫入音檔失敗：%s", target_path)
        raise HTTPException(status_code=500, detail=f"寫入音檔失敗：{e}") from e
    finally:
        await file.close()

    # 寫完後 rescan：把新檔 upsert 進 DB（idempotent，已存在的會 skip）
    try:
        scan_audio_directory(session, audio_dir=audio_dir)
    except (FileNotFoundError, NotADirectoryError) as e:
        log.exception("upload 後 rescan 失敗：%s", e)
        raise HTTPException(status_code=500, detail=f"rescan 失敗：{e}") from e

    audio_row = session.exec(
        select(AudioFile).where(AudioFile.filename == raw_name)
    ).first()
    if audio_row is None:
        # 不該發生 — 寫檔成功 + scan 後仍找不到
        raise HTTPException(status_code=500, detail="upload 後找不到對應 AudioFile row")

    return {
        "audio_id": audio_row.id,
        "filename": audio_row.filename,
        "game_name": audio_row.game_name,
        "game_stage": audio_row.game_stage,
        "is_brand_theme": audio_row.is_brand_theme,
        "size_bytes": bytes_written,
        "added": not file_existed,
        "replaced": file_existed,
    }


# ─── Phase 6：admin delete ──────────────────────────────────


@router.delete("/audio/{audio_id}")
def delete_audio(
    audio_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """admin-only 刪除音檔。

    一併刪除：
    - 磁碟上的音檔
    - AudioFile row
    - 該音檔所有 Annotation rows（手動級聯，SQLite 不強制 FK CASCADE）
    - 該音檔所有 DimensionFeedback rows
    """
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要 admin 權限")

    audio = session.get(AudioFile, audio_id)
    if audio is None:
        raise HTTPException(status_code=404, detail=f"找不到音檔：{audio_id}")

    audio_dir: Path = getattr(request.app.state, "audio_dir", AUDIO_DIR)
    file_path = audio_dir / audio.filename

    n_annotations = session.exec(
        select(Annotation).where(Annotation.audio_file_id == audio_id)
    ).all()
    n_feedback = session.exec(
        select(DimensionFeedback).where(DimensionFeedback.audio_file_id == audio_id)
    ).all()

    for ann in n_annotations:
        session.delete(ann)
    for fb in n_feedback:
        session.delete(fb)
    session.delete(audio)
    session.commit()

    file_removed = False
    if file_path.exists():
        try:
            file_path.unlink()
            file_removed = True
        except OSError as e:
            log.warning("刪除音檔磁碟檔案失敗（%s）：%s — DB row 已刪", file_path, e)

    return {
        "audio_id": audio_id,
        "filename": audio.filename,
        "annotations_deleted": len(n_annotations),
        "feedback_deleted": len(n_feedback),
        "file_removed": file_removed,
    }
