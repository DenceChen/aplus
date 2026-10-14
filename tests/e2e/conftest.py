"""浏览器端到端测试的**夹具层** —— 真 uvicorn + 真 Chrome + 真 SSE。

## 这层为什么单独存在

仓库里有 157 个测试文件、2101 条测试，**没有一条驱动过真实浏览器**。
今天所有关于前端的结论都是「我手动点了一下」得来的。

这个差距是致命的，因为本项目里最贵的一类故障**只发生在浏览器里**：
一段 JS 有解析期语法错误 ⇒ 浏览器**整块丢弃脚本** ⇒ 页面永远停在「加载…」，
而后端 37 条流式测试、前端 11 条接线测试**一条都不会红**。

`node --check` 能抓到这个（`tests/test_chat_wiring.py` 已加），
但它只证明「能 parse」，**不证明「跑起来没报错」**。E2E 补的就是这一段：
console 里的 error、network 里的 4xx/5xx/failed、DOM 里的增量。

## 三条设计纪律（都是今天真踩出来的）

### ① 真数据、真假模型，绝不真钱真模型

- **真数据**：`data/reports`（60 份报告）、`data/master`（628M parquet）用**符号链接**挂进临时
  `data_root`。不复制 —— 628M 复制一次要几十秒，而且复制出来的副本会**与源漂移**，
  测出来的界面就不是生产界面了。
- **假模型**：`create_app(data_root, llm=...)` 有现成的注入点，注入 `FakeLLM`。
  绝不让 E2E 去打真 LLM：那会让测试依赖网络、依赖 key、依赖别人家的限流，
  三样都不可复现，且**测试绝不能产生真实费用**。
- **真 chat.db**：单独的一份空库。**这一步是必须的，不是洁癖** —— 用真库的话，
  每跑一次 E2E 就往生产对话历史里塞垃圾，且不可回滚。

### ② 绝不 `time.sleep` 后「看一眼」

本仓最贵的一课是：等待必须**轮询一个明确的完成条件**。
今天有两次「睡 5 秒再看」把「慢」误读成「坏」。
⇒ 一律 `wait_for_function` / `expect(...).to_have_text`，条件写死、超时写死。

### ③ 每次断言 console 与 network，而不是只在出错时才看

一条 E2E 测试**跑绿了但 console 里有 error**，等于什么都没测。
⇒ 收集器在 `page` 夹具里就挂好，任何测试结束时都能问「这一跑干净吗」。
"""

from __future__ import annotations

import contextlib
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]          # aplus/
_DATA = _ROOT / "data"


# ── ① 假模型 ──────────────────────────────────────────────────────────

class FakeLLM:
    """一个**行为可控**的 LLM：延迟、增量数、失败点，全部是构造参数。

    ★ 为什么不直接用真客户端 + mock `requests`：
    那样测的是「后端内部」，不是「浏览器看到的字节」。
    本夹具要让 **SSE 真的分多次、真的隔时间** 发出去，
    否则「一次性整段上屏」和「真流式」在浏览器里长得一模一样，
    这条套件最核心的那条守卫就白写了。
    """

    def __init__(self, *, name="E2E假链", model="fake-e2e-model-9f3a",
                 kind="fake", text_deltas=6, thinking_deltas=3,
                 delay=0.10, fail_at=None, hang_at=None, reply=None):
        self.name = name
        self.model = model
        self.kind = kind
        self._text_deltas = text_deltas
        self._thinking_deltas = thinking_deltas
        self._delay = delay
        self._fail_at = fail_at            # 第几个增量处抛异常；None = 不失败
        self._hang_at = hang_at            # 第几个增量处开始**静默**（不抛也不吐）
        self._reply = reply
        self.calls: list[dict] = []        # 供测试断言「服务端只被调了一次」

    # —— 一次性接口（非流式端点用） ——
    def chat(self, messages, system: str = "") -> str:
        self.calls.append({"messages": messages, "system": system})
        return self._full_text()

    # —— 流式接口 ——
    def chat_stream(self, messages, system: str = ""):
        self.calls.append({"messages": messages, "system": system})
        # 计划表先行，thinking 与正文走**同一条**发射路径 ——
        # 第一版把它们写成两个循环，`hang_at` 的判断只落在正文循环里，
        # 于是 `hang_at=2` 在 thinking 段就已经被越过，旋钮**转不到那个档**：
        # 模型正常跑完，测试却在等一个永远不会出现的 `.fail`。
        # ★ 假夹具的旋钮也要被验证「它真的能转到那一档」，否则测试在验一个不存在的故障。
        plan = ([("thinking", f"想{k + 1}…") for k in range(self._thinking_deltas)]
                + [("text", f"段{k + 1}") for k in range(self._text_deltas)])
        i = 0
        for kind, payload in plan:
            if self._fail_at is not None and i == self._fail_at:
                raise RuntimeError("E2E 注入的断流")
            i += 1
            yield kind, payload
            self._nap()
            if self._hang_at is not None and i == self._hang_at:
                # ★ 真实故障形状：连接还活着，上游一个字节都不吐。
                # 既不 resolve 也不 reject ⇒ 前端的 finally 永远不会跑
                # ⇒ 必须靠看门狗主动 abort。这条要等满 45 秒。
                while True:
                    time.sleep(1.0)

    def _full_text(self) -> str:
        return self._reply or "".join(f"段{k + 1}" for k in range(self._text_deltas))

    def _nap(self) -> None:
        """同步生成器里用 `time.sleep`：webapp 的 SSE 消费路径本身是同步的
        （`def chat_stream()` → `StreamingResponse(iterable)`），
        跑在 threadpool 里，睡在这里不会堵住事件循环。"""
        if self._delay:
            time.sleep(self._delay)


@pytest.fixture()
def make_llm():
    """让测试自己配 `FakeLLM` 的参数。"""
    return FakeLLM


@pytest.fixture()
def fake_llm():
    """默认档：6 段正文、每段隔 0.1s ⇒ 一次对话约 0.9s，
    足够浏览器采样出多帧 DOM，又不至于让套件变慢。"""
    return FakeLLM()


# ── ② 临时 data_root：真数据（符号链接）+ 独立 chat.db ────────────────

@pytest.fixture()
def e2e_data_root(tmp_path_factory):
    """把真 `data/` 挂进临时目录，**唯独** `chat.db` 用空的新库。

    ⚠️ 为什么用符号链接而不是拷贝：
    - 628M 的 parquet 拷贝一次几十秒；
    - 更要命的是**副本会与源漂移**。E2E 的意义是「测生产界面对不对」，
      一旦数据副本落后于生产，测出来的是一个不存在的界面。

    ⚠️ 为什么 `chat.db` 必须例外：
    webapp 对它是**只写**的（每个 E2E 都会 INSERT 消息）。
    用真库 ⇒ 每跑一次套件就往生产对话历史里灌垃圾，且不可回滚。

    ⚠️ 为什么是 **function 级**而不是 session 级：
    第一版是 session 级共享，结果第二条测试读库时看到了第一条测试留下的
    `conv 1` 的行，断言「库里只有一行 user」直接变红 ——
    那不是产品缺陷，是**我的夹具把测试变成了顺序相关**。
    一个会因执行顺序改变结论的测试套件，比没有测试更坏：
    它会在某次全绿之后，让人相信一件没被验证的事。
    链接本身是 O(1) 的，每条测试重做一次几乎不花钱。
    """
    root = tmp_path_factory.mktemp("e2e_data")
    if not _DATA.is_dir():
        pytest.skip(f"找不到 {_DATA} —— E2E 需要真实报告数据")

    for child in _DATA.iterdir():
        if child.name == "chat.db":
            continue                       # ← 唯一不链接的：它是测试的写入目标
        try:
            (root / child.name).symlink_to(child)
        except OSError:
            # 跨设备 / 权限不允许时退回拷贝
            if child.is_dir():
                shutil.copytree(child, root / child.name, symlinks=True)
            else:
                shutil.copy2(child, root / child.name)
    return root


# ── ③ 真服务器 ────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def _serve(data_root, llm):
    """在**独立线程**里跑真 uvicorn，产出 base_url。

    为什么不用 `httpx.ASGITransport`：那是进程内调用，**没有真实 socket**。
    而「响应头是不是 `text/event-stream`」「chunk 是不是分多次到」这两件事
    恰恰只在真实 socket 上才成立 —— 中间有 uvicorn 的分块、有代理、
    有浏览器自己的缓冲。少一层就测不到 E2E 要测的东西。
    """
    import uvicorn

    sys.path.insert(0, str(_ROOT))
    from aplus.webapp import create_app

    app = create_app(data_root=data_root, llm=llm)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # ★ 轮询「端口真的在听」，不是「睡 1 秒就当它起来了」。
    # 今天吃过两次「睡一下再看」的亏：把慢误读成坏。
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.started:
            break
        if not thread.is_alive():
            raise RuntimeError("uvicorn 线程启动即退出 —— 夹具坏了，这条测试的结论无效")
        time.sleep(0.05)
    else:
        raise RuntimeError("uvicorn 30 秒没起来")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture()
def live_server(e2e_data_root, fake_llm):
    """默认档服务器，配 `fake_llm` 夹具。"""
    with _serve(e2e_data_root, fake_llm) as url:
        yield url


@pytest.fixture()
def server_for(e2e_data_root, make_llm):
    """给需要**自定义 LLM 行为**的测试用：`with server_for(text_deltas=0) as url:`。

    为什么需要它：有的测试必须换掉模型的行为（不吐 thinking、中途断流）才能
    到达它要验的那条路径。把 `fake_llm` 做成可覆盖的会让夹具顺序变成隐式契约，
    而隐式契约在别人加一条测试时就会静默失效。
    """
    @contextlib.contextmanager
    def _factory(**kwargs):
        with _serve(e2e_data_root, make_llm(**kwargs)) as url:
            yield url

    return _factory


# ── ④ 真浏览器 ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def browser():
    """真 Chrome。

    ★ 必须 `channel="chrome"`：本机 playwright 缓存里的 chromium 是
    1217/1223/1228，而 playwright 1.64 要求 1248 ⇒ 默认启动直接失败。
    已装的 Google Chrome 155 走 channel 即可，零下载。
    """
    sa = pytest.importorskip("playwright.sync_api", reason="没装 playwright")
    if not _chrome_installed():
        pytest.skip("本机没装 Chrome —— E2E 需要 `channel=\"chrome\"`，见 conftest 里的说明")
    with sa.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:                       # noqa: BLE001
            pytest.skip(f"Chrome 起不来（{type(exc).__name__}: {str(exc)[:120]}）")
        yield b
        b.close()


def _chrome_installed() -> bool:
    import os
    return (os.path.exists("/Applications/Google Chrome.app")
            or os.path.exists(os.path.expanduser("~/Applications/Google Chrome.app")))


class PageProbe:
    """把「这一跑干不干净」变成可断言的对象。

    ★ 这是本套件最容易被自己骗掉的地方：
    一条 E2E 跑绿了，但 console 里有 error ⇒ 它什么都没测。
    所以收集器**默认全程挂着**，而不是出错时才 `page.on("console", ...)`。
    """

    def __init__(self, page):
        self.page = page
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []      # 未捕获异常（比 console 更硬）
        self.failed_requests: list[str] = []

        page.on("console", self._on_console)
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        page.on("requestfailed",
                lambda r: self.failed_requests.append(
                    f"{r.method} {r.url} :: {r.failure}"))
        page.on("response", self._on_response)

    def _on_console(self, msg):
        # favicon.ico 404 之类不算 console error（浏览器把它记成 network 失败，
        # 那里已经单独收集了），这里只收 JS 层面的问题
        if msg.type == "error":
            self.console_errors.append(msg.text)

    def _on_response(self, resp):
        if resp.status >= 400:
            self.failed_requests.append(f"{resp.status} {resp.request.method} {resp.url}")

    def healthy(self) -> tuple[list[str], list[str], list[str]]:
        return self.console_errors, self.page_errors, self.failed_requests


@pytest.fixture()
def probe(browser):
    """每条测试一个干净页面 + 一套干净的收集器。"""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    p = PageProbe(page)
    p.ctx = ctx
    try:
        yield p
    finally:
        ctx.close()


@pytest.fixture()
def page(probe):
    """惯用名：绝大多数测试要的就是这个带收集器的 page。"""
    return probe.page
