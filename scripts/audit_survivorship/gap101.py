#!/usr/bin/env python
"""补充审计：101 只「登记簿有、池中无」的退市股是窗口外退市还是真缺失 + 回测窗口。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
AUD = ROOT / "scripts" / "audit_survivorship"

reg = pd.read_csv(AUD / "registry.csv")
dele = reg[reg["delist_date"].notna()].copy()
dele["delist_year"] = dele["delist_date"].str[:4].astype(int)
dele["delist_date"] = pd.to_datetime(dele["delist_date"])

in_pool = {p.stem for p in (ROOT / "data" / "master" / "daily").glob("*.parquet")}
missing = dele[~dele["code"].isin(in_pool)].copy()

print("=" * 72)
print("退市股：池中有 vs 池中无，按退市年份")
print("=" * 72)
tab = dele.assign(in_pool=dele["code"].isin(in_pool)).groupby("delist_year").in_pool.agg(["sum", "count"])
tab.columns = ["in_pool", "total"]
tab["missing"] = tab["total"] - tab["in_pool"]
print(tab.to_string())

# 面板起点
first_dates = []
for p in sorted((ROOT / "data" / "master" / "daily").glob("*.parquet"))[:1]:
    pass
print()
print("=" * 72)
print("回测窗口")
print("=" * 72)
bt = pd.read_parquet(ROOT / "data" / "reports" / "backtest.parquet")
print("backtest.parquet columns:", list(bt.columns)[:12])
for c in bt.columns:
    if "date" in c.lower():
        print(f"  {c}: {bt[c].min()} -> {bt[c].max()}  n={bt[c].nunique()}")
print()
print("backtest_summary.json:")
print((ROOT / "data" / "reports" / "backtest_summary.json").read_text()[:900])

print()
print("=" * 72)
print("缺失的退市股：退市日 vs 面板起点")
print("=" * 72)
missing = missing.sort_values("delist_date")
print(f"缺失总数: {len(missing)}")
print(missing[["code", "code_name", "delist_date"]].to_string(index=False))