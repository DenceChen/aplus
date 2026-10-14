"""可转债源数据 → 统一 schema 的纯转换函数（路线 A 第一阶段，PLATFORM.md §7.5）。

与股票侧 convert.py 的同构关系与差异：
- 代码风格同股票：统一 123089.SZ ↔ 腾讯 sz123089
- kline 列序同股票：date, open, close, high, low, volume（close 在 high 前）
- **无复权**：转债不存在分红送转的复权问题（强赎/转股是价格事件，非权益事件）
- 无涨跌停常规限制（沪市有 20%/30% 临停规则，本层不处理）
- **成交量单位按「张」处理**：原样透传，不做股票侧 ×100 归一——
  待双源交叉验证后钉死（腾讯转债 kline 的张/手归属尚未第二源证实）
"""
from datetime import date, datetime

import pandas as pd

_BOND_COLUMNS = ["date", "code", "open", "high", "low", "close", "volume"]


def to_bond_tencent_code(unified: str) -> str:
    """统一风格 123089.SZ / 113056.SH → 腾讯 sz123089 / sh113056。"""
    try:
        symbol, exchange = unified.split(".")
    except ValueError as exc:
        raise ValueError(f"统一代码须为 123089.SZ 风格，得到: {unified!r}") from exc
    if len(symbol) != 6 or not symbol.isdigit() or exchange.upper() not in ("SH", "SZ"):
        raise ValueError(f"统一代码须为 123089.SZ 风格，得到: {unified!r}")
    return f"{exchange.lower()}{symbol}"


def _from_bond_tencent_code(code: str) -> str:
    exchange, symbol = code[:2], code[2:]
    return f"{symbol}.{exchange.upper()}"


def bond_frame_to_unified(payload: dict) -> pd.DataFrame:
    """腾讯转债 kline JSON → 统一 schema。

    数组列序与股票相同：date, open, close, high, low, volume（close 在 high 前），
    末行可能携带附加列（成交额），取前 6 列；响应内嵌 qt 快照块，只取 day 数组。
    成交量按「张」原样透传（待双源验证，见模块 docstring）。
    """
    data = payload.get("data") or {}
    if not data:
        return pd.DataFrame(columns=_BOND_COLUMNS)
    tencent_code = next(iter(data))
    rows = data[tencent_code].get("day") or data[tencent_code].get("qfqday") or []
    code = _from_bond_tencent_code(tencent_code)
    records = [
        {
            "date": str(r[0]),
            "code": code,
            "open": float(r[1]),
            "close": float(r[2]),
            "high": float(r[3]),
            "low": float(r[4]),
            "volume": float(r[5]),  # 张（不做 ×100 股票侧归一）
        }
        for r in rows
        if len(r) >= 6
    ]
    out = pd.DataFrame.from_records(records, columns=_BOND_COLUMNS)
    return out[_BOND_COLUMNS]


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"无法解析日期: {value!r}") from exc


def is_active(last_date, as_of=None, max_stale_days: int = 30) -> bool:
    """摘牌检测：最后一根 K 线日期距今超过 max_stale_days 自然日即 inactive。

    实证背景（2026-10-11 实测）：腾讯对摘牌转债返回**过期数据**而非空——
    九洲转2 K 线停在 2025-10-09、浦发转债停在 2025-10-22，行情快照时间戳
    仍显示「当日 09:00:00」占位（不可用于新鲜度判断），唯有 K 线末根日期可靠。
    阈值 30 自然日与股票侧 filter_universe 的新鲜度门槛一致。
    """
    last = _to_date(last_date)
    ref = _to_date(as_of) if as_of is not None else date.today()
    return (ref - last).days <= max_stale_days
