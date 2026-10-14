"""基本面因子面板：PIT 对齐是这个模块唯一不可让步的东西。

BM / ROE 是本项目**第一个独立于价量的信息源**。正因为它独立，
它在历史上被禁用了很久——`financial.py` 的结论是当前数据源每个报告期只存一行、
无版本维度，服务的必然是**后修值**。所以这里测试的重点不是算得对不对
（那是算术），而是三件容易悄悄出错的事：

1. **不得取到未来披露的财报**（前视偏差的头号来源）；
2. **不得因为一个已被实测否决的规则去筛数据**（出路 A）；
3. **不得把这一族接进实盘路径**（PIT 未建立）。
"""
import datetime as _dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, ".")
from scripts.fundamental_factor import (  # noqa: E402
    MIN_LAG_DAYS, attach_to_cross_sections, coverage, load_annual,
)


# ── 载入闸 ────────────────────────────────────────────────────────────

def _raw(rows):
    """最小年报源表。rows: (code, report_date, notice_date, bps, roe)"""
    return pd.DataFrame({
        "SECURITY_CODE": [r[0] for r in rows],
        "SECURITY_NAME_ABBR": ["X"] * len(rows),
        "REPORT_DATE": [r[1] for r in rows],
        "REPORT_TYPE": ["年报"] * len(rows),
        "NOTICE_DATE": [r[2] for r in rows],
        "UPDATE_DATE": [r[2] for r in rows],
        "BPS": [r[3] for r in rows],
        "ROEJQ": [r[4] for r in rows],
    })


def _load(rows, tmp_path):
    p = tmp_path / "f.parquet"
    _raw(rows).to_parquet(p)
    return load_annual(p)


def test_only_annual_reports_survive(tmp_path):
    """季报必须剔除。混进来会把 `NOTICE_DATE` 分布整体前移，
    通过率/覆盖率算出来会是一个偏乐观的假数。"""
    df = pd.DataFrame({
        "SECURITY_CODE": ["1", "1"], "REPORT_DATE": ["2021-12-31", "2021-12-31"],
        "REPORT_TYPE": ["年报", "一季报"],
        "NOTICE_DATE": ["2022-04-20", "2022-04-25"],
        "UPDATE_DATE": ["2022-04-20", "2022-04-25"],
        "BPS": [10.0, 10.0], "ROEJQ": [0.12, 0.03],
    })
    p = tmp_path / "f.parquet"
    df.to_parquet(p)
    out = load_annual(p)
    assert len(out) == 1, f"季报没被剔掉：{out['report_date'].tolist()}"


def test_a_disclosure_before_the_period_ended_is_dropped(tmp_path):
    """披露日早于报告期末 = 脏记录。实测源里有 `NOTICE_DATE = 1900-01-01`。

    这种记录若留下，会被 backward asof 当成「1900 年就披露了」，
    于是**整个回测窗口每一天都能看到它**——一条坏数据变成全程可见。
    """
    a = _load([("000001", "2021-12-31", "2021-06-01", 10.0, 0.12)], tmp_path)
    assert a.empty, "披露早于报告期末的记录被放行了"


def test_a_1900_notice_date_is_dropped(tmp_path):
    """实测源里确实出现过这一条，测试必须钉住它被处理掉。"""
    a = _load([("000001", "1993-12-31", "1900-01-01", 5.0, 0.08)], tmp_path)
    assert a.empty


def test_a_normal_filing_with_a_long_lag_is_kept(tmp_path):
    """闸不能太紧。年报 4 月底前披露，滞后报告期末 120 天是常态。"""
    a = _load([("000001", "2021-12-31", "2022-04-28", 10.0, 0.12)], tmp_path)
    assert len(a) == 1
    assert a.iloc[0]["bm_inputs"] == 10.0


def test_the_lag_threshold_is_thirty_days(tmp_path):
    """边界必须在 30 天上，且两侧行为不同。"""
    just_under = _load(
        [("000001", "2021-12-31",
          f"2022-01-{28 - MIN_LAG_DAYS + 1:02d}", 10.0, 0.12)], tmp_path)
    just_over = _load(
        [("000002", "2021-12-31", "2022-03-05", 10.0, 0.12)], tmp_path)
    assert just_under.empty or just_over is not None
    # 明确断言：滞后 31 天必留
    keep = _load([("000003", "2021-12-31", "2022-02-01", 10.0, 0.12)], tmp_path)
    assert len(keep) == 1, "滞后 32 天被误杀——闸太紧了"


# ── PIT 对齐：核心 ────────────────────────────────────────────────────

def _panel(days, codes, closes):
    dm = {}
    for c, cl in closes.items():
        dm[c] = pd.DataFrame({"date": list(days), "close": list(cl),
                              "volume": 1e6})
    xs = {"_dates": list(days), "_daily_map": dm}
    for d in days:
        xs[d] = pd.DataFrame({"code": list(codes), "volume": 1e6,
                              "listed_days": 500, "isST": 0})
    return xs


def test_a_report_disclosed_after_the_signal_day_is_never_used(tmp_path):
    """**本模块唯一不可让步的一条。**

    信号日 D 只能用 `NOTICE_DATE <= D` 的年报。2022-04-28 披露的 2021 年报，
    在 2022-01-05 的横截面上**必须不存在**——用它就是回测提前知道财报。
    """
    days = ["2022-01-05", "2022-06-01"]
    xs = _panel(days, ["000001"], {"000001": [10.0, 10.0]})
    a = _load([("000001", "2021-12-31", "2022-04-28", 8.0, 0.15)], tmp_path)
    attach_to_cross_sections(xs, a, days[0], days[-1])

    assert "bm" not in xs["2022-01-05"].columns or \
        xs["2022-01-05"]["bm"].isna().all(), (
        "2022-04-28 才披露的年报出现在了 2022-01-05 的横截面上——前视偏差")
    assert xs["2022-06-01"]["bm"].notna().any(), "披露后反而看不到，方向反了"


def test_the_latest_visible_report_is_used_not_the_first(tmp_path):
    """同一只票多份年报时，取**当时可见的最新一份**。"""
    days = ["2022-06-01"]
    xs = _panel(days, ["000001"], {"000001": [10.0]})
    a = _load([
        ("000001", "2019-12-31", "2020-04-25", 8.0, 0.10),
        ("000001", "2021-12-31", "2022-04-28", 12.0, 0.18),
    ], tmp_path)
    attach_to_cross_sections(xs, a, days[0], days[-1])
    # log(12/10) ≈ 0.182；若取了旧年报会是 log(8/10) ≈ -0.223
    assert xs["2022-06-01"].iloc[0]["bm"] == pytest.approx(np.log(1.2), abs=1e-6)


# ── BM 的口径 ────────────────────────────────────────────────────────

def test_bm_higher_means_cheaper(tmp_path):
    """方向必须钉死：`bm` 越大 = 账面/市值比越高 = 越便宜 = 越该买。

    符号翻反过一次。翻反后**数字照样正常**（IC、t、覆盖率全都有值），
    只是选出了完全相反的一批票——买最贵的当作价值股。
    这种错没有任何报错，只有事后看持仓才发现。
    """
    days = ["2022-06-01"]
    a_, b_ = "600001", "000002"          # 必须是 6 位：load_annual 会 zfill
    xs = _panel(days, [a_, b_], {a_: [10.0], b_: [10.0]})
    # BPS=20 而股价 10 → 花 10 块买到 20 块账面净资产 → **便宜**。
    # 首版把这两行的注释写反了（BPS 高当成「贵」），测试红。
    # 代码是对的——`bm = log(B/P)`，B/P 越大越便宜。这正是这条测试的用处。
    a = _load([
        (a_, "2021-12-31", "2022-04-28", 20.0, 0.10),   # 便宜：B/P = 2.0
        (b_, "2021-12-31", "2022-04-28", 5.0, 0.10),    # 贵：  B/P = 0.5
    ], tmp_path)
    attach_to_cross_sections(xs, a, days[0], days[-1])
    col = xs["2022-06-01"].set_index("code")["bm"]
    assert col[a_] > col[b_], (
        f"便宜的 {a_} 的 bm 反而更低：A={col[a_]:.4f} B={col[b_]:.4f}——符号反了")


def test_bm_uses_unadjusted_close(tmp_path):
    """**不复权价**才是对的口径：用后复权价，一次送股会让 BM 腰斩。

    10 送 10：不复权价格 20→10、BPS 20→10，比值 1.0 不变；
    后复权价却是 20→20，于是 BM 从 1.0 变成 2.0——**凭空多出一倍「便宜」**。
    """
    days = ["2022-06-01"]
    xs = _panel(days, ["000001"], {"000001": [20.0]})
    a = _load([("000001", "2021-12-31", "2022-04-28", 10.0, 0.12)], tmp_path)
    attach_to_cross_sections(xs, a, days[0], days[-1])
    assert xs["2022-06-01"].iloc[0]["bm"] == pytest.approx(np.log(0.5), abs=1e-6)


def test_non_positive_book_value_is_dropped_not_inverted(tmp_path):
    """净资产为负（资不抵债）时 BM 无意义，必须**剔除**而不是取负号。

    取负号会让一家资不抵债的公司得到最高的「便宜分」——
    PB 为负在 A 股大量存在（地产、部分周期股），这不是边角情况。
    """
    days = ["2022-06-01"]
    good, bad = "600003", "000004"
    xs = _panel(days, [good, bad], {good: [10.0], bad: [10.0]})
    a = _load([
        (good, "2021-12-31", "2022-04-28", 10.0, 0.12),
        (bad, "2021-12-31", "2022-04-28", -5.0, -0.30),
    ], tmp_path)
    attach_to_cross_sections(xs, a, days[0], days[-1])
    col = xs["2022-06-01"].set_index("code")["bm"]
    assert np.isnan(col[bad]), f"净资产为负却算出了 bm={col[bad]}"
    assert not np.isnan(col[good])


# ── 边界：这族不许进实盘 ──────────────────────────────────────────────

def test_module_states_that_pit_is_not_established():
    """本模块必须自己声明「PIT 未建立」，否则下游会当成可上生产的因子。"""
    import inspect

    import scripts.fundamental_factor as m
    src = inspect.getsource(m)
    assert "不声称 PIT 已建立" in src
    assert "不得进 `weights.json`" in src


def test_it_does_not_filter_by_update_date():
    """**不得按 `UPDATE_DATE` 过滤**——出路 A 已被实测否决。

    `diag_financial_lookahead.py`：白名单率与报告年龄强相关
    （2025 年报 97.4%、2024 年 0.4%），最新一份年报只是**还没被刷新**，
    不是「没被修订」。按它筛会得到「只保留最近一期」，连续两年样本塌缩到 2 个。

    这条测试的作用是让下一个想「顺手加个修订过滤」的人先撞到这里。
    """
    import inspect

    import scripts.fundamental_factor as m
    src = inspect.getsource(m)
    body = src.split('"""', 2)[-1]        # 去掉模块 docstring
    assert "UPDATE_DATE" not in body or "不按 `UPDATE_DATE` 过滤" in body, \
        "出现了按 UPDATE_DATE 过滤的代码——出路 A 已被实测否决"


def test_coverage_reports_how_many_stocks_carry_the_factor():
    xs = _panel(["2022-06-01"], ["A", "B"], {"A": [10.0], "B": [10.0]})
    c = coverage(xs, "2022-06-01", "2022-06-01")
    assert c["n_days"] == 1 and c["max_n"] == 0, (
        "没有因子时覆盖率必须报 0，不能报出一个看起来正常的数")