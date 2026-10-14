"""过闸候选的逐块分解：超额是不是靠一两个块撑起来的。

## 为什么必须查这个

`{mom20:-1.0}` K=63 在 7.8 年赛马里**首次过闸**：

    测试超额 +0.1196%/日   t=+4.14（>3.47，97 次 Bonferroni）
    成本占比 4.3%

但它只有 **11 个独立观测**（测试段 3.9 年 ÷ 63）。
11 个观测上的 t 值对**个别块**极其敏感——只要两块特别好，
其余九块平平无奇，t 一样能过显著性闸。

**这不是理论担忧，是这套平台已经踩过的形状**：`breadth_tstat`
在低频下逐日 t 虚高 1.0~2.0 倍，正是因为观测单位错了。
这里是同一个问题的另一个面：观测单位对了，但**数量太少**。

## 查什么

1. **逐块列出全部超额**——不摘要，要能一眼看出分布
2. **正块占比**——11 块里几块是正的
3. **去掉最好的一块之后 t 还剩多少**——最关键的一问。
   如果去掉一块就掉到闸门以下，那这个 t 是运气，不是边际
4. **中位数**——比均值更抗离群
5. 涨腿命中率——用户要的是「10 涨 10 跌」，命中率低于 50%
   意味着赢在幅度而不是频率，这件事必须摆到台面上

## 为什么三条腿都要查（2026-10-14 补）

第一版只查涨腿。补上跌腿是因为**用户要的是「10 涨 10 跌」，
跌腿是半个产品**，而闸门里 `excess_t` 只测涨腿。

实测结果（2026-10-14，7.8 年 / 测试段 753 日 / 97 组候选池）：

| | K=63（11 块） | K=21（35 块） |
|---|---|---|
| 涨腿 t | **+4.757**，正块 **11/11**，去掉最好块反升到 5.425 | +4.358，正块 27/35 |
| 跌腿（相对大盘）t | −0.865，正块 4/11 | +0.415，正块 19/35 |
| 广度 t | **+1.005** | +2.035 |

两个预判都被推翻了，必须记下来：

1. **涨腿不是「一两块撑起来的」**。K=63 是 11/11 块全正，去掉最好
   一块 t 反而**升到 5.425**——分母变小、标准差也变小，这个 t 是实的。
   第一版担心「11 个观测对个别块太敏感」，实测不成立。
2. **「跌腿接近噪声」只在相对大盘的意义上成立**。跌腿命中率
   52.8%，而 97/97 组的 `hit_rate_down` 都 > 50%——**方向性对所有候选
   都成立，方差极小**。真正没判别力的是「跑赢大盘」（仅 45/97 跑赢）。

所以跌腿闸的判据最后选了**绝对下跌**（`avg_down_return < 0`）而不是
「跑输大盘」：5 万本金做不了空，用户要证的是「这 10 只别买」。
按「跑输大盘」设闸会否掉 52/97 的典型候选——否掉多数的闸没有判别力。

而广度 t 在两个频率上都够不到 Bonferroni 临界 3.47：**本次显著性
由涨腿独撑**。这一条现在会写进晋级理由（不否决，但要说出来）。

## 为什么同时跑 K=21

K=21 在同一测试段有 **35 个块**，统计上厚得多。
如果 K=21 的分布也是正的，K=63 的结论就不只是「11 块的运气」。

用法：
    .venv/bin/python scripts/candidate_block_audit.py \
        --data-root /tmp/aplus_snap_1012
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus.backtest import ENTRY_LAG, build_cross_sections, evaluate_weights
from aplus.evolve_race import (_block_means, _daily_series, _tstat,
                                breadth_tstat, down_excess_tstat,
                                excess_tstat, split_window)
from aplus.snapshot import load_factors_map
from aplus.store import LocalStore
from aplus.verify import rolling_stats

ROOT = Path(__file__).resolve().parent.parent

CANDIDATE = {"mom20": -1.0}
FREQS = (63, 21)


def leg_blocks(res, direction: str, every: int, negate: bool = False) -> np.ndarray:
    """按调仓周期切块后的单腿超额序列（每块 = 一次独立决策）。

    **直接复用 `_daily_series` + `_block_means`**，不在这里重推口径。
    这不是洁癖：本脚本的全部价值就是「这个 t 到底靠不靠得住」，
    所以它算出的 t 必须**逐位等于**赛马里报的那个 t。
    一旦自己再实现一遍，就会漏掉 `_daily_series` 的 `MIN_BASKET` 护栏
    ——成分不足的日子混进分母，那天的篮子超额其实是单只股票的运气
    （2026-06-04 实测把多空价差顶到 +9.84%）。
    自推口径的审计，会审计出一个跟被审计对象不是同一个数的东西。
    """
    daily = _daily_series(res, direction, negate=negate)
    if daily is None:
        return np.empty(0)
    return np.asarray(_block_means(daily, every), dtype=float)


def breadth_blocks(res, every: int) -> np.ndarray:
    """多空价差的逐块序列。

    **符号必须是 `up + down`**：`down` 那一路取自 `_daily_series(negate=True)`，
    已经被取过反了，它本身就是「回避超额」（正 = 成功避开跌股）。
    所以两端相加才是价差——写成 `up - down` 会把回避超额再加一次，
    实测直接造出 t=2.965 对 t=1.005 的假差异（2026-10-14 自检当场抓到）。

    拼接口径照抄 `breadth_tstat`：同一份 concat + dropna，
    少一天观测不是「保守」，是「两个数不再可比」。
    """
    up = _daily_series(res, "up")
    down = _daily_series(res, "down", negate=True)
    if up is None or down is None:
        return np.empty(0)
    joined = pd.concat([up.rename("up"), down.rename("down")], axis=1).dropna()
    if joined.empty:
        return np.empty(0)
    return np.asarray(_block_means(joined["up"] + joined["down"], every),
                      dtype=float)


def audit(blocks: np.ndarray, label: str) -> dict:
    if len(blocks) == 0:
        return {"label": label, "n_blocks": 0}
    t_all, _ = _tstat(pd.Series(blocks), block=1)
    best = int(np.argmax(blocks))
    # 去掉最好的那一块——问「边际是不是全压在它身上」
    trimmed = np.delete(blocks, best)
    t_trim, _ = _tstat(pd.Series(trimmed), block=1)
    worst = int(np.argmin(blocks))
    t_dropworst, _ = _tstat(pd.Series(np.delete(blocks, worst)), block=1)
    return {
        "label": label,
        "n_blocks": int(len(blocks)),
        "mean": round(float(blocks.mean()), 6),
        "median": round(float(np.median(blocks)), 6),
        "t": None if t_all is None else round(float(t_all), 3),
        "n_positive": int((blocks > 0).sum()),
        "best_block": round(float(blocks[best]), 6),
        "worst_block": round(float(blocks[worst]), 6),
        "t_without_best": None if t_trim is None else round(float(t_trim), 3),
        "t_without_worst": (None if t_dropworst is None
                            else round(float(t_dropworst), 3)),
        "blocks_pct": [round(float(b) * 100, 4) for b in blocks],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="")
    ap.add_argument("--data-root", type=Path, default=ROOT / "data")
    ap.add_argument("--out", default="candidate_block_audit.json")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    if not data_root.is_dir():
        raise SystemExit(f"数据根目录不存在：{data_root}")
    store = LocalStore(data_root)
    reports = data_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    files = sorted((store.root / "master" / "daily").glob("*.parquet"))
    end = args.end or str(pd.read_parquet(files[0])["date"].max())

    t0 = time.time()
    print(f"载入日线 {args.start} ~ {end} …", flush=True)
    daily_map = {p.stem: store.read_daily(p.stem) for p in files}
    factors_map = load_factors_map(store)
    xs = build_cross_sections(store, args.start, end, daily_map=daily_map,
                              factors_map=factors_map, entry_lag=ENTRY_LAG)
    _train, test_dates = split_window(xs["_dates"])
    print(f"横截面 {len(xs['_dates'])} 日 · 测试段 {test_dates[0]} ~ {test_dates[-1]}"
          f"（{len(test_dates)} 日，{time.time()-t0:.0f}s）\n", flush=True)

    out_rows = []
    for every in FREQS:
        res = evaluate_weights(xs, CANDIDATE, top_n=10, bottom_n=10,
                               dates=test_dates, rebalance_every=every)
        st = dict(rolling_stats(res))

        legs = {
            "up": audit(leg_blocks(res, "up", every), "涨腿超额"),
            "down": audit(leg_blocks(res, "down", every, negate=True),
                          "跌腿回避"),
            "breadth": audit(breadth_blocks(res, every), "多空价差"),
        }

        # 口径自检：三条腿的 t/观测数必须与赛马里报的**逐位一致**。
        # 不一致说明「被审计的数」和「赛马报的数」已经是两个东西，
        # 这时审计结论全部作废——必须当场停下，不能带着不一致继续跑。
        upstream = {"up": excess_tstat(res, every),
                    "down": down_excess_tstat(res, every),
                    "breadth": breadth_tstat(res, every)}
        for name, (u_t, u_n) in upstream.items():
            a = legs[name]
            if a.get("n_blocks", 0) != u_n:
                raise SystemExit(
                    f"观测数不一致：K={every} {name} 审计 {a.get('n_blocks')} 块，"
                    f"赛马 {u_n} 块。")
            if a.get("t") != (None if u_t is None else round(float(u_t), 3)):
                raise SystemExit(
                    f"口径漂移：K={every} {name} 审计 t={a.get('t')}，"
                    f"赛马 t={u_t}。审计与赛马已不是同一条路径。")

        row = {
            "rebalance_every": every,
            "weights": CANDIDATE,
            "legs": legs,
            "t_matches_race": True,
            "hit_rate_up": st.get("hit_rate_up"),
            "hit_rate_down": st.get("hit_rate_down"),
            "overall_hit_rate": st.get("overall_hit_rate"),
            "cost_share": st.get("cost_share"),
        }
        out_rows.append(row)

        print(f"=== {CANDIDATE} K={every} ===")
        for name, a in legs.items():
            if not a.get("n_blocks"):
                print(f"  [{name}] 无观测")
                continue
            print(f"  [{name}] {a['n_blocks']} 块 · 均值 {a['mean']*100:+.4f}%/日 · "
                  f"中位数 {a['median']*100:+.4f}%/日 · t={a['t']} · "
                  f"正块 {a['n_positive']}/{a['n_blocks']}")
            print(f"         最好 {a['best_block']*100:+.4f}% · "
                  f"最差 {a['worst_block']*100:+.4f}% · "
                  f"去掉最好块 t={a['t_without_best']} · "
                  f"去掉最差块 t={a['t_without_worst']}")
            print(f"         逐块（%）：{', '.join(f'{x:+.3f}' for x in a['blocks_pct'][:24])}")
            if len(a["blocks_pct"]) > 24:
                print(f"                   … 共 {len(a['blocks_pct'])} 块")
        print(f"  涨腿命中率 {row['hit_rate_up']} · 跌腿命中率 {row['hit_rate_down']}"
              f" · 成本占比 {row['cost_share']}\n")

    out = {"kind": "candidate_block_audit",
           "candidate": CANDIDATE,
           "window": [args.start, end],
           "test_dates": [test_dates[0], test_dates[-1]],
           "rows": out_rows, "elapsed_s": round(time.time() - t0)}
    path = reports / args.out
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    print(f"→ {path}  总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())