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
    # ⇒ 缺口登记在 `docs/TODO_generator_static_guards_2026-10-15.md`。
