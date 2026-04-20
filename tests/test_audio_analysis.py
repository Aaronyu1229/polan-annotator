"""audio_analysis 單元測試：用 real WAV 檔案跑一次 end-to-end。

librosa 的計算結果不需精確斷言（會因版本而變），只驗：
- 計算成功時欄位都有值且在合理範圍
- 檔案不存在時 graceful 回 None
- ensure_cached 不重算已 cache 的 record
"""
from pathlib import Path

import pytest
from sqlmodel import Session, select

from src.audio_analysis import analyze_file, ensure_cached
from src.models import AudioFile


REAL_AUDIO_DIR = Path(__file__).resolve().parent.parent / "data" / "audio"
SAMPLE_FILENAME = "Volcano Goddess_Base Game.wav"


def _has_sample_audio() -> bool:
    return (REAL_AUDIO_DIR / SAMPLE_FILENAME).exists()


@pytest.mark.skipif(not _has_sample_audio(), reason="data/audio 沒有樣本音檔，跳過整合測試")
def test_analyze_real_file_returns_sensible_values():
    result = analyze_file(REAL_AUDIO_DIR / SAMPLE_FILENAME)
    assert result.duration_sec is not None and result.duration_sec > 0
    assert result.sample_rate is not None and result.sample_rate > 0
    assert result.tonal_noise_ratio is not None
    assert 0.0 <= result.tonal_noise_ratio <= 1.0
    assert result.spectral_density is not None
    assert 0.0 <= result.spectral_density <= 1.0


def test_analyze_nonexistent_file_graceful():
    result = analyze_file(Path("/tmp/does-not-exist-xyz.wav"))
    assert result.duration_sec is None
    assert result.tonal_noise_ratio is None
    assert result.spectral_density is None


@pytest.mark.skipif(not _has_sample_audio(), reason="data/audio 沒有樣本音檔，跳過整合測試")
def test_ensure_cached_writes_and_skips_second_run(in_memory_engine):
    with Session(in_memory_engine) as session:
        audio = AudioFile(
            filename=SAMPLE_FILENAME,
            game_name="Volcano Goddess",
            game_stage="Base Game",
        )
        session.add(audio)
        session.commit()
        session.refresh(audio)

        ensure_cached(session, audio, audio_dir=REAL_AUDIO_DIR)
        assert audio.duration_sec is not None
        assert audio.tonal_noise_ratio_auto is not None
        assert audio.spectral_density_auto is not None

        # 第二次呼叫應 no-op — 用「竄改 duration_sec 一個極端值」驗證沒重算
        audio.duration_sec = 9999.0
        session.add(audio)
        session.commit()
        ensure_cached(session, audio, audio_dir=REAL_AUDIO_DIR)
        reloaded = session.exec(
            select(AudioFile).where(AudioFile.filename == SAMPLE_FILENAME)
        ).one()
        assert reloaded.duration_sec == 9999.0  # 未被覆寫
