"""排名分桶探针——回答「头部是不是反的」。

## 起因：一个不需要基准就能算出来的结论

`lowfreq_plausibility.json` 的复利终值 + 修正后的成本，两者相减可以**反解出
毛收益**（净值 = 毛收益 − 成本，成本已知），于是：

    K    净终值    日净      年成本    反解毛日收益
    1    0.114×   −0.179%   37.04%     −0.027%  （−6.6%/年）
    21   0.602×   −0.042%    3.51%     −0.028%  （−6.8%/年）
    63   0.407×   −0.074%    1.02%     −0.069%  （−16.8%/年）

**毛收益在每一个频率上都是负的。** 涨腿篮子（榜宽 10）在扣任何成本之前
就在亏钱。所以「成本太高」这个说法是错的——那是第二层。**榜宽再宽也救不
了一个毛收益为负的篮子**，因为榜宽只压成本，不改选股。

## 这与 IC 同号矛盾

`factor_ic_7y` 说符号修正权重的逐因子符号 63/63 与全段 IC 同号。若全截面
IC 为正而 top-10 毛收益为负，只有一种解释：

    **单调性在极端头部断掉了。**

最可能的具体机制：A 股里「反转最强」的名字大量是 ST、微盘、壳资源股。
IC 测的是**全截面**的平均单调性（rank IC），它完全可能被中段主导；
而 top-10 是**极端尾部**，被最脏的一小撮占据。**rank IC 为正与 top 十分位
巨亏可以同时为真**——这两个结论测的不是一个东西。

## 本脚本测什么

对每个交易日，把可排名股票按 composite_score 降序排，取前 `N_RANKS` 只，
按**排名**分桶（每桶 `BUCKET` 只），算每桶的逐日前瞻收益均值。

    桶 0 = 排名 1..10   （策略真正买的那 10 只）
    桶 1 = 排名 11..20
    ...

然后回答三个问题：

1. **桶 0 的毛收益是不是全场最差？** 是 → 头部反了，别做头部。
2. **单调吗？** 不单调 → rank IC 不能代表可交易的那一段。
3. **有没有某个桶显著好于桶 0？** 有 → 那才是该做的榜段。

## 为什么这个测量是干净的

- **不含市场项。** 每桶的数就是它自己的逐日收益，不需要 `_market` 参与
  判定（基准只用来说明「相对大盘」，不参与达标）。
- **成本不参与。** 这是毛收益探针，成本由 `basket_width_sweep` 负责。
- **不用历史最优权重。** 权重是现役的 `SIGN_FIXED`，本脚本不做任何选择。
- **不做晋级判定，不写 weights.json。**

## 已知硬边界（写在这里，不藏）

本探针测的是 **1 日前瞻**（`entry_lag=1`，与 `actual_pct` 同一口径）。
策略实际按 K 日调仓持有，K>1 时排名与收益的关系会变（衰减）。所以：

> 本探针能回答「横截面头部在 1 日尺度上是否反了」，
> **不能**回答「K=21 时排名头部是否反了」。

后者需要按 K 日前瞻收益重算，那是另一份脚本的事。若本探针显示头部反了，
K 日版本几乎肯定也反（反转效应的衰减方向与本项目观察到的信号衰减一致），
但**这是推断不是实测**，实测出来之前不得当成结论用。

## 事前登记（跑之前定，不许事后改）

- **主指标**：桶 0 的逐日毛收益均值（**不是**相对大盘，不是任何比值）
- **判据**：
  - 「头部反了」= 桶 0 的逐日均值 **小于桶 1..14 里的每一个**
  - 「单调」= 桶 k 的逐日均值对 k 严格递减（配对 t ≤ −3.0 视为反了）
  - 「存在更优桶」= 某桶 k 的逐日均值 − 桶 0 的逐日均值，配对 t ≥ 3.0
- 任一「存在更优桶」成立即是一个**可执行的候选榜段**，但仍须
  ① 到 2024-01~2026-10 复测，② 与 `lowfreq_plausibility` 的成本对照后才算结果。
"""
import argparse
import json
import math
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from aplus.backtest import _next_bars, composite_score  # noqa: E402
from aplus.evolve_race import DEGENERATE_CV as _DEGENERATE_CV  # noqa: E402

DAYS_PER_YEAR = 244

SIGN_FIXED = {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5}
WRONG_SIGN = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
# 第三臂：**随机分数**。`None` = 完全不排因子，改为固定种子打乱。
#
# 为什么非有它不可（2026-10-14 第一版跑完才想清楚的）：
# 首版只跑符号修正 + 错号对照，两臂**都**显示「桶0 低于其余每一桶」。
# 两个方向相反的因子给出同一种形状，最省事的解释不是「这个因子头部反了」，
# 而是「**横截面最极端的那一端本来就是差的**」——微盘、ST、壳、次新，
# 它们在任一排序里都占据顶端，与排序依据无关。
#
# 错号对照**不能**回答这个问题：错号分数与「是坏股票」高度相关，
# 它不是中性打乱，是一个**已知很差**的排序。真正中性的是随机分数：
# 同样的股票池、任意顺序、零因子信息。
#
#     随机对照头部也最差 → 普遍结构事实 → 修 universe（流动性/市值过滤），
#                          平移榜段无效
#     随机对照头部正常   → 因子特有       → 平移榜段可能有效，但仍需过 3.0 显著性
#
# 两条路的修法**方向相反**，选错的代价是几个月白干。所以这个对照必须先跑。
RANDOM_SEED = 20261014
PAIRS = [("符号修正", SIGN_FIXED), ("错号对照", WRONG_SIGN), ("随机对照", None)]

BUCKET = 10          # 每桶几只
N_RANKS = 150        # 看前多少名 → 15 个桶

PREREG = {
    "primary_metric": "桶0逐日毛收益均值（绝对值，非相对值）",
    "head_inverted_if": "桶0 的逐日均值小于桶 1..14 里的每一个",
    "monotone_test": "桶 k 逐日均值随 k 严格递减",
    "better_bucket_test": "桶 k − 桶0 的逐日配对 t ≥ 3.0",
    "three_way_fork": {
        "问": "头部反了是这组因子的性质，还是 A 股横截面排序的普遍结构事实？",
        "随机对照头部也最差": "普遍结构事实 → 修 universe（流动性/市值/流动性过滤），平移榜段无效",
        "随机对照头部正常": "因子特有 → 平移榜段值得试，但未过 3.0 显著性前仍不算结论",
        "为何必须先判这条": "两条路的修法方向相反，选错要付几个月的代价",
    },
    "multiplicity_note": (
        "15 个桶 = 15 次比较。纯零假设下最大 |配对 t| 的期望就在 2.5~2.7，"
        "因此单桶 t=2.7 **不构成证据**，必须过 3.0 且看形状而非只看峰值"),
    "hard_boundary": ("本探针只测 1 日前瞻；K>1 的排名-收益关系未实测，"
                      "任何 K>1 的推断都不得当作结论"),
    "confirm_required": "候选榜段必须到 2024-01~2026-10 复测，并与成本对照后才算结果",
}


def bucket_of(rank: int, size: int = BUCKET) -> int:
    """排名（从 0 起）→ 桶号（从 0 起）。

    纯函数、可单测。刻意不写成 `rank // size` 内联——分桶边界是本脚本
    全部结论的坐标系，它一旦被改，历史上所有「头部反了」的判断都要重看，
    所以它必须有一个自己独立的名字和测试。
    """
    return rank // size


# 配对差值被判为**退化**的阈值——直接复用生产常量，不另立一套。
#
# 判据是变异系数而非 `sd <= 0`：两个逐日完全同步的序列做差得到**常数**，
# 而常数的 `std(ddof=1)` 在浮点下是 ~1e-19 而不是精确 0，于是 `sd <= 0`
# 永远放行，`t = 均值 / (1e-19 / √n)` 吐出 ±1e16 量级的值——能穿过
# **任何**显著性阈值。这不是假想：`aplus/evolve_race.py:234` 记着生产
# 代码踩过同一个坑（实测 `t = 3.1e16`），而我在本脚本第一版里
# **又犯了一次**，由 `tests/test_head_bucket_probe.py` 当场抓到。
#
# 两次同型事故说明：**文档里写「必须返回 None」不等于护栏落地。**
# 所以这里不重写规则，直接 import 生产那一份常量——一处定义，一次修正。
DEGENERATE_CV = _DEGENERATE_CV


def _paired_t(a: pd.Series, b: pd.Series) -> float | None:
    """逐日配对 t：`a − b` 的均值 / 标准误。不是两个独立 t 值。

    分母退化时返回 `None`——**宁可缺证据，不可造证据**。一个凭空出现的
    `t = 3.5e16` 比「没有 t 值」危险得多，因为它不会被任何人质疑。
    """
    d = (a - b).dropna()
    if len(d) < 3:
        return None
    mu = float(d.mean())
    sd = float(d.std(ddof=1))
    if not math.isfinite(sd) or sd <= 0 or sd <= abs(mu) * DEGENERATE_CV:
        return None
    se = sd / math.sqrt(len(d))
    if not (se > 0):
        return None
    return float(mu / se)


def _score(universe: pd.DataFrame, weights: dict | None, rng) -> pd.Series:
    """横截面分数。`weights is None` → 固定种子的随机分数（零因子信息）。

    单独抽出来是因为它决定了「随机对照」这根对照柱到底是不是**中性**的：
    随机必须**每次跑都一样**（同一个种子），否则两次结果对不上时无法区分
    「结论不稳」与「重跑换了随机数」。有测试钉住这条。
    """
    if weights is None:
        return pd.Series(rng.random(len(universe)), index=universe.index)
    return composite_score(universe, weights)


def head_vs_rest(daily: pd.DataFrame) -> tuple[float | None, float, float]:
    """桶0 对「桶1..末」**合并**的配对 t，以及两者年均。

    ## 为什么必须是「合并」而不是逐桶比

    首版的主判据 `head_inverted` 是「桶0 低于其余**每一个**桶」。那是一个
    **15 选 1** 的单次比较——零假设下概率就是 1/15。对同一批数，
    我在读桶2 的 t=2.69 时明确说过「15 个桶 = 15 次比较，2.69 是噪声该给
    的数」，然后转头用**一次没做多重比较修正**的布尔判据宣布头部反了，
    还据此把榜宽扫描扩到 200。**同一套判据，两个方向，两种标准。**

    把桶1..末合并成一个序列，比较就只剩**一次**：2 选 1，没有多重比较。
    代价是丢掉了「哪一段更好」的信息——但那段信息本来就不该用来下结论
    （实测每桶标准误约 10.9%/年，桶间差异全在 1 SE 内）。

    返回 `(配对 t, 桶0年均, 其余合并年均)`；样本不足返回 `(None, ...)`。
    """
    if 0 not in daily.columns:
        return None, float("nan"), float("nan")
    head = daily[0].dropna()
    rest = daily[[c for c in daily.columns if c != 0]].mean(axis=1).dropna()
    d = pd.concat([head, rest], axis=1, keys=["h", "r"]).dropna()
    if len(d) < 3:
        return None, float(head.mean()) * DAYS_PER_YEAR, float(rest.mean()) * DAYS_PER_YEAR
    diff = d["h"] - d["r"]
    return _paired_t(d["h"], d["r"]), float(d["h"].mean()) * DAYS_PER_YEAR, \
        float(d["r"].mean()) * DAYS_PER_YEAR


def probe(xs: dict, dates: list, weights: dict | None, label: str,
          seed: int = RANDOM_SEED) -> dict:
    """对单个权重配置做一次分桶扫描。返回逐日逐桶的毛收益矩阵。"""
    daily_map = xs.get("_daily_map") or {}
    entry_lag = xs.get("_entry_lag", 1)
    factors_map = xs.get("_factors_map") or {}
    adj_cache = xs.setdefault("_adj_cache", {})
    rng = np.random.default_rng(seed)

    rows = []
    for day in dates:
        universe = xs.get(day)
        if universe is None or universe.empty:
            continue
        sc = _score(universe, weights, rng)
        ranked = (universe.assign(_s=sc).dropna(subset=["_s"])
                  .sort_values("_s", ascending=False).head(N_RANKS))
        if ranked.empty:
            continue
        codes = list(ranked["code"])
        bars = _next_bars(daily_map, codes, day, entry_lag=entry_lag,
                          factors_map=factors_map, adj_cache=adj_cache,
                          directions={c: "up" for c in codes})
        if bars.empty:
            continue
        bars["actual_pct"] = bars["close"] / bars["prev_close"] - 1.0
        m = dict(zip(bars["code"], bars["actual_pct"]))
        # 流动性诊断量：横截面已经带着 volume（成交股数）。它**不是**主指标，
        # 只用来回答「桶0 里装的到底是什么」。量级上与流动性同向，够用。
        vol = (dict(zip(universe["code"], universe["volume"]))
               if "volume" in universe.columns else {})
        for rank, code in enumerate(codes):
            r = m.get(code)
            if r is None or pd.isna(r):
                continue          # 买不进 / 停牌 / 未上市——不许猜，缺就是缺
            rows.append({"pred_date": day, "bucket": bucket_of(rank), "actual_pct": r,
                         "volume": vol.get(code)})

    df = pd.DataFrame(rows)
    if df.empty:
        return {"kind": label, "n_days": 0, "daily": pd.DataFrame()}
    med_vol = (df.groupby("bucket")["volume"].median().to_dict()
               if "volume" in df.columns else {})
    return {"kind": label, "n_days": int(df["pred_date"].nunique()),
            "n_obs": len(df), "median_volume": med_vol,
            "daily": df.groupby(["pred_date", "bucket"])["actual_pct"].mean().unstack()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("/tmp/reversal_panel_cache.pkl"))
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path, default=Path("data/reports/head_bucket_probe.json"))
    args = ap.parse_args()

    print("载入面板缓存…", flush=True)
    xs = pickle.loads(args.cache.read_bytes())
    dates = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)
    mkt = xs.get("_market") or {}
    print(f"面板 {len(xs['_dates'])} 日；本段 {len(dates)} 个交易日", flush=True)

    out = {"kind": "head_bucket_probe", "prereg": PREREG,
           "window": [args.start, args.end], "bucket_size": BUCKET,
           "n_ranks": N_RANKS, "configs": {}}

    for label, w in PAIRS:
        r = probe(xs, dates, w, label)
        daily = r["daily"]
        if daily.empty:
            print(f"{label}: 样本为空，跳过")
            continue
        means = daily.mean() * DAYS_PER_YEAR
        finals = {int(b): float((1.0 + daily[b].dropna()).cumprod().iloc[-1])
                  for b in daily.columns if len(daily[b].dropna())}
        base = daily[0] if 0 in daily.columns else None

        print(f"\n{'='*88}\n{label}   {r['n_days']} 日 / {r['n_obs']} 个观测"
              f"   每桶 {BUCKET} 只\n{'='*88}")
        print(f"{'桶':>4}{'排名':>10}{'年均毛收益':>12}{'复利终值':>10}{'vs桶0配对t':>12}")
        for b in sorted(daily.columns):
            t = _paired_t(daily[b], base) if b != 0 and base is not None else None
            rng = f"{int(b)*BUCKET+1}..{(int(b)+1)*BUCKET}"
            print(f"{int(b):>4}{rng:>10}{means[b]:>11.2%}{finals.get(int(b), float('nan')):>10.3f}"
                  f"{(t if t is not None else float('nan')):>12.2f}")

        vals = {int(b): float(means[b]) for b in daily.columns}
        others = [v for k, v in vals.items() if k != 0]
        head_inverted = bool(others) and all(means[0] < v for b, v in vals.items() if b != 0)
        monotone = all(vals[k] > vals[k + 1] for k in sorted(vals)[:-1])
        better = [{"bucket": k, "t": _paired_t(daily[k], base)}
                  for k in sorted(daily.columns) if k != 0]
        better = [x for x in better if x["t"] is not None and x["t"] >= 3.0]
        # 桶0 在全部桶里排第几低（1 = 最差）。布尔容易被单点异常骗，
        # 名次是连续量，能分清「差很多」与「差一点点」。
        head_rank = sorted(vals, key=lambda k: vals[k]).index(0) + 1 if 0 in vals else -1
        _ts = [x["t"] for x in [{"bucket": k, "t": _paired_t(daily[k], base)}
                                 for k in sorted(daily.columns) if k != 0]
               if x["t"] is not None]
        max_t = max(_ts) if _ts else float("nan")

        print(f"\n  头部反了（桶0 低于其余每一桶）: {head_inverted}")
        print(f"    ⚠ 这是 **15 选 1** 的单次比较，零假设下概率 1/15 ——"
              f"不得单独作为结论")
        hr_t, h_ann, r_ann = head_vs_rest(daily)
        print(f"  【正式判据】桶0 vs 桶1..末**合并**：")
        print(f"    桶0 年均 {h_ann:+.2%}   其余合并年均 {r_ann:+.2%}   "
              f"差 {r_ann - h_ann:+.2%}   配对 t = "
              f"{hr_t if hr_t is None else round(hr_t, 2)}"
              f"{'   ← 唯一一次比较，无多重比较' if hr_t is not None else ''}")
        print(f"  严格单调递减: {monotone}")
        print(f"  桶0 在 {len(vals)} 个桶里排第 {head_rank} 低")
        print(f"  显著优于桶0 的桶（配对 t ≥ 3.0）: "
              f"{[(x['bucket'], round(x['t'], 2)) for x in better] or '无'}"
              f"   全部桶最大 t = {max_t:.2f}")
        if base is not None and len(base.dropna()):
            mk = pd.Series({d: mkt.get(d, 0.0) for d in daily.index}).reindex(daily.index)
            tm = _paired_t(daily[0], mk)
            f0 = finals.get(0, float("nan"))
            # 特意并排打出：这两个数同时为真是本项目的常态。
            # 晋级闸4 读的是**相对**量（excess_t = up_excess − down_excess，
            # 市场项加倍而不抵消），「亏不亏」读的是**绝对**量（final_multiple）
            # ——两者方向可以完全相反，这里就是活的例子。
            print(f"  桶0 相对大盘配对 t = {tm if tm is None else round(tm, 2)}"
                  f"   而 桶0 绝对复利终值 = {f0:.3f}"
                  f"（{'跑赢大盘但自己亏钱' if tm and tm > 3 and f0 < 1 else ''}）")

        out["configs"][label] = {
            "n_days": r["n_days"], "n_obs": r["n_obs"],
            "annual_mean_by_bucket": {str(k): float(v) for k, v in vals.items()},
            "final_by_bucket": {str(k): v for k, v in finals.items()},
            "head_inverted": head_inverted, "monotone_decreasing": monotone,
            "head_rank_lowest": head_rank,
            "max_paired_t_vs_head": max_t,
            "better_than_head": better,
            "head_vs_rest_paired_t": hr_t,
            "head_annual": h_ann, "rest_annual": r_ann,
            "median_volume_by_bucket": {str(k): v for k, v in (r["median_volume"] or {}).items()},
        }

        # 流动性诊断：桶0 相对「最优桶」的成交股数倍数。
        #
        # 这是「头部是流动性假象」还是「头部是因子假象」的直接读数：
        #   倍数 < 1/3  → 桶0 是明显的微盘/低流动性簇，加 universe 门槛即可，
        #                  且**无需**平移榜段（平移只是换个方式选回同一批大票）
        #   倍数 ≈ 1    → 桶0 与最优桶流动性相当，问题在**因子极端方向**本身
        # 只有随机对照能区分「A 股横截面普遍如此」与「本因子特有」。
        mv = r["median_volume"] or {}
        if mv and 0 in mv:
            best_b = max(vals, key=lambda k: vals[k])
            v0, vb = mv.get(0), mv.get(best_b)
            if v0 and vb:
                ratio = float(v0) / float(vb)
                best_bk = int(best_b)
                print(f"  【诊断】桶0 中位成交股数 {v0:,.0f}；最优桶 桶{best_bk} {vb:,.0f}"
                      f"；比值 {ratio:.3f}"
                      f"  → {'桶0 显著更小更冷清：疑似流动性假象' if ratio < 1/3 else '量级相当，问题在因子方向本身'}")
                out["configs"][label]["head_vs_best_volume_ratio"] = ratio
                out["configs"][label]["best_bucket"] = best_bk

    # 三臂并排的最终判决。放在循环外，是因为这个问题的答案**由对照决定**，
    # 单看符号修正那一臂会得出相反的结论——这正是首版踩的坑。
    rnd = out["configs"].get("随机对照", {})
    sign = out["configs"].get("符号修正", {})
    if rnd:
        print(f"\n{'='*88}\n三臂判决：头部反了是**因子性质**还是**A 股横截面普遍事实**？\n{'='*88}")
        for nm, c in (("符号修正", sign), ("随机对照", rnd)):
            t = c.get("head_vs_rest_paired_t")
            print(f"  {nm:<6} 桶0 {c.get('head_annual', float('nan')):+.2%}"
                  f"   其余合并 {c.get('rest_annual', float('nan')):+.2%}"
                  f"   配对 t = {t if t is None else round(t, 2)}"
                  f"   （15选1布尔: 桶0排第 {c.get('head_rank_lowest')} 低）")
        # 判决只读**合并配对 t**。布尔 `head_inverted` 是 15 选 1，
        # 零假设下概率就有 1/15 —— 拿它下结论是本项目在 2026-10-14
        # 犯下的同一个错误（对桶2 的 t=2.69 做了多重比较修正，
        # 对主判据一次都没做）。
        rnd_t, sign_t = rnd.get("head_vs_rest_paired_t"), sign.get("head_vs_rest_paired_t")
        rnd_bad = rnd_t is not None and rnd_t < -3.0
        sign_bad = sign_t is not None and sign_t < -3.0
        if rnd_bad and sign_bad:
            verdict = ("**普遍结构事实** → 修 universe；平移榜段无效")
            out["fork"] = {"conclusion": "market_structure",
                           "random_t": rnd_t, "sign_t": sign_t,
                           "fix": "universe_filter(liquidity, size)",
                           "rank_shift": "无效，不要做"}
        elif rnd_bad and not sign_bad:
            verdict = ("随机排序的头部也显著更差，而因子排序没有 —— "
                       "说明**因子其实避开了**结构性问题。因子无用，"
                       "但不是因子有害")
            out["fork"] = {"conclusion": "factor_avoids_structure",
                           "random_t": rnd_t, "sign_t": sign_t,
                           "fix": "先修 universe 再谈因子"}
        else:
            verdict = ("两臂的「桶0 vs 其余合并」都未过 -3.0 —— "
                       "**桶级差异整体不显著**。此前基于分桶表做的任何"
                       "「毛收益平台期」「最优桶」推断全部作废，"
                       "包括把榜宽扫描扩到 200 这项决定")
            out["fork"] = {"conclusion": "inconclusive_all_noise",
                           "random_t": rnd_t, "sign_t": sign_t,
                           "action": "撤回基于分桶表的全部推断"}
        print(f"\n  → {verdict}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())