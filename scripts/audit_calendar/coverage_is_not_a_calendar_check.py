"""审计 1e：证明 coverage.py 的 max_calendar_gap_days 不是日历校验。

做法：拿一只真实满历日股票，把它的日期**挖掉若干天**（模拟日历有洞），
然后问 coverage.classify_frame —— 它报不报？
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/airboom/opencode/aplus")

import pandas as pd

from aplus.coverage import THRESHOLDS, classify_frame

REAL = "data/master/daily/000001.SZ.parquet"
df = pd.read_parquet(REAL)
print(f"基线 {REAL}: rows={len(df)}  {df['date'].iloc[0]} .. {df['date'].iloc[-1]}")
base = classify_frame("000001.SZ", df, "2019-01-02", "2026-10-13")
print(f"  原判: healthy={base['is_healthy']}  reasons={base['reasons']}  gaps={base['gaps']}")

# 逐日挖洞：每挖掉 1 天、连续挖 15 天（远小于 max_calendar_gap_days=20）
dates = df["date"].tolist()
for n_holes in (1, 5, 15):
    holed = pd.DataFrame(
        [r for i, (_, r) in enumerate(df.iterrows()) if i not in range(1000, 1000 + n_holes)]
    ).reset_index(drop=True)
    info = classify_frame("000001.SZ", holed, "2019-01-02", "2026-10-13")
    print(f"\n挖掉 {n_holes} 个交易日（自然日跨度 {n_holes}）:")
    print(f"  rows={info['rows']}  healthy={info['is_healthy']}  "
          f"reasons={info['reasons']}  gaps={info['gaps']}")

print("\n结论：只要缺口 < 20 个**自然日**，coverage.py 一律判 healthy。")
print(f"      max_calendar_gap_days = {THRESHOLDS.max_calendar_gap_days}")
print("      A 股一个长假约 14 个自然日 -> 阈值比长假还宽，永远不会因日历漏一天而报警。")
