#!/usr/bin/env python
"""前瞻 D2 通道的**时间账**：还要攒多少个独立块、还要等多少个日历日。

## 为什么需要这个产物

`forward_d2_verdict.json` 回答的是「**现在**能不能定出超额符号」（答案：`POWERLESS`）。
它**不含**任何「还要多久」字段 —— 那不是它的问题，是 R5 明禁的（不外推年化）。

⇒ 结果是：今天问「那还要等多久」，仓里**没有任何字段能回答**。
CLAUDE.md 观测墙那张「需 174 / 现有 66 / 2.6×」是**回测口径**
（观测单位 = K=21 一次调仓、目标 t = 2.80、来自 `factor_width_mde.json`），
与前瞻通道**不是同一个量**（第 7 层：两个各自正确的数当成同一个量比）：

| | 观测墙 | 本产物 |
|---|---|---|
| 观测单位 | 一次调仓（K=21 日） | 一个 `entry_date` 成交窗口块 |
| 目标 t | 2.80（80% 检出力） | 见 `t_target` 表（判决臂口径 z） |
| 数据源 | 回测全窗口 2021 起 | 前瞻底稿追加式 |

⇒ **两者不许并列引用、不许相加、不许说「总的要等 X」。**

## 外部锚（书，不是自研外推）

Chan《Quantitative Trading》p.85，`data/books/chan_lines.txt:2567-2578`
（引 Bailey et al. 2012, "The Probability of Backtest Overfitting"）逐字：

> 1. If you want to be statistically confident (at the 95 percent level) that your
>    true Sharpe ratio is equal to or greater than 0, you need a backtest Sharpe
>    ratio of 1 and a sample size of **681** data points (e.g., **2.71 years** of daily data).
> 2. The higher the backtest Sharpe ratio, the smaller the sample size is needed.
>    If your backtest Sharpe ratio is **2** or more, then you need only **174**
>    data points (0.69 years of daily data) to be confident...
> 3. If you want to be confident that your true Sharpe ratio is equal to or greater
>    than 1, then you need a backtest Sharpe ratio of at least 1.5 and a sample
>    size of **2,739** (10.87 years of daily data).
>
> **Note that these results not only apply to backtest, but also to out-of-sample
> (paper trading) length.**

⇒ 最后那句逐字把这条判据**适用到前瞻长度** ⇒ 本产物不是自研外推。

🚨 **但锚点口径与本产物不同，不许换算**：`681 / 2.71 年` ⇒ 681 ÷ 244 ≈ 2.79 年 ⇒
那个「数据点」是**日频观测**；本产物的观测单位是 **`entry_date` 块**（一个块 = 一天，
但**一天可能没有块**，周末不出块）。
⇒ 两个 n **不是同一个量**，只能各自成表，**不许相互代入**。

⚠️ 锚点自身也不自洽（必须写进 caliber，否则会被当精确锚用）：
两条点反解出的目标 t* 是 `1 × √681 = 26.096` 与 `2 × √174 = 26.382`，**差 1.1%**
⇒ Bailey 那张表不是由单一公式精确生成的（查表 / 蒙特卡洛）⇒ **只能做量级对拍**。

## 本产物算什么、不算什么

**算**（全部只用已落盘字段，不用公式重算上游任何测量）：

1. `t_obs_implied = abs_mean_over_mde × z`（z 从上游 `ci_method` **解析**，不手打）
2. `n_needed(t*) = n_blocks × (t* / t_obs)²` —— 固定 t* 下 `n ∝ 1/t_obs²`
3. 两道闸取大：`max(MIN_BLOCKS_FOR_ANY_SIGN, 上面那个)`（前者从上游模块 **import**）
4. **真值折价情景**（本产物真正的价值，见下）
5. 日历日 ETA（**两个速率情景**，不给 CI）

**不算 / 不给**（逐条登记在产物的 `what_this_cannot_prove`）：

- ❌ **不外推年化收益率**（R5 明禁；且年化需要 sd 收敛假设，本样本量下无解）
- ❌ **不给速率的置信区间** —— 速率由 3~4 个块估出，那个 CI 是编的
- ❌ **不给「还要攒多少年」** —— 日历日 ÷ 交易日率需要交易日历，本仓没有钉死的那个
- ❌ **不与观测墙的 174/66 并列**（第 7 层）

## ★ 真值折价情景：为什么「一个 n」是错的答案

`n_needed = n × (t*/t_obs)²` 的输入 `t_obs` 是**实测点估计**算出来的
⇒ 它**恒等地假设「真实超额 = 实测超额」**（[[a-data-gap-count-that-hides-its-own-premise]]）。

而 D2 的现状恰恰是**连符号都定不了**（上游 `verdict = POWERLESS`）
⇒ 那个假设没有任何证据支撑。

⇒ 真超额若只有实测的 `α` 倍，`t_obs` 就是 `α × t_obs_hat`（sd 不变时），
而 `n ∝ 1/t_obs²` ⇒ **所需样本 ∝ 1/α²**：

| α | 所需块数（相对） |
|---|---|
| 1.00（实测点估计就是真的） | 1× |
| 0.50 | 4× |
| 0.25 | 16× |
| 0.00 | **无解**（任何有限 n 都定不出符号） |

⚠️ **本表是上界，不是点估计**：它假设 sd **不随**均值折价而缩小。
真实情况下均值变小 ⇒ sd 通常也变小 ⇒ `SE` 变小 ⇒ 所需样本落在 `n/α²` 与 `n` 之间。
⇒ 所以表里每一行只能读成「**至少**要这么多块」，不能读成「要这么多块」。

⇒ ★ **结论的形状**：这个数在 α 未知时**只能给一个下界表**，
而下界表的最后一行是「无解」⇒ **「加数据」这条路的可行性本身也依赖 α**。
这不是本产物的缺陷，是「把未测量的前提写在脸上」。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from aplus import provenance  # noqa: E402
from scripts.forward_d2_verdict import (  # noqa: E402
    MIN_BLOCKS_FOR_ANY_SIGN,
    UP,
)

GENERATOR = "scripts/forward_d2_horizon.py"

# 🚨 本脚本读的是前瞻底稿（单个 parquet），不是 `master/daily` 那个目录 ——
#   不覆盖 `daily_subdir` 的话章上的 `data_fp` 会去数 5460 个股票 parquet，
#   而本脚本一个都没读（第 156 层：章上写着「数据 fresh」而它指着一个没读过的目录）。
DAILY_SUBDIR = "reports"

# ---- 上游产物路径 ----
UPSTREAM_NAME = "forward_d2_verdict.json"

# ---- 真值折价情景：α = 真实超额 / 实测点估计 ----
# 🚨 这是**情景假设**，不是测量。它的唯一作用是让「所需样本 ∝ 1/α²」
#    这条依赖关系显形（[[a-data-gap-count-that-hides-its-own-premise]]）。
#    α=1 是那个**未被证明的前提**；α=0 是「任何 n 都不够」的那一端。
ALPHA_SCENARIOS = (1.0, 0.5, 0.25, 0.0)

# ---- 速率的两个情景（见 `rate_scenarios` 的 docstring） ----
SPEED_MODE_NAIVE = "naive_all_blocks"
SPEED_MODE_STEADY = "steady_single_pred_date_blocks"


def parse_z(ci_method: str) -> float:
    """从上游自报的 `ci_method` 解析临界值 z —— **不许手打**。

    ⚠️ 手打 `1.96` 会让本产物在「上游把口径换成 t 临界值」时
       **静默继续用旧 z**（第 156 层：盖一个描述旧口径的章）。
    ⇒ 上游换了写法，这里**报错**，而**不**回落成默认值。
    """
    prefix = "normal_approx_z="
    if not isinstance(ci_method, str) or not ci_method.startswith(prefix):
        raise ValueError(
            f"无法从上游 ci_method 解析 z：{ci_method!r}。"
            f"本脚本只认 {prefix}<数字> 这种写法；上游换口径时请同步改这里，"
            f"不许加一个默认值把它吞掉。"
        )
    return float(ci_method[len(prefix):])


def t_obs_implied(abs_mean_over_mde: float, z: float) -> float:
    """`t_obs = |mean| / se = (|mean| / MDE) × z`，因为 `MDE = z · se`。

    ⚠️ **这条式子是恒等式**（第 176 层：它对**任何**实现都成立）：
       它**证明不了**上游判决链正确。
    ⇒ 它在本脚本里只有一个用途：**漂移探测器** ——
       如果上游的 `abs_mean_over_mde` 与 `t_stat` 不再同源（例如换了观测单位、
       换了 sd 定义、换了 `MDE` 的构成），这个数会**立刻对不上**，
       而症状（一个静默错的外推）比病因更难发现。
    ⇒ 🚨 任何人都**不许**把它读成「两条独立实现对拍 ⇒ 判决链没问题」。
    """
    return abs_mean_over_mde * z


def assert_same_source(t_implied: float, t_stat_reported: float,
                       rel_tol: float = 1e-12) -> None:
    """`t_implied` 与上游落盘的 `t_stat` 必须逐位一致，否则**拒绝出产物**。

    ⚠️ 不是警告，是**拒绝**：不一致意味着本脚本要外推的那个输入量
       已与判决链脱源，而脱源的外推出来的数**看起来完全正常**。
    """
    if not (t_stat_reported == t_stat_reported) or not (t_implied == t_implied):
        raise ValueError(
            f"t 的两条路径出现 NaN：implied={t_implied!r} reported={t_stat_reported!r}"
            "（上游 se 或 sd 为 0 ⇒ 本产物无法外推）"
        )
    rel = abs(t_implied - t_stat_reported) / abs(t_stat_reported)
    if rel > rel_tol:
        raise ValueError(
            f"abs_mean_over_mde 与 t_stat 已脱源："
            f"implied={t_implied!r} vs reported={t_stat_reported!r} 相对差 {rel:.3e}"
            f" > {rel_tol:.0e} ⇒ 上游口径变了，本产物拒绝外推"
        )


def n_needed_for_t(n_blocks: int, t_obs: float, t_target: float) -> float:
    """固定目标 t* 下，把实测 t 抬到 `t_target` 所需的块数。

    `SE = sd / √n` ⇒ `t = mean·√n / sd` ⇒ **n ∝ t²**
    ⇒ `n_target = n_obs × (t_target / t_obs)²`

    🚨 这条式子的前提是「**sd 不随 n 变大而变小**」——
       即把前瞻通道当成从同一分布里继续抽样。
       若块均值序列的 sd 在更长样本上系统性变小，则所需块数**少于**本式。
       ⇒ 本式的输出是**上界**（[[guards-that-pin-existence-not-property]] 的读法：
          要问「它凭什么被允许」，更要问「**它是什么**」）。
    """
    if t_obs <= 0:
        raise ValueError(f"t_obs 必须为正，收到 {t_obs!r}")
    if t_target <= 0:
        raise ValueError(f"t_target 必须为正，收到 {t_target!r}")
    return n_blocks * (t_target / t_obs) ** 2


def alpha_scenarios(n_blocks: int, t_obs: float,
                    t_target: float) -> list[dict]:
    """真值折价情景表：`n_needed` 随「真实超额 / 实测点估计」= α 展开。

    ⚠️ α=0 那一行**不是 0**，是 `None`（无解）：`t_obs = 0` 时任何有限 n 都定不出符号。
       写成 0 会被读成「不需要样本」，那是**方向相反**的错误。
    """
    base = n_needed_for_t(n_blocks, t_obs, t_target)
    rows = []
    for alpha in ALPHA_SCENARIOS:
        if alpha <= 0:
            rows.append({"alpha": alpha, "label": "真超额为 0（无解）",
                         "t_obs": 0.0, "n_needed": None,
                         "note": "任何有限块数都定不出符号；这不是「不需要样本」"})
            continue
        t_a = t_obs * alpha
        n_a = n_needed_for_t(n_blocks, t_a, t_target)
        rows.append({
            "alpha": alpha,
            "label": ("实测点估计就是真的（未被证明的前提）" if alpha == 1.0
                      else f"真超额只有实测的 {alpha:.0%}"),
            "t_obs": t_a,
            "n_needed": n_a,
            "note": ("上界：假设 sd 不随均值折价而缩小 ⇒ 真实所需落在 "
                     f"[{n_blocks:.0f}, {n_a:.1f}] 区间内"),
        })
    rows.append({
        "_base_n_needed_at_alpha_1": base,
        "_scanned_keys": sorted(rows[0].keys()),
    })
    return rows


def rate_scenarios(blocks: list[dict]) -> list[dict]:
    """两个速率情景 —— **不给 CI**。

    🚨 **本函数第一版的 docstring 写错了一件事，已按实测更正**：
       原文写「第一块装 3 个信号日 ⇒ 全窗口平均速率被压低 ⇒ 朴素速率更保守」。
       实测**两者完全相同（都是 1.000 块/日历日）**，而错因是**把「块里装几条腿」
       当成了「块在 entry 轴上占几格」**：
       - 块 `n_legs` = **29 / 10 / 10 / 10** —— 第一块（entry 10-12）装
         10-09/10-10/10-11 **三个信号日**的腿（都 entry 到同一天、持有同一批 bar）；
       - 🚨 但它在 **entry 轴上仍然只占一格**（10-12 本身是一天）。
       ⇒ 它压低的是「**每块平均装几条腿**」（7.25 vs 3.33），
         **不是速率**。entry 是 10-12/13/14/15 **连续 4 个交易日** ⇒ 两档必然相同。

    ⚠️ 那它们什么时候会分道扬镳？**当 entry 轴出现跳跃时** ——
       周末/长假/某天无出单 ⇒ 该天没有块。
       ⇒ 所以本函数**保留两档**：它是一条**哨兵**，
       「两档相同」本窗口是事实，「两档不同」将来也可能是事实，
       而**哪个是稳态**不该由我写死在散文里（让判读成为计算的输出）。

    ⚠️ **不给置信区间**：速率由 3 个间隔估出，那个 CI 是编的
       （第 160 层：不可测的点估计只支持形状陈述）。
    """
    if len(blocks) < 2:
        return []
    out = [_rate_row(
        SPEED_MODE_NAIVE, blocks,
        "全部块都参与；第一块含 3 个信号日的并集 ⇒ 它压低的是**每块平均腿数**，"
        "**不是速率**（entry 轴上第一块仍然只占一格）")]
    steady = [b for b in blocks if b["n_pred_dates"] == 1]
    if len(steady) >= 2:
        out.append(_rate_row(
            SPEED_MODE_STEADY, steady,
            "只用 n_pred_dates == 1 的块 ⇒ 每信号日一个块。"
            "⚠️ 仍是点估计，**不是**稳态速率的估计"))
    return out


def _rate_row(mode: str, subset: list, note: str) -> dict:
    """一行速率。**分母是间隔数，不是块数** —— 键名必须把这件事说出来。

    🚨 2026-10-17 修正：旧键名 `blocks_per_calendar_day` 配的值是
       `(n_blocks − 1) / span` ⇒ 读者用它自己印的 `blocks=4 / span=3`
       一除得 **1.333**，与产物印的 **1.0** 矛盾（第 179 层：键名与内容互相打脸）。
       数值对**前瞻外推**是对的（从最后一个块往后等，关心的是**新增**速率），
       错的是名字 —— 而一个与内容矛盾的名字比一个错的名字更坏（第 112 层）。
       ⇒ 现在键名自带口径，且**印出的字段能自己除出这个值**（不再需要读者猜分母）。
    """
    dates = sorted(b["entry_date"] for b in subset)
    span = _span_days(dates)
    n_intervals = len(dates) - 1
    return {
        "mode": mode,
        "n_blocks_in_mode": len(dates),
        "entry_first": dates[0],
        "entry_last": dates[-1],
        "n_intervals": n_intervals,
        "span_calendar_days": span,
        "new_blocks_per_calendar_day": (n_intervals / span) if span else None,
        "rate_is": ("n_intervals / span_calendar_days（**间隔数**除以跨度）"
                    "⇒ 可用本行印出的字段直接除出来核对"),
        "note": note,
    }


def _span_days(dates: list[str]) -> int:
    """日历日跨度（两个字符串日期相减）。少于 2 个日期时返回 0。"""
    from datetime import date
    if len(dates) < 2:
        return 0
    ds = sorted(date.fromisoformat(d) for d in dates)
    return (ds[-1] - ds[0]).days


def days_needed(n_needed: float, rate_per_day: float) -> float | None:
    """还要等多少个日历日。速率 ≤ 0 时**返回 None** 而不是 inf/抛错。"""
    if rate_per_day is None or rate_per_day <= 0:
        return None
    if n_needed is None:
        return None
    return max(0.0, (n_needed - 1) / rate_per_day)


def horizon(upstream: dict) -> dict:
    """上游落盘字段 → 本产物。**只读字段，不重算上游任何测量。**"""
    n_blocks = upstream["n_blocks"]
    ratio = upstream["abs_mean_over_mde"]
    z = parse_z(upstream["ci_method"])
    t_impl = t_obs_implied(ratio, z)
    assert_same_source(t_impl, upstream["t_stat"])

    # ---- 目标 t\* 的两张表：一张同口径，一张来自上游自报的敏感性臂 ----
    targets = [{
        "t_target": z,
        "source": f"上游 ci_method 自报（normal_approx_z={z}）",
        "meaning": "双侧 5% 定出符号 —— 与判决臂的 POWERLESS 条件同口径",
    }, {
        "t_target": upstream["sensitivity_student_t"]["t_crit_95"],
        "source": "上游 sensitivity_student_t.t_crit_95（t 分布，df = n_blocks-1）",
        "meaning": ("n 小时正态近似低估真实临界值 ⇒ 这条要求更多样本；"
                    "它是上游自标的 sensitivity，**不是**判决臂口径"),
    }]

    scenarios = []
    for tg in targets:
        if tg["t_target"] is None:
            continue
        n_stat = n_needed_for_t(n_blocks, t_impl, tg["t_target"])
        n_gate = float(MIN_BLOCKS_FOR_ANY_SIGN)
        binding = ("power" if n_stat > n_gate else "min_blocks_gate")
        n_final = max(n_stat, n_gate)
        rates = rate_scenarios(upstream["blocks"])
        rate_rows = [{**r,
                      "days_needed_at_alpha_1": days_needed(
                          n_final, r["new_blocks_per_calendar_day"]),
                      "days_needed_at_alpha_0_5": days_needed(
                          max(n_stat, n_gate) / 0.25,
                          r["new_blocks_per_calendar_day"])}
                     for r in rates]
        # 🚨 「两档速率是否相同」「哪一档更保守」都必须**算出来**落盘，
        #   不许留在散文里断言（第 142 层）。
        #   ⚠️ 2026-10-17：此前 `rate_scenarios` 的 note 里**硬写着**
        #   「全部块都算 ⇒ 速率被压低 ⇒ **这是保守（等更久）的那一个**」，
        #   而实测两档**完全相同** —— 一句关于已消失性质的断言，
        #   等于让读者去找一条不存在的机制（第 170 层的镜像）。
        #   下面两个字段由数算出来，entry 出现跳跃那天「哪一档更保守」自己会变。
        _rates = [r["new_blocks_per_calendar_day"] for r in rate_rows
                  if r["new_blocks_per_calendar_day"] is not None]
        rates_identical = (len(set(_rates)) == 1) if rate_rows else None
        _etas = [days_needed(n_final, x) for x in _rates]
        more_conservative = (
            None if len(_etas) < 2 else
            rate_rows[_etas.index(max(_etas))]["mode"])
        scenarios.append({
            "t_target": tg["t_target"],
            "t_target_source": tg["source"],
            "t_target_meaning": tg["meaning"],
            "t_obs_implied": t_impl,
            "n_blocks_now": n_blocks,
            "n_needed_by_power": n_stat,
            "n_needed_by_min_blocks_gate": n_gate,
            "min_blocks_gate_source": (
                f"从 scripts/forward_d2_verdict.py import MIN_BLOCKS_FOR_ANY_SIGN "
                f"= {MIN_BLOCKS_FOR_ANY_SIGN}（不手打）"),
            "which_binds": binding,
            "n_needed": n_final,
            "alpha_scenarios": [r for r in alpha_scenarios(
                n_blocks, t_impl, tg["t_target"]) if "_base_n_needed_at_alpha_1" not in r],
            "calendar_days_needed_by_rate": rate_rows,
            "rate_scenarios_identical": rates_identical,
            "rate_scenarios_identical_meaning": (
                "True = 本窗口的 entry 轴连续 ⇒ 朴素档与稳态档必然相同，"
                "「用哪一档」在本窗口不影响结论；两档只会在 entry 出现跳跃时分开"),
            "more_conservative_mode": more_conservative,
            "more_conservative_meaning": (
                "按**日历日 ETA 更长**判（速率越低越保守），由数算出来；"
                "两档 ETA 相等时它就是 first/naive 那一档 —— "
                "这不是「朴素档更保守」的证据，是「本窗口分不出来」的证据"),
        })

    return {
        "what_this_is": (
            "前瞻 D2 判决通道的**时间账**：在「真实超额 = 实测点估计」这个未被证明的"
            "前提下，还要攒多少个 entry_date 块、还要等多少个日历日，"
            "才能让上游 forward_d2_verdict.json 有资格走出 POWERLESS。"),
        "what_this_is_not": [
            "**不是年化收益率的预测**（预注册 R5 明禁外推年化；且年化需 sd 收敛假设，"
            "本样本量下无解）",
            "**不是稳态速率的估计**（速率由 3~4 个块估出 ⇒ 只作情景，不给 CI）",
            "**不是「还要攒多少年」**（日历日 ÷ 交易日率需要钉死的交易日历，仓里没有）",
            "**不可与观测墙的 174/66 并列引用**（观测单位 / 目标 t / 数据源三者全不同，"
            "第 7 层：两个各自正确的数当成同一个量）",
            "**不可作为「加数据这条路可行」的证据** —— α=0 那一行是「无解」，"
            "可行性本身依赖那个未被证明的 α",
        ],
        "upstream": {
            "name": UPSTREAM_NAME,
            "path": upstream.get("_path"),
            "source_parquet": upstream["source_parquet"],
            "source_parquet_sha256": upstream["source_parquet_sha256"],
            "verdict": upstream["verdict"],
            "n_blocks": n_blocks,
            "n_pred_dates": upstream["n_pred_dates"],
            "abs_mean_over_mde": ratio,
            "t_stat_reported": upstream["t_stat"],
            "ci_method": upstream["ci_method"],
            "read_only_fields": (
                "本脚本只**读**上游的 n_blocks / abs_mean_over_mde / t_stat / ci_method / "
                "blocks / sensitivity_student_t；**不使用** sd_pct、se_pct、mde_pct "
                "重算任何上游量（第 109 层：核对产物时用公式重算而不是读字段）"),
        },
        "t_obs_derivation": {
            "formula": "t_obs = abs_mean_over_mde × z（因 MDE = z · se）",
            "z": z,
            "t_obs_implied": t_impl,
            "t_stat_reported": upstream["t_stat"],
            "rel_diff": abs(t_impl - upstream["t_stat"]) / abs(upstream["t_stat"]),
            "what_this_check_is": (
                "**漂移探测器，不是正确性证明**。这条式子是恒等式，对任何实现都成立 "
                "（第 176 层）⇒ 它证明不了判决链对。它只证明「我要外推的那个输入量"
                "与判决链落盘的 t 同源」；两者一旦脱源，本脚本**拒绝出产物**。"),
            "what_this_check_is_not": (
                "🚨 不许读成「两条独立实现对拍 ⇒ 上游判决链没问题」"),
        },
        "scenarios": scenarios,
        "external_anchor_book": {
            "role": "external_anchor",
            "source": "data/books/chan_lines.txt:2567-2578（Chan p.85，引 Bailey et al. 2012）",
            "quote_zh": [
                "95% 水平上确信真 Sharpe ≥ 0 ⇒ 回测 SR=1 且样本量 681 个数据点（2.71 年日频）",
                "回测 SR=2 ⇒ 只需 174 个数据点（0.69 年日频）",
                "确信真 SR ≥ 1 ⇒ 回测 SR ≥ 1.5 且样本量 2,739（10.87 年日频）",
                "这些结果不只适用于回测，也适用于 out-of-sample（paper trading）长度",
            ],
            "why_it_qualifies": (
                "最后一句逐字把判据适用到**前瞻长度** ⇒ 本产物不是自研外推"),
            "caliber_mismatch": (
                "🚨 681 ÷ 2.71 年 ⇒ 那个「数据点」是**日频观测**；本产物的观测单位是 "
                "**entry_date 块**。两个 n **不是同一个量** ⇒ 只各自成表，"
                "**不许相互代入**（第 7 层）"),
            "anchor_is_not_self_consistent": (
                "两条点反解出的目标 t* 是 1×√681=26.096 与 2×√174=26.382，**差 1.1%** ⇒ "
                "该表不是单一公式精确生成的 ⇒ **只能做量级对拍，不许当精确锚代入**"),
        },
        "honest_boundary": {
            "n_needed_is_an_upper_bound": (
                "n_needed 假设 sd 不随 n 变大而变小。若块均值 sd 在更长样本上系统性变小，"
                "所需块数**少于**本式 ⇒ 表里每行只能读成「**至少**要这么多块」"),
            "alpha_table_is_the_point": (
                "真值折价情景是本产物的价值核心：单点 n 恒等地假设"
                "「真实超额 = 实测超额」，而上游 verdict=POWERLESS ⇒ 连符号都定不了 ⇒ "
                "那个前提零证据。n ∝ 1/α² ⇒ α 减半则所需样本 ×4"),
            "speed_is_from_three_gaps": (
                "速率由 3 个间隔估出，其抽样误差比块数本身还大 ⇒ 只作情景，不给 CI"),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--upstream", default=None,
                    help="上游 forward_d2_verdict.json（默认 data/reports/ 下）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    # 🚨 第 125 层：抓在跑之前，比对在盖章时。放在 read_parquet 后面抛错
    #   等于「跑完了才告诉你白跑」。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    up_path = Path(args.upstream) if args.upstream else \
        Path(args.data_root) / "reports" / UPSTREAM_NAME
    if not up_path.exists():
        raise SystemExit(f"上游产物不存在：{up_path}\n先跑 scripts/forward_d2_verdict.py")

    upstream = json.loads(up_path.read_text(encoding="utf-8"))
    upstream["_path"] = str(up_path)

    # 🚨 缺字段必须报错，绝不 .get(默认值) 回落（第 114 层：契约从没被写下来过）
    for key in ("n_blocks", "abs_mean_over_mde", "t_stat", "ci_method", "blocks",
                "sensitivity_student_t", "source_parquet", "source_parquet_sha256",
                "verdict", "n_pred_dates"):
        if key not in upstream:
            raise SystemExit(f"上游 {up_path.name} 缺必需字段 {key!r} ⇒ 拒绝外推")

    out_doc = horizon(upstream)

    out_path = Path(args.out) if args.out else \
        Path(args.data_root) / "reports" / "forward_d2_horizon.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    # 🚨 写在 write_text 之后（章落在报告旁边，不污染报告 schema）
    provenance.stamp(out_path, GENERATOR, root=ROOT,
                     data_root=args.data_root, daily_subdir=DAILY_SUBDIR,
                     expect_bytes=expect_bytes,
                     extra={"upstream": UPSTREAM_NAME,
                            "source_parquet_sha256": upstream["source_parquet_sha256"]})

    # ---- CLI ----
    print(f"上游 {up_path.name}：verdict={upstream['verdict']}、"
          f"n_blocks={upstream['n_blocks']}、"
          f"|mean|/MDE={upstream['abs_mean_over_mde']:.4f}（<1 ⇒ 连符号都定不了）")
    print(f"读数方向：{UP} 腿逐行算术超额，观测单位 = entry_date（预注册 R1）\n")
    for sc in out_doc["scenarios"]:
        print(f"── 目标 t* = {sc['t_target']:.4f}"
              f"（{sc['t_target_source']}）")
        print(f"   t_obs = {sc['t_obs_implied']:.4f}"
              f" ⇒ n_needed = {sc['n_needed_by_power']:.1f} 块"
              f"；承重的是 {sc['which_binds']}"
              f"（min_blocks 闸 = {sc['n_needed_by_min_blocks_gate']:.0f}）")
        print(f"   {'真超额/实测':>12} {'t_obs':>9} {'至少需要(块)':>14}")
        print("   " + "─" * 40)
        for r in sc["alpha_scenarios"]:
            n_txt = "无解" if r["n_needed"] is None else f"{r['n_needed']:.1f}"
            print(f"   {r['label']:>12} {r['t_obs']:>9.4f} {n_txt:>14}")
        for d in sc["calendar_days_needed_by_rate"]:
            a1 = d["days_needed_at_alpha_1"]
            a5 = d["days_needed_at_alpha_0_5"]
            f1 = "—" if a1 is None else f"{a1:.0f}"
            f5 = "—" if a5 is None else f"{a5:.0f}"
            print(f"   速率[{d['mode']}] "
                  f"{d['n_intervals']}/{d['span_calendar_days']} = "
                  f"{d['new_blocks_per_calendar_day']:.3f} 新块/日历日"
                  f" ⇒ 还要等 α=1: {f1} 日、α=0.5: {f5} 日")
        print()

    print("⚠️ 每个数都是**至少**（假设 sd 不随 n 变小），且都以"
          "「真实超额 = 实测点估计」为前提 —— 上游连符号都定不了 ⇒ 该前提零证据。")
    print("🚨 不可与观测墙的 174/66 并列引用（观测单位 / 目标 t / 数据源三者全不同）。")
    print(f"\n产物 → {out_path}")


if __name__ == "__main__":
    main()
