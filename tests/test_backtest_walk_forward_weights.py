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
import ast
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


# ── ⑥ 第 9 个暴露 `expo_logcap`（市值暴露，**只读测量**）───────────────
#
# ⚠️ 本节守的是三件事，且每件都有它的**反面失效**：
#   1. **测量与加权必须是两个开关** —— 合并就等于「要不要量市值」
#      变成「要不要按市值加权」的副作用（形状 6）。
#   2. **缺档必须 NaN，绝不回落成 0** —— 0 会被 `robust_z` 当成一个真实的
#      观测值（"这只票市值为零"），而它是"没量到"。
#   3. **标准化必须与那 8 列同一段代码** —— 单列 t/p 对缩放不变，
#      所以换个尺子主判据可能照样过，而**联合 F / R² 会分家**。
#
# 预注册：`docs/TODO_cap_exposure_2026-10-15.md`（含补记 1：口径钉死为 `robust_z`）。

class _StubCapTable:
    """只实现 `cap_on` 的假市值表。

    🚨 刻意**不实现** `a_shares_on`：真去调它会 AttributeError ⇒ 立刻红。
      这是「钉住调的是哪一个方法」最省事的办法 —— 与其 grep 源码，
      不如让错误的那个方法**根本不存在**。
    """

    def __init__(self, caps, default=float("nan")):
        self._caps = dict(caps)
        self._default = default

    def cap_on(self, code, day):
        return float(self._caps.get((code, day), self._default))


def _cs(n=200, seed=7):
    """当日全池横截面：`code` + 一个因子列 + 一批对数正态的市值（肥尾）。"""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "code": [f"{i:06d}.SZ" for i in range(n)],
        "mom20": rng.normal(size=n),
    }), np.exp(rng.normal(0.0, 1.0, n))


def _held_frame(day, codes):
    return pd.DataFrame({"pred_date": [day] * len(codes),
                         "code": list(codes),
                         "direction": ["up"] * len(codes)})


_DAY = "2024-03-01"


def test_the_ninth_column_is_present_and_all_nan_when_no_cap_table_is_given():
    """**不许缺列，也不许填 0。** 两种都是把「没量到」说成「量到了 0」。"""
    uni, _ = _cs()
    codes = list(uni["code"][:10])
    res = _held_frame(_DAY, codes)
    out = M._up_exposure(res, {_DAY: uni}, [_DAY], ["mom20", M.LOGCAP_COL])

    assert M.LOGCAP_COL in out, "列直接不见了 —— 下游会按位置对齐，NaN 才会让回归自己剔掉"
    assert len(out[M.LOGCAP_COL]) == 1
    v = out[M.LOGCAP_COL][0]
    assert math.isnan(v), f"无 CapTable 时应为 NaN，实得 {v!r}"
    assert v != 0.0, "0 会被 robust_z 当成一个真实的零市值观测"
    assert not math.isnan(out["mom20"][0]), "对照：因子列此刻必须有值（否则本条是恒真）"


def test_with_a_cap_table_the_ninth_column_is_finite_and_not_constant():
    """**反控**：这一列不许是装饰（常数列回归时秩为 0，什么也证明不了）。"""
    uni, caps = _cs()
    codes = list(uni["code"][:10])
    tbl = _StubCapTable({(c, _DAY): float(caps[i])
                         for i, c in enumerate(uni["code"])})
    out = M._up_exposure(res=_held_frame(_DAY, codes), xs={_DAY: uni},
                         days=[_DAY], factors=["mom20", M.LOGCAP_COL],
                         cap_table=tbl)
    v = out[M.LOGCAP_COL][0]
    assert not math.isnan(v), v
    # 10 只等权持仓的 z 均值**不可能恰好是 0**（除非持仓恰好是全体均值），
    # 且它落在 [-1, 1] 内（z 的横截面均值量级）—— 断言一个有范围的区间。
    assert -1.0 <= v <= 1.0, v
    assert v != 0.0, "恰好 0 说明取的是持仓内部排名（`_up_exposure` 的第 2 条纪律）"


def test_the_cap_column_is_standardized_by_the_very_same_robust_z():
    """**同尺子** —— 与那 8 列逐字同一段代码。

    反面失效：写成裸 `(x-mean)/std` 时，单列 t/p 仍不变（缩放不变性），
    所以主判据可能照样过，而联合量会分家。
    """
    uni, caps = _cs()
    codes = list(uni["code"][:40])
    tbl = _StubCapTable({(c, _DAY): float(caps[i])
                         for i, c in enumerate(uni["code"])})
    out = M._up_exposure(_held_frame(_DAY, codes), {_DAY: uni}, [_DAY],
                         ["mom20", M.LOGCAP_COL], cap_table=tbl)

    series = M._logcap_series(uni, _DAY, tbl)
    want = float(M.robust_z(series)[uni["code"].isin(codes)].mean())
    assert out[M.LOGCAP_COL][0] == pytest.approx(want, rel=0, abs=1e-12)

    # ★ 正控：这个夹具**必须能分辨**两个尺子，否则上面那条是恒真的。
    #   对数正态是肥尾，缩尾与否会改变均值/sd ⇒ 裸 z 会算出别的数。
    lc = series.to_numpy()
    naive = float(((lc - lc.mean()) / lc.std())[uni["code"].isin(codes)].mean())
    assert not math.isclose(naive, want, rel_tol=1e-9), (
        "★ 夹具分辨不了 robust_z 与裸 z —— 本条守卫是装饰")


def test_a_missing_or_degenerate_cap_reads_as_nan_and_never_as_zero():
    """NaN / 负值 / 0 市值 ⇒ NaN。**不许 log 后落成 -inf 或 clip 成 0。**"""
    uni, caps = _cs(n=6)
    tbl = _StubCapTable({("000000.SZ", _DAY): 0.0,
                         ("000001.SZ", _DAY): -5.0,
                         ("000002.SZ", _DAY): float("nan"),
                         ("000003.SZ", _DAY): float(caps[3]),
                         ("000004.SZ", _DAY): float(caps[4]),
                         ("000005.SZ", _DAY): float(caps[5])})
    s = M._logcap_series(uni, _DAY, tbl)
    assert math.isnan(s.iloc[0]), "市值 0 ⇒ log 不存在，必须 NaN 而不是 -inf"
    assert math.isnan(s.iloc[1]), "负市值必须 NaN"
    assert math.isnan(s.iloc[2]), "缺档必须 NaN（下界 clip 到 0 会变成「一文不值」）"
    assert not (s.to_numpy() == 0).any(), "整列不许出现 0 —— robust_z 会被它拽住尺度"
    assert s.iloc[3:].notna().all(), "有效值必须保留"


def test_the_measurement_is_structurally_independent_of_the_weighting_switch():
    """★ AST 守卫：`cap_table` 在 `run_year` 里**只**许出现在签名、docstring
    与 `_up_exposure(...)` 这一个调用里。

    ⚠️ 为什么不用运行时对比：`run_year` 要跑完整引擎，比 AST 贵两个数量级；
      而这里要证明的是**结构事实**（它没有第二条去路），AST 恰好直接。
    """
    import ast
    src = _SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_year")

    args = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    assert "cap_table" in args and "cap_ctx" in args, args
    assert args.index("cap_table") != args.index("cap_ctx"), \
        "两个参数必须分开 —— 合并即形状 6（一个开关两种含义）"

    doc = ast.get_docstring(fn) or ""

    # 先收集**被允许**的那些读取点：`_up_exposure(..., cap_table=cap_table)`
    # 的实参。必须按**节点身份**记，不能按名字记 —— 按名字记等于没记。
    allowed = set()
    forwarded_to = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        callee = ast.unparse(node.func)
        for kw in node.keywords:
            if kw.arg == "cap_table":
                forwarded_to.append(callee)
                allowed.update(id(n) for n in ast.walk(kw.value)
                               if isinstance(n, ast.Name))
    assert forwarded_to and set(forwarded_to) == {"_up_exposure"}, (
        f"cap_table 被转发到了 {forwarded_to} —— 只能给 _up_exposure")

    # 再抓**所有**读取：凡是 Load 上下文里的 `cap_table`，必须都在白名单里。
    stray = [ast.unparse(n) for n in ast.walk(fn)
             if isinstance(n, ast.Name) and n.id == "cap_table"
             and isinstance(n.ctx, ast.Load) and id(n) not in allowed]
    assert not stray, f"cap_table 在这些非测量位置被读取：{stray}"
    assert "只读测量" in doc, "docstring 必须写明它是测量而不是开关"


def test_the_ninth_column_never_enters_the_attribution_verdict():
    """第 9 列**不进** `factor_attribution.FACTOR_COLS`。

    ⚠️ 那 8 列是它自己预注册过的判决口径（第 145 层），往里加 =
      换判据后重跑 = 撤回已提交结论。要多测，必须另起一份预注册。
    """
    fa = importlib.util.spec_from_file_location(
        "_fa", _SCRIPT.parent / "factor_attribution.py")
    m = importlib.util.module_from_spec(fa)
    fa.loader.exec_module(m)
    assert M.LOGCAP_COL not in m.FACTOR_COLS, "第 9 列混进了判决口径"
    assert len(m.FACTOR_COLS) == 8, m.FACTOR_COLS


# ══════════════════════════════════════════════════════════════════════
# ⑦ 市值对照列 `logcap_raw` + `EXPO_COLS` 单一真相源
# ══════════════════════════════════════════════════════════════════════
#
# ★ 这一节的第一条守卫是被**一次 408 秒的真跑**逼出来的：
#   `dg = _daily_frame(daily_chunks, FACTOR_NAMES + [LOGCAP_COL])`
#   `FACTOR_NAMES` 是 tuple ⇒ `TypeError` ——
#   **崩在面板加载完、逐年结果全算完之后**，而 `tests/` 只调 `run_year`，
#   **从不走 `main()` 的这一句**。
#   更贵的是下一层：随后那份补丁把**同样的写法抄了第二遍**，
#   于是同一个 TypeError 会原样再犯一次，只是换个行号。
#   ⇒ 修法不是在这两处各加一次 `list()`，是让清单**单点持有**，
#     并用一条 AST 守卫让「tuple + list」这个错误**没法写出来**。

def _src_tree():
    return ast.parse(_SCRIPT.read_text(encoding="utf-8"))


def test_tuple_plus_list_is_not_writable_in_this_script():
    """全文件禁止 `Name + [ ... ]` 形态。

    为什么是 AST 而不是正则：`FACTOR_NAMES + [...]` 里没有 `list(`，
    正则要么漏掉它，要么去匹配注释里的话（第 13 条目）。
    ⚠️ 这是**故意偏严**的守卫，但方向是便宜的：
      真撞上时改法只有 `list(X) + [...]` 一行，
      而漏掉它的代价是**再花 408 秒撞同一个 TypeError**。
    """
    bad = []
    for n in ast.walk(_src_tree()):
        if (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add)
                and isinstance(n.left, ast.Name)
                and isinstance(n.right, ast.List)):
            bad.append(f"line {n.lineno}: {ast.unparse(n)[:70]}")
    assert not bad, (
        "tuple 与 list 直接相加（`FACTOR_NAMES` 是 tuple）⇒ TypeError，"
        f"且它只崩在 408 秒之后：{bad}")


def test_the_exposure_column_list_has_exactly_one_owner():
    """暴露列清单**只有一处**定义，且它就是模块级那个常量。

    三处副本 = 第 17 条目（「同一语义被处理 N 次，第 N+1 处必然是漏的」）。
    """
    n_def = 0
    for n in ast.walk(_src_tree()):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "EXPO_COLS"
                for t in n.targets):
            n_def += 1
    assert n_def == 1, f"EXPO_COLS 有 {n_def} 处定义，必须恰好 1 处"


def test_the_authoritative_column_list_is_actually_consumable():
    """★ **把 408 秒的判据变成 3 毫秒的可问判据。**

    这就是「判据不可问就等于没有守卫」那条通则的正面用法：
    崩溃本来埋在「跑完整场回测之后」，现在直接把它那份清单
    喂给真正会崩的那个函数。
    """
    assert len(M.EXPO_COLS) == len(M.FACTOR_NAMES) + 2, M.EXPO_COLS
    assert M.EXPO_COLS[-2:] == [M.LOGCAP_COL, M.LOGCAP_RAW_COL], M.EXPO_COLS
    # 两段 chunk —— 单段时「join」与「堆叠」结果相同，测不出第 11 条那类错
    chunks = [
        {"dates": ["2024-03-01", "2024-03-04"],
         "excess": [0.001, 0.002],
         "exposure": {c: [0.1, 0.2] for c in M.EXPO_COLS}},
        {"dates": ["2024-03-05"],
         "excess": [0.003],
         "exposure": {c: [0.3] for c in M.EXPO_COLS}},
    ]
    dg = M._daily_frame(chunks, M.EXPO_COLS)
    assert isinstance(dg, pd.DataFrame)
    # ⚠️ 第一版这里写的是 `["dates", "excess", *M.EXPO_COLS]` —— **我编的**。
    #   读 `_daily_frame` 的真身（`src/backtest_walk_forward_weights.py:352`）
    #   才是 `["gross_excess"] + [f"expo_{f}"]`，而**日期是索引不是列**。
    #   这是「断言对、成因错」的第三种形态的反面：
    #   它红是因为**我的夹具知识错了**，不是因为实现错了（第 112 条目：
    #   红先归因，再决定改哪一边 —— 这里两边都不是实现）。
    assert list(dg.columns) == ["gross_excess",
                                *[f"expo_{c}" for c in M.EXPO_COLS]], \
        list(dg.columns)
    assert dg.index.name == "date", dg.index.name
    assert len(dg) == 3
    # 两段 chunk 的拼接必须**按日期并集**而不是按行堆叠（第 11 条目：
    # 单段 chunk 时「join」与「堆叠」结果完全一样，测不出那一类错）。
    assert list(dg.index) == ["2024-03-01", "2024-03-04", "2024-03-05"], \
        list(dg.index)
    assert dg["expo_" + M.LOGCAP_RAW_COL].tolist() == [0.1, 0.2, 0.3], \
        dg["expo_" + M.LOGCAP_RAW_COL].tolist()


def test_the_raw_z_control_column_really_differs_from_robust_z():
    """**正控**：对照臂必须真的与主列不同，否则它是装饰。

    ⚠️ 没有这一条，`logcap_raw` 可以恒等于 `expo_logcap` 而全仓绿 ——
    那就是「有两列」这个外观在骗人（第 13 条目的文档满足形态）。

    🚨 **两条我「以为」的性质都是假的，都是先测量才没写进去的**：

    ① 「无肥尾时两者应当一致」——**假**。`robust_z` 先把 q01/q99 缩尾得到 `ref`，
       再用 `ref` 的 mean/std，而缩尾**即使没有极值点也会动 mean/std**：
       实测 `[1..6]` 上 `ref.std()=1.8442` vs 裸 sd `1.8708` ⇒ `max|Δ| = 0.0193`。
       ⇒ 两列**永远**不同，不只在肥尾时。
    ② 「差异随肥尾程度单调增大」——**假**，而且是**非单调**：
       200 个标准正态 + 1 个 `1000` ⇒ `max|Δ| = 1128.7`；
       + 5 个 ⇒ **0.0000**；+ 20 个 ⇒ **0.0000**。
       原因：极值点一旦多到越过 q99 切点，它**自己进了 `ref`**，
       于是 `ref.std()` ≈ 裸 sd ⇒ 两列逐位相同。
       ⇒ 差异最大处恰是「**少数几个**极值点」，不是「最肥尾」。

    真正稳定且有判别力的性质是**方向**（实测：裸 14.106 / 稳健 1142.818）：
    单个极值点把**裸 sd** 拽大，于是裸 z 把**所有其余观测一起缩小**；
    `ref` 的 sd 不含它，所以 `robust_z` 在该点给出**大得多**的 z。
    ⇒ 断言方向（`max|raw| < max|rob|`），不断言数值 —— 数值是夹具的，
      方向是性质的。方向反了 ⇒ 只能由「两个函数被换了个个儿」造成，
      而那正是这一列要防的缺陷。
    """
    from aplus.factor import robust_z

    rng = np.random.default_rng(7)
    s = pd.Series(np.append(rng.normal(size=200), [1000.0]))
    raw = M._raw_z(s)
    rob = robust_z(s)

    gap = float(np.max(np.abs(raw.to_numpy() - rob.to_numpy())))
    assert gap > 1.0, (
        f"裸 z 与 robust_z 只差 {gap:.4g} —— 对照臂是装饰（恒等于主列）")

    # ★ 方向，不是幅度。裸 sd 被极值点拽大 ⇒ 裸 z 把其余观测缩小。
    assert raw.abs().max() < rob.abs().max(), (
        f"裸 z 极值 {raw.abs().max():.3f} ≥ robust_z 极值 "
        f"{rob.abs().max():.3f} —— 缩尾 sd 未生效，或两个函数被换了个儿")

    # 且「差异巨大」这个前提本身要有牙：极值点越界时两列必须显著分离
    assert raw.abs().max() < 20.0 < rob.abs().max(), (
        f"夹具不提供判别力：裸 {raw.abs().max():.3f} / 稳健 {rob.abs().max():.3f}")


def test_raw_z_returns_nan_rather_than_zero_or_inf_on_a_degenerate_series():
    """常数列 ⇒ sd=0 ⇒ 必须是 NaN。

    ⚠️ 不许填 0：「z=0」说的是「恰好在均值上」，
      而常数列的真实情况是**没有任何量级可言**（第 89 层的语义）。
    """
    out = M._raw_z(pd.Series([7.0, 7.0, 7.0]))
    assert out.isna().all(), f"常数列得到 {out.to_list()}，必须是 NaN"
    assert not np.isinf(out.to_numpy()).any()


def test_the_two_cap_columns_are_both_nan_when_no_cap_table_is_given():
    """没有 `CapTable` 时**两列**都是整列 NaN（不是 0、不是缺列）。

    ⚠️ 第一版这里造了个 `type("R", (), {"attrs": {}})()` 当 `res` ——
      而 `_up_exposure` 第一件事就是 `res.empty`，那个壳**没有** `.empty`
      ⇒ `AttributeError`。**红的原因是夹具不合法，不是实现有问题**。
      ⇒ 换成既有夹具 `_cs()` + `_held_frame()`（真 DataFrame，真持仓）。

    并且必须带一条**正控**：同一调用下因子列此刻**有值**，
    否则「两列全 NaN」也可能是「整张表全 NaN」—— 那是一条恒真的守卫
    （第 112 条目：空数据不会让测试变红，只会让它恒真）。
    """
    uni, _ = _cs()
    codes = list(uni["code"][:10])
    res = _held_frame(_DAY, codes)
    out = M._up_exposure(res, {_DAY: uni}, [_DAY],
                         ["mom20", M.LOGCAP_COL, M.LOGCAP_RAW_COL],
                         cap_table=None)
    for c in (M.LOGCAP_COL, M.LOGCAP_RAW_COL):
        assert c in out, f"{c} 直接不见了 —— 缺列会让下游按位置对齐"
        assert np.isnan(np.asarray(out[c], float)).all(), (
            f"{c} = {out[c]}，没有市值表时必须是整列 NaN，绝不是 0")
    # ★ 正控：因子列此刻必须有值 ⇒ 上面两条不是「整张表空」的假绿
    assert not math.isnan(out["mom20"][0]), (
        "因子列也是 NaN —— 夹具失效（不是守卫失效）")
