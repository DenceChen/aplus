"""tushare pro 适配器（可选第三源，120 积分免费档：日线 50 次/分、8000 次/天）。

需要环境变量 TUSHARE_TOKEN；未设置时工厂函数 registry 不会把它加进源链。
"""
import os

from .base import DataSource
from .convert import to_tushare_code, tushare_frame_to_unified


class TushareSource(DataSource):
    name = "tushare"

    def __init__(self, token: str | None = None):
        self._token = token or os.environ.get("TUSHARE_TOKEN", "")
        if not self._token:
            raise ValueError("TUSHARE_TOKEN 未设置——tushare 源需要免费注册的 token")
        import tushare as ts

        ts.set_token(self._token)
        self._pro = ts.pro_api()

    def fetch_daily(self, code: str, start: str, end: str):
        df = self._pro.daily(
            ts_code=to_tushare_code(code),
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
        )
        if df is None or df.empty:
            raise RuntimeError(f"tushare returned empty for {code}")
        return tushare_frame_to_unified(df)
