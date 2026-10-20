"""D1 交付面四份产物的**可引用性**守卫。

## 这份测试在守什么

2026-10-20 实测：`data/reports/` 共 107 份 json，
**44 份 fresh · 12 份 stale · 51 份 unknown**，
而 51 份 unknown 里有**三份就在当天的交付面上**：

| 产物 | 写盘模块（**不是调用它的脚本**） | 当时为什么 unknown |
|---|---|---|
| `run_status.json` | `aplus/reports.py::write_run_status` | **未登记** + 无 sidecar |
| `holdings.json` | `aplus/live_rebalance.py::save_state` | **未登记** + 无 sidecar |
| `forward_results.json` | `aplus/forward.py::merge_results` | **未登记** + 无 sidecar |
| `stats.json` | `scripts/run_daily.py::step_stats` | 已登记但**无章** |

⇒ 按本仓自己的规矩（`aplus/provenance.py`）：
**`unknown` = 不许引用** ⇒ D1 每天真正交付的那几个产物
（今天出了没出单、名单状态、前瞻结果）**当时是不可引用的**。
这不是「少一道守卫」，是**交付物缺身份证**。

## 本轮做了什么、没做什么（别读成「已可引用」）

- ✅ **登记**（`aplus/provenance.py::DEFAULT_GENERATORS`）：三份补登记；
  `stats.json` 核实登记值（`scripts/run_daily.py`）正确。
- ✅ **章可搬迁**：`stamp()` 此前把调用者给的**绝对路径**照抄进 sidecar
  （`scripts/market_nav_caliber.py` 传 `__file__` ⇒ 全仓 55 份里独一份）
  ⇒ 现在规范化成**仓相对路径**。
- ⛔ **盖章接线不在本轮**：`aplus/reports.py` / `aplus/live_rebalance.py` /
  `aplus/forward.py` 由另一批改动接手。
  ⇒ **登记 ≠ 盖章**：在那之前这三份**仍然是 `unknown`**，
  而这正是本轮的真实效果：**判据能跑了，证据还没到。**

## 判据纪律（本文件不许放松的）

`freshness()` 判 `unknown` 的含义是「**判据没跑成 / 没有证据**」，
**不是**「事实为否」。⇒ 本文件钉住「缺章 ⇒ 仍然是 `unknown`」，
不许有人为了让它变绿而放宽 `freshness()`。

⚠️ 本文件**不写生产产物路径**：全部写入都在 `tmp_path` 里，
并由 `_watch_production_writes` 用 **audit hook** 钉住
（它看的是**本进程**实际开过的写句柄，所以不会与别的 agent 并发写
`data/reports/` 互相误伤）。
"""
from __future__ import annotations

import ast
import os
import subprocess
import time
from pathlib import Path

import pytest

from aplus import provenance as P

REPO_ROOT = Path(P.__file__).resolve().parent.parent

#: 生产产物目录——**本文件只读它，绝不写它**。
PRODUCTION_REPORTS = REPO_ROOT / "data" / "reports"

#: D1 每天真正交付的那几份产物。
DELIVERY_ARTIFACTS = (
    "run_status.json",       # 今天出了没出单、哪一步没过
    "holdings.json",         # 名单状态（实盘跨日持仓）
    "forward_results.json",  # 前瞻评估结果
    "stats.json",            # 已交付产品的前瞻账
)

#: **同属交付面、但由另一批改动落地的**产物。
#:
#: `downleg_gate.json` = **跌腿闸门**（D1 每天 10 只跌腿那半边的发布闸），
#: 产物自报 `role = "DOWNLEG_GATE_ARM"` / `avoidance_list_publish_allowed: false`
#: ⇒ **它决定跌腿名单许不许发**，与上面四份同属交付面。
#: 它是 `scripts/measure_downleg_ranking.py` 的第二份产物
#: （`--out` → 对照臂 `downleg_ranking.json`，
#:   `--gate-out` → 闸门 `downleg_gate.json`）⇒ **同一个生成器**。
#:
#: ⚠️ 单列一组是因为它由**另一批改动**在 2026-10-20 23:35 落地 ——
#:   把它并进 `DELIVERY_ARTIFACTS` 会让「谁负责它」这件事在文档里说不清。
DELIVERY_SURFACE_EXTRA = ("downleg_gate.json",)

#: 交付面上的全部产物（本文件所有检查都跑在它上面）。
ALL_DELIVERY = DELIVERY_ARTIFACTS + DELIVERY_SURFACE_EXTRA


# ── 夹具：一个真的临时仓 ────────────────────────────────────────────

@pytest.fixture
def sandbox(tmp_path):
    """一个**真的**临时 git 仓：生成器文件、产物文件全在 `tmp_path` 里。

    ⚠️ 为什么不直接用真仓：真仓的 `aplus/reports.py` 正在被另一批改动编辑，
      「盖章后字节没变 ⇒ fresh」这条断言会在**别人改文件的那一瞬间**翻面
      ⇒ 变成一条**会随机误报**的守卫（第 112 层：过严的守卫和恒真的一样有害）。
    ⇒ 一切写入都在这里，真仓只用来**读登记表**。
    """
    root = tmp_path / "repo"
    (root / "aplus").mkdir(parents=True)
    (root / "reports").mkdir()
    (root / "aplus" / "reports.py").write_text("# v1\n", encoding="utf-8")
    for cmd in ("git init -q", "git config user.email t@t", "git config user.name t"):
        subprocess.run(cmd, shell=True, cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=root, check=True,
                   capture_output=True)
    return root


def _report(sandbox_root: Path, name: str) -> Path:
    p = sandbox_root / "reports" / name
    p.write_text("{}", encoding="utf-8")
    return p


# ── ① 登记：四份产物都必须有生成器 ──────────────────────────────────

def test_every_delivery_artifact_has_a_registered_generator():
    """**表外的产物判 `unknown` = 不可引用。**

    而这四份正是 D1 每天真的交出去的东西 ——
    「今天出了没出单」「名单状态」「前瞻结果」「前瞻账」。
    ⇒ 它们缺登记不是「文档没写全」，是**交付物没有身份证**。
    """
    for name in ALL_DELIVERY:
        gen = P.resolve_generator(name)
        assert gen, (
            f"{name} 不在 DEFAULT_GENERATORS / PATTERN_GENERATORS 里。\n"
            f"  ⇒ 按 aplus/provenance.py 自己的规矩，它判 `unknown` = **不许引用**。\n"
            f"  ⇒ 修法：在 `aplus/provenance.py` 补一行，值必须是**写盘的那个模块**"
            f"（不是调用它的脚本 —— 登记错会让「谁动过这个产物」答的是另一个文件"
            f"的提交历史）。")


def test_the_registered_generator_of_every_delivery_artifact_is_on_disk():
    """🚨 第 123 层：**查不到 ≠ 没有**。

    `freshness()` 判 `stale` 的依据是「生成之后没人动过生成器」；
    而生成器路径**不存在**时，commit 走查返回空 ⇒ 旧代码判 `fresh`
    ⇒ **一个真实 stale 被翻成 fresh**，那是最贵的那个答案。
    ⇒ 登记的值必须**在盘上真的存在**。
    """
    for name in ALL_DELIVERY:
        gen = P.resolve_generator(name)
        assert (REPO_ROOT / gen).is_file(), (
            f"{name} 登记的生成器 `{gen}` 在仓里**不存在**。\n"
            f"  ⇒ `freshness()` 的判据**自己没跑成**，不是「产物是新的」。\n"
            f"  ⇒ 先确认真实写盘点是哪个文件，再改登记值。")


def test_the_registration_key_is_the_artifact_filename_verbatim():
    """**键就是产物文件名**，逐字相等。

    🚨 两个常见写法都会静默失效：
      ① 登记成 `data/reports/run_status.json`（全路径）——
         `resolve_generator()` 拿的是**裸文件名**⇒ 查不到 ⇒ `unknown`；
      ② 登记成 `run_status`（漏扩展名）⇒ 同样查不到。
    ⇒ 两种都必须当场判红，而不是「反正登记了」。
    """
    for name in ALL_DELIVERY:
        assert P.resolve_generator(f"data/reports/{name}") is None, (
            f"全路径 `{name}` 被当成了合法键 ⇒ 查表的人会以为登记生效了，"
            f"而 `resolve_generator()` 拿到的是裸文件名，查不到。")
        bare = name[: -len(".json")]
        assert P.resolve_generator(bare) is None, (
            f"裸名 `{bare}`（漏了扩展名）被当成了合法键 ⇒ 同上。")
    for key in P.DEFAULT_GENERATORS:
        assert "/" not in key, (
            f"登记键 `{key}` 里带路径分隔符 ⇒ 键必须是**产物文件名**，不是路径。")
        assert not key.startswith("."), f"登记键 `{key}` 像是隐藏文件。"


def _read_source_stably(path: Path, attempts: int = 3) -> str:
    """读一个**可能正被别人改写**的源文件。

    🚨 另一批改动正在编辑 `aplus/reports.py` / `aplus/live_rebalance.py` /
      `aplus/forward.py`，而 `write_text` 是「截断 + 写」⇒
      一次并发的 `read_text` 可能读到**半截文件** ⇒ `ast.parse` 抛 `SyntaxError`
      ⇒ 守卫**随机误报**（第 112 层）。

    ⇒ 解析失败就**重读**（最多 3 次，间隔 50 ms）。
    ⚠️ 重试**只针对解析失败**，不针对「解析成功但内容不对」——
      后者是真实的判红，不许被重试吃掉。
    """
    last: Exception | None = None
    for i in range(attempts):
        src = path.read_text(encoding="utf-8")
        try:
            ast.parse(src)
            return src
        except SyntaxError as e:      # 只在**语法不完整**时重试
            last = e
            if i < attempts - 1:
                time.sleep(0.05)
    raise AssertionError(
        f"{path} 读了 {attempts} 次都解析不了（最后一个：{last}）\n"
        f"  ⇒ 要么它真的语法非法，要么有人正在改它 —— 两种都要人来判，"
        f"不该由重试悄悄吞掉。")


def _path_literal_names(src: str) -> set[str]:
    """抽出源码里**被当作路径拼出来**的字符串。

    三种真实形态：
      ① `Path(reports_dir) / "x.json"`      —— 字面量直接做除法的右操作数
      ② `NAME = "x.json"` → `Path(d) / NAME` —— 常量名先赋值后拼接
      ③ `Path("x.json")` / `os.path.join(d, "x.json")` —— 字面量做路径函数实参

    🚨 **必须排除 docstring**：裸字符串表达式（`ast.Expr` + `ast.Constant`）
      是文档，不是代码 ⇒ 拿它当证据会得到一条**恒真**的守卫（第 13 层）。
    """
    tree = ast.parse(src)
    docstring_ids = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            docstring_ids.add(id(node.value))

    const_names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    const_names[t.id] = node.value.value

    out: set[str] = set()
    for node in ast.walk(tree):
        # ① 字面量做除法右操作数
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            r = node.right
            if isinstance(r, ast.Constant) and isinstance(r.value, str) \
                    and id(r) not in docstring_ids:
                out.add(r.value)
            elif isinstance(r, ast.Name) and r.id in const_names:
                out.add(const_names[r.id])
        # ③ 字面量做路径函数实参
        if isinstance(node, ast.Call):
            fname = ""
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname in ("join", "Path", "PurePath"):
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                            and id(a) not in docstring_ids:
                        out.add(a.value)
    return out


def test_the_registered_generator_is_the_module_that_actually_writes_the_artifact():
    """🚨 **登记的是写盘的那个模块** —— 不是调用它的脚本。

    `scripts/run_daily.py` 调 `aplus.reports.write_run_status()`，
    但落盘的是 `aplus/reports.py` ⇒ 登记成 `scripts/run_daily.py`
    会让 `commits_touching()` 去追**调用方**的提交历史，
    于是「谁动过这个产物」答的是另一个文件的提交。

    ⇒ 判据落在**源码的写盘点**上（AST，不是子串 ——
      子串会被注释与 docstring 满足，第 13 层）。
    """
    for name in ALL_DELIVERY:
        gen = P.resolve_generator(name)
        src = _read_source_stably(REPO_ROOT / gen)
        assert name in _path_literal_names(src), (
            f"{name} 登记的生成器是 `{gen}`，但那个模块里**没有**把 "
            f"`{name}` 当路径拼出来。\n"
            f"  ⇒ 登记的不是写盘的那个模块 ⇒ 「谁动过这个产物」会答成"
            f"另一个文件的提交历史。\n"
            f"  ⇒ 请从**写盘点**读出真正的模块再登记"
            f"（第 123 层：生成器名绝不许从输出文件名反推）。")


# ── ② 章的路径：必须可搬迁 ──────────────────────────────────────────

def test_the_stamp_writes_a_repo_relative_generator_path(sandbox):
    """🚨 **绝对路径的章不可搬迁。**

    2026-10-16 实测：全仓 55 份 sidecar 里有 **1 份**把 `generator` 写成
    绝对路径（`/Users/.../aplus/scripts/market_nav_caliber.py`），
    其余 54 份都是仓相对路径 ⇒ **口径孤例**。
    成因是那个调用点传 `__file__`（Python 3.9 起 `__file__` 恒为绝对路径）。

    ⇒ 仓一换位置（换机器、换目录、换挂载点），那枚章就指向一个不存在的文件，
    而**章本身看上去完全正常**。
    """
    gen_abs = str((sandbox / "aplus" / "reports.py").resolve())
    rep = _report(sandbox, "run_status.json")

    P.stamp(rep, gen_abs, root=sandbox)

    rec = P.read_stamp(rep)
    assert rec is not None, "章没落盘 ⇒ 这条守卫在测空气"
    assert rec["generator"] == "aplus/reports.py", (
        f"章里记的生成器是 {rec['generator']!r}，应当是**仓相对路径**。\n"
        f"  ⇒ 绝对路径的章不可搬迁：仓一换位置它就指向不存在的文件。")
    assert not Path(rec["generator"]).is_absolute()


def test_a_relative_generator_path_survives_the_stamp_unchanged(sandbox):
    """反向对照：已经是相对路径的**不许被改写**。

    ⇒ 否则「规范化」会变成「顺手把仓外路径也拽进来」，
    而那会把「它在仓外」这个诚实信息藏起来。
    """
    rep = _report(sandbox, "run_status.json")
    P.stamp(rep, "aplus/reports.py", root=sandbox)
    rec = P.read_stamp(rep)
    assert rec["generator"] == "aplus/reports.py"


def test_a_generator_outside_the_repo_keeps_its_absolute_path(sandbox, tmp_path):
    """仓外的生成器**原样保留绝对路径**。

    把它硬转成 `../../..` 既更难读，也会丢掉「它在仓外」这个信息
    （仓外数据根 `data_dir` 就是这么处理的）。
    """
    outside = tmp_path / "outside" / "gen.py"
    outside.parent.mkdir(parents=True)
    outside.write_text("# v1\n", encoding="utf-8")
    rep = _report(sandbox, "run_status.json")
    P.stamp(rep, str(outside), root=sandbox)
    rec = P.read_stamp(rep)
    assert rec["generator"] == str(outside), (
        f"仓外生成器被改写成了 {rec['generator']!r} ⇒ "
        f"「它在仓外」这个信息被藏起来了。")


# ── ③ 登记 ≠ 盖章：判据不许被放松 ──────────────────────────────────

def test_a_registered_but_unstamped_artifact_is_still_unknown(sandbox):
    """★ **登记只是让判据能跑；没有章就仍然是 `unknown`。**

    这是本文件最核心的一条：它让「登记了」与「可引用了」**不能混成一件事**。
    🚨 有人为了让 `freshness()` 变绿而放宽判据（把「没有 sidecar」那条
    问题去掉），这条当场红。
    """
    # 沙箱里造出**登记表指的那个**生成器文件 ——
    # 这样 `freshness()` 不会因为「生成器不在盘上」而 unknown，
    # unknown 的原因就**只剩**「没有章」这一条。
    gen = P.resolve_generator("run_status.json")
    (sandbox / gen).parent.mkdir(parents=True, exist_ok=True)
    (sandbox / gen).write_text("# v1\n", encoding="utf-8")

    rep = _report(sandbox, "run_status.json")
    got = P.freshness(rep, root=sandbox)

    assert got["verdict"] == "unknown", (
        f"没有章的产物判成了 {got['verdict']!r} ⇒ 判据被放松了。\n"
        f"  理由：{got.get('reason')}")
    assert any("sidecar" in p for p in got.get("problems", [])), (
        f"unknown 的理由里应当明写「没有 sidecar」，实际：{got.get('problems')}\n"
        f"  ⇒ `unknown` 的含义是「判据没跑成 / 没有证据」，"
        f"**不是**「事实为否」—— 理由句必须说清是哪一种。")


def test_the_same_artifact_becomes_fresh_only_after_it_is_stamped(sandbox):
    """★ **正控：登记 + 盖章 ⇒ `fresh`**，用来证明上一条不是恒真。

    两条必须同时成立，「登记」与「盖章」才是两个可分辨的状态。
    """
    gen = P.resolve_generator("run_status.json")
    (sandbox / gen).parent.mkdir(parents=True, exist_ok=True)
    (sandbox / gen).write_text("# v1\n", encoding="utf-8")

    rep = _report(sandbox, "run_status.json")
    assert P.freshness(rep, root=sandbox)["verdict"] == "unknown"

    P.stamp(rep, gen, root=sandbox)
    got = P.freshness(rep, root=sandbox)
    assert got["verdict"] == "fresh", (
        f"盖章之后仍判 {got['verdict']!r} ⇒ 理由：{got.get('reason')}")


def test_a_registered_generator_that_is_not_on_disk_makes_the_verdict_unknown(sandbox):
    """🚨 第 123 层再钉一次：**查不到 ≠ 没有**。

    生成器不在盘上 ⇒ commit 走查返回空 ⇒ 旧代码会说「没提交动过它」
    ⇒ 判 `fresh`。⇒ 正确行为是 `unknown`，理由句必须写清
    **「判据本身没跑」**，而不是「查不到动过」。
    """
    # 沙箱里**故意不放**登记的生成器 ⇒ 判据自己跑不起来。
    (sandbox / "aplus" / "reports.py").unlink()
    rep = _report(sandbox, "run_status.json")
    got = P.freshness(rep, root=sandbox)
    assert got["verdict"] == "unknown", f"实际：{got['verdict']!r}"
    assert any("不存在" in p for p in got.get("problems", [])), (
        f"理由里应当明写「生成器路径在仓里不存在」，实际：{got.get('problems')}")
    assert not any("提交" in p for p in got.get("problems", [])), (
        f"理由里**不许**出现「没有提交动过它」—— 那是第 123 层那个假 fresh 的"
        f"原话（什么都没查被读成「查过了」）。实际：{got.get('problems')}")


def test_the_freshness_verdict_domain_is_still_exactly_three_values(sandbox):
    """**判据本身不许松**：三态（fresh / stale / unknown）一个不多一个不少。

    🚨 这是「不许把 `unknown` 改写成 `fresh`」的**结构性**钉法：
    若有人加一个 `assumed_fresh` 之类的第四态，本条当场红。

    ⚠️ 三个用例用的是**登记表里真实的名字** ——
      `freshness()` 靠**文件名**解析生成器，用一个没登记的假名
      只会得到「不在 DEFAULT_GENERATORS 里」这一条 unknown，
      测不到「有没有章」这一层。
    """
    # 把三份产物各自的生成器都放进沙箱
    gens = {name: P.resolve_generator(name) for name in DELIVERY_ARTIFACTS}
    for gen in gens.values():
        (sandbox / gen).parent.mkdir(parents=True, exist_ok=True)
        (sandbox / gen).write_text("# v1\n", encoding="utf-8")

    # fresh：盖章后生成器字节没变
    p_fresh = _report(sandbox, "run_status.json")
    P.stamp(p_fresh, gens["run_status.json"], root=sandbox)
    # stale：盖章后生成器字节变了
    p_stale = _report(sandbox, "holdings.json")
    P.stamp(p_stale, gens["holdings.json"], root=sandbox)
    (sandbox / gens["holdings.json"]).write_text("# v2 改了\n", encoding="utf-8")
    # unknown：没有章
    p_unknown = _report(sandbox, "forward_results.json")

    verdicts = {P.freshness(p, root=sandbox)["verdict"]
                for p in (p_fresh, p_stale, p_unknown)}
    assert verdicts == {"fresh", "stale", "unknown"}, (
        f"实测到的判据取值是 {verdicts}，应当恰好是三态。")
    assert P.freshness(p_unknown, root=sandbox)["verdict"] == "unknown"


# ── ④ 本文件不许写生产产物路径 ──────────────────────────────────────

_AUDIT_ARMED = [False]
_AUDIT_WRITES: list[str] = []


def _audit_open(event: str, args) -> None:
    """audit hook：记录**本进程**开过的写句柄。

    ⚠️ 为什么用 audit hook 而不是「前后比对生产目录的哈希」：
      别的 agent 正在并发写 `data/reports/`（同一台机器、同一份仓），
      前后比对会**把别人的写误判成我的**⇒ 一条会随机误报的守卫
      （第 112 层）。audit hook 只看**本进程**开过的句柄 ⇒ 不会误伤。
    """
    if not _AUDIT_ARMED[0] or event != "open":
        return
    if not args:
        return
    path = args[0]
    if isinstance(path, int):          # 已经是 fd ⇒ 没有路径可比
        return
    mode = args[1] if len(args) > 1 else ""
    if not any(ch in str(mode) for ch in ("w", "a", "x", "+")):
        return
    try:
        _AUDIT_WRITES.append(str(Path(os.fsdecode(path)).resolve()))
    except (TypeError, ValueError, OSError):
        pass


@pytest.fixture(scope="module", autouse=True)
def _watch_production_writes():
    """本模块的测试期间，装一个 audit hook 盯着生产产物目录。"""
    import sys

    sys.addaudithook(_audit_open)
    _AUDIT_ARMED[0] = True
    _AUDIT_WRITES.clear()
    yield
    _AUDIT_ARMED[0] = False
    bad = [p for p in _AUDIT_WRITES
           if p == str(PRODUCTION_REPORTS) or p.startswith(str(PRODUCTION_REPORTS) + os.sep)]
    assert not bad, (
        f"本测试文件写了生产产物路径：{bad}\n"
        f"  ⇒ 测试必须只写 `tmp_path` —— 写生产路径会污染真实交付物"
        f"（第 132 层：测试把自己的输出写进生产产物路径）。")


def test_the_production_reports_directory_is_only_ever_read_by_this_file():
    """**正控**：生产目录存在、且本模块的写句柄一条都不落在它下面。

    ⚠️ 这条不是「证明没写」—— 真正的证明在上面那个 audit hook。
      这条只保证「被盯的那个目录确实存在」，
      否则 hook 盯一个不存在的目录 ⇒ 恒绿。

    🚨⚠️ **这里刻意不逐个断言「生产目录里有那几份产物」。**
      2026-10-20 实测踩到过一次：另一批改动正在重写 `data/reports/`，
      本条**当场红**（那一次是 `downleg_gate.json` 正在被替换的瞬间）
      ⇒ 症状是「交付面清单与现实脱节」，真因是**别的进程在写同一个目录**。
      ⇒ 那是**会随机误报**的守卫（第 112 层：过严的守卫和恒真的一样有害），
      而「产物在不在」这件事由**产物的守护者**（`aplus/reports.py` 等）
      与**用户**负责，不该由本文件在别人写盘的窗口里去查。
      ⇒ 保留的断言只钉**目录本身**（目录不会被并发写删掉）。
    """
    assert PRODUCTION_REPORTS.is_dir(), (
        f"生产产物目录 {PRODUCTION_REPORTS} 不存在 ⇒ audit hook 盯的是空气。")
    assert any(PRODUCTION_REPORTS.glob("*.json")), (
        f"生产产物目录 {PRODUCTION_REPORTS} 里一份 json 都没有 ⇒ "
        f"要么目录是空的，要么 hook 盯错了地方。")
