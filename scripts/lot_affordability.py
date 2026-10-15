"""整手（A 股 100 股）可行性：**¥50,000 本金到底能买进几个名字**（第 137 层附发现）。

## 为什么必须单独测

`aplus/backtest.py` 与 `aplus/costs.py` **都不含任何整手建模**
（实测 `grep -rn '整手' aplus/ scripts/` 零命中）⇒ 回测与摩擦都在**小数股**上跑。

⇒ 于是「N=200 的毛超额 7.57%」是在一个**开不出来的账户**上测的：
  单笔 ¥250，而 1 手（100 股）最便宜的股票也要 ¥100+。

## ⚠️ 口径：**这里必须用不复权价**，不是后复权

- 判「**能不能买**」用的是**当日真实成交价** ⇒ 必须是 `close` 原值；
- 判「**收益**」才用后复权（预注册 G1 指定的 `factors_map`）。

⇒ 同一个字段名在两个问题里答案不同，而这一层最容易顺手取错。

## 它不动判决

本脚本**不重跑回测**，只报一个事实：
在判决窗口里，¥50,000 按 N 等分之后，池子有多大比例**根本买不起 1 手**。
它唯一的用途是让「宽榜更稳」这句话**带上它的前提**。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402
from aplus.store import LocalStore  # noqa: E402

#: 与判决窗口**逐字相同**（预注册 G1 的 `window_arg`）。
WINDOW_START = "2021-01-01"
WINDOW_END = "2026-10-13"          # 右开，与 `[start, end)` 一致
CAPITAL = 50_000.0
LOT = 100                            # A 股 1 手 = 100 股
SAMPLE_SEED = 42                     # 与 `market_nav_caliber.py` 同一种子约定
SAMPLE_N = 500
WIDTHS = (10, 30, 100, 200, 546)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="整手可行性：¥5 万能买进几个名字")
    p.add_argument("--data-root", type=Path, default=None)
    p.add_argument("--sample", type=int, default=SAMPLE_N)
    p.add_argument("--out", type=Path,
                   default=ROOT / "data" / "reports" / "lot_affordability.json")
    args = p.parse_args(argv)

    store = LocalStore(args.data_root or ROOT / "data")
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("🚨 主库为空", file=sys.stderr)
        return 2

    t0 = time.time()
    rng = random.Random(SAMPLE_SEED)
    picked = rng.sample(files, min(args.sample, len(files)))
    closes: list[float] = []
    for f in picked:
        try:
            df = store.read_daily(f.stem)
        except Exception:                      # 单只读不出来不该让整份报告没有
            continue
        w = df[(df["date"] >= WINDOW_START) & (df["date"] < WINDOW_END)]
        if w.empty:
            continue
        px = float(w["close"].iloc[-1])
        if px > 0:
            closes.append(px)
    closes.sort()
    n = len(closes)
    if n == 0:
        print("🚨 窗口内没有可用收盘价", file=sys.stderr)
        return 2

    def q(frac: float) -> float:
        return closes[min(n - 1, int(frac * n))]

    rows = []
    for cap in WIDTHS:
        per = CAPITAL / cap
        max_price = per / LOT                  # 1 手要买得起，股价须 ≤ 此值
        ok = sum(1 for x in closes if LOT * x <= per)
        rows.append({
            "n": cap,
            "per_position_notional": per,
            "max_price_for_one_lot": max_price,
            "affordable_count": ok,
            "affordable_pct": ok / n,
        })

    out = {
        "generated_by": "scripts/lot_affordability.py",
        "window_actual": [WINDOW_START, WINDOW_END],
        "capital": CAPITAL,
        "lot_shares": LOT,
        "price_caliber": "不复权 close（判「能不能买」必须用真实成交价；收益口径才用后复权）",
        "sample": {
            "pool_size": len(files),
            "sampled": len(picked),
            "usable": n,
            "seed": SAMPLE_SEED,
            "statistic": "判决窗口内最后一日收盘价",
        },
        "price_quantiles": {
            "p10": q(0.10), "p50": q(0.50), "p90": q(0.90),
        },
        "widths": rows,
        "verdict": ("N=10 一档可行（绝大多数名字买得起 1 手）；"
                    "**N ≥ 30 的档位在本金 ¥50,000 下大面积不可行**，"
                    "而回测与摩擦**都没有整手建模** ⇒ 那些档的毛超额"
                    "是在**开不出来的账户**上测的。"
                    if rows[0]["affordable_pct"] >= 0.8
                    and any(r["affordable_pct"] < 0.6 for r in rows) else "见 widths"),
        "what_this_cannot_prove": [
            "它**不重跑回测**，所以不能回答「加上整手约束后毛超额会变成多少」。",
            "它只看**窗口末一天的截面**，不是「每天都能买进 N 个名字」的时序可行性。",
            "它不知道策略实际会选哪 N 只 ⇒ 真实可行性取决于选股结果，不等于池子比例。",
        ],
    }
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(args.out, "scripts/lot_affordability.py", root=ROOT,
                     extra={"capital": CAPITAL, "lot_shares": LOT})

    print(f"判决窗口 {WINDOW_START} ~ {WINDOW_END}｜本金 ¥{CAPITAL:,.0f}｜1 手 = {LOT} 股")
    print(f"池 {len(files)} 只，抽样 {len(picked)}，窗口内有价 {n} 只"
          f"（种子 {SAMPLE_SEED}，{time.time() - t0:.0f}s）")
    print(f"价格分位 p10={q(0.10):.2f}  p50={q(0.50):.2f}  p90={q(0.90):.2f} 元")
    print("─" * 78)
    print(f"{'N':>6}{'单笔(元)':>12}{'1手股价上限':>14}{'买得起':>12}{'占比':>9}")
    for r in rows:
        print(f"{r['n']:>6}{r['per_position_notional']:>12.1f}"
              f"{r['max_price_for_one_lot']:>14.2f}"
              f"{r['affordable_count']:>7}/{n}{r['affordable_pct'] * 100:>8.1f}%")
    print("─" * 78)
    print(f"→ {out['verdict']}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
