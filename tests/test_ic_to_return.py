"""IC → 收益：**本项目一直缺的那座桥**。

## 为什么它是缺的

`data/reports/factor_ic_7y.json` 里 `rev5` 的低尾 IC 是 **+0.04863，t=24.031**
——本项目测过的最强排序信号，没有之一。

而所有**干净**的回测（剔除基准污染的 `FOSSAL_*` 与 `regime_control_sweep`）
里，正的年化超额数量是 **0 条**，最好的 `annual_excess` 是 **−5.06%/年**。

这两件事看起来矛盾，实际上**说的不是同一件事**：

| | 度量 | 问题 |
|---|---|---|
| IC | 秩相关 | 「排得准不准」——**不涉及金额** |
| annual_excess | 收益率 | 「赚了多少」——**已扣成本** |

**IC 高完全可以与收益为负共存**：一个因子每天都能把 4368 只股票排对序，
而这个序对应的价差，乘以 244 次，再减掉 134%/年的成本，还是负的。

**项目里没有任何一个函数做这个换算**——所以「t=24」和「−5%」被并排印出来，
中间那步推理从来没被写下来过。本文件就是把它写下来。

## 换算公式（为什么是这个）

Spearman IC ≈ ρ_pearson / 3（对高斯，ρ 小的时候）。
而「最高 q 分位」与「最低 q 分位」的**均值差**在联合高斯下是：

    spread ≈ ρ_pearson · σ_r · (2 · φ(z_q) / (1 − 2q))

其中 `σ_r` 是横截面收益标准差，`φ` 是标准正态密度。
本项目把 `TAIL_Q = 0.2`，于是 `z_q = Φ⁻¹(0.8) = 0.8416`，
`2φ(0.8416)/(1−0.4) = 0.56/0.6 = 0.9334`。

所以 **`IC × σ_r × 0.9334 ≈ 每日多空价差**。

`σ_r` 只能实测——它是横截面离散度，不是全样本波动率，
**两者差一个数量级**，拿错会让结论整个反号（见下面的反向钉）。

## 这座桥给出的判断

用实测 `σ_r ≈ 0.023`（A股日收益横截面标准差量级）：

    日频毛超额 ≈ 0.0486 × 0.023 × 0.9334 ≈ 0.104%/日 ≈ **25.5%/年**

而日频门槛是 **154.2%**——**差 6 倍**。

> **这不是「再调调参」，是「排序能力离目标差一个数量级」。**
> 这个结论只能由换算得出，IC 本身给不出（IC 没有金额），
> 回测也给不出（回测已经被成本吃掉了，看不见毛值）。

## 三条纪律

1. **`σ_r` 必须传进来**，不许在函数里写死一个「typical」值。
2. **负成本 / 零成本**要报错，否则门槛会低于目标，读起来像「调仓越多越好」。
3. **重算后绝不能引用旧报告的绝对数字**——口径变了，数字全部作废。
"""
import math

import pytest

from aplus.ic_to_return import (
    TAIL_SPREAD_K,
    annualise,
    implied_spread,
    pearson_from_spearman,
    required_ic,
    tail_spread_constant,
)


# ── 一、Spearman → Pearson ─────────────────────────────────────────

def test_spearman_and_pearson_are_close_for_a_weak_signal():
    """ρ 小的时候 `ρ_p ≈ (π/3)·ρ_s ≈ 1.047·ρ_s`。

    > 我第一次写的是 `3ρ_s`，**方向反了**——`3/π ≈ 0.955` 是
    > 「Spearman ≈ 0.955·Pearson」那一侧，即 `ρ_s ≈ ρ_p·3/π`。
    > 两个数只差符号方向，而符号方向错误会**直接把正负判反**。
    """
    assert pearson_from_spearman(0.0486) == pytest.approx(1.047 * 0.0486,
                                                         rel=0.01)


def test_a_perfect_spearman_maps_to_a_perfect_pearson():
    """退化情形必须精确。

    > 这条测试的存在理由：我的实现里多套了一层 `asin`
    > （`2·sin(π/6·asin(r))`）。弱信号区两者都给出 ≈0.0509，
    > **结论完全不受影响**；只有 `ρ_s=±1` 才暴露——它给出 **1.466**。
    > **近似在中间蒙混过关、在两端炸开。**
    """
    assert pearson_from_spearman(1.0) == pytest.approx(1.0, abs=1e-9)
    assert pearson_from_spearman(-1.0) == pytest.approx(-1.0, abs=1e-9)


def test_the_inverse_never_leaves_the_unit_interval():
    """整条曲线的值域必须始终在 [−1, 1] 内。

    > 上一条测试只钉住了两个端点。这条钉住**全体**——
    > 一个在中间某处越界的实现（例如误用 arcsin 分支）能通过端点测试。
    """
    for i in range(-100, 101):
        r = i / 100.0
        p = pearson_from_spearman(r)
        assert -1.0 - 1e-12 <= p <= 1.0 + 1e-12, f"ρ_s={r} → ρ_p={p}"


def test_the_map_is_monotone_across_the_whole_range():
    """单调性是这条曲线的全部意义；非单调会让「IC 更高 = 更好」反号。"""
    xs = [i / 200 - 1.0 for i in range(401)]
    ys = [pearson_from_spearman(x) for x in xs]
    assert all(b >= a - 1e-12 for a, b in zip(ys, ys[1:]))


def test_an_ic_outside_the_unit_interval_is_rejected():
    """|ρ|>1 不是「噪声很大」，是**单位错了**——放行会让结论整个翻号。"""
    for bad in (1.5, -1.5, 2.0):
        with pytest.raises(ValueError):
            pearson_from_spearman(bad)


# ── 二、价差 = IC × σ_r × 常数 ────────────────────────────────────

def test_the_tail_spread_constant_is_the_one_the_code_computes():
    """常数必须**等于 `tail_spread_constant()` 的实算值**，不能只是注释里的数。

    > 它曾写成 0.9334 而实算是 0.93320——差 2 个万分点，
    > 落在 `abs=1e-3` 的容差里所以测试是绿的，
    > 而 `__main__` 却把两个数并排印出来、让人以为它们一样。
    > **容差放宽一次，注释就开始漂。** 现在钉死等式。
    """
    assert TAIL_SPREAD_K == pytest.approx(tail_spread_constant(), abs=1e-12)


def test_zero_ic_means_zero_edge():
    """完全无信号的因子必须给出**精确的零**，不是「接近零」。
    「接近零」会让下游的门槛比较在边界上摇摆。"""
    assert implied_spread(0.0, sigma_r=0.023) == 0.0


def test_the_spread_is_linear_in_sigma_r():
    """σ_r 加倍，价差加倍——这是公式的定义性质，钉住它防止有人加非线性项。"""
    a = implied_spread(0.0486, sigma_r=0.01)
    b = implied_spread(0.0486, sigma_r=0.02)
    assert b == pytest.approx(2 * a, rel=1e-9)


def test_a_negative_ic_gives_a_negative_spread_and_does_not_raise():
    """负 IC 是**做空那条腿的信号**，在本项目里完全正常
    （`mom20` 全段 IC −0.0357）。把它当错误抛出，
    等于把一整族可用信号判死。"""
    assert implied_spread(-0.0357, sigma_r=0.023) < 0


def test_a_non_positive_sigma_is_rejected():
    """σ_r ≤ 0 会让价差反号或恒零，而**函数不会崩**——
    这种错误只让结论悄悄变错，比崩溃危险得多。"""
    for bad in (0.0, -0.023):
        with pytest.raises(ValueError):
            implied_spread(0.0486, sigma_r=bad)


# ── 三、年化与门槛：这才是对用户目标的回答 ────────────────────────

def test_the_daily_spread_annualises_by_trading_days():
    assert annualise(0.001) == pytest.approx(0.001 * 244, rel=1e-9)


def test_the_rev5_signal_is_six_times_short_of_the_daily_hurdle():
    """**本文件最重要的一条。**

    实测 `rev5` 低尾 IC = 0.04863（t=24.031），
    A 股日收益横截面标准差量级 σ_r = 0.023：

        ρ_p   = 2·sin(π/6·0.04863) = 0.05089
        毛超额 = 0.05089 × 0.023 × 0.9332 ≈ 0.109%/日 ≈ **26.7%/年**

    日频门槛 154.2% → **差 5.8 倍**。

    > 这条断言的价值不在数字，在于它**把「排序强」翻译成了「离目标多远」**。
    > 没有这个换算，t=24 会被当成「快到了」。
    """
    from aplus.hurdle import hurdle_rate, rounds_per_year

    spread = implied_spread(0.04863, sigma_r=0.023)
    gross = annualise(spread)

    daily_hurdle = hurdle_rate(rounds_per_year=rounds_per_year(1))

    assert gross == pytest.approx(0.267, abs=0.005)
    assert gross * 5.8 == pytest.approx(daily_hurdle, rel=0.05)


def test_the_monthly_clearing_is_too_thin_to_call_a_conclusion():
    """**这条是本文件最容易被误读的地方，故单独钉住。**

    月频门槛 26.4%，而 `rev5` 低尾换算出 **26.7%**——
    **形式上刚好越过 0.3 个百分点**。

    但 σ_r 是本项目**唯一还没实测**的关键输入：
    取 0.022 而不是 0.023，同样的 IC 就掉到 25.5% —— 不够。

    > 所以正确的结论不是「月频可行」，而是
    > **「月频的可行性完全取决于一个尚未测量的数」**。
    > 把它写成「差一点点」或「基本可行」都是过度解读。
    """
    from aplus.hurdle import hurdle_rate, rounds_per_year

    monthly_hurdle = hurdle_rate(rounds_per_year=rounds_per_year(21))
    at_023 = annualise(implied_spread(0.04863, sigma_r=0.023))
    at_022 = annualise(implied_spread(0.04863, sigma_r=0.022))

    assert at_023 > monthly_hurdle, "σ_r=0.023 时刚好越过月频门槛"
    assert at_022 < monthly_hurdle, "σ_r 降 4% 就掉下去——余量比噪声薄"
    # 关键：结论对 σ_r 敏感，所以**不能**当成「月频可行」
    assert (at_023 - monthly_hurdle) / monthly_hurdle < 0.02


def test_required_ic_inverts_the_formula():
    """反解必须**精确回到原值**——它会出现在报告里，
    读法是「要达到 X 需要多强的排序」。

    > 这条测试第一版只试了三个**小**目标价差，于是完美地
    > 放过了同一个 bug：`required_ic` 里多套了一层 `sin`，
    > 而 `sin(x) ≈ x` 在小参数下把误差压到 0.00%。
    > 同一组 IC 换算代码，这已经是**第三次**栽在
    > 「小参数自洽、两端失真」上了。
    """
    for target_spread in (0.0005, 0.001, 0.002):
        ic = required_ic(target_spread, sigma_r=0.023)
        assert implied_spread(ic, sigma_r=0.023) == pytest.approx(
            target_spread, rel=1e-9)


def test_the_round_trip_holds_across_the_whole_definition_domain():
    """**域覆盖**才是抓住「小参数蒙混」的唯一办法。

    在 ρ_p ∈ (0, 1) 上均匀取样，要求反解-正解往返误差 < 1e-9。
    多套的那层 `sin` 在 ρ_p=0.999 处误差 **15.8%**，
    而在本项目实测的 ρ_p≈0.05 附近只有 **0.06%**——
    **只测工作点，等于没测。**
    """
    for i in range(1, 100):
        rho_p = i / 100.0
        # 由 ρ_p 反解目标价差，再用 required_ic 转回 IC
        target = rho_p * 0.023 * tail_spread_constant()
        ic = required_ic(target, sigma_r=0.023)
        back = pearson_from_spearman(ic)
        assert back == pytest.approx(rho_p, abs=1e-9), \
            f"ρ_p={rho_p} 往返得到 {back}"


def test_daily_needs_an_ic_five_times_stronger_than_any_measured():
    """日频要 154.2% 毛超额 → 反解出的所需 IC ≈ **0.279**。

    > 我最初写的是「所需 IC > 1，超出定义域、不可能」。
    > **那是错的**：0.279 稳稳落在 [−1, 1] 内。
    > 真正成立的结论是别的，而且更有用：
    > **所需 IC 是实测最强值 0.0486 的 5.7 倍。**
    >
    > 「定义上不可能」与「差 5.7 倍」听起来像一回事，
    > 但决策含义完全不同：前者不必再试，后者是「还有路，但要走很远」。
    """
    from aplus.hurdle import hurdle_rate, rounds_per_year

    need_gross = hurdle_rate(rounds_per_year=rounds_per_year(1))
    ic = required_ic(need_gross / 244, sigma_r=0.023)

    assert ic == pytest.approx(0.279, abs=0.005)
    assert ic < 1.0, "仍在定义域内——不是「不可能」，是「差 5.7 倍」"
    assert ic / 0.04863 == pytest.approx(5.7, rel=0.05)


def test_an_out_of_domain_demand_is_reported_as_infinity_not_clamped():
    """若目标本身超出 IC 的上限（低 σ_r + 极高目标），
    必须返回 `inf`——**截断到 1 会让「差 6 倍」读成「差一点」**。"""
    # σ_r 极小 + 目标极高 ⇒ 所需 ρ_p 远超 1
    ic = required_ic(0.05, sigma_r=0.001)
    assert ic == float("inf")


def test_required_ic_rejects_a_zero_target_spread():
    """目标价差为 0 → 需要 IC = ±∞。返回 0 会读成
    「随便什么信号都够」，返回 inf 才对。"""
    with pytest.raises(ValueError):
        required_ic(0.0, sigma_r=0.023)
