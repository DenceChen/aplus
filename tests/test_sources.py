"""源数据转换层测试：TDD RED 先行。

纯函数测试（不打网络）：code 转换、列映射、单位归一。
单位事实（调研报告第二节）：
- baostock：volume 股、amount 元，API 返回全是字符串
- akshare(东财)：成交量「手」→×100 归一为股；成交额 元
- tushare pro：vol 股、amount 千元 →×1000 归一为元；trade_date 是 YYYYMMDD
"""
import pandas as pd
import pytest

from aplus.sources.convert import (
    akshare_frame_to_unified,
    baostock_frame_to_unified,
    to_akshare_code,
    to_baostock_code,
    to_tencent_code,
    to_tushare_code,
    tencent_frame_to_unified,
    tushare_frame_to_unified,
)


# ---------- code 转换 ----------

def test_code_to_baostock_format():
    assert to_baostock_code("000001.SZ") == "sz.000001"
    assert to_baostock_code("600000.SH") == "sh.600000"


def test_code_to_akshare_format():
    assert to_akshare_code("000001.SZ") == "000001"
    assert to_akshare_code("600000.SH") == "600000"


def test_code_to_tushare_format_is_identity():
    assert to_tushare_code("000001.SZ") == "000001.SZ"


# ---------- baostock 帧转换 ----------

def baostock_daily_frame():
    """baostock query_history_k_data_plus 返回的全字符串帧（adjustflag=3 不复权）。"""
    return pd.DataFrame({
        "date": ["2026-01-05", "2026-01-06"],
        "code": ["sz.000001", "sz.000001"],
        "open": ["10.500000", "10.500000"],
        "high": ["10.800000", "11.000000"],
        "low": ["9.900000", "10.200000"],
        "close": ["10.500000", "10.800000"],
        "volume": ["1000000", "800000"],       # 股
        "amount": ["10500000.000000", "8640000.000000"],  # 元
        "tradestatus": ["1", "1"],
        "isST": ["0", "0"],
    })


def test_baostock_frame_converts_types_and_code():
    uni = baostock_frame_to_unified(baostock_daily_frame())
    assert uni["code"].unique().tolist() == ["000001.SZ"]
    assert uni["volume"].dtype.kind == "f"
    assert uni["close"].tolist() == [10.5, 10.8]
    assert uni["date"].tolist() == ["2026-01-05", "2026-01-06"]
    # 主源元数据列保留
    assert uni["tradestatus"].tolist() == [1, 1]
    assert uni["isST"].tolist() == [0, 0]


def test_baostock_suspended_row_empty_volume_becomes_zero():
    """真实场景回归（600009 @2022-04-08）：停牌日 baostock 输出 OHLC 冻结价 +
    volume/amount 空字符串——应转为 0.0（当日无成交），而非崩溃。"""
    df = baostock_daily_frame()
    df.loc[1, "volume"] = ""
    df.loc[1, "amount"] = ""
    df.loc[1, "tradestatus"] = "0"
    uni = baostock_frame_to_unified(df)
    assert uni.loc[1, "volume"] == 0.0
    assert uni.loc[1, "amount"] == 0.0
    assert uni.loc[1, "close"] == 10.8  # OHLC 不受影响


# ---------- akshare 帧转换 ----------

def akshare_daily_frame():
    """akshare stock_zh_a_hist(adjust="") 返回的中文列名帧，成交量单位为手。"""
    return pd.DataFrame({
        "日期": ["2026-01-05", "2026-01-06"],
        "股票代码": ["000001", "000001"],
        "开盘": [10.5, 10.5],
        "收盘": [10.5, 10.8],
        "最高": [10.8, 11.0],
        "最低": [9.9, 10.2],
        "成交量": [10000.0, 8000.0],      # 手
        "成交额": [10500000.0, 8640000.0],  # 元
        "换手率": [0.5, 0.4],
    })


def test_akshare_frame_maps_columns_and_converts_lots_to_shares():
    uni = akshare_frame_to_unified(akshare_daily_frame())
    assert uni["code"].unique().tolist() == ["000001.SZ"]
    # 手 → 股：10000 手 = 1,000,000 股
    assert uni["volume"].tolist() == [1_000_000.0, 800_000.0]
    assert uni["close"].tolist() == [10.5, 10.8]
    assert uni["amount"].tolist() == [10_500_000.0, 8_640_000.0]


def test_akshare_infers_exchange_from_symbol_prefix():
    """akshare 帧只有裸代码，按前缀推断交易所：600xxx → SH。"""
    df = akshare_daily_frame()
    df["股票代码"] = ["600000", "600000"]
    uni = akshare_frame_to_unified(df)
    assert uni["code"].unique().tolist() == ["600000.SH"]


# ---------- tushare 帧转换 ----------

def tushare_daily_frame():
    """tushare pro daily() 返回帧：vol 股、amount 千元、trade_date YYYYMMDD。"""
    return pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "trade_date": ["20260105", "20260106"],
        "open": [10.5, 10.5],
        "high": [10.8, 11.0],
        "low": [9.9, 10.2],
        "close": [10.5, 10.8],
        "vol": [1_000_000.0, 800_000.0],        # 股
        "amount": [10500.0, 8640.0],             # 千元
    })


def test_tushare_frame_converts_date_and_thousands_to_yuan():
    uni = tushare_frame_to_unified(tushare_daily_frame())
    assert uni["date"].tolist() == ["2026-01-05", "2026-01-06"]
    # 千元 → 元
    assert uni["amount"].tolist() == [10_500_000.0, 8_640_000.0]
    assert uni["volume"].tolist() == [1_000_000.0, 800_000.0]


# ---------- 腾讯 kline 帧转换 ----------

def tencent_kline_payload():
    """腾讯 ifzq kline JSON：数组列序为 date, open, close, high, low, volume；
    末行可能携带附加列（如成交额），解析取前 6 列。volume 单位为手。"""
    return {
        "code": 0,
        "msg": "",
        "data": {
            "sz000001": {
                "day": [
                    ["2026-01-05", "10.500", "10.500", "10.800", "9.900", "10000.000"],
                    ["2026-01-06", "10.500", "10.800", "11.000", "10.200", "8000.000", "86400000.000"],
                ]
            }
        },
    }


def test_code_to_tencent_format():
    assert to_tencent_code("000001.SZ") == "sz000001"
    assert to_tencent_code("600000.SH") == "sh600000"


def test_tencent_frame_maps_columns_in_tencent_order():
    """腾讯列序是 open, close, high, low（close 在 high 前）——最容易踩的映射坑。"""
    uni = tencent_frame_to_unified(tencent_kline_payload())
    assert uni["code"].unique().tolist() == ["000001.SZ"]
    assert uni["open"].tolist() == [10.5, 10.5]
    assert uni["close"].tolist() == [10.5, 10.8]
    assert uni["high"].tolist() == [10.8, 11.0]
    assert uni["low"].tolist() == [9.9, 10.2]
    # 手 → 股
    assert uni["volume"].tolist() == [1_000_000.0, 800_000.0]
    assert uni["date"].tolist() == ["2026-01-05", "2026-01-06"]


def test_tencent_frame_tolerates_extra_columns():
    """末行附加列（成交额等）被忽略，不炸解析。"""
    uni = tencent_frame_to_unified(tencent_kline_payload())
    assert len(uni) == 2  # 两行都解析成功，附加列不影响


def test_tencent_payload_uses_first_stock_key_and_day_rows():
    """返回结构以股票代码为键、行情在 day（或 fq 端点的 qfqday）里。"""
    payload = tencent_kline_payload()
    payload["data"]["sz000001"] = {"qfqday": payload["data"]["sz000001"].pop("day")}
    uni = tencent_frame_to_unified(payload)
    assert len(uni) == 2


def test_tencent_kcb_volume_is_shares_not_lots():
    """真实回归（2026-10-10 回填实证）：科创板 688xxx 在腾讯源成交量单位是「股」，
    不做 ×100 归一——交叉校验曾把整段科创板 100% 隔离（volume mismatch 640/640）。"""
    payload = tencent_kline_payload()
    payload["data"]["sh688288"] = payload["data"].pop("sz000001")
    uni = tencent_frame_to_unified(payload)
    assert uni["code"].unique().tolist() == ["688288.SH"]
    assert uni["volume"].tolist() == [10_000.0, 8_000.0]  # 股，原样


# ---------- 三源汇合后的可比性（单位归一的最终验收） ----------

def test_all_three_sources_produce_comparable_unified_frames():
    """同一交易日的同一只股票，三源归一后 OHLCV+amount 应完全可比。"""
    a = baostock_frame_to_unified(baostock_daily_frame())
    b = akshare_frame_to_unified(akshare_daily_frame())
    c = tushare_frame_to_unified(tushare_daily_frame())
    cols = ["date", "code", "open", "high", "low", "close", "volume", "amount"]
    pd.testing.assert_frame_equal(a[cols], b[cols])
    pd.testing.assert_frame_equal(a[cols], c[cols])


# ---------- 复权因子：源站坏行防护（2026-10-11 生产事故修复） ----------
#
# 生产事故：baostock 的 adjustFactor 列偶发垃圾值。实测 sh.601607 的 2026-07-17
# 行：fore=0.993812 / back=10.037703（正确）/ adjust=**1.020971**（垃圾，差一个
# 数量级）。原实现读 adjustFactor，attach_factor 又向前填充 → 该因子污染了
# 2026-07-17~2026-09-17 近两个月，20 日动量被放大到 **+903%**，该股直接霸榜
# 涨名单 Top1 并进入配置方案。原始行情完全正常（真实 mom20 = +1.46%）——
# 纯粹是因子表污染，且不崩、不报错、不可见。

def _fake_bs(monkeypatch, rows):
    """假 baostock 模块：query_adjust_factor 返回给定原始行。"""
    class _RS:
        error_code, error_msg, fields = "0", "", [
            "code", "dividOperateDate", "foreAdjustFactor",
            "backAdjustFactor", "adjustFactor"]

        def __init__(self, data):
            self._d = list(data)
            self._i = -1

        def next(self):
            self._i += 1
            return self._i < len(self._d)

        def get_row_data(self):
            return self._d[self._i]

    class _Fake:
        @staticmethod
        def login():
            class _R:
                error_code, error_msg = "0", "success"
            return _R()

        @staticmethod
        def logout():
            pass

        @staticmethod
        def query_adjust_factor(**kw):
            return _RS(list(rows))

    monkeypatch.setattr("aplus.sources.baostock_source.bs", _Fake)


def test_fetch_adjust_factor_uses_back_adjust_not_adjust_column(monkeypatch):
    """必须取 backAdjustFactor（后复权 = 不复权价 × 累计因子，与 to_hfq 语义一致）。
    adjustFactor 列是源站的冗余列，实测会偶发低一个数量级的垃圾值。"""
    from aplus.sources.baostock_source import BaostockSource

    _fake_bs(monkeypatch, [["sh.601607", "2026-07-17", "0.993812", "10.037703", "1.020971"]])
    df = BaostockSource().fetch_adjust_factor("601607.SH", "2020-01-01", "2030-01-01")
    assert float(df["adjust_factor"].iloc[0]) == pytest.approx(10.037703)


def test_fetch_adjust_factor_enforces_monotonicity(monkeypatch):
    """后复权因子必须单调不减——这是下游 hfq 价格连续性的硬不变量。

    源站偶发倒退伪行（实测 sz.000001 2020-12-31 的 back=99.79 低于前值 119.96，
    而其 fore 与前一行完全相同 → 根本不是真实除权日）。倒退值一律压回前值
    （cummax 语义），保证「只增不减」——否则会在 hfq 价上造出假跳空。
    """
    from aplus.sources.baostock_source import BaostockSource

    _fake_bs(monkeypatch, [
        ["sz.000001", "2020-05-28", "0.781647", "119.960317", "119.960317"],
        ["sz.000001", "2020-12-31", "0.781647", "99.787353", "99.787353"],   # 倒退伪行
        ["sz.000001", "2021-05-14", "0.787793", "100.572054", "100.572054"],
    ])
    df = BaostockSource().fetch_adjust_factor("000001.SZ", "2020-01-01", "2030-01-01")
    assert df["adjust_factor"].is_monotonic_increasing
    assert df["adjust_factor"].min() >= 99.0     # 垃圾值 1.0 绝不会进来
    assert df["adjust_factor"].iloc[-1] == pytest.approx(119.960317)


def test_fetch_adjust_factor_keeps_flat_and_first_event(monkeypatch):
    """首行与相等值不算倒退，必须保留（无除权期间因子本就不变）。"""
    from aplus.sources.baostock_source import BaostockSource

    _fake_bs(monkeypatch, [
        ["sz.000002", "2019-01-02", "0.5", "10.0", "10.0"],
        ["sz.000002", "2020-01-02", "0.5", "10.0", "10.0"],
        ["sz.000002", "2021-01-04", "0.6", "12.0", "12.0"],
    ])
    df = BaostockSource().fetch_adjust_factor("000002.SZ", "2020-01-01", "2030-01-01")
    assert df["adjust_factor"].tolist() == [10.0, 10.0, 12.0]


def test_fetch_adjust_factor_never_yields_garbage_magnitude(monkeypatch):
    """即使源站 adjust 列全是 1.0 这类垃圾，取 back 列也必须拿到真值。"""
    from aplus.sources.baostock_source import BaostockSource

    _fake_bs(monkeypatch, [
        ["sz.000003", "2020-01-02", "0.5", "99.0", "1.0"],
        ["sz.000003", "2021-01-04", "0.5", "98.0", "1.0"],
    ])
    df = BaostockSource().fetch_adjust_factor("000003.SZ", "2020-01-01", "2030-01-01")
    assert (df["adjust_factor"] > 50).all()
    assert df["adjust_factor"].is_monotonic_increasing
