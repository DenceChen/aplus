"""LLM 故障转移链测试：TDD RED 先行。

主链 glm-failover（5 账号轮询），备链 MiniMax（额度大能力弱）。
谁在服务必须可观测（哪个链路服务了本次请求）。
"""
import pytest

from aplus.llm import FallbackChain, load_secrets


class FakeClient:
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


def test_primary_serves_when_healthy():
    primary = FakeClient("glm", reply="强模型回答")
    backup = FakeClient("minimax", reply="备模型回答")
    chain = FallbackChain(primary, backup)
    reply = chain.chat([{"role": "user", "content": "hi"}])
    assert reply == "强模型回答"
    assert chain.last_served_by == "glm"
    assert backup.calls == 0  # 备链未被触碰


def test_backup_serves_when_primary_fails():
    primary = FakeClient("glm", error=ConnectionError("全部 key 冷却"))
    backup = FakeClient("minimax", reply="备模型回答")
    chain = FallbackChain(primary, backup)
    reply = chain.chat([{"role": "user", "content": "hi"}])
    assert reply == "备模型回答"
    assert chain.last_served_by == "minimax"


def test_both_fail_raises_last_error():
    primary = FakeClient("glm", error=ConnectionError("proxy down"))
    backup = FakeClient("minimax", error=TimeoutError("minimax timeout"))
    chain = FallbackChain(primary, backup)
    with pytest.raises(TimeoutError, match="minimax timeout"):
        chain.chat([{"role": "user", "content": "hi"}])


def test_load_secrets_reads_home_config(tmp_path, monkeypatch):
    import json

    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps({"minimax": {"api_key": "sk-x", "model": "MiniMax-M3"}}), encoding="utf-8")
    secrets = load_secrets(cfg)
    assert secrets["minimax"]["api_key"] == "sk-x"


def test_load_secrets_missing_file_returns_empty(tmp_path):
    assert load_secrets(tmp_path / "nope.json") == {}


def test_default_chain_primary_order_from_config(tmp_path):
    """2026-10-14 路线：M3.1 主 / glm 备——M3 完全不装配到默认链。

    由 ~/.aplus/llm.json 的 primary 字段决定，改配置不改代码。
    """
    import json

    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps({
        "primary": "m31-flash",
        "m31_flash": {"api_key": "sk-x", "base_url": "https://api.minimax.cn/anthropic",
                       "model": "MiniMax-M3.1-Flash"},
    }), encoding="utf-8")

    from aplus.llm import default_llm_chain

    chain = default_llm_chain(secrets_path=cfg)
    assert chain.primary.name == "minimax-m31-flash"
    assert chain.backup.name == "glm-proxy"

    cfg.write_text(json.dumps({
        "primary": "glm",
        "m31_flash": {"api_key": "sk-x"},
    }), encoding="utf-8")
    chain2 = default_llm_chain(secrets_path=cfg)
    assert chain2.primary.name == "glm-proxy"


def test_default_chain_without_m31_falls_back_to_glm(tmp_path):
    """未配 m31_flash key 时 → glm 自兜底（不装配 M3）。"""
    import json

    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps({"primary": "m31-flash"}), encoding="utf-8")

    from aplus.llm import default_llm_chain

    chain = default_llm_chain(secrets_path=cfg)
    assert chain.primary.name == "glm-proxy"


def test_default_chain_m31_only_no_m3_in_chain(tmp_path):
    """2026-10-14 用户硬要求：默认链不出现 M3（minimax 客户端）。"""
    import json

    cfg = tmp_path / "llm.json"
    cfg.write_text(json.dumps({
        "primary": "m31-flash",
        "m31_flash": {"api_key": "sk-x"},
        "minimax": {"api_key": "sk-y"},   # 故意也配 M3，但链不应使用它
    }), encoding="utf-8")

    from aplus.llm import default_llm_chain

    chain = default_llm_chain(secrets_path=cfg)
    chain_names = [getattr(chain.primary, 'name', '?'),
                   getattr(chain.backup, 'name', '?')]
    assert "minimax" not in chain_names, (
        f"M3 不应在默认链里出现，实际链：{chain_names}")
    assert "minimax-m31-flash" in chain_names
