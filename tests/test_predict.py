"""预测引擎测试：TDD RED 先行。

设计（PLATFORM.md §2）：
- 股票池过滤：非 ST、上市 > 60 交易日、当日有成交（volume>0）
- 合成分 Top10 = 预测涨，Bottom10 = 预测跌（不足 10 取全部）
- 每条预测带方向、分数、因子快照（可解释性）
"""
import pandas as pd
import pytest

from aplus.predict import build_predictions, filter_universe


def _cross_section(n_stocks, seed_base=0.0):
    """构造 n 只股票的横截面因子表。"""
    return pd.DataFrame({
        "code": [f"{600000 + i}.SH" for i in range(n_stocks)],
        "name": [f"股票{i}" for i in range(n_stocks)],
        "mom20": [seed_base + i * 0.1 for i in range(n_stocks)],
        "rev5": [0.0] * n_stocks,
        "vol20": [0.2] * n_stocks,
        "vsur": [1.0] * n_stocks,
        "isST": [0] * n_stocks,
        "listed_days": [100] * n_stocks,
        "volume": [1e6] * n_stocks,
    })


def test_filter_universe_drops_st_new_and_no_volume():
    df = _cross_section(5)
    df.loc[0, "isST"] = 1          # ST 剔除
    df.loc[1, "listed_days"] = 30  # 次新剔除
    df.loc[2, "volume"] = 0        # 停牌/无成交剔除
    kept = filter_universe(df)
    assert kept["code"].tolist() == ["600003.SH", "600004.SH"]


def test_filter_universe_drops_stale_data():
    """真实回归（2026-10-10 全池回测实证）：退市股最后一行行情停在摘牌日，
    as_of 切片后仍像「活股」，以冻结动量霸榜且次日必 no_data。
    股票池必须要求最新数据日距快照日 ≤ 30 个自然日（长停牌股本来也不该推荐）。"""
    df = _cross_section(3)
    df["last_date"] = ["2026-06-01", "2026-09-20", "2026-10-09"]
    kept = filter_universe(df, as_of="2026-10-10")
    assert kept["code"].tolist() == ["600001.SH", "600002.SH"]  # 6 月退市股剔除；9 月近停牌、10 月正常都保留


def test_build_predictions_top10_bottom10():
    df = _cross_section(30)
    up, down = build_predictions(df, top_n=10, bottom_n=10)
    assert len(up) == 10 and len(down) == 10
    # mom 单调递增 → 分数最高者是 600029，最低者是 600000
    assert up["code"].iloc[0] == "600029.SH"
    assert down["code"].iloc[0] == "600000.SH"
    # up 与 down 无交集
    assert not set(up["code"]) & set(down["code"])
    # 每条带方向与分数
    assert (up["direction"] == "up").all()
    assert (down["direction"] == "down").all()
    assert up["score"].iloc[0] == pytest.approx(up["score"].max())


def test_build_predictions_small_universe_returns_all():
    df = _cross_section(6)
    up, down = build_predictions(df, top_n=10, bottom_n=10)
    assert len(up) == 6 and len(down) == 6
    assert set(up["code"]) == set(down["code"])  # 小池子时同批股票双向参考


def test_build_predictions_carries_factor_snapshot():
    df = _cross_section(12)
    up, _ = build_predictions(df, top_n=3, bottom_n=3)
    for col in ("mom20", "rev5", "vol20", "vsur", "name"):
        assert col in up.columns


def test_build_predictions_drops_nan_scores():
    """真实回归：部分股票因子 NaN（脏数据/窗口不足）——排名必须剔除而非错位。"""
    df = _cross_section(12)
    df.loc[df["mom20"].idxmax(), "mom20"] = float("nan")  # 最高分者变 NaN
    up, down = build_predictions(df, top_n=5, bottom_n=5)
    assert not up["score"].isna().any()
    assert not down["score"].isna().any()
