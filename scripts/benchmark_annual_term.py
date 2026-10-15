"""基准年化项（`benchmark_annual`）对 20% 判决的影响 —— 判决**早已预注册**。

**判据逐字在**：`docs/PREREG_BENCHMARK_ANNUAL_TERM_2026-10-15.md`。
**本脚本只负责执行，一个字都不许改判据。**

## 为什么需要这个产物

`aplus/hurdle.py:80-102` 的签名逐字写着

```
账户净值 = **基准** + 毛超额 − 年成本
⇒ 毛超额 = target − benchmark_annual + rounds × cost
```

而 docstring 自己承认 `benchmark_annual` 默认 `0.0` 是「把一个**藏起来的假设**
摆到签名上」。全仓 9 条调用点**全部**用默认值
（`factor_width_mde.py:473` 等）⇒ 产物里的
`hurdle_gross_annual = 0.2639047619047619` **隐含「基准年化 = 0」**。

⚠️ 而 `docs/GOAL_20PCT_VERDICT_2026-10-15.md` §三 逐字写着
「**升 K 最多只能把门槛压到 20%**」—— 那句话的**下限正是这个假设**。

⇒ 本产物要回答的是**闭式反解**：基准要拿到多少，才够得着 20% **绝对**净值。

## 一台测量仪，不许有第二台

- 交易日历 = `panel.window_dates(...)` —— 与 `build_cross_sections` 里
  `panel.dates` **同一个函数**（`backtest.py:638`）
- 基准 = `_market_by_day(daily_map, dates, entry_lag=ENTRY_LAG, factors_map=...,
  adj_cache={})` —— 与 `backtest.py:639` **同一个函数同一组入参**
- 不传 `cap_ctx` ⇒ **等权臂**，正是 `factor_width_mde` 减的那一个

守卫 `tests/test_benchmark_annual_term.py::test_reproduction_path_is_the_same_instrument`
用 AST 核「不许自己算日历、不许自己抽样基准」。

## 两个口径必须并排印（预注册 §2）

| 口径 | 算式 | 地位 |
|---|---|---|
| 算术 ×244 | `mean(market_pct) × DAYS_PER_YEAR` | **判决用** —— 与 `annual_gross_pct = mean(daily_excess) × DAYS_PER_YEAR` 逐字同口径 |
| 净值复利 | `prod(1+r)^(244/n) − 1` | **对照** —— 这是账户真的赚到的那份，算术口径会高估它 |

🚨 守卫强制两个字段**同时存在** ⇒ 不许只报好看的那个
（`compare-like-calibers-or-not-at-all`）。

## X* 不许手打

反解阈值必须由 `hurdle_rate` 与 `factor_width_mde.json` 的**实测**毛超额**算**出来。
守卫 `test_the_required_benchmark_is_computed_not_typed` 用 AST 核
「脚本里不许出现任何 0.2 / 0.21 / 0.18 的字面量阈值」。

## 用法

```bash
.venv/bin/python scripts/benchmark_annual_term.py
```
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import RUN  # noqa: E402

from aplus import provenance  # noqa: E402
from aplus.backtest import ENTRY_LAG, _market_by_day  # noqa: E402
from aplus.evolve import LIVE_REBALANCE_EVERY  # noqa: E402
from aplus.hurdle import (COST_REAL_50K, DAYS_PER_YEAR, TARGET_NET,  # noqa: E402
                          hurdle_rate, rounds_per_year)
from aplus.panel import window_dates  # noqa: E402
from aplus.snapshot import load_factors_map  # noqa: E402
from aplus.store import LocalStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# ★ 判决窗口与 `cap_weighted_benchmark.json` 的 `window_arg` 同一段。
#   两份产物减的必须是**同一个市场**，否则它们的差没有意义。
WINDOW = ("2021-01-01", "2026-10-12")

# ★ 毛超额的来源：**已存在的** `factor_width_mde.json`（判决之前就已落盘）。
#   不重跑榜宽实验（622s），也不在这里重算毛超额 ——
#   重算就是第二台测量仪（`cap_weighted_benchmark.py:31-34` 同款纪律）。
WIDTH_SOURCE = ROOT / "data" / "reports" / "factor_width_mde.json"


def _gross_by_n(art: dict) -> dict[int, float]:
    """从 `factor_width_mde.json` 取每档的毛超额（分数，不是百分数）。

    ⚠️ `widths` 的键是 **n 的字符串**，不是下标 ⇒ 逐位照抄会 `KeyError: 0`。
    """
    widths = art["widths"]
    rows = list(widths.values()) if isinstance(widths, dict) else widths
    return {int(r["n"]): float(r["gross_annual_pct"]) for r in rows}


def _bench_annual(series: list[float]) -> dict:
    """基准自身的年化 —— 两个口径一起算。

    ⚠️ 算术口径**高估**净值复利口径（波动拖累），两个数必须都在产物里，
    否则读者会把「平均每根 K 线的涨幅」当成「账户实际赚到的净值」。
    """
    r = np.asarray(series, dtype=float)
    n = int(len(r))
    if n == 0:
        raise SystemExit("🚨 判决窗口内一个交易日都没有 —— 复刻路径坏了，不是市场问题")
    return {
        "n_trading_days": n,
        "arithmetic_mean_x_days_per_year": float(r.mean()) * DAYS_PER_YEAR,
        "compounded_nav": float(np.prod(1.0 + r) ** (DAYS_PER_YEAR / n) - 1.0),
        "daily_sd_x_days_per_year": float(r.std(ddof=1)) * DAYS_PER_YEAR,
        "zero_days": int((r == 0.0).sum()),
    }


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "data" / "reports"
                    / "benchmark_annual_term_2026-10-15.json")
    ap.add_argument("--width-source", type=Path, default=WIDTH_SOURCE)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    # 生成器字节冻结（第 125 层）——🚨 取样点必须在**跑之前的第一条可执行语句**：
    # 放在盖章前一行就等于「拿开跑的字节盖开跑的章」⇒ 恒真（第 131 层）。
    expect_bytes = provenance.code_file_sha256(
        ROOT, "scripts/benchmark_annual_term.py")
    args = _parse_args(argv)
    with RUN("benchmark_annual_term", window=list(WINDOW)) as run:
        run.step("装载日线与事件")
        t0 = time.time()
        store = LocalStore(args.data_root)
        files = sorted((store.root / "master" / "daily").glob("*.parquet"))
        if not files:
            print("🚨 主库为空")
            return 1
        daily_map = {p.stem: store.read_daily(p.stem) for p in files}
        factors_map = load_factors_map(store)
        print(f"日线 {len(daily_map)} 只（{time.time() - t0:.0f}s）", flush=True)

        with run.step("基准序列（与 backtest.py:639 同函数同入参）"):
            dates = window_dates(daily_map, *WINDOW)
            print(f"交易日 {len(dates)} 个：{dates[0]} ~ {dates[-1]}", flush=True)
            market = _market_by_day(daily_map, dates, entry_lag=ENTRY_LAG,
                                    factors_map=factors_map, adj_cache={})
            print(f"基准序列 {len(market)} 天（{time.time() - t0:.0f}s）", flush=True)

        with run.step("判决（判据逐字在预注册 §1）"):
            art = json.loads(args.width_source.read_text(encoding="utf-8"))
            gross = _gross_by_n(art)
            k = LIVE_REBALANCE_EVERY
            rpy = rounds_per_year(k)
            # ★ `benchmark_annual` **不传** ⇒ 取默认 0.0（= 现有 9 条调用点的行为）
            h0 = hurdle_rate(rounds_per_year=rpy)
            bench = _bench_annual([market[d] for d in dates])

            # 🚨 X\* = hurdle_rate(0) − 实测毛超额 = −hurdle_rate(X) = ...
            #    **必须算，不许手打**（守卫用 AST 核）
            rows = []
            for n in sorted(gross):
                g = gross[n]
                rows.append({
                    "n": n,
                    "gross_annual_pct": g,
                    "hurdle_if_bench_zero": h0,
                    # 毛超额要够到 TARGET_NET，基准必须给到这么多：
                    "required_benchmark_annual": h0 - g,
                    "reachable_under_arithmetic": (h0 - g) <= bench[
                        "arithmetic_mean_x_days_per_year"],
                    "reachable_under_compounded": (h0 - g) <= bench["compounded_nav"],
                })

            # 预注册 §1 R1/R2/R3 —— 逐字执行，不许改
            exec_n = min(rows, key=lambda r: r["n"])      # N=10 = 可执行的那一档
            need = exec_n["required_benchmark_annual"]
            got = bench["arithmetic_mean_x_days_per_year"]
            if got >= need:
                verdict, branch = "VERDICT_DISTORTED", (
                    f"实测基准年化 {got:.4%} ≥ N=10 所需的 {need:.4%} ⇒ "
                    f"「4 因子榜宽路线封死」的判决**失真**，必须撤销并重写")
            elif got < 0:
                verdict, branch = "SEALED_BENCHMARK_NEGATIVE", (
                    f"实测基准年化 {got:.4%} < 0 ⇒ 门槛**上升**而非下降"
                    f"（docstring 逐字：基准负值合法且是唯一允许为负的参数）"
                    f"⇒ N=10 需要的 {need:.4%} 更不可能达到 ⇒ 封口更紧")
            else:
                verdict, branch = "SEALED_BENCHMARK_TERM_IMMATERIAL", (
                    f"实测基准年化 {got:.4%} < N=10 所需的 {need:.4%} ⇒ "
                    f"**判决不变**；但 `benchmark_annual=0.0` 这个默认值仍必须逐字报告")

            out = {
                "generated": "2026-10-15",
                "prereg": "docs/PREREG_BENCHMARK_ANNUAL_TERM_2026-10-15.md",
                "window_arg": list(WINDOW),
                "k_rebalance_every": k,
                "entry_lag": ENTRY_LAG,
                "rounds_per_year": rpy,
                "cost_per_round": COST_REAL_50K,
                "target_net": TARGET_NET,
                "hurdle_gross_annual_bench0": h0,
                "hurdle_source": ("aplus.hurdle.hurdle_rate（import）—— 本脚本"
                                  "不重写成本链、不出现 0.20 / 26.39% 之类的字面量"),
                "width_source": str(args.width_source.relative_to(ROOT)),
                "benchmark": bench,
                "benchmark_caliber": {
                    "arithmetic_mean_x_days_per_year":
                        "★ 判决口径 —— 与 `annual_gross_pct = mean(daily_excess) × "
                        "DAYS_PER_YEAR`（backtest_walk_forward_weights.py:512）逐字同口径",
                    "compounded_nav": "对照口径 —— 账户**真的**赚到的那份；"
                                       "算术口径会**高估**它（波动拖累）",
                },
                "widths": rows,
                "verdict": verdict,
                "verdict_branch": branch,
                "caliber_assumptions_unchanged": (
                    "预注册 §4：交易日历取 `panel.window_dates`、基准取 "
                    "`backtest._market_by_day`、成本/K/目标全部从 `aplus.hurdle` "
                    "import —— 本脚本不重定义其中的任何一个，也不自算日历、不自抽样基准"),
                "what_this_cannot_prove": [
                    "本产物量的是**仓库自己减的那个基准**（`MARKET_SAMPLE=150` "
                    "等权随机抽样，种子 42）—— **不是**沪深300 / 中证全指，"
                    "换基准是另一次实验",
                    "**不能证明前瞻有效性**；毛超额是样本内测的",
                    "**不能证明基准抽样本身无偏**（150 只、种子固定）",
                    "🚨 R2 下「判决不变」**不等于「基准无关紧要」** —— "
                    "结论相同但**理由不同**：「隐含基准=0」是一个没写出来的前提，"
                    "即使不影响本轮判决也必须写出来",
                    "⚠️ 本产物**不能**推翻 `docs/GOAL_20PCT_VERDICT_2026-10-15.md` §三 "
                    "那句话的**方向**（「升 K 最多把门槛压到 20%」）—— 它只补上"
                    "那句话**没写出来的前提**",
                ],
            }

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        provenance.stamp(args.out, "scripts/benchmark_annual_term.py", root=ROOT,
                         data_root=args.data_root, expect_bytes=expect_bytes)
        print(f"\n→ {args.out}｜总耗时 {time.time() - t0:.0f}s")
        return 0


if __name__ == "__main__":
    sys.exit(main())
