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


def _cells(line: str) -> list[str]:
    """把一行 markdown 表格拆成去掉加粗与空白的单元格。"""
    return [c.strip().replace("*", "") for c in line.strip().strip("|").split("|")]


def _find_table(md: str) -> tuple[list[str], list[list[str]]]:
    """返回 `(表头, 数据行)`；找不到就抛 —— **不许静默返回空表**。

    ⚠️ 第一版把结果存在循环变量 `header` 里、循环结束后再断言它 ——
    而表格后面紧跟一个**空行**，于是那个空行把 `header` 清成了 `None`
    ⇒ 明明命中了第 768 行的表头，最后却抛「找不到表」。
    ⇒ **判据读的东西必须和它要守的东西是同一个量**：这里要守的是
    「找到的那张表」，不是「解析器最后一次迭代的状态」。
    """
    lines = md.splitlines()
    found: tuple[list[str], list[list[str]]] | None = None
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in lines:
        m = _ROW_RE.match(line)
        if not m:
            header, rows = None, []      # 表格结束 ⇒ 后面若有同名的，从头再找
            continue
        if header is None:
            cand = _cells(line)
            if _HEADER_MARK in "".join(cand):
                header, rows = cand, []
            continue
        if all(set(c) <= set("-: ") and c for c in _cells(line)):
            continue                     # 分隔行 `|---|---|---|`
        rows.append(_cells(line))
        found = (header, rows)
    assert found is not None, f"CLAUDE.md 里找不到含「{_HEADER_MARK}」的表"
    return found


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

@pytest.mark.parametrize(
    "edit, expect_frag",
    [
        (lambda s: s.replace("| 10 | 4.81% |", "| 10 | 4.82% |"), "毛超额"),
        (lambda s: s.replace("| 10 | 4.81% | 16.1 万 | **不存在** |",
                             "| 10 | 4.81% | 16.1 万 | 999.9 万 |"), "到目标本金"),
        (lambda s: s.replace("| 546 | 7.57% | **182.4 万** | **不存在** |",
                             "| 546 | 7.57% | **182.4 万** | **不存在** |\n"
                             "| 999 | 5.00% | 9.9 万 | **不存在** |"), "不在产物"),
        (lambda s: s.replace("| 30 | 5.55% | 23.9 万 | **不存在** |\n", ""), "没有出现在表中"),
    ],
    ids=["gross", "target_capital", "extra_row", "missing_row"],
)
def test_each_column_of_the_criterion_is_sensitive(edit, expect_frag):
    md = edit(_claude())
    assert md != _claude(), "夹具没改成 —— 本条已退化成主守卫的同义词"
    hits = _mismatched_cells(md, _widths_by_n())
    assert len(hits) == 1, f"应恰好报一格，却报了 {len(hits)} 格：{hits}"
    assert expect_frag in hits[0], hits[0]


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
