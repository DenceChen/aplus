"""前端**接线**守卫 —— 后端流式有 37 条测试，前端接线一条都没有。

## 为什么必须读源码

`tests/test_chat_stream.py` 能证明 `/api/chat/stream` 真的吐 SSE，
`tests/test_llm_chain.py` 能证明 `chat_stream()` 真的切分 thinking/text。
但**没有任何一条测试**能证明 `index.html` 真的去读了那个流。

这不是假想的风险，是本仓已经写死的失败模式
（`tests/test_docket_warning.py` 的原话）：

> 上一版只写好纯函数、测试全绿，但 `index.html` 没接线 ——
> 那就是**绿着的死代码**。

把后端换成返回一段假 SSE、让 `chat_stream` 产出垃圾，37 条后端测试照样全绿，
而用户看到的仍然是一个「一次性等 json」的界面。

## 为什么用括号配对而不是 `split("\\n}")`

本仓现有范式是 `html.split("async function loadChrome(")[1].split("\\n}")[0]`。
它靠**换行后紧跟 `}`** 来切函数体，于是：函数被重排、改缩进、或内部出现
一行只有一个 `}` 的写法时，它会**静默切错** —— 然后在一个错误的片段上断言，
绿灯继续。

⇒ 这里改成从函数名起手**逐字符配对花括号**（跳过字符串与注释），
对缩进、重排、嵌套都免疫。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_HTML = Path(__file__).resolve().parent.parent / "aplus" / "web" / "index.html"


def _html() -> str:
    assert _HTML.is_file(), f"找不到前端源文件：{_HTML}"
    return _HTML.read_text(encoding="utf-8")


def _body(src: str, header: str) -> str:
    """从 `header`（形如 `async function send(`）起，取出配对花括号内的函数体。

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


def _send_body(src: str) -> str:
    return _body(src, "async function send(")


# ── ① 流式接线 ────────────────────────────────────────────────────────

def test_send_really_reads_the_stream_instead_of_awaiting_json():
    """★ 核心守卫：`send()` 必须把**成功路径**的响应体交给流式读取器。

    上一版的写法是 `const r = await fetch(...); await r.json()` —— 一次性读完、整段上屏。
    只断言「函数里出现了 fetch」会漏掉这一点：fetch 在，一次性读也在。

    ⚠️ 这里**不能**粗暴地全文禁止 `.json()`：非 2xx 分支里
    `await res.json()` 解析错误体是**正确**做法（FastAPI 的错误就是一个 JSON 对象）。
    我第一版就写了这个过强断言，结果它对着正确的代码变红 ——
    **过强断言会把正确实现判成缺陷，等于把守卫变成噪声。**
    ⇒ 只钉成功路径：响应体必须被 `pumpStream` 消费。
    """
    body = _send_body(_html())
    assert re.search(r"await\s+pumpStream\(\s*res\.body", body), \
        "send() 没把成功响应体交给 pumpStream —— 后端在流，前端还在等一次性响应"


def test_the_sse_parser_accepts_every_event_name_the_backend_emits():
    """契约两边必须对齐：后端发的五种事件，前端都得认。

    少认一种 ⇒ 那一类增量被静默丢弃，界面「看起来正常」只是少了一段。

    ⚠️ 断言范围**必须收在 `handleEvent` 里**，不能搜全文：
    第一版写的 `f"'{ev}'" in src` 有洞 —— `'error'` 在文件第 946 行一句
    无关的中文注释里就出现了（「把 state.filter 设成 'error'」），
    于是把解析器里 `ev.name === 'error'` 整个删掉，这条守卫照样绿。
    和端点那条同一个病：**注释满足子串断言**。
    """
    src = _html()
    assert "function handleEvent(" in src, "SSE 事件处理器改名了，这条守卫失效"
    body = _body(src, "function handleEvent(")
    for ev in ("start", "thinking", "text", "done", "error"):
        assert re.search(rf"""ev\.name\s*===?\s*['"]{ev}['"]""", body), \
            f"handleEvent 不处理 `{ev}` 事件 —— 该类增量会被静默丢掉"


def test_the_stream_endpoint_url_is_the_one_the_backend_serves():
    """★ 这条第一版写成 `"/api/chat/stream" in html` —— **守卫有洞**，
    变异验证把它打绿了：把 `fetch('/api/chat/stream'` 改回 `fetch('/api/chat'`，
    全文件里仍有两处该子串（契约注释 + 404 报错文案），断言照样成立。

    洞的形状是本仓的老熟人：**注释满足了一个子串断言**。
    守卫读到的是「某处提到过这个端点」，它想守的却是「fetch 打的正是这个端点」。

    ⇒ 改为只在 `send()` 里提取 fetch 的**实参**，并要求唯一。
    """
    urls = re.findall(r"fetch\(\s*['\"]([^'\"]+)['\"]", _send_body(_html()))
    assert urls == ["/api/chat/stream"], \
        f"send() 打的端点是 {urls} —— 前端接回一次性端点后端不会流了"


# ── ② 防连点接线 ──────────────────────────────────────────────────────

def test_the_double_click_gate_lives_inside_send_not_only_on_the_button():
    """★ 防连点必须有**两道**，少一道就有一个入口绕过。

    只做 `$('send').disabled = true` 时 **Enter 键完全绕过** ——
    它不经过按钮，`disabled` 对它无效。
    ⇒ 门禁必须在 `send()` 内部。
    """
    body = _send_body(_html())
    assert re.search(r"if\s*\(\s*inFlight\s*\)", body), \
        "send() 内部没有 inFlight 门禁 —— Enter 键路径能绕过按钮的 disabled"


def test_the_enter_key_path_calls_the_same_send_and_not_a_private_copy():
    """第二道闸必须是**同一个** `send()`。

    若 Enter 处理器内联了另一份发送逻辑，两道闸就分家了：
    按钮被锁住时 Enter 照样能发。
    """
    src = _html()
    kb = _body(src, "$('chat-text').addEventListener('keydown'")
    calls = re.findall(r"\b(\w+)\s*\(\s*\)", kb)
    calls = [c for c in calls if c != "preventDefault"]
    assert "send" in calls, f"Enter 处理器调的不是 send()，而是 {calls}"
    assert kb.count("async function send") == 0, \
        "Enter 处理器里内联了第二份发送逻辑 —— 两道闸会分家"


def test_the_busy_flag_is_released_in_finally_not_only_on_success():
    """★ 复位漏了 = 按钮**永久锁死**，而这同样「不报错」。

    流在 `catch` 里 return 而没复位时：界面上没有任何错误，
    只有「发送按钮从此点不动了」。
    """
    body = _send_body(_html())
    tail = body[body.rfind("finally"):] if "finally" in body else ""
    assert tail, "send() 里没有 finally —— 出错时 inFlight 永远不复位"
    assert "inFlight = false" in tail, "finally 里没有复位 inFlight"
    assert re.search(r"setBusy\(\s*false\s*\)", tail), "finally 里没有解除按钮禁用"


def test_a_silent_stream_gets_aborted_so_finally_always_runs():
    """实测过的真实故障：服务端半路硬断（RST）时 `reader.read()`
    既不 resolve 也不 reject ⇒ `finally` 永远不跑。

    没有看门狗的话，按钮会锁到刷新为止，而页面上零报错。
    ⇒ 必须有「静默上限 ⇒ 主动 abort」。
    """
    body = _send_body(_html())
    assert "AbortController" in body and "abort(" in body, \
        "没有静默看门狗 —— 上游半死会把发送框锁死"


# ── ③ 不得硬编码模型名 ────────────────────────────────────────────────

def test_no_model_name_is_hardcoded_anywhere_in_the_frontend():
    """硬编码措辞是**静默矛盾**的温床（`docs/MISTAKE_LOG_2026-10-11.md` 第 29 条）——
    它永远不会被后端的改动通知到。

    这正是「界面显示 MiniMax M3 而代码跑 M3.1」那类投诉的结构性来源：
    代码改对了，标签不会自己跟着改。
    """
    src = _html()
    hits = [m for m in re.findall(r"(MiniMax[-\w.]*|glm[-\w.]*)", src)]
    assert not hits, f"index.html 里硬编码了模型名：{sorted(set(hits))} —— 必须读 /api/llm/info"


def test_the_frontend_reads_the_model_from_the_runtime_endpoint():
    """同一条纪律：这条第一版也是全文子串，注释里写一次就能骗过它。
    ⇒ 收在真实请求里。"""
    assert re.search(r"""jget\(\s*['"]/api/llm/info['"]""", _html()), \
        "模型信息没有从运行态接口读"


# ── ④ 整段脚本必须能解析（不依赖我任何正则的一条） ────────────────────

def _inline_scripts(src: str) -> list[str]:
    """取出所有**内联** `<script>`（跳过 `src=` 外部脚本）的代码体。"""
    return [code for _attrs, code in
            re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", src, re.S)]


def test_every_inline_script_block_actually_parses():
    """★ 这条**不用任何正则去猜**，直接问解析器。

    守的是一类「pytest 全绿但页面是死的」事故：`send()` 里若与同一作用域
    已有 `const text`（第 1640 行附近）重复声明，那是**解析期 SyntaxError** ——
    浏览器整块丢弃脚本、永远停在「加载…」，而后端 37 条、前端 11 条测试
    一条都不会红，也没有任何 console 之外的报错。

    后端 agent 在交付时专门警告了这个风险（它无法从 Python 侧验证 JS）。
    ⇒ 用 `node --check` 把它变成会红的东西。

    node 不在时跳过：这是环境能力，不是代码正确性。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node，跳过 JS 解析检查")

    blocks = _inline_scripts(_html())
    assert blocks, "一个内联脚本块都没抽到 —— 提取逻辑坏了，这条守卫在空断言上打转"
    for i, code in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                         encoding="utf-8") as f:
            f.write(code)
            path = f.name
        try:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        finally:
            os.unlink(path)
        assert r.returncode == 0, (
            f"第 {i} 个内联脚本块解析失败 ⇒ 浏览器会丢弃整块，页面静默停在加载态：\n"
            f"{r.stderr.strip()[:800]}"
        )


# ── ⑤ 变异自检：证明这些守卫不是恒真的 ────────────────────────────────

def test_every_helper_extractor_rejects_a_header_that_is_absent():
    """`_body` 若对不存在的 header 也返回内容，上面每一条都在空断言上打转。

    这是「守卫本身要先被守卫一次」——本仓第 4 条的教训：
    结构不变量抓不到错标，因为错标后的结构**仍然自洽**。

    ⚠️ 第一版我在这里把**真实存在**的 `async function send(` 当成「不存在的
    header」写进列表，于是这条守卫自己立刻变红。
    守卫写反时，它保护的东西反而最容易被自己绊倒。
    """
    src = _html()
    for absent in ("async function send(", "$('chat-text').addEventListener('keydown'"):
        # 先确认它**真的**不在——否则这条测试在测一个错误的前提
        assert absent in src, f"夹具前提错：{absent!r} 现在是存在的，不能当反例"
    for absent in ("async function 从不存在的函数名(", "addEventListener('从不存在的事件'"):
        try:
            _body(src, absent)
        except (ValueError, AssertionError):
            continue
        raise AssertionError(f"header {absent!r} 不存在，_body 却返回了内容")


def test_the_extractor_survives_a_brace_inside_a_string():
    """提取器必须跳过字符串里的花括号，否则函数体会被提前截断。"""
    demo = 'async function demo() { const s = "}"; return s; }\nasync function after() { return 1; }'
    got = _body(demo, "async function demo(")
    assert "const s" in got and "return s" in got
    assert "after" not in got, "字符串里的 `}` 把函数体提前截断了"