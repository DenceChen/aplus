"""实盘与回测必须给出**同一个因子值**。

## 为什么这条比任何单因子测试都重要

2026-10-11 实测：`ivol` 在两条路径上相差 4%~21%
（0.020674 vs 0.018110），足以改变横截面排序。
更糟的是 2 年终裁第一名 `{"ivol": -1.0}` **实盘根本算不出 ivol**——
`compute_factors` 的因子集合里根本没有它，权重一旦晋级，实盘当天 KeyError。

根因是架构：`FactorPanel`（回测）与 `cross_rows_from_map`（实盘）
各自实现了一份因子。每新增一个因子就要在两处各写一遍，
口径必然漂移。

**2026-10-11 的修法不是「把两处对齐」，而是让实盘直接复用面板**
（`snapshot.build_cross_section` → `panel.build_panel`）：
单一实现，口径在物理上不可能分叉。

`cross_rows_from_map` 保留为**参照实现**，仅供回归测试使用，
**不在生产路径上**。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.backtest import build_cross_sections
from aplus.panel import FACTOR_COLUMNS
from aplus.snapshot import build_cross_section, cross_rows_from_map
from aplus.store import LocalStore

from tests.test_backtest import _noisy_stock


def _store(tmp_path):
    store = LocalStore(tmp_path)
    for i, dr in enumerate([0.012, 0.011, 0.010, -0.010, -0.011, -0.012]):
        store.upsert_daily(f"60000{i+1}.SH", _noisy_stock(f"60000{i+1}.SH", drift=dr, seed=i))
    store.upsert_daily("600007.SH", _noisy_stock("600007.SH", drift=0.02, seed=9, st=1))
    store.upsert_daily("600008.SH", _noisy_stock("600008.SH", drift=0.02, seed=10))
    return store


def test_live_path_produces_every_panel_factor(tmp_path):
    """实盘横截面必须产出**每一个**面板因子——否则晋级是空头支票。"""
    store = _store(tmp_path)
    dates = store.read_daily("600001.SH")["date"].tolist()
    live = build_cross_section(store, as_of=dates[70])
    missing = [c for c in FACTOR_COLUMNS if c not in live.columns]
    assert not missing, f"实盘缺因子：{missing} —— 赛马晋级用到它们的权重会当天 KeyError"


def test_live_path_matches_backtest_panel_exactly(tmp_path):
    """实盘与回测对同一日、同一批股票给出的因子必须**逐位相同**。

    ## 比较的必须是**同一过滤状态**下的两个结果

    `build_cross_sections`（回测）出的是**已过 `filter_universe`** 的横截面，
    `build_cross_section`（实盘）出的是**未过滤**的横截面。
    早期版本直接比这两者，于是 2026-10-11 报出「实盘 8 行 / 回测 7 行」——
    差的是那只 ST 股（600007.SH），实盘侧还没剔。

    那不是泄露，也不是漂移，是**在比两个定义不同的东西**。
    正确的恒等式是端到端的：**两边都过同一个 `filter_universe`**，
    得到的名单与因子必须逐位相同。这才是「实盘会出的单 == 回测回测过的单」。
    """
    from aplus.predict import filter_universe

    store = _store(tmp_path)
    dates = store.read_daily("600001.SH")["date"].tolist()
    day = dates[70]
    live = filter_universe(build_cross_section(store, as_of=day), day)
    live = live.sort_values("code").reset_index(drop=True)
    xs = build_cross_sections(store, dates[60], dates[74])
    assert day in xs, f"面板窗口里没有 {day}；可用 {sorted(xs['_dates'])[:3]}..."
    panel = xs[day].sort_values("code").reset_index(drop=True)
    assert set(live.code) == set(panel.code), (
        f"名单不同：仅实盘 {set(live.code) - set(panel.code)}；"
        f"仅回测 {set(panel.code) - set(live.code)}")
    shared = [c for c in live.columns if c in panel.columns and c != "name"]
    assert shared, "没有可比列"
    for c in shared:
        # buyable 是 bool 列：bool 相减直接 TypeError，必须先落到 float
        a = pd.to_numeric(live[c], errors="coerce").astype("float64").to_numpy()
        b = pd.to_numeric(panel[c], errors="coerce").astype("float64").to_numpy()
        if len(a) != len(b):
            assert False, f"因子 {c}: 行数不同 实盘{len(a)} 回测{len(b)}"
        d = np.abs(a - b)
        d = d[~np.isnan(d)]
        assert (d.size == 0) or float(d.max()) <= 1e-12, (
            f"因子 {c} 在实盘与回测不一致，最大偏差 {d.max():.3e}")


def test_live_path_is_not_the_legacy_reference_implementation(tmp_path):
    """钉住架构决策：实盘**不得**再回到逐股重算的参照实现上。

    两条路径能给出不同答案（实测 ivol 差 4%~21%）。一旦有人为了「省事」
    把 `build_cross_section` 改回 `cross_rows_from_map`，本文件其余两条
    会立刻红——这里显式记录这个决定的边界。
    """
    store = _store(tmp_path)
    dates = store.read_daily("600001.SH")["date"].tolist()
    live = build_cross_section(store, as_of=dates[70])
    legacy = cross_rows_from_map(
        {p.stem: store.read_daily(p.stem)
         for p in sorted((store.root / "master" / "daily").glob("*.parquet"))},
        as_of=dates[70])
    assert "ivol" in live.columns, "实盘已退回参照实现（无 ivol）"
    # ⚠️ 2026-10-15 第 131 层删掉的原句：
    #     assert "ivol" in legacy.columns or legacy["ivol"].isna().all() or True
    # 那条**恒真**（末项 `or True`）⇒ 它从来不判任何东西，
    # 而它在文件里的样子与旁边两条真断言一模一样 ⇒ 「守卫在这里」是假的。
    # 由 `tests/test_generator_names_resolve.py::test_no_guard_in_tests_asserts_a_constant_truth` 逮到。
    #
    # 这里**不补一条新的判据**：那需要一个从实测量推导的门槛并预注册
    # （「参照实现允不允许缺 ivol」是个尚未测过的问题，不许顺手拍）。
    # ⇒ 缺口登记在 `docs/TODO_live_legacy_parity_2026-10-15.md`。

    # ⚠️ 2026-10-15 实测：**「补一条列存在性断言」这条路是走不通的。**
    # 在本文件的夹具上逐列量过：`legacy` **不缺**任何面板因子（8/8 全有），
    # 且两路逐因子最大偏差 `ivol` 0.0 / `rsi` 0.0 / `vsur` 0.0，
    # `mom20` 4.44e-16、`rev5` 2.22e-16、`vol20` 6.94e-18。
    # ⇒ `assert "ivol" in legacy.columns` 会是**绿的**，而它为真的原因是
    #   「代码恰好同步了」，不是「有东西在守」——那就是另一条恒绿守卫。
    # ⇒ 并且这份夹具**结构性地跑不满因子**：`_noisy_stock` 的 `volume` 是
    #   确定性递增（无随机）⇒ 20 日滚动 std 恰为 0 ⇒ `turnratio` 恒 `NaN`
    #   （0/0）⇒ **8 个因子里只有 6 个真被测过**。
    # ⚠️ 所以夹具上的 0.0 偏差**不能读成「2026-10-11 的漂移已消失」**——
    #   那是拿「合成夹具 / 6 个因子」回答「真实数据 / 8 个因子」（第 119 层）。
    #
    # ⇒ 缺口的正解是 **K4**：守卫的对象是「**谁在生产路径上**」，
    #   不是「参照实现的因子齐不齐」。见下面那条。


# ── K4：守卫的对象是「谁在生产路径上」，不是「参照实现的因子齐不齐」─────
#
# 预注册见 `docs/TODO_live_legacy_parity_2026-10-15.md` §3-K4 / §4。
# ⚠️ 这条守卫**只判架构**，不判数值漂移——真数据对账要 GB 级面板，
#   与重跑链并发会 OOM ⇒ 判决数据尚未跑，登记为未测，**不是**「通过」。

import ast as _ast          # noqa: E402  （放在文件末尾：不改动既有 import 块）

_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
_SNAPSHOT_SRC = (_ROOT / "aplus" / "snapshot.py").read_text(encoding="utf-8")
_SNAPSHOT = _ast.parse(_SNAPSHOT_SRC)


def _function_node(src, name: str):
    for node in src.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"snapshot.py 里没有函数 {name}")


def _body_names(node) -> set:
    """函数体里出现的**裸名与属性名**（docstring 与注释天然不在其中）。

    ★ 为什么必须走 AST 而不是子串（第 13 层）：
      docstring 里的文字是 `ast.Constant(str)`，**不是 `Name` 节点**，
      所以这个采集器**结构性地看不见散文**。
    ⚠️ 第一版我给它加了个 `with_docstring` 开关想「排除 docstring」——
      那是在给一个**不存在的问题**加参数，于是开关成了**死代码**，
      而死代码在这里的表现形式恰恰是「全绿」（第 117 层）。
      ⇒ 被本文件自己的一条断言当场抓住，已删。
    """
    found = set()
    for stmt in node.body:
        for n in _ast.walk(stmt):
            if isinstance(n, _ast.Name):
                found.add(n.id)
            elif isinstance(n, _ast.Attribute):
                found.add(n.attr)
    return found


def test_the_live_path_never_calls_the_legacy_reference_implementation():
    """★ 实盘路径**不得**再调用参照实现（第 131 层那个缺口的正解）。

    2026-10-11 的架构决策是「实盘直接复用面板，单一实现，口径不可能分叉」。
    它此前**只有散文**（`build_cross_section` 的 docstring + 上一条测试的
    docstring），没有任何机械判据 ⇒ 「决策」与「代码」之间没有通路（第 6 种形状）。

    ⚠️ 判据必须落在 **AST 函数体**：
    整函数子串匹配会被 `build_cross_section` 自己的 docstring 满足 ——
    下一条把这件事**实测**出来，不是推理。
    """
    names = _body_names(_function_node(_SNAPSHOT, "build_cross_section"))
    assert "cross_rows_from_map" not in names, (
        "🚨 实盘路径又调回参照实现了 —— 那正是 2026-10-11 修掉的缺陷：\n"
        "  两条路径各自实现一份因子，`ivol` 实测相差 4%~21%，足以改变横截面排序。\n"
        "修法是复用面板（`panel.build_panel`），不是逐字段对齐。"
    )


def _func(body_src: str):
    return _ast.parse("def f():\n" + body_src).body[0]


def test_the_architecture_guard_would_catch_a_revert_and_is_not_satisfied_by_prose():
    """正控 + 反控（第 131 层：**正控要验判决，不只验探测器**）。

    ① **正控**：函数体里真的调一次 ⇒ 主判据**必须**命中；
    ② **反控**：docstring 与注释里提到 ⇒ 主判据**必须**不命中。
      这一条不是假设——真实源码 `build_cross_section` 的 docstring 里
      `cross_rows_from_map` 出现三次，下面第 ④ 条把它变成实测。

    ⚠️ 只验探测器会让「探测器恒真」与「判决恒真」长得一模一样，
    所以必须先造一个**真会判决为红**的输入，看判决本身变不变。
    """
    calls = "    cross_rows_from_map(daily_map)\n"
    prose = ('    # 此前实盘走 `cross_rows_from_map`（逐股重算），保留为参照实现。\n'
             '    """doc 里也写了 cross_rows_from_map""".strip()\n')

    assert "cross_rows_from_map" in _body_names(_func(calls)), "正控失真：真调用抓不到"
    assert "cross_rows_from_map" not in _body_names(_func(prose)), (
        "反控失真：散文被当成了调用（第 13 层）"
    )


def test_the_substring_way_is_wrong_on_this_very_source():
    """★ 把「为什么不能用子串」变成**实测**，不是注释。

    对**同一份真实源码**同时跑两种判据，并要求它们**给出相反的答案**：

    | 判据 | 在 `build_cross_section` 上的答案 |
    |---|---|
    | 子串（`name in getsource(fn)`） | **命中** —— 被 docstring 满足 |
    | AST 名字采集（本守卫用的） | **不命中** |

    ⇒ 这条断言不是恒真：它要求两者**不一致**，
    而「不一致」恰好只在「docstring 里真的提到了这个名字」时成立。
    ⚠️ 如果哪天有人把那段历史叙述从 docstring 里删掉，
      本条会红——那是**真的红了**（反例消失），
      所以它的失败消息必须说清「改判据」与「事实变了」是两件事。
    """
    node = _function_node(_SNAPSHOT, "build_cross_section")

    # 子串判据：拿整个函数的源码段（真实、够宽，足以含 docstring）
    segment = _ast.get_source_segment(_SNAPSHOT_SRC, node) or ""
    assert segment, "取不到 build_cross_section 的源码段"
    assert "cross_rows_from_map" in segment, (
        "🚨 反例消失了：`build_cross_section` 的源码里已经查不到这个名字 ⇒\n"
        "  「子串判据会被 docstring 满足」这一条现在**不成立**了。\n"
        "⚠️ 这不是判据坏了，是**事实变了**（那段历史叙述被删）。\n"
        "  两件事要分开做：先确认再改守卫，不许顺手把断言改小。"
    )

    # AST 判据：在同一份源码上**不**命中
    assert "cross_rows_from_map" not in _body_names(node)

    # 定位本身也要钉：行号取到的必须是那个函数的首行（第 114 层那种
    # 「列清单只存在于空路径那一行」的同族——定位错而断言照绿）
    first_line = _SNAPSHOT_SRC.splitlines()[node.lineno - 1]
    assert first_line.strip().startswith("def build_cross_section"), (
        f"行号定位失效，拿到的是 {first_line!r}"
    )
