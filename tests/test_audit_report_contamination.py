"""基准污染引用面审计。

## 这个脚本最该被钉住的一条

切割点**必须从代码推导**（`git log -S`），**不许退回硬编码时间戳**。

退回的那一刻是最危险的：下一次基准被修好、脚本却还按旧时间切，
于是**新报告被判成干净**。错的方向恰好是「继续引用坏数字」。

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


# ── 派生字段：按分母判定，不按字段名判定 ──────────────────────────

def test_a_derived_market_metric_is_still_caught():
    """**「没扫到字段名」不等于「没有污染」——这是审计今天漏掉自己化石的原因。**

    `reversal_freq_2019_2023.json` 里没有 `up_excess_vs_market`，只有
    `annual_excess`（= up_excess_vs_market × 244）和 `net`（再减成本）。
    污染一点没少，只是换了名字。原版审计因此把这份报告判成无辜。
    """
    obj = {"rows": [{"name": "纯 20 日反转", "annual_excess": -0.2169,
                     "net": -0.3385, "annual_cost": 0.1222}]}
    keys = A.carries_market_metrics(obj)
    assert "annual_excess" in keys
    assert "net" in keys


def test_a_market_free_excess_is_not_swept_in_with_the_derived_ones():
    """**反方向同样要钉住，否则守卫会变成大扫帚。**

    `bond_excess_analysis.json` 的 `excess_ann` 分母是「等权持有当期合格
    宇宙全池」——那是策略自己的对照组，不是被污染的 A 股市场基准。
    把它染红，就是又一次「误伤」，而误伤的成本是守卫被静音。
    """
    obj = {"benchmark": {"name": "ew_pool"},
           "segments": {"full": {"excess_ann": 0.1139}}}
    assert A.carries_market_metrics(obj) == []


def test_a_metric_with_proven_immunity_must_not_be_red_flagged():
    """**全项目最强的一条证据，最容易被误伤，所以要单独钉住。**

    `excess_breadth = up_excess − down_excess = (up−mkt) − (down−mkt)
    = up − down`：市场项在减法里**自动抵消**。所以它是全项目唯一
    不依赖基准的可信锚，也是「排序方向对不对」唯一能看的指标。

    2026-10-14 实测：污染前后同为 −4.65，逐位相同。
    若哪天有人「顺手」把 `breadth_t` 加进污染字段，这份守卫就会
    把唯一不受污染影响的证据作废——**代价远大于多报一份报告。**
    """
    obj = {"rows": [{"breadth_t": -4.65, "hit": 0.47, "days": 1154}]}
    assert A.carries_market_metrics(obj) == []
    assert "breadth_t" in A.NOT_MARKET_DERIVED, \
        "免疫证明要留在代码里，否则下一个人凭什么知道它免疫？"


def test_the_same_key_name_can_mean_different_denominators():
    """`excess` 这个名字有两副面孔：`_verify_reversal` 与 `champion_health`
    里它来自 `up_excess_vs_market`（受污染），`_diag_mom121` 里它来自横截面
    等权均值（不受污染）。所以登记的是**分母来源**，不是名字。"""
    assert "excess" in A.MARKET_DERIVED, \
        "excess 在冠军健康度与独立验证里都以 A 股市场为分母，必须登记"
    assert "excess_ann" not in A.MARKET_DERIVED, \
        "excess_ann 的分母是等权池，登记它就是误伤"


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
    """报告干净、也被引用 → 不是问题。审计不该把所有引用都染红，
    否则红名单三天后就没人看了——**静音的守卫等于没有守卫**。"""
    d = tmp_path / "reports"
    d.mkdir()
    (d / "fresh_race.json").write_text(
        json.dumps({"up_excess_vs_market": 0.01}), encoding="utf-8")
    # **三档判定下，「干净」是有条件的**：光有 mtime 不算干净，
    # 必须在溯源章里自报面板指纹。所以这里连 sidecar 一起写。
    (d / "fresh_race.json.provenance.json").write_text(
        json.dumps({"generator": "aplus/backtest.py",
                    "panel_market_mean": 0.00066}), encoding="utf-8")
    (d / "forward_registry.json").write_text(
        json.dumps([{"registered_on": "2026-10-12", "evidence": "fresh_race.json"}]),
        encoding="utf-8")
    import os, time
    future = time.time() + 10_000
    os.utime(d / "fresh_race.json", (future, future))
    rep = A.audit(d, root=Path("."))
    assert rep["n_clean"] == 1
    assert rep["contaminated_and_cited"] == []


def test_a_post_fix_report_without_a_fingerprint_is_unverified_not_clean(tmp_path):
    """**这是假重跑留下的坑，也是三档判定的全部理由。**

    mtime 晚于修复、报告里也确实有市场指标——看起来完全干净。
    但它没有溯源章，章里没有面板指纹，所以我**判不了它是干净还是脏**。

    如果只有「干净/作废」两档，它会被归到干净那一侧，然后被注册表引用，
    然后所有引用它的结论都继承一个我根本没验证过的前提。
    第三档 `unverified` 就是把它显式拎出来：**不知道 ≠ 没事**。
    """
    d = tmp_path / "reports"
    d.mkdir()
    (d / "fresh_but_unproven.json").write_text(
        json.dumps({"up_excess_vs_market": 0.01}), encoding="utf-8")
    (d / "forward_registry.json").write_text(
        json.dumps([{"registered_on": "2026-10-12",
                     "evidence": "fresh_but_unproven.json"}]),
        encoding="utf-8")
    import os, time
    future = time.time() + 10_000
    os.utime(d / "fresh_but_unproven.json", (future, future))

    rep = A.audit(d, root=Path("."))
    assert rep["n_clean"] == 0, "没有指纹就不许自称干净"
    assert rep["n_unverified"] == 1
    assert rep["unverified"][0]["report"] == "fresh_but_unproven.json"
    assert rep["contaminated_and_cited"][0]["report"] == "fresh_but_unproven.json"


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
