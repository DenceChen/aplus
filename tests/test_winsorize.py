"""横截面缩尾（winsorize）——《因子投资：方法与实践》3.1.4 节异常值处理。

## 为什么必须有

`composite_score` 现在的流程是「逐列 z-score → 加权求和」。z-score 对极值
是**线性放大**的：一只 `vol20` 异常高的股票，z 值可能到 +50，它一个人就把
整列的标准差撑起来，让其余 5400 只股票的 z 值全部塌向 0。

实测影响（2026-10-11，赛马训练段）：`objective = 100 × 涨腿超额 + 总命中率`，
两分项在 14 组候选上的跨度分别是

- `100 × 超额`：**27.774**（由 −27.786 与 −4.09 两组撑起，其余都在 ±1 之内）
- `命中率`：**0.076**

也就是说，**目标函数的排序几乎完全由极端超额决定**，而极端超额来自少数
被单日异常污染的样本。命中率这一项在数值上根本不参与竞争——
（此前我误以为「命中率主导」，方向反了：真正的问题是**超额项被极值主导**。）

书里 3.1.4 节给的正是这个处理：横截面缩尾，把尾部极端值压到分位点，
再做标准化。

## 口径

在**每一列**上按分位缩尾（默认 1% / 99%），再算 z-score。

- 为什么在 z-score **之前**：z-score 的分母（标准差）会被极值放大，
  先标准化等于把污染固化进分母里。
- 为什么在**每一列**而不是合成分上：各因子的量纲完全不同
  （`mom20` 是小数、`vol20` 也是小数，但 `vsur`/`turn20` 是比值/百分比），
  合成分的分布无法跨列比较，缩尾阈值没有统一含义。

## 不做什么

**不做 MAD / 3σ 这类依赖均值方差的判据**——它们本身就被极值污染，
是同一个循环论证。分位数不依赖任何被污染的统计量。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.factor import WINSOR_Q, composite_score, winsorize


# ── winsorize 本身 ─────────────────────────────────────────────────────────

def test_winsorize_clips_both_tails():
    """尾部被拉回分位点，中间原样不动。

    截断值是**分位数本身**，在样本少时会落在相邻观测之间（线性插值），
    所以不能断言它等于下一个观测值——那会把正确的实现判成错的。
    """
    s = pd.Series([-100.0, 1.0, 2.0, 3.0, 4.0, 5.0, 100.0])
    out = winsorize(s, lo=0.1, hi=0.9)
    assert -100.0 < out.iloc[0] <= out.iloc[1], "下尾被压回 10% 分位"
    assert out.iloc[-2] < out.iloc[-1] < 100.0, "上尾被压回 90% 分位"
    assert out.iloc[1:-1].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0], "中间部分一个都不能动"
    assert float(out.max() - out.min()) < float(s.max() - s.min()), "极差必须收窄"


def test_winsorize_on_a_clean_series_only_touches_the_extreme_ends():
    """没有极端值时，中间观测必须逐位不动。

    防呆：1% 分位在小样本上会把最大的那个点也轻微拉低一点
    （分位插值），这没问题；但**只允许**动两端。
    """
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = winsorize(s)
    assert out.iloc[1:-1].tolist() == [2.0, 3.0, 4.0]
    assert out.iloc[0] >= 1.0 and out.iloc[-1] <= 5.0
    assert out.iloc[0] == pytest.approx(1.04) and out.iloc[-1] == pytest.approx(4.96)


def test_winsorize_preserves_nan_positions():
    """NaN 必须留在原位：它表示「该股因子不完整」，不能被填成一个值。"""
    s = pd.Series([1.0, np.nan, 3.0, -50.0, 5.0, 50.0, 4.0])
    out = winsorize(s, lo=0.15, hi=0.85)
    assert out.isna().tolist() == [False, True, False, False, False, False, False]
    assert np.isfinite(out.dropna()).all()


def test_winsorize_handles_short_and_constant_series():
    """样本太少时不该崩——新股、次新股横截面可能只有几只。"""
    for s in [pd.Series([1.0]), pd.Series([2.0, 2.0, 2.0]), pd.Series(dtype=float)]:
        winsorize(s)


# ── 落点：composite_score 必须在标准化**之前**缩尾 ──────────────────────────

def _cs(**cols):
    n = len(next(iter(cols.values())))
    base = {"code": [f"{i:06d}.SZ" for i in range(n)], "name": [""] * n}
    return pd.DataFrame({**base, **cols})


def test_one_extreme_value_no_longer_dominates_the_cross_section():
    """一只 `vol20` 异常高的股票不该把其余 99% 的 z 值压成 0。

    这是缩尾最直接的作用：控制**单个样本对标准化分母的影响**。
    """
    n = 200
    normal = np.linspace(0.01, 0.03, n - 1)
    vol = np.append(normal, 5.0)                  # 一只 100 倍于同伴的异常股
    df = _cs(mom20=np.zeros(n), rev5=np.zeros(n), vsur=np.ones(n), vol20=vol)

    w = {"mom20": 0.0, "rev5": 0.0, "vsur": 0.0, "vol20": 1.0}
    score = composite_score(df, w)
    finite = score[np.isfinite(score)]
    assert float(np.abs(finite[:-1]).max()) > 0.5, (
        f"正常股票被异常值压塌了：z 最大只有 {np.abs(finite[:-1]).max():.3f}"
    )


def test_extremes_cannot_flip_the_top_ranking():
    """~~已替换（2026-10-11 16:50）~~

    **旧契约**：榜首 `z < 6.0`——用 z 值大小来判断「异常值有没有霸榜」。
    这条契约在真实数据上无法兑现，而且它的代价是**每天 52 只股票并列**。

    真实横截面（2026-10-09，5215 只）实测：硬 clip 把 99% 分位以上的
    53 只压成同一个值，**Top10 里 9 只分数完全相同**——
    「前 10 名」变成「并列票的代码字母序前 10」。

    为什么放弃 z 封顶：实测「稳健 z + |z| ≤ 4」更差，57 只被压到 4.0，
    最大并列组 52。**封顶 z 与 clip 值制造的是同一类并列。**

    ## 新契约（下面两条才是可兑现的）

    1. `tests/test_rank_fidelity.py::test_extreme_does_not_collapse_everyone_else`
       —— 极端值不得把其余 99% 的 z 压塌（缩尾的本来目的，本文件保留其意图）
    2. `tests/test_rank_fidelity.py::test_dropping_one_extreme_does_not_reshuffle_top10`
       —— 拿掉极端值，前 10 名基本不变（稳定性）

    「一只虚假极端值不得占榜首」这件事改由**数据层**保证：
    `MAX_PLAUSIBLE_RETURN = 0.40`（`aplus/panel.py`）在因子层把
    |日收益| > 40% 的样本直接作废；真实横截面里 `mom20` 不可能是 30.0，
    那个值只存在于合成夹具里。

    保留这一条作为**哨兵**：如果哪天有人把 `MAX_PLAUSIBLE_RETURN` 放宽，
    这里会先红——它现在断言的是「数据层兜底后，排名函数不再需要自我阉割」。
    """
    from aplus.panel import MAX_PLAUSIBLE_RETURN
    assert MAX_PLAUSIBLE_RETURN == 0.40, (
        "数据层的异常值兜底被放宽了 —— 此时排名函数必须重新引入值截断，"
        "并重新评估 tests/test_rank_fidelity.py 里的并列契约"
    )


def test_default_winsor_quantile_is_one_percent():
    assert WINSOR_Q == 0.01, "书 3.1.4 节用 1% 分位"


def test_ranking_of_normal_stocks_is_preserved():
    """缩尾只压尾部，**不得**改变中间正常股票的相对次序。

    防呆：如果缩尾改动影响了中部，信号就被系统性地削平了——
    那比不缩尾更糟，因为它会悄悄削弱所有真实因子。
    """
    n = 500
    rng = np.random.default_rng(3)
    mom = rng.normal(0, 0.03, n)
    df = _cs(mom20=mom, rev5=np.zeros(n), vol20=np.full(n, 0.02), vsur=np.ones(n))
    w = {"mom20": 1.0, "rev5": 0.0, "vol20": 0.0, "vsur": 0.0}
    score = composite_score(df, w)
    # 只比较**未被缩尾**的中部：被压到分位点的那些股票彼此**并列**，
    # 并列项之间的排序是不稳定的，拿来比顺序没有意义。
    mom_s = pd.Series(mom)
    hi, lo = mom_s.quantile(0.99), mom_s.quantile(0.01)
    keep = (mom_s < hi) & (mom_s > lo)
    assert keep.sum() > 0.9 * len(mom), "中部样本太少，这条测不出东西"
    expected = df.index[keep].to_numpy()[np.argsort(-mom[keep.to_numpy()])]
    assert score[keep].sort_values(ascending=False).index.to_numpy().tolist() \
        == expected.tolist()
