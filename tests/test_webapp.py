"""Web 应用 API 测试：TDD RED 先行。

create_app(data_root, llm=None) 工厂注入数据目录与 LLM 客户端（测试用假客户端）。
"""
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import aplus.webapp as webapp_mod
from aplus.reports import save_allocation, save_predictions
from aplus.webapp import create_app


class FakeLLM:
    """假模型：回显系统提示里的平台状态（验证 grounding 注入）。

    `chat_stream` 与 `chat` 一起实现：生产代码现在要求链上的客户端**两样都有**
    （`aplus.llm.FallbackChain` / `GlmProxyClient` / `MiniMaxM31FlashClient` 都是）。
    替身只实现一半的话，真去调流式端点会得到 `AttributeError`——
    那看起来像「测试坏了」，实际是替身缺了契约。
    """

    def __init__(self):
        self.last_system = ""
        self.last_messages = []

    def chat(self, messages, system=""):
        self.last_system = system
        self.last_messages = messages
        return f" Echo {len(messages)} messages. Top: {system.splitlines()[0][:40] if system else 'none'}"

    def chat_stream(self, messages, system=""):
        self.last_system = system
        self.last_messages = messages
        yield "text", f" Echo {len(messages)} messages. Top: " \
            f"{system.splitlines()[0][:40] if system else 'none'}"


@pytest.fixture
def client(tmp_path):
    reports = tmp_path / "reports"  # 与 run_daily 布局一致：data_root/reports
    up = pd.DataFrame({
        "code": ["600641.SH"], "name": ["先导基电"], "direction": ["up"],
        "score": [2.35], "mom20": [0.42], "rev5": [0.0], "vol20": [0.2], "vsur": [1.0],
    })
    down = pd.DataFrame({
        "code": ["600076.SH"], "name": ["康欣新材"], "direction": ["down"],
        "score": [-0.91], "mom20": [-0.31], "rev5": [0.0], "vol20": [0.2], "vsur": [1.0],
    })
    save_predictions(reports, "2026-10-09", up, down)
    save_allocation(reports, "2026-10-09", {
        "capital": 50000, "positions": [
            {"code": "600641.SH", "name": "先导基电", "weight": 0.1, "amount": 5000, "score": 2.35}
        ],
        "cash_weight": 0.5, "rebalance": [], "risk_notes": ["单票止损 -8%"],
    })
    fake = FakeLLM()
    app = create_app(tmp_path, llm=fake)
    return TestClient(app), fake, tmp_path


def test_status_endpoint(client):
    tc, _, _ = client
    resp = tc.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["last_prediction_date"] == "2026-10-09"
    assert "stats" in body


def test_latest_predictions(client):
    tc, _, _ = client
    body = tc.get("/api/predictions/latest").json()
    assert body["date"] == "2026-10-09"
    assert body["up"][0]["name"] == "先导基电"
    assert body["down"][0]["direction"] == "down"


def test_latest_allocation(client):
    tc, _, _ = client
    body = tc.get("/api/allocation/latest").json()
    assert body["positions"][0]["amount"] == 5000
    assert "止损" in body["risk_notes"][0]


def test_chat_creates_conversation_with_memory(client):
    tc, fake, _ = client
    r1 = tc.post("/api/chat", json={"message": "今天预测了哪些股票？"}).json()
    assert r1["conversation_id"]
    assert "Echo" in r1["reply"]
    # 平台状态注入系统提示（grounding）
    assert "先导基电" in fake.last_system or "600641" in fake.last_system

    r2 = tc.post("/api/chat", json={"conversation_id": r1["conversation_id"], "message": "那它为什么涨？"}).json()
    assert r2["conversation_id"] == r1["conversation_id"]
    # 第二轮的消息列表包含第一轮问答（记忆）
    roles = [m["role"] for m in fake.last_messages]
    assert roles.count("user") >= 2
    assert "assistant" in roles


def test_chat_persists_history(client):
    tc, _, _ = client
    conv = tc.post("/api/chat", json={"message": "你好"}).json()["conversation_id"]
    tc.post("/api/chat", json={"conversation_id": conv, "message": "命中率多少"})
    msgs = tc.get(f"/api/conversations/{conv}").json()
    assert len(msgs) >= 4  # 两轮 = 4 条
    assert msgs[0]["role"] == "user"


def test_chat_llm_failure_returns_502_but_saves_user_message(client):
    tc, _, _ = client

    class BrokenLLM:
        """两个方法都必须抛：少一个，流式路径就会绕过故障守卫静默「成功」。

        `test_chat_llm_failure_returns_502_but_saves_user_message` 靠 chat() 的
        抛错维持 502 守卫；`chat_stream` 的抛错则让流式端点发 error 事件。
        """

        def chat(self, messages, system=""):
            raise ConnectionError("proxy down")

        def chat_stream(self, messages, system=""):
            raise ConnectionError("proxy down")
            yield  # pragma: no cover — 生成器：抛错发生在第一次迭代之前

    app = create_app(client[2], llm=BrokenLLM())
    tc2 = TestClient(app)
    resp = tc2.post("/api/chat", json={"message": "测试"})
    assert resp.status_code == 502
    assert "proxy" in resp.json()["detail"].lower() or "连接" in resp.json()["detail"]


def test_run_status_endpoint(tmp_path):
    from aplus.reports import write_run_status

    write_run_status(tmp_path / "reports", {"date": "2026-10-10", "status": "ok", "steps": {}})
    app = create_app(tmp_path, llm=FakeLLM())
    tc = TestClient(app)
    body = tc.get("/api/run-status").json()
    assert body["date"] == "2026-10-10"
    assert body["status"] == "ok"


def test_run_status_endpoint_missing(tmp_path):
    app = create_app(tmp_path, llm=FakeLLM())
    body = TestClient(app).get("/api/run-status").json()
    assert body["status"] == "never_run"


def test_backtest_endpoint(tmp_path):
    import pandas as pd
    from aplus.backtest import daily_series

    rows = pd.DataFrame({
        "pred_date": ["2026-07-01", "2026-07-01"],
        "direction": ["up", "down"],
        "result": ["hit", "miss"],
        "actual_pct": [0.02, -0.01],
        "market_pct": [0.0, 0.0],
    })
    (tmp_path / "reports").mkdir(exist_ok=True)
    rows.to_parquet(tmp_path / "reports" / "backtest.parquet", index=False)
    (tmp_path / "reports" / "backtest_summary.json").write_text(
        '{"overall_hit_rate": 0.45, "judged": 100}', encoding="utf-8"
    )
    app = create_app(tmp_path, llm=FakeLLM())
    body = TestClient(app).get("/api/backtest").json()
    assert body["summary"]["judged"] == 100
    assert len(body["daily"]) == 1
    assert body["daily"][0]["hit_rate"] == 0.5


def test_weights_endpoint_reports_current_strategy(tmp_path):
    app = create_app(tmp_path, llm=FakeLLM())
    body = TestClient(app).get("/api/weights").json()
    # 未进化过 → 回落到 DEFAULT_WEIGHTS。`mom20` 于 2026-10-14 由 +1.0
    # 翻成 −1.0（实测 IC 逐年全负），所以这里不再钉「1.0」这个魔数，
    # 改为引用真实常量——复制默认值就是本文件 2026-10-14 修掉的毛病。
    from aplus.factor import DEFAULT_WEIGHTS
    assert body["weights"] == DEFAULT_WEIGHTS
    # `source` 于 2026-10-14 从 `default` 改成 `fallback`。
    # 原值把「weights.json 不存在、什么都没跑过」显示成 `default`，
    # 读起来像「一份已知的默认配置」——而 `load_weights` 自己区分得更准。
    # 前端据此展示 caveat（「已知的坏配置」），语义必须对得上。
    assert body["source"] in ("fallback", "evolved")
    assert body["source"] == "fallback", "tmp_path 没有 weights.json，必须是 fallback"


def test_predictions_history_endpoint(tmp_path):
    reports = tmp_path / "reports"
    up = pd.DataFrame({"code": ["600000.SH"], "name": ["浦发银行"], "direction": ["up"],
                       "score": [1.5], "mom20": [0.1], "rev5": [0.0], "vol20": [0.2], "vsur": [1.0]})
    down = pd.DataFrame({"code": ["600001.SH"], "name": ["乙"], "direction": ["down"],
                         "score": [-1.5], "mom20": [-0.1], "rev5": [0.0], "vol20": [0.2], "vsur": [1.0]})
    save_predictions(reports, "2026-10-09", up, down)
    app = create_app(tmp_path, llm=FakeLLM())
    body = TestClient(app).get("/api/predictions/history").json()
    assert body["dates"] == ["2026-10-09"]
    assert len(body["rows"]) == 2
    row = body["rows"][0]
    assert row["code"] == "600000.SH"
    assert row["result"] is None  # 尚未验证


def test_predictions_latest_survives_nan_factor_columns(tmp_path):
    """真实回归（2026-10-10 19:31 预测）：turn20 列对无换手数据的股票是 NaN，
    starlette 拒绝 NaN → 500。所有 DataFrame→JSON 出口必须 NaN→None。"""
    reports = tmp_path / "reports"
    up = pd.DataFrame({"code": ["600000.SH"], "name": ["浦发银行"], "direction": ["up"],
                       "score": [1.5], "mom20": [0.1], "rev5": [0.0], "vol20": [0.2], "vsur": [1.0],
                       "turn20": [float("nan")]})
    down = pd.DataFrame({"code": ["600001.SH"], "name": ["乙"], "direction": ["down"],
                         "score": [-1.5], "mom20": [-0.1], "rev5": [0.0], "vol20": [0.2], "vsur": [1.0],
                         "turn20": [2.1]})
    save_predictions(reports, "2026-10-10", up, down)
    app = create_app(tmp_path, llm=FakeLLM())
    resp = TestClient(app).get("/api/predictions/latest")
    assert resp.status_code == 200
    assert resp.json()["up"][0]["turn20"] is None
    assert resp.json()["down"][0]["turn20"] == 2.1


def test_predictions_history_empty(tmp_path):
    app = create_app(tmp_path, llm=FakeLLM())
    body = TestClient(app).get("/api/predictions/history").json()
    assert body == {"rows": [], "dates": []}


def test_evolution_endpoint_empty_returns_empty_list(tmp_path):
    app = create_app(tmp_path, llm=FakeLLM())
    body = TestClient(app).get("/api/evolution").json()
    assert body == []


# ---------- 可转债纸面账户端点（路线 A 第四阶段） ----------

def test_bond_paper_endpoint_when_absent_returns_empty_not_error(client):
    """账户尚未开张时也必须 200 + 明确 empty 状态——前端据此显示「待命」。"""
    tc, _, _ = client
    resp = tc.get("/api/bond-paper")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["status"] == "empty"
    assert body["curve"] == []
    assert body["latest"] == {}


def test_bond_paper_endpoint_exposes_curve_and_headline(tmp_path):
    from aplus.bond_paper import BondPaperAccount, summarize_paper
    from aplus.bond_horizon import build_horizon_cache
    from aplus.reports import (
        save_bond_paper, write_bond_paper_state, write_bond_paper_summary,
    )
    from aplus.bond_sync import load_bond_map
    import sys
    sys.path.insert(0, str(tmp_path))  # 仅为可读性，无副作用

    reports = tmp_path / "reports"
    state = {"created": "2026-09-01", "start": "2026-09-01", "hold_days": 5,
             "top_n": 10, "cost": 0.003, "phases": [0], "random_seeds": [1]}
    write_bond_paper_state(reports, state)

    # 用真实转债库造一段样本外（库为空时跳过断言而非失败）
    bond_map = load_bond_map(Path(__file__).resolve().parents[1] / "data")
    if not bond_map:
        pytest.skip("转债库为空（CI 环境），端点结构由上一个用例覆盖")
    cache = build_horizon_cache(bond_map)
    rows = BondPaperAccount(hold_days=5, top_n=10, cost=0.003,
                            phases=[0], random_seeds=[1]).run(
        cache, "2026-09-01", "9999-12-31")
    save_bond_paper(reports, rows)
    write_bond_paper_summary(reports, summarize_paper(rows, state))

    tc = TestClient(create_app(tmp_path, llm=FakeLLM()))
    body = tc.get("/api/bond-paper").json()
    assert body["summary"]["as_of"]
    assert body["curve"], "净值曲线不能为空"
    assert body["latest"]["arm"] == "cheap_p0"
    assert "n_positions" in body["latest"]


def test_predictions_with_nan_in_string_column_is_json_safe(tmp_path):
    """RED 回归：str dtype 列里的 NaN 曾让 /api/predictions/latest 线上 500。

    生产 2026-10-11 的预测 parquet 里 news_reason / news_query 全是 NaN，
    但列 dtype 是 str —— 旧的 _json_safe 只清洗 float dtype 列，字符串列的
    NaN 直漏到 JSON，starlette 拒绝 → Internal Server Error，概览页预测区空白。
    """
    reports = tmp_path / "reports"
    up = pd.DataFrame({
        "code": ["600825.SH"], "name": ["新华传媒"], "direction": ["up"],
        "score": [7.2], "mom20": [0.31], "rev5": [0.0], "vol20": [0.2], "vsur": [1.0],
        "news_sentiment": [float("nan")],
        "news_reason": [float("nan")],   # ← str dtype 列装 NaN，真实线上形状
        "news_query": [float("nan")],
    })
    down = pd.DataFrame({
        "code": ["600076.SH"], "name": ["康欣新材"], "direction": ["down"],
        "score": [-0.91], "mom20": [-0.31], "rev5": [0.0], "vol20": [0.2], "vsur": [1.0],
        "news_sentiment": [0.0], "news_reason": ["无消息"], "news_query": ["600076"],
    })
    save_predictions(reports, "2026-10-11", up, down)

    tc = TestClient(create_app(tmp_path, llm=FakeLLM()))
    resp = tc.get("/api/predictions/latest")
    assert resp.status_code == 200, f"NaN 未被清洗 → {resp.status_code}"
    body = resp.json()
    assert body["up"][0]["news_reason"] is None
    assert body["up"][0]["news_sentiment"] is None
    assert body["down"][0]["news_reason"] == "无消息"  # 真实值不得被误伤


def test_json_safe_cleans_every_dtype(tmp_path):
    """单元级：_json_safe 对 object/float/str/int 各 dtype 都不得漏 NaN。"""
    from aplus.webapp import _json_safe

    df = pd.DataFrame({
        "f": [1.5, float("nan")],
        "i": [1, 2],
        "o": ["x", None],
        "s": [float("nan"), "y"],
        "b": [True, False],
    })
    recs = _json_safe(df)
    import json as _json
    for rec in recs:
        _json.dumps(rec, allow_nan=False)  # 任何残留 NaN 都在这里炸
    assert recs[0]["s"] is None and recs[0]["f"] == 1.5


# ══════════════════════════════════════════════════════════════════════════
# 甲（部署层）：响应必须**声明**缓存策略
# 预注册 docs/PREREG_docket_visibility_2026-10-18.md §3.1
#
# 🚨 这三条守卫**一条都不计数**（开工闸 Q4 已登记）：它们钉的是「响应头」，
# 而 2026-10-18 的真实失效形态是「**浏览器里跑的是旧字节**」——
# 那个只有 `performance.getEntriesByType('resource')` 能验，源码形状覆盖不了。
# ══════════════════════════════════════════════════════════════════════════


def test_every_response_declares_a_cache_policy(tmp_path):
    """主守卫：真打生产 app，响应头里有 cache-control 且等于模块级常量。

    ⚠️ **不读源码**（第 96 层）：`grep add_middleware webapp.py` 只能证明
    「有人写了这行字」，证不出它被 `create_app` 真的装上、也证不出它对
    `StaticFiles` 发出的响应生效。要判的是**响应**，不是**字面**。
    """
    c = TestClient(create_app(tmp_path, llm=FakeLLM()))
    for path in ("/verdict.js", "/api/status"):
        r = c.get(path)
        assert r.status_code == 200, f"{path} → status_code={r.status_code}"
        assert "cache-control" in r.headers, (
            f"{path} 没有声明 Cache-Control ⇒ 按 RFC 9111 §4.2.2，浏览器"
            f"**必须**用 10%×(Date−Last-Modified) 猜新鲜度。"
            f"「没写策略」不等于「不缓存」，而是授权浏览器猜。"
        )
        assert r.headers["cache-control"] == webapp_mod.CACHE_CONTROL_VALUE


def test_verdict_js_serves_the_bytes_on_disk_and_not_a_fallback_page(tmp_path):
    """能力守卫（第 120 层）：确认 `/verdict.js` 返回的**就是盘上那份字节**。

    🚨 没有这一条，上面那条主守卫的 `200` 可能来自 `StaticFiles` 在 `/` 上的
    catch-all 兜底页（那里有近 80 行中文注释，任何子串都能命中）⇒
    「缓存控制加在谁身上」这件事会被测空。
    ⚠️ 夹具必须能区分「东西不存在」与「东西是别的」，所以这里比的是**整份字节**。
    """
    c = TestClient(create_app(tmp_path, llm=FakeLLM()))
    r = c.get("/verdict.js")
    assert r.status_code == 200
    on_disk = (Path(webapp_mod.__file__).parent / "web" / "verdict.js").read_text("utf-8")
    assert r.text == on_disk, "/verdict.js 的响应体与盘上字节不一致 ⇒ 取到的不是它"
    assert "d2LedgerVerdict" in r.text, (
        "取到的是 10-18 那份化石副本（顶层只有 4 个函数、无 d2LedgerVerdict）——"
        "浏览器当时执行的就是它，而页面**不报任何错**"
    )


def test_a_bare_app_serves_the_same_file_with_no_cache_header_at_all(tmp_path):
    """反控（第 120 层）：**同一个静态目录 + 裸 FastAPI ⇒ 必须没有 cache-control**。

    🚨 这是让主守卫长出牙齿的那一条。若裸 app 也带上了 cache-control，
    那主守卫测的就不是中间件，而是 ASGI/`StaticFiles` 自己的行为 ——
    而「恒真的守卫」与「守住了的守卫」在门禁输出里长得**逐位相同**。
    ⚠️ 静态目录必须锚到**模块自己的** `webapp.__file__`：
    `webapp.py:935` 的 `Path(__file__).parent / "web"` 里那个 `__file__`
    是**模块**的；写成测试文件的 `__file__` 会解析成 `tests/web`（不存在）
    ⇒ 什么都没挂上，而探针会「跑通了」。
    """
    web_dir = Path(webapp_mod.__file__).parent / "web"
    assert web_dir.is_dir(), f"静态目录锚错了：{web_dir}"
    bare = FastAPI()
    bare.mount("/", StaticFiles(directory=web_dir, html=True), name="static")
    r = TestClient(bare).get("/verdict.js")
    assert r.status_code == 200
    assert "d2LedgerVerdict" in r.text, "裸 app 没挂到同一个目录 ⇒ 反控的前提错了"
    assert "cache-control" not in r.headers, (
        "裸 app 也带上了 Cache-Control ⇒ 主守卫那条是恒真的，"
        "它守的不是中间件而是框架自己的默认行为"
    )


def test_the_middleware_fills_an_undeclared_policy_but_never_overwrites_one(tmp_path):
    """反控：`NoCacheMiddleware` 的**只填空不覆盖**语义。

    它的判据是 `if "cache-control" not in headers`（webapp.py:368）⇒
    已声明策略的响应原样保留。这条守卫把那句 docstring 变成一个会响的性质。

    🚨 探针路由**不能**用 `app.get(...)`：`webapp.py:936` 的
    `app.mount("/", StaticFiles(...))` 是 catch-all，会把后加的路由吃成 404。
    而 `add_middleware` 在 :387 已注册 ⇒ 中间件栈在**第一次请求时**构建
    ⇒ 后加的路由**照样**被包裹。所以两条要同时成立：`insert(0)` 抢在 mount 前，
    且断言它在包裹之内仍保得住自己的头。
    """
    app = create_app(tmp_path, llm=FakeLLM())

    async def _cache_probe(request):
        return PlainTextResponse("ok", headers={"cache-control": "no-store"})

    app.router.routes.insert(0, Route("/__cache_probe", _cache_probe, methods=["GET"]))
    r = TestClient(app).get("/__cache_probe")
    assert r.status_code == 200, "探针路由没挂上（被 StaticFiles 的 catch-all 吃掉了）"
    assert r.text == "ok"
    assert r.headers["cache-control"] == "no-store", (
        f"端点自己声明的策略被中间件顶成了 {r.headers['cache-control']!r} "
        f"⇒ 「只填空不覆盖」这条语义已经没了"
    )
