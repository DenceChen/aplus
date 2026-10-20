"""新闻搜索 key 解析：M3 删除后的回归防护。

## 这条测试为什么存在（2026-10-14 实打实的回归）

按用户要求把 M3 从平台彻底移除后，`~/.aplus/llm.json` 里的 `minimax` 段
被删掉了。而 `scripts/run_daily.py` 的新闻情绪覆盖层读的是：

    secrets.get("minimax", {}).get("api_key")

key 不在了 → 条件为假 → **整个新闻覆盖层被静默跳过**。
没有异常、没有日志、没有任何地方会报错，只是「新闻情绪」这个功能
从此再也没跑过。

这类回归必须用测试钉死：功能消失时不会抛异常，只会让产品安静地少一块。
"""
from aplus.news import search_api_key


def test_reads_key_from_m31_flash_config():
    """现行配置：key 在 `m31_flash` 段下（MiniMax 只剩这一个模型）。"""
    cfg = {"primary": "m31-flash",
           "m31_flash": {"api_key": "sk-m31", "model": "MiniMax-M3.1-Flash"}}
    assert search_api_key(cfg) == "sk-m31"


def test_falls_back_to_legacy_minimax_key():
    """旧配置（key 在 `minimax` 段）仍要能用——搜索 API 与聊天模型解耦，
    有人可能只配了搜索 key。"""
    assert search_api_key({"minimax": {"api_key": "sk-old"}}) == "sk-old"


def test_returns_empty_when_no_key():
    """没有 key 时返回空串而不是 KeyError——调用方按「空即跳过」处理。"""
    assert search_api_key({"primary": "glm"}) == ""
    assert search_api_key({}) == ""


def test_blank_key_is_treated_as_missing():
    """空字符串 / 纯空白不能被当成有效 key——否则会拿空 Bearer 去打搜索接口，
    拿到一个 401 然后被当成「没有新闻」，结论错误但看不出异常。"""
    assert search_api_key({"m31_flash": {"api_key": "  "}}) == ""
    assert search_api_key({"m31_flash": {"api_key": ""}}) == ""


def test_search_client_targets_new_domain():
    """搜索端点必须是 `api.minimax.cn`。旧的 `api.minimaxi.com` 是国内端点，
    与 M3.1 的域名不一致，留着会静默 404。"""
    from aplus.news import MiniMaxSearch

    c = MiniMaxSearch("sk-x")
    assert c.base_url == "https://api.minimax.cn"