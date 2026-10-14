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

import pandas as pd  # noqa: E402

from aplus.backtest import _next_bars, composite_score  # noqa: E402
from aplus.evolve_race import DEGENERATE_CV as _DEGENERATE_CV  # noqa: E402

DAYS_PER_YEAR = 244

SIGN_FIXED = {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5}
WRONG_SIGN = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
PAIRS = [("符号修正", SIGN_FIXED), ("错号对照", WRONG_SIGN)]

BUCKET = 10          # 每桶几只
N_RANKS = 150        # 看前多少名 → 15 个桶

PREREG = {
    "primary_metric": "桶0逐日毛收益均值（绝对值，非相对值）",
    "head_inverted_if": "桶0 的逐日均值小于桶 1..14 里的每一个",
    "monotone_test": "桶 k 逐日均值随 k 严格递减",
    "better_bucket_test": "桶 k − 桶0 的逐日配对 t ≥ 3.0",
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


def probe(xs: dict, dates: list, weights: dict, label: str) -> dict:
    """对单个权重配置做一次分桶扫描。返回逐日逐桶的毛收益矩阵。"""
    daily_map = xs.get("_daily_map") or {}
    entry_lag = xs.get("_entry_lag", 1)
    factors_map = xs.get("_factors_map") or {}
    adj_cache = xs.setdefault("_adj_cache", {})

    rows = []
    for day in dates:
        universe = xs.get(day)
        if universe is None or universe.empty:
            continue
        sc = composite_score(universe, weights)
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
        for rank, code in enumerate(codes):
            r = m.get(code)
            if r is None or pd.isna(r):
                continue          # 买不进 / 停牌 / 未上市——不许猜，缺就是缺
            rows.append({"pred_date": day, "bucket": bucket_of(rank), "actual_pct": r})

    df = pd.DataFrame(rows)
    if df.empty:
        return {"kind": label, "n_days": 0, "daily": pd.DataFrame()}
    return {"kind": label, "n_days": int(df["pred_date"].nunique()),
            "n_obs": len(df), "daily": df.groupby(["pred_date", "bucket"])["actual_pct"].mean().unstack()}


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

        print(f"\n  头部反了（桶0 低于其余每一桶）: {head_inverted}")
        print(f"  严格单调递减: {monotone}")
        print(f"  显著优于桶0 的桶（配对 t ≥ 3.0）: "
              f"{[(x['bucket'], round(x['t'], 2)) for x in better] or '无'}")
        if base is not None and len(base.dropna()):
            mk = pd.Series({d: mkt.get(d, 0.0) for d in daily.index}).reindex(daily.index)
            tm = _paired_t(daily[0], mk)
            print(f"  桶0 相对大盘配对 t = {tm if tm is None else round(tm, 2)}"
                  f"（仅供参考，判定不依赖它）")

        out["configs"][label] = {
            "n_days": r["n_days"], "n_obs": r["n_obs"],
            "annual_mean_by_bucket": {str(k): float(v) for k, v in vals.items()},
            "final_by_bucket": {str(k): v for k, v in finals.items()},
            "head_inverted": head_inverted, "monotone_decreasing": monotone,
            "better_than_head": better,
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())