"""转债低价轮动：样本外**超额**分析——「策略失效」还是「市场 beta」？

## 为什么要重查

`scripts/bond_param_race.py` 的裁决「0/15 组通过样本外验收」只看**绝对收益**：
h=5/top10/cost=0.003 训练 +38.45% / 测试 -13.18%。一个反驳意见是「测试段市场
在崩（600000.SH 同期 -29.85%），策略其实大幅跑赢，只是绝对亏损」。这个反驳的
对错取决于基准选得对不对，本脚本给答案。

## 基准（写死在 aplus.bond_excess.py，见该模块 docstring）

**EW-POOL = 等权持有当期合格转债宇宙全池**，与策略跑在同一引擎、同一网格、
同一 D+1 成交口径、同一换手计费公式上，唯一差别是选券：策略取 cheap Top10，
基准取全池。策略是这个池的子集 → 池亏的钱是 beta，净值跑赢池才是 alpha。
这是对「跑赢」结论最不利、最难跑出正超额的构造方式。

参照股票 600000.SH 只作**背景**打印，不是判据基准——单只银行股不能代表转债
市场（转债 beta 与股票不同），把它当基准会系统性高估策略的「超额」。

用法：
    .venv/bin/python scripts/bond_excess_analysis.py
    .venv/bin/python scripts/bond_excess_analysis.py --equity-ref 600000.SH
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.bond_excess import (  # noqa: E402
    BENCHMARK_DESC,
    BENCHMARK_NAME,
    DEFAULT_COST,
    DEFAULT_HOLD_DAYS,
    DEFAULT_TOP_N,
    EMBARGO_DAYS,
    TRAIN_RATIO,
    equity_reference,
    excess_of,
    pool_period_distribution,
    run_rotation,
    select_cheap_top_n,
    select_full_pool,
    selection_skill,
    split_segments,
)
from aplus.bond_horizon import build_horizon_cache  # noqa: E402
from aplus import provenance  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 🚨 `DEFAULT_GENERATORS` 按这个字符串反查生成器 ⇒ 改了名就解析不出（形状 6：
#   盖一个解析不出生成器的章比不盖更坏，读者无从判断它出自哪份代码）。
GENERATOR = "scripts/bond_excess_analysis.py"

# 🚨 第 156 层：`stamp()` 默认 `daily_subdir="master/daily"` ⇒ 指纹会去数
#   **股票的 5460 个 parquet**，而本脚本读的是 `aplus.bond_sync.load_bond_map`
#   里的 `Path(data_root)/"bonds"/"master"/"daily"`（实测 1087 只）。
#   ⇒ 章上写着「数据 fresh」而它指的是本脚本从没读过的目录（第 156 层同形）。
DAILY_SUBDIR = "bonds/master/daily"

SEG_ORDER = ("train", "test", "full")
SEG_LABEL = {"train": "训练段", "test": "测试段", "full": "全窗口"}


def analyze(cache, segments, equity_df, top_n, hold_days, cost):
    """三段（训练/测试/全窗口）各跑策略臂 + 基准臂，算超额。"""
    out = {}
    for name in SEG_ORDER:
        start, end = segments[name]
        strat = run_rotation(cache, start, end, select_cheap_top_n(top_n),
                             hold_days=hold_days, cost=cost)
        bench = run_rotation(cache, start, end, select_full_pool,
                             hold_days=hold_days, cost=cost)
        row = excess_of(strat, bench)
        row["window"] = [start, end]
        row["strategy"] = {k: strat[k] for k in
                           ("n_periods", "n_days", "total_return", "annualized_return",
                            "max_drawdown", "gross_per_period", "net_per_period",
                            "cost_per_period", "avg_turnover_names", "mean_n_picks")}
        row["benchmark"] = {k: bench[k] for k in
                            ("n_periods", "n_days", "total_return", "annualized_return",
                             "max_drawdown", "gross_per_period", "net_per_period",
                             "cost_per_period", "mean_n_picks")}
        row["selection_skill_gross"] = selection_skill(cache, strat, hold_days)
        row["equity_ref_background"] = equity_reference(equity_df, start, end)
        out[name] = row
    return out


def verdict_text(res: dict) -> str:
    """三段结论的一句话判定。"""
    test = res["test"]
    exc = test["excess_total"]
    t = test["selection_skill_gross"]["vs_pool_mean"]["t_stat"]
    if exc is None:
        return "测试段基准净值归零，超额无定义。"
    if exc > 0:
        return (f"测试段超额为正（{exc:+.2%}）：策略跑赢转债市场但绝对亏损，"
                f"结论应改写为「市场不可投资」。")
    stat = "（统计上与 0 不可区分）" if abs(t) < 2 else "（统计显著）"
    return (f"测试段超额为负（{exc:+.2%}，费前对池均值 t={t:+.2f} {stat}）："
            f"策略未跑赢转债市场，「样本外失败」结论成立——"
            f"但性质是「没有可测量的 alpha」，不是「显著跑输 beta」。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=str(ROOT / "data"))
    ap.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    ap.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS)
    ap.add_argument("--cost", type=float, default=DEFAULT_COST)
    ap.add_argument("--equity-ref", default="600000.SH",
                    help="仅作背景的参照股票，不是判据基准")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    # 🚨 第 125 层：盖章那一刻盘上的字节 ≠ 这一跑真正跑的那些字节。
    #   抓在**跑之前**，比对在**盖章时**；不一致就拒绝盖章（反向的错误证据）。
    # 🚨 第 131 层：必须落在**第一个昂贵步骤之前**（`load_bond_map` 读 1087 个
    #   parquet）—— 放在长跑之后抛错，等于「跑了 10 分钟才告诉你白跑」。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    t0 = time.time()
    from aplus.bond_sync import load_bond_map

    bond_map = load_bond_map(args.data_root)
    if not bond_map:
        print("转债库为空，先跑 scripts/bond_bootstrap.py")
        return
    cache = build_horizon_cache(bond_map)
    cal = cache["calendar"]
    segments = split_segments(cache, TRAIN_RATIO, EMBARGO_DAYS)

    equity_df = None
    eq_path = Path(args.data_root) / "master" / "daily" / f"{args.equity_ref}.parquet"
    if eq_path.exists():
        import pandas as pd
        equity_df = pd.read_parquet(eq_path)

    print(f"转债 {len(bond_map)} 只｜日历 {cal[0]} ~ {cal[-1]}（{len(cal)} 日）")
    print(f"参数 h={args.hold_days} top{args.top_n} cost={args.cost}（与 "
          f"bond_param_race 主判定同口径）")
    print(f"基准 = {BENCHMARK_NAME}：{BENCHMARK_DESC}\n")
    for name in SEG_ORDER:
        s, e = segments[name]
        print(f"{SEG_LABEL[name]} {s} ~ {e}")

    res = analyze(cache, segments, equity_df, args.top_n, args.hold_days, args.cost)

    hdr = (f"{'段':<8}{'策略总收益':>10}{'策略年化':>10}{'基准总收益':>10}"
           f"{'基准年化':>10}{'超额(几何)':>11}{'超额年化':>10}  判定")
    print("\n" + hdr)
    print("─" * 92)
    for name in SEG_ORDER:
        r = res[name]
        print(f"{SEG_LABEL[name]:<8}{r['strategy_total']:>10.2%}"
              f"{r['strategy_ann']:>10.2%}{r['benchmark_total']:>10.2%}"
              f"{r['benchmark_ann']:>10.2%}{r['excess_total']:>11.2%}"
              f"{r['excess_ann']:>10.2%}  {r['verdict']}")

    print("\n费前选券能力（策略 10 只 vs 当期池，事后统计，仅解释结论强度）")
    print(f"{'段':<8}{'vs池均值/期':>13}{'t':>8}{'胜率':>8}"
          f"{'vs池中位数/期':>15}{'t':>8}{'胜率':>8}{'池规模':>8}")
    print("─" * 92)
    for name in SEG_ORDER:
        s = res[name]["selection_skill_gross"]
        vm, vd = s["vs_pool_mean"], s["vs_pool_median"]
        print(f"{SEG_LABEL[name]:<8}{vm['per_period']:>13.2%}{vm['t_stat']:>8.2f}"
              f"{vm['win_rate']:>8.1%}{vd['per_period']:>15.2%}{vd['t_stat']:>8.2f}"
              f"{vd['win_rate']:>8.1%}{s['mean_pool_size']:>8.0f}")

    eq = res["test"]["equity_ref_background"]
    print(f"\n背景（非判据基准）参照股票 {args.equity_ref} 测试段："
          f"{eq['total_return']:+.2%}（{eq['first']} ~ {eq['last']}，{eq['n_days']} 日）")
    print(f"测试段转债宇宙每期毛收益分布（事后环境描述）："
          f"中位数 {res['test']['selection_skill_gross']['vs_pool_median']['per_period']:+.2%}"
          f"（选券-中位数）；均值口径见 JSON pool_distribution_test")

    dist = pool_period_distribution(cache, *segments["test"], hold_days=args.hold_days)
    print("\n【结论】")
    print(verdict_text(res))

    out = {
        "benchmark": {"name": BENCHMARK_NAME, "desc": BENCHMARK_DESC,
                      "note": "基准构造在看到任何收益前定死，未做任何事后调整"},
        "params": {"top_n": args.top_n, "hold_days": args.hold_days, "cost": args.cost,
                   "train_ratio": TRAIN_RATIO, "embargo_days": EMBARGO_DAYS},
        "universe": {"n_bonds": len(bond_map), "calendar_start": cal[0],
                     "calendar_end": cal[-1], "n_calendar_days": len(cal),
                     "calendar_ref": cache.get("calendar_ref")},
        "windows": {k: list(segments[k]) for k in SEG_ORDER},
        "segments": res,
        "pool_distribution_test": dist,
        "equity_ref": args.equity_ref,
        "verdict": verdict_text(res),
        "no_lookahead": ("信号仅用信号日 D 收盘的 cheap 点值，成交一律 D+1；"
                         "本模块不做任何横截面标准化，全窗口统计量不进入决策"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    path = Path(args.out) if args.out else \
        Path(args.data_root) / "reports" / "bond_excess_analysis.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    # 🚨 写在 `write_text` **之后**（章落在报告旁边，不污染报告 schema），
    #   但比对 `expect_bytes` 是它存在的全部理由（第 125 层）。
    provenance.stamp(path, GENERATOR, root=ROOT,
                     data_root=args.data_root, daily_subdir=DAILY_SUBDIR,
                     expect_bytes=expect_bytes,
                     extra={"segments": list(SEG_ORDER),
                            "params": out["params"]})
    print(f"\n总耗时 {time.time() - t0:.0f}s → {path}")


if __name__ == "__main__":
    main()