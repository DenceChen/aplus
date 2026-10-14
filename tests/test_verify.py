"""验证闭环测试：TDD RED 先行。

设计（PLATFORM.md §3）：T+1 收盘后自动判定预测对错，永久留存，滚动统计命中率。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.verify import judge, rolling_stats, verify_predictions


# ---------- 单条判定 ----------

def test_judge_up_prediction_correct():
    assert judge({"direction": "up"}, 0.03) == "hit"
    assert judge({"direction": "up"}, -0.02) == "miss"


def test_judge_down_prediction_correct():
    assert judge({"direction": "down"}, -0.05) == "hit"
    assert judge({"direction": "down"}, 0.01) == "miss"


def test_judge_flat_and_missing():
    assert judge({"direction": "up"}, 0.0) == "flat"
    assert judge({"direction": "down"}, np.nan) == "no_data"


# ---------- 批量验证 ----------

def _preds():
    return pd.DataFrame({
        "date": ["2026-01-05"] * 3,
        "code": ["600000.SH", "600001.SH", "600002.SH"],
        "direction": ["up", "down", "up"],
    })


def _bars():
    return pd.DataFrame({
        "code": ["600000.SH", "600001.SH", "600002.SH"],
        "close": [11.0, 9.0, 10.0],      # 前收都是 10
        "prev_close": [10.0, 10.0, 10.0],
    })


def test_verify_predictions_joins_and_judges():
    results = verify_predictions(_preds(), _bars())
    assert results["result"].tolist() == ["hit", "hit", "flat"]
    assert results["actual_pct"].tolist() == pytest.approx([0.1, -0.1, 0.0])


def test_verify_predictions_missing_bar_marks_no_data():
    preds = _preds()
    preds.loc[2, "code"] = "600099.SH"  # 无次日行情
    results = verify_predictions(preds, _bars())
    assert results.loc[2, "result"] == "no_data"


# ---------- 滚动统计 ----------

def _history():
    return pd.DataFrame({
        "date": ["2026-01-05"] * 4,
        "direction": ["up", "up", "down", "down"],
        "result": ["hit", "miss", "hit", "miss"],
        "actual_pct": [0.03, -0.02, -0.05, 0.01],
        "market_pct": [0.01, 0.01, 0.01, 0.01],
    })


def test_rolling_stats_direction_hit_rates():
    stats = rolling_stats(_history())
    assert stats["hit_rate_up"] == pytest.approx(0.5)
    assert stats["hit_rate_down"] == pytest.approx(0.5)
    assert stats["overall_hit_rate"] == pytest.approx(0.5)


def test_rolling_stats_excess_return():
    stats = rolling_stats(_history())
    # 预测涨的组合平均实际收益 = (0.03 - 0.02)/2 = 0.005；市场 = 0.01 → 超额 -0.005
    assert stats["avg_up_return"] == pytest.approx(0.005)
    assert stats["up_excess_vs_market"] == pytest.approx(-0.005)


def test_rolling_stats_empty_history_is_zero_not_crash():
    stats = rolling_stats(pd.DataFrame(columns=["date", "direction", "result", "actual_pct", "market_pct"]))
    assert stats["overall_hit_rate"] == 0.0


def test_rolling_stats_totally_empty_frame_without_columns():
    """真实回归：文件不存在时 load 返回无列空 DataFrame，不应 KeyError。"""
    stats = rolling_stats(pd.DataFrame())
    assert stats["overall_hit_rate"] == 0.0
    assert stats["up_excess_vs_market"] == 0.0
