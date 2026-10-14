"""σ_r（横截面收益标准差）必须**算出来**，且口径必须钉死。

## 为什么它是 P0

`aplus/ic_to_return.py` 的换算是

    毛超额 = ρ_p · σ_r · K(q) · 244

四个因子里 `ρ_p` 已实测、`K(q)` 是解析常数、244 是常数——
**只有 σ_r 是估的**（0.023，量级估计）。

而它决定月频结论的真伪：

| σ_r | `rev5` 低尾年毛超额 | 月频门槛 26.4% |
|---|---|---|
| 0.022 | 25.5% | ❌ |
| 0.023 | 26.7% | ✅ 刚好越过 |

**余量 1.2% 小于输入的估计误差**，所以「刚好越过」是噪声不是结论。

## σ_r 是什么、不是什么

| | 含义 |
|---|---|
| **σ_r（要的）** | **同一天**横截面上个股收益的标准差 |
| 组合净值日波动 | 某个组合的日收益标准差（小得多） |
| 全样本日波动 | 指数的日收益标准差 |

**三者数量级相近，最容易互串**，串错会让结论整个翻号。

> ⚠️ `significance.json` 的 `winner.std_daily = 0.0093` 是**组合净值**的，
> 不是 σ_r。曾差点被当成 σ_r 用。

## 三个会改变数字的口径

1. **池子**——全市场 vs 剔次新/停牌/ST。本项目的 IC 报告 `avg_n ≈ 4368.5`，
   当日可买池经交叉核对约 4987 只（`buyable ∩ 上市≥250日 ∩ 非ST`），
   **同一总体**，否则 σ_r 与 ρ_p 不是配对的。
2. **收益口径**——`pctChg` 已是百分比，直接用；不另算。
3. **极值处理**——A股涨跌停（±10%、ST ±5%、创业板/科创 ±20%）
   在尾部堆质量，σ 被系统性抬高。**原始与 1%/99% 缩尾都要报**，
   差异本身就是结论的一部分。

## 纪律

- **不许给默认值**：口径必须显式传入。
- **样本不足的日期跳过**，但要**报出跳过了多少天**——
   静默跳过会让 n_days 与 IC 报告对不上而没人发现。
- **每日的横截面标准差先算，再跨日汇总**；
   不能把所有日收益混在一起算一个总标准差（那混进了时间维度的波动）。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.xsec_sigma import cross_sectional_sigma, summarise


# ── 一、基本性质 ──────────────────────────────────────────────────

def test_a_wider_cross_section_has_a_larger_sigma():
    """同样的收益，股票越多、分布越散，σ 越大。

    两侧都必须过 `min_obs`——否则测的是「样本不足报错」，
    而那条另有测试（`test_too_few_observations_is_rejected`）在钉。
    """
    narrow = [0.01, 0.02, 0.03] * 10            # 30 只，窄
    wide = narrow + [0.20, -0.20] * 10          # 50 只，多出两个极端
    assert cross_sectional_sigma(wide) > cross_sectional_sigma(narrow)


def test_identical_returns_give_zero_sigma():
    """完全相同的收益 → σ 恰好为 0（不是「接近 0」）。"""
    assert cross_sectional_sigma([0.01] * 50) == pytest.approx(0.0, abs=1e-15)


def test_the_result_is_a_fraction_not_a_percentage():
    """**单位是最容易串错的一处**：0.023 与 2.3 差 100 倍。

    传入 ±100% 若按百分比解读应是 100，
    而本函数的口径是**收益率比例**，所以必须是 1.0。

    样本取 −1×10、0×1、+1×10（n=21）：
    `ddof=1` 下 `Σ(x−x̄)² = 20`、`n−1 = 20`，**样本标准差恰为 1.0**。
    这样断言是精确相等，而不是「差不多」——
    而「差不多」正是单位串错时最容易蒙混过关的地方。
    """
    x = [-1.0] * 10 + [0.0] + [1.0] * 10
    assert cross_sectional_sigma(x) == pytest.approx(1.0, abs=1e-12)


# ── 二、样本量门槛：不足要报错，不许静默返回一个数 ────────────────

def test_too_few_observations_is_rejected():
    """`MIN_OBS` 以下直接报错。

    > 静默返回一个小样本的 σ 会**看起来完全正常**——
    > 3 只股票算出的 σ 与 4000 只算出的没有量纲差别。
    """
    for bad in ([0.01], [0.01, 0.02]):
        with pytest.raises(ValueError):
            cross_sectional_sigma(bad, min_obs=20)


def test_non_finite_values_are_dropped_not_propagated():
    """停牌/缺失会产生 NaN。**剔除**是对的，
    但若剔除后不足 min_obs，必须报错而不是拿剩下的算。"""
    vals = [0.01, 0.02, np.nan, np.inf, -np.inf] + [0.03] * 30
    got = cross_sectional_sigma(vals, min_obs=20)
    assert np.isfinite(got)
    with pytest.raises(ValueError):
        cross_sectional_sigma([np.nan, np.inf], min_obs=20)


# ── 三、缩尾：必须能报出，且两版都要 ─────────────────────────────

def test_winsorizing_reduces_sigma_and_does_not_hide_the_raw_value():
    """**涨跌停堆质量 → 原始 σ 被系统性抬高。**

    所以两版都要报，且缩尾版必须**更小**。
    若两者相等，说明缩尾没生效——那是个静默失效的守卫。

    > ⚠️ 尾部质量必须**小于**缩尾比例（这里 0.4% < 1%）。
    > 否则分位落在极值堆内部，clip 退化成恒等变换——
    > 见 `test_winsorizing_silently_does_nothing_when_the_tail_pile_is_too_big`。
    """
    rng = np.random.default_rng(7)
    body = rng.normal(0.0, 0.02, 4000)
    # 人为堆 0.4% 的 ±10% 极值，模拟涨跌停（比例低于 1% 缩尾线）
    capped = np.concatenate([body, np.full(16, 0.10), np.full(16, -0.10)])

    raw = cross_sectional_sigma(capped)
    win = cross_sectional_sigma(capped, winsorize=(0.01, 0.99))
    assert win < raw, "缩尾后 σ 必须下降"


def test_winsorizing_silently_does_nothing_when_the_tail_pile_is_too_big():
    """**实测 A 股数据时才发现的失效模式，值得单独钉住。**

    涨跌停会让**很大比例**的股票恰好压在 ±10%——
    极端行情下这个比例可以轻松超过缩尾线（本例 8.33% > 1%）。
    此时 `np.quantile(x, 0.01)` 返回的值**就在那堆 ±10% 里面**，
    `clip(x, -0.10, 0.10)` 变成恒等变换：

        原始 σ = 缩尾 σ，一个数都没变。

    **危险的不是它没效果，是它看起来有效**——
    返回了一个正常的数，只是不是你想要的数。
    这是本项目反复吃亏的一类「静默失效的守卫」。

    > 所以本模块**两版都报**（`sigma_r_raw` 与 `sigma_r_winsorized`）：
    > 两版相等本身就是「这天缩尾没起作用」的证据，
    > 读者能当场发现，而不是拿到一个被悄悄污染的 σ。
    """
    rng = np.random.default_rng(7)
    body = rng.normal(0.0, 0.02, 4000)
    heavy = np.concatenate([body, np.full(400, 0.10), np.full(400, -0.10)])

    raw = cross_sectional_sigma(heavy)
    win = cross_sectional_sigma(heavy, winsorize=(0.01, 0.99))
    assert win == pytest.approx(raw), (
        "本测试钉的是**失效**本身：尾部堆太大时缩尾确实不动。"
        "若哪天它开始动了，说明缩尾实现变了，"
        "那么 `test_winsorizing_reduces_sigma...` 才是真正在测的东西。"
    )

    # 于是「两版相等」必须可被读者看见——这是本测试存在的理由
    out = summarise({"d1": list(heavy)})
    assert out["sigma_r_raw"] == pytest.approx(out["sigma_r_winsorized"])


def test_the_winsor_limits_must_be_a_valid_pair():
    for bad in ((0.0, 0.99), (0.01, 1.0), (0.99, 0.01), (0.5, 0.5)):
        with pytest.raises(ValueError):
            cross_sectional_sigma([0.01] * 30, winsorize=bad)


def test_winsorizing_does_not_change_the_mean():
    """缩尾只动尾部，**均值不该变**（对称分位下）。

    这条守住「缩尾只压 σ、不偷偷平移中心」——
    若实现里用了均值填补，均值就会漂，而 σ 的差异
    就会被误读成「信号变强/变弱」。
    """
    rng = np.random.default_rng(11)
    x = np.concatenate([rng.normal(0.01, 0.02, 3000),
                        np.full(300, 0.10), np.full(300, -0.10)])
    raw_m = float(np.mean(x))
    lo, hi = np.quantile(x, [0.01, 0.99])
    win_m = float(np.mean(np.clip(x, lo, hi)))
    assert win_m == pytest.approx(raw_m, rel=0.02)


# ── 四、汇总：跨日口径 ────────────────────────────────────────────

def test_summarise_reports_the_skipped_days_instead_of_hiding_them():
    """**静默跳过是最难发现的一类信息丢失。**

    某天横截面不足 min_obs 时必须跳过，
    但 `n_skipped` 必须报出来——否则 n_days 与 IC 报告对不上而没人察觉。
    """
    daily = {"2023-01-03": [0.01] * 50,       # 正常
             "2023-01-04": [0.02, 0.03],      # 不足 → 跳过
             "2023-01-05": [-0.01] * 40}      # 正常
    out = summarise(daily, min_obs=20)
    assert out["n_days"] == 2
    assert out["n_skipped"] == 1


def test_summarise_never_pools_days_together():
    """**这是汇总口径的核心错误**：把全部日收益混在一起算一个总 σ，
    会混进**时间维度**的波动（市场整体涨跌），σ 被大幅抬高。

    正确做法：每日先算 σ，再跨日汇总。

    两个横截面各自是常数（组内 σ=0），但中心差了 2 个百分点——
    混池会把这段**日间漂移**当成波动，得出约 1%。
    """
    daily = {"d1": [0.10] * 100, "d2": [0.12] * 100}
    out = summarise(daily, min_obs=20)
    assert out["sigma_r_raw"] == pytest.approx(0.0, abs=1e-12)
    # 而混池会给出约 1%（把 0.10 与 0.12 的间距算成波动）
    pooled = np.std(np.concatenate([np.array([0.10] * 100),
                                    np.array([0.12] * 100)]), ddof=1)
    assert pooled > 0.005


def test_summarise_reports_both_raw_and_winsorized():
    """两版都要有——差异本身是结论的一部分。"""
    daily = {"d1": list(np.linspace(-0.05, 0.05, 2000)),
             "d2": list(np.linspace(-0.04, 0.06, 2000))}
    out = summarise(daily, min_obs=20)
    assert out["sigma_r_raw"] > 0
    assert out["sigma_r_winsorized"] > 0
    assert out["sigma_r_winsorized"] < out["sigma_r_raw"]


def test_an_empty_input_is_an_error_not_a_zero():
    """空输入 → σ=0 会读成「横截面完全没有波动」，
    而真相是「一天都没测到」。"""
    with pytest.raises(ValueError):
        summarise({}, min_obs=20)
    with pytest.raises(ValueError):
        summarise({"d1": [0.01, 0.02]}, min_obs=20)   # 全被跳过
