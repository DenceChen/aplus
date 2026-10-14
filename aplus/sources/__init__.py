"""源适配层。"""
import os

from .akshare_source import AkshareSource
from .base import DataSource
from .baostock_source import BaostockSource
from .tencent_source import TencentSource

__all__ = ["DataSource", "BaostockSource", "TencentSource", "AkshareSource", "TushareSource", "default_sources"]


def default_sources() -> list:
    """默认源链：baostock 主源 → tencent 备源 →（有 token 时）tushare 第三源。

    akshare 不在默认链：其东财源在本机被 CDN 掐 HTTP/1.1 且响应间歇（2026-10 实测），
    代码保留供其他环境/广度数据使用（见 README「已知边界」）。
    """
    sources = [BaostockSource(), TencentSource()]
    if os.environ.get("TUSHARE_TOKEN"):
        from .tushare_source import TushareSource

        sources.append(TushareSource())
    return sources
