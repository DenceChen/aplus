"""t 分布分位数——显著性判定的基础设施。

## 为什么不直接用正态近似

`excess_tstat` 对超额序列做**不重叠分块**（`block=rebalance_every`），
K=63 在 750 个交易日里只剩 **11 块**。11 个观测算出来的 t，尾巴远比分不出
正态厚：`P(|t| > 3.47 | df=10) = 0.0064`，而 df=751 时只有 0.0005，**差 13 倍**。

2026-10-14 的实际事故：`ic_guided_race_7y.json` 用正态近似的临界 3.47
放行了 `t=4.14 @ 11 块` 的候选。真实双侧 p = 2.0e-3，是 Bonferroni
阈值 0.05/97 = 5.2e-4 的 **3.9 倍**。一次假阳性晋级。

df 大时正态近似确实成立（n=752 差 0.02），所以 1123 条测试一条都没抓到——
**它只在低频策略上错，而低频正是最该测的那一档。**

## 为什么不给 scipy

本仓库的依赖只有 baostock/pandas/pyarrow/akshare/tushare，加一个
只为了两个分位数函数的科学栈不划算。这里是不完全 Beta 函数的连分式
展开（Numerical Recipes 的 `betacf`），是标准算法，无外部依赖。

## 数值约定

- `df <= 0` 返回 `nan`：两个观测算不出 t 自由度，这种情况**必须显式失败**，
  不能悄悄退化成 df=1（那会让门槛宽松到形同虚设）。
- 反解用二分法，200 次迭代把区间收到 1e-60，绰绰有余。
"""
from __future__ import annotations

import math

_MAXIT, _EPS, _FPMIN = 300, 3e-16, 1e-300


def _betacf(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数的连分式展开（Numerical Recipes §6.4）。"""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 `I_x(a, b)`。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(lb)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf_two_sided(t: float, df: int) -> float:
    """t 统计量的双侧 p 值。`df <= 0` 返回 `nan`——不可测，不是显著。"""
    if df is None or df <= 0:
        return float("nan")
    t = abs(float(t))
    if not math.isfinite(t):
        return 0.0
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def t_crit_two_sided(p: float, df: int) -> float:
    """双侧 p 值 `p` 对应的 t 临界值（二分反解）。

    `df <= 0` 返回 `inf`：两个观测无法证伪任何东西，门槛应当无穷高，
    而不是悄悄退回正态近似。
    """
    if df is None or df <= 0:
        return float("inf")
    if not (0.0 < p < 1.0):
        raise ValueError(f"p 必须在 (0,1) 内，收到 {p}")
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_sf_two_sided(mid, df) > p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)