"""守卫：`score_ranking_power.py` 的 `what_this_cannot_prove` 里的数**跟着数据走**。

**预注册**：`docs/PREREG_PROSE_NUMBERS_2026-10-16.md` §3 的 P1/P2；§7 是实现方案与延伸点 E1。

本层属**第 73 层**（同一事实被复制到多处）的第三次，形态是
**同一文件里一处写死、一处算**：`what_this_cannot_prove` 写死 `n=4` / `条目数 80`，
而同文件的 stdout（`:306`）用的是算出来的 `res['n_independent_pred_dates']`。
底稿补判后真值走到 5 日 / 100 行 ⇒ 写死的那处被算出来的那处当场打脸。

🚨 这里的两个量具**不能互换**：
- P1 走 **AST**，能看见「它是个字面量常量」；
- P2 走 **`exec_module` + 调用**，能看见「它跟着输入变」。
照抄 `test_cap_exposure.py:_main_dict_values` 只能做 P1 ——
它只收 `ast.Constant`，而修好之后该项是一个 `ast.Call` ⇒ 会返回空 ⇒ **假红**。
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts/score_ranking_power.py"

_spec = importlib.util.spec_from_file_location("_srp_prose_under_test", SRC)
M = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(ROOT))
_spec.loader.exec_module(M)


def _cannot_prove_node():
    """从源码 AST 取 payload 里 `what_this_cannot_prove` 那一项的**值节点**。

    锚在**键的字面量**上，不是「文件里出现过这个词」——
    后者会被注释、甚至本文档的引用满足（第 13 层）。
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    hits = [v for n in ast.walk(tree) if isinstance(n, ast.Dict)
            for k, v in zip(n.keys, n.values)
            if isinstance(k, ast.Constant) and k.value == "what_this_cannot_prove"]
    assert hits, "源码里找不到 `what_this_cannot_prove` 这个键 ⇒ 产物结构变了，先查实现"
    return hits[0]


# ══════════════ P1（静态）：不许是写死的字面量 ══════════════

def test_the_prose_is_not_a_hardcoded_literal():
    node = _cannot_prove_node()
    assert not isinstance(node, (ast.List, ast.Tuple)), (
        f"what_this_cannot_prove 是字面量 {type(node).__name__} ⇒ "
        f"里面的数只会在有人手动改的时候才变 ⇒ 底稿一漂移它就是说假话（第 73 层）")


# ══════════════ P2（主判据）：喂进去就得跟着出来 ══════════════

def test_the_prose_follows_the_sample_size_it_is_given():
    """「数是**对的**」与「数**跟着数据走**」是两个命题（第 142/144 层）。
    静态相等（P1）只保证前者；**本条**才是后者的唯一证据。"""
    lines = M._cannot_prove_lines("NOT_PASSED_POWERLESS", 7, 5, 140)
    assert len(lines) == 3, f"文案应有 3 条，实测 {len(lines)}"

    # ⚠️ 断言必须锚在**具体句子的捕获组**上，不许用「数字不在集合里」：
    #   文案里本来就有 G1/G2/G3/**G5**/G6、§0、2 臂、10 只 ⇒ 「7 不在集合里」会假红。
    m1 = re.search(r"只有 (\d+) 个\*\*出单日\*\*", lines[0])
    assert m1, f"第 1 条里找不到出单日槽位：{lines[0]!r}"
    assert m1.group(1) == "7", f"喂了 n_pred_dates=7，文案写的是 {m1.group(1)}：{lines[0]!r}"

    # 🚨 第 2 条**三个**槽位一起钉（条目数 / 出单日 / **成交窗口**）：
    #   `n_entry_dates` 是后加的第四参，只钉前两个等于放过它写死 ——
    #   而它恰好是本项目「出单日 ≠ 独立成交窗口」这件事唯一落到散文里的那个数。
    m2 = re.search(r"条目数 (\d+) 当独立观测数 —— 它是 (\d+) 个出单日"
                   r".*?只对应 \*\*(\d+)\*\* 个成交窗口", lines[1])
    assert m2, f"第 2 条里找不到条目数/出单日/成交窗口三个槽位：{lines[1]!r}"
    assert m2.groups() == ("140", "7", "5"), \
        f"喂了 (n_rows=140, n_pred_dates=7, n_entry_dates=5)，" \
        f"文案写的是 {m2.groups()}：{lines[1]!r}"


_SLOTS = (r"只有 (\d+) 个\*\*出单日\*\*",            # 第 1 条：出单日
          r"成交窗口 \*\*(\d+)\*\* 个",               # 第 1 条：成交窗口
          r"条目数 (\d+) 当独立观测数",                # 第 2 条：条目数
          r"它是 (\d+) 个出单日",                     # 第 2 条：出单日
          r"只对应 \*\*(\d+)\*\* 个成交窗口")          # 第 2 条：成交窗口


def test_every_measured_slot_moves_when_the_numbers_change():
    """🚨 对照臂，**逐槽位**：证明 `follows` 不是恒真。

    ⚠️ 第一版写的是 `assert a != b`（两段文案整体不同）——
    那是个**弱筛子**：K2 只写死 `条目数`、同一行里的 `它是 {n_dates} 日` 仍跟着变
    ⇒ 整体仍然不同 ⇒ 断言照样绿，而**一半的槽位已经死了**。
    弱筛子和恒真一样有害（第 112 层）⇒ 改成**逐个槽位**比对。

    「只钉喂 7 得 7」的守卫，与「把 7 写死、只让测试用 7 喂它」的守卫逐位同形；
    唯一能分开它们的是「换一组输入，**每一个**槽位都必须换」。"""
    # ⚠️ 出单日**与**成交窗口都必须换（7/5 ⇒ 9/6）：
    #   若只换条目数而让 `n_entry_dates` 两组相同，那个槽位就没人钉 ——
    #   而「只钉喂 7 得 7」的守卫与「把 7 写死」逐位同形（见本测试 docstring）。
    a = M._cannot_prove_lines("NOT_PASSED_POWERLESS", 7, 5, 140)
    b = M._cannot_prove_lines("NOT_PASSED_POWERLESS", 9, 6, 180)
    checked = 0
    for i, (la, lb) in enumerate(zip(a, b)):
        for pat in _SLOTS:
            ma, mb = re.search(pat, la), re.search(pat, lb)
            if not (ma and mb):
                continue
            checked += 1
            assert ma.group(1) != mb.group(1), (
                f"第 {i} 条的槽位 {pat!r} 不随输入变（{ma.group(1)} ⇒ {mb.group(1)}）"
                " ⇒ 这个槽位已经被写死了（第 73 层）")
    assert checked >= 3, f"只对上了 {checked} 个槽位 ⇒ 文案结构变了，先查实现"


def test_the_call_site_passes_measured_fields_not_constants():
    """🚨 §7.5 的 P4：前四条守卫**直接调用**函数，**从不经过 `main()`**
    ⇒ 调用点那一行零覆盖。把它改成 `_cannot_prove_lines(kind, 5, 100)`
    时上面四条**全部照样绿** —— 与第 118 层（守卫问的不是被用的那个东西）同族。

    ⚠️ 本条是**静态**的：它能证明「这里没有常量」，
    **不能**证明「这里传的是对的那两个字段」（那需要比对 `Subscript` 的键名，
    本条不冒充已覆盖）。"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_cannot_prove_lines"]
    assert calls, "找不到对 _cannot_prove_lines 的调用 ⇒ 产物文案是从哪来的？"
    lits = [a.value for c in calls for a in c.args if isinstance(a, ast.Constant)]
    assert not lits, (
        f"调用点传了字面量 {lits} ⇒ 实参不是本次运行的测量值；"
        "直接调函数的守卫看不见这里")


def test_the_prose_names_the_verdict_it_was_given():
    """延伸点 E1（§7.2）：判决名与那三个数字是**同一段散文里的同一个量**。

    ⚠️ 它必须跟着 `kind` 变 —— 否则判决翻转后，读者会读到
    「不能把 NOT_PASSED_POWERLESS 读成…」而当前判决根本不是它。"""
    a = M._cannot_prove_lines("NOT_PASSED_POWERLESS", 7, 5, 140)
    b = M._cannot_prove_lines("PASS", 7, 5, 140)
    assert "NOT_PASSED_POWERLESS" in a[0], a[0]
    assert "NOT_PASSED_POWERLESS" not in b[0], b[0]
    assert "PASS" in b[0], b[0]
