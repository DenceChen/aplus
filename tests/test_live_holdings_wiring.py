"""「当前持仓 + 下次调仓」这条链的守卫（2026-10-17 接上时写的）。

## 这条链为什么必须有守卫：它是**唯一一处口径落差直接面对用户**的地方

2026-10-17 实测：`/api/status` 原本只递 5 个键，`holdings.json`（实盘成交流的
唯一真值）与 `live_execution_status`（K 与缓冲区**是否真的被执行**的那道闸）
**全仓零个消费点**。数据到了 API 边界就断，而**页面照常渲染、HTTP 200、零告警**。

用户于是每天看到一张新的 10 涨 10 跌，却看不到「实盘每 21 个交易日换一次仓」。
照新名单全买年摩擦 **80.81%**，照 K=21 换一次是 **6.41%**（同产物
`delivery_caliber_friction`）⇒ **差 74.4pp/year，与信号好坏无关。**

⇒ 所以守卫问的不是「这个函数算得对吗」，是**「这条链还通着吗」**——
那正是读源码（接线层）才能问的，纯行为测试一条都问不出来。

## 三层，各钉各的，不可互换

| 层 | 问 | 变异能杀 |
|---|---|---|
| 行为（真起 app 打真端点） | 数对不对、坏状态有没有被当成空仓 | 改算法 |
| **接线（前端函数体）** | 前端真的调了它吗 | **删掉 `renderHeld(status.live)`** |
| 接线（后端 AST） | `/api/status` 真的递了 `live` 吗 | **删掉 `"live": ...` 那一行** |

## 为什么不许用子串（第 13 层）

`index.html` 有近 80 行中文注释，而「实盘」「调仓」「21 个交易日」「持有 0 只」
这些词**逐字出现在我自己刚写的注释里** ⇒ `assert X in src` 会被注释满足。

★ **本文件写第一版时当场踩了**：守卫断言「`renderHeld` 里不许出现『持有 0 只』」，
而 `renderHeld` 自己的第 1089 行注释逐字写着
「`state === null` 与『持有 0 只』是**两件事**」⇒ **作者对自己刚写的话没有免疫力。**

⇒ 修法：断言锚在**模板串**上（只有模板串会变成渲染出来的字），
不锚在整个函数体上。`_js_templates()` 与本文件其余提取器同形：
抽不出东西时**必须自己先报错**，否则所有基于它的断言退化成恒真（第 131 层）。

## 一个必须显式区分的字段矛盾（实测，不是推演）

`/api/status` 的 `live` 里同时有两个「换仓周期」：

| 字段 | 值 | 它是什么 |
|---|---|---|
| `live.every` | **21** | **`holdings.json` 的真值** —— 实盘确实在执行的 |
| `live.execution_status.rebalance_every` | **1** | **回落值** —— `weights.json` 不存在 ⇒ `live_execution_status` 走 `cfg.get("source") != "file"` 的早退分支，`every` 停在初值 1 |

⇒ 两个都叫「换仓周期」，**字面互相矛盾**，而 `honored=False` 的 `reason`
逐字说明它是回落。⇒ 前端渲染 `live.every`（真值），**且必须同时逐字显示 `reason`**，
否则用户只看到「21」而看不到「这个配置未经赛马验证」。

⚠️ 因此下面那条守卫叫 `…_every_fields_are_not_silently_equal`：
它不是说这两个数该相等，而是**说它们不相等这件事必须留在视野里** ——
若哪天有人拿回落值覆盖真值让两者相等，那条会红，且红得对。

## 不在本文件里的东西（各有归属，不复制）

- **JS 解析期错误**（同名 `const` 之类）⇒ `tests/test_chat_wiring.py`
  的 `test_every_inline_script_block_actually_parses` 已经用 `node --check` 盖住。
  复制一份只会让两条守卫各自漂移（第 137 层：两份必须同步的东西中间没有锁）。
- **后端数值算法本身** ⇒ `_live_holdings_payload` 是纯换算，行为层已覆盖。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aplus.live_rebalance import REQUIRED_KEYS
from aplus.webapp import _live_holdings_payload, create_app

_ROOT = Path(__file__).resolve().parent.parent
_WEBAPP = _ROOT / "aplus" / "webapp.py"
_HTML = _ROOT / "aplus" / "web" / "index.html"


# ───────────────────── 提取器（三个，各自带「抽不出就报错」的闸） ─────────────────────

def _html_text() -> str:
    return _HTML.read_text(encoding="utf-8")


def _js_function_body(src: str, name: str) -> str:
    """从 `function <name>(` 起手逐字符配对花括号，跳过字符串与注释。

    不用 `split("\\n}")` —— 它靠「换行后紧跟 `}`」切函数体，函数被重排
    或内部出现单 `}` 行时会**静默切错**，然后在错误片段上断言、绿灯继续。
    """
    start = src.find(f"function {name}(")
    assert start != -1, f"index.html 里找不到 function {name}("
    i = src.index("{", start)
    depth, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'`":                       # 跳过字符串 / 模板串
            q, i = ch, i + 1
            while i < n and src[i] != q:
                i += 2 if src[i] == "\\" else 1
        elif ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif ch == "/" and i + 1 < n and src[i + 1] == "*":
            i = src.index("*/", i) + 2
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError(f"function {name} 的花括号没配平")


def _js_templates(body: str) -> list[str]:
    """抽出函数体里的**模板串**（会变成渲染出来的字的那些）。

    为什么不能断言整个函数体：注释里的字会满足子串断言（第 13 层），
    而本文件自己的注释就写着「持有 0 只」。

    `${…}` 内部可以再出现模板串，所以要跟踪插值深度，不能一见到反引号就收。
    """
    out, i, n = [], 0, len(body)
    while i < n:
        if body[i] != "`":
            i += 1
            continue
        j, depth = i + 1, 0
        while j < n:
            c = body[j]
            if c == "\\":
                j += 2
                continue
            if depth == 0 and c == "`":
                break
            if body.startswith("${", j):
                depth += 1
                j += 2
                continue
            if depth and c == "}":
                depth -= 1
            elif depth and c == "`":            # 插值里的嵌套模板串：整段跳过
                k = j + 1
                while k < n and body[k] != "`":
                    k += 1
                j = k
            j += 1
        out.append(body[i + 1:j])
        i = j + 1
    assert out, "一个模板串都没抽到 —— 抽取器坏了，不是页面坏了（第 131 层）"
    return out


def _status_endpoint_keys() -> list[str]:
    """`/api/status` 返回体的键（AST，**不许子串**）。"""
    tree = ast.parse(_WEBAPP.read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name != "status":
            continue
        if not any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "get"
                   and d.args and getattr(d.args[0], "value", "") == "/api/status"
                   for d in fn.decorator_list):
            continue
        for stmt in fn.body:                   # 只看**本层**的语句，不 walk 进嵌套
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict):
                return [k.value for k in stmt.value.keys]
    raise AssertionError("webapp.py 里找不到 /api/status 的 handler 返回体")


# ───────────────────── 行为层：真起 app、真打端点 ─────────────────────

def _make_holdings(reports: Path, **over) -> None:
    """写一份 `load_state` 会**认**的 holdings.json。

    `REQUIRED_KEYS` 从 `aplus.live_rebalance` import 而不是抄一遍 ——
    抄的那份会随生产改而腐烂，而腐烂的夹具**照样让守卫全绿**（空/坏夹具
    只会让守卫恒真，不会让它变红）。
    """
    state = {
        "as_of": "2026-10-16",
        "days_since_rebalance": 1,
        "every": 21,
        "buffer_multiple": None,
        "up": ["600641.SH"],
        "down": ["000001.SZ"],
        "up_names": ["先导基电"],
        "down_names": ["平安银行"],
    }
    assert set(REQUIRED_KEYS) <= set(state), "夹具缺 load_state 要求的键"
    state.update(over)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "holdings.json").write_text(json.dumps(state, ensure_ascii=False),
                                          encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    _make_holdings(tmp_path / "reports")
    return tmp_path


def test_the_status_endpoint_carries_the_rebalance_cadence(root):
    """**K=21 必须原样出现在 `/api/status` 里** —— 这条链的承重字段。"""
    live = TestClient(create_app(root)).get("/api/status").json()["live"]
    assert live["every"] == 21
    assert live["as_of"] == "2026-10-16"
    assert live["up"] == ["600641.SH"]
    assert live["down"] == ["000001.SZ"]


def test_days_to_rebalance_is_computed_from_the_file_not_stored(root):
    """`days_to_rebalance` 由 `every − days_since` **现算**。

    自己再落一份就是手抄件，第二天就会漂。⇒ 改 `days_since_rebalance`
    ⇒ 输出必须跟着变；这是「它是算出来的」与「它是存下来的」的分岔点。
    """
    tc = TestClient(create_app(root))
    assert tc.get("/api/status").json()["live"]["days_to_rebalance"] == 20
    _make_holdings(root / "reports", days_since_rebalance=20)
    assert tc.get("/api/status").json()["live"]["days_to_rebalance"] == 1
    _make_holdings(root / "reports", days_since_rebalance=21)
    assert tc.get("/api/status").json()["live"]["days_to_rebalance"] == 0


def test_days_to_rebalance_never_goes_negative(root):
    """`days_since > every`（停牌多日 / 手改文件）不许给出负的倒计时。

    负数会渲染成「还有 −19 个交易日换仓」——一个用户读得懂但明显不对的字。
    """
    _make_holdings(root / "reports", days_since_rebalance=40)
    live = TestClient(create_app(root)).get("/api/status").json()["live"]
    assert live["days_to_rebalance"] == 0


def test_a_missing_state_is_null_and_not_an_empty_book(root):
    """🚨 **本文件最贵的一条**：`state === null` ≠ 空仓。

    `load_state` 任何异常都返 `None`（= 首次建仓）。若 payload 在这里造一个
    `{"up": [], "down": []}`，前端就会渲染成「你现在持有 0 只」——
    一个**看起来完全正常的假状态**，而真相是「明天会按首次建仓重新买一遍」。
    """
    (root / "reports" / "holdings.json").unlink()
    live = TestClient(create_app(root)).get("/api/status").json()["live"]
    assert live["state"] is None, "state 读不出来时不得造一个空的 state"
    assert live["every"] is None and live["days_to_rebalance"] is None
    assert live["as_of"] is None and live["up"] == []


def test_a_corrupt_state_is_also_null_not_a_partial_book(root):
    """坏 JSON 与缺文件同形 —— 两者的真值都是「读不出来」，不是「空仓」。"""
    (root / "reports" / "holdings.json").write_text("{not json", encoding="utf-8")
    live = TestClient(create_app(root)).get("/api/status").json()["live"]
    assert live["state"] is None
    assert live["up"] == []


def test_a_state_missing_a_required_key_is_also_null(root):
    """`load_state` 逐条校验 `REQUIRED_KEYS` ⇒ 少一个键整个状态就不可信。

    这条钉的是**夹具用的是真常量**：若 `REQUIRED_KEYS` 变了而这里还写死
    旧的那份，坏状态就会被当成好状态放过去。
    """
    state = json.loads((root / "reports" / "holdings.json").read_text(encoding="utf-8"))
    state.pop("every")
    (root / "reports" / "holdings.json").write_text(json.dumps(state), encoding="utf-8")
    assert TestClient(create_app(root)).get("/api/status").json()["live"]["state"] is None


def test_the_payload_reports_the_execution_verdict_and_its_reason(root):
    """`honored` 与 `reason` 都要递出，且 `reason` 不是空串。

    缺 `weights.json` 时实测 `honored=False`，`reason` 逐字带
    「没有 weights.json —— 实盘正在执行**回落默认权重**」。
    ⇒ 压成布尔就等于把「测过且是坏的」说成「没测过」。
    """
    live = TestClient(create_app(root)).get("/api/status").json()["live"]
    assert live["honored"] is False
    assert live["reason"], "honored=False 时 reason 不得为空"
    assert live["reason"] == live["execution_status"]["reason"]
    assert "weights.json" in live["reason"]


def test_the_two_every_fields_are_not_silently_equal(root):
    """⚠️ 两个「换仓周期」**字面矛盾**，而这不是缺陷、是被记录的事实。

    `live.every = 21`（holdings.json 的真值）
    `live.execution_status.rebalance_every = 1`（weights.json 缺失 ⇒ 回落初值）

    ⇒ 这条守卫**不是**说它们该相等，而是说「不相等」必须留在视野里：
    若哪天有人拿回落值覆盖真值让两者相等，这里会红，且红得对。
    """
    live = _live_holdings_payload(root / "reports")
    assert live["every"] == 21
    assert live["execution_status"]["rebalance_every"] != live["every"], (
        "live.every 与 execution_status.rebalance_every 现在相等 —— "
        "要么 weights.json 出现了（那时该改文案），"
        "要么有人用回落值覆盖了真值（那是错的）")


def test_every_payload_key_is_actually_read_by_the_frontend(root):
    """**双向对账**：前端读的 ⊆ payload 递的。

    只钉「payload 有这些键」会给**没人读**的键发永久信用 —— 而本文件第 0 段
    那个断链恰恰就是「数据到了 API 边界、零个消费点」。
    """
    live = _live_holdings_payload(root / "reports")
    body = _js_function_body(_html_text(), "renderHeld")
    read = set(re.findall(r"\blive\.(\w+)", body))
    for key in ("state", "as_of", "every", "days_since_rebalance",
                "days_to_rebalance", "honored", "reason",
                "up", "down", "up_names", "down_names"):
        assert key in live, f"payload 缺 {key}"
        assert key in read, f"前端没读 live.{key} ⇒ 它是无消费者的字段"


# ───────────────────── 接线层：前端 ─────────────────────

def test_the_overview_actually_calls_render_held():
    """`#held-box` 只有被 `loadOverview` 调 `renderHeld` 才会离开加载文案。"""
    body = _js_function_body(_html_text(), "loadOverview")
    assert re.search(r"renderHeld\(\s*status\.live\s*\)", body), \
        "loadOverview 没有 renderHeld(status.live)"


def test_status_is_declared_outside_the_try_that_renders_held():
    """🚨 **这一条守的是「症状与病因逐位同形」的那个坑。**

    若 `status` 退回成第一个 `try` 块内的 `const`，那么
    `renderHeld(status.live)` 抛 `ReferenceError` → 被下面那行的 catch 吃掉 →
    页面显示「**持仓区渲染失败**」——**而后端好好地递着 `live`**。
    ⇒ 排查方向完全相反（去查前端 vs 去查后端），而两边看起来一模一样。

    判据是**声明语句本身必须在函数体顶层**，不是块内的 `const`。
    """
    body = _js_function_body(_html_text(), "loadOverview")
    assert re.search(r"^\s*(let|var)\s+status\b", body, re.M), \
        ("loadOverview 顶层没有 let/var status ⇒ status 又是块内 const，"
         "renderHeld 会 ReferenceError，而页面只会显示「持仓区渲染失败」")
    assert not re.search(r"const\s*\[\s*status\s*,", body), \
        "loadOverview 仍在解构出块内的 const status"


def test_a_missing_state_renders_as_unreadable_not_as_an_empty_book():
    """坏状态那一支必须说「读不出来」，且**渲染出来的字**里不许有「持有 0 只」。

    ★ 断言锚在**模板串**上而不是整个函数体 —— 因为本文件自己的注释里
    就逐字写着「持有 0 只」，用整个函数体会被注释满足（第 13 层）。
    """
    body = _js_function_body(_html_text(), "renderHeld")
    early = body.split("const upCodes")[0]
    assert re.search(r"live\.state\s*===\s*null", early), \
        "renderHeld 没有对 state === null 分支（holdings.json 缺失会被当成空仓）"
    templates = _js_templates(body)
    assert any("持仓状态读不出来" in t for t in templates), \
        "模板串里没有「持仓状态读不出来」这一句"
    bad = [t for t in templates if "持有 0 只" in t]
    assert not bad, f"模板串里出现「持有 0 只」⇒ 坏状态被渲染成看起来正常的空仓：{bad}"


def test_the_execution_reason_is_rendered_verbatim():
    """`reason` 必须逐字进模板串（压成布尔 = 把「测过且是坏的」说成「没测过」）。"""
    body = _js_function_body(_html_text(), "renderHeld")
    assert re.search(r"esc\(\s*live\.reason\s*\)", body), \
        "renderHeld 没有 esc(live.reason) ⇒ 执行口径的理由被压成了布尔"
    assert any("held-why" in t and "reason" in t for t in _js_templates(body)), \
        "没有任何模板串承载 reason"


def test_the_down_leg_is_labelled_as_avoid_not_as_a_holding():
    """跌腿是**回避清单**，不是持仓。

    `run_daily.py::build_allocation(up, …)` 只用 `up`（散户不能做空，
    摩擦也只算单边）。把它渲染成「你也持有 10 只跌的」是凭空多算一半仓位。
    """
    templates = _js_templates(_js_function_body(_html_text(), "renderHeld"))
    assert any("回避" in t for t in templates), \
        "模板串没有把跌腿标成「回避」⇒ 界面暗示你也持有它"


# ───────────────────── 接线层：后端 ─────────────────────

def test_the_status_endpoint_returns_the_live_key():
    """删掉 `"live": ...` 那一行 ⇒ 前端 `#held-box` 永远停在加载文案。"""
    keys = _status_endpoint_keys()
    assert "live" in keys, f"/api/status 的返回体没有 live 键（现有：{keys}）"


# ───────────────────── 元守卫：提取器自己不能坏（第 131 层） ─────────────────────

def test_the_function_extractor_rejects_a_name_that_is_absent():
    """提取器返回空串时，**所有**基于它的断言都会变成恒真。"""
    with pytest.raises(AssertionError):
        _js_function_body(_html_text(), "function_that_does_not_exist_anywhere")


def test_the_function_extractor_survives_a_brace_inside_a_template_string():
    """模板串里的 `${…}` 与其中的 `}` 不能把花括号配对提前结束。"""
    src = "function demo(){ const t = `a${ {b:1}.b }b}`; return 42; }"
    assert "return 42" in _js_function_body(src, "demo")


def test_the_template_extractor_handles_nesting_and_insists_on_output():
    """`${}` 里的嵌套模板串不能把外层截断；抽不出东西必须报错而不是静默返回空。"""
    got = _js_templates("function f(){ return `a${`b${1}c`}d`; }")
    assert got == ["a${`b${1}c`}d"], got
    with pytest.raises(AssertionError):
        _js_templates("function f(){ return 1 + 2; }")
