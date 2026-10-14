"""自进化赛马（样本外协议）—— 用历史数据跑，训练段选权重、测试段验收。

与 scripts/evolve_weekly.py 的区别：
1. **横截面只算一次**（aplus/panel.py 面板），N 组候选复用 → 提速约 5×
2. **样本外切分**（aplus/evolve_race.py）——训练段选权重、测试段验收，
   训练赢测试垮的候选一律否决。用历史挑权重本身就是数据泄露。

用法：
    .venv/bin/python scripts/evolve_race_fast.py                    # 2 年窗口
    .venv/bin/python scripts/evolve_race_fast.py --start 2025-01-01 # 自定义
    .venv/bin/python scripts/evolve_race_fast.py --promote          # 达标才写 weights.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections, evaluate_weights
from aplus.evolve import load_weights, objective, save_weights
from aplus.evolve_race import (breadth_tstat, down_excess_tstat, excess_tstat,
                               oos_decision, race)
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent


def candidates():
    """内置候选池——形态多样的权重组合，含负权重的低换手/低波变体。

    ## `ivol` 的加入（2026-10-11）

    IVOL 从 2026-10-11 起才有实现，**此前从未参与过任何一次赛马**——
    一个新因子不做样本外检验就等于没做。这里补四组低 IVOL 偏好
    （书 5.3 节口径是**做多最低 IVOL**），以及一组反号对照。
    """
    base = [
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},          # 现任 v1
        {"mom20": 1.4, "rev5": 0.6, "vol20": 0.5, "vsur": 0.5},          # 激进动量
        {"mom20": 0.6, "rev5": 0.3, "vol20": 0.8, "vsur": 0.8},          # 保守均衡
        {"mom20": 1.0, "rev5": 0.6, "vol20": 0.5, "vsur": 0.5},          # 反转加码
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5, "turn20": -0.5},
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.0, "vsur": 0.0, "turn20": -1.0},
        {"mom20": 0.8, "rev5": 0.2, "vol20": 0.5, "vsur": 0.3, "turn20": -0.8},
        {"mom20": 0.6, "rev5": 0.2, "vol20": -0.7, "vsur": 0.3, "turn20": -1.0},
        {"mom20": 0.0, "rev5": 1.0, "vol20": 0.5, "vsur": 0.5},          # 纯反转
        {"mom20": -1.0, "rev5": 1.0, "vol20": 0.5, "vsur": 0.5},         # 反转为主
        {"mom20": 0.5, "rev5": 0.5, "vol20": -1.0, "vsur": 0.0},         # 低波防御
        {"mom20": 2.0, "rev5": 0.0, "vol20": 0.0, "vsur": 0.0},         # 纯动量
        {"mom20": 0.0, "rev5": 0.0, "vol20": 0.0, "vsur": 0.0, "turn20": -1.0},  # 纯低换手
        {"mom20": 0.3, "rev5": 0.3, "vol20": 0.2, "vsur": 0.2, "turn20": -0.2},  # 近等权
        # ── IVOL 系列（书 5.3 节：做多最低 IVOL，故负权重为「偏好低 IVOL」）──
        {"mom20": 0.0, "rev5": 0.0, "vol20": 0.0, "vsur": 0.0, "ivol": -1.0},   # 纯低 IVOL
        {"ivol": -1.0},                                                          # 同上（缺省项补 0）
        {"mom20": 1.0, "rev5": 0.3, "ivol": -0.8},                              # 动量 + 低 IVOL
        {"mom20": 0.5, "rev5": 0.3, "vol20": 0.3, "ivol": -0.5},               # 均衡 + 低 IVOL
        {"ivol": 1.0},                                                           # 反号对照：偏好高 IVOL
        # ── 单因子探针（2026-10-11 补充）────────────────────────────────────
        # 候选池一直以「动量向前」为主，但书 3.5 节的 A 股实证是：
        # 11 个月动量 t=−0.14（**无效**），1 个月是强反转 t=5.94。
        # 而 `mom20` 取负权重恰好就是 20 日反转——6 个月快速筛选里，
        # 唯一测试段正超额的候选正是这一类。方向可能从一开始就搞反了，
        # 所以必须把每个因子单独拉出来看一眼边际效果，不能让它们互相抵消。
        {"mom20": -1.0},                                        # 纯 20 日反转（书 3.5）
        {"rev5": -1.0},                                         # 纯 5 日反向（即动量向前）
        {"vol20": -1.0},                                        # 纯低波
        {"ivol": -0.5, "vol20": -0.5},                          # 低 IVOL + 低波（双重低风险）
        {"turnratio": -1.0},                                    # 纯异常换手（书 3.8，低换手溢价）
        {"turnratio": 1.0},                                     # 反号对照
        {"mom20": -1.0, "rev5": 0.5},                           # 反转为主
        {"mom20": -0.5, "turnratio": -0.5, "ivol": -0.3},       # 反转 + 低换手 + 低 IVOL
        # ── RSI 系列（2026-10-11）──────────────────────────────────────────
        # RSI(14) 的横截面用法只有一种：**低 RSI = 超卖 = 排序靠前**，
        # 所以负权重才表示「偏好超卖」。反向权重是对照组。
        # 之所以值得参赛：它与 mom20 的 Spearman 相关 +0.8648，
        # 但 Top10 只重叠 3/10 —— 高度相关 ≠ 同一个东西，
        # 到底该不该当独立因子只能让样本外赛马裁决，不靠推理。
        {"rsi": -1.0},                                              # 纯 RSI 超卖
        {"rsi": 1.0},                                               # 反号对照：买超买
        {"mom20": 0.5, "rsi": -0.5},                                # 动量 + RSI 超卖
        {"mom20": -0.5, "rsi": -0.5},                               # 反转 + RSI 超卖
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5, "rsi": -0.5},  # 现任 + RSI
    ]
    seen, uniq = set(), []
    for c in base:
        # 缺省因子等价于权重 0，按规范形式去重，否则 {ivol:-1} 与
        # {mom20:0,...,ivol:-1} 会被当成两组不同候选，凭空把 Bonferroni
        # 门槛抬高，把本该晋级的候选误杀。
        key = tuple(sorted((k, v) for k, v in c.items() if v != 0))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def _evaluate_factory(xs, top_n, bottom_n, rebalance_every=1, buffer_multiple=None):
    """评估一组权重在指定日期子集上的成绩。

    必须带上 `excess_t`：显著性硬闸靠它判断「这个超额是不是比随机权重强」。
    少了这一项，oos_decision 会因无显著性数据而一律否决（宁可错杀）。

    同时带上跌腿与多空价差指标：涨腿超额会被大盘方向污染，多空价差不会。
    """
    def ev(_xs, w, dates, tn, bn):
        res = evaluate_weights(xs, w, top_n=tn, bottom_n=bn, dates=dates,
                               rebalance_every=rebalance_every,
                               buffer_multiple=buffer_multiple)
        stats = dict(rolling_stats(res))
        t, n_days = excess_tstat(res)
        stats["excess_t"] = t
        stats["excess_days"] = n_days
        bt, _ = breadth_tstat(res)
        stats["breadth_t"] = bt
        dt, _ = down_excess_tstat(res)
        stats["down_t"] = dt
        # 可打分股票数：缺列的股票在某些权重下永远出局，跨候选比较必须看到它
        stats["n_ranked"] = int(res.attrs.get("n_ranked", -1))
        stats["n_universe"] = int(res.attrs.get("n_universe", 0))
        return {"stats": stats, "objective": round(objective({"weights": w, "stats": stats}), 4),
                "n": int(len(res))}
    return ev


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-02")
    ap.add_argument("--end", default="")
    ap.add_argument("--months", type=int, default=0,
                    help="只取最近 N 个自然月的窗口（快速筛选用）。"
                         "短窗口**没有判决权**，只用来排除明显无效的方向："
                         "样本少 → t 检验几乎无功效，不能据此晋级任何权重。")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--bottom-n", type=int, default=10)
    ap.add_argument("--promote", action="store_true", help="达标才写 weights.json")
    ap.add_argument("--out", default="evolution_race.json")
    ap.add_argument("--train-ratio", type=float, default=0.6)
    ap.add_argument("--embargo", type=int, default=5)
    ap.add_argument("--entry-lag", type=int, default=ENTRY_LAG,
                    help="成交滞后：1=信号D收盘/D+1收盘买/D+2收盘卖（T+1最短可成交）；0=旧前视口径，仅供A/B")
    ap.add_argument("--rebalance-every", type=int, default=1,
                    help="每隔 N 个交易日调仓一次（1=每日，即平台原行为）。"
                         "书的 3.1.4 口径是每月末调仓；5 万元本金日频轮动"
                         "实测年成本 16.91%%，而所有已测因子效应都在 ±1%% 以内——"
                         "在找到优势之前，日频这个结构本身就已经出局。")
    ap.add_argument("--buffer-multiple", type=float, default=None,
                    help="缓冲区倍数（默认 3.0 在 aplus/rebalance.py）。"
                         "与频率正交：削减的是换手**幅度**而非频率。")
    ap.add_argument("--data-root", type=Path, default=ROOT / "data",
                    help="数据根目录（日线主库 + reports 都从这里解析）。"
                         "指向一份 rsync 快照即可在冻结数据上跑赛马：主库写入不是"
                         "原子的，补数进行中并发读会读到半截文件；快照让结果可复现。")
    return ap


def resolve_data_root(data_root: Path) -> Path:
    """校验数据根目录，**绝不回落**到仓库 data/。

    静默回落比报错更危险：调用方以为自己跑在快照上（结果可复现），
    实际跑的是活动主库，于是「已复现」变成一句空话。
    """
    data_root = Path(data_root)
    if not data_root.is_dir():
        raise SystemExit(f"数据根目录不存在：{data_root}")
    return data_root


def main():
    args = build_parser().parse_args()

    data_root = resolve_data_root(args.data_root)
    store = LocalStore(data_root)
    reports = data_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空")
        return
    end = args.end or str(pd.read_parquet(files[0])["date"].max())
    start = args.start
    if args.months > 0:
        # 取最近 N 个自然月：从 end 往前推 N 个月，再取其后第一个交易日。
        # 用**自然月**而不是「N×21 个交易日」——后者在长假与新年后偏移很大，
        # 会让「最近 3 个月」悄悄变成 4 个月。
        start = (pd.Timestamp(end) - pd.DateOffset(months=args.months)).strftime("%Y-%m-%d")
        print(f"快速筛选模式：只取最近 {args.months} 个月 → {start} ~ {end}")
        print("  ⚠ 短窗口没有判决权：样本少 → t 检验功效低，"
              "只用来排除明显无效的方向，不据此晋级权重。")
    cur = load_weights(reports)
    champion = cur["weights"]
    cands = candidates()
    if champion not in cands:
        cands.insert(0, champion)

    t0 = time.time()
    print(f"载入日线 {len(files)} 只…", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    print(f"载入因子表…", flush=True)
    factors_map = load_factors_map(store)

    print(f"横截面预计算 {start} → {end}（每只股票只算一次因子）…", flush=True)
    t1 = time.time()
    xs = build_cross_sections(store, start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=args.entry_lag)
    print(f"  {len(xs['_dates'])} 个交易日 × {len(daily_map)} 只 → {time.time()-t1:.0f}s",
          flush=True)

    r = race(xs, cands, _evaluate_factory(xs, args.top_n, args.bottom_n,
                                          args.rebalance_every, args.buffer_multiple),
             top_n=args.top_n, bottom_n=args.bottom_n,
             train_ratio=args.train_ratio, embargo_days=args.embargo)
    if r.get("error"):
        print(f"赛马失败：{r['error']}")
        return

    buf = args.buffer_multiple if args.buffer_multiple is not None else "关"
    print(f"\n调仓频率：每 {args.rebalance_every} 个交易日｜缓冲区：{buf}")
    if args.rebalance_every > 1:
        nb = len(r["test_dates"]) // args.rebalance_every
        if nb < 3:
            print(f"  ⚠ 测试段只剩 {nb} 个调仓周期，不足 3 个——"
                  f"任何频率对比在此样本量下都没有统计意义，结论只能作废。")
    print(f"训练段 {r['train_dates'][0]} ~ {r['train_dates'][-1]}（{len(r['train_dates'])} 日）")
    print(f"隔离带 {args.embargo} 个交易日（丢弃）")
    print(f"测试段 {r['test_dates'][0]} ~ {r['test_dates'][-1]}（{len(r['test_dates'])} 日）\n")
    print(f"{'排名':<4}{'训练目标':>9}{'测试目标':>9}{'训练超额':>10}{'测试超额':>10}"
          f"{'测试价差':>10}{'跌腿超额':>10}{'t':>7}{'价差t':>7}{'可打分':>8}  权重")
    print("─" * 126)
    for i, row in enumerate(r["table"], 1):
        tr, te = row["train"], row["test"]
        mark = " ★现任" if row["weights"] == champion else ""
        te_t = te["stats"].get("excess_t")
        te_bt = te["stats"].get("breadth_t")
        print(f"{i:<4}{tr['objective']:>9.3f}{te['objective']:>9.3f}"
              f"{tr['stats']['up_excess_vs_market']:>10.2%}{te['stats']['up_excess_vs_market']:>10.2%}"
              f"{te['stats']['excess_breadth']:>10.2%}{te['stats']['down_excess_vs_market']:>10.2%}"
              f"{te_t:>7.2f}{te_bt:>7.2f}{te['stats'].get('n_ranked', -1):>8}"
              f"  {row['weights']}{mark}")
    print("\n「价差」= 多空价差（涨篮子 − 跌篮子），对大盘方向免疫；"
          "「t」= 涨腿超额 t 值，「价差t」= 多空价差 t 值。")

    pool_sizes = {row["train"]["stats"].get("n_ranked", -1) for row in r["table"]}
    if len(pool_sizes) > 1:
        print(f"\n⚠ 各候选的可打分股票数不一致（{sorted(pool_sizes)}）——"
              f"**这些候选不在同一批标的上比较**，排名只能作参考。"
              f"根因是部分日线缺列（turn/ivol 等因子恒为 NaN，"
              f"含该权重的组合会永久剔除这些股票）。补齐数据后必须重跑。")

    unadj = xs.get("_unadjusted") or {}
    if unadj:
        print(f"⚠ 有 {len(unadj)} 只股票缺复权因子表，本次收益按**不复权**口径计算："
              f"{sorted(unadj)[:8]}{' …' if len(unadj) > 8 else ''}")
    else:
        print("复权口径：全部命中因子表（0 只降级）")

    dec = oos_decision(r, champion)
    print(f"\n【裁决】{dec['reason']}")
    if dec.get("promote"):
        print(f"  建议权重：{dec['best']['weights']}")
        if args.promote:
            save_weights(reports, dec["best"]["weights"], {
                "promoted_on": pd.Timestamp.today().strftime("%Y-%m-%d"),
                "protocol": "oos train/test split + embargo",
                "train": dec["best"]["train"], "test": dec["best"]["test"],
                "train_gain": dec["train_gain"], "test_edge": dec["test_edge"],
            })
            print("  已写入 weights.json")
    else:
        print(f"  维持现任：{champion}")

    out = {"window": [start, end], "months": args.months,
           "screen_only": bool(args.months), "champion": champion,
           "entry_lag": args.entry_lag,
           "train_dates": [r["train_dates"][0], r["train_dates"][-1]],
           "test_dates": [r["test_dates"][0], r["test_dates"][-1]],
           "table": [{**{k: v for k, v in row.items() if k != "weights"},
                      "weights": row["weights"]} for row in r["table"]],
           "decision": dec, "elapsed_s": round(time.time() - t0, 1)}
    (reports / args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n总耗时 {time.time()-t0:.0f}s → {reports / args.out}")


if __name__ == "__main__":
    main()
