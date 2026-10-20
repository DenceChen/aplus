"""`/api/lot-affordability` 与 `/api/deployed-config` —— 行为层守卫（2026-10-20）。

## 这两个端点挡住什么

| 端点 | 挡住的误读 |
|---|---|
| `/api/lot-affordability` | 「今天 10 涨 10 跌，照着买就行」 ⇒ 实际实盘那 10 只里**只有 4 只**在每份 ¥5,000 的预算内（`688498.SH` 收盘 1010.35，**科创板一手 200 股** ⇒ 1 手 ¥202,070 = 全部本金的 4 倍） |
| `/api/deployed-config` | 「屏幕上那个年化 = 我的年化」 ⇒ 实际判决档是**满仓 10 只 + 8 因子逐年 IC 定权**，实盘是**半仓 5 只 + `DEFAULT_WEIGHTS` 4 因子** |

## 三条纪律

1. 🚨 **端点里不许重算判决数**：要显示的数一律**逐字透传**产物字段。
   端点重算 = 第二份实现 = 公式抄错照常 200 零告警（第 137 层）。
2. 🚨 **缺产物时给 `null` + 一句原因**，**不许** `{}`、**不许**回落成别的数
   （`deployed_account_nav.json` 自报「不是判决」，拿它顶替 = 混引）。
3. 🚨 **前端不许硬编码判决数字** —— 断言锚在**语法位置**（真 AST 的字面量节点），
   不是全文子串（第 13 条：子串能被注释满足）。

## 为什么 `lot_shares` 要**分岔夹具**

科创板 688/689 = 200 股一手，其余（主板/创业板/北交所）= 100 股。
只测一个分支 ⇒ 「688 走 200、其它走 100」这条规则的一半没被测到，
而漏掉的那一半恰好是**最贵的**（`688498.SH` 一手 ¥202,070 = 本金的 4 倍）。
⇒ 夹具里同时放 688 / 689 / 300 / 600 / 002 五个代码。

## 为什么扫描器要**自证**

「扫描器自己坏了」与「前端真的干净」在终端上**长得一样**。
⇒ 两条伴生守卫：把字面量**种进**代码位置必须被抓到；
  把它放进**注释 / 字符串**位置必须**抓不到**（否则守卫过严，把正常的
  注释也判红 —— 第 112 层）。
"""

from __future__ import annotations

import inspect
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aplus.webapp import create_app  # noqa: E402

HTML = _ROOT / "aplus" / "web" / "index.html"
REPORTS = _ROOT / "data" / "reports"

#: 判决产物（三份都要，因为端点顺着产物链读上游）
VERDICT_ARTIFACT = "basket_nav_01_2026-10-16.json"
UPSTREAM_ARTIFACT = "wf_weights_backtest_k21.json"
EQUITY_ARTIFACT = "d2_signal_requirement.json"

#: 实盘档读数的产物（另一个 agent 在生成；**不在**生产路径上写）
DEPLOYED_D2_ARTIFACT = "deployed_d2_verdict.json"

#: 🚨 **对照臂**：已存在的半仓**推算**（不是判决）。
#: 它必须被放进夹具里 —— 否则「端点回落成它」这个变异体在夹具上**看不见**
#: （回落找不到东西 ⇒ 回落是空操作 ⇒ 守卫照样绿）。
#: 这正是第 122 层的形态：**变异体没打中**与「守卫有洞」在终端上长得一样。
CONTROL_ARM_ARTIFACT = "deployed_account_nav.json"

#: 夹具用的价格（**这些数是夹具，不是判据**；真值从主库 parquet 读）
PRICE = {
    "688498.SH": 1010.35,   # 科创板 → 200 股
    "689009.SH": 15.00,     # 科创板 CDR → 200 股
    "300951.SZ": 59.20,     # 创业板 → 100 股
    "600519.SH": 20.00,     # 主板 → 100 股
    "002897.SZ": 46.70,     # 深主板 → 100 股
}
MISSING_PRICE_CODE = "688111.SH"   # 主库里**没有**它 ⇒ 价格读不出来

#: 榜单日
PRED_DATE = "2026-10-20"

#: 主库里**故意多放一根 10-21 的 bar**（close 999）——
#: 端点必须取 `date <= pred_date` 的最后一根，**不是**文件里的最后一根。
FUTURE_BAR_DATE = "2026-10-21"
FUTURE_BAR_CLOSE = 999.0


# ────────────────────────── 夹具 ──────────────────────────

def _write_daily(root: Path, code: str, close: float) -> None:
    """往主库写一只票的日线（**含一根 pred_date 之后的 bar**）。"""
    from aplus.store import LocalStore
    store = LocalStore(root)
    df = pd.DataFrame([
        {"date": "2026-10-16", "open": close, "high": close, "low": close,
         "close": close, "volume": 1000, "amount": close * 1000,
         "turn": 1.0, "pctChg": 0.0, "tradestatus": 1, "isST": 0},
        {"date": PRED_DATE, "open": close, "high": close, "low": close,
         "close": close, "volume": 1000, "amount": close * 1000,
         "turn": 1.0, "pctChg": 0.0, "tradestatus": 1, "isST": 0},
        # 🚨 未来 bar：端点**不许**取它
        {"date": FUTURE_BAR_DATE, "open": FUTURE_BAR_CLOSE, "high": FUTURE_BAR_CLOSE,
         "low": FUTURE_BAR_CLOSE, "close": FUTURE_BAR_CLOSE, "volume": 1000,
         "amount": FUTURE_BAR_CLOSE * 1000, "turn": 1.0, "pctChg": 0.0,
         "tradestatus": 1, "isST": 0},
    ])
    store.upsert_daily(code, df)


def _write_predictions(root: Path) -> None:
    from aplus.reports import save_predictions
    up = pd.DataFrame([
        {"code": "688498.SH", "name": "源杰科技", "direction": "up", "score": 1.57},
        {"code": "300951.SZ", "name": "博硕科技", "direction": "up", "score": 1.63},
        {"code": "600519.SH", "name": "贵州茅台", "direction": "up", "score": 1.11},
        {"code": MISSING_PRICE_CODE, "name": "无价股", "direction": "up", "score": 0.50},
    ])
    down = pd.DataFrame([
        {"code": "689009.SH", "name": "九号公司", "direction": "down", "score": -5.1},
        {"code": "002897.SZ", "name": "意华股份", "direction": "down", "score": -6.1},
    ])
    save_predictions(root / "reports", PRED_DATE, up, down)


@pytest.fixture
def lot_root(tmp_path):
    """一个**自足**的临时 data root：主库 + 榜单。**不碰生产路径**。"""
    for code, close in PRICE.items():
        _write_daily(tmp_path, code, close)
    _write_predictions(tmp_path)
    return tmp_path


@pytest.fixture
def lot_body(lot_root):
    r = TestClient(create_app(lot_root, llm=object())).get("/api/lot-affordability")
    assert r.status_code == 200, r.text[:500]
    return r.json()


@pytest.fixture
def verdict_root(tmp_path):
    """把**真产物**复制进临时 data root（**不在生产目录写任何东西**）。"""
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for name in (VERDICT_ARTIFACT, UPSTREAM_ARTIFACT, EQUITY_ARTIFACT,
                 CONTROL_ARM_ARTIFACT):
        src = REPORTS / name
        assert src.exists(), f"真产物不在：{src} —— 守卫的前提失效"
        (reports / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def _client(root):
    return TestClient(create_app(root, llm=object()))


# ─────────────────── 1. lot-affordability ───────────────────

def test_lot_shares_is_200_for_star_market_and_100_for_everything_else(lot_body):
    """★ **分岔夹具**：688/689 = 200，其余 = 100。

    只测一个分支 ⇒ 规则的一半没被测到，而漏掉的那一半恰好是最贵的
    （`688498.SH` 一手 ¥202,070 = 全部本金的 4 倍）。
    """
    got = {it["code"]: it["lot_shares"] for it in lot_body["items"]}
    assert got["688498.SH"] == 200, "科创板 688 必须是 200 股一手"
    assert got["689009.SH"] == 200, "科创板 CDR 689 必须是 200 股一手"
    assert got["300951.SZ"] == 100, "创业板 300 必须是 100 股一手"
    assert got["600519.SH"] == 100, "主板 600 必须是 100 股一手"
    assert got["002897.SZ"] == 100, "深主板 002 必须是 100 股一手"


def test_lot_cost_is_the_close_times_the_lot_size(lot_body):
    """★ 1 手金额 = 收盘价 × 一手股数（**唯一的算式**，且用的是端点自己的两个字段）。"""
    for it in lot_body["items"]:
        if it["close"] is None:
            continue
        assert it["lot_cost"] == it["close"] * it["lot_shares"], (
            f"{it['code']} 的 1 手金额与 close×lot_shares 不符")


def test_the_price_is_the_last_bar_at_or_before_the_prediction_date(lot_body, lot_root):
    """★ 价格锚在 `date <= pred_date` 的**最后一根** bar，不是文件里的最后一根。

    夹具在主库里**故意多放了一根 10-21 的 bar（close 999）**：
    取了它 ⇒ 整手金额会离谱地大，而页面照常渲染、零告警。
    """
    from aplus.store import LocalStore
    store = LocalStore(lot_root)
    for it in lot_body["items"]:
        if it["close"] is None:
            continue
        df = store.read_daily(it["code"])
        df = df[df["date"].astype(str) <= PRED_DATE].sort_values("date")
        truth = float(df.iloc[-1]["close"])
        assert it["close"] == truth, f"{it['code']} 取错了 bar"
        assert it["close_date"] == PRED_DATE, f"{it['code']} 的 bar 日期不对"
        assert it["close"] != FUTURE_BAR_CLOSE, "取到了 pred_date 之后的 bar"


def test_a_stock_without_a_price_is_null_with_a_reason_not_a_zero(lot_body):
    """★ 没有价 ⇒ `null` + 一句原因，**不许**编 0。

    编 0 会让「买得起」恒真（0 ≤ 5,000）⇒ 假绿（形状 23 的方向）。
    """
    item = next(it for it in lot_body["items"] if it["code"] == MISSING_PRICE_CODE)
    assert item["close"] is None, "没有价却编了一个数"
    assert item["lot_cost"] is None
    assert item["affordable_at_5000"] is None, "没有价却判了买不买得起"
    assert item["affordable_at_10000"] is None
    assert item["price_missing_reason"], "没有价却没给原因"


def test_the_budget_comes_from_the_production_signature_not_from_a_literal(lot_body):
    """★ 预算 = `inspect.signature(build_allocation)` 的默认值，**不是手打的 5000**。"""
    from aplus.alloc import build_allocation
    params = inspect.signature(build_allocation).parameters
    capital = float(params["capital"].default)
    n_pos = int(params["max_positions"].default)
    cap = float(params["per_stock_cap"].default)
    b = lot_body["budget"]
    assert b["capital"] == capital
    assert b["max_positions"] == n_pos
    assert b["per_stock_cap"] == cap
    assert b["per_position_amount"] == capital * cap
    assert b["capital_div_positions"] == capital / n_pos
    assert b["equity_weight"] == n_pos * cap
    assert b["cash_weight"] == 1.0 - n_pos * cap
    assert "inspect.signature" in b["source"]


def test_the_budget_follows_the_production_defaults_when_they_change(lot_root, monkeypatch):
    """★ **能力守卫**：改了生产默认值，端点的数**必须跟着变**。

    只断言「端点的数等于当前默认值」⇒ 端点里手打 0.5/5/0.1 也照样绿
    （第 112 层：只钉存在不钉性质）。⇒ 这里把签名**换掉**再问一次。
    """
    import aplus.alloc as alloc
    real = alloc.build_allocation

    def fake(up_predictions, capital=80_000.0, max_positions=3,
             per_stock_cap=0.20, score_threshold=None, current_positions=None):
        return real(up_predictions, capital=capital, max_positions=max_positions,
                    per_stock_cap=per_stock_cap, score_threshold=score_threshold,
                    current_positions=current_positions)

    monkeypatch.setattr(alloc, "build_allocation", fake)
    body = _client(lot_root).get("/api/lot-affordability").json()
    b = body["budget"]
    assert b["capital"] == 80_000.0
    assert b["max_positions"] == 3
    assert b["per_stock_cap"] == pytest.approx(0.20)
    assert b["equity_weight"] == pytest.approx(0.60)
    assert b["per_position_amount"] == pytest.approx(16_000.0)
    assert b["capital_div_positions"] == pytest.approx(80_000.0 / 3)
    for it in body["items"]:
        if it["lot_cost"] is None:
            continue
        assert it["affordable_at_5000"] == (it["lot_cost"] <= 16_000.0)
        assert it["affordable_at_10000"] == (it["lot_cost"] <= 80_000.0 / 3)


def test_the_summary_is_scoped_to_the_up_leg_and_says_so(lot_body):
    """★ `n_unaffordable` / `investable_ratio` 的分母是**看涨腿**（要买的那几只）。

    看跌腿是**回避清单**，本来就不买 ⇒ 混进分母会把「买不进」说得比实际更严重
    （而且方向是**悲观**的 —— 那是另一种误导）。
    """
    s = lot_body["summary"]
    assert s["scope"] == "up"
    up = [it for it in lot_body["items"] if it["direction"] == "up"]
    down = [it for it in lot_body["items"] if it["direction"] == "down"]
    priced_up = [it for it in up if it["lot_cost"] is not None]
    aff = [it for it in priced_up if it["affordable_at_5000"]]
    assert s["n_up"] == len(up)
    assert s["n_down"] == len(down)
    assert s["n_unaffordable"] == len(priced_up) - len(aff)
    assert s["investable_ratio"] == len(aff) / len(priced_up)
    assert s["by_direction"]["up"]["n"] == len(up)
    assert s["by_direction"]["down"]["n"] == len(down)
    assert "看涨腿" in lot_body["caliber"]["summary_scope"]


def test_the_price_caliber_is_stated_and_the_price_is_never_invented(lot_body):
    """★ 取价口径必须写在返回体里（用哪一天、复不复权）。"""
    cal = lot_body["caliber"]
    assert "LocalStore.read_daily" in cal["price_source"]
    assert "date <= as_of" in cal["price_rule"]
    assert "不复权" in cal["price_adjusted"]
    assert lot_body["pred_date"] == PRED_DATE
    assert lot_body["source"] == f"pred_{PRED_DATE}.parquet"


def test_a_missing_prediction_artifact_is_404_with_a_reason(tmp_path):
    """★ 没有榜单 ⇒ 404 + 一句原因，**不是**空对象。"""
    r = _client(tmp_path).get("/api/lot-affordability")
    assert r.status_code == 404, f"期望 404，实得 {r.status_code}：{r.text[:300]}"
    assert r.json().get("detail"), "404 没带原因"


# ─────────────────── 2. deployed-config ───────────────────

def _verdict_readings(body) -> dict:
    return body["verdict_side"]["readings"]


def test_every_verdict_reading_is_verbatim_from_the_artifact(verdict_root):
    """★ 判决读数**逐字**取产物字段（不许端点里算一遍）。"""
    art = json.loads((verdict_root / "reports" / VERDICT_ARTIFACT).read_text(encoding="utf-8"))
    t1 = art["tiers"][1]
    body = _client(verdict_root).get("/api/deployed-config").json()
    r = _verdict_readings(body)
    assert r["verdict_reading"] == art["verdict_reading"]
    assert r["verdict_mde_annual"] == art["verdict_mde_annual"]
    assert r["verdict_upper_one_sided_95"] == art["verdict_upper_one_sided_95"]
    assert r["target"] == art["target"]
    assert r["verdict_text"] == art["verdict_text"]
    assert r["verdict_rule"] == art["verdict_rule"]
    assert r["verdict_cases_fired"] == art["verdict_cases_fired"]
    assert r["verdict_tier_cagr"] == t1["cagr"]
    assert r["verdict_tier_arith_ann"] == t1["arith_ann"]
    assert r["verdict_tier_n_days"] == t1["n_days"]
    assert r["verdict_tier_years"] == t1["years"]
    assert r["verdict_tier_label"] == t1["tier"]


def _all_numbers(obj):
    out = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        return {float(obj)}
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _all_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _all_numbers(v)
    return out


def test_not_a_single_verdict_reading_number_is_computed(verdict_root):
    """★ 判决读数里**没有一个数**是端点算出来的。

    判据：读数里的每个数都必须**逐字出现在产物里**。
    ⇒ 端点若自己算一个 `c − M` 出来，它不在产物的数集里 ⇒ 红。
    """
    art = json.loads((verdict_root / "reports" / VERDICT_ARTIFACT).read_text(encoding="utf-8"))
    body = _client(verdict_root).get("/api/deployed-config").json()
    allowed = _all_numbers(art)
    for key, val in _verdict_readings(body).items():
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        assert float(val) in allowed, (
            f"判决读数里的 {key}={val!r} **不在产物的数集里** ⇒ 它是端点算出来的"
            "（第 137 层：端点重算 = 第二份实现）")


def test_the_deployed_side_is_the_production_configuration(verdict_root):
    """★ 实盘侧 = 生产签名 + 生产常量，**逐字**。"""
    from aplus.alloc import build_allocation
    from aplus.evolve import LIVE_REBALANCE_EVERY
    from aplus.factor import DEFAULT_WEIGHTS

    params = inspect.signature(build_allocation).parameters
    capital = float(params["capital"].default)
    n_pos = int(params["max_positions"].default)
    cap = float(params["per_stock_cap"].default)

    body = _client(verdict_root).get("/api/deployed-config").json()
    d = body["deployed_side"]["dimensions"]
    assert d["exposure"] == n_pos * cap
    assert d["n_positions"] == n_pos
    assert d["per_position_amount"] == capital * cap
    assert d["signal_weights"] == DEFAULT_WEIGHTS
    assert d["rebalance_every"] == LIVE_REBALANCE_EVERY
    assert d["cash_weight"] == 1.0 - n_pos * cap
    assert body["deployed_side"]["signal_weights"] == DEFAULT_WEIGHTS


def test_the_deployed_side_follows_the_production_signature_when_it_changes(verdict_root, monkeypatch):
    """★ **能力守卫**：改了生产默认值，实盘侧必须跟着变。

    否则「端点里手打 0.5/5/0.1」会一直绿（第 112 层）。
    """
    import aplus.alloc as alloc
    real = alloc.build_allocation

    def fake(up_predictions, capital=80_000.0, max_positions=3,
             per_stock_cap=0.20, score_threshold=None, current_positions=None):
        return real(up_predictions, capital=capital, max_positions=max_positions,
                    per_stock_cap=per_stock_cap, score_threshold=score_threshold,
                    current_positions=current_positions)

    monkeypatch.setattr(alloc, "build_allocation", fake)
    body = _client(verdict_root).get("/api/deployed-config").json()
    d = body["deployed_side"]["dimensions"]
    assert d["exposure"] == pytest.approx(0.60)
    assert d["n_positions"] == 3
    assert d["per_position_amount"] == pytest.approx(16_000.0)
    assert d["cash_weight"] == pytest.approx(0.40)


def test_the_two_sides_are_actually_different(verdict_root):
    """★ 判决档与实盘档**必须**在展示前就分得开（不是同一套配置）。

    这是「用户误读 10.56%」的根。

    ⚠️ **2026-10-21：尺寸维度现在「一样」了** —— 实盘敞口由 0.5 改成 **1.0**、
    仓数由 5 改成 **10** ⇒ 与判决档的尺寸**一致**（每份都是 ¥5,000、现金都 0%）。
    ⇒ **这正是改动生效的证据，不是缺陷。**
    ⇒ 剩下的差异只有两处，**而它们才是必须显示的那两处**：
      ① **信号权重**：判决档 8 因子逐年 IC 定权 vs 实盘 4 因子固定权重
      ② **执行模型**：判决档日再平衡 + 小数股 + 换手×费率
         vs 实盘持有到期 + 整手 + 逐笔成本
    ⇒ 本守卫现在**两头都钉**：尺寸维度必须**一致**（防止有人把实盘又改回半仓
    而界面还显示「一样」），信号维度必须**不一致**（防止把两个量混成一个）。
    """
    body = _client(verdict_root).get("/api/deployed-config").json()
    dims = {x["dim"]: x for x in body["dimensions"]}
    assert set(dims) == {"exposure", "n_positions", "per_position_amount",
                          "signal_weights", "rebalance_every", "cash_weight"}
    # 🚨 尺寸维度现在必须**一致**（实盘已改成满仓 10 只）
    assert dims["exposure"]["differs"] is False, (
        f"敞口又不一样了：判决 {dims['exposure']['verdict']} vs "
        f"实盘 {dims['exposure']['deployed']} —— 实盘应当是满仓 1.0")
    assert dims["n_positions"]["differs"] is False, (
        f"仓数又不一样了：判决 {dims['n_positions']['verdict']} vs "
        f"实盘 {dims['n_positions']['deployed']} —— 实盘应当是 10 只")
    # ⇒ 而「还差在哪」必须仍然显示出来
    assert dims["signal_weights"]["differs"] is True
    assert dims["cash_weight"]["differs"] is False, (
        "满仓后现金占比两边都是 0%")
    # 每个维度都必须带自己的来源（不许「来源不明」）
    for x in body["dimensions"]:
        assert x["verdict_source"], f"{x['dim']} 没有判决侧来源"
        assert x["deployed_source"], f"{x['dim']} 没有实盘侧来源"
    cal = body["caliber"]
    assert "不是同一个量" in cal["the_two_sides_are_not_the_same_quantity"]
    assert "不是" in cal["the_verdict_reading_is_not_your_annual_return"]


def test_a_missing_deployed_d2_artifact_is_null_with_a_reason_not_a_fallback(verdict_root):
    """★ 产物不在 ⇒ 三个字段全 `null` + 一句「产物尚未生成」。

    🚨 **不许回落成别的数**：`deployed_account_nav.json` 里也有一个半仓读数，
    但它自报 `role = "⚠️ 对照臂 / 推算，**不是判决**"` ⇒ **不是同一个量**。
    """
    body = _client(verdict_root).get("/api/deployed-config").json()
    dd = body["deployed_d2_reading"]
    assert dd["available"] is False
    assert dd["cagr"] is None, "缺产物却给了一个年化"
    assert dd["verdict_mde_annual"] is None
    assert dd["gap_to_target_pp"] is None
    assert "尚未生成" in dd["note"]
    assert dd["raw"] is None
    # 不许出现别处的数
    assert "deployed_account_nav" not in json.dumps(dd, ensure_ascii=False)


def test_a_missing_deployed_d2_artifact_does_not_fall_back_to_the_control_arm(verdict_root):
    """★ **回落守卫**：缺产物时，`deployed_d2_reading` 里**不许**出现
    `deployed_account_nav.json`（对照臂推算）里的任何一个数。

    ## 为什么这条要**单独**写一条

    「不许回落」如果只写成「cagr 是 null」，那么一个**回落成对照臂**
    的端点会给出 `cagr = 0.06155795761282645`（半仓推算）⇒ 上一条会红。
    但反过来：一条只检查 `cagr is None` 的守卫，在端点**同时**给出
    别的臂的数时**也许**会红、也许不会 —— 取决于端点怎么写。
    ⇒ 这里把判据落在**数集**上：对照臂的任何一个数出现在读数里 ⇒ 红。

    ## 为什么夹具里**必须**有对照臂

    没有它 ⇒ 端点回落时**找不到东西可回落** ⇒ 回落是**空操作** ⇒
    守卫照样全绿，而它守的东西**根本没被触发**。
    这正是第 122 层：**变异体没打中**与「守卫有洞」在终端上长得一样。
    """
    control = json.loads(
        (verdict_root / "reports" / CONTROL_ARM_ARTIFACT).read_text(encoding="utf-8"))
    control_nums = _all_numbers(control)
    body = _client(verdict_root).get("/api/deployed-config").json()
    dd = body["deployed_d2_reading"]
    leaked = _all_numbers(dd) & control_nums
    assert not leaked, (
        f"实盘档读数里出现了对照臂（{CONTROL_ARM_ARTIFACT}）的数：{sorted(leaked)[:8]}"
        " ⇒ 那是**推算**，不是判决产物；缺产物时必须给 null + 原因")


def test_the_deployed_d2_reading_is_passed_through_verbatim_when_it_exists(verdict_root):
    """★ 产物在 ⇒ **逐字**透传，端点里一个数都不算。

    （产物写到**临时**目录，不碰生产路径。）
    """
    doc = {
        "cagr": 0.0731,
        "verdict_mde_annual": 0.1912,
        "gap_to_target_pp": 12.69,
        "note": "夹具",
    }
    (verdict_root / "reports" / DEPLOYED_D2_ARTIFACT).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    body = _client(verdict_root).get("/api/deployed-config").json()
    dd = body["deployed_d2_reading"]
    assert dd["available"] is True
    assert dd["cagr"] == doc["cagr"]
    assert dd["verdict_mde_annual"] == doc["verdict_mde_annual"]
    assert dd["gap_to_target_pp"] == doc["gap_to_target_pp"]
    assert dd["source"] == DEPLOYED_D2_ARTIFACT
    assert dd["raw"] == doc
    # ★ 端点里**一个数都不算**：读数里的每个数都必须逐字出现在产物里。
    #   （端点若在键缺失时编一个 `0.0731` 出来，它不在产物的数集里 ⇒ 红。）
    allowed = _all_numbers(doc)
    for key, val in dd.items():
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        assert float(val) in allowed, (
            f"实盘档读数里的 {key}={val!r} **不在产物的数集里** ⇒ 它是端点编出来的")


def test_a_deployed_d2_artifact_missing_a_key_gives_null_not_a_made_up_number(verdict_root):
    """★ 产物在但**缺那个键** ⇒ 该字段 `null`，**不许编一个数**。

    「产物在」与「那个数在」是**两件事** ——
    前者只保证读得到文件，后者才保证读得到数。
    """
    doc = {"verdict_mde_annual": 0.1912, "note": "夹具：故意没有 cagr"}
    (verdict_root / "reports" / DEPLOYED_D2_ARTIFACT).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    body = _client(verdict_root).get("/api/deployed-config").json()
    dd = body["deployed_d2_reading"]
    assert dd["available"] is True
    assert dd["cagr"] is None, "产物里没有 cagr 却编了一个出来"
    assert dd["gap_to_target_pp"] is None
    assert dd["verdict_mde_annual"] == 0.1912


def test_a_nested_deployed_d2_artifact_is_found_and_its_path_is_reported(verdict_root):
    """★ 产物结构变了（数在**嵌套**里）也要读得到，且要**报出它在哪一层**。

    形状错一格时，`null` 与「闸是死的」**逐位同形**（第 144 层）
    ⇒ 找到它之后必须把 JSON 路径一起递出去。
    """
    doc = {"verdict": {"cagr": 0.081234, "verdict_mde_annual": 0.2, "gap_to_target_pp": 11.8}}
    (verdict_root / "reports" / DEPLOYED_D2_ARTIFACT).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    body = _client(verdict_root).get("/api/deployed-config").json()
    dd = body["deployed_d2_reading"]
    assert dd["cagr"] == 0.081234
    assert dd["verdict_mde_annual"] == 0.2
    assert dd["gap_to_target_pp"] == 11.8
    assert dd["found_at"]["cagr"] == "$.verdict.cagr"


def test_the_deployed_d2_reading_is_read_from_the_verdict_block_not_from_any_cagr(verdict_root):
    """★ **找到了 ≠ 找对了** —— 满文档找 `cagr` 会读错对象。

    `scripts/measure_deployed_d2.py` 产的产物里**到处**都有 `cagr`
    （`arms` 每档一个、`account_legs`、`control_cross_check`…）。
    ⇒ 端点若满文档找第一个 `cagr`，很可能拿到的是**满仓判决档**的那个
    ⇒ 读数**看起来有数、其实读错了对象**（第 143 层）。

    ⇒ 端点锚在 `verdict` 块上，并把它**在哪**（JSON 路径）递出去。
    """
    doc = {
        "arms": [{"tier": "满仓判决档", "cagr": 0.10556283216930074,
                  "mde_as_annual_pct": 0.22673751360772554}],
        "account_legs": [{"cagr": 0.06155795761282645}],
        "control_cross_check": {"existing_basket_nav_01_cagr": 0.10556283216930074},
        "verdict": {
            "leg": "T2_live_truth",
            "cagr": 0.0731,
            "mde_annual": 0.1912,
            "gap_to_target_pp": 12.69,
            "upper_one_sided_95": 0.2643,
            "target": 0.2,
            "measured_sign_determinable": False,
            "cases": ["`|c| < M` ⇒ 连符号都定不了"],
        },
    }
    (verdict_root / "reports" / DEPLOYED_D2_ARTIFACT).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    body = _client(verdict_root).get("/api/deployed-config").json()
    dd = body["deployed_d2_reading"]
    assert dd["cagr"] == 0.0731, "读到了别的臂的 cagr（满文档找键的典型失败）"
    assert dd["verdict_mde_annual"] == 0.1912
    assert dd["gap_to_target_pp"] == 12.69
    assert dd["scope"] == "$.verdict"
    assert dd["found_at"]["cagr"] == "$.verdict.cagr"
    # 产物那一族写的是 `mde_annual`，端点契约名是 `verdict_mde_annual`
    # ⇒ 两者之间的差别必须**看得见**（`found_key`），不能变成一条没人知道的接缝
    assert dd["found_key"]["verdict_mde_annual"] == "mde_annual"
    assert dd["measured_sign_determinable"] is False
    assert dd["cases"] == ["`|c| < M` ⇒ 连符号都定不了"]


def test_a_missing_verdict_artifact_is_404_with_a_reason(tmp_path):
    """★ 判决产物不在 ⇒ 404 + 一句原因，**不是**空对象。"""
    r = _client(tmp_path).get("/api/deployed-config")
    assert r.status_code == 404, f"期望 404，实得 {r.status_code}：{r.text[:300]}"
    detail = r.json().get("detail", "")
    assert detail and VERDICT_ARTIFACT in detail, f"404 的原因没说是哪个产物缺了：{detail!r}"
    assert r.json() != {}, "404 不许带一个空对象当载荷"


# ─────────────────── 3. 前端不许硬编码判决数字 ───────────────────

_SCAN_JS = r"""
const fs = require('fs');
function acorn() {
  try { return require('acorn'); }
  catch (e) {
    try { return require('internal/deps/acorn/acorn/dist/acorn'); }
    catch (e2) { throw new Error('acorn unavailable: ' + e2.message); }
  }
}
const A = acorn();
const src = fs.readFileSync(0, 'utf8');
const ast = A.parse(src, { ecmaVersion: 'latest', sourceType: 'script' });
const nums = [], strs = [];
(function walk(n) {
  if (!n || typeof n !== 'object') return;
  if (Array.isArray(n)) { n.forEach(walk); return; }
  if (n.type === 'Literal') {
    if (typeof n.value === 'number') nums.push(n.raw);
    else if (typeof n.value === 'string') strs.push(n.value);
  }
  for (const k of Object.keys(n)) {
    if (k === 'type' || k === 'value' || k === 'raw' || k === 'start' || k === 'end' || k === 'loc') continue;
    walk(n[k]);
  }
})(ast);
console.log(JSON.stringify({nums, strs}));
"""


def _script_blocks(html: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, flags=re.S | re.I)


def _ast_literals(js: str) -> dict:
    """用 **node + acorn（真 AST）** 取出 JS 里的数字字面量与字符串字面量。

    🚨 **为什么不用子串**（第 13 条）：子串能被注释满足 ——
    把数字写进一行中文注释，「全文没有这个串」照样成立。
    ⇒ 这里锚在**语法位置**：acorn 的 `Literal` 节点。
    注释里的内容**结构上不可能**被当成字面量。
    """
    r = subprocess.run(
        ["node", "--expose-internals", "-e", _SCAN_JS],
        input=js, capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        f"node/acorn 扫描失败（rc={r.returncode}）：{r.stderr[:500]}")
    return json.loads(r.stdout)


def _html_without_script_and_comments(html: str) -> str:
    """剥掉 `<script>` 块与 HTML 注释 ⇒ 剩下的**可见文本**。"""
    no_script = re.sub(r"<script(?![^>]*\bsrc=)[^>]*>.*?</script>", " ", html,
                       flags=re.S | re.I)
    return re.sub(r"<!--.*?-->", " ", no_script, flags=re.S)


def _forbidden_values() -> tuple[set, set]:
    """从**真产物**推出「不许在前端硬编码」的数 —— 守卫自己也不许手打。

    取判决档的四个头条读数（点估计 / 带宽 / 95% 上界 / 算术年化）
    及其**规范写法**（仓里文档与产物 `verdict_text` 里用的 4 位小数）。
    """
    art = json.loads((REPORTS / VERDICT_ARTIFACT).read_text(encoding="utf-8"))
    t1 = art["tiers"][1]
    raw = [
        art["verdict_reading"],
        art["verdict_mde_annual"],
        art["verdict_upper_one_sided_95"],
        t1["arith_ann"],
    ]
    nums: set = set()
    texts: set = set()
    for v in raw:
        nums.add(float(v))
        pct = float(v) * 100.0
        nums.add(pct)
        # 仓里通行的 4 位小数写法（CLAUDE.md / 产物 verdict_text 逐字用的那一版）
        for d in (4,):
            s = f"{pct:.{d}f}"
            nums.add(float(s))
            texts.add(s)
    return nums, texts


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能扫前端 AST")
def test_the_frontend_has_no_hardcoded_verdict_numbers():
    """★ 前端**不许**硬编码判决数字 —— 断言锚在**语法位置**（真 AST）。

    ⚠️ 判据锚在 **AST 的字面量节点**，不是全文子串：
    写进注释里的数字**不算**硬编码（那是文档），
    而写进代码/模板里的数字**算**（那是第二份实现）。
    """
    html = HTML.read_text(encoding="utf-8")
    nums, texts = _forbidden_values()

    found_nums, found_texts = [], []
    for block in _script_blocks(html):
        lits = _ast_literals(block)
        for raw in lits["nums"]:
            try:
                val = float(raw)
            except ValueError:
                continue
            if val in nums:
                found_nums.append(raw)
        for s in lits["strs"]:
            for t in texts:
                if t in s:
                    found_texts.append((t, s[:80]))

    # 可见文本（剥掉 script 与注释）里也不许有
    visible = _html_without_script_and_comments(html)
    visible_hits = [t for t in texts if t in visible]

    assert not found_nums, (
        f"前端 JS 的**语法位置**上出现了硬编码的判决数字：{found_nums}"
        " ⇒ 判决数字必须从端点拿")
    assert not found_texts, (
        f"前端 JS 的字符串字面量里出现了硬编码的判决数字：{found_texts}"
        " ⇒ 判决数字必须从端点拿")
    assert not visible_hits, (
        f"前端的可见文本里出现了硬编码的判决数字：{visible_hits}"
        " ⇒ 判决数字必须从端点拿")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能扫前端 AST")
def test_the_scanner_really_sees_a_planted_literal_in_code_position():
    """★ **扫描器自证（正控）**：种进去必须被抓到。

    「扫描器自己坏了」与「前端真的干净」在终端上**长得一样**
    ⇒ 扫描器必须先证明自己看得见。
    """
    nums, texts = _forbidden_values()
    planted = sorted(texts)[0]
    js = f"const x = {planted};\nconst y = '判决 {planted}';\n"
    lits = _ast_literals(js)
    assert float(planted) in [float(r) for r in lits["nums"]], "扫描器没看见种在代码位置的数字"
    assert any(planted in s for s in lits["strs"]), "扫描器没看见种在字符串里的数字"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能扫前端 AST")
def test_the_scanner_ignores_comments_and_strings_for_numbers():
    """★ **扫描器自证（反控）**：注释里的数字**不算**。

    否则这条守卫会**过严**（把正常注释判红），
    而过严的守卫和恒真的一样有害 —— 它会一直红而人就会关掉它（第 112 层）。
    """
    nums, texts = _forbidden_values()
    planted = sorted(texts)[0]
    js = f"// 这行注释里写了 {planted}\n/* 块注释里也有 {planted} */\nconst s = '{planted}';\n"
    lits = _ast_literals(js)
    # 注释里的数字**不是**字面量 ⇒ 不该被当成「代码位置上的硬编码」
    assert planted not in [float(r) for r in lits["nums"]], (
        "扫描器把注释里的数字当成了代码位置上的字面量 ⇒ 守卫会过严")
    # 字符串字面量里的数字**是**字面量 ⇒ 那一路要看得见（模板拼接是真实的硬编码途径）
    assert any(planted in s for s in lits["strs"]), "扫描器没看见字符串字面量里的数字"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能扫前端 AST")
def test_the_frontend_scripts_still_parse():
    """★ 前端 JS 必须**能解析**（同名 `const` 是**解析期**错误）。

    pytest 全绿而页面永远停在「加载…」—— 本仓踩过。
    """
    html = HTML.read_text(encoding="utf-8")
    blocks = _script_blocks(html)
    assert blocks, "没找到 <script> 块"
    import tempfile
    for i, b in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(b)
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            assert r.returncode == 0, f"第 {i} 个 script 块语法错误：{r.stderr[:500]}"
        finally:
            Path(path).unlink(missing_ok=True)
