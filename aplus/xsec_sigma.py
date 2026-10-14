"""σ_r —— **横截面**收益标准差，IC→收益换算里唯一未实测的输入。

## 为什么是 P0

`aplus/ic_to_return.py` 的换算是

    毛超额 = ρ_p · σ_r · K(q) · 244

`ρ_p` 已实测（`factor_ic_7y.json`）、`K(q)` 是解析常数、244 是常数，
**只有 σ_r 是估的**。而它单独决定月频结论的真伪：

    σ_r = 0.022 → 25.5%/年  →  够不到月频门槛 26.4%
    σ_r = 0.023 → 26.7%/年  →  刚好越过

**余量 1.2% 小于输入的估计误差**——「刚好越过」是噪声，不是结论。

## σ_r 与它的两个表亲

| | 含义 | 与 σ_r 的关系 |
|---|---|---|
| **σ_r** | **同一天**横截面上的个股收益标准差 | —— |
| 组合净值日波动 | 某个 10 只篮子的日收益标准差 | **小得多**（ diversification） |
| 全样本日波动 | 指数的日收益标准差 | 不同量级 |

**三者数量级相近，最容易互串**，而串错会让结论整个翻号。

> ⚠️ `data/reports/significance.json` 的 `winner.std_daily = 0.0093`
> 是**组合净值**的日波动，不是 σ_r。这里算出来的是另一个数。

## 本模块刻意不做的事

**不读面板、不出默认值。** 只提供「拿到一组当日收益就算 σ」的纯函数，
读面板的口径留在 `scripts/measure_xsec_sigma.py`——
因为口径（池子、缩尾）会随研究变化，而**公式不该跟着变**。

## 汇总口径：每日先算，再跨日

**绝不能把所有日收益混成一个池子算总标准差**——
那会混进**时间维度**的波动（市场整体涨跌），σ 被系统性抬高。
正确做法是逐日算 σ_c，再对 σ_c 汇总。

（`tests/test_xsec_sigma.py::test_summarise_never_pools_days_together` 钉住这条。）
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

import numpy as np

#: 单日横截面少于这么多只就不给 σ。
#: 与 `aplus/factor_ic.py:MIN_PAIRS = 20` 同源——
#: **σ_r 与 ρ_p 必须配对**，池子差太远会让两者的隐含总体不同。
MIN_OBS = 20


def cross_sectional_sigma(returns: Iterable[float], *,
                          min_obs: int = MIN_OBS,
                          winsorize: tuple[float, float] | None = None) -> float:
    """**单日**横截面收益的标准差。

    `returns` 是**当日**各只股票的收益率（比例口径，不是百分数——
    传 ``1.0`` 表示 +100%，返回 1.0 表示标准差 100%）。

    非有限值（NaN / ±inf，停牌与缺失会产生）**成对剔除**。
    剔除后不足 `min_obs` 直接报错——静默返回一个数会
    **看起来完全正常**：3 只股票算出的 σ 与 4000 只算出的没有量纲差别。

    `winsorize=(lo, hi)` 时按分位缩尾，`0.0 < lo < hi < 1.0`（**开区间**）。

    A股涨跌停（±10%、ST ±5%、创业板/科创 ±20%）会在尾部堆质量，
    把 σ 系统性抬高；**缩尾版必须一并报出**，两版之差本身就是结论的一部分。

    > ⚠️ **缩尾会静默失效**：当压在极值上的质量**超过**缩尾比例时，
    > 分位落进那堆质量内部，`clip` 退化成恒等变换，σ 一个数都不变。
    > 极端行情下涨跌停比例轻松超过 1%，所以这不是边角情形。
    >
    > 危险的不是它没效果，是它**看起来有效**——返回了一个正常的数，
    > 只是不是你想要的数。所以 `summarise` 两版都报：
    > **两版相等本身就是失效的证据**，读者能当场发现。
    > （`tests/test_xsec_sigma.py::test_winsorizing_silently_does_nothing_...` 钉住）
    """
    x = np.asarray(list(returns), dtype=float)
    x = x[np.isfinite(x)]
    if x.size < min_obs:
        raise ValueError(
            f"横截面只有 {x.size} 个有限观测，少于 min_obs={min_obs}——"
            "不给出 σ（小样本的 σ 与全样本的 σ 没有量纲差别，"
            "返回它会让错误**看起来正常**）。"
        )
    if winsorize is not None:
        lo, hi = winsorize
        # `0.0 < lo < hi < 1.0`，**闭区间端点一律拒绝**：
        # lo=0 时分位就是最小值本身，clip 到最小值 = 没 clip——
        # 那是一次**静默失效**的守卫（返回了一个数，只是不是你想要的数）。
        if not (0.0 < lo < hi < 1.0):
            raise ValueError(
                f"winsorize 必须 0<lo<hi<1，收到 {winsorize}。"
                f"lo=0 / hi=1 会让分位落在极值自身上，clip 退化成恒等变换。"
            )
        lo_v, hi_v = np.quantile(x, [lo, hi])
        # 对称分位缩尾：只动尾部，均值不变
        x = np.clip(x, lo_v, hi_v)
    return float(np.std(x, ddof=1))


def summarise(daily_returns: Mapping[str, Sequence[float]], *,
              min_obs: int = MIN_OBS,
              winsorize: tuple[float, float] = (0.01, 0.99)) -> dict:
    """逐日算 σ_r，再跨日汇总。

    **每日先算，再汇总**——绝不把所有日的收益混成一个池子算总标准差，
    那会混进时间维度的波动（市场整体涨跌），σ 被系统性抬高。

    样本不足的日期**跳过，但必须报出跳过了几天**
    （`n_skipped`）——静默跳过会让 `n_days` 与 IC 报告对不上
    而没人察觉，那是本项目最难发现的一类信息丢失。
    """
    if not daily_returns:
        raise ValueError("没有任何交易日——σ=0 会被读成「完全没有波动」")

    raw_vals: list[float] = []
    win_vals: list[float] = []
    sizes: list[int] = []
    n_skipped = 0

    for _date, rets in daily_returns.items():
        arr = np.asarray(list(rets), dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size < min_obs:
            n_skipped += 1
            continue
        raw_vals.append(cross_sectional_sigma(arr, min_obs=min_obs))
        win_vals.append(cross_sectional_sigma(arr, min_obs=min_obs,
                                              winsorize=winsorize))
        sizes.append(int(arr.size))

    if not raw_vals:
        raise ValueError(
            f"{len(daily_returns)} 天全部因样本不足被跳过——"
            "返回空汇总会让下游以为「没测到波动」而不是「没测到数据」。"
        )

    r = np.asarray(raw_vals, dtype=float)
    w = np.asarray(win_vals, dtype=float)
    return {
        "sigma_r_raw": float(r.mean()),
        "sigma_r_winsorized": float(w.mean()),
        "sigma_r_median": float(np.median(r)),
        "sigma_r_p05": float(np.quantile(r, 0.05)),
        "sigma_r_p95": float(np.quantile(r, 0.95)),
        "winsorize": list(winsorize),
        "n_days": len(raw_vals),
        "n_skipped": n_skipped,
        "avg_n": float(np.mean(sizes)),
        "unit": "收益率比例（0.023 = 2.3%/日）",
    }


def implied_gross_excess(ic: float, sigma_r: float,
                         tail_q: float | None = None) -> float:
    """便捷入口：把实测 σ_r 喂给 IC→收益换算，返回**年毛超额**。

    存在的意义是**让 σ_r 的出处可追溯**——报告里出现一个年毛超额时，
    能顺着它找到 σ_r 是实测的、用什么池子、缩没缩尾。
    """
    from .ic_to_return import TAIL_Q, annualise, implied_spread

    return annualise(implied_spread(ic, sigma_r=sigma_r,
                                    tail_q=TAIL_Q if tail_q is None
                                    else tail_q))


if __name__ == "__main__":
    # 自检：两个横截面，σ 应为 0.01（均匀分布的标准差）
    demo = {"d1": [0.00, 0.02] * 50, "d2": [-0.01, 0.01] * 50}
    out = summarise(demo)
    for k, v in out.items():
        print(f"  {k:<22} {v}")
