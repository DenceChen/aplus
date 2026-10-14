"""交叉校验的成交量容差——按手取整的备源不该否决主源。

## 事故

`aplus/crosscheck.py` 的容差是 `DEFAULT_TOL_REL = 1e-4`（**相对** 0.01%），
docstring 的依据是「跨源实测成交量/成交额 100% 一致 → 严格比对」。

**那条依据只在大成交量上成立。** 腾讯 kline 的成交量按 **100 股（1 手）取整**，
baostock 给精确股数。于是：

| 成交量 | 取整误差上界 | 相对误差 | vs 1e-4 |
|---|---|---|---|
| 8,397,166 股 | ±50 | 6e-6 | 通过 |
| 300,000 股 | ±50 | **1.7e-4** | **不通过** |

2026-10-11 补数实测：**513 个失配行，最大相对误差 0.0864%，
100% 都能被 ±100 股的绝对容差吸收**，失配行成交量中位数 300,300 股。

后果不是「多隔离几只」那么轻：
- **23 只股票被隔离**，主源已经拿到的完整历史没能进主库；
- 其中 **13 只的 master 文件根本不存在**——它们的全部数据只在隔离区，
  等于**从股票池里被静默删除**，任何统计都看不到它们消失了。

## 修法

不是放宽相对容差（那会让真正的价格错配蒙混过关），
而是给**按手取整的字段**加一个与成交量无关的绝对容差：
`bound = |a| * tol_rel + abs_tol + 1e-9`。

价格字段不加绝对容差——它们没有量化问题，
给它们加容差就是纯粹的放松。
"""
import pandas as pd
import pytest

from aplus.crosscheck import (ABS_TOL_BY_FIELD, DEFAULT_ABS_TOL_VOLUME,
                              DEFAULT_MISMATCH_THRESHOLD, DEFAULT_TOL_REL,
                              compare_frames)


def frame(dates, volume, close=None):
    n = len(dates)
    return pd.DataFrame({
        "date": dates,
        "open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
        "close": close if close is not None else [1.0] * n,
        "volume": volume,
        "amount": [1.0] * n,
    })


def test_lot_rounding_at_300k_volume_passes():
    """300 万→30 万量级上的 ±50 股取整误差不该判为失配。"""
    d = ["2026-01-01", "2026-01-02", "2026-01-03"]
    a = frame(d, [275000.0, 289700.0, 136900.0])
    b = frame(d, [274969.0, 289657.0, 136850.0])       # 精确值
    rep = compare_frames(a, b, coverage="overlap")
    assert rep.field_mismatches["volume"] == 0, (
        f"按手取整被误判为失配：{rep.field_mismatches}")
    assert rep.passed


def test_real_mismatch_still_fails():
    """绝对容差不能变成放水口：5% 的真实差异仍必须拦下。"""
    d = [f"2026-01-{i:02d}" for i in range(1, 21)]
    a = frame(d, [300000.0] * 20)
    b = frame(d, [315000.0] * 20)                     # +5%
    rep = compare_frames(a, b, coverage="overlap")
    assert rep.field_mismatches["volume"] == 20
    assert not rep.passed


def test_price_fields_get_no_absolute_tolerance():
    """价格没有量化问题，加绝对容差就是纯放松。"""
    assert "close" not in ABS_TOL_BY_FIELD
    assert "open" not in ABS_TOL_BY_FIELD
    assert "high" not in ABS_TOL_BY_FIELD
    assert "low" not in ABS_TOL_BY_FIELD


def test_volume_absolute_tolerance_is_one_lot():
    assert DEFAULT_ABS_TOL_VOLUME == 100.0, "A 股 1 手 = 100 股；改它需重跑交叉校验判据"


def test_small_volume_rounding_still_captured():
    """极低成交量下，取整误差占比很大，仍必须由相对容差之外的方式拦住真实差异。"""
    d = ["2026-01-01"]
    a = frame(d, [100.0])
    b = frame(d, [5000.0])                            # 50 倍
    rep = compare_frames(a, b, coverage="overlap")
    assert not rep.passed


def test_bound_is_relative_plus_absolute():
    """边界是**两者之和**，不是二选一：`bound = |a|*tol_rel + abs_tol + 1e-9`。

    30 万股上相对容差已经贡献 30 股，加绝对容差 100 → 实际边界 130 股。
    （本测试第一版按 100 写期望值，直接把正确的实现判成红的——
     这是本轮第四次「测试期望值算错而不是代码错」。）
    """
    d = ["2026-01-01"]
    for diff, should_pass in [(129.0, True), (131.0, False)]:
        a = frame(d, [300000.0])
        b = frame(d, [300000.0 + diff])
        rep = compare_frames(a, b, coverage="overlap")
        assert rep.passed is should_pass, (
            f"30 万股上差 {diff} 股时判定不符预期（实际边界应为 130 股）")


def test_mismatch_threshold_unchanged():
    assert DEFAULT_MISMATCH_THRESHOLD == 0.02
    assert DEFAULT_TOL_REL == 1e-4


def test_full_run_regression_no_mismatch_passes():
    """2026-10-11 补数真实场景：13/23 行失配、其余一致，应整体通过。"""
    d = [f"2026-01-{i:02d}" for i in range(1, 29)]
    exact = [274969.0, 289657.0, 136850.0, 181464.0, 440450.0, 275000.0, 289700.0]
    a_vol = [round(v / 100.0) * 100.0 for v in exact] + [300000.0] * 21
    b_vol = exact + [300000.0] * 21
    a, b = frame(d, a_vol), frame(d, b_vol)
    rep = compare_frames(a, b, coverage="overlap")
    assert rep.passed, f"真实补数场景仍被隔离：{rep.summary()}"
