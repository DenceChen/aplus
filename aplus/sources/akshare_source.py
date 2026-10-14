"""akshare 适配器（备源）：东财源，注意礼貌限速，禁高并发（封 IP/滑块）。"""
import akshare as ak

from .base import DataSource
from .convert import akshare_frame_to_unified, to_akshare_code


class AkshareSource(DataSource):
    name = "akshare"

    def fetch_daily(self, code: str, start: str, end: str):
        df = ak.stock_zh_a_hist(
            symbol=to_akshare_code(code),
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="",  # 不复权
        )
        if df is None or df.empty:
            raise RuntimeError(f"akshare returned empty for {code}")
        return akshare_frame_to_unified(df)
