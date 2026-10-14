"""`llm.chat()` 的**类型契约**守卫 —— 后端加了 `chat_stream` 之后的护栏。

## 为什么需要它

后端为了做流式新增了 `chat_stream()`（产出增量），而 `chat()` 仍是
一次性返回 `str`。两者**长得极像**：同样的入参、同样委派 `_build_request()`
构造 payload、连 529 重试都共用 `_post_with_529_retry`，
**只差一个 `stream: True`**。

于是有一个非常自然的危险改动：**把 `chat()` 改成委托给 `chat_stream()`**，
返回 `"".join(增量)`。返回值**仍然是 str**，`chat()` 的所有测试都会绿。

而这个改动会**毒死两个非 Web 调用方**：

| 位置 | 形态 | 后果 |
|---|---|---|
| `aplus/news.py` | `try: … except Exception: pass` | 记忆里**静默少一条**，无任何报错 |
| `aplus/evolve_researcher.py` | `try: … except Exception: pass` | 同上 |

这两处的 `except: pass` 意味着：**类型漂移在这里没有症状。**
那条新闻不是「报错了」，而是**从来没被写进去**；
下次跑新闻去重、事件抽取、情绪打分，全部在一个残缺的样本上继续算，
一路算到报告里。

## 所以本文件守两件事

1. **静态**：每个 `chat()` 都**显式**标注 `-> str`（靠 AST，不用运行态 ——
   `from __future__ import annotations` 会把注解变成字符串）
2. **行为**：真造一个响应喂进**真实的** client，断言返回的**就是 str** ——
   注解可以被一行 `# type: ignore` 绕过，行为不能
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_LLM = _ROOT / "aplus" / "llm.py"


def _all_chat_defs() -> list[ast.FunctionDef]:
    """静态取出 `aplus/llm.py` 里**所有**名为 `chat` 的函数定义。"""
    tree = ast.parse(_LLM.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "chat"]


class _FakeResp:
    """够用的 Anthropic 形状响应。"""

    status_code = 200
    headers: dict = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"content": [{"type": "text", "text": "今天涨的是先导基电。"}]}


@pytest.fixture()
def canned_post(monkeypatch):
    r"""把 `requests.post` 打桩掉 —— 三个实现全部经由它，无一例外。

    这条的依据是 `grep -n "requests\." aplus/llm.py`：
    GlmProxyClient 走两处直调，MiniMaxM31FlashClient 走 `_post_with_529_retry`
    里的直调，ClaudePClient 不发 HTTP。
    """
    from aplus import llm as L

    seen: list[dict] = []

    def _post(url, json=None, headers=None, **kw):
        seen.append({"url": url, "payload": json, "headers": headers})
        return _FakeResp()

    monkeypatch.setattr(L.requests, "post", _post)
    return seen


# ── ① 静态：每个 chat() 都显式标注 -> str ───────────────────────────────

def test_llm_py_defines_at_least_three_chat_implementations():
    """夹具前提先钉住：链 + 至少两个 client。若将来只剩一个，
    下面每一条 `parametrize` 都会在空集上通过 —— 那是最隐蔽的一种假绿。"""
    assert len(_all_chat_defs()) >= 3, \
        f"只找到 {len(_all_chat_defs())} 个 chat 定义 —— 夹具前提变了，本文件的守卫全部失效"


@pytest.mark.parametrize("node", _all_chat_defs(), ids=lambda n: f"llm.py:{n.lineno}")
def test_every_chat_declares_str_as_its_return_type(node):
    """★ `chat()` 必须**显式**标注 `-> str`。

    用 AST 而非 `inspect`：运行态拿到的注解可能已被
    `from __future__ import annotations` 变成字符串。
    而 `chat_stream()` 的返回标注必然**不是** `str`（它是生成器）
    ⇒ 靠「标注」就能把两个函数区分开，不必去找它们的定义处。
    """
    ret = node.returns
    assert ret is not None, f"llm.py:{node.lineno} 的 chat() 没有返回类型标注 —— 契约无法被检查"
    assert ast.unparse(ret) == "str", \
        f"llm.py:{node.lineno} 的 chat() 返回标注是 `{ast.unparse(ret)}` —— 调用方要的是一次性字符串"


def test_the_stream_methods_are_not_annotated_as_str():
    """反向夹具：确认上面的守卫**不是**靠「所有方法都写 str」蒙对的。

    `chat_stream` 必须返回可迭代对象。若哪天有人给所有方法统一标了 `-> str`，
    上面那条就会变成恒真 —— 这条先红。
    """
    tree = ast.parse(_LLM.read_text(encoding="utf-8"))
    streams = [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "chat_stream"]
    assert streams, "找不到 chat_stream —— 后端流式那批文件没加载进来？"
    for n in streams:
        assert n.returns is None or ast.unparse(n.returns) != "str", \
            f"llm.py:{n.lineno} 的 chat_stream 标成了 -> str —— 它返回的是增量流，标注错了"


# ── ② 行为：真实 client 必须交回 str ───────────────────────────────────

def test_a_real_client_returns_a_str_not_an_iterator(canned_post):
    """★ 注解可以被一行 `# type: ignore` 绕过，**行为不能**。

    打的是**生产路径**（`GlmProxyClient.chat` → `_build_request` → `requests.post`），
    不是假对象：这条守卫的全部价值就在于它跑的是真实实现。
    """
    from aplus import llm as L

    out = L.GlmProxyClient().chat([{"role": "user", "content": "涨"}])
    assert isinstance(out, str), f"chat() 返回了 {type(out).__name__} —— 不是 str"
    assert not isinstance(out, (list, tuple)) and not hasattr(out, "__next__"), \
        "返回值可被迭代/推进：像生成器（`chat` 被改成了 `chat_stream` 的别名？）"
    assert out == "今天涨的是先导基电。"
    assert canned_post, "桩没被调用 —— chat() 成了空壳，本条断言在空跑"


def test_the_fallback_chain_also_returns_a_str(canned_post):
    """链是最外层：它把 `primary.chat()` 的返回值**原样**往上抛。
    链这一层若改成流式，`news.py` 收到的就是生成器 —— 且不报错。"""
    from aplus import llm as L

    chain = L.FallbackChain(L.GlmProxyClient(model="m1"), L.GlmProxyClient(model="m2"))
    out = chain.chat([{"role": "user", "content": "涨"}])
    assert isinstance(out, str), f"链返回了 {type(out).__name__}"
    assert out == "今天涨的是先导基电。"


# ── ③ 反向守卫：静默吞异常的地方必须还在 ───────────────────────────────

def test_the_silent_call_sites_really_do_swallow_exceptions():
    """★ 守的不是「有静默 except」，而是「**我以为有**」这件事是真的。

    若将来有人给 `news.py` / `evolve_researcher.py` 的 except 补了日志
    或重新抛出，这条会红 —— 那是**好事**，意味着这里可以升级守卫
    （静默消失后，类型漂移就有症状了）。
    """
    silent = []
    for rel in ("aplus/news.py", "aplus/evolve_researcher.py"):
        p = _ROOT / rel
        assert p.is_file(), f"{rel} 不存在 —— 本守卫在守一个已经不存在的文件"
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ExceptHandler) or len(node.body) != 1:
                continue
            only = node.body[0]
            is_pass = isinstance(only, ast.Pass)
            is_bare_expr = (isinstance(only, ast.Expr)
                            and isinstance(only.value, ast.Constant))
            if is_pass or is_bare_expr:
                silent.append(f"{rel}:{node.lineno}")
    assert silent, ("news.py / evolve_researcher.py 里已经**没有**静默 except 了 —— "
                    "前提已变：要么升级本守卫，要么连同上面两条一起删掉")