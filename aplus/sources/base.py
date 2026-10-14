"""数据源抽象基类。"""
from abc import ABC, abstractmethod

import pandas as pd


class DataSource(ABC):
    name: str = "base"

    @abstractmethod
    def fetch_daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        """返回统一 schema 的不复权日线。code 为 000001.SZ 风格。"""

    def fetch_adjust_factor(self, code: str, start: str, end: str):
        """返回 {date, adjust_factor} 事件表；源不支持时返回 None。"""
        return None

    def fetch_stock_list(self):
        """返回 {code, name, ipo_date, delist_date} 股票列表（含退市更好）。"""
        return None
