#!/usr/bin/env python
"""退市亏损实测（Method C 强化）：池中退市股最后 N 根 K 线的真实跌幅。

回答「退市亏损有没有被数据捕获」——不是估算，是从 parquet 里量出来的。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
AUD = ROOT / "scripts" / "audit_survivorship"
DAILY = ROOT / "data" / "master" / "daily"

reg = pd.read_csv(AUD / "registry.csv")
dele = reg[reg["delist_date"].notna()].copy()
in_pool = {p.stem for p in DAILY.glob("*.parquet")}
dele = dele[dele["code"].isin(in_pool)]
print(f"池中退市股: {len(dele)}")

rows = []
for _, r in dele.iterrows():
    p = DAILY / f"{r['code']}.parquet"
    # 部分文件缺 baostock 专有列（降级写入指纹），只取必然存在的基线列
    df = pd.read_parquet(p, columns=["date", "close"])
    df = df.sort_values("date").reset_index(drop=True)
    if len(df) < 2:
        continue
    c = df["close"].to_numpy(dtype=float)
    last = c[-1]
    # 各回溯窗口的真实收益
    row = {
        "code": r["code"], "name": r["code_name"], "delist_date": r["delist_date"],
        "last_date": str(df["date"].iloc[-1]),
        "delist_year": int(str(r["delist_date"])[:4]),
        "close_last": last,
        "r_1bar": c[-1] / c[-2] - 1 if len(c) >= 2 else np.nan,
        "r_5bar": c[-1] / c[-6] - 1 if len(c) >= 6 else np.nan,
        "r_10bar": c[-1] / c[-11] - 1 if len(c) >= 11 else np.nan,
        "r_20bar": c[-1] / c[-21] - 1 if len(c) >= 21 else np.nan,
        "r_60bar": c[-1] / c[-61] - 1 if len(c) >= 61 else np.nan,
        "r_to_delist": c[-1] / c[0] - 1,
    }
    rows.append(row)

t = pd.DataFrame(rows)
print(f"有效样本: {len(t)}\n")

def stat(col, label):
    s = t[col].dropna()
    if s.empty:
        return
    print(f"  {label:12s} n={len(s):4d}  中位数={s.median():+8.2%}  均值={s.mean():+8.2%}  "
          f"p10={s.quantile(.10):+8.2%}  p25={s.quantile(.25):+8.2%}  p75={s.quantile(.75):+8.2%}")

print("退市股退市前真实累计收益（从 parquet 量出）:")
for col, lab in [("r_1bar", "最后1根"), ("r_5bar", "最后5根"), ("r_10bar", "最后10根"),
                 ("r_20bar", "最后20根"), ("r_60bar", "最后60根"), ("r_to_delist", "全持有期")]:
    stat(col, lab)

# 退市日(按年份)最差的一批
print("\n年度最差退市股（最后20根累计收益 bottom5）:")
for y in sorted(t["delist_year"].unique()):
    if y < 2019:
        continue
    sub = t[t["delist_year"] == y]
    b = sub.nsmallest(5, "r_20bar")
    print(f"  {y}: " + ", ".join(f"{x['name']}({x['r_20bar']:+.0%})" for _, x in b.iterrows()))

print("\n全池等权：把退市股剔除 vs 保留，对「每只票等权平均日收益」的影响")
# 取最近一年窗口做等权指数对比
maxdate = "2026-10-13"
start = "2025-10-13"
dele_codes = set(t["code"])
acc_keep, acc_drop, n_keep, n_drop = [], [], 0, 0
skip = 0
for p in sorted(DAILY.glob("*.parquet")):
    code = p.stem
    try:
        df = pd.read_parquet(p, columns=["date", "close"])
    except Exception:
        continue
    d = pd.to_datetime(df["date"], errors="coerce")
    s = df.loc[d >= pd.Timestamp(start), "close"].astype(float)
    if len(s) < 100:
        skip += 1
        continue
    total = s.iloc[-1] / s.iloc[0] - 1
    if code in dele_codes:
        n_drop += 1
        acc_drop.append(total)
    else:
        n_keep += 1
        acc_keep.append(total)
print(f"  窗口 {start} -> {maxdate}; 退市股(窗口内停更)={n_drop}, 其他={n_keep}, 数据不足跳过={skip}")
if acc_drop:
    print(f"  含退市股等权一年收益: 中位数={np.median(acc_drop):+.2%} 均值={np.mean(acc_drop):+.2%}")
if acc_keep:
    print(f"  仅存续股等权一年收益: 中位数={np.median(acc_keep):+.2%} 均值={np.mean(acc_keep):+.2%}")
    if acc_drop:
        print(f"  等权收益差(仅存续 - 含退市): {np.mean(acc_keep)-np.mean(acc_drop):+.2%}")

t.to_csv(AUD / "delisting_loss.csv", index=False)
print(f"\n明细已存 {AUD/'delisting_loss.csv'}")