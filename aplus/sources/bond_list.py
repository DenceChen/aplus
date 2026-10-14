"""可转债动态名单源（路线 A 第一阶段，PLATFORM.md §7.5）。

探测结论（2026-10-11 本机实测）：
- **SSE（沪，首选）**：query.sse.com.cn 的 commonQuery/commonSoa 系列 sqlId 均返回空/缺参；
  实际可用的是债券门户静态全量文件 https://bond.sse.com.cn/disclosure/info/tb/allBonds.js
  （2.5MB JS，'代码':['名称',_t[类型]] 映射，_t 含「可转换公司债券」——含已摘牌历史债，
  全量 97k 条中转债 794 只，前缀 110/111/113/118）
- **SZSE（深）**：ShowReport API 可达但债券目录未见（扫描无果），bond.szse.cn 的 JS 404——放弃
- **腾讯兜底（深市主路径 / 沪市降级）**：qt.gtimg.cn 批量行情，一次请求最多 ~60 个代码，
  字段 1=名称、字段 61=品种标记（ZQ-KZZ=可转债；ZQ-GZ=国债）；未知代码整行缺失；
  **摘牌债仍返回**（名字保留、量为 0）——正好构成历史+当前全集，摘牌由 kline 新鲜度判定

名单合成策略：沪市取 SSE 文件（权威、含历史），深市走腾讯前缀扫描（123-128 段全部 ZQ-KZZ，
2026-10-11 全扫实测 1121 只转债、无混入其他品种）；SSE 挂掉时腾讯扫描接管两市。

成本注记：腾讯扫描 ~50 次 HTTP（3000 深市前缀候选 / 60 每批，6-8 并发数秒完成）；
SSE 文件单次 GET。两者均不落盘——名单每调即取，保持动态。
"""
import ast
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

# 沪市可转债代码前缀（110 传统 / 111 新段 / 113 主力 / 118 科创板）；
# 204 段是国债回购、019 段国债——天然被前缀规则排除（任务书要求非 204 国债）
_SH_CONVERT_PREFIXES = ("110", "111", "113", "118")
# 深市可转债代码前缀（123 主力 / 124-125 定向 / 126 古段 / 127 主板新 / 128 旧中小板）
_SZ_CONVERT_PREFIXES = ("123", "124", "125", "126", "127", "128")

SSE_ALL_BONDS_URL = "https://bond.sse.com.cn/disclosure/info/tb/allBonds.js"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
_KZZ_MARKER = "ZQ-KZZ"
_QUOTE_BATCH_SIZE = 60

# qt.gtimg.cn 返回字段下标（实测钉死）：1=名称，61=品种标记
_NAME_IDX, _TYPE_IDX = 1, 61


def _default_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False  # 本机系统代理对国内站点不可靠，直连
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    return session


# ---------- SSE 全量债券文件解析（纯函数） ----------

def parse_all_bonds_js(text: str) -> pd.DataFrame:
    """SSE allBonds.js 文本 → 沪市可转债名单 DataFrame[code,name,exchange]。

    文件结构：var _t = [类型名...]; ... '代码':['名称',_t[下标]] ...
    类型下标随门户改版会变（实测踩过：错把「非公开发行公司债券」当转债），
    必须动态解析 _t 数组按名称定位「可转换公司债券」，再叠加代码前缀规则双保险
    （100xxx 古老转债段腾讯无 K 线，一并剔除）。
    """
    type_match = re.search(r"var _t = (\[[^\]]+\]);", text)
    if not type_match:
        raise ValueError("allBonds.js 中未找到 _t 类型数组")
    types = ast.literal_eval(type_match.group(1))
    try:
        kzz_idx = types.index("可转换公司债券")
    except ValueError as exc:
        raise ValueError("allBonds.js 类型表中缺少「可转换公司债券」") from exc

    records = []
    for m in re.finditer(r"'(\d{6})':\[([^,]+),_t\[(\d+)\]\]", text):
        code, name, tidx = m.group(1), m.group(2), int(m.group(3))
        if tidx != kzz_idx or not code.startswith(_SH_CONVERT_PREFIXES):
            continue
        records.append({"code": f"{code}.SH", "name": ast.literal_eval(name), "exchange": "SH"})
    out = pd.DataFrame(records, columns=["code", "name", "exchange"])
    # 实证（2026-10-11）：文件内含更新块+历史块两个映射，同一代码出现两次且名称可不同
    # （「双良转退」vs「双良转债」）——按代码去重，保留首次出现（更新块在前、名称带摘牌标记）
    return out.drop_duplicates(subset="code", keep="first").reset_index(drop=True)


def fetch_sse_convertibles(session=None) -> pd.DataFrame:
    """拉取 SSE 债券门户全量文件并解析出沪市可转债（含已摘牌历史债）。"""
    if session is None:
        session = _default_session()
    resp = session.get(SSE_ALL_BONDS_URL, timeout=30)
    resp.raise_for_status()
    return parse_all_bonds_js(resp.text)


# ---------- 腾讯批量行情扫描（纯解析 + 网络探测） ----------

def parse_quote_batch(text: str) -> dict:
    """qt.gtimg.cn 批量行情文本 → {腾讯代码: (名称, 品种标记)}，只保留可转债（ZQ-KZZ）。"""
    out = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', text):
        fields = m.group(2).split("~")
        if len(fields) <= _TYPE_IDX:
            continue
        sec_type = fields[_TYPE_IDX]
        if sec_type == _KZZ_MARKER:
            out[m.group(1)] = (fields[_NAME_IDX], sec_type)
    return out


def _probe_batch(session, codes):
    for attempt in range(2):
        try:
            resp = session.get(TENCENT_QUOTE_URL + ",".join(codes), timeout=10)
            resp.raise_for_status()
            return parse_quote_batch(resp.text)
        except Exception:  # noqa: BLE001 — 瞬时网络错误重试一次
            if attempt == 0:
                time.sleep(0.5)
    return {}


def scan_tencent_convertibles(session=None, prefixes=None) -> pd.DataFrame:
    """腾讯前缀段扫描 → 可转债名单（深市主路径；SSE 挂掉时也覆盖沪市）。

    扫描范围：深 123-128 + 沪 110-118 段（2026-10-11 全扫实证：这些段命中的
    全部是 ZQ-KZZ 转债，无国债/公司债混入；112/114-117 段为空）。
    摘牌债腾讯仍返回（供历史回补），活跃与否由 kline 末根日期判定（bond_convert.is_active）。
    """
    if session is None:
        session = _default_session()
    scan_prefixes = prefixes or (
        [f"sz{p}" for p in _SZ_CONVERT_PREFIXES] + [f"sh{p}" for p in _SH_CONVERT_PREFIXES]
    )
    codes = [f"{p}{i:03d}" for p in scan_prefixes for i in range(1000)]
    batches = [codes[i:i + _QUOTE_BATCH_SIZE] for i in range(0, len(codes), _QUOTE_BATCH_SIZE)]
    found = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for result in ex.map(lambda b: _probe_batch(session, b), batches):
            found.update(result)
    records = [
        {
            "code": f"{tcode[2:]}.{tcode[:2].upper()}",
            "name": name,
            "exchange": tcode[:2].upper(),
        }
        for tcode, (name, _t) in sorted(found.items())
    ]
    return pd.DataFrame(records, columns=["code", "name", "exchange"])


# ---------- 名单合成 ----------

def fetch_bond_list(session=None, sse_fetcher=None, scan_fetcher=None) -> pd.DataFrame:
    """全市场可转债名单 → DataFrame[code,name,exchange]（统一代码风格，按代码序）。

    合成：沪市 SSE 文件（权威）+ 深市腾讯扫描；重复代码以 SSE 为准。
    SSE 不可用时腾讯扫描接管两市；两路全挂则抛错（名单是策略前提，不静默降级）。
    """
    if sse_fetcher is None:
        sse_fetcher = fetch_sse_convertibles
    if scan_fetcher is None:
        scan_fetcher = scan_tencent_convertibles

    frames, errors = [], []
    for fetcher in (sse_fetcher, scan_fetcher):
        try:
            frames.append(fetcher(session))
        except Exception as exc:  # noqa: BLE001 — 单源故障降级到下一路
            errors.append(f"{getattr(fetcher, '__name__', fetcher)}: {exc}")
    if not frames:
        raise RuntimeError(f"可转债名单两路来源均失败: {errors}")

    combined = pd.concat(frames, ignore_index=True)
    # SSE 在前：drop_duplicates 保留首次出现（SSE 权威），剔除腾讯重复
    combined = combined.drop_duplicates(subset="code", keep="first")
    return combined.sort_values("code").reset_index(drop=True)
