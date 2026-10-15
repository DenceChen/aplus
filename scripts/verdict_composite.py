"""因子合成的滚动扩展窗判决 —— 按**预注册**规则执行。

## 判决规则在看到任何合成数据之前就已定死

见 `docs/TODO_measure_sigma_r.md`「预注册判据（2026-10-14，抽取之前）」：

- **门槛 0.04167** —— 由 σ_r 实测值与月频成本假设推导，**不是字面量**。
  它只认两个外部锚（σ_r 与成本），与信号侧的一切选择无关。
- **权重不许看全段**：2019/2020 等权（真实起点没有历史），
  2021 起滚动扩展窗只用**该年之前**的数据定权。
- **判决年份 2021 ~ 2026** —— 2019/2020 只报不判。
- **6 个判决年全部 ≥ 0.04167 才算过**；任何一年不过就是不过。

## 两个独立的问题

1. **过没过线** —— 上面的规则。
2. **合成的样本外 IC 是否真的高于最好的单因子** —— 与过线无关，
   但没高就说明组合没起作用，即便碰巧过线也必须知道。

用法：
    .venv/bin/python scripts/verdict_composite.py \
        --matrices data/reports/ic_matrices.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from aplus import provenance
from aplus.composite import daily_ic_series, unpack, walk_forward
from aplus.factor import DEFAULT_WEIGHTS, FACTOR_NAMES
from aplus.hurdle import DAYS_PER_YEAR, hurdle_rate, rounds_per_year
from aplus.ic_to_return import annualise, implied_spread, required_ic

ROOT = Path(__file__).resolve().parents[1]

#: σ_r **实测值**，出处 `data/reports/xsec_sigma_r.json`（带溯源章）。
#: 不写死门槛的理由与 `verdict_monthly_hurdle.py` 相同：
#: 门槛必须是算出来的，σ_r 一改它就跟着变。
SIGMA_R = 0.02656

#: 调仓周期，与月频路线同一口径。
K = 21


def monthly_ic_threshold() -> tuple[float, float]:
    """月频判决的 `(所需毛超额, 所需组合 IC)` —— **单一真相源**。

    提成函数而不是留在 `main()` 里，是因为 `scripts/sweep_factor_count.py`
    也要用同一个门槛。两处各算一遍 = 两个真相源，而它们**只差一点点**
    的时候没人会发现——今天已经有三个 P0 属于「两处各自自洽，
    合起来出事」这一族了。

    返回两个数而不是只返回 IC：打印时两个都要，
    免得只看 IC 而忘了「过关」这件事本来是**关于钱的**。

    ⚠️ 2026-10-14：门槛本身（26.39%）**只由成本推导**，
    与 IC 换算无关 —— 所以下面 `required_ic` 里那个换算
    （就是那座已被证伪的桥）是**判决链上唯一的可疑环节**。
    它现在给 0.01389；常数若再错一次，门槛就跟着错。
    **外部锚（σ_r=0.02656、成本 0.0055、K=21、目标 20%）都不动时，
    门槛变动 100% 来自换算那一步** ——
    `tests/test_verdict_composite.py` 用一条反查把它钉死。
    """
    need = hurdle_rate(rounds_per_year=rounds_per_year(K))
    return need, required_ic(need / DAYS_PER_YEAR, sigma_r=SIGMA_R)

#: 判决起始年 —— 之前的年只有等权，没有历史可用，只报不判。
JUDGED_FROM = 2021

#: 真实起点用等权的年份。
EQUAL_WEIGHT_YEARS = (2019, 2020)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", type=Path,
                    default=Path("data/reports/ic_matrices.parquet"))
    ap.add_argument("--factors", default="",
                    help="逗号分隔，默认全部 8 个")
    ap.add_argument("--out", type=Path,
                    default=Path("data/reports/verdict_composite.json"),
                    help="★ 本判决的 JSON 产物。**2026-10-15 之前这里不存在**——"
                         "三臂数字只活在终端里，于是「原样等权 6/6 全败、"
                         "朴素翻号 6/6 过线」这条结论无人可查，"
                         "差点被一份新预注册重新发现一遍（见第 127 层）。"
                         "**只印不落 = 数字等于没测**。")
    args = ap.parse_args()

    if not args.matrices.is_file():
        raise SystemExit(f"抽取表不存在：{args.matrices}\n"
                         f"先跑 scripts/extract_ic_matrices.py")

    factors = [f.strip() for f in args.factors.split(",") if f.strip()] \
        or list(FACTOR_NAMES)
    df = pd.read_parquet(args.matrices)

    need, ic_need = monthly_ic_threshold()

    print(f"月频门槛（K={K}，年调仓 {rounds_per_year(K):.1f} 次）{need:.2%}")
    print(f"σ_r（实测 {SIGMA_R}）→ 过关所需组合 IC = {ic_need:.5f}")
    print(f"因子 {len(factors)} 个 · 抽取 {len(df)} 天 "
          f"{df['date'].min()} ~ {df['date'].max()}")
    print(f"权重：{', '.join(str(y) for y in EQUAL_WEIGHT_YEARS)} 等权，"
          f"{JUDGED_FROM} 起滚动扩展窗（只用当年之前）\n")

    rows = walk_forward(df, factors, need_ic=ic_need,
                        judged_from=JUDGED_FROM,
                        equal_weight_years=EQUAL_WEIGHT_YEARS)

    # ── 判决前先说清楚「年毛超额」那一列是什么、不是什么 ──────────
    #
    # 2026-10-14：这一列走 `ic_to_return.py` 那座**桥**，而桥已被实测证伪：
    #   ① 尾部常数分母写成 `1−2q` 而非 `q`，差整 `(1−2q)/q` 倍；
    #   ② 即使修正常数，桥在 q=0.2 上仍高估实测 **4.02×**，
    #      且缺口**随 q 变化**（0.25×→0.36×）⇒ 错的是函数不是系数。
    #
    # **所以这一列只是「量级参照」，不是估计值，更不许被引用。**
    # 「判决」那一列只依据 `组合IC` 与门槛，**不受这一列影响**。
    print("⚠️  「桥·年毛超额」一列走 IC→分位价差的**桥**，该桥已被实测证伪"
          "（常数错 + 形状错，q=0.2 上高估 4.02×）。\n"
          "    它只是量级参照，**不是估计值**；判决只依据「组合IC」与门槛。\n"
          "🚨 而且它算的是**多空**价差（顶−底，β≈0）——"
          "**A 股散户融不到券，做不了这个组合**。\n"
          "    账户真拿得到的是「涨腿 − 市场」，实测只有它的约 1/16。\n"
          "    口径对照见 scripts/measure_bridge_error.py 的两组列。")
    print(f"{'年':<6}{'权重来源':>10}{'n':>6}{'组合IC':>10}"
          f"{'桥·年毛超额(多空)':>18}{'t':>8}{'判决':>12}")
    print("─" * 70)
    judged = []
    for r in rows:
        ic = r.get("ic_mean")
        if ic is None:
            print(f"{r['year']:<6}{'—':>10}{r['n_days']:>6}{'缺':>10}"
                  f"{'—':>18}{'—':>8}  {r.get('reason', '无数据')}")
            continue
        gross = annualise(implied_spread(ic, sigma_r=SIGMA_R, kind="spearman"))
        t = r.get("t")
        if r["judged"]:
            judged.append(r)
            mark = "✅ 过" if r["pass_"] else "❌ 差"
            gap = f"  {(gross - need) * 100:+.2f}pp"
        else:
            mark = "— 只报"
            gap = ""
        print(f"{r['year']:<6}{r.get('weights_source', '—'):>10}"
              f"{r.get('n_used', 0):>6}{ic:>10.5f}{gross:>18.2%}"
              f"{t:>8.2f}{mark:>12}{gap}")

    # ── 第三问：石川说的「等权往往就很好」，在 A 股成立吗 ────────
    #
    # 三条臂由弱到强，**只有最强的那条是主判决**：
    #
    #   等权     原样 1/k —— 连方向都不学
    #   朴素翻号 按历史 IC 符号定向 —— 仍然一个系数都不学
    #            ← 石川那句话的**最强版本**，也是这条路线的真正及格线
    #   最优定权 R⁻¹Σ_f —— 额外学了幅度（= 主判决）
    #
    # 第一版只有「原样等权」，而本项目 8 个因子里 7 个 IC 为负，
    # 原样等权的 IC 必然为负，对照臂**输得毫不费力**，
    # 读起来像「等权不行」——而真正的问题是「不学系数够不够」。
    print("\n=== 三条臂并列（石川 7.4，行 14500） ===")
    print("原话：「理论上，变量的权重应和它们预测未来收益率的能力成正比。")
    print("      **然而在实践中，等权重配置所有变量往往就是很好的选择。**」")
    print("主判决用的 R⁻¹Σ_f 正是**偏离等权**——这个警告方向相反，")
    print("所以并列跑一遍，让数据回答，而不是因为读到它就改判据（第 63 条）。\n")

    def arm_rows(**kw):
        return {r["year"]: r for r in
                walk_forward(df, factors, need_ic=ic_need,
                             judged_from=JUDGED_FROM,
                             equal_weight_years=EQUAL_WEIGHT_YEARS, **kw)
                if r.get("judged")}

    opt_by_year = {r["year"]: r for r in judged}
    sign_by_year = arm_rows(weight_rule="sign")
    eq_by_year = arm_rows(all_years_equal=True)

    # 只比**三臂都判了的年份**。等权臂不需要历史，所以它能判主臂
    # 「无历史」而不判的年份——年份集合不同，比较就没有意义。
    common = sorted(set(opt_by_year) & set(sign_by_year) & set(eq_by_year))
    dropped = sorted((set(sign_by_year) | set(eq_by_year)) - set(opt_by_year))
    if dropped:
        print(f"（等权臂多出的 {dropped} 年**不参与对比**：主臂无历史不判，"
              f"两臂年份集合必须一致）")
    dropped_s = sorted(set(eq_by_year) - set(sign_by_year))
    if dropped_s:
        print(f"（翻号臂多出的 {dropped_s} 年同样不参与对比）")

    print(f"{'年':<6}{'最优(主)':>11}{'朴素翻号':>11}{'等权':>10}"
          f"{'最优-翻号':>12}{'翻号判决':>12}")
    print("─" * 62)
    gains, sign_fail, eq_fail, sign_wins = [], 0, 0, 0
    for y in common:
        a = opt_by_year[y]["ic_mean"]
        b = sign_by_year[y]["ic_mean"]
        c = eq_by_year[y]["ic_mean"]
        gains.append(a - abs(b))
        sign_fail += (not sign_by_year[y]["pass_"])
        eq_fail += (not eq_by_year[y]["pass_"])
        sign_wins += (abs(b) >= a)
        print(f"{y:<6}{a:>11.5f}{b:>11.5f}{c:>10.5f}"
              f"{a - abs(b):>+12.5f}"
              f"{('✅ 过' if sign_by_year[y]['pass_'] else '❌ 差'):>12}")
    if gains:
        g = sum(gains) / len(gains)
        print(f"\n**最优定权相对朴素翻号的年均优势：{g:+.5f}**"
              f"（门槛 {ic_need:.5f} 的 {g / ic_need:.1%}）")
        print(f"  翻号臂不及格：{sign_fail} / {len(common)}　"
              f"等权臂不及格：{eq_fail} / {len(common)}　"
              f"翻号在 {sign_wins} / {len(common)} 年不劣于最优定权")
        if sign_fail == 0 and g < ic_need * 0.25:
            print("  → **石川那条在 A 股成立得更彻底**：不学任何系数也够线，"
                  "而线性合成多学的那点幅度**远不足以撑起一个新结论**。")
            print("    结论应当写成「**这条信号本身够强**」，"
                  "而不是「**线性合成很成功**」——")
            print("    后者会让人以为下一轮换更好的估计器还能更好，而实测并不支持。")

    # ── 第二问：合成的样本外 IC 是否真的高于最好的单因子 ──────────
    print("\n=== 样本外 vs 单因子（同一份 Σ_f，口径一致） ===")
    dates, Rs, sf = unpack(df, factors)
    yrs = np.array([int(str(d)[:4]) for d in dates])
    judged_years = {r["year"] for r in judged}
    singles_json: dict[str, dict] = {}
    if judged_years:
        by_year = {r["year"]: r for r in judged}
        singles = {}
        for y in sorted(judged_years):
            yr = yrs == y
            # 用**当年自己的权重**才算真样本外——逐年对比最干净
            wr = np.array([by_year[y]["weights"][f] for f in factors])
            ic_c = daily_ic_series(Rs[yr], sf[yr], wr)
            ic_c = ic_c[np.isfinite(ic_c)]
            per = {}
            for j, nm in enumerate(factors):
                e = np.zeros(len(factors))
                e[j] = 1.0
                ic_s = daily_ic_series(Rs[yr], sf[yr], e)
                ic_s = np.asarray(ic_s)[np.isfinite(ic_s)]
                per[nm] = ic_s.mean() if len(ic_s) else float("nan")
            singles[y] = (ic_c.mean() if len(ic_c) else float("nan"), per)
            # ★ NaN 不是合法 JSON（`json.dumps` 会写出裸 `NaN`，严格解析器直接报错）
            #   ⇒ 缺值一律落 `None`，不让「没有观测」变成「无法解析的产物」。
            bnm = max(per, key=lambda k: abs(per[k]))
            c_ic = float(ic_c.mean()) if len(ic_c) else None
            singles_json[str(y)] = {
                "composite_ic": c_ic,
                "best_single": bnm,
                "best_single_ic": (float(per[bnm]) if np.isfinite(per[bnm]) else None),
                "increment": (abs(c_ic) - abs(float(per[bnm]))
                              if c_ic is not None and np.isfinite(per[bnm]) else None),
            }

        hdr = f"{'年':<6}{'合成':>9}{'最好单因子':>11}{'哪个':>10}{'增量':>9}"
        print(hdr)
        print("─" * 45)
        wins = 0
        for y, (ic_c, per) in sorted(singles.items()):
            best_nm = max(per, key=lambda k: abs(per[k]))
            d = abs(ic_c) - abs(per[best_nm])
            wins += (d > 0)
            print(f"{y:<6}{ic_c:>9.5f}{per[best_nm]:>11.5f}{best_nm:>10}"
                  f"{d:>+9.5f}")
        print(f"\n合成在 {wins} / {len(singles)} 个判决年超过当年最好的单因子")
        if wins < len(singles):
            print("→ 组合**没有**在样本外普遍占优——即便过线也不能当成"
                  "「找到了更好的信号」，只能说「这条路还没死」。")

    # ── 落盘（2026-10-15，第 127 层）────────────────────────────────
    #
    # 🚨 **这三臂的排序是本仓最容易被重新发现的结论之一**：
    #   原样等权 `1/k` **6/6 全败且 IC 六年全为负**（不是弱一点，是**符号翻转**——
    #   8 个因子里 7 个 IC 为负，原样平均必然抵消成负）；
    #   朴素翻号（只学方向、一个系数都不学）**6/6 过线**；
    #   最优定权相对它的年均优势只有门槛的 **36.1%**，6 年里 5 年占优。
    # ⇒ 拿「原样等权」当对照臂，等于**预注册一场输定了的比赛**，
    #   而它的失败会被读成「等权不行」——
    #   `docs/TODO_equal_weight_control_arm_2026-10-15.md` 正是这么写的。
    # ⇒ 所以下面这份 JSON 是**判决的一部分**，不是日志。
    three_arm = {
        "threshold_ic": ic_need,
        "years": common,
        "by_year": {
            str(y): {
                "optimal": opt_by_year[y]["ic_mean"],
                "sign_only": sign_by_year[y]["ic_mean"],
                "equal_raw": eq_by_year[y]["ic_mean"],
                "optimal_minus_sign": opt_by_year[y]["ic_mean"] - abs(sign_by_year[y]["ic_mean"]),
                "pass_optimal": opt_by_year[y]["pass_"],
                "pass_sign": sign_by_year[y]["pass_"],
                "pass_equal": eq_by_year[y]["pass_"],
                "weights": opt_by_year[y]["weights"],
                "weights_source": opt_by_year[y].get("weights_source"),
            }
            for y in common
        },
        "summary": {
            "mean_advantage_optimal_over_sign": float(np.mean(gains)) if gains else None,
            "advantage_as_fraction_of_threshold":
                float(np.mean(gains)) / ic_need if gains else None,
            "sign_arm_failures": int(sign_fail),
            "equal_arm_failures": int(eq_fail),
            "sign_not_worse_years": int(sign_wins),
            "n_years": len(common),
            "equal_arm_ic_negative_years": int(
                sum(1 for y in common if eq_by_year[y]["ic_mean"] < 0)),
        },
        "arm_definitions": {
            "optimal": "w ∝ R⁻¹Σ_f（optimal_weights），只用该年之前的数据 —— **主判决**",
            "sign_only": "sign(历史 IC) —— 只学方向，一个系数都不学，"
                         "代码里自称「石川那句话的最强版本 / 真正及格线」",
            "equal_raw": "1/√k 原样 —— 连方向都不学。"
                         "⚠️ **7/8 因子 IC 为负 ⇒ 这一臂的 IC 必然为负**，"
                         "它是「不学任何东西」的下界，**不是「等权」的中性对照**",
        },
    }

    res = {
        "kind": "verdict_composite_three_arm",
        "generated_by": "scripts/verdict_composite.py",
        "note": ("「桥·年毛超额」那一列走已被实测证伪的 IC→分位价差桥"
                 "（常数错 + 形状错，q=0.2 上高估 4.02×），**不许被引用**；"
                 "判决只依据组合 IC 与门槛。"),
        "config": {
            "matrices": str(args.matrices),
            "factors": factors,
            "judged_from": JUDGED_FROM,
            "equal_weight_years": list(EQUAL_WEIGHT_YEARS),
            "weight_rule_main": "optimal",
            "n_days_extracted": int(len(df)),
            "date_min": str(df["date"].min()),
            "date_max": str(df["date"].max()),
        },
        "threshold": {"need_pct": need, "ic": ic_need, "sigma_r": SIGMA_R,
                      "derived_from": "σ_r 实测 + 月频成本假设（外部锚，非字面量）"},
        "judged_years": sorted(r["year"] for r in judged),
        "n_fail_years": int(sum(1 for r in judged if not r["pass_"])),
        "three_arm": three_arm,
        "vs_best_single_factor": singles_json,
        "default_weights_reference": {
            "weights": dict(DEFAULT_WEIGHTS),
            "why_here": (
                "★ `DEFAULT_WEIGHTS` **不是判决路径用的权重**。"
                "判决路径 = `walk_forward` → `train_weights` → `optimal_weights`"
                "（8 因子、逐年变、只用该年之前）。`DEFAULT_WEIGHTS` 是 "
                "`composite_score(weights=None)` 的回落，即**实盘出单**路径的默认。"
                "⇒ 两者结构不同：`ivol` 在 DEFAULT 里恒 0 而在判决里幅度最大；"
                "`vol20` 符号相反。把 DEFAULT_WEIGHTS 当成「现状基线」会认错基线"
                "（第 127 层）。"),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance.stamp(args.out, "scripts/verdict_composite.py", root=ROOT)
    print(f"\n→ {args.out}")

    print()
    if not judged:
        raise SystemExit("没有任何判决年份——抽取表太短或年份不对")
    n_fail = sum(1 for r in judged if not r["pass_"])
    print(f"判决年份：{len(judged)} 个（{min(judged_years)} ~ "
          f"{max(judged_years)}）")
    print(f"不及格年份：{n_fail} / {len(judged)}")
    if n_fail == 0:
        print("→ 合成月频在**每一个判决年**都够得着。")
        print("  但这**不是充分条件**：只做了信号侧判决，")
        print("  成本假设（0.55%/轮）与实盘滑点尚未进账，")
        print("  且回测尚未在合成权重下重跑。")
    else:
        years = [str(r["year"]) for r in judged if not r["pass_"]]
        print(f"→ **{n_fail} 个判决年够不到**（{', '.join(years)}）。")
        print("  按预注册规则：「合成月频可行」**不成立**。")
        print("  下一步是收缩因子数重来一轮——**门槛不许动**。")

    print(f"\n（抽取表：{args.matrices}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())