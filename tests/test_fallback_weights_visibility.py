"""`/api/weights` 必须把「回落默认权重」的真相递到前端（2026-10-14）。

## 起因：一个**已测量的坏配置**正在给用户出 10 涨 10 跌

`load_weights` 在 `weights.json` 缺失时回落到 `DEFAULT_WEIGHTS`，并返回
一段 `caveat`：

    回落默认权重：未经任何赛马测量。它是 DEFAULT_WEIGHTS=
    {mom20:1.0,rev5:0.3,vol20:0.5,vsur:0.5}，在 2026-10-14 的 730 天赛马里
    排第 81/87 名，测试段超额 −0.74%、t=−3.03 —— 这是**已知的坏配置**，
    不是「中性起点」。日频执行它的年成本量级 351%。

（**2026-10-14 更新**：默认权重的符号已按实测 IC 整体翻转，上面这段是
**翻转前**的配置与它的实测结论。现在在跑的权重是未经赛马验证的新配置，
caveat 已改写为「方向有证据、效果未验证」，并显式标注 81/87 属于旧配置。
原文保留，因为**它记录了本文件要修的那个 bug 是怎么发生的**。）

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
    """回落时必须把 caveat 递出去。

    ## 断言从「已知的坏配置」改成「证据状态」（2026-10-14 默认权重翻转后）

    本条原本要求 caveat 含「已知的坏配置」。那句话**对旧配置成立**
    （`{mom20:+1.0,...}` 确实在 730 天赛马里排 81/87、t=−3.03），
    对翻转后的新配置**不成立**——新配置符号按实测 IC 校正过，但根本没被
    赛马测过。把旧配置的实测结论挂到新配置头上就是撒谎，而撒谎的代价正是
    这份 caveat 存在的理由。

    警示的职责是**如实陈述当前在跑的那组权重的证据状态**。状态变了：
    从「已测量的坏」变成「方向有证据、效果未验证」。两者都是危险状态，
    只是不同的危险——所以下面两条分别钉住「未验收」和「不得冒领旧数字」。
    """
    tc, _ = client
    body = tc.get("/api/weights").json()
    assert "caveat" in body, "/api/weights 丢弃了 load_weights 的 caveat"
    assert body["caveat"], "caveat 不得为空串——空串等于「没有警告」"
    assert "未经赛马验证" in body["caveat"], (
        f"caveat 必须写明这组权重**未经赛马验证**，实得：{body['caveat'][:80]}")


def test_caveat_must_not_claim_the_old_measurement_belongs_to_the_current_weights(client):
    """「排 81/87、t=−3.03」是**旧权重**的实测值，不得挂到新权重头上。

    这是本文件最要紧的一条：把一段真实但不相关的实测数字说成当前配置的
    实绩，比没有数字更坏——它会让用户以为「已经验过了，结论是不好」，
    而真实状态是「根本没验过」。两种谎言导致完全不同的下一步动作。
    """
    tc, _ = client
    caveat = tc.get("/api/weights").json()["caveat"]
    assert "81/87" in caveat, (
        "应保留旧配置的实测记录作为背景——删掉它等于丢失已知信息")
    assert "属于**旧配置**" in caveat, (
        "出现别的配置的实测数字时，必须显式标注它属于旧配置。"
        f"实得：{caveat[:200]}")


def test_fallback_must_expose_execution_parameters(client):
    """回落时必须返回 execution——它决定成本量级。

    ⚠️ 2026-10-15 起这个值是 **21** 而不是 1。

    原来钉 K=1 的理由是「台账第 19 条实测日频与月频差 276 个百分点」——
    那条论据**今天被 `k_sweep_summary.json` 正面推翻了**：K 扫描五档
    （5/10/21/42/63）在 6 年实测下**全部**够不到预注册门槛，
    而 K=1 那一档的成本量级与毛超额同数量级甚至更大
    （K=5 成本 26.84%/年 vs 毛超额 9.78%/年）。

    ⇒ 继续按日频回落不是「保守」，是**结构性不可能盈利**。
    换仓周期定为 21（`LIVE_REBALANCE_EVERY`），理由见 `aplus/evolve.py` 同名常量。
    """
    from aplus.evolve import LIVE_REBALANCE_EVERY

    tc, _ = client
    body = tc.get("/api/weights").json()
    assert "execution" in body, "/api/weights 丢弃了 execution 参数"
    exec_ = body["execution"]
    assert "rebalance_every" in exec_, "缺 rebalance_every（K 值）"
    assert "buffer_multiple" in exec_, "缺 buffer_multiple"
    assert exec_["rebalance_every"] == LIVE_REBALANCE_EVERY == 21, (
        f"回落路径下发 K={exec_['rebalance_every']}，应为 {LIVE_REBALANCE_EVERY}。"
        "改这个值必须同时改 aplus/evolve.py 的注释表与 k_sweep_summary 的依据")


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
