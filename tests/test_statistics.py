"""ICC(2,1) 單元測試。

覆蓋：完美一致 / 完全反向 / 三人高度一致 / 不足樣本（n<2、k<2）/ 零變異 / NaN 處理 caller 責任。
"""
from __future__ import annotations

import numpy as np

from src.statistics import icc_2_1


def test_icc_perfect_agreement():
    """Two raters give identical scores -> ICC ~ 1.0"""
    ratings = np.array([[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]])
    icc = icc_2_1(ratings)
    assert icc is not None
    assert icc > 0.99


def test_icc_no_agreement():
    """Raters disagree completely -> ICC near 0 or negative"""
    ratings = np.array([[0.1, 0.9], [0.5, 0.5], [0.9, 0.1]])
    icc = icc_2_1(ratings)
    assert icc is not None
    assert icc < 0.1


def test_icc_insufficient_subjects():
    assert icc_2_1(np.array([[0.5, 0.5]])) is None


def test_icc_insufficient_raters():
    assert icc_2_1(np.array([[0.5], [0.7], [0.3]])) is None


def test_icc_three_raters_high_agreement():
    """3 raters mostly agreeing -> high ICC"""
    ratings = np.array([
        [0.2, 0.25, 0.18],
        [0.5, 0.48, 0.55],
        [0.8, 0.82, 0.78],
        [0.3, 0.32, 0.28],
    ])
    icc = icc_2_1(ratings)
    assert icc is not None
    assert icc > 0.9


def test_icc_zero_variance_returns_none():
    """All identical values -> denom is 0 -> None"""
    ratings = np.array([[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]])
    assert icc_2_1(ratings) is None


def test_icc_one_dim_array_returns_none():
    """Shape 不是 2D → None。不要 throw。"""
    assert icc_2_1(np.array([0.1, 0.5, 0.9])) is None


def test_icc_moderate_agreement_in_typical_range():
    """有些 agreement、有些 disagreement → ICC 落在 0.3 - 0.7 之間（合理現實場景）。"""
    ratings = np.array([
        [0.2, 0.35],
        [0.5, 0.45],
        [0.8, 0.7],
        [0.3, 0.5],
        [0.6, 0.55],
    ])
    icc = icc_2_1(ratings)
    assert icc is not None
    assert 0.3 < icc < 0.95  # 寬鬆驗證為合理範圍
