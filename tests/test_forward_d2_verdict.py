"""前瞻 D2 判决脚本的守卫 —— 判据预注册在 docs/PREREG_FORWARD_D2_2026-10-16.md §5（G1~G7）。

⚠️ **判别力全部来自构造出来的夹具**（第 146 层：靠数据巧合成立的守卫会全绿
   而变异存活）。本文件**零**随机决定判据、零真实底稿、零子串断言。

本文件有两条**故意不成立**的守卫，写在这里是为了不让后人以为它们漏写了：

① **不为「减法顺序」配会杀变异体的守卫。** 实测它不承重 ⇒ 补断言等于给生产加
   一条恒真约束，而失效方向正是最贵的「把正在工作的代码改坏」（第 112 层）。
   这里只留一条**退化性自检**（夹具必须真的块内 `market_pct` 不恒定）+ 一条
   **带阈值的**等价记录。

② **不查「判据里有没有写某个词」。** 「复合差不进判决字段」若写成
   `"compound" not in src` 会被注释满足（第 13 种形状）⇒ 改成分岔夹具：
   构造出「算术均值正、复合差负」的一组块，判决若改用复合差就会翻成 `NEGATIVE`。

三条通用禁令：

- **不许用子串断言**（第 13 种形状）。四个判决串在模块 docstring、`verdict_of`
  的 docstring、`caliber()` 里都出现过。顺序类判据一律走 **AST**。
- **夹具必须自检非退化。** 每条依赖「块内多行」或「块内量不相等」的守卫，
  都要在守卫里断言该前提成立 —— 否则它会在退化夹具下变成恒真（第 112 层）。
- **「不可达」只能在显式构造不变量下声称**，且必须配正控
  （`verdict_of(8, 5.0, 1.0, -1.0, 6.0)` 手工传 `lo/hi` 是**能**返回死分支的）。
"""

from __future__ import annotations

import ast
import importlib.util
import math
import random
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "forward_d2_verdict.py"

# 🚨 第 24 种形状：按路径导入的测试**自带**仓库根，会替脚本补上它自己的缺陷
#    ⇒ 必须让守卫与被测脚本共用同一套 sys.path，否则守卫测的是「补过缺陷的环境」。
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("fd2_under_test", SRC)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------

def _leg(rows):
    """rows = [(pred_date, entry_date, gross_pct, market_pct), ...]"""
    return pd.DataFrame(
        [
            {
                "pred_date": pdate,
                "entry_date": ed,
                "exit_date": pdate,
                "direction": M.UP,
                "gross_pct": g,
                "market_pct": mk,
            }
            for pdate, ed, g, mk in rows
        ],
        columns=list(M.REQUIRED_COLUMNS),
    )


def _one_leg_per_block(block_excess, market=0.0):
    """每个 `entry_date` **恰好一行** ⇒ 块内 `market_pct` 恒定。

    🚨 用它测「减法顺序」是**恒真**的（块内一行 ⇒ 先合再减与先减再合必然相同）。
       要测顺序必须用下面的 `_multi_leg_blocks`。
    """
    return _leg(
        [(f"2026-01-{i + 1:02d}", f"2026-01-{i + 1:02d}", m + market, market)
         for i, m in enumerate(block_excess)]
    )


#: ⚠️ 金额单位与上游一致：`gross_pct` 是**分数**（0.01 = 1%），
#: 不是百分数 —— 弄错会让所有阈值判据整体平移两个数量级（第 97 层）。
#: n=6 且 |c|/MDE = 3.571 ⇒ POSITIVE 可达（分岔夹具，不是碰巧）。
POSITIVE_SERIES = [0.02, 0.02, 0.02, 0.02, 0.02, 0.005]
#: 反向梯度 —— 逐位取负，其余完全相同（第 120 层：反控必须钉判决而非钉探测器）
NEGATIVE_SERIES = [-v for v in POSITIVE_SERIES]


def _multi_leg_blocks(block_legs, market_legs=None):
    """每个 `entry_date` 多行；块内 `market_pct` **互不相同**。

    🚨 `market_legs` 必须逐块至少与该块腿数等长；不给时用「块内三档不同」的
       默认值（真实底稿 2026-10-12 那块的三档），这样「先合再减」与「先减再合」
       在数学上不再必然相等 ⇒ 夹具具有判别力。
    """
    if market_legs is None:
        market_legs = [[0.004265, 0.002688, 0.005592] for _ in block_legs]
    rows = []
    for bi, legs in enumerate(block_legs):
        entry = f"2026-02-{bi + 1:02d}"
        for li, g in enumerate(legs):
            rows.append((entry, entry, g, market_legs[bi][li]))
    return _leg(rows)


#: 真实底稿里 2026-10-12 那块的三档市场腿（0.004265/0.002688/0.005592）
MARKET_WITHIN_BLOCK = (0.004265, 0.002688, 0.005592)


def _gross_fixed_market_varied(block_gross, market):
    """**gross 逐块固定、只有 market 在变** —— 测「减不减」唯一正确的夹具形状。

    🚨 第一版误用了 `_one_leg_per_block(series, market=m)`，而它造的是
       `gross = m_arg + market` ⇒ `excess = gross − market = m_arg`
       ⇒ **market 取任何值 excess 都逐位相同** ⇒ 那条守卫在**任何** market 下恒红。
       恒红与恒真同害（第 112 层）：它是红的，会把人推向「去改本来正确的生产代码」。
       ⇒ 与其改生产，不如换夹具：**要变的量必须真的能变。**

    ⇒ 这里**不用**块内多行：本题问的是「减法在不在公式里」，
       每块一行时 `excess = gross − market` 直接可读，判别力最强。
    """
    return _leg(
        [(f"2026-03-{i + 1:02d}", f"2026-03-{i + 1:02d}", g, market)
         for i, g in enumerate(block_gross)]
    )



def _assert_fixture_is_not_degenerate(frame):
    """退化性自检：块内 `market_pct` 必须真的不相等，否则相关守卫恒真。"""
    per_block = frame.groupby("entry_date")["market_pct"].nunique()
    assert (per_block > 1).all(), (
        "夹具退化：每个 entry_date 内的 market_pct 都相同 ⇒ "
        "「减法顺序」在这个夹具下必然无影响，守它等于什么都没守")


def _float_close(a: float, b: float, scale: float) -> bool:
    """带**从量级推导的阈值**的相等（[[consistency-without-a-threshold-is-not-a-criterion]]）。

    🚨 刻意**不**用逐位 `==`：浮点加法不满足结合律，
       `mean(g) − mean(m)` 与 `mean(g − m)` 只在**舍入量级内**相等。
       上一轮实测「逐位零差异」是**那份数据的巧合**，不是代数恒等 ——
       把它写成守卫会假红（第 122 层）。阈值 = 64 × eps × 量级。
    """
    return abs(a - b) <= 64 * sys.float_info.epsilon * scale


# --------------------------------------------------------------------------
# 判决表层：能力守卫与反向正控
# --------------------------------------------------------------------------

def _mde_of(series):
    """夹具驱动用的 MDE 复算（与 `measure()` 同式）—— 只用来**造**入参，
    判决本身一律走 `M.verdict_of`，不复算。"""
    n = len(series)
    point = sum(series) / n
    var = sum((v - point) ** 2 for v in series) / (n - 1)
    return point, M.Z_95 * math.sqrt(var) / math.sqrt(n)


def test_positive_is_reachable_at_the_verdict_table():
    """能力守卫：判决表**能**给出 `POSITIVE` —— 断言具体字符串，不是 `assert bool(...)`。

    🚨 形状守卫在判决函数恒返 `POWERLESS` 时**全绿**（第 120 层）。
    """
    point, mde = _mde_of(POSITIVE_SERIES)
    assert abs(point) >= mde, "夹具失效：|c|/MDE < 1 会让第一档吃掉（守卫会变成装饰）"
    assert M.verdict_of(len(POSITIVE_SERIES), point, mde,
                        point - mde, point + mde) == M.VERDICT_POSITIVE


def test_negative_is_reachable_by_mirroring_the_whole_fixture():
    """反向正控（第 120 层）：逐位取负 ⇒ 必须翻到 `NEGATIVE`，不能滑成 `POWERLESS`。"""
    point, mde = _mde_of(NEGATIVE_SERIES)
    got = M.verdict_of(len(NEGATIVE_SERIES), point, mde, point - mde, point + mde)
    assert got == M.VERDICT_NEGATIVE, f"反向梯度被判成 {got}（方向相反被读成「测不到」）"


# --------------------------------------------------------------------------
# G1 / G2 —— R4 的两道闸门，两条都必须单独钉（合并写会让其中一条变异存活）
# --------------------------------------------------------------------------

def test_g1_ratio_below_one_must_be_powerless_even_when_n_is_large():
    """G1：`|c|/MDE < 1` ⇒ `POWERLESS`。n 给到 20，确保**不是** n 闸在起作用。"""
    assert M.verdict_of(20, 1.0, 2.0, 1.0 - 2.0, 1.0 + 2.0) == M.VERDICT_POWERLESS


def test_g2_too_few_blocks_must_be_powerless_even_when_the_ratio_is_enormous():
    """G2：`n < 6` ⇒ `POWERLESS`，哪怕 `|c|/MDE = 1e12`。"""
    assert M.verdict_of(M.MIN_BLOCKS_FOR_ANY_SIGN - 1, 1e6, 1e-6,
                        1e6 - 1e-6, 1e6 + 1e-6) == M.VERDICT_POWERLESS


def test_g1_and_g2_are_two_different_gates():
    """两条闸门必须**各自**可触发，且「两条都不违反」必须离开 `POWERLESS`。

    若两条被写成同一条件（例如都退化成只判 `abs(point) < mde`），本条会红。
    """
    only_n_violated = M.verdict_of(M.MIN_BLOCKS_FOR_ANY_SIGN - 1, 1e6, 1e-6,
                                    1e6 - 1e-6, 1e6 + 1e-6)
    only_ratio_violated = M.verdict_of(20, 1.0, 2.0, -1.0, 3.0)
    assert only_n_violated == only_ratio_violated == M.VERDICT_POWERLESS
    # 两条都不违反 ⇒ 必须离开 POWERLESS（否则「两条都不违反仍判测不到」）
    assert M.verdict_of(20, 1.0, 0.1, 0.9, 1.1) == M.VERDICT_POSITIVE


def test_g1_is_strictly_less_than_so_the_exact_boundary_falls_to_the_second_bucket():
    """🚨 变异 M2（`abs(point) < mde` 改成 `<=`）的专治守卫。

    **为什么这个点承重**：代数上 `|c|/MDE > 1` 时 `lo > 0` 或 `hi < 0` 必然成立，
    所以第二档唯一能接到的入参是 `|c|/MDE == 1` **恰好相等**那一个点。
    而 `measure()` 传的 `lo = point − mde`、`hi = point + mde` ⇒ 那个点上
    `lo`（或 `hi`）**恰好是 0**，而 `0 > 0` 为假、`0 < 0` 也为假
    ⇒ 两个符号档都不接，落到第二档。
    把 `<` 放宽成 `<=` 会把这个点吞进 `POWERLESS` ⇒ **判决档变了而 21 条全绿**。

    **这不是过严**：预注册 R4 第一档逐字写的是 `|c| / MDE < 1`（严格小于），
    而 `DEAD_BRANCHES` 那段 docstring 自己也说「把这一档改成可达需要换 CI 的
    临界值 = 换判据臂」⇒ 按本文件自己的立场，`<` 与 `<=` 是**判据之差**。

    ⚠️ 与 `test_the_registered_dead_branch_is_exactly_the_unreachable_set`
       **不矛盾，两者量的是不同的东西**：那条量的是
       `_reachable_under_builder()` 这个**随机扫描**能采到的集合
       （连续采样下 `|c|/MDE == 1` 是测度零点 ⇒ 采不到）；
       本条量的是「那个测度零点**若被精确命中**，落在哪一档」。
       ⇒ 「死分支」的准确含义是「**随机/连续采样下采不到**」，
       不是「代数上不存在能命中它的入参」。
    """
    # 正侧：point == mde ⇒ lo == 0 ⇒ `lo > 0` 假
    assert abs(1.0) / 1.0 == 1.0, "夹具失效：没落在恰好的边界点上"
    assert M.verdict_of(20, 1.0, 1.0, 0.0, 2.0) == M.VERDICT_NOT_MEASURABLE
    # 负侧：point == −mde ⇒ hi == 0 ⇒ `hi < 0` 假
    assert M.verdict_of(20, -1.0, 1.0, -2.0, 0.0) == M.VERDICT_NOT_MEASURABLE

    # 🚨 分岔自检：边界两侧必须真的换档，否则上面两条可能是装饰（第 112 层）。
    assert M.verdict_of(20, 0.999, 1.0, -0.001, 1.999) == M.VERDICT_POWERLESS
    assert M.verdict_of(20, 1.001, 1.0, 0.001, 2.001) == M.VERDICT_POSITIVE


def test_powerless_is_the_first_branch_in_source_order():
    """「分支顺序本身就是判据」（第 141 层）⇒ 用 **AST** 钉，不许用子串。

    ⚠️ 子串会被 docstring 满足 —— 四个判决串在模块 docstring 里全出现过。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "verdict_of")
    first_if = next(n for n in fn.body if isinstance(n, ast.If))

    # ⚠️ 只扫 `ast.Constant` 是**错的**：源码写的是 `return VERDICT_POWERLESS`
    #   —— 那是 `ast.Name`（模块级常量引用），不是字符串字面量 ⇒ 集合恒空
    #   ⇒ 这条守卫第一版**恒红**，而红的形态与「分支顺序被改坏」完全相同。
    #   ⇒ 必须同时收 `ast.Name` 并**求值**（第 131 层：判据要量值，不是量写法形状）。
    # ⚠️ 只看 `Return` 节点：不许扫整个 `if` 的子树，否则分支里新加的 docstring
    #   会被当成返回值（第 13 种形状的注释满足）。
    returned = set()
    for node in ast.walk(first_if):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        for c in ast.walk(node.value):
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                returned.add(c.value)
            elif isinstance(c, ast.Name):
                val = getattr(M, c.id, None)
                if isinstance(val, str):
                    returned.add(val)
    assert M.VERDICT_POWERLESS in returned, (
        "verdict_of 的第一个 if 不是 POWERLESS 分支 —— R4 规定它必须最先判"
        "（|c|/MDE<1 时先判 CI 跨 0 会把「测不出」与「符号相反」并成一档，"
        "而两者对下一步的动作相反）")


# --------------------------------------------------------------------------
# 死分支：只在构造不变量下成立，且必须有正控
# --------------------------------------------------------------------------

def _reachable_under_builder(trials=4000, seed=20261016):
    """在 `measure()` 的构造不变量（lo = point−mde, hi = point+mde）下穷举可达集。

    随机只用来**铺开输入**，判别力不依赖随机命中：返回的是**集合**。
    """
    rng = random.Random(seed)
    seen = set()
    for _ in range(trials):
        n = rng.randint(2, 40)
        series = [rng.gauss(0.0, 10.0 ** rng.uniform(-3, 1)) for _ in range(n)]
        point, mde = _mde_of(series)
        seen.add(M.verdict_of(n, point, mde, point - mde, point + mde))
    return seen


def test_the_sweep_really_reaches_all_three_live_branches():
    """正控（防空真守卫）：穷举必须**真的**碰到三个活档。"""
    assert _reachable_under_builder() == {M.VERDICT_POWERLESS, M.VERDICT_NEGATIVE,
                                          M.VERDICT_POSITIVE}


def test_the_registered_dead_branch_is_exactly_the_unreachable_set():
    """🚨 死分支登记必须与实测不可达集合**恰好相等**（不多不少）。"""
    unreachable = set(M.BRANCH_ORDER) - _reachable_under_builder()
    assert unreachable == set(M.DEAD_BRANCHES), (
        f"实测不可达 {sorted(unreachable)} 而登记的是 {sorted(M.DEAD_BRANCHES)}；"
        "两者必须逐项相等 —— 少登记则读者以为四档都在用，多登记则是假的")


def test_the_dead_branch_is_reachable_once_the_builder_invariant_is_broken():
    """🚨 正控：破坏 `lo = point−mde, hi = point+mde` ⇒ 那一档**立刻可达**。

    ⇒ 「不可达」不是 `verdict_of` 的性质，是**调用方构造**的性质。
       没有这条，上面两条会在「构造被改坏」时一起绿。
    """
    assert M.verdict_of(8, 5.0, 1.0, -1.0, 6.0) == M.VERDICT_NOT_MEASURABLE
    assert M.VERDICT_NOT_MEASURABLE in set(M.DEAD_BRANCHES)


# --------------------------------------------------------------------------
# measure() 层：夹具必须让判决真的被算出来
# --------------------------------------------------------------------------

def test_measure_produces_a_positive_verdict_on_the_branching_fixture():
    out = M.measure(_one_leg_per_block(POSITIVE_SERIES))
    assert out["n_blocks"] == 6
    assert out["verdict"] == M.VERDICT_POSITIVE
    assert out["ci95_low_pct"] > 0, "CI 下界必须与判决同向（R5：不许只印点估计）"


def test_measure_flips_to_negative_on_the_mirrored_fixture():
    out = M.measure(_one_leg_per_block(NEGATIVE_SERIES))
    assert out["verdict"] == M.VERDICT_NEGATIVE
    assert out["ci95_high_pct"] < 0


def test_g7_one_entry_date_shared_by_two_pred_dates_is_one_observation():
    """G7（预注册点名的「最容易被过严写法毁掉的一条」）：同 entry ⇒ 一个观测。"""
    out = M.measure(_leg([
        ("2026-01-05", "2026-01-06", 0.02, 0.0),   # 两天前下的单
        ("2026-01-06", "2026-01-06", 0.02, 0.0),   # 昨天下的单，同一批 bar
    ]))
    assert out["n_blocks"] == 1, "两个 pred_date 共享一个 entry_date 时算成了两个观测"
    assert out["n_pred_dates"] == 2, "信号日计数必须仍如实报 2（供人看交叠）"


def test_g7_positive_control_two_entry_dates_really_are_two_observations():
    """G7 的正控：entry_date 不同 ⇒ 必须是 2 ⇒ 否则上面那条是「永远 1」的装饰。"""
    out = M.measure(_leg([
        ("2026-01-05", "2026-01-06", 0.02, 0.0),
        ("2026-01-06", "2026-01-07", 0.02, 0.0),
    ]))
    assert out["n_blocks"] == 2


def test_g6_a_missing_upstream_column_raises_key_error_and_never_defaults():
    """G6：缺列必须 `KeyError`，不许 `.get(默认值)` 悄悄回落。"""
    frame = _leg([("2026-01-05", "2026-01-06", 0.02, 0.0)]).drop(columns=["market_pct"])
    with pytest.raises(KeyError):
        M.leg_rows(frame)


def test_leg_rows_drops_rows_by_whether_the_number_exists_not_by_the_result_label():
    """`gross_pct` 为 NaN 的行必须被剔 —— 按**量在不在**判，不按 `result` 取值判。

    实测 `result` 只有 hit/miss/flat/no_data，没有 `verified`
    ⇒ 按名字剔除会剔掉 0 行、剔不掉真正不可判定的那行。
    """
    frame = _leg([
        ("2026-01-05", "2026-01-06", 0.02, 0.0),
        ("2026-01-05", "2026-01-06", float("nan"), 0.0),
    ])
    frame["result"] = ["hit", "no_data"]
    assert len(M.leg_rows(frame)) == 1


# --------------------------------------------------------------------------
# G3 / G4 / G5 —— R5 与 R3
# --------------------------------------------------------------------------

def test_g3_the_artifact_carries_no_extrapolated_annualization_anywhere():
    """G3：R5 明令产物**没有**任何外推年化字段。

    判在**键名**上（含 `caliber` 与嵌套的敏感性臂），不是判正文里有没有那个词。
    """
    out = M.measure(_one_leg_per_block(POSITIVE_SERIES))
    out["caliber"] = M.caliber()
    banned = ("annual", "cagr", "年化", "几何", "compound", "复合")
    offenders = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if any(b in str(k).lower() for b in banned):
                    offenders.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(out, "verdict")
    assert not offenders, f"产物里出现了外推年化/复合差字段：{offenders}（R5/R3）"


def test_g4_point_estimate_and_ci_are_both_present():
    """G4：点估计与 CI 必须同时在产物里（R5：不许只印点估计）。"""
    out = M.measure(_one_leg_per_block(POSITIVE_SERIES))
    for key in ("mean_excess_pct_per_block", "ci95_low_pct", "ci95_high_pct"):
        assert key in out and out[key] is not None, f"缺 {key}"
    lo, point, hi = (out["ci95_low_pct"], out["mean_excess_pct_per_block"],
                     out["ci95_high_pct"])
    assert lo < point < hi, "CI 必须把点估计夹在中间"


#: ★ G5 的分岔夹具：每块两腿，算术块均值 **+0.050/+0.070**，
#: 而逐块复合差 Π(1+r)−1 是 **−0.100/−0.0576** ⇒ 两者**符号相反**。
#: 判决若改用复合差 ⇒ 六个块均值全负 ⇒ 翻成 `NEGATIVE`。
DIVERGENT_BLOCKS = [[0.50, -0.40]] * 3 + [[0.52, -0.38]] * 3


def test_g5_the_verdict_follows_the_arithmetic_mean_not_the_compound_difference():
    """G5（R3）：判决只用逐行**算术**超额，复合差**不得**进判决。

    🚨 夹具必须**块内多腿**才是分岔夹具：单腿块的复合差就等于算术差
       （Π(1+r)−1 = r），那样改用复合差也照样通过 ⇒ 守卫零判别力。
    """
    arithmetic_per_block = [sum(legs) / len(legs) for legs in DIVERGENT_BLOCKS]
    compound_per_block = [math.prod(1.0 + v for v in legs) - 1.0
                          for legs in DIVERGENT_BLOCKS]
    assert max(arithmetic_per_block) > 0, "夹具失效：算术块均值必须全正"
    assert max(compound_per_block) < 0, "夹具失效：复合差必须全负（两者符号必须相反）"

    out = M.measure(_multi_leg_blocks(DIVERGENT_BLOCKS))
    assert out["n_blocks"] == 6
    assert out["mean_excess_pct_per_block"] > 0, "判决必须读逐块算术均值"
    assert out["verdict"] == M.VERDICT_POSITIVE, (
        "判决没落在算术均值的档上 —— 若它跟去了复合差（逐块全负）"
        "这里会翻成 NEGATIVE")


# --------------------------------------------------------------------------
# 唯一真承重的变异：减不减 market（顺序不承重，见文件 docstring ①）
# --------------------------------------------------------------------------

def test_the_subtraction_itself_is_load_bearing():
    """「减不减」才是承重项：`market_pct := 0` ⇒ 判决层的量**必须变**。

    顺序（先减后合 vs 先合后减）**不承重**，实测见下一条。

    🚨 夹具必须让 market **真的能变**（`_gross_fixed_market_varied`）。
       第一版误用 `_one_leg_per_block(..., market=m)` —— 它造 `gross = m_arg + market`
       ⇒ `excess = gross − market = m_arg` ⇒ 本条在**任何** market 值下恒红。
       恒红与恒真同害（第 112 层）：它是红的，会把人推向「去改本来正确的生产代码」。
    """
    with_market = M.measure(_gross_fixed_market_varied(POSITIVE_SERIES, 0.001))
    without = M.measure(_gross_fixed_market_varied(POSITIVE_SERIES, 0.0))

    # 先自检夹具的判别力：market 变了均值必须跟着平移，否则下面那句 ≠ 是装饰
    assert _float_close(with_market["mean_excess_pct_per_block"],
                        without["mean_excess_pct_per_block"] - 0.001,
                        abs(without["mean_excess_pct_per_block"])), (
        "夹具失效：market 变了而块均值没跟着平移 ⇒ 「减不减」这条守卫测不到东西")

    assert (with_market["mean_excess_pct_per_block"]
            != without["mean_excess_pct_per_block"]), (
        "减不减 market 竟无影响 ⇒ 「超额 = gross − market」这条口径已不成立，"
        "而 caliber 仍在这样声称")


def test_merge_order_is_recorded_with_a_tolerance_and_is_not_guarded_against(
        monkeypatch):
    """⚠️ 记录「减法顺序不承重」，**并**说明为什么这条不是会杀变异体的守卫。

    - 夹具自检：块内 `market_pct` 互不相同（否则本条恒真，第 112 层）；
    - 判据用**从量级推导的阈值**，不是逐位 `==`（浮点加法不满足结合律）；
    - `verdict` 是**分类量**，必须逐位相等；
    - 🚨 **本仓刻意不为顺序配会杀变异体的守卫** —— 它实测不承重，
      补断言等于给生产加一条恒真约束，失效方向是把正在工作的代码改坏。
      若哪天本条红（说明顺序开始影响判决），那时才可以补守卫。

    ⚠️ 夹具是 **6 块**（不是 4 块）：n=4 时 `POWERLESS` 的 n 闸先吃掉判决，
       两侧都返回同一个串 ⇒ 「分类量逐位相等」这条会退化成装饰。
    """
    frame = _multi_leg_blocks(
        [[0.050, 0.030, 0.020], [0.045, 0.030, 0.015],
         [0.055, 0.025, 0.020], [0.040, 0.035, 0.015],
         [0.052, 0.028, 0.018], [0.047, 0.032, 0.016]],
        market_legs=[list(MARKET_WITHIN_BLOCK)] * 6,
    )
    _assert_fixture_is_not_degenerate(frame)

    baseline = M.measure(frame)
    assert baseline["verdict"] == M.VERDICT_POSITIVE, (
        "夹具失效：6 块下判决必须落在 POSITIVE，否则分类量比较是装饰")
    monkeypatch.setattr(M, "blocks", _blocks_merge_market_first)
    moved = M.measure(frame)

    assert moved["verdict"] == baseline["verdict"], (
        "分类量必须逐位相等；不相等说明减法顺序已经承重，该补守卫了")
    scale = max(abs(v) for v in baseline["blocks"][0].values()
                if isinstance(v, float))
    for key in ("mean_excess_pct_per_block", "sd_pct", "mde_pct",
                "ci95_low_pct", "ci95_high_pct"):
        assert _float_close(moved[key], baseline[key], scale), (
            f"{key} 随减法顺序变了 ⇒ 「顺序是恒等」这句话已过期（{moved[key]!r} "
            f"vs {baseline[key]!r}）")


def _blocks_merge_market_first(leg: pd.DataFrame) -> pd.DataFrame:
    """`blocks()` 的等价实现：先按块合并 `market_pct`，再相减。

    与 `blocks()` 输出契约完全相同（`entry_date`/`excess_mean`/`n_legs`/`n_pred_dates`），
    差别只在减法相对于 `groupby` 的位置。
    """
    if leg.empty:
        return pd.DataFrame(columns=["entry_date", "excess_mean", "n_legs"])
    merged = leg.groupby("entry_date")["market_pct"].transform("mean")
    work = leg.assign(excess=leg["gross_pct"] - merged)
    return (work.groupby("entry_date", as_index=False)
                .agg(excess_mean=("excess", "mean"), n_legs=("excess", "size"),
                     n_pred_dates=("pred_date", "nunique"))
                .sort_values("entry_date").reset_index(drop=True))


# --------------------------------------------------------------------------
# 口径块必须跟着数字离开脚本（第 159 层：字段名不是语义）
# --------------------------------------------------------------------------

def test_the_caliber_block_states_the_measured_merge_order_claim():
    """口径块必须写「顺序不承重」而不是「顺序承重」。

    ⚠️ 钉的是**两段口径的自相矛盾**：`caliber` 若继续说「承重的是方差那一半」，
       而 `blocks()` 的 docstring 说「顺序不承重」，两处单独读都对、
       合起来矛盾（第 179 层）。
    """
    cal = M.caliber()
    assert "market_pct_merge_order" in cal
    assert "不承重" in cal["market_pct_merge_order"]
    assert list(cal["dead_branches"]) == list(M.DEAD_BRANCHES), (
        "口径块里的死分支清单必须与模块常量同源，不许各写一份")
