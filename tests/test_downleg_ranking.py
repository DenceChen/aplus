"""跌腿臂的守卫。

⚠️ 本文件的三条主判据都写成**可判别的否定性质**，不是形状断言：
形状断言在实现恒返 None / 恒真时**照样全绿**（第 120 层）。

**已知欠账（不在本文件覆盖内）**
- `main()` 的编排层（含 `overlap_check`）未被覆盖（第 96 层：读快照的守卫等于没守）。
- 产物层零覆盖：本文件全部对**函数**取证，不读 `data/reports/downleg_ranking.json`。
  ⇒ 源码改了、产物没重跑，本文件照样全绿（第 96 层）。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "measure_downleg_ranking.py"


def _load():
    spec = importlib.util.spec_from_file_location("_downleg_arm", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_downleg_arm"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


# ── 夹具 ────────────────────────────────────────────────────────────

def _frame(pairs, pred_dates=None, entry_dates=None):
    """pairs = [(score, gross_pct), …]。

    ⚠️ **默认所有行同一天 `entry_date`**（多个 `pred_date` 映射到同一成交窗口）
    —— 这才是真实底稿的形状（实测首窗 30 行来自 3 个信号日）。
    🚨 第一版默认「每行一个 entry_date」⇒ 每窗只有 1 行 < `2*MIN_HALF`
       ⇒ 排序力臂返回 `[]` ⇒ `IndexError`，
       而 `IndexError` 在 pytest 里也是 `rc=1`，与「守卫守住了」**逐位同形**
       （第 172 层：红的原因不对 ⇒ 会被读成「守卫有洞」而去改本来正确的实现）。
    """
    n = len(pairs)
    pred = pred_dates or [f"2026-10-{9 + i:02d}" for i in range(n)]
    entry = entry_dates or ["2026-10-12"] * n
    return pd.DataFrame({
        "pred_date": pred,
        "entry_date": entry,
        "exit_date": entry,
        "direction": ["down"] * n,
        "gross_pct": [g for _, g in pairs],
        "market_pct": [0.0] * n,
        "score": [s for s, _ in pairs],
    })


REQUIRED = ('pred_date', 'entry_date', 'exit_date', 'direction',
            'gross_pct', 'market_pct')


# ── ① 符号约定必须被**测量**，不能被断言 ──────────────────────────────

def test_the_sign_convention_is_measured_on_both_gradients():
    """`spread > 0` ⟺ 有排序力，**两条梯度都要测**。

    🚨 只测「低分跌更多」这一条的话，把 `spread` 整个取反的变异也能活
    ⇒ 必须同时测**反向梯度**拿到负的 spread。
    """
    # 正梯度：分数越低 ⇒ 跌得越多 ⇒ 生产发的那半 gross 更低 ⇒ spread > 0
    good = _frame([(-3.0, -0.05), (-2.0, -0.04), (-1.0, +0.01), (0.0, +0.02)])
    rows = M.ranking_spread_per_window(good)
    assert len(rows) == 1
    assert rows[0]["spread"] > 0.0, "低分跌更多时 spread 必须为正"

    # 反向梯度：分数越低 ⇒ 涨得越多 ⇒ 生产发的那半 gross 更高 ⇒ spread < 0
    bad = _frame([(-3.0, +0.05), (-2.0, +0.04), (-1.0, -0.01), (0.0, -0.02)])
    assert M.ranking_spread_per_window(bad)[0]["spread"] < 0.0, (
        "反向梯度必须给负 spread —— 否则这条守卫只钉住了半边，"
        "而把 spread 取反的变异照样能活（第 112 层）")


def test_the_lowest_half_is_the_one_production_ships():
    """生产发的是 `ranked.tail(bottom_n)` ⇒ 分数**最低**的那半。

    ⚠️ 这是与 `aplus/predict.py:276` 对齐的性质（第 119 层）。
    钉成「`lowest_score_mean < highest_score_mean`」是**恒真的**（由排序定义保证），
    所以真正承重的是**它对着 gross 那一半**——
    若切半方向反了，这个不等式照样成立而 gross 会互换。
    """
    frame = _frame([(-9.0, -0.05), (-8.0, -0.04), (-7.0, -0.03), (-6.0, +0.10)])
    row = M.ranking_spread_per_window(frame)[0]
    assert row["lowest_score_mean"] == pytest.approx(-8.5)
    assert row["lowest_gross_mean"] == pytest.approx(-0.045)
    assert row["highest_gross_mean"] == pytest.approx(0.035)


# ── ② years 不可达判决分支（AST 层，才可判别） ─────────────────────

_VERDICT_FNS = ("judge", "verdict_about_downleg")


def _fn_ast(name):
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"判决函数 {name} 不存在 —— 脚本结构变了，先看它是不是还在判")


def test_the_verdict_functions_cannot_see_years():
    """🚨 `years` **不可达**判决分支。

    行为层测不了这条：给 `caliber` 换 years 不改判决，因为 `caliber` 的输出
    **根本不进** `judge` ⇒ 那样写是**恒真的**（第 112 层）。
    唯一可判别的形态是 AST：判决函数的体里不许出现 `'years'` 这个字符串。
    ⇒ 有人加 `if stats["years"] < 1.0: return POWERLESS` 会当场红。
    """
    for name in _VERDICT_FNS:
        node = _fn_ast(name)
        strings = {n.value for n in ast.walk(node)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert not any("years" in s for s in strings), (
            f"{name}() 里出现了 years —— 预注册 §4 规定只用 "
            f"MIN_BLOCKS_FOR_ANY_SIGN 当结构闸门；years≥1.0 要求 244 个窗口，"
            f"会永久压过 6 而让判决永远 POWERLESS")

        # 同一条性质的第二半：判决分支里不许有**任何数值字面量**。
        # 门槛必须是 import 进来的常量，否则「改一个魔数」就能换判据。
        for cmp_node in ast.walk(node):
            if not isinstance(cmp_node, ast.Compare):
                continue
            operands = list(cmp_node.comparators) + [cmp_node.left]
            for op in operands:
                assert not (isinstance(op, ast.Constant)
                            and isinstance(op.value, (int, float))), (
                    f"{name}() 的判决里出现了数值字面量 "
                    f"{getattr(op, 'value', None)!r} —— 门槛必须来自 import，"
                    f"不许手打（第 175 层：一个好动机把对的公式改成错的）")


def test_years_is_still_reported_even_though_it_cannot_decide():
    """「years 只作报告项」这件事本身也要有守卫。

    否则将来有人嫌它没用就删掉，报告里就看不出 4 个窗口离一年有多远。
    """
    cal = M.caliber(4)
    assert cal["years_report_only"] == pytest.approx(4 / M.TRADING_DAYS_PER_YEAR)
    assert cal["n_windows"] == 4
    assert "years" in cal["years_is_NOT_a_gate"]


def test_the_year_count_is_a_fixed_external_constant_not_a_tunable():
    """244 是外部锚（一年 A 股交易日），**不许变成可调参数**。

    🚨 它**只是**报告项的分母，但它一旦变成参数，
       下一个人就会以为「调小 years 就能让判决开判」——
       而那正是预注册 §4 论证要排除的修法（244 永久压过 6）。
    """
    assert M.TRADING_DAYS_PER_YEAR == 244
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # ⚠️ 第一版只认 `ast.AnnAssign`，而源码写的是裸 `ast.Assign`
    #    ⇒ 0 命中 ⇒ 断言红，但**红的不是它声称在守的那件事**
    #    （CLAUDE.md 记过同一条的另一向：`DEFAULT_GENERATORS` 是 AnnAssign，
    #      第一版只认 Assign ⇒ StopIteration，「红的原因不对」）。
    #    ⇒ 两种赋值形态都收；关键是**收不到就必须报错，不许静默通过**
    #    （`next(..., None)` + 找不到就 return = 一条自己失效了都不知道的守卫）。
    def _target_id(node):
        t = node.target if isinstance(node, ast.AnnAssign) else None
        return getattr(t, "id", None) if t is not None else \
            getattr(getattr(node, "targets", [None])[0], "id", None)

    assigns = [n for n in ast.walk(tree)
               if isinstance(n, (ast.Assign, ast.AnnAssign))
               and _target_id(n) == "TRADING_DAYS_PER_YEAR"]
    assert len(assigns) == 1, (
        f"TRADING_DAYS_PER_YEAR 必须恰好一个定义点，实得 {len(assigns)} 个"
        "（0 个 = 本条守卫自己失效了，不是实现变好了）")
    assert isinstance(assigns[0].value, ast.Constant), "必须是字面量，不许由别的量推出来"


def test_the_years_claim_in_caliber_survives_its_own_hypothetical_mutation():
    """**正控**：把 `years_is_NOT_a_gate` 的解释删掉，上一条就抓不到它了。

    ⇒ 这条断言证明的是「产物里那句解释**存在**」，
    而上面那条证明的是「它**不进**判决分支」——两件事，两个方向。
    """
    cal = M.caliber(4)
    text = cal["years_is_NOT_a_gate"]
    # 它必须点出「244 会永久压过 6」这个**机制**，
    # 而不只是复述「years 不参与判决」——
    # 后者即使把 244 换成 60 也照样成立（第 179 层：键名与内容互相打脸）。
    assert "244" in text and "6" in text and "POWERLESS" in text


# ── ③ 观测单位是 entry_date ─────────────────────────────────────────

def test_the_observation_unit_is_entry_date_not_pred_date():
    """3 个 `pred_date` 落到同一个 `entry_date` ⇒ **1 个**窗口。

    ⚠️ 这不是排版细节：按 `pred_date` 切会让同一批单子被数成 3 次独立观测
    ⇒ 样本量凭空 ×3 ⇒ `MIN_BLOCKS` 提前满足（第 7 层）。

    🚨 夹具必须有 **≥ 2×MIN_HALF = 4 行**，否则排序力臂按设计整窗跳过
       ⇒ `ranking_spread_per_window` 返回 `[]` ⇒ 本条的**排序力那一半**
       断言一个**按设计就不该产出**的东西 ⇒ 红得毫无意义（红的原因不对）。
    """
    frame = _frame(
        [(-4.0, -0.02), (-3.0, -0.01), (-2.0, +0.01), (-1.0, +0.04)],
        pred_dates=["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-11"],
        entry_dates=["2026-10-12"] * 4,
    )
    blocks = M.excess_blocks(frame)
    assert len(blocks) == 1, f"期望 1 个成交窗口，实得 {len(blocks)}"
    assert blocks.iloc[0]["n_legs"] == 4, (
        "窗口里的行数必须是 4 —— 若按 pred_date 切会变成 2 个窗口、"
        "每窗 2 行，而 `MIN_BASKET` 过滤会把它整个丢掉")
    assert len(M.ranking_spread_per_window(frame)) == 1


# ── ④ 判据必须是**同一个对象**，不是复制品（形状 17） ───────────────

def test_the_judgement_functions_are_the_imported_ones():
    """`stats_of` / `verdict_of` 必须**就是** `forward_d2_verdict` 里那一个。

    ⚠️ 如果哪天为了「方便」把实现拷进本文件，两份副本之间**没有守卫**
    （`code-a-guard-cannot-import-is-effectively-unguarded` 的同族）。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import forward_d2_verdict as src

    assert M.stats_of is src.stats_of
    assert M.verdict_of is src.verdict_of
    assert M.MIN_BLOCKS_FOR_ANY_SIGN is src.MIN_BLOCKS_FOR_ANY_SIGN
    assert M.MIN_BLOCKS_FOR_ANY_SIGN == 6


def test_the_module_does_not_redefine_the_shared_statistics():
    """🚨 本文件**不许有** `stats_of` / `verdict_of` 的第二份实现。

    钉法：模块字典里的那个必须 `__module__` 指向源模块
    ⇒ 有人在本文件里 `def stats_of(...)` 时，`M.stats_of is src.stats_of` 那条会红，
    而**这一条**红得更早、并直接点名「你在本文件又写了一份」。
    """
    assert M.stats_of.__module__ == "forward_d2_verdict"
    assert M.verdict_of.__module__ == "forward_d2_verdict"


def test_judge_delegates_instead_of_reimplementing():
    """`judge()` 必须**委托**而不是自己判。

    钉法：给一个「与 `verdict_of` 结论相反」的输入，`judge` 仍必须给出
    `verdict_of` 的答案 ⇒ 若哪天有人把四档分支抄进本文件，两边会分家。
    """
    # n=100（远超 6）、point 巨大、CI 远离 0 ⇒ 委托者必得 POSITIVE
    big = {"n": 100, "point": 0.5, "mde": 0.001, "lo": 0.4, "hi": 0.6}
    assert M.judge(big) == "POSITIVE"
    # n=1（远低于 6）、其余同上 ⇒ POWERLESS 必须**先于**其余三档被判断
    tiny = dict(big, n=1)
    assert M.judge(tiny) == "POWERLESS"


# ── ⑤ 缺列必须响亮拒绝，不许静默回落 ────────────────────────────────

def test_missing_score_is_refused_not_replaced_by_row_order():
    """没有 `score` 就**不许**回落成「按原行序切一半」。

    🚨 那种回落会把「测真排名次」伪装成「测排名次」——
    产物照常出数、判决照常发，而它量的根本不是生产用的那个量。
    """
    frame = _frame([(-3.0, -0.01), (-1.0, +0.02)]).drop(columns=["score"])
    with pytest.raises(KeyError, match="score"):
        M.down_leg_rows(frame)


def test_rows_without_a_measurement_are_dropped_not_scored():
    """`gross_pct` / `entry_date` 为 NaN 的行必须被剔掉。

    ⚠️ 判据是**量在不在**，不是 `result` 字段写了什么
    （第 79 层：两种缺失同形，靠字段名区分会漏）。
    """
    frame = _frame([(-3.0, -0.01), (-2.0, -0.02), (-1.0, +0.03)])
    frame.loc[1, "gross_pct"] = np.nan
    frame.loc[2, "entry_date"] = np.nan
    leg = M.down_leg_rows(frame)
    assert len(leg) == 1
    assert leg.iloc[0]["score"] == -3.0


def test_nan_market_pct_does_not_poison_a_whole_window():
    """⚠️ `market_pct` 是基准抽样，没跑成的那天是 NaN。

    **不剔** ⇒ 一个窗口里一行 NaN 就让整窗均值变 NaN
    ⇒ `NaN != 0` 为真 ⇒ 「测不出」与「算坏了」同形（第 9 种形状的变体）。
    """
    frame = _frame([(-3.0, -0.01), (-2.0, -0.02), (-1.0, +0.03), (0.0, +0.04)])
    frame.loc[0, "market_pct"] = np.nan
    blocks = M.excess_blocks(frame)
    assert len(blocks) == 1
    assert np.isfinite(blocks.iloc[0]["excess_mean"]), (
        "一个 NaN 基准不许污染整个成交窗口的均值")


def test_a_window_with_no_finite_excess_produces_no_number_at_all():
    """🚨 变异 D6 的存活逼出来的：整窗的 `excess` **全是** NaN 时必须**返回空表**。

    ⚠️ 为什么上面那条守不住 `np.isfinite` 那一行：
       `groupby().agg(mean)` **pandas 默认 `skipna=True`** ⇒
       「部分 NaN 不污染均值」是 **pandas 的默认行为**在守，**不是我们的过滤在守**
       ⇒ 把 `work = work[np.isfinite(work["excess"])]` 换成任何不过滤的写法，
       `excess_mean` 仍**逐位相同**（实测 0.016666666666666666 两边一致）。

    ⇒ 而那一行**真正**承重的是**极端分支**：全 NaN ⇒ 剔光 ⇒ 空表 ⇒ 拒绝出判决。
       删掉它，这一支会给出一行 `NaN` 均值，而 `NaN != 0` 为真 ⇒
       「测不出」与「算坏了」同形（第 9 种形状的变体）。

    🚨 本条**刻意用全 NaN 而不是部分 NaN**：夹具形状不对的话，红得不是
       它声称在守的那件事（第 172 层）。
    """
    frame = _frame([(-3.0, -0.01), (-2.0, -0.02), (-1.0, +0.03), (0.0, +0.04)])
    frame["market_pct"] = np.nan          # 基准抽样整天没跑成
    blocks = M.excess_blocks(frame)
    assert len(blocks) == 0, (
        "整窗没有任何可判定的 excess 时必须返回**空表**，"
        f"实得 {len(blocks)} 行 —— 有一行就说明剔 NaN 的那一步被摘掉了，"
        "而这一行会是 NaN 均值，它进 stats_of 之后与「测不出」不可区分")

    # 而空表必须一路变成「没有统计量」，不许拿一个数凑出判决。
    # ⚠️ **拒绝落盘在 `main()`，不在 `measure()`** —— `measure` 只负责算，
    #    所以这里断言的是 `stats is None` 而不是它抛异常。
    #    （写成 `pytest.raises(SystemExit)` 会红，而红的原因是**我断言错了层**，
    #      不是实现有洞 —— 第 172 层「红的原因不对」。）
    out = M.measure(frame)
    assert out["excess_arm"]["stats"] is None, (
        "超额臂没有任何成交窗口时必须交出 None，"
        f"实得 {out['excess_arm']['stats']!r} —— "
        "一个数都不该有，因为整窗的量一个都不存在")


# ── ⑥ 空样本必须响亮拒绝，不许落一个 None 判决 ──────────────────────

def test_the_module_refuses_a_verdict_when_nothing_is_measurable():
    """没有能给出排序力的窗口 ⇒ 抛错，**不是**落一个 `None` 判决。

    🚨 判据不可满足与「没跑出来」在终端上长得一模一样（第 121 层）。
    """
    # 每窗只有 1 行 ⇒ 不足 2*MIN_HALF ⇒ 排序力臂无可用窗口
    with pytest.raises(SystemExit, match="排序力"):
        M.measure(_frame([(-3.0, -0.01)], entry_dates=["2026-10-12"]))
