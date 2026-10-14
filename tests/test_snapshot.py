"""横截面快照构建测试：TDD RED 先行。

build_cross_section 从主库读全部股票、算因子、拼成横截面表（predict 的直接输入）。
"""
import pandas as pd
import pytest

from aplus.snapshot import build_cross_section
from aplus.store import LocalStore


def _make_daily(n=60, start_price=10.0, st=0, code_rows=None):
    dates = pd.bdate_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    closes = [start_price + i * 0.05 for i in range(n)]
    df = pd.DataFrame({
        "date": dates,
        "code": ["600000.SH"] * n,
        "open": closes,
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
        "volume": [1e6] * n,
        "amount": [1e7] * n,
        "tradestatus": [1] * n,
        "isST": [st] * n,
    })
    return df if code_rows is None else df


def test_build_cross_section_assembles_factors_and_flags(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _make_daily(60))
    store.upsert_daily("600001.SH", _make_daily(60, start_price=20.0, st=1))
    store.upsert_daily("600002.SH", _make_daily(30))  # 历史不足 → 因子列 NaN 或剔除

    cross = build_cross_section(store)
    assert set(cross["code"]) >= {"600000.SH", "600001.SH"}
    row = cross[cross["code"] == "600000.SH"].iloc[0]
    for col in ("mom20", "rev5", "vol20", "vsur", "isST", "listed_days", "volume", "name"):
        assert col in cross.columns
    assert row["isST"] == 1 or cross[cross["code"] == "600001.SH"].iloc[0]["isST"] == 1
    assert row["listed_days"] == 60


def test_build_cross_section_skips_stocks_with_insufficient_history(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_daily("600003.SH", _make_daily(10))  # 远不够 41 天
    cross = build_cross_section(store)
    assert "600003.SH" not in set(cross["code"])


def test_build_cross_section_as_of_filters_future_rows(tmp_path):
    store = LocalStore(tmp_path)
    df = _make_daily(60)
    store.upsert_daily("600000.SH", df)
    # 用第 50 天作为 as_of：动量窗口只能用前 50 天
    as_of = df["date"].iloc[49]
    cross = build_cross_section(store, as_of=as_of)
    row = cross[cross["code"] == "600000.SH"].iloc[0]
    assert row["listed_days"] == 50


def test_build_cross_section_uses_names_map(tmp_path):
    """股票中文名来自 baostock 股票列表（日线数据里没有 name）。"""
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _make_daily(60))
    cross = build_cross_section(store, names_map={"600000.SH": "浦发银行"})
    assert cross[cross["code"] == "600000.SH"].iloc[0]["name"] == "浦发银行"


def test_cross_section_tolerates_nan_isst(tmp_path):
    """真实回归（6 个月窗口实证）：baostock 历史行的 isST 可能是空串→NaN，
    `int(NaN)` 曾让回测崩溃。

    **2026-10-12 改了取值断言**（原来断言 `== 0`）。原断言钉的是缺陷本身：
    把「不知道是不是 ST」折成 `0`，而 `0` 的语义是「**不是** ST 股」。
    实测末根被降级源削掉 isST 时，3342 只股票因此绕过 ST 过滤，
    2026-10-11 的 20 只推荐里 6 只落在盲区。

    防崩溃的意图**原样保留**——这才是这个回归真正要防的东西；
    改的只是「崩溃之后该输出什么」。
    """
    import math

    store = LocalStore(tmp_path)
    df = _make_daily(60)
    df.loc[59, "isST"] = float("nan")
    store.upsert_daily("600000.SH", df)
    cross = build_cross_section(store)                 # 不许崩
    value = float(cross[cross["code"] == "600000.SH"].iloc[0]["isST"])
    assert math.isnan(value), \
        f"未知的 ST 状态必须如实传递 NaN，而不是折成 0（实际 {value}）"


def test_build_cross_section_attaches_factor_store(tmp_path):
    """真实缺口回归：日线 parquet 没有 adjust_factor 列——因子必须从 factor 表
    前向填充接上，否则动量在跨除权窗口失真。除权日（第 50 天）落在 mom20 回看
    窗口内：raw 口径把除权跳降当成暴跌（-8.8%），复权口径正确识别趋势（+14%）。
    """
    n = 60
    daily = _make_daily(n)
    closes = [10.0 + i * 0.05 for i in range(n)]
    closes[50:] = [c - 2.0 for c in closes[50:]]  # 第 50 天除权，价格跳降 2 元
    daily["close"] = closes
    events = pd.DataFrame({"date": [daily["date"].iloc[50]], "adjust_factor": [1.25]})
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", daily)
    store.upsert_factor("600000.SH", events)
    cross = build_cross_section(store)
    row = cross[cross["code"] == "600000.SH"].iloc[0]
    expected_adj = closes[59] * 1.25 / closes[39] - 1  # 窗口起点在除权前（因子 1.0）
    expected_raw = closes[59] / closes[39] - 1
    assert row["mom20"] == pytest.approx(expected_adj, rel=1e-6)
    assert abs(row["mom20"] - expected_raw) > 0.05  # 两种口径差异显著，必须走复权
