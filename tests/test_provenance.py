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
    报「排序错〔此字已损，MISTAKE_LOG 第 92 层，待人工确认〕」，把真 bug 藏在噪音里。
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


# ── 2026-10-15：登记表与数据指纹 ────────────────────────────────────
#
# 起因是**实测**的一条时间线，不是推演的：
#   00:21–01:01  wf_weights_backtest_k*.daily.parquet 写出
#   02:22:27     factor_attribution.json 写出
#   02:25:26     数据修复（000043.SZ / 300114.SZ 补历史）—— 晚 77 秒
# ⇒ 归因判决 `NO_LEVER` **可证地**建立在修复前的数据上。
# 而 `code_sha` 全程「新鲜」：代码没变，数据变了，
# 而章里**没有任何字段能表达这件事**。

_CITED = ("factor_width_mde.json", "factor_attribution.json")


def test_the_reports_we_actually_decide_by_are_registered():
    """**登记 ≠ 盖章 ≠ 数据新鲜**，三件事分开。

    全仓 69 份报告只有 5 份带 sidecar；而按本模块自己的规矩，
    表外的报告是 `unknown` = **不许引用** ——
    也就是说当时全仓最要害的两份判决是「不可引用」的，且没人喊。
    """
    for name in _CITED:
        assert name in P.DEFAULT_GENERATORS, f"{name} 漏登记来源"
        assert P.resolve_generator(name), f"{name} 解析不出生成器"


def test_a_new_k_resolves_without_adding_one_entry():
    """★ 逐档产物是**同一模块**的第 6~10 份输出。按精确文件名登记的话，
    **新增一档就又漏登记**（CLAUDE.md 形状 17：同一语义被处理 N−1 次）。

    ⇒ 必须钉住「加一档不用改代码」这个性质，而不只是「现有几档都在表里」。
    """
    assert P.resolve_generator("wf_weights_backtest_k7.json") == \
        P.resolve_generator("wf_weights_backtest_k5.json"), \
        "★ 新增一档必须自动落到同一个生成器上，否则就是下一次漏登记"

    # 反向：模式不许过宽
    for bad in ("wf_weights_backtest_k.json", "wf_weights_backtest_kX.json",
                "wf_weights_backtest_k21.daily.json"):
        assert P.resolve_generator(bad) is None, \
            f"★ {bad} 不是逐档报告，不该被模式匹配上"


def test_registered_is_not_enough_the_writer_must_actually_stamp():
    """★ 第 6 种形状：「看起来接好了，实际焊死」。

    登记只是让 `freshness` **能判**；真的要有 sidecar，
    写报告的脚本必须调 `stamp()`。两者之间没有任何自动通路。

    ⇒ 用 **AST 找调用点**，不用子串：子串会被注释与文档满足（第 13 种形状），
      而 `index.html`/本文件里到处是「盖章」「溯源」这些词。
    """
    import ast

    root = Path(P.__file__).resolve().parent.parent
    for name in _CITED:
        gen = P.resolve_generator(name)
        src = (root / gen).read_text(encoding="utf-8")
        tree = ast.parse(src)
        # 收**每一次** `*.stamp(...)` 调用的全部字符串字面量实参。
        # ⚠️ 第一版只收 `args[0]`，而真实形状是 `stamp(路径, "生成器")` ——
        #    首参是路径不是常量 ⇒ 守卫**永远红**，且红得毫无信息量
        #    （又是一条「红的原因不对」的守卫）。
        seen: list[str] = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "stamp"):
                seen += [a.value for a in node.args
                         if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        assert gen in seen, (
            f"★ {gen} 已登记为 {name} 的生成器，但它**没有带着这个生成器调 stamp()**"
            f"（AST 里 stamp 收到的字符串字面量只有 {seen}）"
            f"—— 登记会让 sidecar 仍然是空的")


def _data_repo(tmp_path):
    """带一个真的数据根的仓库——没有它 `data_fp` 恒为 None，测不出东西。"""
    r = tmp_path / "repo"
    (r / "data" / "master" / "daily").mkdir(parents=True)
    (r / "reports").mkdir()
    for c in ("a", "b"):
        (r / "data" / "master" / "daily" / f"{c}.parquet").write_bytes(b"x")
    return r


def test_a_stamp_carries_the_data_fingerprint(tmp_path):
    """★ 数据指纹**默认写入**，不是可选参数。

    可选 = 下一个报告会忘，而「忘了」没有任何守卫会报。
    """
    root = _data_repo(tmp_path)
    p = root / "reports" / "factor_width_mde.json"
    p.write_text("{}", encoding="utf-8")
    P.stamp(p, "scripts/factor_width_mde.py", root=root)
    rec = P.read_stamp(p)
    assert rec["data_fp"], "★ 章里必须有非空 data_fp"
    assert rec["data_fp"] == P.data_fingerprint(root), "★ 指纹与现算的不一致"


def test_data_moved_after_the_stamp_reads_as_stale(tmp_path):
    """★ 正控：`data_freshness` 必须**真的会红**。

    只写「章里有这个字段」的守卫是恒真的 —— 字段在不在，
    与它判不判是两件事。
    """
    import os, time

    root = _data_repo(tmp_path)
    p = root / "reports" / "factor_width_mde.json"
    p.write_text("{}", encoding="utf-8")
    P.stamp(p, "scripts/factor_width_mde.py", root=root)

    assert P.data_freshness(p, root=root)["verdict"] == "fresh", "★ 正控前提：先要是 fresh"

    d = root / "data" / "master" / "daily" / "c.parquet"
    d.write_bytes(b"y")
    future = time.time() + 60
    os.utime(d, (future, future))

    got = P.data_freshness(p, root=root)
    assert got["verdict"] == "stale", f"★ 数据动了却还报 fresh：{got}"
    assert got["stamped_data_fp"] != got["current_data_fp"], \
        f"★ stale 必须给出两个指纹供人核对：{got}"


def test_no_stamp_means_unknown_not_ok(tmp_path):
    """★ `unknown` 是「不许引用」，**不是「大概没事」**。

    与代码侧那条同义：把「不知道」和「已知坏了」混为一谈，
    会让人连真的坏报告也一起忽略。
    """
    root = _data_repo(tmp_path)
    p = root / "reports" / "factor_width_mde.json"
    p.write_text("{}", encoding="utf-8")
    got = P.data_freshness(p, root=root)
    assert got["verdict"] == "unknown", got
    assert got["problems"], "★ unknown 必须带可操作的原因，不能只给一个符号"


def test_the_fingerprint_does_not_claim_more_than_it_can_prove(tmp_path):
    """★ 把边界钉在代码里：`data_fingerprint` 的 docstring 必须自陈
    它**证明不了**「报告用的就是现在这份数据」。

    这不是注释洁癖：mtime 认不出「改了又改回」（`cp` 还原会刷新 mtime），
    而 `require_run_completed` 已经撞过一次这个边界。
    守卫若只钉「有指纹」，后人很容易把 `fresh` 读成「数据对」。
    """
    doc = P.data_fingerprint.__doc__ or ""
    assert "必要条件" in doc and "不是充分条件" in doc, \
        "★ 数据指纹必须自陈它是必要条件而非充分条件"
    assert "cp" in doc, "★ 必须点名 mtime 认不出「改了又改回」这个已知边界"


# ── 2026-10-15：指纹必须量「这次跑真正读的那棵树」 ──────────────────────
#
# 实测的 bug：`snap_1015` 与仓库差 **5217/5460 只**文件
# （`000001.SZ` 仓库 1885 行 vs 快照 1883 行），
# 而当时**所有**产物都是 `--data-root /tmp/aplus_snap_1015` 跑出来的，
# `data_fp` 却一律按仓库那份算 ⇒ **指纹读成 fresh，而它量的压根是另一份数据。**
#
# ⇒ 这个方向比「不报警」更坏：它**主动给了一份错数据盖章**。

def _two_trees(tmp_path):
    """仓库那份 + 一份「快照」，两者内容不同。"""
    root = _data_repo(tmp_path)
    snap = tmp_path / "snap"
    (snap / "master" / "daily").mkdir(parents=True)
    for i, c in enumerate("abc"):
        (snap / "master" / "daily" / f"{c}.parquet").write_bytes(b"s" * (i + 1))
        (root / "data" / "master" / "daily" / f"{c}.parquet").write_bytes(b"r")
    return root, snap


def test_the_stamp_measures_the_tree_the_run_actually_read(tmp_path):
    """★ 章里记的 `data_dir` 必须是**跑的时候用的那个根**。"""
    root, snap = _two_trees(tmp_path)
    p = root / "reports" / "factor_width_mde.json"
    p.write_text("{}", encoding="utf-8")
    P.stamp(p, "scripts/factor_width_mde.py", root=root, data_root=snap)

    rec = P.read_stamp(p)
    assert rec["data_dir"] == str(snap.resolve() / "master" / "daily"), \
        f"★ 章记的目录不是这次跑读的那棵：{rec['data_dir']}"
    assert rec["data_fp"] == P.data_fingerprint(root, rec["data_dir"]), \
        "★ 指纹与章里记的目录不一致 —— 两者必须同源，否则比的是两棵树"


def test_data_freshness_must_not_compare_against_a_different_tree(tmp_path):
    """★ 这条是上面那个 bug 的**回归测试**，也是本组最要紧的一条。

    场景与实测完全一致：跑的是 `/tmp` 快照，章里却按仓库那份算指纹。
    ⇒ 仓库数据一动，报告被判 `stale`；**而报告用的那份数据根本没动**。
    这是**假红**；反过来的假绿才是致命的，但两种都说明「比错了树」。

    守卫必须能区分两件事：动**章里那棵** ⇒ stale；动**别的** ⇒ 不许喊。
    """
    import os, time

    root, snap = _two_trees(tmp_path)
    p = root / "reports" / "factor_width_mde.json"
    p.write_text("{}", encoding="utf-8")
    P.stamp(p, "scripts/factor_width_mde.py", root=root, data_root=snap)

    assert P.data_freshness(p, root=root)["verdict"] == "fresh", \
        "★ 正控前提：刚盖章就该是 fresh"

    # 动**章里那棵**（快照）—— 必须报 stale
    d = snap / "master" / "daily" / "z.parquet"
    d.write_bytes(b"moved")
    fut = time.time() + 60
    os.utime(d, (fut, fut))
    got = P.data_freshness(p, root=root)
    assert got["verdict"] == "stale", f"★ 快照动了却报 fresh：{got}"


def test_the_three_writers_pass_their_own_data_root(tmp_path):
    """★ 登记 + 盖章都还不够：**必须把各自那个 `--data-root` 传进去**。

    否则参数摆在那里没人用，指纹永远按仓库算 ——
    而 `--data-root` 的默认值是 `/tmp/aplus_snap_1012`（一个几天前的快照）。
    用 AST 钉，不用子串（注释里有 `data_root` 三个字，形状 13）。
    """
    import ast

    root = Path(P.__file__).resolve().parent.parent
    for gen in ("scripts/factor_width_mde.py",
                "scripts/backtest_walk_forward_weights.py",
                "scripts/factor_attribution.py"):
        tree = ast.parse((root / gen).read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "stamp"):
                if any(k.arg == "data_root" for k in node.keywords):
                    found = True
        assert found, f"★ {gen} 调 stamp() 时没有传 data_root —— 指纹会按仓库那份算"


def test_the_two_data_root_layouts_are_not_interchangeable(tmp_path):
    """★ 同名参数，两种布局：`factor_attribution` 的根**就是数据目录**。

    照 `master/daily` 硬套 ⇒ 算出不存在的目录 ⇒ 指纹 `None`。
    那个方向安全（`None` ⇒ `unknown` ⇒ 不许引用），**但它是蒙对的**，
    而一个永远 `unknown` 的章会让人以为「这报告没法管」。
    """
    root = _data_repo(tmp_path)
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "wf_weights_backtest_k5.daily.parquet").write_bytes(b"x")

    assert P.resolve_data_dir(root, flat, daily_subdir="") == str(flat.resolve())
    assert P.resolve_data_dir(root, flat) == str(flat.resolve() / "master" / "daily")
    # 正控：按错布局算出来的目录**必须不存在**，否则「蒙对」会长期藏着
    assert not (Path(P.resolve_data_dir(root, flat)) / "a.parquet").exists()
