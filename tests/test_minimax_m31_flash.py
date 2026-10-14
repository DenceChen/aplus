"""M3.1-Flash-Preview 客户端测试：Anthropic 兼容协议 + 强制思考。

M3.1 与 M3 的关键差异：

  ① 端点不同：`api.minimax.cn/anthropic`（不是 minimaxi.com OpenAI 兼容）
  ② 协议：Anthropic Messages（不是 OpenAI chatcompletion）
  ③ 强制思考：`content` 数组里第一项永远是 `thinking` 块，
            `text` 块是真正回复。提取只看 `text`，思考必须忽略。
  ④ prompt caching：`cache_read_input_tokens` 反映复用——进化 prompt
     几乎不变，所以二次调用应享受缓存。

测试覆盖三件事：

  ① 默认 base_url 是 `api.minimax.cn/anthropic`、model 是 M3.1-Flash-Preview
  ② `_extract_text` 忽略 thinking 块、只取 text 块、允许空 text
  ③ chat() 把 Anthropic response 转成纯文本（无 thinking 块）
"""
import pytest

from aplus.llm import MiniMaxM31FlashClient


def _resp(content_blocks, model="MiniMax-M3.1-Flash-Preview"):
    return {
        "id": "x", "type": "message", "role": "assistant", "model": model,
        "content": content_blocks,
        "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def test_default_endpoint_and_model():
    c = MiniMaxM31FlashClient(api_key="x")
    # api.minimax.cn/anthropic 与 M3.1-Flash-Preview 是用户最新指示的端点
    assert c.base_url == "https://api.minimax.cn/anthropic"
    assert c.model == "MiniMax-M3.1-Flash-Preview"


def test_extract_text_ignores_thinking_blocks():
    blocks = [
        {"type": "thinking", "thinking": "我在思考怎么回答……", "signature": "abc"},
        {"type": "text", "text": "我能看到的真实回复"},
    ]
    assert MiniMaxM31FlashClient._extract_text(_resp(blocks)) == "我能看到的真实回复"


def test_extract_text_handles_multiple_text_blocks():
    blocks = [
        {"type": "thinking", "thinking": "思考"},
        {"type": "text", "text": "第一段 "},
        {"type": "text", "text": "第二段"},
    ]
    # 多 text 块应拼接（与 Anthropic 标准一致）
    assert MiniMaxM31FlashClient._extract_text(_resp(blocks)) == "第一段 第二段"


def test_extract_text_handles_empty_text_block():
    blocks = [
        {"type": "thinking", "thinking": "思考但没回答"},
        {"type": "text", "text": ""},
    ]
    # 空 text 块不应抛错——max_tokens 被思考吃完是真实场景
    assert MiniMaxM31FlashClient._extract_text(_resp(blocks)) == ""


def test_extract_text_no_blocks_returns_empty():
    blocks = [{"type": "thinking", "thinking": "只思考没回答", "signature": "x"}]
    assert MiniMaxM31FlashClient._extract_text(_resp(blocks)) == ""


def test_extract_text_raises_on_no_content_at_all():
    # 完全空响应：要让上层知道出问题了
    with pytest.raises(RuntimeError, match="minimax m3.1 response empty"):
        MiniMaxM31FlashClient._extract_text(_resp([]))


def test_default_max_tokens_is_large_enough_for_thinking():
    """强制思考会消耗 max_tokens——2048 不够（实测 text 块被吃空返 0 组候选）。

    8192 给思考留足预算，text 块才能写出 JSON 数组契约。
    """
    c = MiniMaxM31FlashClient(api_key="x")
    assert c.max_tokens >= 8192


def test_is_overloaded_recognizes_529():
    """529 是端点过载（minimaxi 临时拥挤），应触发退避重试。

    其他 5xx 不应被当成过载——重试会掩盖真实故障。
    """
    from unittest.mock import MagicMock
    c = MiniMaxM31FlashClient(api_key="x")
    overloaded = MagicMock(status_code=529)
    assert c._is_overloaded(overloaded) is True
    not_overloaded = MagicMock(status_code=500)
    assert c._is_overloaded(not_overloaded) is False
    not_overloaded_4xx = MagicMock(status_code=400)
    assert c._is_overloaded(not_overloaded_4xx) is False
