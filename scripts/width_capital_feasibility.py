"""榜宽实验的落地翻译：每个名数**要多少本金才交付得起**，以及**要多少才够 20%**。

## 为什么要有这个脚本

榜宽实验给的是「毛超额 / sd / MDE / 逐笔摩擦」——全是**比率**。
用户的约束是「**我有 5 万块**」——是**金额**。
比率与金额之间隔着一次换算，而**没换算就引用，正是今天一整天在修的那个形状**。

本脚本只做换算与判决，**不重新回测**：
它读 `data/reports/factor_width_mde.json`，所以它继承那份产物的口径（第 7 层）。

## ★ 摩擦不是 ∝ 1/本金

A 股最低佣金 `COMMISSION_MIN = ¥5/笔` 是每笔**固定额**：

```
每笔名义额 per = C/n
佣金(per)      = max(per·费率, ¥5)
整轮成本       = n · [买+卖]  = n·(2·max(per·费率,¥5) + per·k)
摩擦(C)        = 整轮成本 · rpy / C
               = [2·max(费率, ¥5·n/C) + k] · rpy
               = 4.787%/年 + 2·rpy·max(0, ¥5·n/C − 费率)      ← ★ 闭式
```

⇒ 摩擦是 C 的**单调递减饱和函数**：小本金端近似 `∝ 1/C`，
越过 `C > ¥5·n/费率` 后**钉死在下界 4.787%/年**（纯比例费率）。

实测（`annual_friction`，K=21，`摩擦 × 本金` 单调**上升**）：

| 本金 | N=10 摩擦 | 摩擦×本金 | N=200 摩擦 | 摩擦×本金 |
|---|---|---|---|---|
| 5 万 | 6.4137% | 3,207 | 50.5661% | 25,283 |
| 10 万 | 5.2518% | 5,252 | 27.3280% | 27,328 |
| 20 万 | 4.7870% | 9,574 | 15.7090% | 31,418 |
| 5,000 万 | **4.7870%** | 239,350 | **4.7870%** | 239,350 |

## ★ 第一版把换算写错了，而且守卫没抓住

第一版用 `friction_5W × 5万 / gross` 解回本本金。

⚠️ **本 docstring 的这组数字自己过期过一次**（2026-10-15 自查）：
  它原写「低估最多 40%，N=546 真实 111.5 万、近似 66.9 万」，而
  守卫 `tests/test_width_capital_feasibility.py:254` 早已改成
  「五档一律落在解左边，低估 52.6%~60.3%」—— **两处不一致，而我一直读着旧的**。
  ⇒ 真实的低估值随毛超额一起变，而毛超额是**会被重跑改掉的**：
    把一个会变的数字写进解释性文字，就等于让它静默过期。
  ⇒ 现行（钉死窗口 2021-01-01→2026-10-12，n_days=1395）：

  | N | 精确解 | 1/C 近似 | 偏差 |
  |---|---|---|---|
  | 10 | 16.1 万 | 6.7 万 | −58.6% |
  | 30 | 23.9 万 | 10.0 万 | −58.3% |
  | 100 | 55.8 万 | 22.1 万 | −60.3% |
  | 200 | 81.3 万 | 36.4 万 | −55.3% |
  | 546 | 182.4 万 | 86.5 万 | −52.6% |

🚨 守卫没抓住，是因为守卫用**同一个公式**回代：
`gross − friction == 0` 在错公式里也成立。
⇒ **第 5 种形状：断言 == 公式，公式错了断言跟着一错。**
⇒ 正解不是把断言写严，是**给断言换独立真值源**：
① 回代改调底层 `annual_friction`
② 再加一条**代数推导的闭式**做第三方复核（相对差 1e-9）
③ 钉住 1/C 近似**五档一律低估**（实测 52.6%~60.3%）
   —— 🚨 **原文这里写的是「误差方向会翻转」（N=10 高估 2.9%，N≥30 低估 40%）**，
     而那条性质**早已不复存在**：翻转是**旧产物毛超额虚高**的症状
     （毛超额虚高 ⇒ 逼近摩擦下界 ⇒ 小 N 也显得「有解」⇒ N=10 翻到另一边）。
     毛超额被重跑修回真实值之后，五档**一致落在精确解左边**。
     ⇒ 保留一条描述**已消失性质**的断言，等于让读者去找一条不存在的守卫；
       而它是**代码旁边的散文**，不是判据，判据在守卫里（守卫已是对的那份）。

⇒ 现在 `feasibility()` 对 `annual_friction` **数值二分**。

## 两条换算式

- **回本所需本金**：解 `gross == annual_friction(cap, …)`（`gross > 下界` 时有唯一解）
- **到目标净所需本金**：解 `gross − TARGET == annual_friction(cap, …)`
  ⇒ 🚨 **`gross − TARGET ≤ 摩擦下界` 时无解**。这不是「很难」，是可证的。

## ★ 为什么「无解」这一列比任何单档数字都重要

`net = gross − friction`，而摩擦有**正的下界** ⇒ **`net < gross` 严格成立**。
所以 **本金只能吃掉损耗，不能创造收益**。
若某档 `gross` 已经低于目标净，**再多的本金也到不了** ——
瓶颈在信号本身，与资金量、测量精度、样本长度全都无关。

守卫 `tests/test_width_capital_feasibility.py` 钉住七件事：
① 「无解」必须由 `gross − TARGET ≤ 摩擦下界` 推出（不是 `gross ≤ TARGET`）
② 回本点用 `annual_friction` 回代验（不许换算式自证）
③ 数值解 vs 独立代数闭式，相对差 ≤1e-9
④ 摩擦非增 / 固定额区严格递减 / `摩擦×本金` 上升 / 正下界 / 饱和
⑤ 1/C 近似的误差方向会翻转
⑥ `need ≤ 下界` 必须返回 `None`
⑦ 门槛不许手打（第 17 种形状）/ 产物不得比任何消费者旧（第 96 层）
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.costs import annual_friction  # noqa: E402
# ★ `DAYS_PER_YEAR` 从 hurdle 导入（单一真相源）：`gross_se_annual` 的
#   年化换算要它，而手打一个 244 就是第 137 层的「两份副本之间没有守卫」。
from aplus.hurdle import DAYS_PER_YEAR, hurdle_rate, rounds_per_year  # noqa: E402
# ★ 溯源章（2026-10-15 补）。本脚本**不重跑回测**，只把 `factor_width_mde.json`
#   的毛超额翻译成「要多少本金」⇒ 重跑是秒级的，所以章必须能拿到。
#   ⚠️ 它此前**既没登记也没盖章** ⇒ 按本仓规矩 `unknown` = 不可引用，
#   而 CLAUDE.md「五档回本所需本金」那张表**就是照着它写的**。
from aplus import provenance  # noqa: E402

DEFAULT_ARTIFACT = Path("data/reports/factor_width_mde.json")
TARGET_NET = 0.20
# 🚨 第 137 层：`--out` 的默认值**必须是**真实产物路径。
#   它曾是 `None`，而 `if args.out:` 是唯一的写盘闸门
#   ⇒ **裸跑会打印一份漂亮的判决、却一个字节都不落盘**。
#   ⇒ 于是「我重跑过了」这句话可以完全为真而产物逐位未变，
#   而产物层守卫守的是**上游** `factor_width_mde.json` 的 `code_sha`，
#   它**不会**因为这次空操作而变红 ⇒ 「重跑」变成了一个
#   **看起来做过、实则什么都没做**的动作，而没有任何东西会报。
#   （我为此把一次门禁红判成了「产物过期」，正路重跑却是空的。）
DEFAULT_OUT = Path("data/reports/width_capital_feasibility.json")
CAPITAL_5W = 50_000.0
EQUITY_WEIGHT = 1.0


def friction_floor(n: int, rpy: float, cap: float = 1e9) -> float:
    """摩擦的下界 —— **纯比例费率**，与本金无关。

    必须实测，不许推算：推过一次「万三双边 + 印花税 = 1.28%/年」，
    实测是 **4.787%/年**，差 3.7 倍。
    """
    return annual_friction(cap, EQUITY_WEIGHT, n, rpy)


def solve_capital(n: int, rpy: float, need: float) -> float | None:
    """解最小的 `capital` 使 `annual_friction(capital, …) ≤ need`。

    ★ **必须数值求解**，不许用 `friction_5W × 5万 / need` 那种 1/C 换算 ——
      那是第 5 种形状：公式错了，而**用同一个公式回代验的守卫也跟着错**。
    实测那种换算**五档一律低估 52.6%~60.3%**（本 docstring 上方的表）。
    ⚠️ 原文写「最多 40%」—— 那是旧毛超额下的值，随重跑失效。

    `need` 必须 **大于摩擦下界**，否则无解（摩擦降不下去）。
    """
    if need <= friction_floor(n, rpy):
        return None
    lo, hi = 1.0, 1e10
    for _ in range(200):                      # 200 次二分 ⇒ 相对精度 1e-58
        mid = (lo + hi) / 2.0
        if annual_friction(mid, EQUITY_WEIGHT, n, rpy) > need:
            lo = mid
        else:
            hi = mid
    return hi


def gross_se_annual(width_row: dict) -> float:
    """毛超额年化值的**抽样标准误**。

    ⚠️ **必须区分年化 sd 与日 sd**：`annual_sd_pct` 是**日超额序列**年化后的 sd，
    而 `gross_annual_pct = mean(daily) × 244`，所以

        se(年化) = 244 × sd_日 / sqrt(n_days) = **sd_年化 × sqrt(244 / n_days)**

    直接写 `annual_sd_pct / sqrt(n_days)` 会把年化 sd 当日 sd 用，
    **低估 15.6 倍**（实测 5.4627%/年被写成 0.3497%/年）。
    ⚠️ 这个错我在本轮当场犯过一次 ⇒ 所以它必须有守卫。

    ## ★ 双路径交叉验证（第 137 层：两份副本之间没有守卫）

    产物自报 `t_naive = gross / se`。反推 `se = gross / t_naive` 与上面的公式值
    必须**逐位一致** —— 一条来自本函数，一条来自 `factor_width_mde.py`
    的独立实现。⚠️ 只用自己这一条路径 = 第 5 种形状（用同一公式自证）。
    """
    sd = float(width_row["annual_sd_pct"])
    n_days = int(width_row["n_days"])
    se = sd * math.sqrt(DAYS_PER_YEAR / n_days)
    t_naive = width_row.get("t_naive")
    if t_naive:
        se_via_t = float(width_row["gross_annual_pct"]) / float(t_naive)
        if not math.isclose(se, se_via_t, rel_tol=1e-9):
            raise AssertionError(
                f"毛超额标准误两条路径不一致：公式 {se:.10f} vs "
                f"由 t_naive 反推 {se_via_t:.10f} ⇒ 至少有一条错，"
                "不许带着分歧继续算判决"
            )
    return se


def feasibility(width_row: dict, capital: float = CAPITAL_5W,
                target: float = TARGET_NET, rpy: float | None = None) -> dict:
    """一档名数的金额可行性。**不重新回测**，只做换算 + 数值求解。"""
    n = int(width_row["n"])
    if rpy is None:
        rpy = rounds_per_year(21)
    gross = float(width_row["gross_annual_pct"])
    floor = friction_floor(n, rpy)
    se = gross_se_annual(width_row)
    #: 毛超额离摩擦下界还有几个标准误。**判决的强度就在这一个数里。**
    margin_sigma = (gross - floor) / se

    def friction(cap: float) -> float:
        return annual_friction(cap, EQUITY_WEIGHT, n, rpy)

    breakeven = solve_capital(n, rpy, gross)
    #: 毛超额的 ±1σ 区间。**这不是判据阈值**，是「区间跨不跨过摩擦下界」
    #: 这个**无阈值**事实的载体 —— 跨过去了，回本有无这个结论就不成立。
    #: ⚠️ 1σ 取自正态惯例，**没有外部锚**（Harvey 的 t>3.0 是**因子**判据，
    #:   管不到「派生金额结论」的稳定性）。所以它只作**报告**，不作判决。
    lo, hi = gross - se, gross + se
    flips = lo <= floor <= hi
    out = {
        "n": n,
        "gross_annual": gross,
        "friction_at_capital": friction(capital),
        "net_at_capital": gross - friction(capital),
        "friction_floor": floor,
        # net = 0 ⇒ friction(cap) == gross
        "capital_for_breakeven": breakeven,
        # ── 🚨 以下字段回答「上面那个判决有多可信」（2026-10-15 补记）──
        "gross_se_annual": se,
        "gross_lo_1se": lo,
        "gross_hi_1se": hi,
        "breakeven_margin_sigma": margin_sigma,
        "breakeven_flips_within_1se": flips,
        "breakeven_verdict": (
            f"毛超额 ±1σ = [{lo * 100:.2f}%, {hi * 100:.2f}%]"
            + (f"，**跨越摩擦下界 {floor * 100:.2f}%**" if flips else "，未跨越摩擦下界")
            + f"；点估计距下界 {margin_sigma:+.3f}σ ⇒ "
            + ("**「有无回本本金」这个结论不成立**（不是「没有」也不是「有」）"
               if flips else
               ("无解：毛超额低于纯比例摩擦，且区间不跨下界" if breakeven is None
                else f"有解：{breakeven:,.0f} 元"))
        ),
    }
    # 🚨 gross − target ≤ 摩擦下界 ⇒ 摩擦永远降不到那个水平 ⇒ **无解**。
    #   必须显式判无解，而不是让除法产出一个「负本金」再由下游解释。
    need = gross - target
    sol = solve_capital(n, rpy, need) if need > 0 else None
    out["capital_for_target_net"] = sol
    out["target_reachable"] = sol is not None
    return out


def _sha256_of(path: Path) -> str:
    """产物字节指纹。**读文件本身**，不用 mtime —— mtime 是代理不是证据。"""
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="榜宽实验 → 金额可行性")
    p.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    p.add_argument("--capital", type=float, default=CAPITAL_5W)
    p.add_argument("--target", type=float, default=TARGET_NET)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args(argv)

    rep = json.loads(args.artifact.read_text(encoding="utf-8"))
    k = int(rep["k_rebalance_every"])
    rpy = rounds_per_year(k)
    # ★ 门槛从 hurdle_rate 实算，**不手打 26.39%** ——
    #   手打过一次 `0.20 + 0.055×rpy`（COST_REAL_50K 少了个 0），得 83.90%。
    hurdle_gross = hurdle_rate(rounds_per_year=rpy)

    rows = [feasibility(rep["widths"][kk], args.capital, args.target)
            for kk in sorted(rep["widths"], key=int)]

    print(f"K={k}｜年调仓 {rpy:.1f} 次｜本金 {args.capital/1e4:.0f} 万"
          f"｜目标净 {args.target:.0%}/年")
    print(f"判决门槛（毛口径，hurdle_rate 实算）= {hurdle_gross:.2%}\n")
    # 🚨「回本本金」这一列**可以是 None**，而第一版的打印层直接 `None/1e4`
    #   ⇒ TypeError 崩在**第一行表格**上。第一版能跑只是因为当时 N=10 恰好有解
    #   —— 即「它没崩过」是**窗口的偶然**，不是代码的性质（第 139 层同族：
    #   判决强度不到 1σ ⇒ 判决会随日历翻面，而**消费者只见过其中一面**）。
    hdr = (f"{'N':>5}{'毛超额/年':>11}{'摩擦/年':>11}{'真实净/年':>12}"
           f"{'回本本金':>12}{'距下界':>9}{'到目标净本金':>14}{'达标?':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        be = ("**无解**" if r["capital_for_breakeven"] is None
              else f"{r['capital_for_breakeven']/1e4:.1f}万")
        need = ("不存在" if not r["target_reachable"]
                else f"{r['capital_for_target_net']/1e4:.0f}万")
        ok = "✅" if r["gross_annual"] >= hurdle_gross else "❌"
        # ★ 判决强度必须**与判决同排印出**（第 139 层）：
        #   只印「无解」而不印 σ，读起来与 10σ 一样确定。
        sig = f"{r['breakeven_margin_sigma']:+.3f}σ"
        print(f"{r['n']:>5}{r['gross_annual']:>10.2%}{r['friction_at_capital']:>11.2%}"
              f"{r['net_at_capital']:>12.2%}{be:>12}{sig:>9}{need:>14}{ok:>8}")

    flips = [r for r in rows if r["breakeven_flips_within_1se"]]
    if flips:
        print(f"\n🚨 **判决强度**：{len(flips)}/{len(rows)} 档的 ±1σ 区间"
              f"**跨越摩擦下界** ⇒ 「有无回本本金」这个结论**不成立**"
              f"（不是「没有」也不是「有」）。")
        for r in flips:
            print(f"   N={r['n']:>3}：{r['breakeven_verdict']}")

    reachable = [r for r in rows if r["target_reachable"]]
    print()
    if not reachable:
        print(f"★ **没有任何一档存在「本金大到能达标」的解。**")
        print(f"  摩擦 > 0 ⇒ 净 < 毛；毛超额最高 "
              f"{max(r['gross_annual'] for r in rows):.2%}/年 < 目标 {args.target:.0%}。")
        print(f"  ⇒ 瓶颈是**信号大小**，与本金、测量、样本长度全都无关。")
    else:
        best = min(reachable, key=lambda r: r["capital_for_target_net"])
        print(f"★ 最省本金的达标档：N={best['n']}，需 "
              f"{best['capital_for_target_net']/1e4:.0f} 万")

    if args.out:
        payload = {
            "generated_from": str(args.artifact),
            "k_rebalance_every": k,
            "rounds_per_year": rpy,
            "capital": args.capital,
            "target_net": args.target,
            "hurdle_gross_from_hurdle_rate": hurdle_gross,
            "any_width_reaches_target": bool(reachable),
            "widths": rows,
            # ★ 口径标签**随数字一起离开脚本**（D0 / 第 7 层）。
            #   起因：这张表在 CLAUDE.md 里的复制件**不带口径**，
            #   而预注册 G2 写着「口径标签是必填」⇒ 读者无从知道
            #   「回本」是拿**毛超额**去抵摩擦算的、门槛也**不含**成本项。
            # ⚠️ 标签文本**不许手打**（第 142 层：手打的判读散文会过期）——
            #   口径由 `widths[0]` 的字段名与上游 artifact 的键**生成**。
            "caliber": {
                "gross_annual": (
                    "毛超额，**未扣摩擦**。上游 "
                    f"{args.artifact} 的 gross_annual_pct，"
                    "标的为「涨腿 − 基准」的年超额"),
                "net_at_capital": (
                    f"净超额 = gross_annual − friction_at_capital，"
                    f"摩擦按**本行 capital={args.capital:.0f}** 计。"
                    "⚠️ 换个本金这一列就变，不是常量"),
                "capital_for_breakeven": (
                    "解 `gross == annual_friction(capital)` 的数值解 —— "
                    "**拿毛超额去抵摩擦**，不是让净收益等于目标。"
                    "⇒ 它的口径是「回本（净=0）」，**不含**任何收益率目标"),
                "capital_for_target_net": (
                    f"解 `gross − target({args.target:.0%}) == annual_friction(capital)`；"
                    "解不存在时为 `null`。⚠️ 与 `capital_for_breakeven` "
                    "**不是同一个方程**"),
                "hurdle_gross_from_hurdle_rate": (
                    "毛口径判决门槛，由 `aplus.hurdle.hurdle_rate` 实算，"
                    "**不手打**。它 = 目标净 + 名义年成本，"
                    "⇒ 与本表 `net_at_capital` 用的是**两种成本口径**"),
                "friction_at_capital": (
                    f"`annual_friction(capital, …)`，capital = "
                    f"**{args.capital:,.0f} 元**（`--capital`，非产物字段）"),
                "friction_floor": (
                    "**摩擦的正下界** = `annual_friction(1e9, …)`，"
                    "即本金无限大时的纯比例费率成本。"
                    "⚠️ 本金**买不到**零成本（最低佣金 ¥5/笔是固定额）"),
                "breakeven_margin_sigma": (
                    "`(gross_annual − friction_floor) / gross_se_annual`。"
                    "⚠️ 这是**报告口径不是判据**：|值| < 1 时判决会在噪声里翻面"),
                "breakeven_flips_within_1se": (
                    "±1σ 区间是否跨过摩擦下界。**跨过 ⇒ 「有无回本本金」"
                    "这个结论不成立**（不是「没有」也不是「有」）"),
                "gross_se_annual": (
                    "毛超额年化值的抽样标准误 = "
                    "`annual_sd_pct × √(244 / n_days)`，而 `annual_sd_pct` "
                    "**本身已是年化 sd**（= 日 sd × √244）。"
                    "⚠️ 写成 `annual_sd_pct / √n_days` 会把年化 sd 当日 sd 用，"
                    "**低估 15.6 倍**（实测 5.4627%/年被写成 0.3497%/年）；"
                    "三条年化乘数各不相同（均值 ×T、sd ×√T、SE ×T/√n），"
                    "见第 93 层。两条路径互校：公式 vs 由产物自报 `t_naive` 反推，"
                    "不一致即 `AssertionError`，不许带着分歧出判决"),
                "gross_lo_1se": "毛超额 ±1σ 区间下界。⚠️ 1σ 是正态惯例，**无外部锚**",
                "gross_hi_1se": "毛超额 ±1σ 区间上界。同上",
                "breakeven_verdict": (
                    "**把上面三个字段拼成一句话的散文**，由它们算出、"
                    "不手打 ⇒ 不可能与字段脱钩（第 142 层）"),
                "target_reachable": (
                    "`capital_for_target_net is not None`。⚠️ 与 `detectable` "
                    "**无关**：那是统计可测性，这是可交付性"),
            },
        }
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        # ⚠️ 盖章必须在写完之后，且**只能靠真跑**拿 ——
        #   手写 sidecar 是形状 16（捏造一个与真章无法区分的文件），比红着更糟。
        # ★ 记下**上游产物的字节指纹**，不只是路径（第 137 层）。
        #   路径永远不变 —— `generated_from: factor_width_mde.json` 在
        #   上游被重跑一百次之后仍然一模一样 ⇒ **读它的人无法知道
        #   自己手里的数是不是上一次上游的**。
        #   ⇒ 而这正是本脚本咬过我的形态：上游 fwm 重跑两次，
        #     派生产物**一个字都不提示自己过期**，直到我手工去比。
        provenance.stamp(args.out, "scripts/width_capital_feasibility.py",
                         root=Path(__file__).resolve().parents[1],
                         extra={"any_width_reaches_target":
                                payload["any_width_reaches_target"],
                                "target_net": payload["target_net"],
                                "source_artifact": str(args.artifact),
                                # 🚨 缺这一项时守卫会红，而不是回落成「跳过」
                                #   —— 「缺档必须报错，绝不回落」。
                                "source_artifact_sha256":
                                    _sha256_of(args.artifact),
                                "capital": payload["capital"]})
        print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
