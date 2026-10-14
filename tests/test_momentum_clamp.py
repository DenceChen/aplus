"""复牌极值必须进不了 `mom20` / `rev5`（书 3.1.2，TDD）。

## 这条在钉什么

《因子投资：方法与实践》3.1.2 要求：**构造因子所用的收益数据**
一律做 ±10% 异常值压缩。代码里这道压缩叫 `RESUME_CLAMP`，
而且**已经存在**——只是只用在 `vol20` 与 `ivol` 上：

    ret_clamped = ret_real.clip(-0.10, 0.10)     # ← 只有它俩用
    mom20 = adj_close / adj_close.shift(20) - 1 # ← 直接用价比值，绕过压缩

后果不是「数值有点偏」：**复牌首日无涨跌停，实测单日 +593.72%**，
这个数被 `shift(20)` 的比值原样吞进去，`mom20` 冲到数倍量级。

## 为什么在 A 股这件事格外致命

因子是**排序**用的，而排序只认序、不认幅度（`robust_z` 保序）。
一个复牌暴涨的股票会被排到最前——**不管它真实的基本面**。

而 IC 实测（1821 日，8 因子）显示 `mom20` 的 IC 是 **−0.0357（t=−9.40）**：
`mom20` 越高、次日越差。于是复牌极值不只是「噪声」，
它是**把策略送到它最确定会亏的那一端**。

三个窗口里都能看到这个模式：2 年赛马、7.8 年赛马、IC 逐年表，
`mom20` 的负号从未变过。

## 修法

用**压缩后的日收益连乘**重建 `mom20` / `rev5`，
与 `ret_clamped` 同一个口径，而不是从原始价比值算。

复利连乘 `prod(1 + r_t) - 1` 在数学上等价于
`close_t / close_{t-k} - 1`，但**输入是压缩后的收益**，
所以复牌日只能贡献 ±10%，不会污染整个窗口。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.panel import RESUME_CLAMP, _safe_ratio


def _closes(n, prices):
    s = pd.Series(prices, index=pd.date_range("2024-01-01", periods=n, freq="D"))
    return pd.DataFrame({"close": s.values, "volume": [1e6] * n})


def _mom_from_returns(prices, window, clamp=RESUME_CLAMP):
    """连乘口径：`prod(1 + clip(r)) - 1`。测试用参照实现。"""
    r = pd.Series(prices).pct_change()
    if clamp is not None:
        r = r.clip(-clamp, clamp)
    return float((1.0 + r.fillna(0.0)).rolling(window).apply(np.prod, raw=True).iloc[-1] - 1.0)


# ── 复牌极值不得进入动量 ─────────────────────────────────────────

def test_resume_spike_would_destroy_raw_price_momentum():
    """**先把病症算出来**：原始价比值口径下，单日 +593% 的复牌
    会把 20 日动量推到几倍量级。这一条钉住「不做处理会有多糟」。"""
    prices = [10.0] * 20 + [69.37]          # +593.7% 的复牌跳空
    raw_mom = prices[-1] / prices[-21] - 1.0
    assert raw_mom > 5.0                   # 593%


def test_chained_return_momentum_bounds_the_resume_spike():
    """连乘 + 压缩口径下，同一根 K 线最多贡献 ±10% × 20 天。"""
    prices = [10.0] * 20 + [69.37]
    got = _mom_from_returns(prices, 20)
    # 上界：20 天全部 +10% → (1.1^20 − 1) = 6.7275；但只有一天是 +10%，
    # 其余 19 天收益为 0 → 1.1 − 1 = 0.10
    assert got == pytest.approx(0.10, abs=1e-9)


def test_chained_matches_price_ratio_when_nothing_is_clamped():
    """没有一天触到压缩阈值时，两种口径必须**逐位相同**。

    这条是「修法不改变正常样本」的安全网：99% 的交易日不受影响，
    历史结论不会因为这次改动而整体位移。
    """
    prices = [10.0 + 0.1 * i for i in range(30)]
    chained = _mom_from_returns(prices, 20)
    ratio = prices[-1] / prices[-21] - 1.0
    assert chained == pytest.approx(ratio, rel=1e-9)


def test_chained_is_lower_than_raw_when_a_spike_is_clamped():
    """方向性检查：压缩只会把动量拉向 0，不会反向放大。"""
    prices = [10.0] * 20 + [69.37]
    assert _mom_from_returns(prices, 20) < prices[-1] / prices[-21] - 1.0


# ── 与既有构造保持一致 ───────────────────────────────────────────

def test_safe_ratio_matches_naive_ratio_on_clean_prices():
    """`_safe_ratio` 返回**比值本身**，减 1 是调用方做的（见 `mom20` 的写法）。"""
    a = pd.Series([10.0, 11.0, 12.0], index=pd.RangeIndex(3))
    b = pd.Series([10.0, 10.0, 10.0], index=pd.RangeIndex(3))
    assert _safe_ratio(a, b).iloc[-1] == pytest.approx(12.0 / 10.0)


def test_safe_ratio_returns_nan_on_zero_denominator():
    """除零必须给 NaN 而不是 inf——inf 会污染排序。"""
    b = pd.Series([0.0, 0.0, 0.0], index=pd.RangeIndex(3))
    a = pd.Series([10.0, 11.0, 12.0], index=pd.RangeIndex(3))
    assert np.isnan(_safe_ratio(a, b).iloc[-1])


def test_resume_clamp_is_the_books_ten_percent():
    """书 3.1.2 的口径是 ±10%，不是 ±40%。

    ±40% 是**成交收益**的作废闸（`MAX_PLAUSIBLE_RETURN`），
    两者用途不同：一个是「这根 K 线不可信，作废」，
    一个是「这根 K 线合法但极端，压到 10%」。
    混用会让复牌股要么被踢出排名、要么把动量抬到数倍——
    两个极端都是错的。
    """
    assert RESUME_CLAMP == pytest.approx(0.10)
    assert RESUME_CLAMP < 0.40

# ── 面板层集成：复牌跳空不得进 mom20 ──────────────────────────────

def _frame_with_suspension(n=45, vol_zero_at=(30, 31), spike_at=32, spike=0.25):
    """构造：第 30/31 根停牌（volume=0），第 32 根复牌跳空 `spike` 且**保持**。

    停牌日走 `panel` 的 NaN 收益路径，复牌首日的收益横跨整个停牌期。
    跳空后的价格必须保持——否则窗口里还含一次回落，测的就不是压缩了。
    """
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = np.full(n, 10.0)
    vol = np.full(n, 1e6)
    for i in vol_zero_at:
        vol[i] = 0.0
    close[spike_at:] = 10.0 * (1.0 + spike)
    return pd.DataFrame({"date": [d.strftime("%Y-%m-%d") for d in idx],
                         "close": close.tolist(), "open": close.tolist(),
                         "volume": vol.tolist(), "adjust_factor": 1.0})


def test_resume_spike_between_clamp_and_invalidation_is_compressed():
    """复牌首日 +25%（**在 ±10% 压缩与 ±40% 作废之间**）必须被压到 +10%。

    ## 这条测试第一版写错了，值得记下来

    第一版造的是 **+500%** 的复牌跳空，断言 mom20 不被抬高——
    它确实通过，但**根本没走到压缩逻辑**：+500% 早已被
    `MAX_PLAUSIBLE_RETURN(±40%)` 作废成 NaN，那道闸先一步拦住了。
    于是「把压缩整个关掉」的变异体照样全绿（2/3 全杀变成了 2/3，
    存活的那个正是这条测试本该杀掉的）。

    压缩真正起作用的区间是 **10% < |收益| ≤ 40%**：合法但任意。
    造出这个区间位置，才测得到它。
    """
    from aplus.panel import _factor_series
    frame = _frame_with_suspension(spike=0.25)     # 复牌首日 +25%
    out = _factor_series(frame)
    mom = float(out["mom20"].dropna().iloc[-1])
    assert mom == pytest.approx(0.10, abs=1e-9), (
        f"复牌 +25% 未被压到 +10%，实际 {mom:.2%}")


def test_resume_spike_beyond_invalidation_is_dropped_not_clamped():
    """复牌 +500% → 超过 ±40%，按**作废**处理（NaN），不是压到 +10%。

    两层闸各管各的：作废用于「数据不可信」，压缩用于「合法但任意」。
    混用会让 +500% 变成一个 +10% 的**假信号**——比作废更危险，
    因为它看起来是个正常的收益。
    """
    from aplus.panel import _factor_series
    out = _factor_series(_frame_with_suspension(spike=5.0))
    assert out["mom20"].dropna().iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_normal_limit_up_still_reaches_mom20_fully():
    """**非复牌日**的 +20% 涨停必须完整计入。

    这是三层口径里的第三层：真实涨跌停不动它。
    少了这条，修复就会变成「把所有真实波动都削平」。
    """
    from aplus.panel import _factor_series
    n = 45
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = np.full(n, 10.0)
    close[30:] = 12.0                       # 普通交易日的 +20% 涨停，并保持
    frame = pd.DataFrame({"date": [d.strftime("%Y-%m-%d") for d in idx],
                          "close": close.tolist(), "open": close.tolist(),
                          "volume": [1e6] * n, "adjust_factor": 1.0})
    out = _factor_series(frame)
    mom = float(out["mom20"].iloc[-1])
    # 末窗是 25..44，里面只有第 30 根起的一次 +20%，且之后保持
    assert mom == pytest.approx(0.20, abs=1e-9), f"真实涨停被削平成了 {mom:.2%}"
