"""守卫的「等在飞的那跑」必须**真的等**。

## 抓到的 bug（2026-10-21 19:30 实测）

19:30 的守卫打出：

```
今天已有一跑 run_daily 在飞（run_id=183000-edf2cf，始于 2026-10-21 18:30:00）
  ⇒ 等它跑完再看（并发 = 重复发布 + 白烧一轮 CPU）
⚠️ 等了 7200s 那一跑仍未结束 ⇒ 本守卫不补跑
```

⇒ **它一秒都没等**（19:30 进来、19:31 就说「等了 7200s」）
⇒ ⇒ **那句话是假的**，而**安全网是断的**：
18:30 那班若失败，守卫会**立刻放弃而不补跑**。

## 根因

`scripts/guard_daily.py::_wait_for_run` 的原实现：

```python
if row.get("run_id") == run_id:
    return row.get("status") if row.get("finished") else None
```

⇒ **找到那条 run、发现它还没结束 ⇒ 立刻返回 `None`（放弃）**。

## 为什么没有任何东西会红

既有那条守卫（`tests/test_daily_update_resilience.py`）把
`_wait_for_run` **整个 monkeypatch 掉了** ⇒ ⇒ **真函数从没被跑过**
⇒ ⇒ 「等」这个行为**零覆盖**。

⇒ 本文件**直接调真函数**（只把「读运行状态」那一半换成假的，
⇒ 这样能控制它什么时候结束，而**等的逻辑本身是真的**）。
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "guard_daily_wait_guard", ROOT / "scripts" / "guard_daily.py")
GD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GD)


def test_the_wait_keeps_polling_until_the_run_finishes(tmp_path, monkeypatch):
    """🚨 找到那条 run 但它**还没结束** ⇒ 必须**继续等**（不是放弃）。"""
    polls = {"n": 0}

    def fake_runs(data_root):
        polls["n"] += 1
        if polls["n"] < 4:
            return [{"run_id": "x", "finished": False, "status": None}]
        return [{"run_id": "x", "finished": True, "status": "ok"}]

    monkeypatch.setattr(GD, "_daily_runs", fake_runs)
    t0 = time.time()
    got = GD._wait_for_run("x", tmp_path, budget_s=5.0, poll_s=0.05)
    waited = time.time() - t0

    assert got == "ok", (
        f"没等到跑完就返回了 {got!r}（只轮询了 {polls['n']} 次）"
        "⇒ 它是**第一次就放弃**了 ⇒ 安全网是断的")
    assert polls["n"] >= 4, (
        f"只轮询了 {polls['n']} 次 ⇒ 它没有继续等")
    assert waited >= 0.1, (
        f"只花了 {waited:.3f}s ⇒ 它根本没在等（消息里那句「等了 Ns」是假的）")


def test_the_wait_returns_none_only_after_the_budget_is_exhausted(tmp_path, monkeypatch):
    """反向正控：那一跑**一直不结束** ⇒ 超时后返回 `None`（且**真的等满了**）。"""
    monkeypatch.setattr(GD, "_daily_runs",
                        lambda dr: [{"run_id": "x", "finished": False, "status": None}])
    t0 = time.time()
    got = GD._wait_for_run("x", tmp_path, budget_s=0.3, poll_s=0.05)
    waited = time.time() - t0
    assert got is None, f"预算还没用完就返回了 {got!r}"
    assert waited >= 0.25, f"只等了 {waited:.3f}s ⇒ 预算没用满就放弃了"


def test_the_wait_ignores_other_runs(tmp_path, monkeypatch):
    """**只等自己那一条** ⇒ 别的 run 结束不算它结束。"""
    polls = {"n": 0}

    def fake_runs(data_root):
        polls["n"] += 1
        if polls["n"] < 3:
            return [{"run_id": "other", "finished": True, "status": "ok"}]
        return [{"run_id": "x", "finished": True, "status": "ok"}]

    monkeypatch.setattr(GD, "_daily_runs", fake_runs)
    got = GD._wait_for_run("x", tmp_path, budget_s=5.0, poll_s=0.05)
    assert got == "ok"
    assert polls["n"] >= 3, "别的 run 结束了就当自己结束了 ⇒ 提前返回"
