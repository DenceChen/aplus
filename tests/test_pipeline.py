"""容灾流水线测试：TDD RED 先行。

设计依据（调研报告 4.5 节）：多源容灾不是可选项——Tushare 2025-08 停运一周的教训。
- 主源异常 → 自动降级到下一源，降级事件被记录
- 双源都拿到 → 交叉校验；不达标 → 隔离，不进主库
- 单源可用（其余全挂）→ 落库但标记 degraded
"""
import json

import pandas as pd
import pytest

from aplus import log as aplus_log
from aplus.pipeline import SyncPipeline
from aplus.store import LocalStore

# baostock 主源 12 列 / 腾讯备源 7 列（convert.py:81 明确不产出 amount）
FULL_COLUMNS = ("date", "code", "open", "high", "low", "close", "volume",
                "amount", "tradestatus", "isST", "turn", "pctChg")
NARROW_COLUMNS = ("date", "code", "open", "high", "low", "close", "volume")


def _daily(rows=None):
    rows = rows or [
        {"date": "2026-01-05", "open": 10.0, "high": 10.8, "low": 9.9, "close": 10.5, "volume": 1e6, "amount": 1.05e7},
        {"date": "2026-01-06", "open": 10.5, "high": 11.0, "low": 10.2, "close": 10.8, "volume": 8e5, "amount": 8.64e6},
    ]
    return pd.DataFrame(rows)


class FakeSource:
    def __init__(self, name, frame=None, error=None):
        self.name = name
        self._frame = frame
        self._error = error

    def fetch_daily(self, code, start, end):
        if self._error:
            raise self._error
        return self._frame.copy()


def test_primary_success_writes_master(tmp_path):
    primary = FakeSource("baostock", _daily())
    backup = FakeSource("akshare", _daily())
    store = LocalStore(tmp_path)
    result = SyncPipeline([primary, backup], store).sync_daily("000001.SZ", "2026-01-01", "2026-01-31")
    assert result.status == "ok"
    assert store.read_daily("000001.SZ") is not None
    assert result.cross_checked is True


def test_failover_to_backup_on_primary_error(tmp_path):
    """语义统一：主源挂、单源存活 → 数据照常落库（容灾达成），但 status=degraded（无交叉校验）。"""
    primary = FakeSource("baostock", error=ConnectionError("login refused"))
    backup = FakeSource("akshare", _daily())
    store = LocalStore(tmp_path)
    result = SyncPipeline([primary, backup], store).sync_daily("000001.SZ", "2026-01-01", "2026-01-31")
    assert result.status == "degraded"
    assert "baostock" in result.degraded_from
    assert store.read_daily("000001.SZ") is not None


def test_all_sources_fail_raises(tmp_path):
    store = LocalStore(tmp_path)
    pipeline = SyncPipeline(
        [FakeSource("baostock", error=ConnectionError("x")), FakeSource("akshare", error=TimeoutError("y"))],
        store,
    )
    with pytest.raises(RuntimeError, match="all sources failed"):
        pipeline.sync_daily("000001.SZ", "2026-01-01", "2026-01-31")


def test_crosscheck_mismatch_quarantines(tmp_path):
    good = _daily()
    bad = _daily()
    bad.loc[0, "volume"] = 9e9  # 严重不一致
    store = LocalStore(tmp_path)
    result = SyncPipeline([FakeSource("baostock", good), FakeSource("akshare", bad)], store).sync_daily(
        "000001.SZ", "2026-01-01", "2026-01-31"
    )
    assert result.status == "quarantined"
    assert store.read_daily("000001.SZ") is None  # 主库不被污染
    assert store.read_quarantine_daily("000001.SZ") is not None


def test_single_source_survivor_marks_degraded(tmp_path):
    """只剩一个源时：落库但 status=degraded，提示可信度降级。"""
    only = FakeSource("akshare", _daily())
    dead = FakeSource("baostock", error=ConnectionError("down"))
    store = LocalStore(tmp_path)
    result = SyncPipeline([dead, only], store).sync_daily("000001.SZ", "2026-01-01", "2026-01-31")
    assert result.status == "degraded"
    assert result.cross_checked is False
    assert store.read_daily("000001.SZ") is not None


def test_empty_frames_from_all_sources_raises(tmp_path):
    """真实回归：双源都返回空帧（如尚未上市/已退市无数据）≠ 成功——
    不得写出空 parquet（空文件的 date.max()=NaN 曾让每日增量崩溃）。"""
    empty = FakeSource("baostock", _daily().iloc[0:0])
    empty2 = FakeSource("tencent", _daily().iloc[0:0])
    store = LocalStore(tmp_path)
    with pytest.raises(RuntimeError, match="all sources failed|empty"):
        SyncPipeline([empty, empty2], store).sync_daily("999999.SZ", "2026-01-01", "2026-01-31")
    assert store.read_daily("999999.SZ") is None  # 没有空文件落地


def test_overlap_mode_allows_backup_with_shorter_history(tmp_path):
    """全量回填场景：主源有全历史，备源（腾讯）只有近期——overlap 模式下应为 ok 而非误隔离。"""
    primary = FakeSource("baostock", _daily())  # 2 天全历史
    backup_frame = _daily().tail(1).reset_index(drop=True)  # 备源只有最近 1 天
    backup = FakeSource("tencent", backup_frame)
    store = LocalStore(tmp_path)
    result = SyncPipeline([primary, backup], store, coverage="overlap").sync_daily(
        "000001.SZ", "2019-01-01", "2026-01-31"
    )
    assert result.status == "ok"
    assert result.cross_checked is True
    assert store.read_daily("000001.SZ") is not None


# ── 根因 A：降级分支必须能分辨「主源存活」与「备源存活」 ──────────────────

def _full_frame(dates, *, code="600000.SH", **overrides):
    """造 baostock 形态的 12 列帧（价格只由日期决定，两源可对齐比对）。"""
    dates = list(dates)
    closes = [10.0 + (pd.Timestamp(d) - pd.Timestamp("2026-01-05")).days * 0.1 for d in dates]
    df = pd.DataFrame({
        "date": dates,
        "code": [code] * len(dates),
        "open": closes,
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
        "volume": [1e6] * len(dates),
        "amount": [c * 1e6 for c in closes],
        "tradestatus": [1] * len(dates),
        "isST": [0] * len(dates),
        "turn": [1.5] * len(dates),
        "pctChg": [0.0] * len(dates),
    })
    for col, value in overrides.items():
        df[col] = value
    return df


def _narrow_frame(dates, *, code="600000.SH", **overrides):
    """造腾讯形态的 7 列帧。"""
    df = _full_frame(dates, code=code)
    df = df[list(NARROW_COLUMNS)].copy()
    for col, value in overrides.items():
        df[col] = value
    return df


def test_backup_survivor_write_does_not_destroy_master(tmp_path):
    """根因 A：主源 baostock 挂掉、备源 tencent 存活时的写入不得损坏主库。

    旧实现把 7 列降级帧当正常数据 `upsert_daily` 进主库，concat 后 turn/isST/amount
    被抹成 NaN——含换手率因子的候选池因此静默少掉几千只股票。
    """
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _full_frame(["2026-01-05", "2026-01-06"]))
    before = store.read_daily("600000.SH").set_index("date")

    pipeline = SyncPipeline(
        [FakeSource("baostock", error=ConnectionError("login refused")),
         FakeSource("tencent", _narrow_frame(["2026-01-05", "2026-01-06"]))],
        store,
    )
    result = pipeline.sync_daily("600000.SH", "2026-01-01", "2026-01-31")

    assert result.status == "degraded"
    after = store.read_daily("600000.SH").set_index("date")
    assert set(FULL_COLUMNS) - {"date"} <= set(after.columns)
    assert after["turn"].notna().all()
    assert after.loc["2026-01-06", "turn"] == before.loc["2026-01-06", "turn"]


def test_backup_survivor_write_is_labelled_in_reason(tmp_path):
    """源链顺序即优先级：`self.sources[0]` 是主源，其余是备源。

    旧实现 `names[0]` 取的是「第一个**成功**的源」，于是主源一挂，降级源就被当成
    主源写库，调用方从 result 里完全看不出这次写入是降级来源。reason 必须说清。
    """
    pipeline = SyncPipeline(
        [FakeSource("baostock", error=ConnectionError("down")),
         FakeSource("tencent", _narrow_frame(["2026-01-05"]))],
        LocalStore(tmp_path),
    )
    result = pipeline.sync_daily("600000.SH", "2026-01-01", "2026-01-31")
    assert result.status == "degraded"
    assert "tencent" in result.reason      # 谁写的
    assert "baostock" in result.reason     # 谁挂的
    assert "降级源写入" in result.reason    # 这是降级来源的写入
    assert result.sources_used == ["tencent"]
    assert result.degraded_from == ["baostock"]


def test_primary_survivor_reason_is_plain_degraded(tmp_path):
    """对照组：存活源**就是主源**（只是备源挂了）→ 保持现状：写主库 + degraded，
    但不得谎称「降级源写入」。两个分支必须能被调用方分辨。"""
    pipeline = SyncPipeline(
        [FakeSource("baostock", _full_frame(["2026-01-05"])),
         FakeSource("tencent", error=TimeoutError("down"))],
        LocalStore(tmp_path),
    )
    result = pipeline.sync_daily("600000.SH", "2026-01-01", "2026-01-31")
    assert result.status == "degraded"
    assert "降级源写入" not in result.reason
    assert "tencent" in result.reason       # 原始故障原因仍要可追溯


def test_backup_survivor_write_emits_structured_signal(tmp_path, monkeypatch):
    """可观测信号：降级源写入要留下结构化事件，否则这正是当初排查了两天的那类
    「数据悄悄变窄」——没人看见就等于没发生。日志根指向 tmp_path，不碰真实 data/。"""
    monkeypatch.setattr(aplus_log, "_ACTIVE_ROOT", tmp_path)
    pipeline = SyncPipeline(
        [FakeSource("baostock", error=ConnectionError("down")),
         FakeSource("tencent", _narrow_frame(["2026-01-05"]))],
        LocalStore(tmp_path),
    )
    pipeline.sync_daily("600000.SH", "2026-01-01", "2026-01-31")

    events = [json.loads(l) for f in sorted((tmp_path / "logs").glob("*.jsonl"))
              for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    signals = [e for e in events if e.get("event") == "degraded_source_write"]
    assert len(signals) == 1
    assert signals[0]["code"] == "600000.SH"
    assert signals[0]["source"] == "tencent"
    assert signals[0]["primary"] == "baostock"


def test_backup_survivor_still_lands_new_dates(tmp_path):
    """容灾语义不许被保列规则改掉：备源独有的**新日期**必须进主库（缺列如实缺失）。"""
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _full_frame(["2026-01-05"]))
    pipeline = SyncPipeline(
        [FakeSource("baostock", error=ConnectionError("down")),
         FakeSource("tencent", _narrow_frame(["2026-01-05", "2026-01-06"]))],
        store,
    )
    result = pipeline.sync_daily("600000.SH", "2026-01-01", "2026-01-31")
    assert result.status == "degraded"
    back = store.read_daily("600000.SH")
    assert back["date"].tolist() == ["2026-01-05", "2026-01-06"]
    assert pd.isna(back.loc[back["date"] == "2026-01-06", "turn"].iloc[0])
