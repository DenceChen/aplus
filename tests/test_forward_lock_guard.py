"""前瞻验证**不许在主库被改写时建面板**（撕裂读）。

## 为什么这条要存在

两个进程会同时碰主库：

- `run_daily` 的采集步持 `bootstrap.lock` 写 5460 个 parquet（18:30 起，常到 ~20:50）
- `forward_validate`（launchd `com.aplus.forward`，21:00 与 22:30）**建面板**读同一批 parquet

⇒ 两者重叠时，面板读到的是**写了一半**的那一份 ⇒ **撕裂读**。
它**不是损坏**（文件能读、能解析、行数正常）⇒ 没有任何东西会报错，
但算出来的窗口读数是错的 ⇒ 而它会被**写进前瞻台账** ⇒
**前瞻证据被静默污染**。

⇒ `run_daily.py::predict_block_reason` **已经有**这把尺子（同一把锁），
本文件要钉的是**另一条链路也有**。

⇒ 处置：**宁可不判**（当晚跳过，等下一班）⇒ launchd 另配 22:30 的第二班。

⚠️ 这条守卫是**时间局部的**：它证明代码此刻对，管不了上一轮已经写进台账的数。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FV = importlib.util.spec_from_file_location("fv_lock_guard",
                                            ROOT / "scripts" / "forward_validate.py")
M = importlib.util.module_from_spec(FV)
FV.loader.exec_module(M)


class _Reached(Exception):
    """探针：证明代码**走到了**那一步。"""


def _fixture_root(tmp_path: Path, *, with_lock: bool) -> Path:
    """造一个够走到「锁检查」那一步的仓：台账 + 日线 + （可选）锁。"""
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    (reports / "forward_registry.json").write_text(json.dumps([{
        "registered_on": "2026-10-01",
        "note": "夹具",
        "evidence": "夹具",
        "candidates": [{"weights": {"bm": -1.0}, "rebalance_every": 63,
                        "buffer_multiple": None}],
    }], ensure_ascii=False), encoding="utf-8")
    daily = tmp_path / "master" / "daily"
    daily.mkdir(parents=True)
    pd.DataFrame({"date": ["2026-10-01", "2026-10-02"], "close": [10.0, 10.5],
                  "prev_close": [9.5, 10.0], "open": [9.8, 10.2],
                  "high": [10.2, 10.6], "low": [9.7, 10.1],
                  "volume": [100.0, 110.0], "amount": [1000.0, 1100.0],
                  "turn": [1.0, 1.1], "pctChg": [0.5, 0.5],
                  "tradestatus": [1, 1]}).to_parquet(daily / "000001.SZ.parquet")
    if with_lock:
        (tmp_path / "bootstrap.lock").write_text("held", encoding="utf-8")
    return tmp_path


def _run(tmp_path: Path, monkeypatch, *, probe):
    """跑 `main()`，用 `probe` 当作「建面板」的探针。"""
    monkeypatch.setattr(sys, "argv", [
        "forward_validate.py", "--data-root", str(tmp_path),
        "--ignore-run-status"])
    monkeypatch.setattr(M, "build_cross_sections", probe)
    return M.main()


def test_the_forward_validation_does_not_build_a_panel_while_the_store_is_being_written(
        tmp_path, monkeypatch):
    """🚨 锁在场 ⇒ **不许建面板**（撕裂读会把错数写进前瞻台账）。"""
    root = _fixture_root(tmp_path, with_lock=True)

    def boom(*a, **k):
        raise AssertionError("写库锁在场时建了面板 ⇒ 撕裂读 ⇒ 前瞻证据会被污染")

    rc = _run(root, monkeypatch, probe=boom)
    assert rc == 0, "跳过是**正常状态**（不是失败）⇒ 退出码必须是 0"


def test_the_forward_validation_does_build_a_panel_when_the_store_is_idle(
        tmp_path, monkeypatch, capsys):
    """正控：没有锁 ⇒ **必须**走到建面板那一步。

    ⚠️ 没有这条，上面那条就可能退化成「永远不建面板」的装饰
    （第 112 层：过严的筛选会把 `all()` 退化成装饰）。
    """
    root = _fixture_root(tmp_path, with_lock=False)

    def probe(*a, **k):
        raise _Reached()

    with pytest.raises(_Reached):
        _run(root, monkeypatch, probe=probe)


def test_the_skip_is_explicit_in_the_output(tmp_path, monkeypatch, capsys):
    """跳过必须**说清为什么**（不静默）：否则「没攒到证据」与「攒到了」长得一样。"""
    root = _fixture_root(tmp_path, with_lock=True)

    def boom(*a, **k):
        raise AssertionError("不该建面板")

    _run(root, monkeypatch, probe=boom)
    out = capsys.readouterr().out
    assert "bootstrap.lock" in out or "写库锁" in out, (
        f"跳过的理由没有打印出来：{out[-400:]}")
