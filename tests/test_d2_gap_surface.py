"""D2 缺口台账必须出现在首屏，且「达标」支必须先过台账的天花板闸。

这个文件的存在理由不是「加一条守卫」，是封一条**假绿**：

`aplus/web/verdict.js::docketWarning` 的第 7 支在 `cagr >= TARGET_CAGR` 时
返回 `level:'ok'` /「年化 X%，达到 20% 目标」。而 `cagr` 来自
`nDays >= 30` 的**前瞻单笔底稿** —— 30 天里 10 只股票涨 7.6% 完全正常，
于是这一支会**现实可期**地对用户说「达到 20% 目标」。

而同一屏此刻必须能说出的真话在
`data/reports/d2_signal_requirement.json`：宽度 10 / ¥5 万 / 等权下
**账户年化天花板 8.34%**，离 20% 差 **11.66pp**；即使本金 → ∞ 且榜宽 → 546
（该族最宽档），天花板也只有 **12.73%**，仍差 **7.27pp** ⇒
**没有任何 (榜宽 × 本金) 配置能到 20%**。

⇒ 两条断言各钉一个方向：
  ① 台账读数出现在判决里（不许缺席）
  ② 缺口为正时 `ok` 支不可达（第 183 层：这是**加对照臂**，不是换判据臂 ——
     对照臂 d2=null 时第 7 支的行为一个字不许变）

⚠️ 夹具一律**直读真产物**，不手写。理由见本文件末尾「为什么不许手写夹具」。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
VERDICT_JS = ROOT / "aplus" / "web" / "verdict.js"
LEDGER = ROOT / "data" / "reports" / "d2_signal_requirement.json"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="需要 node 才能测前端纯逻辑")


# ---------- node 夹具（照 tests/test_docket_warning.py 的形状，不自造） ----------

def _call(fn: str, *args) -> dict:
    """在 node 里调 `fn(...args)`，把结果转回 python。

    返回 `{"exists": true, ...}`；函数返回 null 时是 `{"exists": true, "r": None}`。
    必须能区分「函数返回了 null」与「函数不存在」，否则一条「函数名写错了」
    的测试会伪装成「判定正确」（第 120 层）。
    """
    payload = json.dumps(list(args))
    script = (
        f"const {{ {fn} }} = require({json.dumps(str(VERDICT_JS.resolve()))});"
        f"if (typeof {fn} !== 'function') {{"
        f"  console.log(JSON.stringify({{exists: false}})); process.exit(0); }}"
        f"const p = {payload};"
        f"console.log(JSON.stringify({{exists: true, r: {fn}(...p)}}));"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _verdict() -> dict:
    out = _call("d2LedgerVerdict", _ledger())
    assert out["exists"], "aplus/web/verdict.js 没有导出 d2LedgerVerdict"
    assert out["r"] is not None, "台账读数齐全却返回 null —— 判决缺席"
    return out["r"]


# ---------- ① 台账读数必须出现在判决里 ----------


def _ledger_numbers() -> dict:
    """从**产物**里读这几个数 —— **不许手抄**。

    🚨 2026-10-21：这组数**随 K 变**（K 21→63 ⇒ 缺口 11.66pp → 7.38pp，
    天花板 12.73% → 15.92%）⇒ 手抄进测试 = **必烂**。
    ⇒ 断言改成**从产物读** ⇒ 与「数字离开脚本之后必须有一条通道把它拉回来」
    这条纪律一致（仓库第 22 层：散文字数会静默过期）。
    """
    import json as _json
    from pathlib import Path as _P
    art = _P(__file__).resolve().parents[1] / "data/reports/d2_signal_requirement.json"
    h = _json.loads(art.read_text(encoding="utf-8"))["headline"]
    cap = h["account_annual_ceiling_pct"]
    return {
        "ceiling_50k": cap["at_deliverable_width_and_50k"],
        "gap_50k_pp": cap["gap_to_target_at_deliverable_width_and_50k_pp"],
        "ceiling_inf": cap["at_infinite_capital"],
        "gap_inf_pp": cap["gap_to_target_at_infinite_capital_pp"],
        "required_gross": h["required_gross_at_50k_deliverable"],
        "measured_gross": h["measured_gross_best"]["at_deliverable_width"],
    }

def test_the_verdict_carries_the_deliverable_ceiling_and_its_gap():
    """屏上必须说「天花板 8.34%、缺口 11.66pp」，而不是只说一个年化点估计。"""
    w = _verdict()
    n = _ledger_numbers()
    assert w["level"] in ("warn", "danger")
    c50 = f"{n['ceiling_50k']:.2f}"
    g50 = f"{n['gap_50k_pp']:.2f}"
    assert c50 in w["headline"] or c50 in w["detail"], (
        f"判决里没有交付档（榜宽10·¥5万）的账户年化天花板 {c50}")
    assert g50 in w["headline"] or g50 in w["detail"], (
        f"判决里没有交付档缺口 {g50}pp —— 用户看不见「离 20% 还有多远」")
    # ⇒ 语义不变量：缺口为正、天花板低于目标、所需毛超额高于实测
    assert n["gap_50k_pp"] > 0 and n["ceiling_50k"] < 20.0
    assert n["required_gross"] > n["measured_gross"]


def test_the_verdict_says_more_data_is_not_the_way_out():
    """最贵的一条：屏上今天说「再攒攒」，而台账说「攒样本不会抬高天花板」。

    `account_annual_ceiling_pct.at_infinite_capital`（宽度 546、本金 → ∞）是
    该族**上确界**：`is_the_supremum_over_widths = True`。它 12.73% < 20%
    ⇒ 缺口 7.27pp ⇒ **没有任何 (榜宽 × 本金) 配置能达标**。
    """
    w = _verdict()
    blob = w["headline"] + w["detail"]
    n = _ledger_numbers()
    ci = f"{n['ceiling_inf']:.2f}"
    gi = f"{n['gap_inf_pp']:.2f}"
    assert ci in blob, f"判决没有带上确界天花板 {ci}%（宽度546·本金→∞）"
    assert gi in blob, f"判决没有带上确界缺口 {gi}pp"
    # ⇒ 语义不变量：**上确界**仍低于目标 ⇒ 没有任何 (榜宽 × 本金) 配置能达标
    assert n["ceiling_inf"] < 20.0


def test_the_verdict_never_says_ok_while_a_positive_gap_is_on_record():
    """假绿闸：缺口为正时，`ok` 级别不可达。

    这是本文件最贵的一条 —— 它对着 `docketWarning` 第 7 支那个方向。
    """
    ledger = _ledger()
    gap = ledger["headline"]["account_annual_ceiling_pct"][
        "gap_to_target_at_deliverable_width_and_50k_pp"]
    assert gap > 0, "台账缺口非正 ⇒ 本条守卫的前提已变（请先读产物再改守卫）"
    assert _verdict()["level"] != "ok"


def test_the_verdict_refuses_to_call_a_gap_a_verdict():
    """|毛超额|/MDE < 1 ⇒ 连符号都定不了 ⇒ 不许说「达不到」，也不许说「接近」。

    载体是 `rows[*].measured_gross_over_mde_abs` 与 `rows[*].detectable`
    —— ⚠️ **不是** `measured_sign_determinable`（那个键名在产物里**不存在**，
    是散文里的词，第 168 层）。
    """
    ledger = _ledger()
    ratios = [r["measured_gross_over_mde_abs"] for r in ledger["rows"]]
    assert max(ratios) < 1.0, "五档里有一档 |g|/MDE ≥ 1 ⇒ 本守卫前提已变"
    assert not any(r["detectable"] for r in ledger["rows"])

    blob = _verdict()["headline"] + _verdict()["detail"]
    for banned in ("达不到", "接近目标", "只差"):
        assert banned not in blob, f"「{banned}」在 |g|/MDE<1 时是无信息的断言"


# ---------- ② 第 7 支必须先过台账闸；对照臂一个字不许变 ----------

def _docket(d2):
    return _call("docketWarning",
                 {"cagr": 0.25, "n_days": 483, "overall_hit_rate": 0.51,
                  "judged": 9000, "arith_ann": 0.30, "ann_vol": 0.04},
                 {}, None, d2)


def test_the_met_target_branch_is_unreachable_while_the_ledger_reports_a_gap():
    """node 实跑：`cagr = 25%`（达标）+ 完整天数 ⇒ 今天说「达到 20% 目标」。

    那个 `cagr` 来自 483 个交易日的单笔底稿，而 30~480 天里 10 只股票涨 25%
    完全可能 ⇒ 这一支**现实可期**，而台账逐字说天花板 8.34%。
    """
    got = _docket(_ledger())
    assert got["exists"]
    assert got["r"]["level"] != "ok", (
        "台账缺口 11.66pp 仍然允许屏上喊「达到 20% 目标」—— 这是活的假绿")
    g50 = f"{_ledger_numbers()['gap_50k_pp']:.2f}"
    assert g50 in got["r"]["headline"] + got["r"]["detail"]


def test_the_control_arm_without_the_ledger_is_unchanged():
    """第 183 层：这是**加对照臂**，不是换判据臂。

    d2 缺席时第 7 支必须逐字保持原样，否则「台账说了算」会退化成
    「台账一缺席就没人管」—— 那与「达标支永远不许 ok」是两件事。
    """
    got = _docket(None)
    assert got["r"]["level"] == "ok"
    assert "达到" in got["r"]["headline"]


def test_the_sample_size_branch_still_wins_over_the_ledger():
    """优先级不许被新臂挤掉：样本不足必须先说「不可解读」。

    真数据此刻 `n_days = 4` ⇒ 第 4 支先 return ⇒ 台账臂根本不该被问到。
    """
    got = _call("docketWarning",
                {"cagr": -0.7365, "n_days": 4, "judged": 116},
                {}, None, _ledger())
    assert got["r"]["level"] == "warn"
    assert "样本不足" in got["r"]["headline"]


# ---------- ③ 台账载体不许被量纲混淆（第 97/182 层） ----------

def test_the_api_payload_keeps_percentages_and_fractions_apart():
    """产物里 `headline.*` 是**百分数**、`rows[*].measured_gross_annual` 是**分数**。

    两族同名族、同一个 artifact，消费侧混读差 100 倍（`9.9471` 当小数 ⇒
    「基准年化 +994.71%」）。⇒ API 面**只暴露百分数族**，
    分数组只以**无量纲**的 `measured_gross_over_mde_abs` 出去。
    """
    from aplus.reports import read_d2_gap

    payload = read_d2_gap(ROOT / "data" / "reports")
    assert payload["status"] == "ok", payload

    ceiling = payload["account_annual_at_50k_pct"]
    assert 0 < ceiling < 100, (
        f"交付档账户年化天花板 = {ceiling} —— 百分数族不可能落在 (0,100) 之外？"
        " 若它变成了 0.0834，那就是被当分数读过一次")

    for row in payload["by_width"]:
        assert 0.0 <= row["abs_gross_over_mde"] < 10.0
        assert isinstance(row["is_detectable"], bool)


def test_the_api_payload_is_a_read_of_the_artifact_not_a_recomputation():
    """第 109 层：核对产物**只读字段**，禁止用公式重算「验证」。

    这里反过来验：API 给的每个数都必须在产物里**逐位相等**地存在。
    """
    from aplus.reports import read_d2_gap

    ledger = _ledger()
    payload = read_d2_gap(ROOT / "data" / "reports")
    ceiling = ledger["headline"]["account_annual_ceiling_pct"]
    assert payload["account_annual_at_50k_pct"] == ceiling[
        "at_deliverable_width_and_50k"]
    assert payload["gap_to_target_pp"] == ceiling[
        "gap_to_target_at_deliverable_width_and_50k_pp"]
    assert payload["account_annual_at_infinite_capital_pct"] == ceiling[
        "at_infinite_capital"]
    assert payload["gap_to_target_at_infinite_capital_pp"] == ceiling[
        "gap_to_target_at_infinite_capital_pp"]
    assert payload["required_gross_at_50k_pct"] == ledger["headline"][
        "required_gross_at_50k_deliverable"]
    assert payload["multiple_needed_at_50k"] == ledger["headline"][
        "multiple_needed_at_50k_deliverable"]


def test_a_missing_ledger_says_so_instead_of_serving_zeros():
    """缺档必须**报错式地**显形，绝不回落成 0（0 会被读成「缺口 0，已达标」）。"""
    from aplus.reports import read_d2_gap

    payload = read_d2_gap(ROOT / "data" / "nonexistent-dir-xyz")
    assert payload["status"] != "ok"
    assert payload["gap_to_target_pp"] is None


# ---------- 为什么不许手写夹具 ----------

# 手写一份 `{...台账...}` 夹具看着更干净，但它把「我以为的键名」与
# 「真实的键名」之间的差别**藏进了夹具**：键名写错 ⇒ 读出 `undefined`
# ⇒ 判决函数多半返回 null ⇒ 而「返回 null」正是**屏上什么都不显示**的那个
# 失败模式，与「判定正确」长得几乎一样（第 120 层）。
#
# 直读真产物把这一类错误**变成结构上不可能通过**：
# 键名写错 ⇒ 读到 undefined ⇒ headline 拼不出 "8.34" ⇒ 测试当场红。
