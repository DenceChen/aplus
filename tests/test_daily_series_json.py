"""`daily_series` 绝不能吐出 NaN —— NaN 不是合法 JSON。

## 事故（2026-10-11 16:40）

用户在看板上点「回测」，页面空白。`/api/backtest` 返回 **500**，
真实报错：

    ValueError: Out of range float values are not JSON compliant: nan

Starlette 的 `JSONResponse` 用 `json.dumps(..., allow_nan=False)`，
遇到 NaN 直接抛。`485` 行里有 `20` 行的 `down_excess` / `breadth` 是 NaN，
整个端点挂掉——**一张图里 4% 的坏点让整页不可用**。

## 为什么已有的 None 处理没挡住

`daily_series` 确实写了 `if len(down) else None`，但漏了一条路径：

    down 非空、而 down["actual_pct"] **全是 NaN** 时，`.mean()` 返回 NaN
    ——不是 None。判空判的是**行数**，而坏点在**值**上。

2026-10-11 那份 `backtest.parquet` 正是这种形状：10:49 写的旧数据，
没有跌腿行，部分日期只剩涨腿的空壳行。

## 为什么在数据源修不够

即使重算数据，任何一次「跌腿样本全部无次日 bar」都会让 `.mean()` 返回 NaN。
**这是结构性的，不是数据事故。** 所以防线必须落在函数自身：
「算不出来」一律表达成 `None`，让消费方明确区分「无数据」与「值为 0」。
"""
import json
import math

import numpy as np
import pandas as pd
import pytest

from aplus.backtest import daily_series


def _results(dates, n_up=3, n_down=3, up_pct=0.01, down_pct=None, market=0.0):
    rows = []
    for d in dates:
        for i in range(n_up):
            rows.append({"pred_date": d, "direction": "up", "code": f"U{i}",
                         "result": "hit", "actual_pct": up_pct, "market_pct": market})
        for i in range(n_down):
            rows.append({"pred_date": d, "direction": "down", "code": f"D{i}",
                         "result": "miss",
                         "actual_pct": (down_pct[i] if down_pct else 0.0),
                         "market_pct": market})
    return pd.DataFrame(rows)


def test_normal_days_have_no_nan():
    out = daily_series(_results(["2026-01-01", "2026-01-02"]))
    assert len(out) == 2
    for r in out:
        for k, v in r.items():
            assert not (isinstance(v, float) and math.isnan(v)), f"{k} 是 NaN"
            assert v is not None


def test_down_leg_all_nan_yields_none_not_nan():
    """核心回归：跌腿有行但全无次日 bar → 必须是 None，不能是 NaN。"""
    df = _results(["2026-01-01"])
    df.loc[df["direction"] == "down", "actual_pct"] = np.nan
    out = daily_series(df)
    assert out[0]["down_excess"] is None, "全 NaN 的均值被当成了数值"
    assert out[0]["breadth"] is None, "单腿缺失时多空价差也必须是 None"


def test_up_leg_all_nan_yields_none_not_nan():
    df = _results(["2026-01-01"])
    df.loc[df["direction"] == "up", "actual_pct"] = np.nan
    out = daily_series(df)
    assert out[0]["up_excess"] is None


def test_missing_down_rows_yields_none():
    df = _results(["2026-01-01"], n_down=0)
    out = daily_series(df)
    assert out[0]["down_excess"] is None
    assert out[0]["breadth"] is None


def test_all_judged_unresolved_yields_none_hit_rate():
    df = _results(["2026-01-01"])
    df["result"] = "pending"
    out = daily_series(df)
    assert out[0]["hit_rate"] is None


def test_output_is_strictly_json_serializable():
    """这就是那条 500 的直接判据：必须能被 allow_nan=False 编码。"""
    df = _results(["2026-01-01", "2026-01-02"])
    df.loc[(df["direction"] == "down") & (df["pred_date"] == "2026-01-02"), "actual_pct"] = np.nan
    out = daily_series(df)
    json.dumps(out, allow_nan=False)   # 不抛即为通过


def test_real_artifact_is_json_safe_after_fix():
    """回归到那份真实文件：10:49 的旧 backtest.parquet 里就有 20 行 NaN。"""
    from pathlib import Path
    p = Path("data/reports/backtest.parquet")
    if not p.exists():
        pytest.skip("无 backtest.parquet 产物")
    out = daily_series(pd.read_parquet(p))
    bad = [i for i, r in enumerate(out)
           if any(isinstance(v, float) and math.isnan(v) for v in r.values())]
    assert not bad, f"{len(bad)} 行仍含 NaN：{bad[:5]}"
    json.dumps(out, allow_nan=False)


def test_zero_return_is_not_confused_with_missing():
    """0.0 是「跌腿恰好持平」，None 是「算不出来」——两者绝不能混。"""
    df = _results(["2026-01-01"], down_pct=[0.0, 0.0, 0.0])
    out = daily_series(df)
    assert out[0]["down_excess"] == 0.0
    assert out[0]["down_excess"] is not None
