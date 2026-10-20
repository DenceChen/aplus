"""D2 缺口在首屏必须有**两个口径**，各自带标签 —— 且实盘档**逐位**等于产物字段。

## 这个文件存在的理由

首屏原来只给一个数：`d2_signal_requirement.json::headline.account_annual_ceiling_pct`
的「榜宽 10 · ¥5 万」那一格 —— **满仓（100% 部署）假设下的公式上界**。
而平台**真会执行**的那个账户（整手 ⇒ 投入率下降、闲置现金 0 计息）测出来的是
`deployed_d2_verdict.json::verdict` 里的另一个数。

⇒ 两者**不是同一个量**（`docs/DELIVERY_DECISIONS_2026-10-20.md §16.2`）。
只给一个 ⇒ 用户看到的「离 20% 还有多远」**少了一截**；
只给另一个 ⇒ 用户看不到「满仓假设下最多能到哪」。
⇒ **两个都要给，各自带标签与一句话来源。**

## 三条硬纪律（每条都有守卫）

① **不许合成一个数** ⇒ 两个口径的标签与数**必须都在**，且**各自带标签**。
② **实盘档逐位等于产物字段** ⇒ 拿公式重算当验证是第 109 层。
③ **不许跨口径取极值**（第 143 层）⇒ 两个数并排，不合并、不取大。

## ⚠️ 为什么「实盘档的年化」有一个 ×100

产物里 `deployed_d2_verdict.json::verdict.cagr` 是**分数**（`0.1042…`），
而 API 面只准出**百分数**（第 182 层：生产者钉住了，消费者一处换算都没有）
⇒ `aplus/reports.py::_read_deployed_d2_caliber` 做**唯一一处** ×100 换算。
⇒ 本文件用**两条**独立断言钉住它：
  · `pct == cagr * 100`（换算恰好是 ×100）
  · `pct / 100 == cagr`（**往返逐位相等** ⇒ 没有别的变换混进来）
🚨 而 `verdict.gap_to_target_pp` 在产物里**已经是 pp** ⇒ **逐位直读**，
  守卫直接比 `==`（实测重算 `(target − cagr) × 100` 会踩 1 ulp）。

## 夹具一律直读真产物

手写一份 `{...台账...}` 夹具会把「我以为的键名」与「真实的键名」之间的差别
藏进夹具（第 120 层）⇒ 这里**一律从产物读**，期望值**不手打**。

## 变异验证

见 `tests/mutation_specs/test_d2_gap_two_calibers.mut` 与同目录 README。
"""
import copy
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
VERDICT_JS = ROOT / "aplus" / "web" / "verdict.js"
HTML = ROOT / "aplus" / "web" / "index.html"
IDEAL_ARTIFACT = ROOT / "data" / "reports" / "d2_signal_requirement.json"
DEPLOYED_ARTIFACT = ROOT / "data" / "reports" / "deployed_d2_verdict.json"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="需要 node 才能测前端纯逻辑")


# ---------- 产物（真读，不手抄） ----------

def _ideal_artifact() -> dict:
    return json.loads(IDEAL_ARTIFACT.read_text(encoding="utf-8"))


def _deployed_artifact() -> dict:
    return json.loads(DEPLOYED_ARTIFACT.read_text(encoding="utf-8"))


def _ideal_numbers() -> dict:
    cap = _ideal_artifact()["headline"]["account_annual_ceiling_pct"]
    return {
        "annual_pct": cap["at_deliverable_width_and_50k"],
        "gap_pp": cap["gap_to_target_at_deliverable_width_and_50k_pp"],
    }


def _deployed_numbers() -> dict:
    v = _deployed_artifact()["verdict"]
    return {
        "cagr_fraction": v["cagr"],
        "gap_pp": v["gap_to_target_pp"],
    }


def _payload() -> dict:
    """经生产模块读出来的整份 `/api/d2-gap` 载荷（不手写夹具）。"""
    from aplus.reports import read_d2_gap
    return read_d2_gap(ROOT / "data" / "reports")


# ---------- node 夹具 ----------

def _node(payload=None) -> dict:
    """在 node 里跑生产判决，把结果转回 python。

    `payload` 缺省 = 生产载荷；给了别的就渲染那个 ⇒ **反向正控**
    可以拿「对调过的载荷」真跑一遍生产代码。
    """
    if payload is None:
        payload = {"ledger": _payload()}
    script = (
        "const V = require(%s);"
        "const p = %s;"
        "const out = {};"
        "out.ideal_label = V.D2_CALIBER_IDEAL_LABEL;"
        "out.deployed_label = V.D2_CALIBER_DEPLOYED_LABEL;"
        "out.ideal_source = V.D2_CALIBER_IDEAL_SOURCE;"
        "out.deployed_source = V.D2_CALIBER_DEPLOYED_SOURCE;"
        "out.verdict = V.d2LedgerVerdict(p.ledger.verdict_input);"
        "out.cells = V.d2GapCalibers(p.ledger.verdict_input);"
        "out.multiples = V.d2Multiples(p.ledger.verdict_input);"
        # 🚨 **屏上真正被渲染的那段**（`detailHtml` 由 renderCaveat 生成）。
        #    验「上了屏」必须验它，验 `detail` 不算数（第 13 层）。
        "out.screen_text = out.verdict ? V.renderCaveat(out.verdict.detail) : '';"
        "console.log(JSON.stringify(out));"
    ) % (json.dumps(str(VERDICT_JS)), json.dumps(payload, ensure_ascii=False))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, f"node 抛异常了 rc={r.returncode}: {r.stderr}"
    return json.loads(r.stdout)


def _cross_label_rendered() -> str:
    """产物给跨口径那一列的**口径名**，**渲染后**的形态（屏上真正出现的那个）。

    ⚠️ **期望值必须取自真产物，不许取自被测载荷** ——
    否则「把标签摘掉」那个反向正控就量不到自己（期望也跟着被摘掉了）。
    """
    raw = _cross_caliber()["caliber_name"]
    script = ("const V = require(%s);"
              "console.log(JSON.stringify(V.renderCaveat(%s)));"
              % (json.dumps(str(VERDICT_JS)), json.dumps(raw)))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, f"node 抛异常了 rc={r.returncode}: {r.stderr}"
    return json.loads(r.stdout)


def _label_number(text: str, label: str) -> str:
    """`label` 后面**第一个带 `%` 的数**（含正负号）。

    ⚠️ 为什么不取「第一个数字」：正文里紧跟着标签的是「榜宽 10 · 本金 ¥5 万」
    ⇒ 第一个数字是 `10`（榜宽），不是年化 ⇒ 必须锚在 **`%` 后面**那个。
    """
    i = text.find(label)
    assert i >= 0, f"文本里没有标签 {label!r}"
    m = re.search(r"([+-]?\d+(?:\.\d+)?)%", text[i + len(label):])
    assert m, f"标签 {label!r} 后面没有带 %% 的数：{text[i:i+160]!r}"
    return m.group(1)


def _carries(text: str, label: str, expected: float, ndigits: int, what: str) -> bool:
    """标签后面跟着的数**是不是那一档的数**。

    ⚠️ 容差由**显示精度**推导（半个末位），不是随手写的 `1e-9`：
    显示值本来就是四舍五入过的 ⇒ 拿它与原值比必须允许半个末位。
    """
    got = float(_label_number(text, label))
    tol = 0.5 * 10 ** (-ndigits) + 1e-12
    return abs(got - expected) <= tol


# ---------- ① 载荷层：两个口径都在，且实盘档逐位等于产物 ----------

def test_the_payload_carries_both_calibers():
    """理想档与实盘档**必须同时**在载荷里（不许只给一个）。

    ⚠️ 缺了实盘档 ⇒ 首屏只剩「能做到的上限」⇒ 少报；
       缺了理想档 ⇒ 用户看不到满仓假设的天花板。
    """
    p = _payload()
    assert p["status"] == "ok", p
    assert p["deployed"]["available"] is True, (
        f"实盘档不可用：{p['deployed'].get('note')}")
    assert isinstance(p["account_annual_at_50k_pct"], float)
    assert isinstance(p["deployed"]["account_annual_pct"], float)
    assert isinstance(p["deployed"]["gap_to_target_pp"], float)


def test_the_deployed_gap_is_bit_identical_to_the_artifact_field():
    """第 109 层：实盘档的缺口**逐位等于** `deployed_d2_verdict.json` 的字段。

    🚨 **不许用公式重算当验证**：实测 `(target − cagr) × 100` 与产物差 1 ulp
    （`20 − cagr × 100` 差得更多）⇒ 拿公式当验证就与产物对不上。
    """
    p = _payload()
    src = _deployed_numbers()
    got = p["deployed"]["gap_to_target_pp"]
    assert got == src["gap_pp"], (
        f"实盘档缺口 {got!r} ≠ 产物字段 {src['gap_pp']!r} ⇒ "
        "这一格不是产物那个数（被重算过，或读错了字段）")
    assert isinstance(got, float)


def test_the_deployed_annual_is_the_artifact_cagr_with_only_a_unit_scale():
    """实盘档的年化 = 产物 `::verdict.cagr`，**唯一的变换是 ×100**（分数→百分数）。

    两条独立断言：
      · `pct == cagr * 100`  ⇒ 换算恰好是 ×100（÷100 / ×1000 都会红）
      · `pct / 100 == cagr`  ⇒ **往返逐位相等** ⇒ 没有别的变换混进来
    🚨 绝对外部锚：年化收益**不可能低于 −100%** ⇒ 这一格若变成 1042.45% 就该红。
    """
    p = _payload()
    src = _deployed_numbers()
    pct = p["deployed"]["account_annual_pct"]
    assert pct == src["cagr_fraction"] * 100.0, (
        f"实盘档年化 {pct!r} ≠ 产物 cagr × 100 ⇒ 换算被改过")
    assert pct / 100.0 == src["cagr_fraction"], (
        f"往返不逐位相等：{pct!r} / 100 ≠ {src['cagr_fraction']!r} ⇒ "
        "有第二个变换混进来了")
    assert -100.0 < pct < 100.0, (
        f"年化 {pct!r}% 落在 (−100, 100) 之外 ⇒ 量纲被读错了")


def test_the_ideal_caliber_keeps_its_original_meaning():
    """🚨 我这一轮**不许**动理想档那一侧的口径。

    `d2_signal_requirement.json` 仍是「满仓假设的公式上界」，
    它的两个数必须**逐位不变**（否则「并排」就变成「换了一个口径」）。
    """
    p = _payload()
    src = _ideal_numbers()
    assert p["account_annual_at_50k_pct"] == src["annual_pct"]
    assert p["gap_to_target_pp"] == src["gap_pp"]
    # 理想档的来源路径仍指向台账那一格
    assert p["deployed"]["fields"]["gap_to_target_pp"] == "$.verdict.gap_to_target_pp"


def test_a_missing_deployed_artifact_does_not_fall_back_to_the_ideal_caliber():
    """缺产物 ⇒ `available = False` + 原因，**绝不**拿理想档的数顶替（第 7 层）。"""
    import tempfile
    from aplus.reports import read_d2_gap

    with tempfile.TemporaryDirectory() as td:
        # 只把理想档那份产物放进去 ⇒ 实盘档必然缺失
        (Path(td) / IDEAL_ARTIFACT.name).write_text(
            IDEAL_ARTIFACT.read_text(encoding="utf-8"), encoding="utf-8")
        out = read_d2_gap(Path(td))
    assert out["status"] == "ok", out
    dep = out["deployed"]
    assert dep["available"] is False, "缺产物却报「可用」"
    assert dep["account_annual_pct"] is None, "缺产物却给了一个年化数"
    assert dep["gap_to_target_pp"] is None, "缺产物却给了一个缺口数"
    assert dep["note"], "缺产物必须说清为什么"


# ---------- ② 判决层：两个口径都在，各自带标签与来源 ----------

def test_the_verdict_carries_both_calibers_each_with_its_own_label():
    """判决里**两个口径都在**，且**各自带标签**（不许合成一个数）。

    标签从 `verdict.js` 的常量导出读 ⇒ **不手抄**（第 137 层）。
    """
    got = _node()
    v = got["verdict"]
    assert v is not None, "台账齐全却判决缺席"
    blob = v["headline"] + " " + v["detail"]

    ideal, dep = _ideal_numbers(), _deployed_numbers()
    assert got["ideal_label"] in blob, "判决里没有理想档的标签"
    assert got["deployed_label"] in blob, "判决里没有实盘档的标签"
    assert f"{ideal['annual_pct']:.2f}" in blob, "判决里没有理想档的年化"
    assert f"{ideal['gap_pp']:.2f}" in blob, "判决里没有理想档的缺口"
    assert f"{dep['cagr_fraction'] * 100.0:.4f}" in blob, "判决里没有实盘档的年化"
    assert f"{dep['gap_pp']:.4f}" in blob, "判决里没有实盘档的缺口"
    # 各自的来源路径也要在（"一句话来源"）
    assert got["ideal_source"] in blob, "判决里没有理想档的来源路径"
    assert got["deployed_source"] in blob, "判决里没有实盘档的来源路径"


def test_the_verdict_labels_each_number_with_its_own_caliber():
    """反混口径：每个标签后面跟着的**必须是对应那一档的数**。

    这条是「不许跨口径取极值 / 不许合成一个数」的行为层判据 ——
    只断言「两个数都在」不够：把它们**对调**同样能通过那一条。
    """
    got = _node()
    v = got["verdict"]
    blob = v["headline"] + " " + v["detail"]
    ideal, dep = _ideal_numbers(), _deployed_numbers()
    assert _carries(blob, got["ideal_label"], ideal["annual_pct"], 2, "理想档"), (
        "理想档标签后面跟的不是理想档的数 ⇒ 两档被对调或合并了")
    assert _carries(blob, got["deployed_label"], dep["cagr_fraction"] * 100.0, 4,
                    "实盘档"), (
        "实盘档标签后面跟的不是实盘档的数 ⇒ 两档被对调或合并了")


def test_the_verdict_does_not_merge_the_two_calibers_into_one_number():
    """两个口径的数**必须不同**（真的是两个量），且**都必须在**。

    🚨 合成一个数最常见的形态是「取一个、标成另一个」⇒
       那时 `ideal != deployed` 仍成立，但**其中一个不在屏上** ⇒ 上面两条会红。
       这条钉的是另一半：**不许只印一个数**。
    """
    got = _node()
    v = got["verdict"]
    blob = v["headline"] + " " + v["detail"]
    ideal, dep = _ideal_numbers(), _deployed_numbers()
    assert ideal["annual_pct"] != dep["cagr_fraction"] * 100.0, (
        "两个口径的年化相同 ⇒ 它们本就不是两个量（或其中一个被换了）")
    assert ideal["gap_pp"] != dep["gap_pp"], "两个口径的缺口相同 ⇒ 疑似同一个量"
    for s in (f"{ideal['annual_pct']:.2f}", f"{ideal['gap_pp']:.2f}",
              f"{dep['cagr_fraction'] * 100.0:.4f}", f"{dep['gap_pp']:.4f}"):
        assert s in blob, f"屏上缺 {s}"


# ---------- ③ 反向正控：把两档对调 ⇒ 守卫必须红 ----------

def _swapped_payload() -> dict:
    """把两档的数**对调**（理想档给实盘档的数，实盘档给理想档的数）。"""
    p = _payload()
    vi = copy.deepcopy(p["verdict_input"])
    ideal_cap = vi["headline"]["account_annual_ceiling_pct"]
    dep = vi["deployed"]
    real_annual = ideal_cap["at_deliverable_width_and_50k"]
    real_gap = ideal_cap["gap_to_target_at_deliverable_width_and_50k_pp"]
    ideal_cap["at_deliverable_width_and_50k"] = dep["account_annual_pct"]
    ideal_cap["gap_to_target_at_deliverable_width_and_50k_pp"] = dep["gap_to_target_pp"]
    dep["account_annual_pct"] = real_annual
    dep["gap_to_target_pp"] = real_gap
    return {"ledger": {**p, "verdict_input": vi}}


def test_swapping_the_two_calibers_is_detected():
    """🚨 **反向正控**：把两档对调 ⇒ 上面那条「标签后面是对应那一档的数」**必须红**。

    ⚠️ 只断言「变红」不够，必须给出**为什么它会红**：
       对调后，理想档标签后面跟的是实盘档的数 ⇒
       `_label_number(...) == 理想档的数` 这条断言为假。
       ⇒ **判别力**在这里是「守卫问的是『标签与数是否配对』」，
          而不只是「两个数是否都在」。
    """
    real = _node()
    ideal, dep = _ideal_numbers(), _deployed_numbers()
    blob = real["verdict"]["headline"] + " " + real["verdict"]["detail"]
    # 正控：真实载荷下配对成立
    assert _label_number(blob, real["ideal_label"]) == f"{ideal['annual_pct']:.2f}"

    swapped = _node(payload=_swapped_payload())
    sblob = swapped["verdict"]["headline"] + " " + swapped["verdict"]["detail"]
    # 反控：对调之后，配对**不再**成立 ⇒ 上面那条断言会红
    assert not _carries(sblob, swapped["ideal_label"], ideal["annual_pct"], 2, "理想档"), (
        "对调两档后守卫仍然成立 ⇒ 这条守卫是恒真的（或没有判别力）")
    assert not _carries(sblob, swapped["deployed_label"],
                         dep["cagr_fraction"] * 100.0, 4, "实盘档"), (
        "对调两档后实盘档那一条仍然成立 ⇒ 同上")


# ---------- ④ 首屏：两个口径并排（index.html） ----------

def _strip_html_comments(src: str) -> str:
    """剥掉 HTML 注释（`<!-- ... -->`），保持行号不变。

    为什么必须剥：注释里也会出现 `id="d2-gap-calibers"` 这种契约说明
    ⇒ 子串断言会被**注释本身**满足（第 13 层）。
    """
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("<!--", i):
            j = src.find("-->", i)
            j = n if j < 0 else j + 3
            out.append(" " * (j - i))
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _inline_script_blocks() -> list:
    """抽内联 `<script>` 块 —— **复用仓里已有的量具，不照抄它的正则**（第 163 层）。"""
    spec = importlib.util.spec_from_file_location(
        "_chat_wiring_for_two_calibers", ROOT / "tests" / "test_chat_wiring.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._inline_scripts(HTML.read_text(encoding="utf-8"))


def _render_first_screen(payload=None) -> str:
    """把**生产源码里**的 `renderD2GapCalibers` 拿到 node 里真跑，量它写出的 HTML。

    ⚠️ 不 `require` 整个 index.html（那要 mock 一整套 DOM ⇒ 测的是我写的 mock）；
       这里只抽那**一个函数**，配上最小 stub（`$` / `esc` / `boldify`），
       `d2GapCalibers` 用**真的** `verdict.js`。
    """
    src = HTML.read_text(encoding="utf-8")
    m = re.search(r"^function renderD2GapCalibers\(d2gap\) \{$", src, re.M)
    assert m, "index.html 里找不到 renderD2GapCalibers ⇒ 守卫打空了"
    rest = src[m.end():]
    end = re.search(r"^\}$", rest, re.M)
    assert end, "找不到 renderD2GapCalibers 的收尾（列 0 的 }）"
    fn = src[m.start():m.end() + end.end()]

    if payload is None:
        payload = {"ledger": _payload()}
    # ⚠️ `d2GapCalibers` 与 `d2Multiples` 必须**注入本脚本的作用域**：
    #   浏览器里 `verdict.js` 是经典脚本 ⇒ 顶层函数声明落在全局对象上 ⇒
    #   `index.html` 的内联脚本能直接引用它；
    #   而 node 的 `require()` 给的是**模块作用域** ⇒ 不注入就是 `undefined`
    #   ⇒ 生产函数会走 `typeof … !== 'function'` 那条早退分支
    #   ⇒ 测到的会是「什么都没渲染」，而不是生产的真实行为。
    script = """
const V = require(%s);
const d2GapCalibers = V.d2GapCalibers;
const d2Multiples = V.d2Multiples;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&','<':'<','>':'>','"':'"',"'":'&#39;'}[c]));
const boldify = h => String(h ?? '').replace(/\\*\\*([^*]+)\\*\\*/g, '<b>$1</b>');
let captured = null;
const $ = id => (id === 'd2-gap-calibers'
  ? {set innerHTML(v) { captured = v; }, get innerHTML() { return captured; }, hidden: true}
  : null);
%s
renderD2GapCalibers(%s);
console.log(JSON.stringify({html: captured}));
""" % (json.dumps(str(VERDICT_JS)), fn, json.dumps(payload, ensure_ascii=False))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, f"node 抛异常了 rc={r.returncode}: {r.stderr}"
    return json.loads(r.stdout)["html"] or ""


def test_the_first_screen_renders_both_calibers_side_by_side():
    """首屏那块**必须并排显示两档**，且**各自带标签**。

    「并排」是**结构**给的 ⇒ 这里量的是**格子数**（两块），
    不是「两个数在不在同一段文字里」（那在折叠的 `<details>` 里也成立）。
    """
    html = _render_first_screen()
    assert html, "首屏那块什么都没渲染出来"
    # ⚠️ 锚在**格子本身**的 class 上（`d2c` / `d2c na`），
    #    不是 `d2c` 前缀（`d2c-num` / `d2c-sub` / `d2c-src` 也是前缀命中）。
    cells = re.findall(r'<div class="d2c(?: na)?">', html)
    assert len(cells) == 2, f"首屏渲染了 {len(cells)} 格（应为两档各一格）：{html[:400]}"

    got = _node()
    assert got["ideal_label"] in html, "首屏没有理想档的标签"
    assert got["deployed_label"] in html, "首屏没有实盘档的标签"
    ideal, dep = _ideal_numbers(), _deployed_numbers()
    assert f"{ideal['annual_pct']:.2f}%" in html, "首屏没有理想档的年化"
    assert f"{ideal['gap_pp']:.2f}pp" in html, "首屏没有理想档的缺口"
    assert f"{dep['cagr_fraction'] * 100.0:.4f}%" in html, "首屏没有实盘档的年化"
    assert f"{dep['gap_pp']:.4f}pp" in html, "首屏没有实盘档的缺口"
    assert got["ideal_source"] in html, "首屏没有理想档的来源路径"
    assert got["deployed_source"] in html, "首屏没有实盘档的来源路径"


def test_the_first_screen_swapped_calibers_are_detected():
    """🚨 **反向正控**（首屏层）：两档对调 ⇒ 配对守卫必须红。"""
    html = _render_first_screen(payload=_swapped_payload())
    got = _node()
    ideal, dep = _ideal_numbers(), _deployed_numbers()
    # 对调之后：理想档的标签后面跟的是实盘档的数
    assert not _carries(html, got["ideal_label"], ideal["annual_pct"], 2, "理想档"), (
        "对调两档后首屏的配对仍然成立 ⇒ 这条守卫没有判别力")
    assert not _carries(html, got["deployed_label"], dep["cagr_fraction"] * 100.0, 4,
                        "实盘档"), "同上"


def test_the_first_screen_block_exists_outside_the_collapsed_fold():
    """那块**必须在判决板里、且不是 `<details>` 的一部分**。

    🚨 理由：`#docket-warn` 的正文在 `<details class="fold">` 里（**默认折叠**）
    ⇒ 缺口数在那里**看不见** ⇒ 必须有一个**常驻可见**的家。
    """
    raw = HTML.read_text(encoding="utf-8")
    stripped = _strip_html_comments(raw)
    assert 'id="d2-gap-calibers"' in stripped, (
        "首屏没有 #d2-gap-calibers 节点 ⇒ 两个口径不在首屏"
        "（在**剥掉注释**之后仍找不到 ⇒ 它只在注释里，或根本不存在）")
    # 位置判定用**原文**（注释标记本身就在原文里，剥掉后就找不到了）
    warns_end = raw.find('<!-- /.docket-warns -->')
    node_at = raw.find('id="d2-gap-calibers"')
    assert warns_end >= 0 and node_at > warns_end, (
        "D2 缺口块被塞进了警示条容器 ⇒ 它的栅格布局会被警示条占掉")


def test_the_first_screen_block_is_two_columns():
    """「并排」是**布局**给的 ⇒ CSS 里必须是两列。

    ⚠️ 判据落在 **CSS 声明**上（`grid-template-columns`），不是全文子串 ——
       子串会被注释满足（第 13 层）。
    """
    html = _strip_html_comments(HTML.read_text(encoding="utf-8"))
    m = re.search(r"\.d2-gap-calibers\s*\{([^}]*)\}", html)
    assert m, "找不到 .d2-gap-calibers 的 CSS 规则"
    rule = m.group(1)
    cols = re.search(r"grid-template-columns:\s*([^;]+);", rule)
    assert cols, f".d2-gap-calibers 没有 grid-template-columns：{rule[:200]}"
    assert cols.group(1).strip() == "1fr 1fr", (
        f"两列的写法变了：{cols.group(1).strip()!r} ⇒ 「并排」不再是结构给的")


# ---------- ⑤ 「还差几倍」：同口径那一列才是缺口 ----------

def _multiple_block() -> dict:
    return _ideal_artifact()["headline"][
        "multiple_needed_at_50k_deliverable_by_caliber"]


def _same_caliber() -> dict:
    return _multiple_block()["same_caliber"]


def _cross_caliber() -> dict:
    return _multiple_block()["cross_caliber"]


def _cross_text() -> str:
    """跨口径那一列在屏上的**样子**（取自产物，不手打）。"""
    return f"{_cross_caliber()['multiple_needed']:.2f}×"


def _same_text() -> str:
    return f"{_same_caliber()['multiple_needed']:.2f}×"


def test_the_payload_exposes_the_multiples_with_their_caliber_names():
    """API 载荷必须把**三个口径**连同**口径名**端出去。

    🚨 前端读不到 ⇒ 同口径那一列白加 ⇒ 「屏上不许出现未加标签的 2.35」这条守卫
       反而会把正确的实现判红 ⇒ **载荷这一层是前提**。
    """
    p = _payload()
    blk = p["multiple_by_caliber"]
    assert isinstance(blk, dict), "载荷里没有 `multiple_by_caliber`"
    assert blk["same_caliber"]["caliber_name"] == _same_caliber()["caliber_name"]
    assert blk["cross_caliber"]["caliber_name"] == _cross_caliber()["caliber_name"]
    assert p["multiple_needed_same_caliber_caliber_name"] == _same_caliber()["caliber_name"]
    # 旧的裸键**原样保留**（既有守卫逐位钉住它）⇒ 但**必须被标注成不是缺口**
    assert p["multiple_needed_at_50k"] == _ideal_artifact()["headline"][
        "multiple_needed_at_50k_deliverable"]
    assert p["multiple_needed_at_50k_is_the_gap"] is False
    assert "不是缺口" in p["multiple_needed_at_50k_note"]


def test_the_same_caliber_multiple_is_quoted_bit_for_bit():
    """同口径倍数**逐位等于**台账的 `same_caliber.multiple_needed`。

    🚨 **不许用公式重算当验证**（第 109 层）：重算的是「我对公式的理解」。
    """
    p = _payload()
    assert p["multiple_needed_same_caliber"] == _same_caliber()["multiple_needed"]
    got = _node()
    assert _same_text() in got["screen_text"], (
        f"屏上没有同口径那一列的 {_same_text()}（那才是唯一可当缺口用的倍数）")


def test_the_cross_caliber_multiple_never_appears_unlabeled():
    """🚨 **首屏不许出现未加标签的跨口径倍数**。

    判据用**上下文**（那一句附近必须有产物给它的口径名），**不是**全文出现检查
    ⇒ 仓里别处也有 `2.35`（`d2_gap_calibers` 那个 K=21 家族的比值）⇒
    全文检查会被别处满足（第 13 层）。

    ⚠️ 标签取**产物自己的** `cross_caliber.caliber_name`（那里面逐字写着「不是缺口」）
    ⇒ 屏上的免责说明**只有一份** ⇒ 产物改了标签，守卫立刻红。
    """
    got = _node()
    blob = got["screen_text"]
    label = _cross_label_rendered()
    assert label, "产物里没有跨口径那一列的口径名 ⇒ 守卫量不到东西"
    bad = [m.start() for m in re.finditer(re.escape(_cross_text()), blob)
           if label not in blob[max(0, m.start() - 400): m.start() + 400]]
    assert not bad, (
        f"跨口径倍数 {_cross_text()} 出现在没有口径标签的位置：{bad}\n"
        f"屏上实际是：{blob[:600]}")


def test_an_unlabeled_cross_caliber_multiple_is_rejected():
    """🚨 **反向正控**：把产物给跨口径那一列的口径名**摘掉「不是缺口」**
    ⇒ 屏上就出现一个**没有免责标签**的 2.35 ⇒ 上面那条守卫**必须红**。

    ⚠️ 这是**真的生产路径**：口径名是**产物给的**，产物改了标签，屏上跟着变
    ⇒ 守卫必须能抓到「免责说明被摘掉」这一种退化。
    ⚠️ **期望标签取自真产物**（`_cross_label_rendered()`），
       **不取自被测载荷** —— 否则期望也跟着被摘掉 ⇒ 量不到自己。
    """
    p = _payload()
    vi = copy.deepcopy(p["verdict_input"])
    blk = vi["headline"]["multiple_needed_at_50k_deliverable_by_caliber"]
    blk["cross_caliber"]["caliber_name"] = "跨口径（K=21 研究臂）"   # 摘掉免责
    got = _node(payload={"ledger": {**p, "verdict_input": vi}})

    blob = got["screen_text"]
    label = _cross_label_rendered()
    bad = [m.start() for m in re.finditer(re.escape(_cross_text()), blob)
           if label not in blob[max(0, m.start() - 400): m.start() + 400]]
    assert bad, (
        "把口径名的免责说明摘掉之后，守卫仍然绿 ⇒ 这条守卫是恒真的"
        "（或它量的不是「标签在不在」）")
    # 正控：原封不动的载荷下，同一条判据是**不报**的
    real = _node()
    real_label = _cross_label_rendered()
    real_bad = [m.start() for m in re.finditer(re.escape(_cross_text()),
                                               real["screen_text"])
                if real_label not in
                real["screen_text"][max(0, m.start() - 400): m.start() + 400]]
    assert not real_bad, "正控失败：原封不动的载荷被判成有未标注的倍数"


def test_the_first_screen_block_shows_the_same_caliber_multiple_only():
    """首屏那块**只**显示同口径那一列（跨口径那个不进常驻块）。

    🚨 常驻块是首屏最显眼的位置 ⇒ 一个没有免责标签的 2.35 出现在这里
    ⇒ 比藏在折叠的推导里危险得多。
    """
    html = _render_first_screen()
    assert _same_text() in html, f"首屏那块没有同口径那一列的 {_same_text()}"
    assert _cross_text() not in html, (
        f"首屏那块出现了跨口径的 {_cross_text()} ⇒ 它不是缺口，不许当缺口上首屏")


# ---------- ⑥ 口径对照表：那个「唯一可引用」的认领必须保持退休 ----------

def _caliber_table() -> dict:
    """**跑生成器**（`scripts/d2_gap_calibers.py::build`）拿本表。

    ⚠️ 为什么不直接读落盘 JSON：那是**读快照的守卫**（第 96 层）——
       脚本改了、产物没重跑，它照样绿 ⇒ 它守不住「改动是否还在」。
    ⇒ 这里量**生成器**的输出 ⇒ 变异改生成器时它立刻红。
    """
    spec = importlib.util.spec_from_file_location(
        "_d2_gap_calibers_generator", ROOT / "scripts" / "d2_gap_calibers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build()


def test_the_only_citable_claim_stays_retired():
    """🚨 `d2_gap_calibers` 的顶层键 `the_only_citable_d2_gap` **不许回来**。

    那个键自称「D2 唯一可引用的缺口陈述」，而它绑定的是 **K=21 判决档**
    ⇒ 生产配置已在 2026-10-20 换成 **K=63 满仓 10 只** ⇒ 那个认领**过期**。
    ⇒ 现在是**三个口径并排**，屏上给用户的是**实盘档**。

    ⚠️ **键名本身就是那句认领** ⇒ 判据落在**顶层键集合**上，不是散文子串。
    """
    built = _caliber_table()
    assert "the_only_citable_d2_gap" not in built, (
        "「唯一可引用的缺口陈述」这个键又回来了 —— 它的口径绑定已过期")
    assert "d2_gap_by_caliber" in built, "口径对照表没有并排容器"
    calibers = built["d2_gap_by_caliber"]
    assert set(calibers) == {"verdict_k21", "deployed_live_k63",
                             "ideal_ceiling_k63"}, (
        f"并排容器的口径集合变了：{sorted(calibers)}")


def test_the_retirement_is_recorded_and_each_caliber_carries_its_k():
    """退休**要留下记录**（第 25 层：标注作废，不抹掉），且**每个口径带 K**。

    🚨 「K 不同 ⇒ 摩擦不同 ⇒ 门槛不同」⇒ 不带 K 的引用会让两档被当同一档比。
    """
    built = _caliber_table()
    retired = built.get("retired_claims")
    assert retired, "退休没有留记录 ⇒ 后人会以为那个键从来不存在"
    assert retired[0]["old_key"] == "the_only_citable_d2_gap"
    assert str(retired[0]["status"]).startswith("RETIRED")

    calibers = built["d2_gap_by_caliber"]
    for key in ("verdict_k21", "deployed_live_k63", "ideal_ceiling_k63"):
        binding = calibers[key].get("caliber_binding")
        assert isinstance(binding, dict), f"{key} 没有 caliber_binding ⇒ 它的 K 没被标出来"
        assert isinstance(binding.get("K"), int), f"{key} 的 K 不是整数"

    # 🚨 判决档的 K 必须**不等于**生产配置的 K（它们就是不同的档）
    assert (calibers["verdict_k21"]["caliber_binding"]["K"]
            != calibers["deployed_live_k63"]["caliber_binding"]["K"]), (
        "判决档与实盘档的 K 相同 ⇒ 「不同 K」那句标注是假的")
    # 实盘档的 K 必须与产物自报的 K 一致（只读，不许手打）
    assert (calibers["deployed_live_k63"]["caliber_binding"]["K"]
            == _deployed_artifact()["verdict"]["config"]["K"])


def test_the_screen_facing_caliber_is_the_deployed_one():
    """屏上给用户的那个数**是实盘档** —— 这一条必须写在产物里，不许靠人记得。"""
    built = _caliber_table()
    which = built.get("which_one_goes_on_the_screen")
    assert which, "产物没有说「屏上给哪个口径」"
    assert "实盘档" in which["answer"], which["answer"]
    assert "deployed_live_k63" in which["answer"], (
        "屏上口径的答案没有指到 `deployed_live_k63`")


def test_the_multiple_family_is_not_a_fourth_gap_caliber():
    """🚨「倍数」那一族**不许**混进 `d2_gap_by_caliber` 的三档里当第四档。

    它回答的是**另一个问题**（「信号还差几倍」），
    而那三档回答的是「账户年化离 20% 差多少」
    ⇒ 混在一起，读者会把「1.79×」当成又一个「缺口 pp」。
    """
    built = _caliber_table()
    calibers = built["d2_gap_by_caliber"]
    assert set(calibers) == {"verdict_k21", "deployed_live_k63",
                             "ideal_ceiling_k63"}, (
        f"缺口口径的档数变了：{sorted(calibers)}")
    assert "multiple_by_caliber" not in calibers, (
        "「倍数」那一族被塞进了缺口口径表 ⇒ 第四档出现了")
    mult = built["multiple_by_caliber"]
    assert mult["available"] is True, mult.get("note")
    which = mult["which_column_is_the_gap"]
    assert "✅" in which["same_caliber"], which["same_caliber"]
    assert "不是缺口" in which["cross_caliber"], which["cross_caliber"]


def test_the_multiple_family_is_read_only_from_the_ledger():
    """产物里那一族**逐位等于**台账的块（只读，不许重算、不许手抄）。"""
    built = _caliber_table()
    block = built["multiple_by_caliber"]["block"]
    ledger_block = _multiple_block()
    assert block["same_caliber"]["multiple_needed"] == \
        ledger_block["same_caliber"]["multiple_needed"]
    assert block["cross_caliber"]["multiple_needed"] == \
        ledger_block["cross_caliber"]["multiple_needed"]
    assert block["cross_caliber"]["is_the_gap"] is False


def test_the_first_screen_block_is_actually_wired_into_render_docket():
    """那块**必须真的被渲染** —— 函数存在 ≠ 被调用。

    🚨 上一条守卫全都直接调 `renderD2GapCalibers`（把它从生产里摘掉它们照样绿）
    ⇒ 「接上了」这件事必须**单独**钉住。
    ⚠️ 锚在 `renderDocket` 的**函数体**里（剥掉注释后），不是全文子串
       （第 13 层：注释与契约说明会满足子串断言）。
    """
    mod = _wiring_helpers()
    clean = mod.strip_js_comments(HTML.read_text(encoding="utf-8"))
    body = mod.body_of(clean, "function renderDocket(stats, evo, weights,"
                               " predictions, gate, d2gap)")
    calls = re.findall(r"renderD2GapCalibers\(\s*([A-Za-z0-9_$.?]+)\s*\)", body)
    assert calls, (
        "renderDocket 里没有调 renderD2GapCalibers ⇒ 首屏那块永远不出现"
        "（函数还在、守卫还绿、屏上什么都没有）")
    assert calls[0] == "d2gap", f"实参不是 d2gap：{calls[0]!r}"


def _wiring_helpers():
    """复用仓里已有的剥注释器 / 取函数体量具 —— **不照抄它的正则**（第 163 层）。"""
    spec = importlib.util.spec_from_file_location(
        "_d2_gap_wiring_helpers", ROOT / "tests" / "test_d2_gap_wiring.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
