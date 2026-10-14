"""成交口径 A/B：旧前视（entry_lag=0）vs T+1 可成交（entry_lag=1）。

## 为什么要做这个对比

旧口径的收益窗口从**信号日 D 的收盘**开始算起，而信号是用「含 D 收盘」的
数据算出来的 —— 窗口起点落在信息集之内，是一根 K 线的前视。它衡量的不是
「策略赚不赚钱」，而是「一笔你无法执行的交易赚不赚钱」。

面板（因子计算 + 股票过滤）与成交口径**无关**，所以这里只建一次面板，
在它之上拼两个横截面字典（各自的市场基准 + entry_lag），A/B 成本近乎为零。

## 结论怎么读

- 若两个口径**结论一致**（都是 0 组样本外超额为正）→ 前视没有伪造 alpha，
  原来的「股票侧无因子 edge」判决成立
- 若新口径明显更差 → 说明旧结论被前视**美化过**，判决要加重
- 若新口径明显更好 → 极可疑，先查是不是哪里又漏了别的

**无论哪种，都要先看测试段，不能只看全窗口。**
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.backtest import _market_by_day, evaluate_weights
from aplus.evolve import load_weights, objective
from aplus.evolve_race import oos_decision, race
from aplus.panel import build_panel
from aplus.predict import filter_universe
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent
START = "2024-01-02"


def build_sections(panel, daily_map, entry_lag):
    """同一份面板 → 指定成交口径的横截面字典。"""
    out = {
        "_dates": panel.dates,
        "_daily_map": daily_map,
        "_entry_lag": entry_lag,
        "_market": _market_by_day(daily_map, panel.dates, entry_lag=entry_lag),
    }
    for i, day in enumerate(panel.dates):
        out[day] = filter_universe(panel.cross_section(i))
    return out


def evaluate_factory(xs):
    def ev(_xs, w, dates, tn, bn):
        res = evaluate_weights(xs, w, top_n=tn, bottom_n=bn, dates=dates)
        stats = rolling_stats(res)
        return {"stats": stats,
                "objective": round(objective({"weights": w, "stats": stats}), 4),
                "n": int(len(res))}
    return ev


def main():
    t0 = time.time()
    store = LocalStore(ROOT / "data")
    reports = ROOT / "data" / "reports"
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空")
        return
    end = str(pd.read_parquet(files[0])["date"].max())
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"载入 {len(daily_map)} 只（{time.time()-t0:.0f}s）", flush=True)

    t1 = time.time()
    panel = build_panel(store, START, end, None, daily_map, factors_map)
    print(f"面板 {len(panel.dates)} 个交易日 → {time.time()-t1:.0f}s", flush=True)

    champion = load_weights(reports)["weights"]
    sys.path.insert(0, str(ROOT / "scripts"))
    from evolve_race_fast import candidates
    cands = candidates()
    if champion not in cands:
        cands.insert(0, champion)

    results = {}
    for lag, label in ((0, "旧口径 entry_lag=0（前视）"), (1, "新口径 entry_lag=1（T+1 可成交）")):
        t2 = time.time()
        xs = build_sections(panel, daily_map, lag)
        r = race(xs, cands, evaluate_factory(xs), top_n=10, bottom_n=10)
        if r.get("error"):
            print(f"{label} 赛马失败：{r['error']}")
            continue
        dec = oos_decision(r, champion)
        passed = [row for row in r["table"]
                  if row["test"]["stats"].get("up_excess_vs_market", 0) > 0]
        results[lag] = {
            "train_dates": [r["train_dates"][0], r["train_dates"][-1]],
            "test_dates": [r["test_dates"][0], r["test_dates"][-1]],
            "n_passed_excess_gate": len(passed),
            "decision": dec,
            "table": [{k: v for k, v in row.items()} for row in r["table"]],
            "elapsed_s": round(time.time() - t2, 1),
        }
        print(f"\n{'='*92}\n{label}  （{time.time()-t2:.0f}s）")
        print(f"训练 {r['train_dates'][0]}~{r['train_dates'][-1]} | "
              f"测试 {r['test_dates'][0]}~{r['test_dates'][-1]}")
        print(f"{'排名':<4}{'训练超额':>10}{'测试超额':>10}{'训练命中':>9}{'测试命中':>9}  权重")
        for i, row in enumerate(r["table"], 1):
            tr, te = row["train"], row["test"]
            mark = " ★现任" if row["weights"] == champion else ""
            gate = "✓过闸" if te["stats"].get("up_excess_vs_market", 0) > 0 else ""
            print(f"{i:<4}{tr['stats'].get('up_excess_vs_market', 0):>10.2%}"
                  f"{te['stats'].get('up_excess_vs_market', 0):>10.2%}"
                  f"{tr['stats'].get('overall_hit_rate', 0):>9.1%}"
                  f"{te['stats'].get('overall_hit_rate', 0):>9.1%}  "
                  f"{row['weights']}{mark} {gate}")
        print(f"通过超额硬闸：{len(passed)}/{len(r['table'])}")
        print(f"裁决：{dec['reason']}")

    (reports / "execution_lag_ab.json").write_text(
        json.dumps({"window": [START, end], "champion": champion,
                    "results": {str(k): v for k, v in results.items()}},
                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n总耗时 {time.time()-t0:.0f}s → {reports / 'execution_lag_ab.json'}")


if __name__ == "__main__":
    main()