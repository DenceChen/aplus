"""存储层测试：TDD RED 先行。

设计依据：不复权 OHLCV 落 parquet，按 code 分文件；upsert 按 date 去重（后写覆盖先写）；
因子表独立存储。
"""
import pandas as pd

from aplus.store import LocalStore


def _daily(rows=None):
    rows = rows or [
        {"date": "2026-01-05", "open": 10.0, "high": 10.8, "low": 9.9, "close": 10.5, "volume": 1e6, "amount": 1.05e7},
        {"date": "2026-01-06", "open": 10.5, "high": 11.0, "low": 10.2, "close": 10.8, "volume": 8e5, "amount": 8.64e6},
    ]
    return pd.DataFrame(rows)


def test_roundtrip(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_daily("000001.SZ", _daily())
    back = store.read_daily("000001.SZ")
    pd.testing.assert_frame_equal(back, _daily())


def test_upsert_dedups_by_date(tmp_path):
    store = LocalStore(tmp_path)
    store.upsert_daily("000001.SZ", _daily())
    # 重取数据后修正了 01-06 的收盘价
    corrected = _daily()
    corrected.loc[1, "close"] = 10.9
    store.upsert_daily("000001.SZ", corrected)
    back = store.read_daily("000001.SZ")
    assert len(back) == 2
    assert back.loc[back["date"] == "2026-01-06", "close"].iloc[0] == 10.9


def test_read_missing_returns_none(tmp_path):
    assert LocalStore(tmp_path).read_daily("999999.SZ") is None


def test_factor_roundtrip(tmp_path):
    store = LocalStore(tmp_path)
    factors = pd.DataFrame({"date": ["2026-01-05", "2026-01-06"], "adjust_factor": [1.0, 1.25]})
    store.upsert_factor("000001.SZ", factors)
    back = store.read_factor("000001.SZ")
    assert back["adjust_factor"].tolist() == [1.0, 1.25]


def test_quarantine_writes_outside_master(tmp_path):
    store = LocalStore(tmp_path)
    store.quarantine_daily("000001.SZ", _daily(), reason="cross-check mismatch 50%")
    assert store.read_daily("000001.SZ") is None  # 主库不受污染
    quarantined = store.read_quarantine_daily("000001.SZ")
    assert quarantined is not None and len(quarantined) == 2


def test_upsert_daily_clears_stale_quarantine(tmp_path):
    """真实场景回归：修复单位 bug 后重跑，股票成功入库——隔离区旧文件必须清除，
    否则同一股票既在主库又在隔离区，误导排查。"""
    store = LocalStore(tmp_path)
    store.quarantine_daily("688288.SH", _daily(), reason="volume unit bug")
    store.upsert_daily("688288.SH", _daily())  # 重跑成功
    assert store.read_daily("688288.SH") is not None
    assert store.read_quarantine_daily("688288.SH") is None


def test_upsert_refuses_empty_frame(tmp_path):
    """真实回归（2026-10-10 update 崩溃根因）：双源都返回空帧被当成成功，
    写出空 parquet → date.max()=NaN → NaN>=str TypeError。空帧必须拒写。"""
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _daily())
    store.upsert_daily("600000.SH", _daily().iloc[0:0])  # 空帧
    assert len(store.read_daily("600000.SH")) == 2  # 原数据不受影响，且未新增空文件
    store.upsert_daily("600099.SH", _daily().iloc[0:0])  # 对新代码也是拒写
    assert store.read_daily("600099.SH") is None


def test_upsert_coerces_dates_to_str(tmp_path):
    """防御：混入 datetime 类型的 date 列在存储边界统一为 str，杜绝类型混杂。"""
    df = _daily()
    df["date"] = pd.to_datetime(df["date"])
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", df)
    back = store.read_daily("600000.SH")
    assert back["date"].tolist() == ["2026-01-05", "2026-01-06"]
