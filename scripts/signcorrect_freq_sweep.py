"""符号修正后，「反转 + 低频」的净年化扫描 —— 直接回答「能不能到 20%」。

## 为什么这个实验现在才做

`reversal_freq_2019_2023.json`（2026-10-11）那张表是在**错号权重**上算的：

| 权重 | K | 年成本 | 年超额 | 净 |
|---|---|---|---|---|
| 现任 `{mom20:+1.0,...}` | 1 | 272.1% | −145.9% | −418.0% |
| `{mom20:−1.0}` | 63 | 10.5% | +39.7% | **+29.2%**（t=1.23）|

`factor_ic_7y.json` 随后证明那组正号权重**逐符号反**（8 个因子里 7 个
满 8 年，合计 63 个（因子×年）观测，63/63 与该因子的全段 IC 同号；
`turnratio` 缺 2019——需累积成交量历史，缺口计入分母）。
所以旧表里「现任」那一列整个作废——它在一个为「挑明天
会跌的票」而生的配置上记账。而 `{mom20:−1.0}` 那一列**恰好是符号修正的方向**，
只是当时没有理由把它和其余因子一起翻。

## 事前登记（写在跑之前，不许事后改）

**主指标**：2019-01 ~ 2023-12 的 `net`（年化净超额，已扣实际换手成本）。

**达标线**：`net ≥ 0.20`（用户的年化目标）**且** `t_block ≥ 3.0`
**且** `n_ranked ≥ 4000`（覆盖率闸）。

**防过拟合**：本脚本扫描 ~100 个 (候选 × K × 缓冲) 组合。**在这 100 个里挑
最好的，就是又一次多重比较**——和那 97 组权重搜索同类。因此：

1. 任何在 2019-2023 上达标的配置，必须再到 **2024-01 ~ 2026-10** 上复测；
2. 两段**都**达标才算结果，否则只算「值得注册前瞻验证的候选」；
3. 本脚本**不写 `weights.json`**，**不做晋级判定**。

**已知的硬边界**：IC 是 2019-2026 全段测的，所以符号决策对两段都不算
样本外。这里能拿到样本外的只有**频率与缓冲的选择**。

## 为什么频率和缓冲才是真正的战场

日频 K=1 的成本量级是 351%/年 ≈ **0.6%/日**。把日频超额从 0.12%/日
提到 0.24%/日，减去 0.6%/日仍是深度负值——**放大日频超额救不了日频**，
信号和成本在同一个频率上互相抵消。所以要同时动「拿多久」和「换多少」。

而 IC 随持有期**增长**（rev5 低尾 lag1=0.0486 → hold4=0.0635 → hold21=0.0882），
说明信号不是 1 日噪音，拿久不会失效——这是低频可行的前提。

成本口径**不在这里估**：`pred["cost"]` 由 `backtest.cost_drag` 按实际换手
算出（第一版凭印象写了个 `_annual_cost()`，那是编的，且照样打印出一张
漂亮的表——见 `_verify_reversal_freq.py` 的同一段教训）。

## 但它必须**按日取均值再求和**，不能直接 `sum()`

`evaluate_weights` 把同一个组合级 drag 写给涨腿的**每一行**
（`res.loc[res["direction"] == "up", "cost"] = drag`）。于是：

- `groupby("pred_date").mean()` → 每期恰好一个 drag，**正确**；
- `up["cost"].sum()` → 每期记了 `top_n` 个 drag，**放大 top_n 倍**。

第一版写的是 `sum()`，报出的「年成本 370.4%」实际是 **37.04%**——整列虚高
10 倍，且**恰好虚高得越离谱的地方（K=1 日频）越致命**：它让「日频因成本
不可行」这个核心结论看起来像铁证，而真实的 37%/年完全不是那个量级。
写测试时必须钉住这条，否则改回来不会有任何人发现。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.backtest import (  # noqa: E402
    build_cross_sections, evaluate_weights, scoreable_count,
)
from aplus.store import LocalStore  # noqa: E402
from aplus.verify import rolling_stats  # noqa: E402

DAYS_PER_YEAR = 244

# ── 候选 ──
#
# `IC_WEIGHTED` 的权重**直接取 −IC**（因子 ic，权重 −ic）。这是唯一
# 一条不作挑选的构造规则：不是「我试了几组挑了个好的」，而是「按
# 2019-2026 的 IC 原样搬运」。它也最容易证伪——重算 IC 后规则自动跟着变。
IC_WEIGHTED = {
    "mom20": -0.0357, "rev5": +0.0262, "vol20": -0.0514, "vsur": -0.0290,
    "turn20": -0.0428, "turnratio": -0.0378, "ivol": -0.0590, "rsi": -0.0366,
}
CANDIDATES = [
    ("旧默认(错号对照)", {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}),
    ("符号修正默认", {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5}),
    ("纯20日反转", {"mom20": -1.0}),
    ("反转+低换手", {"mom20": -0.5, "turnratio": -0.5}),
    ("IC原样搬运", IC_WEIGHTED),
]
FREQS = [1, 5, 21, 63]
BUFFERS = [None, 2.0, 3.0, 5.0]


def _panel(data_root: Path, start: str, end: str, cache: Path, rebuild: bool):
    if cache.exists() and not rebuild:
        import pickle
        xs = pickle.loads(cache.read_bytes())
        print(f"面板从缓存载入 {len(xs['_dates'])} 个交易日", flush=True)
        return xs
    store = LocalStore(data_root)
    daily_map = {p.stem: store.read_daily(p.stem)
                 for p in sorted((data_root / "master" / "daily").glob("*.parquet"))}
    t = time.time()
    print(f"日线 {len(daily_map)} 只载入 {time.time()-t:.0f}s", flush=True)
    from aplus.snapshot import load_factors_map
    xs = build_cross_sections(store, start, end, daily_map=daily_map,
                              factors_map=load_factors_map(store))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(__import__("pickle").dumps(xs))
    print(f"横截面 {len(xs['_dates'])} 个交易日 {time.time()-t:.0f}s", flush=True)
    return xs


def sweep(xs, start: str, end: str, label: str) -> list[dict]:
    from aplus.evolve_race import breadth_tstat, excess_tstat
    dates = sorted(d for d in xs["_dates"] if d >= start and d <= end)
    probe = next((d for d in dates if not xs[d].empty), None)
    rows = []
    hdr = (f"\n{'候选':<18}{'K':>4}{'缓冲':>6}{'日数':>6}{'年成本':>9}"
           f"{'年超额':>9}{'净':>9}{'t块':>7}{'n_rank':>8}")
    print(f"\n{'='*78}\n{label}  {start} ~ {end}\n{'='*78}{hdr}")
    for name, w in CANDIDATES:
        nr = scoreable_count(xs[probe], w) if probe else 0
        for k in FREQS:
            for buf in BUFFERS:
                pred = evaluate_weights(xs, w, top_n=10, bottom_n=10,
                                        dates=dates,
                                        rebalance_every=k, buffer_multiple=buf)
                up = pred[pred["direction"] == "up"]
                if up.empty:
                    continue
                n_days = max(len(set(up["pred_date"])), 1)
                n_years = max(n_days / DAYS_PER_YEAR, 1e-9)
                cost = float(up.groupby("pred_date")["cost"].mean().sum()) / n_years
                t, _ = excess_tstat(pred)
                bt, _ = breadth_tstat(pred)
                st = rolling_stats(up)
                ann_x = float(st.get("up_excess_vs_market", 0.0)) * DAYS_PER_YEAR
                row = {"name": name, "weights": w, "rebalance_every": k,
                       "buffer_multiple": buf, "annual_cost": cost,
                       "annual_excess": ann_x, "net": ann_x - cost,
                       "t": float(t) if t is not None else 0.0,
                       "breadth_t": float(bt) if bt is not None else 0.0,
                       "n_ranked": nr, "n_days": n_days}
                rows.append(row)
                mark = "  <<<" if (row["net"] >= 0.20 and row["t"] >= 3.0
                                   and nr >= 4000) else ""
                print(f"{name:<18}{k:>4}{str(buf):>6}{n_days:>6}{cost:>9.1%}"
                      f"{ann_x:>9.1%}{row['net']:>9.1%}{row['t']:>7.2f}{nr:>8}{mark}",
                      flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_final"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--confirm-start", default=None,
                    help="留空则只跑第一段；给日期则跑第二段复测")
    ap.add_argument("--confirm-end", default=None)
    ap.add_argument("--cache", type=Path,
                    default=Path("/tmp/reversal_panel_cache.pkl"))
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/signcorrect_freq_sweep.json"))
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    xs = _panel(args.data_root, "2019-01-01", "2026-12-31", args.cache, args.rebuild)
    out = {"kind": "signcorrect_freq_sweep",
           "prereg": {"target_net": 0.20, "min_t": 3.0, "min_n_ranked": 4000,
                      "note": "达标组合必须两段都达标才算结果"},
           "window": [args.start, args.end], "rows": []}
    out["rows"] = sweep(xs, args.start, args.end, "选段（2019-2023）")
    if args.confirm_start:
        out["confirm_window"] = [args.confirm_start, args.confirm_end]
        out["confirm_rows"] = sweep(xs, args.confirm_start, args.confirm_end,
                                    "复测段（未参与选择）")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())