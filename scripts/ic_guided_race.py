"""IC 引导候选：让候选池的**符号**来自数据，而不是来自手调。

## 这条脚本存在的唯一理由

2026-10-12 的 IC 诊断给出一个结构性发现：**8 个因子里 7 个的
信息系数为负**，而现任权重 `{mom20:1.0, rev5:0.3, vol20:0.5, vsur:0.5}`
给其中三个（mom20 / vol20 / vsur）挂了**正**权重。

再去看候选池——14 组权重里 9 组是 `mom20` 正权重，只有 3 组测负动量，
**没有任何一组同时用负 vol20 与正 mom20**。

也就是说：如果数据说的是「这三个因子该反着来」，
84 组赛马**在候选生成阶段就看不见那个组合**。
这不是调参没调好，是搜索空间本身缺了这一块。
现任在 3 段独立窗口上全部显著为负（t = −2.83 / −3.55 / −7.56），
与「三个因子的权重符号是反的」这个解释完全一致。

## 协议：符号只从训练段来

这是本脚本唯一不可让步的一条。

- IC 在 `split_window` 切出的**训练段**上计算，测试段一根都不碰。
- 候选由训练段的 IC 符号与幅度生成（`ic_sign_*` / `ic_mag_*` / `ic_topK`）。
- 然后这些候选与 84 组标准候选**一起**进赛马，走同一套闸门（`N_GATES` 道，判据以 `aplus/evolve_race.py` 为准）。
- IC 引导候选**不豁免任何闸门**，也不自动晋级。

IC 是用数据算出来的，所以「IC 为正的方向」在这一步之后
**不再算未验证方向**——它的验证发生在测试段，不是这里。
这一点必须说清楚，否则等于用 IC 当证据、用测试段当装饰。

## 与前瞻验证的关系

若某个 IC 引导候选在测试段过闸，它获得的资格是
「**值得开一份新的前瞻注册**」，不是「可以上实盘」。
前瞻台账的三条硬规则（注册即冻结 / 窗口晚于注册日 / 门槛随候选数收紧）
一条都不因为这条脚本而放松。

用法：
    .venv/bin/python scripts/ic_guided_race.py --start 2024-10-01 \
        --data-root /tmp/aplus_snap_1012 --out ic_guided_race_v1.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections, evaluate_weights
from aplus.evolve import candidate_key, load_weights, objective
from aplus.evolve_race import (EMBARGO_DAYS, TRAIN_RATIO, breadth_tstat,
                               down_excess_tstat, excess_tstat, oos_decision,
                               race, split_window)
from aplus.factor import FACTOR_NAMES
from aplus.provenance import stamp  # noqa: E402
from aplus.factor_ic import (FWD_WINDOWS, MIN_OBS, build_price_cache,
                             daily_ic_ranked, forward_returns,
                             make_ranked_forward, window_block)
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent
IC_WINDOW = "lag1_real"          # 定符号只用真实成交口径，不用泄露窗口
IC_GUIDED_FREQS = (1, 5, 21)


def train_ic(train_dates, xs, cache, factors, window=IC_WINDOW):
    """**只**在给定日期上算逐因子 IC。调用方必须把训练段传进来。

    不设默认日期 = 不允许调用方「忘了传」而悄悄用全窗口 ——
    全窗口 IC 定出的符号再用同一段数据验收，就是自证。
    """
    enter, exit_off = FWD_WINDOWS[window]
    vals = {f: [] for f in factors}
    for day in train_dates:
        universe = xs.get(day)
        if universe is None or universe.empty:
            continue
        u_idx = universe.set_index("code")
        fwd = forward_returns(cache, list(u_idx.index), day, enter, exit_off)
        rf = make_ranked_forward(u_idx, fwd)
        if rf is None:
            continue
        for f in factors:
            ic = daily_ic_ranked(u_idx, rf, f)
            if ic["all"] is not None:
                vals[f].append(ic["all"])
    out = {}
    for f, v in vals.items():
        s = pd.Series(v, dtype=float)
        if len(s) < MIN_OBS:
            out[f] = None                 # 测不出来 → 不参与定符号
            continue
        sd = float(s.std(ddof=1))
        out[f] = {"mean": round(float(s.mean()), 5),
                  "t": (round(float(s.mean() / (sd / len(s) ** 0.5)), 3)
                        if sd > 0 else None),
                  "n": len(s)}
    return out


def ic_candidates(ic: dict, freqs=IC_GUIDED_FREQS) -> list[dict]:
    """由训练段 IC 生成候选，全部带 `source` 标记以便事后区分。

    四种形态：
    - `ic_sign_all`  只用符号（等权）
    - `ic_mag_all`   用 IC 幅度当权重（幅度越强权重越大）
    - `ic_top3/5`    只用 |IC| 最强的 k 个
    - `ic_champ_sign` 只对现任用到的因子按 IC 重新定符号，其余置 0

    权重全部**不归一化到固定尺度**：`composite_score` 用 `sum(|w|)` 作分母，
    绝对大小不影响排名顺序，归一与否只影响可读性。
    """
    usable = {f: v for f, v in ic.items() if v and v["mean"] is not None}
    if not usable:
        return []
    out = []

    def emit(name, w):
        w = {f: round(float(x), 4) for f, x in w.items() if abs(x) > 1e-9}
        if not w:
            return
        for k in freqs:
            out.append({"weights": dict(w), "rebalance_every": k,
                        "buffer_multiple": None, "source": name})

    emit("ic_sign_all", {f: 1.0 if v["mean"] > 0 else -1.0
                         for f, v in usable.items()})
    emit("ic_mag_all", {f: v["mean"] for f, v in usable.items()})
    for k in (3, 5):
        top = sorted(usable.items(), key=lambda kv: -abs(kv[1]["mean"]))[:k]
        emit(f"ic_top{k}", {f: v["mean"] for f, v in top})
    return out


def ic_champion_sign(ic: dict, champion: dict) -> dict | None:
    """现任用到的因子，按训练段 IC 重新定符号；IC 测不出来的置 0。

    这不是「优化现任」，而是检验一个具体假设：
    **现任之所以在三段窗口上全负，是因为三个因子的符号反了。**
    """
    out = {}
    for f in champion:
        hit = ic.get(f)
        out[f] = 0.0 if (not hit or hit["mean"] is None) else hit["mean"]
    return {f: v for f, v in out.items() if abs(v) > 1e-9} or None


def evaluate_factory(xs):
    def ev(_xs, w, dates, tn, bn, rebalance_every=1, buffer_multiple=None):
        res = evaluate_weights(xs, w, top_n=tn, bottom_n=bn, dates=dates,
                               rebalance_every=rebalance_every,
                               buffer_multiple=buffer_multiple)
        stats = dict(rolling_stats(res))
        t, n_obs = excess_tstat(res, rebalance_every=rebalance_every)
        stats["excess_t"] = t
        stats["excess_days"] = n_obs
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
    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_1012"))
    ap.add_argument("--out", default="ic_guided_race_v1.json")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--bottom-n", type=int, default=10)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        raise SystemExit(f"数据根目录不存在：{data_root}")
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
    cache = build_price_cache(daily_map, factors_map)
    print(f"日线 {len(daily_map)} 只 / 价格缓存 {len(cache)} 只"
          f"（{time.time() - t0:.0f}s）", flush=True)

    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    dates = xs["_dates"]
    train_dates, test_dates = split_window(dates, TRAIN_RATIO, EMBARGO_DAYS)
    print(f"横截面 {len(dates)} 日；训练段 {train_dates[0]} ~ {train_dates[-1]}"
          f"（{len(train_dates)} 日）／测试段 {test_dates[0]} ~ {test_dates[-1]}"
          f"（{len(test_dates)} 日）\n", flush=True)

    print(f"在**训练段**上算 IC（窗口 {IC_WINDOW}）——测试段一根都不碰：")
    ic = train_ic(train_dates, xs, cache, list(FACTOR_NAMES))
    print(f"{'因子':<12}{'训练段IC':>11}{'t':>8}{'日数':>7}  现任权重")
    print("─" * 56)
    champ = load_weights(reports)["weights"]
    for f in FACTOR_NAMES:
        v = ic.get(f)
        if not v or v["mean"] is None:
            print(f"{f:<12}{'样本不足':>19}      {champ.get(f, 0)}")
            continue
        cur = champ.get(f)
        flag = ""
        if cur and v["mean"] != 0 and (cur > 0) != (v["mean"] > 0):
            flag = "  ← 符号与现任相反"
        print(f"{f:<12}{v['mean']:>11.4f}{(v['t'] if v['t'] is not None else 0):>8.2f}"
              f"{v['n']:>7}  {cur if cur is not None else '—'}{flag}")

    guided = ic_candidates(ic)
    cs = ic_champion_sign(ic, champ)
    if cs:
        guided.append({"weights": cs, "rebalance_every": 1,
                       "buffer_multiple": None, "source": "ic_champ_sign"})
    print(f"\nIC 引导候选 {len(guided)} 组")

    from evolve_weekly import candidate_weights
    standard = candidate_weights(champ)
    all_cands = standard + guided
    print(f"标准候选 {len(standard)} 组 + 引导 {len(guided)} 组 = {len(all_cands)} 组"
          f"（Bonferroni 按 {len(all_cands)} 次比较校正）\n", flush=True)

    r = race(xs, all_cands, evaluate_factory(xs), top_n=args.top_n,
             bottom_n=args.bottom_n)
    if r.get("error"):
        print(f"赛马失败：{r['error']}")
        return 1
    src = {candidate_key(c): c.get("source", "standard") for c in all_cands
           if isinstance(c, dict)}
    champ_key = candidate_key(champ)

    print(f"{'排名':<4}{'来源':<16}{'K':>4}{'训练目标':>9}{'测试超额':>10}"
          f"{'测试t':>8}{'广度t':>8}{'有效观测':>9}{'成本占比':>9}  权重")
    print("─" * 118)
    for i, row in enumerate(r["table"], 1):
        te = row["test"]["stats"]
        key = candidate_key({"weights": row["weights"],
                             "rebalance_every": row.get("rebalance_every", 1),
                             "buffer_multiple": row.get("buffer_multiple")})
        mark = " ★现任" if key == champ_key else ""
        t = te.get("excess_t")
        bt = te.get("breadth_t")
        t_s = f"{t:+.2f}" if t is not None else "—"
        bt_s = f"{bt:+.2f}" if bt is not None else "—"
        cs = te.get("cost_share")
        cs_s = f"{cs:.0%}" if isinstance(cs, (int, float)) else "—"
        print(f"{i:<4}{src.get(key, '?'):<16}{row.get('rebalance_every', 1):>4}"
              f"{row['train']['objective']:>9.3f}"
              f"{te.get('up_excess_vs_market', 0):>10.2%}"
              f"{t_s:>8}{bt_s:>8}"
              f"{te.get('excess_days', 0):>9}{cs_s:>9}  {row['weights']}{mark}")

    n_cmp = len(r["table"])
    from evolve_weekly import UNVERIFIED_DIRECTIONS
    dec = oos_decision(r, champ, unverified_directions=UNVERIFIED_DIRECTIONS)
    print(f"\n【裁决】{'✅ 晋级' if dec.get('promote') else '⏸️ 不晋级'}"
          f"（Bonferroni 按 {n_cmp} 次比较校正；逐一检查 {dec.get('examined')} 组）"
          f"\n{dec.get('reason')}")
    print("\n注意：即使 IC 引导候选过闸，它获得的资格也只是"
          "「**值得开一份新的前瞻注册**」——前瞻台账的三条硬规则"
          "（注册即冻结 / 窗口晚于注册日 / 门槛随候选数收紧）一条都不放松。")

    def _row_key(row):
        return candidate_key({"weights": row["weights"],
                              "rebalance_every": row.get("rebalance_every", 1),
                              "buffer_multiple": row.get("buffer_multiple")})

    best_guided = [x for x in r["table"] if src.get(_row_key(x)) != "standard"]
    if best_guided:
        bg = best_guided[0]
        bg_key = _row_key(bg)
        print(f"\nIC 引导候选里训练目标最高的一组：{bg['weights']} "
              f"K={bg.get('rebalance_every')}（来源 {src.get(bg_key)}）"
              f" 测试超额 {bg['test']['stats'].get('up_excess_vs_market', 0):+.2%}")

    out = {"kind": "ic_guided_race", "label": args.label or "standard",
           "window": [args.start, end], "entry_lag": ENTRY_LAG,
           "ic_window": IC_WINDOW,
           "train_ic_window": [train_dates[0], train_dates[-1]],
           "test_window": [test_dates[0], test_dates[-1]],
           "train_ic": ic, "n_candidates": len(all_cands), "n_raced": n_cmp,
           "n_guided": len(guided), "champion": champ,
           "train_dates": [r["train_dates"][0], r["train_dates"][-1]],
           "test_dates": [r["test_dates"][0], r["test_dates"][-1]],
           "table": [{"weights": x["weights"],
                      "source": src.get(candidate_key({
                          "weights": x["weights"],
                          "rebalance_every": x.get("rebalance_every", 1),
                          "buffer_multiple": x.get("buffer_multiple")}), "?"),
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
    # ── 2026-10-14 补盖溯源章 ──────────────────────────────────────
    # 这份报告是**全项目最吃重的一份**：它是唯一产出过闸候选
    # `{mom20:-1.0} K=63 t=+4.14` 的赛马，也是冻结进前瞻验证候选集的
    # 那个数的唯一来源。而 10-12 23:26 那一版同时栽在两处：
    #   ① 早于基准污染修复（3c846ff，14:02）
    #   ② 判决用正态近似临界 3.47，而 df=10 的真临界是 5.03
    # 两处都让数字**看起来完全正常**——这正是不盖章最危险的地方。
    #
    # 生成者登记成 `aplus/evolve_race.py`（`oos_decision` 在那里），
    # 而不是本脚本：判新鲜要看**判决逻辑**是否变过，而不只是驱动脚本。
    stamp(path, "aplus/evolve_race.py", root=ROOT,
          extra={"window": out["window"], "n_candidates": out["n_candidates"],
                 "decision_promote": bool(dec.get("promote"))})
    print(f"\n→ {path}  总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())