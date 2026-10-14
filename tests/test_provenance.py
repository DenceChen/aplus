"""报告溯源守卫。

## 为什么要有它

2026-10-14 我读了 11:45 生成的 `backtest_summary.json`，
告诉用户「信号符号反了」；而 14:02 的 commit 修掉了市场基准污染
（虚增约 114 个百分点/年）。**那份报告当时已经作废。**

数字看着完全正常——没有报错、没有异常字段。
**只有「生成时间 vs 之后的相关改动」这一个问题能揭穿它。**

所以这里的测试重点不是「功能能用」，而是
**它必须在真实失效场景下喊出来，而且不能对无关改动喊。**
"""
import json
import subprocess
from pathlib import Path

import pytest

from aplus import provenance as P


# ── 测试夹具：一个真的 git 仓库 ──────────────────────────────────────

@pytest.fixture
def repo(tmp_path):
    """建一个真仓库——用假 git 调用测不出「相对 HEAD」是否算对了。"""
    r = tmp_path / "repo"
    (r / "aplus").mkdir(parents=True)
    (r / "reports").mkdir()
    for f in ("git init -q", "git config user.email t@t", "git config user.name t"):
        subprocess.run(f, shell=True, cwd=r, check=True, capture_output=True)

    def commit(msg: str):
        subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=r, check=True,
                       capture_output=True)
        return P.code_sha(r)

    (r / "aplus" / "backtest.py").write_text("# v1\n")
    sha1 = commit("first")
    return {"root": r, "commit": commit, "sha1": sha1}


def _report(repo, name="backtest_summary.json"):
    p = repo["root"] / "reports" / name
    p.write_text(json.dumps({"hit_rate_up": 0.44}), encoding="utf-8")
    return p


# ── 核心：能不能揭穿「报告早于相关改动」 ─────────────────────────────

def test_a_report_generated_before_a_change_to_its_generator_is_stale(repo):
    """**这条是整个模块存在的理由。**

    报告在 sha1 生成，之后有人改了 backtest.py → 必须报 stale。
    """
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    (repo["root"] / "aplus" / "backtest.py").write_text("# v2 修了基准污染\n")
    repo["commit"]("fix: 基准混入未上市股票")

    got = P.freshness(p, root=repo["root"])
    assert got["verdict"] == "stale"
    assert got["commits"], "stale 必须给出是哪几次提交，否则无从重跑"
    assert got["trustworthy"] is False


def test_a_report_with_no_later_change_is_fresh(repo):
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    assert P.freshness(p, root=repo["root"])["verdict"] == "fresh"


def test_an_unrelated_change_does_not_invalidate_the_report(repo):
    """**这是让告警有意义的那一半。**

    改了 `vintage.py` 不该让 `backtest_summary.json` 报红。
    若 HEAD 每动一次就全盘报警，红色会立刻退化成背景噪声——
    和「永远红的测试」是同一种失败：教会所有人忽略告警。
    """
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    (repo["root"] / "aplus" / "vintage.py").write_text("# 无关的改动\n")
    repo["commit"]("feat(vintage): 快照")

    assert P.freshness(p, root=repo["root"])["verdict"] == "fresh"


# ── 未知 ≠ 新鲜 ─────────────────────────────────────────────────────

def test_a_report_with_no_sidecar_is_unknown_not_fresh():
    """**现存的老报告都没有 sidecar。**

    把它们报成 fresh 会让「不知道来源」看起来像「已确认没问题」——
    而那正是最容易让人直接引用一个坏数字的状态。
    `unknown` 的含义是**不许引用**。
    """
    got = P.freshness(Path("/nonexistent/x.json"), generator="aplus/backtest.py",
                      root=".")
    assert got["verdict"] == "unknown"
    assert got["trustworthy"] is False
    assert "sidecar" in got["reason"]


def test_an_unregistered_report_is_unknown_with_an_actionable_reason():
    """不在映射表里 → unknown，且**告诉人去哪儿补一行**。

    静默 unknown 会让人以为「不管它也没事」；
    带指路的 unknown 才是待办。
    """
    p = Path("/tmp/x/unmapped_report.json")
    got = P.freshness(p, generator=None, root=".")
    assert got["verdict"] == "unknown"
    assert "DEFAULT_GENERATORS" in got["reason"]


def test_every_reason_is_reported_not_just_the_first_one():
    """**只报第一个原因，等于把能修的那条藏起来。**

    又老（没 sidecar）又没登记来源的报告，两个问题都要说：
    只报「没有 sidecar」的话，人补完 sidecar，下次还是 unknown，
    而且这次连「去补一行」这个线索都没了。
    """
    got = P.freshness(Path("/tmp/x/unmapped_report.json"), generator=None,
                      root=".")
    assert len(got["problems"]) == 2, got["problems"]
    assert any("sidecar" in r for r in got["problems"])
    assert any("DEFAULT_GENERATORS" in r for r in got["problems"])


def test_a_stamp_without_a_sha_cannot_support_any_verdict():
    """手工写了个空壳 sidecar 不等于有溯源。"""
    p = Path("/tmp/x/r.json")
    sc = P.sidecar_path(p)
    sc.parent.mkdir(parents=True, exist_ok=True)
    sc.write_text(json.dumps({"generator": "aplus/backtest.py"}), encoding="utf-8")
    assert P.freshness(p, generator="aplus/backtest.py",
                       root=".")["verdict"] == "unknown"


def test_a_baseless_stamp_does_not_claim_freshness(repo):
    """`since` 为 None（sha 不是本仓库的 commit）时必须返回空列表，
    而不是「没变化」——后者会让无法判断看起来像确认无误。"""
    assert P.commits_touching(repo["root"], "aplus/backtest.py", None) == []


# ── 脏工作区：sha 描述不了真实状态 ──────────────────────────────────

def test_a_dirty_worktree_at_stamp_time_is_flagged(repo):
    """脏的时候 sha 只描述已提交部分，**描述不了真实代码**。

    `fresh` 在这种情况下不可靠——必须让调用方看见。
    """
    p = _report(repo)
    (repo["root"] / "aplus" / "backtest.py").write_text("# 未提交的改动\n")
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    got = P.freshness(p, root=repo["root"])
    assert got["dirty_at_stamp"] is True
    assert got["trustworthy"] is False, (
        "脏工作区下 sha 不足以标识代码状态，fresh 结论必须标为不可信")


# ── 排序：已知的坏要排在「不知道」前面 ──────────────────────────────

def test_scan_puts_known_bad_ahead_of_unknown(repo):
    """**排序不是装饰。**

    `stale` 是**已知的坏**，`unknown` 是**不知道**。
    只按文件名排序会把两者混在一起，人只看前几行，
    最要命的那条就永远没被看到。

    用真仓库做 root，而不是 tmp 目录——否则 stamp 记的是当前 HEAD，
    而 HEAD 之后没有新提交，造不出 stale，测试会**因为自己的错误**
    报「排序错���」，把真 bug 藏在噪音里。
    """
    d = repo["root"] / "reports"
    for n in ("backtest_summary.json", "zzz_unknown.json", "mmm_unknown.json"):
        (d / n).write_text("{}", encoding="utf-8")
    P.stamp(d / "backtest_summary.json", "aplus/backtest.py", root=repo["root"])
    (repo["root"] / "aplus" / "backtest.py").write_text("# v2\n")
    repo["commit"]("fix: 让它变 stale")

    rows = P.scan(d, root=repo["root"])
    assert rows[0]["report"] == "backtest_summary.json", "已知的坏必须排在最前"
    assert rows[0]["verdict"] == "stale"
    verdicts = [r["verdict"] for r in rows]
    assert verdicts.count("unknown") == 2
    # stale 只出现一次且在最前，其余按名字升序
    assert verdicts == ["stale", "unknown", "unknown"]
    assert [r["report"] for r in rows[1:]] == ["mmm_unknown.json",
                                                "zzz_unknown.json"]


def test_the_summary_separates_the_three_verdicts(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "a.json").write_text("{}", encoding="utf-8")
    s = P.summary(d, root=".")
    assert s["n"] == 1 and s["unknown"] == ["a.json"]


# ── sidecar 不污染报告本身 ──────────────────────────────────────────

def test_the_sidecar_does_not_touch_the_report_file(repo):
    """报告的 schema 是下游在读的。往里塞字段会污染它，
    也会在 `sha256` 比对时炸掉——溯源必须写在旁边。"""
    p = _report(repo)
    before = p.read_text(encoding="utf-8")
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    assert p.read_text(encoding="utf-8") == before
    assert P.sidecar_path(p).is_file()


def test_the_generator_table_covers_the_reports_people_actually_cite():
    """**表外的报告会被报成 unknown。**

    那比「默默放行」安全，但漏登记会让人以为没人管。
    所以这里钉住最常被引用的那几份确实在表里。
    """
    for name in ("backtest_summary.json", "forward_registry.json",
                 "lowfreq_plausibility.json"):
        assert name in P.DEFAULT_GENERATORS, f"{name} 漏登记来源"

def test_scan_does_not_count_its_own_sidecars_as_reports(repo):
    """**告警不许自己制造告警。**

    sidecar 也以 `.json` 结尾，首版 `glob("*.json")` 会把它扫成一份
    「没有 sidecar 的 unknown 报告」——每盖一次章就多一份噪音。
    这种守卫上线几天就会被静音，而静音的守卫等于没有守卫。
    """
    d = repo["root"] / "reports"
    p = d / "backtest_summary.json"
    p.write_text("{}", encoding="utf-8")
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    assert P.sidecar_path(p).is_file(), "sidecar 必须真的落盘，否则这测的不是它"

    names = [r["report"] for r in P.scan(d, root=repo["root"])]
    assert names == ["backtest_summary.json"]
