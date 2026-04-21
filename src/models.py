"""SQLModel 資料表定義。

Phase 1 就把所有 Phase 2 需要的欄位都到位，避免後續 schema migration。
連續維度存 Optional[float]，離散 loop_capability 也用 float 便於 aggregation。
多選欄位（function_roles / style_tag）存 JSON-serialized 字串，由 application 層負責 (de)serialize。
"""
from datetime import datetime, UTC
from typing import Optional
import uuid

from sqlmodel import SQLModel, Field
import sqlalchemy as sa


def _utcnow() -> datetime:
    """timezone-aware UTC now（取代 deprecated 的 datetime.utcnow）。"""
    return datetime.now(UTC)


class AudioFile(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    filename: str = Field(index=True, unique=True)
    game_name: str
    game_stage: str
    # librosa 分析結果；Phase 1 先留 None，Phase 2 由 audio_analysis 填
    duration_sec: Optional[float] = None
    bpm: Optional[float] = None
    sample_rate: Optional[int] = None
    # Phase 2 的 auto-compute 快取：避免每次開啟標註頁重算 librosa
    tonal_noise_ratio_auto: Optional[float] = None
    spectral_density_auto: Optional[float] = None
    # 三段式品牌主題曲 flag：temporal_position 的 filename auto-suggest 要跳過這些
    is_brand_theme: bool = Field(default=False)
    discovered_at: datetime = Field(default_factory=_utcnow)


class Annotation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    audio_file_id: str = Field(foreign_key="audiofile.id", index=True)
    annotator_id: str = Field(index=True)

    # 10 維度
    valence: Optional[float] = None
    arousal: Optional[float] = None
    emotional_warmth: Optional[float] = None
    tension_direction: Optional[float] = None
    temporal_position: Optional[float] = None
    event_significance: Optional[float] = None
    # loop_capability 離散三選一 {0.0, 0.5, 1.0}，仍用 float 儲存
    loop_capability: Optional[float] = None
    tonal_noise_ratio: Optional[float] = None
    spectral_density: Optional[float] = None
    world_immersion: Optional[float] = None

    # Layer 1 / Layer 2
    source_type: Optional[str] = None
    # function_roles 多選 → JSON-serialized list[str]，SQLite 無原生陣列型別
    function_roles: Optional[str] = None

    # 離散 tags
    genre_tag: Optional[str] = None
    worldview_tag: Optional[str] = None
    # style_tag 多選 → JSON-serialized list[str]
    style_tag: Optional[str] = None
    notes: Optional[str] = None

    # is_complete 由 POST /api/annotations 計算；Phase 4 export 只匯出 True
    is_complete: bool = Field(default=False)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # 同一 (audio, annotator) 只能有一筆：upsert 時靠這個約束
    __table_args__ = (
        sa.UniqueConstraint("audio_file_id", "annotator_id", name="uq_audio_annotator"),
    )


class TagSuggestion(SQLModel, table=True):
    """autocomplete 用的動態 tag 建議池。

    Phase 2 使用者輸入新 tag 時寫入這張表，sort by use_count desc 回傳給前端。
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    field: str = Field(index=True)  # "genre" / "worldview" / "style"
    value: str = Field(index=True)
    use_count: int = Field(default=1)

    __table_args__ = (
        sa.UniqueConstraint("field", "value", name="uq_field_value"),
    )


class DimensionFeedback(SQLModel, table=True):
    """Phase 5 #3：Amber 對每個維度定義的結構化回饋。

    與 Annotation 無 FK 關聯（軟關聯 audio+annotator）— 兩個獨立生命週期。
    Aaron 看 summary 後決定改哪個維度的定義文字。
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    audio_file_id: str = Field(foreign_key="audiofile.id", index=True)
    annotator_id: str = Field(index=True)
    dimension_key: str = Field(index=True)  # e.g. "emotional_warmth"
    feedback_type: str                       # "clear" / "vague" / "misaligned" / "note"
    note_text: Optional[str] = None          # 僅 feedback_type="note" 時填
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    __table_args__ = (
        sa.UniqueConstraint(
            "audio_file_id", "annotator_id", "dimension_key",
            name="uq_feedback_audio_annotator_dim",
        ),
    )
