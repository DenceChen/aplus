"""`scripts/measure_deployed_d2.py` 的守卫 —— 预注册 `docs/PREREG_DEPLOYED_D2_2026-10-20.md`。

## 这些测试在守什么

**不是**「跑通了」——那是形状守卫在恒真时的长相。
每一条都钉一个**外部锚**或**接线条**：

| 守卫 | 钉住的东西 |
|---|---|
| 整手 200/100 | 科创板起买 200 股是**外部规则** ⇒ 只能钉「代码里写的是 200」，钉不住「交易所真的这么规定」 |
| 窗口钉死 | `--end` 留空 ⇒ 拒绝（不是回落 `files[0]`，形状 23） |
| 数据根 | 默认值必须落在仓内 `data/`（不许指 `/tmp` 快照） |
| 账户引擎 | **成本只扣一次**（会计恒等式，只靠减法）、**整手取整后有余钱**、**逐日盯市** |
| 判决 | 必须**复用** `measure_basket_nav_01.py` 的 `judge()`，不许本地重写 |
| 盖章 | `expect_bytes` 必须是 `main()` 的**第一条可执行语句**（第 125 层） |
| 登记 | 生成器必须在 `aplus/provenance.py::DEFAULT_GENERATORS` 里（不登记 ⇒ 不可引用） |

🚨 **本文件不许写生产产物路径**（第 132 层）：一切输出走 `tmp_path`。
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "measure_deployed_d2.py"


def _load():
    spec = importlib.util.spec_from_file_location("_deployed_d2_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def M():
    if not SCRIPT.exists():
        pytest.fail(f"生成器不存在：{SCRIPT}")
    return _load()


# ── 整手（外部规则，钉的是代码里的常量与判定逻辑）──────────────────

def test_lot_shares_is_200_for_the_star_market_and_100_for_the_rest(M):
    assert M.lot_shares("688498.SH") == 200
    assert M.lot_shares("689009.SH") == 200
    assert M.lot_shares("600000.SH") == 100
    assert M.lot_shares("000001.SZ") == 100
    assert M.lot_shares("300750.SZ") == 100
    assert M.lot_shares("") == 100


def test_a_star_market_name_at_1010_cannot_afford_a_5000_slot(M):
    """688498 收盘 1010.35 元 ⇒ 1 手（200 股）= ¥202,070 ⇒ ¥5,000 买不到。

    ⚠️ 这是**整手买不起**，**不是**「买不进」（一字板/停牌/涨停收盘）——
    两者在仓内是不同机制，本守卫只钉前者。
    """
    assert not M.affordable_at(1010.35, "688498.SH", 5_000.0)
    assert not M.affordable_at(1010.35, "688498.SH", 10_000.0)


def test_a_main_board_name_at_20_can_afford_a_5000_slot(M):
    assert M.affordable_at(20.0, "600000.SH", 5_000.0)      # 1 手 = ¥2,000
    assert not M.affordable_at(60.0, "600000.SH", 5_000.0)  # 1 手 = ¥6,000 ⇒ 买不起


def test_a_missing_or_invalid_price_is_never_affordable(M):
    """信号缺失不默认放行（与 `aplus/panel.py::can_buy` 同纪律）。"""
    assert not M.affordable_at(None, "600000.SH", 5_000.0)
    assert not M.affordable_at(float("nan"), "600000.SH", 5_000.0)
    assert not M.affordable_at(0.0, "600000.SH", 5_000.0)
    assert not M.affordable_at(-1.0, "600000.SH", 5_000.0)


# ── 窗口与数据根（形状 23 + 快照漂移）────────────────────────────

def test_the_window_is_pinned_by_default(M):
    args = M.parse_args([])
    assert args.start == "2021-01-01"
    assert args.end == "2026-10-12"


def test_an_empty_end_is_rejected_rather_than_falling_back_to_files_0(M):
    """留空 ⇒ 拒绝执行（rc=2），**不许**回落 `files[0]`（形状 23）。"""
    args = M.parse_args(["--end", "  "])
    with pytest.raises(SystemExit) as ei:
        M.validate_args(args)
    assert ei.value.code == 2


def test_the_data_root_default_is_inside_the_repository(M):
    args = M.parse_args([])
    root = Path(args.data_root).resolve()
    assert root == (ROOT / "data").resolve()
    assert str(root).startswith(str(ROOT.resolve())), "数据根不许指到仓外（快照）"


def test_a_data_root_outside_the_repository_is_rejected(M, tmp_path):
    args = M.parse_args(["--data-root", str(tmp_path)])
    with pytest.raises(SystemExit) as ei:
        M.validate_args(args)
    assert ei.value.code == 2


# ── 账户引擎 ──────────────────────────────────────────────────────

def _steps():
    """三天：第 1 天建仓（2 份预算，其中 1 只买不进），第 2 天涨 10%，第 3 天换仓卖出。"""
    return [
        {"date": "2021-01-04", "rebalance": True,
         "buy": [("600000.SH", 20.0), ("688498.SH", 1010.35)],
         "returns": {}},
        {"date": "2021-01-05", "rebalance": False,
         "buy": [],
         "returns": {"600000.SH": 0.10}},
        {"date": "2021-01-06", "rebalance": True,
         "buy": [],
         "returns": {}},
    ]


def _run(M, **kw):
    base = dict(capital=50_000.0, equity_weight=1.0, n_slots=2,
                slot_budget=25_000.0, lot_sizes={"600000.SH": 100, "688498.SH": 200},
                cash_annual_return=0.0)
    base.update(kw)
    return M.simulate_account(_steps(), **base)


def test_the_account_engine_leaves_idle_cash_when_a_lot_does_not_fit(M):
    out = _run(M)
    from aplus.costs import FeeSchedule
    fee = FeeSchedule()
    # 600000 @ 20 ⇒ 1 手 = ¥2,000；每份 25,000 ⇒ 12 手 = ¥24,000
    # 688498 @ 1010.35 ⇒ 1 手 = ¥202,070 ⇒ 一份也买不起 ⇒ 钱留现金
    assert out["n_bought"] == 1
    assert out["n_unaffordable"] == 1
    assert out["invested_at_rebalances"][0] == pytest.approx(24_000.0, rel=1e-9)
    # 每份预算**含它自己的买入费** ⇒ 实投 + 买入费 ≤ 每份预算
    assert (out["invested_at_rebalances"][0] + fee.buy(24_000.0)
            <= 25_000.0 + 1e-9)
    assert out["idle_at_rebalances"][0] == pytest.approx(
        50_000.0 - 24_000.0 - fee.buy(24_000.0), rel=1e-9)


def test_the_account_engine_ignores_a_name_it_cannot_afford(M):
    out = _run(M, slot_budget=5_000.0, n_slots=1, equity_weight=0.1)
    # 600000 @ 20 ⇒ 1 手 = ¥2,000（预算 5,000 只够 2 手 = ¥4,000）
    assert out["n_bought"] == 1
    assert out["n_unaffordable"] == 1
    assert out["invested_at_rebalances"][0] == pytest.approx(4_000.0, rel=1e-9)


def test_the_account_engine_marks_to_market_every_day(M):
    out = _run(M)
    from aplus.costs import FeeSchedule
    fee = FeeSchedule()
    buy_fee = fee.buy(24_000.0)
    assert out["dates"] == ["2021-01-04", "2021-01-05", "2021-01-06"]
    # 第 1 天：买进付了费（还没有卖出）
    assert out["total_values"][0] == pytest.approx(50_000.0 - buy_fee, rel=1e-9)
    # 第 2 天：持仓 24,000 涨 10% ⇒ +2,400；现金不动
    assert out["total_values"][1] == pytest.approx(
        50_000.0 - buy_fee + 2_400.0, rel=1e-9)
    assert len(out["daily_returns"]) == 3


def test_the_account_engine_deducts_costs_exactly_once(M):
    """会计恒等式（只靠减法）：毛 − 净 ≡ 已付成本，一次不多一次不少。"""
    out = _run(M)
    from aplus.costs import FeeSchedule
    fee = FeeSchedule()
    buy_fee = fee.buy(24_000.0)
    sell_fee = fee.sell(26_400.0)
    assert out["total_fees"] == pytest.approx(buy_fee + sell_fee, rel=1e-9)
    # 毛（无费）= 50,000 + 2,400；净 = 毛 − 费
    assert out["total_values"][-1] == pytest.approx(
        52_400.0 - (buy_fee + sell_fee), rel=1e-9)


def test_the_cash_sensitivity_is_reported_and_changes_the_result(M):
    a = _run(M, cash_annual_return=0.0)
    b = _run(M, cash_annual_return=0.015)
    assert b["total_values"][-1] > a["total_values"][-1]


# ── 判决：必须复用既有公式，不许本地重写 ──────────────────────────

def test_the_verdict_is_delegated_to_the_shared_judge(M):
    """判决文本必须**逐字**来自 `measure_basket_nav_01.py::judge()`。"""
    mbn = M.load_measure_basket_nav_01()
    got = M.verdict_for(0.105563, 0.226738, "T2")
    want_cases, want_u = mbn.judge(0.105563, 0.226738, "T2")
    assert got["cases"] == want_cases
    assert got["upper_one_sided_95"] == pytest.approx(want_u)


def test_the_gap_to_target_is_signed_and_carries_the_sign_flag(M):
    got = M.verdict_for(0.105563, 0.226738, "T2")
    assert got["gap_to_target_pp"] == pytest.approx((0.20 - 0.105563) * 100.0)
    assert got["measured_sign_determinable"] is False   # |c| < M
    assert got["target"] == 0.20


def test_the_tier_statistics_reuse_the_shared_tier(M):
    mbn = M.load_measure_basket_nav_01()
    r = np.array([0.001, -0.002, 0.0005, 0.003, -0.001])
    got = M.tier_stats(r)
    want = mbn.tier("x", r)
    assert got["cagr"] == pytest.approx(want["cagr"])
    assert got["mde_as_annual_pct"] == pytest.approx(want["mde_as_annual_pct"])
    assert got["arith_ann"] == pytest.approx(want["arith_ann"])


def test_R_is_the_hurdle_module_value(M):
    from aplus.hurdle import DAYS_PER_YEAR
    assert M.R == DAYS_PER_YEAR == 244
    assert M.R != 378        # aplus.verify.TRADING_DAYS_PER_YEAR 是另一个量


# ── 盖章与登记 ────────────────────────────────────────────────────

def test_expect_bytes_is_the_first_executable_statement_of_main(M):
    """第 125 层：抓在跑之前，不是盖章前一行。"""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = fn.body
    # docstring 不算语句
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    assert body, "main() 没有语句"
    first = body[0]
    src = ast.unparse(first)
    assert "expect_bytes" in src, f"main() 第一条可执行语句不是 expect_bytes：{src}"


def test_the_generator_is_registered_in_provenance(M):
    from aplus.provenance import DEFAULT_GENERATORS
    assert DEFAULT_GENERATORS.get("deployed_d2_verdict.json") == M.GENERATOR


def test_the_production_output_path_is_the_registered_one(M):
    assert M.DEFAULT_OUT.name == "deployed_d2_verdict.json"
    assert M.DEFAULT_OUT.parent.name == "reports"


# ── 产物的形状（用 tmp_path，不许碰生产路径）──────────────────────

def test_the_artifact_can_be_written_to_a_temporary_path(M, tmp_path):
    out = tmp_path / "deployed_d2_verdict.json"
    payload = {"artifact_meta": {"window": {}}, "caliber": {}}
    M.write_artifact(out, payload)
    assert out.exists()
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["artifact_meta"]["window"] == {}
    # 生产路径**不得**被这次调用创建/改写
    prod = ROOT / "data" / "reports" / "deployed_d2_verdict.json"
    assert not prod.exists() or prod.stat().st_mtime < out.stat().st_mtime


def test_the_budgets_are_derived_from_the_configuration_not_hardcoded(M):
    """每份金额 = 本金 × **总权益敞口** ÷ 持仓数。

    实盘现役：`capital=50000`、`per_stock_cap=0.10`、**5 只** ⇒
    总权益 = 5 × 10% = **50%** ⇒ 每份 = 50000 × 0.50 / 5 = **¥5,000**。
    满仓 10 只：50000 × 1.0 / 10 = **¥5,000**（**每份相同、总敞口差一倍**）。
    """
    assert M.slot_budget_for(50_000.0, 0.50, 5) == pytest.approx(5_000.0)
    assert M.slot_budget_for(50_000.0, 1.00, 10) == pytest.approx(5_000.0)
    assert M.slot_budget_for(50_000.0, 1.00, 5) == pytest.approx(10_000.0)


def test_the_live_configuration_is_full_capital_after_the_2026_10_20_change(M):
    """实盘（2026-10-20 起）：**10 只 × 每份 ¥5,000 = ¥50,000 满仓**。

    生产真相源：`aplus/alloc.py::build_allocation(max_positions=10, per_stock_cap=0.10)`
    ⇒ 权益 100%；每份金额 = `capital × per_stock_cap` = ¥5,000。
    🚨 「5 万 / 5 只 = 每份 ¥10,000」是**另一种读法**（半仓那版），不是现在的生产行为。
    """
    assert M.CAPITAL * M.PER_STOCK_CAP == pytest.approx(5_000.0)
    assert M.LIVE_MAX_POSITIONS == 10
    assert M.LIVE_MAX_POSITIONS * 5_000.0 == pytest.approx(50_000.0)
    assert M.LIVE_MAX_POSITIONS * 5_000.0 / M.CAPITAL == pytest.approx(1.0)
    assert M.LIVE_EQUITY_WEIGHT == pytest.approx(1.0)


def test_the_rebalance_period_has_a_single_source_of_truth(M):
    """🚨 **不许**有第二份 `LIVE_REBALANCE_EVERY`。

    两份真相源是本仓反复吃亏的形状：改了生产那份、这份还停在旧值 ⇒
    「产物说的配置」与「真跑的配置」分叉，**而没有任何东西会喊**。
    ⇒ 本脚本必须 **import 生产那一份**。
    """
    from aplus.evolve import LIVE_REBALANCE_EVERY as PROD
    assert M.LIVE_REBALANCE_EVERY == PROD
    src = SCRIPT.read_text(encoding="utf-8")
    # 🚨 **用 AST 判「有没有自己赋值」，不用文本匹配** ——
    # 文本匹配会被**口径字符串**满足（本脚本的 `production_sources` 里
    # 逐字写着 "aplus/evolve.py::LIVE_REBALANCE_EVERY = 63"）⇒
    # 那是一条**恒真/误报的守卫**（第 13 种形状：子串断言 + 文档满足）。
    tree = ast.parse(src)
    assigned = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name) and t.id == "LIVE_REBALANCE_EVERY"]
    assert not assigned, f"本脚本自己赋值了 LIVE_REBALANCE_EVERY（行 {assigned}）"


def test_the_lot_size_rule_is_declared_as_an_external_rule(M):
    """科创板 200 股是**外部交易所规则**，本仓无数据源 ⇒ 产物必须自报证据等级。"""
    assert M.LOT_SHARES_STAR == 200
    assert M.LOT_SHARES_DEFAULT == 100
    assert "688" in M.STAR_MARKET_PREFIXES and "689" in M.STAR_MARKET_PREFIXES
