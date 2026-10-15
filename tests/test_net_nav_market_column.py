"""`market_return` 那一列的守卫（预注册 `PREREG_7Y_NET_NAV` §8.1 的实现侧）。

## 这一列为什么是关键

`aplus/evolve_race.py::_daily_series` 落盘的 `gross_excess` 是**篮子 − 市场**，
即**超额**。而常设目标问的是**绝对净值年化**（用户的钱变 20%）——
这两个是**两个量**（第 7 层）。缺的正是**被减掉的那一项**。

`market` 在 `:263` 每一步都在算，只是落盘时被扔掉。
⇒ 这不是「引擎缺失」，是**一列**。⇒ 守卫也只需要钉住一列的**三个性质**。

## 本文件钉的四件事（每条一个射程，不共用断言）

| 守卫 | 射程 | 它抓什么 |
|---|---|---|
| **G1** | `_daily_series` 默认调用 | 8 个现存调用点**逐位不变** |
| **G2** | `return_parts=True` 的返回值 | 两列**索引逐位相同**（不是「行数相同」） |
| **G3** | 运行层合成夹具 | `market + excess ≡ 等权篮子收益`（**外部锚**，不依赖实现） |
| **G4** | `MIN_BASKET` 过滤后 | 索引仍逐位相同 —— 防 `reindex` 被摘掉 |

## ⚠️ 两条判据设计上的教训（都属于第 112/113 层，写在这里防复现）

1. **不许扫全仓的 `import aplus.ic_to_return`**（预注册 V3 那一版写法）：
   实测仓里有 **6 处**真实 import（`verdict_composite` / `measure_bridge_error` /
   `xsec_sigma` 等），那些地方是**对的**。
   ⇒ 过严的守卫和恒真的一样有害，它会逼人拆掉本来正确的东西。
   ⇒ 射程收窄到**净值路径的生成器**（`_NET_NAV_PATH`），且用 **AST** 不用子串
   （`backtest_walk_forward_weights.py` 的**模块 docstring 里逐字写着**
   `ic_to_return.py`，子串守卫会被注释满足 —— 第 13 层）。

2. **「索引逐位相同」不能写成「行数相同」**：
   行数相同是**必要条件**，而缺陷形态恰好是**等长但错位**（`market` 未过滤时
   若幸存日恰好连续，两列长度会碰巧相等）。⇒ 必须断言 `index` 对象逐位相等。

3. **正控**：G3 的夹具必须断言它**真的**构造出了非平凡的多篮子日，
   否则 `all()` 会在空集合上退化成装饰（第 117 层）。
"""
import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aplus.evolve_race import _daily_series
from aplus.verify import MIN_BASKET

_ROOT = Path(__file__).resolve().parent.parent
_BACKTEST = _ROOT / "scripts" / "backtest_walk_forward_weights.py"
_EVOLVE = _ROOT / "aplus" / "evolve_race.py"

# 🚨 净值路径的**射程**：只有这些文件产出 `market_return` / 组合净值。
#   刻意**不包含** `verdict_composite` / `measure_bridge_error` 等
#   —— 它们 import `ic_to_return` 是对的（那两条路量的就是 IC→价差桥本身，
#   桥错了就该在桥那一侧量出来），把它们一起禁掉是过严（第 112 层）。
_NET_NAV_PATH = (_BACKTEST, _EVOLVE)


def _synth(n_per_day=(4, 2, 4), market_pct=0.002, cost=0.0):
    """合成夹具：三天的涨腿，第 2 天**故意只有 2 只** < `MIN_BASKET`=3。

    ⚠️ 那天会被 `_daily_series` **整天丢掉** —— 这正是 G4 要验的场景：
    若 `market` 不过滤就返回，两列**行数不同**（或等长但错位），
    下游按位置对齐会把不同日子配成一对，且**不报任何异常**。
    """
    days = pd.to_datetime(["2021-01-05", "2021-01-06", "2021-01-07"])
    rows = []
    for d, n in zip(days, n_per_day):
        for i in range(n):
            rows.append({
                "pred_date": d, "direction": "up", "code": f"C{i}",
                "actual_pct": 0.01 * (i + 1),   # 0.01/0.02/0.03/0.04
                "cost": cost, "market_pct": market_pct, "cap": 1.0,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- G1


def test_the_default_call_is_bitwise_identical_so_the_eight_call_sites_are_untouched():
    """默认 `return_parts=False` ⇒ 返回值**逐位**等于 `parts[0]`。

    ⚠️ 这是「不破坏既有 8 个调用点」的**唯一**可机械判定形式。
    仓里 8 个调用点读的是 `.mean()/.std()/len()` 之类，
    而 `_daily_series` 返回 Series 还是 tuple 会让它们**在 AttributeError 处崩**
    —— 崩点离原因很远。
    """
    res = _synth()
    default = _daily_series(res, "up")
    parts = _daily_series(res, "up", return_parts=True)

    assert isinstance(default, pd.Series), (
        f"默认调用必须返回 Series，实际 {type(default)} —— "
        "仓里有 8 个调用点正读它")
    excess, _market = parts
    assert isinstance(default.index, pd.DatetimeIndex)
    assert default.index.equals(excess.index)
    assert np.array_equal(default.to_numpy(float), excess.to_numpy(float)), (
        "默认路径的值与 parts[0] 逐位不同 ⇒ 返回值不再是原来那一根序列")


# ---------------------------------------------------------------- G2


def test_return_parts_hands_out_two_series_whose_indexes_are_identical():
    """两列**索引逐位相同** —— 钉索引，不是钉行数。

    ⚠️ 行数相同是**必要条件**，而缺陷形态恰好是**等长但错位**。
    """
    excess, market = _daily_series(_synth(), "up", return_parts=True)

    assert isinstance(excess, pd.Series) and isinstance(market, pd.Series)
    assert excess.index.equals(market.index), (
        "两列索引不同 ⇒ 下游按位置对齐会把不同日子配成一对")
    assert len(excess) == len(market)


def test_return_parts_is_off_by_default_so_the_signature_cannot_silently_flip():
    """签名默认必须是 `False`。**钉默认值，不钉「它出现过」。**

    ⚠️ 只钉存在不钉性质（第 79 层同族）：断言源码里有 `return_parts`
    对「谁在用、用成什么」零信息量。
    """
    import inspect
    sig = inspect.signature(_daily_series)
    assert sig.parameters["return_parts"].default is False, (
        "默认必须是 False —— 改了默认值会让 8 个现存调用点"
        "在某次重构后静默换成 tuple")


# ---------------------------------------------------------------- G3


def test_market_plus_excess_reconstructs_the_basket_against_an_external_anchor():
    """`market + excess ≡ 等权篮子收益` —— **不依赖实现的外部锚**。

    判据来自定义而非实现：`excess` 是 `basket − market` 这个**差**，
    所以还原它必须得回 `basket`。手工算的等权均值 0.025 是**我们自己写的常数**，
    实现怎么改都不会跟着改 ⇒ 这才是外部锚（第 5/8 层）。
    """
    excess, market = _daily_series(_synth(), "up", return_parts=True)
    basket = market + excess

    # 幸存两天各 4 只：0.01/0.02/0.03/0.04 的等权 = 0.025
    assert len(basket) == 2, (
        f"夹具失效：幸存天数 {len(basket)} ≠ 2，"
        f"MIN_BASKET={MIN_BASKET} —— 这会让下面的断言变成恒真")
    assert np.allclose(basket.to_numpy(float), 0.025, rtol=0, atol=1e-12), (
        f"还原出的篮子收益 {basket.to_numpy(float)} 不是等权均值 0.025 ⇒ "
        "market 与 excess 不是同一个量的两项")
    assert np.allclose(market.to_numpy(float), 0.002, rtol=0, atol=1e-12), (
        "market 不是面板等权收益 0.002 ⇒ 它量的不是被减掉的那一项")


# ---------------------------------------------------------------- G4


def test_the_surviving_day_index_survives_the_min_basket_filter_too():
    """`MIN_BASKET` 剔除整天后，**`market` 也必须落在同一批日子上**。

    ⚠️ 掩码由 `count`（篮子行数）决定，与 `market` 无关 ⇒ 直接返回未过滤的
    `market` 会让两列行数不同。索引**本身就是幸存日集合的证明**。
    """
    res = _synth(n_per_day=(4, MIN_BASKET - 1, 4))
    excess, market = _daily_series(res, "up", return_parts=True)

    surviving = [str(d.date()) for d in excess.index]
    assert surviving == ["2021-01-05", "2021-01-07"], (
        f"夹具失效：幸存日 {surviving} 不等于预期（中间那天应被 "
        f"MIN_BASKET={MIN_BASKET} 整天剔除）")
    assert [str(d.date()) for d in market.index] == surviving, (
        "market 落在与 excess 不同的一批日子上")


# ------------------------------------------------ G5/G6/G7：equity_weight 与逐腿成本


def _uniform(n: int, actual_pct: float, market_pct: float, cost: float = 0.0):
    """两天、每天 `n` 条腿，且**逐条收益相同**的合成夹具（给 G5 用）。"""
    days = pd.to_datetime(["2021-01-05", "2021-01-06"])
    rows = [
        {"pred_date": d, "direction": "up", "code": f"C{i}",
         "actual_pct": actual_pct, "cost": cost,
         "market_pct": market_pct, "cap": 1.0}
        for d in days for i in range(n)
    ]
    return pd.DataFrame(rows)


def test_the_basket_is_a_mean_not_a_sum_so_the_account_is_fully_invested():
    """🚨 `equity_weight = 1.0`（满仓、无杠杆）—— **§12.3 的承重墙**。

    预注册 §12.3 那句「`real` 档在满仓下与真值只差 **0.002pp**」挂在
    「每笔名义 = ¥50,000 / N」上；而「每笔名义 = 本金 / N」**只在组合收益是均值时成立**。
    若哪天引擎把 `.mean()` 改成 `.sum()`，隐含权益立刻变成 `equity_weight = N`
    （实测 `equity_weight=0.5` 时真实往返 0.752% 已高于 `real` 的 0.550%），
    而**产物一列都不会少、没有一个值会变 NaN**。

    ⇒ 判据是**不依赖实现的外部锚**：N 条腿收益**全等于 `r`** 时，
    篮子必须等于 `r`，**与 N 无关**。`r` 是我们自己写的常数，
    实现怎么改都不会跟着改（第 5/8 层）⇒ 这才是外部锚而不是「反推实现的期望」。
    """
    r, m = 0.01, 0.002
    for n in (MIN_BASKET, 10, 40):
        excess, market = _daily_series(
            _uniform(n, actual_pct=r, market_pct=m), "up", return_parts=True)
        basket = (market + excess).to_numpy(float)
        assert len(basket) == 2, (
            f"夹具失效：N={n} 时幸存天数 {len(basket)} ≠ 2 ⇒ "
            f"MIN_BASKET={MIN_BASKET}，下面的断言会变成恒真")
        assert np.allclose(market.to_numpy(float), m, rtol=0, atol=1e-12), (
            f"N={n} 时 market 不是 {m} ⇒ 被减掉的那一项已经不对，"
            "这条守卫量的不是加法本身")
        assert np.allclose(basket, r, rtol=0, atol=1e-12), (
            f"N={n} 时篮子收益 {basket} 不是 {r} ⇒ 组合收益**不是均值**。"
            "若这是 `sum`，隐含 equity_weight 就变成 N 了，"
            "§12.3 关于 `real` 档上下界的整张表随之作废")


def test_the_cost_is_charged_per_leg_before_averaging_not_once_on_the_sum():
    """成本**逐腿**扣、扣完再平均 —— 会计恒等式 `毛 − 净 ≡ 实测成本` 的引擎侧。

    ⚠️ 成本**故意取成不等值**：全相等时「逐腿扣后平均」与「先平均再扣一次」
    在代数上**不可区分**，那种夹具会给两种写法同时发绿（第 117 层的
    「夹具让 `all()` 退化成装饰」的同族）。
    不等成本下两者才分得开：`sum` 后减一次 ⇒ 0.012，逐腿扣后平均 ⇒ 0.018。

    ⚠️ **边界**：本条**分不出**「逐腿扣再平均」与「先平均再减平均成本」——
    两者代数等价。要分出来必须把成本与收益**非共线**地构造，超出本条射程。
    """
    actual = [0.01, 0.02, 0.03, 0.04]
    cost = [0.001, 0.002, 0.003, 0.001]
    rows = [
        {"pred_date": pd.Timestamp("2021-01-05"), "direction": "up",
         "code": f"C{i}", "actual_pct": a, "cost": c,
         "market_pct": 0.002, "cap": 1.0}
        for i, (a, c) in enumerate(zip(actual, cost))
    ]
    excess, market = _daily_series(pd.DataFrame(rows), "up", return_parts=True)
    net_basket = (market + excess).to_numpy(float)

    expect = float(np.mean([a - c for a, c in zip(actual, cost)]))
    assert len(net_basket) == 1, (
        f"夹具失效：幸存天数 {len(net_basket)} ≠ 1 ⇒ "
        f"MIN_BASKET={MIN_BASKET}，下面的断言会变成恒真")
    assert np.allclose(net_basket, expect, rtol=0, atol=1e-12), (
        f"净篮子收益 {net_basket} 不是逐腿扣成本后的均值 {expect} ⇒ "
        "成本被以别的次序/次数施加（毛 − 净 就对不上实测成本，"
        "而 §8.3 的三档判决全建立在这条恒等式上）")


def test_only_the_up_leg_pays_the_cost_so_the_down_leg_is_not_double_charged():
    """成本**只**由 `direction == "up"` 那条腿付（`_daily_series` 的既有约定）。

    ⚠️ 钉的是「down 腿的成本不许被扣」。反过来若两条腿都扣，
    down 腿会变成 `0.01 − 0.005 − 0.002 = 0.003`，**与 up 腿同号同值** ——
    而事实是它只扣市场项 ⇒ `0.008`。两条腿同值正是「成本被扣两次」的签名，
    且它在产物里长得与正确答案非常像（只是更小、更容易被读成「策略不行」）。
    """
    day = pd.Timestamp("2021-01-05")
    rows = [
        {"pred_date": day, "direction": dirn, "code": f"{dirn}{i}",
         "actual_pct": 0.01, "cost": 0.005, "market_pct": 0.002, "cap": 1.0}
        for dirn in ("up", "down") for i in range(4)
    ]
    up_excess, _ = _daily_series(pd.DataFrame(rows), "up", return_parts=True)
    down_excess, _ = _daily_series(pd.DataFrame(rows), "down", return_parts=True)

    assert len(up_excess) == 1 and len(down_excess) == 1, (
        f"夹具失效：up/down 腿的幸存天数 {len(up_excess)}/{len(down_excess)} ≠ 1")
    assert np.allclose(up_excess.to_numpy(float), 0.003, rtol=0, atol=1e-12), (
        f"up 腿超额 {up_excess.to_numpy(float)} 不是 0.003 ⇒ "
        "涨腿的成本扣错了次数或方向")
    assert np.allclose(down_excess.to_numpy(float), 0.008, rtol=0, atol=1e-12), (
        f"down 腿超额 {down_excess.to_numpy(float)} 不是 0.008 ⇒ "
        "空头腿也在付成本 —— 那是成本被扣两次的签名，"
        "且它在产物里长得与正确答案非常像（只是更小、更像「策略不行」）")


# ------------------------------------------------- V3：射程正确的物理排除


def _imported_modules(path: Path) -> set:
    """该文件在**语法位置**上真正 import 的模块名。

    ⚠️ 用 AST 不用子串：`backtest_walk_forward_weights.py` 的模块 docstring 里
    逐字写着 `ic_to_return.py`（第 6 行「走的是 `ic_to_return.py` 那座桥」）⇒
    子串守卫会被**注释**满足（第 13 层）。
    """
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.add(f"{'.' * node.level}{mod}")
            out.update(f"{'.' * node.level}{mod}.{a.name}" for a in node.names)
    return out


def test_the_net_nav_path_never_imports_the_disproved_ic_to_return_bridge():
    """预注册 V3：`IC 只进权重，不进收益` —— 在**语法位置**上物理排除。

    🚨 射程**只限净值路径**。全仓有 6 处合法的 `ic_to_return` import
    （`verdict_composite` 等量的是桥本身）⇒ 扫全仓是过严守卫（第 112 层），
    它会把人推向「拆掉本来正确的东西」。
    """
    for path in _NET_NAV_PATH:
        hit = {m for m in _imported_modules(path) if "ic_to_return" in m}
        assert not hit, (
            f"{path.name} 在语法位置上 import 了被证伪的桥 {hit} ⇒ "
            "收益里出现了 IC × 价差这条被撤回的路径（预注册 V3）")


def test_the_exclusion_detector_can_actually_see_an_import():
    """**正控**：探测器本身不是恒真的（第 117 层）。

    一个 `return set()` 的 `_imported_modules` 会让上面那条**永远绿** ——
    而「永远绿」比「红」更危险，因为它看起来像「守住了」。
    """
    got = _imported_modules(_BACKTEST)
    assert got, "探测器没扫到任何 import —— 它坏了，不是实现干净"
    assert any(m.startswith("aplus") for m in got), (
        f"探测器没认出 aplus.* import：{sorted(got)}")
    assert any(m.startswith("pandas") or m.startswith("numpy") or m == "importlib.util"
               for m in got), f"探测器形状可疑：{sorted(got)}"


def test_the_ic_to_return_bridge_is_still_imported_where_measuring_it_is_the_point():
    """**反向正控**：过严守卫的解毒剂。

    若哪天有人为了「让它绿」把 `verdict_composite` 里的 import 也删了，
    那条路量的正是桥本身（桥错了就该在桥那侧量出来）⇒ 删掉是把测量拆了。
    ⇒ 显式钉住「那 6 处不许消失」。
    """
    must_keep = [_ROOT / "scripts" / "verdict_composite.py",
                 _ROOT / "scripts" / "measure_bridge_error.py"]
    for path in must_keep:
        hit = {m for m in _imported_modules(path) if "ic_to_return" in m}
        assert hit, (
            f"{path.name} 不再 import `ic_to_return` ⇒ "
            "要么测量被拆了，要么有人为了让 V3 那条守卫变绿而删错了地方")


# ------------------------------------------------ 透传：包装层不许吞掉参数


def test_the_wrapper_forwards_return_parts_instead_of_swallowing_it():
    """包装函数 `_daily` 必须**真的把 `return_parts` 传下去**。

    ⚠️ 只钉「签名里有这个参数」是第 79 层的「只钉存在不钉性质」：
    一个签名收了参数而调用时不传的包装函数，会让**上游拿到 Series**
    ⇒ 下游 `gross, market = parts` 报「不是足够多的值」，
    而这个错误出现在**重跑的最后一行**（GB 级 / 10 分钟后）。
    ⚠️ 判据落在**调用的 keyword 实参**上，不是源码里有那个词。
    """
    calls = [n for n in ast.walk(ast.parse(_BACKTEST.read_text()))
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_daily_series"]
    assert calls, "找不到对 `_daily_series` 的调用点 —— 守卫自己失效了"
    fwd = [c for c in calls
           if any(k.arg == "return_parts" for k in c.keywords)]
    assert fwd, (
        "包装函数没有把 `return_parts` 转发给 `_daily_series` ⇒ "
        "打开开关的人拿到的仍是单根 Series，而崩点出现在重跑的最后一行")


# ---------------------------------------------------------------- 落盘


def _wanted_pairs(path: Path):
    """`_assemble` 里 `wanted = [...]` 的字面项 `[(列名, 取值表达式), …]`。

    ⚠️ 判据必须锚在**产生那列的语法位置**上。
    只断言「`market_return` 这个串在文件里出现过」会被**注释**满足 ——
    而本文件的模块 docstring 恰好逐字讨论过这个口径（第 13 层）。
    """
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "wanted" not in targets or not isinstance(node.value, ast.List):
            continue
        pairs = []
        for el in node.value.elts:
            if (isinstance(el, ast.Tuple) and len(el.elts) == 2
                    and isinstance(el.elts[0], ast.Constant)):
                pairs.append((el.elts[0].value, el.elts[1]))
        if pairs:
            return pairs
    return []


def test_the_landed_column_reads_market_from_the_chunk_not_from_a_literal():
    """落盘列名 `market_return`，取值**必须**是 `c.get("market")` 这条路径。

    ⚠️ 只钉「列名存在」不够 —— 那会让一个写成 `("market_return", None)` 的
    版本照样绿，而 `.daily.parquet` 里会多一整列 NaN、**不报任何错**
    （第 79 层：只钉存在不钉性质）。
    """
    pairs = _wanted_pairs(_BACKTEST)
    assert pairs, (
        "找不到 `_assemble` 里的 `wanted` 字面列表 —— 守卫自己失效了，"
        "不是实现变干净了")
    names = [p[0] for p in pairs]
    assert "gross_excess" in names, f"落盘列名变了：{names}"
    assert "market_return" in names, (
        f"落盘列名不是 `market_return`（实得 {names}）⇒ "
        "产物里的基准项无标签，会被读成指数")

    src_expr = dict(pairs)["market_return"]
    assert isinstance(src_expr, ast.Call), (
        f"`market_return` 取值是 {type(src_expr).__name__} 而不是 `c.get(...)` ⇒ "
        "它不是从 chunk 里取的那一项")
    func = src_expr.func
    assert isinstance(func, ast.Attribute) and func.attr == "get", (
        "`market_return` 没有走 `.get(...)` ⇒ 缺列时会 KeyError，"
        "而那条分支在旧产物上从未被执行过（第 9 种形状）")
    assert isinstance(func.value, ast.Name) and func.value.id == "c", (
        "`market_return` 取的不是 chunk `c` 里的东西")


def test_the_market_caliber_is_stamped_into_the_artifact_not_only_into_prose():
    """口径必须**跟着数字离开脚本**（预注册 §8.4 决策三）⇒ 结构化字段。

    ⚠️ 写进 `note` 不算：`note` 是散文，读的人不必读它。
    ⚠️ 判据全部走 **AST 的 Dict 节点**，不用字符串切块 —— 切块会被
    同一份源码里的注释与 key 名满足（第 13 层：本文件自己的常量就能喂饱它）。
    """
    caliber = _product_dict_literal("caliber")
    assert caliber is not None, (
        "产物 dict 里没有 `caliber` 结构化口径块 ⇒ 口径只活在源码注释里，"
        "而 `.daily.parquet` 的读者看不到注释")

    mkt = _subdict(caliber, "market_return")
    assert mkt is not None, "`caliber` 里没有认领 `market_return`"
    keys = _string_keys(mkt)
    assert "what_it_is" in keys, f"`market_return` 口径缺 `what_it_is`：{keys}"
    assert "what_it_is_not" in keys, (
        f"只写了「它是什么」而没写「它不是什么」（实得 {keys}）⇒ "
        "最可能的误读（把它当中证全指 / 市值加权）没有任何东西挡住")

    text = " ".join(_all_strings(mkt))
    assert "等权" in text, f"口径没说是哪一档（实得 {text[:200]}）"
    assert "市值加权" in text or "cap_weighted" in text, (
        "口径块没提「不是市值加权」—— 那是另一个产物（cap_weighted_benchmark.json）"
        "的口径，两个混起来会得到另一个净值（第 7 层）")


def _product_dict_literal(key: str):
    """产物 `res = {...}` 里 `key` 对应的 Dict 字面量。"""
    for node in ast.walk(ast.parse(_BACKTEST.read_text())):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if (isinstance(k, ast.Constant) and k.value == key
                    and isinstance(v, ast.Dict)):
                return v
    return None


def _subdict(d: ast.Dict, key: str):
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and k.value == key and isinstance(v, ast.Dict):
            return v
    return None


def _string_keys(d: ast.Dict):
    return [k.value for k in d.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)]


def _all_strings(node) -> list:
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
