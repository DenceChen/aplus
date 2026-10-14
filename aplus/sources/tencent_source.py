"""腾讯行情适配器（备源）：python 直连稳定（本机实测），无 Referer 限制。

限制（调研报告 4.2）：kline 仅最近约 640 个交易日、无成交额列——
用于近期日线交叉校验与降级备源；深历史依赖 baostock 主源。

本机环境注记：requests 默认会走 macOS 系统代理（127.0.0.1:8118），
该代理对国内行情站点不可靠，故显式 trust_env=False 直连。
"""
import time

import requests

from .base import DataSource
from .convert import tencent_frame_to_unified, to_tencent_code

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
MAX_BARS = 640  # 腾讯 kline 单次上限（交易日）


class _EmptyResult(RuntimeError):
    """稳定为空（如退市股）——不重试。"""


class TencentSource(DataSource):
    name = "tencent"

    def __init__(self, session=None):
        if session is None:
            session = requests.Session()
            session.trust_env = False  # 绕过系统代理，国内站点直连
            session.headers["User-Agent"] = "Mozilla/5.0 (compatible; aplus/0.1)"
        self._session = session

    def fetch_daily(self, code: str, start: str, end: str):
        """带一次重试的日 K 拉取（实测存在瞬时失败；退市股则稳定为空——不重试那种）。"""
        last_exc = None
        for attempt in range(2):
            try:
                param = f"{to_tencent_code(code)},day,{start},{end},{MAX_BARS},"
                resp = self._session.get(KLINE_URL, params={"param": param}, timeout=15)
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("code") != 0 or not payload.get("data"):
                    raise RuntimeError(f"tencent kline error for {code}: {payload.get('msg')}")
                df = tencent_frame_to_unified(payload)
                if df.empty:
                    # 退市股腾讯无数据是稳定状态，不重试直接抛（由流水线降级处理）
                    raise _EmptyResult(f"tencent returned empty for {code}")
                return df
            except _EmptyResult:
                raise
            except Exception as exc:  # noqa: BLE001 — 瞬时网络错误重试一次
                last_exc = exc
                if attempt == 0:
                    time.sleep(1.0)
        raise last_exc
