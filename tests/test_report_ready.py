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

# ── 2026-10-15：副产物比主产物新 ⇒ 这一轮在中途死了 ──────────────────

def test_a_newer_sibling_means_the_run_died_midway(tmp_path):
    """★ 本次事故的形状：`.daily.parquet` 落了，`.json` 没落。

    事故经过：`backtest_walk_forward_weights.py` 先落 parquet、后落 JSON，
    两行之间一句 `sum(... for r in results)` 抛 `NameError`
    （`results` 从未定义，逐年的结果叫 `rows`）。

    结果是目录里**有**这一轮的文件。任何用「最新 mtime」判断
    「这轮跑完了」的办法都会判成跑完了。
    """
    from aplus.report_ready import require_run_completed

    main = tmp_path / "r.json"
    sib = tmp_path / "r.daily.parquet"

    # 副产物先落
    sib.write_text("x", encoding="utf-8")
    main.write_text("{}", encoding="utf-8")
    # ⚠️ 时间戳必须相对**当下**构造，不能写字面量。
    # 第一版写了 `os.utime(sib, (3000, 3000))` —— 那是 1970 年，
    # 而主文件的 mtime 是「现在」（~1.7e9），于是副产物变成**更旧**，
    # 守卫判「跑完」是对的，测试却以为它错。
    # ⇒ **测试写错时，症状与守卫有洞一模一样：一条红。**
    #    这个形状只有靠「先读失败值」才能分开（失败值会说明谁新谁旧）。
    import os, time
    now = time.time()
    os.utime(sib, (now - 100, now - 100))    # sib 更早 → 正常跑完
    os.utime(main, (now, now))
    assert require_run_completed(main).ok

    os.utime(sib, (now + 100, now + 100))    # sib 更新 → 中途死了
    r = require_run_completed(main)
    assert not r.ok, "副产物更新却判成跑完 —— 这正是本次事故的漏判"
    assert "中途死" in r.reason, r.reason


def test_missing_main_report_is_not_satisfied_by_a_fresh_sibling(tmp_path):
    """★ 主文件**根本不存在**时，旁边的副产物不能顶替它。

    这是最容易出事的形态：`require_keys` 的用途是「主文件在、内容不齐」，
    缺文件是另一回事。混在一起 ⇒ 上一次读到的就是**上一轮**的报告
    （本模块 docstring 里记的那次事故）。
    """
    from aplus.report_ready import require_keys, require_run_completed

    (tmp_path / "r.daily.parquet").write_text("x", encoding="utf-8")
    main = tmp_path / "r.json"

    assert not require_run_completed(main).ok
    r = require_keys(main, ["years"])
    assert not r.ok and "不存在" in r.reason


def test_the_guard_reports_which_side_is_older(tmp_path):
    """报错必须指出**谁新谁旧**。「没跑完」三个字查不动。"""
    import os

    from aplus.report_ready import require_run_completed

    main = tmp_path / "r.json"
    sib = tmp_path / "r.daily.parquet"
    main.write_text("{}", encoding="utf-8")
    sib.write_text("x", encoding="utf-8")
    os.utime(main, (2000, 2000))
    os.utime(sib, (3000, 3000))
    reason = require_run_completed(main).reason
    assert "r.daily.parquet" in reason and "r.json" in reason, reason
