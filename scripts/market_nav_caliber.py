#!/usr/bin/env python
"""判决窗口上**基准自身**的复利净值（G1，预注册 §4）。

## 为什么这个脚本存在

`aplus/hurdle.py::hurdle_rate` 的恒等式是

    净 = 毛超额 − 年成本   ⇒   毛超额 = target + rounds × cost

它**当基准 = 0 时才成立**。真实的账户净值是

    账户净值 = 基准 + 超额 − 摩擦

⇒ 全仓判决挂在「超额」上，而用户的目标是「净值」。
⇒ 缺的正是**基准自己在判决窗口上的复利收益**，它从未被测量过。

预注册：`docs/TODO_nav_vs_excess_caliber_2026-10-15.md`。

## ★ 为什么它不加载因子面板也能跑（这是本脚本能存在的前提）

GB 级 / 10 分钟那一层是**因子面板**（8 因子 × 5460 只 × 1700 天）。
基准只需要 `close` / `prev_close`，依赖的 `factors_map` 是
`master/factor/` 下的**除权事件表**——实测 5556 个文件 / **22 MB**，
每只票只有几十行 `adjust_factor`。

⇒ 基准可以**完整复现生产口径**而不碰面板。
⚠️ 这不是「省了一步」，是「这一步本来就不贵」——
**贵的是面板，不是基准，而分母一直比分子便宜。**

## ★ 为什么调用 `_market_by_day` 而不是自己重写一遍

抽样（种子 42 / 150 只）、`entry_lag`、后复权口径、上市前剔除
（`pos == 0` 那个 −114%/年 的事故）**全在这个函数里**。
在旁边另写一个「基准版」就是**第二台测量仪**，两台之间的差永远查不出来
（第 22 层 / 形状 17）。

⇒ 这里**只加参数，不加路径**：`benchmark_cap=True` 时
`cap_sink` 拿到市值加权口径，与等权**共用同一批 `ret`**。

## 口径必须显式标注（预注册 G2）

本脚本报出的是**几何（复利）**，不是算术；
基准本身可以是**等权**或**市值加权**，两者在 2021–2026 差 8.04pp/年
（`cap_weighted_benchmark.json`）。
⇒ 两者都报，且字段名自带口径。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

from aplus.backtest import (ENTRY_LAG, MARKET_SAMPLE, _market_by_day)
from aplus.capweight import CapContext, CapTable
from aplus.panel import market_date_axis
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore

# 预注册 §4 G1：与 cap_weighted_benchmark.json 的 window_arg 逐字相同。
# ⚠️ 端点是**开区间**（回测窗口语义），与 cap_weighted_benchmark 同款。
WINDOW_START = "2021-01-01"
WINDOW_END = "2026-10-12"


def _annualized_compound(final_multiple: float, n_days: int,
                         days_per_year: int = 244) -> float | None:
    """复利终值 → **几何**年化。

    ⚠️ 刻意不用 `mean * 244`（算术）——那是 `factor_attribution.py::power_floor`
    的 `annual_mean_pct` 那一路，与 `TARGET_NET = 0.20`（复利）不是同一个量
    （第 4 次「比较前先确认是同一个量」）。两者在 σ²T/2 上分开。
    """
    if final_multiple <= 0 or n_days <= 0:
        return None
    years = n_days / days_per_year
    if years <= 0:
        return None
    return float(final_multiple ** (1.0 / years) - 1.0)


def _sample_equity_weighted_daily(daily_map: dict, dates: list,
                                   entry_lag: int, factors_map: dict,
                                   adj_cache: dict) -> float:
    """**全池等权**逐日收益（不过抽样）——用来量抽样偏差（预注册 G5）。

    ⚠️ 这是**对照臂**，不是被测对象。它走的是同一份 `ret` 定义
    （`_next_bars` 的成交窗口 + 后复权 + 上市前剔除），
    唯一区别是**不抽样** ⇒ 两者之差就是抽样带来的偏差。
    """
    out = []
    for day in dates:
        frame = _next_bars_all(daily_map, day, entry_lag, factors_map, adj_cache)
        if not len(frame):
            continue
        ret = frame["close"] / frame["prev_close"] - 1
        out.append(float(ret.mean()))
    return float(np.mean(out)) if out else float("nan")


def _next_bars_all(daily_map: dict, after_date: str, entry_lag: int,
                   factors_map: dict, adj_cache: dict):
    """全池走 `_next_bars`（同一函数、同一语义），只是不抽样。"""
    from aplus.backtest import _next_bars
    return _next_bars(daily_map, list(daily_map), after_date,
                      entry_lag=entry_lag, factors_map=factors_map,
                      adj_cache=adj_cache)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="判决窗口上基准自身的复利净值（预注册 G1）")
    ap.add_argument("--start", default=WINDOW_START)
    ap.add_argument("--end", default=WINDOW_END)
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "market_nav_caliber.json"))
    ap.add_argument("--skip-full-pool", action="store_true",
                    help="跳过全池对照臂（很慢；不跑则 G5 的偏差项为 null 且标为未测）")
    args = ap.parse_args(argv)

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("🚨 主库为空", file=sys.stderr)
        return 2

    t0 = time.time()
    # ★ 只读抽样池 + 面板轴，绝不建面板。
    rng = random.Random(42)
    pool = [p.stem for p in files]
    sample = set(rng.sample(pool, min(MARKET_SAMPLE, len(pool))))

    # ★ `cap_weighted_benchmark.py` 的池是**全池**（5460 只），不是抽样池 ——
    #   市值加权要用全池的 `a_shares`，抽样池会让权重分母变小。
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"日线 {len(daily_map)} 只 + 因子 {len(factors_map)} 只（{time.time() - t0:.0f}s）",
          flush=True)

    all_dates = market_date_axis(daily_map)
    dates = [d for d in all_dates if args.start <= str(d) < args.end]
    if len(dates) < 2:
        print(f"🚨 窗口内交易日不足：{len(dates)}", file=sys.stderr)
        return 1

    # ── 等权臂（生产默认口径）──
    equal = _market_by_day(daily_map, dates, entry_lag=ENTRY_LAG,
                           factors_map=factors_map)
    # ── 市值加权臂：与等权**共用同一批 ret**，只换聚合（G2：两者都报）──
    cap_table = CapTable(daily_map)
    cap_ctx = CapContext(cap_table)
    cap_sink: dict = {}
    cap_equal = _market_by_day(daily_map, dates, entry_lag=ENTRY_LAG,
                               factors_map=factors_map, cap_ctx=cap_ctx,
                               cap_sink=cap_sink)

    def _summ(name: str, series: dict, caliber: str) -> dict:
        # ★ 判决窗口的逐日基准收益，一个交易日都不许缺（G1）
        vals = [series[d] for d in dates]
        missing = [d for d in dates if d not in series]
        mult = 1.0
        for v in vals:
            mult *= (1.0 + v)
        n = len(vals)
        sd = float(np.std(vals, ddof=1)) if n > 1 else float("nan")
        return {
            "caliber": caliber,
            "n_days": n,
            "n_days_missing": len(missing),
            "missing_dates": missing[:10],
            "arithmetic_daily_mean_pct": float(np.mean(vals)) * 100 if n else None,
            "compounded_final_multiple": float(mult),
            "geometric_annual_pct": (_annualized_compound(mult, n) * 100
                                     if _annualized_compound(mult, n) is not None else None),
            "daily_sd_pct": sd * 100 if n > 1 else None,
            "annualized_sd_pct_by_sqrt_time": (sd * np.sqrt(244) * 100
                                               if n > 1 else None),
        }

    equal_s = _summ("equal", equal, "equal_weight_benchmark")
    cap_s = _summ("cap", cap_sink, "cap_weight_benchmark")
    cross = _summ("cross_check", cap_equal, "equal_weight_benchmark")

    # ★ 交叉验：等权臂两次独立调用必须**逐位相同**。
    #   这一条是 G5 的最低要求 —— 若两次不同，说明抽样顺序或全局状态
    #   在两次调用之间被改动，而那个差永远查不出来（第 22 层）。
    rel = (abs(cross["compounded_final_multiple"]
               - equal_s["compounded_final_multiple"])
           / max(abs(equal_s["compounded_final_multiple"]), 1e-12))

    full_pool_mean = None
    if not args.skip_full_pool:
        fp = _sample_equity_weighted_daily(daily_map, dates, ENTRY_LAG,
                                           factors_map, {})
        full_pool_mean = fp * 100

    out = {
        "prereg": "docs/TODO_nav_vs_excess_caliber_2026-10-15.md",
        "window_arg": [args.start, args.end],
        "window_actual": [dates[0], dates[-1]],
        "n_days": len(dates),
        "sample": {"size": MARKET_SAMPLE, "pool": len(pool),
                   "seed": 42, "sampler": "random.Random(42).sample"},
        "entry_lag": ENTRY_LAG,
        "factors_adjusted": True,
        "equal_weight": equal_s,
        "cap_weight": cap_s,
        "gates": {
            "no_missing_trading_day": len(equal_s["missing_dates"]) == 0,
            "two_independent_calls_agree_bitwise": rel == 0.0,
            "cap_fallback_days_counted": len(cap_sink),
        },
        "cross_check_rel_diff": float(rel),
        "full_pool_daily_mean_pct": full_pool_mean,
        "what_this_cannot_prove": [
            "本产物只给**基准自身**的净值，不含任何组合 ⇒ 答不了「策略能不能达标」",
            "等权基准不是任何被动资金能拿到的（见 cap_weighted_benchmark 预注册）；"
            "要答「相对被动投资」，用 cap_weight 那一支",
            "geometry 与 arithmetic 是两个量，本产物报几何；"
            "与 annual_mean_pct 那类字段不可混引（第 4 次口径事故）",
        ],
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))

    from aplus import provenance
    provenance.stamp(args.out, __file__, extra={"window": out["window_arg"]})

    print(f"\n窗口 {out['window_actual'][0]} ~ {out['window_actual'][1]}"
          f"（{out['n_days']} 个交易日）")
    print(f"抽样 {MARKET_SAMPLE}/{len(pool)}，种子 42，entry_lag={ENTRY_LAG}")
    print("─" * 96)
    print(f"{'臂':<24}{'复利终值':>12}{'几何年化':>12}{'算术日均':>12}{'年化sd':>12}")
    for key, label in (("equal_weight", "等权基准"),
                       ("cap_weight", "市值加权基准")):
        s = out[key]
        print(f"{label:<20}{s['compounded_final_multiple']:>12.4f}"
              f"{(s['geometric_annual_pct'] or float('nan')):>11.2f}%"
              f"{(s['arithmetic_daily_mean_pct'] or float('nan')):>11.4f}%"
              f"{(s['annualized_sd_pct_by_sqrt_time'] or float('nan')):>11.2f}%")
    print("─" * 96)
    print(f"缺交易日 {equal_s['n_days_missing']}｜两次独立调用相对差 {rel:.3e}"
          f"｜市值回退登记 {len(cap_sink)} 天")
    if full_pool_mean is not None:
        print(f"全池等权日均 {full_pool_mean:.4f}%/日"
              f" vs 抽样 {equal_s['arithmetic_daily_mean_pct']:.4f}%/日"
              f" ⇒ 抽样偏差 {(full_pool_mean - equal_s['arithmetic_daily_mean_pct']):+.4f}pp/日")
    else:
        print("⚠️ 全池对照臂已跳过 ⇒ G5 的抽样偏差项**未测**，不得当作 0")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
