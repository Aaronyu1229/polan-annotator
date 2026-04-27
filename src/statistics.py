"""ICC (Intraclass Correlation Coefficient) 計算。

跨標註員一致性的標準度量。本實作使用 ICC(2,1):
  - Two-way random effects（標註員與 item 都視為隨機效應）
  - Single rater（一位標註員的分數，不是平均）
  - Absolute agreement（要求數值上一致，不只是 ranking 一致）

參考：Shrout & Fleiss (1979). Intraclass correlations: Uses in assessing
rater reliability. Psychological Bulletin, 86(2), 420-428.

使用範例：
    >>> import numpy as np
    >>> from src.statistics import icc_2_1
    >>> ratings = np.array([[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]])
    >>> icc_2_1(ratings)  # 完美一致
    1.0
"""
from __future__ import annotations

from typing import Optional

import numpy as np


def icc_2_1(ratings: np.ndarray) -> Optional[float]:
    """Compute ICC(2,1) for a (n_subjects x n_raters) matrix.

    Args:
        ratings: shape (n, k) where n=items, k=raters.
                 No NaN allowed; caller must filter.

    Returns:
        ICC value (typically in [-1, 1]) or None if computation infeasible
        (n < 2 or k < 2 or zero variance / zero denominator).
    """
    ratings = np.asarray(ratings, dtype=float)
    if ratings.ndim != 2:
        return None

    n, k = ratings.shape
    if n < 2 or k < 2:
        return None

    mean_per_subject = ratings.mean(axis=1)
    mean_per_rater = ratings.mean(axis=0)
    grand_mean = ratings.mean()

    # Sum of squares
    ss_subjects = k * np.sum((mean_per_subject - grand_mean) ** 2)
    ss_raters = n * np.sum((mean_per_rater - grand_mean) ** 2)
    ss_total = np.sum((ratings - grand_mean) ** 2)
    ss_error = ss_total - ss_subjects - ss_raters

    # Mean squares
    ms_subjects = ss_subjects / (n - 1)
    ms_raters = ss_raters / (k - 1)
    ms_error = ss_error / ((n - 1) * (k - 1))

    # ICC(2,1) formula
    denom = ms_subjects + (k - 1) * ms_error + k * (ms_raters - ms_error) / n
    if denom == 0:
        return None

    icc = (ms_subjects - ms_error) / denom
    return float(icc)
