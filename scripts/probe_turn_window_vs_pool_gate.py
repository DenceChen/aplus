"""各长窗口 L 下 `turnratio` 的股票池大小 —— 能不能过 2% 可比闸的**筛查**。

## 为什么需要这个筛查

7 年赛马实测：**所有不含 `turnratio` 的候选，显著性全灭**（82/82 组，
连 IC 最强的 `ic_top3` 也只有 t=1.89）。也就是说 `turnratio` 同时扛着
**收益**和**股票池闸门**——它是唯一值得动的杠杆。

而闸门的成因已经查清（`probe_turn_coverage.py`）：`mean(turn, 252)`
**定义上**就排除上市不足一年的股票（1.92%）+ 真缺数据的（3.01%），
合计缺口 6.54%，门槛是 2.0%。

方向因此明确：**保留 turnratio，缩短长窗口**，让次新股进池。

## 本脚本的作用：在花 70 分钟跑赛马**之前**先筛查

赛马一次要 70 分钟。先用几分钟算出「各 L 下股票池能到多大」，
**只有池子能过闸的 L 才值得跑完整赛马**。省下的时间是纯赚的。

## 但筛选不能替代赛马

池子够大 ≠ 因子还有预测力。缩短长窗口会**改变因子值**，
缩短到某个程度「近期 vs 典型」的对比就不再是异常换手率了。
所以本脚本只回答「闸门能不能过」，不回答「还能不能赚」。

用法：
    .venv/bin/python scripts/probe_turn_window_vs_pool_gate.py --data-root data
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.panel import MIN_TURN_COVERAGE, TURN_RATIO_SHORT

LONG_WINDOWS = (42, 63, 90, 120, 180, 252)
# 现任权重（不含 turnratio）的实测 n_ranked，取自 ic_guided_race_7y.json
CHAMP_N_RANKED = 4893
POOL_GATE = 0.02


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("data"))
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--out", default="/tmp/turn_window_pool.csv")
    args = ap.parse_args()

    files = sorted((args.data_root / "master" / "daily").glob("*.parquet"))
    if not files:
        print(f"主库为空：{args.data_root / 'master' / 'daily'}")
        return 1
    if args.sample:
        files = files[::max(1, len(files) // args.sample)]

    rows = []
    for p in files:
        try:
            m = pd.read_parquet(p)
        except Exception:
            continue
        if "turn" not in m.columns:
            continue
        t = m["turn"].astype(float).dropna()
        rec = {"code": p.stem, "n_bar": len(m)}
        for L in LONG_WINDOWS:
            seg = t.iloc[-L:]
            rec[f"cov{L}"] = len(seg) / L if len(seg) else 0.0
        rows.append(rec)

    df = pd.DataFrame(rows)
    if df.empty:
        print("没有可用样本")
        return 1
    df.to_csv(args.out, index=False)

    short_ok_needed = math.ceil(TURN_RATIO_SHORT * MIN_TURN_COVERAGE)
    n_all = len(df)
    print(f"全池 {n_all} 只（{'抽样' if args.sample else '全池'}）"
          f"  短窗口固定 {TURN_RATIO_SHORT} 日（≥{short_ok_needed} 根有效）\n")
    print(f"{'L':>5}{'上市≥L 日':>11}{'长窗合格':>10}{'池大小 n':>11}"
          f"{'vs 现任':>9}{'闸门 2%':>9}   结论")
    print("─" * 72)
    for L in LONG_WINDOWS:
        old = df["n_bar"] >= L
        ok = old & (df[f"cov{L}"] >= MIN_TURN_COVERAGE)
        n = int(ok.sum())
        gap = abs(CHAMP_N_RANKED - n) / CHAMP_N_RANKED
        verdict = "✅ 可跑赛马" if gap <= POOL_GATE else f"✗ 仍差 {gap - POOL_GATE:.1%}"
        print(f"{L:>5}{int(old.sum()):>11}{int((old & (df[f'cov{L}'] >= MIN_TURN_COVERAGE)).sum()):>10}"
              f"{n:>11}{gap:>9.1%}{POOL_GATE:>9.0%}   {verdict}")

    print(f"\n注：现任（不含 turnratio）的实测 n_ranked = {CHAMP_N_RANKED}，")
    print(f"    闸门要求两者相差 ≤ {POOL_GATE:.0%}。")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
