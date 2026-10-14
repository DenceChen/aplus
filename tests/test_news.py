"""新闻情绪覆盖层测试：TDD RED 先行。

架构：量化因子全市场排名 → 仅对 Top 候选拉新闻 → LLM 情绪分（-1..+1）+ 理由。
search 与 llm 均可注入（测试用假件，生产走 MiniMax 搜索 + M3 思考）。
"""
import pandas as pd
import pytest

from aplus.news import news_overlay, parse_sentiment


class FakeSearch:
    def __init__(self, results=None, error=None):
        self._results = results or [{"title": "业绩超预期", "link": "https://x", "snippet": "净利增30%", "date": "2026-10-09"}]
        self._error = error
        self.queries = []

    def search(self, q):
        self.queries.append(q)
        if self._error:
            raise self._error
        return self._results


class FakeLLM:
    def __init__(self, reply='{"sentiment": 0.6, "reason": "业绩超预期"}', error=None):
        self._reply = reply
        self._error = error
        self.calls = 0

    def chat(self, messages, system=""):
        self.calls += 1
        if self._error:
            raise self._error
        return self._reply


def _cands(n=3):
    return pd.DataFrame({
        "code": [f"60000{i}.SH" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "direction": ["up"] * n,
        "score": [3.0 - i for i in range(n)],
    })


def test_news_overlay_adds_sentiment_and_reason():
    overlay = news_overlay(_cands(), FakeSearch(), FakeLLM())
    assert overlay["news_sentiment"].iloc[0] == 0.6
    assert overlay["news_reason"].iloc[0] == "业绩超预期"
    assert overlay["news_query"].iloc[0].startswith("股票0")


def test_news_overlay_adjusted_score():
    overlay = news_overlay(_cands(), FakeSearch(), FakeLLM())
    # score 3.0 + 0.5×0.6 = 3.3
    assert overlay["adjusted_score"].iloc[0] == pytest.approx(3.3)


def test_news_overlay_tolerates_bad_llm_output():
    overlay = news_overlay(_cands(1), FakeSearch(), FakeLLM(reply="我认为利好"))
    assert pd.isna(overlay["news_sentiment"].iloc[0])
    assert overlay["adjusted_score"].iloc[0] == pytest.approx(overlay["score"].iloc[0])


def test_news_overlay_search_failure_keeps_stock():
    overlay = news_overlay(_cands(1), FakeSearch(error=TimeoutError("search down")), FakeLLM())
    assert pd.isna(overlay["news_sentiment"].iloc[0])


def test_parse_sentiment_variants():
    assert parse_sentiment('{"sentiment": -0.4, "reason": "利空"}') == (-0.4, "利空")
    assert parse_sentiment("胡言乱语") == (None, None)
    # 越界值夹紧到 [-1, 1]
    s, _ = parse_sentiment('{"sentiment": 2.0, "reason": "x"}')
    assert s == 1.0
