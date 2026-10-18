"""README 不得教读者绕过门禁唯一入口（2026-10-18）

**这个洞是真的，而且有两次事故在后面。**

`aplus/README.md:80` 原文：

    .venv/bin/python -m pytest tests/ -v

而本仓的门禁**唯一入口**是 `scripts/run_gate.py`，`CLAUDE.md` 有专章写着
「**不要手敲 `pytest … | tail`**」——2026-10-15 这一天我已经因为管道误判门禁
**两次**（第 107 层），第二次的终端输出长这样：

    2 failed, 2477 passed, 1 skipped, 1 warning in 284.85s
    [exited with code 0]        ← 就在失败摘要下面三行

⇒ 那 2 条红守卫正是为了拦这种提交而存在的，而管道本来要把信号吞掉。
⇒ 所以 README 教这条命令不是「过时」，是**在一份会被陌生人读到的文档里
   教那个已经付出两次代价才禁掉的动作**。

---

🚨 **为什么这条守卫不能写成子串断言**（第 13 层）

`assert "run_gate.py" in readme_text` 会被 README 自己的一句注释满足：

    # 历史上有人手敲 pytest tests/ | tail —— 别这么干

⇒ 子串能告诉你「有什么」，不能告诉你「**在哪用**」（第 13 层）。
⇒ 这里的做法是**解析围栏代码块**，逐行判**调用形态**，
并显式跳过 `#` 开头的注释行。

⚠️ **为什么只认显式标注 `bash`/`sh`/`shell`/`console` 的块**：无标注的 ``` 块
在本仓的惯例里**大量用于贴输出**（pytest 摘要、`git status` 结果…）。
把它们也算成「命令」会得到一条**永远红**的守卫，而永远红的守卫会把人推向
「为了变绿而删掉真命令」——第 112 层的过严守卫，危害与恒真守卫同阶。
⇒ 判据宁可漏，不许逼人改对的东西。

🚨 **防恒真（防「扫了 0 处所以绿」）**：`_fenced_bash_blocks` 若因 README 写法
变化而返回空列表，主守卫会**永远绿**，而它守的东西正在消失。第 126 层的同族。
⇒ `test_the_scanner_actually_scans_blocks` 钉住「扫到了块」。
"""
from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"

# 显式标注为 shell 家族的围栏；无标注的块按「输出」处理（见模块 docstring）
_SHELL_TAGS = frozenset({"bash", "sh", "shell", "console", "zsh"})

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+.-]*)\s*$")

# `-m pytest`（含 .venv/bin/python -m pytest）与裸 `pytest` / `py.test` 可执行体
_MODULE_FORM = re.compile(r"-m\s+pytest\b")
_EXEC_FORM = re.compile(r"(?:^|[\s;&|(\"'=])(?:[\w./-]*/)?py(?:test|\.test)\b")


def _fenced_shell_blocks(md: str) -> list[tuple[int, str]]:
    """返回 [(1-based 起始行号, 块正文)]，只收显式标注为 shell 的围栏。"""
    lines = md.splitlines()
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        marker, tag = m.group(1), m.group(2).lower()
        start, body, i = i, [], i + 1
        while i < len(lines):
            close = _FENCE.match(lines[i])
            # 闭合围栏 = 同种反引号/波浪线、长度不短、且**无标签**。
            # ⚠️ 标签组（group 2）在闭合围栏上是空串 ⇒ 不许对它取下标（第 141 层）。
            if (close and close.group(1)[0] == marker[0]
                    and len(close.group(1)) >= len(marker) and not close.group(2)):
                break
            body.append(lines[i])
            i += 1
        if tag in _SHELL_TAGS:
            out.append((start + 1, "\n".join(body)))
        i += 1
    return out


def _is_bare_pytest_call(line: str) -> bool:
    """这一行是否在**直接调用** pytest（而不是经 scripts/run_gate.py）。"""
    s = line.strip()
    if not s or s.startswith("#"):
        return False
    if "run_gate.py" in s:
        return False
    return bool(_MODULE_FORM.search(s) or _EXEC_FORM.search(s))


def _offending_lines(md: str) -> list[tuple[int, str]]:
    hits = []
    for start, body in _fenced_shell_blocks(md):
        for offset, line in enumerate(body.splitlines()):
            if _is_bare_pytest_call(line):
                hits.append((start + 1 + offset, line.strip()))
    return hits


# ── 主守卫 ────────────────────────────────────────────────────────────────

def test_the_readme_never_teaches_a_bare_pytest_invocation():
    """README 的命令块里不许出现绕开门禁入口的 pytest 调用。"""
    offenders = _offending_lines(README.read_text(encoding="utf-8"))
    assert offenders == [], (
        "README 在教读者手敲 pytest —— 那是 CLAUDE.md 第 107 层禁止的动作"
        f"（管道吞掉退出码，曾两次误判门禁）。请改用 scripts/run_gate.py。\n"
        + "\n".join(f"  README.md:{n}: {t}" for n, t in offenders)
    )


# ── 防恒真：扫描器本身不许空转 ────────────────────────────────────────────

def test_the_scanner_actually_scans_blocks():
    """若 README 不再有任何 shell 块，主守卫会永远绿而它守的东西正在消失。

    ⇒ 这里断言的是**扫描器扫到了东西**，不是 README 的内容政策。
    """
    blocks = _fenced_shell_blocks(README.read_text(encoding="utf-8"))
    assert blocks, (
        "README.md 里一个显式标注 bash/sh/shell/console 的围栏都没有了 —— "
        "主守卫会空转通过。请确认是格式变了还是命令真的被删了。"
    )


# ── 反向正控：探测器必须真的能判红（第 131 层：正控要验判决，不只验探测器）──

def test_the_detector_flags_a_synthetic_bare_pytest_block():
    """造一份有洞的 README，探测器必须指出它 —— 否则主守卫是装饰。"""
    bad = (
        "# aplus\n\n"
        "## 测试\n\n"
        "```bash\n"
        ".venv/bin/python -m pytest tests/ -v\n"
        "```\n"
    )
    hits = _offending_lines(bad)
    assert [t for _, t in hits] == [".venv/bin/python -m pytest tests/ -v"], (
        f"探测器没抓到合成的裸 pytest 调用，返回了 {hits}"
    )


def test_the_detector_accepts_the_gate_entry_and_ignores_comments():
    """经 run_gate.py 的调用**必须**放行；注释里提到 pytest 也必须放行。"""
    good = (
        "```bash\n"
        ".venv/bin/python scripts/run_gate.py\n"
        ".venv/bin/python scripts/run_gate.py -k provenance\n"
        "# 历史教训：不要手敲 pytest tests/ | tail（第 107 层）\n"
        "```\n"
    )
    assert _offending_lines(good) == [], "门禁入口与注释被误判了"
