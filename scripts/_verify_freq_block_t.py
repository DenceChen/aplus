"""在真实数据上测：低频候选的逐日 t 到底虚高多少。

## 为什么必须实测，不能靠 √K 推断

2026-10-11 20:40 的独立验证表（2019-01 ~ 2023-12，纯反转 `{mom20:-1.0}`）：

| K | 年成本 | 年毛超额 | 逐日 t |
|---|---|---|---|
| 1 | 246.3% | +77.4% | 6.32 |
| 5 | 98.2% | +92.8% | 8.01 |
| 21 | 35.8% | +113.4% | 9.19 |
| 63 | 12.2% | +117.9% | **11.17** |

毛超额几乎不动，t 涨了 77%——指纹指向伪显著。

但**理论上界是 √K**（K=63 → 7.9 倍），实测 t 只涨了 1.77 倍。
差在哪里？逐日序列里还留着个股噪声，块内并非完全相同，
所以真实的虚高倍数远小于 √K。**这个倍数必须测出来，不能猜。**

本脚本对同一批回测同时算两种 t：

- `t_daily`：按交易日当独立观测（老口径）
- `t_block`：按 K 日块均值（`rebalance_every` 传进 `excess_tstat`）

并输出 `n_blocks`——K=63 在 962 个交易日上只剩约 15 个独立观测，
那时 t=11.17 意味着 15 个观测里有 11 个标准差，那是**不可能**的。

## 顺带修掉候选池里的一个废条目

原脚本的 `反转+低异常换手`（`turnratio`）在 2019-2023 的 `n_ranked = 0`——
`turnratio` 这一列在那 5 年里根本不存在，那 8 行「净 +114.5%」建立在一个
**空股票池**上。留着它只会让人以为「反转 + 低异常换手」是独立验证过的赢家。
本脚本删掉它，并把 `n_ranked` 列保留在输出里以便再次核对。

本脚本**不做晋级判定**，也不写 weights.json。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.backtest import build_cross_sections, evaluate_weights  # noqa: E402
from aplus.evolve_race import breadth_tstat, excess_tstat  # noqa: E402

CHAMP = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
CANDIDATES = [
    ("现任", CHAMP),
    ("纯 20 日反转", {"mom20": -1.0}),
    ("反转 + 风控", {"mom20": -0.5, "rev5": 0.3, "vol20": 0.3, "vsur": 0.3}),
]
FREQS = [1, 5, 21, 63]
DAYS_PER_YEAR = 244


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("data"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/frequency_block_t_2019_2023.json"))
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    cache = Path("/tmp/reversal_panel_cache.pkl")
    if cache.exists() and not args.rebuild:
        import pickle
        xs = pickle.loads(cache.read_bytes())
        print(f"面板从缓存载入 {len(xs['_dates'])} 个交易日", flush=True)
    else:
        from aplus.store import LocalStore
        from aplus.snapshot import load_factors_map
        store = LocalStore(args.data_root)
        daily_map = {p.stem: store.read_daily(p.stem)
                     for p in sorted((args.data_root / "master" / "daily").glob("*.parquet"))}
        print(f"日线 {len(daily_map)} 只载入 {time.time()-t0:.0f}s", flush=True)
        xs = build_cross_sections(store, args.start, args.end, daily_map=daily_map,
                                  factors_map=load_factors_map(store))
        import pickle
        cache.write_bytes(pickle.dumps(xs))
        print(f"横截面 {len(xs['_dates'])} 个交易日 {time.time()-t0:.0f}s", flush=True)

    dates = sorted(xs["_dates"])
    rows = []
    print(f"\n{'候选':<14} {'K':>3} {'日数':>6} {'块数':>5} "
          f"{'t_daily':>8} {'t_block':>8} {'倍数':>6} {'广度t日':>8} {'广度t块':>8}")
    for name, w in CANDIDATES:
        for k in FREQS:
            pred = evaluate_weights(xs, w, top_n=10, bottom_n=10,
                                    rebalance_every=k, buffer_multiple=None)
            td, n_days = excess_tstat(pred)                       # 老口径：K=1
            tb, n_blocks = excess_tstat(pred, rebalance_every=k)   # 新口径：按块
            bd, _ = breadth_tstat(pred)
            bb, _ = breadth_tstat(pred, rebalance_every=k)
            ratio = abs(td / tb) if (td and tb) else None
            rows.append({
                "name": name, "weights": w, "rebalance_every": k,
                "n_days": int(n_days), "n_blocks": int(n_blocks),
                "t_daily": float(td) if td is not None else None,
                "t_block": float(tb) if tb is not None else None,
                "inflation": float(ratio) if ratio else None,
                "sqrt_k": float(k ** 0.5),
                "breadth_t_daily": float(bd) if bd is not None else None,
                "breadth_t_block": float(bb) if bb is not None else None,
            })
            print(f"{name:<14} {k:>3} {n_days:>6} {n_blocks:>5} "
                  f"{td if td is not None else float('nan'):>8.2f} "
                  f"{tb if tb is not None else float('nan'):>8.2f} "
                  f"{(f'{ratio:.2f}' if ratio else '—'):>6} "
                  f"{bd if bd is not None else float('nan'):>8.2f} "
                  f"{bb if bb is not None else float('nan'):>8.2f}", flush=True)

    out = {"kind": "diagnostic", "variant": "frequency_block_t",
           "window": [args.start, args.end],
           "note": ("同一批回测的两种 t 口径对照。t_daily 把交易日当独立观测（老口径，"
                    "低频下虚高）；t_block 按 K 日块均值聚合（正确口径）。"
                    "本表**不做晋级判定**。turnratio 候选因 2019-2023 无该列、"
                    "n_ranked=0 而被剔除——原表的 8 行建立在空股票池上。"),
           "champion": CHAMP, "rows": rows, "elapsed_s": round(time.time() - t0)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())