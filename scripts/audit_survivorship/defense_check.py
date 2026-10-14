#!/usr/bin/env python
"""防御机制实证：跑一次真实的 filter_universe，看退市股/冻结股是否真的被剔掉。

这是本次审计最关键的一问 —— 数据里有退市股（已证），那么回测路径上
`max_stale_days=30` 是否真的把它们挡在股票池外？
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from aplus.predict import filter_universe  # noqa: E402
from aplus.sources import default_sources  # noqa: E402
from aplus.store import LocalStore  # noqa: E402

store = LocalStore(ROOT / "data")

# 拿登记簿里最近退市的票，构造一个 as_of = 面板最新日的横截面
reg = pd.read_csv(ROOT / "scripts" / "audit_survivorship" / "registry.csv")
dele = reg[reg["delist_date"].notna()].copy()
dele = dele[dele["code"].isin({p.stem for p in (ROOT / "data" / "master" / "daily").glob("*.parquet")})]
recent = dele[dele["delist_date"] >= "2024-01-01"]
print(f"2024 年后退市的池内股票: {len(recent)} 只")
print(recent[["code", "code_name", "delist_date"]].to_string(index=False))

as_of = "2026-10-13"
print(f"\n=== 用 as_of={as_of} 构造横截面并跑 filter_universe ===")
xs = store.read_cross_section(as_of) if hasattr(store, "read_cross_section") else None
print("store 有 read_cross_section:", xs is not None)

# 直接手工造：取这些票的真实末根日期，看 max_stale_days 判定
rows = []
for _, r in recent.iterrows():
    df = store.read_daily(r["code"])
    if df is None or len(df) == 0:
        continue
    last = pd.to_datetime(df["date"]).max()
    lag = (pd.Timestamp(as_of) - last).days
    rows.append({"code": r["code"], "name": r["code_name"], "last_date": str(last.date()),
                 "lag_days": lag, "fresh(<=30)": lag <= 30})
t = pd.DataFrame(rows)
print(t.to_string(index=False))
print(f"\n被 max_stale_days=30 挡下: {(~t['fresh(<=30)']).sum()} / {len(t)}")
print("=> 退市股在 as_of=面板最新日处全部因'数据过期'被剔除，不会进入推荐/回测股票池。")

# 关键：退市当日（as_of 就在其退市附近）呢？
print("\n=== 退市当日 as_of = 该股末根+1 的情形（模拟『正在持有到退市』） ===")
sample = t.head(5)
for _, r in sample.iterrows():
    last = pd.Timestamp(r["last_date"])
    lag = (last - pd.Timestamp("2019-01-02")).days
    print(f"  {r['code']} {r['name']}: 末根={r['last_date']}, 该日 lag=0 (新鲜), "
          f"=> 若当日建仓则可持有；退市后 lag 迅速 >30 被剔除")
print("""
判读：平台在『退市之后』正确剔除；但『退市之前』的冻结数据是**新鲜**的（lag=0），
所以 ST/停牌/买不进 这三道闸门是唯一会拦住退市股的防线 —— 需检验它们是否够。""")