"""单只股票读坏了，不能杀掉全市场 5459 只；出单必须声明数据截止日。

## 事故一（2026-10-10 18:30 生产日志）

```
[update] 步骤失败：TypeError("'>=' not supported between instances of 'float' and 'str'")
[verify] 预测日 2026-10-09 的次日数据未就绪，跳过
[predict] 池子 5032 只 → 涨 10 / 跌 10
    601607.SH 上海医药 score=22.53 mom20=903.72%
...
[done] 2026-10-10 全流程完成（partial）
```

`daily_update.py` 的循环体：

```python
df = pd.read_parquet(path)
last = df["date"].max()      # ← 空表 / 全 NaN 列 → nan（float）
if last >= end:             # ← float >= str 抛 TypeError
    ...
try:                        # ← try 在这两行**之后**
```

**这一行抛错，整只循环就没了**——5460 只票**一只都没更新**。
上层 `run_step` 把它记成 `partial` 然后**继续出单**，
于是名单建立在**陈旧数据**上，而且名单本身没有任何地方说明它是陈旧的。

`601607.SH mom20=903.72%` 就是这份陈旧数据里还带着复权因子污染的证据
（`docs/INCIDENTS` 里记的那次 `backAdjustFactor` 事故）。

## 两条防线

1. **单票隔离**：读表、算 `last`、同步、复权因子——全在 `try` 里。
   单票坏掉只影响它自己，计数上报，绝不中断全市场。
2. **数据截止日必须出现在出单里**：`[predict] 数据截至 YYYY-MM-DD`。
   陈旧不陈旧，用户一眼能看见；这是「安静的错误输出」的反面。
"""

import pandas as pd
import pytest


def _run_update(tmp_path, monkeypatch, bad_codes=(), empty_codes=()):
    """跑 daily_update.update_all（纯函数化后），返回统计。"""
    from scripts import daily_update as du

    root = tmp_path / "master" / "daily"
    root.mkdir(parents=True)
    for code in bad_codes:
        pd.DataFrame({"date": [pd.NA], "close": [1.0]}).to_parquet(root / f"{code}.parquet")
    for code in empty_codes:
        pd.DataFrame({"date": [], "close": []}).to_parquet(root / f"{code}.parquet")
    for i in range(3):
        pd.DataFrame({"date": ["2026-10-09", "2026-10-08"], "close": [10.0, 9.9]}
                     ).to_parquet(root / f"60000{i}.SH.parquet")

    synced = []

    class FakePipeline:
        def __init__(self, *a, **k):
            pass

        def sync_daily(self, code, start, end):
            synced.append((code, start, end))
            return type("R", (), {"status": "ok", "reason": ""})()

    class FakeSource:
        def fetch_adjust_factor(self, code, start, end):
            return pd.DataFrame()

    monkeypatch.setattr(du, "SyncPipeline", FakePipeline)
    monkeypatch.setattr(du, "default_sources", lambda: [FakeSource()])
    monkeypatch.setattr(du, "LocalStore", lambda r: type("S", (), {"root": tmp_path})())
    monkeypatch.setattr(du.time, "sleep", lambda s: None)
    return du.update_all(tmp_path, "2026-10-12"), synced


def test_one_bad_stock_does_not_stop_the_market(tmp_path, monkeypatch):
    """一只 date 全 NaN 的票，不能让另外几只**一只都不更新**。"""
    stats, synced = _run_update(tmp_path, monkeypatch,
                                bad_codes=("000001.SZ",))
    assert synced, f"一只坏票就让全市场停更了（stats={stats}）"
    assert len(synced) >= 3


def test_empty_parquet_does_not_stop_the_market(tmp_path, monkeypatch):
    """空表 `date.max()` 返回 nan → 同样炸。"""
    stats, synced = _run_update(tmp_path, monkeypatch,
                                empty_codes=("600519.SH",))
    assert len(synced) >= 3, f"空表让全市场停更了（stats={stats}）"


def test_bad_stocks_are_counted_not_swallowed(tmp_path, monkeypatch):
    """坏票必须**计数上报**——静默跳过会让「数据全好」看起来一样。"""
    stats, _ = _run_update(tmp_path, monkeypatch,
                           bad_codes=("000001.SZ",), empty_codes=("600519.SH",))
    assert stats["unreadable"] == 2, stats


def test_sync_start_is_the_last_stored_date(tmp_path, monkeypatch):
    """起点必须是库内最后日期（含），这样末根缺列能���补上。"""
    _, synced = _run_update(tmp_path, monkeypatch)
    assert synced[0][1] == "2026-10-09", synced[0]


def test_a_stock_already_current_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "master" / "daily"
    root.mkdir(parents=True)
    pd.DataFrame({"date": ["2026-10-12"], "close": [10.0]}
                 ).to_parquet(root / "600000.SH.parquet")
    from scripts import daily_update as du
    synced = []

    class FakePipeline:
        def __init__(self, *a, **k):
            pass

        def sync_daily(self, code, start, end):
            synced.append(code)
            return type("R", (), {"status": "ok", "reason": ""})()

    class FakeSource:
        def fetch_adjust_factor(self, code, start, end):
            return pd.DataFrame()

    monkeypatch.setattr(du, "SyncPipeline", FakePipeline)
    monkeypatch.setattr(du, "default_sources", lambda: [FakeSource()])
    monkeypatch.setattr(du, "LocalStore", lambda r: type("S", (), {"root": tmp_path})())
    monkeypatch.setattr(du.time, "sleep", lambda s: None)
    stats = du.update_all(tmp_path, "2026-10-12")
    assert synced == []
    assert stats["up_to_date"] == 1


# --- 写库互斥（2026-10-12 补）------------------------------------------------
#
# 事故：`daily_update.py` 原先**一点锁都没有**，而 `backfill_gaps` /
# `repair_tail_cols` 各自手搓一份 `if lock.exists()` 的判断。撞上时两个进程
# 同时 concat + to_parquet 同一批文件，后写覆盖先写，丢的列和行没人知道。
# 现在统一走 `aplus.write_lock`。


def test_update_all_refuses_when_lock_held(tmp_path):
    """锁被占时，update_all 必须拒绝启动，而不是照跑不误。"""
    import os
    from aplus.write_lock import LockBusy
    from scripts import daily_update as du

    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text(str(os.getpid()))          # 自己持锁 = 重入

    with pytest.raises(LockBusy):
        du.update_all(tmp_path, "2026-10-12")


def test_update_all_takes_and_releases_lock(tmp_path, monkeypatch):
    """正常路径：锁被拿到，跑完释放。"""
    from scripts import daily_update as du
    (tmp_path / "master" / "daily").mkdir(parents=True)
    lock_path = tmp_path / "bootstrap.lock"

    class FakePipeline:
        def __init__(self, *a, **k):
            pass

        def sync_daily(self, code, start, end):
            return type("R", (), {"status": "ok", "reason": ""})()

    class FakeSource:
        def fetch_adjust_factor(self, code, start, end):
            return pd.DataFrame()

    monkeypatch.setattr(du, "SyncPipeline", FakePipeline)
    monkeypatch.setattr(du, "default_sources", lambda: [FakeSource()])
    monkeypatch.setattr(du, "LocalStore", lambda r: type("S", (), {"root": tmp_path})())
    monkeypatch.setattr(du.time, "sleep", lambda s: None)

    du.update_all(tmp_path, "2026-10-12")
    assert not lock_path.exists(), "跑完必须放锁，否则明晚就锁死了"


def test_update_all_releases_lock_even_when_pipeline_explodes(tmp_path, monkeypatch):
    """崩了也要放锁——否则一次异常锁死全市场，第二天 18:30 静默跳过更新。"""
    from scripts import daily_update as du
    (tmp_path / "master" / "daily").mkdir(parents=True)
    # 必须是**能读的** parquet：读不了的会被单票隔离吃掉，根本到不了 sync_daily
    pd.DataFrame({"date": ["2026-10-09"], "close": [10.0]}).to_parquet(
        tmp_path / "master" / "daily" / "000001.SZ.parquet")
    lock_path = tmp_path / "bootstrap.lock"

    class Boom:
        def __init__(self, *a, **k):
            pass

        def sync_daily(self, code, start, end):
            raise KeyboardInterrupt("硬中断")

    class FakeSource:
        def fetch_adjust_factor(self, code, start, end):
            return pd.DataFrame()

    monkeypatch.setattr(du, "SyncPipeline", Boom)
    monkeypatch.setattr(du, "default_sources", lambda: [FakeSource()])
    monkeypatch.setattr(du, "LocalStore", lambda r: type("S", (), {"root": tmp_path})())
    monkeypatch.setattr(du.time, "sleep", lambda s: None)

    with pytest.raises(KeyboardInterrupt):
        du.update_all(tmp_path, "2026-10-12")
    assert not lock_path.exists(), "硬中断也必须放锁"
