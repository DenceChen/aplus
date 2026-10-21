"""★ **目标达成进度**的守卫 —— 每日快照 + 页面 tab（2026-10-21）。

用户 2026-10-21 的原话：「web 页面加一个 tab 页面 记录目标达成页面。
记录目标达成进度，**不用每次我都问你**。」

## 这个文件守什么

| # | 守卫 | 它堵住的失效 |
|---|---|---|
| 1 | 快照里**每个数都能指到产物路径**，且**逐位等于产物字段** | 手抄 / 用公式重算（第 109 层） |
| 2 | 每个**推导数**都能被**独立重算**一遍 | 「我算了一遍」= 传闻（第 134 层） |
| 3 | **`measured_sign_determinable = false` ⇒ 目标 1 必须是「不可判定」** | 把「测不出」写成「未达」/「接近」/「已达成」（第 16 层） |
| 4 | **两个 D2 口径都在，且各带标签**；倍数那一族里**只有同口径是缺口** | 跨口径取极值 / 拿一个顶替另一个（第 143 层） |
| 5 | **缺档 ⇒ 明说缺**，绝不回落成 0 | 「没有台账」被读成「缺口是 0」（第 89 层） |
| 6 | **8 条目标一条不少** | 悄悄少一条 |
| 7 | 摘要与变化行**不含手打的数** | 散文里的数静默过期（第 73 层） |
| 8 | 屏上**不出现**「投入率」「闲置比例」 | 字段名与内容相反（`docs/TODO_deployed_idle_cash_caliber_2026-10-21.md`） |
| 9 | 目标 4/5/6 的**不可自动化部分**明写「由人判断」 | 「不知道」塌成 0（第 7 层） |
| 10 | **不许算「8 条目标的达成率」** | 手段类被算成分数（归档表 §1） |
| 11 | 端点：缺档 ⇒ 404；读不出的那份**仍出现在历史里**；中间的漏日**明写** | 空数组当正常（第 89 层） |
| 12 | 前端 JS **一个测量值都不许手打**；**不许**写「已达成 20%」 | 数字离开脚本没有通道把它拉回来 |

## ⚠️ 为什么第 1 条是**跑一遍**而不是**读快照**

读快照的守卫只对**上一次落盘**有效（层 96：产物层守卫对当前代码零覆盖）⇒
这里**每次测试都真跑一遍生成器**，拿**当场**的产物对**真产物**逐位核对。
⇒ 生产目录那份快照另有一条守卫（第 13 条）盯它有没有漂。

## ⚠️ 为什么夹具一律直读真产物

手写一份夹具会把「我以为的键名」与「真实的键名」之间的差别藏进夹具（第 120 层）
⇒ 这里**一律从产物读**，期望值**不手打**。
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── 被测脚本（按路径加载；**与仓内其它测试同一做法**） ───────────────

def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "measure_goal_progress_under_test",
        ROOT / "scripts" / "measure_goal_progress.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load_generator()

HTML = ROOT / "aplus" / "web" / "index.html"


# ── 独立量具（**不复用脚本里的函数**） ───────────────────────────────

_AT_RE = re.compile(r"^(?P<path>[^:]+?) :: (?P<jpath>.+)$")
_SEG_RE = re.compile(r"[^.\[\]]+|\[\d+\]")


def resolve_at(at: str):
    """`产物路径 :: JSON 路径` ⇒ 值。**独立实现**（不复用脚本的 `dig`）。

    ⚠️ 独立实现是刻意的：复用脚本的取数函数 ⇒ 脚本取错地方时守卫跟着取错
    ⇒ 守的是「我对路径的理解」而不是「产物里那个数是不是上游那个数」。
    """
    m = _AT_RE.match(at)
    assert m, f"出处格式不对（期望「产物路径 :: JSON 路径」）：{at!r}"
    path = ROOT / m.group("path")
    assert path.is_file(), f"出处指向的文件不存在：{path}"
    doc = json.loads(path.read_text(encoding="utf-8"))
    node = doc
    for seg in _SEG_RE.findall(m.group("jpath")):
        if seg.startswith("["):
            node = node[int(seg[1:-1])]
        else:
            node = node[seg]
    return node


def bitwise_equal(a, b) -> bool:
    """**逐位**相等（不是「差不多相等」）。

    ⚠️ 浮点用 `struct.pack` 比字节 ⇒ 差 1 ulp 也算不同。
    ⚠️ 布尔单独判：Python 里 `True == 1` ⇒ 不分开就会把布尔当数字放过。
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, float) or isinstance(b, float):
        if not (isinstance(a, float) and isinstance(b, float)):
            return False
        return struct.pack("<d", a) == struct.pack("<d", b)
    return a == b


def _run_generator(tmp_path, snapshot_date="2026-10-22", artifact_root=None):
    """真跑一遍生成器 ⇒ 拿**当场**的快照。

    ⚠️ 输出落在 `tmp_path` ⇒ **不碰生产目录**（第 132 层）。
    """
    old_root = M.ARTIFACT_ROOT
    if artifact_root is not None:
        M.ARTIFACT_ROOT = artifact_root
    try:
        out = tmp_path / f"goal_progress_{snapshot_date}.json"
        rc = M.main(["--date", snapshot_date, "--out", str(out)])
        assert rc == 0, f"生成器返回 rc={rc}"
        doc = json.loads(out.read_text(encoding="utf-8"))
    finally:
        M.ARTIFACT_ROOT = old_root
    return doc


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    """**当场**跑出来的快照（对着真产物）。"""
    tmp = tmp_path_factory.mktemp("goal_progress")
    return _run_generator(tmp)


@pytest.fixture(scope="module")
def committed():
    """生产目录里**已落盘**的那份快照（若有）。"""
    # ⚠️ glob 会把 sidecar（`….json.provenance.json`）也吃进来 ⇒ 严格文件名。
    files = sorted(p for p in (ROOT / "data" / "reports").glob("goal_progress_*.json")
                   if re.fullmatch(r"goal_progress_\d{4}-\d{2}-\d{2}\.json", p.name))
    if not files:
        pytest.skip("生产目录里还没有 goal_progress_*.json")
    path = files[-1]
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_metrics(snapshot):
    for g in snapshot["goals"]:
        for m in g.get("metrics") or []:
            if m.get("kind") == "artifact":
                yield g, m


def _derived_metrics(snapshot):
    for g in snapshot["goals"]:
        for m in g.get("metrics") or []:
            if m.get("kind") == "derived":
                yield g, m


def _goal(snapshot, gid):
    for g in snapshot["goals"]:
        if g["id"] == gid:
            return g
    raise AssertionError(f"快照里没有目标 {gid}")


# ── 1 · 每个数都能指到产物路径，且逐位等于产物字段 ──────────────────

def test_every_artifact_number_points_to_an_artifact_and_matches_it_bitwise(fresh):
    """★ 核心守卫：**每个数都直读产物**，出处可解析，且**逐位相等**。

    🚨 判据是**逐位**（`struct.pack` 比字节）不是「差不多相等」——
    差 1 ulp 的换算（第 182 层）在本仓真的发生过。
    🚨 **不许用公式重算当验证**（第 109 层）：这里比的是「产物里那个字段」
    与「快照里那个数」是不是**同一个字节序列**。
    """
    seen = 0
    for g, m in _artifact_metrics(fresh):
        assert m.get("at"), (
            f"目标 {g['id']} 的读数 `{m.get('key')}` 没有出处 ⇒ "
            "屏幕上的数离开产物就再也拉不回来了")
        got = resolve_at(m["at"])
        assert bitwise_equal(got, m["value"]), (
            f"目标 {g['id']} 的 `{m.get('key')}` 与产物**不逐位相等**：\n"
            f"  产物 {m['at']} = {got!r}\n"
            f"  快照            = {m['value']!r}")
        seen += 1
    assert seen >= 20, (
        f"只找到 {seen} 个带出处的读数 ⇒ 这个快照几乎没读产物 "
        "（守卫必须真的在守东西，第 112 层）")


def test_every_artifact_number_says_its_own_caliber(fresh):
    """**口径标签**必须跟着数字走（第 7 层：数字离开脚本要带口径）。

    🚨 只对**有口径可言**的读数要求：D1 的「名单日期」与「是否许发布」
    没有第二个口径 ⇒ 逼它们写口径就是**过严的守卫**（第 112 层），
    而过严的守卫会让人去改本来正确的代码。
    """
    caliber_free = {
        (2, "prediction_date"), (2, "gate_verdict"), (2, "publish_allowed"),
    }
    for g, m in _artifact_metrics(fresh):
        if (g["id"], m["key"]) in caliber_free:
            continue
        assert m.get("caliber"), (
            f"目标 {g['id']} 的 `{m.get('key')}` 没有口径标签 ⇒ "
            "它可能被当成另一个量的同义词读")


# ── 2 · 推导数能被独立重算 ──────────────────────────────────────────

def _recompute(verify: dict):
    """按 `verify` 配方**独立**重算一个推导数。"""
    kind = verify.get("kind")
    if kind == "count_by_column":
        import pandas as pd
        df = pd.read_parquet(ROOT / verify["file"])
        return int((df[verify["column"]] == verify["value"]).sum())
    if kind == "row_count":
        import pandas as pd
        return int(len(pd.read_parquet(ROOT / verify["file"])))
    if kind == "pred_filename_date":
        return Path(verify["file"]).stem.removeprefix("pred_")
    if kind == "list_len":
        doc = json.loads((ROOT / verify["file"]).read_text(encoding="utf-8"))
        return len(doc)
    if kind == "sum_candidates":
        doc = json.loads((ROOT / verify["file"]).read_text(encoding="utf-8"))
        return sum(len(b.get("candidates") or []) for b in doc)
    if kind == "registered_on":
        doc = json.loads((ROOT / verify["file"]).read_text(encoding="utf-8"))
        return [b.get("registered_on") for b in doc]
    if kind in ("shard_cells_sum", "shard_cells_ratio"):
        # ⚠️ 独立于脚本的取法：脚本按**严格文件名正则**筛分片，
        #    这里按「不含 .provenance.」筛 ⇒ 两条推导路径不同。
        ok = tot = 0
        for p in (ROOT / "data" / "reports").glob("dividend_pit_coverage_shard*of*.json"):
            if ".provenance." in p.name:
                continue
            cov = json.loads(p.read_text(encoding="utf-8")).get("coverage") or {}
            ok += cov.get("cells_ok") or 0
            tot += cov.get("cells_total") or 0
        if kind == "shard_cells_sum":
            return f"{ok} / {tot}"
        return (ok / tot * 100.0) if tot else None
    if kind == "glob_len":
        # ⚠️ glob 会把 sidecar 也吃进来 ⇒ 这里按**另一条**规则排除
        #    （脚本那边用的是严格文件名正则 ⇒ 两条推导路径不同）。
        return len([p for p in (ROOT / "data" / "reports").glob(verify["pattern"])
                    if ".provenance." not in p.name])
    if kind == "git_toplevel":
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    if kind == "file_exists":
        return verify["file"] if (ROOT / verify["file"]).is_file() else None
    raise AssertionError(f"未知的 verify 配方：{kind!r}")


def test_every_derived_number_is_independently_recomputable(fresh):
    """★ 推导数（次数 / 计数 / 比率）必须能被**独立重算**一遍。

    🚨 「我算了一遍」不是证据（第 134 层）⇒ 这里**重算**，且用的是
    **配方**（`verify`）而不是脚本的实现 ⇒ 脚本的算错与守卫的算错不会一起错。
    🚨 **每个推导数都必须带配方** —— 没有配方的推导数是「我说是就是」。
    """
    seen = 0
    for g, m in _derived_metrics(fresh):
        verify = m.get("verify")
        assert verify, (
            f"目标 {g['id']} 的推导数 `{m.get('key')}` 没有复核配方 ⇒ "
            "它只是「我算了一遍」")
        got = _recompute(verify)
        assert bitwise_equal(got, m["value"]), (
            f"目标 {g['id']} 的 `{m.get('key')}` 独立重算**对不上**：\n"
            f"  重算 = {got!r}\n  快照 = {m['value']!r}\n"
            f"  配方 = {verify!r}")
        seen += 1
    assert seen >= 8, f"只找到 {seen} 个可复核的推导数 ⇒ 守卫几乎没在守东西"


# ── 3 · 「不可判定」不许被写成达成/未达 ─────────────────────────────

def test_goal_1_is_undecidable_when_the_sign_is_not_determinable(fresh):
    """★ `measured_sign_determinable = false` ⇒ 目标 1 **必须**是「不可判定」。

    🚨 这是本仓最贵的一种错：**方向偏乐观**（「测不出」被说成「做不到」
    ⇒ 会误导人放弃**唯一**能解锁判决的加数据）。
    🚨 也不许写成「已达成」—— 那是**方向偏危险的另一种**。
    """
    g = _goal(fresh, 1)
    sign = None
    for m in g["metrics"]:
        if m["key"] == "measured_sign_determinable":
            sign = m["value"]
    assert sign is False, (
        f"这一轮 `measured_sign_determinable` = {sign!r}（不是 false）⇒ "
        "本守卫的前提不成立；若产物真的变了，请改**产物**，不要改判据")
    assert g["status"] == "UNDECIDABLE", (
        f"目标 1 的状态是 `{g['status']}`，而符号不可定 ⇒ "
        "**必须**是 UNDECIDABLE（不许写「未达」，也不许写「已达成」）")
    assert "不可判定" in g["status_label"], (
        f"状态标签是 {g['status_label']!r} ⇒ 屏上读不出「这是测不出」")
    # ⚠️ **分支名**也要钉住：状态「不可判定」有**两条不同的路径**
    #    （符号不可定 / c < T ≤ U）⇒ 只钉状态的话，
    #    「把符号那一支的判据写坏」这条变异会**存活**（落进另一条同样诚实的分支）。
    # 🚨 为什么不钉「理由里含某句」：产物的 `cases` 引文里**也**含那句话
    #    ⇒ 那个子串在两条分支里都成立 ⇒ 钉它等于没钉（第 13 层）。
    assert g.get("verdict_branch") == "SIGN_NOT_DETERMINABLE", (
        f"目标 1 的判定分支是 `{g.get('verdict_branch')}`，"
        "而符号不可定 ⇒ 必须是 `SIGN_NOT_DETERMINABLE`")


def test_goal_1_never_says_achieved_or_not_achieved(fresh):
    """目标 1 的**理由文本**里不许出现「已达成」「达不到」「接近」作为结论。

    🚨 产物自己逐字写着「既不许说『达不到』，也不许说『接近』」⇒
    这里**逐字**检查那三个词**不以结论的身份**出现。
    ⚠️ 产物引文里会出现这三个词（是**禁令**的引文）⇒ 所以检查的是
    「理由文本里有没有**把它们当成结论说出去**的句子」——
    判据：这三个词**不许**出现在「不许说」以外的上下文里。
    """
    g = _goal(fresh, 1)
    reason = g.get("reason") or ""
    for phrase in ("已达成", "达不到", "接近"):
        # 只允许出现在引用禁令的句子里（前面有「不许说」）
        for m in re.finditer(re.escape(phrase), reason):
            window = reason[max(0, m.start() - 12):m.start()]
            assert "不许说" in window or "不许把" in window, (
                f"目标 1 的理由里出现了 `{phrase}`，而它不是禁令的引文：\n"
                f"  …{reason[max(0, m.start() - 30):m.start() + 20]}…")
    assert "测不出" in reason or "不可判定" in reason, (
        "目标 1 的理由里没有「测不出 / 不可判定」⇒ 屏上会读成某个结论")


def test_no_goal_claims_the_target_is_achieved(fresh):
    """**任何**一条目标都不许是「已达成 20%」。

    🚨 现状：目标 1 的 c < T ≤ U ⇒ **测不出**；目标 2 的闸门未放行。
    ⇒ 「已达成」只可能来自**编造**或**判据被改**。
    """
    for g in fresh["goals"]:
        if g["id"] in (7, 8):
            continue          # 这两条判的是「位置」与「授权已落纸」，不是收益率
        assert g["status"] != "ACHIEVED", (
            f"目标 {g['id']} 判成了「已达成」⇒ 当前没有任何一条交付目标达成过")


# ── 4 · 两个 D2 口径都在且带标签；倍数只有同口径是缺口 ─────────────

def test_both_d2_calibers_are_present_with_labels(fresh):
    """★ **两个口径都在**（实盘档 / 理想档），**各带标签**。

    🚨 只给一个 ⇒ 用户看到的缺口少了一截（理想档不含整手与闲置现金）
    或拿到的是**已被取代的配置**的数（第 143 层）。
    """
    g = _goal(fresh, 1)
    calibers = g.get("calibers") or []
    assert len(calibers) >= 2, f"目标 1 只有 {len(calibers)} 个口径 ⇒ 少于两个"
    keys = [c["key"] for c in calibers]
    assert "deployed_live_k63" in keys, f"缺实盘档：{keys}"
    assert "ideal_ceiling_k63" in keys, f"缺理想档：{keys}"
    for c in calibers:
        assert c.get("name"), f"口径 `{c['key']}` 没有名字 ⇒ 屏上是一个无主的数"
        assert c.get("role"), f"口径 `{c['key']}` 没有角色说明"
        assert c.get("honest_boundary"), f"口径 `{c['key']}` 没有诚实边界"
        assert c.get("metrics"), f"口径 `{c['key']}` 没有读数"


def test_the_two_d2_calibers_are_not_the_same_quantity(fresh):
    """🚨 两个口径**不许是同一个量**（第 7 层）。

    判据：两个口径的**账户年化**读数**不相等** —— 若相等，说明有人把
    同一个数贴了两个标签。
    """
    g = _goal(fresh, 1)
    vals = {}
    for c in g["calibers"]:
        for m in c.get("metrics") or []:
            if m["key"] in ("account_cagr_annual", "account_annual_at_50k_pct"):
                vals[c["key"]] = m["value"]
    assert len(vals) == 2, f"两个口径的年化读数没都取到：{vals}"
    a, b = vals["deployed_live_k63"], vals["ideal_ceiling_k63"]
    assert a != b, (
        "实盘档与理想档的账户年化**逐位相同** ⇒ 这不是两个口径，"
        "是把同一个数贴了两个标签（第 7 层）")


def test_only_the_same_caliber_multiple_is_the_gap(fresh):
    """★ 「还差几倍」**只有同口径那一列是缺口**。

    🚨 台账自己标 `cross_caliber.is_the_gap = false`
    （分子是 K=63 的要求、分母是 K=21 研究臂的实测 ⇒ K 与信号两处口径都不同）。
    ⇒ 把 2.35× 当缺口显示就是**错的**（第 7 层）。
    """
    g = _goal(fresh, 1)
    mults = g.get("multiples") or {}
    cols = {c["key"]: c for c in mults.get("columns") or []}
    assert "same_caliber" in cols and "cross_caliber" in cols, (
        f"倍数那一族缺列：{list(cols)}")
    assert cols["same_caliber"]["is_the_gap"] is True, (
        "同口径那一列没有标成缺口 ⇒ 屏上的「还差几倍」会取错列")
    assert cols["cross_caliber"]["is_the_gap"] is False, (
        "🚨 跨口径那一列被标成了缺口 —— 台账自己写着它 `is_the_gap = false`")
    # 缺口那一列的值必须**逐位**等于台账的同口径倍数
    ledger = json.loads(
        (ROOT / "data/reports/d2_signal_requirement.json").read_text(encoding="utf-8"))
    truth = ledger["headline"]["multiple_needed_at_50k_deliverable_by_caliber"]
    assert bitwise_equal(cols["same_caliber"]["metric"]["value"],
                          truth["same_caliber"]["multiple_needed"]), (
        "屏上那个「还差几倍」与台账的同口径倍数**不逐位相等**")
    assert bitwise_equal(cols["cross_caliber"]["metric"]["value"],
                          truth["cross_caliber"]["multiple_needed"]), (
        "跨口径那一列的数与台账**不逐位相等**")
    assert cols["cross_caliber"]["metric"]["value"] != cols["same_caliber"]["metric"]["value"], (
        "🚨 两个倍数逐位相同 ⇒ 跨口径那一列被换成了同口径的值（口径混比）")


def test_the_gap_multiple_shown_on_the_page_is_the_same_caliber_one(fresh):
    """🚨 **屏上那个倍数**必须是同口径的（1.79×），**不是**跨口径的（2.35×）。

    判据：**不许**在页面/脚本里手打任何一个倍数；
    屏上的倍数**只能**来自后端标了 `is_the_gap = true` 的那一列。
    """
    region = _goal_progress_js_region()
    assert "is_the_gap" in region, (
        "前端 JS 里没有 `is_the_gap` ⇒ 屏上那一列是前端自己挑的 ⇒ "
        "跨口径那一列有可能被显示成缺口")
    for literal in ("1.79", "2.34", "2.35"):
        assert literal not in region, (
            f"前端 JS 里手打了 `{literal}` ⇒ 数字离开脚本没有通道把它拉回来")


# ── 5 · 缺档 ⇒ 明说缺，不回落成 0 ──────────────────────────────────

def test_a_missing_artifact_yields_null_and_a_reason_never_zero(tmp_path):
    """★ **缺档 ⇒ `value: null` + 原因**，**绝不回落成 0**。

    🚨 0 会被读成「缺口 0，已达标」—— 而「没有台账」与「缺口是 0」
    是**相反**的结论（第 89 层）。
    ⚠️ 夹具用**空的临时产物根** ⇒ 生产目录一个字节都不碰（第 132 层）。
    """
    empty = tmp_path / "no_artifacts"
    (empty / "data" / "reports").mkdir(parents=True)
    snap = _run_generator(tmp_path / "out", artifact_root=empty)
    assert (empty / "data" / "reports").exists()
    n_missing = 0
    for g, m in _artifact_metrics(snap):
        if m.get("status") != "missing":
            continue
        n_missing += 1
        assert m["value"] is None, (
            f"目标 {g['id']} 的 `{m.get('key')}` 在产物缺失时给出了 "
            f"{m['value']!r} ⇒ **回落成了 0/别的数**")
        assert m["display"] == "缺失", (
            f"目标 {g['id']} 的 `{m.get('key')}` 的显示是 {m['display']!r} ⇒ "
            "屏上会读成一个数")
        assert m.get("reason"), (
            f"目标 {g['id']} 的 `{m.get('key')}` 缺档但没有原因 ⇒ "
            "「没跑到」与「数据坏了」长得一模一样")
    assert n_missing >= 10, (
        f"产物全缺时只有 {n_missing} 个读数报缺 ⇒ 大概率是**回落成了别的数**")


def test_a_missing_artifact_does_not_silently_flip_a_verdict(tmp_path):
    """🚨 缺档 ⇒ 状态**不许**变成「未达 / 不可判定」之外的任何结论。

    判据：产物全缺时目标 1 的状态必须是 `UNDECIDABLE`，
    且理由里必须说「读不出来」。
    """
    empty = tmp_path / "no_artifacts2"
    (empty / "data" / "reports").mkdir(parents=True)
    snap = _run_generator(tmp_path / "out2", artifact_root=empty)
    g = _goal(snap, 1)
    assert g["status"] == "UNDECIDABLE", (
        f"产物缺失时目标 1 的状态是 `{g['status']}` ⇒ "
        "「读不出来」被渲染成了一个方向性结论")
    assert "读不出来" in (g.get("reason") or ""), (
        f"理由里没有「读不出来」：{g.get('reason')!r}")


# ── 6 · 8 条目标一条不少 ───────────────────────────────────────────

def test_the_eight_goals_are_all_present(fresh):
    """★ 8 条目标**一条不少**，且逐条对应用户原话。"""
    ids = [g["id"] for g in fresh["goals"]]
    assert ids == [1, 2, 3, 4, 5, 6, 7, 8], f"目标清单不是 1..8：{ids}"
    for g in fresh["goals"]:
        assert g.get("title"), f"目标 {g['id']} 没有标题"
        assert g.get("goal_text"), f"目标 {g['id']} 没有用户原话"
        assert g.get("status") in M.ALL_STATUSES, (
            f"目标 {g['id']} 的状态 `{g.get('status')}` 不在四态词表里")
        assert g.get("registry_class"), f"目标 {g['id']} 没有归档类别"
        assert g.get("registry_at"), f"目标 {g['id']} 没有归档出处"
        assert g.get("criterion"), f"目标 {g['id']} 没有判据"
        assert g.get("honest_boundary"), f"目标 {g['id']} 没有诚实边界"


def test_no_goal_carries_two_readings_with_the_same_key(fresh):
    """🚨 同一个目标里**不许有两个读数用同一个 key**。

    症状：页面上两行同名，第二行把第一行盖掉 ⇒ 读者看到的是**另一个数**，
    而两边都「有出处」⇒ 看不出来。
    """
    for g in fresh["goals"]:
        keys = [m["key"] for m in (g.get("metrics") or [])]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        assert not dupes, f"目标 {g['id']} 里有重名的读数：{dupes}"


def test_the_summary_line_is_the_rendered_one(fresh):
    """🚨 快照里的摘要**必须**就是渲染器算出来的那一句。

    判据：快照里的 `summary_line` 逐位等于 `render_summary(goals)`
    ⇒ **手改过摘要**（或渲染器与写入层脱钩）当场露馅。
    """
    assert fresh["summary_line"] == M.render_summary(fresh["goals"]), (
        "快照里的 summary_line 与渲染器算出来的不一致 ⇒ "
        "有人手改过它，或写入层与渲染器脱钩了")


def test_the_goal_1_status_rule_reaches_every_branch():
    """★ **能力守卫**：D2 的四条分支**都可达**，且各给出**它自己**的状态。

    🚨 为什么必须造**分岔夹具**：真实数据落在「符号不可定」那一支 ⇒
    「达成」与「未达」两支在真实产物上**不可达** ⇒ 只拿真实产物测，
    这两支的判据**改坏了也没人喊**（第 172 层：变异意图与判据对不上）。
    ⇒ 这里用**合成输入**把四条分支各走一遍。

    ⚠️ 合成输入的数是**判据的输入**，不是测量值 ⇒ 不违反「不许手打测量值」
    （本仓的规矩是「不许手抄**产物的数**」，而这里是**夹具**）。
    """
    cases = [
        # (cagr, mde, upper, target, sign_ok, 期望状态, 期望分支)
        (0.25, 0.05, 0.30, 0.20, True, "ACHIEVED", "C_AT_OR_ABOVE_TARGET"),
        (0.15, 0.05, 0.18, 0.20, True, "NOT_ACHIEVED", "U_BELOW_TARGET"),
        (0.15, 0.05, 0.25, 0.20, True, "UNDECIDABLE", "C_BELOW_TARGET_WITHIN_U"),
        (0.15, 0.20, 0.25, 0.20, False, "UNDECIDABLE", "SIGN_NOT_DETERMINABLE"),
        (None, None, None, 0.20, None, "UNDECIDABLE", "UNREADABLE"),
    ]
    for cagr, mde, upper, target, sign_ok, want_status, want_branch in cases:
        status, reason, branch = M._judge_d2(cagr, mde, upper, target, sign_ok, None)
        assert (status, branch) == (want_status, want_branch), (
            f"输入 (cagr={cagr}, mde={mde}, upper={upper}, target={target}, "
            f"sign_ok={sign_ok}) 落到了 ({status}, {branch})，"
            f"期望 ({want_status}, {want_branch})")
        assert reason, "分支必须带一句理由"


def test_the_goal_registry_cites_the_single_archive_table(fresh):
    """8 条目标的**唯一出处** = 决策登记里那张表（不许另抄一份清单）。"""
    assert fresh["goal_registry"] == "docs/DELIVERY_DECISIONS_2026-10-18.md §1"
    for g in fresh["goals"]:
        assert g["registry_at"].startswith("docs/DELIVERY_DECISIONS_2026-10-18.md §1"), (
            f"目标 {g['id']} 的归档出处是 `{g['registry_at']}` ⇒ "
            "另抄了一份清单（形状 17：同一语义被处理 N−1 次）")


# ── 7 · 摘要与变化行不含手打的数 ───────────────────────────────────

def test_the_summary_and_change_lines_contain_no_hand_typed_numbers(fresh):
    """★ 摘要与变化行**不许含测量值** —— 只许有日期与 `D1`/`D2` 编号。

    🚨 手打进散文的数会**静默过期**（第 73 层）。
    ⇒ 判据：剥掉 ISO 日期与 `D1`/`D2` 之后，**不许剩下任何数字**。
    """
    for label, text in (("summary_line", fresh["summary_line"]),
                        ("change.line", (fresh.get("change") or {}).get("line") or "")):
        stripped = re.sub(r"\d{4}-\d{2}-\d{2}", "", text)
        stripped = stripped.replace("D1", "").replace("D2", "")
        assert not re.search(r"\d", stripped), (
            f"`{label}` 里出现了数字 ⇒ 手打值进散文了（第 73 层）：\n"
            f"  {text!r}")


def test_the_summary_line_is_generated_from_the_statuses(fresh):
    """🚨 摘要**必须**由状态码生成（不许手打一句总结）。

    判据：把快照里的状态改成别的，摘要**必须跟着变**
    ⇒ 手打的摘要会当场露馅。
    """
    goals = [dict(g) for g in fresh["goals"]]
    mutated = [dict(g) for g in goals]
    mutated[0]["status_label"] = "（被改过的标签）"
    assert M.render_summary(goals) != M.render_summary(mutated), (
        "改了状态标签而摘要没变 ⇒ 摘要是手打的，不是从状态生成的")


# ── 8 · 屏上不出现「投入率」「闲置比例」 ───────────────────────────

def test_the_snapshot_never_carries_the_idle_cash_or_investment_ratio(fresh):
    """🚨 那两个字段**名字与内容相反** ⇒ 一个都不许端出去。

    `docs/TODO_deployed_idle_cash_caliber_2026-10-21.md`：
    `idle_cash_ratio_mean` 量的是「累计现金 ÷ 原始本金」，
    `lot_investable_rate` 的名字是「买得起 1 手的比例」而内容是「实投比例」。
    """
    # ⚠️ 只扫**结构化那一半**（goals）：诚实边界那一段**提到**这两个词
    #    是在声明「不许端出去」⇒ 把它一起扫就是**过严的守卫**（第 112 层）。
    blob = json.dumps(fresh["goals"], ensure_ascii=False)
    for banned in ("idle_cash", "lot_investable", "投入率", "闲置比例",
                   "idle_cash_ratio_mean", "idle_cash_decomposition"):
        assert banned not in blob, (
            f"快照里出现了 `{banned}` ⇒ 屏上会端出一个名字与内容相反的数")


# ── 9 · 目标 4/5/6 的不可自动化部分明写「由人判断」 ────────────────

@pytest.mark.parametrize("gid", [4, 5, 6])
def test_goals_4_5_6_mark_the_non_automatable_part_as_manual(fresh, gid):
    """★ 「不知道」不许塌成 0、不许假装有（第 7 层）。

    判据：`manual` 块**存在**、值恒为 `null`、显示恒为「由人判断」、
    且**给了判据**（不许只说「不知道」）。
    """
    g = _goal(fresh, gid)
    manuals = g.get("manual") or []
    assert manuals, f"目标 {gid} 没有「由人判断」那一块"
    for m in manuals:
        assert m["kind"] == "manual", f"目标 {gid} 的 manual 块 kind 不对"
        assert m["value"] is None, (
            f"目标 {gid} 的「由人判断」给出了值 {m['value']!r} ⇒ "
            "「不知道」被塌成了数")
        assert m["display"] == "由人判断", (
            f"目标 {gid} 的「由人判断」显示是 {m['display']!r}")
        assert m.get("criteria"), f"目标 {gid} 的「由人判断」没有判据"
    assert g.get("automatable"), f"目标 {gid} 没有「可自动化的部分」那一块"
    assert g["automatable"].get("caveat"), (
        f"目标 {gid} 的可自动化部分没有 caveat ⇒ "
        "「跑偏率是防跑偏的读数，不是成就」这句话会丢")


# ── 10 · 不许算「8 条目标的达成率」 ─────────────────────────────────

def test_the_snapshot_does_not_compute_an_overall_completion_rate(fresh):
    """🚨 归档表把分母定死 = D1 + D2 两项；**手段类不产生分数**。

    判据：快照里**没有**任何「达成率 / 完成度」字段。
    """
    blob = json.dumps(fresh, ensure_ascii=False)
    for banned in ("达成率", "完成率", "完成度", "overall_completion",
                   "completion_rate", "goal_completion"):
        assert banned not in blob, (
            f"快照里出现了 `{banned}` ⇒ 手段类被算成了分数（归档表 §1）")


# ── 11 · 端点：缺档 404；读不出的仍在历史里；漏日明写 ──────────────

def _client(tmp_path):
    from fastapi.testclient import TestClient

    from aplus.webapp import create_app

    class _LLM:
        def chat(self, *a, **k):
            return "x"

        def chat_stream(self, *a, **k):
            yield "x"

    app = create_app(tmp_path, llm=_LLM())
    return TestClient(app)


def test_the_endpoint_404s_when_there_is_no_snapshot_at_all(tmp_path):
    """🚨 **一份都没有 ⇒ 404 + 一句原因**，不许返回空数组当正常。

    空数组在界面上与「判决读不出来」长得分毫不差（形状 23 的方向）。
    """
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    tc = _client(tmp_path)
    resp = tc.get("/api/goal-progress")
    assert resp.status_code == 404, (
        f"没有快照时返回了 {resp.status_code} ⇒ 空数组被当成了正常")
    assert "goal_progress" in resp.json().get("detail", ""), (
        "404 的理由里没有说清缺哪个文件")


def test_the_endpoint_returns_the_latest_snapshot_and_the_history(tmp_path):
    """端点返回**最新**快照 + **历日历史**（各带生成时间）。"""
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    snap = _run_generator(tmp_path, snapshot_date="2026-10-21")
    (reports / "goal_progress_2026-10-21.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    older = dict(snap)
    older["snapshot_date"] = "2026-10-20"
    older["generated_at"] = "2026-10-20T22:00:00"
    (reports / "goal_progress_2026-10-20.json").write_text(
        json.dumps(older, ensure_ascii=False, indent=2), encoding="utf-8")
    tc = _client(tmp_path)
    resp = tc.get("/api/goal-progress")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["latest"]["snapshot_date"] == "2026-10-21"
    assert len(data["history"]) == 2
    for h in data["history"]:
        assert h["generated_at"], f"{h['date']} 没有生成时间"
        assert h["summary_line"], f"{h['date']} 没有摘要"


def test_the_endpoint_keeps_an_unreadable_snapshot_in_the_history(tmp_path):
    """🚨 读不出来的那份**仍然出现在历史里**，带错误原文。

    跳过它 = 把「那天没跑到」读成「那天没变化」（第 89 层）。
    """
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    snap = _run_generator(tmp_path, snapshot_date="2026-10-22")
    (reports / "goal_progress_2026-10-22.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    (reports / "goal_progress_2026-10-23.json").write_text(
        "{ 这不是 JSON", encoding="utf-8")
    tc = _client(tmp_path)
    data = tc.get("/api/goal-progress").json()
    days = [h["date"] for h in data["history"]]
    assert "2026-10-23" in days, f"读不出来的那份被静默跳过了：{days}"
    bad = [h for h in data["history"] if h["date"] == "2026-10-23"][0]
    assert bad["readable"] is False
    assert bad["error"], "读不出来的快照没有错误原文"
    assert data["latest_note"], "最新一份读不出来 ⇒ 没有 `latest_note` 说明"


def test_the_endpoint_discloses_the_calendar_days_it_missed(tmp_path):
    """🚨 中间漏了几天 ⇒ 那些天进 `gaps` ⇒ 不许让时间轴看起来连续。"""
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    snap = _run_generator(tmp_path, snapshot_date="2026-10-24")
    for day in ("2026-10-20", "2026-10-21", "2026-10-24"):
        d = dict(snap)
        d["snapshot_date"] = day
        (reports / f"goal_progress_{day}.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tc = _client(tmp_path)
    data = tc.get("/api/goal-progress").json()
    assert data["gaps"] == ["2026-10-22", "2026-10-23"], data["gaps"]


# ── 12 · 前端：一个测量值都不许手打；不许写「已达成 20%」 ──────────

def _goal_progress_js_region() -> str:
    """抽出「目标进度」那段 **JS**。

    ⚠️ **不含 CSS**：CSS 里的浮点是**排版值**（`line-height: 1.6`、
    `font-size: 12.5px`）⇒ 把它们一起禁就是**过严的守卫**（第 112 层），
    而过严的守卫会让人去改本来正确的代码。
    ⇒ CSS 另有一条**窄**守卫（见 `test_the_goal_progress_css_holds_no_baked_number`）。
    """
    src = HTML.read_text(encoding="utf-8")
    start = src.find("function goalMetricRow")
    assert start > 0, "index.html 里找不到 goalMetricRow ⇒ 前端那段被删了"
    end = src.find("/* ── 转债纸面账户", start)
    assert end > start, "找不到目标进度段的结尾标记"
    return src[start:end]


def _goal_progress_css_region() -> str:
    """抽出「目标进度」那段 CSS。"""
    src = HTML.read_text(encoding="utf-8")
    css_start = src.find(".goal-grid {")
    assert css_start > 0, "index.html 里找不到 .goal-grid ⇒ 样式段被删了"
    css_end = src.find("</style>", css_start)
    assert css_end > css_start
    return src[css_start:css_end]


def test_the_frontend_does_not_hand_type_any_measured_number():
    """★ 前端**一个测量值都不许手打**。

    🚨 数字离开脚本必须有一条通道把它拉回来 ⇒ 数字只许来自 API 的 `display`。
    判据：目标进度那两段里**不许出现任何浮点字面量**（`12.5` 这种）。
    ⚠️ 只禁**浮点**：整数（像素、列数）不是测量值，禁了就是**过严的守卫**
    （第 112 层：过严的守卫会让人去改本来正确的代码）。
    """
    region = _goal_progress_js_region()
    floats = re.findall(r"(?<![\w.])\d+\.\d+(?![\w.])", region)
    assert not floats, (
        f"目标进度那段 JS 里有手打的浮点字面量 {floats} ⇒ "
        "数字离开脚本没有通道把它拉回来")


def test_the_goal_progress_css_holds_no_baked_number():
    """🚨 CSS 里**不许**把一个测量值烤进 `content:`（那是唯一能藏数的口子）。

    ⚠️ 只查 `content:` —— `line-height: 1.6` / `font-size: 12.5px` 是**排版值**
    ⇒ 一起禁就是过严（第 112 层）。
    """
    css = _goal_progress_css_region()
    for m in re.finditer(r"content\s*:\s*([^;]+);", css):
        assert not re.search(r"\d", m.group(1)), (
            f"CSS 的 content: 里出现了数字 ⇒ 有测量值被烤进样式：{m.group(0)!r}")


def _render_goal_progress_js_in_node(payload: dict) -> dict:
    """把「目标进度」那几段**纯渲染函数**交给 node 真跑一遍 ⇒ 拿渲染后的 HTML。

    🚨 为什么不用「源码里有某个串」当判据：那是第 13 层（子串断言会被注释满足）。
    ⇒ 这里**跑**：真函数、真夹具、真输出 ⇒ 断言落在**行为**上。
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("本机没有 node ⇒ 前端渲染闸门跑不了")
    src = HTML.read_text(encoding="utf-8")
    start = src.find("function goalMetricRow")
    end = src.find("async function loadGoalProgress")
    assert start > 0, "index.html 里找不到 goalMetricRow"
    assert end > start, "index.html 里找不到目标进度段的结尾标记"
    region = src[start:end]
    # ⚠️ `esc` **从 index.html 里原样取出来** ⇒ 不用在 Python 里重新转义一遍
    #    （转义出错过一次：单引号被吃掉 ⇒ 整个文件语法错）。
    esc_line = re.search(r"^const esc = .*$", src, re.M)
    assert esc_line, "index.html 里找不到 `esc` ⇒ 夹具前提错了"
    driver = (
        esc_line.group(0) + "\n"
        + region + "\n"
        "const payload = JSON.parse(process.argv[2]);\n"
        "console.log(JSON.stringify({mults: goalMultiplesHtml(payload.multiples),"
        " card: goalCardHtml(payload.goal)}));\n"
    )
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(driver)
        path = f.name
    try:
        r = subprocess.run([node, path, json.dumps(payload, ensure_ascii=False)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"node 跑渲染器失败：{r.stderr}"
        return json.loads(r.stdout)
    finally:
        Path(path).unlink(missing_ok=True)


def test_the_page_marks_only_the_same_caliber_multiple_as_the_gap(fresh):
    """★ **行为**守卫：屏上「是缺口」的那个标记，只落在**同口径**那一列。

    🚨 静态断言「源码里有 `is_the_gap`」会被注释满足（第 13 层）
    ⇒ 这里**真跑一遍渲染器**，看**输出**里哪一列被标成缺口。
    """
    g = _goal(fresh, 1)
    out = _render_goal_progress_js_in_node({"multiples": g["multiples"], "goal": g})
    mults = out["mults"]
    chunks = mults.split("<div class=")
    gap = [c for c in chunks if c.startswith('"goal-mult is-gap"')]
    notgap = [c for c in chunks if c.startswith('"goal-mult not-gap"')]
    assert len(gap) == 1, f"被标成「是缺口」的列数 = {len(gap)}（必须恰好 1）"
    assert len(notgap) == 1, f"被标成「不是缺口」的列数 = {len(notgap)}（必须恰好 1）"
    assert "同口径" in gap[0], "被标成缺口的那一列**不是**同口径那一列"
    assert "跨口径" in notgap[0], "跨口径那一列没有被标成「不是缺口」"
    same = _goal(fresh, 1)["multiples"]["columns"][0]["metric"]["display"]
    cross = _goal(fresh, 1)["multiples"]["columns"][1]["metric"]["display"]
    assert same in gap[0], f"缺口那一列显示的不是同口径的数（{same}）"
    assert cross in notgap[0], f"跨口径那一列显示的不是跨口径的数（{cross}）"


def test_the_deep_link_allowlist_matches_the_tab_buttons():
    """🚨 深链白名单 `TABS` 必须与**实际的 tab 按钮**逐项一致。

    🚨 起因（2026-10-21 实测）：加了「目标进度」这个 tab 之后，
    `TABS` 名单**忘了加** ⇒ 深链 `#/goal` **静默回落概览**
    ⇒ 页面完全正常、console 无报错、pytest 全绿 ——
    **只有深链这一条路坏了**，而没有任何东西会喊。
    ⇒ 这条守卫把两张名单钉在一起 ⇒ **下一个 tab 忘了改就当场红**。
    """
    html = HTML.read_text(encoding="utf-8")
    # 🚨 **先把注释剥掉**：否则文档里任何一句「按钮长这样」都会满足这个正则
    #    （第 13 层：子串断言 + 注释 = 守卫可被文档满足）。
    #    —— 实测：这条守卫第一版就抓到了**我自己刚写的注释**。
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    panels = re.findall(r'<button[^>]*class="tab"[^>]*data-panel="([^"]+)"', html)
    assert panels, "夹具前提错：一个 tab 按钮都没抽到"
    m = re.search(r"const TABS = \[(.*?)\];", html, re.S)
    assert m, "index.html 里找不到 TABS 名单"
    listed = re.findall(r"'([^']+)'", m.group(1))
    assert set(listed) == set(panels), (
        f"TABS 名单 {sorted(listed)} 与实际 tab 按钮 {sorted(panels)} 不一致 ⇒ "
        "深链会静默回落到概览（页面不报错 ⇒ 没人会发现）")


def test_a_text_valued_reading_gets_its_own_row(fresh):
    """★ **行为**守卫：文本型读数（判决名 / 理由 / 路径）**另起一行**。

    🚨 症状（2026-10-21 浏览器实测）：那一列是 `white-space: nowrap`
    ⇒ 一句长文本被压成**逐字竖排**（实测「哪一档才可交付」那一格竖成了一列字）
    ⇒ 读不出来，而**没有任何报错**。
    ⇒ 判据是**后端给的 unit**，不是前端量长度（前端一个数都不算）。
    """
    g = _goal(fresh, 1)
    out = _render_goal_progress_js_in_node({"multiples": g["multiples"], "goal": g})
    card = out["card"]
    # 找一个 unit 是 text 的读数（判决名 / 理由 / 路径）
    # ⚠️ **口径块里的读数也要算** —— 目标 1 的文本型读数在理想档那块里
    text_metrics = [m for m in g["metrics"]
                    if m.get("unit") in ("text", "lines", "json", "list")]
    for c in (g.get("calibers") or []):
        text_metrics += [m for m in (c.get("metrics") or [])
                         if m.get("unit") in ("text", "lines", "json", "list")]
    assert text_metrics, "夹具前提错：目标 1 里没有文本型读数"
    for m in text_metrics:
        assert "is-text" in card, (
            f"文本型读数 `{m['key']}` 没有被标成 is-text ⇒ "
            "它会被压进 nowrap 那一列 ⇒ 长文本竖排（浏览器实测过的症状）")


def test_the_frontend_never_claims_the_target_is_achieved():
    """🚨 页面**不许**说「已达成 20%」—— 现状是**不可判定**（测不出）。"""
    region = _goal_progress_js_region()
    for banned in ("已达成 20", "已达到 20", "达成 20%", "目标达成 20"):
        assert banned not in region, f"页面里出现了 `{banned}`"
    html = HTML.read_text(encoding="utf-8")
    assert 'id="tab-goal"' in html, "index.html 里没有「目标进度」这个 tab"
    assert 'id="panel-goal"' in html, "index.html 里没有「目标进度」这个面板"
    assert "loadGoalProgress" in html, "index.html 里没有目标进度的加载函数"
    assert "goal: loadGoalProgress" in html, "loaders 里没有挂上目标进度"


def test_the_inline_scripts_of_the_page_still_parse():
    """🚨 同名 `const` 是**解析期**错误 ⇒ pytest 全绿而页面永远停在「加载…」。

    判据：把 index.html 的**每一段**内联 `<script>` 交给 `node --check`。
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("本机没有 node ⇒ 前端语法闸门跑不了")
    src = HTML.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", src, re.S)
    assert blocks, "index.html 里没有内联脚本块 ⇒ 夹具前提错了"
    import tempfile
    for i, b in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(b)
            p = f.name
        try:
            r = subprocess.run([node, "--check", p], capture_output=True, text=True)
            assert r.returncode == 0, (
                f"第 {i} 段内联脚本解析失败 ⇒ 页面会停在「加载…」：\n{r.stderr}")
        finally:
            Path(p).unlink(missing_ok=True)


# ── 13 · 生产目录那份快照没有漂 ───────────────────────────────────

def test_the_committed_snapshot_has_not_drifted_from_the_artifacts(committed):
    """★ 生产目录里**已落盘**的那份，每个数仍与产物**逐位相等**。

    🚨 产物会被重跑覆盖，而快照不会 ⇒ 这一条是「那份存档还成不成立」的判据。
    ⚠️ 缺档时（读不到）**不回落成 0** ⇒ 只核对读得到的那几条。
    """
    n = 0
    for g, m in _artifact_metrics(committed):
        if m.get("status") != "ok":
            continue
        got = resolve_at(m["at"])
        assert bitwise_equal(got, m["value"]), (
            f"**已落盘的快照**与产物漂了：目标 {g['id']} 的 `{m.get('key')}`\n"
            f"  产物 {m['at']} = {got!r}\n"
            f"  快照            = {m['value']!r}\n"
            "⇒ 那个数要么被重跑了，要么被人手改过")
        n += 1
    assert n >= 20, f"只核对了 {n} 条 ⇒ 这条守卫几乎没在守东西"


# ── 14 · 溯源：产物登记 + 盖章 ─────────────────────────────────────

def test_the_snapshot_resolves_to_its_generator():
    """🚨 盖了章却解析不出生成器 ⇒ 按仓内规矩**不可引用**。

    判据：`aplus/provenance.py::resolve_generator("goal_progress_<日期>.json")`
    必须给出生成器 ⇒ 走**模式表**（逐名登记就是形状 17：每天漏一行）。
    """
    from aplus import provenance

    gen = provenance.resolve_generator("goal_progress_2026-10-21.json")
    assert gen == "scripts/measure_goal_progress.py", (
        f"快照解析出的生成器是 {gen!r} ⇒ 它按仓内规矩不可引用")
    assert provenance.resolve_generator("goal_progress_2099-01-01.json") == gen, (
        "模式表只认了某一个日期 ⇒ 明天那份就解析不出来了（形状 17）")


def test_the_generator_freezes_its_own_bytes_before_running():
    """🚨 第 125 层：`stamp(expect_bytes=…)` 的抓取点必须在**开跑第一条**。

    判据：生成器源码里 `expect_bytes = provenance.code_file_sha256(...)`
    出现在 `provenance.stamp(` **之前**。
    """
    src = (ROOT / "scripts" / "measure_goal_progress.py").read_text(encoding="utf-8")
    i_capture = src.find("expect_bytes = provenance.code_file_sha256(")
    i_stamp = src.find("provenance.stamp(")
    assert i_capture > 0, "生成器里没有抓取生成器字节"
    assert i_stamp > 0, "生成器里没有盖章"
    assert i_capture < i_stamp, (
        "抓取字节**晚于**盖章 ⇒ 拿开跑的字节盖开跑的章 ⇒ 恒真（第 131 层）")
