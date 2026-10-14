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


# ══════════════════════════════════════════════════════════════════
# baostock 停牌日的空串语义（2026-10-15）
# ══════════════════════════════════════════════════════════════════

def _halted_frame(**overrides):
    """按 **baostock 的真实返回**构造：停牌行 OHLC 全为空串、`tradestatus=0`。

    样本取自 000043.SZ 的实测原始行（2019-12-16），不是编的。
    """
    import pandas as pd

    rows = [
        ["2019-12-12", "sz.000043", "20.2000", "20.2400", "19.3300", "19.8600",
         "5856617", "116232118.5700", "1", "0", "0.8783", "-0.7992"],
        ["2019-12-13", "sz.000043", "19.9100", "20.4900", "19.7100", "20.2500",
         "7096940", "142983766.5000", "1", "0", "1.0643", "1.9637"],
        # ↓ 停牌日：OHLC/volume/amount/turn/pctChg **全空串**，只有 tradestatus=0
        ["2019-12-16", "sz.000043", "", "", "", "", "", "", "0", "0", "", ""],
    ]
    cols = ["date", "code", "open", "high", "low", "close",
            "volume", "amount", "tradestatus", "isST", "turn", "pctChg"]
    df = pd.DataFrame(rows, columns=cols)
    for k, v in overrides.items():
        df[k] = v
    return df


def test_a_halted_row_no_longer_crashes_the_baostock_converter():
    """★ 停牌日的空串曾让转换层**抛异常**，进而把整份文件打成 7 列。

    机制是一条**自我维持的洞**：
        主源 `astype(float)` 遇 `''` 抛 `ValueError`
        → 流水线降级到腾讯（只有 7 列 OHLCV）
        → 文件只剩 7 列，**下次重试撞同一个异常**，永远补不回来。
    实测受害者：`000043.SZ`(221 行) / `300114.SZ`(640 行)，
    共 **861 行的 `isST` 全程失明**。

    🚨 这条以前根本走不到 —— 调用方先崩了。守卫的第一职责是「不许崩」。
    """
    from aplus.sources.convert import baostock_frame_to_unified

    out = baostock_frame_to_unified(_halted_frame())      # 崩在这里就是红
    assert len(out) == 3
    assert list(out.columns)[:12] == [
        "date", "code", "open", "high", "low", "close",
        "volume", "amount", "tradestatus", "isST", "turn", "pctChg",
    ], "转换后缺列 —— 这正是降级路径留下的 7 列形态"


def test_a_halted_row_carries_the_last_close_rather_than_zero_or_nan():
    """★ 停牌日的价格取**上一根有效收盘价**，与库里既有的停牌行同构。

    🚨 **不写 0**：0 收盘价会造出 −100% 的假跌幅。
    🚨 **不写 NaN**：`close.pct_change()` 会从该行起整段断链。
    实测库里的约定（400 文件样本）：`O=H=L=C=` 前收盘、`volume=amount=0`。
    """
    from aplus.sources.convert import baostock_frame_to_unified

    out = baostock_frame_to_unified(_halted_frame())
    h = out.iloc[-1]
    assert h["close"] == 20.25, f"停牌行收盘价应结转上一根 20.25，实测 {h['close']}"
    for col in ("open", "high", "low"):
        assert h[col] == 20.25, f"停牌行 {col} 应等于结转价 20.25，实测 {h[col]}"
    assert h["volume"] == 0.0 and h["amount"] == 0.0, "停牌日成交必须为 0"
    assert h["tradestatus"] == 0

    # ★ 收益率链不许断：停牌日的 pct_change 必须是 0，不是 NaN
    chg = out["close"].pct_change()
    assert chg.iloc[-1] == 0.0, (
        f"停牌日收益率应恰好为 0（价格没动），实测 {chg.iloc[-1]} —— "
        "若是 NaN，说明 pct_change 链被切断了"
    )


def test_the_halt_convention_matches_what_the_real_store_already_holds():
    """★ 转换层产出的停牌行形态，必须与**真实库**里已有的停牌行一致。

    这是本条的判别式：不是「我以为该是结转」，而是
    「库里 1.5 万条既有停牌行就是这么存的，转换层必须与它们同构」。
    约定写错 ⇒ 新写入的数据与历史数据对不上，而这种不一致极难被发现。
    """
    import pathlib

    import pandas as pd

    from aplus.sources.convert import baostock_frame_to_unified

    out = baostock_frame_to_unified(_halted_frame())
    mine = out[out["tradestatus"] == 0].iloc[0]

    root = pathlib.Path(__file__).resolve().parents[1] / "data" / "master" / "daily"
    checked = 0
    for p in sorted(root.glob("*.parquet"))[:400]:
        df = pd.read_parquet(p)
        if "tradestatus" not in df.columns:
            continue
        z = df[(df["tradestatus"] == 0) & (df["volume"] == 0)]
        if not len(z):
            continue
        row = z.iloc[0]
        # 库里的停牌行：四个价相等且非 0，成交为 0
        assert row["open"] == row["high"] == row["low"] == row["close"], (
            f"{p.stem} 的停牌行四个价不相等：{row[['open','high','low','close']].to_dict()}"
        )
        assert row["close"] > 0, f"{p.stem} 的停牌行收盘价为 {row['close']}（不该是 0）"
        assert mine["open"] == mine["high"] == mine["low"] == mine["close"], (
            "转换层产出的停牌行四个价不相等，与库里的既有形态不同构"
        )
        checked += 1
        if checked >= 20:
            break
    assert checked >= 5, f"只核到 {checked} 条真实停牌行，样本不足，结论不可信"


def test_a_leading_halted_row_stays_nan_instead_of_inventing_a_price():
    """★ 首根就停牌 ⇒ 没有「上一根」⇒ **留 NaN，不编值**。

    为什么不拿 `preclose` 兜底：baostock 的 `_DAILY_FIELDS` 没有请求这一列，
    要用就得改字段列表（会牵动全链路）。**宁可空着，也不造一个没验证过的价。**
    """
    import pandas as pd

    from aplus.sources.convert import baostock_frame_to_unified

    df = _halted_frame()
    df = pd.concat([df.iloc[[2]], df.iloc[[0, 1]]], ignore_index=True)  # 停牌行提到最前
    out = baostock_frame_to_unified(df)

    assert pd.isna(out.iloc[0]["close"]), (
        f"首根停牌的收盘价应为 NaN（不编值），实测 {out.iloc[0]['close']}"
    )
    # 但停牌日的成交仍必须是 0 —— 这个有源可依
    assert out.iloc[0]["volume"] == 0.0


def test_the_old_astype_float_path_really_did_crash_on_this_input():
    """★ **守卫只有变异后算数**（本仓铁律）—— 证明缺陷是**真的**，不是想象。

    🚨 这里刻意**不走「改被测函数再恢复」那条路**：
    那是第 14 次同族错误的温床（探针打在自己复制的代码上）。
    本条的判别式更直接 —— **同一份输入，旧写法崩、新写法不崩**。
    崩了，说明 `ValueError` 是真的；不崩，说明守卫挂在一条真实的数据通路上。
    """
    import pandas as pd

    df = _halted_frame()

    # 旧写法：OHLC 用裸 astype(float)
    with pytest.raises(ValueError, match="could not convert string to float"):
        _ = df["open"].astype(float)

    # 新写法：同一列，errors="coerce"
    assert pd.to_numeric(df["open"], errors="coerce").isna().sum() == 1, (
        "新写法没能把空串转成 NaN —— 那它同样过不了下一道"
    )

    # ★ 而且新写法必须真的让整条转换跑通（守卫不是在验一个无关的等价物）
    from aplus.sources.convert import baostock_frame_to_unified
    assert len(baostock_frame_to_unified(df)) == len(df)
