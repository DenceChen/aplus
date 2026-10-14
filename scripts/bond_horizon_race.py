"""路线 A 第三阶段实跑：低价因子持有期经济学——换手摊薄判决（PLATFORM.md §7.5）。

用法：
    .venv/bin/python scripts/bond_horizon_race.py               # 默认 top10、5 随机种子
    .venv/bin/python scripts/bond_horizon_race.py --top-n 5

背景（二阶段收盘条目）：cheap 低价深度对随机 +0.091%/天，但万 15/边双边
= -0.3%/次换手把一切毛超额压成深度负。转债无 T+1——本脚本问：每 h 个交易日
调仓一次（h ∈ {1,3,5,10,20}），换手摊薄后低价轮动是否转正？

判定标准（提前立，跑完不许改；以保守费率万 15/边双边 cost=0.003 为准）：
  任一 h：净年化 > +10% 且 对随机净超额 > 0 且 窗口三等分至少两段净超额为正
  → 「可进纸面验证」；否则如实报负/边缘。

口径（与 aplus/bond_horizon.py 一致）：D+1 开盘买入持有 h 日收盘卖出；费用按
换手比例收（两臂各自计费——低价池稳定 vs 随机近全换，费用差是摊薄问题本体）；
每期收益连乘；净年化按覆盖交易日折算。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.bond_horizon import build_horizon_cache, cheap_rotation_backtest
from bond_race import load_bond_map, window_of

HORIZONS = [1, 3, 5, 10, 20]
COST_WAN15 = 0.003   # 万 15/边 × 双边（平台保守费率——判定口径）
COST_LOW = 0.0003    # 万 1.5/边 × 双边（低保费通道敏感性，不作判定）
TOP_N = 10
SEEDS = 5
ANN_MIN = 0.10       # 判定线：净年化 > +10%
EXCESS_MIN = 0.0     # 判定线：对随机净超额 > 0
SEG_POS_MIN = 2      # 判定线：三等分至少两段为正


def nav_metrics(net_series, n_days):
    """期净收益序列 → (总收益, 净年化, 最大回撤)——与 bond_horizon 同口径。"""
    nav = 1.0
    peak, mdd = 1.0, 0.0
    for r in net_series:
        nav *= (1.0 + r)
        peak = max(peak, nav)
        mdd = max(mdd, (peak - nav) / peak)
    total = nav - 1.0
    ann = (1.0 + total) ** (250.0 / n_days) - 1.0 if total > -1 else -1.0
    return total, ann, mdd


def run_grid(cache, start, end, h, cost, seeds, top_n):
    cheap = cheap_rotation_backtest(None, start, end, top_n=top_n, hold_days=h,
                                    cost=cost, cache=cache)
    rands = [cheap_rotation_backtest(None, start, end, top_n=top_n, hold_days=h,
                                     cost=cost, random_seed=s, cache=cache)
             for s in range(1, seeds + 1)]
    n = cheap["n_periods"]
    for r in rands:  # 网格配对完整性：同日历同网格
        assert r["n_periods"] == n
        assert [p["day"] for p in r["periods"]] == [p["day"] for p in cheap["periods"]]
    rand_net = [[p["net"] for p in r["periods"]] for r in rands]
    avg_rand_net = [sum(col) / len(col) for col in zip(*rand_net)]
    rand_turn = sum(r["avg_turnover_names"] for r in rands) / len(rands)
    cheap_net = [p["net"] for p in cheap["periods"]]
    cheap_gross = [p["gross"] for p in cheap["periods"]]
    rand_gross_cols = zip(*[[p["gross"] for p in r["periods"]] for r in rands])
    avg_rand_gross = [sum(col) / len(col) for col in rand_gross_cols]
    excess = [cn - a for cn, a in zip(cheap_net, avg_rand_net)]
    gross_excess = [cg - a for cg, a in zip(cheap_gross, avg_rand_gross)]
    return {"cheap": cheap, "rand_ann": nav_metrics(avg_rand_net, rands[0]["n_days"])[1],
            "rand_turn": rand_turn, "excess": excess, "gross_excess": gross_excess,
            "h": h, "cost": cost}


def segments(excess_per_period, h, n_parts=3):
    """窗口三等分（按调仓期切），每段净超额 %/天。"""
    n = len(excess_per_period)
    out = []
    for k in range(n_parts):
        seg = excess_per_period[n * k // n_parts: n * (k + 1) // n_parts]
        out.append(sum(seg) / (len(seg) * h) if seg else 0.0)
    return out


def verdict(cheap, excess_per_day, segs):
    why = []
    if cheap["annualized_return"] <= ANN_MIN:
        why.append(f"净年化{cheap['annualized_return']*100:+.1f}%≤+10%")
    if excess_per_day <= EXCESS_MIN:
        why.append("对随机净超额≤0")
    if sum(1 for s in segs if s > 0) < SEG_POS_MIN:
        why.append(f"三段仅{sum(1 for s in segs if s > 0)}段为正")
    return "达标" if not why else "未达标：" + "；".join(why)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--top-n", type=int, default=TOP_N)
    parser.add_argument("--seeds", type=int, default=SEEDS)
    args = parser.parse_args()

    print("[1/3] 加载转债日线库并构建因子缓存……", flush=True)
    bond_map = load_bond_map(args.data_root)
    if not bond_map:
        print("无数据：先跑 .venv/bin/python scripts/bond_bootstrap.py")
        return 1
    start, end = window_of(bond_map)
    cache = build_horizon_cache(bond_map)
    print(f"    {len(bond_map)} 只 | 回测窗口 {start} → {end}", flush=True)

    print("[2/3] 五档持有期 × 两档费率 × 随机对照……", flush=True)
    results = {c: [run_grid(cache, start, end, h, c, args.seeds, args.top_n)
                   for h in HORIZONS] for c in (COST_WAN15, COST_LOW)}

    print("[3/3] 判决（标准已提前立）……\n", flush=True)
    for cost, label in ((COST_WAN15, f"主判定 · cost={COST_WAN15}（万15/边双边，保守）"),
                        (COST_LOW, f"敏感性 · cost={COST_LOW}（万1.5/边双边，低保费通道）")):
        print(f"=== {label} ===")
        hdr = (f"{'h':>3} {'调仓次数':>8} {'毛收益/期':>9} {'费用/期':>8} {'净收益/期':>9} "
               f"{'净年化':>8} {'最大回撤':>8} {'换手/期':>7} {'随机换手':>7} "
               f"{'净超额/天':>9} {'随机净年化':>9}")
        print(hdr)
        print("-" * len(hdr))
        for g in results[cost]:
            ch = g["cheap"]
            exc_per_day = sum(g["excess"]) / (len(g["excess"]) * g["h"])
            print(f"{g['h']:>3} {ch['n_periods']:>8} {ch['gross_per_period']*100:>+8.4f}% "
                  f"{ch['cost_per_period']*100:>+7.4f}% {ch['net_per_period']*100:>+8.4f}% "
                  f"{ch['annualized_return']*100:>+7.1f}% {ch['max_drawdown']*100:>7.1f}% "
                  f"{ch['avg_turnover_names']:>7.1f} {g['rand_turn']:>7.1f} "
                  f"{exc_per_day*100:>+8.4f}% {g['rand_ann']*100:>+8.1f}%")
        print()

    print("分段检验（窗口三等分，对随机净超额 %/天）——regime 稳定性：")
    print(f"{'cost':>7} {'h':>3} {'段1':>10} {'段2':>10} {'段3':>10}")
    for cost in (COST_WAN15, COST_LOW):
        for g in results[cost]:
            segs = segments(g["excess"], g["h"])
            print(f"{cost:>7} {g['h']:>3} " + " ".join(f"{s*100:>+9.4f}%" for s in segs))
    print()

    print("毛超额对照（未扣费，%/天；费用影响见两臂换手差）：")
    for g in results[COST_WAN15]:
        ge = sum(g["gross_excess"]) / (len(g["gross_excess"]) * g["h"])
        print(f"  h={g['h']:>2}: 毛超额 {ge*100:+.4f}%/天 | 净超额 "
              f"{sum(g['excess'])/(len(g['excess'])*g['h'])*100:+.4f}%/天")

    print("\n判定（提前立：任一 h 净年化>+10% 且 对随机净超额>0 且 三段≥两段为正 → 可进纸面验证）：")
    qualified = []
    for g in results[COST_WAN15]:
        segs = segments(g["excess"], g["h"])
        exc_per_day = sum(g["excess"]) / (len(g["excess"]) * g["h"])
        v = verdict(g["cheap"], exc_per_day, segs)
        print(f"  h={g['h']:>2}：{v}")
        if v == "达标":
            qualified.append(g["h"])
    if qualified:
        print(f"\n结论：h ∈ {qualified} 全部达标 → 可进纸面验证。")
    else:
        print("\n结论：无任何 h 达标——如实记录负/边缘结果，不进纸面验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
