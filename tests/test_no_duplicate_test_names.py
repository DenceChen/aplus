"""同一模块内不许有重名的 `def test_*`（2026-10-15 实测抓到 4 处）

## 洞在哪：同名 `def` 会**静默覆盖**，而 pytest 的条数不变

模块顶层 `def f(): …` 第二次定义时，**模块字典里前一份被无声丢掉**
（不报错、不告警、不影响 pytest 收集条数 —— pytest 收集的是
**后定义的那一份**）。
⇒ 于是一份守卫可以被「修好了、搬过来了、想再留一份对照」的动作**静默吃掉**，
而 `pytest` 打印的 `N passed` **一格都不变**。
⇒ 删掉的若是活的那份，测试立刻红；删掉的是死的那份，**全绿**，
而**覆盖已经没了**。这就是它能活下来的原因：**症状与「一切正常」逐位同形。**

实测（`tests/**/*.py` 218 个文件）：4 处，全部是
「上一轮重写时把旧的留在原地、新的写在后面」。

## 为什么判据必须**按文件分组**而不是全树计数

同仓库有 **3 组跨不同文件的同名 `test_`**（实测，不是构造）：

| 同名函数 | 分属 |
|---|---|
| `test_a_blank_window_end_is_refused_with_a_diagnosable_message` | `test_backtest_walk_forward_weights.py` / `test_basket_weighting.py` |
| `test_the_branch_order_is_the_conservative_one` | `test_factor_attribution.py` / `test_placebo_control.py` |
| `test_the_artifact_is_registered_and_stamped` | `test_nav_caliber_judgment.py` / `test_score_ranking_power.py` |

它们是**刻意同款守卫、每个模块各留一份**（各自的模块常量不同）。
⇒ **按全树计数写出来的守卫会把这 3 组判红**，而它们没做错任何事
⇒ 那是**过严守卫**（第 112 层）：它会把人推向「为了变绿去改名」这个错误动作，
而改名**恰好破坏**了「这两个模块各有自己的版本」这件事。
⇒ 所以下面那条主守卫按 `(文件, 名字)` 分组，
并且这 3 组被钉成**活体反控** —— 它们比合成夹具更难被绕过，
因为它们是真实存在的、结构上就应当豁免的样本。

## 三条形态纪律（每一条都有正控 / 反控）

1. **按名字计数，不数 AST 节点。** 同名 `def` 是**两个节点**，
   而模块里活着的**只有一个**。数节点会把「文件里有两处同名定义」数对，
   但**「哪一份活着」这件事它答不了** —— 而那正是本守卫要守的。
2. **只看模块顶层。** 内层 `def`（函数里的局部函数）属于别的作用域，
   那里重名是**合法的**（局部遮蔽）。把它们算进来 = 过严。
3. **扫 `tests/**/*.py` 而不是 `tests/test_*.py`。** 实测
   **0 处**「非 `test_*.py` 的文件里定义 `test_`」⇒ 无需白名单。
   ⚠️ 这条是**实测结论**而不是前提 —— 若哪天出现了，
   报出来的会是真实违规，而不是「白名单忘了更新」。

## 🚨 本文件自身的两个失效方向

- **探测器坏了 ⇒ 主守卫恒绿**（形状 16）。故
  `…probe_actually_sees_the_test_suite` 先断言「扫到的 `test_` 定义数
  远超违规数」—— 这条是**恒真守卫的解药**。
- **判据被改成全树计数 ⇒ 3 组活体豁免变成违规 ⇒ 假红**（第 112 层）。
  故 `…live_cross_module_names_are_exempt` 把那 3 组**钉成数据**，
  一旦有人收紧分组方式，这两条会**同时**变红，而不是让人去猜该改哪边。

## ⚠️ 变异必读

判「这组守卫是不是安慰剂」**只能靠变异**，且**只有 `rc == 1` 才算被杀**
（rc∈{2,3,4,5} = 收集/用法/内部错误 ⇒ 不可验证）。
**跑变异后必须先 `grep` 确认改动落在文件里**再看 pytest 结果，
顺序反过来会得出相反结论（第 14/148 层）。
"""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


# ── 探测器（唯一的实现；变异自测与主判据共用它）──────────────────

def _top_level_test_names_in(src: str, filename: str = "<synthetic>") -> list[str]:
    """一份源码里**算作「测试」的名字**：模块顶层的、以 `test_` 开头的 `def`。

    ⚠️ 这是「什么算一个测试」这条判据的**唯一一份实现**。
    初版把它写了两遍（`_duplicate_names_per_module` 与 `_top_level_test_names`
    各一份 `startswith("test_")` + 各自解析一遍 AST），而**没有任何断言守着它**
    ⇒ 写变异规格时才发现：前缀过滤一旦失效，没有任何守卫会红。
    ⇒ 判据**必须收敛成单点**，否则它有两个可以各自漂移的副本。

    ⚠️ 三个刻意的形态选择，缺一个守卫就会假绿或假红：
      · 只看 `tree.body`（**模块顶层**）—— 内层 `def` 是别的作用域，
        那里重名是合法的局部遮蔽；
      · 只认 `def`/`AsyncFunctionDef`，不认 `lambda`/赋值；
      · 前缀 `test_` 不可省 —— 否则同名 helper 会被报成违规。
    """
    tree = ast.parse(src, filename=filename)
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")]


def _duplicate_names_per_module(src: str, filename: str = "<synthetic>") -> list[str]:
    """一个源文件里**重名的模块顶层 `test_`** 定义名，按名字返回。

    `Counter` 按**名字**计数是刻意的：同名 `def` 在模块字典里静默覆盖，
    数 AST 节点会把「文件里有两处同名定义」数对，却答不出「哪一份活着」。
    """
    names = _top_level_test_names_in(src, filename)
    return sorted(name for name, c in Counter(names).items() if c > 1)


def _all_test_files() -> list[Path]:
    return [p for p in sorted(TESTS.rglob("*.py")) if "__pycache__" not in p.parts]


def _top_level_test_names(p: Path) -> Counter:
    return Counter(_top_level_test_names_in(p.read_text(encoding="utf-8"), str(p)))


def _duplicates_across_suite() -> list[str]:
    """全 `tests/` 的模块内重名违规清单。**唯一的扫描实现。**

    ⚠️ 主判据与活体反控**共用这一个函数**（第 131 层：
    「正控要验判决不只验探测器」）—— 反控若自己重算一遍分组，
    它验的就是探测器而不是判决，于是「把分组键从文件改成全局」
    这个变异只会让主判据假红、而反控纹丝不动 ⇒ 反控成了装饰。
    """
    bad = []
    for p in _all_test_files():
        counts = _top_level_test_names(p)
        for name, c in sorted(counts.items()):
            if c > 1:
                lines = [n.lineno for n in
                         ast.parse(p.read_text(encoding="utf-8")).body
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and n.name == name]
                bad.append(f"{p.relative_to(ROOT)}::{name} ×{c} 行 {lines}")
    return bad


# ── ① 主判据：模块内不许重名 ────────────────────────────────────

def test_no_module_defines_the_same_test_name_twice():
    """任何一个 `tests/**/*.py` 里，同名 `test_*` 的模块顶层定义必须唯一。

    🚨 **失效方向是「删掉死的那份」⇒ 全绿而覆盖没了**，
    与「代码坏了」在终端上逐位同形。
    ⇒ 失败消息必须把**行号**打出来 —— 只有行号才能让人认出哪一份活着。
    """
    bad = _duplicates_across_suite()
    assert not bad, (
        "这些模块里有重名的 `def test_*`（后定义的那份会**静默覆盖**前一份，"
        "而 pytest 收集条数一格不变）：\n  " + "\n  ".join(bad) +
        "\n  ⇒ 被覆盖的那份若是活守卫，覆盖就没了，而**没有任何东西会红**。"
        "\n  ⇒ 修法：删掉死的那份（保留**行号较大**的那份，"
        "pytest 收集的是后定义的那一份），"
        "而不是改名字 —— 改名会掩盖「这里曾经有两份」这个事实。")


# ── ② 探测器能力：恒真守卫的解药（形状 16）──────────────────────

def test_the_duplicate_probe_actually_sees_the_test_suite():
    """探测器必须**真的扫到了整个测试套件**，否则 ① 永远绿。

    这不是装饰性检查：若 `_all_test_files()` 返回空、或解析器对每个文件
    都返回空 `Counter`，① 会**因为找不到任何东西而全绿** ——
    那与「代码干净」在终端上逐位同形（形状 16）。
    """
    total_defs = sum(sum(_top_level_test_names(p).values())
                     for p in _all_test_files())
    assert len(_all_test_files()) > 100, (
        f"只扫到 {len(_all_test_files())} 个测试文件（实测 218）⇒ "
        f"探测器的作用域变了，① 很可能正对着空气判决")
    assert total_defs > 500, (
        f"扫到 {total_defs} 个模块顶层 `test_` 定义（实测远超 500）⇒ "
        f"探测器认不出定义了 ⇒ ① 恒绿")


def test_the_duplicate_probe_catches_a_synthetic_in_module_redefinition():
    """**正控**：同一模块里重名一次就必须被抓到。"""
    src = ("def test_alpha():\n    assert True\n"
           "\n"
           "def test_alpha():\n    assert True\n")
    got = _duplicate_names_per_module(src)
    assert got == ["test_alpha"], (
        f"合成源码里同模块重名，探测器返回 {got!r} ⇒ ① 是装饰")


# ── ③ 反控：不同模块同名**必须不被抓**（否则是过严守卫）────────

def test_the_duplicate_probe_ignores_the_same_name_in_different_modules():
    """**反控**：两个模块各定义一次同名 `test_` ⇒ 必须**不**报。

    ⚠️ 这是 ① 的**正确性边界**，不是补充：同仓库实测有 3 组这样的活样本
    （见模块 docstring）。把它们判红会把人推向「为了变绿去改名」，
    而改名恰好破坏了「每个模块各留自己那份」这件事（第 112 层）。
    """
    a = "def test_alpha():\n    assert True\n"
    b = "def test_alpha():\n    assert True\n"
    assert _duplicate_names_per_module(a) == []
    assert _duplicate_names_per_module(b) == []
    assert _duplicate_names_per_module(a) == _duplicate_names_per_module(b) == [], (
        "正控失败：单模块无重名时探测器都报东西了 ⇒ ① 会永远红")


def test_the_duplicate_probe_ignores_a_local_def_that_shadows_the_name():
    """**反控**：内层 `def`（局部遮蔽）合法，不许算作模块级重名。

    内层 `def` 属于别的作用域，那里重名是**正常的局部变量遮蔽**。
    把它们算进来 = 过严。
    """
    src = ("def test_outer():\n"
           "    def test_inner():\n"
           "        pass\n"
           "    def test_inner():\n"       # 局部重名：合法
           "        pass\n"
           "    test_inner()\n")
    assert _duplicate_names_per_module(src) == [], (
        "探测器把**内层** `def` 的重名算成了模块级违规 ⇒ 过严")


def test_the_duplicate_probe_ignores_duplicated_helpers_that_are_not_tests():
    """**反控**：同名但**不叫 `test_*`** 的 `def` 不算违规。

    ⚠️ 这条守的是 `_top_level_test_names_in()` 里那行 `startswith("test_")`。
    没有它时，那行前缀过滤**可以整行删掉而没有任何守卫会红** ——
    而后果是主判据把 `helper()` / `fake_source()` / `_raw()` 这类
    **同名重定义完全合法**的模块级 helper 报成违规 ⇒ 过严（第 112 层）。
    那种报告没人会去核实行号，只会把人推向「去改个名」，
    而改名**正是**本文件要防的那个动作的伪装版。

    ⚠️ 收敛成单点**不等于**它有牙 —— 收敛只让变异能一次打到多处，
    牙要由这条断言来给。
    """
    src = ("def helper():\n    pass\n\ndef helper():\n    pass\n")
    assert _top_level_test_names_in(src) == [], (
        f"单点判据把非 `test_` 的 def 也算成了测试："
        f"{_top_level_test_names_in(src)!r}")
    assert _duplicate_names_per_module(src) == [], (
        "探测器把同名重定义的 helper 报成了违规 ⇒ ① 会假红（过严守卫）")


def test_live_cross_module_names_are_exempt():
    """**活体反控**：仓里那 3 组跨文件同名必须仍然分属不同文件。

    为什么用活样本而不是只靠合成夹具：这 3 个名字**每天都在被收集**，
    它们一旦被并成一处（例如某个守卫被「提到公共模块」的重构顺手改掉），
    本条会立刻红 —— 而那正是 ① 需要保持绿的真实场景。
    ⚠️ 若哪天它们**真的**合并了，改的是本条，不是 ①。
    """
    known = {
        "test_a_blank_window_end_is_refused_with_a_diagnosable_message",
        "test_the_branch_order_is_the_conservative_one",
        "test_the_artifact_is_registered_and_stamped",
    }
    seen: dict[str, set[str]] = {}
    for p in _all_test_files():
        for name in _top_level_test_names(p):
            seen.setdefault(name, set()).add(str(p.relative_to(ROOT)))
    shared = {n: files for n, files in seen.items()
              if len(files) > 1 and n.startswith("test_")}
    missing = known - set(shared)
    assert not missing, (
        f"这些名字原本跨 {len(known)} 个文件共存，现在每个只剩一处了："
        f"{sorted(missing)}\n"
        f"  ⇒ 要么有人把它们合并了（那本条该更新），"
        f"要么探测器认不出定义了（那 ① 正对着空气判决）。"
        f"\n  ⇒ 当前实测跨文件同名 {len(shared)} 组。")
    # ★ **验判决，不验探测器**（第 131 层）。若这里只重算一遍分组，
    #   「把分组键从文件改成全局」这个变异就只会让 ① 假红、而本条绿
    #   ⇒ 反控成了装饰。下面直接读 ① 的判决清单。
    flagged = {entry.split("::")[1].split(" ")[0]
               for entry in _duplicates_across_suite() if "::" in entry}
    wrongly = known & flagged
    assert not wrongly, (
        f"① 把这 {len(wrongly)} 组**跨文件同名**判成了模块内重名："
        f"{sorted(wrongly)}\n"
        f"  ⇒ 分组键不再按文件了 —— 它们是**刻意同款守卫、每个模块各留一份**，"
        f"被判红会把人推向「为了变绿去改名」，"
        f"而改名恰好破坏了「各模块各有自己那份」这件事（第 112 层：过严守卫）。")


# ── ④ 白名单的依据（实测结论，不是前提）────────────────────────

def test_no_non_test_named_file_under_tests_defines_a_test():
    """`tests/` 下**只有** `test_*.py` 定义 `test_*`（实测 0 处）。

    这是 ① 能扫 `tests/**/*.py` 而**不配白名单**的依据。
    ⚠️ 若哪天这条红了：说明出现了「helper 文件里定义了 test_」——
    pytest **会收集它**（收集规则只认函数名与 `python_files` 的交集，
    而这里证明的是「连非 `test_*.py` 都会被扫到」），
    所以那是 ① 的**新违规**，不是「白名单要更新」。
    """
    offenders = []
    for p in _all_test_files():
        if p.name.startswith("test_"):
            continue
        if _top_level_test_names(p):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, (
        f"`tests/` 下这些非 `test_*.py` 文件里定义了 `test_*`：{offenders}\n"
        f"  ⇒ 它们**不在** pytest 的默认收集范围里，却会被 ① 扫到 —— "
        f"若它们因此被报成重复，那是 ① 的作用域比 pytest 宽，"
        f"而不是「白名单该加一条」。")
