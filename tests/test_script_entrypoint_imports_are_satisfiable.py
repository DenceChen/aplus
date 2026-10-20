"""★ 第 24 种形状：**守卫测的是函数，脚本跑不起来**（2026-10-15 实测发现）

## 实例（已修）

`scripts/basket_weighting.py` 有**两处**硬伤，都在主路径上：

1. `sys.path.insert(0, str(ROOT / "scripts"))` —— 只插了 `scripts/`，
   **没插仓库根** ⇒ 命令行直接跑必然 `ModuleNotFoundError: No module named 'aplus'`
   （脚本运行时 `sys.path[0]` 是 `scripts/`，cwd **不在**里面；
   注意 `python -c` 才把 cwd 放进去，`python <脚本>` 不放）。
2. `from aplus.snapshot import Snapshot` —— `aplus/snapshot.py` **存在**（9531 字节）
   却**没有定义 `Snapshot`** ⇒ `ImportError`。模块搬走/改名，调用点没跟着改。

⇒ 两条守卫（`tests/test_basket_weighting.py`，全部绿）**一条都没抓到**。

## ★ 为什么没抓到：测试的导入方式绕过了入口点

`tests/test_basket_weighting.py` 用

    importlib.util.spec_from_file_location(...)

导入模块。**这个方式自带仓库根在 `sys.path` 上**（pytest 从仓库根启动），
于是脚本自己那行 `sys.path.insert` **根本不需要生效** ——
测试环境替它补上了。

⇒ 形状 24 的一般形态：**「按路径导入」比「按脚本运行」多给了它一样东西，
而那样东西恰好补上了脚本的缺陷。**

## 本守卫判什么

判**导入期依赖是否自足**，不判「脚本能否跑完」（后者要真跑面板，风险高）。

做法是**求值**而不是文本匹配：把模块级的简单路径表达式（`ROOT = Path(__file__)...`）
放进一个小命名空间，逐个求值 `sys.path.insert` 的参数，看**仓库根**在不在里面。

⚠️ 为什么不文本匹配：今天已经三次栽在文本/正则上
（字面量 grep 找生成器、正则匹配参数却要求出现调用名、`unparse` 的单双引号）。
**求值一次就够，文本猜测要错三次。**
"""
from __future__ import annotations

import ast
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"

# 仓库内的包/模块前缀：导入它们就必须让仓库根在 `sys.path` 上
_LOCAL_PREFIXES = ("aplus.", "aplus")


def _module_level_imports(tree: ast.Module):
    """**只**收模块级的 import 语句。

    🚨 2026-10-21：原实现用 `ast.walk(tree)`（**全树**）⇒
    把**函数体内的惰性 import**也算成「导入期依赖」
    ⇒ 实测误报 `scripts/_fundamental_family_stats.py`：
    它的 `from aplus.factor_fundamental import bare_code` 在**函数体内**（惰性）
    ⇒ 导入期**不跑** ⇒ 调用时调用方早已把仓根放进 sys.path ⇒ **那个文件是对的**。

    ⇒ 而一条**过严的守卫比恒真的更危险**：它会让人去「修」本来正确的代码
    （本仓第 112 层）⇒ ⇒ **只收模块级**。
    ⚠️ 模块级的 `if`/`try` 里的 import **也算**（导入期会跑）
    ⇒ 递归进它们，但不进 `FunctionDef`/`ClassDef`。
    """
    def walk_body(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue                      # 惰性 import：导入期不跑
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node
                continue
            for attr in ("body", "orelse", "finalbody", "handlers"):
                sub = getattr(node, attr, None)
                if isinstance(sub, list):
                    yield from walk_body(sub)

    return list(walk_body(tree.body))


def _imports_local_module(tree: ast.Module) -> bool:
    for node in _module_level_imports(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
            if node.level:  # from . import x
                return True
        for m in mods:
            if m == "aplus" or m.startswith("aplus."):
                return True
    return False


def _module_namespace(tree: ast.Module, script: pathlib.Path) -> dict:
    """把模块级「简单赋值」收进命名空间，供后续求值。

    只收**能安全求值**的：常量、`Path(__file__)` 及其 `.resolve()/.parent/.parents[]` 链。
    遇到算术、调用其它函数等一律跳过（跳过只会让本守卫更严，不会更松）。
    """
    from pathlib import Path

    ns: dict = {"__file__": str(script), "Path": Path, "ROOT": _ROOT}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        try:
            ns[tgt.id] = eval(compile(ast.Expression(node.value), "<m>", "eval"), {"__builtins__": {}}, ns)
        except Exception:
            continue
    return ns


def _inserted_paths(tree: ast.Module, ns: dict) -> set[str]:
    # ⚠️ 第一版传 `{"__builtins__": {}}`，于是 `str` 也不可用 ——
    #   而仓里插入的全是 `str(Path(...))` 形态 ⇒ 求值器对**所有**脚本返回空集。
    #   （正控 `test_the_module_namespace_evaluation_...` 当场抓到，不是靠 review。）
    #   这里只放**无副作用**的名字：路径推导用得着的，仅此而已。
    import os

    safe = {"__builtins__": {}, "str": str, "repr": repr, "int": int,
            "Path": pathlib.Path, "os": os, "sys": sys, "abspath": os.path.abspath}
    out: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "insert" or ast.unparse(node.func.value) != "sys.path":
            continue
        for arg in node.args[1:]:
            try:
                out.add(str(eval(compile(ast.Expression(arg), "<a>", "eval"), safe, ns)))
            except Exception:
                continue
    return out


def _imports_module(tree: ast.Module, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == name:
            return True
    return False


def _bootstrap_provides_repo_root() -> bool:
    """★ `_bootstrap` 豁免**必须条件于实测**，不能无条件。

    仓里的正确形态有两种（`ic_guided_race.py:48-49` 是第一种）：

      A. 自己把仓库根插进去；
      B. 只插 `scripts/`，然后 `from _bootstrap import …` ——
         `_bootstrap.py:23-25` 自己 `sys.path.insert(0, str(_ROOT))`。

    第一版守卫只认 A，于是把 B 形态的 `factor_width_mde.py` / `placebo_control.py`
    判成缺陷。**那是过严，而过严的守卫和恒真的一样有害** ——
    它会把人推向「为了变绿而乱插 sys.path」这个错误修法。

    ⚠️ 但豁免也不能无条件：否则「import 了 `_bootstrap`」就等于通行证，
    它哪天不干了，所有 B 形态脚本**一起**静默失效。
    ⇒ 这里每次都重新实测 `_bootstrap` 到底插没插。
    """
    bs = _SCRIPTS / "_bootstrap.py"
    if not bs.is_file():
        return False
    tree = ast.parse(bs.read_text(encoding="utf-8"))
    return str(_ROOT) in _inserted_paths(tree, _module_namespace(tree, bs))


def _scripts_needing_repo_root() -> dict[str, tuple[pathlib.Path, ast.Module]]:
    out = {}
    for py in sorted(_SCRIPTS.rglob("*.py")):
        if py.name.startswith("_diag"):
            continue  # 诊断探针，刻意钉死具体路径
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if _imports_local_module(tree):
            out[str(py.relative_to(_ROOT))] = (py, tree)
    return out


# ── 正控 ────────────────────────────────────────────────────────

def test_the_scanner_finds_enough_scripts_to_be_worth_listening_to():
    found = _scripts_needing_repo_root()
    assert len(found) >= 30, (
        f"★ 只认出 {len(found)} 个导入仓内模块的脚本（需要 ≥30），扫描器多半瞎了"
    )
    assert "scripts/basket_weighting.py" in found, \
        "★ 刚修好的那个脚本没被认出来 ⇒ 扫描器认不出 `aplus.store` 的导入形态"


# ── 主守卫 ──────────────────────────────────────────────────────

def test_every_script_importing_local_modules_puts_the_repo_root_on_sys_path():
    """★ 导入期依赖必须**自足**：脚本不能依赖「别人替它把仓库根放进 sys.path」。

    这正是形状 24 的病根 —— 测试按路径导入时自带仓库根，把脚本的缺陷补上了。

    ★ 两种合法形态都算数（见 `_bootstrap_provides_repo_root` 的说明）：
      A. 自己插仓库根；B. 插 `scripts/` + `from _bootstrap import …`。
    """
    offenders = []
    bootstrap_ok = _bootstrap_provides_repo_root()
    for rel, (py, tree) in sorted(_scripts_needing_repo_root().items()):
        inserted = _inserted_paths(tree, _module_namespace(tree, py))
        if str(_ROOT) in inserted:
            continue  # 形态 A
        via_bootstrap = bootstrap_ok and _imports_module(tree, "_bootstrap") and inserted
        if via_bootstrap:
            continue  # 形态 B
        if not inserted:
            offenders.append(f"{rel}：没有任何可求值的 sys.path.insert")
        else:
            offenders.append(
                f"{rel}：只插入了 {sorted(inserted)}，**仓库根不在其中**"
                f" ⇒ 命令行直接跑会 ModuleNotFoundError"
            )
    assert not offenders, (
        "★ 这些脚本导入 `aplus.*` 却没把仓库根放进 sys.path：\n   "
        + "\n   ".join(offenders)
        + "\n   ⇒ 二选一：① 自己插仓库根（`sys.path.insert(0, str(ROOT))`，"
              "见 `ic_guided_race.py:48`）；② 插 `scripts/` 后 "
              "`from _bootstrap import …`（见 `factor_width_mde.py:50`）。"
    )


def test_the_bootstrap_exemption_is_not_unconditional():
    """★ 正控：`_bootstrap` **今天**确实把仓库根插进去了。

    没有这条，上面那条守卫里 `bootstrap_ok` 就只是一个恒为真的乐观假设 ——
    而 B 形态的脚本全靠它。`_bootstrap` 一旦不插，全部同时静默失效。
    """
    assert _bootstrap_provides_repo_root(), (
        "★ `scripts/_bootstrap.py` 不再把仓库根放进 sys.path 了！\n"
        "   ⇒ 所有「插 scripts/ + import _bootstrap」形态的脚本会**同时**失效，"
        "而它们各自的守卫都不会响。要么改 `_bootstrap`，要么把这些脚本改成形态 A。"
    )


def test_the_module_namespace_evaluation_can_actually_resolve_a_real_insert():
    """★ 正控之二：求值器本身要能**解出真实路径**，否则上一条可能恒红/恒绿。

    断言的是**解析后**的集合里确实含仓库根 —— 用一个已知正确的脚本。
    """
    py = _ROOT / "scripts" / "backtest_walk_forward_weights.py"
    tree = ast.parse(py.read_text(encoding="utf-8"))
    inserted = _inserted_paths(tree, _module_namespace(tree, py))
    assert str(_ROOT) in inserted, (
        f"★ 求值器解不出 `{py.name}` 的仓库根（只解出 {sorted(inserted)}）"
        f" ⇒ 上一条守卫的判决不可信"
    )


def test_no_script_imports_a_sibling_script_module_by_bare_name():
    """★ 兄弟模块（`scripts/*.py`）**不许裸 import**。

    ## 为什么这是一个真的坑（2026-10-21 实测）

    `scripts/run_daily.py` 里写 `from signal_scope import ...` ⇒
    **直接跑脚本时**成立（`sys.path[0]` 就是 `scripts/`），
    **被当模块 import 时**崩（`sys.path[0]` 是仓根或 cwd ⇒ 那里没有 `scripts/`）
    ⇒ ⇒ **同一个文件，两种跑法，两个结果** —— 而生产跑的是脚本、测试跑的是模块。

    ⇒ **正确写法是包形式** `from aplus.signal_scope import ...`
    ⇒ 仓根在 sys.path 上时它**两种跑法都成立**
    （仓根已由 `run_daily.py` 自己插入；命名空间包不需要 `scripts/__init__.py`）。

    ⚠️ 这条守卫**锚在 AST 的 import 节点上**，不是全文子串
    —— 否则一个提到 `signal_scope` 的注释就会把它判成违规（第 13 条）。
    """
    offenders = []
    for py in sorted(_SCRIPTS.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # ⚠️ **形态 B 豁免**：仓里既定的合法形态是「插 `scripts/` + `from _bootstrap import …`」
        # （见 `factor_width_mde.py:50`）⇒ 那种脚本的**裸**兄弟 import 是**对的**
        # （`scripts/` 在 sys.path 上）⇒ 不许被本守卫判成违规
        # —— 否则又是一条**过严的守卫**（第 112 层）。
        if str(_SCRIPTS) in _inserted_paths(tree, _module_namespace(tree, py)):
            continue
        for node in _module_level_imports(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
                if node.level:
                    continue          # 相对 import：天然在同包内
            for m in mods:
                if not m or m.startswith("scripts."):
                    continue
                if m == "_bootstrap":
                    continue          # 形态 B 的入口模块：仓里指定的 bootstrap
                if (_SCRIPTS / f"{m}.py").exists():
                    offenders.append(
                        f"{py.relative_to(_ROOT)}：裸 import `{m}`（兄弟模块）"
                        f" ⇒ 直接跑脚本时成立、被当模块 import 时崩"
                        f" ⇒ 改成 `from scripts.{m} import …`（包形式，两种跑法都成立）")
    assert not offenders, (
        "★ 这些脚本用裸名 import 兄弟模块：\n   " + "\n   ".join(offenders)
    )
