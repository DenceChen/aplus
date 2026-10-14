"""转债侧参数赛马（样本外协议）—— 20% 目标的真正所在。

## 为什么股票侧调权不是答案

股票侧日频多头被 **T+1 制度**结构性阻断（隔夜 -0.55%/天、盘中 +0.38%/天，
而 h=1 才是唯一有边际的窗口、当日却买不进）。权重调得再好也兑现不了。
**转债 T+0 没有这个约束**——这是第三阶段筛出低价轮动的全部理由。

## 本脚本做什么

在 `cheap_rotation_backtest`（与纸面账户共用漏斗与费用公式）上跑参数网格，
并按 `aplus/evolve_race` 的**样本外协议**裁决：训练段挑参数、测试段验收，
训练赢测试垮的一律否决。第三阶段的持有期赛马只看了**全窗口**，且用回测
锚点（最幸运相位）——这次换成训练/测试切分 + 隔离带，结论才敢拿去上线。

用法：
    .venv/bin/python scripts/bond_param_race.py
    .venv/bin/python scripts/bond_param_race.py --promote
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.bond_horizon import (
    COST_WAN15,
    TRADING_DAYS_PER_YEAR,
    build_horizon_cache,
    cheap_rotation_backtest,
)
from aplus.evolve_race import split_window

ROOT = Path(__file__).resolve().parent.parent


def load_bond_map(data_root):
    from aplus.bond_sync import load_bond_map as _l

    return _l(data_root)


def grid(hold_list, top_list, cost_list):
    return [{"hold_days": h, "top_n": t, "cost": c}
            for h in hold_list for t in top_list for c in cost_list]


def evaluate(cache, params, start, end, seed=None):
    """一组参数的回测。seed=None 走 cheap 臂，给定 seed 走同漏斗随机对照臂。"""
    return cheap_rotation_backtest(None, start, end, top_n=params["top_n"],
                                   hold_days=params["hold_days"], cost=params["cost"],
                                   random_seed=seed, cache=cache)


def arm(r, seed):
    return {"ann": r["annualized_return"], "total": r["total_return"],
            "mdd": r["max_drawdown"], "n_periods": r["n_periods"],
            "net_per_period": r["net_per_period"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--top-list", type=int, nargs="+", default=[10, 15, 20])
    ap.add_argument("--hold-list", type=int, nargs="+", default=[1, 3, 5, 10, 20])
    ap.add_argument("--cost-list", type=float, nargs="+", default=[COST_WAN15])
    ap.add_argument("--train-ratio", type=float, default=0.6)
    ap.add_argument("--embargo", type=int, default=5)
    ap.add_argument("--promote", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    bond_map = load_bond_map(args.data_root)
    if not bond_map:
        print("转债库为空，先跑 scripts/bond_bootstrap.py")
        return
    cache = build_horizon_cache(bond_map)
    cal = cache["calendar"]
    train_dates, test_dates = split_window(cal, args.train_ratio, args.embargo)
    if not train_dates or not test_dates:
        print(f"日历太短（{len(cal)} 天），切不出训练/测试两段")
        return

    print(f"转债 {len(bond_map)} 只｜日历 {cal[0]} ~ {cal[-1]}（{len(cal)} 日）")
    print(f"训练段 {train_dates[0]} ~ {train_dates[-1]}（{len(train_dates)} 日）")
    print(f"隔离带 {args.embargo} 日（丢弃）")
    print(f"测试段 {test_dates[0]} ~ {test_dates[-1]}（{len(test_dates)} 日）\n")

    # 回测内部以「信号日」为网格锚，段边界取首尾信号日，向右闭到下个信号日
    tr_start, tr_end = train_dates[0], train_dates[-1]
    te_start, te_end = test_dates[0], test_dates[-1]

    params = grid(args.hold_list, args.top_list, args.cost_list)
    print(f"{'h':>3}{'n':>4}{'cost':>8}{'训练年化':>10}{'测试年化':>10}"
          f"{'测试回撤':>10}{'测试超额/天':>12}  裁决")
    print("─" * 76)
    rows = []
    SEEDS = (1, 2, 3)
    for p in params:
        tr = arm(evaluate(cache, p, tr_start, tr_end), None)
        te = arm(evaluate(cache, p, te_start, te_end), None)
        # 对随机超额：同漏斗同费用的随机臂逐期净收益之差（对随机，不对市场）
        tr_rnd = [arm(evaluate(cache, p, tr_start, tr_end, sd), sd) for sd in SEEDS]
        te_rnd = [arm(evaluate(cache, p, te_start, te_end, sd), sd) for sd in SEEDS]
        tr["exc_per_period"] = tr["net_per_period"] - sum(
            r["net_per_period"] for r in tr_rnd) / len(tr_rnd)
        te["exc_per_period"] = te["net_per_period"] - sum(
            r["net_per_period"] for r in te_rnd) / len(te_rnd)
        ok = te["ann"] > 0 and te["exc_per_period"] > 0 and te["n_periods"] >= 3
        rows.append({"params": p, "train": tr, "test": te, "passes": ok})
        print(f"{p['hold_days']:>3}{p['top_n']:>4}{p['cost']:>8.4f}"
              f"{tr['ann']:>10.1%}{te['ann']:>10.1%}{te['mdd']:>10.1%}"
              f"{te['exc_per_period']:>12.2%}  {'✓' if ok else '✗'}")

    good = [r for r in rows if r["passes"]]
    good.sort(key=lambda r: r["test"]["ann"], reverse=True)
    print()
    if good:
        b = good[0]
        print(f"【样本外最强】h={b['params']['hold_days']} top{b['params']['top_n']} "
              f"cost={b['params']['cost']}：测试年化 {b['test']['ann']:.1%}、"
              f"回撤 {b['test']['mdd']:.1%}、对随机超额 {b['test']['exc_per_period']:.2%}/期"
              f"（训练年化 {b['train']['ann']:.1%}）")
    else:
        print("【样本外无一参数达标】——测试段年化/对随机超额/期数三条全过的参数集为空")

    out = {"window": {"train": [tr_start, tr_end], "test": [te_start, te_end]},
           "rows": rows, "best_oos": good[0] if good else None,
           "elapsed_s": round(time.time() - t0, 1)}
    path = Path(args.data_root) / "reports" / "bond_param_race.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(f"\n总耗时 {time.time()-t0:.0f}s → {path}")


if __name__ == "__main__":
    main()
