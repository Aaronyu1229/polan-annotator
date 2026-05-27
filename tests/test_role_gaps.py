from __future__ import annotations

import pytest

from src.models import Annotation
from src.role_gaps import pairwise_gaps, needs_full_arbitration


def _approx(x):
    return pytest.approx(x, abs=1e-9)


def _ann(**dims) -> Annotation:
    return Annotation(audio_file_id="a", annotator_id="x", **dims)


def test_three_way_gaps_all_present():
    by_role = {
        "creator": _ann(valence=0.5),
        "industry": _ann(valence=0.6),
        "audience": _ann(valence=0.9),
    }
    g = pairwise_gaps(by_role)
    assert g["valence"]["creator_industry"] == _approx(0.1)
    assert g["valence"]["creator_audience"] == _approx(0.4)
    assert g["valence"]["industry_audience"] == _approx(0.3)


def test_missing_side_yields_none():
    by_role = {"creator": _ann(valence=0.5), "industry": None, "audience": None}
    g = pairwise_gaps(by_role)
    assert g["valence"]["creator_industry"] is None
    assert g["valence"]["creator_audience"] is None


def test_needs_full_arbitration_only_creator_industry_over_gate():
    # creator-industry 0.25 > 0.20 → full；audience 偏離 0.6 不影響
    by_role = {
        "creator": _ann(valence=0.5, arousal=0.5),
        "industry": _ann(valence=0.75, arousal=0.55),
        "audience": _ann(valence=0.99, arousal=0.99),
    }
    g = pairwise_gaps(by_role)
    assert needs_full_arbitration(g) == {"valence"}  # arousal gap 0.05 ≤ 0.20；audience 不算


def test_boundary_equal_gate_is_fast():
    by_role = {"creator": _ann(valence=0.5), "industry": _ann(valence=0.7),
               "audience": None}
    g = pairwise_gaps(by_role)  # gap = 0.20 exactly
    assert needs_full_arbitration(g) == set()  # ≤ gate → fast
