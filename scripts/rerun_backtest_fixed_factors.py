"""用修复后的复权因子重跑 2 年跨周期回测（2026-10-11 因子事故追溯）。

背景：`attach_factor` 的填充语义 bug 让 **65.8% 的股票**在日线区间内的因子被误填
成 1.0（merge 只认落在区间内的事件 + fillna(1.0)），而动量/反转/波动全部建立在
`close × adjust_factor` 之上——既有「追高动量 2 年 -78.3%」「跌名单 61.3%」
「quality-tilt 53.6%」等数字都建立在失真的横截面排序上，必须重跑才能采信。

不受影响的是 **T+1 制度性结论**：收益分解（隔夜 -0.55%/天 + 盘中 +0.38%/天）与
持有期曲线（h=1 唯一为正、h≥2 全负）用的是未复权原始价，与因子无关。

本脚本只做**同口径重跑 + 与旧产物对比**，不改任何策略参数——对比才有意义。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.backtest import walk_forward
from aplus.store import LocalStore

START = "2024-01-02"
END = "2026-03-30"
OLD = Path("data/reports/backtest_reversal_2y.parquet")
NEW = Path("data/reports/backtest_reversal_2y_fixed.parquet")


def summarize(df: pd.DataFrame, tag: str) -> dict:
    up = df[df["direction"] == "up"]
    dn = df[df["direction"] == "down"]
    hit_all = float((df["result"] == 1).mean())
    hit_up = float((up["result"] == 1).mean()) if len(up) else 0.0
    hit_dn = float((dn["result"] == 1).mean()) if len(dn) else 0.0
    exc = float(up["actual_pct"].mean() - df["market_pct"].mean()) if len(up) else 0.0
    return {"tag": tag, "n": int(len(df)), "dates": int(df["pred_date"].nunique()),
            "hit_up": round(hit_up, 4), "hit_down": round(hit_dn, 4),
            "hit_overall": round(hit_all, 4), "up_excess": round(exc, 6)}


def main():
    store = LocalStore(Path("data"))
    print(f"重跑 {START} → {END}（修复后因子）…", flush=True)
    t0 = time.time()
    rows = walk_forward(store, START, END, top_n=10, bottom_n=10)
    print(f"walk_forward 完成 {len(rows)} 条，{time.time() - t0:.0f}s", flush=True)
    rows.to_parquet(NEW, index=False)

    new_s = summarize(rows, "修复后因子")
    out = {"window": [START, END], "new": new_s, "elapsed_s": round(time.time() - t0, 1)}
    if OLD.exists():
        old_s = summarize(pd.read_parquet(OLD), "修复前因子（旧产物）")
        out["old"] = old_s
        print(json.dumps(out, ensure_ascii=False, indent=2))
        print("\n=== 逐项对比（修复前 → 修复后） ===")
        for k, label in [("hit_up", "涨命中率"), ("hit_down", "跌命中率"),
                         ("hit_overall", "总命中率"), ("up_excess", "涨超额/天")]:
            a, b = old_s[k], new_s[k]
            flag = "" if abs(a - b) < 1e-9 else "  ← 变了"
            print(f"  {label:<10} {a:>10.4f} → {b:>10.4f}{flag}")
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))

    Path("data/reports/backtest_factor_fix_comparison.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出 {NEW}")


if __name__ == "__main__":
    main()
