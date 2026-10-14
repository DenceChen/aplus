"""源数据 → 统一 schema 的纯转换函数（单位归一在这里完成）。

单位事实（调研报告第二节）：
- baostock：volume 股、amount 元、API 返回全字符串、code 形如 sz.000001
- akshare(东财)：成交量「手」→ ×100 归一为股；成交额 元；code 为裸 6 位数字
- tushare pro：vol 股、amount 千元 → ×1000 归一为元；trade_date 形如 20260105

统一 schema：date(YYYY-MM-DD), code(000001.SZ), open/high/low/close(元),
volume(股), amount(元) + 源附带的元数据列（tradestatus/isST/turn/pctChg）。
"""
import pandas as pd

# 股票代码前缀 → 交易所（用于 akshare 裸代码补全）
_SH_PREFIXES = ("600", "601", "603", "605", "688", "689", "900")
_SZ_PREFIXES = ("000", "001", "002", "003", "300", "301", "200")


def _infer_exchange(symbol: str) -> str:
    if symbol.startswith(_SH_PREFIXES):
        return "SH"
    if symbol.startswith(_SZ_PREFIXES):
        return "SZ"
    return "BJ"  # 43/83/87/92 开头的北交所代码


def to_baostock_code(code: str) -> str:
    symbol, exchange = code.split(".")
    return f"{exchange.lower()}.{symbol}"


def to_akshare_code(code: str) -> str:
    return code.split(".")[0]


def to_tushare_code(code: str) -> str:
    return code


def to_tencent_code(code: str) -> str:
    symbol, exchange = code.split(".")
    return f"{exchange.lower()}{symbol}"


def _from_tencent_code(code: str) -> str:
    exchange, symbol = code[:2], code[2:]
    return f"{symbol}.{exchange.upper()}"


def tencent_frame_to_unified(payload: dict) -> pd.DataFrame:
    """腾讯 ifzq kline JSON → 统一 schema。

    列序为 date, open, close, high, low, volume（注意 close 在 high 前）；
    末行可能携带附加列，取前 6 列。成交量单位：主板/创业板为「手」→×100 归一为股；
    **科创板（688/689）为「股」，不乘**（2026-10-10 回填实证，交叉校验整段隔离过）。
    腾讯 kline 不提供成交额，amount 列不产出（交叉校验自动跳过缺失字段）。
    """
    data = payload.get("data") or {}
    if not data:
        return pd.DataFrame(columns=["date", "code", "open", "high", "low", "close", "volume", "amount"])
    tencent_code = next(iter(data))
    rows = data[tencent_code].get("day") or data[tencent_code].get("qfqday") or []
    code = _from_tencent_code(tencent_code)
    symbol = code.split(".")[0]
    is_kcb = symbol.startswith(("688", "689"))  # 科创板单位是股
    records = [
        {
            "date": str(r[0]),
            "code": code,
            "open": float(r[1]),
            "close": float(r[2]),
            "high": float(r[3]),
            "low": float(r[4]),
            "volume": float(r[5]) if is_kcb else float(r[5]) * 100.0,
        }
        for r in rows
        if len(r) >= 6
    ]
    out = pd.DataFrame.from_records(
        records, columns=["date", "code", "open", "high", "low", "close", "volume"]
    )
    return out[["date", "code", "open", "high", "low", "close", "volume"]]


def _from_baostock_code(code: str) -> str:
    exchange, symbol = code.split(".")
    return f"{symbol}.{exchange.upper()}"


def baostock_frame_to_unified(df: pd.DataFrame) -> pd.DataFrame:
    # 停牌日 volume/amount 为空字符串——语义是「当日无成交」，转 0.0
    volume = df["volume"].replace("", "0")
    amount = df["amount"].replace("", "0")
    out = pd.DataFrame({
        "date": df["date"].astype(str),
        "code": df["code"].astype(str).str.lower().map(_from_baostock_code),
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": volume.astype(float),
        "amount": amount.astype(float),
    })
    for extra in ("tradestatus", "isST", "turn", "pctChg"):
        if extra in df.columns:
            out[extra] = pd.to_numeric(df[extra], errors="coerce")
    return out


def akshare_frame_to_unified(df: pd.DataFrame) -> pd.DataFrame:
    symbols = df["股票代码"].astype(str)
    codes = [f"{s}.{_infer_exchange(s)}" for s in symbols]
    return pd.DataFrame({
        "date": df["日期"].astype(str),
        "code": codes,
        "open": df["开盘"].astype(float),
        "high": df["最高"].astype(float),
        "low": df["最低"].astype(float),
        "close": df["收盘"].astype(float),
        # 东财成交量单位为「手」，归一为股
        "volume": df["成交量"].astype(float) * 100.0,
        "amount": df["成交额"].astype(float),
    })


def tushare_frame_to_unified(df: pd.DataFrame) -> pd.DataFrame:
    dates = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return pd.DataFrame({
        "date": dates,
        "code": df["ts_code"].astype(str),
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": df["vol"].astype(float),
        # tushare pro amount 单位为千元，归一为元
        "amount": df["amount"].astype(float) * 1000.0,
    })
