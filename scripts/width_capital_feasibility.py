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

第一版用 `friction_5W × 5万 / gross` 解回本本金，**低估最多 40%**
（N=546 真实 **111.5 万**，近似 66.9 万）。

🚨 守卫没抓住，是因为守卫用**同一个公式**回代：
`gross − friction == 0` 在错公式里也成立。
⇒ **第 5 种形状：断言 == 公式，公式错了断言跟着一错。**
⇒ 正解不是把断言写严，是**给断言换独立真值源**：
① 回代改调底层 `annual_friction`
② 再加一条**代数推导的闭式**做第三方复核（相对差 1e-9）
③ 钉住 1/C 近似的**误差方向会翻转**（N=10 高估 2.9%，N≥30 低估 40%）
   —— 方向翻转比单向偏差更危险：单向的可以修正，翻转的连修正都不成

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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.costs import annual_friction  # noqa: E402
from aplus.hurdle import hurdle_rate, rounds_per_year  # noqa: E402

DEFAULT_ARTIFACT = Path("data/reports/factor_width_mde.json")
TARGET_NET = 0.20
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
    实测那种换算把回本本金低估最多 **40%**。

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


def feasibility(width_row: dict, capital: float = CAPITAL_5W,
                target: float = TARGET_NET, rpy: float | None = None) -> dict:
    """一档名数的金额可行性。**不重新回测**，只做换算 + 数值求解。"""
    n = int(width_row["n"])
    if rpy is None:
        rpy = rounds_per_year(21)
    gross = float(width_row["gross_annual_pct"])

    def friction(cap: float) -> float:
        return annual_friction(cap, EQUITY_WEIGHT, n, rpy)

    out = {
        "n": n,
        "gross_annual": gross,
        "friction_at_capital": friction(capital),
        "net_at_capital": gross - friction(capital),
        "friction_floor": friction_floor(n, rpy),
        # net = 0 ⇒ friction(cap) == gross
        "capital_for_breakeven": solve_capital(n, rpy, gross),
    }
    # 🚨 gross − target ≤ 摩擦下界 ⇒ 摩擦永远降不到那个水平 ⇒ **无解**。
    #   必须显式判无解，而不是让除法产出一个「负本金」再由下游解释。
    need = gross - target
    sol = solve_capital(n, rpy, need) if need > 0 else None
    out["capital_for_target_net"] = sol
    out["target_reachable"] = sol is not None
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="榜宽实验 → 金额可行性")
    p.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    p.add_argument("--capital", type=float, default=CAPITAL_5W)
    p.add_argument("--target", type=float, default=TARGET_NET)
    p.add_argument("--out", type=Path, default=None)
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
    hdr = (f"{'N':>5}{'毛超额/年':>11}{'摩擦/年':>11}{'真实净/年':>12}"
           f"{'回本本金':>12}{'到目标净本金':>14}{'达标?':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        be = f"{r['capital_for_breakeven']/1e4:.1f}万"
        need = ("不存在" if not r["target_reachable"]
                else f"{r['capital_for_target_net']/1e4:.0f}万")
        ok = "✅" if r["gross_annual"] >= hurdle_gross else "❌"
        print(f"{r['n']:>5}{r['gross_annual']:>10.2%}{r['friction_at_capital']:>11.2%}"
              f"{r['net_at_capital']:>12.2%}{be:>12}{need:>14}{ok:>8}")

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
        }
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
