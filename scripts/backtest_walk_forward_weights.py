"""**真回测**：`R⁻¹Σ_f` 逐年权重，跑一遍真实的钱能赚多少。

## 为什么这个脚本存在

判决侧（`verdict_composite.py`）已经过了：组合 IC 0.0709，**0/6 不及格**。
它打印的「年毛超额」走的是 `ic_to_return.py` 那座**桥**，
而那座桥的常数错了 3 倍、形状也不成立（见
`docs/TODO_measure_sigma_r.md` 的撤回段）。

于是摆着两个互相矛盾、都为真的结论：

| 来源 | 说的是 |
|---|---|
| 信号侧判决 | 排序**强到够线** |
| `ic_guided_race_7y_corrected.json` | 所有 8 因子臂的**日净超额全为负** |

> **信号侧判决永远只能回答「排序有没有用」，不能回答「能不能赚钱」。**
> 后者只有真回测能答。而 `R⁻¹Σ_f` 权重**从未进过 `evaluate_weights`** ——
> 这个脚本补上那一格。

## 它**不是**又一座桥

刻意保留的四件真回测要素，一件都不省：

1. **可成交性闸门**（`execution_gate=True`，默认）—— 按 `panel.can_buy/can_sell`
   判成交，被拦下的**按原因计数**（`attrs["unfillable_execution*"]`）。
   计数缺失 = 这些观测**从统计里消失且不留痕迹**。
2. **成本** —— 按**换手**算，且**只在调仓日**收一次（非调仓日不换手 → 0）。
3. **Top-N 选股** —— 真买 10 只，不是买一整个分位。
4. **逐日聚合 → 分块 t** —— 观测单位是「一次调仓决策」，不是「一天」。

## 两档成本都要报（这是本脚本最容易被跳过的一步）

`evaluate_weights` 默认用 `STOCK_COST_DEFAULT = 0.003`，
而它**只是四项成本里的价差那项**（0.0015×2），佣金、印花税、过户费全缺席。
真实现况（5 万本金 / 单笔 5000）是 **0.0055**，**低估 1.84 倍**。

只报 0.003 那档，等于让读者以为「净值已经扣干净了」——
而门槛 `hurdle_rate` 用的恰恰是 0.0055。**两档混用 = 口径不一致**
（静默失效形状 ②）。

因为 `cost_drag = turnover × cost` 对 cost **线性**，
把输出里 `cost` 列按 `0.0055/0.003` 缩放**精确**等价于换成本率重跑，
不必（也不该）改引擎。`tests/test_backtest_walk_forward_weights.py`
用线性性做了变异验证。

用法：
    .venv/bin/python scripts/backtest_walk_forward_weights.py \
        --matrices data/reports/ic_matrices.parquet \
        --out data/reports/wf_weights_backtest.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.backtest import (ENTRY_LAG, STOCK_COST_DEFAULT, build_cross_sections,
                            cost_drag, evaluate_weights)
from aplus.composite import unpack, walk_forward
from aplus.factor import FACTOR_NAMES
from aplus.hurdle import COST_REAL_50K, DAYS_PER_YEAR, hurdle_rate, rounds_per_year
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore

#: 与判决的 K=21 同一口径：月频调仓。
K = 21

_VERDICT = None


def _verdict():
    """延迟载入 —— 模块级常量必须在函数定义**之前**算出来，反之不行。"""
    global _VERDICT
    if _VERDICT is None:
        path = Path(__file__).resolve().parent / "verdict_composite.py"
        spec = importlib.util.spec_from_file_location("_verdict_composite", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _VERDICT = mod
    return _VERDICT


def weights_by_year(df: pd.DataFrame, factors: list[str]) -> dict[int, dict]:
    """**判决用的那一份逐年权重** —— 单一真相源就在这里。

    刻意调用 `walk_forward` 而不是自己重算。自己重算等于
    「在测量台旁边另造一台测量仪」，两台之间的差永远查不出来。
    """
    rows = walk_forward(df, factors,
                        need_ic=_verdict().monthly_ic_threshold()[1],
                        judged_from=2021, equal_weight_years=(2019, 2020))
    return {r["year"]: r for r in rows if r.get("weights") is not None}


def at_cost(results: pd.DataFrame, cost: float) -> pd.DataFrame:
    """把输出换算到 `cost` 口径。

    `evaluate_weights` 已经把 `cost_drag` 落在 `cost` 列上了，
    而 `cost_drag = turnover × cost` 对 cost **线性**，
    所以按 `cost / STOCK_COST_DEFAULT` 缩放**精确**等价于换成本率重跑。

    > 线性性是这里唯一的假设，`tests/` 里对它做了变异验证：
    > 若哪天 `cost_drag` 里出现了 `min(1, …)` 之类的非线性项，
    > 这条缩放就不再精确，而**它不会报错**——只会安静地算错。
    """
    out = results.copy()
    if cost != STOCK_COST_DEFAULT and "cost" in out.columns:
        out["cost"] = out["cost"] * (cost / STOCK_COST_DEFAULT)
    return out


def _daily(results: pd.DataFrame, direction: str = "up"):
    """逐日单腿净超额序列 —— **复用 `evolve_race` 的那一份**，不自己写。

    它已经处理了三件容易错的事：逐日先聚合再检验、
    成分不足 `MIN_BASKET` 的日子丢掉、只有实际建仓的涨腿付成本。
    自己重写一遍就是第 22 层静默失效。
    """
    from aplus.evolve_race import _daily_series
    return _daily_series(results, direction)


def _t(daily, block: int):
    """分块 t —— 观测单位是**一次调仓决策**，不是一天。

    K 日调仓时，一天里「选了谁」在 K 天内不变。
    把 K×n 个交易日当成 K×n 个独立观测，等于把同一个决策数了 K 遍。
    """
    from aplus.evolve_race import _tstat
    return _tstat(daily, block=block)


def _rescale_check() -> dict:
    """实测一遍成本线性性 —— 让这个假设在**报告里**有数，不只在注释里。"""
    prev, cur = ["a", "b", "c"], ["b", "c", "d"]
    a = cost_drag(prev, cur, 3, cost=STOCK_COST_DEFAULT)
    b = cost_drag(prev, cur, 3, cost=COST_REAL_50K)
    ratio = b / a if a else float("nan")
    return {"cost_drag_at_default": a, "cost_drag_at_real": b, "ratio": ratio,
            "expected_ratio": COST_REAL_50K / STOCK_COST_DEFAULT,
            "linear": bool(abs(ratio - COST_REAL_50K / STOCK_COST_DEFAULT) < 1e-12)}


def run_year(xs, weights, dates, top_n: int, bottom_n: int,
             rebalance_every: int) -> dict:
    """跑一个年份，返回三个口径的逐年数字。"""
    res = evaluate_weights(xs, weights=weights, top_n=top_n, bottom_n=bottom_n,
                           dates=dates, rebalance_every=rebalance_every,
                           execution_gate=True)
    if res is None or res.empty:
        return {"n_rows": 0, "n_days": 0}

    gross = _daily(res.assign(cost=0.0))
    out = {"n_rows": int(len(res)), "n_days": int(len(dates))}
    for tag, c in (("bt003", STOCK_COST_DEFAULT), ("real", COST_REAL_50K)):
        daily = _daily(at_cost(res, c))
        t, n_obs = _t(daily, block=rebalance_every)
        mean = float(daily.mean()) if daily is not None and len(daily) else float("nan")
        sd = float(daily.std(ddof=1)) if daily is not None and len(daily) > 1 else float("nan")
        out[tag] = {
            "cost_per_round": c,
            "daily_excess": mean, "daily_sd": sd,
            "annual_gross_pct": mean * DAYS_PER_YEAR,
            "t_blocked": t, "n_independent_obs": n_obs,
            "annual_cost_pct": (rounds_per_year(rebalance_every) * c
                                if "cost" in res.columns else 0.0),
        }
        out[tag]["annual_net_pct"] = out[tag]["annual_gross_pct"] - out[tag]["annual_cost_pct"]
        out[tag]["hit_threshold"] = bool(out[tag]["annual_net_pct"] >= hurdle_rate(
            rounds_per_year=rounds_per_year(rebalance_every)))

    gm = float(gross.mean()) if gross is not None and len(gross) else float("nan")
    out["gross"] = {"daily_excess": gm, "annual_pct": gm * DAYS_PER_YEAR}
    out["unfillable_execution"] = {
        k[len("unfillable_execution"):]: v
        for k, v in getattr(res, "attrs", {}).items()
        if k.startswith("unfillable_execution")}
    out["unadjusted_touch"] = dict(getattr(res, "attrs", {}).get("unadjusted", {}) or {})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", type=Path,
                    default=Path("data/reports/ic_matrices.parquet"))
    ap.add_argument("--factors", default="")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_1012"))
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/wf_weights_backtest.json"))
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--bottom-n", type=int, default=10)
    ap.add_argument("--rebalance-every", type=int, default=K)
    args = ap.parse_args()

    if not args.matrices.is_file():
        raise SystemExit(f"抽取表不存在：{args.matrices}；先跑 scripts/extract_ic_matrices.py")

    lin = _rescale_check()
    if not lin["linear"]:
        print(f"🚨 成本非线性，`at_cost` 的缩放不再精确：{lin}", file=sys.stderr)
        return 2

    factors = [f.strip() for f in args.factors.split(",") if f.strip()] \
        or list(FACTOR_NAMES)
    df = pd.read_parquet(args.matrices)
    unpack(df, factors)
    wmap = weights_by_year(df, factors)

    store = LocalStore(args.data_root)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空")
        return 1
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    print(f"\n权重来自判决同一份抽取表，判决年 "
          f"{sorted(y for y in wmap if y >= 2021)}，{len(factors)} 个因子")
    print(f"成本口径：回测默认 {STOCK_COST_DEFAULT:.2%} / "
          f"真实现况 {COST_REAL_50K:.2%}（缩放比 {lin['ratio']:.4f}，已验证线性）")

    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"\n日线 {len(daily_map)} 只（{time.time() - t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    by_year: dict[int, list] = {}
    for d in dates:
        by_year.setdefault(int(str(d)[:4]), []).append(d)
    print(f"横截面 {len(dates)} 个交易日（{time.time() - t0:.0f}s）\n", flush=True)

    judged = sorted(y for y in by_year if y >= 2021 and y in wmap)
    if not judged:
        raise SystemExit("没有任何判决年份")

    rounds = rounds_per_year(args.rebalance_every)
    need = hurdle_rate(rounds_per_year=rounds)
    print(f"{'年':<6}{'日数':>6}{'组合IC':>10}{'毛超额/年':>12}"
          f"{'净@0.3%/年':>13}{'净@0.55%/年':>13}{'t(分块)':>9}{'独立观测':>9}{'够线':>7}")
    print("─" * 95)

    rows = []
    for y in judged:
        r = run_year(xs, wmap[y]["weights"], by_year[y],
                     args.top_n, args.bottom_n, args.rebalance_every)
        if r.get("n_rows", 0) == 0:
            print(f"{y:<6}空")
            continue
        ic = wmap[y].get("ic_mean")
        t_real = r["real"]["t_blocked"]
        n_obs = r["real"]["n_independent_obs"]
        print(f"{y:<6}{r['n_days']:>6}{(ic if ic is not None else float('nan')):>10.5f}"
              f"{r['gross']['annual_pct']:>12.2%}"
              f"{r['bt003']['annual_net_pct']:>13.2%}"
              f"{r['real']['annual_net_pct']:>13.2%}"
              f"{(t_real if t_real is not None else float('nan')):>9.2f}"
              f"{n_obs:>9}"
              f"{'✓' if r['real']['hit_threshold'] else '✗':>7}")
        r["year"] = y
        r["composite_ic"] = ic
        r["weights"] = wmap[y]["weights"]
        r["weights_source"] = wmap[y].get("weights_source")
        rows.append(r)
        print(f"      不可成交计数：{r['unfillable_execution'] or '（无）'}"
              f"   未复权触碰：{r['unadjusted_touch'] or '（无）'}", flush=True)

    if rows:
        print("─" * 95)
        for key, label in (("gross", "毛超额"), ("bt003", "净 @0.003"),
                           ("real", "净 @0.0055")):
            vals = [r[key]["annual_pct" if key == "gross" else "annual_net_pct"] for r in rows]
            ts = [r[key]["t_blocked"] for r in rows if r[key].get("t_blocked") is not None]
            hit = sum(1 for r in rows if key != "gross" and r[key]["hit_threshold"])
            tail = f"   t 均 {np.mean(ts):.2f}" if ts else ""
            print(f"{label:<14} 年均 {np.mean(vals):>8.2%}  最低 {np.min(vals):>8.2%}"
                  f"  最高 {np.max(vals):>8.2%}  够线 {hit}/{len(rows)}{tail}")
        print(f"\n门槛：年毛超额 {need:.2%}（= 目标 20% + 成本 {COST_REAL_50K:.2%}"
              f" × {rounds:.1f} 次/年）")

    res = {
        "kind": "wf_weights_backtest", "top_n": args.top_n,
        "bottom_n": args.bottom_n, "rebalance_every": args.rebalance_every,
        "rounds_per_year": rounds, "hurdle_gross": need,
        "cost_bt_default": STOCK_COST_DEFAULT, "cost_real": COST_REAL_50K,
        "cost_rescale_linear_check": lin,
        "factors": factors,
        "note": ("净超额 = 涨篮子 − 市场，日频序列按调仓周期分块后求 t；"
                 "观测单位是「一次调仓决策」。可成交性闸门已开。"
                 "两个成本口径都报：回测默认低估真实现况 1.84 倍。"),
        "years": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {args.out}\n总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
