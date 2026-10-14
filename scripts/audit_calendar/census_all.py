"""审计 1b：全量 5460 只股票的逐日成交只数普查。

用途：区分「这天没有股票交易（可疑 = 假阳性交易日）」和「这天日历漏了」。
同时统计每只股票缺多少个交易日（停牌）。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path("/Users/airboom/opencode/aplus")
DAILY = ROOT / "data" / "master" / "daily"
OUT = ROOT / "scripts" / "audit_calendar"

per_date = Counter()
n_stocks = 0
fails = []

for p in sorted(DAILY.glob("*.parquet")):
    try:
        col = pq.read_table(p, columns=["date"]).column("date").to_pylist()
    except Exception as exc:  # noqa: BLE001
        fails.append((p.stem, f"{type(exc).__name__}: {exc}"))
        continue
    n_stocks += 1
    per_date.update({str(x)[:10] for x in col if x is not None})

json.dump({"n_stocks_read": n_stocks, "fails": fails,
           "per_date": dict(sorted(per_date.items()))},
          open(OUT / "census_all.json", "w"))

print(f"读到 {n_stocks} 只，失败 {len(fails)}")
print(f"不同日期总数 = {len(per_date)}")
print(f"范围 {min(per_date)} .. {max(per_date)}")

vals = sorted(per_date.values())
print(f"每日期股票数：min={vals[0]} p1={vals[len(vals)//100]} "
      f"median={vals[len(vals)//2]} max={vals[-1]}")

print("\n== 成交股票数最少的 40 个日期（假阳性嫌疑最大）==")
for d, n in sorted(per_date.items(), key=lambda kv: kv[1])[:40]:
    print(f"  {d}: {n} 只")

json.dump(sorted(per_date.items()), open(OUT / "per_date_counts.json", "w"))
