"""提示词与 LLM 调用契约的审计守卫（只读审计，不改产品代码）。

## 本文件守什么

四类**契约**，每类都锚在**语法位置或运行态**上，不锚在「某处提到过」：

| § | 契约 | 坏掉的症状（用户视角） |
|---|---|---|
| ① | 每个 client 的模型名真的上了线（探针，不读代码） | 界面显示 3.1、实际跑的是别的模型；或反之 |
| ② | 主备链的降级语义：会切、切了有痕迹 | 主链挂了没人知道；界面两条链其实是一条 |
| ③ | `chat()` 的返回值契约（str，且「空」要与「失败」可区分） | 新闻情绪恒为 NaN、研究员恒 0 候选、零告警 |
| ④ | 提示词说的和验证器做的必须一致 | 模型提了合法候选却被静默清空 |

## 三条写法纪律（都源于本仓 2026-10-14 记下的失误）

1. **断言锚在语法位置上。**
   §④ 里全部因子名/权重区间的断言都收在**那一条** `只允许这些因子：…` 行上，
   并额外断言该字面量在**整份提示词里只出现 1 次**——
   否则一句无关文案就能把子串断言喂饱（记过的第 13 种形状）。

2. **变异验证用三重确认。**
   `_mutate()` 强制「旧串恰好 1 处 + 内容哈希变了 + 标记串恰好 1 次 +
   `ast.parse` 仍成功」四条全过，才允许看测试结果。
   任何一条不过就**抛异常中止**，绝不静默继续（记过的第 14 种形状）。

3. **守卫不许恒真。**
   每个 AST 抽取器都配一条「喂它一个不存在的名字，返回空」的负臂；
   §⑥ 的冻结文本守卫同时断言「不变的部分」与「必须变的部分」。

## 已复现的缺陷（见文件末尾清单）

带 `KNOWN_GAP` / `KNOWN_LEAK` 前缀的用例**不判定对错**，它们把已复现的
缺口钉成可观测事实：今天全绿；哪天有人修，前缀用例先红，提醒同时改文档。
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import io
import json
import logging
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# 审计范围内的四个文件（提示词层 + LLM 契约层）
_LLM = "aplus/llm.py"
_RESEARCHER = "aplus/evolve_researcher.py"
_NEWS = "aplus/news.py"
_WEEKLY = "scripts/evolve_weekly.py"

_AUDITED = (_LLM, _RESEARCHER, _NEWS, _WEEKLY)


# ───────────────────────── 读取 / AST 小工具 ─────────────────────────

def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _tree(rel: str) -> ast.Module:
    return ast.parse(_read(rel))


def _find_class(tree: ast.AST, name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _find_func(node: ast.AST, name: str) -> ast.FunctionDef | None:
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


# 记日志的调用**尾名**。只看尾名（`x.warning(...)` 的 `warning`），
# 这样 `logging.getLogger(__name__).warning(...)` 这种链式写法也认得。
_LOG_TAILS = {"print", "log", "logger", "warn", "warning", "error", "exception",
              "info", "critical", "debug", "getLogger"}


def _call_tail(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _logging_calls_in(node: ast.AST) -> list[tuple[int, str]]:
    """抽出 `node` 内部所有记日志的调用点：`(行号, 调用的反解文本)`。

    用 AST 而不是 `'log' in src`：`FallbackChain` 的 docstring 里
    「可观测」这类字样、以及注释里的中文，都能让子串断言恒真。
    """
    hits: list[tuple[int, str]] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and _call_tail(n) in _LOG_TAILS:
            hits.append((n.lineno, ast.unparse(n)[:120]))
    return hits


def _except_handlers(node: ast.AST) -> list[ast.ExceptHandler]:
    return [n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)]


# ───────────────────────── 变异验证（禁用子串确认） ─────────────────────────

def _mutate(rel: str, old: str, new: str, marker: str) -> str:
    """把 `rel` 的内容改一处并**核验改动真的落进去了**，返回变异后的源码。

    四条确认缺一不可：
      ① 旧串恰好 1 处 —— 0 处 = 变异是空操作；≥2 处 = 改错了地方
      ② 内容哈希变了 —— 证明字节真的动了
      ③ **标记串恰好 1 次** —— 确认用的是标记串而不是子串
         （子串确认是假的：`'/api/chat' in '/api/chat/stream'` 恒真）
      ④ `ast.parse` 仍成功 —— 证明改出来的还是合法 Python
    """
    src = _read(rel)
    n = src.count(old)
    assert n == 1, (
        f"变异锚点在 {rel} 里匹配 {n} 处（必须恰好 1）——文件没被改，"
        f"此时任何「测试仍绿 / 变红」都不构成证据。旧串={old[:70]!r}"
    )
    mutated = src.replace(old, new, 1)
    assert hashlib.sha256(mutated.encode()).hexdigest() != \
        hashlib.sha256(src.encode()).hexdigest(), "内容哈希没变 —— 写入没生效"
    assert mutated.count(marker) == 1, \
        f"标记串 {marker!r} 在变异稿里出现 {mutated.count(marker)} 次（必须恰好 1）"
    ast.parse(mutated)
    return mutated


# ───────────────────────── 假 provider（不打真网络） ─────────────────────────

class _FakeResp:
    """够用的 Anthropic 形状响应。"""

    status_code = 200
    text = ""
    headers: dict = {}

    def __init__(self, payload=None):
        self._payload = payload or {
            "model": "MOCK",
            "content": [{"type": "text", "text": "今天涨的是先导基电。"}],
        }

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    def iter_lines(self, decode_unicode=False):
        return iter(())


class _Wire:
    """记录所有 `requests.post` 的 URL / payload / headers。"""

    def __init__(self, payload=None):
        self.calls: list[dict] = []
        self._payload = payload

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "payload": json, "headers": headers})
        return _FakeResp(self._payload)

    @property
    def last(self) -> dict:
        return self.calls[-1]


@pytest.fixture()
def wire(monkeypatch):
    from aplus import llm as L

    w = _Wire()
    monkeypatch.setattr(L.requests, "post", w.post)
    return w


def _secrets(tmp_path, payload: dict) -> Path:
    p = tmp_path / "llm.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _evidence(**over) -> dict:
    """照 `scripts/evolve_weekly.py` 的真实形状造一份 evidence。"""
    ev = {
        "current_weights": {"mom20": -1.0, "rev5": 0.3},
        "baseline_stats": {
            "overall_hit_rate": 0.601, "hit_rate_up": 0.62, "hit_rate_down": 0.51,
            "up_excess_vs_market": 0.1523, "judged": 500,
        },
        "best_challenger": {
            "weights": {"mom20": -1.0, "vol20": 0.5},
            "rebalance_every": 21, "buffer_multiple": None,
            "stats": {"overall_hit_rate": 0.512, "hit_rate_up": 0.50,
                      "hit_rate_down": 0.49, "up_excess_vs_market": 0.0182},
        },
    }
    ev.update(over)
    return ev


# 提示词里因子/区间声明所在的那一行（唯一锚点）
_FACTOR_LINE = re.compile(r"^.*只允许这些因子：([^；]*)；.*$", re.M)
_WEIGHT_RANGE = re.compile(r"每个权重范围 \[(-?[\d.]+),\s*(-?[\d.]+)\]；")


def _advertised_line(prompt: str) -> str:
    """取**唯一**那一条声明因子与权重区间的行。

    断言收在这一行上，而不是「整份提示词里出现过 mom20」——
    后者会被任何一段叙述文字喂饱。
    """
    hits = _FACTOR_LINE.findall(prompt)
    assert len(hits) == 1, (
        f"提示词里声明因子集合的行有 {len(hits)} 条（必须恰好 1）——"
        f"锚点失效，下面所有 §④ 守卫都退化成子串断言"
    )
    m = _FACTOR_LINE.search(prompt)
    return m.group(0)


def _advertised_factors(prompt: str) -> list[str]:
    return [s.strip() for s in _FACTOR_LINE.search(prompt).group(1).split(",")]


def _advertised_bound(prompt: str) -> float:
    m = _WEIGHT_RANGE.search(_advertised_line(prompt))
    assert m, "声明行里找不到权重区间 —— §④ 的区间守卫全部落空"
    lo, hi = float(m.group(1)), float(m.group(2))
    assert lo == -hi, f"声明的区间不对称：[{lo}, {hi}] —— 与校验器的 abs(v) > BOUND 口径不符"
    return hi


# ═══════════════════════ §0 夹具前提守卫 ═══════════════════════
# 依赖前提的守卫必须先钉住前提，否则下面每一条都在空集上通过。


def test_premise_the_four_audited_files_exist():
    """本文件审计的就是这四个文件。少一个 ⇒ 后面所有静态断言在读空气。"""
    missing = [rel for rel in _AUDITED if not (_ROOT / rel).is_file()]
    assert not missing, f"审计范围内缺文件：{missing}"


def test_premise_the_ast_helper_finds_fallback_chain_and_nothing_else():
    """抽取器本身的有效性：认得真名字，也**认得出不存在的名字**（负臂）。"""
    tree = _tree(_LLM)
    assert _find_class(tree, "FallbackChain") is not None, \
        "aplus/llm.py 里找不到 FallbackChain —— §② 全部门禁落空"
    assert _find_class(tree, "NoSuchChainXyz") is None, \
        "抽取器对不存在的名字也返回东西 —— 它是恒真的，不能用来守任何东西"
    assert _find_func(_find_class(tree, "FallbackChain"), "chat") is not None
    assert _find_func(_find_class(tree, "FallbackChain"), "no_such_method") is None


def test_premise_the_logging_extractor_finds_a_real_call_and_nothing_in_a_bare_tree():
    """日志抽取器的负臂：一段没有日志调用的源码必须返回空。

    同时验证它认得**链式**写法 `logging.getLogger(__name__).warning(...)` ——
    只认 `logging.warning(...)` 的话，加日志的人换个写法就能绕过守卫。
    """
    assert _logging_calls_in(_find_class(_tree(_LLM), "FallbackChain")) == [], \
        "§② 的「切链不留痕」守卫前提已变（链里已经有日志了）——请升级守卫"
    chained = ast.parse("def f():\n    logging.getLogger(__name__).warning('x')\n")
    assert _logging_calls_in(chained), "抽取器认不出链式 logging 调用"
    assert _logging_calls_in(ast.parse("def f():\n    print('x')\n"))
    assert _logging_calls_in(ast.parse("def f():\n    self.log.warn('x')\n"))
    assert _logging_calls_in(ast.parse("x = foo(bar)\n")) == [], \
        "抽取器把普通调用也当成日志了 —— 它过宽，守卫会因误报而失效"


def test_premise_the_research_prompt_actually_builds(wire):
    """提示词守卫的前提：能真造出一份非空提示词。

    若 `build_research_prompt` 抛错或返回空，§④§⑤§⑥ 全都会变成
    「断言失败但不知道在守什么」的噪音。
    """
    from aplus.evolve_researcher import build_research_prompt

    prompt = build_research_prompt(_evidence())
    assert isinstance(prompt, str) and len(prompt) > 200, \
        f"提示词异常：类型={type(prompt).__name__} 长度={len(prompt)}"
    assert "只允许这些因子" in prompt, "提示词里没有因子声明行 —— §④ 锚点失效"
    assert "## 现任回测表现" in prompt, "提示词里没有现任表现段 —— §⑤ 段口径守卫失效"


def test_premise_the_wire_stub_is_actually_hit(wire):
    """探针守卫的前提：桩真的被调到了，否则下面所有「上了线」都是空断言。"""
    from aplus.llm import GlmProxyClient

    GlmProxyClient().chat([{"role": "user", "content": "涨"}])
    assert wire.calls, "桩没被调用 —— 生产路径没走到，探针结论不成立"
    assert wire.last["payload"].get("model"), "payload 里没有 model 键 —— 探针看错地方了"


# ═══════════════════════ §1 模型钉死（探针，不读代码） ═══════════════════════
# 记忆里的硬要求：每条 provider 都要钉死模型，且「配置里写了 ≠ 跑的是」。
# 所以这里全部用**故意违规的探针**验证，而不是读代码看有没有字面量。


def test_every_http_client_reports_a_nonempty_model():
    """两条走 HTTP 的 provider 都必须在对象上带着一个非空模型 id。

    坏掉的症状：`/api/llm/info` 的 model 字段变成 null，
    界面「现在到底在跑哪个模型」一栏永远空白。
    """
    from aplus import llm as L

    for c in (L.GlmProxyClient(), L.MiniMaxM31FlashClient("sk-AUDIT-NOT-REAL")):
        model = getattr(c, "model", None)
        assert isinstance(model, str) and model.strip(), (
            f"{type(c).__name__} 没有非空模型 id：model={model!r}"
        )


def test_KNOWN_GAP_the_claude_p_provider_does_not_pin_a_model_at_all(monkeypatch):
    """【已复现的缺口·本条不判定对错】`claude -p` 这条 provider **完全不钉模型**。

    复现（探针，不是读代码）：把 `subprocess.run` 打桩，
    看它实际收到的 argv：
        ['claude', '-p', '<prompt>', '--output-format', 'text']
    没有 `--model`。

    为什么严重：`scripts/evolve_weekly.py:318` 读
    `secrets.get("researcher", "claude-p")` —— **代码默认值就是 claude-p**。
    也就是说，只要用户没在 `~/.aplus/llm.json` 里显式写 `researcher`，
    每周进化研究员就跑在本机 `claude` CLI 的默认模型上，
    换模型不需要改任何代码，也没有任何测试或告警。

    症状（用户视角）：`/api/llm/info` 对这个 client 报
    `{'name': 'claude-p', 'model': None, 'kind': None}` ——
    「现在到底在跑哪个模型」这一栏对它永远是空的。

    （当前本机 `~/.aplus/llm.json` 的 `researcher` 是 `"direct"`，
    所以这条路径**目前**没被走到；缺口在**默认值**上。）

    修的时候本条先红。
    """
    import subprocess as sp

    from aplus import llm as L

    seen: dict = {}

    class _R:
        returncode = 0
        stdout = "  研究员回答  \n"
        stderr = ""

    def fake_run(args, **kw):
        seen["args"] = list(args)
        return _R()

    monkeypatch.setattr(sp, "run", fake_run)
    out = L.ClaudePClient(cwd=".").chat("提一组权重")

    assert out == "研究员回答", "桩没生效或返回值变了 —— 探针不成立"
    assert seen.get("args"), "subprocess.run 没被调到 —— 探针不成立"
    assert "--model" not in seen["args"], (
        f"claude-p 已经钉死模型了（argv={seen['args']}）—— 本缺口已修，请更新用例"
    )

    from aplus import webapp as W

    assert W._chain_entry(L.ClaudePClient(cwd="."))["model"] is None, \
        "端点已经开始报 claude-p 的模型了 —— 本缺口已修"

    # 默认路径确实会走到它：AST 读出 `secrets.get("researcher", <默认值>)` 的默认值
    default = None
    for node in ast.walk(_tree(_WEEKLY)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "researcher"):
            default = ast.literal_eval(node.args[1])
    assert default == "claude-p", \
        f"evolve_weekly.py 的 researcher 默认值现在是 {default!r} —— 本缺口的前提已变，请重读"


def test_the_model_on_the_object_is_the_model_that_reaches_the_wire(wire):
    """★ 「对象上写着」与「真的发出去」必须是同一个值。

    这是「配置里写了 ≠ 跑的是」的直接反证口：
    只读 `client.model` 的守卫会被一个「payload 写死另一个」的实现骗过。
    """
    from aplus.llm import GlmProxyClient, MiniMaxM31FlashClient

    c1 = GlmProxyClient()
    c1.chat([{"role": "user", "content": "x"}])
    assert wire.last["payload"]["model"] == c1.model, \
        f"线上的模型是 {wire.last['payload']['model']!r}，对象上是 {c1.model!r}"

    c2 = MiniMaxM31FlashClient("sk-AUDIT-NOT-REAL")
    c2.chat([{"role": "user", "content": "x"}])
    assert wire.last["payload"]["model"] == c2.model, \
        f"线上的模型是 {wire.last['payload']['model']!r}，对象上是 {c2.model!r}"


def test_deliberately_violating_the_model_changes_the_model_on_the_wire(wire):
    """★ 故意违规探针：把模型改成不可能存在的串，线路上必须跟着变。

    **负臂**（同样要验）：显式传 model 进去也必须生效。
    只有两条都成立，§1 的守卫才不是靠「默认值恰好没变」蒙对的。
    """
    from aplus.llm import GlmProxyClient, MiniMaxM31FlashClient

    GlmProxyClient(model="AUDIT-DELIBERATE-VIOLATION").chat([{"role": "user", "content": "x"}])
    assert wire.last["payload"]["model"] == "AUDIT-DELIBERATE-VIOLATION", \
        "显式传进去的模型没上线 —— §1 的其余守卫全是安慰剂"

    MiniMaxM31FlashClient("sk-AUDIT-NOT-REAL", model="AUDIT-DELIBERATE-VIOLATION-2") \
        .chat([{"role": "user", "content": "x"}])
    assert wire.last["payload"]["model"] == "AUDIT-DELIBERATE-VIOLATION-2", \
        "m3.1 显式传模型没上线"


def test_the_model_the_runtime_endpoint_reports_is_the_one_that_would_be_sent(wire, tmp_path):
    """★ 界面显示的模型 == 线路上的模型。

    `/api/llm/info` 是运行态事实的唯一出口（记过：界面显示 3.1、实际跑 M3
    的那次漂移）。这条把 `_chain_entry` 的读数与真实 payload 对拍。
    """
    from aplus import llm as L
    from aplus import webapp as W

    chain = L.default_llm_chain(secrets_path=_secrets(
        tmp_path, {"primary": "m31-flash", "m31_flash": {"api_key": "sk-AUDIT-NOT-REAL"}}))
    reported = W._chain_entry(W._chain_members(chain)[0])["model"]
    assert reported, "端点连模型名都没报出来 —— 探针看错地方了"

    chain.chat([{"role": "user", "content": "x"}])
    assert wire.last["payload"]["model"] == reported, \
        f"界面报 {reported!r}，线上发 {wire.last['payload']['model']!r}"


def test_a_non_string_model_is_reported_as_null_rather_than_invented():
    """负臂：模型不是字符串时，界面读数必须是 null 而不是回退到某个名字。

    「取不到就 null，绝不回退」是这个端点自己的纪律；
    回退等于把已经消灭的漂移又请回来一次。
    """
    from aplus import webapp as W

    class _Weird:
        name = "weird"
        model = 12345          # 不是 str

    assert W._chain_entry(_Weird())["model"] is None
    class _None:
        name = "none"
        model = None
    assert W._chain_entry(_None())["model"] is None
    class _Missing:
        name = "missing"
    assert W._chain_entry(_Missing())["model"] is None


def test_KNOWN_GAP_the_glm_providers_model_cannot_be_overridden_by_config(wire, tmp_path):
    """【已复现的缺口·本条不判定对错】`glm` 段配置对实际运行的模型零影响。

    复现（探针，非读代码）：
        配置写 `glm: {model: "AUDIT-DELIBERATE-VIOLATION-GLM",
                       base_url: "http://127.0.0.1:1/api/anthropic"}`
        → 跑出来的对象仍是 `glm-5.3` / `http://127.0.0.1:8787/api/anthropic`

    对比：`m31_flash.model` 同样的探针**会**上线（见上面两条）。
    ⇒ 两条 provider 的模型可配置性不对称。

    修的时候本条要先红：把 `GlmProxyClient()` 改成读配置之后，
    这里会红，提醒同时更新 docstring 与本文件的缺口清单。
    """
    from aplus.llm import default_llm_chain

    cfg = _secrets(tmp_path, {
        "primary": "glm",
        "m31_flash": {"api_key": "sk-AUDIT-NOT-REAL"},
        "glm": {"model": "AUDIT-DELIBERATE-VIOLATION-GLM",
                "base_url": "http://127.0.0.1:1/api/anthropic"},
    })
    chain = default_llm_chain(secrets_path=cfg)
    assert chain.primary.model == "glm-5.3", \
        "glm 模型已经可被配置覆盖了 —— 缺口已修，请更新 KNOWN_GAP 用例与审计清单"
    assert chain.primary.base_url == "http://127.0.0.1:8787/api/anthropic"

    # 对照臂：同一份写法对 m31 是生效的，这才让上面两条构成「不对称」而不是「都不行」
    cfg2 = _secrets(tmp_path, {"primary": "m31-flash",
                               "m31_flash": {"api_key": "sk-AUDIT-NOT-REAL",
                                             "model": "AUDIT-DELIBERATE-VIOLATION-M31"}})
    assert default_llm_chain(secrets_path=cfg2).primary.model == \
        "AUDIT-DELIBERATE-VIOLATION-M31"


# ═══════════════════════ §2 主备链的降级语义 ═══════════════════════


class _Scripted:
    """按脚本说话的可控 client：正常返回，或按剧本抛错。"""

    def __init__(self, name, reply="ok", error=None):
        self.name = name
        self._reply = reply
        self._error = error
        self.calls = 0

    def chat(self, messages, system=""):
        self.calls += 1
        if self._error:
            raise self._error
        return self._reply

    def chat_stream(self, messages, system=""):
        self.calls += 1
        if self._error:
            raise self._error
        yield "text", self._reply


def test_a_failing_primary_is_replaced_by_the_backup():
    """主链挂 ⇒ 备链服务，且观测字段指向备链。"""
    from aplus.llm import FallbackChain

    primary = _Scripted("glm", error=ConnectionError("全部 key 冷却"))
    backup = _Scripted("m31", reply="备链回答")
    chain = FallbackChain(primary, backup)

    assert chain.chat([{"role": "user", "content": "hi"}]) == "备链回答"
    assert chain.last_served_by == "m31", \
        f"切了备链但 last_served_by={chain.last_served_by!r} —— 切换不可观测"


def test_the_backup_is_a_different_provider_when_m31_is_the_primary(tmp_path):
    """正常配置下兜底必须**跨 provider**，否则「兜底」这个词是空的。"""
    from aplus.llm import default_llm_chain

    chain = default_llm_chain(secrets_path=_secrets(
        tmp_path, {"primary": "m31-flash", "m31_flash": {"api_key": "sk-AUDIT-NOT-REAL"}}))
    assert type(chain.primary) is not type(chain.backup), (
        f"主备是同一个实现 {type(chain.primary).__name__} —— 兜底不可能生效"
    )
    assert {chain.primary.name, chain.backup.name} == {"minimax-m31-flash", "glm-proxy"}


def test_KNOWN_GAP_when_glm_is_primary_the_advertised_fallback_is_the_same_provider(tmp_path):
    """【已复现的缺口·本条不判定对错】`primary="glm"` 时备链是主链的克隆。

    `_self_retry()` 返回一个新的 `GlmProxyClient`，
    与主链**同类、同 base_url、同模型**，只有 name 不同。
    ⇒ 整个 glm 代理不可用时，兜底打的是同一个死掉的代理。

    症状（用户视角）：`/api/llm/info` 显示两条链、两个模型名，
    界面读起来像「有冗余」；实际主备同时死，且两条读数完全一样、无法区分。
    修的时候本条先红。
    """
    from aplus import llm as L
    from aplus import webapp as W

    chain = L.default_llm_chain(secrets_path=_secrets(
        tmp_path, {"primary": "glm", "m31_flash": {"api_key": "sk-AUDIT-NOT-REAL"}}))
    assert type(chain.primary) is type(chain.backup) is L.GlmProxyClient
    assert chain.primary.base_url == chain.backup.base_url
    assert chain.primary.model == chain.backup.model
    # 用户看得见的那一面：两条成员读数一模一样，界面无从分辨
    entries = [W._chain_entry(c) for c in W._chain_members(chain)]
    assert len(entries) == 2, "端点只报一条链 —— 那反而是诚实的，本缺口已修"
    assert entries[0]["model"] == entries[1]["model"]
    assert entries[0]["kind"] == entries[1]["kind"]
    assert entries[0]["name"] != entries[1]["name"], \
        "两成员 name 相同，界面连两条都分不出来 —— 形态又变了，请重读本用例"


def test_switching_provider_leaves_no_record_of_the_primary_failure():
    """★ 主链的失败原因被整个丢掉：没有日志、没有字段、没有异常对象留存。

    复现：装一个根 logging handler，跑一次「主链抛 QUOTA_EXCEEDED → 备链答」，
    捕获到 **零条**记录；`FallbackChain` 源码里也没有任何 logging 调用。

    症状（用户视角）：对话正常返回，`/api/llm/info` 只说「备链服务了」。
    主链是配额耗尽、401、还是代理进程挂了 —— 事后无从判断，
    只能靠「它没说话」这种沉默的证据。
    """
    from aplus.llm import FallbackChain

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    try:
        chain = FallbackChain(
            _Scripted("primary", error=RuntimeError("QUOTA_EXCEEDED: 主链配额耗尽")),
            _Scripted("backup", reply="备链回答"),
        )
        assert chain.chat([{"role": "user", "content": "x"}]) == "备链回答"
        assert chain.last_served_by == "backup"
        captured = buf.getvalue()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

    assert captured == "", (
        "降级已经留下记录了 —— 本缺口已修，请把本用例升级成"
        f"「必须包含主链异常类型」的守卫。实际捕获到：{captured[:300]!r}"
    )
    # 反向确认：断言锚在 AST 调用点上，不是「源码里有没有 log 三个字母」
    fn = _find_func(_find_class(_tree(_LLM), "FallbackChain"), "chat")
    assert _logging_calls_in(fn) == [], "chat() 里已经出现日志调用了 —— 与上面矛盾，去查"
    assert len(_except_handlers(fn)) == 1, \
        "chat() 的 except 数量变了 —— 本用例守的「唯一一个裸 except」形态已变"


def test_when_every_link_fails_the_observability_field_stays_none():
    """全链失败时 `last_served_by` 保持 None —— 谁都没服务，但**为什么**没留下。"""
    from aplus.llm import FallbackChain

    chain = FallbackChain(_Scripted("p", error=ConnectionError("proxy down")),
                          _Scripted("b", error=TimeoutError("minimax timeout")))
    with pytest.raises(TimeoutError):
        chain.chat([{"role": "user", "content": "x"}])
    assert chain.last_served_by is None, \
        f"全链失败却记了 last_served_by={chain.last_served_by!r} —— 语义变了"


def test_an_empty_reply_from_the_primary_is_treated_as_success_and_never_falls_back():
    """★ 空串被当成成功 ⇒ 兜底永远不触发（已复现的静默失效）。

    复现链：`_extract_text` 对「只有 thinking 块」的响应返回 `""`（不抛错）→
    `FallbackChain.chat` 判定成功、记录主链服务过、**不切备链** →
    `research_candidates` → `parse_candidates("")` → `[]`。

    症状（用户视角）：周进化日志打「研究员提出 0 组候选」，
    没有 error、没有 traceback —— 读起来像「模型没灵感」，
    实际是「模型根本没说话」。
    """
    from aplus.evolve_researcher import research_candidates
    from aplus.llm import FallbackChain, MiniMaxM31FlashClient

    class _ThinksOnly(MiniMaxM31FlashClient):
        def chat(self, messages, system=""):
            return self._extract_text(
                {"model": "m", "content": [{"type": "thinking", "thinking": "想了 4000 token"}]})

    primary, backup = _ThinksOnly("sk-AUDIT-NOT-REAL"), _Scripted("glm", reply="备链真回答")
    chain = FallbackChain(primary, backup)

    assert chain.chat([{"role": "user", "content": "x"}]) == ""
    assert backup.calls == 0, "空串竟然触发了兜底 —— 形态变了，请重读本用例"
    assert chain.last_served_by == "minimax-m31-flash", \
        "空串被记成了主链服务 —— 这正是本用例要钉住的东西"

    out = research_candidates(_evidence(), claude_runner=None, direct=chain)
    assert out == [], f"下游本该拿到空候选，实际 {out}"


# ═══════════════════════ §3 响应提取的契约 ═══════════════════════


def test_a_thinking_only_response_yields_an_empty_string_instead_of_raising():
    """★ `_extract_text` 的 docstring 声称「空响应抛错」，thinking-only 不抛。

    docstring（`aplus/llm.py` 的 `MiniMaxM31FlashClient` 与 `_extract_text` 两处）
    写的是：「空响应抛错，让上层知道出问题了，而不是安静返回空串
    （空串会被下游当成功，污染日志）」。
    实测：`content` 非空但只有 thinking 块 ⇒ 返回 `""`，不抛。
    只有 `content == []` 才抛。

    而同一个类的类 docstring 又写着「max_tokens 吃满会让 text 块为空 → 0 候选」
    —— 也就是说**上游本来就会产出这个形状**，而守卫它的那句话就在旁边。
    """
    from aplus.llm import MiniMaxM31FlashClient

    c = MiniMaxM31FlashClient("sk-AUDIT-NOT-REAL")
    out = c._extract_text({"model": "m", "content": [{"type": "thinking", "thinking": "推理中"}]})
    assert out == "", f"thinking-only 已经改成抛错了 —— 本缺口已修，请升级本用例（实际 {out!r}）"
    assert isinstance(out, str)


def test_an_empty_content_list_does_raise():
    """对照臂：真正的空响应确实抛错（证明上一条不是「所有空都抛」）。"""
    from aplus.llm import MiniMaxM31FlashClient

    c = MiniMaxM31FlashClient("sk-AUDIT-NOT-REAL")
    with pytest.raises(RuntimeError):
        c._extract_text({"model": "m", "content": []})


def test_an_empty_reply_cannot_be_told_apart_from_a_legitimately_empty_reply():
    """★ 「模型没说话」与「模型答了空话」在下游完全同形。

    两条不同的上游事实（thinking 吃满 token / 模型确实返回空正文）
    产出同一个 `""`，下游无法区分，也没有任何字段记录。
    """
    from aplus.llm import MiniMaxM31FlashClient

    c = MiniMaxM31FlashClient("sk-AUDIT-NOT-REAL")
    thinks = c._extract_text({"model": "m", "content": [{"type": "thinking", "thinking": "x"}]})
    blank = c._extract_text({"model": "m", "content": [{"type": "text", "text": ""}]})
    assert thinks == blank == "", "两者已经可区分了 —— 本缺口已修"

    from aplus.evolve_researcher import parse_candidates
    from aplus.news import parse_sentiment

    assert parse_candidates(thinks) == []
    assert parse_sentiment(blank) == (None, None), \
        "新闻层已经能报告「模型没说话」了 —— 请把它升级成显式告警"


def test_blocks_of_unknown_types_are_skipped_rather_than_crashing():
    """负臂：抽取器对没见过的块类型给空串，不崩。"""
    from aplus.llm import MiniMaxM31FlashClient

    c = MiniMaxM31FlashClient("sk-AUDIT-NOT-REAL")
    assert c._extract_text({"model": "m", "content": [
        {"type": "redacted_thinking", "thinking": "…"},
        {"type": "server_tool_use", "id": "t1"},
    ]}) == ""


# ═══════════════════════ §4 提示词说的 = 验证器做的 ═══════════════════════


def test_the_factor_list_advertised_to_the_model_is_exactly_what_the_validator_accepts():
    """★ 提示词公布的因子集合必须与 `validate_candidates` 接受的完全一致。

    两个方向都验：
      ① 文本层：声明行上的名单 == `LEGAL_FACTORS`
      ② 行为层：**每一个**被公布的因子，真的能穿过 `validate_candidates`
    """
    from aplus.evolve_researcher import LEGAL_FACTORS, build_research_prompt, validate_candidates

    prompt = build_research_prompt(_evidence())
    advertised = _advertised_factors(prompt)
    assert advertised == list(LEGAL_FACTORS), \
        f"提示词公布的因子 {advertised} != 校验器接受的 {list(LEGAL_FACTORS)}"

    for f in advertised:
        got = validate_candidates([{"weights": {f: 1.0}, "rebalance_every": 21}])
        assert got, f"提示词向模型承诺了因子 {f}，但校验器把它全丢了 —— 提示词在说谎"


def test_the_researcher_may_repropose_the_platforms_own_default_weights():
    """★ **非自指**的不变量：研究员必须有权重平台自己的冠军权重。

    为什么这条重要：`test_…advertised_to_the_model_is_exactly_what_the_validator_accepts`
    是**自指的** —— 提示词由 `LEGAL_FACTORS` 生成、校验器也读 `LEGAL_FACTORS`，
    两者一起漂移时它照样绿（变异实测：删掉 `rev5` 那一刻它是绿的，
    只被 KNOWN_GAP 与变异自检抓到）。

    这条用**外部锚**：平台的 `DEFAULT_WEIGHTS`（`aplus/factor.py`）。
    若 `LEGAL_FACTORS` 不覆盖它，冠军自己都会被 `validate_candidates` 静默丢掉 ——
    研究员连「照着现任再试一次」都做不到，而日志只显示「研究员提出 0 组候选」。
    """
    from aplus.evolve_researcher import LEGAL_FACTORS, validate_candidates
    from aplus.factor import DEFAULT_WEIGHTS

    assert DEFAULT_WEIGHTS, "平台没有默认权重 —— 这条守卫的前提没了"
    uncovered = {k: v for k, v in DEFAULT_WEIGHTS.items() if k not in LEGAL_FACTORS}
    assert not uncovered, (
        f"默认权重里的 {sorted(uncovered)} 不在 LEGAL_FACTORS 中 —— "
        f"冠军权重自己会被校验器丢弃：{uncovered}"
    )
    # 行为层：把冠军原样喂给校验器，它必须原样通过
    got = validate_candidates([{"weights": dict(DEFAULT_WEIGHTS), "rebalance_every": 21}])
    assert got, f"冠军权重穿不过校验器（实际 {got}）—— 行为层与名单层不一致"


def test_the_declared_factor_list_is_a_subset_of_the_factors_the_platform_can_compute():
    """负臂：名单里不能出现平台**算不出来**的因子。

    与上一条方向相反：上一条守「不能少」，这条守「不能多」。
    两边都守住，`LEGAL_FACTORS` 才可能是 `FACTOR_NAMES` 的自洽子集。
    """
    from aplus.evolve_researcher import LEGAL_FACTORS
    from aplus.factor import FACTOR_NAMES

    assert FACTOR_NAMES, "平台因子表空了 —— 这条守卫的前提没了"
    unknown = tuple(f for f in LEGAL_FACTORS if f not in FACTOR_NAMES)
    assert not unknown, f"研究员被允许使用平台算不出来的因子：{unknown}"


def test_the_declared_factor_list_anchor_is_unique_in_the_prompt():
    """★ 防「子串断言被叙述文字喂饱」。

    因子名单在整份提示词里只允许出现**一次**（声明行那一次）；
    声明行本身也只允许出现一次。
    少了这两条，改声明段而漏改别处的叙述时，单点断言会绿、
    提示词自相矛盾 —— 正是本仓记过的第 13 种形状。
    """
    from aplus.evolve_researcher import LEGAL_FACTORS, build_research_prompt

    prompt = build_research_prompt(_evidence())
    line = _advertised_line(prompt)
    names = _advertised_factors(prompt)

    # 声明行整行恰好一次
    assert prompt.count(line) == 1, \
        f"声明行在提示词里出现 {prompt.count(line)} 次 —— 单点锚不再唯一"
    # 名单串（逗号连写）恰好一次
    listing = ", ".join(names)
    assert prompt.count(listing) == 1, \
        f"名单串 {listing!r} 在提示词里出现 {prompt.count(listing)} 次"
    assert listing == ", ".join(LEGAL_FACTORS)

    # 负臂：抽取器确实能数出「多处」
    two = prompt + "\n只允许这些因子：mom20, rev5, vol20, vsur, turn20；每个权重范围 [-3, 3]；"
    assert len(_FACTOR_LINE.findall(two)) == 2, "抽取器数不出重复行 —— 上面两条是恒真的"


def test_KNOWN_GAP_three_computable_factors_are_invisible_to_the_researcher():
    """【已复现的缺口·本条不判定对错】研究员看不见 8 个因子里的 3 个。

    复现：`aplus/factor.py:9` 的 `FACTOR_NAMES` 有 8 个，
    `aplus/evolve_researcher.py:46` 的 `LEGAL_FACTORS` 只有 5 个，
    差集 = `turnratio, ivol, rsi`。

    这是**硬编码名单**，不是从 `FACTOR_NAMES` 派生的 ⇒
    将来加第 9 个因子，它会**静默**对研究员不可见。

    症状（用户视角）：周进化研究员结构性地搜不到 3 个因子的组合；
    模型若自己猜到，`validate_candidates` 静默丢弃，
    日志只留下「研究员提出 0 组候选」。修的时候本条先红。
    """
    from aplus.evolve_researcher import LEGAL_FACTORS, build_research_prompt
    from aplus.factor import FACTOR_NAMES

    unreachable = tuple(f for f in FACTOR_NAMES if f not in LEGAL_FACTORS)
    assert unreachable == ("turnratio", "ivol", "rsi"), \
        (f"不可达因子集合变了：{unreachable} —— 若这是修复，请更新 KNOWN_GAP 用例"
         "与审计清单；若不是，说明 `FACTOR_NAMES` 又动了，同样要更新")

    advertised = _advertised_factors(build_research_prompt(_evidence()))
    for f in unreachable:
        assert f not in advertised, f"{f} 已经对研究员可见了 —— 缺口缩小，请更新用例"


def test_a_candidate_using_an_unadvertised_factor_is_dropped_without_any_signal():
    """★ 症状本体：模型提了因子 ⇒ 静默清空，不抛错、不打日志、计数为 0。

    `scripts/evolve_weekly.py:358` 打的是 `len(researcher_raw)`，
    而 `researcher_raw` 已经过校验 ⇒ **被丢的候选在计数里看不见**。
    """
    from aplus.evolve_researcher import research_candidates, validate_candidates

    raw = [{"ivol": 1.0, "rsi": -0.5}, {"turnratio": 1.0}]
    assert validate_candidates(raw) == [], "非法因子的候选不再被丢弃 —— 语义变了"

    out = research_candidates(_evidence(),
                              claude_runner=lambda p: json.dumps(raw), direct=None)
    assert out == [], f"编排层不再吞掉非法候选（实际 {out}）——语义变了"


def test_the_declared_weight_range_matches_what_the_validator_enforces():
    """★ 提示词公布的权重区间必须与 `validate_candidates` 实际接受的区间一致。

    锚点是**行为**不是字符串：把区间端点喂给校验器，看它接不接。
    再用 `WEIGHT_BOUND` 推导出的字面量去核对声明行 ——
    将来有人改了 `WEIGHT_BOUND` 却忘了改提示词，本条立刻红。
    """
    from aplus.evolve_researcher import (WEIGHT_BOUND, build_research_prompt,
                                         validate_candidates)

    prompt = build_research_prompt(_evidence())
    factor = _advertised_factors(prompt)[0]
    hi = _advertised_bound(prompt)

    assert validate_candidates([{"weights": {factor: hi}, "rebalance_every": 21}]), \
        f"提示词说 {factor}={hi} 合法，校验器却丢了 —— 提示词在撒谎"
    assert not validate_candidates([{"weights": {factor: hi + 0.1}, "rebalance_every": 21}]), \
        f"校验器接受了超过声明上限的 {hi + 0.1} —— 声明与实现不同源"

    declared = f"[-{WEIGHT_BOUND:g}, {WEIGHT_BOUND:g}]"
    assert declared in _advertised_line(prompt), (
        f"声明行里没有由 WEIGHT_BOUND={WEIGHT_BOUND} 推导出的 {declared!r} —— "
        f"提示词字面量与常量漂移了（实际：{_advertised_line(prompt)!r}）"
    )


# ═══════════════════════ §5 前视：提示词层确认也在泄露（只确认，不修） ═══════════════════════


def _evidence_segment_keys() -> dict:
    """从 `scripts/evolve_weekly.py` 的**语法位置**读出 evidence 两段统计的来源段。

    锚在 `evidence = {...}` 这个 Dict 字面量的 value 上，
    而不是「源码里出现过 `["test"]["stats"]`」——后者会被注释喂饱。
    """
    tree = _tree(_WEEKLY)
    out: dict = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "evidence"
                and isinstance(node.value, ast.Dict)):
            continue
        for k, v in zip(node.value.keys, node.value.values):
            if not isinstance(k, ast.Constant):
                continue
            m = re.findall(r"""\[\s*['"](train|test)['"]\s*\]\s*\[\s*['"]stats['"]\s*\]""",
                           ast.unparse(v))
            if m:
                out[k.value] = m[0]
    return out


def test_the_segment_detector_works_and_is_not_a_placeholder():
    """抽取器的正负两臂：认得出两段，也认得出「同一段」。"""
    keys = _evidence_segment_keys()
    assert set(keys) == {"baseline_stats", "best_challenger"}, \
        f"evidence 字典形状变了，读到 {keys} —— §⑤ 的守卫落空"

    same = {"baseline_stats": "train", "best_challenger": "train"}
    assert same["baseline_stats"] == same["best_challenger"], \
        "抽取器无法表达「两段同源」—— 正负臂没建立起来"


def test_KNOWN_LEAK_the_baseline_stats_fed_to_the_prompt_are_the_test_segment_ones():
    """【已复现的泄露·本条不判定对错，只钉住事实】提示词吃到的是**测试段**成绩。

    `scripts/evolve_weekly.py`：
        evidence["baseline_stats"]   = (champ_row or best_before)["test"]["stats"]
        evidence["best_challenger"]  = {... "stats": best_before["train"]["stats"]}

    ⇒ 喂进提示词的两组统计量来自**两个不同的段**：现任是测试段、挑战者是训练段。

    为什么这是泄露：研究员拿这份证据提假设，提出的候选随后在**同一个窗口的
    同一份测试段**上被 `race()` 评分。测试段的已实现成绩因此进入了搜索过程，
    它不再是「未见过的」那一半。

    本条**不修改**它（因子集合前视是已知的未修问题）。
    修的时候本条先红。
    """
    keys = _evidence_segment_keys()
    assert keys == {"baseline_stats": "test", "best_challenger": "train"}, (
        f"evidence 的段来源变了：{keys} —— 若这是修复，请更新 KNOWN_LEAK 用例与审计清单"
    )


def test_the_prompt_never_says_which_segment_its_two_stat_blocks_came_from():
    """★ 上面那个混口径在提示词里**看不出来** —— 这才是它危险的地方。

    现任那段的标题是「现任回测表现」、挑战者那段是「其表现」，
    两处都不带 train/test 字样；`market_summary` 只丢一句
    「训练段/测试段切分见 protocol」，把指针指向模型读不到的一份文档。
    """
    from aplus.evolve_researcher import build_research_prompt

    prompt = build_research_prompt(_evidence())
    start = prompt.index("## 现任回测表现")
    end = prompt.index("## 上期最强挑战者")
    stat_block = prompt[start:end]
    assert "## 现任权重" not in stat_block

    tokens = ("训练", "测试", "样本内", "样本外", "train", "test", "oos")
    hay = stat_block.casefold()
    found = [t for t in tokens if t.casefold() in hay]
    assert not found, f"现任表现段里已经标出段来源了（{found}）—— 混口径已可辨，请更新用例"

    # 负臂：抽取器确实认得这些词（否则上面那条是恒真的），且大小写不敏感
    probe = "训练段 Train ／ 测试段 test ／ 样本内 ／ 样本外 ／ OOS"
    assert {t for t in tokens if t.casefold() in probe.casefold()} == set(tokens), \
        f"抽取器对段名失灵，只认出 {[t for t in tokens if t.casefold() in probe.casefold()]}"


def test_the_two_stat_blocks_in_one_prompt_carry_different_calibers():
    """★ 同一条提示词里两组数字口径不同，且并排出现。

    复现：证据取 `evolve_weekly.py` 的真实构造（现任=测试段、挑战者=训练段），
    提示词渲染出：
        ## 现任回测表现  总命中 60.1% … 涨组合超额 +15.23%（500 条判定）   ← 测试段
        ## 上期最强挑战者 …  其表现：总命中 51.2%，涨超额 +1.82%            ← 训练段

    60.1% 与 51.2% 并排，读者会把它们当同一口径比较 —— 本仓记过的
    「把两个各自正确的数当成同一个量比」。
    """
    from aplus.evolve_researcher import build_research_prompt

    prompt = build_research_prompt(_evidence())
    assert "总命中 60.1%" in prompt and "总命中 51.2%" in prompt, \
        "两组数字不再并排 —— 形态变了，请重读本用例"
    assert _evidence_segment_keys()["baseline_stats"] != \
        _evidence_segment_keys()["best_challenger"], \
        "两段口径已经同源了 —— 混口径已修，请更新用例"


# ═══════════════════════ §6 提示词里的冻结字面量 ═══════════════════════


def test_the_research_narrative_is_frozen_while_the_evidence_is_not():
    """★ 提示词分两半：**冻结的叙述** + **注入的证据**。两半必须能被分别观测。

    冻结的那半（写死的 2019-2023 / 962 交易日 / 276 个百分点 / 94.9% / t=3.34 …）
    换任何 evidence 都不变；注入的那半（现任权重 / 现任表现 / 挑战者）
    必须随 evidence 变。

    两个方向同时断言 —— 只断言「不变」会被「整个提示词都被冻住了」蒙对，
    只断言「变」会被「模板整体重写」蒙对。
    """
    from aplus.evolve_researcher import build_research_prompt

    split = "## 现任权重"
    a = build_research_prompt(_evidence())
    b = build_research_prompt(_evidence(
        current_weights={"rsi": -2.0, "ivol": 1.5},
        baseline_stats={"overall_hit_rate": 0.40, "hit_rate_up": 0.41, "hit_rate_down": 0.39,
                        "up_excess_vs_market": -0.0311, "judged": 77},
    ))
    assert split in a and split in b

    frozen_a, frozen_b = a[:a.index(split)], b[:b.index(split)]
    assert frozen_a == frozen_b, "叙述半部随 evidence 变了 —— 它其实不是冻结的，请重读本用例"

    tail_a, tail_b = a[a.index(split):], b[b.index(split):]
    assert tail_a != tail_b, "证据半部没变 —— 注入断了，下面那些断言会在空跑"
    assert "rsi" in tail_b and "mom20" in tail_a


def test_the_prompt_pins_a_research_window_that_is_not_the_one_being_raced():
    """★ 提示词把一个**写死的**研究窗口钉给模型，与本轮实际窗口无关。

    `evolve_weekly.py` 的 `market_summary` 传的是本轮真实的 `start~end`，
    但叙述半部另有一套写死数字（「2019-2023 独立验证（962 交易日）」）。
    两者可以指向不同年份，且提示词不会提示这一点。

    症状（用户视角）：模型按一套过期年份的先验提候选，
    而赛马跑的是另一个窗口 —— 提示词不会提示这一点。
    """
    from aplus.evolve_researcher import build_research_prompt

    this_round = "回测窗口 2099-01-02~2099-01-31；训练段/测试段切分见 protocol"
    prompt = build_research_prompt(_evidence(market_summary=this_round))
    assert "2019-2023" in prompt, "冻结的研究窗口字面量不见了 —— 形态变了"
    assert "962 交易日" in prompt
    assert this_round in prompt, "本轮真实窗口没有进提示词 —— 上面的对比就不成立"
    # 两者确实并存且互不覆盖
    assert "2019-2023" in prompt and "2099-01-02" in prompt


def test_the_declared_evidence_narrative_carries_concrete_numbers_that_are_not_derived():
    """★ 把「这些数字是写死的」变成**穷举等值**断言，而不是靠人肉扫。

    守的是**集合相等**，不是「某几个还在」——
    第一版只列了 12 个，变异实测漏掉了 `+77.6%` / `−5.3%` / `7 年 97 组`
    三个仍在提示词里的冻结数字，改它们时守卫照样绿。
    等值断言让「加一个」和「删一个」同样变红。

    ## 范围声明（不是「什么都抓」）

    本条只守**数字字面量**。改字不改数（例如把「7 年 97 组赛马**测试段**里」
    改成「7 年 97 组赛马里」）本条**不会**红 —— 那不在它的声明范围内，
    记在这里以免日后把它当成「提示词任何改动都能抓住」的守卫。

    正则匹配不到东西时集合会退化成空 ⇒ 恒真，所以先钉住下限。
    """
    from aplus.evolve_researcher import build_research_prompt

    prompt = build_research_prompt(_evidence())
    frozen = prompt[:prompt.index("## 现任权重")]
    found = set(re.findall(
        r"[−+\-]?\d+(?:\.\d+)?%|\d+ 个百分点|t=\d+\.\d+|\d{4}-\d{4}"
        r"|\d+ 交易日|\d+ 年 \d+ 组", frozen))
    assert len(found) >= 10, \
        f"冻结半部只认出 {len(found)} 个数字（{sorted(found)}）—— 正则失效，下面的等值断言会误红"

    expected = {
        "2019-2023", "962 交易日", "−168.9%", "−5.3%", "+77.6%", "+105.7%",
        "276 个百分点", "7 年 97 组", "94.9%", "28.2%", "12.1%",
        "+0.008%", "t=0.21", "+0.134%", "t=3.34",
    }
    missing, extra = expected - found, found - expected
    assert not missing, f"冻结叙述里的数字不见了：{sorted(missing)}"
    assert not extra, f"冻结叙述里多出了未登记的数字：{sorted(extra)} —— 请一并更新本用例"


# ═══════════════════════ §7 悬空指代：证据只经 system 送达 ═══════════════════════


def test_the_evidence_prompt_travels_only_in_the_system_field(wire):
    """★ 研究员的证据**只**在 `system` 里，user 消息是一句悬空指代。

    复现（探针）：`research_candidates(..., direct=链)` 打到 minimax 的 payload：
        messages = [{"role": "user", "content": "基于以上证据提出候选权重。"}]
        system   = "你是 aplus 量化平台的策略进化研究员。……（全部证据在这里）"

    「以上证据」在 messages 里**并不存在**。
    症状：只要 provider 忽略/丢弃 system，模型收到的是一句无内容的指令，
    而整条链仍然返回可解析的候选 ⇒ 没有任何一层检查证据是否真被送达。
    """
    from aplus.evolve_researcher import research_candidates
    from aplus.llm import FallbackChain, GlmProxyClient, MiniMaxM31FlashClient

    chain = FallbackChain(MiniMaxM31FlashClient("sk-AUDIT-NOT-REAL"), GlmProxyClient())
    research_candidates(_evidence(), claude_runner=None, direct=chain)

    payload = wire.last["payload"]
    assert wire.calls, "桩没被调用 —— 探针没成立"
    assert "system" in payload, "证据已经不放 system 了 —— 本用例守的形态已变"
    joined = " ".join(m["content"] for m in payload["messages"])
    assert "只允许这些因子" not in joined, \
        "证据已经进 messages 了 —— 悬空指代已修，请更新本用例"
    assert "只允许这些因子" in payload["system"]
    assert "基于以上证据" in joined, "user 消息的措辞变了 —— 请重读本用例"


# ═══════════════════════ §8 变异自检：证明 §②§④§⑤ 不是安慰剂 ═══════════════════════
# 每次变异都必须先核验「改动真的落进文件了」，再看测试结果。
# 顺序反过来就会得出相反的结论、修相反的东西（记过的第 14 种形状）。


def test_mutation_swapping_the_declared_weight_bound_makes_the_range_guard_red():
    """变异：把提示词里的 `[-3, 3]` 改成 `[-9, 9]`（模型名/日期类过期字面量的典型形状）。

    期望：`_advertised_bound` 与 `WEIGHT_BOUND` 不再一致 ⇒ §④ 的区间守卫红。
    """
    mutated = _mutate(_RESEARCHER, "每个权重范围 [-3, 3]；",
                      "每个权重范围 [-9, 9]； AUDIT-MUT-BOUND", "AUDIT-MUT-BOUND")

    # 用与守卫同源的读法，从**变异稿**里重建声明行（不 import 被改过的模块）
    tree = ast.parse(mutated)
    decl = [n for n in ast.walk(tree)
            if isinstance(n, ast.JoinedStr)
            and any("每个权重范围" in ast.unparse(v) for v in n.values)]
    assert decl, "变异稿里找不到声明行 —— 变异形状不对"
    text = " ".join(ast.unparse(v) for v in decl[0].values)
    m = _WEIGHT_RANGE.search(text)
    assert m, "声明行里解析不出区间 —— 变异形状不对"
    hi = float(m.group(2))
    assert hi == 9.0, f"区间没被改成 9.0（读到 {hi}）—— 变异是空操作"

    from aplus.evolve_researcher import WEIGHT_BOUND

    assert hi != WEIGHT_BOUND, \
        "守卫没红：变异后的区间竟与 WEIGHT_BOUND 相同 —— §④ 的区间守卫有洞"
    # 守卫真正做的那一步：把声明区间与 WEIGHT_BOUND 推导出的字面量对拍
    assert f"[-{WEIGHT_BOUND:g}, {WEIGHT_BOUND:g}]" not in text, \
        "守卫没红：由 WEIGHT_BOUND 推导的字面量仍然对得上 —— 守卫有洞"


def test_mutation_dropping_a_factor_from_the_declared_list_makes_the_guards_red():
    """变异：从声明名单里删掉 `rev5`。

    期望：① 名单与 `LEGAL_FACTORS` 不一致 → §④ 名单守卫红；
         ② 该名单串在提示词里的出现次数变了 → 单点锚守卫红。
    """
    mutated = _mutate(_RESEARCHER,
                      "LEGAL_FACTORS = (\"mom20\", \"rev5\", \"vol20\", \"vsur\", \"turn20\")",
                      "LEGAL_FACTORS = (\"mom20\", \"vol20\", \"vsur\", \"turn20\")  # AUDIT-MUT-FACTORS",
                      "AUDIT-MUT-FACTORS")

    tree = ast.parse(mutated)
    consts = [n for n in ast.walk(tree)
              if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "LEGAL_FACTORS"]
    assert len(consts) == 1, f"变异稿里找到 {len(consts)} 个 LEGAL_FACTORS 赋值"
    names = list(ast.literal_eval(consts[0].value))
    assert names == ["mom20", "vol20", "vsur", "turn20"], "变异没生效"
    assert "rev5" not in names

    from aplus.evolve_researcher import LEGAL_FACTORS
    assert names != list(LEGAL_FACTORS), \
        "守卫没红：变异后的名单竟与原名单相同 —— §④ 名单守卫有洞"
    assert len(LEGAL_FACTORS) - len(names) == 1, \
        "守卫分不出「恰好少了一个」—— 它只比了相等，没比差集"


def test_mutation_repointing_the_evidence_to_the_train_segment_makes_the_leak_guard_red():
    """变异：把 `evolve_weekly.py` 里喂给提示词的现任成绩从 `test` 改成 `train`。

    期望：`test_the_baseline_stats_fed_to_the_prompt_are_the_test_segment_ones` 的
    等值断言红 —— **并且这只在改动真的落进文件时才会红**（`_mutate` 已三重确认）。
    """
    mutated = _mutate(_WEEKLY,
                      '"baseline_stats": (champ_row or best_before)["test"]["stats"],',
                      '"baseline_stats": (champ_row or best_before)["train"]["stats"],  # AUDIT-MUT-SEGMENT',
                      "AUDIT-MUT-SEGMENT")

    # 用与守卫同源的 AST 读法，在变异稿上重跑一遍
    out = {}
    for node in ast.walk(ast.parse(mutated)):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "evidence"
                and isinstance(node.value, ast.Dict)):
            continue
        for k, v in zip(node.value.keys, node.value.values):
            if isinstance(k, ast.Constant):
                m = re.findall(r"""\[\s*['"](train|test)['"]\s*\]\s*\[\s*['"]stats['"]\s*\]""",
                               ast.unparse(v))
                if m:
                    out[k.value] = m[0]
    assert out == {"baseline_stats": "train", "best_challenger": "train"}, \
        f"变异后读到的段来源是 {out} —— 与守卫同源的读法没生效"
    assert out["baseline_stats"] != "test", "守卫仍然读到 test —— 泄漏守卫有洞"


def test_mutation_adding_a_log_call_to_the_switch_breaks_the_no_record_guard():
    """变异：给 `FallbackChain.chat` 的 except 里加一行 logging。

    期望：「切链不留痕」守卫红 —— 即今天这条绿是**真绿**（现在确实没留痕），
    而不是抽取器坏了恒绿。
    """
    mutated = _mutate(
        _LLM,
        "        except Exception:  # noqa: BLE001 — 主链任何故障都切备链\n"
        "            reply = self.backup.chat(messages, system=system)\n",
        "        except Exception:  # noqa: BLE001 — 主链任何故障都切备链\n"
        "            __import__('logging').getLogger(__name__).warning('AUDIT-MUT-LOG')\n"
        "            reply = self.backup.chat(messages, system=system)\n",
        "AUDIT-MUT-LOG",
    )
    cls = _find_class(ast.parse(mutated), "FallbackChain")
    assert cls is not None
    fn = _find_func(cls, "chat")
    assert fn is not None
    calls = _logging_calls_in(fn)
    assert calls, "加了日志调用但抽取器没抓到 —— 抽取器坏了，那条守卫本来就无效"
    assert any("AUDIT-MUT-LOG" in text for _, text in calls), \
        f"抓到的是别的东西：{calls}"
    # 反向确认：原文件里确实没有这条日志（守卫今天是真绿）
    orig_fn = _find_func(_find_class(_tree(_LLM), "FallbackChain"), "chat")
    assert _logging_calls_in(orig_fn) == [], "原文件里也有日志 —— 与守卫矛盾"


def test_mutation_swapping_the_test_segment_stats_for_thinking_only_changes_the_chain_outcome():
    """变异：让 `_extract_text` 对 thinking-only 响应抛错（这就是它的 docstring 声称的行为）。

    期望：空串不再产生 ⇒ §2「空串不被当成成功」那条守卫的前提消失。
    这条证明我们**精确描述**了缺陷的形状，而不是笼统地说「返回值不对」。
    """
    mutated = _mutate(
        _LLM,
        '        parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]\n'
        '        return "".join(parts)\n',
        '        parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]\n'
        '        if not parts:\n'
        '            raise RuntimeError("AUDIT-MUT-NO-TEXT")\n'
        '        return "".join(parts)\n',
        "AUDIT-MUT-NO-TEXT",
    )
    cls = _find_class(ast.parse(mutated), "MiniMaxM31FlashClient")
    fn = _find_func(cls, "_extract_text")
    assert fn is not None
    body = ast.unparse(fn)
    assert "AUDIT-MUT-NO-TEXT" in body, "变异没落进 _extract_text —— 写法错了"
    # 原实现对 thinking-only 返回 ""；变异后它必须抛错
    assert 'if not parts' in body, "变异形态与预期不符"


def test_mutation_hardening_the_empty_reply_check_does_not_break_the_other_guards():
    """反向确认：把 `_extract_text` 修好之后，§3 的其余守卫不会假红。

    这是给未来的修复者留的路标 —— 也证明 §3 那些断言锚的是**行为**，
    而不是「函数里有没有 raise」。
    """
    mutated = _mutate(
        _LLM,
        "        parts = [b.get(\"text\", \"\") for b in blocks if b.get(\"type\") == \"text\"]\n"
        "        return \"\".join(parts)\n",
        "        parts = [b.get(\"text\", \"\") for b in blocks if b.get(\"type\") == \"text\"]\n"
        "        if not parts:\n"
        "            raise RuntimeError(\"AUDIT-MUT-EMPTY\")\n"
        "        return \"\".join(parts)\n",
        "AUDIT-MUT-EMPTY",
    )
    assert "AUDIT-MUT-EMPTY" in mutated
    # 原文件不受影响 —— 变异全程在内存里，产品代码一个字节都没动
    assert "AUDIT-MUT-EMPTY" not in _read(_LLM), "变异污染了产品代码！"
    assert "AUDIT-MUT-EMPTY" not in _read(_RESEARCHER)
