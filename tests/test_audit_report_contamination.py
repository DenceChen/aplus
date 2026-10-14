"""基准污染引用面审计。

## 这个脚本最该被钉住的一条

切割点**必须从代码推导**（`git log -S`），**不许退回硬编码时间戳**。

退回的那一刻是最危险的：下一次基准被修好、脚本却还按旧时间切，
于是**新报告被判成干净**。错的��向恰好是「继续引用坏数字」。

所以「找不到判据」必须**报错退出**，而不是用一个默认值糊过去。
"""
import json
import subprocess
from pathlib import Path

import pytest

from scripts import audit_report_contamination as A


def _commit(root, msg, path="aplus/backtest.py", body="# v"):
    f = root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=root, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "aplus").mkdir(parents=True)
    for c in ("git init -q", "git config user.email t@t", "git config user.name t"):
        subprocess.run(c, shell=True, cwd=r, check=True, capture_output=True)
    _commit(r, "初始")
    return r


# ── 切割点：必须来自代码 ────────────────────────────────────────────

def test_the_cutoff_is_the_commit_that_added_the_listing_guard(repo):
    """先有报告，再修基准——切割点应当是**修基准那一版**，不是别的提交。"""
    _commit(repo, "无关提交", path="aplus/vintage.py", body="# 别的文件")
    _commit(repo, "修基准污染", body="# v2\nif pos == 0:\n    continue\n")
    ts, sha = A.fix_commit(repo)
    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%h", "-S",
                          "pos == 0", "--", "aplus/backtest.py"],
                         capture_output=True, text=True)
    assert sha == log.stdout.strip()


def test_a_missing_guard_is_an_error_not_a_default(repo):
    """**这条是整个脚本的底线。**

    找不到判据时若退回一个硬编码时间戳，脚本会安静地继续工作，
    而分类依据是错的——方向是「把作废的当干净」。
    **「无法判断」必须看起来像无法判断。**
    """
    # 注意：这里**不能**在正文里写出那个标记字符串，否则 `git log -S`
    # 会命中它，测试就变成在测「找得到」而不是「找不到时报错」。
    _commit(repo, "改了但没有守卫", body="# 没有任何守卫\n")
    with pytest.raises(RuntimeError) as ei:
        A.fix_commit(repo)
    assert "GUARD_MARKER" in str(ei.value) or "pos == 0" in str(ei.value)
    assert "硬编码" in str(ei.value), "错误信息要指明**不要**做什么"


# ── 分类：按内容，不按文件名 ────────────────────────────────────────

def test_detection_is_by_content_not_by_filename(tmp_path):
    """`stats.json` 这种名字完全看不出它带着完整的市场指标。

    按文件名猜会漏掉一半，而漏掉的那一半正是最容易被引用的。
    """
    obj = {"hit_rate_up": 0.5, "up_excess_vs_market": 0.001}
    assert "up_excess_vs_market" in A.carries_market_metrics(obj)
    assert A.carries_market_metrics({"hit_rate_up": 0.5}) == []


def test_nested_rows_are_searched_too():
    """赛马报告的指标在 `table` 列表的每一行里，深度不止一层。"""
    obj = {"table": [{"row": {"excess_tilt": 0.1}}]}
    assert "excess_tilt" in A.carries_market_metrics(obj)


def test_a_report_carrying_nothing_is_not_listed(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "bond_paper.json").write_text(json.dumps({"dur": 3.2}), encoding="utf-8")
    rep = A.audit(d, root=Path("."))
    assert rep["n_contaminated"] == 0 and rep["n_clean"] == 0


# ── 引用面：这才是危害落地的地方 ────────────────────────────────────

def test_a_contaminated_report_cited_by_a_registry_batch_is_flagged(tmp_path):
    d = tmp_path / "reports"
    d.mkdir()
    (d / "old_race.json").write_text(
        json.dumps({"up_excess_vs_market": 0.01}), encoding="utf-8")
    (d / "forward_registry.json").write_text(json.dumps([
        {"registered_on": "2026-10-12",
         "evidence": "old_race.json / 别的.json",
         "candidates": [{"weights": {"mom20": -1.0}, "rebalance_every": 63}]}],
        ensure_ascii=False), encoding="utf-8")

    # 报告必须**早于切割点**，否则它本来就该被判成干净——
    # 那时测试就变成在测「干净的不算污染」，与本条无关。
    import os
    cutoff, _ = A.fix_commit(Path("."))
    os.utime(d / "old_race.json", (cutoff - 3600, cutoff - 3600))

    rep = A.audit(d, root=Path("."))
    assert rep["contaminated_and_cited"], "被注册表引用的作废报告必须被抓出来"
    assert rep["contaminated_and_cited"][0]["report"] == "old_race.json"
    assert "批次1" in rep["contaminated_and_cited"][0]["cited_by"][0]


def test_a_cited_but_clean_report_is_not_flagged(tmp_path):
    """报告干净、也��被引用 → 不是问题。审计不该把所有引用都染红，
    否则红名单三天后就没人看了——**静音的守卫等于没有守卫**。"""
    d = tmp_path / "reports"
    d.mkdir()
    (d / "fresh_race.json").write_text(
        json.dumps({"up_excess_vs_market": 0.01}), encoding="utf-8")
    (d / "forward_registry.json").write_text(
        json.dumps([{"registered_on": "2026-10-12", "evidence": "fresh_race.json"}]),
        encoding="utf-8")
    import os, time
    future = time.time() + 10_000
    os.utime(d / "fresh_race.json", (future, future))
    rep = A.audit(d, root=Path("."))
    assert rep["contaminated_and_cited"] == []


def test_sidecars_are_not_counted_as_reports(tmp_path):
    """**告警不许自己制造告警。** sidecar 也以 .json 结尾。"""
    d = tmp_path / "reports"
    d.mkdir()
    (d / "x.json.provenance.json").write_text(
        json.dumps({"market_pct": 0.1}), encoding="utf-8")
    rep = A.audit(d, root=Path("."))
    assert rep["n_contaminated"] + rep["n_clean"] == 0


def test_a_missing_registry_is_not_an_error(tmp_path):
    """没有注册表就是没有引用，不是审计失败。"""
    d = tmp_path / "reports"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"market_pct": 0.1}), encoding="utf-8")
    assert A.registry_citations(d) == {}
