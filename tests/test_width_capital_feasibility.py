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

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus import provenance as provenance_mod  # noqa: E402
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

    🚨🚨 **2026-10-15 第三次改写**：重跑把 N=10 毛超额从 4.8108% 改到 4.6733%，
    而摩擦纯比例下界是 4.787% ⇒ `capital_for_breakeven` 变成 **None**。
    ⚠️ 但这个 None **不是判决**，是 **−0.021σ** 的噪声
    （毛超额 se = 5.4627%/年，见产物 `breakeven_margin_sigma`）
    ⇒ 「无解」和上一版的「有解 16.1 万」**两个都在噪声的 5% 以内**。

    ⇒ 所以本条不再假设「解存在」，而是**分岔**：
      · 有解 ⇒ 照旧用 `annual_friction` 独立验证解（原来的守法）
      · 无解 ⇒ 断言它**恰好**因为 `gross ≤ 摩擦下界` 而无解，
        **且**产物明确标注了这个结论落在噪声里
    ⇒ 「无解」这条分支**不许退化成恒真**：它守的是
      「产物不许把一个 0.02σ 的判定说成确定结论」。

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
        if cap is None:
            # 无解分支：必须**恰好**因为毛超额低于摩擦下界，且必须自报不可信
            assert g["gross_annual"] <= g["friction_floor"], (
                f"N={g['n']} 报无解，但毛超额 {g['gross_annual']:.4%} "
                f"高于摩擦下界 {g['friction_floor']:.4%} ⇒ 无解判定被改了"
            )
            assert g["breakeven_flips_within_1se"] is True, (
                f"N={g['n']} 报无解却没标出「±1σ 跨越下界」⇒ "
                "一个 0.02σ 的判定被说成了确定结论"
            )
            assert "结论不成立" in g["breakeven_verdict"], (
                f"N={g['n']} 的 verdict 文案没说不成立：{g['breakeven_verdict']}"
            )
            continue
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

    🚨 **无解档位也必须走这条路**：闭式在 `cap → ∞` 时退化成**摩擦下界**，
    那正是判「无解」用的量 ⇒ 无解分支验证的是
    「`annual_friction` 的下界 == 闭式的下界」，仍然两条独立路径。
    （直接 `continue` 跳过 = 让一半档位零覆盖，比过时的前提更坏。）
    """
    rpy = _rpy()
    for row in _widths().values():
        g = feasibility(row)
        n, cap, gross = g["n"], g["capital_for_breakeven"], g["gross_annual"]
        if cap is None:
            assert _closed_friction(1e12, n, rpy) == pytest.approx(
                g["friction_floor"], rel=1e-9), (
                f"N={n}：闭式下界 {_closed_friction(1e12, n, rpy):.6f} != "
                f"annual_friction 的下界 {g['friction_floor']:.6f}"
            )
            continue
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


def test_the_1_over_capital_approximation_understates_capital_by_a_lot():
    """★ 那条错公式**必须被量刑**：它把回本本金低估掉一半以上。

    🚨 方向机制是可证的，且**与数据无关**（这一条不许随数据改）：
        naive = (摩擦×C)|₅万 / 毛超额 ，而 (摩擦×C) 单调上升，
        且在解 C\\* 上 (摩擦×C)|C\\* = 毛超额 × C\\*
        ⇒ **naive < 真实 ⟺ 真实 > 5万**

    ★ **本守卫的前提在 2026-10-15 被自己推翻过三次，而第三次被推翻的是前提本身**：
      第一次（方向翻转 N=10 高估 2.9%）：翻转**从来不是近似式的性质**，
        是旧产物毛超额虚高的症状 —— 虚高把 C\\*(10) 压到 4.78 万 < 5 万。
      第二次（毛超额修正为 4.81%）：C\\*(10) = 16.1 万 > 5 万，
        五档一律落在解左边，低估 52.6%~60.3%，翻转消失。
      第三次（2026-10-15 重跑，毛超额 4.6733% < 摩擦下界 4.787%）：
        **N=10 的解整个变成 None**，而这个变化只有 **−0.021σ**
        ⇒ 它不是「近似式错了」，是**判决本身在噪声里**。

    🚨⇒ **判别力改由合成夹具给**（容差判据从真实数据通过里得不到信息量，
    牙齿只能来自合成夹具）。合成毛超额取 **`摩擦下界 × 1.02`**：
      · 略高于下界 ⇒ **必然有解**（不受真实毛超额落在哪一侧影响）
      · 实测解落在 **2.9×5万 ~ 160×5万** ⇒ 全部在解的右边 ⇒ 低估方向可验
    ⇒ 守卫不再依赖产物是否恰好有解，**第四次也不会失效**。
    产物那一半**只做观测并报出来**，不参与判据。

    ⚠️ 误差量级本质是 **`cap/5万` 的函数**（1/本金近似在 cap 远大于 5万 时失效），
    所以夹具**必须离 5 万足够远**：`下界 × 1.5` 会把 N=10 的解压到 3.76 万
    ⇒ 方向翻转、误差只有 18.8% ⇒ 那是**夹具选错**，不是近似式变好了。
    ⇒ 夹具**自检**下面两个前提，不成立就当场报错（不许静默降级）。

    ⇒ 于是量刑从「方向翻转」改成「**方向固定但幅度巨大**」。
    为什么这个改写不是把守卫放松：误差变成**单边**，
    按原文自己的推理「固定方向的误差让人以为可以拿它做修正」——
    而 −55% 这个量级根本不是一个能用常数因子修正的偏差。
    所以判据必须同时钉住**方向**与**量级**，少一个都会漏。
    """
    rpy = _rpy()
    signs = set()
    for row in _widths().values():
        n = int(row["n"])
        floor = friction_floor(n, rpy)
        # ── 合成夹具：毛超额 = 下界 × 1.02 ⇒ 必然有解，且解远大于 5 万 ──
        sd = float(row["annual_sd_pct"])
        se = sd * math.sqrt(244.0 / int(row["n_days"]))
        synth = {**row, "gross_annual_pct": floor * 1.02,
                 "t_naive": floor * 1.02 / se}
        g = feasibility(synth)
        gross, true = g["gross_annual"], g["capital_for_breakeven"]
        assert true is not None, f"N={n} 合成夹具仍无解 ⇒ 下界或求解器坏了"
        # 夹具自检：方向机制的前提是「解在 5 万右边」，不成立就是夹具选错
        assert true > CAPITAL_5W, (
            f"N={n}：夹具解 {true:,.0f} ≤ 5万 ⇒ 误差方向会是**高估**，"
            "而本守卫量刑的是低估 ⇒ 夹具倍数要调小（如 ×1.02），"
            "**不许**把门槛调小来迁就它"
        )
        naive = annual_friction(CAPITAL_5W, EQUITY_WEIGHT, n, rpy) * CAPITAL_5W / gross
        err = (naive - true) / true
        signs.add(err > 0)
        # 方向由「解相对 5万 的位置」唯一决定 —— 可证，且与数据无关
        assert (naive < true) is (true > CAPITAL_5W), (
            f"N={n}：近似 {naive:,.0f} vs 真实 {true:,.0f}，"
            f"5 万基准 ⇒ 误差方向应当由 C* {'>' if true > CAPITAL_5W else '<'} 5万 决定"
        )
        # 量级：合成夹具实测门槛 30%
        assert abs(err) >= 0.30, (
            f"N={n}：误差只有 {err:+.2%}（真实 {true:,.0f}，近似 {naive:,.0f}）。"
            "若毛超额或摩擦口径又动了，先查是不是**量刑数字**要更正，"
            "别直接把门槛调小 —— 见 CLAUDE.md「预注册门槛」"
        )
    assert signs == {False}, (
        f"五档里出现了 {sorted(signs)} 方向的误差。"
        "修正后的产物里毛超额一律低于摩擦 ⇒ 五档真实回本点都高于 5 万 ⇒ 近似一律低估。"
        "若这里重新出现翻转，说明**毛超额又被虚高了**，"
        "请先查产物口径，不要改本守卫"
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


def _consumer_content_at(repo_commit: str, rel: str) -> str | None:
    """该消费者在 `repo_commit` 那一刻的**内容**哈希；取不到返回 None。"""
    r = subprocess.run(["git", "show", f"{repo_commit}:{rel}"],
                       capture_output=True, cwd=_ROOT)
    if r.returncode != 0:
        return None
    return hashlib.sha256(r.stdout).hexdigest()


def test_the_artifact_is_not_older_than_every_consumer_of_it():
    """★ 产物层守卫的通病（第 96 层）：脚本改了、产物没重跑 ⇒ 数字全是旧的。

    本条比单文件 mtime 更进一步：**一个产物有多个消费者**，
    只要**任一**消费者在**产物生成之后**被改过，产物就该被判过期。

    🚨 **2026-10-15 把判据从 mtime 换成内容**（mtime 只是代理，不是证据）：
      本仓的变异验证会「改源码 → 跑守卫 → 用 `write_bytes` 还原」，
      内容回到逐位相同，**mtime 却被顶到现在** ⇒ 纯 mtime 判据把
      「一次没留下痕迹的变异测试」报成「产物过期」，
      而真实后果是逼人去重跑一个 10 分钟 / GB 级的产物。

    ⚠️ **换判据时最危险的写法是「内容 == HEAD 就算没变」** ——
      那会让「改了**并提交**」假阴性，而那正是本守卫最该抓的日常。
    ⇒ 所以真值源是 sidecar 里的 `code_sha`（**产物生成那一刻的 commit**），
      比对「当前内容」vs「code_sha 时的内容」，两个方向都成立：
        内容不同 ⇒ 生成之后真的改过 ⇒ 过期（哪怕 mtime 更旧）；
        内容相同 ⇒ 没改过 ⇒ **mtime 是谎言，不算证据**。
    无 sidecar / `git show` 取不到时**退回 mtime 并如实标注**（宁可保守）。
    """
    art_mtime = ART.stat().st_mtime
    consumers = [
        _ROOT / "scripts" / "factor_width_mde.py",
        _ROOT / "scripts" / "width_capital_feasibility.py",
        _ROOT / "scripts" / "placebo_control.py",
        _ROOT / "aplus" / "costs.py",
        _ROOT / "aplus" / "hurdle.py",
    ]
    sidecar = ART.with_name(ART.name + provenance_mod.SIDECAR_SUFFIX)
    code_sha = None
    if sidecar.exists():
        code_sha = json.loads(sidecar.read_text(encoding="utf-8")).get("code_sha")

    changed, mtime_lied, no_reference = [], [], []
    for c in consumers:
        if not c.exists():
            continue
        rel = c.relative_to(_ROOT).as_posix()
        at_gen = _consumer_content_at(code_sha, rel) if code_sha else None
        if at_gen is None:
            # 没有真值源 ⇒ 退回 mtime，并把「这是降级判据」写进失败消息
            no_reference.append(rel)
            if c.stat().st_mtime > art_mtime:
                changed.append(f"{rel}(无真值源，按 mtime 判)")
            continue
        if hashlib.sha256(c.read_bytes()).hexdigest() != at_gen:
            changed.append(rel)
        elif c.stat().st_mtime > art_mtime:
            mtime_lied.append(rel)

    assert not changed, (
        f"这些消费者在产物生成之后被改过：{changed}\n"
        "⇒ 榜宽实验没重跑，**重跑前的绝对收益数字不可引用**"
        + (f"\n⚠️ 以下消费者没有可比对的真值源，本次是降级判据：{no_reference}"
           if no_reference else "")
    )
    # ★ 让判据把「它凭什么这么说」一起印出来（第 18 种形状）：
    #   mtime 说更新、内容说没变 —— 这个矛盾必须被看见，而不是被静默吞掉。
    if mtime_lied:
        print(f"\n  ℹ️  mtime 比产物新但内容与 code_sha={code_sha[:8]} 逐位相同："
              f"{mtime_lied}\n     ⇒ 判为**未改动**（mtime 是还原/拷贝留下的假象），"
              "按 mtime 判会误报")


def test_a_bare_run_actually_writes_the_report_not_just_prints_it():
    """★ 第 137 层：**「我重跑过了」可以完全为真而产物一个字节没变。**

    `scripts/width_capital_feasibility.py` 的 `--out` 曾默认 `None`，
    而 `if args.out:` 是唯一的写盘闸门 ⇒ **裸跑打印一份漂亮的判决、
    却什么都不落盘**。

    ⇒ 危险的地方在于**没有任何东西会报**：
      产物层守卫（`test_the_artifact_is_not_older_than_every_consumer_of_it`）
      守的是**上游** `factor_width_mde.json` 的 `code_sha`，
      它**不会**因为这次空操作而变红 ⇒ 「按正路重跑」这一步
      悄悄变成了一个看起来做过、实则什么都没做的动作。

    实测代价：我据此把一次门禁红判成「产物过期」，
    而真正的正路（重跑上游）从头到尾没被执行过。

    ⚠️ 这里只钉**接线**（默认指向真实产物），不在测试里真跑一遍 ——
    真跑会把 sidecar 重新盖章，而 sidecar 的 `code_sha`
    正被上面那条守卫当**真值源**读，中途改它会让那条守卫求值于半途。
    """
    import ast  # noqa: PLC0415

    src_path = _ROOT / "scripts" / "width_capital_feasibility.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    default_out = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument"
                and node.args and node.args[0].value == "--out"):
            for kw in node.keywords:
                if kw.arg == "default":
                    default_out = ast.unparse(kw.value)
    assert default_out == "DEFAULT_OUT", (
        f"`--out` 的默认值变成了 {default_out!r} —— 裸跑将只打印、不落盘。"
        "⇒ 把它改回 DEFAULT_OUT；**不要**改成 None（那正是本条要防的回归）"
    )
    mod_out = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "DEFAULT_OUT" for t in node.targets):
            mod_out = ast.unparse(node.value)
    # ⚠️ `ast.unparse` 把 `Path("x")` 渲染成 `Path('x')` —— 比的是渲染结果，
    #   所以这里比**单引号**形态；写成双引号会当场红，而红的原因看起来像「值不对」。
    assert mod_out == "Path('data/reports/width_capital_feasibility.json')", mod_out
    assert (_ROOT / "data" / "reports" / "width_capital_feasibility.json").is_file(), \
        "默认路径指向的产物不存在 —— 默认值与真实产物脱钩了"
