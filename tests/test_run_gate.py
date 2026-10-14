"""`scripts/run_gate.py` 的守卫 —— 门禁的退出码必须是 pytest 的退出码。

## 为什么这条值得有守卫

门禁是我**手敲的命令**，不是产物。前面所有缺陷（成本扣两次、口径混比、
分支顺序是判据）都能写测试，因为它们有产物可以断言；这一条没有。
它在同一天复发两次，第二次的输出是：

```
2 failed, 2477 passed, 1 skipped, 1 warning in 284.85s
[exited with code 0]        ← 就在失败摘要下面三行
```

⇒ 修法不是再写一条「记得别加管道」的纪律（那条已失效两次），
而是把命令固化成脚本，让管道**没有可乘之机**。

## 这些测试为什么是行为性的

第 ① 条**真的起一个子进程跑真的 pytest**，然后断言拿到的是 pytest 的码。
它不去检查源码里有没有 `shell=True` —— 那与第 13 种形状同族：
**子串断言会被注释和文档满足**，而这里真正要证的是「码对不对」，
只有跑一遍才知道。
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "run_gate.py"


def _gate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )


# ────────────────────────────── 退出码透传 ──────────────────────────────

def test_a_red_gate_exits_red(tmp_path):
    """🚨 **这条是整个脚本存在的理由。**

    造一个必然失败的测试，让门禁跑它。
    期望 `rc == 1`（pytest 的失败码）。

    若这条红着，`2 failed … exit 0` 那件事还会再来第三次。
    """
    bad = tmp_path / "test_deliberately_red.py"
    bad.write_text("def test_red():\n    assert 1 == 2\n", encoding="utf-8")
    r = _gate(str(bad))
    assert r.returncode == 1, (
        f"门禁没把失败透传出来：rc={r.returncode}\n{r.stdout[-800:]}\n{r.stderr[-400:]}"
    )


def test_a_green_gate_exits_zero(tmp_path):
    """**正控臂**：门禁不能变成「永远红」。

    只测红不测绿的守卫，分不清「正确透传」与「永远返回 1」。
    """
    good = tmp_path / "test_deliberately_green.py"
    good.write_text("def test_green():\n    assert 1 == 1\n", encoding="utf-8")
    r = _gate(str(good))
    assert r.returncode == 0, f"明明全绿却 rc={r.returncode}\n{r.stdout[-800:]}"


def test_the_summary_line_is_reprinted_so_a_pipe_cannot_eat_it(tmp_path):
    """即使调用方自己又接了管道，`GATE: 摘要` 那一行也必须单独出现。

    这是给「人又手滑加了管道」兜底的 —— 脚本管不住自己的 stdout 被谁消费，
    所以**结论要打两次**。
    """
    bad = tmp_path / "test_deliberately_red2.py"
    bad.write_text("def test_red():\n    assert 0 == 1\n", encoding="utf-8")
    r = _gate(str(bad))
    assert "GATE: 摘要 →" in r.stdout, f"没打摘要行：\n{r.stdout[-800:]}"
    assert "GATE: 判决 → ❌ 红" in r.stdout, f"没打判决行：\n{r.stdout[-800:]}"


def test_a_gate_that_died_mid_run_says_so_instead_of_looking_like_it_is_running(tmp_path):
    """**pytest 猝死时不会打任何判决行** —— OOM、被 kill、断电都长这样。

    🚨 这时候「没有摘要」**极易被读成「还在跑」**，因为门禁没有崩、
    没有 traceback，只有输出戛然而止。
    ⇒ 必须显式播报「未找到摘要行」，让人立刻知道这一轮不算数。

    ⚠️ 这里的模拟手法值得说明：直接写一个语法错误的文件**测不到这条**，
    因为 pytest 会照样打出 `1 error in 0.29s` —— 那**是**判决行，
    收集失败是「有判决的失败」。第一版就是这么写错的，
    结果断言一个永远不会出现的字符串。
    ⇒ 真正的无判决形态是**进程中途消失**，用 `os._exit` 让解释器直接死掉，
    这是 OOM / SIGKILL 在文件系统上留下的同一种痕迹。
    """
    boom = tmp_path / "test_dies_mid_run.py"
    boom.write_text(
        "import os\n"
        "def test_never_runs():\n"
        "    os._exit(1)      # 模拟 OOM / 被 kill：没有判决行\n",
        encoding="utf-8",
    )
    r = _gate(str(boom))
    assert r.returncode != 0, "pytest 猝死却返回 0"
    assert "未找到摘要行" in r.stdout, (
        f"猝死没有被播报出来，读起来会像「还在跑」：\n{r.stdout[-900:]}"
    )
    assert "判决 → ❌ 红" in r.stdout, f"猝死却没判红：\n{r.stdout[-900:]}"


# ────────────────────────────── 摘要提取 ──────────────────────────────

def test_the_summary_is_the_last_matching_line_not_the_first():
    """测试名里可能含 `passed` —— 全文 grep 会命中噪声并把真摘要淹掉。

    顺序反过来（取首个命中）就会读到某个测试的名字，
    打印出「GATE: 摘要 → test_passed_edge_case」这种毫无意义的东西。
    """
    from scripts.run_gate import summary_line

    text = (
        "tests/test_x.py::test_passed_case FAILED\n"
        "1 failed, 42 passed, 3 skipped in 12.34s (0:00:12)\n"
    )
    assert summary_line(text) == "1 failed, 42 passed, 3 skipped in 12.34s (0:00:12)"


def test_a_run_with_no_tests_is_still_recognised_as_a_summary():
    """空选集（`-k` 什么都没匹配上）是 pytest 的 rc=5，不是 0，也不是崩溃。

    守卫必须能把它和「崩溃」分开，否则「跑了个寂寞」会被读成「跑过了」。
    """
    from scripts.run_gate import summary_line

    assert summary_line("no tests ran in 0.02s (0:00:00)") is not None
    assert summary_line("") is None


# ────────────────────────────── 结构保证 ──────────────────────────────

def test_the_gate_never_goes_through_a_shell():
    """🚨 **这条要用 AST，不能用子串。**

    第一版写的是 `assert "shell=True" not in src`，
    当场被**我自己写在 `run_gate.py` 里的注释**判红：

        #   一旦 `shell=True`，管道就会回来，`$?` 也就重新有歧义。

    ⇒ **第 13 种形状亲手复刻**：子串断言会被同文件的注释满足，
    哪怕写断言的人就是我。子串能告诉你*有什么*，不能告诉你*在哪用*。
    ⇒ 正解是锚在**语法位置**上：`shell` 只能作为 `keyword` 出现，
    且值必须是 `False`（或干脆不传）。

    真正承重的是上面那四条**行为测试**（真的起子进程、真的拿非零码）；
    这条负责把「有人日后把 shell 加回来」在第一处变成红灯。
    """
    tree = ast.parse(GATE.read_text(encoding="utf-8"))

    shell_kwargs: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    if not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                        shell_kwargs.append(kw.value.lineno)
                    else:
                        shell_kwargs.append(-1)   # 显式 False：允许，但要看见
    assert all(ln == -1 for ln in shell_kwargs), (
        f"第 {shell_kwargs} 行把命令交给了 shell ⇒ 管道会回来，$? 重新有歧义"
    )

    # 另一条把命令交给 shell 的路：os.system / os.popen / subprocess.getoutput
    handed_to_shell: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in {"system", "popen", "getoutput", "getstatusoutput"}:
                handed_to_shell.append(f"{name} @行{node.lineno}")
    assert not handed_to_shell, f"这些同样把命令交给 shell：{handed_to_shell}"

    # 承重的是「逐行透传」而不是「一次性收完再打」：Popen 必须存在，
    # 且必须有 `for line in proc.stdout` 这样一个逐行循环。
    assert "subprocess.Popen(" in GATE.read_text(encoding="utf-8")
    has_line_loop = any(
        isinstance(n, ast.For) and "stdout" in ast.dump(n.iter)
        for n in ast.walk(tree)
    )
    assert has_line_loop, "门禁必须逐行透传输出，不能一次性收完再打（会失去实时可观测性）"


def test_the_gate_is_the_documented_single_entry_point():
    """文档里必须把门禁指向这个脚本 —— 否则下一个人还是会手敲管道。"""
    readme = ROOT / "CLAUDE.md"
    if not readme.exists():
        pytest.skip("没有 CLAUDE.md")
    assert "scripts/run_gate.py" in readme.read_text(encoding="utf-8"), (
        "CLAUDE.md 没有把门禁指向 scripts/run_gate.py —— "
        "文档不指路，纪律就等于没有"
    )
