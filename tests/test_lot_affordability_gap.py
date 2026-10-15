"""整手（A 股 100 股）这个缺口：**必须一直保持「看得见的缺口」**（预注册 §4 G6）。

## 缺口是什么

回测与摩擦**都在小数股上跑**：
实测 `aplus/backtest.py` 与 `aplus/costs.py` 的**可执行代码**里没有任何整手建模
（本守卫第 ③ 组用 AST 证明，且**注释/docstring 不算**）。

⇒ 而 `scripts/factor_width_mde.py:103` 是 `EQUITY_WEIGHT = 1.0`（满仓、无现金缓冲），
单笔 = `50000 / N`，1 手（100 股）要买得起，股价须 ≤ `50000/(100·N)`。
实测抽样 499 只的买得起比例（产物 `data/reports/lot_affordability.json`）：

| N | 10 | 30 | 100 | 200 | 546 |
|---|---|---|---|---|---|
| 买得起 1 手 | **89.2%** | 54.1% | 14.4% | 4.2% | 3.2% |

⇒ **「宽榜更稳」（毛超额 4.81% → 7.57%）建立在一个比真实账户更宽的集合上。**

## 为什么这条守卫是**双向**的

这个缺口是**实测出来的**，不是猜的；但它也不会自己消失 ——
**代码不会因为文档里写了「这是缺口」就长出整手建模**。
⇒ 只登记不钉，它就会慢慢变成「已处理」（文档里有一节，
读者以为有人管了，而代码一个字没变）。

- 有人**真的加上了**整手建模 ⇒ 本条红（要求删掉预注册的债登记）；
- 有人**删掉**债登记 ⇒ 本条红（要求把缺口写回去）。

## ⚠️ 不要把它改成「断言已经有整手建模」

那要一次 GB 级重跑，而 `factor_width_mde.json` 在判决链上（第 125 层）。
**先把缺口钉成缺口**，再谈修法。

## ⚠️ `MIN_BASKET` 不是整手过滤器

`aplus/verify.py:398` 是 `daily = daily[size >= MIN_BASKET]`，
`size` 是**篮子里的成分个数**。它守的是「有效成分数太少的日子整天丢掉」，
**与股价、与 1 手买不买得起毫无关系**。
⚠️ 仓里**已经有**人拿它当过挡箭牌，所以第 ② 组专门钉住这件事。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "data" / "reports" / "lot_affordability.json"
PREREG = ROOT / "docs" / "TODO_nav_vs_excess_caliber_2026-10-15.md"
MISTAKE = ROOT / "docs" / "MISTAKE_LOG_2026-10-14.md"
PROVENANCE = ROOT / "aplus" / "provenance.py"

#: 「这个缺口还没被修」的登记措辞 —— 删掉任何一处都会让本守卫红。
_PREREG_MARK = "整手建模仓内为零"
_LOG_MARK = "整手建模仓内为零"

#: **判定范围**：缺口说的就是这两个模块，别的模块有 100 股不构成缺口消失。
_MODULES = ("aplus/backtest.py", "aplus/costs.py")


# ── 产物自洽性：先证明这份产物自己没毛病，再拿它当证据 ──────────────────

def _artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("row_key", ["per_position_notional", "max_price_for_one_lot"])
def test_every_width_row_is_internally_consistent(row_key):
    """单笔 × N == 本金；1 手股价上限 × 100 == 单笔。

    ⚠️ 这不是「重新推导一遍」，是**产物自己跟自己**对账 ——
    判据读的就是产物里那两个字段（第 109 层：核对产物只读字段）。"""
    art = _artifact()
    for w in art["widths"]:
        per = w["per_position_notional"]
        assert per * w["n"] == pytest.approx(art["capital"], rel=1e-9), w
        assert w["max_price_for_one_lot"] * art["lot_shares"] == pytest.approx(
            per, rel=1e-9), w
        assert 0 <= w["affordable_pct"] <= 1, w
        assert w["affordable_count"] <= art["sample"]["usable"], w


def test_the_artifact_states_its_price_caliber_and_what_it_cannot_prove():
    """⚠️ 口径标签是**必填**（预注册 G2）：判「能不能买」必须用**不复权**价。

    少了这条，这份产物可以哪天悄悄改成后复权价而无人发现 ——
    而后复权价在除权后会**低得多** ⇒ 「买得起比例」会**虚高**，
    正好把这条缺口的方向翻过来。"""
    art = _artifact()
    assert "不复权" in art["price_caliber"]
    assert art["what_this_cannot_prove"], "产物不许把自己说得比实际强"


def test_the_artifact_is_registered_as_generated_by_that_script():
    """登记 + 盖章缺一不可（第 127 层：`unknown` = **不许引用**）。"""
    tree = ast.parse(PROVENANCE.read_text(encoding="utf-8"))
    reg = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == ARTIFACT.name:
                    if isinstance(v, ast.Constant):
                        reg = v.value
    assert reg == "scripts/lot_affordability.py", reg
    assert ARTIFACT.with_suffix(".json.provenance.json").exists(), \
        "登记了却没盖章 ⇒ freshness 报 unknown ⇒ 按本仓规矩**不许引用**"


# ══ ② `MIN_BASKET` 守的是「成分个数」，不是「买不买得起」 ═══════════════
#
# ⚠️ 这一条钉的是**读法**，不是代码：仓里已经有人把它当过整手问题的挡箭牌。
# ⇒ 所以判据锚在**语法位置**（它被拿去和什么比），不是锚在它的名字上。

def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_min_basket_is_compared_against_a_basket_size_not_a_price():
    """`MIN_BASKET` 的**每一次比较**都必须拿「成分个数」当左边。

    ⚠️ 用 AST 而不是 `grep`：第一版按行过滤 `#` 注释，
    结果 `verify.py` 的一句 **docstring**（「低于 `MIN_BASKET` 的日子整天丢掉」）
    被当成了一处用法 ⇒ 判据红在**散文**上 —— 又是一次「读的东西 ≠ 守的东西」。
    ⇒ 改判据，不改代码：真正的「用法」是**比较表达式**，不是提到这个词的行。"""
    tree = ast.parse((ROOT / "aplus" / "verify.py").read_text(encoding="utf-8"))
    cmps = [n for n in ast.walk(tree)
            if isinstance(n, ast.Compare)
            and "MIN_BASKET" in {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}]
    assert cmps, "MIN_BASKET 在 verify.py 里没有参与任何比较 —— 先看它去哪了"
    for c in cmps:
        left = _names(c.left) | _names(c.comparators[0])
        assert any("size" in nm for nm in left), (
            f"verify.py:{c.lineno} 把 MIN_BASKET 和不是成分个数的东西比了："
            f"{ast.unparse(c)}")


def test_the_prereg_says_explicitly_that_min_basket_is_not_a_lot_filter():
    """债必须**点名**那个被误用的东西；只写「有这个缺口」不够。"""
    doc = PREREG.read_text(encoding="utf-8")
    assert "MIN_BASKET" in doc and "不是**价格/整手过滤器" in doc


# ══ ③ 双向钉：缺口是「看得见的缺口」，不是「已处理」 ═══════════════════

def _lot_hits_in(path: Path) -> list[str]:
    """该文件**可执行代码**里出现「整手」的位置（注释与 docstring 不算）。

    ⚠️ 必须排除 docstring：`aplus/provenance.py` 的登记表里就写着
    「实测 `grep -rn '整手' aplus/ scripts/` 零命中」这一行说明 ——
    而用 `grep` 判「有没有整手建模」会被**它自己的登记文字**命中。
    ⇒ 这是第 119 层的又一次：**判据读的东西必须和它要守的东西是同一个量**。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and body and \
                isinstance(body[0], ast.Expr) and \
                isinstance(getattr(body[0], "value", None), ast.Constant) and \
                isinstance(body[0].value.value, str):
            doc_ids.add(id(body[0].value))
    return [f"{path.name}:{node.lineno}" for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "整手" in node.value and id(node) not in doc_ids]


def _executable_整手_hits(rel: str) -> list[str]:
    return _lot_hits_in(ROOT / rel)


def test_the_lot_modelling_gap_still_exists_and_is_registered_on_both_sides():
    """★ 双向钉的正控：缺口**在**，登记**也在**。

    ⚠️ 断言的是「**同时**成立」。只断登记那条 ⇒ 加了建模不报；
    只断缺口那条 ⇒ 登记被删不报 —— 而这两种都是「看起来已处理」。"""
    hits = [h for rel in _MODULES for h in _executable_整手_hits(rel)]
    assert hits == [], (
        f"整手建模已经出现在 {hits} ⇒ **缺口被修掉了** —— "
        "请删掉预注册 §4 G6 与本文件的双向钉，"
        "并重跑 GB 级的 scripts/factor_width_mde.py（产物口径会变，"
        "第 125 层：产物字节在生成期间必须冻结）")
    assert _PREREG_MARK in PREREG.read_text(encoding="utf-8"), \
        "预注册里的债登记被删了 ⇒ 请把「整手建模仓内为零」写回去"
    assert _LOG_MARK in MISTAKE.read_text(encoding="utf-8"), \
        "MISTAKE_LOG 里的债登记被删了 ⇒ 同上"


def test_the_detector_sees_real_lot_modelling_but_ignores_the_registration_prose(tmp_path):
    """★ 探测器自身的正反控（第 131 层：只验探测器 = 假信用）。

    正控必须是**可执行代码里真的一句话常量**，反控必须是**纯 docstring** ——
    因为 `aplus/provenance.py` 里的登记说明恰好就是纯 docstring/注释形态。
    ⚠️ 只测反控的话，一个**永远返回空**的探测器也能全绿。"""
    real = tmp_path / "real.py"
    real.write_text('LOT_RULE = "整手 = 100 股"\n', encoding="utf-8")
    assert len(_lot_hits_in(real)) == 1

    prose = tmp_path / "prose.py"
    prose.write_text(
        '"""实测 `grep -rn 整手 aplus/ scripts/` 零命中。"""\n'
        '# 整手建模仓内为零\n'
        'VALUE = 1\n',
        encoding="utf-8")
    assert _lot_hits_in(prose) == []
