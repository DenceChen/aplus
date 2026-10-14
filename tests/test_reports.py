"""报告持久化测试：TDD RED 先行。"""
import pandas as pd
import pytest

from aplus.reports import (
    append_verification,
    latest_prediction_date,
    load_predictions,
    load_verification_history,
    save_allocation,
    save_predictions,
)


def _updown():
    up = pd.DataFrame({"code": ["600000.SH"], "name": ["甲"], "direction": ["up"], "score": [2.0]})
    down = pd.DataFrame({"code": ["600001.SH"], "name": ["乙"], "direction": ["down"], "score": [-2.0]})
    return up, down


def test_predictions_roundtrip(tmp_path):
    save_predictions(tmp_path, "2026-01-05", *_updown())
    up, down = load_predictions(tmp_path, "2026-01-05")
    assert up["code"].tolist() == ["600000.SH"]
    assert down["direction"].tolist() == ["down"]


def test_load_missing_predictions_returns_none(tmp_path):
    assert load_predictions(tmp_path, "1999-01-01") is None


def test_latest_prediction_date(tmp_path):
    assert latest_prediction_date(tmp_path) is None
    save_predictions(tmp_path, "2026-01-05", *_updown())
    save_predictions(tmp_path, "2026-01-08", *_updown())
    assert latest_prediction_date(tmp_path) == "2026-01-08"


def test_verification_append_and_history(tmp_path):
    day1 = pd.DataFrame({
        "pred_date": ["2026-01-05"], "code": ["600000.SH"], "direction": ["up"],
        "actual_pct": [0.03], "result": ["hit"], "market_pct": [0.01],
    })
    day2 = day1.assign(pred_date=["2026-01-06"], result=["miss"], actual_pct=[-0.02])
    append_verification(tmp_path, day1)
    append_verification(tmp_path, day2)
    history = load_verification_history(tmp_path)
    assert len(history) == 2
    assert set(history["pred_date"]) == {"2026-01-05", "2026-01-06"}


def test_allocation_roundtrip(tmp_path):
    from aplus.reports import load_allocation

    plan_dict = {"capital": 50000, "positions": [], "cash_weight": 1.0, "risk_notes": ["x"]}
    save_allocation(tmp_path, "2026-01-05", plan_dict)
    assert load_allocation(tmp_path, "2026-01-05")["capital"] == 50000


def test_run_status_roundtrip_and_missing(tmp_path):
    from aplus.reports import read_run_status, write_run_status

    assert read_run_status(tmp_path) is None  # 无记录 → None
    write_run_status(tmp_path, {"date": "2026-10-10", "status": "ok", "steps": {"update": "ok"}})
    status = read_run_status(tmp_path)
    assert status["date"] == "2026-10-10"
    assert status["steps"]["update"] == "ok"
