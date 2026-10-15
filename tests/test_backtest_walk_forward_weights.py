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


# ── ⑤ 判决窗口末端（2026-10-15，形状 23 的第二个受害者）──────────────
#
#  起因：`--end` 默认 `""`，而调用点是
#  `end = args.end or str(pd.read_parquet(files[0])["date"].max())`
#  —— 那个回落是**一只票定全池窗口**：`files[0]` 是 glob 排序后的第一只。
#
#  ★ 实测：`files[0]` = `000001.SZ.parquet`，它的 `date.max()` = **2026-10-14**，
#    而全仓其余 20+ 份产物钉的是 **2026-10-12**
#    ⇒ 已出的那 5 份 k 产物窗口比别人**长 2 天**，与所有别的东西不可比。
#    `CLAUDE.md` 第 1266 行早就为**另一个脚本**记过同一件事并显式绕开过 ——
#    也就是说这个坑**被遇到过、被绕过了、但没有被根治**。
#
#  修法：默认钉死 + 留空即拒 + 删掉那个永远不可能触发的回落。


def test_the_default_window_end_is_pinned_rather_than_empty():
    """★ 不传 `--end` 时必须拿到**钉死的字符串**，不是空串。

    ⚠️ 这条能便宜地问出来，是因为 CLI 被拆成了 `_parse_args`；
      拆之前，「默认是什么」只能在跑完 10 分钟回测之后才看得到 ——
      **判据不可问，就等于没有守卫**。
    """
    assert M._parse_args([]).end == M.WINDOW_END_PINNED
    assert M.WINDOW_END_PINNED == "2026-10-12", (
        f"钉死的末端变成了 {M.WINDOW_END_PINNED!r} —— "
        f"改它是一个**判据变更**，得先改预注册，不是改个常量"
    )


def test_the_pinned_end_is_the_same_one_every_other_report_uses():
    """★ 单一真相源：`cap_weighted_benchmark` / `basket_weighting` 必须同值。

    ⚠️ 三处各写一个字符串字面量，迟早会漂；这里钉的是「**漂了会红**」，
      不是「它们相等」——后者在只有两处时会漏掉第三处。
    """
    import importlib.util as _u
    for name, mod in (("cap_weighted_benchmark", "cap_weighted_benchmark.py"),
                      ("basket_weighting", "basket_weighting.py")):
        spec = _u.spec_from_file_location(f"_pin_{name}", _SCRIPT.parent / mod)
        m = _u.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.WINDOW_END_PINNED == M.WINDOW_END_PINNED, (
            f"{name} 的钉死末端是 {m.WINDOW_END_PINNED!r}，"
            f"而 backtest_walk_forward_weights 是 {M.WINDOW_END_PINNED!r} "
            f"⇒ 这三份产物的窗口不可比"
        )

def _pinned_end_definitions(scripts_dir=None):
    """★ **扫出**全仓自己定义 `WINDOW_END_PINNED` 的地方与它们的值。

    ## 为什么必须是「扫」而不是「列」
    上一条守卫是个**手维护的三元组**。而它的 docstring 自己就写着
    「后者在只有两处时会漏掉第三处」—— 话音刚落，第三处就出现了：
    `factor_width_mde.py`（2026-10-15，陈旧性守卫触发重跑时暴露）。

    ⇒ **守卫的枚举方式决定了它能发现第几个。** 人维护的清单永远差一个，
      扫出来的清单自动包含新来者。
    ⇒ 用 AST 而不是子串：本文件与 `factor_width_mde.py` 的**注释里都引用了**
      `WINDOW_END_PINNED = "2026-10-12"` 这段原文（记录旧代码），
      子串扫描会把注释当成定义。
    """
    import ast
    out = {}
    for py in sorted((scripts_dir or _SCRIPT.parent).glob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(tg, ast.Name) and tg.id == "WINDOW_END_PINNED"
                       for tg in node.targets):
                continue
            v = node.value
            out[py.name] = (v.value if isinstance(v, ast.Constant)
                            and isinstance(v.value, str) else f"<非字面量 {ast.unparse(v)}>")
    return out


def test_every_copy_of_the_pinned_end_agrees(tmp_path):
    """★ 第 137 层的正面对策：两份副本之间必须有守卫，否则它们会漂。

    CLAUDE.md 第 1311 行声称这几份「**共用同一个常量**」——
    实测是**各自一份字面量，没有任何 import**。那句话本身就是第 137 层的现场。
    ⇒ 这里钉的不是「它们相等」（那是上一次手维护版本的写法），
      是「**扫到的每一份都必须相等，且必须至少扫到一份**」。
    """
    defs = _pinned_end_definitions()
    assert defs, (
        "扫不到任何 WINDOW_END_PINNED 定义 ⇒ **探测器坏了**，"
        "本条会恒绿（第 131 层：只验探测器 = 假信用）")
    bad = {k: v for k, v in defs.items() if v != M.WINDOW_END_PINNED}
    assert not bad, (
        f"这些脚本各自写了一份钉死末端，与 {M.WINDOW_END_PINNED!r} 不一致："
        f"{bad}\n⇒ 它们的产物窗口不可比，而且**不会有人发现**")


def test_the_pinned_end_detector_really_sees_a_divergent_copy(tmp_path):
    """★ 探测器的正反控。⚠️ 少一条，本条主守卫就是恒绿。"""
    (tmp_path / "aaa_ok.py").write_text('WINDOW_END_PINNED = "2026-10-12"\n',
                                        encoding="utf-8")
    (tmp_path / "bbb_bad.py").write_text('WINDOW_END_PINNED = "2026-10-14"\n',
                                         encoding="utf-8")
    (tmp_path / "ccc_comment.py").write_text(
        '# 旧代码：WINDOW_END_PINNED = "2026-10-14"  ← **注释不是定义**\n',
        encoding="utf-8")
    got = _pinned_end_definitions(tmp_path)
    assert got == {"aaa_ok.py": "2026-10-12", "bbb_bad.py": "2026-10-14"}, got


def test_factor_width_mde_consumes_the_pin_instead_of_writing_a_fourth_copy():
    """★ 第三个受害者必须**消费**它，不许再抄一份。

    ⚠️ 它**曾经没有这个常量**（`--end` 默认 `""`），
      修法不是给它加一份字面量 —— fwm 本来就已经 import
      `backtest_walk_forward_weights`（`ENTRY_LAG` 等），加进同一行是零成本的真去重。

    🚨 **第一版只断言了「值相等」，而变异 N3 存活了** ——
      让 fwm 自己写一份**同值**字面量，`WINDOW_END_PINNED` 与
      `_parse_args([]).end` 全都照样通过。
      ⇒ 测试名声称守的是「consumes 而非 writes」，实际守的是「值相同」。
      ⇒ **两份同值副本正是第 137 层要防的东西**：一旦有人只改其中一份，
        这条守卫立刻瞎。⇒ 断言必须落在**来源**上：
      **本文件不许出现模块级定义**，因为它必须消费那一份。
    """
    assert "factor_width_mde.py" not in _pinned_end_definitions(), (
        "factor_width_mde 自己又写了一份 WINDOW_END_PINNED ⇒ 那是第 4 份副本，"
        "与其它几份之间没有守卫，会漂。\n⇒ 从 backtest_walk_forward_weights "
        "import（它已经有这条 import）。")

    import importlib.util as _u
    spec = _u.spec_from_file_location("_pin_fwm", _SCRIPT.parent / "factor_width_mde.py")
    m = _u.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.WINDOW_END_PINNED == M.WINDOW_END_PINNED, (
        f"factor_width_mde 看到的是 {m.WINDOW_END_PINNED!r}，"
        f"而钉死值是 {M.WINDOW_END_PINNED!r}")
    # ⇒ 而且它的**默认实参**必须就是这个值，不是另一个常量
    assert m._parse_args([]).end == M.WINDOW_END_PINNED


def test_a_blank_window_end_is_refused_with_a_diagnosable_message(tmp_path, capsys):
    """末端留空必须**拒**且**说清为什么**。

    ⚠️ 锚在「末端」这个**判据专属**的字上，而不是 `end` ——
      `build_panel` 自己的报错也含 `end`，守卫会守成「响」
      而不是「响得可诊断」（第 121 层入口二的同族）。
    """
    dummy = tmp_path / "m.parquet"          # 只过 `is_file()`，读不到它
    dummy.write_bytes(b"")

    rc = M.main(["--matrices", str(dummy), "--end", "   "])

    err = capsys.readouterr().err
    assert rc == 2, f"末端留空被放行了（rc={rc}）"
    assert "末端" in err, (
        f"拒了但没说为什么，读者只能自己去猜是哪一条纪律：{err[-300:]!r}"
    )
