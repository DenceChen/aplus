"""交叉校验测试：TDD RED 先行。

设计依据（调研报告 4.1/4.4 节）：
- 跨源实测：成交量/成交额 100% 一致 → 严格比对
- 收盘价 0.58% 差异来自 qfq 刷新窗（本项目存不复权，理论上应一致）→ 小相对容差
- 单源合理性：high>=low、high>=open/close、价格量为正
- 不达标 → 隔离，不进主库
"""
import pandas as pd

from aplus.crosscheck import compare_frames, sanity_check


def _frame(rows):
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])


IDENTICAL = [
    {"date": "2026-01-05", "open": 10.0, "high": 10.8, "low": 9.9, "close": 10.5, "volume": 1_000_000.0, "amount": 10_500_000.0},
    {"date": "2026-01-06", "open": 10.5, "high": 11.0, "low": 10.2, "close": 10.8, "volume": 800_000.0, "amount": 8_640_000.0},
]


def test_identical_frames_pass():
    report = compare_frames(_frame(IDENTICAL), _frame(IDENTICAL))
    assert report.passed is True
    assert report.mismatch_ratio == 0.0
    assert report.common_dates == 2


def test_volume_mismatch_fails_strictly():
    """成交量跨源应 100% 一致——超容差即记 mismatch。"""
    b = _frame(IDENTICAL)
    b.loc[0, "volume"] = 1_200_000.0  # +20%，远超容差
    report = compare_frames(_frame(IDENTICAL), b)
    assert report.passed is False
    assert report.field_mismatches["volume"] == 1


def test_tiny_price_diff_within_tolerance_passes():
    """浮点级别的价格微差（0.01%）在容差内。"""
    b = _frame(IDENTICAL)
    b.loc[0, "close"] = IDENTICAL[0]["close"] * 1.0001
    report = compare_frames(_frame(IDENTICAL), b)
    assert report.passed is True


def test_missing_dates_reported():
    b = _frame([IDENTICAL[1]])  # B 缺 01-05
    report = compare_frames(_frame(IDENTICAL), b)
    assert report.passed is False
    assert report.missing_in_b == ["2026-01-05"]
    assert report.common_dates == 1


def test_overlap_mode_ignores_out_of_window_missing_dates():
    """长窗口回填场景：备源（腾讯）只有近 640 交易日，窗口外的缺失是能力边界不是数据错误。

    coverage="overlap" 只对交集日期严格比对；缺失日期仍然报告（供人工核查）但不判死。
    """
    b = _frame([IDENTICAL[1]])  # B 只有 01-06
    report = compare_frames(_frame(IDENTICAL), b, coverage="overlap")
    assert report.passed is True
    assert report.missing_in_b == ["2026-01-05"]  # 报告仍在，但不再导致 fail
    assert report.common_dates == 1


def test_mismatch_ratio_threshold():
    """2 行中 1 行错 = 50% > 默认阈值 2% → fail。"""
    rows = [dict(r) for r in IDENTICAL]
    b = _frame(rows)
    b.loc[0, "amount"] = 99_999_999.0
    b.loc[1, "amount"] = 99_999_999.0  # 两行都错，确保 ratio=1.0
    report = compare_frames(_frame(IDENTICAL), b)
    assert report.mismatch_ratio == 1.0
    assert report.passed is False


def test_sanity_detects_high_below_low():
    bad = _frame([dict(IDENTICAL[0])])
    bad.loc[0, "high"] = 9.0  # high < low
    issues = sanity_check(bad)
    assert any("high" in i for i in issues)


def test_sanity_detects_negative_volume():
    bad = _frame([dict(IDENTICAL[0])])
    bad.loc[0, "volume"] = -1.0
    issues = sanity_check(bad)
    assert any("volume" in i for i in issues)


def test_sanity_clean_frame_has_no_issues():
    assert sanity_check(_frame(IDENTICAL)) == []


def test_sanity_tolerates_missing_amount_column():
    """真实场景回归：腾讯源无 amount 列——sanity_check 应跳过缺失字段而非 KeyError 崩溃。"""
    df = _frame(IDENTICAL).drop(columns=["amount"])
    assert sanity_check(df) == []
