"""守卫：`measure_pool_board_exposure.py` 量的是**可执行性**，不是收益。

## 这份守卫存在的理由

D1 每天推 10 涨 10 跌。候选池里 **621 只科创板**（`688` 620 + `689` 1）
⇒ 涨腿里出现科创板是**结构性**的，不是偶然。
实测 `data/reports/pool_board_exposure_2026-10-21.json`：
10 天 100 个涨腿名额里 **35 个**是科创板，**1 手金额占 81.0%**。

⇒ 本脚本的**每一个数**都必须是「买不买得起」，**一个收益数都不许有**。

## 六条纪律（每一条都对应一个真的会坏的形态）

1. **板块分类必须覆盖多个前缀**，不许只测一个（只测 `688` 的探测器
   对 `000/002/300/600` 是瞎的）。
2. **板块只看前缀、不看价格** —— 仓内 `aplus/webapp.py:456-460` 逐字写着
   「不许用『价格高就 200 股』这种推断」。
3. **缺价 ⇒ 显式缺失**，不许编 0（`0` 会被读成「免费」）。
4. **A 类（物理）与 B 类（分配）不许合并** —— 两个完全不同的量（第 7 层）。
5. **能力守卫**：探测器在真实形状的数据上必须给出**非 None** 的分类；
   并且**反向正控**：未知前缀必须落到 `OTHER` 而不是悄悄算进主板。
6. **测试不许写生产产物路径**（第 132 层：`--out` 的默认值落在 `data/reports/`
   ⇒ 一次测试就把自己输出重写成合成数据 + 一枚说真话的章）。
   ⇒ 本文件**只**用 `tmp_path`，并**断言生产目录没被碰过**。

## ⚠️ 为什么没有「产物层守卫」当主判据

`data/reports/pool_board_exposure_2026-10-21.json` **存在**，但读它的守卫
**看不见当前代码**（第 96 层：产物层守卫对当次产物零覆盖）。
⇒ 本文件的主判据全部是**行为层**（跑真函数 / 跑真脚本），
产物只在**第 12 组**里被读一次，且那次读的是**本轮刚跑出来的 tmp_path 产物**。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "measure_pool_board_exposure.py"
PRED_DIR = ROOT / "data" / "reports" / "predictions"
DAILY_DIR = ROOT / "data" / "master" / "daily"

sys.path.insert(0, str(ROOT / "scripts"))
import measure_pool_board_exposure as M  # noqa: E402


# ── 夹具：一小份**形状与真产物一致**的数据 ────────────────────────────

def _write_daily(path: Path, code: str, rows: list[tuple[str, float | None]]) -> None:
    """写一只票的日线。`close=None` ⇒ NaN（**缺价**）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": [d for d, _ in rows],
        "code": [code] * len(rows),
        "close": [c if c is not None else float("nan") for _, c in rows],
    }).to_parquet(path)


def _write_pred(path: Path, pred_date: str, rows: list[tuple[str, str, str, float]]) -> None:
    """写一份 `pred_*.parquet`（列名与真产物一致）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "code": [r[0] for r in rows],
        "name": [r[1] for r in rows],
        "direction": [r[2] for r in rows],
        "score": [r[3] for r in rows],
        "pred_date": [pred_date] * len(rows),
    }).to_parquet(path)


def _mini_root(tmp_path: Path) -> tuple[Path, Path]:
    """造一个**小**数据根：6 只票 × 2 天 + 1 份推荐（up 4 / down 2）。"""
    daily = tmp_path / "master" / "daily"
    #               code        bars (date, close)
    fixtures = {
        "688498.SH": [("2026-01-01", 1010.35), ("2026-01-02", 1010.35)],   # 科创板·超贵
        "688110.SH": [("2026-01-01", 59.02), ("2026-01-02", 59.02)],     # 科创板·买得起
        "300951.SZ": [("2026-01-01", 59.20), ("2026-01-02", 59.20)],     # 创业板·差一点
        "603031.SH": [("2026-01-01", 28.36), ("2026-01-02", 28.36)],     # 沪主板·买得起
        "000978.SZ": [("2026-01-01", 6.38), ("2026-01-02", 6.38)],       # 深主板·买得起
        "002897.SZ": [("2026-01-01", None), ("2026-01-02", None)],       # 缺价（NaN）
    }
    for code, bars in fixtures.items():
        _write_daily(daily / f"{code}.parquet", code, bars)
    pred = tmp_path / "predictions"
    _write_pred(pred / "pred_2026-01-02.parquet", "2026-01-02", [
        ("688498.SH", "源杰科技", "up", 1.0),
        ("688110.SH", "东芯股份", "up", 2.0),
        ("300951.SZ", "博硕科技", "up", 3.0),
        ("603031.SH", "安孚科技", "up", 4.0),
        ("000978.SZ", "桂林旅游", "up", 5.0),
        ("002897.SZ", "意华股份", "up", 6.0),
        ("688498.SH", "源杰科技", "down", -1.0),
        ("688110.SH", "东芯股份", "down", -2.0),
    ])
    return tmp_path, pred


# ── ① 板块分类：多个前缀各一例（不许只测一个）─────────────────────────

@pytest.mark.parametrize("code,board", [
    # 科创板（688 主板 + 689 CDR）
    ("688498.SH", "STAR"),
    ("689009.SH", "STAR"),
    # 创业板
    ("300951.SZ", "CHINEXT"),
    ("301190.SZ", "CHINEXT"),
    # 沪主板（四个前缀各一例）
    ("600519.SH", "SH_MAIN"),
    ("601318.SH", "SH_MAIN"),
    ("603031.SH", "SH_MAIN"),
    ("605058.SH", "SH_MAIN"),
    # 深主板（四个前缀各一例）
    ("000978.SZ", "SZ_MAIN"),
    ("001285.SZ", "SZ_MAIN"),
    ("002897.SZ", "SZ_MAIN"),
    ("003000.SZ", "SZ_MAIN"),
    # 北交所（本仓 0 只，但分类器必须认得它 —— 否则它会悄悄落进别的板）
    ("430047.BJ", "BSE"),
    ("830799.BJ", "BSE"),
    ("870508.BJ", "BSE"),
    ("880001.BJ", "BSE"),
    ("920002.BJ", "BSE"),
])
def test_board_classification_covers_every_prefix_family(code, board):
    """每块板**每个前缀**各一例。⚠️ 只测 `688` 的探测器对其它板是瞎的。"""
    assert M.board_of(code) == board


def test_unknown_prefix_lands_in_other_and_not_in_a_real_board():
    """反向正控：认不出的票必须**显式**落到 `OTHER`。

    🚨 回落成某个默认板（尤其主板）⇒ 一个认不出的票会**静默地**算进主板分母
    ⇒ 比例看起来正常，而分子分母已经不是同一批票。
    """
    assert M.board_of("302132.SZ") == "OTHER"
    assert M.board_of("999999.SH") == "OTHER"
    assert M.board_of("") == "OTHER"
    assert M.board_of(None) == "OTHER"
    # 302 是本仓真实存在的前缀（1 只）⇒ 它必须被**点名**，不能消失
    assert M.board_of("302132.SZ") not in ("SH_MAIN", "SZ_MAIN", "CHINEXT", "STAR")


def test_board_classification_does_not_depend_on_price():
    """板块**只看前缀** —— 仓内 `aplus/webapp.py:456-460` 逐字禁止「价格高就 200 股」。

    ⇒ 一个**便宜**的科创板票仍是科创板、仍是 200 股一手。
    """
    assert M.board_of("688001.SH") == "STAR"
    assert M.lot_shares_for("688001.SH") == 200
    # 贵的创业板仍是 100 股 —— 「价格高 ⇒ 200 股」是两条口径混成一条规则
    assert M.lot_shares_for("300951.SZ") == 100
    assert M.lot_shares_for("300999.SZ") == 100


# ── ② 一手股数与金额 ───────────────────────────────────────────────

def test_lot_shares_is_200_only_for_star_prefixes():
    assert M.lot_shares_for("688498.SH") == 200
    assert M.lot_shares_for("689009.SH") == 200
    for c in ("300951.SZ", "301190.SZ", "600519.SH", "000978.SZ",
              "002897.SZ", "430047.BJ"):
        assert M.lot_shares_for(c) == 100, c


def test_lot_cost_is_price_times_lot_shares():
    assert M.lot_cost(1010.35, "688498.SH") == pytest.approx(1010.35 * 200)
    assert M.lot_cost(59.20, "300951.SZ") == pytest.approx(59.20 * 100)


# ── ③ 缺价：显式缺失，不编 0 ─────────────────────────────────────────

def test_missing_price_is_explicit_none_never_zero():
    """缺价 ⇒ `None`（不是 0、不是 inf）。

    🚨 编 0 ⇒ 「1 手 = ¥0」⇒ 被读成「**免费**」⇒ 可买率虚高。
    """
    assert M.lot_cost(None, "688498.SH") is None
    assert M.lot_cost(float("nan"), "688498.SH") is None
    assert M.lot_cost(-1.0, "688498.SH") is None
    assert M.lot_cost(0.0, "688498.SH") is None


def test_missing_price_is_its_own_state_not_a_purchase_verdict():
    """缺价 ⇒ `PRICE_MISSING`，**不归到 OK / B_SLOT / A_PHYSICAL 任何一档**。

    「判不出」≠「买得起」≠「买不了」—— 三个不同的量（第 7 层）。
    """
    assert M.classify_affordability(None) == "PRICE_MISSING"
    assert M.classify_affordability(None) not in ("OK", "B_SLOT", "A_PHYSICAL")


def test_rollup_counts_a_missing_price_and_keeps_it_out_of_the_money_sums():
    rows = [
        {"code": "603031.SH", "board": "SH_MAIN", "close": 28.36, "lot_cost": 2836.0},
        {"code": "002897.SZ", "board": "SZ_MAIN", "close": None, "lot_cost": None},
    ]
    r = M.board_rollup(rows)
    assert r["n"] == 2
    assert r["n_priced"] == 1
    assert r["n_price_missing"] == 1
    # 缺价那只**不贡献金额**，但**被点名**
    assert r["totals"]["lot_cost_sum"] == pytest.approx(2836.0)
    assert r["totals"]["budget_capped_spend"] == pytest.approx(2836.0)
    assert r["totals"]["n_afford_PRICE_MISSING"] == 1
    assert r["by_board"]["SZ_MAIN"]["n_price_missing"] == 1


# ── ④ A 类（物理）与 B 类（分配）不许合并 ──────────────────────────────

def test_physical_and_allocation_classes_are_separate_states():
    """A（物理买不进）与 B（分配不够）是**两个完全不同的量**。

    - A：1 手 > 全部本金 ⇒ 与怎么分配**无关** ⇒ 物理不可能
    - B：1 手 > 每份 ¥5,000 但 ≤ 本金 ⇒ 「得集中」，**不是买不了**
    """
    assert M.classify_affordability(202070.0) == "A_PHYSICAL"      # 688498 一手
    assert M.classify_affordability(5920.0) == "B_SLOT"             # 300951 一手
    assert M.classify_affordability(2836.0) == "OK"                 # 603031 一手
    # 恰好等于本金 ⇒ 物理**买得起**（边界是 ≤，不是 <）
    assert M.classify_affordability(50000.0) == "B_SLOT"
    assert M.classify_affordability(50000.01) == "A_PHYSICAL"
    # 恰好等于每份金额 ⇒ 照等权买得起
    assert M.classify_affordability(5000.0) == "OK"
    assert M.classify_affordability(5000.01) == "B_SLOT"


# ── ⑤ 能力守卫 + 反向正控（真实形状的数据）────────────────────────────

def test_the_classifier_gives_a_non_none_verdict_on_every_real_pool_code():
    """**能力守卫**：在**真实形状**的数据（真池 5460 只）上，
    每只票都必须得到一个**非 None** 的分类。

    🚨 一个恒返 `None` / 恒返某个默认值的探测器，
    「测不出来」与「一切正常」长得**一模一样**（第 120 层）。
    """
    codes = sorted(p.stem for p in DAILY_DIR.glob("*.parquet"))
    assert len(codes) > 5000, "真池读不到 —— 守卫变成了空转"
    verdicts = {c: M.board_of(c) for c in codes}
    assert all(v is not None for v in verdicts.values())
    assert all(v in M.BOARD_ORDER for v in verdicts.values())
    # 真值：688 前缀 620 只 + 689 前缀 1 只 = 621 只科创板
    assert sum(1 for v in verdicts.values() if v == "STAR") == 621
    # 真池里**没有**北交所 ⇒ BSE 的分类分支在真数据上不可达
    # （⇒ 它的正确性由上面的参数化夹具守，不由这条守）


def test_the_classifier_can_actually_fail_on_unseen_input():
    """反向正控：探测器**必须能看见**「认不出」这件事。

    ⇒ 一个把一切都归成主板的探测器，在上面那条能力守卫里**照样全绿**
    （全是合法字符串）⇒ 必须另有一条让它**红**的。
    """
    assert M.board_of("999999.SH") == "OTHER"
    assert M.board_of("777777.SZ") == "OTHER"
    # 且它**不许**把别的板吞掉
    for c, b in (("688498.SH", "STAR"), ("300951.SZ", "CHINEXT"),
                 ("600519.SH", "SH_MAIN"), ("000978.SZ", "SZ_MAIN")):
        assert M.board_of(c) == b


def test_every_real_prediction_code_gets_a_board_and_a_price():
    """**能力守卫（腿级）**：真名单里的每一只都必须有分类；
    有价的那些必须算出 1 手金额（**不是 None**）。

    ⇒ 「探测器在真实形状的数据上给出非 None 的分类」这一条，
    在**腿**这一层也要成立，不只是在**池**这一层。
    """
    files = sorted(PRED_DIR.glob("pred_*.parquet"))
    assert len(files) >= 5, "真预测产物读不到 —— 守卫变成了空转"
    seen = 0
    for f in files:
        df = pd.read_parquet(f)
        assert set(df["direction"].unique()) <= {"up", "down"}
        for code in df["code"]:
            b = M.board_of(code)
            assert b is not None and b in M.BOARD_ORDER
            seen += 1
    assert seen >= 100


# ── ⑥ 分母完整：没有一只被悄悄丢掉 ────────────────────────────────────

def test_rollup_accounts_for_every_row_it_is_given():
    """`n_priced + n_price_missing == n`（**每只都被点名**）。

    🚨 跌腿是**回避清单、不占资金** ⇒ 板块/整手约束**只作用于涨腿**
    ⇒ 跌腿**只报、不据此过滤** ⇒ 任何一只被静默丢掉都是缺陷。
    """
    rows = [
        {"code": "688498.SH", "board": "STAR", "close": 1010.35, "lot_cost": 202070.0},
        {"code": "300951.SZ", "board": "CHINEXT", "close": 59.20, "lot_cost": 5920.0},
        {"code": "002897.SZ", "board": "SZ_MAIN", "close": None, "lot_cost": None},
    ]
    r = M.board_rollup(rows)
    assert r["n"] == 3
    assert r["n_priced"] + r["n_price_missing"] == r["n"]
    assert r["totals"]["n_afford_OK"] + r["totals"]["n_afford_B_SLOT"] \
        + r["totals"]["n_afford_A_PHYSICAL"] + r["totals"]["n_afford_PRICE_MISSING"] == r["n"]


def test_the_two_money_calibers_are_not_the_same_quantity():
    """「1 手金额」与「每份 ¥5,000」是**两个不同的口径**（第 7 层）。

    ⇒ 它们的分板占比**不许**被当成同一个比值。
    """
    rows = [
        {"code": "688498.SH", "board": "STAR", "close": 1010.35, "lot_cost": 202070.0},
        {"code": "603031.SH", "board": "SH_MAIN", "close": 28.36, "lot_cost": 2836.0},
    ]
    r = M.board_rollup(rows)
    star = r["by_board"]["STAR"]
    main = r["by_board"]["SH_MAIN"]
    # 1 手金额口径：科创板吃掉几乎全部
    assert star["lot_cost_share"] > 0.98
    # 每份 ¥5,000 口径：科创板被**封顶**在 ¥5,000（钱掏不出去 ⇒ 闲置）
    assert star["budget_capped_share"] < 0.75
    assert r["totals"]["idle_cash"] == pytest.approx(50000.0 - (5000.0 + 2836.0))
    # 两者**不相等** ⇒ 不许只报一个
    assert star["lot_cost_share"] != star["budget_capped_share"]


def test_board_shares_partition_the_total():
    """分板占比之和 == 1（**不是**恒等式凑数：分母是真实总额）。

    ⇒ 这条同时钉住「没有一只被漏在分母外」。
    """
    rows = [
        {"code": "688498.SH", "board": "STAR", "close": 1010.35, "lot_cost": 202070.0},
        {"code": "300951.SZ", "board": "CHINEXT", "close": 59.20, "lot_cost": 5920.0},
        {"code": "603031.SH", "board": "SH_MAIN", "close": 28.36, "lot_cost": 2836.0},
    ]
    r = M.board_rollup(rows)
    s1 = sum(c["lot_cost_share"] for c in r["by_board"].values())
    s2 = sum(c["budget_capped_share"] for c in r["by_board"].values())
    assert s1 == pytest.approx(1.0)
    assert s2 == pytest.approx(1.0)


# ── ⑦ 反事实：它是区间与估计，**不是**重排，**不是**收益 ───────────────

def test_the_counterfactual_reports_a_range_and_says_it_is_not_a_reranking():
    """反事实**必须**同时给出上界与下界，**且**自带「不是重排」的自述。

    🚨 只给一个点估计 ⇒ 读者会把它当「剔池后就是 X 只可买」的事实
    ⇒ 而全池排名**没有落盘**（`scripts/run_daily.py:680` 只落 20 行）
    ⇒ 那个点估计是**猜**的。
    """
    leg = [
        {"code": "688498.SH", "board": "STAR", "afford": "A_PHYSICAL"},
        {"code": "688110.SH", "board": "STAR", "afford": "B_SLOT"},
        {"code": "300951.SZ", "board": "CHINEXT", "afford": "B_SLOT"},
        {"code": "603031.SH", "board": "SH_MAIN", "afford": "OK"},
        {"code": "000978.SZ", "board": "SZ_MAIN", "afford": "OK"},
    ]
    pool = {b: {"n_priced": 100, "n_afford_OK": 90, "n_afford_B_SLOT": 8,
                "n_afford_A_PHYSICAL": 2} for b in M.BOARD_ORDER}
    cf = M.counterfactual(leg, pool)
    for key in ("main_plus_chinext", "main_only"):
        c = cf[key]
        assert c["worst_case_n_affordable"] <= c["expected_n_affordable"] \
            <= c["best_case_n_affordable"]
        assert 0 <= c["worst_case_n_affordable"] <= c["best_case_n_affordable"] <= len(leg)
        assert "不是" in c["估计方法"] and "重排" in c["估计方法"]
        assert "偏差" in c["估计的偏差方向"]
    # 已知部分：main_plus_chinext 收 300951(创业板) + 603031(沪主板) + 000978(深主板) = 3
    #           main_only 只收 603031 + 000978 = 2（两只都 OK）
    assert cf["main_plus_chinext"]["n_current_up_in_set"] == 3
    assert cf["main_only"]["n_current_up_in_set"] == 2
    assert cf["main_only"]["n_current_up_affordable_OK"] == 2


# ── ⑧ 脚本不许算收益（AST，不靠子串）────────────────────────────────

def test_the_script_computes_no_return_quantity():
    """脚本**不许** import 回测/成本/验证模块 —— 它只量可执行性。

    ⇒ 反事实那两个数**是执行性读数**，一旦脚本自己开始算收益，
    「不重算收益」这句话就成了假话。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported.add(mod)
            for a in node.names:
                imported.add(f"{mod}.{a.name}")
    forbidden = ("backtest", "costs", "verify", "composite", "hurdle", "portfolio")
    for name in imported:
        assert not any(f == name or name.endswith("." + f) or f".{f}." in name
                       for f in forbidden), f"脚本 import 了 {name}"


def test_the_script_contains_no_annualization_formula():
    """脚本里不许出现年化/CAGR 之类的**代码**（注释与 docstring 不算）。

    ⚠️ 用 AST 读**代码**而不是全文 grep —— 全文 grep 会被 docstring 满足
    （第 13 种形状：子串断言 + 同一文件里存在注释 ⇒ 守卫被文档满足）。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    banned = ("cagr", "annual_return", "annualized", "sqrt(244", "x244", "* 244")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            continue          # 字符串字面量（docstring、提示语）不算「代码」
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
    # 用「AST 里出现的一元/二元运算常数 244」与「叫 cagr 的标识符」两条来判
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (names | attrs) & set(banned), "脚本里出现了年化口径的标识符"
    consts = {n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))}
    assert 244 not in consts, "脚本里出现了 244（年化乘数）—— 它不许算收益"


# ── ⑨ `expect_bytes` 必须在第一条（第 125 层）──────────────────────────

def test_expect_bytes_is_captured_as_the_first_executable_statement_of_main():
    """盖章要的是「这次跑真正跑的那些字节」⇒ `expect_bytes` 必须在**第一条**取。

    ⚠️ 放在 `argparse` 之后不算错，但放在 `_run()` 里就是第 131 层那枚雷
    （`NameError` 炸在盖章那一行，而产物已经落盘）。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main_fn = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = main_fn.body
    # 第一条**可执行**语句（docstring 不算）
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str):
        first = body[1]
    assert isinstance(first, ast.Assign), f"main() 第一条可执行语句不是赋值：{ast.dump(first)}"
    tgt = first.targets[0]
    assert isinstance(tgt, ast.Name) and tgt.id == "expect_bytes", \
        f"main() 第一条可执行语句不是 expect_bytes，而是 {getattr(tgt, 'id', tgt)}"
    src = ast.unparse(first)
    assert "code_file_sha256" in src and "GENERATOR" in src, src
    # 第二实参必须是**模块级 GENERATOR 常量**，不是手打的路径
    # （手打路径会与脚本搬家脱钩 ⇒ 章不具可搬迁性）
    assert M.GENERATOR == "scripts/measure_pool_board_exposure.py"
    assert (ROOT / M.GENERATOR).is_file()


# ── ⑩ 脚本会盖章（`stamp` 接线）──────────────────────────────────────

def test_the_script_stamps_its_artifact_with_expect_bytes():
    """产物必须带 sidecar，且**带 `expect_bytes`**（第 125 层）。

    ⇒ 走 AST 找 `stamp()` 调用点 + 判**实参**，不用子串
    （子串会被本文件自己的注释满足）。
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "stamp":
                calls.append(node)
    assert calls, "没有 provenance.stamp(...) 调用点"
    assert any(any(kw.arg == "expect_bytes" for kw in c.keywords) for c in calls), \
        "stamp() 没有传 expect_bytes"


# ── ⑪ 端到端：跑真脚本，产物落在 tmp_path，生产目录不许被碰 ───────────

def test_running_the_script_writes_only_into_tmp_path(tmp_path):
    """**端到端**：跑真脚本 ⇒ 产物与 sidecar 都落在 `tmp_path`，
    且 **`data/reports/` 下的文件一个字节都没变**。

    🚨 第 132 层：脚本的 `--out` 默认值落在生产路径上，
    而测试用合成数据跑它 ⇒ **一次测试就把生产产物重写成合成数据 + 一枚真章**
    ⇒ 后面任何复核都「确认」了一份合成产物。
    ⇒ 这条守卫**当场**钉住「不许写生产路径」。
    """
    data_root, pred_dir = _mini_root(tmp_path)
    out = tmp_path / "reports" / "mini.json"
    before = {p: p.read_bytes() for p in (ROOT / "data" / "reports").glob("*.json")}

    rc = M.main(["--data-root", str(data_root), "--pred-dir", str(pred_dir),
                 "--out", str(out)])
    assert rc == 0
    assert out.is_file()
    assert (out.with_suffix(".json.provenance.json")).is_file() or \
        (tmp_path / "reports" / "mini.json.provenance.json").is_file()

    after = {p: p.read_bytes() for p in (ROOT / "data" / "reports").glob("*.json")}
    changed = [p.name for p in before if p in after and before[p] != after[p]]
    assert not changed, f"生产产物被改写了：{changed}"
    assert set(before) == set(after), "生产产物目录的文件集合变了"


def test_the_end_to_end_artifact_sees_the_fixture_as_written(tmp_path):
    """端到端读数：合成夹具 ⇒ **已知答案**。

    涨腿 6 只：688498(¥202,070 A 物理) / 688110(¥11,804 B) / 300951(¥5,920 B)
    / 603031(¥2,836 OK) / 000978(¥638 OK) / 002897(**缺价**)。
    ⇒ OK=2、B=2、A=1、缺价=1。
    """
    data_root, pred_dir = _mini_root(tmp_path)
    out = tmp_path / "reports" / "mini.json"
    assert M.main(["--data-root", str(data_root), "--pred-dir", str(pred_dir),
                    "--out", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    day = d["days"][0]
    t = day["up"]["totals"]
    assert t["n_afford_OK"] == 2
    assert t["n_afford_B_SLOT"] == 2
    assert t["n_afford_A_PHYSICAL"] == 1
    assert t["n_afford_PRICE_MISSING"] == 1
    assert day["up"]["by_board"]["STAR"]["n"] == 2
    assert day["up"]["by_board"]["CHINEXT"]["n"] == 1
    # 跌腿**只报**：8 行里 up 6 / down 2 ⇒ 跌腿的 n 必须是 2（一只不许被丢）
    assert day["down"]["n"] == 2
    assert d["caliber"]["down_leg_scope"].startswith("**跌腿只报")


def test_the_end_to_end_artifact_declares_what_it_cannot_prove(tmp_path):
    """产物必须自带「不能证明什么」—— 尤其**不许**把可买性说成涨跌。"""
    data_root, pred_dir = _mini_root(tmp_path)
    out = tmp_path / "reports" / "mini.json"
    assert M.main(["--data-root", str(data_root), "--pred-dir", str(pred_dir),
                    "--out", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    cannot = "\n".join(d["what_this_cannot_prove"])
    assert "涨不涨" in cannot
    assert "两个完全不同的量" in cannot
    assert "权限" in cannot
    # 权限口径块必须**声明本产物一个字都不判定权限**
    assert "一个字都不判定权限" in d["caliber"]["permission"]
