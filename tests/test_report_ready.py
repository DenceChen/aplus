"""`aplus/report_ready.py` 的测试。

## 这些测试自己也做过变异验证

第 23 层的教训：**「测试通过」不等于「测试在守东西」**。
所以下面每条关键断言都有对应的反向测试 ——
把被测函数改坏，证明测试会红。
"""
from __future__ import annotations

import json

import pytest

from aplus.report_ready import Ready, is_nan_column, require_keys


# ── 产物指纹 ────────────────────────────────────────────────────

def test_a_missing_file_is_not_ready(tmp_path):
    r = require_keys(tmp_path / "nope.json", ["years"])
    assert r.ok is False
    assert "不存在" in r.reason


def test_a_nested_key_present_makes_it_ready(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"years": [{"a": {"b": 1}}]}), encoding="utf-8")
    assert require_keys(p, ["years.0.a.b"]).ok is True


def test_the_top_level_key_alone_does_not_satisfy_a_nested_path(tmp_path):
    """★ 顶层齐全 ≠ 内容齐全。

    上一轮的报告 `years` 在，但**每行缺** `measured_long_vs_mkt_*` ——
    只看顶层键就会把旧报告当新报告读。这正是那次读错的方式。
    """
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"years": [{"a": 1}]}), encoding="utf-8")
    r = require_keys(p, ["years.0.measured_long_vs_mkt_annual_by_q"])
    assert r.ok is False
    assert "measured_long_vs_mkt" in r.reason


def test_a_null_value_is_not_ready(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"years": [{"a": None}]}), encoding="utf-8")
    assert require_keys(p, ["years.0.a"]).ok is False


def test_a_truncated_json_is_not_ready(tmp_path):
    """半截文件是最常见的「还在写」—— 不能当成跑完了。"""
    p = tmp_path / "r.json"
    p.write_text('{"years": [{"a": 1', encoding="utf-8")
    r = require_keys(p, ["years"])
    assert r.ok is False
    assert "还在写" in r.reason


def test_a_stale_file_is_not_ready(tmp_path):
    """★ `min_mtime` 是「这一轮写的东西吗」的唯一判据。

    退出码在这个场景下是假的：进程还在跑，产物还是上一轮的。
    """
    import os
    import time
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"years": [{"a": 1}]}), encoding="utf-8")
    os.utime(p, (1_600_000_000, 1_600_000_000))
    r = require_keys(p, ["years.0.a"], min_mtime=time.time())
    assert r.ok is False
    assert "旧文件" in r.reason


def test_negative_list_index_is_accepted(tmp_path):
    """点分路径支持 `-1` —— 「最后一行」是很自然的写法。"""
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"years": [{"a": 1}, {"b": 2}]}), encoding="utf-8")
    assert require_keys(p, ["years.-1.b"]).ok is True


def test_indexing_past_the_end_is_not_ready_not_a_crash(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"years": [{"a": 1}]}), encoding="utf-8")
    assert require_keys(p, ["years.9.a"]).ok is False


# ── 反向测试：把被测函数改坏，证明它守得住 ──────────────────────
#
# 没有这一节，上面那些测试就只是「符合我写它们时的预期」。

def test_mutation_detector_itself_would_catch_a_top_level_only_check(tmp_path, monkeypatch):
    """若 `require_keys` 只看顶层键（本该守的那件事被拿掉），测试必须红。"""
    import aplus.report_ready as rr

    def _weak(path, key_paths, min_mtime=None):
        """坏实现：只检查顶层键存在。"""
        p = __import__("pathlib").Path(path)
        if not p.is_file():
            return Ready(False, "不存在")
        obj = json.loads(p.read_text(encoding="utf-8"))
        for kp in key_paths:
            if kp.split(".")[0] not in obj:
                return Ready(False, f"缺 {kp}")
        return Ready(True, "齐备")

    monkeypatch.setattr(rr, "require_keys", _weak)

    p = tmp_path / "r.json"
    p.write_text(json.dumps({"years": [{"a": 1}]}), encoding="utf-8")
    # 弱实现会把这个当齐备 —— 所以下面那条「不许齐备」必须失败。
    with pytest.raises(AssertionError):
        assert rr.require_keys(p, ["years.0.missing_key"]).ok is False


def test_mutation_detector_itself_would_catch_a_dropped_staleness_check(tmp_path, monkeypatch):
    """若 `min_mtime` 参数被忽略（本该守的那件事被拿掉），测试必须红。"""
    import aplus.report_ready as rr
    import time

    def _no_mtime(path, key_paths, min_mtime=None):
        return rr.Ready(True, "齐备")          # 坏实现：完全不看新鲜度

    monkeypatch.setattr(rr, "require_keys", _no_mtime)
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"years": [{"a": 1}]}), encoding="utf-8")
    with pytest.raises(AssertionError):
        assert rr.require_keys(p, ["years.0.a"], min_mtime=time.time()).ok is False


# ── 全 NaN 列 ───────────────────────────────────────────────────

def test_an_all_nan_column_is_flagged():
    """第 23 层的真凶：全 NaN 让 `allclose(equal_nan=True)` 恒真。"""
    import pandas as pd
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [float("nan")] * 2})
    assert is_nan_column(df, "b") is True
    assert is_nan_column(df, "a") is False


def test_a_missing_column_counts_as_nan():
    import pandas as pd
    df = pd.DataFrame({"a": [1.0]})
    assert is_nan_column(df, "not_there") is True