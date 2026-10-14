"""「ST 状态未知」必须看得见，而不是被折成「不是 ST」。

## 缺陷（2026-10-12）

`panel.py` 与 `snapshot.py` 各有一处把缺失的 `isST` 折成 `0`，
而 `0` 的语义是「**不是** ST 股」：

```python
"isST": np.where(np.isnan(vals[...]), 0, vals[...])   # panel.py
isst = last.get("isST", 0)                             # snapshot.py
```

**缺数据 = 声称自己正常。** 过滤器在最需要它的时候最不设防。
实测 2026-10-11 的 20 只推荐里有 6 只（30%）末根 `isST` 为空，
其中 5 只在跌篮子。

## 本次只做「可见」，不做「fail-closed」

`filter_universe` 保持原样：`isST != 1` 即放行，NaN 不被剔除。
理由是它 docstring 里记着一条实测——要求 `isST == 0` 才保留会砍掉
88% 的正常股票。在数据还有洞的当下改成 fail-closed，等于让平台不可用。

**正确的组合是：先让洞看得见 → 补洞 → 数据干净后 fail-closed 才有意义。**
顺序反了就是拿可用性换安全感，而洞还在原地。

所以本文件钉死的是「未知要被数出来并上报」，不是「未知要被剔除」。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.predict import filter_universe, is_st_name  # noqa: E402


def make_rows(n=6, **over):
    base = {
        "code": [f"{i:06d}.SZ" for i in range(n)],
        "name": [f"公司{i}" for i in range(n)],
        "isST": [0.0] * n,
        "volume": [1_000_000.0] * n,
        "listed_days": [200] * n,
        "last_date": ["2026-10-09"] * n,
        "buyable": [True] * n,
    }
    base.update(over)
    return pd.DataFrame(base)


def test_unknown_st_is_counted_not_hidden():
    """ST 状态未知的票要被数出来——剔除与否是另一回事，但不能悄无声息。"""
    df = make_rows()
    df.loc[0, "isST"] = np.nan
    df.loc[1, "isST"] = np.nan
    unknown = {}
    out = filter_universe(df, as_of="2026-10-09", unknown_st=unknown)
    assert unknown == {"000000.SZ": 1, "000001.SZ": 1}, \
        f"未知 ST 状态必须按日计数上报，实际 {unknown}"
    assert len(out) == n_passed(df), "计数不得改变过滤行为"


def n_passed(df):
    return len(df)


def test_known_non_st_is_not_counted():
    df = make_rows()
    unknown = {}
    filter_universe(df, as_of="2026-10-09", unknown_st=unknown)
    assert unknown == {}, "isST 明确的股票不该被算成未知"


def test_st_stocks_are_still_excluded():
    df = make_rows()
    df.loc[0, "isST"] = 1.0
    unknown = {}
    out = filter_universe(df, as_of="2026-10-09", unknown_st=unknown)
    assert "000000.SZ" not in set(out["code"])
    assert unknown == {}, "已知的 ST 不是「未知」，不该混进未知的计数"


def test_st_named_stocks_still_excluded():
    """名称信号独立生效——isST 缺失时它兜底。"""
    df = make_rows()
    df.loc[0, "isST"] = np.nan
    df.loc[0, "name"] = "*ST宏图"
    out = filter_universe(df, as_of="2026-10-09")
    assert "000000.SZ" not in set(out["code"]), "名称带 ST 的必须剔掉"
    assert is_st_name("*ST宏图") is True


def test_unknown_counter_is_optional():
    """不传计数器不许炸——慢路径的调用方还没接。"""
    df = make_rows()
    df.loc[0, "isST"] = np.nan
    out = filter_universe(df, as_of="2026-10-09")
    assert len(out) == len(df)


def test_unknown_counter_accumulates_across_days():
    """同一只票连着多天未知，计数要累加——这正是「按日计数」的意义。"""
    df = make_rows()
    df.loc[0, "isST"] = np.nan
    unknown = {}
    filter_universe(df, as_of="2026-10-09", unknown_st=unknown)
    filter_universe(df, as_of="2026-10-12", unknown_st=unknown)
    assert unknown["000000.SZ"] == 2


# --- 两处「未知折成 0」都要拆掉 ---------------------------------------------


def test_snapshot_keeps_unknown_isst_as_nan():
    """慢路径 `snapshot.cross_rows_from_map` 也不许把缺失折成 0。"""
    from aplus import snapshot
    from aplus.snapshot import FACTOR_WINDOW_MIN

    # 行数必须够因子窗口，否则被 min_history 滤空——空表断言不出任何东西
    n = FACTOR_WINDOW_MIN + 10
    dates = pd.bdate_range("2026-06-01", periods=n)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "code": "600000.SH",
        "open": np.linspace(10.0, 12.0, n), "high": np.linspace(10.2, 12.3, n),
        "low": np.linspace(9.9, 11.8, n), "close": np.linspace(10.0, 12.0, n),
        "volume": np.linspace(1e6, 1.2e6, n),
    })   # 没有 isST 列
    out = snapshot.cross_rows_from_map({"600000.SH": df})
    assert len(out) == 1, f"帧被滤空了（{len(out)} 行），断言就成空转"
    assert np.isnan(float(out.iloc[0]["isST"])), \
        "缺失的 isST 必须是 NaN（未知），不是 0（不是 ST）"


def test_panel_keeps_unknown_isst_as_nan():
    """快路径 `panel` 同样不许折成 0。"""
    from aplus.panel import FactorPanel

    n = 45
    dates = pd.bdate_range("2026-08-01", periods=n)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "code": "600000.SH",
        "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0,
        "volume": 1e6, "amount": 1e7,
        "tradestatus": 1,
    })   # 没有 isST 列
    panel = FactorPanel({"600000.SH": df}, None, list(dates.strftime("%Y-%m-%d")))
    cs = panel.cross_section(n - 1, min_history=1)
    assert len(cs) == 1
    assert np.isnan(float(cs["isST"].iloc[0])), \
        "缺失的 isST 必须是 NaN（未知），不是 0（不是 ST）"


def test_panel_keeps_known_isst():
    """已知值不许被改动——0 还是 0，1 还是 1。"""
    from aplus.panel import FactorPanel

    n = 45
    dates = pd.bdate_range("2026-08-01", periods=n)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "code": "600000.SH",
        "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0,
        "volume": 1e6, "amount": 1e7,
        "tradestatus": 1, "isST": 1.0,
    })
    panel = FactorPanel({"600000.SH": df}, None, list(dates.strftime("%Y-%m-%d")))
    cs = panel.cross_section(n - 1, min_history=1)
    assert float(cs["isST"].iloc[0]) == 1.0
