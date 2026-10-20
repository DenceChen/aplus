"""LLM 客户端：经本机 glm-failover 代理调用（Anthropic 兼容协议）。

5 账号轮循、冷却、周限全部由代理处理——本客户端只管一件事：把 OpenAI 风格的
消息列表转成 Anthropic Messages 格式发出去，拿回文本。

## `chat()` 与 `chat_stream()` 的分工（别合并）

`chat()` 返回 **str**，这个签名被两个**非 Web** 调用方依赖：
`aplus/news.py`（→ `parse_sentiment`）和 `aplus/evolve_researcher.py`
（→ `parse_candidates`）。两处都把调用包在 `except Exception` 里，
所以一旦 `chat()` 变成生成器：生成器喂给解析函数会失败 → 被 except 吞掉 →
**新闻情绪恒为 None、进化研究恒返回空候选，全程零告警，报告照常出数**。
流式一律走新增的 `chat_stream()`。
"""
import json
import time

import requests


def _anthropic_sse_deltas(resp):
    """Anthropic 兼容 SSE → `("thinking"|"text", 增量)` 生成器。

    ## 为什么自己解 utf-8 而不用 `iter_lines(decode_unicode=True)`

    requests 用响应头里的 charset 决定解码，**缺 charset 时默认 ISO-8859-1**。
    思考与正文都是中文，按 ISO-8859-1 解出来是乱码，而且不报错——
    一路拼进页面，用户看到的是「模型答非所问」。所以这里固定 utf-8。

    ## 上游 SSE 里的 error 必须抛，不能吞

    吞掉它 = 页面拿到一个「成功但空白」的回复：不报错、有 HTTP 200、
    前端照常渲染空正文。这正是本仓记过的静默失效形状。
    """
    resp.raise_for_status()
    name = None
    for raw in resp.iter_lines(decode_unicode=False):
        if raw is None:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line:
            name = None
            continue
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except ValueError as exc:
            raise RuntimeError(f"SSE data 不是 JSON: {payload[:200]}") from exc
        if name == "error" or obj.get("type") == "error":
            detail = (obj.get("error") or {}).get("message") or json.dumps(obj, ensure_ascii=False)
            raise RuntimeError(f"上游 SSE 报错：{str(detail)[:200]}")
        if obj.get("type") != "content_block_delta":
            continue  # message_start / ping / message_stop / content_block_start
        delta = obj.get("delta") or {}
        if delta.get("type") == "thinking_delta":
            text = delta.get("thinking") or ""
            if text:
                yield "thinking", text
        elif delta.get("type") == "text_delta":
            text = delta.get("text") or ""
            if text:
                yield "text", text


class GlmProxyClient:
    name = "glm-proxy"
    kind = "proxy"  # 只读端点 /api/llm/info 从**对象**上读，不写死模型名

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8787/api/anthropic",
        model: str = "glm-5.3",  # 代理会剥离 [1m] 后缀，这里直接用干净 id
        api_key: str = "local",
        timeout: int = 120,
        max_tokens: int = 2048,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens

    def _build_request(self, messages: list, system: str = ""):
        """请求构造的**唯一出口**：chat() 与 chat_stream() 都委派到这里。

        两份 payload 就会各自漂移——流式忘带 system、非流式忘带 max_tokens，
        而且两处都不报错，只是答案悄悄变差。
        """
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }
        if system:
            payload["system"] = system
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        return payload, headers

    def chat(self, messages: list, system: str = "") -> str:
        payload, headers = self._build_request(messages, system)
        resp = requests.post(
            f"{self.base_url}/v1/messages",
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return "".join(block.get("text", "") for block in data.get("content", []))

    def chat_stream(self, messages: list, system: str = ""):
        """SSE 真流式。请求构造委派 `_build_request`，只多一个 `stream: True`。

        代理侧已强制 `stream:true` 与思考开关，这里显式声明是为了让
        「打到别的端点也能流」——依赖代理的隐式行为就是依赖一个看不见的约定。
        """
        payload, headers = self._build_request(messages, system)
        payload["stream"] = True
        resp = requests.post(
            f"{self.base_url}/v1/messages",
            json=payload,
            headers=headers,
            timeout=self.timeout,
            stream=True,
        )
        return _anthropic_sse_deltas(resp)


class FallbackChain:
    """主链失败 → 备链服务；last_served_by 记录本次由谁服务（可观测）。"""

    def __init__(self, primary, backup):
        self.primary = primary
        self.backup = backup
        self.last_served_by = None

    def chat(self, messages: list, system: str = "") -> str:
        try:
            reply = self.primary.chat(messages, system=system)
            self.last_served_by = getattr(self.primary, "name", "primary")
            return reply
        except Exception:  # noqa: BLE001 — 主链任何故障都切备链
            reply = self.backup.chat(messages, system=system)
            self.last_served_by = getattr(self.backup, "name", "backup")
            return reply

    def chat_stream(self, messages: list, system: str = ""):
        """流式故障转移：**只在还没吐出任何增量时**才切备链。

        中途断流切备链重来会重发已发过的增量，
        页面上的正文直接翻倍——而 `"".join(text) == reply` 仍是绿的，
        因为两边一起变长。已吐增量就往上抛，让上层发 error。
        """
        emitted = False
        try:
            for kind, delta in self.primary.chat_stream(messages, system=system):
                emitted = True
                yield kind, delta
            self.last_served_by = getattr(self.primary, "name", "primary")
            return
        except Exception:  # noqa: BLE001
            if emitted:
                raise
        for kind, delta in self.backup.chat_stream(messages, system=system):
            yield kind, delta
        self.last_served_by = getattr(self.backup, "name", "backup")


class ClaudePClient:
    """claude -p 子进程（无头 Claude Code）：能读文件、带技能栈的深度研究员。

    只用于低频深度任务（周度进化研究等）；Web 对话走直连链。"""

    name = "claude-p"

    def __init__(self, cwd: str, timeout: int = 600):
        self.cwd = cwd
        self.timeout = timeout

    def chat(self, prompt: str) -> str:
        import subprocess

        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            cwd=self.cwd, capture_output=True, text=True, timeout=self.timeout,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError(f"claude -p failed: rc={result.returncode} {result.stderr[:200]}")
        return result.stdout.strip()


class MiniMaxM31FlashClient:
    """M3.1-Flash 直连：Anthropic Messages 协议 + 强制思考。

    ## 🚨 服务端**不校验模型名**，会静默回落（2026-10-20 实测）

    发四个名字过去，全部 **HTTP 200 + 正常正文 + `stop_reason=end_turn`**，
    但响应里的 `model` 字段各不相同：

    | 发出去的 | 响应 `model`（= 真正跑的那个） |
    |---|---|
    | `MiniMax-M3.1-Flash` | `MiniMax-M3.1-Flash` ✅ 逐位对上 |
    | `MINIMAX-M3.1-FLASH`（错大小写） | `MiniMax-M3.1-Flash`（归一化后认出） |
    | `MiniMax-M9.9-Flash-NotAModel`（不存在） | **`MiniMax-M3`** ⬅️ 静默回落 |
    | `MiniMax-M3.1-Flash-Preview-Extra`（不存在） | **`MiniMax-M3`** ⬅️ 静默回落 |

    ⇒ **拼错模型名不会报错，只会悄悄换成一个更弱的模型继续跑。**
    它满足「看起来正常」的全部条件：200、有内容、无异常、无 stderr。
    ⇒ **判「跑的是哪个模型」只能读响应的 `model` 字段，读请求里那个字符串是恒真的。**
      注意本类 `self.model` 存的是**请求侧**的名字，而 `/api/llm/info`
      从对象上读它 —— 所以配置层报出来的模型名**证明不了**服务端认了这个名字。
    ⇒ 这也是上面「M3 客户端已删除」那条纪律的动机：
      一个从不报错的降级路径，比一个会崩的缺失路径更危险。

    ## 为什么仓库里没有 M3

    2026-10-14 用户硬要求：MiniMax 只用 `MiniMax-M3.1-Flash-Preview`。
    M3 的客户端类**已删除**而不是保留——留着它就意味着某天有人会
    `from aplus.llm import MiniMaxClient` 把更弱的模型悄悄接回线上，
    而链路配置（`~/.aplus/llm.json`）看起来一切正常。
    删掉比"默认不装配"更可靠：不可用之物不会被人误用。

    **2026-10-20 用户改要求**：模型换成 `MiniMax-M3.1-Flash`（无 `-Preview` 后缀）。
    已实测该名被服务端逐位认出（上行表第一行），
    而带 `-Preview` 的旧名**同样仍然可用** —— 所以这次改名不是修一个坏配置，
    是**换用**。上面 10-14 那条记录保留原样：它记的是当时的事实，改写它就是伪造。
    本仓凡「带日期的历史记录」一律**追加不改写**。

    ## 关键参数

    | 项 | 值 |
    |---|---|
    | 域名 | `api.minimax.cn` |
    | 协议 | Anthropic `/anthropic/v1/messages` |
    | 思考 | **强制、不可关闭**，content 首项是 thinking 块 |
    | max_tokens | 8192（思考吃满 2048 会让 text 块为空 → 0 候选） |

    ## 提取策略

    只取 `content[].type == "text"` 的文本，`thinking` 块**仅在应用层不暴露**——
    **思考从未关闭**：模型本身始终在思考（消耗 server 端 token 预算，
    可在响应 `usage.thinking_tokens` 里看到数字），思考结果正常影响 text 块。
    跳过的唯一原因是思考内容包含推理过程，不该外泄到 aplus 日志（防 prompt 注入）。

    多 text 块按 Anthropic 标准拼接；空 text 块允许（max_tokens 被思考吃完）。

    ## max_tokens 预算

    强制思考 + 必须给 text 块留够空间输出 JSON 数组契约（实测单组 200~400 token），
    2048 全部被思考吃完 → 0 组候选。**默认 8192**。

    ## 529 过载重试

    minimaxi 端点偶发 529 `overloaded_error`（临时拥挤）。3 次指数退避重试
    （1s / 2s / 4s），只对 529 重试——其他 5xx/4xx 不重试，避免掩盖真实故障。

    ## prompt caching

    进化 prompt 几乎不变（事实是同一个 evidence 模板），二次调用应享受
    `cache_read_input_tokens` 命中——实测可大幅降本。
    """

    name = "minimax-m31-flash"
    kind = "direct"  # 只读端点 /api/llm/info 从**对象**上读，不写死模型名

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.minimax.cn/anthropic",
        model: str = "MiniMax-M3.1-Flash",
        timeout: int = 120,
        max_tokens: int = 8192,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.max_retries = max_retries

    @staticmethod
    def _is_overloaded(resp) -> bool:
        """529 `overloaded_error` 是 minimaxi 端临时拥挤——可重试。

        其他 5xx/4xx 是真故障（参数错、权限、配额），重试会掩盖错误。
        """
        return getattr(resp, "status_code", None) == 529

    @staticmethod
    def _extract_text(resp: dict) -> str:
        """从 Anthropic response 提取纯文本——**思考过程**不暴露。

        注意：模型本身始终在思考（`thinking_tokens` > 0 可验证），
        只是思考内容不进 aplus 日志。思考结果正常影响 text 块。

        空响应抛错，让上层知道出问题了，而不是安静返回空串
        （空串会被下游当成功，污染日志）。
        """
        blocks = resp.get("content") or []
        if not blocks:
            raise RuntimeError(
                f"minimax m3.1 response empty: model={resp.get('model', '?')}")
        parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        return "".join(parts)

    def _build_request(self, messages: list, system: str = ""):
        """请求构造的**唯一出口**：chat() 与 chat_stream() 都委派到这里。"""
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": list(messages),
        }
        if system:
            payload["system"] = system
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        return payload, headers

    def _post_with_529_retry(self, payload: dict, headers: dict, **post_kwargs):
        """只对 529 重试——其他 5xx/4xx 是真故障，重试会掩盖错误。"""
        last_err = None
        for attempt in range(self.max_retries):
            resp = requests.post(
                f"{self.base_url}/v1/messages",
                json=payload,
                headers=headers,
                timeout=self.timeout,
                **post_kwargs,
            )
            if self._is_overloaded(resp):
                last_err = RuntimeError(
                    f"minimax m3.1 overloaded (529) attempt {attempt+1}/{self.max_retries}")
                time.sleep(2 ** attempt)   # 1s, 2s, 4s
                continue
            break
        else:
            raise last_err  # noqa: F821 — 仅 529 路径走到这里
        return resp

    def chat(self, messages: list, system: str = "") -> str:
        payload, headers = self._build_request(messages, system)
        resp = self._post_with_529_retry(payload, headers)

        if resp.status_code != 200:
            raise RuntimeError(
                f"minimax m3.1 http {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if data.get("base_resp", {}).get("status_code", 0) != 0:
            raise RuntimeError(
                f"minimax m3.1 error: {data['base_resp'].get('status_msg')}")
        return self._extract_text(data)

    def chat_stream(self, messages: list, system: str = ""):
        """流式增量。M3.1 强制思考 ⇒ `thinking_delta` 是常态增量，
        与正文 `text_delta` 靠 `delta.type` 区分，不可混为一谈。

        与 `chat()` 同一个 `_build_request`，只多 `stream: True`。
        """
        payload, headers = self._build_request(messages, system)
        payload["stream"] = True
        resp = self._post_with_529_retry(payload, headers, stream=True)
        if resp.status_code != 200:
            raise RuntimeError(
                f"minimax m3.1 http {resp.status_code}: {resp.text[:200]}")
        return _anthropic_sse_deltas(resp)


def load_secrets(path) -> dict:
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def default_llm_chain(secrets_path=None, cwd: str = "."):
    """平台默认 LLM 链。主备顺序由 ~/.aplus/llm.json 的 `primary` 字段决定
    （`m31-flash` / `glm`），改配置不改代码。

    ## 2026-10-14 路线调整

    全平台 LLM 只有 m3.1 一个 MiniMax 客户端（`MiniMaxM31FlashClient`）。

    当前推荐：`primary="m31-flash"`（新模型、强制思考、prompt caching
    可显著降本）；glm-failover 始终是最后兜底（5 账号轮询，本机代理）。
    """
    from pathlib import Path

    if secrets_path is None:
        secrets_path = Path.home() / ".aplus" / "llm.json"
    secrets = load_secrets(secrets_path)

    glm = GlmProxyClient()
    glm.name = "glm-proxy"
    m31_cfg = secrets.get("m31_flash", {})
    m31 = (
        MiniMaxM31FlashClient(
            m31_cfg["api_key"],
            m31_cfg.get("base_url", "https://api.minimax.cn/anthropic"),
            m31_cfg.get("model", "MiniMax-M3.1-Flash"),
        )
        if m31_cfg.get("api_key") else None
    )

    primary_name = secrets.get("primary", "glm")
    if primary_name == "m31-flash" and m31 is not None:
        candidates = [m31]
    elif primary_name == "glm":
        candidates = [glm]
    else:
        # 未识别 primary → 走 m3.1（已配）或 glm
        candidates = [m31] if m31 is not None else [glm]
    # 始终保留 glm 作为最后兜底（只要它活着，且不在首位）
    if glm not in candidates:
        candidates.append(glm)
    primary = candidates[0]
    backup = candidates[1] if len(candidates) > 1 else _self_retry(primary)
    return FallbackChain(primary, backup)


def _self_retry(client):
    clone = GlmProxyClient()
    clone.name = "glm-proxy-retry"
    return clone
