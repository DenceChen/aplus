"""持仓框必须显示**钱包**，不是候选池（前端那一半）。

## 缺陷（同 `tests/test_holdings_layer_reads_the_portfolio.py` 记的那三张脸之一）

`renderHeld(live)` 渲染的是 `live.up` —— 而那是**候选池**（那份 10 只涨腿名单）。
实测那 10 只里 6 只因「1 手 > 每份 ¥5,000」配置层配不起来
⇒ 框里写着「你现在持有 10 只」，其中 **6 只用户根本没买过**。

⇒ 后端已经递了 `live.held_codes`（钱包）+ `live.held_codes_known`（读不读得出来），
本文件钉的是**前端真的用了它**。

## 为什么在 node 里真跑一遍

判据落在**渲染出来的 HTML** 上（喂夹具 → 真调 `renderHeld` → 看输出），
不是「源码里有没有 `held_codes` 这个串」——
那是子串断言，会被注释满足（第 13 种形状）。

## 抄来的东西要说明出处

`_body()` / `_fn()` / `_code_block()` / `_DOM_STUB` / `_esc_line()`
**照抄** `tests/test_ui_readability.py`（本仓的既定做法：
每个文件各带一份，**不要**在这里重写一份「更好的」——
两份提取器会漂，而守卫会各自盯不同的东西）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 前端源文件（照抄 `tests/test_ui_readability.py` 的同名常量）
_HTML = ROOT / "aplus" / "web" / "index.html"


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



def _esc_line(src: str) -> str:
    """从 index.html 里**原样**取出 `esc` 的定义（不重新转义一遍）。"""
    m = re.search(r"^const esc = .*$", src, re.M)
    assert m, "index.html 里找不到 `esc` ⇒ 夹具前提错了"
    return m.group(0)




def _node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node，跳过 JS 执行检查")
    return node


def _run(tmp_path: Path, script: str) -> str:
    p = tmp_path / "probe.cjs"
    p.write_text(_DOM_STUB + script, encoding="utf-8")
    r = subprocess.run([_node(), str(p)], capture_output=True, text=True)
    assert r.returncode == 0, f"探针自己跑挂了（不是被测代码的问题）：\n{r.stderr[:900]}"
    return r.stdout


def _render_held(tmp_path: Path, live: dict) -> str:
    src = _html()
    fn = _code_block(_fn(_body(src, "function renderHeld(")))
    inline_md = _code_block(_fn(_body(src, "function inlineMd(")))
    boldify = _code_block(_fn(_body(src, "function boldify(")))
    driver = (
        "const $ = id => global.document.getElementById(id);\n"
        + _esc_line(src) + "\n"
        + inline_md + "\n"
        + boldify + "\n"
        + fn + "\n"
        + "renderHeld(" + json.dumps(live, ensure_ascii=False) + ");\n"
        + "console.log(global.__html('held-box'));\n"
    )
    return _run(tmp_path, driver)


def _live(**extra) -> dict:
    base = {
        "state": {"as_of": "2026-10-21"},
        "as_of": "2026-10-21",
        "held_since": "2026-10-21",
        "held_since_is_fallback": False,
        "held_since_reliable": True,
        "every": 63,
        "days_since_rebalance": 0,
        "days_to_rebalance": 63,
        "every_matches_live": True,
        "up": [f"C{i}.SZ" for i in range(10)],
        "up_names": [f"候选{i}" for i in range(10)],
        "down": ["D0.SZ"],
        "down_names": ["跌0"],
        "held_codes": ["C0.SZ", "C1.SZ", "C2.SZ", "C3.SZ"],
        "held_codes_known": True,
        "honored": True,
        "reason": None,
    }
    base.update(extra)
    return base


def test_the_holdings_box_counts_the_wallet_not_the_candidate_pool(tmp_path):
    """🚨 「你现在持有 N 只」的 N 必须是**钱包**的只数（4），不是候选池（10）。"""
    out = _render_held(tmp_path, _live())
    assert "你现在持有 4 只" in out, (
        f"持仓框显示的不是钱包的只数：\n{out[:800]}")
    assert "你现在持有 10 只" not in out, (
        f"持仓框显示的是候选池的只数 ⇒ 其中 6 只用户没买过：\n{out[:800]}")


def test_the_candidate_pool_is_labelled_as_not_held(tmp_path):
    """候选池**可以**出现在框里，但**不许**被标成「持有」。"""
    out = _render_held(tmp_path, _live())
    assert "候选池" in out and "不是" in out, (
        f"候选池没有与「持仓」分开标注：\n{out[:800]}")
    assert "候选0" in out, "候选池那批反而没显示：\n%s" % out[:400]
    assert "C0.SZ" in out, "钱包里的票没显示：\n%s" % out[:400]


def test_an_unreadable_wallet_says_so_and_does_not_claim_an_empty_book(tmp_path):
    """🚨 钱包读不出来 ⇒ 必须**说读不出来**。

    报成「空仓」会让用户以为该清仓 ⇒ **错的方向是让人卖光**。
    """
    out = _render_held(tmp_path, _live(held_codes=[], held_codes_known=False))
    assert "读不出来" in out, (
        f"钱包读不出来却没说出来（用户会以为该清仓）：\n{out[:800]}")
    assert "你现在持有 0 只" not in out, (
        f"把「读不出来」写成了「0 只」：\n{out[:800]}")
    # ⚠️ **不许断言「空仓」这个词不在屏上**（过严的守卫，第 112 层）：
    #   提示语里就有一句「这不是「空仓」」⇒ 那个词**必然**在。
    #   要守的是「**声称**空仓」那个**形状**，不是那个词。
    assert "空仓（确实一只都没有）" not in out, (
        f"把「读不出来」说成了「空仓」：\n{out[:800]}")
    assert "确实一只都没有" not in out, (
        f"把「读不出来」说成了「确实没有」：\n{out[:800]}")


def test_a_known_empty_wallet_is_not_called_unreadable(tmp_path):
    """反向正控：真空仓**是**「空仓」，不许与「读不出来」同形。"""
    out = _render_held(tmp_path, _live(held_codes=[], held_codes_known=True))
    assert "空仓" in out, f"真空仓没渲染成「空仓」：\n{out[:800]}"
    assert "读不出来" not in out


def test_the_alloc_panel_gets_the_wallet_count_not_the_candidate_pool():
    """🚨 `renderAlloc(a, heldCount)` 的第二个实参是「你手上现在有几只」。

    ⇒ 它**必须**是**钱包**的只数，不是候选池（`live.up` 恒 10 只，
    其中 6 只从未拿到钱）⇒ 否则屏上「你手上现在的 10 只」与配置方案的
    「目标配置 4 只」**当场对撞**（两个不相干的篮子并排）。
    ⚠️ `null`（**读不出来**）与 `0`（**真空仓**）必须分开传。
    """
    src = _html()
    m = re.search(r"renderAlloc\(\s*await jget\([^)]*\)\s*,\s*(.+?)\)\s*;", src, re.S)
    assert m, "找不到 renderAlloc 的调用点 —— 守卫需要重新评估"
    arg = m.group(1)
    assert "held_codes" in arg, (
        f"renderAlloc 的第二个实参是 {arg!r} ⇒ 它数的是候选池，不是钱包")
    assert ".up" not in arg, (
        f"renderAlloc 的第二个实参里还有 `.up`（候选池）⇒ {arg!r}")
    assert "held_codes_known" in arg, (
        f"renderAlloc 的第二个实参没带「读不读得出来」⇒ {arg!r}")
