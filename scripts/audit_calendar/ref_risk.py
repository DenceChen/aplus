"""审计 1d：ref 选择脆弱性量化。

🚨🚨 **本脚本已作废（2026-10-15，第 126 层）** ⇒ 默认拒绝运行。

问题：panel.py 的 `ref = max(daily_map.values(), key=len)` 用「行数最多的那只」
当全市场日期轴。若哪只被选中的股票自身停牌，那一天就从**整个回测**里消失。

**生产早就不这么干了**：`panel.py::window_dates` 现在走 `market_date_axis`
（全体并集）⇒ 「哪只会被选成 ref」这个问题**今天没有答案**，本脚本量化的是
一个**已被放弃的形态**的风险。

而且它读的那份输入 `db_calendar.json` 的 `ref_dates` **已经无法由仓里的
`extract_calendar.py` 重现**（快照 ref = `000001.SZ` / 1884 行；今天重跑会选
`000043.SZ` / 6127 行 —— 数据修复之后）⇒ 本脚本今天跑出来的任何数字，
**都不是在量化生产代码的任何行为**。

⚠️ 底层数据**并没有因此作废**：`miss_by_stock.json` 是一份**停牌/缺口普查**
（哪只在自己的 [首根,末根] 之间缺了哪些交易日），这件事本身跟「谁当 ref」无关，
仍然成立。要用那份普查，直接读 JSON，不必重跑本脚本。

⇒ 不删脚本、不删 JSON（第 25 层），只把作废变成显式拒绝。
"""
from __future__ import annotations

import datetime
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 🚨 与 `extract_calendar.py` 同一道闸门，同样放在重活之前。
if "--ack-superseded" not in sys.argv:
    print(
        "🚨 本脚本量化的 `ref = max(key=len)` 形态**已作废**（第 126 层）：\n"
        "   生产 `panel.py::window_dates` 现在走全体并集（`market_date_axis`），\n"
        "   「哪只会被选成 ref」今天没有答案 ⇒ 本脚本测的不是生产的任何行为。\n"
        "   它的输入 `db_calendar.json` 也已无法由仓里的 `extract_calendar.py` 重现。\n"
        "   ⇒ 若你只是想要那份停牌/缺口普查，直接读 `miss_by_stock.json` 即可。\n"
        "   ⇒ 确知自己在做历史复现，才加 `--ack-superseded`。",
        file=sys.stderr,
    )
    raise SystemExit(2)

import pyarrow.parquet as pq  # noqa: E402  （刻意放在闸门之后）

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
