"""前端与交互审计守卫（只读审计，不改产品代码）。

## 覆盖边界

E2E（`tests/e2e/`，playwright，别人建的）已经用真浏览器覆盖了：
`/api/chat/stream` 接线、SSE 五类事件、防连点两道闸、`finally` 复位、
静默看门狗、模型名读运行态、iPad 视口不横向滚动。

**这份文件只补 E2E 够不着的两类面**：

1. **注入面** —— E2E 验的是「正常数据下页面能用」，而 XSS 恰恰要靠
   **畸形数据**才暴露。浏览器正常数据下永远是绿的。
   ⇒ 做法：把 index.html 里真正的渲染函数**抠出来用 node 真跑一遍**，
   喂 `<img src=x onerror=alert(1)>`，看输出里出现的是 `&lt;img` 还是 `<img`。
   **不推理，直接执行**——本轮报出的 4 处注入全部是这么复现的。

2. **无障碍/移动的「键盘与触屏可达性」** —— E2E 用鼠标点得到，
   测不出「Tab 键够不到」「手指点不出 tooltip」。

## 为什么断言锚在语法位置

`"${r.code}" in html` 这类全文子串是**恒真陷阱**：注释里写一次就过。
本文件里所有针对注入的断言都是「**在某个函数的函数体内**，
某个 `${...}` 插值**外面套没套 `esc(`**」——
用 `_body()` 逐字符配对花括号取函数体（照抄 `tests/test_chat_wiring.py`），
函数被重排/改缩进都不影响，但函数体里那一行被删/被改会立刻变红。

## 关于「别写对着正确代码变红的过强断言」

本文件刻意区分三件事，而不是一刀切「所有 `${}` 都得 esc」：

* **已覆盖的那一半** —— `renderRows` 的 name/code、
  `renderVerdict` 的全部字段、`loadLogs` 的全部字段本来就有 `esc`。
  本文件**不去断言它们**，因为断言「正确代码碰巧长什么样」没有意义。
* **真正缺的 4 处** —— 见 §①，全部是同一个字段在同一个函数里
  **一边 esc 一边不 esc** 的不对称（`esc(r.name ?? r.code)` 旁边写 `${r.code}`），
  或「已转义的显示名 + 没转义的原始键」成对出现。
  这种不对称是「漏了」而不是「故意」，所以断言它安全。
* **不该管的** —— `fmtPct(x, 2)`、`(p.weight*100).toFixed(0)`、
  `${cls}`（内部枚举字面量）不是注入面，断言它们就是过强。
  ⚠️ 本文件第一版就差点犯这个错：把 `${s.as_of}`（后端日期串）
  也列进注入面——那会对着正确代码变红。

## 每条守卫的「非恒真」自证

§④ 的 `test_the_guards_fire_on_adversarial_input` 用**同一个分析器**
喂一个明知有洞的样本，要求它**报出洞**；再喂一个明知无洞的样本，
要求它**不报**。分析器自己先被守卫一次，否则上面每条都在空断言上打转。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HTML = _ROOT / "aplus" / "web" / "index.html"
_VERDICT_JS = _ROOT / "aplus" / "web" / "verdict.js"

# ── 提取器（照抄 tests/test_chat_wiring.py::_body，勿改） ──────────────

def _html() -> str:
    assert _HTML.is_file(), f"找不到前端源文件：{_HTML}"
    return _HTML.read_text(encoding="utf-8")


def _body(src: str, header: str) -> str:
    """从 `header` 起，取出配对花括号内的函数体（**不含收尾的 `}`**）。

    跳过 `'...'` / `"..."` / `` `...` `` 与 `//` `/* */`，
    否则函数体里的字符串（哪怕是带花括号的那种）会把配对数带偏。
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

# ── 注入分析器（§④ 先守卫它自己） ────────────────────────────────────

# 插值里若出现这些，说明它不是「直接落到 innerHTML 的外部数据」，
# 而是先被某个纯函数格式化过（数字/枚举字面量），不在注入面。
_NEVER_TAINTED = (
    "fmtPct(", "fmtNum(", ".toFixed(", ".toLocaleString(", "String(",
    ".join(", "esc(",
)


def _interpolations(template: str) -> list[tuple[int, str]]:
    """取出一段模板字面量里的全部 `${...}`，返回 (相对行号, 插值内部文本)。

    逐字符配对，跳过字符串里的花括号。
    """
    out, i, n, base = [], 0, len(template), 0
    while True:
        k = template.find("${", i)
        if k < 0:
            break
        depth, j = 1, k + 2
        while j < n and depth > 0:
            if template[j] == "{":
                depth += 1
            elif template[j] == "}":
                depth -= 1
            j += 1
        out.append((template[:k].count("\n"), template[k + 2:j - 1].strip()))
        i = j
    return out


def _unescaped_in_templates(body: str) -> list[tuple[int, str]]:
    """返回函数体里**未套 `esc(`** 的 `${...}` 插值（相对行号, 文本）。

    只看反引号模板字面量（``innerHTML = `...` `` 的那种）。
    单引号/双引号字符串里的 `${}` 不会插值，不算。
    """
    hits: list[tuple[int, str]] = []
    i, n, base = 0, len(body), 0
    while i < n:
        if body[i] != "`":
            i += 1
            continue
        j, state = i + 1, None
        while j < n:
            ch = body[j]
            if ch == "\\":
                j += 2
                continue
            if state is None and ch == "`":
                break
            j += 1
        chunk = body[i:j]
        for rel, expr in _interpolations(chunk):
            if any(tok in expr for tok in _NEVER_TAINTED):
                continue
            # `?? k` / `?? '—'` 这类兜底里的裸值也要看：factorNames[k] ?? k
            hits.append((base + rel, expr))
        base += chunk.count("\n")
        i = j + 1
    return hits


def _code_block(body: str) -> str:
    """去掉块注释与行注释，避免注释里的 `${...}` 被当成代码。"""
    body = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), body, flags=re.S)
    return re.sub(r"//[^\n]*", "", body)


def _line_of(src: str, needle: str) -> int:
    """`needle` 所在行号（1 起）。找不到返回 0，**不抛异常**。

    ⚠️ 这里**故意不抛**：变异验证实测到，抛异常的版本会让 xfail
    把「探针自己坏了」当成「缺陷还在」——守卫当场变成绿的，
    而它守的东西已经没了。找不到就报 0，让下游断言去红。
    """
    for ln, txt in enumerate(src.split("\n"), 1):
        if needle in txt:
            return ln
    return 0


def _templates(src: str) -> list[str]:
    """抽出 `src` 里**所有**反引号模板字面量（含嵌套的内层那个）。

    必须递归处理嵌套，否则抠不出这种：
        `<td ...>${Object.entries(x).map(([k,v]) => `${k}:${v}`).join(' ')}</td>`
    ——外层的 `${}` 里套着内层模板，而要守的正是**内层**那一句。
    """
    def _scan(k: int) -> tuple[int, list[str]]:
        """从 `src[k]`（一个开引号）起，返回 (结束引号下标, 内层模板列表)。"""
        j, depth, inner = k + 1, 0, []
        while j < len(src):
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == "$" and j + 1 < len(src) and src[j + 1] == "{":
                depth += 1
                j += 2
                continue
            if depth and c == "}":
                depth -= 1
                j += 1
                continue
            if c == "`":
                if depth == 0:
                    return j, inner
                end, deeper = _scan(j)        # 内层模板：单独收一份再跳过
                inner.append(src[j:end + 1])
                inner.extend(deeper)
                j = end + 1
                continue
            j += 1
        return j, inner

    out, i = [], 0
    while i < len(src):
        k = src.find("`", i)
        if k < 0:
            break
        end, inner = _scan(k)
        out.extend(inner)
        out.append(src[k:end + 1])
        i = end + 1
    return out


def _backtick_expr(src: str, needle: str) -> str:
    """从 `src` 里抠出**包含 `needle` 的那个反引号模板字面量**原文。

    ★ 这是变异验证逼出来的：探针里手抄一份模板表达式，守卫读的就是
    那份**副本**，把产品代码改了它毫无反应——绿着的死代码。
    ⇒ 必须让探针跑**文件里那段原文**。

    嵌套模板（外层 `${}` 里套内层反引号）由 `_templates` 递归处理。

    找不到就返回 `''`，让下游断言去红，而不是在这里静默给个替代品。
    """
    hits = [t for t in _templates(src) if needle in t]
    return min(hits, key=len) if hits else ""

# ── node 执行夹具 ─────────────────────────────────────────────────────

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


def _run_js(tmp_path: Path, script: str, *, want_stderr: bool = False):
    """把脚本写到临时 .cjs 跑 node，返回 stdout（或 stderr）。

    探针用 `console.error` 往 stderr 记「这一跑真的收到了哪个 HTTP 状态」，
    而被断言的 DOM 文本留在 stdout —— 两条流互不干扰，
    于是「两次输出是否相同」这个比较不会被诊断日志污染。
    """
    p = tmp_path / "probe.cjs"
    p.write_text(_DOM_STUB + script, encoding="utf-8")
    r = subprocess.run([_node(), str(p)], capture_output=True, text=True)
    assert r.returncode == 0, f"探针自己跑挂了（不是被测代码的问题）：\n{r.stderr[:900]}"
    if want_stderr:
        return r.stderr
    return r.stdout


def _fn(body: str) -> str:
    """把 `_body()` 的结果补上收尾 `}`，变成可直接 eval 的完整函数声明。

    ⚠️ `_body()` **故意**不含收尾花括号（它只给正则看内部），
    直接 eval 会报 `Unexpected end of input` —— 第一版就是这么栽的。
    """
    return body + "}"

# ── 探针构造：**从文件里抠原文**，不在测试里手抄副本 ────────────────────
#
# ⚠️ 变异验证实测到的最重要一条：探针里手抄一份模板表达式，
#   守卫守的就是那份**副本**——把 index.html 改了它毫无反应。
#   第一次变异验证有 2 条就是这样被"打绿"的。
# ⇒ 下面每个函数都返回「跑文件里那段原文」的脚本。

def _probe_alloc(src: str, *, name: str, code: str) -> str:
    """把 index.html 里**真正的** `renderAlloc` 抠出来，用 node 真跑一遍。

    `name` / `code` 是**注入点**：模板里 `<b>${esc(p.name ?? p.code)}</b>` 走
    `esc()`，而同一 `<td>` 的 `<span>${p.code}</span>` 没有。两次调用分别把
    探针打在两路上，差集才是结论 —— 单跑一次只能说「有一路没转义」，
    跑两次才能说「esc 接好了，漏的是那一路」。
    """
    body = _code_block(_fn(_body(src, "function renderAlloc(")))
    return (
        "const $ = id => global.document.getElementById(id);\n"
        "const esc = s => String(s ?? '').replace(/[&<>\"']/g, c =>\n"
        "  ({'&':'&amp;','<':'&lt;','>':'&gt;','\"' :'&quot;',\"'\":'&#39;'}[c]));\n"
        + body + "\n"
        + "renderAlloc({ date: 'd', cash_weight: 0.1, capital: 100000,\n"
        + f"  positions: [{{ name: {name}, code: {code}, weight: 0.1,\n"
        + "    amount: 10000, score: 1.2 }] });\n"
        + "console.log(global.__html('alloc-body'));\n"
    )


def _probe_history(src: str, *, name: str, code: str) -> str:
    """同 `_probe_alloc`：两路探针（`esc(r.name)` vs `${r.code}`）打进
    `renderHistory` 里真正的 `const col = dir =>` 闭包。"""
    i = src.index("const col = dir =>")
    end = "|| '<div class=\"empty\" style=\"padding:14px\">无</div>'"
    decl = _code_block(src[i:src.index(end, i) + len(end)])
    return (
        "const esc = s => String(s ?? '').replace(/[&<>\"']/g, c =>\n"
        "  ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
        "const fmtPct = (v, d = 1) => v == null ? '\u2014'\n"
        "  : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';\n"
        "const verdictZh = { hit: '命中' };\n"
        "const day = [{ direction: 'up', name: " + name + ", code: " + code + ",\n"
        "  actual_pct: 0.012, result: 'hit' }];\n"
        "const passes = () => true;\n"
        + decl + "\nconsole.log(col('up'));\n"
    )


_Q = lambda v: "\"" + v + "\""      # 造一个 JS 字符串字面量

# ── 探针：后端 5xx 与「本来就没有」在界面上是否可区分 ──────────────────
#
# 三处都跑 index.html 里**真正的**加载函数，只换掉数据源：
#   ok=True  → 后端返回空集合（用户确实还没有数据）
#   ok=False → 后端 500（服务挂了/炸了）
# 然后比对两次渲染出来的 DOM 文本。

def _prelude(src: str) -> str:
    """探针运行前的环境。**`jget` / `isApiMissing` 从源文件抽取，不手抄。**

    ⚠️ 为什么改成抽取：`_PRELUDE` 原先**手抄**了一份 `jget`。
    手抄的副本只靠一条守卫（`test_the_jget_stub_is_not_what_makes_the_probe_equal`）
    比对，而那次比对只问「两条跑出来一样吗」—— **不问内容**。
    于是今天修好 `jget`（给它加上 `e.status`）之后，
    探针里的副本仍是旧的：不设 `e.status` ⇒ `isApiMissing` 恒假 ⇒
    500 跑渲染出「尚无回测记录」⇒ **守卫又绿了，而且是绿的错误理由。**

    这正是 §1085 记的那类自伤（探针缺陷被读成产品缺陷），
    只是这次分叉来自**被测代码的修复**而非探针的初版 ——
    说明「手抄副本 + 只比对行为」这条防线**方向不对**：
    它能发现副本缺失，发现不了副本过时。

    ⇒ 副本不该存在。抽取出来的才是同一份东西。
    下面 `test_the_prelude_runs_the_real_jget_from_the_source_file` 钉住这一点。
    """
    jget = _code_block(_fn(_body(src, "async function jget(")))
    missing = _code_block(_fn(_body(src, "function isApiMissing(")))
    return (
        "const $ = id => global.document.getElementById(id);\n"
        "const esc = s => String(s ?? '').replace(/[&<>\"']/g, c =>\n"
        "  ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
        "const fmtPct = (v, d = 1) => v == null ? '\\u2014'\n"
        "  : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';\n"
        "const verdictZh = { hit: '命中', miss: '未中' };\n"
        "const state = { filter: 'all', history: { rows: [], dates: [] }, backtest: null };\n"
        "const passes = () => true;\n"
        + jget + "\n"
        + missing + "\n"
    )


# 共享 fetch 桩。`console.error` 走 stderr，被断言的 DOM 文本走 stdout ——
# 两条流分开，于是「两次输出是否相同」这个比较不会被诊断日志污染。
_FETCH_STUB = (
    "global.fetch = async (u) => { console.error('HTTP', String(u), OKSTATUS);\n"
    "  return PAYLOAD; };\n"
)

_DETAIL_BODY = "{ ok: false, status: %d, json: async () => ({ detail: 'boom' }) }"


def _fetch(status: int, payload: str) -> str:
    return (_FETCH_STUB
            .replace("OKSTATUS", f"{'true' if 200 <= status < 300 else 'false'}, {status}")
            .replace("PAYLOAD", payload))


def _probe_history_empty_vs_500(src: str, *, server_500: bool) -> str:
    """跑真 `loadHistory()` + 真 `renderHistory()`，只把数据源换成 500。"""
    load = _code_block(_fn(_body(src, "async function loadHistory(")))
    render = _code_block(_fn(_body(src, "function renderHistory(")))
    stub = (_fetch(500, _DETAIL_BODY % 500) if server_500
            else _fetch(200, "{ ok: true, status: 200, json: async () => ({ rows: [], dates: [] }) }"))
    return (
        "(async () => {\n"
        + _prelude(src) + stub
        + load + "\n" + render + "\n"
        + "function renderCompareSummary() { $('compare-summary').textContent = 'x'; }\n"
        + "await loadHistory();\n"
        + "console.log(global.__html('history-body'));\n"
        + "})();\n"
    )


def _probe_backtest_notfound_vs_500(src: str, *, code: int = 404) -> str:
    """跑真 `loadBacktest()`：默认对照跑给 **404**，传 `code=500` 就是被测跑。

    ★ 对照状态码不是我随手挑的，是从后端源码读出来的
    （`aplus/webapp.py:442`）：`/api/backtest` 在没有回测时
    `raise HTTPException(404, "尚无回测记录")`。
    ⇒ 「真的还没有回测」= 404，「服务炸了」= 5xx，
    **在 HTTP 这一层它们本来就是两件事**，前端没有理由合并。

    第一版把对照跑写成「返回一份合法数据」，断言恒过——
    「有数据」当然和「500」不一样，证明不了任何东西。
    铁律那条「如果它没坏呢」在这里的落地就是：
    对照样例必须和被测样例**只差一个真正有区别的东西**。
    """
    body = _code_block(_fn(_body(src, "async function loadBacktest(")))
    # ⚠️ detail 必须随状态码变。给 500 也塞一句「尚无回测记录」的话，
    #    `catch (e) { e.message === '尚无回测记录' ? ... }` 那种真修法会照样
    #    渲染出同一句话，守卫就一直绿——这是我自己第一版犯的错。
    detail = "尚无回测记录" if code == 404 else "boom"
    stub = _fetch(code, "{ ok: false, status: %d, json: async () => ({ detail: '%s' }) }" % (code, detail))
    return (
        "(async () => {\n"
        + _prelude(src)
        + "function lineChart() {}\n"
        + stub + body + "\n"
        + "await loadBacktest();\n"
        + "console.log(global.__text('bt-sub'));\n"
        + "})();\n"
    )


def _probe_logs_empty_vs_500(src: str, *, server_500: bool) -> str:
    """跑真 `loadLogs()`，只把数据源换成 500。"""
    body = _code_block(_fn(_body(src, "async function loadLogs(")))
    stub = (_fetch(500, "{ ok: false, status: 500, json: async () => { throw new Error('not json'); } }")
            if server_500
            else _fetch(200, "{ ok: true, status: 200, json: async () => [] }"))
    return (
        "(async () => {\n"
        + _prelude(src) + stub + body + "\n"
        + "await loadLogs('');\n"
        + "console.log(global.__html('log-rows'));\n"
        + "})();\n"
    )


def _probe_weekday_labels(src: str) -> str:
    """跑真 `nextTradingDayLabel()`，对一周七天各问一次「它指的是星期几」。

    探针**自己**判断，不靠断言去猜：把返回串里的「M月D日」按已知的年还原成
    Date，交给 `getDay()`。这样守卫问的是「预测标题指的日子到底能不能交易」，
    而不是「源码里那行 add 的写法对不对」——后者换个正确写法就误报了。
    """
    fn = _code_block(_fn(_body(src, "function nextTradingDayLabel(")))
    return (
        "(async () => {\n"
        + fn + "\n"
        + "const names = ['周日','周一','周二','周三','周四','周五','周六'];\n"
        + "const out = [];\n"
        + "for (const iso of ['2026-10-12','2026-10-13','2026-10-14','2026-10-15',\n"
        + "                  '2026-10-16','2026-10-17','2026-10-18']) {\n"
        + "  const [y, m, dd] = iso.split('-').map(Number);\n"
        + "  const base = new Date(y, m - 1, dd);\n"
        + "  const label = nextTradingDayLabel(base);\n"
        + "  const hit = /(\\d+)月(\\d+)日/.exec(label);\n"
        + "  const back = hit ? new Date(y, Number(hit[1]) - 1, Number(hit[2])) : null;\n"
        + "  out.push(names[base.getDay()] + '|' + label + '|'\n"
        + "        + (back ? names[back.getDay()] : 'PARSE_FAIL'));\n"
        + "}\n"
        + "console.log(out.join('\\n'));\n"
        + "})();\n"
    )


def _esc_decl(src: str) -> str:
    """抠出 index.html 里**那个** `esc` 定义。

    探针必须用文件里这一份，而不是抄一份：变异验证里给 `loadEvolution`
    加上 `esc(...)` 时，抄写版探针根本没定义 `esc` → `ReferenceError` →
    xfail 报「缺陷还在」→ 守卫对着**已修好的**代码继续睡。
    """
    m = re.search(r"^const esc = .*;$", src, re.M)
    assert m, "抠不出 esc 的定义 —— 提取器坏了"
    return m.group(0)


def _probe_chips(src: str) -> str:
    """跑 `loadEvolution` 里**权重 chip 那段模板的原文**。

    ★ 模板是从 index.html 抠出来的，不是抄的。变异验证抓出来的正是抄写版：
    在 index.html 给 `factorNames[k]` 加上 esc()，抄写版探针毫无反应，
    守卫对着已修好的代码报「仍然有洞」——一个永远睡过去的守卫。

    chip 模板本身是 `.map(([k, v]) => ...)` 的返回值，探针把 `k`/`v` 提到
    顶层声明，再原样 eval 那段模板：模板文本零改动。
    """
    tmpl = _backtick_expr(src, "factorNames[k]")     # ← 文件里的原文
    return (
        _esc_decl(src) + "\n"
        "const factorNames = { mom20: '动量' };\n"
        "const w = { source: 'default' };\n"
        "const k = KEY_PAYLOAD;\n"
        "const v = 1;\n"
        "const html = " + (tmpl or "undefined /* 抠不出模板 */") + ";\n"
        "console.log(html);\n"
    ).replace("KEY_PAYLOAD", _Q(_XSS_PAYLOAD))


def _probe_ledger(src: str) -> str:
    """跑台账那段**嵌套内层模板** `${k}:${v}` 的原文。

    它藏在外层的 `${...map(... => `${k}:${v}`...)}` 里，
    非递归的提取器抠不出来——第一版就是这么静默返回空的。
    """
    tmpl = (_backtick_expr(src, "${esc(k)}:${v}")     # ← 内层模板的原文（修好后带 esc）
            or _backtick_expr(src, "${k}:${v}"))
    return (
        _esc_decl(src) + "\n"
        "const k = KEY_PAYLOAD;\n"
        "const v = 0.5;\n"
        "const wts = " + (tmpl or "undefined") + ";\n"
        "console.log(wts);\n"
    ).replace("KEY_PAYLOAD", _Q(_XSS_PAYLOAD))

# ── ① 注入面：4 处实测可利用的 innerHTML 未转义 ─────────────────────────
#
# 每条都是**实际执行**渲染函数后，看到输出里留着可执行的 `<img ...>`，
# 而同一行的相邻字段却是 `&lt;img ...&gt;`。
#
# 为什么「相邻字段已转义」是判据的关键：它排除了「这个函数压根不转义」
# 这种设计选择，把结论收敛到「漏了一个字段」。

_XSS_PAYLOAD = '<img src=x onerror=alert(1)>'
_ESCAPED_PAYLOAD = '&lt;img src=x onerror=alert(1)&gt;'

# ── ✅ 这份清单里的缺陷在 2026-10-14 全部修复完毕，xfail 标记已摘除 ──
#
# 当时（缺陷未修）的做法与现在的做法，理由都留在这里：
#
#   守卫断言的是**正确行为**，用 `xfail(strict=True)` 挂上：
#   缺陷在时全绿（记录在案），有人修好的那一刻立刻红（XPASS strict），
#   逼着把标记摘掉 —— 这就是它们全部被摘掉的方式。
#
#   ⚠️ 为什么不用 `xfail(strict=False)`：那样修好了也是绿的，
#   守卫就永远睡过去了 —— 正是本仓记过的「绿着的死代码」。
#   ⚠️ 为什么不直接写成「断言缺陷存在」：那样它**方向是反的**，
#   修好了反而变红，会被当成误报删掉，等于把修复赶走。
#
# ★ 现在这些测试是**普通绿测试**，不是「记录已知缺陷」的台账。
#   若将来再出现同类缺陷，正确做法是重新挂 `xfail(strict=True)`，
#   **不要**把断言反过来写成「期望有洞」。

# ── ①b 探针自身的健康守卫（不带任何标记） ─────────────────────────────
#
# ★ 这几条为什么必须与缺陷守卫分开：xfail 会把**测试体内任何** AssertionError
#   都报成「缺陷还在」。第一版把「夹具前提」也写在 xfail 里，
#   结果一旦有人修好那行，前提先炸 → xfail 照旧 → **守卫当场睡过去**
#   （变异验证实测：1391/1428 两条就是这么被"打绿"的）。
# ⇒ 探针能不能跑通、payload 有没有真的到达渲染点，一律在这里钉。

# `_MARKER` 故意**不含任何需要转义的字符** —— 这样「它出现在输出里」
# 这件事在修好前后都成立，健康守卫不会因为缺陷被修而自己变红。
# 第一版用 payload 本身当下标，等于把「缺陷还在」写进了夹具前提：
# 缺陷一修，§①b 先炸，然后被当成探针坏了。
_MARKER = "ZZCODEZZ"


def test_the_alloc_probe_really_renders_both_cells(tmp_path):
    """★ 探针健康度 —— 守的是守卫自己，不是产品。

    坏掉的症状（用户视角）：无。坏掉的**后果**是 §① 那条永远判「没坏」，
    而注入其实还在（变异验证实测：把两处洞都修好，守卫照样绿）。

    分两跑，**两跑都必须是与修复无关的**：
      · 对照跑（payload 放 `p.name`，走 `esc()`）⇒ 必出 `&lt;img ...&gt;`。
        它证明 esc() 接上了、DOM 收集器也在工作——排除「这个函数压根不转义」
        这种设计选择。
      · 落格跑（标记放 `p.code`）⇒ 必出 `ZZCODEZZ`。
        它证明 §① 打 payload 的那个 `<span>` 真的被渲染了。
    """
    src = _html()

    ctl = _run_js(tmp_path, _probe_alloc(src, name=_Q(_XSS_PAYLOAD), code=_Q("000001")))
    assert _ESCAPED_PAYLOAD in ctl, (
        f"对照跑没出转义结果 ⇒ esc()/DOM 收集器这条链断了，§① 的对照前提不成立：{ctl[:400]}"
    )
    assert _XSS_PAYLOAD not in ctl, "对照跑里 payload 没被转义——那就不是「漏一路」而是「全漏」"

    hit = _run_js(tmp_path, _probe_alloc(src, name=_Q("平安银行"), code=_Q(_MARKER)))
    assert _MARKER in hit, (
        f"落格跑没渲染出代码那一格 ⇒ §① 打在了一个不存在的位置上。输出：{hit[:400]}"
    )


def test_the_history_probe_really_renders_both_cells(tmp_path):
    """★ 同上，对 `renderHistory` 的 `const col = dir =>`。"""
    src = _html()

    ctl = _run_js(tmp_path, _probe_history(src, name=_Q(_XSS_PAYLOAD), code=_Q("000001")))
    assert _ESCAPED_PAYLOAD in ctl, f"对照跑没出转义结果 ⇒ 前提不成立：{ctl[:400]}"
    assert _XSS_PAYLOAD not in ctl, "对照跑里 payload 没被转义——那就不是「漏一路」而是「全漏」"

    hit = _run_js(tmp_path, _probe_history(src, name=_Q("平安银行"), code=_Q(_MARKER)))
    assert _MARKER in hit, f"落格跑没渲染出代码位 ⇒ §① 打偏了。输出：{hit[:400]}"


def test_the_chips_probe_really_runs_the_template_from_the_source_file(tmp_path):
    """★ 探针健康度：chip 探针跑的是**文件里那段模板原文**，不是测试里的副本。

    这是变异验证抓出来的：手抄副本的版本，在 index.html 里加 esc 毫无反应。
    ⇒ 这里断言「抠出来的模板里确实带着 factorNames 的插值」，
    且 payload 恰好出现 **2** 次（显示名一次 + 键名一次，两条路径都漏）。
    """
    src = _html()
    tmpl = _backtick_expr(src, "factorNames[k]")
    assert tmpl, "抠不出含 factorNames 的模板 —— 提取器坏了（结构变了？）"
    assert "factorNames[k]" in tmpl, "抠出来的模板不是权重 chip 那段"
    out = _run_js(tmp_path, _probe_chips(src))
    # ★ 断言「**转义后**出现 2 次」，不是「原始 payload 出现 2 次」。
    #   这条守卫的职责是证明**探针真的打到了两个插值点**（显示名 + 键名），
    #   「原始 payload 出现 2 次」把「打到了」和「有洞」混成了一件事 ——
    #   缺陷一修好，这个前提就失效，于是守卫在一个**正确**的代码上报红。
    #   转义后仍是 2 次，才说明两个点都执行了、都过了 esc()。
    assert out.count(_ESCAPED_PAYLOAD) == 2, (
        f"探针健康前提错：转义后的 payload 出现 {out.count(_ESCAPED_PAYLOAD)} 次"
        f"（预期 2 = 显示名 + 键名两个插值点都跑到了）。输出：{out[:300]}"
    )
    assert _XSS_PAYLOAD not in out, "原始 payload 竟然出现在输出里 ⇒ esc() 没生效"


def test_the_ledger_probe_really_runs_the_template_from_the_source_file(tmp_path):
    """★ 同上：台账探针必须跑**文件里那个嵌套内层模板** `${k}:${v}`。

    它藏在外层模板的 `${}` 里（外层 `<td>${...map(... => `${k}:${v}`...)}</td>`），
    非递归的提取器抠不出来——第一版就是这么静默返回空的。
    """
    src = _html()
    # 探针按**当前源码里的插值形状**找内层模板。修好之后它带上了 esc()，
    # 形状从 `${k}:${v}` 变成 `${esc(k)}:${v}` —— 提取器的 needle 必须跟着改，
    # 否则它会静默返回空字符串，而下面的断言会因为「输出里什么都没有」而误判。
    # ★ 这里用「在 tmpl 里找插值点」而不是钉一个字面量，
    #   免得每次修转义都要动这个 needle。
    tmpl = (_backtick_expr(src, "${esc(k)}:${v}")
            or _backtick_expr(src, "${k}:${v}"))
    assert tmpl, "抠不出内层权重串模板 —— 提取器没处理嵌套（或源码形状变了）"
    out = _run_js(tmp_path, _probe_ledger(src))
    # 同上：断言「转义后的 payload 在」，把「探针打到了」与「有没有洞」分开。
    assert _ESCAPED_PAYLOAD in out, (
        f"探针健康前提错：转义后的 payload 没出现在台账权重串里。输出：{out[:300]}"
    )
    assert _XSS_PAYLOAD not in out, "原始 payload 竟然出现在输出里 ⇒ esc() 没生效"

# ── ① 注入面本体：断言**正确行为**（缺陷已于 2026-10-14 修复） ──────────
#
# 每条都把 index.html 里真正的渲染代码抠出来**用 node 真跑一遍**，
# 喂 `<img src=x onerror=alert(1)>`，看输出里是 `&lt;img`（好）还是 `<img`（洞）。
#
# 为什么「相邻字段已转义」是判据的关键：它排除了「这个函数压根不转义」
# 这种设计选择，把结论收敛到「漏了一个字段」。
#
# 断言方向必须是**正确行为**，绝不能写成「断言缺陷存在」：
# 后者在有人修好的那一刻变红，会被当成误报删掉，等于把修复赶走。
# xfail(strict=True) 让它今天是绿的、修好时立刻红（XPASS strict），逼着摘标记。

def test_renderAlloc_escapes_the_code_cell_not_only_the_name(tmp_path):
    """★ 实测注入：`index.html:940` —— `renderAlloc()` 的 `${p.code}`。

    源码形状（同一行里两套写法）：
        <b>${esc(p.name ?? p.code)}</b> <span ...>${p.code}</span>

    坏掉的症状（用户视角）：配置方案表里，某只票的**代码**那一格能执行任意脚本。
    `p.code` 与已被 `esc()` 的 `p.name` 来自同一条记录、同一份后端 JSON——
    没有任何理由只保护其中一个。

    探针健康度由 `test_the_alloc_probe_really_reaches_the_code_cell` 单独钉。
    """
    line = _line_of(_html(), "esc(p.name ?? p.code)")
    out = _run_js(tmp_path, _probe_alloc(_html(), name=_Q("平安银行"), code=_Q(_XSS_PAYLOAD)))
    assert _XSS_PAYLOAD not in out, (
        f"第 {line} 行的 ${{p.code}} 未转义 ⇒ 持仓表代码格可执行注入。"
    )


def test_renderHistory_escapes_the_code_cell_not_only_the_name(tmp_path):
    """★ 实测注入：`index.html:987` —— `renderHistory()` 的 `${r.code}`。

    源码形状：
        <div class="who"><b>${esc(r.name ?? r.code)}</b><span>${r.code}</span></div>

    坏掉的症状（用户视角）：历史·对比页里每条记录的代码位置能执行脚本。

    这条与 `renderRows`（第 923 行）构成**同仓内的反证**：
    同一个 `r.code` 字段，在 `renderRows` 里是 `esc(r.code ?? '')`，
    在 `renderHistory` 里是裸的 `${r.code}`。
    同一份数据、同一个字段、两处写法不同 ⇒ 这不是设计选择，是漏了。
    """
    src = _html()
    line = _line_of(src, "esc(r.name ?? r.code)")
    out = _run_js(tmp_path, _probe_history(src, name=_Q("平安银行"), code=_Q(_XSS_PAYLOAD)))
    assert _XSS_PAYLOAD not in out, (
        f"第 {line} 行的 ${{r.code}} 未转义 ⇒ 历史页代码位可执行注入。"
    )


def test_the_evolution_weight_chips_escape_the_factor_key(tmp_path):
    """★ 实测注入：`index.html:1391` —— 权重 chip 的 `${factorNames[k] ?? k} ${k}`。

    源码形状：
        <span>${factorNames[k] ?? k} ${k}</span>

    `factorNames` 只有 5 个键（mom20/rev5/vol20/vsur/turn20），
    兜底 `?? k` 就是「认不出的因子名原样显示」——而 k 来自后端权重 JSON 的键，
    **没转义**。同一个 `<span>` 里 `${k}` 还出现第二次。

    坏掉的症状（用户视角）：进化页权重条的因子名位置能执行脚本，
    且它连出现两次（显示名一次、键名一次）。

    ⚠️ 这里**不能**断言「factorNames 里那 5 个键渲染时也要转义」——
    那是查表命中，写死键名不构成注入面（第一版差点写成这种过强断言）。
    只钉兜底那一路：`?? k` 的 `k`。
    """
    line = _line_of(_html(), "factorNames[k] ?? k")
    out = _run_js(tmp_path, _probe_chips(_html()))
    assert _XSS_PAYLOAD not in out, (
        f"第 {line} 行的权重键兜底 `factorNames[k] ?? k` / 裸 `k` 未转义 ⇒ 权重条可执行注入。"
    )


def test_the_evolution_ledger_escapes_the_weight_key(tmp_path):
    """★ 实测注入：`index.html:1428` —— 台账里的 `${k}:${v}`。

    源码形状（**嵌套在内层模板**里）：
        const wts = `<td ...>${Object.entries(ev.weights)
          .map(([k, v]) => `${k}:${v}`).join(' ')}</td>`;

    这是**整行台账的权重串**，k 直接进 innerHTML。

    坏掉的症状（用户视角）：进化页每一行台账的权重列能执行脚本。

    ⚠️ 边界说明：同一函数里 `e.protocol` 是**用了** `esc()` 的（第 1436 行）。
    同一张表里一处转一处不转 ⇒ 漏，不是取舍。
    """
    line = _line_of(_html(), "${k}:${v}")
    out = _run_js(tmp_path, _probe_ledger(_html()))
    assert _XSS_PAYLOAD not in out, (
        f"第 {line} 行的 `${{k}}:${{v}}` 未转义 ⇒ 台账权重列可执行注入。"
    )

# ── ② 「如果它没坏呢？」—— 已正确转义的路径，**不许**被守卫改成红灯 ──────
#
# 这一节存在的理由：本仓今天出现过 7 次「把证据读成坏了而其实没坏」。
# 最容易误判的正是这一节：只看「用了 innerHTML + 拼了字段」就判 XSS。
# 下面每条都是**负结果**，它们的作用是把「已覆盖的部分」钉住，
# 防止将来有人顺手「统一风格」时把正确的 esc 拆掉。

def test_renderRows_escapes_name_and_code_both():
    """负结果钉桩：`renderRows()`（第 917~927 行）name 与 code **都**转义了。

    这是上面 `renderHistory` 那条的对照组：同仓、同字段、两种写法。
    钉住它，将来若有人把这里的 `esc(r.code ?? '')` 也删掉，
    两条守卫会一起给出线索，而不是让「不一致」这个证据消失。
    """
    body = _code_block(_body(_html(), "function renderRows("))
    assert "esc(r.name ?? r.code)" in body, "renderRows 的 name 失去转义"
    assert "esc(r.code ?? '')" in body, "renderRows 的 code 失去转义"
    # news_reason 由 newsChip() 负责，那条路径也有 esc
    assert "esc(reason)" in _code_block(_body(_html(), "function newsChip(")), \
        "newsChip 的 reason 失去转义"


def test_the_docket_and_the_error_banner_never_take_raw_html_from_the_backend():
    """负结果钉桩：判决板警示条与错误横幅都过了 `esc()` 或受控的 detailHtml。

    ⚠️ **这里刻意不写「detailHtml 也必须转义」**——
    `detailHtml` 由 `verdict.js` 的 `renderCaveat()` 自己生成，
    它内部是「先 esc 再把 `**` 还原成 `<b>`」，顺序是对的；
    那条路径**不接受调用方传入 HTML**（见 index.html 第 851~853 行注释）。
    把它判成缺陷就是把预注册条款明确排除的对照臂当成了失败。
    ⇒ 只钉「外部字符串必须经过 esc」。
    """
    src = _code_block(_html())
    docket = _body(src, "function renderDocket(")
    assert "esc(warnOut.headline)" in docket, "判决板标题失去转义"
    assert "warnOut.detailHtml ?? esc(warnOut.detail)" in docket, \
        "判决板正文：非 detailHtml 分支必须走 esc"
    # 错误展示：`showFault` 的正文必须过 esc
    fault = _body(src, "function showFault(")
    assert "esc(why)" in fault, "showFault 的错误正文没有转义"
    assert "n.innerHTML = `<b>${head}</b>`" in _code_block(fault), \
        "showFault 的标题分支写法变了（head 是内部字面量枚举，不该要求转义）"
    # ⚠️ `send()` **不在**这个断言名单里 —— 它全程用 `textContent` 上屏，
    # 不走 innerHTML，本来就不需要 esc。第一版把它算进来，
    # 结果对着**正确实现**变红——这正是「过强断言把正确代码判成缺陷」。
    send_body = _code_block(_body(src, "async function send("))
    assert ".innerHTML" not in send_body, \
        "send() 改用 innerHTML 上屏了 —— 那条路径必须重新做转义审查"


def test_esc_covers_every_character_the_sinks_actually_use():
    """★ 夹具前提守卫：`esc()` 覆盖本文件注入面用到的全部字符集。

    为什么不写「`esc()` 必须转义所有可能字符」——
    那是不可能的（引号/反斜杠/空格都有正当用法），也是过强断言。
    只钉**本文件里实际出现过的字符集**：`& < > " '`。
    若哪天有人把 `esc` 改成只转 `< >`，这条会先红。

    坏掉的症状（用户视角）：`esc` 收窄后，含引号的字段能突破属性边界。
    """
    src = _html()
    m = re.search(r"const esc = s => String\(s \?\? ''\)\.replace\(/\[([^\]]+)\]/g", src)
    assert m, "esc() 的写法变了（找不到 replace(/[...]/)），这条守卫失效"
    charset = set(m.group(1))
    missing = {c for c in "&<>\"'" if c not in charset}
    assert not missing, f"esc() 不再覆盖 {sorted(missing)} —— 属性/文本边界可被突破"

# ── ③ 无障碍与移动（E2E 的鼠标够不着的面） ────────────────────────────

def test_the_tab_widget_has_roving_tabindex_so_only_one_tab_is_a_tab_stop():
    """★ 键盘可达性：7 个 tab 按钮**全部**在 Tab 序列里（无 roving tabindex）。

    ARIA 的 tab 模式要求 roving tabindex：同一时刻只有一个 tab 可 Tab 到达，
    组内用方向键移动（这个文件**已经**实现了方向键，见下一条）。
    缺 roving 的症状（用户视角）：用户按 Tab 想跳过导航，
    却必须连按 7 次才能进到正文——每按一次焦点都停在一个 tab 上。

    ⚠️ 不变量是「**恰好一个** tab 是 Tab 停靠点（`tabindex="0"`），
    且它是选中项」，不是「至少有一个 tabindex」。
    原先只断言「存在 tabindex」，留了个洞：**7 个全写 `tabindex="-1"` 时
    整个导航一个都 Tab 不到**（键盘完全不可达），守卫照样全绿 ——
    变异实测过，把选中项也改成 -1 之后两条守卫依然 passed。
    「不断言必须等于 0」的顾虑是对的（不该逼死写法），
    但正解不是放宽成「≥0 个」，而是收紧到「**恰好 1 个，且是选中的那个**」。
    """
    src = _html()
    # 只看 <button ...> 标签的属性串，注释与 CSS 里的 [tabindex] 不算
    attrs = re.findall(r"<button\b[^>]*>", src)
    tabs = [a for a in attrs if 'role="tab"' in a]
    assert tabs, "夹具前提错：一个 role=\"tab\" 按钮都没抽到"

    def _ti(a):
        m = re.search(r'tabindex="(-?\d+)"', a)
        return None if m is None else int(m.group(1))

    values = [_ti(a) for a in tabs]
    stops = [a for a, v in zip(tabs, values) if v == 0]
    assert len(stops) == 1, (
        f"{len(tabs)} 个 role=\"tab\" 里有 {len(stops)} 个 tabindex=\"0\""
        f"（必须恰好 1 个）⇒ "
        + ("一个都没有 ⇒ 键盘 Tab 完全到不了导航\n" if not stops
           else "多于一个 ⇒ roving 失效，每次 Tab 都要穿过整组\n")
        + f"实际 tabindex 列表：{values}"
    )
    assert 'aria-selected="true"' in stops[0], (
        f"唯一的 Tab 停靠点不是选中项：{stops[0][:110]!r}"
        "⇒ 用户 Tab 进导航时落在一个未激活的页签上"
    )
    others = [a for a, v in zip(tabs, values) if v != 0]
    assert all(v == -1 for v in (_ti(a) for a in others)), (
        f"非选中 tab 必须写 tabindex=\"-1\"，实际："
        f"{[_ti(a) for a in others]} ⇒ roving 只做了一半"
    )


def test_the_tabs_keep_working_after_the_roving_tabindex_change():
    """夹具前提守卫：方向键导航**必须**还在。

    存在的理由：第 3 条要求补 roving tabindex。但 roving tabindex 是个陷阱——
    补了 `tabindex="-1"` 而方向键处理被删掉的话，**键盘用户一个 tab 都到不了**，
    比现在还惨（现在至少能 Tab 过去）。

    ⇒ 这条先钉住「方向键还在」，让第 3 条的修复有安全网。
    坏掉的症状（用户视角）：roving 一上，键盘彻底失灵。
    """
    kb = _code_block(_body(_html(), "t.addEventListener('keydown'"))
    assert "ArrowLeft" in kb and "ArrowRight" in kb, "tab 的方向键导航不见了"
    assert re.search(r"\.\s*focus\(\)", kb), "方向键处理里没有 .focus()，焦点不跟着走"
    assert "activate(" in kb, "方向键没有切面板，只挪焦点"


def test_the_new_chat_control_is_reachable_by_keyboard():
    """★ 键盘可达性：`<a id="new-chat">开始新对话</a>`（第 571 行）。

    源码形状：`<a>` **没有 `href`、没有 `role="button"`、没有 `tabindex`**。
    按 HTML 规范，无 `href` 的 `<a>` 不是链接、**不进焦点序列**。
    而 CSS（第 359 行）给它写了 `cursor:pointer; text-decoration:underline`
    —— 看起来完全是个可点的链接。

    坏掉的症状（用户视角）：键盘/读屏用户**无法开始新对话**。
    页面上看得见、点得动（鼠标），Tab 键却永远停不到它。
    这不是「不优雅」，是功能对一部分人彻底不存在。

    ⚠️ 判据是「**能否进焦点序列**」，不是「必须长什么样」。
    第一版要求 `href`/`tabindex`/`role="button"` **三者都在**才通过 ——
    变异验证把 `href="#" tabindex="0"` 打进去（一个**完全正确**的修法），
    守卫毫无反应，因为它还在等 `role="button"`。
    这正是本轮明令禁止的「过强断言把正确代码判成缺陷」。
    ⇒ 改成：`<a>` 只要有 `href` **或** `tabindex` 之一即可进焦点序列。
    """
    src = _html()
    btn = re.search(r"<button\b[^>]*id=\"new-chat\"[^>]*>", src)
    if btn:
        return                      # `<button>` 天然可聚焦，正确的另一种修法
    m = re.search(r"<a\b[^>]*id=\"new-chat\"[^>]*>", src)
    assert m, "夹具前提错：#new-chat 元素整个没找到"
    tag = m.group(0)
    focusable = ("href" in tag) or ("tabindex" in tag) or ('role="button"' in tag)
    assert focusable, (
        f"#new-chat 的标签是 {tag} —— 无 href 且无 tabindex，"
        "按 HTML 规范不进焦点序列。它被 CSS 画成了可点的样子，"
        "但键盘 Tab 永远停不到，用户按键盘无法开始新对话。"
    )


def test_the_chart_tooltips_have_a_touch_or_pointer_path():
    """★ 触屏可达性：三张图的 tooltip **只**绑了 `mousemove`/`mouseleave`。

    `lineChart`(1118) 与 `dualLineChart`(1086) 都只用鼠标事件。
    全文件 `touchstart` / `touchmove` / `pointerdown` / `pointermove` 出现 **0** 次。

    坏掉的症状（用户视角）：**在 iPad 上把手指放在曲线上，什么都不出现**——
    命中率的逐日读数、累计超额、净值曲线当天的值，一个都看不到。
    iPad 是项目的硬需求，而这三张图是回测结论唯一的细节载体
    （表格只给月度汇总）。

    ⚠️ E2E 的 iPad 用例测的是「不横向滚动」，用鼠标驱动；
    鼠标路径是通的，所以 E2E 全绿也盖不住这一条。
    """
    src = _code_block(_html())
    for fn, label in (("function lineChart(", "lineChart"),
                      ("function dualLineChart(", "dualLineChart")):
        body = _code_block(_body(src, fn))
        # ★ 前提从「mousemove 在」改成「**至少有一条指针/触摸路径**」。
        #   修法是把 mousemove/mouseleave 换成 pointermove/pointerleave
        #   （Pointer Events 同时覆盖鼠标与触摸），所以 mousemove 现在**不该**在。
        #   保留旧前提会让这条守卫在修好之后仍然红，诱导人去把鼠标监听加回去 ——
        #   那正是本文件记录的「在正确代码上误判成坏了」。
        has_touch = any(ev in body for ev in
                        ("touchstart", "touchmove", "touchend",
                         "pointerdown", "pointermove", "pointerup"))
        assert has_touch, (
            f"{label} 没有指针/触摸路径 ⇒ iPad 上把手指放在曲线上不出现任何读数"
        )

# ── ③b 后端 5xx 与「本来就没有」在界面上必须是两件事 ──────────────────
#
# 这类故障不会报错、不会崩、不会红任何一条已有测试：
# 后端挂了，前端把异常吞掉、渲染成「暂无数据」，用户以为系统正常。
# 本仓把这种形态叫**静默失败**，它比崩溃更难发现。



def test_a_backend_500_is_not_rendered_as_merely_no_data_yet(tmp_path):
    """★ 实测静默失败：`loadHistory()` —— `index.html:948`。

    源码形状：
        try { state.history = await jget('/api/predictions/history'); }
        catch { state.history = { rows: [], dates: [] }; }

    `catch` 把失败**改写成成功**：`rows/dates` 为空正是「用户还没跑过」的形状，
    于是 500 与真空走到同一个 `renderHistory()` 的同一个分支。

    坏掉的症状（用户视角）：后端炸了，页面不报错、不空、不转圈，
    只是历史列表安安静静显示「首日预测已生成…」——
    **看起来像一个全新系统，而不是一个坏掉的系统。**

    复现（探针只换数据源，两次跑的都是 index.html 里的真函数）：
        node → ok200: '<div class="empty">首日预测已生成。…</div>'
                 500  : '<div class="empty">首日预测已生成。…</div>'   ← 完全相同

    断言方向是**正确行为**（两者必须不同），修好的那一刻立刻红（XPASS strict）。
    """
    src = _html()
    ok = _run_js(tmp_path, _probe_history_empty_vs_500(src, server_500=False))
    bad = _run_js(tmp_path, _probe_history_empty_vs_500(src, server_500=True))
    assert ok.strip(), "对照跑没渲染出任何东西 —— 探针失效，这条守卫会误报"
    # ★ 同样要钉分支归属：只断言「两者不同」时，`isApiMissing` 恒假
    #   （把 jget 的 `e.status` 去掉）两条输出**仍然不同** ——
    #   「真的没有数据」那句文案会出现在 500 的输出里，掩盖分流失效。
    assert '首日预测已生成' in ok, (
        f"对照跑（200 + 空数据）应显示「首日预测已生成」，实际 {ok.strip()[:80]!r}"
    )
    assert '首日预测已生成' not in bad, (
        f"后端 500 时仍显示「首日预测已生成」（{bad.strip()[:80]!r}）"
        "⇒ 一句关于未来的陈述，出现在系统已经坏掉的时候"
    )
    assert '加载失败' in bad, (
        f"后端 500 时没有明确说「加载失败」（{bad.strip()[:80]!r}）"
        "⇒ 用户读不出这是故障"
    )
    assert bad.strip() != ok.strip(), (
        f"第 {_line_of(src, 'catch { state.history = { rows: [], dates: [] }; }')} 行："
        f"后端 500 与「暂无数据」渲染出了逐字相同的内容（{ok.strip()[:80]!r}）"
        "⇒ 服务挂了用户却看不出来"
    )


def test_the_backtest_panel_does_not_claim_no_records_when_the_api_failed(tmp_path):
    """★ 实测静默失败：`loadBacktest()` —— `index.html:1023`。

    源码形状：
        try { bt = await jget('/api/backtest'); }
        catch { $('bt-sub').textContent = '尚无回测记录'; return; }

    ★ 对照组的状态码来自后端源码，不是猜的：`aplus/webapp.py:442` 在真的
      没有回测时 `raise HTTPException(404, "尚无回测记录")`。
      也就是说后端已经把两件事分开了：**404 = 还没跑过，5xx = 跑了但炸了**。
      前端这个 `catch` 把两者重新粘成同一句话。

    坏掉的症状（用户视角）：回测服务 500 → 副标题写「尚无回测记录」，
    底下两张命中率和超额曲线是空的。用户读到的结论是
    「还没跑过回测」，而真相是「跑失败了」——两者导向完全不同的下一步：
    前者不用管，后者意味着策略/数据链路出问题了。

    复现（只换状态码，其余完全相同）：
        node → 404: '尚无回测记录'    500: '尚无回测记录'    ← 完全相同
    """
    src = _html()
    nf = _run_js(tmp_path, _probe_backtest_notfound_vs_500(src, code=404))
    bad = _run_js(tmp_path, _probe_backtest_notfound_vs_500(src, code=500))
    assert nf.strip(), "对照跑（404）没产出文字 —— 探针失效"
    # ★ 不只断言「两者不同」，还要钉住**各自分到哪一支**。
    #   只断言不等价有个洞：`isApiMissing` 恒假时（把 jget 的 `e.status` 去掉），
    #   两条输出仍然不同 —— 因为 404 的 detail 是「尚无回测记录」、500 的是「boom」，
    #   文案差异掩盖了分流失效。于是守卫全绿，而「真的还没跑过」被渲染成
    #   「回测接口失败：尚无回测记录」：一次**误报**，方向正好相反。
    #   ⇒ 必须断言 404 **恰好**是「尚无回测记录」，且 500 **不含**它。
    assert nf.strip() == '尚无回测记录', (
        f"404（真的还没跑过回测）没有被渲染成「尚无回测记录」，"
        f"实际是 {nf.strip()[:80]!r} ⇒ 分流失效，会把「还没跑」误报成「接口失败」"
    )
    assert '尚无回测记录' not in bad, (
        f"回测接口 5xx 被说成「尚无回测记录」（{bad.strip()[:80]!r}）"
        f"⇒ 用户会把服务故障读成「还没跑」"
    )
    assert bad.strip() != nf.strip(), (
        f"第 {_line_of(src, 'catch { $(\'bt-sub\')')} 行："
        f"回测接口 5xx 与「后端明确回 404＝还没跑过」渲染出了逐字相同的内容"
        f"（{nf.strip()[:80]!r}）⇒ 用户会把服务故障读成「还没跑」"
    )


def test_the_log_panel_does_not_claim_no_logs_when_the_api_failed(tmp_path):
    """★ 实测静默失败：`loadLogs()` —— `index.html:722`。

    源码形状：
        const rows = await fetch('/api/logs?tail=300')
          .then(r => r.json()).catch(() => []);

    这里比前两条更隐蔽：`.then(r => r.json())` **不检查 `r.ok`**，
    非 2xx 时响应体不是日志数组，`.json()` 的结果照样被当数组用
    （`rows.slice().reverse()` 直接抛 → 被外层兜住成 `[]`），
    结果同样是「暂无日志」。

    坏掉的症状（用户视角）：**恰恰是最该报警的时候**——
    日志接口挂了，运维打开日志页看到「暂无日志」，
    于是得出「系统很干净」的结论。这是把故障信号本身给关掉了。

    复现：
        node → ok200: '<tr>…暂无日志</tr>'    500: '<tr>…暂无日志</tr>'   ← 完全相同
    """
    src = _html()
    ok = _run_js(tmp_path, _probe_logs_empty_vs_500(src, server_500=False))
    bad = _run_js(tmp_path, _probe_logs_empty_vs_500(src, server_500=True))
    assert ok.strip(), "对照跑没渲染出任何东西 —— 探针失效"
    assert bad.strip() != ok.strip(), (
        f"第 {_line_of(src, 'async function loadLogs(')} 行："
        f"日志接口失败时仍渲染「暂无日志」（{bad.strip()[:80]!r}）"
        "⇒ 最该报警的面把故障信号显示成了「一切正常」"
    )

# ── ③c 预测标题指的日子必须真的是交易日 ────────────────────────────────

def test_the_prediction_title_never_points_at_a_weekend(tmp_path):
    """★ 实测缺陷：`nextTradingDayLabel()` —— `index.html:585-593`。

    源码形状（函数上方自带的注释就写着它的意图）：
        /* 「明日预测」在周六周日是错的——明天的明天才是交易日。… */
        const add = dow === 6 ? 2 : dow === 0 ? 1 : 1;

    作者防了周六和周日，**没防周五**：周五（`dow === 5`）落进 `else 1`
    ⇒ 标题写「下一交易日 10月17日」，而 10月17日是**周六**。

    坏掉的症状（用户视角）：周五晚上打开页面，标题说
    「下一交易日 10月17日预测」——**周六根本不开盘**。
    用户按这个日子去核对，得到的必然是「那天哪来的预测」，
    而真正的那天（周一）从没被提起过。每周五都错一次。

    复现：
        node（探针把返回串还原成日期再问 getDay）：
        周一|下一交易日 10月13日|周二
        周二|下一交易日 10月14日|周三
        周三|下一交易日 10月15日|周四
        周四|下一交易日 10月16日|周五
        周五|下一交易日 10月17日|周六   ← ★ 这里
        周六|周一 10月19日|周一
        周日|周一 10月19日|周一

    ⚠️ 判据故意**不钉写法**：只问「返回的那个日子是不是周六/周日」，
    不问 `add` 那行长什么样。改成查表、改成循环、换成别的正确实现都照过——
    这正是铁律里「如果它没坏呢」那条要求的落地。
    """
    out = _run_js(tmp_path, _probe_weekday_labels(_html()))
    rows = [r.split("|") for r in out.strip().split("\n") if r.strip()]
    assert len(rows) == 7, f"探针没跑满一周七天（拿到 {len(rows)} 行）—— 守卫会误判"
    weekend = ["周六", "周日"]
    bad = [(from_dow, label, landed) for from_dow, label, landed in rows if landed in weekend]
    assert not bad, (
        f"第 {_line_of(_html(), 'function nextTradingDayLabel(')} 行起："
        f"预测标题在 {bad} 的情况下指向了非交易日 —— 用户按标题的日子去找预测必然扑空"
    )

# ── ③d 「两次输出相同」到底是产品的锅还是我探针的锅 ──────────────────────
#
# ★ 这条是**踩过坑之后**才写出来的，而且坑很典型：
#   我第一版的 `_PRELUDE` 忘了定义 `jget`，于是对照跑和被测跑**都**
#   撞上 `ReferenceError: jget is not defined`、都掉进同一个 `catch`，
#   两条输出天然相同 —— 守卫绿的，而且**看起来**像是成功复现了
#   「后端 5xx 被吞成『暂无数据』」。
#   真相是：复现出来的是我自己 harness 的 bug。
#
#   判据：**失败那一路必须真的失败在 HTTP 层**，而不是失败在探针自己身上。
#   做法：让被测那一路先打印 `r.ok`/`r.status`，
#   只有它真的是一次 HTTP 5xx，这次对比才说明问题。

@pytest.mark.parametrize(
    "which, ctl, bad",
    [
        ("history", lambda s: _probe_history_empty_vs_500(s, server_500=False),
                      lambda s: _probe_history_empty_vs_500(s, server_500=True)),
        ("backtest", lambda s: _probe_backtest_notfound_vs_500(s, code=404),
                       lambda s: _probe_backtest_notfound_vs_500(s, code=500)),
        ("logs",    lambda s: _probe_logs_empty_vs_500(s, server_500=False),
                      lambda s: _probe_logs_empty_vs_500(s, server_500=True)),
    ],
)
def test_both_arms_really_differ_only_at_the_http_layer(tmp_path, which, ctl, bad):
    """★ 探针自证 —— 这条是**踩过坑之后**才写出来的，坑很典型。

    我第一版的 `_PRELUDE` 忘了定义 `jget`，于是对照跑和被测跑**都**撞上
    `ReferenceError: jget is not defined`、都掉进同一个 `catch`，
    两条输出天然相同 —— 守卫绿的，而且**看起来**像是成功复现了
    「后端 5xx 被吞成『暂无数据』」。
    真相是：复现出来的是我自己 harness 的 bug。

    坏掉的症状（用户视角）：无——守的是守卫自己。坏掉的**后果**是
    §③b 那几条永远判「失败被正确呈现」，而其实什么都没验证。

    判据：两跑都必须**真的走到 fetch**，且拿到的确实是各自那个状态码
    （stderr 里的 `HTTP … true 200` / `HTTP … false 500` / `HTTP … false 404`）。
    只要有一跑是死在探针自己的 ReferenceError 上，这条立刻红。
    """
    src = _html()
    want = ("true 200", "false 500")
    if which == "backtest":
        want = ("false 404", "false 500")
    for arm, script, expect in (("对照", ctl(src), want[0]), ("被测", bad(src), want[1])):
        err = _run_js(tmp_path, script, want_stderr=True)
        assert "HTTP " in err, (
            f"{which} 的{arm}跑根本没调用 fetch —— 两次输出相同是探针自己炸了造成的"
            f"（多半是漏定义 jget 之类），不是产品把错误吞了。stderr={err[:300]!r}"
        )
        assert expect in err, (
            f"{which} 的{arm}跑拿到的不是 {expect}：stderr={err[:300]!r}"
        )

# ── ③e 探针本身必须活着（**不带 xfail**） ──────────────────────────────
#
# ★ 这条是被一次真实事故逼出来的：我用区间替换重排文件时，
#   把 `_probe_weekday_labels` 整段删掉了。
#   结果：周末那条守卫（带 xfail）里的探针立刻抛错，
#   **xfail 把这个 AssertionError 报成「缺陷还在」，全绿通过。**
#   一个探针都不存在的守卫，看起来和一个正常工作的守卫一模一样。
#
# ⇒ 任何被 xfail 包着的探针，必须在这里先被证明「存在、能跑、有输出」。

def test_every_probe_builder_still_exists_and_produces_runnable_code(tmp_path):
    """探针清单自证：构建器在、脚本非空、node 跑得动、stdout 有东西。

    坏掉的症状（用户视角）：无。坏掉的**后果**是所有 xfail 守卫集体睡着，
    而 pytest 全绿 —— 这正是本仓反复记下的「绿着的死代码」。
    """
    src = _html()
    probes = {
        "_probe_alloc":       lambda: _probe_alloc(src, name=_Q("x"), code=_Q("y")),
        "_probe_history":     lambda: _probe_history(src, name=_Q("x"), code=_Q("y")),
        "_probe_chips":       lambda: _probe_chips(src),
        "_probe_ledger":      lambda: _probe_ledger(src),
        "_probe_weekday_labels": lambda: _probe_weekday_labels(src),
        "_probe_history_empty_vs_500": lambda: _probe_history_empty_vs_500(src, server_500=True),
        "_probe_backtest_notfound_vs_500": lambda: _probe_backtest_notfound_vs_500(src, code=500),
        "_probe_logs_empty_vs_500": lambda: _probe_logs_empty_vs_500(src, server_500=True),
    }
    missing = [n for n in probes if n not in globals()]
    assert not missing, (
        f"探针构建器不见了：{missing} —— "
        "挂在它们身上的 xfail 守卫会把「探针不存在」报成「缺陷还在」，集体睡着"
    )
    for name, build in probes.items():
        script = build()
        assert script and script.strip(), f"{name} 返回了空脚本"
        out = _run_js(tmp_path, script)
        assert out.strip() and "Error" not in out.split("\n")[0], (
            f"{name} 跑不出东西（多半是抠模板/抠函数失败）：{out[:200]!r}"
        )

# ── ④ 分析器自身的非恒真自证 ─────────────────────────────────────────

def test_the_injection_analyzer_fires_on_adversarial_input_and_stays_quiet_on_safe_input():
    """★ 守卫分析器自己。

    拿 §① 的判据（`_unescaped_in_templates`）跑两个**人造**样本：

    * 有洞的样本必须报出来 —— 否则上面每条都在空断言上打转；
    * 无洞的样本必须**不**报 —— 否则这就是一条会对正确代码变红的过强断言，
      正是本轮明令禁止的那种。

    两个样本都用同一段函数体形状，只改插值那一行，
    保证「报 / 不报」的差异只可能来自插值本身。
    """
    holed = _code_block("""
      function demo(a) {
        el.innerHTML = `<td><b>${esc(a.name)}</b><span>${a.code}</span></td>`;
      }
    """)
    safe = _code_block("""
      function demo(a) {
        el.innerHTML = `<td><b>${esc(a.name)}</b><span>${esc(a.code)}</span></td>`;
      }
    """)
    # 前提：两个样本确实只有那一行之差
    assert holed.replace("${a.code}", "") != safe.replace("esc(a.code)", ""), \
        "夹具前提错：两个样本构造得一样，测不出差异"

    holed_hits = _unescaped_in_templates(holed)
    safe_hits = _unescaped_in_templates(safe)

    assert any("a.code" in e for _ln, e in holed_hits), \
        f"分析器对着有洞的样本没报警 —— 它恒真，§① 全是空断言。命中：{holed_hits}"
    assert not any("a.code" in e for _ln, e in safe_hits), \
        f"分析器对着已转义的样本也报警 —— 这是过强断言，会判正确代码有缺陷。命中：{safe_hits}"


def test_the_body_extractor_rejects_a_header_that_does_not_exist():
    """`_body()` 对不存在的 header 必须抛错，否则它返回的可能是别处的片段。

    这是本仓的老教训（「守卫本身要先被守卫一次」）。
    ⚠️ 第一版把**真实存在**的 header 当反例写进列表，这条守卫自己立刻变红——
    守卫写反时，它保护的东西反而最容易被自己绊倒。
    """
    src = _html()
    real = "function renderRows("
    assert real in src, f"夹具前提错：{real!r} 现在不存在，不能当反例"
    for absent in ("function 从不存在的函数名(", "function 另一个假的("):
        try:
            _body(src, absent)
        except (ValueError, AssertionError):
            continue
        raise AssertionError(f"header {absent!r} 不存在，_body 却返回了内容")


def test_the_body_extractor_survives_a_brace_inside_a_template_string():
    """提取器必须跳过字符串里的花括号，否则函数体会被提前截断。

    `index.html` 里满地都是 `${...}` 和 `}`，这不是假想风险。
    """
    demo = ('function demo() { const s = `a${x}b`; const t = "}";'
            ' return s + t; }\nfunction after() { return 1; }')
    got = _body(demo, "function demo(")
    assert "return s + t" in got
    assert "after" not in got, "字符串里的花括号把函数体提前截断了"


def test_every_guarded_function_still_has_the_shape_this_file_assumes():
    """★ 全局夹具前提守卫：本文件锚定的每个 header 都还在，且抠出来非空。

    守的是「守卫静默失效」这个失败模式：函数改名后 `_body()` 会抛
    `ValueError`，那会**变红**（好）；但函数被内联进别的函数时，
    `_body()` 可能匹配到一段结构完全不同、恰好非空的代码。

    ⇒ 这里把 5 个 header 与它们的最小特征串一一钉住。
    坏掉的症状（用户视角）：守卫还在跑，但守的是别的东西。
    """
    src = _html()
    # ⚠️ 这些 marker 必须随**被测代码的形状**更新，不是随缺陷更新。
    #   2026-10-14 修了三处之后：renderAlloc 的 `${p.code}` → `${esc(p.code)}`，
    #   两张图的 `'mousemove'` → `'pointermove'`（为 iPad 触摸）。
    #   守卫守的是「我抠出来的还是那个函数」，函数签名/关键表达式变了就得换 marker，
    #   否则它会在正确代码上变红，诱导人去「修」一个已经被修好的东西 ——
    #   那正是 CLAUDE.md 记录的「把证据往坏的方向读」第 4 号。
    expected = {
        "function renderAlloc(": "esc(p.code)",
        "function renderRows(": "esc(r.code ?? '')",
        "function renderHistory(": "esc(r.name ?? r.code)",
        "function newsChip(": "esc(reason)",
        "function renderDocket(": "warnOut.detailHtml ?? esc(warnOut.detail)",
        "function lineChart(": "'pointermove'",
        "function dualLineChart(": "'pointermove'",
    }
    for header, marker in expected.items():
        body = _code_block(_body(src, header))
        assert marker in body, \
            f"{header!r} 的函数体里找不到 {marker!r} —— 结构变了，这条守卫在盯别的东西"


# ── ⑳ D1 交付形态：渲染层必须渲染出与产物同数的行 ─────────────────────
#
# 🚨 这条守卫是被**一次真的交付物缺陷**逼出来的（2026-10-16 浏览器实测）：
#   `renderRows($('pred-rows-up'), pred.up.slice(1), 'up')` —— 只有 up 侧 slice，
#   down 侧没 slice ⇒ 表格 up 9 行 / down 10 行，
#   而 `up-count` 打的是 `pred.up.length` = 10
#   ⇒ **「标题写 10 只、表格只有 9 行」而 HTTP 200、前端无报错、零告警。**
#
# 为什么此前没抓到：`head(10)` / `slice(1)` 这类形状**不是异常**，
# 它照常返回一串 HTML。而四层里落盘层（`rows=20`）、读回层（API `10/10`）
# 今天都实读通过了 —— **只有渲染层从没被量过**，
# 而它恰好是用户唯一会直接看到的那一层。
#
# ⇒ 判据不是「源码里没有 slice」（子串会被注释与 docstring 满足，第 13 层），
#   是**真跑 renderRows 并数它产出的 `<tr>` 行数**：
#   喂 N 行 ⇒ 必须渲染 N 行。少一行、多一行都红。

_ROWS_N = 10          # 与 DELIVERABLE_N 同值，但**不许 import 生产常量**：那条 import 会让
                      # 「产品改了交付形态」时守卫跟着改，永远判不出退化。
_ROWS_MARK = "ZZROWZZ"


def _probe_render_rows_count(src: str, n: int) -> str:
    """真跑 `renderPredBoard(pred)`，返回它**渲染出来的**两张表的行数与两个标题计数。

    ⚠️⚠️ 第一版探针跑的是 `renderRows` 的**函数体**、由测试自己调用 ——
    而真正的缺陷（`.slice(1)`）长在**调用点**，两者之间零通路
    ⇒ 变异存活、守卫全绿（第 21 层：承重逻辑不可测 = 判据不存在）。
    ⇒ 现在探针跑的是**承重函数本身**，`renderRows` / `renderHeroes`
    都是从文件里抠出来的**原文**，不是副本。

    ⚠️ 数的是**渲染出来的 HTML**，不是传进去的数组 ——
    这两件事只有在真执行时才会分开，而缺陷只发生在它们分开的地方。
    `_DOM_STUB` 的 `querySelectorAll` 恒返 `[]`，所以只能数字符串里的 `<tr`。
    """
    board = _code_block(_fn(_body(src, "function renderPredBoard(")))
    heroes = _code_block(_fn(_body(src, "function renderHeroes(")))
    rows = _code_block(_fn(_body(src, "function renderRows(")))
    prelude = (
        "const $ = id => global.document.getElementById(id);\n"
        "const esc = s => String(s ?? '').replace(/[&<>\"']/g, c =>\n"
        "  ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
        "const fmtPct = (v, d = 1) => v == null ? '\\u2014'\n"
        "  : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';\n"
        "const newsChip = r => String(r.name ?? '');\n"
        + heroes + "\n" + rows + "\n" + board + "\n"
    )
    up_js = "[" + ",".join(
        "{code:'%sUP%d', name:'UP%d', mom20:0.01, score:1.0, news_sentiment:0}"
        % (_ROWS_MARK, i, i) for i in range(n)
    ) + "]"
    down_js = "[" + ",".join(
        "{code:'%sDN%d', name:'DN%d', mom20:-0.01, score:-1.0, news_sentiment:0}"
        % (_ROWS_MARK, i, i) for i in range(n)
    ) + "]"
    return prelude + (
        "renderPredBoard({up: " + up_js + ", down: " + down_js + "});\n"
        "const hu = global.__html('pred-rows-up');\n"
        "const hd = global.__html('pred-rows-down');\n"
        "const cnt = h => (String(h).match(/\\d+/g) || []).join('');\n"
        "console.log(JSON.stringify({\n"
        "  up_tr: (hu.match(/<tr/g) || []).length,\n"
        "  down_tr: (hd.match(/<tr/g) || []).length,\n"
        "  up_codes: (hu.match(/ZZROWZZ/g) || []).length,\n"
        "  down_codes: (hd.match(/ZZROWZZ/g) || []).length,\n"
        "  up_label: cnt(global.__text('up-count')),\n"
        "  down_label: cnt(global.__text('down-count')),\n"
        "  hero_has_top: global.__html('heroes').includes('ZZROWZZUP0'),\n"
        "}));\n"
    )


def test_render_rows_paints_exactly_one_row_per_row_it_is_given(tmp_path):
    """★ 能力守卫：**喂 N 行 ⇒ 必须渲染 N 行，且标题计数与之同源。**

    这条不是「源码里不许有 slice」——那是子串断言，会被注释满足（第 13 层）。
    它真跑**承重函数** `renderPredBoard`、数它产出的 `<tr>`：

    - 少渲染 ⇒ 表格比产物小，而 HTTP 200、零告警、**标题仍写着全数**
    - 多渲染 ⇒ 表格里有从未被选中的票

    两条失效方向**对称地**被这一条覆盖 ⇒ 探针不偏袒任何一侧（第 20 层那条通则：
    选探针时要问「缺陷在两侧是否对称」）。

    ⚠️ 断言的是**两侧都测**：真实缺陷只出现在 up 侧（down 侧当时没写 slice），
    只测一侧的守卫会让「down 侧也开始 slice」这类新退化完全隐形。
    """
    out = _run_js(tmp_path, _probe_render_rows_count(_html(), _ROWS_N))
    got = json.loads(out.strip().splitlines()[-1])
    for side in ("up", "down"):
        assert got[f"{side}_tr"] == _ROWS_N, (
            f"喂了 {_ROWS_N} 行，renderPredBoard 在 {side} 侧只渲染出 "
            f"{got[f'{side}_tr']} 个 <tr> ⇒ **表格与产物不同数**，"
            f"而标题仍按产物长度渲染 ⇒ 交付形态在页面上变形"
        )
        # 正控：行数对了还不够，**每只票都得真的画出来**。
        #   一条「渲染 N 个空壳 <tr>」的实现能骗过 tr 计数 ⇒ 必须再数票。
        assert got[f"{side}_codes"] == _ROWS_N, (
            f"{got[f'{side}_tr']} 个 <tr> 里只画出了 {got[f'{side}_codes']} 只票 ⇒ "
            f"渲染层造了空行，或行与票的对应关系断了"
        )
    # ★ 这才是今天那条缺陷本身：**标题上的数与表格里的行数走了两个不同的量**。
    #   曾经 up 侧渲染 `pred.up.slice(1)`（9 行）而 `up-count` 打 `pred.up.length`（10）。
    assert got["up_label"] == str(_ROWS_N), (
        f"标题写「{got['up_label']} 只」而 up 表格有 {got['up_tr']} 行 ⇒ "
        f"**计数与行数不同源**，用户在页面上看到的形状与产品交付的形状不一致"
    )
    assert got["down_label"] == str(_ROWS_N), (
        f"标题写「{got['down_label']} 只」而 down 表格有 {got['down_tr']} 行 ⇒ 同上"
    )
    # 正控（钉住上面两条不是恒真）：hero 卡确实拿到了首行 ⇒
    #   「榜单第 1 只」这条通路是通的，row 数据没在传参处就断掉。
    assert got["hero_has_top"] is True, (
        "hero 卡里看不到榜单首只 ⇒ 探针的输入构造坏了（这是夹具失效，不是产品失效）"
    )


def test_the_row_probe_would_notice_if_it_stopped_counting(tmp_path):
    """★ 探针健康度 —— 守的是守卫自己，不是产品（第 20 层那条通则的第 4 步）。

    `*_codes` 断言里嵌着「每只票都画出来了」的正控，
    而那个正控与主断言**用的是同一个探针**。
    ⇒ 探针哪天坏成「只数 `<tr>`、数不到票」时，正控会**跟着一起失效**，
    主守卫仍然全绿 —— 两条检查共享同一个失效路径。

    ⇒ 这里造一个**只渲染 1 行**的输入：真探针必须给出每侧 `tr=1`（≠ 10）。
    如果连这个都「渲染出 10 行」，说明探针根本没在数行数。

    ⚠️ 同时钉住「标题计数真的跟着变」：喂 1 行必须打「1 只」，
    否则 `up_label` 那个键有可能只是被硬编码在探针里（恒真）。
    """
    out = _run_js(tmp_path, _probe_render_rows_count(_html(), 1))
    got = json.loads(out.strip().splitlines()[-1])
    for side in ("up", "down"):
        assert got[f"{side}_tr"] == 1 and got[f"{side}_codes"] == 1, (
            f"喂 1 行却在 {side} 侧量到 tr={got[f'{side}_tr']} / "
            f"codes={got[f'{side}_codes']} ⇒ 探针没有真的在数行数"
            f"（或是 _DOM_STUB 的 innerHTML 收集坏了）"
        )
        assert got[f"{side}_label"] == "1", (
            f"喂 1 行时 {side} 标题打「{got[f'{side}_label']}」⇒ "
            f"标题计数没有跟着输入走，可能是探针里写死的 ⇒ 主守卫的 label 断言是恒真的"
        )
