"""IC → 收益——**把「排得准」翻译成「赚多少」的那座桥**。

## 为什么它必须是个模块

本项目手里有两组一直并排摆着、却从没被放在一起算的数字：

- `factor_ic_7y.json`：`rev5` 低尾 IC **+0.04863，t=24.031**（最强排序信号）
- 干净回测：正的年化超额 **0 条**，最好 **−5.06%/年**

它们不矛盾，因为**它们量的不是同一件事**：

- **IC** 是秩相关 → 「排得准不准」，**不涉及金额**
- **超额** 是收益率 → 「赚了多少」，**已扣成本**

一个因子可以每天把四千只股票排对序，而这个序对应的价差
乘以 244 次、再减掉 134%/年的成本之后，**仍然是负的**。

项目里没有任何一个函数做这个换算，于是 t=24 和 −5% 中间那步推理
**从来没有被写下来过**——两串数字各自成立，中间的桥梁是空的。
本模块就是那座桥。

## 公式

对联合高斯，正向关系是

    ρ_s = (6/π) · arcsin(ρ_p / 2)

**反函数**（本模块用的）：

    ρ_p = 2 · sin(π/6 · ρ_s)

弱信号区 `ρ_p ≈ (π/3)·ρ_s ≈ 1.047·ρ_s`。

> **这里我写错过一次**：反函数里多套了一层 `asin`
> （`2·sin(π/6·asin(r))`）。弱信号区两者都给出 ≈0.0509，
> **结论完全不受影响**；但两端 `ρ_s=1` 时它给出 **1.466**，
> 超出了定义域。**近似在中间蒙混过关、在两端炸开**——
> 只有专门钉住 `ρ_s=±1 → ρ_p=±1` 的测试才抓得到。
> 同一处我还把弱信号近似记成了 `3ρ_s`——那是 `ρ_s/ρ_p` 的近似，
> **方向反了**。两次都错在「先有结论、后凑公式」。

最高 q 分位与最低 q 分位的均值差是

    spread = ρ_p · σ_r · 2φ(z_q)/(1 − 2q)，  z_q = Φ⁻¹(1−q)

`TAIL_Q = 0.2` 时 `2φ(0.8416)/0.6 = 0.9332`，即 `TAIL_SPREAD_K`。

## σ_r 只能是实测值

`σ_r` 是**横截面**收益标准差，不是全样本波动率。
A股日频的横截面标准差在 0.02 量级，而全样本日波动约 0.015——
**两者数量级相近，最容易互串**。传错会让结论整个反号，
所以本模块**不提供任何默认 σ_r**：调用方必须显式传进来。

（`significance.json` 里的 `winner.std_daily = 0.0093` 是**组合净值**的
日波动，是**另一个东西**，不能拿来当 σ_r——它比横截面离散度小得多。）

## 这个换算给出的判断

以实测 `rev5` 低尾 IC = 0.04863、σ_r = 0.023 计：

    日频毛超额 ≈ 0.0509 × 0.023 × 0.9332 ≈ 0.109%/日 ≈ **26.7%/年**

日频门槛 154.2%（`aplus.hurdle`），**差 5.8 倍**；
月频门槛 26.4%——**刚好越过 0.3 个百分点**。

> **日频达标不是一个「努力一下能做到」的目标**：
> 反解出的所需 IC 是 0.282，是实测 0.0486 的 **5.8 倍**。
> 而月频那 0.3pp 的余量**小到不能当结论用**——
> σ_r 取 0.022 而不是 0.023，它就变成负的。
> σ_r 是本项目**唯一还没实测**的关键输入（见 `docs/` 里的待办）。
>
> **而这个判断只能由换算得出**：IC 本身没有金额，
> 回测本身已被成本吃掉、看不见毛值。
"""
from __future__ import annotations

import math

from .hurdle import DAYS_PER_YEAR

#: 本项目 IC 报告用的尾部分位（`aplus/factor_ic.py:TAIL_Q`）。
TAIL_Q = 0.20


def _norm_ppf(p: float) -> float:
    """标准正态分位数（Acklam 有理近似，|误差| < 1.15e-9）。

    只传 0.8 这一个值，所以精度要求不高；但**不能用查表**——
    表会漂，而这个常数直接乘在收益上。
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p 必须在 (0, 1)，收到 {p}")
    a = (-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _tail_spread_constant_for_q(tail_q: float) -> float:
    """`2·φ(Φ⁻¹(1−q)) / (1 − 2q)`。模块加载时算一次，供 `TAIL_SPREAD_K` 用。

    与公开的 `tail_spread_constant()` 是同一套算式；
    拆开只因为**模块级常量必须在函数定义之前算出来**。
    """
    z = _norm_ppf(1.0 - tail_q)
    phi = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    return 2.0 * phi / (1.0 - 2.0 * tail_q)

#: `2·φ(Φ⁻¹(1−q)) / (1 − 2q)`，q = 0.2 时 = 0.933206402024047。
#:
#: **由 `tail_spread_constant()` 算出来，不写死字面量**——
#: 这里先后抄过 0.9334 和 0.9332 两个「四舍五入版」，
#: 两次都对不上实算值。注释里的数会漂，算式不会；
#: 而这个常数**直接乘在收益上**，错的量级就是错的结论。
TAIL_SPREAD_K = _tail_spread_constant_for_q(TAIL_Q)


def tail_spread_constant(tail_q: float = TAIL_Q) -> float:
    """最高 q 分位与最低 q 分位的均值差，相对于 `ρ_p·σ_r` 的倍数。

        K(q) = 2·φ(Φ⁻¹(1−q)) / (1 − 2q)

    q → 0 时分母趋零、K 爆炸（只取最极端的一小撮，价差当然大）；
    q = 0.5 时 K = 0（分位重合，价差定义上为零）。
    这两个退化行为都交给调用方判断，本函数只负责算。
    """
    if not 0.0 < tail_q < 0.5:
        raise ValueError(f"tail_q 必须落在 (0, 0.5)，收到 {tail_q}")
    return _tail_spread_constant_for_q(tail_q)


def pearson_from_spearman(rho_s: float) -> float:
    """Spearman 秩相关 → Pearson 相关（联合高斯下的精确反函数）。

        正向：ρ_s = (6/π)·arcsin(ρ_p/2)
        反向：**ρ_p = 2·sin(π/6·ρ_s)**

    弱信号区约等于 `(π/3)·ρ_s ≈ 1.047·ρ_s`；两端精确收敛到 ±1。

    |ρ_s| > 1 直接报错：那是**单位错了**而不是「噪声大」，
    放行会让整条结论翻号。
    """
    r = float(rho_s)
    if not -1.0 <= r <= 1.0:
        raise ValueError(f"Spearman IC 必须在 [-1, 1]，收到 {rho_s}")
    return 2.0 * math.sin((math.pi / 6.0) * r)


def implied_spread(ic: float, sigma_r: float,
                   tail_q: float = TAIL_Q) -> float:
    """IC → **每日**尾部分位价差（收益率比例，**毛**）。

        spread = ρ_p · σ_r · K(q)

    `sigma_r` 是**横截面**收益标准差，必须由调用方显式传入：
    本模块不提供默认值，因为「典型的 σ_r」是一个会被无声取用的猜测，
    而它对结论的影响是**数量级**的。
    """
    s = float(sigma_r)
    if s <= 0.0:
        raise ValueError(
            f"σ_r 必须为正，收到 {sigma_r}。它是横截面离散度——"
            "取 0 或负会让价差反号或恒零，而函数不会崩，"
            "这种错误只让结论悄悄变错。"
        )
    rho_p = pearson_from_spearman(ic)
    return rho_p * s * tail_spread_constant(tail_q)


def annualise(daily_spread: float, days_per_year: int = DAYS_PER_YEAR
              ) -> float:
    """日频价差 → 年化毛超额。**线性**，因为这里是「复利满仓」的粗口径，
    与 `aplus.hurdle` 的门槛算术同源（门槛那边也是线性的）。"""
    return float(daily_spread) * float(days_per_year)


def required_ic(target_spread: float, sigma_r: float,
                tail_q: float = TAIL_Q) -> float:
    """反解：要多强的排序才能撑起 `target_spread` 的每日价差？

    读法是「要达到 X，需要 IC 达到 Y」。
    **返回值可以大于 1**——而那正是最有信息量的答案：
    它说明目标不是「努力一下能做到」，而是**在定义上不可能**
    （IC 的定义域是 [−1, 1]）。这时不该截断到 1，
    截断会让「差 6 倍」读成「差一点」。
    """
    t = float(target_spread)
    if t == 0.0:
        raise ValueError(
            "目标价差为 0 需要 IC = ±∞；返回 0 会被读成"
            "「随便什么信号都够」，那是完全相反的意思。"
        )
    k = tail_spread_constant(tail_q)
    rho_p = t / (float(sigma_r) * k)
    if abs(rho_p) > 1.0:
        # 超出定义域：**如实返回 inf**，不截断到 1。
        # 截断会让「差 6 倍」读成「差一点」——那正是本模块要防的事。
        return math.copysign(float("inf"), rho_p)
    # 正向关系是 ρ_s = (6/π)·arcsin(ρ_p/2)，**到 arcsin 为止**。
    # 这里曾多套一层 sin：小参数下 sin(x)≈x 把误差藏到 0.00%，
    # ρ_p=0.999 时误差达 15.8%——**又是「中间蒙混、两端炸开」**。
    return math.asin(rho_p / 2.0) * 6.0 / math.pi


if __name__ == "__main__":
    from .hurdle import hurdle_rate, rounds_per_year

    # 实测值：`factor_ic_7y.json` 的 rev5 低尾（lag1_real）
    IC_REV5_LOW = 0.04863
    SIGMA_R = 0.023          # A 股日收益横截面标准差量级

    print(f"尾部分位价差常数 K(q={TAIL_Q}) = {tail_spread_constant():.4f}")
    print(f"  （测试钉的是 {TAIL_SPREAD_K}）\n")

    for name, ic in (("rev5 低尾", IC_REV5_LOW),
                     ("rev5 全截面", 0.02617),
                     ("mom20 全截面", -0.03571)):
        sp = implied_spread(ic, sigma_r=SIGMA_R)
        gross = annualise(sp)
        print(f"{name:<14} IC={ic:>8.5f} → 日价差 {sp:>8.4%} "
              f"→ 年毛超额 {gross:>7.2%}")

    print("\n对照门槛（实况成本 0.55%/轮）：")
    for k in (1, 5, 21, 63):
        print(f"  K={k:<3} 年调仓 {rounds_per_year(k):>5.1f} → "
              f"需毛超额 {hurdle_rate(rounds_per_year=rounds_per_year(k)):>7.1%}")

    need = hurdle_rate(rounds_per_year=rounds_per_year(1))
    ic_need = required_ic(need / DAYS_PER_YEAR, sigma_r=SIGMA_R)
    print(f"\n日频达标所需的 IC：{ic_need:.3f}")
    print(f"  = 实测 rev5 低尾 IC 的 {ic_need / IC_REV5_LOW:.1f} 倍"
          f"（{IC_REV5_LOW}）")
    print("  → 仍在 IC 定义域 [-1, 1] 内，所以**不是「不可能」，"
          f"是「差 {ic_need / IC_REV5_LOW:.1f} 倍」**。")
    print("  这两句听起来像一回事，决策含义完全不同："
          "前者不必再试，后者是「还有路，但要走很远」。")
