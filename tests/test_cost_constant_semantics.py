"""两个 `0.003` 是**同一个数、不同的含义**——而它们住在一个项目里。

## 这是本项目里最贵的一种巧合

| 位置 | 值 | 含义 | 覆盖了哪些费用 |
|---|---|---|---|
| `aplus/backtest.py:STOCK_COST_DEFAULT` | `0.003` | **单边价差 × 2** | 只有价差 |
| `aplus/bond_horizon.py:COST_WAN15` | `0.003` | **万15/边 × 双边** | 只有佣金 |

股票侧漏掉的是**佣金、印花税、过户费**——实测 5 万本金单笔往返
`FeeSchedule.round_trip(5000) = ¥27.60 = 0.5520%`，而 `0.003` 只有 **0.30%**，
**低估 1.84 倍**。

债券侧则确实没有印花税（免税），它的 0.003 是佣金全额，**在债券语境下是对的**。

> **所以「把债券侧的 0.003 改成 0.0055」是错的**——
> 那是股票的修法，套到债券上会引入一个新的偏差。
> 我先前把这个记成「债券侧 fork，一起改」，那是一个**看起来周全、
> 实际会造成第二个错误**的判断。

## 为什么这条必须钉住

`docs/TODO_cost_constant_alignment.md` 计划把股票侧提到 0.0055。
如果那时有人 grep `0.003` 顺手把债券侧一起改，
就会在**修一个低估的同时制造一个高估**——
而债券侧的数字会因此变得更乐观，正好又是「支持既有结论」的方向。

## 三条纪律

1. **同名不同义的常数必须改名**，让 grep 找不到它们。
2. **债券侧不该有自己的 `DEFAULT_COST`**——它已经有一个有名字、
   有历史的真相源 `COST_WAN15`，另外两处裸字面量是复制品。
3. **跨市场不能类推**：股票的「该收哪些费」与债券不同，
   股票侧的低估结论**不可迁移**。

## 为什么「默认取保守档」在债券侧是对的、股票侧却抄了同一个数

`bond_horizon.py` 记录过一次真实事故：默认 0.0003（低保费通道）
而判定用 0.003，**任何漏传 cost 的调用方都静默拿到乐观两倍的结果**。
于是它「宁可默认保守」。

股票侧学到这一点后，把 0.003 也标成了「保守」——
**但它保守的是价差，不是全部费用**。
两个默认值看起来都是「保守」，覆盖的费用项目却不同。
**「保守」是一个关于方向的形容词，不是关于覆盖范围的承诺。**
"""
import inspect
import re

import pytest


# ── 债券侧：裸字面量必须消失，全部指回有名字的真相源 ─────────────────

def test_bond_modules_reference_the_named_constant_not_a_literal():
    """三个 0.003 里只有 `COST_WAN15` 有名字、有历史、有注释。
    另外两处是复制品——复制品的注释说「与主判定口径一致」，
    而**没有任何东西保证它们真的同步**。"""
    from aplus import bond_excess, bond_horizon, bond_paper

    for mod in (bond_excess, bond_paper):
        src = inspect.getsource(mod)
        # 裸赋值 `DEFAULT_COST = 0.003` 视为复制品
        assert not re.search(r"^DEFAULT_COST\s*=\s*0\.003", src, re.M), (
            f"{mod.__name__} 有裸的 DEFAULT_COST = 0.003——"
            f"**改成从 bond_horizon.COST_WAN15 导入**，"
            f"否则债券侧有三处可以各自漂移的同一费率"
        )

    assert bond_excess.DEFAULT_COST == bond_horizon.COST_WAN15
    assert bond_paper.DEFAULT_COST == bond_horizon.COST_WAN15


# ── 同名不同义：这是本文件的核心 ─────────────────────────────────────

def test_the_stock_and_bond_constants_are_numerically_equal_but_mean_different_things():
    """**这条是本文件存在的理由。**

    两个常数都叫「成本」，都是 0.003，但：
    - 股票的 0.003 = 单边价差 × 2（佣金/印花税/过户费**全缺**）
    - 债券的 0.003 = 万15/边 × 双边（债券无印花税，佣金已全额计入）

    数值相同纯属巧合。**grep 一个数字去批量修，会在修好一个的同时弄坏另一个。**
    """
    from aplus.backtest import STOCK_COST_DEFAULT
    from aplus.bond_horizon import COST_WAN15

    assert STOCK_COST_DEFAULT == COST_WAN15, (
        "两者数值仍相同——**这正是陷阱本身**。"
        "若将来股票侧改口径，本文件其他测试会提醒你：别顺手改债券侧。"
    )
    # 而股票侧的低估是真的，且方向是「低估」：
    assert STOCK_COST_DEFAULT < 0.0055, "股票侧 0.003 已知低估真实往返成本"


def test_the_bond_constant_must_not_be_changed_to_the_stock_reality():
    """**反向钉**：若有人把债券侧改成 0.0055，这条会红。

    股票的 0.5520% 包含印花税 0.05% 与过户费，**债券两者都没有**。
    把股票的修法套到债券上 = 凭空多收 10 个百分点的费用，
    债券策略会显得比实际更差——**方向同样不利于既有结论**，
    同样不会被质疑。
    """
    from aplus.bond_horizon import COST_WAN15

    assert COST_WAN15 == 0.003, (
        "债券侧费率先核对再改。股票侧的修法（0.003 → 0.0055）**不可迁移**："
        "印花税与过户费是股票独有，债券没有。"
        "真要改，必须先拿债券实际成交费率单独立论，"
        "而不是沿用股票侧的数。"
    )


def test_the_stock_constant_is_known_to_understate_and_that_is_documented():
    """股票侧的低估必须**有据可查**，而不是靠记忆。
    `docs/TODO_cost_constant_alignment.md` 是那份依据。"""
    from pathlib import Path

    from aplus.backtest import STOCK_COST_DEFAULT

    doc = Path("docs/TODO_cost_constant_alignment.md")
    assert doc.is_file(), "低估值必须有成文依据，不能只活在源码注释里"
    assert "0.0055" in doc.read_text(encoding="utf-8")
    assert STOCK_COST_DEFAULT == 0.003, (
        "若股票侧已改口径，本文件的多处断言需要重新评估——"
        "**改之前先读 docs/TODO_cost_constant_alignment.md 的影响面清单**"
    )


# ── 真实往返成本的可复算性 ─────────────────────────────────────────

def test_the_real_round_trip_cost_is_reproducible_not_asserted():
    """真实口径必须是**算出来的**，不是注释里写着的一个数。
    注释里的数会漂（本轮已发生两次），算式不会。"""
    from aplus.costs import FeeSchedule

    f = FeeSchedule()
    rt = f.round_trip(5000)
    assert rt == pytest.approx(27.60, abs=0.01)
    # 0.0055 是「往返成本的比率」，不是某个费用项的费率
    assert rt / 5000 == pytest.approx(0.0055, abs=0.0001)