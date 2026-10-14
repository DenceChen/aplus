"""`/api/weights` 必须把「回落默认权重」的真相递到前端（2026-10-14）。

## 起因：一个**已测量的坏配置**正在给用户出 10 涨 10 跌

`load_weights` 在 `weights.json` 缺失时回落到 `DEFAULT_WEIGHTS`，并返回
一段 `caveat`：

    回落默认权重：未经任何赛马测量。它是 DEFAULT_WEIGHTS=
    {mom20:1.0,rev5:0.3,vol20:0.5,vsur:0.5}，在 2026-10-14 的 730 天赛马里
    排第 81/87 名，测试段超额 −0.74%、t=−3.03 —— 这是**已知的坏配置**，
    不是「中性起点」。日频执行它的年成本量级 351%。

但 `/api/weights` 的路由**把这句 caveat 整个丢掉了**，只回
`source: 'default'`。前端于是显示「未经赛马验收的默认值」——
听起来像「还没验证过」，而真相是「**验证过，是坏的**」。

## 更严重的一处：execution 参数根本没返回

同一段代码连 `execution` 一起丢了。而执行参数（`rebalance_every` /
`buffer_multiple`）不是装饰——台账第 19 条实测（2019-2023，纯反转）：

    | K | 年成本 | 净       |
    |---|--------|----------|
    | 1 | 246.3%  | −168.9%  |
    |63 |  12.2%  | +105.7%  |

**276 个百分点的差距**全部来自这一个参数。回落时它固定是 K=1，
也就是说线上的实际成本量级是 351%/年。前端看不到这个数，
用户以为「每天出一次单」是免费的。

## 为什么这三条都写成测试

这三条都是**静默失败**：HTTP 200、字段类型正确、前端不报错。
`tests/test_webapp.py::test_status_endpoint` 只断言 `"stats" in body`
——那种松断言正是它们能活到今天的原因。这里逐字段断言内容。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aplus.evolve import DEFAULT_WEIGHTS
from aplus.webapp import create_app


@pytest.fixture
def client(tmp_path):
    """空 data_root —— 没有 weights.json，走回落路径。

    同时把 `tmp_path` 一并返回：`app.state` 上**没有**存 data_root
    （`webapp.py` 只挂了 `app.state.llm`），所以要自己留着路径，
    才能和 `load_weights` 的产出逐字比对。
    """
    return TestClient(create_app(tmp_path, llm=None)), tmp_path


def test_fallback_must_expose_the_caveat(client):
    """回落时必须把 caveat 递出去。"""
    tc, _ = client
    body = tc.get("/api/weights").json()
    assert "caveat" in body, "/api/weights 丢弃了 load_weights 的 caveat"
    assert body["caveat"], "caveat 不得为空串——空串等于「没有警告」"
    assert "已知的坏配置" in body["caveat"], (
        f"caveat 必须写明这是**已测量的坏配置**，实得：{body['caveat'][:80]}")


def test_fallback_must_expose_execution_parameters(client):
    """回落时必须返回 execution——它决定成本量级（K=1 是 351%/年）。"""
    tc, _ = client
    body = tc.get("/api/weights").json()
    assert "execution" in body, "/api/weights 丢弃了 execution 参数"
    exec_ = body["execution"]
    assert "rebalance_every" in exec_, "缺 rebalance_every（K 值）"
    assert "buffer_multiple" in exec_, "缺 buffer_multiple"
    assert exec_["rebalance_every"] == 1, (
        "回落路径固定日频（K=1）；测试要钉住这个值，"
        "因为台账第 19 条实测日频与月频差 276 个百分点")


def test_source_must_distinguish_fallback_from_measured_default(client):
    """`source` 必须区分「赛马落盘的」与「回落未测量的」。

    原来路由写的是 `"evolved" if loaded["evidence"] else "default"`——
    它把「回落默认权重」显示成 `default`，与「有一份初始默认值配置」同义。
    而 `load_weights` 自己区分得更准：`file` vs `fallback`。
    """
    tc, root = client
    body = tc.get("/api/weights").json()

    assert body["source"] in ("fallback", "evolved", "file"), \
        f"source 必须能区分回落与落盘，实得 {body['source']!r}"
    assert body["source"] == "fallback", (
        "没有 weights.json 时必须是 fallback，不能是 default —— "
        "`default` 读起来像「一份已知的默认配置」，而它其实是「什么都没测过」")
    assert body["weights"] == DEFAULT_WEIGHTS


def test_promoted_weights_must_not_carry_the_fallback_caveat(tmp_path):
    """一旦有 weights.json 落盘，caveat 必须消失。

    反方向测试：防止「caveat 永远非空」这种把所有情形都标成警告的实现。
    """
    from aplus.evolve import save_weights

    reports = tmp_path / "reports"
    save_weights(reports, {"mom20": -1.0}, {"promoted_on": "2026-10-14"},
                 execution={"rebalance_every": 63, "buffer_multiple": None})
    body = TestClient(create_app(tmp_path, llm=None)).get("/api/weights").json()

    assert body["source"] in ("evolved", "file")
    assert not body.get("caveat"), "落盘权重不该再带回落警告"
    assert body["evidence"]["promoted_on"] == "2026-10-14"


def test_api_caveat_must_match_the_one_load_weights_produced(client):
    """路由不许改写 caveat 的措辞——它是一次实测的完整记录。

    这条钉住「路由逐字透传」，防止将来有人为了「简洁」把它截短成
    「未经验证」——那就把「已知为坏」降级成了「未知」，方向完全相反。
    """
    from aplus.evolve import load_weights

    tc, root = client
    body = tc.get("/api/weights").json()
    loaded = load_weights(Path(root) / "reports")

    assert body["caveat"] == loaded["caveat"]
