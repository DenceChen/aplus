"""复合赛马：权重 × 调仓频率 × 缓冲区，三者一起进 `N_GATES` 道闸。

★ 这里原先把闸数**写死成「七道」**，而 `aplus/evolve_race.py` 的
`N_GATES = len(GATE_NAMES)` 早已是 **12**。同一判决在两个脚本里对外说了两个数 ——
而 `evolve_race.py:89` 自己写着「混进 GATE_NAMES 会让判决宣称 N 道闸全过，
而实际少了一道，这是**对外的错误陈述**，比数字漂移更糟」。

⚠️ 这个 bug 的形状是 `docs/MISTAKE_LOG_2026-10-14.md:177` 记过的那个：
**主脚本修好了，兄弟脚本没跟着修。** ⇒ 所以这里不再写死数字，
直接用 `N_GATES` 格式化：下次加闸，两边自动一致。

## 为什么需要单独一个脚本

`evolve_race_fast.py` 的频率是**命令行参数**（一次只跑一个 K），
`evolve_weekly.py` 的候选池到今天为止也只有权重那一维。
真正要回答的问题是「**哪一组权重配哪种执行方式**」，
那是一个 84 格的联合搜索，不是 4 次独立的单频率赛马——
后者每跑一次只改一个轴，排序只在那一个轴上成立。

## 与单频率赛马的差别（不可省略）

1. `excess_tstat` / `breadth_tstat` 收到 `rebalance_every`：
   K 日调仓时逐日序列高度自相关，逐日 t 会虚高（实测 1.0–2.0 倍，
   见 `data/reports/frequency_block_t_2019_2023.json`）
2. 去重与「谁才是现任」都按**三元组**（`candidate_key`）
3. 测试段装不下 3 个调仓周期的候选被排除**并留痕**（`race` 的 `rejected`）

## 默认不写 weights.json

必须显式 `--promote` 才会落盘，且仍要过 `oos_decision` 的全部闸门。
本脚本跑完会打印全部闸门的判决原文——不解析 JSON 去猜结论。

用法：
    .venv/bin/python scripts/evolve_race_composite.py \
        --start 2024-10-01 --data-root /tmp/aplus_snap_final \
        --out race_composite_v1.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections, evaluate_weights
from aplus.evolve import (candidate_key, load_weights, objective, save_weights)
from aplus.evolve_race import (N_GATES, breadth_tstat, down_excess_tstat,
                               excess_tstat, oos_decision, race)
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent


def resolve_data_root(data_root: Path) -> Path:
    """校验数据根目录，**绝不回落**到仓库 data/。

    静默回落比报错更危险：调用方以为自己跑在快照上（结果可复现），
    实际跑的是活动主库，于是「已复现」变成一句空话。
    """
    data_root = Path(data_root)
    if not data_root.is_dir():
        raise SystemExit(f"数据根目录不存在：{data_root}")
    return data_root


def evaluate_factory(xs):
    """7 参 evaluate——频率与缓冲必须能被 race 送进来，否则就必须报错。"""

    def ev(_xs, w, dates, tn, bn, rebalance_every=1, buffer_multiple=None):
        res = evaluate_weights(xs, w, top_n=tn, bottom_n=bn, dates=dates,
                               rebalance_every=rebalance_every,
                               buffer_multiple=buffer_multiple)
        stats = dict(rolling_stats(res))
        t, n_obs = excess_tstat(res, rebalance_every=rebalance_every)
        stats["excess_t"] = t
        stats["excess_days"] = n_obs              # 有效观测数（块数），非交易日数
        bt, _ = breadth_tstat(res, rebalance_every=rebalance_every)
        stats["breadth_t"] = bt
        dt, _ = down_excess_tstat(res, rebalance_every=rebalance_every)
        stats["down_t"] = dt
        stats["n_ranked"] = int(res.attrs.get("n_ranked", -1))
        stats["n_universe"] = int(res.attrs.get("n_universe", 0))
        return {"stats": stats,
                "objective": round(objective({"weights": w, "stats": stats}), 4),
                "n": int(len(res))}

    return ev


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-10-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_final"))
    ap.add_argument("--out", default="race_composite_v1.json")
    ap.add_argument("--promote", action="store_true", help="达标才写 weights.json")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--bottom-n", type=int, default=10)
    ap.add_argument("--label", default="",
                    help="给报告打一个标签。**方向污染时必须写明**，"
                         "例如 exploratory：反转方向假设来自 2019-2023 独立验证，"
                         "而那段数据现在落在本窗口内——用它验收自己就是自证")
    args = ap.parse_args()

    # 候选池与 evolve_weekly 共用一份定义，不在这里另抄一遍
    sys.path.insert(0, str(ROOT / "scripts"))
    from evolve_weekly import UNVERIFIED_DIRECTIONS, candidate_weights

    data_root = resolve_data_root(args.data_root)
    store = LocalStore(data_root)
    reports = data_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空")
        return 1
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    print(f"载入日线 {args.start} ~ {end} …", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    print(f"日线 {len(daily_map)} 只载入完成（{time.time()-t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    print(f"横截面 {len(xs['_dates'])} 个交易日（{time.time()-t0:.0f}s）", flush=True)

    cur = load_weights(reports)
    champion = cur["weights"]
    cands = candidate_weights(champion)
    print(f"复合候选 {len(cands)} 组（权重 × 频率 × 缓冲）", flush=True)

    r = race(xs, cands, evaluate_factory(xs), top_n=args.top_n, bottom_n=args.bottom_n)
    if r.get("error"):
        print(f"赛马失败：{r['error']}")
        return 1
    if r.get("rejected"):
        print(f"（{len(r['rejected'])} 组因测试段装不下 3 个调仓周期被排除）", flush=True)

    champ_key = candidate_key(champion)
    print(f"\n训练段 {r['train_dates'][0]} ~ {r['train_dates'][-1]}（{len(r['train_dates'])} 日）")
    print(f"测试段 {r['test_dates'][0]} ~ {r['test_dates'][-1]}（{len(r['test_dates'])} 日）\n")
    print(f"{'排名':<4}{'K':>4}{'缓冲':>6}{'训练目标':>9}{'测试超额':>10}"
          f"{'测试t':>8}{'广度t':>8}{'有效观测':>9}{'毛超额':>9}{'成本占比':>9}  权重")
    print("─" * 122)
    for i, row in enumerate(r["table"], 1):
        tr, te = row["train"], row["test"]
        key = candidate_key({"weights": row["weights"],
                             "rebalance_every": row.get("rebalance_every", 1),
                             "buffer_multiple": row.get("buffer_multiple")})
        mark = " ★现任" if key == champ_key else ""
        st = te["stats"]
        t = st.get("excess_t")
        bt = st.get("breadth_t")
        t_s = f"{t:+.2f}" if t is not None else "—"
        bt_s = f"{bt:+.2f}" if bt is not None else "—"
        cs = st.get("cost_share")
        # 成本占比是「能不能活」的数：毛超额里有多少被手续费吃掉。
        # 5 万本金 / 10 只持仓时它和 20% 的年化目标同量级。
        cs_s = f"{cs:.0%}" if isinstance(cs, (int, float)) else "—"
        print(f"{i:<4}{row.get('rebalance_every', 1):>4}{str(row.get('buffer_multiple')):>6}"
              f"{tr['objective']:>9.3f}{st.get('up_excess_vs_market', 0):>10.2%}"
              f"{t_s:>8}{bt_s:>8}"
              f"{st.get('excess_days', 0):>9}"
              f"{st.get('gross_up_excess', 0):>9.2%}{cs_s:>9}  {row['weights']}{mark}")

    n_cmp = len(r["table"])
    dec = oos_decision(r, champion, unverified_directions=UNVERIFIED_DIRECTIONS)
    print(f"\n【裁决】{'✅ 晋级' if dec.get('promote') else '⏸️ 不晋级'}"
          f"（Bonferroni 按本轮 {n_cmp} 次比较校正；逐一检查 {dec.get('examined')} 组）"
          f"\n{dec.get('reason')}")
    if args.label:
        print(f"\n⚠️ 报告标签：{args.label}——该标签下**不得据此晋级**，理由见 --label 说明")

    champ_row = next((x for x in r["table"]
                      if candidate_key({"weights": x["weights"],
                                        "rebalance_every": x.get("rebalance_every", 1),
                                        "buffer_multiple": x.get("buffer_multiple")})
                      == champ_key), None)
    if champ_row is not None:
        cs, ts = champ_row["train"]["stats"], champ_row["test"]["stats"]
        print(f"\n现任（{champion}）训练目标 {champ_row['train']['objective']:.3f} "
              f"排名 #{r['table'].index(champ_row) + 1}/{n_cmp}")
        print(f"  测试超额 {ts.get('up_excess_vs_market', 0):+.2%}"
              f"  t={ts.get('excess_t')}  广度t={ts.get('breadth_t')}")

    out = {"kind": "composite_race", "label": args.label or "standard",
           "window": [args.start, end],
           "entry_lag": ENTRY_LAG, "n_candidates": len(cands),
           "n_raced": n_cmp, "n_rejected": len(r.get("rejected") or []),
           "champion": champion, "train_dates": [r["train_dates"][0], r["train_dates"][-1]],
           "test_dates": [r["test_dates"][0], r["test_dates"][-1]],
           "table": [{"weights": x["weights"],
                      "rebalance_every": x.get("rebalance_every", 1),
                      "buffer_multiple": x.get("buffer_multiple"),
                      "train_objective": x["train"]["objective"],
                      "test_objective": x["test"]["objective"],
                      "train_stats": x["train"]["stats"],
                      "test_stats": x["test"]["stats"]} for x in r["table"]],
           "rejected": r.get("rejected") or [],
           "decision": {"promote": bool(dec.get("promote")),
                        "reason": dec.get("reason", "")},
           "elapsed_s": round(time.time() - t0)}
    path = reports / args.out
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(f"\n→ {path}  总耗时 {time.time()-t0:.0f}s")

    if dec.get("promote") and args.promote:
        best = dec["best"]
        save_weights(reports, best["weights"], {
            "promoted_on": pd.Timestamp.today().strftime("%Y-%m-%d"),
            "rebalance_every": best.get("rebalance_every", 1),
            "buffer_multiple": best.get("buffer_multiple"),
            "reason": dec.get("reason"),
            "window": [args.start, end],
            "protocol": "oos train/test split + embargo + composite(weight x freq x buffer)",
            "entry_lag": ENTRY_LAG,
            "train": best["train"], "test": best["test"]})
        print(f"  已写入 weights.json：{best['weights']} K={best.get('rebalance_every')}")
    elif dec.get("promote"):
        print(f"  （{N_GATES} 道闸全过，但未加 --promote，不写 weights.json）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())