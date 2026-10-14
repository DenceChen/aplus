"""路线 A 第二阶段实跑：转债 T+0 因子赛马 + 随机对照（PLATFORM.md §7.5）。

用法：
    .venv/bin/python scripts/bond_race.py                 # 默认全窗口 top10
    .venv/bin/python scripts/bond_race.py --top-n 5       # 改持仓数

判定标准（提前立，跑完不许改）：
- 扣费后对随机超额 > +0.1%/天 → 强（够格进入纸面验证）
- 0 ~ +0.1%/天 → 观察
- < 0 → 淘汰

对照臂（同漏斗同费用同口径，只换最后一步选券）：
- 随机 N 种子：与候选完全相同的 universe 过滤 + D+1 open→close 口径
- 全池等权：当日全部合格债等权（市场平均盘中基准）
费用：万 15/边 × 双边（转债佣金万 0.5~2 无印花税，含滑点保守口径）。
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.bond_backtest import (
    build_bond_cross_sections,
    bond_portfolio_sim,
    bond_walk_forward,
)
# load_bond_map 已迁入包内（run_daily 每日流水线要用），此处保留同名导出
from aplus.bond_sync import load_bond_map

CANDIDATES = {
    "动量型": {"mom20": 1.0, "vsur": 0.3, "rev5": -0.3},   # 追强势 + 量能确认
    "反转型": {"rev5": 1.0, "cheap": 0.3},                  # 买超跌 + 低价保护
    "低价型": {"cheap": 1.0},                               # 纯低价深度（债底保护）
    "均衡型": {"mom20": 0.4, "rev5": 0.4, "vol20": 0.3, "vsur": 0.3, "cheap": 0.5},
}

VERDICT_STRONG, VERDICT_WATCH = 0.001, 0.0  # +0.1%/天以上强；0~0.1% 观察


def window_of(bond_map: dict) -> tuple[str, str]:
    """回测窗口以数据实际覆盖为准。

    摘牌历史债腾讯也返回 640 根（其生涯末段，可能是 2004-2007）——按根数判
    「满历史」会把窗口锚到古代。正确锚点：**仍然新鲜**的满历史债（≥600 根
    且末日距今 ≤30 自然日）的最早首日 → 全池最晚末日。古债已被 is_active
    挡在合格宇宙之外，不污染回测（防幸存者偏差靠的是它们在场、而非计入）。
    """
    end = max(df["date"].iloc[-1] for df in bond_map.values())
    fresh_cut = (pd.Timestamp(end) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    full = [df for df in bond_map.values() if len(df) >= 600 and df["date"].iloc[-1] >= fresh_cut]
    if not full:
        raise RuntimeError("无新鲜的满历史转债（≥600 根且末日 ≤30 自然日），先跑 scripts/bond_bootstrap.py")
    start = min(df["date"].iloc[0] for df in full)
    return start, end


def daily_series(results: pd.DataFrame) -> pd.Series:
    if results is None or results.empty:
        return pd.Series(dtype=float)
    return results.groupby("pred_date")["intraday"].mean().sort_index()


def market_results(cross_sections: list) -> pd.DataFrame:
    """全池等权基准：当日全部合格债都入选（同 schema 走同一 sim）。"""
    rows = []
    for entry in cross_sections:
        for code, n in entry["next"].items():
            rows.append({"pred_date": entry["day"], "code": code, "trade_date": n["date"],
                         "open": n["open"], "close": n["close"], "intraday": n["intraday"],
                         "score": float("nan")})
    return pd.DataFrame(rows)


def verdict(excess: float) -> str:
    if excess > VERDICT_STRONG:
        return "强（> +0.1%/天）"
    if excess > VERDICT_WATCH:
        return "观察（0 ~ +0.1%/天）"
    return "淘汰（< 0）"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--equity", type=float, default=1.0)
    parser.add_argument("--seeds", type=int, default=5, help="随机对照种子数")
    args = parser.parse_args()

    print("[1/4] 加载转债日线库……", flush=True)
    bond_map = load_bond_map(args.data_root)
    if not bond_map:
        print("无数据：先跑 .venv/bin/python scripts/bond_bootstrap.py")
        return 1
    start, end = window_of(bond_map)
    bars = [len(df) for df in bond_map.values()]
    fresh_cut = (pd.Timestamp(end) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    fresh = sum(1 for df in bond_map.values() if df["date"].iloc[-1] >= fresh_cut)
    print(f"    {len(bond_map)} 只（含摘牌历史债）| 近 30 日仍新鲜 {fresh} 只 | "
          f"K 线中位数 {int(pd.Series(bars).median())} 根", flush=True)
    print(f"    回测窗口（数据实际覆盖）：{start} → {end}", flush=True)

    print("[2/4] 构建逐日合格宇宙横截面（权重无关，只算一次）……", flush=True)
    cs = build_bond_cross_sections(bond_map, start, end)
    sizes = pd.Series([len(e["cross"]) for e in cs])
    idx = [i for i, e in enumerate(cs) if len(e["cross"]) >= sizes.quantile(0.98)]
    print(f"    {len(cs)} 个信号日 | 合格宇宙中位 {int(sizes.median())} 只 | "
          f"峰值 {int(sizes.max())} 只（{cs[idx[0]]['day'] if idx else '-'}）", flush=True)

    print("[3/4] 随机对照（同漏斗同口径）+ 全池等权基准……", flush=True)
    rand_runs = [bond_walk_forward(bond_map, start, end, top_n=args.top_n,
                                   random_seed=s, cross_sections=cs)
                 for s in range(1, args.seeds + 1)]
    rand_dailies = [daily_series(r) for r in rand_runs]
    rand_avg_panel = pd.concat(rand_dailies, axis=1).mean(axis=1)  # 逐日对随机均值
    rand_baseline = float(pd.concat(rand_dailies).groupby(level=0).mean().mean())
    rand_sims = [bond_portfolio_sim(r, equity=args.equity) for r in rand_runs]
    rand_sim = rand_sims[len(rand_sims) // 2]  # 中位种子代表

    mkt = market_results(cs)
    mkt_sim = bond_portfolio_sim(mkt, equity=args.equity)
    mkt_daily = daily_series(mkt)

    print("[4/4] 因子赛马（判定标准已提前立）……\n", flush=True)
    print("注：候选与随机对照每日成交笔数/费用结构完全相同 → 「对随机超额」扣费前后同值，"
          "判定阈值直接适用。全池等权为逐日全部合格债（笔数更多、费用占比略低）。\n")
    header = (f"{'组':　<4} {'日均盘中':>9} {'对随机超额':>10} {'对全池超额':>10} "
              f"{'净值(扣费)':>10} {'年化':>8} {'最大回撤':>8} {'判定':<18}")
    print(header)
    print("-" * len(header))

    def fmt_sim(sim):
        return (f"{sim['total_return']*100:+9.1f}% {sim['annualized_return']*100:+7.1f}% "
                f"{sim['max_drawdown']*100:7.1f}%")

    rand_row_sim = fmt_sim(rand_sim)
    print(f"{'随机对照':　<4} {rand_baseline*100:+8.4f}% {'—':>10} "
          f"{(rand_baseline - float(mkt_daily.mean()))*100:+9.4f}% {rand_row_sim} "
          f"{'（中位种子，n=' + str(args.seeds) + '）'}")
    seed_avgs = [float(d.mean()) * 100 for d in rand_dailies]
    print(f"{'':4} 随机种子带: {', '.join(f'{a:+.4f}%' for a in seed_avgs)}")
    mkt_row = fmt_sim(mkt_sim)
    print(f"{'全池等权':　<4} {float(mkt_daily.mean())*100:+8.4f}% "
          f"{(float(mkt_daily.mean()) - rand_baseline)*100:+9.4f}% {'—':>10} {mkt_row}")

    seg_report = {}
    for name, w in CANDIDATES.items():
        res = bond_walk_forward(bond_map, start, end, weights=w, top_n=args.top_n,
                                cross_sections=cs)
        d = daily_series(res)
        sim = bond_portfolio_sim(res, equity=args.equity)
        excess = float((d - rand_avg_panel).mean())  # 逐日配对：同信号日相减
        excess_mkt = float((d - mkt_daily).mean())
        print(f"{name:　<4} {float(d.mean())*100:+8.4f}% {excess*100:+9.4f}% "
              f"{excess_mkt*100:+9.4f}% {fmt_sim(sim)} {verdict(excess)}")
        seg_report[name] = d

    # 分段诚实性：窗口三等分，看超额是否只在某一段成立（股票侧 8-9 月失效的教训）
    print("\n分段检验（窗口三等分，对随机的逐日配对超额 / 天）：")
    days = sorted(rand_avg_panel.index)
    thirds = [days[len(days) * i // 3] for i in (1, 2)] + [None]
    labels, lo = [], "0000-00-00"
    for j, hi in enumerate(thirds, 1):
        labels.append((f"段{j}", lo, hi if hi else "9999-99-99"))
        lo = hi
    seg_names = [f"{lab}({lo}~{hi})" for lab, lo, hi in labels]
    print(f"{'组':　<4} " + " ".join(f"{s:>26}" for s in seg_names))
    for name, d in seg_report.items():
        cells = []
        for _, lo, hi in labels:
            seg = d[(d.index >= lo) & (d.index < hi)]
            rseg = rand_avg_panel[(rand_avg_panel.index >= lo) & (rand_avg_panel.index < hi)]
            cells.append(f"{float((seg - rseg).mean())*100:+9.4f}%/{len(seg)}日")
        print(f"{name:　<4} " + " ".join(f"{c:>26}" for c in cells))
    mkt_cells = []
    for _, lo, hi in labels:
        seg = mkt_daily[(mkt_daily.index >= lo) & (mkt_daily.index < hi)]
        rseg = rand_avg_panel[(rand_avg_panel.index >= lo) & (rand_avg_panel.index < hi)]
        mkt_cells.append(f"{float((seg - rseg).mean())*100:+9.4f}%/{len(seg)}日")
    print(f"{'全池等权':　<4} " + " ".join(f"{c:>26}" for c in mkt_cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
