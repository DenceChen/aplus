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

import numpy as np
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

    ## ⚠️ 这条测试**抓不到** 2026-10-14 那个 3 倍错误，而它一直在绿

    它断言的是 `TAIL_SPREAD_K == tail_spread_constant()`——
    **公式与它自己**。公式改成 `2φ(z_q)/q`（正确）或
    `2φ(z_q)/(1−2q)`（错 3 倍），这条断言**都通过**。

    > 这就是 CLAUDE.md 那条「**只钉前半会假绿**」的又一例：
    > 钉住「代码与代码一致」只能防**抄错字面量**，
    > 防不住「**整条公式写错**」。
    > 防后者只有一条路：**外部锚**——下面两条。
    """
    assert TAIL_SPREAD_K == pytest.approx(tail_spread_constant(), abs=1e-12)


def test_the_constant_must_match_a_simulation_of_the_world_it_assumes():
    """**外部锚**：在高斯世界里**真的做一次分位**，看常数对不对。

    这是唯一能抓住「整条公式写错」的测试。构造让 `ρ_p = 1`
    （收益 = σ·z，分数 = z 的秩，Spearman = 1），
    于是真值必然是 `σ·K(q)`——用 40 万个样本实测分位差去对。

    > 旧常数 0.9332 在这里差 **3 倍**，测试**当场红**。
    """
    from aplus.ic_to_return import implied_spread, tail_spread_constant

    rng = np.random.default_rng(13)
    n, sigma = 400_000, 0.02656
    z = rng.normal(size=n)
    score = np.argsort(np.argsort(z)).astype(float)      # 严格单调 ⇒ ρ_s = 1
    ret = sigma * z                                      # ⇒ ρ_p = 1

    k = int(n * 0.20)
    order = np.argsort(score, kind="stable")
    measured = ret[order[-k:]].mean() - ret[order[:k]].mean()

    assert measured == pytest.approx(
        implied_spread(1.0, sigma_r=sigma, kind="spearman"), rel=0.02)
    assert measured == pytest.approx(
        sigma * tail_spread_constant(0.20), rel=0.02)
    # 旧常数：0.9332。差 3 倍，这个断言正是它的照妖镜
    assert TAIL_SPREAD_K != pytest.approx(2 * 0.28 / 0.6, rel=0.02)


def test_the_constant_must_be_finite_at_the_half_split():
    """**端点行为本身也是判据。** `q → 0.5` 时 K 必须收敛到 **1.5958**。

    旧常数 `2φ(z_q)/(1−2q)` 在这里**发散到 ∞**——
    而「上半减下半」的答案显然有限（`E[Z|Z>0] = 0.7979`，
    差 = 1.5958）。**一个在答案显然有限处发散的公式，写错了。**

    > 旧 docstring 在这里写「K = 0（分位重合）」——
    > 上半与下半**不重合**，那句话是假的，而它**恰好把破绽盖住了**。
    > 「注释为错误的公式提供理由」是最难查的一类静默失效，
    > 所以这一条把**数值**钉死，不信注释。

    `tail_q = 0.5` 本身在定义域外（两段刚好不重叠），所以取 `0.499` 看**极限**；
    `q = 0.5` 抛 `ValueError` 是**对的**，一并钉住。
    """
    assert tail_spread_constant(0.499) == pytest.approx(1.5958, abs=0.01)
    # 旧常数在这里是 39.9 —— 差 25 倍
    assert tail_spread_constant(0.499) < 2.0
    with pytest.raises(ValueError):
        tail_spread_constant(0.5)


def test_the_constant_must_fall_as_the_tail_gets_more_extreme():
    """**单调性是最便宜的判据** —— 它不需要任何蒙特卡洛。

    取更极端的一小撮股票，价差**必须更大**，
    所以 `K(q)` 必须在 (0, 0.5) 上**严格递减**。

    | q | 1e-6 | 0.01 | 0.05 | 0.2 | 0.4 | 0.49 |
    |---|---|---|---|---|---|---|
    | K | 9.897 | 5.330 | 4.125 | 2.800 | 1.932 | 1.628 |

    > 旧公式 `2φ(z_q)/(1−2q)` 的单调性**正好相反**：
    > 它随 q 递增，且在 q→0 处趋于 **0**——
    > 也就是「只看最极端的那 0.0001% 反而价差最小」。
    > **这句话在经济学上说不通**，所以单调性一条就能判它死刑。
    >
    > 写这条测试时我先断言了「递增」，实测是**递减**。
    > **又一次：要么读，要么别猜。**
    """
    grid = [1e-6, 0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.49]
    ks = [tail_spread_constant(q) for q in grid]
    assert all(b < a for a, b in zip(ks, ks[1:])), \
        f"K 必须随 q 递减，实际 {[f'{k:.3f}' for k in ks]}"
    for q, k in zip(grid, ks):
        assert k == pytest.approx({1e-6: 9.8967, 0.01: 5.3304, 0.05: 4.1254,
                                   0.1: 3.5100, 0.2: 2.7996, 0.3: 2.3180,
                                   0.4: 1.9317, 0.49: 1.6278}[q], rel=1e-3)
    # q → 0 的极限是 `≈ 2·z_q`，**很慢**地发散；不是「一进去就爆炸」
    assert tail_spread_constant(1e-6) == pytest.approx(9.8967, rel=1e-3)


def test_zero_ic_means_zero_edge():
    """完全无信号的因子必须给出**精确的零**，不是「接近零」。
    「接近零」会让下游的门槛比较在边界上摇摆。"""
    assert implied_spread(0.0, sigma_r=0.023, kind="spearman") == 0.0


def test_the_spread_is_linear_in_sigma_r():
    """σ_r 加倍，价差加倍——这是公式的定义性质，钉住它防止有人加非线性项。"""
    a = implied_spread(0.0486, sigma_r=0.01, kind="spearman")
    b = implied_spread(0.0486, sigma_r=0.02, kind="spearman")
    assert b == pytest.approx(2 * a, rel=1e-9)


def test_a_negative_ic_gives_a_negative_spread_and_does_not_raise():
    """负 IC 是**做空那条腿的信号**，在本项目里完全正常
    （`mom20` 全段 IC −0.0357）。把它当错误抛出，
    等于把一整族可用信号判死。"""
    assert implied_spread(-0.0357, sigma_r=0.023, kind="spearman") < 0


def test_a_non_positive_sigma_is_rejected():
    """σ_r ≤ 0 会让价差反号或恒零，而**函数不会崩**——
    这种错误只让结论悄悄变错，比崩溃危险得多。"""
    for bad in (0.0, -0.023):
        with pytest.raises(ValueError):
            implied_spread(0.0486, sigma_r=bad, kind="spearman")


# ── 三、年化与门槛：这才是对用户目标的回答 ────────────────────────

def test_the_daily_spread_annualises_by_trading_days():
    assert annualise(0.001) == pytest.approx(0.001 * 244, rel=1e-9)


def test_the_rev5_signal_is_twice_short_of_the_daily_hurdle():
    """**本文件最重要的一条。**

    实测 `rev5` 低尾 IC = 0.04863（t=24.031），σ_r = 0.023：

        ρ_p   = 2·sin(π/6·0.04863) = 0.05089
        毛超额 = 0.05089 × 0.023 × **2.7996** ≈ 0.3279%/日 ≈ **80.0%/年**

    日频门槛 154.2% → **还差 1.93 倍**。

    > 这条断言的价值不在数字，在于它**把「排序强」翻译成了「离目标多远」**。
    > 没有这个换算，t=24 会被当成「快到了」。
    >
    > **它 2026-10-14 改过一次**：原来钉的是 26.7%/年与「差 5.8 倍」，
    > 而那组数来自 `K = 2φ(z_q)/(1−2q)` —— **分母用错，差整 3 倍**。
    >
    > **教训不在常数错了，而在「把公式的当前输出硬编码进断言」
    > 会把任何一次错误公式一起冻结**：这条测试从写下的那天起
    > 就把 3 倍误差当成了正确答案；每次重算都红，
    > 而**红灯会被读成「环境变了」而不是「原来错了」**。
    >
    > 它的对家就在同文件里：
    > `test_the_tail_spread_constant_is_the_one_the_code_computes`
    > 断言 `TAIL_SPREAD_K == tail_spread_constant()` —— **公式与自己一致**，
    > 所以公式从写错那天起就**永远绿**。
    > **硬编码冻结**与**自指恒绿**是同一枚硬币的两面：
    > 一个抓不到公式变对，另一个抓不到公式变错。**两者都要外部锚。**
    """
    from aplus.hurdle import hurdle_rate, rounds_per_year

    spread = implied_spread(0.04863, sigma_r=0.023, kind="spearman")
    gross = annualise(spread)

    daily_hurdle = hurdle_rate(rounds_per_year=rounds_per_year(1))

    assert gross == pytest.approx(0.800, abs=0.005)
    assert daily_hurdle / gross == pytest.approx(1.93, rel=0.02)


def test_the_bridge_now_returns_numbers_too_large_to_trust():
    """**今天最重要的结论：桥已经不能当门槛计算器用了。**

    修正常数后，单因子 `rev5` 就给出 **80%/年**毛超额；
    判决用的合成 IC 0.0709 给出 **约 117%/年**（σ_r = 0.023）。

    这不是好消息，而是**模型失效的信号**：同一个线性外推式，
    常数放大 3 倍就把结论吹成荒谬量级——说明错的**不只是常数**，
    而是「IC 可线性外推成分位价差」这个**形状**在该 IC 水平上不成立。

    > 所以本文件原有那条「月频余量薄到不能当结论」的测试**被删了**：
    > 修正常数后余量变成 3 倍，那条结论连同它的前提一起作废。
    > 替代它的诚实结论是本条 —— **桥给不出可信的数了**。
    >
    > 真正的数只有 `scripts/measure_bridge_error.py` **直接测出来**的那个。
    > 判决脚本打印的「年毛超额」从此只能当**量级参照**，不能当预测。
    """
    from aplus.hurdle import hurdle_rate, rounds_per_year

    rev5 = annualise(implied_spread(0.04863, sigma_r=0.023, kind="spearman"))
    composite = annualise(implied_spread(0.0709, sigma_r=0.023, kind="spearman"))
    monthly = hurdle_rate(rounds_per_year=rounds_per_year(21))

    assert rev5 > 0.7, f"单因子年毛 {rev5:.1%} 已超出任何可信范围"
    assert composite > 1.0, f"合成年毛 {composite:.1%} 荒谬"
    # 「过线 3 倍」这种话本身就该被打问号 —— 月频门槛只有 26.4%
    assert composite / monthly > 3.0


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
        assert implied_spread(ic, sigma_r=0.023, kind="spearman") == pytest.approx(
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


def test_daily_needs_an_ic_about_twice_any_measured():
    """日频要 154.2% 毛超额 → 反解出的所需 IC ≈ **0.0938**（σ_r = 0.023）。

    是实测最强值 0.0486 的 **1.93 倍**。

    > 「定义上不可能」与「差 1.93 倍」听起来像一回事，
    > 但决策含义完全不同：前者不必再试，后者是「还有路，但要走很远」。
    > 修正前这里是 0.279 / 5.7 倍 —— **「要走很远」变成「基本够得着」**，
    > 而那个翻转**完全来自一个分母**。
    """
    from aplus.hurdle import hurdle_rate, rounds_per_year

    need_gross = hurdle_rate(rounds_per_year=rounds_per_year(1))
    ic = required_ic(need_gross / 244, sigma_r=0.023)

    assert ic == pytest.approx(0.0938, abs=0.005)
    assert ic < 1.0, "仍在定义域内 —— 不是「不可能」，是「差 1.93 倍」"
    assert ic / 0.04863 == pytest.approx(1.93, rel=0.02)


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


# ── 口径必须由调用方声明，不许由函数猜 ────────────────────────────
#
# ## 这里出过一次真实的静默失效
#
# `implied_spread` 的公式 `spread = ρ_p · σ_r · K(q)` 来自联合高斯下的
# 秩↔Pearson 恒等式，所以它**期待 Spearman**，内部再转成 ρ_p。
#
# 但 `aplus/composite.py` 的 `composite_ic` 算的是 z 标准化**原始值**与收益的
# **Pearson** 相关。一个 Pearson 喂进期待 Spearman 的函数，
# 于是**在已经换算过一次的口径上又乘了一次 1.047**——
# 而它直接乘在年化收益上。
#
# 更隐蔽的是另一层：`required_ic` 返回的**是 ρ_s**，
# 所以判决那一步是「拿 Spearman 门槛比一个 Pearson 实测值」，
# **口径错在宽松方向**，而且两边都各自自洽，不会有任何报错。

def test_the_ic_kind_must_be_declared_not_guessed():
    """`kind` 必须**必填**——不给默认值，让编译器逼每个调用点表态。

    > 给默认值「Spearman」看着无害：现存调用点大多确实是 Spearman。
    > 但下一个拿着原始值 IC 的人会拿到**又一次静默换算**，
    > 而且**没有任何迹象**表明换过。
    """
    with pytest.raises(TypeError):
        implied_spread(0.05, sigma_r=0.023)          # 完全不给 kind
    with pytest.raises(TypeError):
        implied_spread(0.05, 0.023, "spearman")      # 位置参数也不收：kind 是 keyword-only
    with pytest.raises(TypeError):
        implied_spread(0.05, sigma_r=0.023, tail_q=0.2)


def test_declaring_pearson_must_skip_the_conversion_entirely():
    """`kind="pearson"` 时**一次都不许换算**。

    1.047 倍不是小数点后第三位的事：0.0624 的 IC 会被报成 39.51%
    而不是 37.75%，年化收益上白送 1.76 个百分点。
    """
    ic = 0.0624
    got = implied_spread(ic, sigma_r=0.02656, kind="pearson")
    assert got == pytest.approx(ic * 0.02656 * TAIL_SPREAD_K, rel=1e-12)
    assert got < implied_spread(ic, sigma_r=0.02656, kind="spearman")
    # 差值就是那 1.047
    assert (implied_spread(ic, sigma_r=0.02656, kind="spearman") / got
            ) == pytest.approx(1.047, abs=0.002)


def test_the_two_kinds_must_agree_at_the_gaussian_fixed_point():
    """联合高斯下两种口径**必须**给出同一个 ρ_p——否则恒等式写错了。

    Pearson 反推 Spearman 再正推回来，在联合高斯里是恒等映射；
    不一致就说明两条路里有算错的。
    """
    import math
    for rho_p in (0.02, 0.05, 0.15, 0.4, 0.8):
        rho_s = math.asin(rho_p / 2.0) * 6.0 / math.pi
        back = implied_spread(rho_s, sigma_r=1.0, kind="spearman")
        assert back == pytest.approx(rho_p * TAIL_SPREAD_K, rel=1e-12)
        assert implied_spread(rho_p, sigma_r=1.0,
                              kind="pearson") == pytest.approx(back, rel=1e-12)


def test_an_unknown_kind_is_refused_rather_than_silently_treated_as_spearman():
    """拼错的 `kind="spearmann"` 必须报错，不能回落到 Spearman。

    回落就是**换个名字的静默失效**——它正是这条测试存在的理由。
    """
    with pytest.raises(ValueError):
        implied_spread(0.05, sigma_r=0.023, kind="spearmann")
    with pytest.raises(ValueError):
        implied_spread(0.05, sigma_r=0.023, kind="")


def test_pearson_outside_the_domain_is_refused_too():
    """Spearman 有 ±1 的定义域，Pearson 有——但两者是**不同的守卫**。

    换算只在联合高斯下有意义；越界说明单位/口径错了，放行会让结论翻号。
    """
    implied_spread(1.0, sigma_r=0.023, kind="pearson")   # 恰好合法
    with pytest.raises(ValueError):
        implied_spread(1.4, sigma_r=0.023, kind="pearman")
