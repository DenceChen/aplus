"""因子数的前向扫描 —— **只比判决年**，并**用定理当场守门**。

## 为什么要有这个脚本

两条都不是小事：

**① 必须只比判决年。** `walk_forward` 里 2019/2020 走 `equal_weight_years`
的**等权臂**，2021 起走 `R⁻¹Σ_f` 的**最优臂**。权重规则不同 ⇒ 两个数
**不可比**。第一次跑这个扫描时我把 2019/2020 也算进了「判决年均」，
于是「k=2 的最优 > k=8」——**看起来像个 bug，我差点去查一个不存在的 bug**。

> 口径统一不是洁癖，是「定理能不能用」的前提。
> 与「一半秩一半原始值」同一族：**两处各自自洽，合起来违反数学**。

**② 必须带定理守卫。** 组合 IC 的**最优值**（`w ∝ R⁻¹Σ_f` 处）就是
r 对该因子集的**多重相关**：

    ρ(w) = (wᵀΣ_f)/√(wᵀRw)

因子集**缩小 ⇒ 可行集缩小 ⇒ 最优值不可能变大**。所以
「最优 k=1 ≤ 最优 k=2 ≤ … ≤ k=全集」必须逐段成立。
**一旦破了，说明代码在骗你**，而不是市场在给你惊喜。

2026-10-14 那个「R 按位置存、按顺序读」的 P0，正是被这条守卫抓住的：
剔除任一因子后组合 IC 近乎翻倍，与定理直接冲突。

> 静默失效产出的东西，通常比真结论**更有说服力**（数字更大、更整齐）。
> 所以护栏必须是**外部的数学约束**，不是「内部跑通了」。

## 只做描述，不做判决

门槛 `IC_NEED` 从 `verdict_composite.py` 导入，**不在这里另设一个**。
本脚本**不产判决**，只把扫描结果和守卫打出来——判决归判决脚本。

用法：
    .venv/bin/python scripts/sweep_factor_count.py \
        --matrices data/reports/ic_matrices.parquet
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from aplus.composite import unpack, walk_forward
from aplus.factor import FACTOR_NAMES


def _load_verdict():
    """按路径载入判决脚本——`scripts/` 不是包，且门槛必须与它**同一份**。

    门槛**不许**在本脚本里另算一遍：两份算出来只要不是逐位相同，
    「扫描的最优过不过线」就会和「判决的过不过线」说出不同的话，
    而那两句话读起来都很有道理。
    """
    path = Path(__file__).resolve().parent / "verdict_composite.py"
    spec = importlib.util.spec_from_file_location("_verdict_composite", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_VERDICT = _load_verdict()
JUDGED_FROM = _VERDICT.JUDGED_FROM
#: 单一真相源：门槛由判决脚本自己算，这里**只借不用**
#: ——同一个函数、同一份 σ_r、同一个调仓周期。
IC_NEED = _VERDICT.monthly_ic_threshold()[1]


def judged_ic(df, factors, judged_from: int = JUDGED_FROM) -> dict:
    """跑一次滚动判决，返回**判决年**的组合 IC。"""
    out = walk_forward(df, list(factors), IC_NEED, judged_from=judged_from,
                       weight_rule="optimal")
    return {r["year"]: r["ic_mean"] for r in out
            if r.get("ic_mean") is not None and r["year"] >= judged_from}


def check_monotone(chain: list[tuple[int, float]]) -> list[bool]:
    """定理守卫：`(k, 最优年均 IC)` 递增时，**每一段都必须非降**。

    单调性**成立**时，这个函数返回一个全是 `True` 的列表——**看起来什么也没做**。
    这正是它容易被写成死代码的原因，所以它必须有两样东西：

    1. **一条「必须能红」的测试**（下面 `tests/test_sweep_factor_count.py`）
    2. **一个非零退出码**，让守卫破了**在 CI 里立刻炸**

    > 一个只会通过的检查，等于没有检查。
    > 今天已经有四次「跑通了所以是对的」被证伪，
    > 所以「响」比「对」更重要。
    """
    return [v1 <= v2 + 1e-12
            for (_, v1), (_, v2) in zip(chain, chain[1:])]


def _summarise(label: str, ic: dict, years: list[int]) -> str:
    fails = sum(1 for y in years if abs(ic[y]) < IC_NEED)
    mean = sum(ic[y] for y in years) / len(years)
    mark = "全过" if fails == 0 else f"{fails}/{len(years)} 不及格"
    return (f"{label:>20}  年均 {mean:+.5f}  {mark:<12} "
            + " ".join(f"{ic[y]:+.4f}" for y in years))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrices", type=Path,
                    default=Path("data/reports/ic_matrices.parquet"))
    ap.add_argument("--factors", default="",
                    help="逗号分隔，默认 FACTOR_NAMES 全部")
    ap.add_argument("--judged-from", type=int, default=JUDGED_FROM)
    ap.add_argument("--max-k", type=int, default=3,
                    help="扫描到几因子（全集永远另外单独列）")
    args = ap.parse_args()

    factors = [f.strip() for f in args.factors.split(",") if f.strip()] \
        or list(FACTOR_NAMES)
    if not args.matrices.is_file():
        print(f"抽取表不存在：{args.matrices}\n"
              f"先跑 scripts/extract_ic_matrices.py")
        return 1

    df = pd.read_parquet(args.matrices)
    # **顺手验一次列名格式** —— 旧的位置格式在这里响亮失败，
    # 而不是等到读出错标的 R 还照样出数。
    unpack(df, factors)

    probe = judged_ic(df, factors, args.judged_from)
    years = sorted(probe)
    if not years:
        print("没有任何判决年——扫描无从谈起，先查抽取表覆盖的年份")
        return 1

    print(f"判决年 {years[0]}~{years[-1]}，门槛 {IC_NEED:.5f}"
          f"（{len(years)} 个判决年）\n")

    best_by_k: dict[int, float] = {}
    for k in range(1, min(args.max_k, len(factors)) + 1):
        rows = []
        for combo in itertools.combinations(factors, k):
            ic = judged_ic(df, list(combo), args.judged_from)
            if sorted(ic) != years:
                print(f"  ⚠️ {combo} 的判决年不齐 {sorted(ic)}——跳过，"
                      f"不按缺失兜底")
                continue
            rows.append((sum(ic.values()) / len(ic), combo, ic))
        if not rows:
            continue
        rows.sort(reverse=True, key=lambda t: t[0])
        best_by_k[k] = rows[0][0]
        n_all = len(list(itertools.combinations(factors, k)))
        print(f"── k={k}（{n_all} 个组合，最优 3）──")
        for mean, combo, ic in rows[:3]:
            print(_summarise("+".join(combo), ic, years))
        if k >= 2:
            worst = rows[-1]
            print(_summarise("…最差 " + "+".join(worst[1]), worst[2], years))
        print()

    ic_full = probe
    mean_full = sum(ic_full.values()) / len(years)
    print("── 参照 ──")
    print(_summarise(f"全集 {len(factors)} 因子", ic_full, years))
    print()

    print("── 定理守卫：最优 k 递增时，多重相关不可能变小 ──")
    chain = sorted(best_by_k.items()) + [(len(factors), mean_full)]
    verdicts = check_monotone(chain)
    for (k1, v1), (k2, v2), good in zip(chain, chain[1:], verdicts):
        print(f"  k={k1} {v1:+.5f} ≤ k={k2} {v2:+.5f} ?  "
              f"{'成立' if good else '【矛盾 = 有 bug】'}")
    ok = all(verdicts)
    if not ok:
        print("\n  🚨 **守卫破了**：因子集缩小却让组合 IC 变大。")
        print("     按定理这只可能是 bug——先查代码，**别写进结论**。")
        print("     最常见的两条：① R 的列按**位置**存、按**顺序**读")
        print("     （子集实验会整块错标）；② 权重学的是**当年之内**的数据。")
        return 2

    best1 = best_by_k.get(1)
    if best1 is not None:
        print(f"\n合成**比最优单因子**多出来的：{mean_full - best1:+.5f}"
              f"（门槛的 {(mean_full - best1) / IC_NEED:.1%}）")
    print("\n> ⚠️ k≥2 的「最优」是在**判决年上**从多个候选里挑出来的，"
          "必然带样本内挑选的偏差（因子选择前视，见 CLAUDE.md）。")
    print("> 所以「全集 ≥ k=2/3 最优」是**偏保守**的比较：全集没被挑选过。")
    print("> 诚实说法不是「某个 k 最好」，而是"
          "「**候选里没有一组能稳赢全集**」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())