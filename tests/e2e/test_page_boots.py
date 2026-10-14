"""★ 第一组 E2E：页面**能不能真的起来**，以及起来之后**是不是真流式**。

这组四条对应今天已实证的四类事故，每一条都是「2101 条 pytest 全绿时照样会发生」的那种。

| E2E | 它能抓到、pytest 抓不到的 |
|---|---|
| `…boots_with_zero_console_errors…` | 任何 4xx/5xx 静态资源、任何运行期 JS 异常 |
| `…actually_left_the_skeleton_state` | 整块脚本被丢弃 ⇒ 页面永远停在「加载中」 |
| `…model_label_comes_from_the_runtime_endpoint` | 界面显示的模型 ≠ 实际跑的模型（静默矛盾） |
| `…deltas_land_in_the_dom_incrementally` | 后端在流、前端仍一次性整段上屏 |

## 为什么「跑绿了还要看 console」

这是本套件最容易被自己骗掉的地方。E2E 测试的默认失败模式**不是红**，
是「绿着一件没测的事」：断言写在错误的元素上、等待条件写得太宽，
于是页面其实在报错而测试没问。
⇒ 每条测试都拿 `probe` 断言 console / pageerror / failed_requests，
不是「出错时才去看」。
"""

from __future__ import annotations

import pytest


def _goto_chat(page, live_server):
    """打开页面并切到「追问」页签。

    `panel-chat` 在 HTML 里带 `hidden`，不点 tab 的话
    `#msgs` 的 innerText 恒为空 —— 而「空」会被后面的断言读成「还在加载」。
    """
    page.goto(live_server, wait_until="domcontentloaded")
    page.click("#tab-chat")
    page.wait_for_selector("#panel-chat:not([hidden])", timeout=15_000)


# ── ① 页面能起来，且这一跑是干净的 ──────────────────────────────────

def test_the_page_boots_with_zero_console_errors_and_zero_failed_requests(page, probe, live_server):
    """★ 这条是整组的地基：页面必须**零 console 错误、零失败请求**地起来。

    为什么把它排在第一位：后面每一条断言都建立在「页面确实活着」之上。
    页面若在启动时就炸了，后面所有断言都会因为元素不存在而变成另一种失败，
    我们会**误诊**，而不是拿到真正的根因。

    收集器在 `conftest.PageProbe` 里挂好，不是这里临时挂的 ——
    否则「只有出错时才监听」会把「出错发生在监听之前」这类漏掉。
    """
    page.goto(live_server, wait_until="domcontentloaded")

    # ★ 等「页面自己说它准备好了」，不是 sleep。
    # 判据取自真实的首屏信号：模型信息读回来了（初值是「读取模型信息…」）。
    page.wait_for_function(
        "() => { const e = document.getElementById('llm-info');"
        "        return e && !e.textContent.includes('读取模型信息'); }",
        timeout=30_000,
    )

    # 收集器需要一点时间把「异步回来才出现」的 4xx/5xx 记进来，
    # 这里等的是**网络静默**而不是固定时长（见 conftest 的纪律②）。
    page.wait_for_load_state("networkidle", timeout=30_000)

    console_errors, page_errors, failed = probe.healthy()
    assert not console_errors, f"console 里有 error：{console_errors}"
    assert not page_errors, f"有未捕获异常：{page_errors}"
    # favicon 是浏览器自己发的、我们没提供的资源，与前端正确性无关
    real = [f for f in failed if "favicon" not in f]
    assert not real, f"有失败请求：{real}"


def test_the_page_actually_left_the_skeleton_state(page, live_server):
    """★ 反静默失效专用。

    `panel-overview` 的首屏元素初值是 `加载预测数据…`（`index.html:406`）。
    **脚本整块没跑起来时，这个占位符会永远留在页面上** ——
    而页面**不报任何错**，地址栏正常，HTTP 200 正常。

    今天真发生过：同一作用域里重复声明 `const text` 是解析期 SyntaxError，
    浏览器整块丢弃脚本，页面停在「加载…」，而
    `test_chat_stream.py` 37 条 + `test_chat_wiring.py` 11 条全绿。

    ⇒ 这里断言的是**占位符消失了**，不是「某个接口返回了 200」。
    """
    page.goto(live_server, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const h = document.getElementById('heroes');"
        "        return h && !h.textContent.includes('加载预测数据'); }",
        timeout=30_000,
    )
    assert "加载预测数据" not in page.inner_text("#heroes"), \
        "#heroes 还停在骨架屏 —— 首屏脚本没跑起来"


# ── ② 模型名必须来自运行态 ──────────────────────────────────────────

def test_the_model_label_comes_from_the_runtime_endpoint(page, fake_llm, live_server):
    """★ 守「界面显示的模型 = 实际跑的模型」这条铁律。

    做法不是 grep 源码（`grep MiniMax index.html == 0` 那条静态守卫已经在
    `test_chat_wiring.py` 里了），而是**端到端证明**：
    注入一个**故意不存在于任何源码里**的模型名 `fake-e2e-model-9f3a`，
    它必须出现在界面上。

    这样才能同时证明两件事：接口真的返回了它、前端真的读了它。
    任何一端断掉，这条都红。

    事故原型（`index.html:1753` 的原话）：M3 客户端被删了只剩 M3.1，
    界面还硬编码着「MiniMax M3（思考模式）」，**没有任何测试或告警**。
    """
    assert fake_llm.model == "fake-e2e-model-9f3a", \
        "夹具前提变了：这条测试的判别力来自「这个名字不在任何源码里」"

    page.goto(live_server, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const e = document.getElementById('llm-info');"
        "        return e && !e.textContent.includes('读取模型信息'); }",
        timeout=30_000,
    )
    label = page.inner_text("#llm-info")
    assert fake_llm.model in label, \
        f"界面模型标签是 {label!r}，不含运行态的 {fake_llm.model!r} —— 前端可能在显示硬编码名"


# ── ③ 真流式：增量必须**分帧**落进 DOM ──────────────────────────────

def test_sse_deltas_land_in_the_dom_incrementally(page, fake_llm, live_server):
    """★ 全套件里最重要的一条 —— 而且它**第一版是有洞的**，洞的形状值得记住。

    `FakeLLM` 每 0.1 秒吐一段、共 6 段 ⇒ 一次对话约 0.9 秒。
    如果前端是**一次性**上屏（`await res.json()`），正文只会跳一次。

    为什么不能退化成「最终内容对」：最终内容对，恰恰是一次性上屏也满足的。
    **那条断言守不住「有没有流」这件事** —— 它只能守「流到的内容对不对」。

    ## ★ 第一版的洞（变异验证抓出来的，不是想出来的）

    第一版量的是 `inner_text('#msgs')` 的**总长度**。变异把 `text` 分支改成
    「攒进 `turn.mbuf`、只在 `done` 时一次性写进 DOM」之后，
    **测试照样绿**（我确认过：哈希变了、`node --check` 通过、1 passed）。

    为什么绿：**thinking 增量自己也是分帧追加的**（`appendText(turn.think, …)`，
    3 段 × 0.1 秒）。DOM 总长度照样增长 4 次，把一次性上屏的正文**喂饱了**。

    这和今天踩的「注释满足子串断言」是同一个病：**口径太宽，把别的信号算了进来**。
    ⇒ 现在只量**正文**：遍历 `.turn.a` 的文本节点，跳过 `details.think`
    内部的内容，也跳过 `who`/`at` 标签里的固定文案。
    下面那条 `…even_when_the_model_thinks_nothing` 是同一条守卫的对照臂 ——
    把 thinking 关掉，剩下的增长只能来自正文自己。
    """
    expected = fake_llm._full_text()          # 夹具的正文真值，不是从页面上反推的

    _goto_chat(page, live_server)
    page.fill("#chat-text", "明天涨哪些")
    page.click("#send")

    samples = _sample_answer_growth(page, expected)

    text = page.inner_text("#msgs")
    assert expected in text.replace("\n", ""), \
        f"最终正文不对。期望 {expected!r}，页面里是 {text!r}"

    assert len(samples) >= 3, (
        f"正文只观察到 {len(samples)} 个互异长度 {samples} —— "
        "后端在流，前端却一次性整段上屏（这条 pytest 从原理上看不见）")


def test_the_answer_still_streams_when_the_model_thinks_nothing(page, server_for):
    """★ 对照臂：把 thinking 关掉（`thinking_deltas=0`）。

    留这条不是为了多测一个场景，而是为了让上面那条**无法被别的信号喂饱** ——
    任何「另一个也在流的东西」都不能再替正文提供增长帧。

    这就是「判据必须预注册」在测试上的形态：
    写下 `>=3` 这个门槛时，我知道它对应的是「6 段正文至少观察到 3 帧」；
    这条对照臂证明这个门槛不是被 thinking 蹭出来的。
    """
    with server_for(thinking_deltas=0, text_deltas=6, delay=0.12) as url:
        expected = "段1段2段3段4段5段6"
        _goto_chat(page, url)
        page.fill("#chat-text", "明天涨哪些")
        page.click("#send")

        samples = _sample_answer_growth(page, expected)

    assert expected in page.inner_text("#msgs").replace("\n", ""), \
        f"最终正文不对，页面里是 {page.inner_text('#msgs')!r}"
    assert len(samples) >= 3, \
        f"无 thinking 时正文只观察到 {len(samples)} 帧 {samples} —— 一次性上屏"


# 只量「正文」：遍历答案气泡的文本节点，排除思考块与固定标签。
_ANSWER_TEXT_LEN = """
(minLen) => {
  const a = document.getElementById('msgs').querySelector('.turn.a');
  if (!a) return 0;
  const w = document.createTreeWalker(a, NodeFilter.SHOW_TEXT);
  let n = 0, t;
  while ((t = w.nextNode())) {
    const p = t.parentElement;
    if (!p) continue;
    if (p.closest('details.think')) continue;                 // 思考块不算正文
    if (p.classList.contains('who') || p.classList.contains('at')) continue;
    n += t.textContent.length;
  }
  return n;
}
"""


def _sample_answer_growth(page, expected: str, *, timeout_ms: int = 15_000) -> list[int]:
    """采样正文长度直到出现完整正文，返回**互异**长度序列。

    ⚠️ 这是**采样**，不是穷举：中间可能漏掉几帧。所以判据只能写成
    「至少 N 个互异长度」，不能写「恰好 N 帧」——
    后者会在慢机器上假红，而一个会假红的守卫最终会被所有人忽略。
    """
    samples: list[int] = []
    elapsed = 0
    while elapsed < timeout_ms:
        n = page.evaluate(_ANSWER_TEXT_LEN)
        if not samples or n != samples[-1]:
            samples.append(n)
        if n >= len(expected):
            break
        page.wait_for_timeout(40)
        elapsed += 40
    return samples
