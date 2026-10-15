"""**逐日净超额**那一列的守卫（预注册 `PREREG_7Y_NET_NAV` C2 / §8.3 的实现侧）。

## 这一列为什么缺

`market_return` 那一列（`tests/test_net_nav_market_column.py`）补上了**被减掉的那一项**，
于是 `market + gross_excess` 已经是组合净值。🚨 但那是**毛**的：

    产物 caliber 块逐字写着「落盘的是**毛**的那一遍（cost=0）；
    net 那遍只用于算实测成本」

⇒ 逐日**净**序列只在内存里活过一次，被汇总成一个标量 `annual_net_pct` 就扔了。
⇒ 而 Y1 判的是**账户净值**（C4：绝对净值，不是超额），C2 又要求
**0 / 0.003 / 0.006 三档并排**。三档的**日频序列**一份都不在产物里
⇒ **① 组合净值在中档成本下，现在算不出来。**

这不是「引擎缺能力」：`at_cost` 与 `_daily` 都现成。
这是**一列**，所以守卫也只需要钉住它的性质。

## 本文件钉的五件事（每条一个射程，不共用断言）

| 守卫 | 射程 | 它抓什么 |
|---|---|---|
| **T1** | `run_year` 的 chunk | `net_by_cost` 是**逐档的推导式**，不是写死的一条 |
| **T2** | 同上 | net 序列**按索引**对齐到 `gross`，不许按位置 |
| **T3** | `_assemble` | 档名来自**常量**，不许把 `0.003` 抄进列名 |
| **T4** | 产物 `caliber` | 口径块含「是什么**与不是什么**」 |
| **T5** | 运行层（外部锚） | `gross − net ≡ 逐日均摊成本`，逐位、索引相同、**且不相等** |

## ⚠️ 判据设计上的三条（第 112/113/79 层，防复现）

1. **不许只钉「列名存在」** —— 写成 `("net_excess_bt003", c.get("excess"))`
   会让「把毛值再抄一遍」的实现照样绿，而产物里两列逐位相同、
   **不报任何错**（第 79 层：只钉存在不钉性质）。
   ⇒ T5 的第三条断言就是为它准备的：**`net` 不得等于 `gross`**。
2. **不许用行数相同代替索引逐位相同**（同族，见 `test_net_nav_market_column.py`）。
   M2（`iloc[:len(daily)]`）就是等长但错位。
3. **T5 必须是不依赖实现的外部锚**：手算成本由夹具自己给，
   不许调用被测对象内部的任何私有函数去「验证」自己（第 163 层：
   别拿仓里的尺子量这把尺子）。
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

from aplus.evolve_race import _daily_series
from aplus.backtest import STOCK_COST_DEFAULT, cost_drag
from scripts.backtest_walk_forward_weights import _net_by_cost, _tier_cost

_ROOT = Path(__file__).resolve().parent.parent
_BACKTEST = _ROOT / "scripts" / "backtest_walk_forward_weights.py"

#: 判决要用的那一档（C2 中档 / C1）。**从真相源 import，不写死 0.003**。
_MID_TAG = "bt003"


def _tree():
    return ast.parse(_BACKTEST.read_text())


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _dict_entries(node):
    """dict 字面量 → {key 常量: value 节点}；不是字面量则返回 {}。"""
    if not isinstance(node, ast.Dict):
        return {}
    out = {}
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out[k.value] = v
    return out


def _chunk_dict(tree):
    """`run_year` 里 `daily_out.append({…})` 那个 dict 字面量。"""
    fn = _func(tree, "run_year")
    assert fn is not None, "找不到 `run_year` ⇒ 守卫自己失效了，不是实现变干净了"
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append" and node.args
                and isinstance(node.args[0], ast.Dict)):
            return node.args[0]
    return None


def _src(node):
    return ast.unparse(node) if node is not None else ""


# ---------------------------------------------------------------- T1

def test_the_chunk_carries_a_per_tier_derivation_not_a_single_hardcoded_series():
    """`net_by_cost` 必须是**逐档推导**出来的（`DictComp` over `.items()`）。

    ⚠️ 钉「key 存在」不够：写成 `"net_by_cost": {"bt003": daily}`
    的一档版会绿，而 C2 的高档/低档**在产物里彻底消失**，
    成本敏感性又被藏起来（C2 逐字：「只报中档」= 藏起成本敏感性）。
    """
    chunk = _chunk_dict(_tree())
    assert chunk is not None, "找不到 `daily_out.append({…})` ⇒ 守卫自己失效了"
    entries = _dict_entries(chunk)
    assert "net_by_cost" in entries, (
        f"chunk 里没有 `net_by_cost`（实得 {sorted(entries)}）⇒ "
        "逐日净超额没有落盘，① 组合净值在中档成本下算不出来")
    node = entries["net_by_cost"]
    assert isinstance(node, ast.DictComp), (
        f"`net_by_cost` 是 {type(node).__name__} 而不是 DictComp ⇒ "
        "成本档被写死成一条，C2 的三档退化成单档")
    iters = [_src(g.iter) for g in node.generators]
    assert any(".items()" in s for s in iters), (
        f"`net_by_cost` 不遍历档的字典（实得 {iters}）⇒ "
        "它是硬编码的一条，不是逐档推导")


# ---------------------------------------------------------------- T2

def test_the_net_series_is_aligned_to_gross_by_index_not_by_position():
    """net 序列必须 `.reindex(gross.index)`。

    ⚠️ 缺口形态就是 M2 那一族：`list(ser)[:len(gross)]` 看着对得上，
    实则把不同日子配成一对，而 `.daily.parquet` 里**不报任何异常**。
    """
    chunk = _chunk_dict(_tree())
    entries = _dict_entries(chunk)
    node = entries.get("net_by_cost")
    assert node is not None, "chunk 里没有 `net_by_cost`"
    # ⚠️ `DictComp` 的值字段是 `.value`；`.elt` 是 `ListComp` 的。
    #   写错会得到 `AttributeError` —— 而它在 pytest 里**也是 FAILED / rc=1**
    #   ⇒ 变异台会把一个**无法求值**的守卫读成「它杀掉了变异」
    #   （第 158 层的同族：守卫自己崩了 ≠ 它在守东西）。
    body = _src(node.value)
    assert ".reindex(" in body, (
        f"推导式里没有 `.reindex(...)`（实得 `{body}`）⇒ "
        "净序列与 `gross` 是按位置对齐的")
    assert "gross" in body, (
        f"`.reindex(...)` 的参照不是 `gross`（实得 `{body}`）⇒ "
        "对齐到了别的东西上，而净/毛必须是同一批日子")


# ---------------------------------------------------------------- T3

def test_the_column_names_come_from_a_constant_not_from_a_retyped_0_003():
    """档名必须来自模块常量 `COST_TIER_TAGS`。

    ⚠️ 把 `0.003` 抄进列名（`net_excess_003`）看着更「明确」，
    实则改费率时列名不会跟着变 —— 而 `STOCK_COST_DEFAULT` 是会变的常量
    （`aplus/backtest.py:48`）。两份真相 = 下次费率调整后列名撒谎。
    """
    tree = _tree()
    tags = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "COST_TIER_TAGS"
                for t in node.targets):
            tags = node.value
    assert tags is not None, (
        "模块里没有 `COST_TIER_TAGS` 常量 ⇒ 档名只存在于字面量里，"
        "而费率是会改的")
    if isinstance(tags, (ast.Tuple, ast.List)):
        got = [e.value for e in tags.elts if isinstance(e, ast.Constant)]
    else:
        got = None
    assert got is not None, (
        f"`COST_TIER_TAGS` = `{_src(tags)}` 不是字面量元组 ⇒ 守卫无法验证档名，"
        "守卫自己失效了")
    assert _MID_TAG in got, (
        f"判决中档标签 `{_MID_TAG}` 不在 `COST_TIER_TAGS`（实得 {got}）⇒ "
        "C2 的中档不会落盘")
    assert got == sorted(set(got)), f"档名重复或无序（实得 {got}）"


# ---------------------------------------------------------------- T4

def test_the_caliber_block_says_what_the_net_column_is_and_is_not():
    """口径块必须认领这一列，且含「不是什么」（§8.4 / 第 79 层）。"""
    tree = _tree()
    calib = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            e = _dict_entries(node)
            if "caliber" in e:
                calib = _dict_entries(e["caliber"])
                break
    assert calib, (
        "产物 dict 里没有 `caliber` 结构化口径块 ⇒ "
        "口径只活在源码注释里，而产物的读者看不到注释")
    key = f"net_excess_{_MID_TAG}"
    assert key in calib, (
        f"`caliber` 里没有认领 `{key}`（实得 {sorted(calib)}）⇒ "
        "读者无从知道这一列是扣过一次成本的**净**超额")
    sub = _dict_entries(calib[key])
    assert "what_it_is" in sub, f"缺 `what_it_is`：{sorted(sub)}"
    assert "what_it_is_not" in sub, (
        f"只写了「它是什么」而没写「它不是什么」（实得 {sorted(sub)}）⇒ "
        "最可能的误读（把它当毛值 / 当绝对净值）没有任何东西挡住")


# ---------------------------------------------------------------- T5

def _synth_res(turnover: str = "full", n: int = 4, cost: float = STOCK_COST_DEFAULT):
    """合成 `res`：三天涨腿，每天 `n` 只（≥ `MIN_BASKET`=3）。

    `cost` 列的语义由 `aplus/evolve_race.py:259-261` 定死：
    `net = actual_pct − cost`，且**只有涨腿付成本**。
    成本本身按 `cost_drag` 的定义逐日算 ⇒ 夹具的**成本形状**与生产同源，
    而 T5a 的期望值由 `groupby().mean()` **独立**得出，不复用它。
    """
    days = pd.to_datetime(["2021-01-05", "2021-01-06", "2021-01-07"])
    codes = []
    for i in range(3):
        # 「零换手」= 每天同一批 code；「全换手」= 每天互不相交
        codes.append([f"D{j}" for j in range(n)] if turnover == "flat"
                     else [f"D{i}_{j}" for j in range(n)])
    rows, prev = [], None
    for d, day_codes in zip(days, codes):
        drag = float(cost_drag(prev, day_codes, n, cost=cost))
        for j, code in enumerate(day_codes):
            rows.append({
                "pred_date": d, "direction": "up", "code": code,
                "actual_pct": 0.01 * (j + 1) - 0.001 * codes.index(day_codes),
                "cost": drag,
                "market_pct": 0.002,
            })
        prev = day_codes
    return pd.DataFrame(rows)


def _gap(turnover: str, cost: float = STOCK_COST_DEFAULT):
    """`(gross − net, 净夹具)`。

    ⚠️ 两个夹具**必须各建一次**（费率分别传 0 与 `cost`）——
    建好再 `assign(cost=标量)` 会把 `cost_drag` 逐日算好的成本**覆盖掉**，
    于是 `gross − net` 恒等于那个标量。
    🚨 那个症状与「实现扣了个固定费率」**完全一样**：
    不查清方向就会去改本来正确的 `_daily_series`（第 165 层）。
    """
    gross_res = _synth_res(turnover=turnover, cost=0.0)
    net_res = _synth_res(turnover=turnover, cost=cost)
    gross = _daily_series(gross_res, "up")
    net = _daily_series(net_res, "up")
    assert gross is not None and net is not None, "合成夹具没造出序列（守卫失效）"
    assert list(net.index) == list(gross.index), (
        f"净/毛的日期集合不同（{len(net)} vs {len(gross)} 天）⇒ "
        "两列不是同一批日子")
    assert not net.equals(gross), (
        "净序列与毛序列逐位相同 ⇒ 这一列没有真的扣成本，"
        "把毛值改个名字落盘也能过 T1–T4")
    return gross - net, net_res


def test_gross_minus_net_equals_the_hand_computed_daily_cost_verbatim():
    """**外部锚**：`gross − net` 逐位等于夹具自己给的逐日均摊成本。

    ⚠️ 期望值用 `pandas.groupby().mean()` 直接算，**不调用被测对象**
    （第 163 层：别拿仓里的尺子量这把尺子）。
    """
    gap, net_res = _gap("full")
    # ★ 正控：夹具造出的序列必须**非常量**（第 117 层）
    assert gap.nunique() > 1, f"夹具的成本轨迹是常量（守卫失效）：{list(gap)}"

    expected = net_res.groupby("pred_date")["cost"].mean()
    pd.testing.assert_series_equal(
        gap.reindex(expected.index), expected,
        check_names=False, rtol=0, atol=1e-12)


def test_the_net_gap_tracks_turnover_rather_than_a_flat_haircut():
    """零换手与全换手必须给出**不同**的成本轨迹。

    ⚠️ 这一条杀的是「按固定费率扣一次」的实现：那种实现的
    `gross − net` 在两种夹具下**完全一样** ⇒ C2 的成本敏感性是假的，
    而产物里三档并排印出来看着毫无破绽。
    """
    gap_full, _ = _gap("full")
    gap_flat, _ = _gap("flat")
    assert len(gap_full) == len(gap_flat) and len(gap_flat) > 0, "夹具失效"
    assert gap_full.round(12).tolist() != gap_flat.round(12).tolist(), (
        f"零换手与全换手的成本轨迹相同（都是 {list(gap_flat.round(6))}）⇒ "
        "这一列扣的不是换手×费率，而是某个固定数")
    # 零换手时第 2、3 天不换仓 ⇒ 成本必须**恰好为 0**
    assert gap_flat.iloc[1:].abs().max() == 0.0, (
        f"零换手日仍有成本：{list(gap_flat)} ⇒ "
        "成本没有跟着 `cost_drag` 的换手走")


def test_the_zero_turnover_fixture_really_has_zero_turnover():
    """**反向正控**：上面那条断言的前提是「flat 真的零换手」。"""
    res = _synth_res(turnover="flat")
    per_day = res.groupby("pred_date")["code"].apply(lambda s: tuple(sorted(s)))
    assert per_day.nunique() == 1, (
        f"flat 夹具每天的成分不同（{list(per_day)}）⇒ "
        "「零换手」的前提不成立，上面那条断言在测别的东西")


# ------------------------------------------- T6：接线层（N5 存活逼出来的）

def test_the_rate_that_flows_into_the_net_series_is_the_declared_one():
    """`_net_by_cost` 必须按**声明的档费率**扣成本，不是按别的什么。

    ⚠️ 这条守卫是被变异台逼出来的（N5 首轮**存活**）：
    原先两条行为守卫直接调 `_daily_series`，验的是**机制**；
    而档费率是从 `run_year` **流进来**的，那是**接线**。
    两处分开时，「把费率翻倍」对守卫完全不可见 ——
    守卫钉住了被调用的函数，没钉住**调用它时传的那个数**。

    ⇒ 修法不是补一条更窄的断言，是把两处合成一处（抽出 `_net_by_cost`），
    让被守卫的函数与被变异的函数是同一个。
    """
    res = _synth_res("full", cost=STOCK_COST_DEFAULT)
    got = _net_by_cost(res, None, 4, 1)
    assert "bt003" in got and "real" in got, (
        f"成本档没落全（实得 {sorted(got)}）⇒ C2 的档在接线处就丢了")
    expected = _daily_series(res, "up")        # res 的 cost 已按中档费率算好
    pd.testing.assert_series_equal(
        got["bt003"], expected, check_names=False, rtol=0, atol=1e-12)


def test_the_declared_mid_tier_rate_is_the_stock_cost_default_not_a_neighbour():
    """中档的费率必须**恰好**是 `STOCK_COST_DEFAULT`（C1/C2 的定义式）。

    ⚠️ 用 `_tier_cost` 直接取，避免「守卫和实现读同一个常量」之外的任何耦合 ——
    但**也**钉住它不是邻近的数（0.006 / 0.0032 都会绿否则）。
    """
    got = _tier_cost("bt003", 10, 21)
    assert got == STOCK_COST_DEFAULT, (
        f"中档费率是 {got} 而不是 `STOCK_COST_DEFAULT`={STOCK_COST_DEFAULT} "
        "⇒ C1 定义的 0.003 与实际扣的不是同一个数")


def test_an_unregistered_tier_raises_instead_of_silently_defaulting():
    """未知档必须 **KeyError 当场崩**，不许回落成一个「看起来合理」的费率。

    ⚠️ 静默回落的后果是具体的：新增一档忘了登记费率 ⇒ 它按别的档落盘 ⇒
    产物里三档并排印着，其中一档是**别的档的数**，而报告看不出任何区别
    （本仓第 5 层那一族：静默回落）。
    """
    with pytest.raises(KeyError):
        _tier_cost("net_excess_003", 10, 21)
