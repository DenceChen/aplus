"""每周自进化：候选权重 → 样本外赛马 → 裁决 → weights.json。

launchd 每周日 20:00 执行（只读主库，不打任何外部源，与回填/增量天然无冲突）。
所有评估（含被否决者）写入 evolution_log.json——证据链完整。

## 第四阶段改版（2026-10-11）：为什么不再用单窗口 maybe_promote

**旧版**在同一个 130 天窗口上既选冠军又验收（`maybe_promote` 的三道闸——冷却期 /
样本量 / 改进幅度——全部作用在同一段历史上）。这挡不住过拟合：在同一段数据上挑
权重，挑出来的权重**已经记住了那段历史的噪声**，把它当「进化」写进线上配置就是
把答案抄进答卷。

2 年赛马（`evolution_race.json`）给出的判决是 **14 组候选 0 组样本外超额为正**。
也就是说旧版每跑一次，就在自动生产一个「样本内漂亮、样本外为负」的权重。

**新版**改成标准样本外协议（`aplus/evolve_race.py`）：

1. 窗口拉长到 2 年，按时间切 **训练段 60% / 隔离带 5 日 / 测试段 40%**
   （隔离带是因为 20 日窗口因子与次日收益在边界仍有重叠，直接相接会互相污染）
2. 排名**只看训练段**（这才是可以拿来调参的部分）
3. 晋级要两条腿都过：训练段领先达标 **且** 测试段超额为正 **且** 测试段不劣于现任
4. 横截面只算一次（`aplus/panel.py`），N 组候选复用 —— 提速约 5×

默认不晋级是**正常结果**，不是失败。样本外否决比假晋级便宜得多。
"""
import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from _bootstrap import RUN                      # 无缓冲 + 噪音隔离 + run/step 日志
from aplus.backtest import ENTRY_LAG, build_cross_sections, evaluate_weights
from aplus.evolve import (candidate_key, dedupe_candidates, load_weights,
                          objective, save_weights)
from aplus.log import setup_logging
from aplus.provenance import stamp
from aplus.evolve_race import excess_tstat, oos_decision, race
from aplus.rebalance import BUFFER_MULTIPLE
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent
WINDOW_DAYS = 730   # 约 2 年——测试段要够长才有判决力（130 天切出来测试段只有 35 日）


# ── 方向污染台账（2026-10-11）──────────────────────────────────────
# 这里的每一项，都是**看过测试段结果之后**才决定要试的方向。它们进入候选池，
# 但会被 `oos_decision` 的第五道闸拦下：不晋级、不写 weights.json，
# 直到有**没被看过的**交易日做前瞻验证。
#
# 闸门是否放行由 `aplus.evolve_race.DIRECTION_VERDICTS` 决定（独立验证记录），
# 本表只声明「哪些方向属污染」。两者分开：**声明**与**解除**分开放，
# 否则「解除」会被误当成「当初就没污染过」。
#
# 起因：2 年终裁（2024-10~2026-10）跑完后，我看到现任 `{mom20:1.0}` 样本外
# 涨腿超额 −0.60%、t=−2.83，而 `{mom20:-1.0}` 是 +0.03%。诊断又定位到机制：
# 涨榜 Top10 占全市场 `mom20>100%` 那仅有的 3 只里的 2 只，集中度 370 倍。
# 于是想试反转——**但这个方向来自测试段本身**，训练/测试切分挡不住。
#
# `low_ivol` 一并登记：它的历史成绩来自 **ivol 前视泄露期**，
# 至今没在干净数据上重验过。顺手解除它是很自然、也完全错误的动作。
# （`negative_momentum` 已于 19:42 在 2019-2023 独立数据上证实并解除。）
UNVERIFIED_DIRECTIONS = {"negative_momentum", "low_ivol"}


# ── 执行参数轴（2026-10-11 20:50）───────────────────────────────
# 独立验证（2019-01 ~ 2023-12，962 个交易日，纯反转 `{mom20:-1.0}`）：
#
#   | K | 年成本 | 年毛超额 | 净     |
#   |---|--------|----------|--------|
#   | 1 | 246.3% |  +77.4%  | −168.9%|
#   | 5 |  98.2% |  +92.8%  |   −5.3%|
#   | 21|  35.8% | +113.4%  |  +77.6%|
#   | 63|  12.2% | +117.9%  | +105.7%|
#
# **276 个百分点的差距，全部来自这一个参数。** 候选池里没有它，
# 赛马就永远在「已知不可行的执行方式」下比较信号强弱——
# 挑出来的权重再送进那个不可行的执行方式，**测得再准也落不了地**。
REBALANCE_GRID = (1, 5, 21, 63)

# 缓冲区只在**低频**上砍得动换手。实测（同一批回测，纯反转）：
#
#   K=1  成本 246.3% → 122.5%（砍掉一半）
#   K=5  成本  98.2% →  78.1%
#   K=21 成本  35.8% →  35.4%（可忽略）
#   K=63 成本  12.2% →  12.1%（完全无效）
#
# 所以 K≥21 不跑缓冲变体——**那不是省算力，是跑实测已知的无效格子**。
# 网格在看任何赛马结果之前就已固定。
BUFFER_GRID = {1: (None, BUFFER_MULTIPLE), 5: (None, BUFFER_MULTIPLE),
               21: (None,), 63: (None,)}


def execution_of(row: dict) -> dict:
    """从一条赛马行取出执行参数（调仓频率 + 缓冲区倍数）。

    ## 为什么必须有这么一个函数

    晋级落盘与回测基线**都要**用它——两处漏一处，实盘跑的就不是
    被闸门检验过的那套执行方式：

    - `save_weights(...)` 不传 execution → 落成日频 → 实盘 `run_daily`
      读到 `rebalance_every=1` → **上线的是日频执行**。
      实测同一组 `{mom20:-0.5, turnratio:-0.5}`：K=21 净 +77.6%/年，
      K=1 净 **−168.9%/年**。闸门放行了月频策略，上线跑的是日频版本。
    - `evaluate_weights(...)` 不传 → 回测基线（前端展示的数字）
      也是日频口径，于是**上线的和展示的都是没被检验过的那个版本**。

    两处必须同源，否则「展示为正、上线为负」这种最难查的错就出现了。
    """
    return {"rebalance_every": int(row.get("rebalance_every", 1) or 1),
            "buffer_multiple": row.get("buffer_multiple")}


def base_weight_sets(current: dict) -> list:
    """14 组权重。锚定在**看到任何结果之前**：不因上周谁赢了而增删，
    否则就是拿结果改考题。频率与缓冲是另一根轴，见 `candidate_weights`。"""
    return [
        dict(current),
        {"mom20": 1.0, "rev5": 0.0, "vol20": 0.5, "vsur": 0.5},   # 纯动量+风控因子
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.0, "vsur": 0.0},   # 纯价格动量
        {"mom20": 1.4, "rev5": 0.6, "vol20": 0.5, "vsur": 0.5},   # 激进动量
        {"mom20": 0.6, "rev5": 0.3, "vol20": 0.8, "vsur": 0.8},   # 保守均衡
        {"mom20": 1.0, "rev5": 0.6, "vol20": 0.5, "vsur": 0.5},   # 反转加码
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5, "turn20": -0.5},
        {"mom20": 1.0, "rev5": 0.3, "vol20": 0.0, "vsur": 0.0, "turn20": -1.0},
        {"mom20": 0.8, "rev5": 0.2, "vol20": 0.5, "vsur": 0.3, "turn20": -0.8},
        {"mom20": 0.6, "rev5": 0.2, "vol20": -0.7, "vsur": 0.3, "turn20": -1.0},
        # ── 反转族（2026-10-11）：原池 10 组**全是动量向前**，反转方向根本没被试过 ──
        # 机制诊断：涨榜把 10 个名额里的 2 个花在全市场仅有的 3 只 `mom20>100%`
        # 上（集中度 370 倍），现任是「追逐极端」而非动量。
        # 书 3.5 节的 A 股实证也一致：11 个月动量无效，1 个月强反转。
        {"mom20": -1.0},                                        # 纯 20 日反转
        {"mom20": -0.5, "rev5": 0.5},                           # 反转为主
        {"mom20": -0.5, "turnratio": -0.5},                      # 反转 + 低异常换手
        {"mom20": -0.5, "rev5": 0.3, "vol20": 0.3, "vsur": 0.3}, # 反转 + 风控因子
    ]


def candidate_weights(current: dict) -> list:
    """候选集：14 组权重 × 调仓频率 × 缓冲区。

    三元组去重必须看**全部三个字段**（`dedupe_candidates`）——
    `{mom20:-1} 日频` 与 `{mom20:-1} 月频` 净值差 276 个百分点，
    当成同一组就把最关键的差异抹掉了。

    现任必然落在 `(现任权重, K=1, 无缓冲)` 这一格上，
    裁决层用 `candidate_key` 认的就是这一格。
    """
    out = []
    for w in base_weight_sets(current):
        for k in REBALANCE_GRID:
            for buf in BUFFER_GRID.get(k, (None,)):
                out.append({"weights": dict(w), "rebalance_every": k,
                            "buffer_multiple": buf})
    return dedupe_candidates(out)


def decide_promotion(race_result: dict, champion_weights: dict) -> dict:
    """唯一的晋级入口——样本外裁决。

    闸门清单**不在这里重写一遍**：见 `aplus.evolve_race.GATE_NAMES`，
    那是唯一真相源。闸数由它算出，写死在 docstring 里的数字只会漂
    （2026-10-14 的实测：docstring「七道闸」、行内注释「第五道闸」、
    判决理由「六条腿」三处互相矛盾，且都不等于代码里的实际否决点数）。

    旧版这里是 `maybe_promote(..., last_promotion, ...)`，三道闸全在单窗口上，
    挡不住过拟合。现在整条链只有这一个裁决点，不留后门。

    方向污染闸（2026-10-11）：方向来自事后观察的候选，即便前面的闸全过也不晋级。
    训练/测试切分挡不住「看完测试段结论再决定试哪个方向」，Bonferroni 也数不了它。
    """
    return oos_decision(race_result, champion_weights,
                        unverified_directions=UNVERIFIED_DIRECTIONS)


def build_log_record(race_result: dict, today: str, window: list, champion: dict,
                     proposed: int = 0, raced: int = 0) -> dict:
    """进化日志记录——形状必须与前端 /api/evolution 兼容。

    `evaluations[].stats` 取**测试段**（样本外）统计，不是训练段：前端那张表以前
    显示的其实是同窗口数字，看上去像「实绩」。样本外才是能拿去用的那个数。
    """
    evals = []
    for row in race_result.get("table") or []:
        tr, te = row.get("train") or {}, row.get("test") or {}
        tr_stats = tr.get("stats") or {}
        te_stats = te.get("stats") or {}
        evals.append({
            "weights": row["weights"],
            "rebalance_every": row.get("rebalance_every", 1),
            "buffer_multiple": row.get("buffer_multiple"),
            "objective": tr.get("objective", 0.0),          # 排序依据 = 训练段
            "source": row.get("source", "builtin"),
            "stats": te_stats,                               # 前端展示 = 样本外
            "train_stats": tr_stats,
            "train_objective": tr.get("objective", 0.0),
            "test_objective": te.get("objective", 0.0),
            "oos_edge": row.get("oos_edge", 0.0),
        })
    dec = decide_promotion(race_result, champion)
    return {
        "date": today,
        "window": window,
        "protocol": "oos train/test split + embargo",
        "train_dates": race_result.get("train_dates", [])[:1] + race_result.get("train_dates", [])[-1:],
        "test_dates": race_result.get("test_dates", [])[:1] + race_result.get("test_dates", [])[-1:],
        "evaluations": evals,
        "rejected": race_result.get("rejected", []),
        "decision": {"promoted": bool(dec.get("promote")), "reason": dec.get("reason", "")},
        "researcher": {"proposed": proposed, "raced": raced},
    }


def _evaluate_factory(xs, top_n, bottom_n):
    """评估一组权重在指定日期子集上的成绩。**7 参**——多出的两个是
    `rebalance_every` / `buffer_multiple`，`race` 据此决定能不能把频率送进来。

    ## t 必须按调仓周期分块（2026-10-11 20:40）

    K 日调仓时，一天里「选了谁」在 K 天内不变。逐日序列把这 K 天当成 K 个
    独立观测，等于把同一个决策数了 K 遍。实测指纹：同一组权重从 K=1 到 K=63，
    毛超额几乎不动（+77.4% → +117.9%），t 却从 6.32 涨到 11.17。

    所以 `excess_tstat` 收到 `rebalance_every`，按 K 日块均值聚合。
    `excess_days` 从此报的是**有效观测数（块数）**，不是交易日数——
    这正是它该报的量：K=63 在 962 日上只有约 15 个独立观测。
    """
    def ev(_xs, w, dates, tn, bn, rebalance_every=1, buffer_multiple=None):
        res = evaluate_weights(xs, w, top_n=tn, bottom_n=bn, dates=dates,
                               rebalance_every=rebalance_every,
                               buffer_multiple=buffer_multiple)
        stats = dict(rolling_stats(res))
        t, n_obs = excess_tstat(res, rebalance_every=rebalance_every)
        stats["excess_t"] = t
        stats["excess_days"] = n_obs
        stats["trading_days"] = int(res["pred_date"].nunique()) if len(res) else 0
        return {"stats": stats,
                "objective": round(objective({"weights": w, "stats": stats}), 4),
                "n": int(len(res))}
    return ev


def main():
    # `--days` 只用于**冒烟测**：候选池从 14 涨到 84 之后，整条链路的
    # 任何一个环节出错，都要能在周日晚 20:00 之前发现，而不是等那一刻
    # 静默崩掉、平台从此不再进化。
    # 正式运行不传这个参数，窗口仍是 WINDOW_DAYS=730。
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=WINDOW_DAYS,
                    help=f"进化窗口天数（默认 {WINDOW_DAYS}）。"
                         f"小于约 120 时测试段装不下 3 个调仓周期，"
                         f"K≥21 的候选会被 race 排除——冒烟测用，别拿它的结论")
    args = ap.parse_args()
    window_days = int(args.days)

    data_root = ROOT / "data"
    reports_dir = data_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    store = LocalStore(data_root)
    setup_logging(data_root)

    files = sorted((data_root / "master" / "daily").glob("*.parquet"))
    if not files:
        print("主库为空，跳过")
        return
    end = pd.read_parquet(files[0])["date"].max()
    start = (pd.Timestamp(end) - pd.Timedelta(days=window_days)).strftime("%Y-%m-%d")
    print(f"进化窗口：{start} ~ {end}（{window_days} 天，entry_lag={ENTRY_LAG}）", flush=True)
    if window_days != WINDOW_DAYS:
        print(f"⚠️ 非正式窗口（{window_days} 天 ≠ {WINDOW_DAYS}）——冒烟测，结论无效",
              flush=True)

    with RUN("evolve_weekly", window=[start, end], entry_lag=ENTRY_LAG,
             candidates=len(candidate_weights(load_weights(reports_dir)["weights"]))) as run:
        t0 = time.time()
        with run.step("载入日线与因子表"):
            daily_map = {p.stem: store.read_daily(p.stem) for p in files}
            factors_map = load_factors_map(store)
        run.log("数据载入完成", stocks=len(daily_map), elapsed_s=round(time.time() - t0))
        print(f"日线 {len(daily_map)} 只 + 因子表载入完成（{time.time()-t0:.0f}s）", flush=True)

        current = load_weights(reports_dir)
        champion_weights = current["weights"]

        with run.step("横截面预计算"):
            xs = build_cross_sections(store, start, end, daily_map=daily_map,
                                      factors_map=factors_map, entry_lag=ENTRY_LAG)
        run.log("面板完成", days=len(xs["_dates"]))
        print(f"横截面预计算 {len(xs['_dates'])} 个交易日 → {time.time()-t0:.0f}s", flush=True)

        # ── 第一轮：内置候选 ──
        cands = candidate_weights(champion_weights)
        evaluate = _evaluate_factory(xs, 10, 10)
        with run.step("内置候选赛马", n=len(cands)):
            r = race(xs, cands, evaluate, top_n=10, bottom_n=10)
            if r.get("error"):
                run.log("赛马失败", level="error", error=r["error"])
                print(f"赛马失败：{r['error']}")
                return
            for row in r["table"]:
                row["source"] = "builtin"

        # ── 研究员候选：默认 claude -p（能自己读进化日志），直连链兜底 ──
        with run.step("研究员候选"):
            from aplus.evolve_researcher import research_candidates
            from aplus.llm import ClaudePClient, default_llm_chain, load_secrets

            researcher_mode = load_secrets(Path.home() / ".aplus" / "llm.json").get(
                "researcher", "claude-p")
            claude_runner = ClaudePClient(str(ROOT)).chat if researcher_mode == "claude-p" else None

            champ_row = next((x for x in r["table"]
                              if candidate_key({"weights": x["weights"],
                                                "rebalance_every": x.get("rebalance_every", 1),
                                                "buffer_multiple": x.get("buffer_multiple")})
                              == candidate_key(champion_weights)), None)
            best_before = r["table"][0]
            evidence = {
                "current_weights": champion_weights,
                "baseline_stats": (champ_row or best_before)["test"]["stats"],
                "best_challenger": {"weights": best_before["weights"],
                                    "rebalance_every": best_before.get("rebalance_every", 1),
                                    "buffer_multiple": best_before.get("buffer_multiple"),
                                    "stats": best_before["train"]["stats"]},
                "market_summary": f"回测窗口 {start}~{end}；训练段/测试段切分见 protocol",
                "log_path": str(reports_dir / "evolution_log.json"),
            }
            researcher_raw = research_candidates(evidence, claude_runner=claude_runner,
                                                 direct=default_llm_chain())
            # 研究员候选**自带执行参数**（`weights` + `rebalance_every` +
            # `buffer_multiple`），原样带频率进赛马，不再强制日频。
            #
            # 去重键必须看**三元组**，且必须从赛马行里取回真实的频率与缓冲——
            # 旧代码写 `candidate_key(x["weights"])`，把行里的 `rebalance_every`
            # 丢掉了，于是 seen_keys 只收录每组权重的 K=1 变体，
            # 同一组权重的 K=63 行不在里面，
            # 研究员提 `{mom20:-1} K=63` 就会被判成「没重复」而跑第二遍。
            seen_keys = {candidate_key({"weights": x["weights"],
                                        "rebalance_every": x.get("rebalance_every", 1),
                                        "buffer_multiple": x.get("buffer_multiple")})
                         for x in r["table"]}
            researcher_cands = [c for c in researcher_raw
                                if candidate_key(c) not in seen_keys]
            # 上限 6 个三元组：研究员通常提 1-3 组权重，但同一组权重的
            # 两个频率是**两个候选**，只截 3 会把频率轴上的差异砍掉。
            # 代价是 Bonferroni 比较次数上升、显著性门槛抬高——
            # 那是诚实的代价，不该靠少报候选来掩盖。
            RACED_CAP = 6
            print(f"\n研究员提出 {len(researcher_raw)} 组候选"
                  f"（去重后 {len(researcher_cands)} 组进入赛马，"
                  f"最多取 {RACED_CAP} 组）", flush=True)
            run.log("研究员候选", proposed=len(researcher_raw),
                    deduped=len(researcher_cands),
                    raced=min(len(researcher_cands), RACED_CAP))

            if researcher_cands:
                extra = race(xs, researcher_cands[:RACED_CAP], evaluate,
                             top_n=10, bottom_n=10)
                for row in extra.get("table") or []:
                    row["source"] = "researcher"
                r["table"] = sorted(r["table"] + extra.get("table", []),
                                    key=lambda x: x["train"]["objective"], reverse=True)

        print(f"\n训练段 {r['train_dates'][0]} ~ {r['train_dates'][-1]}（{len(r['train_dates'])} 日）")
        print(f"测试段 {r['test_dates'][0]} ~ {r['test_dates'][-1]}（{len(r['test_dates'])} 日）\n")
        print(f"{'排名':<4}{'K':>4}{'缓冲':>6}{'训练目标':>9}{'测试目标':>9}"
              f"{'训练超额':>10}{'测试超额':>10}{'测试t':>8}{'有效观测':>9}  权重")
        print("─" * 118)
        champ_key = candidate_key(champion_weights)
        for i, row in enumerate(r["table"], 1):
            tr, te = row["train"], row["test"]
            key = candidate_key({"weights": row["weights"],
                                 "rebalance_every": row.get("rebalance_every", 1),
                                 "buffer_multiple": row.get("buffer_multiple")})
            mark = " ★现任" if key == champ_key else ""
            t = te["stats"].get("excess_t")
            print(f"{i:<4}{row.get('rebalance_every', 1):>4}"
                  f"{str(row.get('buffer_multiple')):>6}"
                  f"{tr['objective']:>9.3f}{te['objective']:>9.3f}"
                  f"{tr['stats'].get('up_excess_vs_market', 0):>10.2%}"
                  f"{te['stats'].get('up_excess_vs_market', 0):>10.2%}"
                  f"{(f'{t:+.2f}' if t is not None else '—'):>8}"
                  f"{te['stats'].get('excess_days', 0):>9}  {row['weights']}{mark}")
        if r.get("rejected"):
            print(f"\n（{len(r['rejected'])} 组候选因测试段装不下调仓周期被排除，未参与排名）")

        with run.step("裁决"):
            dec = decide_promotion(r, champion_weights)
        run.log("裁决", promoted=bool(dec.get("promote")), reason=dec.get("reason", ""))
        print(f"\n【裁决】{'✅ 晋级' if dec.get('promote') else '⏸️ 维持现任'}\n{dec.get('reason')}")

        today = date.today().isoformat()
        promoted_w = dec["best"]["weights"] if dec.get("promote") else champion_weights

        if window_days != WINDOW_DAYS:
            # **冒烟测不落盘**：一个 120 天窗口的裁决写进 evolution_log.json /
            # backtest_summary.json 之后，判决板会把它的数字当成一次真实赛马展示。
            # 那些数字没有任何判决权（测试段装不下 3 个调仓周期），
            # 而报告上看不出来——这正是「安静的错误输出」。
            print(f"\n[smoke] 非正式窗口（{window_days} 天）——"
                  f"**不写 weights.json / evolution_log.json / 回测基线**。"
                  f"本次运行只验证链路能跑通。")
            run.log("冒烟测", window_days=window_days, wrote=False)
            return

        with run.step("落盘权重与回测基线"):
            if dec.get("promote"):
                # **执行参数必须一起落盘**（2026-10-14 修复）。
                # 漏传的后果不是"配置丢了"，而是「闸门检验的是 K=21，
                # 上线跑的是 K=1」——同一组权重净 +77.6% vs −168.9%。
                promoted_exec = execution_of(dec["best"])
                save_weights(reports_dir, promoted_w, {
                    "promoted_on": today,
                    "objective": dec["best"]["train"]["objective"],
                    "reason": dec.get("reason"),
                    "window": [start, end],
                    "protocol": "oos train/test split + embargo",
                    "entry_lag": ENTRY_LAG,
                    "train": dec["best"]["train"],
                    "test": dec["best"]["test"],
                }, execution=promoted_exec)
                print(f"  已写入 weights.json：{promoted_w}"
                      f"  执行 K={promoted_exec['rebalance_every']}"
                      f" 缓冲={promoted_exec['buffer_multiple']}")

            # 回测基线：始终用**现任**权重（= 线上真实配置）刷新，前端 /api/backtest 读它。
            # 口径必须与刚落盘的配置**逐字一致**——展示 K=21 数字而实盘跑 K=1，
            # 是最难排查的那种错：两个数字都"对"，只是说的不是同一件事。
            champ_exec = execution_of(dec["best"]) if dec.get("promote") \
                else (load_weights(reports_dir).get("execution") or {})
            final_res = evaluate_weights(
                xs, promoted_w, top_n=10, bottom_n=10,
                rebalance_every=champ_exec["rebalance_every"],
                buffer_multiple=champ_exec["buffer_multiple"])
            final_stats = rolling_stats(final_res)
            final_res.to_parquet(reports_dir / "backtest.parquet", index=False)
            summary_path = reports_dir / "backtest_summary.json"
            summary_path.write_text(
                json.dumps({**final_stats, "window": [start, end],
                            "entry_lag": ENTRY_LAG, "rows": len(final_res)},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            # **盖溯源章。** 这份报告的数字会被判决板当赛马展示、被人引用，
            # 而 2026-10-14 正是我引用了一份 14:02 被修掉的逻辑算出来的
            # 11:45 报告，把「信号符号反了」当结论讲了出去。
            # 没有章，就无从判断一份数字是不是早于某次修复。
            stamp(summary_path, "aplus/backtest.py", root=ROOT,
                  extra={"window": [start, end], "rows": len(final_res)})

            rec = build_log_record(r, today, [start, end], champion_weights,
                                   proposed=len(researcher_raw),
                                   raced=min(len(researcher_cands), 3))
            log_path = reports_dir / "evolution_log.json"
            log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
            log.append(rec)
            log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")
        run.log("落盘完成", promoted=bool(dec.get("promote")), log_entries=len(log))
        print(f"进化日志已追加（{len(log)} 条记录）· 总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()