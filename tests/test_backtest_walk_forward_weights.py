"""`scripts/backtest_walk_forward_weights.py` 的测试。

这个脚本要回答的是**当前唯一还没有答案的问题**：
`R⁻¹Σ_f` 逐年权重跑进真实引擎之后，扣掉可成交性与成本还剩多少。

所以这里守的不是「算得对」，是**「算的是同一个东西」**——
因为本项目栽过的第五种静默失效正是「换了一个口径，看起来还是那个口径」。

## 四条纪律

1. **权重必须取判决同一份** —— 自己重算 = 造第二台测量仪
2. **两个成本口径都要报** —— 回测默认 0.003 **只是价差那项**，
   真实现况 0.0055，低估 1.84 倍；只报前者 = 与门槛口径不一致
3. **成本缩放依赖线性性** —— 而线性性是一条**会过期的假设**
4. **逐日序列必须复用 `evolve_race` 的那一份** —— 自己重写就是第 22 层
"""
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = (Path(__file__).resolve().parent.parent / "scripts"
           / "backtest_walk_forward_weights.py")


def _mod():
    spec = importlib.util.spec_from_file_location("_wf_bt", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _mod()


# ── ② 两个成本口径 ──────────────────────────────────────────────

def test_both_cost_levels_are_reported_not_just_the_optimistic_one():
    """脚本必须**同时**报 0.003 与 0.0055，且明确说出哪个是真实现况。

    `STOCK_COST_DEFAULT = 0.003` 只是四项成本里的**价差**那项
    （0.0015×2），佣金、印花税、过户费全缺席 —— 真实现况 0.0055，
    **低估 1.84 倍**。

    而门槛 `hurdle_rate` 用的恰恰是 0.0055。
    只报 0.003 ⇒ **分子用乐观口径、分母用悲观口径**，系统性地自我美化。
    """
    from aplus.backtest import STOCK_COST_DEFAULT
    from aplus.hurdle import COST_REAL_50K

    assert STOCK_COST_DEFAULT == 0.003
    assert COST_REAL_50K == 0.0055
    assert COST_REAL_50K / STOCK_COST_DEFAULT == pytest.approx(1.8333, abs=1e-3)

    lin = M._rescale_check()
    assert lin["linear"], f"成本缩放的前提（线性）不成立：{lin}"


# ── ③ 成本缩放的线性性 ──────────────────────────────────────────

def test_rescaling_the_cost_column_is_exactly_equivalent_to_changing_the_rate():
    """`at_cost` 的缩放必须与「换成本率重跑」**逐位相同**。

    做法：用 `cost_drag` 造出两个成本率下的真值，
    再看缩放 `cost` 列能不能复现第二个。
    若哪天 `cost_drag` 里出现 `min(1, …)` 之类的非线性项，
    这条会当场红——**而在那之前它不会报错，只会安静地算错**。
    """
    from aplus.backtest import STOCK_COST_DEFAULT, cost_drag
    from aplus.hurdle import COST_REAL_50K

    prev, cur = ["a", "b", "c", "d"], ["d", "e", "f", "g"]
    for top_n in (3, 10):
        real = cost_drag(prev, cur, top_n, cost=COST_REAL_50K)
        res = pd.DataFrame({"cost": [cost_drag(prev, cur, top_n)]})
        got = M.at_cost(res, COST_REAL_50K)["cost"].iloc[0]
        assert got == pytest.approx(real, rel=1e-12), (
            f"top_n={top_n}：缩放得 {got}，真值 {real}"
        )


def test_rescaling_leaves_the_default_rate_untouched():
    """`at_cost` 在默认成本率上必须是**恒等** —— 否则默认档也被悄悄改了。"""
    from aplus.backtest import STOCK_COST_DEFAULT

    res = pd.DataFrame({"cost": [0.0, 0.0015, 0.003]})
    got = M.at_cost(res, STOCK_COST_DEFAULT)["cost"].to_numpy()
    assert np.array_equal(got, res["cost"].to_numpy())


def test_rescaling_does_not_mutate_the_input():
    """`at_cost` 改的是**副本**。

    原地修改会让第二档成本读到第一档已经缩放过的数——
    而那种错误报出来的净超额只会「略微偏大」，完全看不出异常。
    """
    from aplus.hurdle import COST_REAL_50K

    res = pd.DataFrame({"cost": [0.001, 0.002, 0.003]})
    before = res["cost"].tolist()
    M.at_cost(res, COST_REAL_50K)
    assert res["cost"].tolist() == before


# ── ① 权重单一真相源 ────────────────────────────────────────────

def test_the_weights_come_from_the_verdicts_own_walk_forward():
    """脚本**必须调用** `walk_forward`，不许自己重算一遍权重。

    自己重算等于「在测量台旁边另造一台测量仪」：
    两台之间的差**永远查不出来**，因为没人会去比对它们。
    这正是 `measure_bridge_error.py` 里同一条纪律的第二次应用。
    """
    import ast

    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "walk_forward" in called, (
        "权重必须取判决用的那一份 `walk_forward`——自己重算就没有可比性了"
    )


def test_the_daily_series_reuses_evolve_race_instead_of_being_rewritten():
    """逐日序列**复用** `evolve_race._daily_series`，不许自己写。

    它已经处理了三件极易错的事：逐日先聚合再检验、
    成分不足 `MIN_BASKET` 的日子丢掉、只有实际建仓的涨腿付成本。
    自己重写一遍就是第 22 层静默失效——**而且更隐蔽**，
    因为新版本一开始是对的，只是少了一个 `where`。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "_daily_series" in src, "必须复用 evolve_race 的逐日聚合"
    assert "_tstat" in src, "必须复用 evolve_race 的分块 t"
    # 不许在本文件里另起炉灶实现分块
    assert "def _block_means" not in src, (
        "分块逻辑已存在于 evolve_race._tstat，本脚本再写一遍 = 第二台测量仪"
    )


# ── ④ 闸门不许被关掉 ────────────────────────────────────────────

def test_the_execution_gate_must_stay_on():
    """可成交性闸门**不许**被关掉。

    关掉之后成本会偏低（买不进的高收益票照样计入），
    而那个偏差**只朝一个方向**——让结果好看。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "execution_gate=True" in src, (
        "回测必须开着可成交性闸门；关掉它 = 用买不进的高收益票抬高净值"
    )


def test_a_blocked_fill_must_be_counted_not_silently_dropped():
    """被闸门拦下的观测必须**按原因计数**并报出。

    计数缺失 = 这些观测从统计里彻底消失且不留任何痕迹。
    脚本已经把 `attrs["unfillable_execution*"]` 收集进逐年结果，
    这条钉住那个字段名不许改。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "unfillable_execution" in src, (
        "可成交性拦截必须计数上报——不计数等于从统计里消失且不留痕迹"
    )


# ── 门槛口径 ────────────────────────────────────────────────────

def test_the_hurdle_is_derived_from_the_real_cost_not_the_backtest_default():
    """门槛**必须**用 `COST_REAL_50K` 推导。

    若改用 `STOCK_COST_DEFAULT`，门槛会从 26.39% 掉到 20% + 3.49% = 23.49%，
    整整松 2.9 个百分点——而方向是**朝松**的，最不容易被发现。
    """
    from aplus.hurdle import (COST_REAL_50K, DAYS_PER_YEAR, hurdle_rate,
                              rounds_per_year)

    rounds = rounds_per_year(21)
    need = hurdle_rate(rounds_per_year=rounds)
    assert need == pytest.approx(0.20 + COST_REAL_50K * rounds)
    assert need == pytest.approx(0.2639, abs=5e-4)
    # 顺手钉住「若用回测默认成本，门槛会松多少」
    from aplus.backtest import STOCK_COST_DEFAULT
    loose = 0.20 + STOCK_COST_DEFAULT * rounds
    assert loose == pytest.approx(0.2349, abs=5e-4)
    assert need - loose == pytest.approx((COST_REAL_50K - STOCK_COST_DEFAULT) * rounds)


def test_t_is_computed_on_blocks_of_the_rebalance_period():
    """`block` **必须**等于调仓周期。

    K 日调仓时一天里「选了谁」在 K 天内不变；
    把 K×n 个交易日当成 K×n 个独立观测 = 把同一个决策数了 K 遍。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "block=rebalance_every" in src or "block=args.rebalance_every" in src, (
        "t 必须按调仓周期分块——这是 2026-10-11 修过的真实缺陷"
    )
