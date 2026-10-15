"""`CLAUDE.md` 的「回本所需本金」表必须与产物**逐格**一致（第 137 层）。

## 背景：这张表是手抄件，而抄错不会被任何人发现

`CLAUDE.md` 里那张表写的是：

| N | 毛超额/年 | **回本所需本金**（数值解） | 到 20% 净所需本金 |
|---|---|---|---|
| 10 | 4.81% | 16.1 万 | **不存在** |
| 30 | 5.55% | 23.9 万 | **不存在** |
| 100 | 6.17% | **55.8 万** | **不存在** |
| 200 | 6.95% | **81.3 万** | **不存在** |
| 546 | 7.57% | **182.4 万** | **不存在** |

而产物 `data/reports/width_capital_feasibility.json::widths[*]` 是
`161185.1141 / 239318.7683 / 557726.7131 / 813476.6179 / 1823729.5441`。

★ **我第一版把它抄成了 55.9 / 81.2 / 182.3 —— 五格错三格**，
而且第一格向上取、后两格向下取，**连舍入约定都不统一**。

## ★ 为什么它能拖到今天（第 137 层最刺眼的地方）

发现它的不是「读表时算错」，而是一条**完全无关的红**：
`test_the_artifact_is_not_older_than_every_consumer_of_it`
（因为 `aplus/hurdle.py` 改了）。按正路重跑那个纯推导脚本之后：

```
git diff data/reports/width_capital_feasibility.json   → 空
```

⇒ **产物逐位未变，变的只能是散文。**

⇒ ★ 也就是说：**守「新旧」的守卫不可能发现「抄错」** ——
这个问题的方向是**文档比产物旧**，而那条守卫守的是**产物比消费者新**。
⇒ 落盘产物与**代码**的关系会断（第 118 层），
落盘产物与**散文**的关系同样会断，而**前面那些守卫一条都看不见**。

## ⚠️ 危险方向恰好是最无害的那个

若错得离谱（16.1 万抄成 4.8 万）我会立刻发现。
**偏偏「差 0.1 万」最安全**：判决一字未变（「没有任何一档存在解」）、
结论方向未变（还是 ❌）、表格排版完全正常。
而**读者引用的是表里的数**。
⇒ 所以这类错**只能靠对账守卫拦** —— 「我下次小心点」不产生任何新信息。

## ⚠️ 核对方式：只读字段，禁止用公式重算（第 109 / 132 层）

格里的数必须等于 `round(产物字段, 1)`，
**不是**等于「我拿摩擦公式再算一遍回本本金」——
后者就是第 109 层那个坑：**用公式重算一遍等于换了个口径再犯一次**。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "data" / "reports" / "width_capital_feasibility.json"
CLAUDE = ROOT / "CLAUDE.md"

#: 表格的**身份**：靠表头文字定位，不靠「第几张表」也不靠列号下标 ——
#: 列号会随文档里别的表插进来而整体平移，届时守卫会**指向别的表却全绿**。
_HEADER_MARK = "回本所需本金"
_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


#: markdown 的删除线：`~~旧值~~ 新值` 表示「**旧值已作废，现行值是新值**」。
#: 仓里 `docs/MISTAKE_LOG_2026-10-14.md` 的那张表就是这么改的 ——
#: 它把 55.9/81.2/182.3 划掉、在后面补上 55.8/81.3/182.4。
_STRIKE_RE = re.compile(r"~~.*?~~", re.S)


def _cells(line: str) -> list[str]:
    """把一行 markdown 表格拆成去掉加粗、**去掉删除线内容**与空白的单元格。

    ⚠️ 删除线**整段删掉、保留其后**（`~~55.9 万~~ 55.8 万` → `55.8 万`），
    而不是把删除线当普通字符 ⇒ 判据读到的就是**这一格现在的值**。

    ⚠️ 危险方向：若改成「保留删除线里的旧值」，那张表会重新红；
    若改成「两段都忽略」，那一格变成空串 ⇒ `re.fullmatch` 立刻失败、
    **响亮地**报出来，而不是悄悄通过。
    """
    out = []
    for c in line.strip().strip("|").split("|"):
        c = _STRIKE_RE.sub("", c).replace("*", "").strip()
        out.append(c)
    return out


def _is_separator(cells: list[str]) -> bool:
    """这是不是 markdown 的**分隔行**（`|---|---|`）？

    ⚠️ 单元格内容是 `---`，所以 `set(cells) == {"-"}` **永远不成立**；
    正确写法是「每格都只由 `-`/`:`/空格组成且非空」。"""
    return bool(cells) and all(c and set(c) <= set("-: ") for c in cells)


def _find_table_with_pos(md: str) -> tuple[list[str], list[list[str]], int]:
    """返回 `(表头, 数据行, 表头所在行号)`；找不到就抛 —— **不许静默返回空表**。

    ⚠️ 第一版把结果存在循环变量 `header` 里、循环结束后再断言它 ——
    而表格后面紧跟一个**空行**，于是那个空行把 `header` 清成了 `None`
    ⇒ 明明命中了第 768 行的表头，最后却抛「找不到表」。
    ⇒ **判据读的东西必须和它要守的东西是同一个量**：这里要守的是
    「找到的那张表」，不是「解析器最后一次迭代的状态」。

    🚨🚨 第二版栽在**「什么是表头」**上：判据把「任何以 `|` 开头结尾、
    且内容里含标记的那一行」当表头 —— 而 `CLAUDE.md` 的文档索引表里
    **每一行都是一整条守则说明**，我只要在第 137 层里写一句
    「全仓每张『回本所需本金』表都必须二选一」，那一行就被当成了表头，
    判据转而去报告「表头缺列」。
    ⇒ 它红得**响亮**，这是好事；但它红在**一个错误的对象**上，
    而那正是第 119 层（诊断用了被诊断对象不用的量）的变体。
    ⇒ **修法是结构判据不是启发式**：markdown 表头**后面必然跟一条分隔行**，
    所以「下一行是不是分隔行」就是「这一行是不是表头」的**定义**。

    ⚠️ 第三版才发现：这**必须**是**唯一一份实现**。
    我先写了 `_find_table`，后来为了拿行号又抄了一份 `_find_table_with_pos` ——
    两份判据抄在一起，就等于给「它们不一致」造了一个没有守卫的地方。
    """
    lines = md.splitlines()
    found: tuple[list[str], list[list[str]], int] | None = None
    header: list[str] | None = None
    rows: list[list[str]] = []
    pos = -1                         # 表头所在行号，**不随表格结束而清零**
    i = 0
    while i < len(lines):
        m = _ROW_RE.match(lines[i])
        if not m:
            header, rows = None, []   # 表格结束 ⇒ 后面若有同名的，从头再找
            i += 1
            continue
        if header is None:
            cand = _cells(lines[i])
            nxt = _ROW_RE.match(lines[i + 1]) if i + 1 < len(lines) else None
            if _HEADER_MARK in "".join(cand) and nxt and _is_separator(_cells(nxt.group(1))):
                header, rows, pos = cand, [], i
                i += 2                  # 连分隔行一起跳过
                continue
            i += 1
            continue
        if _is_separator(_cells(m.group(1))):
            i += 1
            continue
        rows.append(_cells(lines[i]))
        # ⚠️ 结果必须存进**另一个绑定**：循环变量 `header`/`rows` 会被下一个
        #   非表格行清空，而文档末尾的状态**通常不是**我们找到的那张表。
        #   （第三版就是漏了这个 `found`，结果 17 条测试全红而原因在最后一行。）
        found = (header, list(rows), pos)
        i += 1
    assert found is not None, \
        f"找不到含「{_HEADER_MARK}」的表头（表头后必须跟一条分隔行）"
    return found


def _find_table(md: str) -> tuple[list[str], list[list[str]]]:
    """`_find_table_with_pos` 的两元组视图 —— **不另写一份判据**。"""
    header, rows, _ = _find_table_with_pos(md)
    return header, rows


def _mismatched_cells(md: str, widths: dict[int, dict]) -> list[str]:
    """`md` 里那张表与产物逐格对照，返回**不一致的格的清单**（空清单 = 一致）。

    ⚠️ 返回清单而不是 bool：空清单恒为假，而条目本身可读 ——
    变异探针打在自己的副本上时，第二条就会看不出差别（第 102 层）。
    """
    header, rows = _find_table(md)
    try:
        i_n = header.index("N")
        i_gross = header.index("毛超额/年")
        i_cap = next(i for i, h in enumerate(header) if _HEADER_MARK in h)
        i_tgt = next(i for i, h in enumerate(header) if "所需本金" in h and i != i_cap)
    except (ValueError, StopIteration) as exc:
        return [f"表头缺列（{exc}）：{header}"]

    out: list[str] = []
    seen: set[int] = set()
    for row in rows:
        if len(row) != len(header):
            out.append(f"列数不符：{row}")
            continue
        try:
            n = int(row[i_n])
        except ValueError:
            out.append(f"N 不可解析：{row[i_n]!r}")
            continue
        seen.add(n)
        w = widths.get(n)
        if w is None:
            out.append(f"N={n} 不在产物 widths 里")
            continue

        want_gross = f"{w['gross_annual'] * 100:.2f}%"
        if row[i_gross].replace(" ", "") != want_gross:
            out.append(f"N={n} 毛超额 {row[i_gross]} ≠ 产物 {want_gross}")

        m = re.fullmatch(r"([0-9.]+)\s*万", row[i_cap])
        want_cap = round(w["capital_for_breakeven"] / 1e4, 1)
        if not m or float(m.group(1)) != want_cap:
            got = m.group(1) if m else repr(row[i_cap])
            out.append(f"N={n} 回本本金 {got} 万 ≠ 产物 {want_cap} 万")

        tgt = w["capital_for_target_net"]
        want_tgt = "不存在" if tgt is None else f"{tgt / 1e4:.1f} 万"
        if row[i_tgt].replace(" ", "") != want_tgt:
            out.append(f"N={n} 到目标本金 {row[i_tgt]} ≠ 产物 {want_tgt}")

    missing = sorted(set(widths) - seen)
    if missing:
        out.append(f"产物里的 N 没有出现在表中：{missing}")
    return out


def _widths_by_n() -> dict[int, dict]:
    data = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    return {int(w["n"]): w for w in data["widths"]}


def _claude() -> str:
    return CLAUDE.read_text(encoding="utf-8")


# ══ ① 能力守卫：判据先被验证，再拿去判别人（第 131 层）═════════════

def test_the_criterion_flags_exactly_the_three_cells_i_actually_mistranscribed():
    """★ 反控：**用我第一版真的抄错的那三个数**当夹具。

    ⚠️ 编一个错不如用真错有说服力：这三条是**已经发生过的**，
    它们证明判据对**真实失效方向**敏感（第 136 层），
    而不是对「我想象出来的失效方向」敏感。"""
    md = _claude().replace("**55.8 万**", "**55.9 万**") \
                   .replace("**81.3 万**", "**81.2 万**") \
                   .replace("**182.4 万**", "**182.3 万**")
    assert md != _claude(), "夹具没改成 —— 反控已经退化成主守卫的同义词"
    hits = _mismatched_cells(md, _widths_by_n())
    assert len(hits) == 3, hits
    assert {h.split()[0] for h in hits} == {"N=100", "N=200", "N=546"}, hits


def test_the_criterion_accepts_the_current_table_verbatim():
    """★ 正控：正确副本 ⇒ 空清单。

    ⚠️ 少了这条，判据「永远返回非空」也能让主守卫全绿。"""
    assert _mismatched_cells(_claude(), _widths_by_n()) == []


# ★ 逐项灵敏度：判据的**每一格**都必须能红。
#   ⚠️ 只测「万」那一列的话，把毛超额那一列的检查删掉，
#     主守卫与上面那条反控**照样全绿** —— 那就是一条守着半个命题的守卫。
#   ⇒ 每一条都是把**夹具**改坏（不是改判据），断言它被抓到且只被抓一次。

# 🚨 2026-10-15 修正：下面 4 个夹具曾**腐烂**，全仓门禁带着 4 条红提交了。
#   成因：CLAUDE.md 那张表**新增了 `se/年` 与「距摩擦下界」两列**（5 值列），
#   而夹具仍写死旧的三值列形状 `| 10 | 4.81% | 16.1 万 | **不存在** |`
#   ⇒ `str.replace` 变成**空操作**。
#   ★ 判据**一个字都不用改**：`_mismatched_cells` 按**表头名**解析列下标
#     （`header.index("毛超额/年")` / `next(i for i,h … if _HEADER_MARK in h)`），
#     所以表变宽它照样对 —— 腐烂的只有夹具。
#     这就是为什么「主守卫绿 + 灵敏度全红」**不矛盾**，也不该靠改判据来修。
#   ⇒ 纪律：这张表**增删列时，下面每个夹具的整行锚点都要跟着重锚**，
#     否则 `md != _claude()` 会以「夹具没改成」**响亮地**报出来（这正是它的作用）。
@pytest.mark.parametrize(
    "edit, expect_frag",
    [
        (lambda s: s.replace("| 10 | 4.81% |", "| 10 | 4.82% |"), "毛超额"),
        # ⚠️ 锚点用**整行**（含 se 与距下界两列），不用行首片段 ——
        #   行首片段在 CLAUDE.md 里有 3 处命中，另外两处在别的表里。
        (lambda s: s.replace(
            "| 10 | 4.81% | 5.46% | **16.1 万** | **+0.004σ** | **不存在** |",
            "| 10 | 4.81% | 5.46% | **16.1 万** | **+0.004σ** | 999.9 万 |"),
         "到目标本金"),
        (lambda s: s.replace(
            "| 546 | 7.57% | 3.33% | **182.4 万** | **+0.836σ** | **不存在** |\n",
            "| 546 | 7.57% | 3.33% | **182.4 万** | **+0.836σ** | **不存在** |\n"
            "| 999 | 5.00% | 3.00% | 9.9 万 | **+9.9σ** | **不存在** |\n"),
         "不在产物"),
        (lambda s: s.replace(
            "| 30 | 5.55% | 4.38% | **23.9 万** | **+0.173σ** | **不存在** |\n", ""),
         "没有出现在表中"),
    ],
    ids=["gross", "target_capital", "extra_row", "missing_row"],
)
def test_each_column_of_the_criterion_is_sensitive(edit, expect_frag):
    md = edit(_claude())
    assert md != _claude(), "夹具没改成 —— 本条已退化成主守卫的同义词"
    hits = _mismatched_cells(md, _widths_by_n())
    assert len(hits) == 1, f"应恰好报一格，却报了 {len(hits)} 格：{hits}"
    assert expect_frag in hits[0], hits[0]


# ── 🚨 「什么才算表头」的能力守卫（第 131 层：判据的第二版就是这么坏的）
#
# ★ 触发它的就是**真实内容**：我在 `CLAUDE.md` 第 137 层的说明里写了
#   「全仓每张『回本所需本金』表都必须二选一」，而那一行**本身是文档索引表的一行**
#   ⇒ 老判据把它当成表头，转而去报「表头缺列」。
#   ⇒ 所以反控夹具必须是**一行含标记、但后面不是分隔行**的伪表头 ——
#   删掉这条的话，正控会把它一起盖住（两者都会去找那张真表）。

def test_a_decoy_row_containing_the_mark_is_not_mistaken_for_a_header():
    decoy = "| `docs/SOMETHING.md` | 全仓每张「回本所需本金」表都必须二选一 |\n" \
            "|---|---|---|\n"          # ← 分隔行在**它下面**，它自己仍是数据行
    md = _claude().replace("## ", "## ") + "\n" + decoy
    assert _mismatched_cells(md, _widths_by_n()) == []


def test_a_real_header_with_a_wrong_row_is_still_reported():
    """反控：结构判据不许把**真**表里的错行也放过。

    ⚠️ 与上一条成对：上一条说「别认错对象」，这一条说「别放过真错」。
    只写上一条的话，把分隔行检查写成永远 False 也能全绿。"""
    md = _claude().replace("| 10 | 4.81% | 5.46% | **16.1 万** |",
                          "| 10 | 4.81% | 5.46% | 99.9 万 |")
    hits = _mismatched_cells(md, _widths_by_n())
    assert len(hits) == 1 and "回本本金" in hits[0], hits


# ══ ② 主守卫：CLAUDE.md 真的与产物一致 ═════════════════════════════

def test_claude_mds_breakeven_table_matches_the_artifact_cell_by_cell():
    widths = _widths_by_n()
    hits = _mismatched_cells(_claude(), widths)
    assert hits == [], (
        "CLAUDE.md 的回本本金表与产物脱钩：\n  " + "\n  ".join(hits) +
        "\n⇒ 第 137 层：这张表是手抄件，抄错不会被任何人发现"
        "（判决一字未变 ⇒ 没人会来报）。修法是照产物字段改回去，"
        "**不许**改判据、也不许在这里重算一遍。"
    )


# 📌 这里**曾经**有一条 `assert sorted(widths) == [10, 30, 100, 200, 546]`，
#   删掉了（第 136 层：过严的守卫和恒真的一样有害）。
#   它的意图（产物多一档时表必须跟着改）是对的，但那条写法要求
#   **改产物时同步改这个字面量**，而产物多一档本来是**正常演进**、
#   不是回归 ⇒ 它会在正确的一边红，把人推向「别加新档」或「改测试让它绿」。
#   ⇒ 完备性已经由判据自己的 `missing` 分支 + `missing_row` 那条控制守住，
#     **字面量版本是纯冗余**（重复守同一件事，且守得更差）。


# ══ ③ 债条：**这张表自己不自报口径**（与 `factor_width_mde` 同族）════

def test_the_claude_table_does_not_self_label_its_caliber_and_the_debt_is_registered():
    """★ 债条，**双向**钉。

    `width_capital_feasibility.json` 的 `widths[*]` **没有**口径标签，
    `CLAUDE.md` 这张表也**没有** —— 而预注册 G2 写着「口径标签是必填」。
    ⇒ 读这张表的人无从知道「回本」是拿**毛超额**去抵摩擦算的。

    补上标签 ⇒ 本条红（要求删掉债登记）；
    删掉债登记 ⇒ 本条红（要求补标签）。

    ⚠️ 不许把它改成「断言它有标签」：那份产物在判决链上，
    补字段要一次 GB 级重跑，而当前跑还在飞（第 125 层）。
    ⚠️ 也不许守「加粗/排版」这类美学 —— 过严的守卫和恒真的一样有害
    （第 112 层），且它与「读者会不会引用错数」无关。"""
    log = (ROOT / "docs" / "MISTAKE_LOG_2026-10-14.md").read_text(encoding="utf-8")
    assert "不自报口径" in log, "债登记被删了 —— 请给产物与这张表补上口径标签"
    assert _mismatched_cells(_claude(), _widths_by_n()) == [], \
        "债登记在，但主守卫已经红了 —— 先修表，别用这条挡住它"


# ── 删除线解析的能力守卫（第 131 层：新加的宽容必须先证明它不吞错）────
#
# ⚠️ `_cells` 为了让 MISTAKE_LOG 那张「划掉旧值 + 补上新值」的表能解析，
#   删掉了 `~~…~~` 的**内容**。这是一处**新引入的宽容** ——
#   而宽容的默认结局就是「把错吞掉」。所以必须有反控：
#   **只划掉、不补新值**的那一格，必须被报出来，不许因为「反正有删除线」而放过。

@pytest.mark.parametrize(
    "cell, expect_ok",
    [
        ("~~55.9 万~~ 55.8 万", True),      # 划掉旧值 + 补上新值 ⇒ 取新值，通过
        ("~~55.9 万~~", False),            # 只划掉、没有新值 ⇒ 空 ⇒ 响亮地红
        ("55.9 万", False),                # 没划也没改 ⇒ 红（这是原方向）
    ],
    ids=["corrected", "struck_only", "plain_wrong"],
)
def test_strikethrough_is_stripped_but_a_struck_only_cell_is_not_whitelisted(cell, expect_ok):
    md = _claude().replace("**55.8 万**", f"**{cell}**")
    hits = _mismatched_cells(md, _widths_by_n())
    assert (len(hits) == 0) is expect_ok, f"{cell!r} ⇒ {hits}"


# ══ ④ 🚨 覆盖面：上面那 8 条**只扫 CLAUDE.md 一个文件** ═════════════
#
# ★ 第 137 层真正的教训不是「我抄错了」，而是**「加了对账守卫」本身
#   也可以只对了一半**。写完那个守卫之后我又撞见同一组错数
#   （55.9/81.2/182.3）的**第二份副本**在 `aplus/provenance.py` 的注释里 ——
#   而守卫**全绿**。所以下面这组把判据的**覆盖面**变成被守卫的东西：
#   **全仓每一张「回本所需本金」表，都必须二选一**：
#   与产物逐格一致，**或**在同一个文件里就地声明作废。
#
# ⚠️ 为什么允许「声明作废」而不是强求全部一致：
#   `docs/TODO_basket_width_2026-10-15.md` 里那张表是**修正前的旧口径**
#   （毛超额 6.52%~9.78%，比现行高 1.29~1.55 倍），而那份文件**通篇是留痕**。
#   把它改成新值 = 抹掉「我们曾经算错」这件事实（本项目就靠这条留痕）；
#   放着不管 = 读者会把 4.8 万当成现行结论（`CLAUDE.md` 上是 16.1 万，
#   **两份差 3.4 倍**，而两个数看起来一模一样地正经）。
#   ⇒ 唯一诚实的处置是**标注**，不是改数、也不是删表。

#: 「这张表已作废」的就地声明。
#: ⚠️ **必须是表头之前的一条 markdown 引用行（`> …`）**，不是「文件里出现过这句话」。
#: 🚨 第三版写的是 `if _STALE_MARK in text` —— 而 `CLAUDE.md` 的第 137 层说明里
#:   恰好**引用了这六个字**（「或就地声明 `下面这张表已作废`」），
#:   于是**每一个以 `CLAUDE.md` 为夹具的文件都被判成「已声明作废」**
#:   ⇒ 判据退化成**永远空清单** ⇒ 守的正是它自己的反控。
#: ⇒ 这是「守卫里的字符串不许是它自己要找的东西」——
#:   **判据读的东西必须和它要守的东西是同一个量**（第 132 层通则的又一次）。
_STALE_MARK = "下面这张表已作废"
_STALE_RE = re.compile(r"^\s*>\s.*" + re.escape(_STALE_MARK), re.M)


def _declares_stale(md: str, header_line: int) -> bool:
    """这个文件**在该表之前**声明了它作废吗？

    ⚠️ 限定「之前」是因为**声明在表之后**没有任何意义 ——
    而允许它的话，一份文件可以先放一张**现行的**表、
    再在文末挂一句「下面这张表已作废」，把现行表也一并赦免。"""
    return any(_STALE_RE.match(line)
               for line in md.splitlines()[:header_line])

#: 不扫的目录：虚拟环境 / 版本库 / 产物 / 缓存。
_SKIP_DIRS = {".venv", "venv", ".git", "data", "__pycache__",
              ".pytest_cache", ".testtmp", "node_modules"}


def _capital_table_files(base: Path | None = None) -> list[Path]:
    """`base` 下所有**含「回本所需本金」表格**的文件（按路径排序）。

    ⚠️ 用 `os.walk` 且**剪枝**，不是 `rglob`：`rglob("*")` 会走进 `.venv`，
    在一台装了 scipy/pandas 的机器上那是几万次无用的 stat。
    """
    root = base or ROOT
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in sorted(filenames):
            if not fn.endswith((".md", ".py")):
                continue
            path = Path(dirpath) / fn
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            try:
                _find_table(text)
            except AssertionError:
                continue
            out.append(path)
    return out


def _unclassified(files: list[Path], widths: dict) -> list[str]:
    """既不与产物一致、又没就地声明作废的表 —— **返回清单而不是 bool**。"""
    out = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        hits = _mismatched_cells(text, widths)
        if not hits:
            continue
        _, _, pos = _find_table_with_pos(text)
        if _declares_stale(text, pos):
            continue
        try:
            label: Path = path.relative_to(ROOT)
        except ValueError:            # 夹具在仓外（tmp_path）—— 只报文件名
            label = Path(path.name)
        out.append(f"{label}：{'；'.join(hits)}")
    return out


def test_every_capital_table_in_the_repo_is_either_current_or_declared_stale():
    """主守卫（覆盖面）：**全仓**没有「默默错着」的回本本金表。"""
    widths = _widths_by_n()
    files = _capital_table_files()
    assert files, f"扫描器一个表都没找到 —— 它已经瞎了（第 131 层：守卫要验守卫）"
    bad = _unclassified(files, widths)
    assert bad == [], (
        "以下「回本所需本金」表与产物脱钩，且没有就地声明作废：\n  " + "\n  ".join(bad) +
        "\n⇒ 第 137 层：这张表是手抄件。**要么照产物改回去，"
        "要么在同一处加一句「下面这张表已作废」并说明现行值在哪。**"
        "\n⚠️ 若它是旧口径的留痕，**改数等于抹掉历史** —— 标注，不要改。"
    )


def test_a_stale_declaration_is_removed_once_the_table_has_been_brought_current():
    """★ 反向钉：声明作废之后**表被改正了** ⇒ 声明变成假话 ⇒ 本条红。

    ⚠️ 没有这条，`下面这张表已作废` 会变成一句**可以永远躺着的话** ——
    而「作废声明本身腐烂」与「表腐烂」是同一种病，看症状完全一样。"""
    widths = _widths_by_n()
    for path in _capital_table_files():
        text = path.read_text(encoding="utf-8")
        _, _, pos = _find_table_with_pos(text)
        if not _declares_stale(text, pos):
            continue
        hits = _mismatched_cells(text, widths)
        assert hits, (
            f"{path.name} 声明「{_STALE_MARK}」，但它的表现在与产物一致了"
            " ⇒ 请删掉作废声明（留着就是一句假话）")


# ── 扫描器的能力守卫（正反各一，第 131 层）────────────────────────────

def test_the_coverage_criterion_accepts_a_current_table_and_rejects_a_silent_one(tmp_path):
    """夹具：四张都**只含表**，差别只在「声明那句话写在哪、怎么写」。

    ⚠️ 反控必须精确到**声明的位置与形态**：判据若退化成
    「文件里出现过『下面这张表已作废』就算数」，那
    **一句普通散文**、甚至一句**写在表后面**的话，都能赦免一张错的表。
    ⇒ 这正是它自己栽过的那一跤（`CLAUDE.md` 自己在第 137 层里引用了这六个字）。"""
    widths = _widths_by_n()
    wrong = _claude().replace("**182.4 万**", "**182.3 万**")
    (tmp_path / "current.md").write_text(_claude(), encoding="utf-8")
    (tmp_path / "silent.md").write_text(wrong, encoding="utf-8")
    (tmp_path / "declared.md").write_text(f"> {_STALE_MARK}（旧口径）\n\n" + wrong,
                                          encoding="utf-8")
    # 🚨 只在**散文里提到**那六个字（没有 `>`）⇒ 不算声明
    (tmp_path / "prose.md").write_text(f"本文要求就地声明 `{_STALE_MARK}`。\n\n" + wrong,
                                       encoding="utf-8")
    # 🚨 声明写在**表后面** ⇒ 不算声明（声明在表之后没有意义）
    (tmp_path / "after.md").write_text(wrong + f"\n\n> {_STALE_MARK}\n",
                                       encoding="utf-8")
    hits = _unclassified(_capital_table_files(tmp_path), widths)
    # ⚠️ 断言**完整的标签**，不取 `.name` —— 取了就等于把「标签可读性」
    #   这条变异彻底屏蔽掉（它当时就是这样存活的）。
    got = sorted(h.split("：")[0] for h in hits)
    assert got == ["after.md", "prose.md", "silent.md"], got


def test_the_scan_does_not_walk_the_virtualenv(tmp_path):
    """★ 剪枝是真的剪了：**造一个叫 `.venv` 的毒文件**，它必须看不见。

    ⚠️ 第 119 层的变体：一个**永远绿**的扫描器（因为它什么都没找到）
    与一个**真的扫过了**的扫描器，在这仓里长得一模一样。
    ⇒ 必须有一条测试证明它**看得见**，还得有一条证明它**不去看不该看的**。"""
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "poison.md").write_text(_claude(), encoding="utf-8")
    assert _capital_table_files(tmp_path) == []
    real = tmp_path / "real.md"
    real.write_text(_claude(), encoding="utf-8")
    assert [p.name for p in _capital_table_files(tmp_path)] == ["real.md"]


def test_the_known_copies_are_all_still_classified():
    """★ 登记在册的那几张表**一张都不许悄悄变成新的存在方式**。

    这条把「覆盖面」从一个开放的集合变成**可点名的清单**：
    新增一张回本表 ⇒ 它要么自动通过前两条，要么在这里被登记。
    ⚠️ 不许把清单写死成「必须恰好是这几个文件」——
    那是第 112 层的过严守卫（产物正常演进时会红），正确写法是
    **每张表都必须被前两条之一分类**，本条只断言这些**已知**的没被改名躲掉。"""
    names = {p.relative_to(ROOT).as_posix() for p in _capital_table_files()}
    for must in ("CLAUDE.md",
                 "docs/TODO_basket_width_2026-10-15.md",
                 "tests/test_claude_md_capital_table_matches_artifact.py"):
        assert must in names, f"{must} 不再被扫到 —— 它改名或搬走了？先更新覆盖面"
