"""赛马冠军 `{mom20:-0.5, turnratio:-0.5}` 对随机 —— t=3.96 到底是不是真的。

## 上一轮留下的 open

`evolve_race` 报出该配置 K=21 测试段 t=+3.96 > 临界 3.79（84 次比较），
裁决判晋级；但 `breadth_t=2.03` 没跟上，且**它量的是「对大盘的超额」**。

第 70 条已经实测：现役因子在 2019~2023 的四个榜宽上与**随机对照** t 全部 ≈ 0。
一个「对大盘的大 t」不等于「因子有横截面选股能力」——大盘本身在涨跌。
所以这里问一个更窄也更硬的问题：

    同一批票、同一批日子、同一套闸门，只改排序依据，因子臂能跑赢随机臂吗？

## 为什么不能把本次登记成 1 次比较（最贵的一处）

`turnratio` 是**被那 84 组赛马挑出来的**。提名这个动作本身就是一次选择，
选择带来的偏差必须计入族的大小。所以：

    候选族 = 84（赛马）+ 1（本次）= 85
    临界 = bonferroni_t(85, df) ≈ 3.64   （若误登记成 1 次比较则是 2.00）

差 1.8 倍。而冠军报的 t 恰好是 **3.96**——**登记方式直接决定它死还是活**。
所以这个数字写死在 `N_FAMILY` 常量里，不接受运行时传入。

## 三条硬边界

1. **不写 `weights.json`，不晋级**。该配置早在 2026-10-12 就冻结进前瞻验证
   第1、2批了；它的判决权在**未来数据**，不在这里。
2. **本窗口对 `turnratio` 是样本内的**——赛马的训练段就在 2019-01~2023-08，
   提名用了这些数据。所以即便过闸，本结果也只是诊断，**不能作为采纳依据**。
3. **多种子全报**。判据是「是否所有种子都在临界值同一侧」，沿用
   `horizon_sweep.seed_panel` 的规则：单种子过闸不是发现，是运气。

## 为什么 K=21 的功效必然很低（必须先认）

非重叠网格下 1214 天只有约 57 个观测。K=21 是赛马的胜出档位，
改成别的 K 就变成另一个假设。**功效不足意味着「没检出」的证明力很弱**——
本脚本的阴性结论只能否掉「存在很大的效应」，不能否掉「存在小效应」。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.evolve_race import bonferroni_t  # noqa: E402
from scripts.head_bucket_probe import DAYS_PER_YEAR  # noqa: E402
from scripts.horizon_sweep import (  # noqa: E402
    SEED_PANEL, arm, per_year, seed_panel,
)

# 赛马挑出的冠军权重与调仓周期（与 forward_registry 第1、2批登记的一致）
CHAMPION = {"mom20": -0.5, "turnratio": -0.5}
K = 21

# **候选族 = 84（赛马）+ 1（本次）。** 见文件头「为什么不能登记成 1 次比较」。
# 写成 1 会把临界从 3.64 压到 2.00，而冠军报的 t 是 3.96——
# 那等于用登记口径去救一个已经被选出来的冠军。
N_FAMILY = 85

TOP_N = 10


def compare(xs: dict, days: list, k: int = K, weights: dict | None = None,
            seed: int | None = None) -> dict:
    """单种子下的因子臂 vs 随机臂。"""
    import pandas as pd

    from scripts.factor_vs_random import _paired_t
    from scripts.head_bucket_probe import RANDOM_SEED

    w = CHAMPION if weights is None else weights
    sd = RANDOM_SEED if seed is None else seed
    s = arm(xs, days, w, sd, k)
    r = arm(xs, days, None, sd, k)
    both = pd.concat([s, r], axis=1, keys=["s", "r"]).dropna()
    if len(both) < 3:
        return {"n_days": int(len(both)), "paired_t": None,
                "verdict": "有效日不足——不下任何结论"}
    t = _paired_t(both["s"], both["r"])
    crit = bonferroni_t(N_FAMILY, df=len(both) - 1)
    d = both["s"] - both["r"]
    if t is None:
        v = "差值退化，不可估"
    elif abs(t) >= crit:
        v = f"**对随机显著（t={t:+.2f}，临界 {crit:.2f}）**"
    else:
        v = f"对随机不可区分（t={t:+.2f} < 临界 {crit:.2f}）"
    return {
        "n_days": int(len(both)), "t_crit": crit, "paired_t": t,
        "factor_annual": per_year(float(both["s"].mean()), k),
        "random_annual": per_year(float(both["r"].mean()), k),
        "diff_annual": per_year(float(d.mean()), k), "verdict": v,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--start", default="2019-01-02")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/turnratio_vs_random.json"))
    args = ap.parse_args()

    import pickle
    print("载入面板缓存…", flush=True)
    xs = pickle.loads(args.cache.read_bytes())
    days = sorted(d for d in xs["_dates"] if args.start <= d <= args.end)
    print(f"本段 {len(days)} 个交易日；K={K} 非重叠网格约 {len(days)//K} 个观测", flush=True)

    main_row = compare(xs, days)
    t = main_row.get("paired_t")
    print(f"\n{'='*88}\n{CHAMPION} @ K={K}  对随机（族 {N_FAMILY} 次比较）\n{'='*88}")
    print(f"  有效日        {main_row['n_days']}")
    print(f"  因子年均      {main_row.get('factor_annual', float('nan')):+.2%}")
    print(f"  随机年均      {main_row.get('random_annual', float('nan')):+.2%}")
    print(f"  差            {main_row.get('diff_annual', float('nan')):+.2%}")
    print(f"  配对 t        {t if t is not None else float('nan'):+.2f}"
          f"   临界 {main_row.get('t_crit', float('nan')):.2f}")
    print(f"\n  → {main_row['verdict']}")

    # 对照：冠军的两个成分单独跑一遍。若组合过闸而成分不过，
    # 说明效应来自成分而不是组合，那与「组合被提名」是两回事。
    print(f"\n{'─'*88}\n成分单独拆开（同一判据）", flush=True)
    parts = {}
    for name, w in (("mom20 单独", {"mom20": -0.5}),
                    ("turnratio 单独", {"turnratio": -0.5})):
        row = compare(xs, days, weights=w)
        parts[name] = row
        print(f"  {name:<16} 有效日 {row['n_days']:>3}  "
              f"t={row.get('paired_t') if row.get('paired_t') is not None else float('nan'):>+6.2f}"
              f"  临界 {row.get('t_crit', float('nan')):.2f}  {row['verdict']}", flush=True)

    sp = seed_panel(xs, days, K, seeds=SEED_PANEL)
    # `seed_panel` 内部用的是 SIGN_FIXED，这里换成冠军权重重算逐种子 t
    import pandas as pd
    from scripts.factor_vs_random import _paired_t
    from scripts.head_bucket_probe import RANDOM_SEED
    s_arm = arm(xs, days, CHAMPION, SEED_PANEL[0], K)
    ts = []
    for sd in SEED_PANEL:
        r_arm = arm(xs, days, None, sd, K)
        both = pd.concat([s_arm, r_arm], axis=1, keys=["s", "r"]).dropna()
        ts.append({
            "seed": sd, "n_days": int(len(both)),
            "paired_t": _paired_t(both["s"], both["r"]) if len(both) >= 3 else None,
        })
    ok = [x for x in ts if x["paired_t"] is not None]
    crit = bonferroni_t(N_FAMILY, df=min((x["n_days"] for x in ok), default=10) - 1)
    signs = {1 if x["paired_t"] > crit else (-1 if x["paired_t"] < -crit else 0)
             for x in ok}
    robust = len(ok) == len(SEED_PANEL) and len(signs) == 1 and 0 not in signs
    cells = " ".join(f"{x['paired_t']:+.2f}" if x["paired_t"] is not None else " n/a"
                     for x in ts)
    print(f"\n{'─'*88}\n种子稳健性（判据：全部种子都在临界值同一侧）")
    print(f"  逐种子t: {cells}")
    print(f"  → {'**种子稳健**' if robust else '**结论依赖种子，不成立**'}")

    out = {
        "kind": "turnratio_vs_random",
        "weights": CHAMPION, "k": K, "top_n": TOP_N,
        "n_family": N_FAMILY,
        "why_n_family": ("turnratio 是被赛马的 84 组挑出来的，提名本身是一次选择，"
                         "必须计入族大小。登记成 1 次比较会把临界从 3.64 压到 2.00，"
                         "而冠军报的 t 是 3.96——**登记口径直接决定它死还是活**"),
        "seed_panel": ts, "seed_robust": robust, "t_crit": crit,
        "window": [args.start, args.end], "n_days_window": len(days),
        "in_sample_warning": ("本窗口对 turnratio 是**样本内**的：赛马训练段就是 "
                              "2019-01-02~2023-08-16。即便过闸也只能作诊断，"
                              "不能作为采纳依据；它的判决权在 2026-10-12 起冻结的"
                              "前瞻验证里"),
        "no_promotion": "不写 weights.json。该配置已在前瞻验证第1、2批冻结",
        "main": main_row, "components": parts,
        "power_warning": (f"K={K} 非重叠观测只有约 {len(days)//K} 个。"
                          "阴性结论只能否掉「存在很大的效应」，"
                          "**不能否掉存在小效应**——不出结果不等于没效果"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  ⚠️ {out['power_warning']}")
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())