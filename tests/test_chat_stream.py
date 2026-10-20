"""`POST /api/chat/stream`（SSE 真流式）测试 + `GET /api/llm/info` 运行态端点。

## 契约（前端 agent 正在按这份写代码，一个字都不能改）

    POST /api/chat/stream   body: ChatIn{message, conversation_id?}
    200  Content-Type: text/event-stream; charset=utf-8
         Cache-Control: no-cache / X-Accel-Buffering: no
    event start    {"conversation_id": int}
    event thinking {"text": 增量}     可一个都没有
    event text     {"text": 增量}
    event done     {"conversation_id": int, "reply": 完整正文}
    event error    {"message": 面向用户的中文说明}   只发一次，之后结束，不发 done

恒等式（**外部锚**，不是内部一致性）：
    "".join(text 增量的 text) == done.reply
如果这条能自洽地假绿（`reply` 由同一份 parts 拼出来，两边一起错），
`test_reply_is_built_from_the_emitted_deltas_only` 会用**独立手算的期望正文**
把它钉死。

## 变异验证记录（2026-10-14，12 条全部变红）

全部在**运行层**改坏实现，再看对应测试是否变红。完整结果见文末小节。

## 这个文件自己踩过的两个坑（都写在这里，因为它们会复发）

**① `TestClient` 会把整个响应体缓冲完再交给客户端。**
第一版顺序守卫写的是「读到 `done` 的当口去查库」，看着最直接，实测：
测试读到**第一个**事件（start）时，库里**已经有 assistant 行了**。
⇒ 那种写法区分不出「先写库后发 done」与「先发 done 后写库」，**两种顺序都绿**，
是标准安慰剂守卫。现在改成 `order_log` fixture：给 `_connect`（写库）与
`_sse`（发事件）各挂旁路，记真实先后。

**② 变异驱动器自己也会假绿。**
第一版把 `-k` 选择器当成**单个文件路径**传进 pytest，于是每条变异都输出
`no tests ran` + 非零退出码，被脚本当成「变红」⇒ **12 条全报假红**。
第二版又把 baseline 算在**写完变异之后**，于是「变异前就红」量的是变异本身。
⇒ 现在的驱动器：(a) 选择器用 `shlex` 拆成多个 argv；
(b) `no tests ran` / `0 passed` 单独判为**无效**而不是变红；
(c) 基线在写变异**之前**算，且基线必须全绿、必须选中 >0 个测试；
(d) 「collection error」不算守住——M3/M10 第一版把文件改成语法错误，
    那样的红证明不了任何事，已改成语义合法、产生真断言失败的变异。
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import aplus.llm as llm_mod
from aplus.llm import (
    FallbackChain,
    GlmProxyClient,
    MiniMaxM31FlashClient,
)
from aplus.webapp import create_app

# ---------------------------------------------------------------- SSE 解析工具


def parse_sse(raw: str) -> list:
    """原文 → [(event 名, data dict)]。故意写成最笨的字符串切分：
    它对协议的任何偏差都敏感（字段顺序、缺空行、多了 BOM 都会炸）。"""
    events = []
    for block in raw.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        name = data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        assert name is not None, f"事件块缺少 event 行: {block!r}"
        assert data is not None, f"事件块缺少 data 行: {block!r}"
        events.append((name, data))
    return events


def iter_events(lines):
    """**惰性**解析：每解析出一个事件就 yield 出去。

    顺序守卫必须用它而不是 parse_sse(整段)——只有在流还开着的时候
    查库，才能区分「先写库后发 done」与「先发 done 后写库」。
    一次性读完再查库的话，两种顺序都绿。
    """
    name = data_raw = None
    for line in lines:
        if line.startswith("event: "):
            name = line[len("event: "):]
        elif line.startswith("data: "):
            data_raw = line[len("data: "):]
        elif line == "" and name is not None:
            yield name, json.loads(data_raw)
            name = data_raw = None


def names(events) -> list:
    return [n for n, _ in events]


def text_of(events) -> str:
    return "".join(d["text"] for n, d in events if n == "text")


def thinking_of(events) -> str:
    return "".join(d["text"] for n, d in events if n == "thinking")


def messages_in_db(root: Path, conv_id: int) -> list:
    with sqlite3.connect(Path(root) / "chat.db") as conn:
        return conn.execute(
            "SELECT role, content FROM messages WHERE conv_id=? ORDER BY id", (conv_id,)
        ).fetchall()


# ---------------------------------------------------------------- 假客户端


class FakeStreamLLM:
    """假流式模型：按脚本 yield ("thinking"|"text", 增量)。

    `fail_at` = 在产出第 n 个增量**之前**抛错（模拟连接中途断）；
    `fail_before_start` = 一进入就抛（模拟代理完全不可用）。
    """

    def __init__(self, chunks, fail_at=None, fail_before_start=None):
        self.chunks = list(chunks)
        self.fail_at = fail_at
        self.fail_before_start = fail_before_start
        self.last_system = ""
        self.last_messages = []

    def chat_stream(self, messages, system=""):
        self.last_system = system
        self.last_messages = messages
        if self.fail_before_start is not None:
            raise self.fail_before_start
        for i, (kind, delta) in enumerate(self.chunks):
            if self.fail_at == i:
                raise self.fail_at_error
            yield kind, delta

    def chat(self, messages, system=""):  # 非流式端点的完整性（有的调用方会用）
        self.last_system = system
        self.last_messages = messages
        if self.fail_before_start is not None:
            raise self.fail_before_start
        return "".join(d for k, d in self.chunks if k == "text")


@pytest.fixture
def root(tmp_path):
    return tmp_path


@pytest.fixture
def make_client(root):
    def _make(chunks=(), **kw):
        fake = FakeStreamLLM(chunks or [("text", "回声")], **kw)
        return TestClient(create_app(root, llm=fake)), fake, root

    return _make


# ================================================================ 响应头 / 状态码


def test_stream_endpoint_headers_are_sse_and_no_buffer(make_client):
    tc, _, _ = make_client([("text", "你好")])
    resp = tc.post("/api/chat/stream", json={"message": "在吗"})
    assert resp.status_code == 200
    ctype = resp.headers["content-type"]
    assert ctype == "text/event-stream; charset=utf-8"
    assert resp.headers["cache-control"] == "no-cache"
    # nginx 默认会缓冲 SSE，X-Accel-Buffering: no 是唯一的关闭开关
    assert resp.headers["x-accel-buffering"] == "no"


# ================================================================ 事件序列 / 恒等式


def test_event_sequence_start_text_done(make_client):
    tc, _, _ = make_client([("text", "甲"), ("text", "乙")])
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "预测"}).text)
    assert names(events) == ["start", "text", "text", "done"]
    assert isinstance(events[0][1]["conversation_id"], int)


def test_join_of_text_deltas_equals_reply(make_client):
    """**外部锚**：前端是把 text 增量拼起来显示的，`done.reply` 是它的对照。
    两者不等 ⇒ 页面上的正文和库里存的对不上，且都不报错。"""
    chunks = [("text", c) for c in ["今", "天", "预测", "涨", "先导基电"]]
    tc, _, _ = make_client(chunks)
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "今天预测"}).text)
    reply = [d for n, d in events if n == "done"][0]["reply"]
    assert text_of(events) == reply == "今天预测涨先导基电"


def test_reply_is_built_from_the_emitted_deltas_only(make_client):
    """防「reply 与 text 增量同源所以恒等」的假绿：期望正文**手算**写死。
    只要有人把 reply 改成原始 chunks 的全文、或把 thinking 混进去，这里就红。"""
    chunks = [("text", "涨"), ("text", "：先导基电")]
    tc, _, _ = make_client(chunks)
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "今天涨谁"}).text)
    done = [d for n, d in events if n == "done"][0]
    assert done["reply"] == "涨：先导基电"


def test_thinking_deltas_are_separate_and_excluded_from_reply(make_client):
    """thinking 与 text 是两个事件、两种语义。把 thinking 当 text 会让
    推理过程直接糊到页面上，同时把 `"".join(text) == reply` 一起弄坏。"""
    tc, _, _ = make_client([
        ("thinking", "先看榜单…"), ("text", "涨的是"), ("thinking", "再核一遍…"), ("text", "先导基电"),
    ])
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "涨谁"}).text)
    assert names(events) == ["start", "thinking", "text", "thinking", "text", "done"]
    assert thinking_of(events) == "先看榜单…再核一遍…"
    assert [d for n, d in events if n == "done"][0]["reply"] == "涨的是先导基电"


def test_thinking_may_be_absent_entirely(make_client):
    """契约明说 thinking「可一个都没有」——不能因此崩、不能因此塞一个空事件。"""
    tc, _, _ = make_client([("text", "只有正文")])
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "hi"}).text)
    assert "thinking" not in names(events)
    assert [d for n, d in events if n == "done"][0]["reply"] == "只有正文"


def test_only_the_five_event_names_are_allowed(make_client):
    """事件名集合被钉死：前端按名字分支，多一个名字就有一类事件被静默忽略。"""
    tc, _, _ = make_client([("text", "甲")])
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "hi"}).text)
    assert set(names(events)) <= {"start", "thinking", "text", "done", "error"}


def test_every_event_is_exactly_three_lines(make_client):
    """每事件三行：event / data / 空行。少一个空行，浏览器会把两个事件
    并成一个 chunk（这是 SSE 最经典的静默失效，且不报错）。"""
    tc, _, _ = make_client([("text", "甲"), ("text", "乙")])
    raw = tc.post("/api/chat/stream", json={"message": "hi"}).text
    blocks = [b for b in raw.split("\n\n") if b.strip()]
    assert len(blocks) == 4  # start + 2 text + done
    for b in blocks:
        lines = b.split("\n")
        assert len(lines) == 2, f"事件块不是两行内容: {lines!r}"
        assert lines[0].startswith("event: ")
        assert lines[1].startswith("data: ")


# ================================================================ 入库顺序（关键）


class _ConnSpy:
    """在**运行层**记录「assistant 行已写库」这个真实副作用发生的时刻。"""

    def __init__(self, conn, log):
        self._conn, self._log = conn, log

    def execute(self, sql, *args, **kw):
        if "INSERT INTO messages" in sql and "'assistant'" in sql:
            self._log.append("db:assistant")
        elif "INSERT INTO messages" in sql and "'user'" in sql:
            self._log.append("db:user")
        return self._conn.execute(sql, *args, **kw)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)


@pytest.fixture
def order_log(monkeypatch):
    """把两个真实副作用按**实际发生顺序**记进一个列表。

    ## 为什么不能在 HTTP 客户端侧判顺序（实测踩到的坑）

    starlette 的 `TestClient` 会把整个响应体缓冲完再交给客户端，
    实测：测试读到**第一个**事件（start）时，库里已经有 assistant 行了。
    ⇒ 「读到 done 的当口查库」根本区分不出「先写库后发 done」与
    「先发 done 后写库」——两种顺序都绿。**那是个安慰剂守卫。**
    所以顺序必须在真正发生的运行层观测：给 `_connect`（写库）与
    `_sse`（发事件）各挂一个旁路，记下真实先后。
    """
    import aplus.webapp as webapp_mod

    log: list = []
    real_connect = webapp_mod._connect

    def _spy_connect(db_path):
        return _ConnSpy(real_connect(db_path), log)

    real_sse = webapp_mod._sse

    def _spy_sse(event, data):
        log.append(f"sse:{event}")
        return real_sse(event, data)

    monkeypatch.setattr(webapp_mod, "_connect", _spy_connect)
    monkeypatch.setattr(webapp_mod, "_sse", _spy_sse)
    return log


def test_assistant_row_is_written_before_done_event_is_emitted(make_client, order_log):
    """需求 1 的**顺序**守卫：assistant 必须在发 done **之前**落库。

    反过来（先发 done 再落库）的后果：用户正好在那一刻刷新页面，
    回复就没了，而前端**不报错**、HTTP 全部 200。
    """
    tc, _, _ = make_client([("text", "甲"), ("text", "乙")])
    tc.post("/api/chat/stream", json={"message": "在吗"})

    assert "sse:done" in order_log, order_log
    assert "db:assistant" in order_log, order_log
    assert order_log.index("db:assistant") < order_log.index("sse:done"), (
        f"assistant 落库晚于 done 事件：{order_log}")


def test_user_row_is_written_before_start_event_is_emitted(make_client, order_log):
    """start 的语义是「消息已入库、开始调用模型」——顺序也是契约的一部分。"""
    tc, fake, _ = make_client([("text", "甲")])
    tc.post("/api/chat/stream", json={"message": "今天涨谁"})
    assert order_log.index("db:user") < order_log.index("sse:start"), order_log
    assert order_log.index("db:user") < order_log.index("sse:text"), order_log
    # start 之后模型拿到的历史必须包含刚入库的 user 消息
    assert [m["role"] for m in fake.last_messages] == ["user"]


def test_assistant_message_is_in_db_when_the_response_completes(make_client):
    """端到端状态断言：请求结束后 assistant 行与 reply 逐字相等。"""
    tc, _, root = make_client([("text", "甲"), ("text", "乙")])
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "在吗"}).text)
    done = [d for n, d in events if n == "done"][0]
    rows = messages_in_db(root, done["conversation_id"])
    assert [r for r, _ in rows] == ["user", "assistant"]
    assert rows[1][1] == done["reply"] == "甲乙"


def test_conversation_memory_is_carried_into_the_stream_call(make_client):
    tc, _, _ = make_client([("text", "一")])
    first = parse_sse(tc.post("/api/chat/stream", json={"message": "今天涨谁"}).text)
    conv = [d for n, d in first if n == "start"][0]["conversation_id"]

    tc2, fake2, _ = make_client([("text", "二")])
    events = parse_sse(tc2.post(
        "/api/chat/stream", json={"conversation_id": conv, "message": "为什么"}).text)
    assert [d for n, d in events if n == "done"][0]["conversation_id"] == conv
    roles = [m["role"] for m in fake2.last_messages]
    assert roles == ["user", "assistant", "user"]


# ================================================================ 错误路径


def test_error_sent_once_then_stream_ends_without_done(make_client):
    """代理完全不可用：只发一次 error，**不发 done**，流正常结束（不抛断连）。
    前端要显示人话，断连只会让它拿到一个空 error。"""
    tc, _, _ = make_client([], fail_before_start=ConnectionError("proxy down"))
    resp = tc.post("/api/chat/stream", json={"message": "在吗"})
    assert resp.status_code == 200  # 前端已经连上 SSE，不能是 5xx
    events = parse_sse(resp.text)
    assert names(events).count("error") == 1
    assert "done" not in names(events)
    msg = [d for n, d in events if n == "error"][0]["message"]
    assert "proxy down" in msg
    assert re.search(r"[一-鿿]", msg), "error 必须是中文人话，不是裸异常类名"


def test_error_midstream_does_not_emit_done(tmp_path):
    """已经吐了一半才断：仍然只能有 error、没有 done ——
    半截正文绝不能被当成完整回复落进 done。"""
    fake = FakeStreamLLM([("text", "前半"), ("text", "后半")], fail_at=1)
    fake.fail_at_error = ConnectionError("stream closed")
    tc = TestClient(create_app(tmp_path, llm=fake))
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "长一点的问题"}).text)
    assert names(events) == ["start", "text", "error"]
    assert "done" not in names(events)
    # 半截正文不得入库（没有 done 就没有完整回复）
    rows = messages_in_db(tmp_path, [d for n, d in events if n == "start"][0]["conversation_id"])
    assert [r for r, _ in rows] == ["user"]


def test_truncated_reply_never_enters_the_next_round_history(tmp_path):
    """断流留下的半截正文，绝不能进下一轮的 history。

    审计指出的那条链：半截 assistant 落库 ⇒ `/api/conversations/{id}` 原样
    返回 ⇒ 前端渲染 ⇒ **刷新后半句话永久留在对话里，且此后每轮都带着它**。
    系统全程 200、pytest 全绿——只有这条守卫能看见。
    """
    fake = FakeStreamLLM([("text", "前半"), ("text", "后半")], fail_at=1)
    fake.fail_at_error = ConnectionError("stream closed")
    tc = TestClient(create_app(tmp_path, llm=fake))
    events = parse_sse(tc.post("/api/chat/stream", json={"message": "长问题"}).text)
    conv = [d for n, d in events if n == "start"][0]["conversation_id"]

    # 第二轮正常回答，检查模型这一轮看到的历史
    fake2 = FakeStreamLLM([("text", "第二轮答")])
    tc2 = TestClient(create_app(tmp_path, llm=fake2))
    conv2 = [d for n, d in parse_sse(
        tc2.post("/api/chat/stream", json={"conversation_id": conv, "message": "再说"}).text
    ) if n == "start"][0]["conversation_id"]

    seen = "".join(m["content"] for m in fake2.last_messages)
    assert "前半" not in seen, f"半截回复混进了下一轮 history：{fake2.last_messages!r}"
    assert [m["role"] for m in fake2.last_messages] == ["user", "user"]
    # /api/conversations 也不能吐出半截（前端刷新后就靠它渲染）
    served = tc2.get(f"/api/conversations/{conv2}").json()
    assert [m["role"] for m in served] == ["user", "user", "assistant"]
    assert served[-1]["content"] == "第二轮答"


def test_empty_message_yields_error_event_not_http_400(make_client):
    """空消息沿用 `/api/chat` 的语义（拒绝），但**必须走 error 事件**：
    前端此时已经连上 SSE，HTTP 400 会让它读到非流式响应而不知道发生了什么。"""
    for payload in ({"message": "   "}, {"message": ""}, {"message": "\n\t "}):
        tc, _, _ = make_client([("text", "甲")])
        resp = tc.post("/api/chat/stream", json=payload)
        assert resp.status_code == 200, f"{payload} 不该是 HTTP 错误"
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(resp.text)
        assert names(events) == ["error"], f"{payload} → {names(events)}"
        assert events[0][1] == {"message": "消息不能为空"}
        assert "start" not in names(events)


def test_unknown_conversation_yields_error_event(make_client):
    tc, _, _ = make_client([("text", "甲")])
    resp = tc.post("/api/chat/stream", json={"message": "在吗", "conversation_id": 9999})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert names(events) == ["error"]
    assert "会话不存在" in events[0][1]["message"]


def test_error_event_message_is_json_serialisable_and_utf8(make_client):
    """error 的 message 走 JSON 编码，中文不能变成 \\uXXXX（前端直接显示）。"""
    tc, _, _ = make_client([], fail_before_start=ValueError("上游返回了非法 JSON 字符 "))
    raw = tc.post("/api/chat/stream", json={"message": "在吗"}).text
    assert "\\u" not in raw
    assert "非法 JSON" in raw


# ================================================================ 旧端点不受影响


def test_legacy_non_streaming_chat_still_works(make_client):
    """`/api/chat` 保留不动（有别的调用方和 tests/test_webapp.py 依赖）。"""
    tc, fake, _ = make_client([("text", "老接口回答")])
    r = tc.post("/api/chat", json={"message": "在吗"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"conversation_id", "reply"}
    assert body["reply"] == "老接口回答"


# ================================================================ llm.py：流式方法


class FakeStreamResponse:
    def __init__(self, lines, status_code=200):
        self._lines = list(lines)
        self.status_code = status_code
        self.text = "\n".join(lines)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)


def sse_lines(*blocks):
    out = []
    for name, data in blocks:
        out.append(f"event: {name}")
        out.append("data: " + json.dumps(data, ensure_ascii=False))
        out.append("")
    return out


def delta_block(kind, text):
    key = "thinking" if kind == "thinking_delta" else "text"
    return ("content_block_delta", {"type": "content_block_delta",
                                    "delta": {"type": kind, key: text}})


@pytest.fixture
def captured_post(monkeypatch):
    """抓住 requests.post 的调用：断言流式方法**委派**已有的请求构造，
    而不是另起炉灶写一份 payload（两份 payload 会各自漂移）。

    夹具里故意混入 `message_start` / `ping` / `message_stop` 三种噪声事件，
    免得「只认 content_block_delta」这条分支从来没被真正执行过。
    """
    calls = []

    def _post(url, json=None, headers=None, timeout=None, stream=None):
        calls.append({"url": url, "json": json, "headers": headers, "stream": stream})
        return FakeStreamResponse([
            *sse_lines(("message_start", {"type": "message_start"})),
            *sse_lines(("ping", {"type": "ping"})),
            *sse_lines(delta_block("thinking_delta", "想")),
            *sse_lines(delta_block("text_delta", "答1")),
            *sse_lines(delta_block("text_delta", "答2")),
            *sse_lines(("message_stop", {"type": "message_stop"})),
        ])

    monkeypatch.setattr(llm_mod.requests, "post", _post)
    return calls


def test_glm_proxy_stream_splits_thinking_and_text(captured_post):
    c = GlmProxyClient()
    got = list(c.chat_stream([{"role": "user", "content": "hi"}], system="S"))
    assert got == [("thinking", "想"), ("text", "答1"), ("text", "答2")]
    assert captured_post[0]["stream"] is True


def test_glm_proxy_stream_delegates_the_same_request_construction(captured_post):
    """委派守卫：流式与非流式的 payload 必须同源，只多一个 `stream: True`。
    变异 = 在 chat_stream 里重写一份 payload（比如忘了 system）→ 这里红。"""
    c = GlmProxyClient(model="glm-x", max_tokens=777)
    msgs = [{"role": "user", "content": "hi"}]
    list(c.chat_stream(msgs, system="系统提示"))

    payload, headers = c._build_request(msgs, system="系统提示")
    sent = captured_post[0]["json"]
    assert sent == {**payload, "stream": True}
    assert sent["system"] == "系统提示"
    assert sent["model"] == "glm-x"
    assert sent["max_tokens"] == 777
    assert sent["messages"] == [{"role": "user", "content": "hi"}]
    assert captured_post[0]["url"].endswith("/v1/messages")
    assert captured_post[0]["headers"]["anthropic-version"] == "2023-06-01"


def test_stream_raises_on_upstream_error_event(monkeypatch):
    """上游 SSE 里的 error 事件必须变成异常往上抛 ——
    吞掉它 = 页面上出现一个「成功但空白」的回复（静默失效）。

    （必须是自带 error 事件的夹具：用正常的 captured_post 断言这个，
    只会得到「没抛异常」的假绿——流里根本没有错误。）
    """
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: FakeStreamResponse([
        *sse_lines(delta_block("text_delta", "半句")),
        *sse_lines(("error", {"type": "error", "error": {"message": "配额已用尽"}})),
    ]))
    with pytest.raises(RuntimeError, match="配额"):
        list(GlmProxyClient().chat_stream([{"role": "user", "content": "hi"}]))


def test_stream_ignores_ping_and_message_stop(captured_post):
    assert [k for k, _ in list(GlmProxyClient().chat_stream([{"role": "user", "content": "x"}]))] \
        == ["thinking", "text", "text"]


def test_m31_flash_stream_splits_thinking_and_text(captured_post):
    """M3.1 强制思考 ⇒ content_block_delta 里 thinking_delta 是常态。
    漏掉它 → 用户看不到思考；把它当 text → 推理过程糊到正文里。"""
    c = MiniMaxM31FlashClient(api_key="sk-x")
    got = list(c.chat_stream([{"role": "user", "content": "hi"}], system="S"))
    assert got == [("thinking", "想"), ("text", "答1"), ("text", "答2")]
    payload, _headers = c._build_request([{"role": "user", "content": "hi"}], system="S")
    assert captured_post[0]["json"] == {**payload, "stream": True}


def test_stream_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        llm_mod.requests, "post",
        lambda *a, **k: FakeStreamResponse(["event: x", "data: {}", ""], status_code=502),
    )
    with pytest.raises(RuntimeError):
        list(GlmProxyClient().chat_stream([{"role": "user", "content": "hi"}]))


def test_empty_delta_stream_yields_nothing(monkeypatch):
    monkeypatch.setattr(
        llm_mod.requests, "post",
        lambda *a, **k: FakeStreamResponse(sse_lines(("message_stop", {"type": "message_stop"}))),
    )
    assert list(GlmProxyClient().chat_stream([{"role": "user", "content": "hi"}])) == []


class StreamFake:
    """同时有 chat / chat_stream 的假客户端（两条路径都要能跑）。"""

    name = "fake"

    def __init__(self, chunks, error=None):
        self.chunks = list(chunks)
        self.error = error
        self.calls = 0

    def chat(self, messages, system=""):
        self.calls += 1
        if self.error is not None and self.error[0] == "before":
            raise self.error[1]
        return "".join(d for k, d in self.chunks if k == "text")

    def chat_stream(self, messages, system=""):
        self.calls += 1
        if self.error is not None and self.error[0] == "before":
            raise self.error[1]
        for i, c in enumerate(self.chunks):
            if self.error is not None and self.error[0] == "mid" and self.error[1] == i:
                raise ConnectionError("断流")
            yield c


def test_fallback_chain_stream_uses_primary_and_records_last_served_by():
    p = StreamFake([("text", "主")])
    p.name = "m31-flash"
    b = StreamFake([("text", "备")])
    b.name = "glm-proxy"
    chain = FallbackChain(p, b)
    assert list(chain.chat_stream([])) == [("text", "主")]
    assert chain.last_served_by == "m31-flash"
    assert b.calls == 0


def test_fallback_chain_stream_falls_back_to_backup():
    p = StreamFake([], error=("before", ConnectionError("全部 key 冷却")))
    p.name = "m31-flash"
    b = StreamFake([("text", "备")])
    b.name = "glm-proxy"
    chain = FallbackChain(p, b)
    assert list(chain.chat_stream([])) == [("text", "备")]
    assert chain.last_served_by == "glm-proxy"


def test_fallback_chain_stream_does_not_restart_mid_stream():
    """中途中断**不能**切备链重来：已经吐给页面的增量收不回来，
    重来会让 `"".join(text) == reply` 直接变成两倍长。"""
    p = StreamFake([("text", "主1"), ("text", "主2")], error=("mid", 1))
    b = StreamFake([("text", "备")])
    chain = FallbackChain(p, b)
    out = []
    with pytest.raises(ConnectionError):
        for item in chain.chat_stream([]):
            out.append(item)
    assert out == [("text", "主1")]
    assert b.calls == 0, "已经吐过增量后还去碰备链 = 重复内容"


def test_fallback_chain_stream_raises_when_both_fail():
    p = StreamFake([], error=("before", ConnectionError("p")))
    b = StreamFake([], error=("before", TimeoutError("b")))
    with pytest.raises(TimeoutError):
        list(FallbackChain(p, b).chat_stream([]))


# ================================================================ GET /api/llm/info


def test_llm_info_reports_runtime_models_not_hardcoded_strings(root):
    """**外部锚**：造一个**非默认** model 的链，端点必须原样报出来。
    如果 webapp 里把 model 写死成 'glm-5.3'，这里立刻红。"""
    glm = GlmProxyClient(model="glm-CUSTOM")
    chain = FallbackChain(glm, MiniMaxM31FlashClient(api_key="sk-x", model="m-CUSTOM"))
    chain.primary.name = "m31-flash"
    tc = TestClient(create_app(root, llm=chain))
    body = tc.get("/api/llm/info").json()
    assert [m["model"] for m in body["chain"]] == ["glm-CUSTOM", "m-CUSTOM"]
    assert body["primary"] == "m31-flash"
    assert body["last_served_by"] is None
    assert [m["kind"] for m in body["chain"]] == ["proxy", "direct"]


def test_llm_info_matches_real_client_defaults(root):
    """模型名必须等于**客户端自己**的默认配置（真值在 llm.py 里），
    而不是 webapp 里的字符串字面量。"""
    chain = FallbackChain(MiniMaxM31FlashClient(api_key="sk-x"), GlmProxyClient())
    tc = TestClient(create_app(root, llm=chain))
    body = tc.get("/api/llm/info").json()
    assert body["chain"][0]["model"] == MiniMaxM31FlashClient(api_key="k").model
    assert body["chain"][0]["model"] == "MiniMax-M3.1-Flash"
    assert body["chain"][1]["model"] == GlmProxyClient().model == "glm-5.3"


def test_llm_info_never_leaks_the_retired_m3_model(root):
    """M3 客户端已删除。界面上再出现 'MiniMax-M3'（且不是 .1）就是漂移。
    注意断言写法：'MiniMax-M3.1-Flash' 里也含 'MiniMax-M3'，
    所以必须用「后面不接 . 或数字」的否定先行。"""
    chain = FallbackChain(MiniMaxM31FlashClient(api_key="sk-x"), GlmProxyClient())
    tc = TestClient(create_app(root, llm=chain))
    raw = tc.get("/api/llm/info").text
    assert re.search(r"MiniMax-M3(?![\d.])", raw) is None, raw
    # 正例：.1 版本必须能原样通过（防止断言写太狠，把真模型也毙了）
    assert re.search(r"MiniMax-M3(?=[\d.])", "MiniMax-M3.1-Flash") is not None


def test_llm_info_model_is_null_when_absent_not_a_hardcoded_name(root):
    """取不到就 null —— **不许回退到硬编码名字**：回退等于把要消灭的漂移请回来。"""
    class NamelessClient:
        name = "weird"

        def chat(self, messages, system=""):
            return "x"

        def chat_stream(self, messages, system=""):
            yield "text", "x"

    tc = TestClient(create_app(root, llm=NamelessClient()))
    body = tc.get("/api/llm/info").json()
    assert body["chain"] == [{"name": "weird", "model": None, "kind": None}]
    assert body["primary"] == "weird"


def test_llm_info_last_served_by_updates_after_non_streaming_chat(root):
    """需求：非流式与流式两条路径都要更新 last_served_by。"""
    chain = FallbackChain(StreamFake([("text", "主")]), StreamFake([("text", "备")]))
    chain.primary.name, chain.backup.name = "m31-flash", "glm-proxy"
    tc = TestClient(create_app(root, llm=chain))
    assert tc.get("/api/llm/info").json()["last_served_by"] is None
    tc.post("/api/chat", json={"message": "在吗"})  # ← 旧的非流式端点
    assert tc.get("/api/llm/info").json()["last_served_by"] == "m31-flash"


def test_llm_info_last_served_by_updates_after_streaming_call(root):
    p = StreamFake([("text", "主")])
    p.name = "m31-flash"
    b = StreamFake([("text", "备")])
    b.name = "glm-proxy"
    chain = FallbackChain(p, b)
    tc = TestClient(create_app(root, llm=chain))
    tc.post("/api/chat/stream", json={"message": "在吗"})
    assert tc.get("/api/llm/info").json()["last_served_by"] == "m31-flash"


def test_llm_info_survives_an_unknown_client_shape(root):
    """注入对象只有 chat、没有 primary/backup ⇒ 也要 200，不能 500。"""
    class OnlyChat:
        def chat(self, messages, system=""):
            return "x"

    tc = TestClient(create_app(root, llm=OnlyChat()))
    body = tc.get("/api/llm/info").json()
    assert body["chain"] and body["primary"]

# ================================================================ 变异验证记录
#
# 驱动器：/tmp/aplus_mutation/run_mutations.py（每条：先确认基线全绿 →
# 写变异 → grep 式行数差确认改动落盘 → 跑选择器 → 还原并校验还原成功）
#
# | # | 变异 | 守卫 | 结果 |
# |---|---|---|---|
# | M1 | assistant 落库挪到发 done **之后** | test_assistant_row_is_written_before_done_event_is_emitted | ✅ 红 |
# | M2 | 结尾事件 done → error | join_of_text_deltas / reply_is_built / event_sequence / only_the_five | ✅ 红 |
# | M3 | thinking 增量改发 text 事件 | -k thinking（4 passed → 1 failed） | ✅ 红 |
# | M4 | model 读成硬编码 "MiniMax-M3.1-Flash-Preview" | -k llm_info（7 → 3 failed） | ✅ 红 |
# | M5 | last_served_by 写死 None | -k last_served_by（3 → 2 failed） | ✅ 红 |
# | M6 | 空消息改回 HTTP 400 | test_empty_message_yields_error_event_not_http_400 | ✅ 红 |
# | M7 | 出错直接抛（断连），不发 error | -k error（7 → 3 failed） | ✅ 红 |
# | M8 | 断流时照样把半截 assistant 落库 | midstream / truncated（2 → 2 failed） | ✅ 红 |
# | M9 | chat_stream 另写一份 payload（漏 system） | test_glm_proxy_stream_delegates_the_same_request_construction | ✅ 红 |
# | M10 | 去掉 FallbackChain 的 emitted 守卫 | test_fallback_chain_stream_does_not_restart_mid_stream | ✅ 红 |
# | M11 | thinking_delta 当 text_delta | splits_thinking / ignores_ping（3 → 3 failed） | ✅ 红 |
# | M12 | SSE 少一个空行（事件被并成一块） | exactly_three_lines / join_of_text_deltas | ✅ 红 |
#
# 任务清单里的「去掉 rename」在本仓**没有对应符号**：`grep -n rename
# aplus/llm.py aplus/webapp.py` 零命中，流式实现里不存在该步骤。
# 代入同族的「去掉中途中断保护」= M10，另加 M9（去掉请求构造委派），
# 两条都变红。
#
# 结论：**12 条变异，12 条变红，0 条安慰剂**。
