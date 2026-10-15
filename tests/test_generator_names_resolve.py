"""第 131 层：生成器里的**未定义名**是运行期才炸的那种错，而炸点常在最后一行。

## 本层要抓的东西

2026-10-15 实测：`factor_width_mde.py` 与 `placebo_control.py` 两个
**长跑生成器**（14 分钟 / 46 分钟）都写着

```python
def main(argv=None) -> int:
    expect_bytes = provenance.code_file_sha256(ROOT, "scripts/…")
def _run(args) -> int:
    …
    provenance.stamp(…, expect_bytes=expect_bytes)      # ← 读不到
```

⇒ `NameError: name 'expect_bytes' is not defined`，
**炸在盖章那一行**，而那一行前面**产物已经写完落盘了**
⇒ 目录里有一份没有 sidecar 的新报告，
而任何用「最新 mtime 判这轮跑完了」的办法都会判成跑完了。

## 为什么现有守卫全都看不见

| 检查 | 为什么漏 |
|---|---|
| `compile()` / `ast.parse` | Python 的名字**运行期**才解析；语法完全合法 |
| `test_registered_is_not_enough_the_writer_must_actually_stamp` | 它用 AST 找 `stamp()` **调用点**，不解析实参里的名字 |
| 全仓门禁 | 测试从不调用 `_run()`（它要 14–46 分钟） |

★ 而这不是第一次：`tests/test_report_ready.py::test_a_newer_sibling_means_the_run_died_midway`
的 docstring 记着上一只同型雷（`sum(... for r in results)`，`results` 从未定义）。
**记下来了，但没有守卫** ⇒ 这一层就是那份记录缺的守卫。

## 为什么不能只看这两份文件

名单挂在 `DEFAULT_GENERATORS` 的**值**上，不是某几个我记着的文件名：
新脚本登记那一刻自动进网，改名/删登记则自动出网。

## 这一层量的是**静态可解析性**，不是「跑得通」

它证明不了生成器算得对，只证明它**没有那种一跑到底才炸的名字**。
⚠️ 刻意**不**试图证明「跑得通」——那需要真跑 46 分钟，
而门禁必须是分钟级的。
"""
from __future__ import annotations

import ast
import builtins
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_BUILTINS = frozenset(dir(builtins))

#: 模块级隐式存在的名字。`__file__` 只在**作为模块加载**时存在，
#: 生成器都是 `python scripts/x.py`，那时它在 `__main__` 里同样有。
_DUNDERS = frozenset(
    {"__name__", "__doc__", "__package__", "__loader__", "__spec__",
     "__builtins__", "__file__", "__debug__", "__path__"})

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


class _Frame:
    """一个词法作用域。`is_class` 决定它**能不能**被内层看见。"""

    __slots__ = ("name", "lineno", "bindings", "outer_refs", "is_class")

    def __init__(self, name: str, lineno: int, is_class: bool = False):
        self.name = name
        self.lineno = lineno
        self.bindings: set[str] = set()
        #: `global` / `nonlocal` 声明的名字 ⇒ 它不属于本作用域，往外找
        self.outer_refs: set[str] = set()
        self.is_class = is_class


def _arg_names(a: ast.arguments) -> set[str]:
    out: set[str] = set()
    for grp in (a.posonlyargs, a.args, a.kwonlyargs):
        out |= {x.arg for x in grp}
    for x in (a.vararg, a.kwarg):
        if x is not None:
            out.add(x.arg)
    return out


def _evaluated_at_def_time(fn) -> list[ast.AST]:
    """装饰器与默认值 —— 这些在 `def` 那一刻、**外层**作用域求值。

    漏掉它们会漏报：`def f(x=expect_bytes)` 在模块里读一个未定义名，
    报错点在 `def` 行而不是函数体内。
    """
    a = fn.args
    return (list(getattr(fn, "decorator_list", []))
            + list(a.defaults) + [d for d in a.kw_defaults if d is not None])


def _annotation_nodes(fn) -> list[ast.AST]:
    """注解表达式。**只在模块没有 `from __future__ import annotations` 时求值**。

    ★ 这是假阳性最容易来的地方：`x: SomeTypeOnlyImportedUnderTypeChecking`
      在 lazy 模式下**根本不会在运行期求值**，报了它就是在惩罚一条正确的写法。
      而没有 lazy 模式的模块里注解是真的会求值的 ⇒ 必须查。
    """
    out = [x.annotation for x in (list(fn.args.posonlyargs) + list(fn.args.args)
                                  + list(fn.args.kwonlyargs))
           if x.annotation is not None]
    if getattr(fn, "returns", None) is not None:
        out.append(fn.returns)
    return out


class _UnboundNameChecker(ast.NodeVisitor):
    """扫一个模块，找**运行期会 NameError** 的自由变量。

    ⚠️ 这是 pyflakes「未定义名」那一条的最小实现，只覆盖本仓会出现的形状。
      认不出的形状一律**不报**（宁可漏报也不假报）——
      假报的守卫会被关掉，而关掉的守卫等于没有（第 112 层）。
    """

    def __init__(self, lazy_annotations: bool) -> None:
        self.lazy_annotations = lazy_annotations
        self.frames = [_Frame("<module>", 0)]
        self.findings: list[tuple[str, int, str, str]] = []
        #: `from x import *` ⇒ 本模块不可判，返回 None 让调用方跳过
        self.opaque = False

    # ── 可见名 ────────────────────────────────────────────────────
    def _visible(self) -> set[str]:
        out = set(_DUNDERS) | _BUILTINS
        for fr in self.frames:
            if fr is self.frames[0]:
                out |= fr.bindings
            elif fr.is_class and fr is not self.frames[-1]:
                # ★ **类体不是外层作用域**：类里的 `x = 1` 对方法里的 `x` 不可见。
                #   直接在类体里（fr is last）则可见。
                continue
            else:
                out |= fr.bindings
        return out

    def _bind(self, name: str) -> None:
        self.frames[-1].bindings.add(name)

    def _load(self, name: str, lineno: int) -> None:
        if name in self._visible():
            return
        for fr in self.frames:                       # global / nonlocal ⇒ 往外找
            if name in fr.outer_refs:
                return
        self.findings.append(
            (self.frames[-1].name, lineno, name,
             "类体作用域" if self.frames[-1].is_class else ""))

    # ── 绑定 ──────────────────────────────────────────────────────
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)
        else:
            self._load(node.id, node.lineno)

    def visit_arg(self, node: ast.arg) -> None:
        self._bind(node.arg)

    def visit_Global(self, node: ast.Global) -> None:
        self.frames[-1].outer_refs |= set(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.frames[-1].outer_refs |= set(node.names)

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self._bind(a.asname or a.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if any(a.name == "*" for a in node.names):
            self.opaque = True
            return
        for a in node.names:
            self._bind(a.asname or a.name)

    def _binds_from_target(self, tgt: ast.AST) -> None:
        """For / With / except / match 捕获 / 解包 / 海象 —— 全部落进本作用域。"""
        if isinstance(tgt, ast.Name):
            self._bind(tgt.id)
        elif isinstance(tgt, (ast.Tuple, ast.List)):
            for e in tgt.elts:
                self._binds_from_target(e)
        elif isinstance(tgt, ast.Starred):
            self._binds_from_target(tgt.value)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        # ★ 必须**先绑定再走 body**：`except ValueError as exc:` 的 `exc`
        #   在 try 块内就是合法名字。第一版反过来写（先 body 后绑定），
        #   于是 `except … as e:` 后面用 `e` 的每一行都成了假阳性 ——
        #   实测 3 处，全是假报。假报比漏报更贵：它会把守卫关掉（第 112 层）。
        if node.name:
            self._bind(node.name)
        for s in node.body:
            self.visit(s)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.pattern is not None:
            self.visit(node.pattern)
        if node.name:
            self._bind(node.name)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        for k in node.keys:
            self.visit(k)
        for p in node.patterns:
            self.visit(p)
        if node.rest:
            self._bind(node.rest)

    # ── 作用域边界 ────────────────────────────────────────────────
    def _visit_function(self, fn, *, want_annotations: bool) -> None:
        for n in _evaluated_at_def_time(fn):      # 外层作用域，永远求值
            self.visit(n)
        if want_annotations:
            for n in _annotation_nodes(fn):       # lazy 模式下**跳过**
                self.visit(n)
        self.frames.append(_Frame(getattr(fn, "name", "<lambda>"),
                                  getattr(fn, "lineno", 0)))
        for n in _arg_names(fn.args):
            self._bind(n)
        if isinstance(fn, ast.Lambda):
            self.visit(fn.body)
        else:
            for st in fn.body:
                self.visit(st)
        self.frames.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._bind(node.name)                     # 名字绑在**外层**
        self._visit_function(node,
                             want_annotations=not self.lazy_annotations)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._bind(node.name)
        for n in list(node.decorator_list) + list(node.bases) \
                + [kw.value for kw in node.keywords]:
            self.visit(n)
        self.frames.append(_Frame(node.name, node.lineno, is_class=True))
        for st in node.body:
            self.visit(st)
        self.frames.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_function(node,
                             want_annotations=not self.lazy_annotations)

    def _visit_comp(self, node) -> None:
        # ★ 推导式是**自己的作用域**（Python 3）：`[x for x in y]` 不把 x 泄漏出去。
        #   若把它的目标名算进外层，就漏报（少抓 bug）；按独立作用域算才正确。
        self.frames.append(_Frame("<comprehension>", node.lineno))
        for gen in node.generators:
            self.visit(gen.iter)
            self._binds_from_target(gen.target)
            for cond in gen.ifs:
                self.visit(cond)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.frames.pop()

    visit_ListComp = _visit_comp
    visit_SetComp = _visit_comp
    visit_DictComp = _visit_comp
    visit_GeneratorExp = _visit_comp


def unbound_names(path: Path) -> list[tuple[str, int, str, str]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    lazy = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names)
               for n in tree.body)
    # ★ 必须**两遍**。`ast` 按源码顺序访问，而 Python 的模块级名字在
    #   **整个模块执行完**就全部就位 —— 于是「先定义、后引用」在 Python 里合法，
    #   在单遍检查器里却是假阳性。
    #   实测：`aplus/backtest.py` 的 `_next_bars` 定义在 `_build_bar_cache` 之前，
    #   第一遍报了 16 个「未定义」，全部是这一条。
    pre = _UnboundNameChecker(lazy)
    pre.visit(tree)                     # 第一遍只为收集模块级绑定
    module_bindings = set(pre.frames[0].bindings)

    ck = _UnboundNameChecker(lazy)
    ck.frames[0].bindings |= module_bindings
    ck.visit(tree)                      # 第二遍才是判决
    if ck.opaque:
        return []                      # `import *` 不可静态判定 ⇒ 不报
    return ck.findings


# ══════════════════════════════════════════════════════════════════════
# 守卫
# ══════════════════════════════════════════════════════════════════════

def _generator_scripts() -> list[Path]:
    """名单 = `DEFAULT_GENERATORS` 的**值**（哪些文件真的在产出报告）。

    ⚠️ 刻意不写死文件清单：写死的那天就会有人新增生成器而守卫看不见
    （今天这两颗雷之所以能活过门禁，正是因为守卫盯的是**别的**文件）。
    """
    from aplus.provenance import DEFAULT_GENERATORS

    out: list[Path] = []
    for name in sorted(DEFAULT_GENERATORS):
        gen = DEFAULT_GENERATORS[name]
        if not gen:
            continue
        p = ROOT / gen
        if p.is_file():
            out.append(p)
    return sorted(set(out))


def test_the_generator_list_is_not_silently_empty():
    """**探测器自检**：名单空了 ⇒ 后面所有绿都是假的。

    与 `test_every_stamped_report_resolves_back_to_a_registered_generator`
    里那句「一份 sidecar 都没有 ⇒ 探测器坏了」同一个道理。
    """
    gens = _generator_scripts()
    assert len(gens) >= 10, (
        f"只认出 {len(gens)} 个生成器 ⇒ 名单解析坏了，不是仓里真的只有这些。"
        f"先修解析器再谈这条守卫绿不绿。")
    names = {p.name for p in gens}
    # 今天的两颗雷必须仍在网内 —— 否则这条守卫已经名存实亡
    assert {"factor_width_mde.py", "placebo_control.py"} <= names, (
        f"今天出雷的那两个生成器不在网内：{sorted(names)}")


def test_no_registered_generator_can_raise_nameerror_at_its_last_line():
    """★ 本层主判据：任何**登记在册**的生成器，都不许有运行期 NameError。

    2026-10-15：`expect_bytes` 定义在 `main()`、用在 `_run()` ⇒
    14 分钟和 46 分钟两档都在**盖章那一行**炸，而产物已经落盘。
    """
    bad: list[str] = []
    for p in _generator_scripts():
        for scope, lineno, name, why in unbound_names(p):
            bad.append(f"{p.relative_to(ROOT)}:{lineno} 在 {scope} 里读了 "
                       f"未定义的 {name!r}{('（' + why + '）') if why else ''}")
    assert not bad, (
        "登记在册的生成器里有运行期未定义名 —— 它们只在**跑到底**时才炸，\n"
        "而那一刻产物往往已经写完落盘：\n  " + "\n  ".join(bad) +
        "\n★ 本仓上一只同型雷记在 tests/test_report_ready.py 的 docstring 里"
        "（`sum(... for r in results)`），也是跑到底才炸。")


def test_the_checker_can_actually_find_one():
    """★ 变异守卫的替代品：**正控**。

    一个从没报过警的检查器与一个没有检查器在输出上无法区分。
    所以必须先证明它认得出一道**人写出来的**未定义名 ——
    与 `test_provenance.py` 里「★ 正控前提：先要是 fresh」同一纪律。
    """
    src = ("def main():\n"
           "    expect_bytes = 1\n"
           "def _run():\n"
           "    return expect_bytes + 1\n")
    found = unbound_names_from_source(src)
    assert found, "正控失败：连人写的 `expect_bytes` 都认不出 ⇒ 主判据是装饰"
    assert any(n == "expect_bytes" for _, _, n, _ in found), found

    # 反向对照：修好之后必须**不再**报 —— 证明它认得「什么是不报的」
    ok = ("def main():\n"
          "    pass\n"
          "def _run():\n"
          "    expect_bytes = 1\n"
          "    return expect_bytes + 1\n")
    assert not unbound_names_from_source(ok), "定义在使用处就不该报"


def unbound_names_from_source(src: str) -> list[tuple[str, int, str, str]]:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        p = Path(fh.name)
    try:
        return unbound_names(p)
    finally:
        p.unlink()


def test_every_generator_main_keeps_its_docstring():
    """★ 第二只缺陷：代码插在 docstring 前面 ⇒ 它静默降级成死代码。

    `stamp()` 的 docstring 写着「由 `main()` 开头抓一次」——
    **那句处方就是今天两颗雷的来源**。
    而插进去那两行之后，`main.__doc__` 变成 `None`：
    那段「`begin_run` 是 `@contextmanager`」的告诫还在源码里，
    但任何读 `__doc__` 的工具（本仓至少三处这么干：
    `test_live_rebalance_frequency.py`、`test_provenance.py`、`test_ic_to_return.py`）
    拿到的是 `None`，而**没有一条会报错**。

    ## 判据只抓「降级」，不抓「本来就没有」——第 112 层的教训

    第一版写的是「`main()` 首条语句必须是字符串」，跑出来 **20 多个文件全红**：
    本仓多数生成器的 `main()` 是 `def main(): args = _parse_args()`，
    **从来就没有 docstring**，那是仓库惯例，不是缺陷。

    ⇒ 判据改成可区分两者的形状：**函数体里存在字符串字面量语句，
      但它不在第一条** ⇒ 那必然是被人挤下去的。
      一个字面量都没有 ⇒ 从来没有过 ⇒ 不判。
    """
    demoted: list[str] = []
    for p in _generator_scripts():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in tree.body:
            if not (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == "main"):
                continue

            def _is_str_expr(st) -> bool:
                return (isinstance(st, ast.Expr)
                        and isinstance(st.value, ast.Constant)
                        and isinstance(st.value.value, str))

            pos = [i for i, st in enumerate(n.body) if _is_str_expr(st)]
            if pos and pos[0] != 0:
                st = n.body[pos[0]]
                demoted.append(
                    f"{p.relative_to(ROOT)}:{st.lineno} main() 里那段 "
                    f"{len(st.value.value)} 字的说明在**第 {pos[0] + 1} 条**语句"
                    f"（首条是 {type(n.body[0]).__name__}）⇒ __doc__ 是 None")
    assert not demoted, (
        "main() 的 docstring 被挤到了后面 ⇒ 源码里看得见、__doc__ 里读不到：\n  "
        + "\n  ".join(demoted) +
        "\n★ 判据量的是「有没有一段说明被挤下去」，**不是**「首条语句是不是字符串」"
        "——后者会把 20 多个本来就没 docstring 的生成器全判红，"
        "而那种红会让人直接关掉守卫（第 112 层）。")


def _shell_scripts() -> list[Path]:
    """盘上**实际存在**的 shell 脚本（vendor 目录除外）。

    ⚠️ 本仓的 shell 是**编排脚本**（`.testtmp/` 下的重跑链、变异脚本），
      它们**不入 git** —— 因为它们钉的是当天的 PID 与窗口。
      ⇒ 守卫扫的是盘上真实存在的那批，而不是 `git ls-files`（那会是空的）。
      靠下面的**正控**保证「扫到 0 个」不等于「这条守卫是装饰」。
    """
    skip = (".git", ".venv", "node_modules", "__pycache__")
    return [p for p in sorted(ROOT.rglob("*.sh"))
            if not any(x in p.parts for x in skip)]


#: `$NAME` 紧跟一个非 ASCII 字符。负向断言**只能**排除 `${`、`$(` 和数字 ——
#: 把 `[A-Za-z_]` 也排掉会让这个模式**永远匹配不到任何东西**，
#: 而「0 处命中」看起来与「扫干净了」完全一样（第 131 层的教训）。
_SWALLOWED_VAR = re.compile(r"\$(?!\{|\(|\d)([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])")


def test_bash3_never_swallows_a_variable_followed_by_chinese():
    """★ macOS `/bin/bash` 是 **3.2**：变量名紧跟多字节字符时，bash 会把那些
    字节**吞进变量名**，代入空串 —— **不报错**。

    实测三行（`/bin/bash --version` 自报 3.2.57）：

    | 写法 | 结果 |
    |---|---|
    | `echo "值是 $STATE，好"` | `值是 好` —— `$STATE，` **整段被吞成空串**（终端上留下的是一串替换字符） |
    | `echo "值是 ${STATE}，好"` | `值是 abc，好` ✔ |
    | `echo "值是 $STATE, 好"`（ASCII 逗号） | `值是 abc, 好` ✔ |

    2026-10-15：`chain.sh` 的 `（$STATE，章 sha=…）` 正好中招。
    那次是 `set -u` 顺手救了场（抛 `unbound variable`），
    **而它报的变量名是 `STATE，`，不是 `STATE`** ——
    于是那条报错把我指向了一个不存在的名字，多绕了一圈才定位到真因。

    ★ 这一层**不设「扫到几个文件」的下限**：本仓 shell 不入 git，
      干净克隆上会是 0 个文件。下面那条正控保证它在那种情况下
      仍然证明得了自己有眼睛。
    """
    swallowed: list[str] = []
    for p in _shell_scripts():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in _SWALLOWED_VAR.finditer(line):
                nxt = line[m.start() + len(m.group(0)) + 1]
                swallowed.append(f"{p.relative_to(ROOT)}:{i} ${m.group(1)} 紧跟 "
                           f"{nxt!r} (U+{ord(nxt):04X})")
    assert not swallowed, (
        "bash 3.2 会把这些变量**静默**代入空串：\n  " + "\n  ".join(swallowed) +
        "\n★ 一律写成 `${NAME}`。这不是「细心就能避开」：本轮我自己诊断完"
        "这个坑，二十分钟后就在变异脚本里又写了一遍（`$label：`、`$rc）`）。")


def test_the_shell_scanner_can_actually_see_a_swallowed_variable():
    """★ 正控：一个从没报过警的扫描器与一个没有扫描器在输出上无法区分。"""
    for bad in ('echo "值是 $STATE，好"',
                'echo "  ✗ $label：变异没写进去"',
                'echo "  ✗ $label **活着**（rc=$rc）｜$which"'):
        assert _SWALLOWED_VAR.search(bad), f"正控失败：认不出 {bad!r}"
    # 反向对照：加花括号 / ASCII 分隔的一律不该报
    for good in ('echo "值是 ${STATE}，好"',
                 'echo "  ✓ $label 被杀（rc=1）｜$which"',
                 'echo "值是 $STATE, 好"',
                 'echo "  ✗ ${which}"',
                 'echo "章节 sha=${SHA:0:7}"',
                 'echo "第 $((i+1)) 行"'):
        assert not _SWALLOWED_VAR.search(good), f"假阳性：不该报 {good!r}"


def _always_truthy(node: ast.AST) -> bool:
    """这个表达式**恒为真**吗？（只看语法，不求值）

    覆盖三种写法：`True` / 非空常量、`X or <真常量>`（`or` 的右支吃掉左边）、
    `not False`。
    """
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(_always_truthy(v) for v in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return (isinstance(node.operand, ast.Constant)
                and not bool(node.operand.value))
    return False


def test_no_guard_in_tests_asserts_a_constant_truth():
    """★ 元守卫：守卫自己不许**恒真**。

    本轮变异台实测：把 lint 的 `assert not bad, ...` 改成
    `assert True or not bad, ...`，**没有任何测试变红** ——
    正控验的是**探测器**（正则、`_visible()`），不是**判决的接线**。
    于是「探测器有眼睛、判决被摘掉」这条状态在输出上与全绿完全同形。

    ⇒ 能抓住它的只有一件事：**看那条 `assert` 自己是不是恒真**。

    判据刻意窄：只认三种恒真写法，不做常量折叠、不做符号推理 ——
    宁可漏报，不可假报（第 112 层）。
    """
    bad: list[str] = []
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert) and _always_truthy(node.test):
                bad.append(f"{p.relative_to(ROOT)}:{node.lineno} "
                           f"assert 的判据恒为真 ⇒ 这条守卫是装饰")
    assert not bad, (
        "恒真的断言：\n  " + "\n  ".join(bad) +
        "\n★ 正控验探测器、这条验接线，两样都要在："
        "探测器有眼睛而判决被摘掉，与全绿在输出上同形。")


def test_the_meta_check_can_see_a_constant_truth_assert():
    """★ 正控：元守卫自己也得有眼睛。"""
    for taut in ("assert True",
                 "assert True, 'gone'",
                 "assert maybe_ok or True",
                 "assert not False",
                 "assert 1"):
        assert _always_truthy(ast.parse(taut).body[0].test), taut
    for real in ("assert not bad",
                 "assert len(x) == 3",
                 "assert maybe_ok or fallback",
                 "assert all(x for x in y)",
                 "assert bool(z)",
                 "assert name in self._visible()"):
        assert not _always_truthy(ast.parse(real).body[0].test), real


if __name__ == "__main__":       # 手工跑：看清单和判决
    for p in _generator_scripts():
        print(p.relative_to(ROOT))
    print()
    for p in _generator_scripts():
        for f in unbound_names(p):
            print(f"  {p.relative_to(ROOT)}:{f[1]} {f[2]} in {f[0]}")
    print("(以上为空即绿)")
    raise SystemExit(pytest.main([__file__, "-q"]))
