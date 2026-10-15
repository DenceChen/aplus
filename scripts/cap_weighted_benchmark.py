"""市值加权基准 —— A/B/C 三臂，判决**早已预注册**。

**判据逐字在**：`docs/TODO_cap_weighted_benchmark_2026-10-15.md`。
**本脚本只负责执行，一个字都不许改判据。**

## 要回答的问题（预注册 §0）

今天的「超额」是拿 **top-N 组合**减 **等权全池**得来的。
等权全池**根本不是一个基准** —— 没有任何被动资金拿得到它，
它既不是沪深 300，也不是中证全指，更不是任何一只可买的指数。
⇒ 「相对被动投资赚了多少」这个问题，用它**问不出来**。

本实验把口径两端都换成被动资金真拿得到的东西（流通市值加权），
然后问：**换完之后，因子的超额还在不在？**

## 三臂（预注册 §1）

| 臂 | 组合侧 | 基准侧 | 作用 |
|---|---|---|---|
| **A** | top-N 等权 | 等权全池 | 复现今天那 6.95%/年 |
| **B** | top-N 等权 | **市值加权**全池 | 只换分母 ⇒ 分解用 |
| **C** | top-N **市值加权** | **市值加权**全池 | **判决落在这一臂** |

🚨 **`C − B` 必须真的输出**（预注册 §1 末段）。
  只报 C 不报 B，等于把一次市值下注包装成「因子 alpha」。
  A → B 是**基准**口径的贡献，B → C 是**持仓**口径的贡献，两段不许混。

## 一台测量仪，不许有第二台

本脚本**不自己算任何逐日超额、不自己抽样基准、不自己分块**。
`run_year` / `_daily_series` / `_tstat` / `power_floor` / `_t`
全部从共享脚本 import —— 基准相减发生在共享路径最深处，
在旁边另写一份「市值加权版」就是第二台测量仪，
而两台之间的差**永远查不出来**（CLAUDE.md 第 22 层）。

守卫 `tests/test_cap_weighted_benchmark.py::test_experiment_reuses_run_year_and_never_recomputes_daily_returns`
用 AST 核这一条（子串会被注释与文档字符串骗过）。

## 口径的单一真相源

- 市值 = `close × a_shares`，`a_shares = volume×100/turn`
  —— 从 `scripts/turn_integrity_audit.py` **import**，不在这里重写
  （21/21 只对 `data/master/shares/` 的地面真值验过，中位比值 1.0000）。
  **不是 `float_shares`** —— 那含 B 股/H 股，系统性高估。
- 成本 / MIN_BASKET / K / 窗口起点 —— 全部从共享脚本 import 或作为默认值，
  **本脚本不许重新定义**（守卫 `test_cost_assumptions_are_not_redefined_anywhere_in_the_experiment`）。
- t_crit —— `factor_attribution.T_CRIT_5PCT`，本脚本**不许出现 1.96 字面量**
  （守卫用 AST 核浮点常量，不是核子串）。

## 用法

```bash
.venv/bin/python scripts/cap_weighted_benchmark.py
```
"""
from __future__ import annotations

import argparse
import json
import math
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
from aplus import provenance  # noqa: E402
from aplus.backtest import build_cross_sections  # noqa: E402
from aplus.capweight import CapContext, CapTable  # noqa: E402
from aplus.composite import unpack  # noqa: E402
from aplus.factor import FACTOR_NAMES, robust_z  # noqa: E402
from aplus.snapshot import load_factors_map  # noqa: E402
from aplus.store import LocalStore  # noqa: E402

from backtest_walk_forward_weights import (ENTRY_LAG, _rescale_check, _t,  # noqa: E402
                                           run_year, weights_by_year)
from factor_attribution import T_CRIT_5PCT, power_floor  # noqa: E402
from factor_width_mde import scaled_min_basket, series_from_chunks  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# 预注册 §4：与判决窗口同一套口径 —— 榜宽 N=200、调仓 K=21、窗口 2021 起。
BASKET = 200
K = 21

# 🚨 窗口末端**钉死**，理由见 CLAUDE.md 形状 23：
#   `end = args.end or str(pd.read_parquet(files[0])["date"].max())` 取的是
#   `000001.SZ` 一只票的末端，而仓里数据比快照多 2 天 ⇒ 不钉的话窗口会**无声地**
#   从 10-12 长到 10-14，20+ 份既有产物全部按 10-12 出的。
#   A 臂必须复现 `factor_width_mde.json` 的 1395 天 —— 末端一旦不同就不是同一个量。
WINDOW_END_PINNED = "2026-10-12"


def decide(gross_annual: float, t_blocked: float | None) -> tuple[str, str]:
    """预注册 §2 的三分支，**逐字照抄**，一个字都不许改。

    | 分支 | 条件 |
    |---|---|
    | `CAP_FACTOR_WORKS`      | C 臂毛超额 > 0 且分块 `t ≥ t_crit` |
    | `CAP_POSITIVE_NOT_SIG`  | C 臂毛超额 > 0 但 `t < t_crit` |
    | `CAP_ZERO_OR_NEGATIVE`  | C 臂毛超额 ≤ 0 |

    ★ 临界值**从 `factor_attribution` import**，本函数里不许出现 1.96。
      守卫 `test_verdict_module_never_hardcodes_the_critical_value` 用 AST 核。

    ★ `t` 测不出来（`None`）时落在 `CAP_POSITIVE_NOT_SIG`，**不是** WORKS。
      「测不到」与「测到了」必须长得不一样；把 None 当显著是第 16 种形状
      （「没测出显著」与「测不出来」被同一个符号呈现）。

    ★ 分支顺序 = 判据的严厉程度降序，且**互斥、无重叠**：
      第一条把「≤ 0」全部吃掉，后两条只对「> 0」成立。

    ## 🚨🚨 NaN 不是「未知」，是「压根没算出数」——它不属于任何一支（2026-10-15）

    实测原写法在 NaN 上的行为：

    | 入参 | 原判决 | 原文案 |
    |---|---|---|
    | `gross=NaN, t=None` | `CAP_POSITIVE_NOT_SIG` | 「C 臂毛超额 **+nan%/年 > 0**」 |
    | `gross=NaN, t=NaN`   | `CAP_POSITIVE_NOT_SIG` | 「…**t = nan < 1.9600**」 |
    | `gross=.05, t=NaN`   | `CAP_POSITIVE_NOT_SIG` | 「…**t = nan < 1.9600**」 |

    原因与 `np.isfinite` vs `!= 0` **完全同型**：`NaN <= 0` 与
    `NaN >= T_CRIT` **都是 False** ⇒ 两支都被跳过 ⇒ 落进第三支。

    🚨 而第三支的文案会**把 NaN 印成一次测量**，并断言「方向为正」：
       `t = nan < 1.9600` 读起来就是「测得偏小」，
       `+nan%/年 > 0` 读起来就是「方向为正」。
       ⇒ 这不是缺档被读成结论，是**凭空造出一个方向性结论**。

    ⇒ 新增第 0 支 `CAP_UNMEASURABLE`。**它不是第四个预注册分支**，
      而是「预注册的三支**没有这一状态**」的显式声明 ——
      缺档必须报错、绝不回落（本仓铁律）。
    """
    if not math.isfinite(gross_annual) or (
            t_blocked is not None and not math.isfinite(t_blocked)):
        return ("CAP_UNMEASURABLE",
                f"🚨 **判决不下**：C 臂毛超额 = {gross_annual!r}、"
                f"t = {t_blocked!r}，至少一项**不是有限值**。"
                "NaN 不是「未知」也不是「测得小」——是**压根没算出数**，"
                "而 `NaN <= 0` 与 `NaN >= t_crit` 都是 False，"
                "会让它一路滑进 `CAP_POSITIVE_NOT_SIG`，文案再把这份 NaN "
                "**印成一次「t 比 t_crit 小」的测量**。⇒ **预注册的三支没有这一状态**，"
                "此处不判定；先查上游为何没算出数。")
    if gross_annual <= 0:
        return ("CAP_ZERO_OR_NEGATIVE",
                f"C 臂毛超额 {gross_annual:+.2%}/年 ≤ 0 ⇒ **A 臂那 6.95%/年"
                f"全部是等权基准造出来的**；`factor_width_mde.json` / "
                f"`placebo_control.json` 的结论降级。正确动作是如实上报并作废结论，"
                f"**不是**回头去调 `a_shares`、**不是**改窗口、"
                f"**不是**退回等权基准。")
    if t_blocked is not None and t_blocked >= T_CRIT_5PCT:
        return ("CAP_FACTOR_WORKS",
                f"C 臂毛超额 {gross_annual:+.2%}/年 > 0 且分块 t = {t_blocked:.2f} "
                f"≥ t_crit ⇒ 市值口径下因子仍有可证明的超额。")
    t_txt = "测不出（样本不足）" if t_blocked is None else f"t = {t_blocked:.2f} < {T_CRIT_5PCT:.4f}"
    return ("CAP_POSITIVE_NOT_SIG",
            f"C 臂毛超额 {gross_annual:+.2%}/年 > 0 但 {t_txt} ⇒ "
            f"方向为正、**在现有样本上测不到显著性**。"
            f"⚠️ 这不等于「没有超额」，只等于「测不到」——"
            f"必须与 MDE 并排读。")


# 测试用别名：语义上「这就是判决函数本身」，不是替身。
decide_for_test = decide


def arm_stats(series: pd.Series, k: int, mb: int, *,
              portfolio_weighting: str, benchmark_weighting: str,
              extra: dict | None = None) -> dict:
    """一条毛超额逐日序列 → 一臂的全部数字。

    ★ 年化 / sd / MDE **走 `power_floor`**，分块 t **走 `_t`** ——
      与 A 臂那次测量同一个函数，否则 A 臂复现不了已发布数字。
    """
    pf = power_floor(series.to_numpy(float))
    t_blk, n_obs = _t(series, block=k)
    days = [str(d)[:10] for d in series.index]
    out = {
        "portfolio_weighting": portfolio_weighting,
        "benchmark_weighting": benchmark_weighting,
        "n_days": int(len(series)),
        # 🚨 预注册 §4：产物**必须打印实际终点**。
        #   `--end` 是钉死的，但产物自报的那一天才是「真的量到的那一天」——
        #   两者不一致时以产物为准，且差异看得见。
        "window_start": days[0] if days else None,
        "window_end": days[-1] if days else None,
        "min_basket": int(mb),
        "gross_annual_pct": pf["annual_mean_pct"],
        "annual_sd_pct": pf["annual_sd_pct"],
        "MDE_annual_pct": pf["MDE_annual_pct"],
        "t_naive": pf["t_naive"],
        "t_blocked": float(t_blk) if t_blk is not None else None,
        "n_independent_obs": int(n_obs) if n_obs is not None else None,
        "detectable": bool(abs(pf["annual_mean_pct"]) > pf["MDE_annual_pct"]),
    }
    if extra:
        out.update(extra)
    return out


def run_arm(xs, wmap, judged, by_year, *, portfolio_cap, benchmark_cap,
            cap_ctx=None, score_transform=None,
            portfolio_weighting: str, benchmark_weighting: str,
            k: int, mb: int) -> tuple[dict, pd.Series]:
    """跑一整臂，返回 `(那一臂的数字, 毛超额逐日序列)`。

    ★ 逐年拆开跑、最后用 `series_from_chunks` 拼回一条 ——
      与 `factor_width_mde.run_widths` **同一段拼法**。
      自己拼一遍就是「看起来一样其实不同」的第二个口径。
    """
    chunks: list = []
    per_year = []
    for y in judged:
        r = run_year(xs, wmap[y]["weights"], by_year[y], BASKET, BASKET, k,
                     daily_out=chunks,
                     score_transform=score_transform,
                     benchmark_cap=benchmark_cap, portfolio_cap=portfolio_cap,
                     cap_ctx=cap_ctx)
        if r.get("n_rows", 0):
            per_year.append(r)
    series = series_from_chunks(chunks)
    if len(series) < 60:
        raise SystemExit(f"🚨 样本太少（{len(series)} 天），不构成测量")
    stats = arm_stats(
        series, k, mb,
        portfolio_weighting=portfolio_weighting,
        benchmark_weighting=benchmark_weighting,
        extra={
            # 净口径只作对照：判决落在**毛**超额上（预注册 §2）。
            "annual_net_pct_at_cost": float(np.mean(
                [r["bt003"]["annual_net_pct"] for r in per_year])) if per_year else None,
            "annual_cost_pct_at_cost": float(np.mean(
                [r["bt003"]["annual_cost_pct"] for r in per_year])) if per_year else None,
            "cap_fallback_days": int(sum(
                r.get("cap_fallback_days", 0) for r in per_year)),
        })
    return stats, series


def a_shares_score_transform(table: CapTable):
    """预注册 §3.3：`a_shares` 作为**单因子**时的逐日分数。

    ★ 走 `score_transform` 钩子（`run_year` → `evaluate_weights` →
      `build_predictions` 的既有注入点），**不在旁边另搭一条排名路径**：
      逐日超额、分块、成本、成交闸门全部仍由那台机器出。
      钩子只负责「换一个排序依据」，这正是它的契约。

    ⚠️ `robust_z` 而不是原始股本直接排序：
      股本跨 3 个数量级，直接排序等价于「只买最大的几只」，
      而缩尾后才是在问「按市值排序有没有区分度」。
    """
    def _transform(day, df):
        out = df.copy()
        sh = pd.Series([table.a_shares_on(c, day) for c in out["code"]],
                       index=out.index, dtype=float)
        out["score"] = robust_z(sh)
        return out
    return _transform


def median_ratio_by_day(num: dict, den: dict) -> tuple[float | None, int]:
    """两天中位数序列的比值中位数 —— 预注册 §3.1。

    ⚠️ 两边**必须逐日成对**：某天组合有数而基准没数（或反过来）时，
      把两天各自的中位数拿来比就是在比两个不同的日子。
    缺任一侧的那天直接丢掉，并**报出丢了几天**。
    """
    pairs = [(num[d], den[d]) for d in sorted(set(num) & set(den))
             if np.isfinite(num[d]) and np.isfinite(den[d]) and den[d] > 0]
    dropped = len(set(num) | set(den)) - len(pairs)
    if not pairs:
        return None, dropped
    return float(np.median([a / b for a, b in pairs])), dropped


def main(argv=None) -> int:
    args = _parse_args(argv)
    with begin_run("cap_weighted_benchmark", basket=BASKET, k=K, start=args.start):
        return _run(args)


def _parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01",
                    help="★ 必须与判决窗口一致（2021-01-01），不得改成 2019")
    ap.add_argument("--end", default=WINDOW_END_PINNED,
                    help="★ 钉死窗口末端（形状 23）；不得留空")
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--matrices", type=Path,
                    default=Path("data/reports/ic_matrices.parquet"))
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/cap_weighted_benchmark.json"))
    return ap.parse_args(argv)


def _run(args) -> int:
    if not args.start.startswith("2021"):
        print(f"🚨 窗口起点 {args.start} 与判决窗口（2021 起）**不是同一个量**。",
              file=sys.stderr)
        return 2
    if not args.end:
        print("🚨 `--end` 不许留空：末端必须显式钉死（形状 23：一只票定全池窗口）。",
              file=sys.stderr)
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

    t0 = time.time()
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"日线 {len(daily_map)} 只（{time.time() - t0:.0f}s）", flush=True)

    cap_table = CapTable(daily_map)
    # ★ 基准侧与组合侧**共用同一张市值表**（缓存只建一次），
    #   但**各自一个 context** —— context 身上挂着回退计数与诊断通道，
    #   共用会把两段统计混在一起。
    ctx_bench = CapContext(cap_table)
    xs = build_cross_sections(store, args.start, args.end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG,
                              benchmark_cap=True, cap_ctx=ctx_bench)
    dates = xs["_dates"]
    by_year: dict[int, list] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    print(f"横截面 {len(dates)} 个交易日（{time.time() - t0:.0f}s）", flush=True)

    judged = sorted(y for y in by_year if y >= 2021 and y in wmap)
    if not judged:
        print("🚨 没有任何判决年份")
        return 1

    mb = scaled_min_basket(BASKET)
    print(f"\n判决年 {judged}｜N={BASKET}｜K={K}｜MIN_BASKET={mb}"
          f"（scaled_min_basket，从 factor_width_mde import）"
          f"｜t_crit={T_CRIT_5PCT:.6f}（从 factor_attribution import）")
    print(f"窗口 {args.start} ~ {args.end}（末端钉死，见形状 23）")
    print("─" * 108)
    print(f"{'臂':>4}{'组合':>10}{'基准':>10}{'天数':>7}{'毛超额/年':>11}"
          f"{'sd/年':>9}{'MDE/年':>9}{'t(分块)':>9}{'独立观测':>9}{'终点':>13}")

    arms: dict[str, dict] = {}
    series: dict[str, pd.Series] = {}
    original_min_basket = verify_mod.MIN_BASKET
    try:
        verify_mod.MIN_BASKET = mb          # `_daily_series` 在调用时读这个模块属性
        for name, kw in (
            ("A", dict(portfolio_cap=False, benchmark_cap=False,
                       portfolio_weighting="equal", benchmark_weighting="equal")),
            ("B", dict(portfolio_cap=False, benchmark_cap=True,
                       portfolio_weighting="equal", benchmark_weighting="cap")),
            ("C", dict(portfolio_cap=True, benchmark_cap=True,
                       cap_ctx=CapContext(cap_table),
                       portfolio_weighting="cap", benchmark_weighting="cap")),
        ):
            stats, ser = run_arm(xs, wmap, judged, by_year, k=K, mb=mb, **kw)
            arms[name], series[name] = stats, ser
            _print_arm(name, stats)
            sys.stdout.flush()

        # ── 预注册 §1 的最强不变量：cap 参数真的没接上时，三臂会逐位相同 ──
        # 「组合端也走市值加权那一段代码，但喂给它一组全相等的假市值」
        # ⇒ 加权退化回等权 ⇒ 这一臂**必须**落回 B 臂（同一基准口径）。
        # ⇒ 它把「B 与 C 的差全部来自市值加权」这句话变成一次实测，
        #   而不是一句推理。（全等市值下 `sum(w_i·x_i)` 与 `.mean()` 的浮点
        #   舍入顺序不同，所以是容差相等而不是逐位相等 —— 逐位那条在
        #   `_daily_series(weights_col=None)` 的单元守卫上钉。）
        uniform_ctx = CapContext(CapTable(daily_map, weight_override=1.0))
        v_stats, _ = run_arm(xs, wmap, judged, by_year, k=K, mb=mb,
                             portfolio_cap=True, benchmark_cap=True,
                             cap_ctx=uniform_ctx,
                             portfolio_weighting="cap(uniform)",
                             benchmark_weighting="cap")
        b_val = arms["B"]["gross_annual_pct"]
        v_val = v_stats["gross_annual_pct"]
        rel = abs(v_val - b_val) / abs(b_val) if b_val else float("inf")
        # 🚨 这条对照**不是装饰**：它不成立就说明「C 与 B 的差全部来自市值加权」
        #   这句话是假的，而报告正打算把那 个差印成「因子贡献」。
        if rel > 1e-12:
            raise SystemExit(
                f"🚨 全等市值对照臂未落回 B 臂：V={v_val!r} vs B={b_val!r}"
                f"（相对差 {rel:.3e}）。⇒ 加权路径里还有别的东西在起作用，"
                f"**B → C 的差不能当成持仓端市值加权的贡献**。")
        arms["V_uniform_control"] = v_stats
        print(f"{'V':>4}{'cap(全等)':>12}{'cap':>10}{v_stats['n_days']:>7}"
              f"{v_val:>11.2%}{v_stats['annual_sd_pct']:>9.2%}"
              f"{v_stats['MDE_annual_pct']:>9.2%}"
              f"{(v_stats['t_blocked'] or float('nan')):>9.2f}"
              f"{str(v_stats['n_independent_obs']):>9}{v_stats['window_end']:>13}")

        # ── 预注册 §3.3：`a_shares` 作为单因子的分块 t（**诊断，不是判据**）──
        st = a_shares_score_transform(cap_table)
        shares_eq, _ = run_arm(
            xs, wmap, judged, by_year, k=K, mb=mb,
            portfolio_cap=False, benchmark_cap=False,
            portfolio_weighting="equal(a_shares only)",
            benchmark_weighting="equal",
            score_transform=st)
        shares_cap_b, _ = run_arm(
            xs, wmap, judged, by_year, k=K, mb=mb,
            portfolio_cap=False, benchmark_cap=True,
            portfolio_weighting="equal(a_shares only)",
            benchmark_weighting="cap",
            score_transform=st)
        print(f"{'a':>4}{'a_shares':>10}{'equal':>10}{shares_eq['n_days']:>7}"
              f"{shares_eq['gross_annual_pct']:>11.2%}{shares_eq['annual_sd_pct']:>9.2%}"
              f"{shares_eq['MDE_annual_pct']:>9.2%}"
              f"{(shares_eq['t_blocked'] or float('nan')):>9.2f}"
              f"{str(shares_eq['n_independent_obs']):>9}{shares_eq['window_end']:>13}")
        print(f"{'a*':>3}{'a_shares':>10}{'cap':>10}{shares_cap_b['n_days']:>7}"
              f"{shares_cap_b['gross_annual_pct']:>11.2%}"
              f"{shares_cap_b['annual_sd_pct']:>9.2%}"
              f"{shares_cap_b['MDE_annual_pct']:>9.2%}"
              f"{(shares_cap_b['t_blocked'] or float('nan')):>9.2f}"
              f"{str(shares_cap_b['n_independent_obs']):>9}"
              f"{shares_cap_b['window_end']:>13}")
    finally:
        verify_mod.MIN_BASKET = original_min_basket      # ★ 无论如何都要还原

    # ── 诊断：组合 vs 基准的流通市值中位数比（预注册 §3.1）──
    ctx_c = CapContext(cap_table)
    port_diag = _diag_for_portfolio(xs, wmap, judged, by_year, ctx_c, mb,
                                    reference=arms["C"]["gross_annual_pct"])
    ratio, dropped = median_ratio_by_day(port_diag,
                                         ctx_bench.diag.get("benchmark", {}))
    print("─" * 108)

    c_minus_b = arms["C"]["gross_annual_pct"] - arms["B"]["gross_annual_pct"]
    a_minus_b = arms["A"]["gross_annual_pct"] - arms["B"]["gross_annual_pct"]
    verdict, branch = decide(arms["C"]["gross_annual_pct"], arms["C"]["t_blocked"])

    print(f"\n分解：A − B = {a_minus_b:+.2%}/年（**基准**口径换市值加权的贡献）"
          f"\n      C − B = {c_minus_b:+.2%}/年（**持仓**口径换市值加权的贡献）")
    print(f"诊断：组合/基准 流通市值中位数比 = {ratio:.4f}"
          f"（>1 ⇒ 组合偏小盘；成对后丢掉 {dropped} 天）")
    print(f"诊断：a_shares 单因子 分块 t = {shares_eq['t_blocked']:.2f}"
          f"（等权基准）/ {shares_cap_b['t_blocked']:.2f}（市值基准）"
          f" —— 🚨 只作解释，**不是判据**")
    print(f"⚠️ {verdict}：{branch}")

    out = {
        "generated": date.today().isoformat(),
        "prereg": "docs/TODO_cap_weighted_benchmark_2026-10-15.md",
        "window_arg": [args.start, args.end],
        "k_rebalance_every": K,
        "basket_n": BASKET,
        "min_basket": int(mb),
        "min_basket_source": "factor_width_mde.scaled_min_basket（import，不重定义）",
        "t_crit": T_CRIT_5PCT,
        "t_crit_source": "factor_attribution.T_CRIT_5PCT（import，模块内无 1.96 字面量）",
        "cap_definition": {
            "formula": "close × a_shares，a_shares = volume × 100 / turn",
            "source": "aplus.capweight → scripts.turn_integrity_audit.implied_a_shares（import）",
            "not_float_shares": ("float_shares 含 B 股/H 股，系统性高估；"
                                 "a_shares 对 data/master/shares/ 21/21 只命中"
                                 "（中位比值 1.0000，区间 [0.9889, 1.0027]）"),
            "renormalized_daily": True,
        },
        "arms": arms,
        "controls": {
            "V_uniform_control": {
                "what": ("组合端走同一段市值加权代码，但喂全相等假市值 ⇒ 退化回等权。"
                         "它必须落回 B 臂（同一基准口径）"),
                "gross_annual_pct": v_val,
                "equals_B": bool(abs(v_val - b_val) <= 1e-12 * max(1.0, abs(b_val))),
                "relative_diff_vs_B": rel,
                "tolerance_note": ("容差 1e-12（相对）而非逐位：全等权重下"
                                   "`sum(w_i·x_i)` 与 `.mean()` 的浮点舍入顺序不同。"
                                   "「不传权重时逐位不变」由 "
                                   "test_daily_series_without_weights_col_is_bitwise_unchanged 钉住"),
            },
        },
        "diagnostics": {
            "cap_exposure_ratio": ratio,
            "cap_exposure_ratio_note": ("组合持仓的流通市值中位数 ÷ 基准抽样池的中位数，"
                                        "**逐日成对**后取中位数。> 1 ⇒ 组合偏小盘。"
                                        "分子分母都由共享路径交出（CapContext.diag），"
                                        "不在这里第二次抽样"),
            "cap_exposure_ratio_days_dropped": dropped,
            "c_minus_b": c_minus_b,
            "c_minus_b_note": "C − B = 持仓端市值加权的贡献（预注册 §1 强制输出）",
            "a_minus_b": a_minus_b,
            "a_minus_b_note": "A − B = 基准端换市值加权的贡献",
            "a_shares_t_blocked": shares_eq["t_blocked"],
            "a_shares_arm": shares_eq,
            "a_shares_arm_cap_benchmark": shares_cap_b,
            "a_shares_t_blocked_note": ("`a_shares` 当单因子（robust_z 后排序）的分块 t。"
                                        "🚨 **诊断，不是判据** —— 预注册 §3 明令"
                                        "这些只作解释，不得反过来用于挑臂"),
        },
        "verdict": verdict,
        "verdict_branch": branch,
        "cost_assumptions_unchanged": (
            "预注册 §4：成本链 / MIN_BASKET 规则 / K=21 / 窗口起点 2021-01-01 "
            "全部未改。本脚本不定义 FEE_SCHEDULE、COST_REAL_50K、MIN_BASKET、"
            "COST_REAL_CAPITAL、EQUITY_WEIGHT 中的任何一个。"),
        "what_this_cannot_prove": [
            "全样本内（权重用同一段历史拟合）⇒ 量级偏乐观，只有符号与排序有参考价值",
            "**不能证明前瞻有效性**，也不能证明 20%/年（5 万本金 N=10 需 ≥26.41% 毛超额）",
            "摩擦正下界 4.787%/年与本判决无关、也不因本判决改变",
            "「t < t_crit」只等于「测不到」，**不等于「没有超额」** —— 必须与 MDE 并排读",
            "B 臂只用于分解；⚠️ A 臂的数字一律不得作为最终结论",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(args.out, "scripts/cap_weighted_benchmark.py", root=ROOT,
                     data_root=args.data_root)
    print(f"\n→ {args.out}｜总耗时 {time.time() - t0:.0f}s")
    return 0


def _diag_for_portfolio(xs, wmap, judged, by_year, ctx, mb, reference: float):
    """重跑一遍 C 臂的组合端，只为把 `CapContext.diag["portfolio"]` 取出来。

    ⚠️ **为什么值得多花一趟**：这个比值的分子只存在于
    `evaluate_weights` 那一层。在实验脚本里对着 `daily_map` 重算一遍持仓
    与市值，就是**第二台测量仪**（而且连抽样时点都可能对不上）。
    ⇒ 多跑一趟 C 臂（约 1/5 的总耗时）比造第二台便宜。

    🚨 若这一趟的毛超额与 `arms["C"]` 对不上，本函数**当场报错** ——
      「诊断用的那一趟」与「判决用的那一趟」必须是同一个量；
      对不上就说明它们量的根本不是同一件事，而差值会被当成诊断印进报告。
    """
    verify_mod.MIN_BASKET = mb
    stats, _ = run_arm(xs, wmap, judged, by_year, k=K, mb=mb,
                       portfolio_cap=True, benchmark_cap=True, cap_ctx=ctx,
                       portfolio_weighting="cap", benchmark_weighting="cap")
    got = stats["gross_annual_pct"]
    if abs(got - reference) > 1e-12 * max(1.0, abs(reference)):
        raise SystemExit(
            f"🚨 诊断那一趟的 C 臂毛超额 {got!r} 与判决那一趟 {reference!r} 不一致 —— "
            f"两趟量的不是同一个量，比值诊断会被当成结论印出去。")
    return ctx.diag.get("portfolio", {})


def _print_arm(name: str, s: dict) -> None:
    print(f"{name:>4}{s['portfolio_weighting']:>10}{s['benchmark_weighting']:>10}"
          f"{s['n_days']:>7}{s['gross_annual_pct']:>11.2%}{s['annual_sd_pct']:>9.2%}"
          f"{s['MDE_annual_pct']:>9.2%}"
          f"{(s['t_blocked'] if s['t_blocked'] is not None else float('nan')):>9.2f}"
          f"{str(s['n_independent_obs']):>9}{s['window_end']:>13}")


if __name__ == "__main__":
    sys.exit(main())
