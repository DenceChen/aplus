"""概览页可读性守卫（2026-10-21）。

## 这一批守的是**呈现层**，不是数据

用户原话：「优化页面展示，使得展示阅读性强，提高 UI 展示能力；
不要乱七八糟，发现 UI 的 bug 修复，做好测试。」

⇒ 本文件**一个数字都不许改**。它只回答两件事：

1. **内容到不到得了**（窄视口下有没有被 `overflow:hidden` 裁掉、
   裁掉之后**滚不到** ⇒ 那就是「用户永远读不到」）；
2. **读起来是不是分得清**（口径标签是不是只靠颜色、四态是不是写反了、
   裸 Markdown 是不是漏在屏上）。

## 为什么有「浏览器级」和「静态级」两套

- **静态级**（§A）：CSS 规则、DOM 顺序、渲染函数的**输出**。
  快、无依赖、能进每一次门禁。
  ⚠️ 断言锚在**语义位置**：CSS 规则的**选择器 + 声明**、
     DOM 的**顺序**、渲染函数**真跑一遍的输出** ——
     **不是**「源码里出现过某个串」（第 13 种形状：子串会被注释满足）。
- **浏览器级**（§B）：真 Chromium，真视口矩阵（1440 / 820 / 768 / 390）。
  只有真浏览器能回答「这一块**真的**被裁掉了、且**真的**滚不到」。
  没有 Chrome / playwright 时**跳过**（不假装通过）。

## 抄来的东西要说明出处

`_body()`（花括号配对提取器）**照抄** `tests/test_audit_frontend.py`
（那里又照抄 `tests/test_chat_wiring.py`）—— 本仓的既定做法。
**不要**在这里重写一份「更好的」：两份提取器会漂，而守卫会各自盯不同的东西。
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HTML = _ROOT / "aplus" / "web" / "index.html"
_DATA = _ROOT / "data"

# ────────────────────────────────────────────────────────────────────
# §A · 静态级
# ────────────────────────────────────────────────────────────────────


def _html() -> str:
    assert _HTML.is_file(), f"找不到前端源文件：{_HTML}"
    return _HTML.read_text(encoding="utf-8")


def _style_css() -> str:
    """抽出 `<style>` 块的内容（剥掉 HTML 注释，免得注释里的字面量满足断言）。"""
    html = re.sub(r"<!--.*?-->", "", _html(), flags=re.S)
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    assert m, "index.html 里没有 <style> 块 ⇒ 夹具前提错了"
    return m.group(1)


def _css_blocks(css: str) -> list[tuple[str, str]]:
    """把 CSS 拆成 (选择器, 声明块) 列表。

    ⚠️ **不解析嵌套**：`@media` 里的规则会被当成顶层规则抽出来（选择器是干净的）。
       那是刻意的 —— 本文件要断言的媒体查询规则**本身**就带着 `@media` 的语义，
       所以另有一个 `_media_block()` 专门取媒体查询的原文。
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel = " ".join(m.group(1).split())
        if sel.startswith("@"):
            continue
        out.append((sel, m.group(2)))
    return out


def _decls(body: str) -> dict[str, str]:
    """把声明块解析成 {属性: 值}。"""
    out: dict[str, str] = {}
    for part in body.split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip().lower()] = v.strip()
    return out


def _rule_for(css: str, selector: str) -> dict[str, str]:
    """取选择器里含 `selector` 的规则的声明（**合并**多条，后面的覆盖前面的）。

    ⚠️ 为什么合并而不是取第一条：同一条声明可能被拆在好几条规则里
       （例如「表头底色」一条、「表头不许折行」另一条），
       取第一条会**漏看**后面那条 ⇒ 守卫会对着一个不完整的样式下判决。
    ⚠️ 找不到就**抛**：找不到 ⇒ 守卫在盯别的东西，必须当场喊，
       不能返回一个空 dict 让下游断言静默地过。
    """
    merged: dict[str, str] = {}
    hit = False
    for sel, body in _css_blocks(css):
        if selector in sel:
            hit = True
            merged.update(_decls(body))
    if not hit:
        raise AssertionError(f"CSS 里找不到与 {selector!r} 相关的规则 ⇒ 样式结构变了")
    return merged


def _media_block(css: str, query: str) -> str:
    """取出 `@media <query> { ... }` 的原文（花括号配对，跳过注释）。"""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i = css.find(f"@media {query}")
    assert i >= 0, f"CSS 里找不到 @media {query} ⇒ 窄屏规则没了"
    j = css.index("{", i)
    depth, n = 0, len(css)
    while j < n:
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[i:j]
        j += 1
    raise AssertionError(f"@media {query} 的花括号没配平")


def _body(src: str, header: str) -> str:
    """从 `header` 起，取出配对花括号内的函数体（**不含**收尾的 `}`）。

    ⚠️ 照抄 `tests/test_audit_frontend.py::_body`（那里照抄
    `tests/test_chat_wiring.py`）—— 本仓既定做法，**不要**在这里另写一份。
    """
    i = src.index(header)
    j = src.index("{", i)
    depth, n, state = 0, len(src), None
    while j < n:
        ch = src[j]
        nxt = src[j + 1] if j + 1 < n else ""
        if state is None:
            if ch in "'\"`":
                state = ch
            elif ch == "/" and nxt == "/":
                state = "//"
            elif ch == "/" and nxt == "*":
                state = "/*"
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return src[i:j]
        elif state in ("'", '"', "`"):
            if ch == "\\":
                j += 1
            elif ch == state:
                state = None
        elif state == "//":
            if ch == "\n":
                state = None
        elif state == "/*":
            if ch == "*" and nxt == "/":
                state, j = None, j + 1
        j += 1
    raise AssertionError(f"{header!r} 的花括号没配平 —— 前端结构可能变了，这条守卫失效")


def _fn(body: str) -> str:
    return body + "}"


def _code_block(body: str) -> str:
    body = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), body, flags=re.S)
    return re.sub(r"//[^\n]*", "", body)


# ── node 夹具 ──────────────────────────────────────────────────────

_DOM_STUB = r"""
const store = {};
function el(id) {
  return { id, _html: '', _text: '',
    set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
    set textContent(v) { this._text = String(v); }, get textContent() { return this._text; },
    className: '', hidden: false, style: {},
    querySelector() { return el('x'); }, querySelectorAll() { return []; },
    appendChild() {}, firstElementChild: { style: {} } };
}
global.document = {
  getElementById: (id) => store[id] || (store[id] = el(id)),
  querySelectorAll: () => [], createElement: () => el('n'),
};
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.location = { hash: '' }; global.history = { replaceState() {} };
global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
global.__html = (id) => (store[id] ? store[id]._html : '<MISSING #' + id + '>');
global.__text = (id) => (store[id] ? store[id]._text : '<MISSING #' + id + '>');
"""


def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node，跳过 JS 执行检查")
    return node


def _run_js(tmp_path: Path, script: str, arg: str | None = None) -> str:
    p = tmp_path / "probe.cjs"
    p.write_text(_DOM_STUB + script, encoding="utf-8")
    cmd = [_node(), str(p)] + ([arg] if arg is not None else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"探针自己跑挂了（不是被测代码的问题）：\n{r.stderr[:900]}"
    return r.stdout


def _write_json(tmp_path: Path, obj) -> str:
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _esc_line(src: str) -> str:
    """从 index.html 里**原样**取出 `esc` 的定义（不重新转义一遍）。"""
    m = re.search(r"^const esc = .*$", src, re.M)
    assert m, "index.html 里找不到 `esc` ⇒ 夹具前提错了"
    return m.group(0)


# ── §A-1 · 概览的块顺序 ───────────────────────────────────────────


def _overview_panel_titles() -> list[str]:
    """概览页里各块在**文档顺序**上的标题。

    ⚠️ 先剥 HTML 注释 ⇒ 注释里的话不会满足这条断言（第 13 种形状）。
    """
    html = re.sub(r"<!--.*?-->", "", _html(), flags=re.S)
    start = html.index('id="panel-overview"')
    end = html.index("</section>", start)
    seg = html[start:end]
    titles = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<h2[^>]*>(.*?)</h2>", seg, re.S)]
    assert titles, "概览页一个 <h2> 都没抽到 ⇒ 夹具前提错了"
    return titles


def test_the_action_block_sits_right_after_the_daily_list():
    """★ **顺序本身就是内容**：概览页按「看什么 → 该动吗 → 依据」排。

    🚨 2026-10-21 实测（1440×900）：「持仓降级预警」原来在**第 6 屏**
    （y = 5015px）⇒ 用户读完全部依据才看到「今天要不要动」。
    ⇒ 判据钉**文档顺序**（不是「有没有这个块」——那是恒真的）。
    """
    titles = _overview_panel_titles()
    assert "下一交易日预测" in titles, f"概览页的块变了：{titles}"
    assert "持仓降级预警" in titles, f"概览页的块变了：{titles}"
    i_list = titles.index("下一交易日预测")
    i_act = titles.index("持仓降级预警")
    assert i_act == i_list + 1, (
        f"「持仓降级预警」没有紧跟在名单后面（顺序是 {titles}）"
        " ⇒ 首屏答不完「看什么 / 该动吗」两问")
    # 「依据」类块必须排在动作块**后面**（不许把台账提到动作前面）
    for ev in ("资产配置方案", "今天的名单 · 5 万买得起吗", "实盘配置 vs 判决配置"):
        if ev in titles:
            assert titles.index(ev) > i_act, f"依据块 {ev!r} 排到了动作块前面：{titles}"


# ── §A-2 · 目标进度 tab 的裸 Markdown ──────────────────────────────


def _goal_region(src: str) -> str:
    start = src.find("function goalMetricRow")
    assert start > 0, "index.html 里找不到 goalMetricRow ⇒ 夹具前提错了"
    end = src.find("/* ── 转债纸面账户", start)
    assert end > start, "找不到目标进度段的结尾标记"
    return src[start:end]


def test_the_goal_progress_tab_renders_markdown_instead_of_printing_it(tmp_path):
    """★ **行为**守卫：目标进度那段带 Markdown 的文案，**不许原样打印**。

    🚨 2026-10-21 实测：整份快照有 **143 处 `**加粗**`** 与 **118 处行内代码**，
       而页面原样打印 ⇒ 屏上到处是裸星号与裸引号符号。
    ⇒ 这里**真跑一遍渲染器**（node），喂一个带 Markdown 的夹具，
       看输出里是 `<strong>`/`<code>` 还是裸标记。
    ⚠️ 判据落在**输出**上，不是「源码里有没有调用某个函数」（第 13 种形状）。
    """
    src = _html()
    region = _code_block(_goal_region(src))
    esc_line = _esc_line(src)
    fixture = {
        "id": 1, "title": "T", "status": "UNDECIDABLE", "status_label": "不可判定",
        "registry_class": "D2", "reason": "**不可判定**：|c| < M ⇒ **连符号都定不了**",
        "criterion": "判据 `measure_basket_nav_01.py::judge`",
        "honest_boundary": "**不许**说「未达」",
        "metrics": [{"key": "c", "label": "账户年化 c", "display": "10.4245%",
                     "unit": "pct", "caliber": "**实盘档**", "at": "data/reports/x.json::v"}],
    }
    driver = (
        esc_line + "\n" + region + "\n"
        "const payload = JSON.parse(process.argv[2]);\n"
        "console.log(goalCardHtml(payload));\n"
    )
    out = _run_js(tmp_path, driver, json.dumps(fixture, ensure_ascii=False)).strip()
    assert "**" not in out, f"渲染结果里还有裸的加粗标记：{out[:400]}"
    assert "`" not in out, f"渲染结果里还有裸的行内代码标记：{out[:400]}"
    assert "<strong>" in out, f"加粗没有渲染成 <strong>：{out[:400]}"
    assert "<code>" in out, f"行内代码没有渲染成 <code>：{out[:400]}"


# ── §A-3 · 四态的颜色语义 ─────────────────────────────────────────


def test_the_four_holding_states_use_the_danger_colours_and_not_the_swapped_ones():
    """★ 「该减仓」不许是绿的，「拿着没事」不许是红的。

    🚨 2026-10-21 发现的真实缺陷：原来的映射是
       `REDUCE → --down(绿)`、`HOLD → --up(红)`
       ⇒ 「**该减仓**」被涂成绿色、「**拿着没事**」被涂成红色。
       而**同一段注释自己写着**「绿色会被读成『查过了，没问题』」
       ⇒ 原映射与作者自己的理由**互相打脸**。
    ⇒ 正确映射：REDUCE=红(危险) / WATCH=金(要看一眼) / HOLD=绿(没事) / UNDECIDED=灰(测不出)。

    ⚠️ 判据钉的是**语义映射**（哪个状态用哪个 token），
       不是具体色值 ⇒ 换主题色不会误报。
    """
    css = _style_css()
    # 数字的颜色：状态专属规则
    for state, want, forbid in (("REDUCE", "--up", "--down"),
                                 ("HOLD", "--down", "--up"),
                                 ("WATCH", "--seal", None),
                                 ("UNDECIDED", None, "--up")):
        decls = _rule_for(css, f".ha-chip.s-{state} b")
        color = decls.get("color", "")
        if want is not None:
            assert f"var({want})" in color, (
                f"状态 {state} 的读数颜色是 {color!r}，应当是 var({want})"
                "（这一条曾被写反：把「该减仓」涂绿、把「拿着没事」涂红）")
        if forbid is not None:
            assert f"var({forbid})" not in color, (
                f"状态 {state} 的读数颜色是 {color!r}，不许用 var({forbid})"
                "（绿=「查过了没问题」、红=「危险/做不到」，两处语义都不对）")
    # 「未定」不许是绿的 —— 绿色会被读成「查过了，没问题」
    und = _rule_for(css, ".ha-chip.s-UNDECIDED b").get("color", "")
    assert "var(--down)" not in und, f"「未定（测不出）」不许用绿色：{und!r}"
    # 行左色条：同一套映射
    assert "var(--up)" in _rule_for(css, ".ha-row.REDUCE").get("border-left-color", ""), (
        "持仓行左色条：REDUCE 应当是危险色（红）")
    assert "var(--down)" in _rule_for(css, ".ha-row.HOLD").get("border-left-color", ""), (
        "持仓行左色条：HOLD 应当是安全色（绿）")


def test_a_zero_count_is_quiet_but_still_visible():
    """★ 「0 只」不许用警告色喊人，但**数字必须还在**。

    🚨 「减仓 0」若把那个 0 涂成红色，它就成了全块最扎眼的东西，
       而它说的是「**没有**要减的」—— 这正是本页要消灭的失败模式
       （「不可判定」不许长得像「没变差」那一族）。
    ⇒ 零计数走一条**变暗**的规则（`is-zero`）⇒ 安静，但不是消失。
    """
    css = _style_css()
    decls = _rule_for(css, ".ha-chip.is-zero b")
    color = decls.get("color", "")
    assert color == "var(--faint)", (
        f"零计数的颜色是 {color!r}，应当是 var(--faint)"
        "（安静 = 变暗，不是「换成另一种彩色」）")
    # 「测不出」不参与变暗：**只写在渲染层**（`chip()` 里），
    # 这里刻意**不**再写一份 CSS 规则 —— 两处各写一份 ⇒ 没有守卫（第 97 层）。
    assert ".ha-chip.s-UNDECIDED.is-zero" not in css, (
        "「测不出不参与变暗」被写成了第二份 CSS 规则 ⇒ "
        "哪一处说了算会随改动漂移（这一条只许在渲染层写）")


# ── §A-4 · 四态必须有文字（不许只靠颜色） ────────────────────────


def test_the_four_states_are_written_out_not_only_coloured(tmp_path):
    """★ **行为**守卫：四个状态各有**文字**，颜色只是第二通道。

    ⚠️ 视觉可达性：只靠颜色区分 ⇒ 色觉障碍与黑白打印下四态全糊。
    ⇒ 这里真跑 `renderHoldingsAlert`，看输出里有没有那四个字。
    """
    src = _html()
    fn = _code_block(_fn(_body(src, "function renderHoldingsAlert(")))
    payload = {
        "available": True, "as_of": "2026-10-20", "report": "holdings_alert.json",
        "summary": {"REDUCE": 0, "WATCH": 2, "HOLD": 3, "UNDECIDED": 0},
        "items": [{"code": "001309.SZ", "name": "德明利", "action": "WATCH",
                   "action_label": "观察", "criterion": "c"}],
        "criteria": {"anchors": {"citation": "石川"}, "bottom_decile_percentile": 0.1},
        "entry": {"entry_day": "2026-10-15", "entry_day_source": "state.up"},
        "population": {"n_heldings": 5}, "freshness": {"stamped_at": "x", "verdict": "fresh"},
    }
    driver = (
        "const $ = id => global.document.getElementById(id);\n"
        + _esc_line(src) + "\n" + fn + "\n"
        "const p = JSON.parse(process.argv[2]);\n"
        "renderHoldingsAlert(p);\n"
        "console.log(global.__html('holdings-alert-body'));\n"
    )
    p = tmp_path / "payload.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    js = tmp_path / "probe.cjs"
    js.write_text(_DOM_STUB + driver, encoding="utf-8")
    r = subprocess.run([_node(), str(js), json.dumps(payload, ensure_ascii=False)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"探针自己跑挂了：\n{r.stderr[:900]}"
    out = r.stdout
    for label in ("减仓", "观察", "持有", "未定（测不出）"):
        assert label in out, f"四态里 {label!r} 没有以文字出现 ⇒ 只剩颜色可辨"
    for state in ("REDUCE", "WATCH", "HOLD", "UNDECIDED"):
        assert f"s-{state}" in out, f"状态 {state} 没有自己的类名 ⇒ 颜色无从区分"
    for n in ("0", "2", "3"):
        assert f">{n}<" in out, f"计数 {n} 没有渲染出来"
    # 「测不出」**不参与**变暗：「测不出」不是「零」，是「不知道」。
    # ⚠️ 这一条**刻意只判渲染层**（类名），不在 CSS 里再写一份规则 ——
    #    两处各写一份 ⇒ 两份副本之间没有守卫（第 97 层）。
    import re as _re

    und_chip = _re.search(r'<span class="ha-chip s-UNDECIDED[^"]*">', out)
    assert und_chip, f"「未定」那一格没渲染出来：{out[:400]}"
    assert "is-zero" not in und_chip.group(0), (
        f"「未定（测不出）」被当成零计数调暗了：{und_chip.group(0)}"
        "（把未知说成没有 = 第 16 层）")
    red_chip = _re.search(r'<span class="ha-chip s-REDUCE[^"]*">', out)
    assert red_chip, f"「减仓」那一格没渲染出来：{out[:400]}"
    assert "is-zero" in red_chip.group(0), (
        f"计数为 0 的「减仓」没有被变暗：{red_chip.group(0)}"
        " ⇒ 「减仓 0」会用警告色喊人")


# ── §A-5 · 宽表必须可横滚，不许被裁掉 ────────────────────────────


def test_the_wide_tables_are_scrollable_and_never_clipped_away():
    """★ 窄视口下**内容必须到得了**：要么放得下，要么**可横滚**。

    🚨 2026-10-21 实测（390px / 768px 视口）：
       「买得起吗」表 829px 装在 298px 的容器里、容器是 `overflow:hidden`
       ⇒ 「1 手要 ¥202,070」整列 + 「实盘配置」整列**永久不可达**。
       「买得起吗」的名称列还被压成**逐字竖排**（瑞/立/科/密）。
    ⇒ 修法：容器 `overflow-x:auto` + 短字段 `nowrap` + 长散文给 `min-width`。
    """
    css = _style_css()
    for cls in (".lot-table", ".cfg-table"):
        decls = _rule_for(css, cls)
        ovx = decls.get("overflow-x", "")
        assert ovx in ("auto", "scroll"), (
            f"{cls} 的 overflow-x 是 {ovx!r} ⇒ 窄屏下超出部分**不可达**"
            "（要么 auto/scroll 可横滚，要么表必须放得下）")
        assert decls.get("overflow", "") != "hidden", (
            f"{cls} 仍是 overflow:hidden ⇒ 超出的内容被裁掉且滚不到")
    # 短字段不许折行（中文折行 = 逐字竖排）
    decls = _rule_for(css, ".lot-table th")
    assert "nowrap" in decls.get("white-space", ""), (
        "「买得起吗」的表头没有 nowrap ⇒ 窄屏下中文会被压成逐字竖排")


def test_the_ledger_narrows_its_header_notes_instead_of_being_cut():
    """★ 名单表在窄屏下**必须放得下**（不许横滚）。

    ⚠️ 为什么这一张**不许**加 `overflow-x:auto`：
       横向滚动容器会**接管** `position:sticky` 的参照系
       ⇒ `.ledger thead th` 的吸顶列头失效 —— 而那正是 20 行密排表格最需要的。
    ⇒ 只能靠「放得下」：≤600px 时列名下的注记允许折行、量条缩窄、内边距收窄。
    """
    css = _style_css()
    block = _media_block(css, "(max-width: 600px)")
    assert ".th-note" in block, "≤600px 的媒体查询里没有 .th-note 规则"
    assert "white-space: normal" in block, (
        "≤600px 时列名下的注记没有允许折行 ⇒ 注记把列撑宽 ⇒ 表格放不下")
    assert ".bar-cell .track" in block, "≤600px 时量条没有缩窄 ⇒ 综合分那一列太宽"
    assert re.search(r"\.ledger (th|td)[^{]*\{[^}]*padding", block), (
        "≤600px 时单元格内边距没有收窄 ⇒ 表格放不下")
    # 反向：窄屏下**不许**给名单容器加横滚
    for sel, body in _css_blocks(css):
        if ".ledger-col" in sel and "overflow-x" in body:
            raise AssertionError(
                f"`.ledger-col` 上出现了 overflow-x（{sel}）⇒ 吸顶列头会失效")


def test_the_first_screen_picks_are_one_line_not_a_second_copy_of_row_one(tmp_path):
    """★ 首屏那两张「首选卡」必须是**一行对照**，不许又抄一遍第 01 行。

    🚨 2026-10-21 实测：原来每张 ~230px，而它们的每一项都在下面的表里出现过
       （名称 / 代码 / 20 日涨跌 / 综合分 / 消息面 = 第 01 行）⇒
       首屏被它吃掉 ~180px，而首屏要回答的是「今天看哪 10 只」。
    ⇒ 压成一行，只留「并排比较」这件事本身。
    ⚠️ 判据落在**渲染出来的 HTML** 上（node 真跑一遍 `renderHeroes`），
       不是「源码里有没有某个类名」—— 那是子串断言（第 13 种形状）。
    ⚠️ 浏览器级那条在 `tests/e2e/test_ui_readability_browser.py`；
       这一条是**不依赖浏览器**的那一层（进每一次门禁）。
    """
    src = _html()
    fn = _code_block(_fn(_body(src, "function renderHeroes(")))
    driver = (
        "const $ = id => global.document.getElementById(id);\n"
        + _esc_line(src) + "\n"
        + "const fmtPct = (v, d = 1) => v == null ? '—'"
        + " : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';\n"
        + fn + "\n"
        "renderHeroes({name:'甲', code:'000001.SZ', mom20:0.01, score:1.0,"
        " news_reason:'理由', news_sentiment:0.5},"
        " {name:'乙', code:'000002.SZ', mom20:-0.02, score:-1.0,"
        " news_reason:'理由', news_sentiment:-0.5});\n"
        "console.log(global.__html('heroes'));\n"
    )
    out = _run_js(tmp_path, driver)
    assert 'class="why"' not in out, (
        "首选卡里又出现了第 01 行的副本（近 20 日涨跌 · 综合分）"
        f" ⇒ 首屏被重复内容吃掉：{out[:300]}")
    assert 'class="news"' not in out, (
        "首选卡里又出现了第 01 行的消息面副本"
        f" ⇒ 首屏被重复内容吃掉：{out[:300]}")
    assert "甲" in out and "000001.SZ" in out, "首选卡连名字和代码都没了"


# ────────────────────────────────────────────────────────────────────
# §B · 浏览器级（真 Chromium · 真视口矩阵）
#   ⇒ 在 `tests/e2e/test_ui_readability_browser.py`：
#      本仓 e2e 那套 harness 已经有一个 session 级 playwright 会话，
#      它用完之后**主线程上会留下一个正在运行的 asyncio 循环**
#      （playwright 的同步 API 用 greenlet 驱动事件循环，
#        `run_until_complete` 在 greenlet 切换点被中断 ⇒ 那个循环一直 running）
#      ⇒ 同一进程里再开第二个 `sync_playwright()` 会被 playwright 自己拒绝
#        （`Error: It looks like you are using Playwright Sync API inside the asyncio loop`）
#      ⇒ 浏览器级守卫必须**复用** `tests/e2e/conftest.py` 的夹具。
# ────────────────────────────────────────────────────────────────────


def test_the_window_count_must_explain_why_it_is_this_number(tmp_path):
    """⚠️ 「这个窗口数**为什么**是这个数」必须说在屏上。

    ## 为什么（2026-10-21 实测）

    归属基准在 2026-10-21 从 `weights_hash` 切到 `signal_id`
    ⇒ ⇒ 底稿里**只算当前信号的窗口** ⇒ ⇒ 计数**从 3 掉到 1**
    ⇒ ⇒ 屏上若只写「现在 1 个」，**用户会以为平台把证据弄丢了**
    ⇒ ⇒ 实际是「换了一套信号，重新攒」。

    ⇒ 判据是**行为级**：在 node 里真调 `scopeNote()`，
    **不许**用「全文有没有那句话」的子串断言（第 13 层：
    子串会被注释与契约说明满足）。

    ⚠️ 并且**没有归属信息时必须什么都不说** ——
    凭空造一句解释比不解释更坏。
    """
    src = _html()
    m = re.search(r"^function scopeNote\(scope\) \{.*?^\}", src, re.M | re.S)
    assert m, "index.html 里找不到 `scopeNote` ⇒ 夹具前提变了（先读代码再改这里）"
    fn = m.group(0)
    esc = _esc_line(src)

    scope = {"status": "ATTRIBUTED_BY_SIGNAL_ID", "current_signal_id": "0df5828c"}
    out = _run_js(tmp_path, esc + "\n" + fn + "\n"
                  + "console.log(JSON.stringify(scopeNote("
                  + json.dumps(scope, ensure_ascii=False) + ")));")
    text = json.loads(out)
    assert "只算当前信号的窗口" in text, text
    assert "0df5828c" in text, f"必须点名当前信号：{text}"
    assert "不是" in text and "弄丢" in text, (
        f"必须说清「变小不是把证据弄丢了」：{text}")

    # 没有归属信息 ⇒ **一个字都不许说**（凭空造解释比不解释更坏）
    out2 = _run_js(tmp_path, esc + "\n" + fn + "\n"
                   + "console.log(JSON.stringify(scopeNote(null)));")
    assert json.loads(out2) == "", "没有归属信息时不该给出解释"
