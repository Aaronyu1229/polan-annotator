"""librosa 驅動的音訊分析：計算 duration、bpm、tonal_noise_ratio、spectral_density。

結果 cache 到 AudioFile 的 auto 欄位，避免每次開啟標註頁都重算。

計算失敗（librosa ImportError / 檔案壞掉 / 無法判讀）時，回 None，不 raise。
由 caller 決定要不要寫 log。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlmodel import Session

from src.db import PROJECT_ROOT
from src.models import AudioFile

log = logging.getLogger("polan.audio_analysis")

AUDIO_DIR = PROJECT_ROOT / "data" / "audio"


@dataclass(frozen=True)
class AnalysisResult:
    """librosa 算完後的摘要，全部欄位可為 None（計算失敗時）。"""
    duration_sec: Optional[float]
    bpm: Optional[float]
    sample_rate: Optional[int]
    tonal_noise_ratio: Optional[float]
    spectral_density: Optional[float]


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """把值夾到 [low, high]。"""
    return max(low, min(high, value))


def analyze_file(audio_path: Path) -> AnalysisResult:
    """讀檔用 librosa 算 5 個欄位；任何錯誤都 graceful fallback 到 None。

    spectral_density 用三個指標平均：
    - spectral_centroid / (sr/2)  — 頻譜重心相對 Nyquist 的比例
    - zero_crossing_rate          — 零穿越率（越高越 noisy / 高頻成分越多）
    - onset_density / 10          — 每秒 onset 事件數，normalize 以 10 events/sec 為上限
    """
    try:
        import librosa
        import numpy as np
    except ImportError as e:
        log.warning("librosa 未安裝，跳過分析：%s", e)
        return AnalysisResult(None, None, None, None, None)

    if not audio_path.exists():
        log.warning("音檔不存在：%s", audio_path)
        return AnalysisResult(None, None, None, None, None)

    try:
        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    except (OSError, ValueError, RuntimeError) as e:
        log.warning("librosa 載入失敗（%s）：%s", audio_path.name, e)
        return AnalysisResult(None, None, None, None, None)

    if y.size == 0 or sr is None or sr <= 0:
        log.warning("音檔空白或 sr 異常：%s", audio_path.name)
        return AnalysisResult(None, None, None, None, None)

    duration_sec: Optional[float] = None
    bpm: Optional[float] = None
    tnr: Optional[float] = None
    density: Optional[float] = None

    try:
        duration_sec = float(librosa.get_duration(y=y, sr=sr))
    except Exception as e:  # noqa: BLE001 — librosa 會丟多種型別
        log.warning("get_duration 失敗（%s）：%s", audio_path.name, e)

    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm_value = float(np.atleast_1d(tempo)[0])
        bpm = bpm_value if bpm_value > 0 else None
    except Exception as e:  # noqa: BLE001
        log.warning("beat_track 失敗（%s）：%s", audio_path.name, e)

    try:
        flatness = librosa.feature.spectral_flatness(y=y).mean()
        tnr = _clip(1.0 - float(flatness))
    except Exception as e:  # noqa: BLE001
        log.warning("spectral_flatness 失敗（%s）：%s", audio_path.name, e)

    try:
        density = _compute_spectral_density(y, sr, duration_sec)
    except Exception as e:  # noqa: BLE001
        log.warning("spectral_density 失敗（%s）：%s", audio_path.name, e)

    return AnalysisResult(
        duration_sec=duration_sec,
        bpm=bpm,
        sample_rate=int(sr),
        tonal_noise_ratio=tnr,
        spectral_density=density,
    )


def _compute_spectral_density(y, sr: int, duration_sec: Optional[float]) -> float:
    """combined metric ∈ [0, 1]：centroid + ZCR + onset density 的加權平均。"""
    import librosa
    import numpy as np

    # spectral centroid：normalize 到 [0,1]，用 sr/2 當上限（Nyquist）
    centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
    centroid_norm = _clip(centroid / (sr / 2.0))

    zcr = float(librosa.feature.zero_crossing_rate(y=y).mean())
    zcr_norm = _clip(zcr)

    # onset density：每秒 onset 事件，以 10 events/sec 為「滿密度」上限
    if duration_sec and duration_sec > 0:
        onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
        onset_per_sec = len(onsets) / duration_sec
        onset_norm = _clip(onset_per_sec / 10.0)
    else:
        onset_norm = 0.0

    # 權重：centroid 0.4（頻譜能量分佈）、zcr 0.3（高頻率 / 噪度）、onset 0.3（時間密度）
    combined = 0.4 * centroid_norm + 0.3 * zcr_norm + 0.3 * onset_norm
    return _clip(combined)


def ensure_cached(session: Session, audio: AudioFile, audio_dir: Path = AUDIO_DIR) -> AudioFile:
    """若 audio 的 auto 欄位尚未填，用 librosa 算一次並 commit。

    已填過的欄位不重算 — 判斷條件是 duration_sec、tonal_noise_ratio_auto、spectral_density_auto
    全不為 None。任一為 None 則整組重算（librosa load 只跑一次，成本集中在這裡）。
    """
    already_cached = (
        audio.duration_sec is not None
        and audio.tonal_noise_ratio_auto is not None
        and audio.spectral_density_auto is not None
    )
    if already_cached:
        return audio

    audio_path = audio_dir / audio.filename
    result = analyze_file(audio_path)

    if result.duration_sec is not None:
        audio.duration_sec = result.duration_sec
    if result.bpm is not None:
        audio.bpm = result.bpm
    if result.sample_rate is not None:
        audio.sample_rate = result.sample_rate
    if result.tonal_noise_ratio is not None:
        audio.tonal_noise_ratio_auto = result.tonal_noise_ratio
    if result.spectral_density is not None:
        audio.spectral_density_auto = result.spectral_density

    session.add(audio)
    session.commit()
    session.refresh(audio)
    return audio
