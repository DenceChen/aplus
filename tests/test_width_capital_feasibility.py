"""「加大本金就能达标」这句话必须**在代码层面可证伪**。

## 起因（2026-10-15）

榜宽实验跑完，五档毛超额 6.52%~9.78%/年，判决门槛 26.39%/年。
直觉上「榜太窄了，加宽一点、再多投点钱」像是出路，于是我做了张换算表。

换算表的第一版**列名在说谎**：列名写「要 20% 净的本金」，
算出来的净是 **−13.48%**。那个本金其实是**摩擦降到 20% 的点**，不是达标点。

⇒ 第 7 种形状的变体：**表头与数据不是同一个量**，
而这次差的是一个符号 —— 不报错、不越界，只是读的人会得出相反的结论。

第二版改对之后，结论是**可证的**，不是估计：

    net = gross − friction，friction > 0 ⇒ **net < gross**
    而 max(gross) = 9.78% < 20% ⇒ **不存在任何本金能让净达标**

⇒ **本金只能吃掉损耗，不能创造收益。** 瓶颈在信号本身。
这个结论必须被守卫钉住，否则下一次有人算这张表时又会滑回「多投点钱就好了」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.costs import annual_friction  # noqa: E402
from aplus.hurdle import COST_REAL_50K, hurdle_rate, rounds_per_year  # noqa: E402
from scripts.width_capital_feasibility import (  # noqa: E402
    CAPITAL_5W, EQUITY_WEIGHT, TARGET_NET, feasibility, friction_floor,
    solve_capital,
)

ART = _ROOT / "data" / "reports" / "factor_width_mde.json"


def _widths() -> dict:
    assert ART.exists(), (
        f"{ART} 不存在 —— 本守卫不允许在缺档时静默通过。"
        "跑 `.venv/bin/python scripts/factor_width_mde.py --start 2021-01-01`"
    )
    return json.loads(ART.read_text(encoding="utf-8"))["widths"]


def _rpy() -> float:
    return rounds_per_year(int(json.loads(ART.read_text(encoding="utf-8"))["k_rebalance_every"]))


def test_no_width_reaches_the_target_net_at_any_capital():
    """★ **主条**：五档都不存在「本金大到能达标」的解。

    这不是「很难」，是**可证的**：`net = gross − friction`，而摩擦有**正的下界**。
    """
    rows = [_widths()[k] for k in sorted(_widths(), key=int)]
    rpy = _rpy()
    for row in rows:
        got = feasibility(row)
        # 🚨 判「无解」的条件是 **`gross − 目标 ≤ 摩擦下界`**，不是 `gross ≤ 目标`。
        #    摩擦降不到目标那个水平 ⇒ 加大本金也没用。差这一条会多报出一档假解。
        need = got["gross_annual"] - TARGET_NET
        expect = need > friction_floor(got["n"], rpy)
        assert got["target_reachable"] is expect, (
            f"N={got['n']} 毛超额 {got['gross_annual']:.2%}，"
            f"需要摩擦降到 {need:.2%}，摩擦下界 {friction_floor(got['n'], rpy):.4%}，"
            f"却报 target_reachable={got['target_reachable']} —— "
            "「无解」的判定条件被改了"
        )
    reachable = [feasibility(r) for r in rows if feasibility(r)["target_reachable"]]
    if not reachable:
        best = max(float(r["gross_annual_pct"]) for r in rows)
        assert best < TARGET_NET, (
            f"最高毛超额 {best:.2%} 已经 ≥ 目标 {TARGET_NET:.0%}，"
            "⇒ **存在可行解**，本守卫的前提已失效，请重读上面的算式并更新本文档"
        )
    # 净 < 毛 对每一档都必须成立（摩擦恒正）
    for row in rows:
        g = feasibility(row)
        assert g["net_at_capital"] < g["gross_annual"], (
            f"N={g['n']} 的净 {g['net_at_capital']:.4%} ≥ 毛 {g['gross_annual']:.4%} "
            "⇒ 摩擦被算成了负数或 0"
        )


def test_breakeven_capital_is_where_net_is_exactly_zero():
    """★ 数值解必须**由 `annual_friction` 自己验证**，不许用换算式自证。

    🚨 第一版是这样写的：

        friction_at_cap = g["friction_at_capital"] * CAPITAL_5W / cap
        assert g["gross_annual"] - friction_at_cap == approx(0)

    它**永远绿** —— 因为它把 `1/本金` 的假设同时塞进了「算」和「验」，
    哪怕那个假设错了 40%。这是第 5 种形状：**断言 == 公式，公式错了断言跟着一错**。
    现在断言直接调**底层真值源** `aplus.costs.annual_friction`，
    于是「解」与「验」之间第一次有了两条独立的计算路径。
    """
    rpy = _rpy()
    for row in _widths().values():
        g = feasibility(row)
        cap = g["capital_for_breakeven"]
        assert cap is not None, f"N={g['n']} 毛超额低于摩擦下界 ⇒ 回本无解"
        friction_at_cap = annual_friction(cap, EQUITY_WEIGHT, g["n"], rpy)
        assert g["gross_annual"] - friction_at_cap == pytest.approx(0.0, abs=1e-9), (
            f"N={g['n']}：毛 {g['gross_annual']:.6f} − "
            f"回本本金 {cap:,.0f} 下的真实摩擦 {friction_at_cap:.6f} ≠ 0"
        )
        # 必须是**最小**解：再小一点，摩擦就降不到毛超额以下 ⇒ 净转负
        smaller = annual_friction(cap * 0.999, EQUITY_WEIGHT, g["n"], rpy)
        assert smaller > g["gross_annual"], (
            f"N={g['n']}：本金 {cap * 0.999:,.0f}（小 0.1%）的摩擦 "
            f"{smaller:.6f} 仍 ≤ 毛超额 ⇒ {cap:,.0f} 不是最小解"
        )


def _closed_friction(cap: float, n: int, rpy: float) -> float:
    """★ **独立闭式**，用来交叉验数值解。

    推导（每一轮调仓，整轮换手）：
        per      = C / n                      每笔名义额
        成本/笔  = 2·max(per·rate, ¥5) + per·(2·过户费 + 2·价差 + 印花税)
        整轮成本 = n · 成本/笔
        摩擦     = 整轮成本 · rpy / C
                = (2·max(rate, ¥5·n/C) + k) · rpy ,  k = 2·过户费+2·价差+印花税
                = 下界 + 2·rpy·max(0, ¥5·n/C − rate)
    ⇒ **闭式与 `annual_friction` 是两条独立的推导路径**（代数 vs 逐笔循环）。
    """
    from aplus.costs import COMMISSION_MIN, COMMISSION_RATE  # noqa: PLC0415
    from aplus.costs import SPREAD_ONE_WAY, STAMP_DUTY_SELL, TRANSFER_FEE  # noqa: PLC0415
    k = 2 * TRANSFER_FEE + 2 * SPREAD_ONE_WAY + STAMP_DUTY_SELL
    floor = (2 * COMMISSION_RATE + k) * rpy
    return floor + 2 * rpy * max(0.0, COMMISSION_MIN * n / cap - COMMISSION_RATE)


def test_the_numeric_solve_agrees_with_an_independent_closed_form():
    """★ 数值解必须有一条**代数上独立**的路径能复核它。

    这是本文件的第 5 种形状的**正解**：不再「用同一个公式回代」，
    而是拿一条**独立推导**的闭式来比。相对差必须到 1e-9。
    """
    rpy = _rpy()
    for row in _widths().values():
        g = feasibility(row)
        n, cap, gross = g["n"], g["capital_for_breakeven"], g["gross_annual"]
        assert _closed_friction(cap, n, rpy) == pytest.approx(gross, rel=1e-9), (
            f"N={n}：闭式在回本本金 {cap:,.0f} 下算得 "
            f"{_closed_friction(cap, n, rpy):.6f}，毛超额 {gross:.6f}"
        )


def test_friction_saturates_at_a_positive_floor_it_is_not_inversely_proportional_to_capital():
    """★ **钉住非线性本身** —— 这条假设错了，回本表就错了 40%，而当年没有任何守卫拦住。

    `COMMISSION_MIN = ¥5/笔` 是每笔**固定额**，所以：
      · 名义额小 ⇒ 总成本里有固定项 ⇒ `摩擦 × 本金` **随本金上升**
      · 越过 `5/费率`（每笔名义额超过最低佣金生效线）⇒ 固定项失效
        ⇒ 摩擦**饱和**在纯比例费率上
    ⇒ 摩擦**非增**、**饱和**、下界**为正**。三条都是「∝ 1/C」的直接反例。
    """
    rpy = _rpy()
    n = 100
    caps = [5e4, 1e5, 2e5, 5e5, 1e6, 5e6, 1e7]
    frictions = [annual_friction(c, EQUITY_WEIGHT, n, rpy) for c in caps]

    # ① 非增（注意是**非**增 —— 饱和之后严格递减会自己变红）
    for i in range(1, len(caps)):
        assert frictions[i] <= frictions[i - 1] + 1e-15, (
            f"摩擦随本金上升了：{caps[i-1]:,.0f}→{caps[i]:,.0f} "
            f"{frictions[i-1]:.6%}→{frictions[i]:.6%}"
        )
    # ② 固定额还在生效的区间里，摩擦**严格递减**（这就是 1/C 近似能骗过人的地方）
    for i in range(1, 4):
        assert frictions[i] < frictions[i - 1], (
            f"{caps[i-1]:,.0f}→{caps[i]:,.0f} 摩擦没降："
            f"{frictions[i-1]:.6%}→{frictions[i]:.6%}"
        )
    # ③ `摩擦 × 本金` 单调**上升** —— 这是「∝ 1/C」最直接的证伪
    for i in range(1, len(caps)):
        assert frictions[i] * caps[i] > frictions[i - 1] * caps[i - 1], (
            f"本金 {caps[i]:,.0f} 的「摩擦×本金」= {frictions[i] * caps[i]:,.1f} ≤ "
            f"上一档 {frictions[i-1] * caps[i-1]:,.1f} ⇒ 固定额效应被弄丢了"
        )
    # ④ 正下界 + 饱和
    floor = friction_floor(n, rpy)
    assert floor > 0, "摩擦下界必须为正 —— 为 0 意味着「本金够大就零成本」"
    assert annual_friction(1e11, EQUITY_WEIGHT, n, rpy) == pytest.approx(floor, rel=1e-9), (
        "本金放大到 1000 亿还没到底 ⇒ 固定额仍在生效，下界判错了"
    )
    assert frictions[-1] == pytest.approx(floor, rel=1e-9), (
        f"1000 万本金的摩擦 {frictions[-1]:.6%} ≠ 下界 {floor:.6%} ⇒ 下界不是这一档的取值"
    )


def test_the_1_over_capital_approximation_is_wrong_in_a_direction_that_flips():
    """★ 那条错公式**必须被量刑，而且必须说清方向会翻转**。

    实测：N=10 **高估** 2.93%，N≥30 **低估** 最多 39.9%（真实 111.5 万，近似 66.9 万）。

    🚨 方向翻转的机制是可证的：
        naive = (摩擦×C)|₅万 / 毛超额 ，而 (摩擦×C) 单调上升，
        且在解 C\\* 上 (摩擦×C)|C\\* = 毛超额 × C\\*
        ⇒ **naive < 真实 ⟺ 真实 > 5万**
    N=10 的回本点 4.78 万 **小于** 5 万 ⇒ 近似落在解的右边 ⇒ 高估。

    为什么要钉「方向翻转」而不只是「低估」：
    **一半的错比全错更危险** —— 固定方向的误差让人以为可以拿它做修正，
    翻转方向的误差连修正都不成。
    """
    rpy = _rpy()
    signs = set()
    for row in _widths().values():
        g = feasibility(row)
        n, gross, true = g["n"], g["gross_annual"], g["capital_for_breakeven"]
        naive = annual_friction(CAPITAL_5W, EQUITY_WEIGHT, n, rpy) * CAPITAL_5W / gross
        err = (naive - true) / true
        signs.add(err > 0)
        # 方向由「解相对 5万 的位置」唯一决定
        assert (naive < true) is (true > CAPITAL_5W), (
            f"N={n}：近似 {naive:,.0f} vs 真实 {true:,.0f}，"
            f"5 万基准 ⇒ 误差方向应当由 C* {'>' if true > CAPITAL_5W else '<'} 5万 决定"
        )
        assert abs(err) > 0.02, f"N={n}：误差只有 {err:+.2%}，文档里的量刑需要更正"
    assert signs == {True, False}, (
        f"五档里只出现了 {sorted(signs)} 方向的误差 —— "
        "如果误差不再翻转，请重读上面的推导并改写本守卫的前提"
    )


def test_solve_capital_reports_no_solution_below_the_friction_floor():
    """★ `need ≤ 下界` 必须返回 `None`，而不是返回一个巨大但无解的本金。"""
    rpy = _rpy()
    for n in (10, 100, 546):
        floor = friction_floor(n, rpy)
        assert solve_capital(n, rpy, floor) is None, f"need == 下界 时判了有解（N={n}）"
        assert solve_capital(n, rpy, floor * 0.5) is None, f"need < 下界 时判了有解（N={n}）"
        assert solve_capital(n, rpy, floor * 1.000001) is not None, (
            f"need 刚刚超过下界就判无解（N={n}）—— 数值比较写错了"
        )


def test_the_threshold_is_computed_not_typed():
    """★ **不许手打门槛常数**。

    实测我手打过一次 `0.20 + 0.055 * rounds_per_year(k)` ——
    `COST_REAL_50K` 是 **0.0055**，我少写了一个 0，门槛从 26.39% 变成 83.90%。
    ⇒ 这正是第 17 种形状（被标定/被推导的常数被凭记忆重打），
      只是这次落在**我自己的分析脚本**里，而不是生产路径。

    本条把脚本里的门槛钉成 `hurdle_rate(...)` 的实算值。
    """
    import ast
    import inspect
    import textwrap

    import scripts.width_capital_feasibility as wcf  # noqa: PLC0415

    src = inspect.getsource(wcf.main)
    assert "hurdle_rate(" in src, "main() 必须调 hurdle_rate 实算门槛"

    # ★★ 用 **AST** 而不是子串：`getsource` **包含注释**，
    #    而这个函数里恰好有一条注释写着「不手打 26.39%」——
    #    第一版守卫用 `assert "26.39" not in src`，结果被**唯一正确的注释**证伪。
    #    这是第 13 种形状的标准解法：**子串断言 → AST 断言**。
    #    AST 里没有注释，只有真正会被执行的常量。
    tree = ast.parse(textwrap.dedent(src))
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))}
    # 允许的：TARGET_NET(0.2)、CAPITAL_5W 相关的 1e4、以及 k_rebalance_every 那类索引
    # —— 一律按「不是这些可疑值」来判，不做白名单（白名单会变成新的可绕过面）。
    for forbidden, why in ((26.39, "判决门槛百分数"),
                           (0.2639, "判决门槛分数"),
                           (0.055, "COST_REAL_50K 少写一个 0 的错值"),
                           (0.0055, "COST_REAL_50K —— 它必须来自 import")):
        assert forbidden not in literals, (
            f"main() 的可执行树里手打了 {forbidden}（{why}）—— "
            "门槛与成本常数一律从 aplus.hurdle 取，不许凭记忆重打（第 17 种形状）"
        )
    # 正向：实算门槛必须与 COSTS 的定义一致
    k = int(json.loads(ART.read_text(encoding="utf-8"))["k_rebalance_every"])
    assert hurdle_rate(rounds_per_year=rounds_per_year(k)) == pytest.approx(
        TARGET_NET + COST_REAL_50K * rounds_per_year(k), rel=1e-12
    )


def test_the_artifact_is_not_older_than_every_consumer_of_it():
    """★ 产物层守卫的通病（第 96 层）：脚本改了、产物没重跑 ⇒ 数字全是旧的。

    本条比单文件 mtime 更进一步：**一个产物有多个消费者**，
    只要**任一**消费者比产物新，产物就该被判过期。
    """
    art_mtime = ART.stat().st_mtime
    consumers = [
        _ROOT / "scripts" / "factor_width_mde.py",
        _ROOT / "scripts" / "width_capital_feasibility.py",
        _ROOT / "scripts" / "placebo_control.py",
        _ROOT / "aplus" / "costs.py",
        _ROOT / "aplus" / "hurdle.py",
    ]
    stale = [c.name for c in consumers
             if c.exists() and c.stat().st_mtime > art_mtime]
    assert not stale, (
        f"产物比这些消费者旧：{stale}\n"
        "⇒ 它们改过而榜宽实验没重跑，**重跑前的绝对收益数字不可引用**"
    )
