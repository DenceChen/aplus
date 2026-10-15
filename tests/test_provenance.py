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
import hashlib
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

def test_a_report_with_no_sidecar_is_unknown_not_fresh(repo, tmp_path):
    """**现存的老报告都没有 sidecar。**

    把它们报成 fresh 会让「不知道来源」看起来像「已确认没问题」——
    而那正是最容易让人直接引用一个坏数字的状态。
    `unknown` 的含义是**不许引用**。
    """
    # ⚠️ 原来这里传 `/nonexistent/x.json` —— 产物**根本不存在**，
    #   于是测试**因为错误的原因通过**：它声称在测「产物在、但没盖章」，
    #   实际测的是「文件找不到」，而那句 "sidecar in reason" 是从
    #   「没有 sidecar——生成它的 commit 无从得知」里凑出来的。
    #   ★ 与第 134/135 层同一个病：**测试因为错误的原因通过**，
    #     而它长得和旁边那些真的测试一模一样。
    #   ⇒ 修法不是改断言，是**让被测物名副其实**：文件建出来。
    #     改完之后 `"sidecar" in reason` 才第一次为**真**地成立。
    rep = tmp_path / "x.json"
    rep.write_text("{}", encoding="utf-8")
    got = P.freshness(rep, generator="aplus/backtest.py", root=repo["root"])
    assert rep.is_file(), "前置：产物必须真的存在，否则测的是另一件事"
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
    # ⚠️ 门槛（两条）**一个字没放宽**，只换了其中一条的**内容**。
    #   旧断言要求出现 "sidecar"，那等于把「产物缺失」钉成「产物在但没盖章」，
    #   而 §1.1 指出那是**方向相反**的静默失效：
    #   不是把好产物丢掉，而是**让人去找一个从不存在的东西**。
    assert any("不存在" in r for r in got["problems"]), got["problems"]
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

_CITED = ("factor_width_mde.json", "factor_attribution.json",
          "placebo_control.json")


def test_every_stamped_report_resolves_back_to_a_registered_generator():
    """🚨 **盖了章却查不到生成器** = 比「没盖章」更隐蔽的一种不可引用。

    2026-10-15 实测：`placebo_control.json` 的判决 `PLACEBO_NULL` 是全项目的
    **承重判决**（在它之前「因子有效」一个字都不能写），
    而它**既没登记也没盖章** ⇒ 按本模块自己的规矩它 `unknown` = 不可引用，
    **而它自己从不发出这个信号**。

    ⇒ 那是形状 6 的**镜像**：形状 6 是「假装能调」（开关焊死、永远走默认），
    这一条是「**假装不可引用**」—— 产物照常出数、判决照常被抄进文档，
    缺的那份证据**只影响能不能引用**，所以现场看什么都是对的。

    ⇒ 本条只判**已盖章**的一侧（反向的「已登记必须真盖章」由
    `test_registered_is_not_enough_the_writer_must_actually_stamp` 覆盖）。
    ⚠️ 刻意**不**判「仓里每一份报告都得有 sidecar」——
    那会是一条**永远红**的守卫（69 份里大半是历史中间产物），
    而**永远红的守卫会把人推向「为了变绿去乱盖章」这个错误修法**（第 112 层）。
    """
    # ⚠️ 用**真仓库根**，不用 `repo` 夹具 —— 那个夹具是 tmp_path 里的假仓库
    #   （它测的是「相对 HEAD 算得对不对」），扫不到真产物。
    root = Path(P.__file__).resolve().parent.parent
    stamped = sorted(p.name for p in (root / "data/reports").glob("*.provenance.json"))
    assert stamped, "一份 sidecar 都没有 ⇒ 探测器坏了，不是仓里真的干净"
    unknown = [n for n in stamped
               if P.resolve_generator(n[: -len(".provenance.json")]) is None]
    assert not unknown, (
        f"这些产物盖了章却解析不出生成器 ⇒ 读者无从判断它出自哪份代码："
        f"{unknown}。要么补进 DEFAULT_GENERATORS，要么删掉那份 sidecar"
        f"（**盖一个假章比不盖更坏** —— 形状 16）")


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
            # ★ 两种真接线形态都要认：`provenance.stamp(...)` 与 `stamp(...)`
            #   （`from aplus.provenance import stamp`）。
            #   ⚠️ 第一版只认属性调用，于是我给 `placebo_control.py` 写的是
            #   裸名导入的 `stamp(...)` ⇒ 守卫报「没接线」而它**明明接着**。
            #   —— 又一次「红的原因不对」的守卫：**把真接线判成没接**，
            #      而人会去「修」一个正确的写法。
            is_stamp_call = (isinstance(node, ast.Call)
                             and ((isinstance(node.func, ast.Attribute)
                                   and node.func.attr == "stamp")
                                  or (isinstance(node.func, ast.Name)
                                      and node.func.id == "stamp")))
            if is_stamp_call:
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


# ══ 字节判据：`code_sha` 在 dirty 时**标识不了实际跑的代码** ══════════
#
# 2026-10-15 实测（`wf_weights_backtest_k5.json`）：
#
#   03:20  把 `provenance.stamp(...)` 写进脚本 —— **未提交**
#   03:21  跑 k=5 ⇒ 侧卡写下 `code_sha = HEAD = 27bcdfd`、`code_dirty = True`
#   03:29  提交 a09fbdb，其内容 == 03:21 实际跑的那份字节
#
# ⇒ 生成之后生成器**确实没再变过**，今天用 HEAD 就能复现 k=5；
#   而 `27bcdfd` 那棵树里**根本没有盖章调用** ——
#   **侧卡本身就是它所声称那个 sha 的反证**。
# ⇒ commit 走查从 27bcdfd 走到 HEAD 会撞见 a09fbdb ⇒ 判 `stale`（实测确实如此）。


def test_the_commit_of_dirty_bytes_is_not_mistaken_for_a_later_change(repo):
    """★ 上面那条误报的最小复现：字节没变，就不该判「生成后动过」。

    这是**过严守卫**的形态：它红的时候把人推向「去重跑/去改本来正确的产物」，
    而真相是产物完全可复现。见 memory `an-over-strict-guard-...`。
    """
    p = _report(repo)

    # 盖章发生在「字节已含新内容、但还没提交」的时刻
    f = repo["root"] / "aplus" / "backtest.py"
    f.write_text("# v1\n# stamped into the file\n")
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    st = P.read_stamp(p)
    assert st["code_dirty"] is True, "★ 用例前提不成立：盖章时工作区不脏"
    assert st["code_file_sha256"], "★ 用例前提不成立：盖章没写字节哈希"

    # 事后把**刚跑过的那份字节**提交掉 —— 一个字节都没改
    f.write_text("# v1\n# stamped into the file\n")   # 显式重写，内容不变
    sha_after = repo["commit"]("commit the bytes that actually ran")
    assert sha_after != repo["sha1"]

    v = P.freshness(p, root=repo["root"])
    assert v["verdict"] == "fresh", (
        f"★ 对一个**当前代码就能复现**的产物报了 `{v['verdict']}`：{v.get('reason')}\n"
        f"   生成之后生成器一个字节都没动过 —— 变的只是这些字节**什么时候被提交**。\n"
        f"   ⇒ commit 走查把「脏改动后来被提交」误当成「生成之后动过」。"
    )


def test_byte_evidence_does_not_make_a_dirty_stamp_trustworthy(repo):
    """★ 字节一致**只证明生成器自己**，不证明它 import 的 `aplus/factor.py` 等。

    ⇒ 这里修的只是**误报**，**不是放松可信度**：dirty 时仍然
      `trustworthy=False`，且理由要说清「只覆盖到哪一层」。
    """
    p = _report(repo)
    (repo["root"] / "aplus" / "backtest.py").write_text("# v1\n# dirty\n")
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    v = P.freshness(p, root=repo["root"])
    assert v["verdict"] == "fresh"
    assert v["trustworthy"] is False, (
        "★ 盖章时工作区是脏的，却给出了 trustworthy=True —— "
        "字节哈希只覆盖生成器自身，依赖闭包在那一刻是未知的。"
    )
    assert "依赖闭包" in v["reason"], f"★ 理由没说明证据的边界：{v['reason']}"


def test_the_report_writing_itself_must_not_change_the_verdict(repo):
    """★ 产出物造成的「脏」不该改判决，但**会**压低可信度 —— 而后者是**实测的缺陷**。

    实测 2026-10-15：`data/reports/` 是**被跟踪**的（69 个文件）⇒
    任何报告重跑一遍都把自己的输出改写成 `M` ⇒ `is_dirty()` 为真。
    ⇒ `code_dirty=True` 对「会写自己输出的报告」是**结构性必然**，
       `trustworthy` 于是永远是 `False`。

    ⚠️ 这里**只**断言不受影响的部分（`fresh` 不变）；
       `trustworthy` 恒 False 的口径问题是独立决定，见 CLAUDE.md TODO
       「`code_dirty` 把产出物算成了代码改动」。
    """
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    st = P.read_stamp(p)
    assert st["code_file_sha256"], "★ 用例前提不成立：没写字节哈希"

    # 模拟「报告改写了自己的输出」⇒ 工作区多一条 M
    (repo["root"] / "reports" / "backtest_summary.json").write_text(
        json.dumps({"hit_rate_up": 0.45}), encoding="utf-8")
    v = P.freshness(p, root=repo["root"])
    assert v["verdict"] == "fresh", (
        f"★ 产出物变化把判决改成了 {v['verdict']}：{v.get('reason')}\n"
        f"   ⇒ 字节证据在，正是为了不受 git 历史与产物改写影响。"
    )
    assert v["trustworthy"] is False, (
        "★ 若这里变成 True，说明可信度判据被放松了 —— "
        "本守卫此刻**已知**只能证明生成器自身，闭包未证。"
    )


def test_changing_the_generator_bytes_afterwards_is_caught(repo):
    """★ 字节判据的**牙齿**：改了生成器字节 ⇒ 不许还是 fresh。

    这条是上一条的镜像：既要不误报「没变」，也要不漏报「变了」。
    """
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    (repo["root"] / "aplus" / "backtest.py").write_text("# v1\n# v2 edited\n")
    v = P.freshness(p, root=repo["root"])
    assert v["verdict"] != "fresh", (
        "★ 生成器字节已被改动，却仍判 fresh —— 字节判据没有牙齿"
    )


def test_the_byte_hash_is_the_files_actual_content(repo):
    """★ 正控：`code_file_sha256` 必须等于文件的真实 sha256。

    断言的是**内容**，不是「函数返回了一个非空字符串」。
    """
    f = repo["root"] / "aplus" / "backtest.py"
    got = P.code_file_sha256(repo["root"], "aplus/backtest.py")
    assert got == hashlib.sha256(f.read_bytes()).hexdigest(), "★ 字节哈希不是文件内容的哈希"


def test_a_missing_generator_gets_no_byte_hash_rather_than_a_guess(repo):
    """★ 取不到就返回 `None`，**不做降级猜测**（与 `code_sha` 同规矩）。"""
    assert P.code_file_sha256(repo["root"], "aplus/nope.py") is None
    assert P.code_file_sha256(repo["root"], None) is None


def test_a_generator_path_that_does_not_exist_is_unknown_never_fresh(repo):
    """★ 第 123 层：**「查不到动过」与「查不到那个文件」必须分开**。

    实测（2026-10-15）：`freshness(f, "scripts/wf_weights_backtest.py")` ——
    那个文件**根本不存在**，而 `commits_touching` 对不存在的路径返回 `[]`、
    `code_file_sha256` 返回 `None` ⇒ 走查得出「没有提交动过它」
    ⇒ **判 fresh**。

    🚨 这正是**最贵的那个答案**：一个真实 stale 的 k 产物，
      仅仅因为生成器名字写错，就从「不许引用」变成「可以引用」。
      （脏工作区时 `trustworthy=False` 恰好兜住了它，
       **但工作区一干净就是一次彻底的假 fresh**。）

    ⇒ 所以显式传入的生成器路径必须在仓里**真实存在**，否则一律 `unknown`。
    """
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    repo["commit"]("commit so the tree is clean")          # ⇒ trustworthy 本该是 True

    got = P.freshness(p, "aplus/nope.py", root=repo["root"])

    assert got["verdict"] == "unknown", (
        f"生成器不存在却判了 {got['verdict']}：{got['reason']}"
    )
    assert got["trustworthy"] is False
    assert "不存在" in got["reason"], (
        f"拒了但没说为什么，读者无从判断是自己写错了还是产物真过期：{got['reason']!r}"
    )


def test_a_generator_that_is_deleted_afterwards_also_stops_being_fresh(repo):
    """反控：不是「只有我手打错的名字」会触发，而是**任何**取不到的文件。

    ⚠️ 这条同时钉住方向：生成器被**删掉**时，git 走查同样返回空
    ⇒ 旧代码照样判 fresh。**删除比改名更隐蔽**，因为没人会去改调用点。
    """
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    (repo["root"] / "aplus" / "backtest.py").unlink()
    repo["commit"]("删掉生成器")

    got = P.freshness(p, "aplus/backtest.py", root=repo["root"])

    assert got["verdict"] == "unknown", (
        f"生成器被删却判了 {got['verdict']}：{got['reason']}"
    )


def test_the_stamp_actually_writes_the_byte_field(repo):
    """★ 「登记 ≠ 盖章」：字段写进源码里，不等于它真被写进 sidecar。"""
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    rec = json.loads(P.sidecar_path(p).read_text(encoding="utf-8"))
    assert "code_file_sha256" in rec, "★ stamp 没把字节哈希写进 sidecar"


# ── 判决**依据**必须可见（2026-10-15 新增）────────────────────────────
#
# ★ 为什么这一组和上面 32 条不是同一层：
#   上面测的是「机制能不能写字段」——那 32 条**全绿**，
#   而当时 data/reports 下 **12 份 sidecar 一份都没有** `code_file_sha256`
#   （字段在它们盖章之后才落地）。
#   ⇒ 守卫断言的是**机制**，我的主张说的是**数据**。
#     两者之间没有任何测试，于是我把「落进 sidecar」写进了 CLAUDE.md。
#
#   这一组测的是**报告层**：审计输出必须说清判决是
#   「字节证据」还是「只靠 commit 走查」。两种强度差一个量级的绿，
#   不许看起来一模一样。

def _legacy_sidecar(p, root, generator="aplus/backtest.py", sha=None):
    """造一份 **`code_file_sha256` 字段不存在** 的旧 sidecar（字段落地之前盖的）。"""
    p.with_suffix(p.suffix + ".provenance.json").write_text(json.dumps({
        "code_sha": sha or P.code_sha(root),
        "code_dirty": False,
        "generator": generator,
        "stamped_at": "2026-10-14T00:00:00+00:00",
    }), encoding="utf-8")
    return p


def test_a_fresh_verdict_without_the_byte_field_says_so(repo):
    """★ 旧 sidecar 判 fresh 时，**必须**自认「我没有字节证据」。

    这就是 2026-10-15 那 12 份的真实形态：它们真的 fresh，
    但强度只等于 commit 走查。不许把它印成和字节证据一样的绿。
    """
    p = _legacy_sidecar(_report(repo), repo["root"])
    row = P.freshness(p, generator="aplus/backtest.py", root=repo["root"])
    assert row["verdict"] == "fresh"
    assert row["byte_evidence"] is False, "旧 sidecar 不许自称有字节证据"
    assert "无字节证据" in row["reason"]


def test_a_fresh_verdict_with_a_matching_byte_hash_reports_byte_evidence(repo):
    """★ 正控：新盖的章（有字段且一致）必须自称**有**字节证据。

    没有这条，上面那条会把「永远说没有」也判绿。
    """
    p = _report(repo)
    P.stamp(p, "aplus/backtest.py", root=repo["root"])
    row = P.freshness(p, generator="aplus/backtest.py", root=repo["root"])
    assert row["verdict"] == "fresh"
    assert row["byte_evidence"] is True
    assert "无字节证据" not in row["reason"]


def test_the_cli_prints_the_basis_of_every_fresh_verdict(repo, capsys):
    """★ 缺口当初就藏在这里：CLI 对 fresh 只印名字，依据一个字都不说。

    真失效形态是**旧的、无字段的 sidecar 在审计里显得和有字节证据的一样绿**。
    """
    d = repo["root"] / "reports"
    _legacy_sidecar(_report(repo, "backtest_summary.json"), repo["root"])
    p2 = _report(repo, "fresh_with_bytes.json")
    P.stamp(p2, "aplus/backtest.py", root=repo["root"])
    P._main(["--reports-dir", str(d), "--root", str(repo["root"])])
    out = capsys.readouterr().out
    assert "backtest_summary.json" in out and "fresh_with_bytes.json" in out
    assert "⚠️ 仅 commit 走查" in out, "无字节证据的那份必须自曝"
    assert "字节证据" in out, "有字节证据的那份必须说清"
    # ⇒ 两种绿不许长得一样
    assert out.count("仅 commit 走查") == 1


def test_default_generators_has_no_duplicate_keys():
    """登记表里**同一个产物不许登记两次**。

    ★ 2026-10-15 实测踩到：`k_sweep_summary.json` 被登记了两次
      （一处是「找生成器」那轮补的，一处是今天补盖章那轮补的）。
      而 `DEFAULT_GENERATORS` 是 **dict 字面量** ⇒ 后者**静默覆盖**前者：
      **不报错、不告警、登记表读起来完全正常。**

    ⇒ 运行时永远看不到这个重复（dict 已经把它吞了），
      所以只能在 **AST 层**数键：出现两次就红。

    为什么值得一条守卫：**重复登记意味着有一次登记从来没生效过**，
      而读表的人无从知道是哪一次。这与「陈旧产物能通过一致性检查」同源 ——
      一致性检查查的是「彼此吻合」，查不出「这一条根本没被用上」。
    """
    import ast

    src = Path(P.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    # ⚠️ `DEFAULT_GENERATORS` 带类型标注 ⇒ 节点是 **AnnAssign**，不是 Assign。
    #   写成只认 `Assign` 的第一版在这上面抛 `StopIteration` ——
    #   **红的原因不对**：读起来像「数据有问题」，实际是「守卫自己写错了」。
    #   而 `next(..., None)` + 找不到就 return 更糟：那是一条**静默通过**的守卫，
    #   它自己失效了都不知道。所以这里显式断言找到了。
    def _is_dg(node):
        if isinstance(node, ast.AnnAssign):
            return getattr(node.target, "id", None) == "DEFAULT_GENERATORS"
        if isinstance(node, ast.Assign):
            return any(getattr(t, "id", None) == "DEFAULT_GENERATORS"
                       for t in node.targets)
        return False

    nodes = [n for n in ast.walk(tree) if _is_dg(n)]
    assert nodes, "源码里找不到 DEFAULT_GENERATORS 的赋值节点 —— 守卫失效了（宁可红）"
    keys = [k.value for k in nodes[0].value.keys if isinstance(k, ast.Constant)]
    assert keys, "赋值节点里一个键都没有 —— 守卫在数别的东西"
    assert len(keys) == len(set(keys)), "登记了重复的产物名（dict 已静默吞掉其中一次）"
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"重复登记：{dupes}"
    # 反向对照：AST 数出来的键必须与运行时 dict 一致，否则上面那条在数别的东西
    assert set(keys) == set(P.DEFAULT_GENERATORS)


# ── 第 125 层：盖章时校验「跑的和盖章的是同一份字节」 ─────────────────

def test_the_stamp_refuses_when_the_generator_changed_mid_run(repo):
    """★ 这条守的是「产物正在生成时，它的生成器字节必须冻结」。

    2026-10-15 的实测代价：K 链串行跑 ~10 分钟一档，我一边跑一边改生成器，
    而 `stamp()` 是在**每档写盘那一刻**算哈希 ⇒ 算的是**当时盘上**的字节，
    不是**这一档执行**的字节 ⇒ K=5 用旧字节跑完却盖上新字节的哈希。
    ⇒ 那枚章说的不是「这份产物是这些字节造的」，
      而是「写盘那一刻磁盘上恰好是这些字节」—— **反向的错误证据**。

    ⇒ 不一致时**必须拒绝盖章**：偏写出去比不写更坏。
    """
    root = repo["root"]
    gen = "aplus/backtest.py"
    (root / "reports" / "master" / "daily").mkdir(parents=True, exist_ok=True)
    before = P.code_file_sha256(root, gen)

    # 「跑的过程中」被改了 —— 这是本条要抓的**唯一**事件
    (root / gen).write_text("# v2 —— 在产物生成期间被改\n", encoding="utf-8")

    report = _report(repo)
    with pytest.raises(RuntimeError) as ei:
        P.stamp(report, gen, root=root, expect_bytes=before)

    msg = str(ei.value)
    assert before in msg and P.code_file_sha256(root, gen) in msg, (
        "拒绝必须**同时报出两侧的字节** —— 只说「不一致」的话，"
        "读的人无从判断是谁变了")
    assert "第 125 层" in msg, "拒绝要说清这是哪条铁律，否则会被当成环境问题"


def test_the_refusal_happens_before_any_write(repo):
    """🚨 拒绝必须发生在**写盘之前**（第 126 层：闸门放在 `json.dump` 之后，
    会**先覆盖掉原始证据再打印「已作废」**）。"""
    root = repo["root"]
    gen = "aplus/backtest.py"
    (root / "reports" / "master" / "daily").mkdir(parents=True, exist_ok=True)
    before = P.code_file_sha256(root, gen)
    (root / gen).write_text("# v2\n", encoding="utf-8")

    report = _report(repo)
    side = P.sidecar_path(report)
    side.write_text('{"code_sha": "PREVIOUS-EVIDENCE"}', encoding="utf-8")
    keep = side.read_bytes()

    with pytest.raises(RuntimeError):
        P.stamp(report, gen, root=root, expect_bytes=before)

    assert side.read_bytes() == keep, (
        "★ 拒绝之后仍把旧 sidecar 覆盖了 —— 那是**用一条警告换走了原始证据**。")


def test_the_matching_case_stamps_normally(repo):
    """正控：字节没变时**必须照常盖章** ——
    否则这条守卫会退化成「永远拒绝」，而那等价于没有 provenance。"""
    root = repo["root"]
    gen = "aplus/backtest.py"
    (root / "reports" / "master" / "daily").mkdir(parents=True, exist_ok=True)
    expect = P.code_file_sha256(root, gen)

    out = P.stamp(_report(repo), gen, root=root, expect_bytes=expect)
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["code_file_sha256"] == expect


def test_omitting_expect_bytes_keeps_the_old_behaviour(repo):
    """⚠️ 刻意**不做成必填**。

    几十个调用点，一次性改完会把「大家都在传」变成「大家都在抄一个变量」——
    那样检查照样在，而没人再读它（第 102 层：探针打在自己的副本上）。
    ⇒ 正确性靠新脚本按模式写，而不是靠把旧调用点全改一遍。
    """
    root = repo["root"]
    out = P.stamp(_report(repo), "aplus/backtest.py", root=root)
    assert json.loads(out.read_text(encoding="utf-8"))["code_file_sha256"]


def test_the_comparison_is_exact_and_not_a_prefix_match(repo):
    """★ 抓「只比前 8 位」那种截断。

    真实失效形态：`expect_bytes[:8] != now_bytes[:8]` 看着完全够用
    （commit 短 sha 本来就只 8 位），而**篡改者只要保留前 8 位就绕过了**。
    ⚠️ 我第一版的测试只做「把生成器整个改掉」⇒ 前缀当然也不同 ⇒
    **截断实现照样绿**（变异 N2 存活）。⇒ 必须构造
    「**前缀逐位相同、尾部不同**」的输入才问得到这个问题。
    """
    root = repo["root"]
    gen = "aplus/backtest.py"
    (root / "reports" / "master" / "daily").mkdir(parents=True, exist_ok=True)
    now = P.code_file_sha256(root, gen)

    forged = now[:8] + "0" * (len(now) - 8)
    assert forged != now and forged[:8] == now[:8], "夹具本身没造出「前缀同、尾不同」"

    with pytest.raises(RuntimeError):
        P.stamp(_report(repo), gen, root=root, expect_bytes=forged)


# ── §1.2：判决不许随 cwd 变（2026-10-15）──────────────────────────────

_REAL_ROOT = Path(P.__file__).resolve().parents[1]


def test_the_root_default_never_looks_at_the_current_working_directory(monkeypatch, tmp_path):
    """★ `root` 的默认值曾经是 `"."`（= cwd），而 sidecar 里的 `generator`
    是**仓库相对**路径 ⇒ 在仓外调用 `freshness()` 时，
    `Path(root) / generator` 指向仓外那个不存在的路径。

    实测（修复前）：仓内 14 fresh / 7 stale，`/tmp` 下 **21 份全塌成 `unknown`**，
    理由句写的是「生成器路径在仓里不存在 ⇒ **判据本身没跑**」——
    **谎报我没验**，而我明明验了且逐位一致。那比「产物坏了」更危险：
    安全判据谎报「没跑」会把人推去重跑一个 45 分钟的活。

    ⚠️ 本条只钉**方向**：仓内与仓外判决必须相同。
      钉「每个产物都是 fresh」会立刻过严——真有 stale 时它就红。
    """
    reports = sorted((_REAL_ROOT / "data" / "reports").glob("*.provenance.json"))
    if not reports:
        pytest.skip("本仓没有带章产物，测不了判决")

    # 🚨🚨 **必须用绝对路径。** 第一版这里传的是裸文件名，于是
    #   `freshness("placebo_control.json")` 在**两个** cwd 下都**找不到文件**
    #   ⇒ 两边都是 `unknown` ⇒ `inside == outside` 恒成立
    #   ⇒ 我写的那条守卫在变异下**没红**（第 136 层当场复发：
    #   「守卫不变 ≠ 守卫没洞」，更糟的是它比较的是**两个同样错误**的答案）。
    #
    #   ⇒ 传绝对路径，让两个 cwd 差**唯一**的那个变量：`root` 的默认值。
    paths = [q.with_name(q.name[: -len(".provenance.json")]) for q in reports][:6]

    monkeypatch.chdir(_REAL_ROOT)
    inside = {q.name: P.freshness(q)["verdict"] for q in paths}

    monkeypatch.chdir(tmp_path)
    outside = {q.name: P.freshness(q)["verdict"] for q in paths}

    # 前置：两个 cwd 下都必须真的判出东西，而不是两边一起「找不到」
    assert set(inside.values()) - {"unknown"}, (
        f"仓内判决全是 unknown：{inside}\n"
        "  ⇒ 守卫比较的是「找不到」，那不是判决，是空集合相等。")

    assert inside == outside, (
        f"判决随 cwd 变了（`TODO_generator_static_guards` §1.2）：\n"
        f"  仓内 {inside}\n  仓外 {outside}\n"
        "  ⇒ `root` 的默认值必须是仓库根，**不能**是 cwd")


def test_scan_from_outside_the_repo_does_not_see_an_empty_directory(monkeypatch, tmp_path):
    """★ `scan(reports_dir="data/reports")` 的默认值也吃 cwd。

    在仓外跑它会扫到**空目录**并返回 `[]`，
    而 `[]` 在 `summary()` 里读成「零 stale、零 unknown、**全 fresh**」。
    ⇒ **空与全好不可区分**（第 89 层同族）。

    ⚠️ 钉的是「看得到产物」，**不是**「全都 fresh」——后者会把真 stale 变成红灯。
    """
    monkeypatch.chdir(tmp_path)
    s = P.summary()
    assert s["n"] > 0, (
        f"在 {tmp_path} 下 `summary()` 返回 n={s['n']} —— "
        "它扫的是 cwd 下的 `data/reports`，那里当然空。\n"
        f"  得到的是 {{'n': 0, 'stale': [], 'unknown': [], 'fresh': []}}，"
        "**读起来与「零问题」一模一样**。")


def test_stamping_from_outside_the_repo_does_not_write_a_null_code_sha(monkeypatch, tmp_path):
    """★ 同一个默认值在 `stamp()` 上更险：`code_sha(root)` 在 cwd 不是仓库时
    返回 `None` ⇒ 章里写下 `code_sha: null` ⇒ **事后无法分辨「没盖章」与「盖了个空章」**。

    ⚠️ 这一条**从未实际发生**：现有 21 份 sidecar 的 `code_sha` 全部非空，
    因为每个生成器都**显式**传了 `root=ROOT`。
    ⇒ 它是**接线运气**，不是设计。本条把那份运气钉成不变量。
    """
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "probe.json"
    out.write_text("{}", encoding="utf-8")
    P.stamp(out, "scripts/does_not_matter.py")
    st = P.read_stamp(out)
    assert st is not None and st.get("code_sha"), (
        f"章里 code_sha={st and st.get('code_sha')!r} —— "
        "盖章时 cwd 不在仓库里，于是记了个空 sha。\n"
        "  ⇒ 事后拿到这份 sidecar 的人，分不清「生成它的 commit 无从得知」"
        "（没盖章）和「commit 就是空的」（盖了个空章）。")


# ── §1.5：三种输入不许塌成两句话（C1/C2）───────────────────────────────

def test_a_bare_name_is_reported_as_a_bare_name_not_as_an_unstamped_report():
    """🚨 C1：传裸名时，旧代码报的两条理由**都是假的**。

    `freshness("verdict_composite")`：
      - `sidecar_path` 找 `verdict_composite.provenance.json`，
        真名是 `verdict_composite**.json**.provenance.json` ⇒ 「没有 sidecar」（假）；
      - `DEFAULT_GENERATORS` 31 个键里 29 个带 `.json`
        ⇒ `resolve_generator("verdict_composite")` 返回 None
        ⇒ 「不在 DEFAULT_GENERATORS 里」（也假，它**登记着**）。

    ⇒ 两条假理由会把人引到「去 DEFAULT_GENERATORS 补一行」——
      而那里**已经有一行**，补进去就是**重复键**，会被静默覆盖。

    ⚠️ 钉的是「**说法**」，不是 `verdict`：`unknown` 在这里是**对的**
      （它确实没被正确地指到产物），本条不许去改判决方向。
    """
    got = P.freshness("verdict_composite")
    assert got["verdict"] == "unknown"
    assert "裸名" in got["reason"], got["reason"]
    assert "没有溯源 sidecar" not in got["reason"], (
        f"它对着一个**盖过章的**产物说「没有 sidecar」：{got['reason']}")
    assert "不在 DEFAULT_GENERATORS" not in got["reason"], (
        f"它说这份产物没登记，而它登记着（键是 `verdict_composite.json`）："
        f"{got['reason']}\n  ⇒ 照这条假建议去补一行 = 制造重复键。")
    # 可操作的那条必须给出来：补 `.json` 后能解析到谁
    assert P.resolve_generator("verdict_composite.json"), (
        "前置：`verdict_composite.json` 必须真的在登记表里，"
        "否则本条钉的就是「补 .json 也救不了」那个分支")


def test_a_missing_artifact_is_not_reported_as_a_report_that_lacks_a_stamp():
    """🚨 C2：产物**根本不存在**时，旧代码说「没有溯源 sidecar——
    生成它的 commit 无从得知」。

    那是在**陈述一件不成立的事**（这份产物从没生成过，没有什么 commit 可知），
    而且是**方向相反**的静默失效：
    不是把好产物丢掉（保守），而是**让人去找一个从不存在的东西**。

    对照组：文件**存在**但没盖章，必须仍然说「没有 sidecar」——
      否则 C2 就变成了「一律改口」，那与旧缺陷同构。
    """
    missing = P.freshness(Path("/tmp/x/definitely_not_there.json"))
    assert "不存在" in missing["reason"], missing["reason"]
    assert "没有溯源 sidecar" not in missing["reason"], missing["reason"]

    d = Path("/tmp/x")
    d.mkdir(parents=True, exist_ok=True)
    real = d / "exists_but_unstamped.json"
    real.write_text("{}", encoding="utf-8")
    present = P.freshness(real)
    assert real.is_file()
    assert "没有溯源 sidecar" in present["reason"], (
        f"文件明明在，却报「产物不存在」：{present['reason']}\n"
        "  ⇒ C2 变成了「一律改口」，方向与旧缺陷同构。")
