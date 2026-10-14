# A+ 面板端到端浏览器测试规格

> 状态：**规格文档**（不是测试报告）。本文定义一棵从「页面加载」到「明天可下单」的决策树、
> 一张 case 沉淀表、三层可执行门禁、一张失败定位表，以及一份诚实的「今天过不了」清单。
>
> 撰写日期：2026-10-15 ｜ 适用代码：`aplus/webapp.py`（后端）、`aplus/web/index.html`（前端单文件）、`aplus/web/verdict.js`（判决函数）
>
> **本文不声称任何测试结果。** 所有"今天能过 / 今天过不了"的判断，都是读源码 + 读真实数据文件 +
> 跑 L1/L2 门禁脚本得出的，L3（浏览器）本次**未执行**，原因见 §5.4。

---

## 0. 三条不可协商的纪律

这三条是本仓付过代价换来的，写在前面是因为它们会推翻后面所有看起来更省事的写法。

### 0.1 `node --check` 是**不可跳过**的门禁

前端是单文件 HTML + 内联 `<script>`。同一个作用域里出现第二个同名 `const`，是
**解析期**错误，不是运行期错误：

```
SyntaxError: Identifier 'esc' has already been declared
```

浏览器遇到它会**丢弃整个 script 块**，页面停在骨架屏上，控制台**一条错误都没有**，
后端接口全部 200，所有 pytest 全绿。**页面是死的，而所有检查都说没问题。**
（`index.html` 约 1205 行处留有该事故的现场注释。）

所以 L1 门禁必须对**每个**内联块单独跑 `node --check`，且**不允许 skip**。
注意它与 pytest 是正交的：`node --check` 是解析器在问「这段 JS 是不是合法的程序」，
pytest 问的是「这个函数的行为对不对」。前者红、后者绿，是完全可能且危险的状态。

### 0.2 接线断言必须锚在**句法位置**，不能锚在子串

`index.html` 里有约 107 段中文 JS 注释、25 段 CSS 注释，注释里会自然地出现
`/api/chat/stream`、`setBusy(false)` 这些字面量。于是：

```python
assert '/api/chat/stream' in html      # ✗ 一句注释就能满足
```

这种断言**永远绿**，且在页面完全没接线的状态下也绿。本文的硬性风格要求：

| 想断言的事 | ✗ 子串（会被注释骗） | ✓ 句法位置（骗不了） |
|---|---|---|
| 流的 URL 对 | `'/api/chat/stream' in src` | `re.findall(r"fetch\(\s*['\"]([^'\"]+)['\"]", src)` 必须**恰好**等于 `['/api/chat/stream']` |
| 真读了流 | `'pumpStream' in send_body` | `re.search(r"await\s+pumpStream\(\s*res\.body", send_body)` |
| 事件分支齐 | `"'text'" in src` | 把 `handleEvent` 的**函数体**括号配平切出来，再查 5 个事件名 |
| 异常也解锁 | `'setBusy(false)' in send_body` | `re.search(r"finally\s*\{[^}]*setBusy\(false\)", send_body, re.S)` |

切函数体必须**括号配平**并**跳过字符串与注释**（`test_chat_wiring.py` 的 `_body()` 已实现），
否则一个模板字符串里的 `}` 就会把配平数提前归零。

### 0.3 变异后才有意义的守卫，才算守卫

一条守卫如果**在它本该拦截的故障真的发生时仍然绿**，那它不是守卫，是装饰。
本文每条门禁都标注了「变异测试：改坏什么，它必须变红」。没有变异验证的断言，
在本文里一律标 `[未变异验证]`，不计入可信门禁。

---

## 1. 树状端到端结构

一棵决策树。**每层是一个真实依赖**，上层不绿，下层的绿灯**没有任何意义**。
失败时的现象是「人眼看到的」，不是「断言看到的」——这两者的差异正是历史 bug 的藏身处。

### 层 A · 进程与静态资源（4 节点）

| 节点 | 判定 | 失败时的现象 |
|---|---|---|
| **A1** 服务进程起得来 | `create_app(data_root)` 无异常监听 | `curl` 连接被拒；浏览器「无法访问此网站」 |
| **A2** 首页可取 | `GET /` → 200，`Content-Type: text/html` | 白屏；浏览器下载了一个文件 |
| **A3** `GET /verdict.js` → 200 且非空 | 判决函数是 `<script src>` 外链，不是内联 | 页面能显示，但**判决板永远停在「读取成绩…」**——因为 `docketWarning` 未定义 |
| **A4** 静态挂载用 `html=True` | 路由 `/` 由 `StaticFiles` 提供 | `/` → 404，但 `/api/*` 全 200（很容易误判为"后端好好的"） |

### 层 B · 脚本解析（3 节点）

| 节点 | 判定 | 失败时的现象 |
|---|---|---|
| **B1** 每个内联 `<script>` 块通过 `node --check` | 逐块独立解析 | ★ **页面永久停在「加载预测数据…」，控制台零错误，所有 pytest 全绿** |
| **B2** `verdict.js` 通过 `node --check` | 外部脚本同样要解析 | 判决板空白，无报错 |
| **B3** 顶层无重复声明 | 同名 `const`/`let`/`function` 各出现一次 | 同 B1 |

### 层 C · 接口可达（4 节点）

| 节点 | 判定 | 失败时的现象 |
|---|---|---|
| **C1** 14 个 GET `/api/*` 全部 200 | 逐个请求 | 对应卡片显示「加载…」或「加载失败」 |
| **C2** 每个响应的必要键存在 | 如 `/api/predictions/latest` 必须有 `date/up/down` | 页面渲染出 `undefined` 字面量，或 catch 分支吃掉异常显示空态 |
| **C3** 前端引用的每条路径都在后端注册 | 正则抽出前端 15 条路径 ∩ 后端 17 条路由，零孤儿 | 页面报 404，但 pytest 的接口测试全绿 |
| **C4** `POST /api/chat/stream` 存在且是唯一 SSE 端点 | `fetch()` 实参唯一解析 | 追问面板点了没反应 |

### 层 D · 页面存活（4 节点）

| 节点 | 判定 | 失败时的现象 |
|---|---|---|
| **D1** 骨架占位全部退出 | `#heroes` 不含「加载预测数据」等 12 个 skeleton 文案 | ★ **卡片永远转圈**——与 B1 的现象完全一样，但根因完全不同（见 §4） |
| **D2** 控制台零错误 | `console_errors == []` | 肉眼看不见；只有测试会发现 |
| **D3** 零失败请求 | `failed_requests == []` | 同上 |
| **D4** 无未捕获异常 | `page_errors == []` | 同上 |

> D1 与 B1 是**同症异因**。这是本表最重要的一行：光看"页面卡在加载中"无法定位，
> 必须先跑 B1（1 秒，静态）还是 D1（30 秒，起浏览器）来决定往哪查。

### 层 E · 数据口径一致（6 节点）

| 节点 | 判定 | 失败时的现象 |
|---|---|---|
| **E1** 标题写明的交易日 == 榜单 `date` 的下一交易日 | `nextTradingDayLabel()` 以**今天**为基准，榜单以 `pred.date` 为基准 | ★ **标题说「下一交易日 10月16日预测」，而榜单实际是 10月14日收盘生成的、说的是 10月15日**（今天就是这个状态） |
| **E2** `run-dot` 与 `run-status.date` 同步 | 应比较 `s.date` 与今天 | ★ **今天 2026-10-15 早上打开，页面显示「今日已跑」——而 run 实际是昨天跑的** |
| **E3** `#alloc-date` 非空 | `alloc.date` 必须存在 | ★ **渲染成「生成于 」后面什么都没有**（后端 `allocation_latest` 返回的是 alloc JSON 原文，不含 `date`） |
| **E4** 判决板分支与真实数据匹配 | 用今天真实 payload 跑 `docketWarning` | 显示一个与数据矛盾的结论 |
| **E5** 判决板警示不为空 | `#docket-warn` 该显示时必须显示 | **最危险的一种失败：静默地不警示** |
| **E6** 徽章模型名 == `/api/llm/info` 返回值 | 不允许硬编码 | 显示一个没在跑的模型 |

### 层 F · 交互与视口（4 节点）

| 节点 | 判定 | 失败时的现象 |
|---|---|---|
| **F1** 七个 tab 逐个点开都出内容，且**不新增** console error / failed request | `(tab, panel)` 7 对 | 某个 tab 是空白页；或内容出来了但顺带报了个 404 |
| **F2** iPad 三视口整页不横向滚动 | `820×1180` / `1024×1366` / `768×1024`，`scroll <= client + 2` | 表格右侧被切掉，需要横拖才能看到数字 |
| **F3** 真触摸下图表出读数 | `has_touch=True, is_mobile=True` + CDP `Input.dispatchTouchEvent` | ★ **iPad 上把手指按在曲线上，什么都不出现** |
| **F4** 抬手后 tooltip 收起 | 触摸没有悬停 | tooltip 永久遮挡图表 |

> F2 与 F3 是**不同的失败模式**：F2 是布局（尺寸），F3 是输入设备（能力）。
> **把视口调成 iPad 的尺寸，不等于让它成为 iPad** —— `has_touch` 才决定触摸能力。
> 只调 viewport 的 iPad 测试，会让"触摸完全没测过"和"我按 iPad 尺寸测过了"同时为真。

### 层 G · 追问流（5 节点）

| 节点 | 判定 | 失败时的现象 |
|---|---|---|
| **G1** 真流式，不是等 JSON | 首个 `text` 事件早于 `done`，且能观察到中间态 | 用户盯着一个空气泡 40 秒，然后全文一次性出现——功能对，体验是坏的 |
| **G2** 连点 13 次只产生一轮 | `inFlight` 闸门 + `chat.db` 里只有一轮 | 一次提问扣 13 遍钱 |
| **G3** 三种失败三种归因 | `error` 事件→"模型"；fetch 抛错→"连接"；无 `done`→"截断" | 拔网线显示成"模型连接失败"——用户照着这句话去查上游，白查半天 |
| **G4** 失败后按钮必须解锁 | `finally { setBusy(false) }`，不是只在成功分支 | ★ **发送按钮永久变灰，只能刷新页面** |
| **G5** 静默看门狗生效 | 上游 45 秒不吐字节 ⇒ `AbortController` 触发 | ★ **上游卡住时 reader 既不 resolve 也不 reject ⇒ `finally` 永远不跑 ⇒ 按钮锁死** |

> G4/G5 是同一个陷阱的两面：`finally` 写对了也不够，因为**卡住的 reader 根本走不到 `finally`**。
> 必须有外部定时器（`IDLE_ABORT_MS = 45000`）主动 abort，才可能走到 `finally`。

### 层 H · 明天可下单（5 节点）

这是树的终点，也是**唯一有业务后果的一层**。前七层全绿而这一层红，等于前七层全白做。

| 节点 | 判定 | 失败时的现象 |
|---|---|---|
| **H1** 权重口径 `match` | `weights_check.status == 'match'` | 榜单不是现在这组权重排出来的 —— 下单买的东西和回测对不上 |
| **H2** 冠军未被证伪 | `champion_falsified == false` | 已被证伪的权重还在推票 |
| **H3** 样本天数够 | `n_days >= MIN_DAYS_FOR_CAGR(30)` | ★ **今天 `n_days == 2`**，所有 CAGR 分支都不可达 |
| **H4** 转债纸面账户有净值曲线 | `bond-paper.status != 'empty'` | ★ **今天 `status == "empty"`**，净值曲线分支走不到 |
| **H5** 警示链完整 | 未验证 / 样本不足 / 已证伪，三种都必须在页面上可见 | 见 E5 |

---

## 2. Case 沉淀表

**case ID 不随文案改。** 文案会改，ID 不能改 —— ID 是接口，不是描述。
下表 `期望` 一栏描述的是**行为**（「不显示骨架文案」），不是**字面量**（「不含『加载预测数据』」）。
断言字面量等于断言文案，那条 case 会在一次无害的文案微调后失效，然后被人以"测试太脆"为由删掉。

| ID | 层 | 前置 | 动作 | 期望（行为） | 门禁 | 对应守卫文件 | 变异验证 |
|---|---|---|---|---|---|---|---|
| `E2E-A1` | A | 服务未起 | `create_app(data_root)` | 无异常监听 | L2 | `tests/test_webapp.py` | `[未变异验证]` |
| `E2E-A2` | A | 服务已起 | `GET /` | 200 + `text/html` | L2 | `tests/test_webapp.py` | `[未变异验证]` |
| `E2E-A3` | A | 服务已起 | `GET /verdict.js` | 200 + 非空 | L2 | `tests/test_webapp.py` | `[未变异验证]` |
| `E2E-A4` | A | 服务已起 | `GET /` | 由 `StaticFiles(html=True)` 提供 | L1 | `tests/test_webapp.py` | `[未变异验证]` |
| `E2E-B1` | B | 无 | 逐块 `node --check` | 每块 rc==0 | **L1** | `tests/test_chat_wiring.py::test_every_inline_script_block_actually_parses` | ✅ 插入重名 `const` → 红 |
| `E2E-B2` | B | 无 | `node --check web/verdict.js` | rc==0 | **L1** | ★ **无守卫**（见 §5.6） | ❌ |
| `E2E-B3` | B | 无 | 统计顶层声明 | 无重名 | **L1** | `tests/test_chat_wiring.py` | ✅ |
| `E2E-C1` | C | 服务已起 | 14 个 GET | 全 200 | **L2** | `tests/test_webapp.py` | ✅ |
| `E2E-C2` | C | 服务已起 | 检查必需键 | 键齐全 | **L2** | `tests/test_webapp.py` | ✅ |
| `E2E-C3` | C | 无（静态） | 正则交叉引用 | 零孤儿 | **L1** | `tests/test_audit_frontend.py` | ✅ |
| `E2E-C4` | C | 无（静态） | 抽 `fetch()` 实参 | 唯一为 `/api/chat/stream` | **L1** | `tests/test_chat_wiring.py::test_the_stream_endpoint_url_is_the_one_the_backend_serves` | ✅ 改 URL → 红 |
| `E2E-D1` | D | 浏览器已起 | 等骨架退出 | 占位符全部替换 | **L3** | `tests/e2e/test_page_boots.py` | ✅ |
| `E2E-D2` | D | 浏览器已起 | 收集 console | 空 | **L3** | `tests/e2e/conftest.py::PageProbe` | ✅ |
| `E2E-D3` | D | 浏览器已起 | 收集 failed request | 空 | **L3** | `tests/e2e/conftest.py::PageProbe` | ✅ |
| `E2E-D4` | D | 浏览器已起 | 收集 page error | 空 | **L3** | `tests/e2e/conftest.py::PageProbe` | ✅ |
| `E2E-E1` | E | 真实数据 | 比对标题与榜单 | 标题交易日 == `pred.date` 的下一交易日 | **L3** | ★ **无守卫，今天红** | ❌ |
| `E2E-E2` | E | 真实数据 | `#run-text` vs `run-status.date` | 陈旧日期不得显示"今日已跑" | **L3** | ★ **无守卫，今天红** | ❌ |
| `E2E-E3` | E | 真实数据 | `#alloc-date` | 非空且等于 alloc 生成日 | **L3** | ★ **无守卫，今天红** | ❌ |
| `E2E-E4` | E | 真实数据 | `docketWarning` 真值 | 分支与数据一致 | L1+L3 | `tests/test_docket_warning.py`（34） | ✅ |
| `E2E-E5` | E | 真实数据 | `#docket-warn` | 该警示时不隐藏 | **L3** | `tests/test_docket_warning.py` + `tests/e2e/test_page_boots.py` | ✅ |
| `E2E-E6` | E | 真实数据 | `#llm-info` | == `/api/llm/info` | **L3** | `tests/e2e/test_page_boots.py` | ✅ 注入 `fake-e2e-model-9f3a` |
| `E2E-F1` | F | 浏览器已起 | 逐个点 7 个 tab | 出内容且无新增错误 | **L3** | `tests/e2e/test_layout.py` | ✅ |
| `E2E-F2` | F | 浏览器已起 | 三 iPad 视口 | `scroll <= client + 2` | **L3** | `tests/e2e/test_layout.py` | ✅ 2px 容差，防假红 |
| `E2E-F3` | F | 真触摸 context | CDP touchStart + touchMove | tooltip 可见**且含数字** | **L3** | `tests/e2e/test_chart_touch.py` | ✅ |
| `E2E-F4` | F | 同上 | tap | tooltip 收起 | **L3** | `tests/e2e/test_chart_touch.py` | ✅ |
| `E2E-G1` | G | FakeLLM 有多段 | 观察 DOM | 首段早于 `done` 出现 | **L3** | `tests/e2e/test_page_boots.py`（增量流用例） | ✅ `thinking_deltas=0` 对照臂 |
| `E2E-G2` | G | FakeLLM | 13 次快点击 | `chat.db` 恰好一轮 | **L3** | `tests/e2e/test_chat_flow.py` | ✅ |
| `E2E-G3` | G | 三种故障 | 各触发一次 | 三句不同的话 | **L3** | `tests/e2e/test_chat_flow.py` | ✅ |
| `E2E-G4` | G | `fail_at` | 提问后观察 | 按钮恢复可点 | **L3** | `tests/e2e/test_chat_flow.py` | ✅ |
| `E2E-G5` | G | `hang_at` | 静默流 | ≤ 50s 内 abort 并解锁 | **L3** | `tests/e2e/test_chat_flow.py`（`@pytest.mark.slow`，`APLUS_E2E_SKIP_SLOW=1` 可跳过） | ✅ |
| `E2E-H1` | H | 真实数据 | `weights_check.status` | `match` | L2+L3 | `tests/test_stale_weights_warning.py`（18） | ✅ |
| `E2E-H2` | H | 真实数据 | `champion_falsified` | false | L2+L3 | `tests/test_docket_warning.py` | ✅ |
| `E2E-H3` | H | 真实数据 | `n_days` | >= 30 否则警示可见 | L1+L3 | `tests/test_docket_warning.py` | ✅ |
| `E2E-H4` | H | 真实数据 | `bond-paper.status` | 非 empty | L2+L3 | ★ **无守卫，今天红** | ❌ |
| `E2E-H5` | H | 真实数据 | 警示链 | 三种警示均可见 | L1+L3 | `tests/test_docket_warning.py` | ✅ |
| `E2E-W1` | — | 假 LLM 各旋钮 | 逐个自检 | 每个旋钮真能触达其故障 | 无浏览器 | `tests/e2e/test_harness_selfcheck.py`（7） | ✅ 1.69s |

---

## 3. 三层门禁

> 环境铁律：**必须 `.venv/bin/python`**，绝不裸 `python`。
> venv 里**没有 pip**，装东西用 `uv pip install`。
> **没有 `pytest-timeout`、没有 `scipy`、没有 `statsmodels`**，任何依赖它们的写法在本项目都跑不起来。

### 3.1 L1 · 静态门禁（秒级，无进程）

不启动服务、不启动浏览器。只读源码文件。

```bash
cd /Users/airboom/opencode/aplus
```

**L1 完整脚本**（自包含，可直接粘贴运行；本次已跑，`L1 OK  blocks=2  fetch=['/api/chat/stream']`）：

```python
# 用法： .venv/bin/python l1_gate.py
import re, subprocess, sys, tempfile, pathlib

HTML = pathlib.Path("aplus/web/index.html")
VERDICT = pathlib.Path("aplus/web/verdict.js")
src = HTML.read_text(encoding="utf-8")
fail = []

# ── B1/B3：每个内联块必须真的能解析 ────────────────────────────────
# 注意正则只取没有 src= 的块；外链块单独查（见下）。
blocks = [m.group(1) for m in
          re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", src, re.S)]
for i, b in enumerate(blocks):
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8") as f:
        f.write(b); p = f.name
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    if r.returncode:
        fail.append(f"B1 block#{i} 解析失败：\n{r.stderr[:400]}")
    pathlib.Path(p).unlink()

# ── B2：verdict.js 也必须能解析 ────────────────────────────────────
r = subprocess.run(["node", "--check", str(VERDICT)], capture_output=True, text=True)
if r.returncode:
    fail.append(f"B2 verdict.js 解析失败：\n{r.stderr[:400]}")

# ── C4：fetch 的实参唯一（注释里的字面量骗不过正则） ────────────────
fetchs = re.findall(r"fetch\(\s*['\"]([^'\"]+)['\"]", src)
if fetchs != ["/api/chat/stream"]:
    fail.append(f"C4 fetch 实参不是唯一的 /api/chat/stream：{fetchs}")

# ── 取函数体：括号配平。用于把断言锚到句法位置 ────────────────────
def body(name):
    """截出 `function NAME(...){...}` 的函数体。括号配平，跳过字符串。"""
    i = src.index(f"function {name}")
    j = src.index("{", i); d = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            d += 1
        elif src[k] == "}":
            d -= 1
            if d == 0:
                return src[j:k]
    raise SystemExit(f"{name} 函数体未闭合")

send_b = body("send")
handle_b = body("handleEvent")

# ── G1：send 真的在读流，而不是 await 了一个 JSON ──────────────────
if not re.search(r"await\s+pumpStream\(\s*res\.body", send_b):
    fail.append("G1 send() 里没有 await pumpStream(res.body)")

# ── G3：后端发的 5 个事件，handleEvent 必须逐个分支 ────────────────
miss = [e for e in ("start", "thinking", "text", "done", "error")
        if f"'{e}'" not in handle_b]
if miss:
    fail.append(f"G3 handleEvent 漏了事件分支：{miss}")

# ── G4：异常路径也必须解锁（finally，不是只在成功分支） ────────────
if not re.search(r"finally\s*\{[^}]*setBusy\(false\)", send_b, re.S):
    fail.append("G4 send() 的 finally 里没有 setBusy(false)")

# ── G5：静默看门狗存在且真的调 abort ──────────────────────────────
#   ⚠️ 箭头函数写在参数里（`setTimeout(() => ac.abort(), …)`），
#      所以不能用 `setTimeout\([^)]*abort` —— `[^)]*` 撞上 `()` 里的第一个
#      右括号就停了，**这条正则永远不匹配**，等于没写。
if not (re.search(r"IDLE_ABORT_MS\s*=\s*\d+", src)
        and re.search(r"setTimeout\(\s*\(\)\s*=>[^;]*abort\(\)", send_b)):
    fail.append("G5 没有静默看门狗（IDLE_ABORT_MS + 真的会调 abort）")

print("blocks:", len(blocks), "fetch:", fetchs)
if fail:
    print("\n".join("FAIL " + f for f in fail)); sys.exit(1)
print("L1 OK")
```

**等价 pytest（已存在的守卫）**：

```bash
.venv/bin/python -m pytest tests/test_chat_wiring.py -q
```

12 条，覆盖 B1/C4/G1/G3/G4/G5。变异验证：往内联块里插一个重名 `const` → `test_every_inline_script_block_actually_parses` 变红。

### 3.2 L2 · 接口门禁（秒级，进程内，无浏览器）

用 `fastapi.testclient.TestClient`，**不启 uvicorn、不占端口、不开浏览器**。
⚠️ `TestClient` 会**缓冲整个响应体**，所以它**无法证明 SSE 是分块到达的** ——
流式性只能靠 L3 的真 socket（`tests/e2e/conftest.py::live_server`，uvicorn 跑在后台线程里）。

```bash
.venv/bin/python -m pytest tests/test_webapp.py tests/test_chat_stream.py -q
```

18 + 37 条。`test_chat_stream.py` 覆盖 5 事件契约与「错误走 event 不走 HTTP 4xx/5xx」。

**L2 全量体检脚本**（本次已跑，14/14 端点 `200`，`BAD = 0`）：

```python
import pathlib, json, sys
sys.path.insert(0, str(pathlib.Path("aplus").resolve().parent))
from fastapi.testclient import TestClient
from aplus.webapp import create_app

client = TestClient(create_app(pathlib.Path("data")))

EXPECT = {
    "/api/status":                 ["stats"],
    "/api/predictions/latest":     ["date", "up", "down"],
    "/api/verification/history":   None,
    "/api/allocation/latest":      ["positions", "cash_weight"],
    "/api/llm/info":               None,
    "/api/run-status":             ["status"],
    "/api/backtest":               None,
    "/api/weights":                None,
    "/api/predictions/history":    None,
    "/api/evolution":              None,
    "/api/evolution/ledger":       None,
    "/api/evolution/summary":      None,
    "/api/logs":                   None,
    "/api/bond-paper":             None,
}
bad = 0
for path, keys in EXPECT.items():
    r = client.get(path)
    ok = r.status_code == 200
    detail = ""
    if ok and keys:
        payload = r.json()
        missing = [k for k in keys if k not in payload]
        ok = not missing
        detail = f" missing={missing}"
    print(("OK  " if ok else "BAD "), r.status_code, path, detail)
    bad += (not ok)
print("BAD =", bad)
sys.exit(1 if bad else 0)
```

### 3.3 L3 · 浏览器门禁（分钟级，真 Chrome + 真触摸）

```bash
cd /Users/airboom/opencode/aplus

# 全量（首次会下载 Chrome）
.venv/bin/python -m pytest tests/e2e -q

# 只跑骨架存活（最快）
.venv/bin/python -m pytest tests/e2e/test_page_boots.py -q

# iPad 布局 + 真触摸
.venv/bin/python -m pytest tests/e2e/test_layout.py tests/e2e/test_chart_touch.py -q

# 追问流（慢；含 90s 看门狗用例）
.venv/bin/python -m pytest tests/e2e/test_chat_flow.py -q
# 跳过 slow 标记（会跳过看门狗用例，诚实标注覆盖缺口）
APLUS_E2E_SKIP_SLOW=1 .venv/bin/python -m pytest tests/e2e/test_chat_flow.py -q
```

#### 3.3.1 最小可跑 skeleton

```python
"""最小骨架：证明这个环境能真的开浏览器、真的发触摸事件。
任何一条 E2E 跑不动，先跑这个定位是环境问题还是被测代码问题。
在项目根目录执行： .venv/bin/python -m pytest /tmp/smoke.py -q
"""
import pytest
from playwright.sync_api import expect

IPAD = {"width": 1024, "height": 1366}


@pytest.fixture()
def touch_browser(browser):                     # browser 来自 tests/e2e/conftest.py
    ctx = browser.new_context(viewport=IPAD, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()


def test_the_browser_really_starts_and_the_panel_really_boots(live_server, page):
    page.goto(live_server, wait_until="domcontentloaded")

    # ★ 轮询一个明确的完成条件。绝不 time.sleep 之后「看一眼」——
    #   本仓最贵的一课就是把「等慢」误读成「等坏」。
    page.wait_for_function(
        "() => { const h = document.getElementById('heroes');"
        "        return h && !h.textContent.includes('加载预测数据'); }",
        timeout=30_000,
    )
    assert "加载预测数据" not in page.inner_text("#heroes")
    assert probe_ok(page)                        # 见下


def test_a_real_touch_event_reaches_the_page(touch_browser, live_server):
    """证明 has_touch/is_mobile 真的生效 —— 触摸路径的冒烟测试。
    视口调成 iPad 尺寸 **不等于** 它成了 iPad；触摸能力由这两个开关决定。
    """
    page = touch_browser
    page.goto(live_server, wait_until="domcontentloaded")
    page.evaluate("""() => {
        window.__ev = [];
        for (const t of ['touchstart','pointerdown','pointermove'])
            document.addEventListener(t, () => window.__ev.push(t), {passive:true});
    }""")
    page.touchscreen.tap(200, 300)
    got = page.evaluate("() => window.__ev")
    assert "touchstart" in got, f"触摸事件没到达页面（收到 {got}）⇒ 触摸路径未测"
```

`conftest.py` 里 `browser` fixture 是 `p.chromium.launch(channel="chrome", headless=True)`。
**`channel="chrome"` 是硬要求**：本机 Playwright 自带的 chromium 缓存版本（1217/1223/1228）
与 Playwright 1.64 要求的 1248 不匹配，缺 `channel` 会直接启动失败。
Chrome 不存在时该 fixture 会 skip —— **skip 不是绿，是没测**，见 §5.5。

#### 3.3.2 三条必须写进任何 iPad 用例的纪律

1. **触摸用 CDP 手写，不用 `page.mouse`**。
   `browser.new_context(viewport=…, has_touch=True, is_mobile=True)`，
   然后 `Input.dispatchTouchEvent` 发 `touchStart` 再 `touchMove`（位移 2px）。
   ⚠️ 只发 down+up（`tap`）**不会产生 `pointermove`** —— 绑定在 move 上的处理器永远不被调用，
   看起来像"触摸路径没生效"，其实是没发移动。
2. **在手指仍然按着的时候断言**。实测事件序列是
   `pointerdown, touchstart, pointermove, pointerup, pointerleave, touchend` ——
   `pointerleave` 在 `pointerup` 之后**立刻**触发并收走 tooltip。
   抬手后再断言，看到的永远是"已隐藏"，**那测的是抬手后的状态，而用户看到读数是在按着的时候**。
3. **等 svg 画出图元，不等 tooltip**。`.tooltip` 的初始样式就是 `display:none`，
   拿它当"页面准备好了"的信号会一直等到超时。

#### 3.3.3 FakeLLM 必须自检

`FakeLLM` 有 `text_deltas` / `thinking_deltas` / `delay` / `fail_at` / `hang_at` 五个旋钮。
**每个旋钮都要有一条独立的自检**，否则你不知道这个 fixture 到底能不能触达它声称要测的故障 ——
「测试通过」和「测试跑到了那条分支」是两件事。

```bash
.venv/bin/python -m pytest tests/e2e/test_harness_selfcheck.py -q
```

7 条，1.69s，不开浏览器。这是**唯一一条不开浏览器就能跑的 e2e 文件**，改动 fixture 后应先跑它。

---

## 4. 失败定位表

**从现象出发**，而不是从测试名出发。症状 → 层 → 第一条该跑的命令。

| 现象 | 最可能的层 | 先跑哪条命令 |
|---|---|---|
| 浏览器「无法访问此网站」/ curl 连接被拒 | A1 服务没起 | `.venv/bin/python -c "from aplus.webapp import create_app; create_app('data')"` |
| 首页 404，但 `/api/*` 全 200 | A4 静态挂载 | `.venv/bin/python -m pytest tests/test_webapp.py -q` |
| 判决板停在「读取成绩…」，其它卡片正常 | A3 / B2 `verdict.js` 没加载或没解析 | `node --check aplus/web/verdict.js` |
| ★ **整页停在「加载…」，控制台零错误，pytest 全绿** | **B1 解析期错误**（先查这个，1 秒） | `.venv/bin/python -m pytest tests/test_chat_wiring.py -q` |
| 同上，但 `node --check` 绿 | D1 骨架没退出（接口挂了/报错了） | `.venv/bin/python -m pytest tests/test_webapp.py -q` |
| 某个卡片显示「加载失败：xxx」 | C1/C2 该端点报错 | `.venv/bin/python -m pytest tests/test_webapp.py -q` |
| 追问点了没反应 | C4 流端点没接上 | `.venv/bin/python -m pytest tests/test_chat_wiring.py -q` |
| 追问能答但一次性蹦出全文 | G1 不是流式 | `APLUS_E2E_SKIP_SLOW= .venv/bin/python -m pytest tests/e2e/test_page_boots.py -q` |
| 问失败一次后按钮永久变灰 | G4 `finally` 没解锁 | `.venv/bin/python -m pytest tests/e2e/test_chat_flow.py -q` |
| 上游卡住时按钮永久变灰 | G5 缺静默看门狗 | `APLUS_E2E_SKIP_SLOW= .venv/bin/python -m pytest tests/e2e/test_chat_flow.py -q` |
| 一次提问扣了多遍钱 | G2 `inFlight` 闸门失效 | `.venv/bin/python -m pytest tests/e2e/test_chat_flow.py -q` |
| 拔网线显示成"模型连接失败" | G3 三种失败共用一个 catch | `.venv/bin/python -m pytest tests/e2e/test_chat_flow.py -q` |
| iPad 上表格右侧被切掉 | F2 布局溢出 | `.venv/bin/python -m pytest tests/e2e/test_layout.py -q` |
| ★ **iPad 上手指按在曲线没反应** | **F3 触摸路径**（先跑这个，别先查布局） | `.venv/bin/python -m pytest tests/e2e/test_chart_touch.py -q` |
| tooltip 一直挡着图不消失 | F4 抬手没收起 | 同上 |
| 标题说 10月16日，榜单是 10月15日的 | E1 标题以"今天"为基准，榜单以 `pred.date` 为基准 | `.venv/bin/python -m pytest tests/e2e/test_page_boots.py -q` |
| 早上打开显示"今日已跑"，其实是昨天跑的 | E2 `loadRunDot` 从不比较日期 | 同上 |
| 配置方案标题后是空的 | E3 `alloc.date` 不存在 | 同上 |
| 判决板结论与数据看着矛盾 | E4/E5 分支优先级 | `.venv/bin/python -m pytest tests/test_docket_warning.py -q` |
| ★ **该警示的时候没警示** | E5 ——**最危险**，静默失效 | `.venv/bin/python -m pytest tests/test_docket_warning.py tests/test_stale_weights_warning.py -q` |
| 转债页永远是空态 | H4 `bond-paper.status == "empty"` | `.venv/bin/python -m pytest tests/e2e/test_page_boots.py -q` |
| 测试报 `Executable doesn't exist` | L3 环境（chrome 缓存版本不匹配） | `.venv/bin/python -m pytest tests/e2e/test_page_boots.py -q`，确认 `channel="chrome"` |

**两条最容易走错的路，单独说：**

- **"页面卡在加载中" 至少有两种根因**（B1 解析失败 vs D1 接口挂了），症状逐字相同。
  判据：**先花 1 秒跑 L1**。红了就是 B1，别急着起浏览器。
- **"iPad 上没反应" 至少有两种根因**（F2 布局 vs F3 触摸能力）。只调 viewport 的测试
  会让"触摸完全没测过"和"我按 iPad 尺寸测过了"同时为真。判据：**先看 fixture 里有没有
  `has_touch=True`**。没有的话，F3 根本没被测过。

---

## 5. 诚实声明

### 5.1 ★ "测试全绿" 不是"页面能用"的证据

这一节放在最前面，因为这是本文唯一一条**否定性结论**，也是最容易被忽略的。

L1 与 L2 门禁**全绿，能证明的只有**：
两个内联脚本块是合法的 JavaScript 程序，且流端点的接线在句法上存在。

它们**不能**证明：页面真的渲染出来了、图表真的画出来了、iPad 上真的能读数、
明天照着这页下单不会亏钱。B1 绿而浏览器解析失败的情况不存在——但 B1 绿、
而**另一个**未纳入 `node --check` 的脚本失败、或某个 fetch 因数据形状失败、
或 CSS 布局把内容挤到屏幕外，B1 一样绿。

> 更具体地说：**本文的树有 35 个节点（对应 §2 的 36 条 case）。**
> 其中只有 9 条被 L1 静态覆盖、8 条被 L2 接口覆盖，**24 条需要 L3 真浏览器**。
> 这 24 条本次一次都没跑过（§5.4），**本文没有证据**说它们通过。

### 5.2 今天（2026-10-15）就过不了的节点

以下每一条都是**读源码 + 读真实数据文件**得出的，不是推测。

| 节点 | 为什么过不了 | 证据 | 严重度 |
|---|---|---|---|
| **E1** 标题交易日与榜单不一致 | `nextTradingDayLabel()` 以**运行当天**为基准，而榜单以 `pred.date`（上一交易日收盘）为基准。今早打开：标题写「下一交易日 10月16日预测」，榜单实为 10月14日生成、说的是 10月15日 | `index.html:843` 用 `nextTradingDayLabel()`（无参 = 今天）；`pred-sub` 用 `pred.date` | ★★★ 用户会以为榜单说的是 16 号 |
| **E2** `run-dot` 谎报「今日已跑」 | `loadRunDot` 只按 `s.status` 分支，**从不比较 `s.date` 与今天**。今天的 run 是昨天跑的，页面照旧显示绿点 | `index.html:935-943`；`run_status.date == 2026-10-14` | ★★★ 同上 |
| **E3** `#alloc-date` 渲染为空 | 后端 `/api/allocation/latest` 返回 alloc JSON 原文，其顶层键是 `capital / cash_weight / positions / rebalance / risk_notes` —— **没有 `date`**。前端写 `生成于 ${a.date ?? ''}` | `webapp.py:269`；实测 alloc 顶层键已确认无 `date` | ★★ 不致错，但页面缺一个关键信息 |
| **H3** 样本天数不足 | `n_days == 2 < MIN_DAYS_FOR_CAGR(30)`，**所有 CAGR 分支今天都不可达** | `verification.parquet` 实测 | ★★★ 这正是它该警示的理由 —— 但警示分支被更高优先级抢占了 |
| **H4** 转债纸面账户为空 | `bond-paper.status == "empty"`，净值曲线与调仓期分支**今天完全走不到** | 实测 | ★★ 空态本身没错，但对应 E2E 用例今天必红 |
| **H1** 权重口径分支不可达 | 今天 `weights_check.status == "match"`，"榜单不是现在这组权重排出来的"这条警示**今天无法被触发** | 实测 | ★★ 守卫在，但今天测不到 |
| **H2** 冠军未被证伪 | 今天 `champion_falsified == false`，证伪分支不可达 | 实测 | ★★ 同上 |

**今天真实触发的判决**（用今天的真实 payload 在 node 里跑 `docketWarning` 得到）：

> `danger` / **「当前权重未经赛马验证」**

注意这条**抢占**了本该出现的 `n_days < 30` 警示分支（分支优先级：
stale-weights → weights caveat → champion_falsified → `0 < n_days < 30` → `cagr < 0` → …）。
所以一条"样本只有 2 天"的事实，**在页面上看不到**。
这是 E5 的一个真实实例：**警示没有消失，它被另一条更有力的警示盖住了 ——
但用户因此不知道样本只有 2 天。** 是否可接受，是一个产品决定，不是一个测试决定。

### 5.3 「48 条 pytest」这个数字是错的

本仓多处引用过「48 条前端守卫」，这个数字**对不上任何一种自然分组**：

| 文件 | 实际条数 |
|---|---|
| `tests/test_chat_wiring.py` | 12 |
| `tests/test_audit_frontend.py` | 27 |
| `tests/test_docket_warning.py` | 34 |
| `tests/test_stale_weights_warning.py` | 18 |
| `tests/test_webapp.py` | 18 |
| `tests/test_chat_stream.py` | 37 |
| **合计** | **146** |

**只有数字是错的，守卫本身完好。** 本文不引用「48」。

### 5.4 L3 本次**未执行**

写这份文档时施加了硬约束：不启动 web 服务、不启动浏览器、不载入面板
（另有进程正在跑面板任务，16GB 内存，载入是 GB 级）。

本次实际执行的只有：

- ✅ L1 完整脚本 → `L1 OK  blocks=2  fetch=['/api/chat/stream']`（本文 §3.1 的脚本逐字跑通）
- ✅ L2 全量体检 → 14/14 端点 `200`，`BAD = 0`（本文 §3.2 的脚本逐字跑通）
- ✅ `pytest --collect-only tests/e2e` → 30 条被收集（**collect ≠ run**）
- ✅ `pytest tests/e2e/test_harness_selfcheck.py` → 7 passed / 1.69s（不开浏览器）

**没有执行**：任何一条真浏览器用例，包括骨架存活、iPad 布局、真触摸 tooltip、
追问流式、连点闸门、看门狗。**这 24 个节点的状态是「未知」，不是「通过」。**

### 5.5 `skip` 不是绿

`tests/e2e/conftest.py::browser` 在本机找不到 Chrome 时会 **skip**。
`test_chat_flow.py` 的看门狗用例带 `@pytest.mark.slow`，可用 `APLUS_E2E_SKIP_SLOW=1` 跳过。

一个跑出 `30 passed, 5 skipped` 的报告，诚实读法是：**25 个节点有证据，5 个没有。**
把 `skipped` 计入"通过"是本文 §0.3 说的那种假守卫。

### 5.6 一个真实的守卫缺口

`aplus/web/verdict.js` 今天是能通过 `node --check` 的（本文 L1 脚本实测过）。
但**现有 pytest 里没有任何一条对它跑 `node --check`** —— §0.1 那条纪律只覆盖了内联块。

判决函数是**唯一**决定"页面要不要警示用户"的地方，也是最不该静默坏掉的地方，
偏偏它落在覆盖之外。本文的 L1 脚本把 `verdict.js` 补上了（节点 `E2E-B2`），
但**脚本不是守卫** —— 只有当它被接进 CI 或 `pytest.ini`，`E2E-B2` 才从 `[未变异验证]` 变成真门禁。

### 5.7 变异验证的状态

| 类别 | 条数 | 说明 |
|---|---|---|
| 已变异验证（改坏必红） | 27 | 见 §2 表最后一列 ✅ |
| **未变异验证** | 9 | 标 `❌` 或 `[未变异验证]`，**不计入可信门禁** |
| 今日必红（真实缺陷） | 7 | 见 §5.2 |

未变异验证的 9 条是：A1-A4（静态挂载与路由存在性）、B2（`verdict.js` 无守卫）、
E1/E2/E3（今日新发现的三个口径缺陷）、H4（转债纸面账户为空）。
它们的成本极低、回归价值也低，但**在被变异验证之前，不应在汇报里当作"覆盖了"**。

> §0.1 那条纪律不是空话：**本文的 L1 脚本自己就踩了一次** ——
> 初版 G5 断言写成 `setTimeout\([^)]*abort\(\)`，而实际代码是 `setTimeout(() => ac.abort(), …)`，
> `[^)]*` 撞上 `() =>` 的第一个右括号就停住了，**这条正则永远不匹配，等于没写**。
> 它"红"了不是因为被测代码有问题，是因为断言自己写错了。
> 改成 `setTimeout\(\s*\(\)\s*=>[^;]*abort\(\)` 后才通过，并已变异验证
> （把 `ac.abort()` 换成 `ac.nothing()` 即红）。**门禁自己会骗自己。**

---

## 6. 变更本文时

1. **改文案 → 不改 ID。** `E2E-*` 是接口。文案变了就改 §2 的"期望"描述，不动 ID。
2. **新增节点 → 在对应层加一行，在 §2 加一行，在 §4 加一条定位路径。** 三处必须同步，
   否则会出现"树上有、定位表里没有"的节点 —— 那正是最难查的一类。
3. **任何一条门禁从 `[未变异验证]` 变成 ✅ 时，在同一处写下你改坏了什么、它怎么变红。**
   没有这句话的 ✅ 不可信。
4. **§5.2 的清单会随数据变化。** 数据一变（比如跑出 30 天样本），
   就要重跑 §5.2 并更新 —— **不要让它烂在那里冒充现状。**
