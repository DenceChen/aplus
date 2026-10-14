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
    """baostock 原始帧 → 统一 schema。

    ★ **停牌日的空串语义**（2026-10-15 实测后写死，别再改回去）：
    baostock 对停牌日返回 `open/high/low/close/volume/amount/turn/pctChg` **全为空串**、
    `tradestatus=0`。**同一个函数里对这个语义的处理曾经自相矛盾** ——
    `volume`/`amount` 走 `.replace("", "0")`，附加列走 `to_numeric(errors="coerce")`，
    唯独 OHLC 四列是裸 `astype(float)`，遇 `''` 直接 `ValueError`。

    后果不是「某天缺个数」，而是一条**自我维持的洞**：
    主源抛异常 → 流水线降级到腾讯（只有 7 列 OHLCV）→ 整份文件只剩 7 列；
    而**下一次重试会撞同一个异常**，所以永远补不回来。
    实测两只：`000043.SZ`(221 行) / `300114.SZ`(640 行)，
    共 **861 行的 `isST` 全程失明** —— ST 过滤对这两只完全无效。

    停牌日的价格取**上一根有效收盘价**，这与库里既有的 1.5 万条停牌行一致
    （实测 400 文件样本：`O=H=L=C=` 前收盘、`volume=amount=0`、`tradestatus=0`）。
    🚨 **不写 0**：0 收盘价会造出 −100% 的假跌幅。
    🚨 **不写 NaN**：`close.pct_change()` 会从该行起整段断链。
    ⚠️ 首根就停牌时没有「上一根」，此时留 NaN —— **不编值**（baostock 未请求 preclose）。
    """
    # 停牌日 volume/amount 为空字符串——语义是「当日无成交」，转 0.0
    volume = df["volume"].replace("", "0")
    amount = df["amount"].replace("", "0")

    ohlc = pd.DataFrame({
        name: pd.to_numeric(df[name], errors="coerce")
        for name in ("open", "high", "low", "close")
    })
    # 任一价缺失 ⇒ 判为停牌行，四个价统一取上一根有效收盘价
    halted = ohlc.isna().any(axis=1)
    carried = ohlc["close"].ffill()
    for name in ohlc.columns:
        ohlc[name] = ohlc[name].where(~halted, carried)

    out = pd.DataFrame({
        "date": df["date"].astype(str),
        "code": df["code"].astype(str).str.lower().map(_from_baostock_code),
        "open": ohlc["open"],
        "high": ohlc["high"],
        "low": ohlc["low"],
        "close": ohlc["close"],
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
