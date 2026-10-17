"""`step_incremental_update` 必须真正**持锁**，而不是只 `exists()` 预检查。

## 事故形状

`run_daily.py` 原先两处都写成：

```python
lock = Path(data_root) / "bootstrap.lock"
if lock.exists():
    print("[update] 全市场回填进行中，跳过增量")
    return
```

检查有了，**握没有**。这个函数自己会调 `pipeline.sync_daily` 写 5460 个
parquet，却全程不持锁。检查与写入之间隔着一整个网络往返：
在那个窗口里 `repair_tail_cols` / `backfill_gaps` 拿到锁并开始改写，
两边就同时在 `concat + to_parquet` 同一批文件，后写覆盖先写，
丢的列和行没有任何人会知道。

而这条路径是 **launchd 每天 18:30 无人在看地跑**的——
出问题时既没有告警，也没有人会发现名单建立在一半损坏的数据上。

## 预测路径只需读锁

`step_predict` 只读不写，`exists()` 预检查对它是足够的（读到一个正在
被改写的文件会横截面不可信，所以宁可跳过）。两处都用同一份 `WriteLock`
判定，避免「锁」这个词在两个地方指两件事。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import run_daily as rd  # noqa: E402
from aplus.write_lock import lock_holder  # noqa: E402


class _FakeStore:
    def __init__(self, root):
        self.root = root


def _seed(root: Path, n=2):
    daily = root / "master" / "daily"
    daily.mkdir(parents=True)
    for i in range(n):
        pd.DataFrame({"date": ["2026-10-09", "2026-10-08"], "close": [10.0, 9.9]}
                     ).to_parquet(daily / f"60000{i}.SH.parquet")


def test_update_step_holds_lock_while_syncing(tmp_path, monkeypatch):
    """同步进行中，锁必须在场——这是本测试存在的全部理由。"""
    _seed(tmp_path)
    lock_path = tmp_path / "bootstrap.lock"
    seen = {}

    class FakePipeline:
        def __init__(self, *a, **k):
            pass

        def sync_daily(self, code, start, end):
            # 关键瞬间：正在写库的那一刻，锁必须被别人看得见
            seen[code] = lock_holder(lock_path)
            return type("R", (), {"status": "ok", "reason": ""})()

    monkeypatch.setattr(rd, "SyncPipeline", FakePipeline)
    monkeypatch.setattr(rd.time, "sleep", lambda s: None)

    rd.step_incremental_update(_FakeStore(tmp_path), [], "2026-10-12", tmp_path)
    assert seen, "根本没同步过，测试无效"
    assert all(v is not None for v in seen.values()), \
        f"同步时锁不在场，会与回填并发写同一批文件：{seen}"
    assert not lock_path.exists(), "跑完必须放锁，否则明晚就锁死了"


def test_update_step_declines_when_lock_busy(tmp_path, monkeypatch):
    """锁被别人占着时不许开跑，也不许崩——安静跳过并说明原因。

    ⚠️ P0（2026-10-17）：锁占用现在是 `raise StepSkipped` 而不是裸 `return`。
    裸 return 会被 `run_step` 记成 `"ok"` ⇒ `status="ok"`
    ⇒ 守卫打「已成功执行，无需补跑」，而增量一步都没做。
    ⇒ 三态语义见 `tests/test_run_daily_step_status.py`。

    ⚠️ 断言**必须在 `with` 之外**：放进去就永远不执行，
    而「测试通过」与「测试什么都没测」在门禁输出里长得一模一样。
    """
    _seed(tmp_path)
    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text("999999")
    called = []

    class FakePipeline:
        def __init__(self, *a, **k):
            pass

        def sync_daily(self, code, start, end):
            called.append(code)
            return type("R", (), {"status": "ok", "reason": ""})()

    monkeypatch.setattr(rd, "SyncPipeline", FakePipeline)
    with pytest.raises(rd.StepSkipped):
        rd.step_incremental_update(_FakeStore(tmp_path), [], "2026-10-12", tmp_path)
    assert called == [], "锁被占还照样写库"
    assert lock_path.read_text() == "999999", "不许动别人的锁"


def test_predict_step_skips_when_lock_busy(tmp_path, capsys):
    """预测只读，但锁被占时同样要跳过——正在改写的文件横截面不可信。

    `load_weights` 是在 `step_predict` 函数体内 import 的，模块上没有这个名字，
    没法 monkeypatch（第一版就这么写错了）。

    ⚠️ P0（2026-10-17）：这里原来是 `is None` —— **那条断言钉住的正是缺陷**：
    裸 return ⇒ `run_step` 记 `"ok"` ⇒ `status="ok"` ⇒ 19:30 的守卫打
    「已成功执行，无需补跑」，而 D1 当日名单一个字节都没落盘。
    ⇒ 现在是 `raise StepSkipped`，断言改成「抛且被分类成 skipped」。
    """
    (tmp_path / "bootstrap.lock").write_text("999999")
    with pytest.raises(rd.StepSkipped):
        rd.step_predict_and_allocate(_FakeStore(tmp_path), tmp_path,
                                     "2026-10-12", 50000, {})
    assert "回填" in capsys.readouterr().out, "跳过必须说明原因，不能静默"


def test_update_step_releases_lock_on_failure(tmp_path, monkeypatch):
    """同步炸了也要放锁——一次崩溃会把全市场锁死到第二天。"""
    _seed(tmp_path)
    lock_path = tmp_path / "bootstrap.lock"

    class Boom:
        def __init__(self, *a, **k):
            pass

        def sync_daily(self, code, start, end):
            raise ValueError("炸了")

    monkeypatch.setattr(rd, "SyncPipeline", Boom)
    with pytest.raises(Exception):
        rd.step_incremental_update(_FakeStore(tmp_path), [], "2026-10-12", tmp_path)
    assert not lock_path.exists(), "异常路径必须放锁"
