"""排名保序性：合成 z 分不得制造并列。

## 事故（2026-10-11 16:50，真实横截面 2026-10-09，5215 只）

`composite_score` 先 `winsorize()`（硬 `clip` 到 1%/99% 分位）再 z-score。
`clip` 把超过 99% 分位的所有值压成**同一个数**，于是

    现方案(硬clip)：不同取值 4948/5215，最大并列组 53，并列股票 380
    现方案 Top10 内部并列：9      ← 10 只里有 9 只分数完全相同

**「前 10 名」是 52~53 只分数完全相同的股票里按代码字母序挑的 10 个。**
这意味着榜单头部不是「动量最强的 10 只」，而是「并列票的字母序前 10」。
所有单因子探针的结论都建立在这个任意选择之上。

## 为什么不能用「z 封顶」代替

实测替代方案「稳健 z + |z| ≤ 4 封顶」更糟：57 只被压到 4.0，
最大并列组 **52**（比原方案还大）。**封顶 z 与 clip 值制造的是同一类并列。**

## 那「异常值霸榜」怎么办

`tests/test_winsorize.py: test_extremes_cannot_flip_the_top_ranking`
断言榜首 `z < 6.0`，本意是「一只虚假的极端值不得直接占第一名」。
这条契约**由数据层接管**了：`MAX_PLAUSIBLE_RETURN = 0.40`
（`aplus/panel.py`）在因子层把 |日收益| > 40% 的样本直接作废，
真实横截面里 `mom20` 不可能是 30.0——那个值只存在于合成测试里。

把「异常值不许霸榜」继续压在排名函数上，代价是**每天 52 只真实股票变成并列**。
这笔账不划算，所以本文件把契约换成可兑现的形式：**排名必须严格保序**。

## 实现契约

    均值/标准差 ← 用缩尾后的参考序列估计（抗极值，缩尾的本来目的）
    z 得分     ← 用**原值**与上面那个尺度做仿射变换（严格单调，无人工并列）

于是极值不会撑大分母（原有防线保住），而排序与原始因子**完全一致**。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.factor import DEFAULT_WEIGHTS, composite_score


def _cs(**cols):
    n = len(next(iter(cols.values())))
    df = pd.DataFrame(cols)
    df.insert(0, "code", [f"{i:06d}" for i in range(n)])
    return df


def test_single_factor_ordering_matches_raw_exactly():
    """单因子候选的排序必须与原始因子**完全一致**，一个位置都不能差。"""
    rng = np.random.default_rng(5)
    raw = rng.standard_t(df=2.5, size=2000) * 0.1
    score = composite_score(_cs(mom20=raw), {"mom20": 1.0})
    got = score.sort_values(ascending=False).index.to_numpy()
    want = np.argsort(-raw, kind="stable")
    assert got.tolist() == want.tolist(), "排序与原始因子不一致"


def test_no_artificial_ties_in_top10():
    """核心回归：Top-10 内部不得出现并列。

    这是现方案最刺眼的问题——10 只里有 9 只分数完全相同。
    """
    rng = np.random.default_rng(11)
    raw = rng.standard_t(df=3, size=4935) * 0.5
    score = composite_score(_cs(mom20=raw), {"mom20": 1.0})
    top = score.nlargest(10)
    assert len(set(np.round(top.values, 12))) == 10, (
        f"Top10 内部并列：{top.round(4).tolist()}")


def test_top10_is_the_actual_top10():
    """前 10 名必须是分数最高的 10 只，且与 |raw| 排名一致。"""
    rng = np.random.default_rng(7)
    raw = rng.standard_cauchy(size=4935) * 0.05
    score = composite_score(_cs(mom20=raw), {"mom20": 1.0})
    top_idx = set(score.nlargest(10).index)
    true_top = set(np.argsort(-raw, kind="stable")[:10])
    assert top_idx == true_top, (
        f"选出的不是真正的前 10：重合 {len(top_idx & true_top)}/10")


def test_extreme_does_not_collapse_everyone_else():
    """一只极端值不得把其余 99% 的 z 压塌——缩尾的本来目的必须保住。"""
    n = 300
    normal = np.linspace(0.01, 0.03, n - 1)
    raw = np.append(normal, 50.0)
    score = composite_score(_cs(mom20=raw), {"mom20": 1.0})
    body = np.abs(score.to_numpy()[:-1])
    assert body.max() > 0.5, f"正常股票 z 被压塌：最大只有 {body.max():.3f}"


def test_dropping_one_extreme_does_not_reshuffle_top10():
    """把极端值拿掉，前 10 名基本不该变——这是缩尾的价值，必须留住。"""
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.05, 2000)
    a = composite_score(_cs(mom20=np.append(base, 30.0)), {"mom20": 1.0})
    b = composite_score(_cs(mom20=base), {"mom20": 1.0})
    ov = len(set(a.nlargest(10).index) & set(b.nlargest(10).index))
    assert ov >= 9, f"去掉极端值后前 10 重叠只有 {ov}/10 —— 缩尾没起作用"


def test_suspended_zero_momentum_does_not_win():
    """停牌股 mom20 恰为 0，几十只精确并列；它们不得因此排到前面。"""
    n = 200
    raw = np.concatenate([np.full(50, 0.0), np.linspace(0.01, 0.2, n - 50)])
    score = composite_score(_cs(mom20=raw), {"mom20": 1.0})
    top = score.nlargest(10)
    assert raw[top.index].min() > 0.0, "停牌的 0 动量股混进了前十"


def test_multi_factor_ordering_also_clean():
    rng = np.random.default_rng(13)
    n = 4935
    score = composite_score(_cs(mom20=rng.standard_t(3, n) * .5,
                                rev5=rng.standard_t(3, n) * .2,
                                vol20=rng.standard_t(3, n) * .1,
                                vsur=rng.normal(0, 1, n)),
                            DEFAULT_WEIGHTS)
    top = score.nlargest(10)
    assert len(set(np.round(top.values, 12))) == 10


def test_nan_rows_still_excluded():
    """任一因子 NaN → 合成分 NaN，股票不得参与排名（原有契约不动）。"""
    raw = np.linspace(0.01, 0.2, 100)
    rev = np.linspace(0.0, 0.1, 100)
    rev[5] = np.nan
    score = composite_score(_cs(mom20=raw, rev5=rev), {"mom20": 1.0, "rev5": 1.0})
    assert score.isna().sum() >= 1


def test_robust_mean_keeps_body_centred_near_zero():
    """稳健尺度的**均值**也必须是稳健的。

    这条是变异测试逼出来的：`robust_z` 最初只用缩尾序列估标准差、
    用原序列估均值，而没有任何测试能杀掉那个变异——因为加性平移
    **保序**，所有「排序一致」的测试照样绿。

    但它确实是缺陷：一只极端值会把全场的 z 平移几十个 sigma，
    z=0 不再代表横截面平均，所有 z 的解读全部失效。

    2026-10-11 变异体 M6「均值用原值」在 8 条测试下全绿，
    换句话说这条防线当时是空的。
    """
    n = 300
    body = np.linspace(0.01, 0.03, n - 1)
    raw = np.append(body, 50.0)                     # 一只极端值
    score = composite_score(_cs(mom20=raw), {"mom20": 1.0})
    body_z = score.to_numpy()[:-1]
    assert abs(float(np.mean(body_z))) < 1.0, (
        f"正常股票的 z 均值被极端值带偏到 {np.mean(body_z):.1f} —— "
        f"均值没有用稳健序列估计"
    )
    assert abs(float(np.median(body_z))) < 1.0
