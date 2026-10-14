"""baostock 适配器（主源）：日线 1990 起 + 复权因子 + 股票列表。

频控铁律（官网）：日限 5 万次、禁并发、黑名单 6h 起按累计递增——
本适配器只做串行单连接，调用方不得并发使用多个实例打同一 IP。
"""
import baostock as bs
import pandas as pd

from .base import DataSource
from .convert import baostock_frame_to_unified, to_baostock_code

_DAILY_FIELDS = "date,code,open,high,low,close,volume,amount,tradestatus,isST,turn,pctChg"


class BaostockSource(DataSource):
    name = "baostock"

    def __init__(self):
        # baostock SDK 无原生超时——半开 TCP 连接会让进程无限挂起（2026-10-10 回填实证）
        import socket

        socket.setdefaulttimeout(90)
        self._logged_in = False

    def _ensure_login(self):
        if not self._logged_in:
            lg = bs.login()
            if lg.error_code != "0":
                raise ConnectionError(f"baostock login failed: {lg.error_msg}")
            self._logged_in = True

    def _collect(self, rs) -> pd.DataFrame:
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query failed: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=rs.fields)

    def fetch_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        self._ensure_login()
        rs = bs.query_history_k_data_plus(
            to_baostock_code(code),
            _DAILY_FIELDS,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="3",  # 不复权——只落库原始价 + 因子
        )
        return baostock_frame_to_unified(self._collect(rs))

    def fetch_adjust_factor(self, code: str, start: str, end: str):
        """复权因子（后复权 = 不复权价 × 累计因子，与 aplus.adjust.to_hfq 语义一致）。

        两个源站陷阱，都必须防（2026-10-11 生产事故）：
        1. **取列**：用 backAdjustFactor，不用 adjustFactor。后者与前者多数行相同，
           但偶发垃圾值且**低一个数量级**——实测 sh.601607 的 2026-07-17 行
           adjustFactor=1.020971 而 backAdjustFactor=10.037703。该因子被
           attach_factor 向前填充后污染近两个月，20 日动量被放大到 +903%。
        2. **单调性**：后复权因子必然单调不减（除权只增不减）。源站在 back 列也偶发
           倒退伪事件（实测 sz.000001 / sh.601607 各有 1~2 处）——倒退行一律丢弃，
           让因子保持前值直到下一个真实事件（宁缺毋滥，绝不灌垃圾进库）。
        """
        self._ensure_login()
        rs = bs.query_adjust_factor(code=to_baostock_code(code), start_date=start, end_date=end)
        raw = self._collect(rs)
        if raw.empty:
            return None
        out = pd.DataFrame({
            "date": raw["dividOperateDate"].astype(str),
            "adjust_factor": pd.to_numeric(raw["backAdjustFactor"], errors="coerce"),
        }).sort_values("date").reset_index(drop=True)
        out = out[out["adjust_factor"].notna() & (out["adjust_factor"] > 0)]
        # 倒退值压回前值（cummax）：后复权因子只增不减，倒退即源站伪事件。
        # 必须用 cummax 而非丢行——丢掉倒退行后，其后的正常值仍可能低于前值，
        # 单调性照样被破坏。
        out["adjust_factor"] = out["adjust_factor"].cummax()
        return out.reset_index(drop=True)

    def fetch_stock_list(self):
        self._ensure_login()
        rs = bs.query_stock_basic()
        raw = self._collect(rs)
        out = raw[raw["type"] == "1"].copy()  # 1=股票
        return pd.DataFrame({
            "code": out["code"].astype(str).str.lower().map(
                lambda c: f"{c.split('.')[1]}.{c.split('.')[0].upper()}"
            ),
            "name": out["code_name"].astype(str),
            "ipo_date": out["ipoDate"].astype(str),
            "delist_date": out["outDate"].astype(str).replace("", None),
        })

    def close(self):
        if self._logged_in:
            bs.logout()
            self._logged_in = False
