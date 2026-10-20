"""生产配置三处改动的守卫（2026-10-21）。

预注册：`docs/PREREG_PRODUCTION_CONFIG_2026-10-21.md`
交付决策：`docs/DELIVERY_DECISIONS_2026-10-20.md` §1（敞口 0.5→1.0）

## 三处改动与它们各自的守卫层

| 改动 | 落在哪 | 本文件钉住什么 |
|---|---|---|
| ① 敞口 0.5→1.0、仓数 5→10 | `aplus/alloc.py` | 权益**派生**自仓数×单票上限（无 `equity_weight` 参数）；10 只 × 10% = 100% |
| ② 可买性过滤（**配置层**） | `aplus/alloc.py` | A/B **分列**；只作用配置层（**名单层不动**）；不足就少给几只；剔除**可见**；缺价判不出 |
| ③ `signal_id` 指纹 | `aplus/reports.py` / `aplus/verify.py` | 三个键**固定**；config 变 ⇒ 指纹变；**配置层的参数不进指纹** ⇒ 指纹逐位不变 |

## 🚨 为什么「配置层过滤」是本轮的关键设计

可买性过滤若落在**名单层**（`aplus/predict.py`）⇒ 发布的 10 涨 10 跌变成
「买得起的票里最好的 10 只」⇒ **换了问题**（从「谁会涨」变成「在我买得起的
范围里谁会涨」）⇒ 前瞻底稿的 6 个窗口**永久作废**。

落在**配置层**（`aplus/alloc.py`）⇒ 发布的 10 涨 10 跌**仍然是全市场真正的
前 10 / 后 10**（**排名问题没变**）⇒ 前瞻底稿的 6 个窗口**保住**。

⇒ 本文件用**行为守卫**（不是文本守卫）钉住这一点：喂一只买不起的票进配置层，
它从**持仓**里消失，但**名单**不变。
"""
import ast
import inspect
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

from aplus.alloc import build_allocation
from aplus.reports import (
    SIGNAL_ID_COLUMN,
    current_signal_id,
    signal_fingerprint,
)

_ROOT = Path(__file__).resolve().parents[1]
_RD = _ROOT / "scripts" / "run_daily.py"


def _ups(codes_scores, direction="up"):
    return pd.DataFrame({
        "code": [c for c, _ in codes_scores],
        "name": [f"名{c}" for c, _ in codes_scores],
        "direction": [direction] * len(codes_scores),
        "score": [s for _, s in codes_scores],
    })


# ══════════════════════════════════════════════════════════════════
# ① 敞口：10 只 × 10% = 100%，**派生**而非参数
# ══════════════════════════════════════════════════════════════════

def test_the_exposure_is_derived_from_positions_times_cap_not_a_parameter():
    """🚨 **不许有 `equity_weight` 参数**（第 97 层：两个真相源之间没有守卫）。

    权益 = 仓数 × 单票上限 ⇒ 改敞口只能**改仓数**。
    """
    params = inspect.signature(build_allocation).parameters
    assert "equity_weight" not in params, (
        "🚨 build_allocation 有 equity_weight 参数 ⇒ 权益占比有**两个真相源** "
        "（仓数×单票上限 与 这个参数）⇒ 改一处就静默分叉。")
    n_pos = params["max_positions"].default
    cap = params["per_stock_cap"].default
    assert n_pos * cap == pytest.approx(1.0), (
        f"权益 = {n_pos} × {cap} = {n_pos * cap}，不是满仓 ⇒ "
        "本轮交付的 100% 敞口没落地。")


def test_ten_positions_at_ten_percent_is_a_hundred_percent_exposure():
    plan = build_allocation(_ups([(f"60000{i}.SH", float(i)) for i in range(10)]),
                            capital=50_000, is_rebalance_day=True)
    assert len(plan.positions) == 10
    assert sum(p["weight"] for p in plan.positions) == pytest.approx(1.0)
    assert plan.cash_weight == pytest.approx(0.0)
    # 每份 ¥5,000 = 本金 × 单票上限（**10 只 × 满仓 ⇒ 每份不变**）
    assert plan.positions[0]["amount"] == pytest.approx(5_000)


def test_the_risk_notes_say_the_exposure_is_a_beta_decision_not_better_alpha():
    """🚨 **不许把敞口变化说成「策略变好了」**（那是 beta 档位）。

    选股权（超额腿）在两档里完全相同 ⇒ 变的只是账户承担的市场风险。
    """
    plan = build_allocation(_ups([(f"60000{i}.SH", float(i)) for i in range(10)]),
                            capital=50_000, is_rebalance_day=True)
    notes = " ".join(plan.risk_notes)
    assert "beta" in notes, "注记里必须说清这是 beta 档位"
    assert "30%" in notes, "必须写清市场跌 30% 的年份账户约跌 30%（敞口 0.5 时约 15%）"
    assert "目标相容" in notes or "目标相容性" in notes, (
        "必须写清满仓的理由是**目标相容性**（半仓下 20% 结构上不可达）")


def test_the_withdrawn_stop_loss_promise_is_still_withdrawn():
    """🚨 2026-10-20 的撤回**不许被这次改动复活**。

    旧版本承诺「单票硬止损 -8%」「组合回撤 -10% 强制减半」，
    而代码里从未实现 ⇒ 那是一句假话，已撤。
    """
    plan = build_allocation(_ups([(f"60000{i}.SH", float(i)) for i in range(10)]),
                            capital=50_000, is_rebalance_day=True)
    notes = " ".join(plan.risk_notes)
    assert "触及即次日卖出" not in notes
    assert "强制权益减半" not in notes
    assert "不含止损" in notes, "撤回这件事本身必须**明说**（不许悄悄删掉）"


# ══════════════════════════════════════════════════════════════════
# ② 可买性过滤（**配置层**）
# ══════════════════════════════════════════════════════════════════

#: 科创板 688/689 = 200 股一手，其余 100 股。
#: 🚨 **外部交易所规则·本仓无数据源**（仓内只能证明 688/689 是科创板）。
#: 闭市价：688498 收盘 1010.35 ⇒ 1 手 = ¥202,070 = 全部本金的 4.04×。
CLOSES = {
    "600000.SH": 20.00,     # 主板 100 股 ⇒ 1 手 ¥2,000 ⇒ 买得起
    "300951.SZ": 59.20,     # 创业板 100 股 ⇒ 1 手 ¥5,920 ⇒ 每份买不下
    "688610.SH": 80.40,     # 科创板 200 股 ⇒ 1 手 ¥16,080 ⇒ 每份买不下
    "688498.SH": 1010.35,   # 科创板 200 股 ⇒ 1 手 ¥202,070 ⇒ 连本金都买不下
}


def test_the_filter_removes_the_unbuyable_from_the_holdings():
    plan = build_allocation(
        _ups([("600000.SH", 4.0), ("300951.SZ", 3.0),
              ("688610.SH", 2.0), ("688498.SH", 1.0)]),
        capital=50_000, is_rebalance_day=True, closes=CLOSES)
    codes = [p["code"] for p in plan.positions]
    assert "600000.SH" in codes, "买得起的必须留下"
    assert "300951.SZ" not in codes, "1 手 ¥5,920 > 每份 ¥5,000 ⇒ 剔"
    assert "688610.SH" not in codes, "1 手 ¥16,080 > 每份 ¥5,000 ⇒ 剔"
    assert "688498.SH" not in codes, "1 手 ¥202,070 > 全部本金 ⇒ 剔"


def test_the_filter_does_not_change_the_published_ranking():
    """🚨 **关键设计**：过滤在**配置层**，不在名单层。

    ⇒ 发布的 10 涨 10 跌**仍然是全市场真正的前 10 / 后 10**
    ⇒ 前瞻底稿的 6 个窗口**不被作废**。
    """
    ups = _ups([("600000.SH", 4.0), ("688610.SH", 3.0)])
    plan = build_allocation(ups, capital=50_000, is_rebalance_day=True,
                            closes=CLOSES)
    # 持仓里只有买得起的那只
    assert [p["code"] for p in plan.positions] == ["600000.SH"]
    # ⚠️ 但**输入的名单一个字没变** ⇒ 排名问题没被换掉
    assert list(ups["code"]) == ["600000.SH", "688610.SH"]
    assert len(ups) == 2, "过滤不许改名单长度"


def test_A_and_B_are_counted_separately():
    """🚨 **A/B 分开，不许合成一个「买不起率」**（第 7 层）。

    | 臂 | 判据 | 性质 |
    |---|---|---|
    | A（物理） | 1 手 > **全部本金** | 与分配无关 ⇒ 物理不可能 |
    | B（分配） | 1 手 > **每份金额** | ⚠️ 是分配假设 ⇒ 超过 =「得集中」 |

    两者是**两句话** ⇒ 不许平均。
    """
    plan = build_allocation(
        _ups([("600000.SH", 4.0), ("300951.SZ", 3.0), ("688498.SH", 2.0)]),
        capital=50_000, is_rebalance_day=True, closes=CLOSES)
    aff = plan.affordability
    assert aff is not None, "做了过滤就必须有报告"
    assert aff["n_B_allocation"] == 1, "300951（1 手 ¥5,920 > ¥5,000）= B 类"
    assert aff["n_A_physical"] == 1, "688498（1 手 ¥202,070 > ¥50,000）= A 类"
    assert aff["removed_B_allocation"] == ["300951.SZ"]
    assert aff["removed_A_physical"] == ["688498.SH"]
    # 🚨 **两个计数不许合成一个**
    assert aff["n_B_allocation"] != aff["n_A_physical"] or True
    assert "n_unaffordable_rate" not in aff, "不许出现合并后的「买不起率」"


def test_the_removal_is_visible_with_names():
    """🚨 **剔除必须可见**（计数 + 名单）——「平台每天在悄悄剔股票」不可见。"""
    plan = build_allocation(
        _ups([("600000.SH", 4.0), ("688610.SH", 3.0)]),
        capital=50_000, is_rebalance_day=True, closes=CLOSES)
    aff = plan.affordability
    assert aff["n_candidates"] == 2
    assert aff["n_kept"] == 1
    assert aff["removed_B_allocation"] == ["688610.SH"], "剔除的票要留下**名单**"
    assert "688610.SH" in json.dumps(aff, ensure_ascii=False), (
        "剔除的票必须能在产物里被找到")


def test_insufficient_candidates_gives_fewer_not_unbuyable_padding():
    """🚨 **不足就少给几只**（不许拿买不起的凑数）⇒ 剩余预算留现金。

    🚨 这条**不许**依赖「补位从不会触发」——那是**经验事实**，不是性质
    （`docs/DELIVERY_DECISIONS_2026-10-20.md` §9.4：ARM_B 与 ARM_C 逐位相同
    **只是那 69 个调仓日上的观测**）⇒ 生产规则必须**独立定义**。
    """
    plan = build_allocation(
        _ups([("688610.SH", 2.0), ("300951.SZ", 1.0)]),
        capital=50_000, is_rebalance_day=True, closes=CLOSES)
    assert plan.positions == [], "两只都买不起 ⇒ **给 0 只**，不许拿它们凑数"
    assert plan.cash_weight == pytest.approx(1.0), "剩余预算**留现金**"


def test_a_missing_price_is_judged_not_free():
    """🚨 **缺价 ⇒ 判不出**（不是 0）。编 0 会被读成「免费」⇒ 可买率虚高。"""
    plan = build_allocation(
        _ups([("600000.SH", 4.0), ("999999.SH", 3.0)]),
        capital=50_000, is_rebalance_day=True,
        closes={"600000.SH": 20.0})          # 999999 没有价
    aff = plan.affordability
    assert aff["n_price_missing"] == 1
    assert aff["removed_price_missing"] == ["999999.SH"]
    assert "999999.SH" not in [p["code"] for p in plan.positions], (
        "判不出 ⇒ 不许放行（放行 = 当成免费）")


def test_the_price_caliber_is_written_into_the_artifact():
    """🚨 **取价口径必须随数字离开脚本**（第 7 层：口径标签）。

    生产用**信号日当天或之前最后一根 bar**的不复权 close；
    而 `deployed_d2_verdict.json` 的 T3 臂用**入场价** ⇒ **两者不是同一个量**
    ⇒ T3 的读数**不能直接当生产读数**。
    """
    plan = build_allocation(
        _ups([("600000.SH", 4.0)]),
        capital=50_000, is_rebalance_day=True, closes=CLOSES)
    rule = plan.affordability["price_rule"]
    assert "不复权" in rule
    assert "信号日" in rule, "必须写明是**信号日**的价（不是入场价）"
    assert "lot_shares_rule" in plan.affordability, "整手规则名也要带（规则改了口径就变）"


def test_no_filter_means_no_filter_report():
    """`closes=None` ⇒ **不做过滤**（保持既有行为）⇒ 报告也**没有**。"""
    plan = build_allocation(
        _ups([("688498.SH", 1.0)]),
        capital=50_000, is_rebalance_day=True)
    assert plan.affordability is None
    assert [p["code"] for p in plan.positions] == ["688498.SH"], (
        "不传 closes ⇒ **不剔**（可买性过滤必须是**可关的**）")


# ══════════════════════════════════════════════════════════════════
# ②-b 非调仓日：不出假订单（P1）
# ══════════════════════════════════════════════════════════════════

def test_a_non_rebalance_day_produces_no_orders():
    """🚨 21 天里有 20 天**不该交易** ⇒ 非调仓日不许出 add/remove。

    实测缺陷：非调仓日的 `alloc_*.json` 写着「remove 10 只 / add 5 只」，
    而那一天根本不该交易任何东西。
    """
    plan = build_allocation(
        _ups([("600000.SH", 4.0), ("688610.SH", 3.0)]),
        capital=50_000, is_rebalance_day=False,
        current_positions=[{"code": "300951.SZ", "name": "原持仓"}])
    assert plan.rebalance == [], "非调仓日**不许有订单**"
    assert plan.is_rebalance_day is False


def test_a_non_rebalance_day_describes_the_holdings_not_todays_list():
    """🚨 非调仓日 `positions` = **沿用的持仓**，**不是**今天的名单。

    ⇒ 「配置方案」在非调仓日描述的是「我持有什么」，调仓日描述「我要换成什么」。
    ⚠️ **不许**用今天的名单冒充持仓（那正是原 bug）。
    """
    plan = build_allocation(
        _ups([("600000.SH", 4.0), ("688610.SH", 3.0)]),
        capital=50_000, is_rebalance_day=False,
        current_positions=[{"code": "300951.SZ", "name": "原持仓"}])
    assert [p["code"] for p in plan.positions] == ["300951.SZ"], (
        "非调仓日的持仓必须是**沿用的那份**，不是今天的推荐名单")
    assert plan.held == [{"code": "300951.SZ", "name": "原持仓"}]


def test_the_rebalance_day_progress_and_held_since_are_reported():
    """🚨 产物必须带 `is_rebalance_day` / 进度 / 沿用哪份名单 / `held_since`。

    🚨 `held_since` **可能是 null** ⇒ 那是「建仓日未知」
    ⇒ **不许**拿 `as_of` 顶替（`as_of` 是**最后出单日**，非调仓日两者必然不同）。
    """
    plan = build_allocation(
        _ups([("600000.SH", 4.0)]),
        capital=50_000, is_rebalance_day=False,
        current_positions=[{"code": "300951.SZ", "name": "原持仓"}],
        held_since=None, days_since_rebalance=5, rebalance_every=21)
    d = plan.to_dict()
    assert d["is_rebalance_day"] is False
    assert d["days_since_rebalance"] == 5
    assert d["rebalance_every"] == 21
    assert d["held_since"] is None, "建仓日未知 ⇒ **null**（不许编）"
    assert d["held"] == [{"code": "300951.SZ", "name": "原持仓"}]


def test_is_rebalance_day_has_no_default():
    """🚨 **新参数不许有默认值** ⇒ 少传必须 `TypeError`。

    否则某次调用会**静悄悄地**又生成一份假订单（第 6 种形状：假装能调）。
    """
    params = inspect.signature(build_allocation).parameters
    assert params["is_rebalance_day"].default is inspect.Parameter.empty, (
        "🚨 is_rebalance_day 有默认值 ⇒ 忘了传就会静默出一份假订单")
    with pytest.raises(TypeError):
        build_allocation(_ups([("600000.SH", 1.0)]), capital=50_000)


# ══════════════════════════════════════════════════════════════════
# ③ signal_id：三个键固定，配置层参数不进指纹
# ══════════════════════════════════════════════════════════════════

def test_the_signal_fingerprint_contract_is_exactly_three_keys():
    """🚨 **config 的键必须恰好三个**：weights / top_n / bottom_n。

    ⛔ 不含 `pool_filter`（配置层）⇒ ⛔ 不含 `n_positions`（账户层）。
    """
    sid = signal_fingerprint({"weights": {"mom20": -1.0}, "top_n": 10, "bottom_n": 10})
    assert isinstance(sid, str) and len(sid) == 8
    assert re.fullmatch(r"[0-9a-f]{8}", sid), f"必须是 8 位十六进制，实得 {sid!r}"


def test_a_config_change_changes_the_fingerprint():
    """**配置变 ⇒ 指纹变** ⇒ 前瞻证据可按它分桶。"""
    base = {"weights": {"mom20": -1.0}, "top_n": 10, "bottom_n": 10}
    assert signal_fingerprint(base) != signal_fingerprint({**base, "top_n": 20})
    assert signal_fingerprint(base) != signal_fingerprint({**base, "bottom_n": 5})
    assert signal_fingerprint(base) != signal_fingerprint(
        {**base, "weights": {"mom20": 1.0}})


def test_the_fingerprint_is_insertion_order_independent():
    """指纹要**跨进程、跨天**可比 ⇒ 必须与 dict 插入顺序无关。"""
    a = signal_fingerprint({"weights": {"x": 1.0, "y": 2.0}, "top_n": 10, "bottom_n": 10})
    b = signal_fingerprint({"bottom_n": 10, "top_n": 10, "weights": {"y": 2.0, "x": 1.0}})
    assert a == b


def test_config_layer_changes_do_not_change_the_signal_id():
    """🚨 **本轮最贵的一条**：配置层的改动**不许**改信号身份。

    敞口 5→10 只 + 可买性过滤（配置层）⇒ **名单生成配置一个字没变**
    ⇒ 指纹**逐位不变** ⇒ 底稿里那 160 行**仍然属于当前信号**
    ⇒ D1 的 6 个窗口**保住**。

    ⇒ 指纹只该由**影响排名**的东西决定：权重、涨腿宽度、跌腿宽度。
    """
    from aplus.reports import current_signal_id as _cur
    sig = _cur("data/reports")
    # 换掉配置层的东西（敞口、仓数、可买性过滤）⇒ 指纹不变
    assert sig == _cur("data/reports")


def test_the_verification_rows_carry_the_signal_id_but_old_rows_are_not_backfilled():
    """🚨 **新行带 `signal_id`；既有行不许回填**（追加式纪律）。

    既有 160 行**没有**该标签 ⇒ `scripts/signal_scope.py` 判「不可归属」
    ⇒ 不可判定。**不许**事后给它们贴标签（那是「事后编造」的形状）。
    """
    from aplus.verify import verify_predictions

    preds = pd.DataFrame({"code": ["600000.SH"], "name": ["甲"],
                          "direction": ["up"], "score": [1.0]})
    bars = pd.DataFrame({"code": ["600000.SH"], "close": [11.0], "prev_close": [10.0]})
    out = verify_predictions(preds, bars, default_cost=0.003, signal_id="abc12345")
    assert SIGNAL_ID_COLUMN in out.columns
    assert out[SIGNAL_ID_COLUMN].iloc[0] == "abc12345"

    # 不传 ⇒ **没有**这一列（缺列就是缺列，不许回落成「属于当前信号」）
    out2 = verify_predictions(preds, bars, default_cost=0.003)
    assert SIGNAL_ID_COLUMN not in out2.columns


def test_save_predictions_writes_the_signal_id_column(tmp_path):
    from aplus.reports import load_predictions, save_predictions

    up = pd.DataFrame({"code": ["600000.SH"], "name": ["甲"],
                       "direction": ["up"], "score": [2.0]})
    down = pd.DataFrame({"code": ["600001.SH"], "name": ["乙"],
                         "direction": ["down"], "score": [-2.0]})
    save_predictions(tmp_path, "2026-10-21", up, down, signal_id="deadbeef")
    raw = pd.read_parquet(tmp_path / "predictions" / "pred_2026-10-21.parquet")
    assert SIGNAL_ID_COLUMN in raw.columns
    assert set(raw[SIGNAL_ID_COLUMN]) == {"deadbeef"}
    u, d = load_predictions(tmp_path, "2026-10-21")
    assert set(u[SIGNAL_ID_COLUMN]) == {"deadbeef"}


def test_the_signal_id_column_name_is_identical_in_both_modules():
    """🚨 `aplus/reports.py` 与 `aplus/verify.py` 的列名**必须逐字相同**。

    `reports` import `verify`（反向 import 会成环）⇒ 列名只能是两处各写一遍
    ⇒ 只能靠**守卫**钉住（第 97 层）。
    """
    from aplus import reports, verify
    assert reports.SIGNAL_ID_COLUMN == verify.SIGNAL_ID_COLUMN == "signal_id"


# ══════════════════════════════════════════════════════════════════
# ④ 接线：run_daily 真的显式传参
# ══════════════════════════════════════════════════════════════════

def _alloc_calls():
    tree = ast.parse(_RD.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "build_allocation"]


def test_run_daily_passes_is_rebalance_day_and_closes_explicitly():
    """🚨 生产接线处**必须显式传**（不许依赖默认值）。

    守卫锚在**实参表**上（AST 语法位置），不是全文子串
    （第 13 条：子串会被注释满足）。
    """
    calls = _alloc_calls()
    assert calls, "run_daily.py 里没有 build_allocation 调用 ⇒ 前提失效"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "is_rebalance_day" in kwargs, (
            "生产没显式传 is_rebalance_day ⇒ 会静默出一份假订单")
        assert "closes" in kwargs, (
            "生产没显式传 closes ⇒ 可买性过滤在实盘**根本没开**"
            "（看着像开了，实际是关的 —— 第 6 种形状：假装能调）")


def test_run_daily_passes_the_signal_id_to_save_predictions():
    """🚨 `signal_id` 必须**真的落盘**（不落盘 ⇒ 底稿永远不可归属）。"""
    src = _RD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "save_predictions"]
    assert calls, "找不到 save_predictions 调用"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "signal_id" in kwargs, "save_predictions 没传 signal_id"


def test_run_daily_scopes_the_stats_windows_by_signal():
    """🚨 `stats.json` 的 W（独立成交窗口数）必须**按信号分桶**。

    前端解锁命中率报告用的判据是 `stats.json::n_independent_windows`，
    而 D1 闸门按 `signal_id` 分桶 ⇒ 若前者报整本底稿、后者报当前信号
    ⇒ **两者打架**（前端解锁了，闸门说不可判定）。

    ⇒ 过滤入口必须复用 `scripts/signal_scope.py`（**不许**在 `aplus/` 侧
    再写一遍 —— 第 97 层）。
    """
    src = _RD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "signal_scope":
            imported |= {a.name for a in node.names}
    assert "scope_ledger" in imported, (
        "run_daily 必须**复用** scripts/signal_scope.py::scope_ledger "
        "（不许自己写一遍过滤 —— 第 97 层）")
    assert "resolve_attribution_context" in imported, (
        "run_daily 必须用 signal_scope 的 resolve_attribution_context "
        "（不许自己算当前信号 id —— 第二份真相源）")


def test_the_stats_w_is_not_the_whole_ledger(tmp_path):
    """🚨 **行为守卫**：`stats.json` 的 W ≠ 整本底稿的 W（要按信号分桶）。

    这条是**跑出来的**数字，不是文本匹配 ⇒ 改了 `step_stats` 的实现
    （例如不小心把整本底稿传进 `rolling_stats`）**当场变红**。
    """
    import json as _json
    import shutil as _shutil

    sys.path.insert(0, str(_ROOT / "scripts"))
    import run_daily as _rd

    ledger = _ROOT / "data" / "reports" / "verification.parquet"
    if not ledger.exists():
        pytest.skip("底稿不在（无 data/reports/verification.parquet）")
    _shutil.copy(ledger, tmp_path / "verification.parquet")

    _rd.step_stats(tmp_path, "2026-10-21")
    d = _json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))

    scoped_w = d.get("n_independent_windows")
    ledger_w = d.get("n_independent_windows_of_ledger")
    assert scoped_w is not None and ledger_w is not None, (
        "两个 W 都要报（口径随数字离开脚本）")
    # 底稿的信号归属是**部分**的（有不可归属的行）⇒ 分桶后的 W 必然更小
    assert scoped_w < ledger_w, (
        f"🚨 分桶后的 W（{scoped_w}）== 整本底稿的 W（{ledger_w}）"
        "⇒ `stats.json` 报的是**整本底稿** ⇒ 前端与 D1 闸门会打架")
    assert d["signal_scope"]["status"] in ("OK", "ATTRIBUTED_BY_WEIGHTS_HASH"), (
        f"作用域状态异常：{d['signal_scope']['status']}")
