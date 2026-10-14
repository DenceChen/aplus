"""历史预测与验证装配层测试：TDD RED 先行。"""
import pandas as pd
import pytest

from aplus.history import hit_rate_by_date, prediction_history
from aplus.reports import append_verification, save_predictions

HISTORY_COLUMNS = [
    "pred_date", "code", "name", "direction", "score", "mom20", "actual_pct", "result",
]


def _pred_frame(rows):
    """rows: (code, name, direction, score) 元组列表 → 补齐 mom20/rev5/vol20/vsur 因子列。"""
    return pd.DataFrame(
        {
            "code": [r[0] for r in rows],
            "name": [r[1] for r in rows],
            "direction": [r[2] for r in rows],
            "score": [r[3] for r in rows],
            "mom20": [0.1] * len(rows),
            "rev5": [0.0] * len(rows),
            "vol20": [0.2] * len(rows),
            "vsur": [1.0] * len(rows),
        }
    )


def _fixture(reports_dir):
    """两个预测日（01-08 / 01-05）+ 仅 01-05 一天的验证数据。

    01-05 判定：hit 1 条 + miss 1 条 + flat 1 条（flat 用于命中率排除断言）。
    """
    save_predictions(
        reports_dir,
        "2026-01-05",
        _pred_frame([("600000.SH", "甲", "up", 3.0), ("600002.SH", "丙", "up", 1.5)]),
        _pred_frame([("600001.SH", "乙", "down", -2.0)]),
    )
    save_predictions(
        reports_dir,
        "2026-01-08",
        _pred_frame([("600003.SH", "丁", "up", 2.5)]),
        _pred_frame([("600004.SH", "戊", "down", -1.0)]),
    )
    verification = pd.DataFrame(
        {
            "pred_date": ["2026-01-05"] * 3,
            "code": ["600000.SH", "600002.SH", "600001.SH"],
            "direction": ["up", "up", "down"],
            "result": ["hit", "flat", "miss"],
            "actual_pct": [0.03, 0.0, 0.02],
            "market_pct": [0.01, 0.01, 0.01],
            "verified_on": ["2026-01-06"] * 3,
        }
    )
    append_verification(reports_dir, verification)


def test_prediction_history_join_verified_and_unverified(tmp_path):
    _fixture(tmp_path)
    df = prediction_history(tmp_path)
    assert list(df.columns) == HISTORY_COLUMNS

    by_code = df.set_index("code")
    # 已验证行：带 result 与 actual_pct
    assert by_code.loc["600000.SH", "result"] == "hit"
    assert by_code.loc["600000.SH", "actual_pct"] == pytest.approx(0.03)
    assert by_code.loc["600001.SH", "result"] == "miss"
    assert by_code.loc["600002.SH", "result"] == "flat"
    # 未验证行：result / actual_pct 为 NaN 或 None
    assert pd.isna(by_code.loc["600003.SH", "result"])
    assert pd.isna(by_code.loc["600003.SH", "actual_pct"])
    assert pd.isna(by_code.loc["600004.SH", "result"])


def test_prediction_history_sorted_date_desc_then_score_desc(tmp_path):
    _fixture(tmp_path)
    df = prediction_history(tmp_path)
    assert df["pred_date"].tolist() == [
        "2026-01-08", "2026-01-08",  # 新预测日在最前
        "2026-01-05", "2026-01-05", "2026-01-05",
    ]
    # 同日内按 score 降序
    day5 = df[df["pred_date"] == "2026-01-05"]
    assert day5["score"].tolist() == sorted(day5["score"], reverse=True)
    assert df["code"].tolist() == ["600003.SH", "600004.SH", "600000.SH", "600002.SH", "600001.SH"]


def test_prediction_history_empty_dir(tmp_path):
    df = prediction_history(tmp_path)
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert list(df.columns) == HISTORY_COLUMNS


def test_hit_rate_by_date_excludes_flat_and_unjudged_day_is_none(tmp_path):
    _fixture(tmp_path)
    rates = hit_rate_by_date(tmp_path)
    assert [r["date"] for r in rates] == ["2026-01-08", "2026-01-05"]  # 日期降序
    # 01-08 未验证：judged=0 → hit_rate=None
    unverified = rates[0]
    assert unverified["judged"] == 0
    assert unverified["hit_rate"] is None
    # 01-05：hit 1 + miss 1 计入，flat 排除 → 0.5
    verified = rates[1]
    assert verified["judged"] == 2
    assert verified["hit_rate"] == pytest.approx(0.5)


def test_hit_rate_by_date_empty_dir(tmp_path):
    assert hit_rate_by_date(tmp_path) == []
