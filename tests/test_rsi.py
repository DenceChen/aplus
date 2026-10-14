"""RSI（Wilder 1978）作为候选因子。

## 先说清楚它是什么，不是什么

**RSI 不是自进化。** 它是 Wilder 1978 年的固定公式：
`RS = 平均涨幅 / 平均跌幅`（窗口锁死 14 天），
`RSI = 100 − 100/(1+RS)`。同一段价格永远给同一个输出，
实测两次计算逐位相同。**参数固定 = 没有学习任何东西。**

用户以为它是自进化，这里把这个误解钉成测试，防止以后又把它当新东西加一遍。

## 它在横截面里到底提供什么信息

真实横截面（2026-10-09，5196 只）实测 Spearman 排名相关：

    RSI vs mom20 : +0.8648      ← 极高
    RSI vs mom5  : +0.7397
    RSI vs rev5  : −0.7397      （rev5 定义就是 −mom5）
    RSI vs mom60 : +0.7353

**RSI 与 `mom20` 高度共线**，它是动量的有界平滑版本。
Top10 只重叠 3/10，所以不是纯重复。

但注意它的**值域是有界的**（实测 8.1 ~ 98.3，中位 47.6），
而 `mom20` 无界。在 `robust_z` 下，有界因子几乎不贡献极值，
也就几乎不会成为决定性因子——**这一点必须实测，不能靠推理下结论。**

## 口径选择（两个都错会得到相反的策略）

1. **时间序列用法**（RSI < 30 抄底）：这是**择时**，无法表达为横截面因子权重。
   本平台的 `composite_score` 只能做**横截面排序**。硬塞进去等于假装支持择时。
2. **横截面用法**（RSI 高 = 近期强）：与 `mom20` 高度共线，预期收益有限。

本实现是第 2 种。若将来要做第 1 种，那是**另一个策略**，
不是给因子换个权重——不能混为一谈。

## 价格口径

用**后复权价**：不复权价在除权日会跳空，制造假的「暴跌/暴涨」，
污染 RSI 的分子分母。书 3.1.2 明确要求一律用后复权。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.factor import rsi

N = 14  # Wilder 原版窗口，看板软件的默认值


def test_flat_prices_give_midpoint():
    """无涨无跌 → RSI = 50（分母为 0 的极限约定）。"""
    assert rsi(np.full(50, 10.0)) == pytest.approx(50.0)


def test_monotonic_rise_gives_hundred():
    v = rsi(np.arange(1.0, 60.0))
    assert v == pytest.approx(100.0)


def test_monotonic_fall_gives_zero():
    v = rsi(np.arange(60.0, 1.0, -1.0))
    assert v == pytest.approx(0.0)


def test_bounded_zero_to_hundred():
    rng = np.random.default_rng(3)
    for _ in range(50):
        v = rsi(100 + np.cumsum(rng.normal(0, 1, 200)))
        assert 0.0 <= v <= 100.0


def test_deterministic_no_learning():
    """同一段输入必须给出逐位相同的输出——**这就是「它不自进化」的定义**。

    若哪天这里开始随机，说明有人把参数学习混进来了，那不是 RSI。
    """
    rng = np.random.default_rng(9)
    c = 50 + np.cumsum(rng.normal(0, 1, 300))
    assert rsi(c) == rsi(c)


def test_insufficient_history_is_nan_not_50():
    """历史不足 14 根必须 NaN，不能悄悄给 50 ——那会让新股混进横截面。"""
    assert np.isnan(rsi(np.arange(1.0, 14.0)))


def test_matches_wilder_reference():
    """与 Wilder 定义的逐日递推对拍。

    参考实现就是公式本身（SMA 播种 + 递推平滑），不是另一个库——
    拿库做参照只会变成「这个库和那个库差多少」。
    """
    rng = np.random.default_rng(11)
    c = 100 + np.cumsum(rng.normal(0, 1, 200))

    d = np.diff(c)
    g = np.clip(d, 0, None)
    l = np.clip(-d, 0, None)
    ag, al = g[:N].mean(), l[:N].mean()
    for i in range(N, len(d)):
        ag = (ag * (N - 1) + g[i]) / N
        al = (al * (N - 1) + l[i]) / N
    expect = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)

    assert rsi(c) == pytest.approx(expect, abs=1e-9)


def test_all_gains_no_losses_is_100():
    """连续上涨但首根无前值时，也不能退化成 50。"""
    c = np.concatenate([[50.0], np.arange(50.0, 80.0)])
    assert rsi(c) == pytest.approx(100.0)


def test_reversal_symmetry():
    """价格完全镜像时，RSI 必须互补：`RSI(镜像) = 100 − RSI(原序列)`。

    本测试第一版写成「交替涨跌 → RSI 恰好 50」，实测 53.57，
    那是**期望值错了**：Wilder 的递推平滑给最后一天更高权重，
    而交替序列的最后一根 diff 恰好是 +1。正确的性质是对称性，不是常数。
    """
    rng = np.random.default_rng(23)
    c = 100 + np.cumsum(rng.normal(0, 1, 300))
    mirrored = c[0] + (c[0] - c)                 # 关于起点镜像
    assert rsi(mirrored) == pytest.approx(100.0 - rsi(c), abs=1e-9)


def test_alternating_series_is_not_exactly_fifty():
    """钉住上面那条：交替涨跌的结果**不是** 50，别再写成 50。"""
    c = np.array([100.0, 101.0] * 8 + [100.0])
    v = rsi(c)
    assert v != pytest.approx(50.0), "交替序列恰好 50 —— 与递推平滑的语义不符"
    assert 49.0 < v < 55.0


def test_series_input_matches_scalar_input():
    """标量与序列必须共用同一份判据——两条路径分叉就会回测/实盘口径不一。"""
    rng = np.random.default_rng(17)
    c = 80 + np.cumsum(rng.normal(0, 1, 120))
    assert rsi(pd.Series(c)) == pytest.approx(rsi(c))


def test_nan_input_is_nan_output():
    c = np.concatenate([np.full(30, np.nan), np.arange(1.0, 40.0)])
    assert np.isnan(rsi(c))


def test_window_is_fourteen_by_default():
    """窗口在看结果之前就固定为 Wilder 原版；改它需重跑全部历史结论。"""
    assert N == 14


# ── 向量化版本必须与标量逐位一致 ────────────────────────────────────────────
def test_rsi_series_matches_scalar_exactly():
    """面板走的是向量路径，回测/实盘走标量路径——两者不一致就是口径分裂。

    向量化不能直接用 `ewm(alpha=1/n, adjust=False)`：那是从**第一个观测**
    播种，而 Wilder 用的是**前 n 个的简单平均**。两者在小样本上差得可观。
    修法是播种修正：两者在 n-1 之后共用同一个递推，所以
        W[t] = E[t] + (W[n-1] − E[n-1]) · (1−1/n)^(t−n+1)
    """
    from aplus.factor import rsi_series

    rng = np.random.default_rng(31)
    for _ in range(20):
        c = 100 + np.cumsum(rng.normal(0, 1, 250))
        got = rsi_series(pd.Series(c))
        # 末值必须逐位一致（容差只留给浮点运算顺序）
        assert got.iloc[-1] == pytest.approx(rsi(c), abs=1e-9)


def test_rsi_series_handles_nan_prefix():
    from aplus.factor import rsi_series
    c = pd.Series(np.concatenate([np.full(30, np.nan), np.arange(1.0, 60.0)]))
    out = rsi_series(c)
    assert np.isnan(out.iloc[29])
    assert not np.isnan(out.iloc[-1])


def test_rsi_series_all_nan():
    from aplus.factor import rsi_series
    out = rsi_series(pd.Series([np.nan] * 50))
    assert out.isna().all()
