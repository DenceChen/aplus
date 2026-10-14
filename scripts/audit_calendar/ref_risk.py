"""审计 1d：ref 选择脆弱性量化。

问题：panel.py:655 `ref = max(daily_map.values(), key=len)` 用「行数最多的那只」
当全市场日期轴。若哪只被选中的股票自身停牌，那一天就从**整个回测**里消失。

量化：统计全体 5460 只里，每只在 [首根, 末根] 区间内缺多少个**真交易日**。
缺 0 的股票 = 可安全当 ref；缺 >0 的 = 一旦被选中就污染日期轴。
"""
from __future__ import annotations

import datetime
import json
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

D = Path("/Users/airboom/opencode/aplus/data/master/daily")
OUT = Path("/Users/airboom/opencode/aplus/scripts/audit_calendar")

R = json.load(open(OUT / "db_calendar.json"))["ref_dates"]
IDX = {d: i for i, d in enumerate(R)}

miss_by_stock: dict[str, list[str]] = {}
for p in sorted(D.glob("*.parquet")):
    try:
        ds = sorted({str(x)[:10] for x in
                     pq.read_table(p, columns=["date"]).column("date").to_pylist() if x})
    except Exception:  # noqa: BLE001
        continue
    if not ds:
        continue
    s = set(ds)
    lo, hi = IDX[ds[0]], IDX[ds[-1]]
    miss = [R[i] for i in range(lo, hi + 1) if R[i] not in s]
    if miss:
        miss_by_stock[p.stem] = miss

n_clean = 5460 - len(miss_by_stock)
print(f"全库 5460 只中，区间内 0 缺失（可安全当 ref）= {n_clean} 只 ({100*n_clean/5460:.1f}%)")
print(f"                        区间内有缺失（被选中即污染）= {len(miss_by_stock)} 只 "
      f"({100*len(miss_by_stock)/5460:.1f}%)")

hist = Counter(len(v) for v in miss_by_stock.values())
print("\n缺失天数分布（缺失=停牌/未回填）:")
for k in sorted(hist)[:12]:
    print(f"  缺 {k:>4} 天: {hist[k]:>4} 只")
print(f"  ... 缺失最多的 10 只:")
for c, m in sorted(miss_by_stock.items(), key=lambda kv: -len(kv[1]))[:10]:
    w = "一二三四五六日"[datetime.date.fromisoformat(m[0]).weekday()]
    print(f"    {c}: 缺 {len(m):>4} 天, 首缺 {m[0]} 周{w}")

json.dump({k: v for k, v in sorted(miss_by_stock.items())},
          open(OUT / "miss_by_stock.json", "w"))
print("\n-> scripts/audit_calendar/miss_by_stock.json")
