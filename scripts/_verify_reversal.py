"""一次性独立验证：反转方向在 **2019-2023** 上成立吗？

## 为什么必须是 2019-2023

2026-10-11 的处境：

- 我在 2024-10 ~ 2026-10 的 2 年窗口上看到 `{mom20:-1.0}` 优于现任
  （+0.03% vs −0.60%），又定位到机制（涨榜 Top10 占全市场 `mom20>100%`
  那仅有的 3 只里的 2 只，集中度 370 倍）。
- **这个方向认识本身就来自那段数据。** 再用同一段验收是自欺，
  所以加了「方向污染闸」把它挡下，等前瞻数据。

而 2019-2023 这段数据，我**从未看过任何一次赛马结果**（历次赛马都是近 2 年）。
它是当下唯一真正干净的验证集。用它来问「反转是真的，还是 2024-2026 的噪音」。

## 候选只有 4 组，且**全部避开 turnratio**

`turnratio` 只覆盖 92.7% 的横截面（4541/4899），而其他因子覆盖 99.5%~100%。
把股票池不一样的组放在一起比，比的就是噪声。这里只比股票池一致的四组。

## 这不是生产赛马

它不进 `evolution_log`，不参与晋级判定，报告写到 /tmp。
它的作用只有一个：**回答「方向对不对」**。
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.backtest import build_cross_sections, evaluate_weights  # noqa: E402
from aplus.store import LocalStore  # noqa: E402

CANDIDATES = [
    ("现任", {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}),
    ("纯 20 日反转", {"mom20": -1.0}),
    ("反转为主", {"mom20": -0.5, "rev5": 0.5}),
    ("现任 + 低换手", {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5,
                       "turn20": -1.0}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_final"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--train-ratio", type=float, default=0.6)
    ap.add_argument("--embargo", type=int, default=5)
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/independent_validation_2019_2023.json"))
    ap.add_argument("--rebuild", action="store_true",
                    help="忽略 /tmp 下的面板缓存，强制重建（面板构建约 223s）")
    args = ap.parse_args()

    t0 = time.time()
    store = LocalStore(args.data_root)
    daily_map = {p.stem: store.read_daily(p.stem)
                 for p in sorted((args.data_root / "master" / "daily").glob("*.parquet"))}
    from aplus.snapshot import load_factors_map
    print(f"日线 {len(daily_map)} 只载入 {time.time()-t0:.0f}s", flush=True)

    cache = Path("/tmp/reversal_panel_cache.pkl")
    if cache.exists() and not args.rebuild:
        import pickle
        xs = pickle.loads(cache.read_bytes())
        print(f"面板从缓存载入 {len(xs['_dates'])} 个交易日（--rebuild 可强制重建）", flush=True)
    else:
        xs = build_cross_sections(store, args.start, args.end, daily_map=daily_map,
                                  factors_map=load_factors_map(store))
        import pickle
        # 缓存**必须保留 `_daily_map`**（次日 bar 来源）。
        # 第一版只存 `_dates/_market/_entry_lag` + 日期键，丢掉了它，
        # 于是从缓存载入的 `evaluate_weights` 取不到次日 bar、返回空表——
        # 表现为「所有数字都是 0.00%，且 n=0」，完全不报错。
        cache.write_bytes(pickle.dumps(xs))
    dates = sorted(xs["_dates"])
    print(f"横截面 {len(dates)} 个交易日 {dates[0]} ~ {dates[-1]} "
          f"（{time.time()-t0:.0f}s）", flush=True)

    # 切段：与生产赛马同一套 embargo（隔离带），避免交界处泄漏
    from aplus.evolve_race import split_window
    train_dates, test_dates = split_window(dates, train_ratio=args.train_ratio,
                                           embargo_days=args.embargo)
    print(f"训练 {len(train_dates)} 日 {train_dates[0]}~{train_dates[-1]} | "
          f"测试 {len(test_dates)} 日 {test_dates[0]}~{test_dates[-1]}", flush=True)

    rows = []
    for name, w in CANDIDATES:
        # 必须传 dates：否则 evaluate_weights 的 attrs["n_ranked"] 算不出来（返回 0）
        pred = evaluate_weights(xs, w, top_n=10, bottom_n=10, dates=dates)
        # 列名是 **`pred_date`**，不是 `date`。
        # 第一版写成 `if "date" in pred.columns else pred` —— 不在，于是静默走 else
        # 分支拿全部行，训练段与测试段**算成了同一段**，还会照常打印出一张很像样的表。
        # 「有输出的错误数字」比崩溃危险得多，这里改成硬断言。
        assert "pred_date" in pred.columns, f"列名不对：{list(pred.columns)}"
        assert "direction" in pred.columns, f"缺 direction 列：{list(pred.columns)}"
        st = {}
        for seg, seg_dates in (("train", train_dates), ("test", test_dates)):
            sub = pred[pred["pred_date"].isin(seg_dates)]
            st[seg] = _stats(sub)
        # 可打分股票数：不能用 `dates[0]`。
        # 2019-01-02 是数据首日，`listed_days=1 < min_history=41` → 横截面为空 →
        # scoreable_count 返回 0。（`evaluate_weights` 的 attrs["n_ranked"] 用的正是
        # `use_dates[0]`，在早期窗口上会同样错算成 0。）
        # 取**第一个非空**横截面，才代表这组权重实际能打分的股票数。
        from aplus.backtest import scoreable_count
        probe = next((d for d in dates if not xs[d].empty), None)
        assert probe is not None, "没有任何非空横截面"
        st["test"]["n_ranked"] = scoreable_count(xs[probe], w)
        st["test"]["probe_date"] = probe
        assert st["test"]["n_ranked"] > 0, f"股票池为 0：{w} 的因子列可能全缺"
        rows.append({"name": name, "weights": w, **st})
        t, te = st["train"], st["test"]
        print(f"  {name:<14} 训练 {t['excess']*100:>+7.2f}% (t={t['excess_t']:>+5.2f}, "
              f"广度t={t['breadth_t']:>+5.2f}, {t['days']:>4}日) | "
              f"测试 {te['excess']*100:>+7.2f}% (t={te['excess_t']:>+5.2f}, "
              f"广度t={te['breadth_t']:>+5.2f}, {te['days']:>4}日, "
              f"n_ranked={te['n_ranked']})", flush=True)

    # 训练段与测试段必须真的不同——切段写错时两者会完全相等，这里当哨兵
    for r in rows:
        assert r["train"]["n"] != r["test"]["n"] or r["test"]["n"] == 0, (
            f"{r['name']}：训练 {r['train']['n']} 与测试 {r['test']['n']} 行数相同——"
            f"切段没生效，两段数字会一模一样")

    out = {"kind": "independent_validation",
           "window": [args.start, args.end],
           # 现任那行单独标出来：证伪判定要的是**跨窗口**证据，
           # 所以每段窗口里「现任自己」的表现必须可定位。
           "champion": {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},
           "train_dates": [train_dates[0], train_dates[-1]],
           "test_dates": [test_dates[0], test_dates[-1]],
           "note": ("独立验证集：我从未看过这段数据的任何赛马结果。"
                    "只比股票池一致的 4 组（避开 turnratio，它只覆盖 92.7%）。"),
           "rows": rows, "elapsed_s": round(time.time() - t0)}
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {args.out}")
    return 0


def _stats(sub: pd.DataFrame) -> dict:
    """分段统计。

    ## t 值必须用 `excess_tstat`，不能用 `rolling_stats`

    第一版写 `rs.get("excess_t", 0.0)` —— 但 `rolling_stats` **根本不返回**
    `excess_t`（它的键是 hit_rate_* / avg_*_return / up_excess_vs_market /
    excess_breadth / excess_tilt）。`excess_tstat` 是 `aplus.evolve_race` 里
    单独的一层：先按日聚成「涨篮子 − 市场」序列再做 t 检验。

    取错函数的后果与取错列名一样：**t 恒为 0.00，而表照常打印**——
    +0.41% 看上去是结论，实际上一行显著性都没算过。
    """
    from aplus.evolve_race import breadth_tstat, excess_tstat
    from aplus.verify import rolling_stats

    blank = {"excess": 0.0, "excess_t": 0.0, "breadth_t": 0.0,
             "days": 0, "hit": 0.0, "n": 0}
    if len(sub) == 0:
        return blank
    rs = rolling_stats(sub)
    t, days = excess_tstat(sub)
    bt, _ = breadth_tstat(sub)
    return {"excess": float(rs.get("up_excess_vs_market", 0.0)),
            "excess_t": float(t) if t is not None else 0.0,
            "breadth_t": float(bt) if bt is not None else 0.0,
            "days": int(days or 0),
            "hit": float(rs.get("overall_hit_rate", 0.0)),
            "n": len(sub)}


if __name__ == "__main__":
    raise SystemExit(main())
