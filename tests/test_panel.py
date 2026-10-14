"""预计算面板的因子健全性（TDD）。

## 这条测试在钉什么

`vsur`（量能比）= 当日成交量 / 前 20 日均量。停牌股连续 20 日成交量为 0 时，
分母 `prior_mean` 就是 **0**，`volume / 0` 得到 **inf**。

实测（2026-10-11）：**抽样 102 只股票里有 6 只的 vsur 出现 inf**，其中
`000029.SZ` 有 429 天基准量为 0（长期停牌）。这不是边角料，是约 6% 的样本。

## 为什么必须挡

inf 一路进加权合成分，`cross_section_z` 里的 `scores.std()` 遇到 inf 就算出
inf/nan —— **那一天该因子的整个横截面塌成 0**，排序信号凭空消失，而且是静默的：
不崩、不报错，只是那一列从此没有区分度。

诚实的表示是 NaN 而不是 inf：**除以 0 的比值是「无定义」，不是「无穷大」**。
NaN 会被下游 `notna` 正常剔除；inf 不会。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.panel import FACTOR_COLUMNS, _factor_series


def _frame(volumes, closes=None):
    n = len(volumes)
    closes = closes if closes is not None else [10.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame({"date": [f"d{i:04d}" for i in range(n)],
                         "close": closes, "volume": volumes,
                         "adjust_factor": 1.0})


def test_vsur_never_infinite_when_baseline_is_zero():
    """核心回归：20 日零成交（停牌）不得产出 inf。"""
    volumes = [0.0] * 40 + [1000.0] * 10
    out = _factor_series(_frame(volumes))
    vsur = out["vsur"].to_numpy(dtype=float)
    assert not np.isinf(vsur).any(), f"停牌导致 vsur=inf，{int(np.isinf(vsur).sum())} 个"


def test_vsur_baseline_zero_becomes_nan_not_inf():
    """分母为 0 时应是 NaN（无定义），NaN 会被下游正常剔除，inf 不会。"""
    volumes = [0.0] * 30 + [500.0] * 5
    out = _factor_series(_frame(volumes))
    vsur = out["vsur"].to_numpy(dtype=float)
    zero_baseline = out["vsur"].iloc[21:30].to_numpy(dtype=float)
    assert np.isnan(zero_baseline).all(), "分母为 0 的位置应当是 NaN"


def test_vsur_unchanged_when_baseline_positive():
    """修复不能动正常路径：分母为正时数值必须与修复前一致。"""
    volumes = [1000.0 + i * 7 for i in range(60)]
    out = _factor_series(_frame(volumes))
    vsur = out["vsur"].to_numpy(dtype=float)
    finite = vsur[np.isfinite(vsur)]
    assert len(finite) > 30
    # 手工复核一个点：volume[i] / mean(volume[i-20:i])
    i = 50
    expected = volumes[i] / (sum(volumes[i - 20:i]) / 20.0)
    assert vsur[i] == pytest.approx(expected, rel=1e-12)


def test_no_factor_column_contains_inf_for_realistic_edge_cases():
    """把几种真实脏数据一起喂进去，五个因子列都不许出 inf。"""
    cases = {
        "全零成交": [0.0] * 50,
        "长期停牌后放量": [0.0] * 45 + [1e6] * 10,     # 55 根
        "零收盘价": [1000.0] * 50,
        "交替零量": [0.0 if i % 2 else 1000.0 for i in range(50)],
    }
    for name, vol in cases.items():
        closes = [10.0] * len(vol)                   # 长度必须跟 K 线数一致
        if name == "零收盘价":
            closes[25] = 0.0
        out = _factor_series(_frame(vol, closes))
        for col in FACTOR_COLUMNS:
            if col not in out.columns:
                continue
            arr = out[col].to_numpy(dtype=float)
            assert not np.isinf(arr).any(), f"{name}/{col} 出现 inf"