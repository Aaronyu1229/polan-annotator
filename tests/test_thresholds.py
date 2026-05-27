from src import thresholds


def test_thresholds_present_and_ordered():
    assert thresholds.ARBITRATION_GATE == 0.20
    assert thresholds.INDUSTRY_RECAL == 0.30
    assert thresholds.PRODUCT_DIVERGENCE == 0.40
    # 邏輯排序：仲裁 gate < industry 校準警示 < 商品分歧
    assert thresholds.ARBITRATION_GATE < thresholds.INDUSTRY_RECAL < thresholds.PRODUCT_DIVERGENCE


def test_human_continuous_dims():
    assert len(thresholds.HUMAN_CONTINUOUS_DIMS) == 7
    assert "valence" in thresholds.HUMAN_CONTINUOUS_DIMS
    assert "tonal_noise_ratio" not in thresholds.HUMAN_CONTINUOUS_DIMS
