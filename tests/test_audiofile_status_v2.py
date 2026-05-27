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


def test_audience_only_is_generic_draft():
    assert _call([_ann("vic", valence=0.5)]) == "draft"


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
    creator = _ann("amber", valence=0.5)
    creator.updated_at = t
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
