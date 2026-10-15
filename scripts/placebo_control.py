"""安慰剂对照 —— 「t=2.44」是因子，还是一个**差基准**？

**判据已预注册**：`docs/TODO_placebo_control_2026-10-15.md`
三条臂、种子、20 个 rep、四个判决分支全在那边写死。**本脚本只负责执行。**

## 起因（一句话）

榜宽实验主档 N=200 给出分块 t = 2.44（全项目第一次出现显著的涨腿超额）。
但 N=200 是全池的 5%，基准是**等权全市场** ⇒ 有一个不需要任何 bug
就能产生 t=2.44 的替代解释：**等权池被小市值/低流动性拖住，
于是「持有任意 200 只」本身就能跑赢它**，与 score 有没有信息量无关。
⇒ 在这个假设被排除之前，「因子有效」一个字都不能写。

## 三条臂（预注册 §2）

| 臂 | 做法 | 检验什么 |
|---|---|---|
| **P0 观测** | 原样，N=200，按 `composite_score` 排序 | 基准线 |
| **P1 打乱 score** | **每个横截面内**对 score 做**随机置换**，再取 top 200 | 若 t(P1) 仍高 ⇒ 差基准 |
| **P2 打乱名单** | 不排序，在当日可交易池里随机抽 200 只 | 最粗的对照：连排序都去掉 |

**P1 必须是横截面内置换，不是整体打乱**（预注册 §2）：整体打乱会把日期结构
一起毁掉，测的就不是同一个东西了。置换**逐日期独立**、种子固定，
否则不可复现，而**不可复现的阴性结果等于没跑**。

## 为什么是 20 个 rep 而不是 1 个（预注册 §2）

`aplus/evolve_race` 的历史注释里已经记过一次：「30 组随机权重里也有 1 组能过闸」。
⇒ 单个随机臂分不开「1 个 placebo 恰好也很高」与「placebo 系统性偏高」。
**必须报分布**（min / 中位数 / max / ≥ P0 的个数）。

## 三个刻意写死的约束

1. **三条臂走同一条代码路径**：同一个 `run_year`、同一个 `evaluate_weights`、
   同一条成本链、同一份 `MIN_BASKET`。**禁止**为安慰剂另写更快的路径 ——
   那测的是另一个东西。本脚本里唯一的差别是 `score_transform` 这一个入参。
2. **不碰 `composite_score`**：安慰剂是**注入**（`score_transform`）不是**改写**。
   改共享代码就等于把安慰剂的定义写进了被检验的那个对象。
3. **P0 的 t 从本脚本 P0 臂的产物读**，不硬编码 2.44 —— 2.44 是榜宽实验那个
   文件里的数，而本脚本不必依赖那个文件在不在。

## 用法

```bash
.venv/bin/python scripts/placebo_control.py            # 20 个 rep（数小时）
.venv/bin/python scripts/placebo_control.py --reps 2   # 冒烟（判决必然不完整）
```

⚠️ **面板是 GB 级、~10 分钟**，且三条臂 × 20 个 rep = 41 遍 `run_year`。
与赛马同跑会 OOM（CLAUDE.md 环境节）。守卫测试全部用合成横截面，
**不碰面板**（`tests/test_placebo_control.py` 里有 autouse 闸门钉死这一点）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import begin_run, configure_noisy_stderr  # noqa: E402

configure_noisy_stderr()

import aplus.verify as verify_mod  # noqa: E402
from aplus.backtest import build_cross_sections  # noqa: E402
from aplus.composite import unpack  # noqa: E402
from aplus.factor import FACTOR_NAMES  # noqa: E402
from aplus.snapshot import load_factors_map  # noqa: E402
from aplus.store import LocalStore  # noqa: E402
# ★ 溯源章。2026-10-15 补：这份产物的判决 `PLACEBO_NULL` 是全项目的
#   **承重判决**（「t=2.44 是不是因子」），而它此前**既没登记也没盖章**
#   ⇒ 按本仓自己的规矩，`unknown` = **不许引用**。
#   ⇒ 登记 + 盖章两件都要：登记是「谁生成它」，盖章是「跑的是哪份字节」。
# ⚠️ 用 `provenance.stamp(...)` 而不是 `from ... import stamp`：
#   守卫 `test_registered_is_not_actually_stamp` 按 **AST** 找
#   `*.stamp(...)` 属性调用；裸名调用它看不见 ⇒ 会把一条真接线判成没接。
from aplus import provenance  # noqa: E402

# ★ 权重、`run_year`、逐日聚合、分块 t、成本链**全部从主回测 import**，
#   一件都不自己写。自己写一遍 = 造第二台测量仪，两台之间的差永远查不出来。
from backtest_walk_forward_weights import (ENTRY_LAG, _rescale_check, _t,  # noqa: E402
                                           run_year, weights_by_year)
from factor_attribution import T_CRIT_5PCT, power_floor  # noqa: E402
# ★ `scaled_min_basket` / `series_from_chunks` 也**从榜宽实验 import**：
#   安慰剂的成分下限必须与榜宽实验**同一个尺子**，不许在这里重新推导一遍。
from factor_width_mde import scaled_min_basket, series_from_chunks  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

PREREG = "docs/TODO_placebo_control_2026-10-15.md"

# ── 预注册 §2 / §3：全部单点持有，文档是唯一真相源 ────────────────────
PLACEBO_SEED = 20261015
N_PLACEBO_REPS = 20

# 主档**沿用榜宽实验的预注册，一个字不改**（预注册 §3 第一行）。
PRIMARY_N = 200
K = 21
JUDGEMENT_START = "2021-01-01"

# 三条臂的名字。写死而不是拼字符串：产物里要能逐条对回预注册的表。
ARM_P0 = "P0_observed"
ARM_P1 = "P1_shuffle_score"
ARM_P2 = "P2_shuffle_list"

#: 中位数口径：中位数 = `numpy.median`（偶数个 rep 取中间两个的均值）。
#: 写在这里是为了让产物里的 `median_rule` 有个可指认的东西，
#: 而不是让读的人猜「中位数」在这个表里是哪个数。
MEDIAN_RULE = "numpy.median（偶数个取中间两个的均值）"


def rep_seed(rep: int) -> int:
    """第 `rep` 个 rep 的独立种子 = `PLACEBO_SEED + rep`（预注册 §2）。"""
    return PLACEBO_SEED + int(rep)


def _day_key(day) -> int:
    """信号日 → 整数熵源：`2021-03-05` → 20210305。

    ★ **不许用 `hash(day)`**：PYTHONHASHSEED 让它逐进程变 ⇒ 不可复现，
      而不可复现的阴性结果等于没跑（预注册 §2）。
    ★ **也不许「按调用顺序抽一次」**：那让每一天的置换依赖于日期被遍历的次序，
      同一个信号日在另一个窗口下会得到不同的置换 —— 那不叫「逐日期独立置换」，
      那叫「一次性打乱再按顺序切片」。种子必须由**日期本身**决定。
    """
    s = str(day)[:10].replace("-", "").strip()
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"day 必须是 'YYYY-MM-DD'，收到 {day!r} —— "
                         "逐日期置换的种子由日期本身决定，日期不合法就没有种子")
    return int(s)


def _rng(seed: int, day, salt: int = 0) -> np.random.Generator:
    """由 `(rep 种子, 信号日, 用途盐)` 造一个独立的随机流。"""
    return np.random.default_rng([int(seed), _day_key(day), int(salt)])


def make_shuffle_score(seed: int):
    """**P1**：在当日横截面内对 `score` 做随机置换（预注册 §2）。

    ## NaN 原地不动（一个必须写下来的解释）

    置换只搬动**可打分**的那些分数（`~isnan`），NaN 留在原位。
    理由是预注册 §2 自己给的那句：「横截面内置换保留了**每日池子的成分构成**…
    它**只**摧毁『score 把好票排在前面』这件事」。
    把 NaN 一起搬走 = 顺手把**谁有资格进池子**也随机化了，
    于是 P1 与 P0 的差同时含「排序」与「成分」两件事，分不开。
    守卫：`tests/test_placebo_control.py::test_shuffle_keeps_the_scorable_pool_identical`。
    """

    def _transform(day, df: pd.DataFrame) -> pd.DataFrame:
        rng = _rng(seed, day)
        out = df.copy()
        values = df["score"].to_numpy(float)
        movable = ~np.isnan(values)              # 与下游 `notna()` 同一口径
        if movable.any():
            shuffled = values.copy()
            shuffled[movable] = rng.permutation(values[movable])
            out["score"] = shuffled
        return out

    _transform.__name__ = f"shuffle_score_seed{seed}"
    return _transform


def make_shuffle_list(seed: int):
    """**P2**：不排序，直接在当日**可交易池**里随机抽 `top_n` 只（预注册 §2）。

    实现上给每一行一个**互不相同**的随机名次再按它取 top_n ——
    与其用 `rng.random()` 让重复值去撞运气（重复值的切点靠 pandas 的
    稳定排序任意决定），不如直接给一个随机排列：任意 200 只等概率。

    ★ 与 P1 的**池子不同**，这是预注册原文要的：
      P1 在「能打分的那些」里换顺序（与 P0 同一个池）；
      P2 在「当日可交易池」里抽 —— 因子不完整者**也**在池里。
      两者的差因此不能直接当同一件事的两个读数，产物里分开报。
    """

    def _transform(day, df: pd.DataFrame) -> pd.DataFrame:
        rng = _rng(seed, day, salt=1)            # salt=1 ⇒ 与 P1 是不同的流
        out = df.copy()
        out["score"] = rng.permutation(len(out)).astype(float)
        return out

    _transform.__name__ = f"shuffle_list_seed{seed}"
    return _transform


def arm_stats(series: pd.Series, k: int) -> dict:
    """一条臂的逐日毛超额序列 → 该臂的统计量（与榜宽实验同一个口径）。"""
    if series is None or len(series) < 60:
        return {"n_days": int(0 if series is None else len(series)),
                "enough_for_a_verdict": False,
                "why_empty": "样本不足 60 天 —— 不构成测量（与榜宽实验同一条门槛）"}
    t_blk, n_obs = _t(series, block=k)
    pf = power_floor(series.to_numpy(float))
    return {
        "n_days": int(len(series)),
        "enough_for_a_verdict": True,
        "gross_annual_pct": pf["annual_mean_pct"],
        "annual_sd_pct": pf["annual_sd_pct"],
        "MDE_annual_pct": pf["MDE_annual_pct"],
        "t_naive": pf["t_naive"],
        "t_blocked": float(t_blk) if t_blk is not None else None,
        "n_independent_obs": int(n_obs) if n_obs is not None else None,
    }


def summarize_reps(runs: list[dict], t_p0: float | None) -> dict:
    """20 个 rep → 分布（min / 中位数 / max / ≥ P0 的个数）。

    **缺的 rep 一律报数，不静默丢弃**（`n_reported` 与 `n_reps` 并排）。
    `t` 为 `None` 的 rep 意味着那一条**没测出**数，把它从分布里悄悄拿掉，
    分布就变成了「只报跑通了的那几条」—— 预注册 §3 规则 2 明令禁止。
    """
    ts = [r.get("t_blocked") for r in runs]
    finite = [float(t) for t in ts if t is not None and np.isfinite(t)]
    n_ge = None
    if t_p0 is not None and finite:
        n_ge = int(sum(1 for t in finite if t >= t_p0))
    return {
        "n_reps": len(runs),
        "n_reported": len(finite),
        "t_list": [None if t is None else float(t) for t in ts],
        "min": float(min(finite)) if finite else None,
        "median": float(np.median(finite)) if finite else None,
        "max": float(max(finite)) if finite else None,
        "median_rule": MEDIAN_RULE,
        "n_ge_p0": n_ge,
        "n_ge_p0_rule": "逐个 rep 的分块 t ≥ P0 的分块 t（按符号比，不取绝对值，"
                        "与预注册写法一致）",
        "missing_reps": int(len(runs) - len(finite)),
    }


def placebo_conditions(t_p0: float, p1_ts: list) -> dict:
    """预注册 §3 四条分支的**条件**，各自独立求值一次。

    ## 为什么不是「一个 if 链」

    这四条**不互斥**，而且有一条被另一条**完全包住**：

        PASSES ⟺ `t(P0) > max(P1)`
        NULL   ⟺ `全部 < 1.96 且 max < t(P0)`

    「全部 < 1.96」= **max** < 1.96（不是 min），而 `max < t(P0)` 是 NULL 的
    另一个合取项 ⇒ **NULL ⊆ PASSES**（穷举验证，20000 组随机输入下
    「NULL 真而 PASSES 假」0 例）。
    ⇒ 按预注册的**表格顺序**写成 if 链，`PLACEBO_NULL` **永远到不了**——
    那正是 `docs/MISTAKE_LOG_2026-10-14.md` 第 88 层（判决函数里三条冻结分支不可达）。

    ⇒ 所以这里**四条全部求值**，判决码由 `decide()` 按 `_BRANCH_ORDER` 取第一条命中。
      四个布尔值一起进产物，读者能看到「哪几条同时成立」，
      不会因为只拿到一个标签而丢掉信息。

    条件本身**逐字照抄预注册**，一个阈值、一个种子都没动。
    """
    vals = sorted(float(t) for t in p1_ts)
    lo, mid, hi = vals[0], float(np.median(vals)), vals[-1]
    p = float(t_p0)
    return {
        "t_p0": p,
        "p1_min": lo,
        "p1_median": mid,
        "p1_max": hi,
        "n_reps": len(vals),
        "n_ge_p0": int(sum(1 for t in vals if t >= p)),
        "t_crit": T_CRIT_5PCT,
        "PASSES__t_p0_gt_p1_max": bool(p > hi),
        "BENCHMARK__p1_median_ge_tcrit": bool(mid >= T_CRIT_5PCT),
        "MIXED__median_lt_tcrit_and_some_ge_p0": bool(
            mid < T_CRIT_5PCT and any(t >= p for t in vals)),
        # 「全部 < t_crit」判的是 **max**（每个 rep 都得在门槛以下）。
        # 写成 lo < T_CRIT 会变成「只要有一个不显著就算全部不显著」，
        # 于是 max=2.30、min=0.5 这种分布也被判成 NULL ——
        # 而它恰恰是预注册留给 MIXED 的那类分布。
        "NULL__all_lt_tcrit_and_max_lt_p0": bool(hi < T_CRIT_5PCT and hi < p),
    }


#: 判决码的**求值顺序**（不是条件，条件全在 `placebo_conditions` 里）。
#:
#: ## ★ 顺序本身就是一个判据，必须按「失败模式不对称」排，不能按表格行序
#:
#: 四条条件**不是互斥的**。穷举 40000 组（`placebo_control.py` 自带守卫
#: `test_the_branch_order_is_the_conservative_one`）实测恰好两组会同时成立：
#:
#: | 同时成立 | 次数 | 谁能压谁才对 |
#: |---|---|---|
#: | `NULL` ∧ `PASSES` | 557 | `NULL` 在前（`NULL ⊆ PASSES`，PASSES 是更弱的话） |
#: | **`BENCHMARK` ∧ `PASSES`** | **284** | 🚨 **`BENCHMARK` 必须在 `PASSES` 之前** |
#:
#: 🚨 **`BENCHMARK` 的含义是「测的是差基准，榜宽实验的显著性全部作废」——
#:    它是最响的警报；`PASSES` 的含义是「因子确实有效」——它是唯一能报喜的分支。
#:    让报喜压过警报，代价是不对称的：压对了省一次重测，压错了整轮结论作废。
#:
#: ⇒ 所以顺序按**结论的严厉程度**降序排：
#:    `BENCHMARK`（全盘作废）→ `MIXED`（边缘，禁报喜）→ `NULL`（干净对照）→ `PASSES`（最好听）。
#:
#: 预注册 §3 的**表格行序**曾经被实现默认继承成这个优先级（本文件第一版就是），
#: 于是 284 例「中位数已经 ≥ 1.96」被判成 `PLACEBO_PASSES` —— **警报被静默吃掉**。
#: 这是预注册没写死的地方，补录见 `docs/TODO_placebo_control_2026-10-15.md` §3 修订 1。
#: **阈值、种子、窗口一个都没动。**
_BRANCH_ORDER = (
    "BENCHMARK__p1_median_ge_tcrit",
    "MIXED__median_lt_tcrit_and_some_ge_p0",
    "NULL__all_lt_tcrit_and_max_lt_p0",
    "PASSES__t_p0_gt_p1_max",
)

_VERDICT_OF = {
    "NULL__all_lt_tcrit_and_max_lt_p0": "PLACEBO_NULL",
    "PASSES__t_p0_gt_p1_max": "PLACEBO_PASSES",
    "BENCHMARK__p1_median_ge_tcrit": "PLACEBO_BENCHMARK",
    "MIXED__median_lt_tcrit_and_some_ge_p0": "PLACEBO_MIXED",
}


def decide(t_p0: float | None, p1_ts: list) -> tuple[str | None, str]:
    """预注册 §3 的**四个判决分支**（条件见 `placebo_conditions`）。

    提成独立函数是为了让守卫能**直接调它** —— 测试里把筛选逻辑再写一遍，
    验的只是概念，变异实现它抓不到（与 `factor_width_mde.decide` 同一个理由）。

    ## `t_p0` 是入参，不是字面量

    预注册正文写的是「`t(P0)=2.44`」，但 **2.44 是榜宽实验那个产物里的数**。
    本函数收 `t_p0`（由本脚本 P0 臂实测得到），
    硬编码它就等于把「P0 必须真的重跑一遍」这个要求架空 ——
    而 P0 重跑恰恰是本实验的基准线。
    """
    if t_p0 is None or not np.isfinite(float(t_p0)):
        return None, ("P0 没跑出 t ⇒ **拒绝出判决**。"
                      "缺档必须报错，绝不拿 2.44 这类字面量顶上。")
    ts = list(p1_ts)
    if not ts:
        return None, "P1 一个 rep 都没有 ⇒ 拒绝出判决（空分布的中位数不是数）"
    if any(t is None or not np.isfinite(float(t)) for t in ts):
        n_bad = sum(1 for t in ts if t is None or not np.isfinite(float(t)))
        return None, (f"P1 有 {n_bad}/{len(ts)} 个 rep 没测出 t ⇒ 拒绝出判决："
                      "把跑不通的那些悄悄剔掉，分布就成了「只报好看的」。")

    c = placebo_conditions(float(t_p0), ts)
    for key in _BRANCH_ORDER:
        if not c[key]:
            continue
        lo, mid, hi, p = c["p1_min"], c["p1_median"], c["p1_max"], c["t_p0"]
        if key == "NULL__all_lt_tcrit_and_max_lt_p0":
            return _VERDICT_OF[key], (
                f"P1 全部 < {T_CRIT_5PCT:.2f}（max {hi:.2f}）且 max < t(P0)={p:.2f}"
                f" ⇒ 干净的阴性对照；且 t(P0) 高于全部 {c['n_reps']} 个 placebo rep。"
                f"⚠️ 这只等于「随机臂在**能测的量级上**测不出超额」，"
                f"**不等于「因子无效」**；P0 的 {p:.2f} 值得往下走，"
                f"但「P1 跑赢 P0」与「P1 能赚钱」是两件事。")
        if key == "PASSES__t_p0_gt_p1_max":
            return _VERDICT_OF[key], (
                f"t(P0)={p:.2f} > P1 的 max={hi:.2f}（{c['n_reps']} 个 rep 全在它下面）"
                f"⇒ 横截面排序确实把好票排在前面。"
                f"⚠️ 只说明**排序有信息量**，不说明扣成本后还剩多少（预注册 §5）。")
        if key == "BENCHMARK__p1_median_ge_tcrit":
            return _VERDICT_OF[key], (
                f"P1 中位数 {mid:.2f} ≥ {T_CRIT_5PCT:.2f} ⇒ 🚨 **测的是差基准，不是因子**："
                f"把 score 全部打乱后，照样「显著」（max {hi:.2f}）。"
                f"⇒ 榜宽实验的显著性全部作废，**必须换基准（等权 → 流通市值加权）后整轮重测**；"
                f"⚠️ 不许把它解释成「因子也许还有一点」，也不许拿现有数字减去一点。")
        if key == "MIXED__median_lt_tcrit_and_some_ge_p0":
            return _VERDICT_OF[key], (
                f"P1 中位数 {mid:.2f} < {T_CRIT_5PCT:.2f}，但 {c['n_ge_p0']}/{c['n_reps']} "
                f"个 rep ≥ t(P0)={p:.2f}（max {hi:.2f}）⇒ 边缘。"
                f"**不许报喜**：只记录，且需要更多 rep 才谈得上判断。")
    # 落到这里说明四条分支没有覆盖全空间 ——
    # 那正是 `docs/MISTAKE_LOG_2026-10-14.md` 第 88 层（判决函数里冻结分支不可达）。
    # **当场炸，不返回一个看起来正常的默认值。**
    raise AssertionError(
        f"四条判决分支没有一条命中（{c}）—— 判决函数与预注册脱节了，"
        "不许兜底成某个分支。")


# 测试用别名：语义上「这就是判决函数本身」，不是替身。
decide_for_test = decide


def run_arm(xs, wmap, judged, by_year, score_transform, n: int, k: int) -> dict:
    """跑**一条臂**的所有判决年，返回该臂的统计量。

    ★ 与 P0 用的是**同一个 `run_year`**，参数逐个相同 ——
      唯一的差别是 `score_transform`。这里没有任何 if/else 走捷径。
    """
    chunks: list = []
    n_rows = 0
    for y in judged:
        r = run_year(xs, wmap[y]["weights"], by_year[y], n, n, k,
                     daily_out=chunks, score_transform=score_transform)
        n_rows += int(r.get("n_rows", 0))
    out = arm_stats(series_from_chunks(chunks), k)
    out["n_prediction_rows"] = n_rows
    return out


def run_arms(xs, wmap, judged, by_year, n: int, k: int, reps: int) -> dict:
    """跑 P0 一遍 + P1/P2 各 `reps` 遍，返回 `{臂: [每次的统计量]}`。

    ★ 提成独立函数是为了让守卫**不碰面板**（与 `factor_width_mde.run_widths` 同源）：
      `main()` 会读几千个日线 parquet（GB 级），
      守卫一旦触发那一步，守卫本身就成了最贵的那一步。

    `aplus.verify.MIN_BASKET` 是全局量，这里改了必须在 `finally` 里还原 ——
    否则同进程后续的每日任务会静默按 N=200 的成分下限去跑 N=10。
    """
    original_min_basket = verify_mod.MIN_BASKET
    runs: dict[str, list[dict]] = {ARM_P0: [], ARM_P1: [], ARM_P2: []}
    try:
        verify_mod.MIN_BASKET = scaled_min_basket(n)   # `_daily_series` 调用时读它
        runs[ARM_P0].append(run_arm(xs, wmap, judged, by_year, None, n, k))
        for rep in range(int(reps)):
            seed = rep_seed(rep)
            runs[ARM_P1].append(run_arm(xs, wmap, judged, by_year,
                                        make_shuffle_score(seed), n, k))
            runs[ARM_P2].append(run_arm(xs, wmap, judged, by_year,
                                        make_shuffle_list(seed), n, k))
    finally:
        verify_mod.MIN_BASKET = original_min_basket      # ★ 无论如何都要还原
    return runs


def caliber_block(args, end: str, n: int, k: int, reps: int) -> dict:
    """口径声明块 —— **口径必须跟着数字一起落盘**（第 7 层）。

    一个数字离开脚本时，必须带着「它是哪个量、按什么口径算的」一起走。
    安慰剂的三个臂尤其危险：它们与 P0 只差一个钩子，
    而钩子是可以被悄悄改掉的 —— 声明块是读者唯一能查的锚。
    """
    return {
        "prereg": PREREG,
        "primary_basket_n": n,
        "k_rebalance_every": k,
        "window": [args.start, end],
        "window_start_is_the_judgement_window": str(args.start).startswith("2021"),
        "min_basket": scaled_min_basket(n),
        "min_basket_rule": "N≥50 → max(10, N//4)（**从 factor_width_mde import**，"
                           "不重新推导）",
        "placebo_seed": PLACEBO_SEED,
        "n_reps_requested": int(reps),
        "n_reps_preregistered": N_PLACEBO_REPS,
        "rep_seed_rule": "第 rep 个种子 = PLACEBO_SEED + rep（预注册 §2）；"
                         "再由 (rep 种子, 信号日, 用途盐) 派生当日随机流 ⇒ 逐日期独立",
        "t_crit": T_CRIT_5PCT,
        "t_crit_source": "scripts/factor_attribution.T_CRIT_5PCT（math.erf 二分反解）",
        "median_rule": MEDIAN_RULE,
        "code_path": {
            "shared": "三个臂调用**同一个** backtest_walk_forward_weights.run_year "
                      "→ aplus.backtest.evaluate_weights → aplus.predict.build_predictions；"
                      "同一条成本链（_daily / at_cost / cost_drag）、同一个 MIN_BASKET、"
                      "同一个分块 t（evolve_race._tstat, block=K）、"
                      "同一个 `series_from_chunks` 聚合",
            "only_difference": "score_transform 钩子：P0=None，P1=横截面内置换，"
                               "P2=随机名次覆盖全池",
            "forbidden": "禁止为安慰剂另写更快的路径 —— 那测的是另一个东西",
            "composite_score_untouched": "钩子只改 score 列；`aplus.factor.composite_score` "
                                         "的实现一个字没动（预注册 §4）",
        },
        "arm_pools": {
            ARM_P0: "当日可交易池中**因子完整**的那些（即 `build_predictions` 的 notna 过滤后）",
            ARM_P1: "**与 P0 完全同一个池**（NaN 原地不动，只换顺序）",
            ARM_P2: "当日**可交易池**（含因子不完整者，预注册原文「随机抽 200 只」）",
        },
        "p0_t_source": "本脚本 P0 臂实测的分块 t。**不硬编码 2.44**："
                       "预注册正文里的 2.44 是榜宽实验产物上的数，"
                       "硬编码它就等于免掉「P0 必须真的重跑一遍」。",
    }


def read_width_artifact_t(path: Path) -> float | None:
    """榜宽实验产物里的 N=200 分块 t（**可选**，缺失返回 None）。

    不是判决输入 —— 判决的 P0 只认本脚本自己跑的那一遍。
    它的用途是**交叉核对**：若两者差得离谱，说明安慰剂的配置已经漂离主档。
    文件不在（现在就不在）时返回 None 并在产物里说明，**不猜、不回落**。
    """
    try:
        if not path.is_file():
            return None
        blob = json.loads(path.read_text(encoding="utf-8"))
        widths = blob.get("widths") or {}
        return float(widths[str(PRIMARY_N)]["t_blocked"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def main(argv=None) -> int:
    """薄包装：把真正的活放进 `with begin_run(...)`。

    `begin_run` 是 `@contextmanager`；写成 `run = begin_run(...)` + `run.done()`
    会在账本里只留 `run_start`、不留 `run_end` ——
    「跑完了正常收尾」与「写到最后一个字崩了」在日志里长得一模一样。
    """
    args = _parse_args(argv)
    with begin_run("placebo_control", reps=args.reps, n=args.n, k=K,
                   start=args.start):
        return _run(args)


def _parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=JUDGEMENT_START,
                    help="★ 必须与判决窗口一致（2021-01-01），不得改成 2019")
    ap.add_argument("--end", default="")
    ap.add_argument("--n", type=int, default=PRIMARY_N,
                    help="主档榜宽；预注册钉死 200，不是自由参数")
    ap.add_argument("--reps", type=int, default=N_PLACEBO_REPS,
                    help="每个安慰剂臂的重复次数；预注册钉死 20")
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--matrices", type=Path, default=Path("data/reports/ic_matrices.parquet"))
    ap.add_argument("--width-report", type=Path,
                    default=Path("data/reports/factor_width_mde.json"),
                    help="榜宽实验产物（可选，仅供交叉核对）")
    ap.add_argument("--out", type=Path, default=Path("data/reports/placebo_control.json"))
    return ap.parse_args(argv)


def _run(args) -> int:
    # ★ 第 125 层：开跑时抓一次本文件的字节，盖章时比对。
    #   这一档要跑 ~46 分钟；期间若有人改了本文件，章会盖上**没跑过的字节**，
    #   而那种章不是弱证据、是**反向的错误证据** ⇒ 不一致就拒绝盖章。
    #
    # ⚠️ 必须抓在**这里**、`_run` 的第一行。两个位置都不对：
    #   · 放 `main()` 里 ⇒ `_run` 读不到（NameError 落在最后一行盖章处，
    #     而那已经**写完产物**了 ⇒ 产物在、章没有，第 131 层那一颗雷）
    #   · 放盖章**前**一行 ⇒ 「开跑的字节」变成「盖章的字节」⇒ 检查恒真，
    #     正是第 125 层要防的那种装饰性守卫
    expect_bytes = provenance.code_file_sha256(ROOT, "scripts/placebo_control.py")
    # ── 口径守卫：三条预注册的死线，一个都不许在这里被参数改掉 ──
    if not str(args.start).startswith("2021"):
        print(f"🚨 窗口 {args.start} 与判决窗口（2021 起）**不是同一个量**。",
              file=sys.stderr)
        return 2
    if args.n != PRIMARY_N:
        print(f"🚨 主档 N={args.n}，预注册钉死 {PRIMARY_N}（§3 规则 1："
              "不得在看到安慰剂数字之后改 P0 的档位）。", file=sys.stderr)
        return 2
    if args.reps > N_PLACEBO_REPS:
        print(f"🚨 --reps={args.reps} > 预注册的 {N_PLACEBO_REPS}。"
              "多跑可以（只报分布不报判决），但判决按完整的 "
              f"{N_PLACEBO_REPS} 个 rep 出。", file=sys.stderr)
        return 2

    lin = _rescale_check()
    if not lin["linear"]:
        print(f"🚨 成本非线性，at_cost 的缩放不再精确：{lin}", file=sys.stderr)
        return 2
    if not args.matrices.is_file():
        print(f"🚨 抽取表不存在：{args.matrices}", file=sys.stderr)
        return 2

    factors = list(FACTOR_NAMES)
    df = pd.read_parquet(args.matrices)
    unpack(df, factors)
    wmap = weights_by_year(df, factors)

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("🚨 主库为空")
        return 1
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"日线 {len(daily_map)} 只（{time.time() - t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    by_year: dict[int, list] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    judged = sorted(y for y in by_year if y >= 2021 and y in wmap)
    if not judged:
        print("🚨 没有任何判决年份")
        return 1

    print(f"\n判决年 {judged}｜N={args.n}｜K={K}｜窗口 {args.start}~{end}")
    print(f"安慰剂：P1/P2 各 {args.reps} 个 rep，种子 {PLACEBO_SEED}+rep"
          f"（{rep_seed(0)} … {rep_seed(args.reps - 1)}），逐日期独立置换")
    print(f"MIN_BASKET={scaled_min_basket(args.n)}（从 factor_width_mde import）")
    print(f"🚨 三个臂走**同一条** run_year → evaluate_weights → build_predictions；"
          f"唯一差别是 score_transform 一个入参。")
    print("─" * 96)

    runs = run_arms(xs, wmap, judged, by_year, args.n, K, args.reps)

    p0 = runs[ARM_P0][0]
    t_p0 = p0.get("t_blocked")
    p1 = summarize_reps(runs[ARM_P1], t_p0)
    p2 = summarize_reps(runs[ARM_P2], t_p0)
    p1_short = p1["n_reported"] < args.reps

    print(f"\nP0 观测：{p0['gross_annual_pct']:+.2%}/年｜"
          f"t(分块)={t_p0 if t_p0 is None else round(t_p0, 2)}"
          f"｜独立观测 {p0.get('n_independent_obs')}｜天数 {p0['n_days']}")
    for name, s in ((ARM_P1, p1), (ARM_P2, p2)):
        if s["n_reported"] == 0:
            print(f"{name}：一个 rep 都没测出 t（缺 {s['n_reps']} 个）")
            continue
        print(f"{name}：min {s['min']:+.2f}｜中位数 {s['median']:+.2f}｜"
              f"max {s['max']:+.2f}｜≥ P0 的 {s['n_ge_p0']}/{s['n_reported']} 个"
              f"｜缺 {s['missing_reps']} 个")

    verdict, branch = (None, "") if args.reps < N_PLACEBO_REPS else decide(t_p0, p1["t_list"])
    conditions = (placebo_conditions(t_p0, p1["t_list"])
                  if (t_p0 is not None and p1["n_reported"] == p1["n_reps"] and p1["n_reps"])
                  else None)
    reasons = []
    if args.reps < N_PLACEBO_REPS:
        reasons.append(f"只跑了 {args.reps} 个 rep（预注册 {N_PLACEBO_REPS}）")
    if t_p0 is None:
        reasons.append("P0 没测出 t")
    if p1_short:
        reasons.append(f"P1 有 {p1['missing_reps']} 个 rep 没测出 t")
    if reasons:
        branch = (f"{'；'.join(reasons)} ⇒ **不出判决**；"
                  "下面是诊断读数，不是结论。")
    print(f"\n判决分支：{branch}")
    if verdict:
        print(f"判决：{verdict}")

    out = {
        "generated": date.today().isoformat(),
        "prereg": PREREG,
        "window": [args.start, end],
        "k_rebalance_every": K,
        "t_crit": T_CRIT_5PCT,
        "caliber": caliber_block(args, end, args.n, K, args.reps),
        "arms": {
            ARM_P0: {"runs": runs[ARM_P0], "summary": summarize_reps(runs[ARM_P0], t_p0)},
            ARM_P1: {"runs": runs[ARM_P1], "summary": p1},
            ARM_P2: {"runs": runs[ARM_P2], "summary": p2},
        },
        "p0_t_blocked": t_p0,
        "p0_t_is_measured_not_hardcoded": True,
        "conditions": conditions,
        "condition_note": "四条分支的布尔条件**全部**求值后一起落盘。"
                          "它们不互斥：NULL ⊆ PASSES（`max < t(P0)` 是 NULL 的合取项之一），"
                          "所以判决码按 `_BRANCH_ORDER` 取第一条命中，"
                          "而**哪个条件成立过**在 `conditions` 里看得见。",
        "width_report_cross_check": {
            "path": str(args.width_report),
            "t_blocked": read_width_artifact_t(args.width_report),
            "note": "可选交叉核对，**不是判决输入**。文件缺失即 null —— "
                    "不猜、不从预注册正文里抄一个 2.44 顶上。",
        },
        "verdict": verdict,
        "verdict_branch": branch,
        "verdict_suppressed_reasons": reasons,
        "rules_from_prereg_section_3": [
            "① 不得在看到 P1 之后再改 P0 的档位或窗口（脚本对 --start/--n 直接拒绝）",
            "② 不得只报对自己有利的那一臂（三个臂的 20 个 t 全部在产物里）",
            "③ PLACEBO_BENCHMARK 不许解释成「因子也许还有一点」——"
            "正确动作是换基准（等权 → 流通市值加权）后整轮重测",
        ],
        "what_this_cannot_prove": [
            "不能证明前瞻有效（全样本内）",
            "不能证明 20%/年的目标可达 —— P0 的毛超额离门槛差得远，"
            "且 200 只 × 250 元/只的形态 5 万本金也无法交付",
            "PLACEBO_PASSES 只说明「横截面排序有信息量」，"
            "**不说明这个信息量扣成本后还剩多少**（后者是 K 扫描的事，两者不合并）",
            "P1 与 P2 的池子不同（P1 与 P0 同池、P2 是全可交易池），"
            "两者的差不能当成同一件事的两个读数",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float),
                        encoding="utf-8")
    # ★ 盖章必须在**写完之后**：章记的是「产物落盘那一刻」的字节哈希。
    #   ⚠️ 而它只对**真跑**生效——手写一个 sidecar 是形状 16
    #   （从不可测的输入捏造一个与真章无法区分的文件），比红着更糟。
    provenance.stamp(args.out, "scripts/placebo_control.py", root=ROOT,
          extra={"verdict": out.get("verdict"),
                 "verdict_branch": out.get("verdict_branch"),
                 "n_reps": out.get("conditions", {}).get("n_reps"),
                 "p0_t_blocked": out.get("p0_t_blocked"),
                 "test_window": out.get("window"),
                 "k_rebalance_every": out.get("k_rebalance_every"),
                 "expect_bytes_checked": True},
                expect_bytes=expect_bytes)
    print(f"\n→ {args.out}｜总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
