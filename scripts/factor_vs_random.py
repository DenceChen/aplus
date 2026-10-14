"""因子 vs 随机 —— 全项目最该先回答的一个问题。

## 为什么这个问题排在所有其他实验前面

`head_bucket_probe` 在 2019-01~2023-12（1154 日）测得涨腿毛收益：

    符号修正 top-10   -4.19%/年   复利终值 0.661
    随机对照 top-10   -5.33%/年   复利终值 0.681
    错号对照 top-10  -124.02%/年   复利终值 0.002

三个数字里，**因子的 top-10 和随机抽的 10 只差 1.14 个百分点**。
如果这个差是真实的，因子至少不是负价值；如果它是 0，那
「97 组权重搜索 + rank IC 定符号 + 三道晋级护栏」全都在一个
**没有价值的地方**搜索，而每多搜一次就多一次多重比较风险。

**这决定了后面所有工作是否有意义，所以它必须第一个被回答。**

## 为什么这个比较特别干净

1. **只有一次比较。** 不像分桶表那样要付 14~15 次的多重比较成本。
   一个 t 值、一个阈值，Bonferroni 不需要动。
2. **逐日配对。** 同一天、同一批股票池、同一个 universe 过滤，
   唯一的差别是**排序依据**：因子分数 vs 固定种子随机数。
   共同的当日市场波动在差值里抵消，于是这个 t 测的是
   **纯粹的"排序依据值多少"**，不掺市场、不掺择时、不掺成本。
3. **不扣成本。** 两臂的成本在同一天由同一套换手公式算出，
   差值里大部分抵消；但仍以**毛收益**为唯一主指标——
   成本另由 `basket_width_sweep` 负责，一件事只在一个地方判。

## 事前登记（跑之前定，不许事后改）

- **主指标**：逐日配对 t，均值 = 符号修正桶0 − 随机对照桶0
- **判据**：
  - `有微弱价值` = t ≥ **3.0**（正）
  - `无价值`     = −3.0 ≤ t < 3.0 —— **这是当前的先验**
  - `因子有害`   = t ≤ −3.0
- **宽度**：不止比桶 0。整个前 N 名（10/20/50/100）都并排比一遍，
  因为「因子无用」和「因子在某一段有用」是不同结论，而后者若存在，
  扫描 4 个宽度只值 4 次比较，付得起。
- **不做晋级判定，不写 `weights.json`。**

## 已知硬边界

- 与 `head_bucket_probe` 同口径：**1 日前瞻**。K>1 未测。
- 随机臂用固定种子（`head_bucket_probe.RANDOM_SEED`），
  **单一种子**。若结论落在 −3<t<3 这个"分不出"的区间，
  再换种子能提高分辨率，但那是**增加比较次数**，必须另行登记。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from scripts.head_bucket_probe import (  # noqa: E402
    BUCKET, DAYS_PER_YEAR, PREREG as PROBE_PREREG, RANDOM_SEED, SIGN_FIXED,
    _paired_t, probe,
)

# 并排比较的榜宽（前 N 名 = 桶 0..N/BUCKET-1 的逐日均值）。
# 4 个宽度 = 4 次比较，Bonferroni 在 n=4 时阈值约 3.0 左右量级，
# 这里仍用保守的 3.0 而不放宽。
WIDTHS = [10, 20, 50, 100]

PREREG = {
    "primary_metric": "逐日配对 t（符号修正 − 随机对照），逐宽度分别算",
    "verdicts": {
        "有微弱价值": "t ≥ 3.0",
        "无价值": "−3.0 ≤ t < 3.0（当前先验）",
        "因子有害": "t ≤ −3.0",
    },
    "threshold": 3.0,
    "why_so_clean": ("只有 1 次（或每宽度 1 次）比较，无多重比较；"
                     "逐日配对抵消当日市场波动；同一 universe 过滤；"
                     "唯一差别是排序依据"),
    "hard_boundary": ("与 head_bucket_probe 同为 1 日前瞻；K>1 未测。"
                      "随机臂单一种子，落在 −3<t<3 时不换种子重试——"
                      "那是增加比较次数，必须另行登记"),
    "不得用于晋级": ("不写 weights.json，不改晋级门限。本实验的产出"
                     "只能回答「现役因子值不值」，不能成为任何配置被"
                     "采纳的依据——采纳要另走 `evolve_race` 的三道护栏"),
    "upstream": PROBE_PREREG["three_way_fork"],
}


def topn_daily(daily: pd.DataFrame, n: int) -> pd.Series:
    """前 N 名的逐日毛收益（桶 0..N/BUCKET-1 的等权均值）。

    **等权**而不是按成分数加权：因子分数本身没有收益含义，
    拿它当权重就是给一个未验证的量加了个未验证的权重。
    策略实际买的是等权 top-N（`build_predictions` 取 `head(top_n)`）。
    """
    nb = max(1, n // BUCKET)
    cols = [c for c in daily.columns if 0 <= int(c) < nb]
    if not cols:
        return pd.Series(dtype=float)
    return daily[cols].mean(axis=1).dropna()


def compare(sign_daily: pd.DataFrame, rand_daily: pd.DataFrame,
            widths=WIDTHS) -> list[dict]:
    """逐宽度做「因子 − 随机」的逐日配对 t。"""
    rows = []
    for n in widths:
        s = topn_daily(sign_daily, n)
        r = topn_daily(rand_daily, n)
        both = pd.concat([s, r], axis=1, keys=["s", "r"]).dropna()
        t = _paired_t(both["s"], both["r"]) if len(both) >= 3 else None
        diff = (both["s"] - both["r"]) if len(both) else pd.Series(dtype=float)
        rows.append({
            "n": n,
            "n_days": int(len(both)),
            "factor_annual": float(both["s"].mean()) * DAYS_PER_YEAR if len(both) else None,
            "random_annual": float(both["r"].mean()) * DAYS_PER_YEAR if len(both) else None,
            "diff_annual": float(diff.mean()) * DAYS_PER_YEAR if len(diff) else None,
            "paired_t": t,
            "verdict": ("样本不足" if t is None else
                        "因子有害" if t <= -3.0 else
                        "无价值" if t < 3.0 else "有微弱价值"),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path, default=Path("data/reports/factor_vs_random.json"))
    args = ap.parse_args()

    import pickle
    print("载入面板缓存…", flush=True)
    xs = pickle.loads(args.cache.read_bytes())
    dates = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)
    print(f"本段 {len(dates)} 个交易日", flush=True)

    sign = probe(xs, dates, SIGN_FIXED, "符号修正")["daily"]
    print("符号修正 完成", flush=True)
    rand = probe(xs, dates, None, "随机对照", seed=RANDOM_SEED)["daily"]
    print("随机对照 完成", flush=True)

    if sign.empty or rand.empty:
        print("至少一臂样本为空——**不要拿这份输出下任何结论**", flush=True)
        return 1

    rows = compare(sign, rand)
    print(f"\n{'='*96}\n因子 vs 随机（{len(dates)} 日，逐日配对，唯一差别是排序依据）\n{'='*96}")
    print(f"{'榜宽':>6}{'有效日':>8}{'因子年均':>11}{'随机年均':>11}{'差':>10}"
          f"{'配对t':>9}  判定")
    for x in rows:
        t = x["paired_t"]
        print(f"{x['n']:>6}{x['n_days']:>8}{x['factor_annual']:>10.2%}"
              f"{x['random_annual']:>10.2%}{x['diff_annual']:>10.2%}"
              f"{(t if t is not None else float('nan')):>9.2f}  {x['verdict']}")

    positive = [x for x in rows if x["paired_t"] is not None and x["paired_t"] >= 3.0]
    overall = ("至少一个榜宽显著优于随机" if positive else
               "**所有榜宽都与随机不可区分**——因子在可交易段没有可证明的价值")
    print(f"\n  → {overall}")
    for x in positive:
        print(f"      榜宽 {x['n']}  t={x['paired_t']:.2f}")

    out = {"kind": "factor_vs_random", "prereg": PREREG,
           "window": [args.start, args.end], "n_days": len(dates),
           "random_seed": RANDOM_SEED, "rows": rows,
           "overall": overall}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())