"""`cross_sections` 的下划线元数据键：**读的每个键都必须有写的地方**。

## 这条守卫防的是什么

`aplus/backtest.py` 曾经有一行：

```python
cost = cross_sections.get("_cost", STOCK_COST_DEFAULT)
```

它看起来是一个**开关**——「成本可以按日覆盖」。
但 `build_cross_sections` **从不写** `_cost`，全仓（含 tests）
也没有第二处写入。所以那一行**永远**走默认分支。

这是本项目遇到的**第五种静默失效形状**，与已有的四种都不同：

| | 前四种 | 这一种 |
|---|---|---|
| 表现 | **算错** | **假装能调，实际焊死** |
| 后果 | 数字错 | 数字对，但**口径是错的** |
| 为什么阴险 | — | 出错的是**读者的判断**，不是计算 |

留着它比删掉它更坏：读代码的人看到「成本可覆盖」，
于是**在它调不动的情况下**以为自己已经考虑过成本口径了。
一句假话比没有这句话危险，因为它免掉了追问。

## 为什么这条测试不是安慰剂

CLAUDE.md 第 4 条警告过：R 矩阵无论怎么错标都仍然对称、
对角线仍然全 1，所以「对称 + 单位对角」这类校验**恒真**——
它是安慰剂，不是守卫。

所以这条测试必须能被证伪。下面 `test_the_guard_itself_detects_a_key_with_no_writer`
就是它的**反向测试**：现场造一个无主的键，守卫必须当场变红。
没有那条反向测试，本文件的所有绿色都不可信。
"""
import ast
from pathlib import Path

APLUS = Path(__file__).resolve().parent.parent / "aplus"

#: 读元数据用的变量名。`build_cross_sections` 的返回值在这套代码里
#: 到处都叫这几个名字，所以守卫按**变量名**而不是按类型追。
READERS = {"cross_sections", "xs", "sections"}


def _str_keys(node: ast.AST) -> set[str]:
    """节点里出现的所有字符串字面量（只取下划线开头的元数据键）。"""
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.startswith("_")}


def _read_keys(tree: ast.AST) -> set[str]:
    """所有 `reader.get("_k", …)` / `reader["_k"]` 读出来的键。"""
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            if (f.attr == "get" and isinstance(f.value, ast.Name)
                    and f.value.id in READERS and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
              and node.value.id in READERS
              and isinstance(node.slice, ast.Constant)
              and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
    return keys


def _write_keys(tree: ast.AST) -> set[str]:
    """所有 `writer.setdefault("_k", …)` 写进去的键。"""
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    return keys


def _builder_keys(tree: ast.AST) -> set[str]:
    """`build_cross_sections` 里所有字典字面量的键——那就是元数据的产地。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_cross_sections":
            return {k for k in _str_keys(node)}
    return set()


def _module(name: str) -> ast.AST:
    return ast.parse((APLUS / name).read_text(encoding="utf-8"))


# ── 守卫本体 ────────────────────────────────────────────────────

def test_every_metadata_key_that_is_read_is_also_written_somewhere():
    """`cross_sections.get("_k", …)` 里的 `_k`，全仓必须**有一处写入**。

    写入点 = `build_cross_sections` 的字典字面量 ∪ 任意 `setdefault("_k", …)`。

    没有写入点的键就是 `_cost` 那种：**永远返回默认值**，
    而调用方以为自己在读一个可配置项。
    """
    reads: dict[str, str] = {}
    for py in sorted(APLUS.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for k in _read_keys(tree):
            reads.setdefault(k, py.name)

    backtest = _module("backtest.py")
    written = _builder_keys(backtest) | _write_keys(backtest)

    orphans = {k: v for k, v in reads.items() if k not in written}
    assert not orphans, (
        f"这些元数据键只被读、从没被写过 —— 调用方拿到的一直是默认值："
        f"{orphans}。\n"
        f"要么在 build_cross_sections 里真的写它，要么把那次 get 删掉——"
        f"留着一个焊死的开关，比没有开关更坏。"
    )


def test_the_guard_itself_detects_a_key_with_no_writer():
    """**反向测试**：守卫必须能当场抓到 `_cost` 那种键。

    没有这一条，上面那条测试的绿色毫无意义——
    一个恒真的守卫和没有守卫看起来一模一样。
    这里不碰真实文件，只喂一段假的源码给同一个解析函数。
    """
    fake_reader = ast.parse('xs.get("_ghost_key", 0)')
    fake_builder = ast.parse(
        'def build_cross_sections():\n'
        '    return {"_dates": [], "_market": {}}\n')
    reads = _read_keys(fake_reader)
    written = _builder_keys(fake_builder) | _write_keys(fake_builder)
    assert "_ghost_key" in reads
    assert "_ghost_key" not in written, "守卫若抓不到无主键，它就是安慰剂"
    assert reads - written == {"_ghost_key"}


def test_the_writer_scanner_actually_sees_a_setdefault_writer():
    """同样要反向验证 `_write_keys` 本身——它不能是空的或瞎的。"""
    assert "_adj_cache" in _write_keys(_module("backtest.py"))
    assert "_unadjusted" in _write_keys(_module("backtest.py"))
    assert "_dates" in _builder_keys(_module("backtest.py"))


# ── 具体到本项目的那一颗雷 ──────────────────────────────────────

def test_there_is_no_cost_key_on_the_cross_sections_mapping():
    """`_cost` 这个键**已经不存在了**（在**可执行代码**里）。

    这条不是「重复上一条」，而是把**具体那一次修复**钉在案上：
    后人若觉得「成本按日可覆盖」是个好主意而重新加回来，
    这条会告诉他上一次为什么删。

    ## ⚠️ 这条测试**第一版是裸子串匹配，当场把自己判红了**

    删掉那行代码后我在注释里解释了「为什么删」，
    而注释里引了 `"_cost"` 这个字面量——于是子串匹配把
    **解释它为什么有害的那段话**当成了违规。

    修法与 `tests/test_verdict_composite.py` 里
    「只查可执行代码，不查 docstring / 注释」是同一条纪律：
    **注释必须能说出那个字符串来说明来源，检查范围就得排除它。**
    AST 里根本没有注释节点，所以改用 AST 即可。
    """
    tree = _module("backtest.py")
    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "_cost" not in literals, (
        "`_cost` 曾经是个焊死的开关（没人写、永远返回默认值）。"
        "真要按日覆盖成本，走 `cost_drag(cost=...)` 显式入参，"
        "不要塞回一个没有写入方的映射键。\n"
        "（注释里提到它是允许的——本测试查 AST，不查注释。）"
    )
