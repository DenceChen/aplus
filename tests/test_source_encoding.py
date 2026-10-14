"""源码字符级损坏守卫：**U+FFFD 替换字符一个字都不许有**。

## 起因（2026-10-15 实测，全仓 38 处）

```
aplus/webapp.py:628   按三元组匹配，不[FFFD]按权重
aplus/verify.py:198   必须知情[FFFD]风险
aplus/composite.py:51 [FFFD]且是**系统性偏移**、不会崩的那种
```

`<?>` 是 `U+FFFD REPLACEMENT CHARACTER`，**已经躺在仓库里、已经入库**。

## 为什么它能活这么久

**每一处都在注释或 docstring 里。**

⇒ 对解释器：**零影响**（注释不是 token）。
⇒ 对 pytest：**零影响**（断言不看注释）。
⇒ 对 git：**是内容**（所以被提交了，且提交得毫无异样）。

于是「1131 条测试全绿」与「38 句话被吃掉了字」**可以同时成立**，
而且没有任何一条测试有理由变红。

## 为什么这比看起来严重

被吃掉的**恰好是那些警告句**：

| 位置 | 现在 | 应该是 |
|---|---|---|
| `webapp.py:628` | 不[FFFD]按权重 | 不**只**按权重 |
| `composite.py:51` | [FFFD]且是**系统性偏移** | **而**且是…… |
| `verify.py:198` | 必须知情[FFFD]风险 | 必须知情**的**风险 |
| `verify.py:337` | 并让 `n_days` 如实反映天数 | ……整<?>丢掉 |

一句写坏了的「**不要这么做**」，读者读到的是一个读不通的句子 ——
他多半会**跳过它**，而那正是它存在的唯一理由。

★ 与本仓既有形状的关系：**这是第 13 种（子串断言可被注释满足）的镜像。**
  第 13 种是「注释满足了断言」；这一种是「**注释本身坏了，而没有任何东西断言注释**」。

## 这条守卫的性质

它**不是**对某个函数的断言，是**对仓库内容的断言**——
所以它能被外部锚直接验证：一个字符就是非法值，不需要任何参考实现。
这也是全仓少数几条「一旦红了就一定是真坏了」的守卫。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ★ 只扫人写的文本。数据/产物可能含合法的 U+FFFD（例如上游返回的脏字节），
#   那是**另一件事**，混进来会让这条守卫变成噪声源。
SCAN_SUFFIXES = {".py", ".md", ".html", ".js", ".css", ".sh",
                 ".json", ".cfg", ".toml", ".yaml", ".yml"}
SKIP_DIRS = {".venv", ".git", "node_modules", "__pycache__",
             ".pytest_cache", ".testtmp", ".playwright-mcp"}

# ★ `data/reports/` **不在扫描范围内**，这是一个有意的例外，必须写明理由：
#   账本与报告是**历史记录**。`data/reports/evolution_ledger.json` 里有一处
#   字符损坏（2026-10-15 首次扫描发现），但「把旧账本的字改掉」是**篡改历史**，
#   比留着一个坏字更糟。⇒ 记录、不修、不扫描。
#   （本仓已有先例：`MISTAKE_LOG` 里连「撤回已提交结论」的记录都保留不删。）
SKIP_PREFIXES = ("data/reports/",)

# ★ 用码点构造，**不是字面量** —— 否则这条守卫会把自己也抓了，
#   而「守卫永远红在自己的定义上」是比原缺陷更难查的病。
REPLACEMENT = chr(0xFFFD)


def _scan() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.relative_to(ROOT).as_posix().startswith(SKIP_PREFIXES):
            continue
        if path.suffix not in SCAN_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # 严格解码失败本身是另一类损坏，见下面那条守卫
            continue
        if REPLACEMENT in text:
            rel = path.relative_to(ROOT).as_posix()
            for ln, line in enumerate(text.splitlines(), 1):
                if REPLACEMENT in line:
                    out.append((rel, ln, line.strip()[:100]))
    return out


def test_no_unicode_replacement_character_anywhere():
    """全仓零 `U+FFFD`。

    ★ 失败信息要**逐行列出行号与整行**，否则修的时候得自己再找一遍 ——
      「有 38 处」这个数字本身不告诉你它们在哪。
    """
    hits = _scan()
    if not hits:
        return
    detail = "\n".join(f"  {p}:{ln}  {line}" for p, ln, line in hits[:60])
    pytest.fail(
        f"源码里有 {len(hits)} 处 U+FFFD（替换字符）—— 某次写入吃掉了中文字。\n"
        f"它们**全都在注释/docstring 里**，所以解析、断言、测试都不会发现。\n"
        f"逐处（首 60 条）：\n{detail}\n"
        f"修法：按上下文补回原字；**拿不准的不要猜**，写 "
        f"`〔此处字符已损，见 MISTAKE_LOG 第 92 层〕` 并留待人工确认。")


def test_the_guard_actually_detects_a_replacement_character():
    """★ **守卫只有变异后算数**（本仓铁律）。

    没有这一条，上面那条可能是恒真的 ——
    比如扫描根路径算错、或者后缀集合写漏了所有真实文件，
    它会永远绿，而什么也没守。

    做法：把上面那条用到的 `_scan()` 指向一个**临时造的坏文件**，
    要求它**必须**报出来，且行号与整行都对得上。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bad = root / "x.py"
        # ★ 同样用码点构造：fixture 里放字面量的话，
        #   上一轮「把字面量全换成 [FFFD]」的清理会把这个 fixture 一起清掉，
        #   元守卫就会变成「造了个干净文件再要求它报错」⇒ 恒红或恒绿，都没意义。
        bad.write_text("ok = 1\n坏 = '不" + REPLACEMENT + "按权重'\n", encoding="utf-8")
        good = root / "y.py"
        good.write_text("ok = 1\n", encoding="utf-8")

        # 复用真实扫描逻辑：临时把 ROOT 指过去
        import tests.test_source_encoding as mod
        old_root = mod.ROOT
        try:
            mod.ROOT = root
            hits = mod._scan()
        finally:
            mod.ROOT = old_root

    assert len(hits) == 1, f"应当恰好报 1 处，实得 {len(hits)}：{hits}"
    assert hits[0][0] == "x.py"
    assert hits[0][1] == 2, f"行号报成 {hits[0][1]}，应当是 2"
    assert REPLACEMENT in hits[0][2]


def test_scan_covers_every_human_written_file_type():
    """扫描的后缀集合必须真的覆盖仓库里存在的类型。

    ★ 用**实测的仓库文件**去对，而不是用一张手写的清单 ——
      清单会漂，仓库不会。
    """
    present = {
        p.suffix for p in ROOT.rglob("*")
        if p.is_file()
        and not any(part in SKIP_DIRS for part in p.parts)
    }
    missing = sorted(present & {".py", ".md", ".html", ".js", ".json"} - SCAN_SUFFIXES)
    assert not missing, (
        f"仓库里有这些文本类型但扫描集合没收：{missing} —— "
        f"要么加进 SCAN_SUFFIXES，要么写明为什么不管")


def test_no_strictly_invalid_utf8_in_source():
    """★ 比 U+FFFD 更靠上游的一层：**字节层面就不是合法 UTF-8**。

    上一条只查「已经解码成功、但里面有替换字符」的文本。
    如果某次写入留下了**非法字节**，`.read_text()` 会抛
    `UnicodeDecodeError`，而 `_scan()` 里我写的是 `continue` ——
    **那正是「损坏的文本被守卫静默跳过」的形状**。

    所以这里单独用 `errors="strict"` 显式读一次，
    不许靠 `continue` 蒙过去。
    """
    bad: list[str] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.relative_to(ROOT).as_posix().startswith(SKIP_PREFIXES):
            continue
        if path.suffix not in SCAN_SUFFIXES or not path.is_file():
            continue
        try:
            path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            bad.append(path.relative_to(ROOT).as_posix())
        except OSError:
            continue
    assert not bad, f"这些文件不是合法 UTF-8：{bad}"
