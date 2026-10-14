"""可转债数据层测试：TDD RED 先行（路线 A 第一阶段，见 docs/PLATFORM.md §7.5）。

纯函数测试（不打网络）：
- bond_convert：腾讯代码归一、kline 帧转换（volume 单位=张）、摘牌检测 is_active
- bond_list：SSE allBonds.js 解析、腾讯批量行情解析、名单合成与前缀过滤
- TencentBondSource：注入假 session 的 fetch_daily 行为

网络事实（2026-10-11 实测）：
- 腾讯 kline 覆盖转债（sz123089/sh113056/sh110059），列序与股票相同 date,open,close,high,low,volume
- 摘牌债（九洲转2 @2025-10、浦发转债 @2025-10）返回过期 K 线——名字单必须配摘牌检测
- qt.gtimg.cn 批量行情：字段 1=名称、字段 61=品种标记（ZQ-KZZ=可转债），未知代码整行缺失
- SSE 债券门户 allBonds.js：'代码':['名称',_t[类型下标]]，_t 含「可转换公司债券」
"""
from datetime import date

import pandas as pd
import pytest

from aplus.sources.bond_convert import (
    bond_frame_to_unified,
    is_active,
    to_bond_tencent_code,
)
from aplus.sources.tencent_bond_source import TencentBondSource


# ---------- 代码归一 ----------

def test_to_bond_tencent_code_sh():
    assert to_bond_tencent_code("113056.SH") == "sh113056"
    assert to_bond_tencent_code("110059.SH") == "sh110059"
    assert to_bond_tencent_code("118038.SH") == "sh118038"


def test_to_bond_tencent_code_sz():
    assert to_bond_tencent_code("123089.SZ") == "sz123089"
    assert to_bond_tencent_code("127045.SZ") == "sz127045"
    assert to_bond_tencent_code("128036.SZ") == "sz128036"


def test_to_bond_tencent_code_rejects_bad_format():
    with pytest.raises(ValueError):
        to_bond_tencent_code("sz123089")  # 必须是统一风格 123089.SZ
    with pytest.raises(ValueError):
        to_bond_tencent_code("123089")  # 缺交易所


# ---------- kline 帧转换 ----------

def tencent_bond_kline_payload():
    """腾讯转债 kline JSON（sz123089 实测形状）：数组列序 date,open,close,high,low,volume，
    末行可携带附加列。此债实为摘牌债——K 线停在 2025-10-09（正好当摘牌检测素材）。"""
    return {
        "code": 0,
        "msg": "",
        "data": {
            "sz123089": {
                "day": [
                    ["2025-09-18", "130.301", "127.742", "131.202", "127.201", "95901.000"],
                    ["2025-10-09", "124.110", "123.799", "124.830", "122.684", "67729.000", "8407131.000"],
                ],
                "qt": {"sz123089": ["51", "九洲转2"]},
            }
        },
    }


def test_bond_frame_maps_columns_in_tencent_order():
    """转债 kline 列序与股票相同（close 在 high 前）——映射坑与股票侧一致。"""
    uni = bond_frame_to_unified(tencent_bond_kline_payload())
    assert uni["code"].unique().tolist() == ["123089.SZ"]
    assert uni["open"].tolist() == [130.301, 124.110]
    assert uni["close"].tolist() == [127.742, 123.799]
    assert uni["high"].tolist() == [131.202, 124.830]
    assert uni["low"].tolist() == [127.201, 122.684]
    assert uni["date"].tolist() == ["2025-09-18", "2025-10-09"]


def test_bond_frame_volume_is_lots_raw():
    """转债成交量按「张」处理：原样透传，不做股票侧的 ×100 归一（单位待双源验证）。"""
    uni = bond_frame_to_unified(tencent_bond_kline_payload())
    assert uni["volume"].tolist() == [95901.0, 67729.0]


def test_bond_frame_schema_and_tolerates_extra_columns():
    uni = bond_frame_to_unified(tencent_bond_kline_payload())
    assert list(uni.columns) == ["date", "code", "open", "high", "low", "close", "volume"]
    assert len(uni) == 2  # 末行附加列（成交额）不炸解析


def test_bond_frame_empty_payload_returns_empty_schema():
    payload = {"code": 0, "msg": "", "data": {}}
    uni = bond_frame_to_unified(payload)
    assert uni.empty
    assert list(uni.columns) == ["date", "code", "open", "high", "low", "close", "volume"]


def test_bond_frame_ignores_qt_metadata_block():
    """kline 响应内嵌 qt 快照块——解析只取 day 数组。"""
    uni = bond_frame_to_unified(tencent_bond_kline_payload())
    assert len(uni) == 2


# ---------- 摘牌检测 ----------

def test_is_active_fresh_within_threshold():
    assert is_active("2026-10-09", as_of=date(2026, 10, 11)) is True


def test_is_active_stale_marks_inactive():
    """摘牌实证：浦发转债 K 线停在 2025-10-22——对 2026-10 视角必须判 inactive。"""
    assert is_active("2025-10-22", as_of=date(2026, 10, 11)) is False


def test_is_active_boundary_is_inclusive():
    assert is_active("2026-09-11", as_of=date(2026, 10, 11), max_stale_days=30) is True
    assert is_active("2026-09-10", as_of=date(2026, 10, 11), max_stale_days=30) is False


def test_is_accepts_date_objects_and_custom_threshold():
    assert is_active(date(2026, 10, 1), as_of=date(2026, 10, 11), max_stale_days=10) is True
    assert is_active(date(2026, 9, 1), as_of=date(2026, 10, 11), max_stale_days=10) is False


def test_is_active_defaults_as_of_to_today():
    # 显式 as_of None → date.today()；用今天/昨天的日期验证默认路径
    assert is_active(date.today()) is True


def test_is_active_rejects_garbage_date():
    with pytest.raises(ValueError):
        is_active("not-a-date")


# ---------- TencentBondSource ----------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """记录请求参数的假 session（复刻 TencentSource 的 session 协议）。"""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return _FakeResp(self._payload)


def test_bond_source_fetch_daily_uses_bond_code_and_frame():
    session = _FakeSession(tencent_bond_kline_payload())
    src = TencentBondSource(session=session)
    df = src.fetch_daily("123089.SZ", "2025-09-01", "2025-10-31")
    assert df["code"].unique().tolist() == ["123089.SZ"]
    assert uni_rows_sorted(df)
    url, params = session.calls[0]
    assert params["param"].startswith("sz123089,day,2025-09-01,2025-10-31")


def uni_rows_sorted(df):
    return df["date"].tolist() == sorted(df["date"].tolist())


def test_bond_source_empty_raises_stable_empty():
    """腾讯无数据的转债代码（从未上市/错码）→ 稳定为空，不重试（复刻股票侧语义）。"""
    from aplus.sources.tencent_bond_source import _EmptyBondResult

    session = _FakeSession({"code": 0, "msg": "", "data": {}})
    src = TencentBondSource(session=session)
    with pytest.raises(_EmptyBondResult):
        src.fetch_daily("129999.SZ", "2026-01-01", "2026-01-31")


def test_bond_source_default_session_bypasses_proxy():
    """本机系统代理（127.0.0.1:8118）对国内行情站不可靠——默认 session 必须 trust_env=False。"""
    src = TencentBondSource()
    assert src._session.trust_env is False
    assert "Mozilla" in src._session.headers["User-Agent"]


# ---------- 名单源：SSE allBonds.js 解析 ----------

ALL_BONDS_JS_SAMPLE = (
    "var _t = ['记账式国债','地方政府债券','普通金融债','企业债券','中小企业私募债券',"
    "'公司债券','可转换公司债券','分离交易的可转换公司债券','可交换公司债券',"
    "'非公开发行公司债券','次级债券','证券公司资产支持证券'];\n"
    "function getAllBonds(){\n"
    " var _allBonds = {\n"
    "'019742':['24特国01',_t[0]],'204001':['GC001',_t[14]],"
    "'113056':['重银转债',_t[6]],'110059':['浦发转债',_t[6]],"
    "'118038':['金宏转债',_t[6]],'111022':['宏丰转债',_t[6]],"
    "'100xxx-placeholder':['ancient',_t[6]],"
    "'243003':['葛洲YK10',_t[5]],'020056':['16附息国债',_t[0]]};\n"
    " return _allBonds;}\n"
)


def test_parse_all_bonds_js_extracts_convertibles():
    from aplus.sources.bond_list import parse_all_bonds_js

    df = parse_all_bonds_js(ALL_BONDS_JS_SAMPLE)
    # 只留可转换公司债券，且代码前缀必须命中 110/111/113/118（100xxx 古老代码剔除）
    assert set(df["code"]) == {"113056.SH", "110059.SH", "118038.SH", "111022.SH"}
    assert set(df["exchange"]) == {"SH"}
    names = dict(zip(df["code"], df["name"]))
    assert names["113056.SH"] == "重银转债"
    assert list(df.columns) == ["code", "name", "exchange"]


def test_parse_all_bonds_js_dedups_revision_blocks():
    """实证回归（2026-10-11）：allBonds.js 内含两个映射块（更新块在前、历史块在后），
    同一代码出现两次且名称可不同（「双良转退」vs「双良转债」）——必须按代码去重，
    保留首次出现（更新块名称带「转退」摘牌标记，更新）。"""
    from aplus.sources.bond_list import parse_all_bonds_js

    text = ALL_BONDS_JS_SAMPLE + "'113056':['重银转债旧名',_t[6]];"
    df = parse_all_bonds_js(text)
    assert (df["code"] == "113056.SH").sum() == 1
    assert df.loc[df["code"] == "113056.SH", "name"].iloc[0] == "重银转债"


# ---------- 名单源：腾讯批量行情解析 ----------

QT_SAMPLE = (
    'v_sz127045="51~牧原转债~127045~116.971~116.188~0.000~0~0~0~0.000~0~0.000~0~0.000~0~0.000~0~0.000'
    "~0~0.000~0~0.000~0~0.000~0~0.000~0~0.000~0~~20261009090000~0.000~0.00~0.000~0.000~116.971/0/0~0~0~"
    '~~D~0.000~0.000~0.00~~~0.00~-1~-1~0.00~0~0.000~~~~~~0.0000~0.0000~0~ ~ZQ-KZZ~0.00~0.00~CNY~0~";\n'
    'v_sh019742="1~24特国01~019742~100.5~100.6~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~~'
    "20261009090000~0~0~0~0~0/0/0~0~0~~D~0~0~0~~~0~0~-1~-1~0~0~0~~~~~~0~0~0~ ~ZQ-GZ~CNY~0~\";\n"
)


def test_parse_quote_batch_keeps_only_kzz():
    from aplus.sources.bond_list import parse_quote_batch

    out = parse_quote_batch(QT_SAMPLE)
    assert out == {"sz127045": ("牧原转债", "ZQ-KZZ")}  # 国债 ZQ-GZ 剔除


# ---------- 名单合成 ----------

def test_fetch_bond_list_combines_sse_and_tencent_scan(monkeypatch):
    from aplus.sources import bond_list

    monkeypatch.setattr(
        bond_list, "fetch_sse_convertibles",
        lambda session=None: pd.DataFrame({
            "code": ["110059.SH", "113056.SH"],
            "name": ["浦发转债", "重银转债"],
            "exchange": ["SH", "SH"],
        }),
    )
    monkeypatch.setattr(
        bond_list, "scan_tencent_convertibles",
        lambda session=None: pd.DataFrame({
            "code": ["127045.SZ", "128036.SZ", "113056.SH"],  # 113056 重复——SSE 权威去重
            "name": ["牧原转债", "金农转债", "重银转债(腾讯)"],
            "exchange": ["SZ", "SZ", "SH"],
        }),
    )
    df = bond_list.fetch_bond_list()
    assert list(df.columns) == ["code", "name", "exchange"]
    assert len(df) == 4  # 2 SH + 2 SZ，重复剔除
    names = dict(zip(df["code"], df["name"]))
    assert names["113056.SH"] == "重银转债"  # SSE 权威
    assert names["127045.SZ"] == "牧原转债"
    assert df["code"].is_monotonic_increasing


def test_fetch_bond_list_survives_sse_outage(monkeypatch):
    """SSE 挂了 → 腾讯扫描兜底接管沪深两所（任务书 c 兜底路径）。"""
    from aplus.sources import bond_list

    def _boom(session=None):
        raise RuntimeError("sse down")

    monkeypatch.setattr(bond_list, "fetch_sse_convertibles", _boom)
    monkeypatch.setattr(
        bond_list, "scan_tencent_convertibles",
        lambda session=None: pd.DataFrame({
            "code": ["113056.SH", "127045.SZ"],
            "name": ["重银转债", "牧原转债"],
            "exchange": ["SH", "SZ"],
        }),
    )
    df = bond_list.fetch_bond_list()
    assert set(df["code"]) == {"113056.SH", "127045.SZ"}


def test_fetch_bond_list_all_sources_down_raises():
    from aplus.sources import bond_list

    with pytest.raises(RuntimeError):
        bond_list.fetch_bond_list(
            sse_fetcher=lambda session=None: (_ for _ in ()).throw(RuntimeError("down")),
            scan_fetcher=lambda session=None: (_ for _ in ()).throw(RuntimeError("down")),
        )
