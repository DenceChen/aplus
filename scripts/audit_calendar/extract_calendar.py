"""审计 1：DB 里真实的交易日历长什么样。

🚨🚨 **本脚本的 A 趟已作废（2026-10-15，第 126 层）** ⇒ 默认拒绝运行。

它复刻的是 `panel.py` 的 `ref = max(daily_map, key=len)` ——「用行数最多的那一只
的日期当全市场日期轴」。**生产代码早就不这么干了**：`panel.py::window_dates`
现在走 `market_date_axis`（全体并集），并对空窗口抛 `ValueError`。
⇒ 「回测面板真正用的那一只参考股」**这个对象今天不存在**，A 趟问的是一个已废的问题。

更糟的是**它连自己的快照都重现不出来**（实测 2026-10-15）：

  |              | 快照落盘时（2026-10-14 11:17） | 今天重跑会得到 |
  |--------------|------------------------------|----------------|
  | `ref`        | `000001.SZ`                  | `000043.SZ`    |
  | `ref_rows`   | 1884                         | 6127           |

数据修复把 `000043.SZ` 从 221 行补到 6127 行之后，`max(key=len)` 选中的就是它了
——**和 `panel.py` 文档里记录的那次引爆是同一只票**。顺带 `000001.SZ` 自己也
1884 → 1885（补了一个交易日）。⇒ **提交在仓里的脚本与提交在仓里的 JSON 已经不对应**，
而当时**没有任何守卫会发现这件事**。

⇒ B 趟（分层抽样 + 并集日历）**仍然有效**，而且比当初更重要：并集正是生产现在用的东西。
所以这里**不删脚本、不删 JSON**（第 25 层：撤回已提交结论需要独立判决，不是顺手做的），
只把作废的那一半**变成显式拒绝**：不带 `--ack-superseded` 一律 rc=2，
免得有人重跑它、把 `db_calendar.json` 覆盖成「今天那个 ref」，然后照着死前提读结论。

只读。不写任何生产文件。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

# 🚨 作废闸门放在最前面（连 pyarrow 都不 import）⇒ 守卫跑它只要毫秒级。
if "--ack-superseded" not in sys.argv:
    print(
        "🚨 本脚本的 `ref = max(key=len)` 那一趟**已作废**（第 126 层）：\n"
        "   生产 `panel.py::window_dates` 现在走全体并集（`market_date_axis`），\n"
        "   「面板的参考股」这个对象今天不存在。\n"
        "   而且仓里的 `db_calendar.json`（2026-10-14 11:17）**已经无法由本脚本重现**：\n"
        "   快照 ref = 000001.SZ / 1884 行；今天重跑会选 000043.SZ / 6127 行。\n"
        "   ⇒ 你若只想重放当时的历史快照，加 `--ack-superseded`，并记住产物会覆盖。\n"
        "   ⇒ 若你想要**生产真正在用的**日历，去看 `panel.py::market_date_axis`，不是这里。",
        file=sys.stderr,
    )
    raise SystemExit(2)

import pyarrow.parquet as pq  # noqa: E402  （刻意放在闸门之后）

ROOT = Path("/Users/airboom/opencode/aplus")
DAILY = ROOT / "data" / "master" / "daily"
OUT = ROOT / "scripts" / "audit_calendar"


def scan_all_rows() -> list[tuple[str, int]]:
    """只读 parquet metadata 拿行数，不读列。"""
    out = []
    for p in sorted(DAILY.glob("*.parquet")):
        try:
            n = pq.ParquetFile(p).metadata.num_rows
        except Exception as exc:  # noqa: BLE001
            print(f"UNREADABLE {p.stem}: {exc}", file=sys.stderr)
            n = -1
        out.append((p.stem, n))
    return out


def pick_stratified_sample(all_rows: list[tuple[str, int]]) -> list[str]:
    """50 只大盘 + 面板参考股。"""
    codes = [c for c, n in all_rows if n > 0]
    sh = sorted(c for c in codes if c.startswith("60") or c.startswith("68"))
    sz = sorted(c for c in codes if c.startswith("00") or c.startswith("30"))
    sample = sh[:25] + sz[:25]
    return sample


def main():
    print("== A) 扫描全部 parquet 行数 ==", flush=True)
    all_rows = scan_all_rows()
    json.dump(all_rows, open(OUT / "rowcounts.json", "w"))

    # 复刻 panel.py:655  ref = max(daily_map.values(), key=len)
    # daily_map 来自 sorted(glob) -> dict 顺序 = 文件名字典序 -> max() 取首个最大值
    best = max((n for _, n in all_rows), default=-1)
    ref = next(c for c, n in all_rows if n == best)
    print(f"panel.py 参考股 ref = {ref}  (rows={best})")

    sample = pick_stratified_sample(all_rows)
    sample = [ref] + [c for c in sample if c != ref]
    print(f"抽样 N = {len(sample)}（50 只大盘 + ref）")
    json.dump({"ref": ref, "ref_rows": best, "sample": sample},
              open(OUT / "sample_frame.json", "w"), indent=2)

    print("\n== B) 读 date 列 ==", flush=True)
    per_stock: dict[str, list[str]] = {}
    for c in sample:
        p = DAILY / f"{c}.parquet"
        try:
            col = pq.read_table(p, columns=["date"]).column("date").to_pylist()
        except Exception as exc:  # noqa: BLE001
            print(f"  {c}: FAIL {exc}")
            continue
        ds = sorted({str(x)[:10] for x in col if x is not None})
        per_stock[c] = ds
        print(f"  {c}: {len(ds)} 行 {ds[0] if ds else '-'} .. {ds[-1] if ds else '-'}")

    # ref 的日历 = 面板日期轴
    ref_dates = per_stock[ref]
    union = sorted({d for ds in per_stock.values() for d in ds})
    json.dump({"ref": ref, "ref_dates": ref_dates, "union": union,
               "per_stock_counts": {c: len(d) for c, d in per_stock.items()}},
              open(OUT / "db_calendar.json", "w"))

    print(f"\nref 日历      : {len(ref_dates)} 天  {ref_dates[0]} .. {ref_dates[-1]}")
    print(f"50 只并集日历 : {len(union)} 天  {union[0]} .. {union[-1]}")
    extra = sorted(set(union) - set(ref_dates))
    print(f"并集里有、ref 里没有的日期: {len(extra)}")
    for d in extra:
        holders = [c for c, ds in per_stock.items() if d in set(ds)]
        print(f"  {d}  仅 {len(holders)}/{len(per_stock)} 只: {holders[:6]}")

    by_year = defaultdict(int)
    for d in union:
        by_year[d[:4]] += 1
    print("\n== 并集日历 年度交易日数 ==")
    for y in sorted(by_year):
        print(f"  {y}: {by_year[y]}")

    by_year_ref = defaultdict(int)
    for d in ref_dates:
        by_year_ref[d[:4]] += 1
    print("\n== ref（面板日期轴）年度交易日数 ==")
    for y in sorted(by_year_ref):
        print(f"  {y}: {by_year_ref[y]}")


if __name__ == "__main__":
    main()
