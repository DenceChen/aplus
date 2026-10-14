"""腾讯可转债行情适配器（路线 A 第一阶段）。

复用 TencentSource 的会话与重试模式（trust_env=False 直连——本机系统代理
127.0.0.1:8118 对国内行情站不可靠），但走 bond_convert 的转债转换：
- volume 单位为张（不 ×100）
- 摘牌债返回过期数据而非空——消费方必须用 bond_convert.is_active 做新鲜度过滤
- 腾讯 kline 仅最近约 640 个交易日（转债 2018 年后上市密集，覆盖足够）
"""
import time

import requests

from .base import DataSource
from .bond_convert import bond_frame_to_unified, to_bond_tencent_code

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
MAX_BARS = 640  # 与股票侧一致：腾讯 kline 单次上限（交易日）


class _EmptyBondResult(RuntimeError):
    """稳定为空（腾讯无此转债代码）——不重试。"""


class TencentBondSource(DataSource):
    name = "tencent_bond"

    def __init__(self, session=None):
        if session is None:
            session = requests.Session()
            session.trust_env = False  # 绕过系统代理，国内站点直连
            session.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        self._session = session

    def fetch_daily(self, code: str, start: str, end: str):
        """带一次重试的转债日 K 拉取（瞬时失败重试；无此代码稳定为空不重试）。"""
        last_exc = None
        for attempt in range(2):
            try:
                param = f"{to_bond_tencent_code(code)},day,{start},{end},{MAX_BARS},"
                resp = self._session.get(KLINE_URL, params={"param": param}, timeout=15)
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("code") != 0:
                    raise RuntimeError(f"tencent bond kline error for {code}: {payload.get('msg')}")
                # 腾讯对无此代码的转债返回 data:{}（与退市股票的稳定空同语义）
                df = bond_frame_to_unified(payload)
                if df.empty:
                    raise _EmptyBondResult(f"tencent bond returned empty for {code}")
                return df
            except _EmptyBondResult:
                raise
            except Exception as exc:  # noqa: BLE001 — 瞬时网络错误重试一次
                last_exc = exc
                if attempt == 0:
                    time.sleep(1.0)
        raise last_exc
