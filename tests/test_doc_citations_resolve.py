"""代码里写的「文档 §N」必须真的存在。

## 为什么要有它

2026-10-15：`scripts/factor_attribution.py` 里有一行注释

> ⇒ 两侧一起改是独立的一项工作，**判据预注册在**
> `docs/TODO_factor_attribution_2026-10-15.md` 的**最后一节之后**

那个节号**从未存在**（该文档当时只到 §6）—— 唯一包含该文件的提交里也没有。

也就是说：**一句声称「判据已经预注册」的注释，指向一个不存在的登记。**
而「判据必须预注册」是本仓的第一条纪律。

★ 处置不是把那行注释删掉就算完 —— 那是把症状连同证据一起清了。
真实内容补写成该文档的**第 7 节**，并在节首明写「补记于动手当日，
**不是事前登记**」。**补写出来的登记不能冒充预注册。**

⚠️ 这与形状 19（章只记代码不记数据）同族，但更靠前一层：
**provenance 管 JSON，不管注释。** 注释不是产物，没有 sidecar，
所以它引用的锚点**永远不会被校验**，直到有人真的去读那份文档。

## 为什么守卫只查「带路径的 §N」

仓库里还有 58 处**裸** `§N`（不写文档名），它们大多指向
**本文件 docstring 内部**的预注册小节（例如 `factor_width_mde.py` 里的
「预注册 §4.1」指的是它自己模块顶部那张表）。
裸引用**没有唯一的真相源**，机械判定只会误伤。

⇒ 只对**带路径**的引用判死。实测：带路径 4 处，**3 处落地、1 处悬空**
（就是上面那条）。修掉悬空后本守卫转绿。

## 探针自己被验证过（重要）

第一版解析器把 `## 3.1 显著性怎么算` **整行**当节号，
`.rstrip()` 只去尾部空白 ⇒ `§3` 匹配不上 ⇒ 报出「0 ok / 4 bad」的假结论
（把 3 条好引用也说成坏的）。**守卫自己也可能是错的**，
所以下面这条把解析规则钉住，且带正控。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 形如 `<某个 docs 下的 md 文件> §<数字>` / `… §<数字>` —— 必须带路径才判定
# ⚠️ 这里的示例**故意不写成能被上面那条正则匹配的样子**：守卫会扫到
#   自己的源文件，示例长得像真引用就会把自己判死（第一次就这么翻车了）。
CITED = re.compile(r"(docs/[\w\-./]+\.md)\s*(?:的)?\s*§\s*(\d+(?:\.\d+)*)")
HEADING = re.compile(r"^#+ +(.*)$", re.M)


def _sections(doc: Path) -> set[str]:
    """文档里真实存在的节号。

    ⚠️ 必须取标题的**前导数字**并丢弃其余文字 —— 第一版保留整行，
    于是 `## 3.1 xxx` 的节号成了 `3.1 xxx`，`§3` 匹配不上。
    """
    out: set[str] = set()
    for h in HEADING.findall(doc.read_text(encoding="utf-8")):
        h = h.strip()
        m = re.match(r"(?:§\s*)?(\d+(?:\.\d+)*)", h)
        out.add(m.group(1) if m else re.sub(r"^§?\s*", "", h))
    return out


def _sources() -> list[Path]:
    return (sorted(ROOT.glob("scripts/**/*.py")) + sorted(ROOT.glob("aplus/**/*.py"))
            + sorted(ROOT.glob("tests/**/*.py")))


def _citations() -> list[tuple[Path, str, str, int]]:
    found = []
    for p in _sources():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in CITED.finditer(line):
                found.append((p, m.group(1), m.group(2), i))
    return found


# ── 解析器自己的守卫（它一旦错，全盘结论就是错的） ──────────────────

def test_the_resolver_reads_the_leading_number_not_the_whole_heading():
    """★ 正控：**先证明探针认得带文字的标题**，再拿它去判别人。"""
    doc = ROOT / "docs/TODO_factor_attribution_2026-10-15.md"
    secs = _sections(doc)
    assert "3" in secs, secs
    assert "3.1" in secs, secs
    assert not any(s.startswith("3 ") for s in secs), \
        f"★ 节号里混进了标题正文：{sorted(secs)}"
    assert "7" in secs, \
        "★ 正控前提：抵消那一节（补写的处置记录）必须在，否则这条是恒真的"


# ── 主守卫 ──────────────────────────────────────────────────────────

def test_every_path_qualified_section_citation_resolves():
    """★ 注释不是产物，所以**没人校验它引用的锚点** —— 那就自己校验。"""
    bad = []
    for src, doc, sec, line in _citations():
        f = ROOT / doc
        if not f.is_file():
            bad.append(f"{src.relative_to(ROOT)}:{line} → 文档不存在：{doc}")
        elif sec not in _sections(f):
            bad.append(
                f"{src.relative_to(ROOT)}:{line} → {doc} 里没有 §{sec}；"
                f"实有 {sorted(_sections(f), key=lambda s: [int(x) for x in s.split('.')] if s.replace('.', '').isdigit() else [0])}")

    assert not bad, (
        "★ 以下引用指向不存在的节 —— **注释里的引用从不校验**，"
        "它会静静地把「判据已预注册」这句话变成谎话：\n  "
        + "\n  ".join(bad))


def test_the_guard_actually_sees_the_citations():
    """★ 恒真守卫的解药：断言探针**真的扫到了东西**。

    若哪天正则改坏（或目录改名），`_citations()` 返回空列表，
    上面那条会**永远绿** —— 那比红更危险。
    """
    cits = _citations()
    assert len(cits) >= 4, f"★ 只扫到 {len(cits)} 处带路径引用，正控失败：{cits}"
    assert any(d.endswith("TODO_factor_attribution_2026-10-15.md") for _, d, _, _ in cits), \
        "★ 归因文档的引用应被扫到（§7 是本仓引用密度最高的一处）"


@pytest.mark.parametrize("doc", sorted(
    {d for _, d, _, _ in _citations()}))
def test_the_cited_document_exists(doc):
    """逐个文档确认存在 —— 让报错直接指向缺哪个文件，而不是在主守卫里猜。"""
    assert (ROOT / doc).is_file(), f"★ 被代码引用的文档不存在：{doc}"
