"""★ 数据根默认值必须是**唯一真相源**（2026-10-15 修，20 个脚本）

## 问题（实测，不是推演）

    ap.add_argument("--data-root", type=Path, default=Path("/tmp/aplus_snap_1012"))

裸跑 = 读**几天前的快照**，而产出的产物**与用新数据跑的完全无法区分**。
当时 `/tmp` 里四个快照**全都还在磁盘上**：

    /tmp/aplus_snap_1012    12 个脚本拿它当默认
    /tmp/aplus_snap_final    7 个
    /tmp/aplus_snap_1015     1 个
    /tmp/aplus_snap_1610     3 个 `_diag_*.py` 硬编码 ROOT（诊断脚本，刻意保留）

⇒ 快照存在 ⇒ 默认值**真的会被用上**，不是「反正路径没了也跑不了」的自欺。

## 为什么这是「口径」而不是「风格」

数据根决定**读哪一批数据**，也就是决定**所有绝对数字**。
而 2026-10-15 这一天实测：`snap_1015` 与仓库**差 5217/5460 只**。
同一天、同一份代码，两个默认根 ⇒ 两套互不可比的结论，
而产物里**没有任何字段**能区分它们（这正是形状 18/19 的病根）。

## 修法与本文件的分工

修法是机械的（20 处默认值 → `ROOT / "data"` 或 `Path("data")`）。
本文件负责**让它回不来**：任何新的 `--data-root` 默认值指向仓外 ⇒ 红。

## ⚠️ 扫描器必须自己有正反控

「扫不到东西」的扫描器返回空集合，空集合对「不允许」类断言**天然为真**。
所以本文件先证明扫描器**看得见**（`_SCANNER_SEES_THE_THING_IT_FORBIDS`），
再证明分类函数**判得对**（合成用例，不依赖真实树）。
"""
from __future__ import annotations

import ast
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"

# ★ 刻意保留在仓外的**诊断**脚本：它们的名字（`_diag_`）就说明是临时探针，
#   钉一个具体快照是它们的用法本身，不是疏漏。新增即红 ⇒ 逼人当场判断。
#   （2026-10-15 实测同时清掉了 `scripts/audit_tradeability/` 下 10 个
#    `ROOT = "/Users/airboom/opencode/aplus"` —— 目标对但机制错：
#    绝对路径带用户名，换机/换目录即失效，也把用户名写进了仓库。）
_KNOWN_HARDCODED_ROOTS = {
    "scripts/_diag_mom121.py",
    "scripts/_diag_limitup.py",
    "scripts/_diag_rebalance_freq.py",
}

# 「仓内」的判定：相对路径且不含 `..`、不以 `/` 开头、不含盘符
_ABS = re.compile(r"^\s*Path\(\s*['\"]/|['\"]/|\.\.")


def _is_inside_repo(default_src: str) -> bool:
    """分类器：给 add_argument 的 `default=` 表达式源码，判它是否指向仓内。

    单独拿出来测，是因为它是本文件唯一的**判决逻辑** ——
    判决逻辑必须有合成用例，不能只靠真实树恰好没坏。
    """
    s = default_src.strip()
    if 'ROOT / "data"' in s or "ROOT / 'data'" in s:
        return True
    if s in ('"data"', "'data'"):
        return True
    return not _ABS.search(s)


def _data_root_defaults() -> dict[str, list[tuple[int, str]]]:
    """扫出所有 `--data-root` 的 default 表达式。返回 {脚本: [(行号, 源码)]}"""
    out: dict[str, list[tuple[int, str]]] = {}
    for py in sorted(_SCRIPTS.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "add_argument"):
                continue
            first = node.args[0] if node.args else None
            # ⚠️ `ast.unparse` 一定给**单引号**：实测 `unparse(Constant('--data-root'))`
            #   == `"'--data-root'"`。第一版拿双引号去比 ⇒ 扫出 0 处 ⇒ 守卫恒绿。
            #   这个 bug 被下面那条正控当场抓住（`n >= 25` 失败），没写进主干。
            if first is None or ast.unparse(first) != "'--data-root'":
                continue
            rel = str(py.relative_to(_ROOT))
            for kw in node.keywords:
                if kw.arg == "default":
                    out.setdefault(rel, []).append((node.lineno, ast.unparse(kw.value)))
    return out


def _hardcoded_roots() -> set[str]:
    out = set()
    for py in sorted(_SCRIPTS.rglob("*.py")):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^\s*(?:ROOT|DATA_ROOT)\s*=\s*['\"](/\S+)['\"]", line)
            if m:
                out.add(str(py.relative_to(_ROOT)))
    return out


# ── 正控：扫描器看得见，且分类器判得对 ────────────────────────────

def test_the_scanner_actually_finds_data_root_arguments():
    found = _data_root_defaults()
    n = sum(len(v) for v in found.values())
    assert n >= 25, f"★ 只扫到 {n} 个 `--data-root`（{len(found)} 个脚本），扫描器多半瞎了"
    # 反向：仓内默认值必须被判为仓内（否则守卫会因为「全都不在仓内」而误红）
    assert any(_is_inside_repo(src) for v in found.values() for _, src in v), \
        "★ 一个仓内默认值都没判出来 ⇒ 分类器坏了，不是树坏了"


def test_the_classifier_decides_the_four_shapes_that_actually_occur():
    """★ 合成用例：判决逻辑的正反控，不依赖真实树。

    这四种是仓里真实出现过的形状（不是编的），
    前两个应判「仓内」，后两个应判「仓外」。
    """
    inside = ['ROOT / "data"', 'Path("data")', '"data"', "'data'"]
    outside = ['Path("/tmp/aplus_snap_1012")', '"/tmp/aplus_snap_final"',
               'Path("../other/data")', 'Path("/Users/x/data")']
    for src in inside:
        assert _is_inside_repo(src), f"★ {src} 应判为仓内"
    for src in outside:
        assert not _is_inside_repo(src), f"★ {src} 应判为仓外"


# ── 主守卫 ───────────────────────────────────────────────────────

def test_no_data_root_default_points_outside_the_repo():
    bad = []
    for rel, items in sorted(_data_root_defaults().items()):
        for lineno, src in items:
            if not _is_inside_repo(src):
                bad.append(f"{rel}:{lineno} → default={src}")
    assert not bad, (
        "★ `--data-root` 的默认值指向仓外：\n   "
        + "\n   ".join(bad)
        + "\n   ⇒ 裸跑会读快照/别处的数据，而产物与新鲜数据跑出来的**无法区分**。"
          "\n   快照必须**显式传**，默认值只能是仓库（唯一真相源）。"
    )


def test_the_deliberately_pinned_diagnostics_are_still_exactly_three():
    """★ 冻结名单：新增即红。不是禁止，是新增时要当场判断「这是不是又漏一个」。"""
    actual = _hardcoded_roots()
    assert actual == _KNOWN_HARDCODED_ROOTS, (
        f"★ 硬编码仓外根的脚本变了。\n"
        f"   新增：{sorted(actual - _KNOWN_HARDCODED_ROOTS)}\n"
        f"   消失：{sorted(_KNOWN_HARDCODED_ROOTS - actual)}\n"
        f"   ⇒ 消失的也要解释：是被修好了，还是被改名躲过了扫描？"
    )
