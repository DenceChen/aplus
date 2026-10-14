"""新闻情绪覆盖层：MiniMax 搜索 + LLM 情绪打分。

定位：量化因子在全市场排名后，仅对 Top 候选做新闻验证（全市场逐个搜不现实）。
任何故障（搜索超时/模型胡言）都降级为 NaN——绝不阻塞每日主流程。
"""
import json
import re

import pandas as pd

SENTIMENT_PROMPT = (
    "你是股票新闻情绪分析员。根据以下关于某只 A 股的最新搜索结果，判断未来一个交易日"
    "股价受消息面影响的倾向。只输出一行 JSON："
    '{"sentiment": -1到1之间的小数(正=利好,负=利空,0=中性), "reason": "10字以内中文理由"}'
    "\n搜索结果：\n"
)


def search_api_key(secrets: dict) -> str:
    """从 `~/.aplus/llm.json` 里取新闻搜索要用的 key，取不到返回空串。

    ## 为什么要有这个函数（而不是各处 `secrets["minimax"]["api_key"]`）

    2026-10-14 平台把 MiniMax 收敛到只剩 M3.1，配置里的 `minimax` 段被删除。
    而调用点读的是 `secrets.get("minimax", {}).get("api_key")`——
    key 不在了，条件为假，**整个新闻覆盖层被静默跳过**：
    不抛异常、不打日志，产品安静地少了一块功能。

    搜索 API（`api.minimax.cn/v1/coding_plan/search`）与聊天模型是两个东西，
    但**共用同一把 key**。所以按 `m31_flash` → `minimax` 顺序找，
    旧配置继续能用，新配置也能用。

    空白字符串按「没有」处理：拿空 Bearer 去打搜索会拿到 401，
    而上层会把 401 当成「没搜到新闻」，结论错了却看不出异常。
    """
    for section in ("m31_flash", "minimax"):
        key = (secrets.get(section) or {}).get("api_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    return ""


def parse_sentiment(llm_reply: str):
    """解析 LLM 的情绪 JSON；失败返回 (None, None)；越界夹紧 [-1, 1]。"""
    if not llm_reply:
        return None, None
    match = re.search(r"\{.*\}", llm_reply, re.S)
    if not match:
        return None, None
    try:
        data = json.loads(match.group(0))
        sentiment = float(data["sentiment"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, None
    sentiment = max(-1.0, min(1.0, sentiment))
    reason = str(data.get("reason", ""))[:30]
    return sentiment, reason or None


def news_overlay(candidates: pd.DataFrame, search, llm, sentiment_weight: float = 0.5) -> pd.DataFrame:
    """为候选表附加 news_sentiment / news_reason / adjusted_score 列。

    adjusted_score = score + sentiment_weight × sentiment（温和微调，不剧变排名）。
    """
    out = candidates.copy()
    sentiments, reasons, queries = [], [], []
    for _, row in out.iterrows():
        query = f"{row['name']} {row['code']} 股票 最新消息"
        queries.append(query)
        sentiment, reason = None, None
        try:
            results = search.search(query)
            if results:
                digest = "\n".join(
                    f"- {r.get('title', '')}：{r.get('snippet', '')[:150]}" for r in results[:5]
                )
                reply = llm.chat([{"role": "user", "content": SENTIMENT_PROMPT + digest}])
                sentiment, reason = parse_sentiment(reply)
        except Exception:  # noqa: BLE001 — 覆盖层任何故障不得影响主流程
            pass
        sentiments.append(sentiment)
        reasons.append(reason)
    out["news_sentiment"] = sentiments
    out["news_reason"] = reasons
    out["news_query"] = queries
    fill = out["news_sentiment"].fillna(0.0)
    out["adjusted_score"] = out["score"] + sentiment_weight * fill
    return out


class MiniMaxSearch:
    """MiniMax M Plan 搜索（/v1/coding_plan/search，api.minimax.cn）。"""

    def __init__(self, api_key: str, base_url: str = "https://api.minimax.cn", timeout: int = 45):
        import requests

        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update({"Authorization": f"Bearer {api_key}"})
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def search(self, q: str) -> list:
        resp = self._session.post(
            f"{self.base_url}/v1/coding_plan/search",
            json={"q": q}, timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"minimax search http {resp.status_code}")
        data = resp.json()
        if data.get("base_resp", {}).get("status_code", 0) != 0:
            raise RuntimeError(f"minimax search error: {data['base_resp'].get('status_msg')}")
        return data.get("organic", [])
