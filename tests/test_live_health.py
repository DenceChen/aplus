"""判决板必须报出**线上这组权重在样本外到底赚没赚钱**。

## 事故（2026-10-11 18:50）

ivol 前视泄露修好后重跑 31 组终裁，得到一个此前被掩盖的事实：

线上当时正在跑的默认权重 `{mom20:1.0, rev5:0.3, vol20:0.5, vsur:0.5}`
（注：这组符号已于 2026-10-14 按实测 IC 整体翻转——`factor_ic_7y.json`
显示这三个因子的 IC 逐年全为负。本事故的记录保留原值，因为**测出
这个负数的正是当时那组错号权重**；新符号的那一组尚未做同样的验收。）
在 2 年样本外测试段：

- 涨腿超额 **−0.5978%/日**
- `excess_t = −2.83`、`breadth_t = −3.39`
- 命中率 **47.46%**（低于抛硬币）
- 涨篮子平均收益 −0.66%

叠加日频 34.22% 的年化换手成本，是**负负**。

而当时的判决板只写「0 次晋级 / 未验收」。用户看到的画面是
「候选全被否决、保持现状」——听上去像「谨慎地维持一个还行的策略」，
完全看不出**维持的那个东西本身显著为负**。

`live_validated=False` 说的是「没验收过」，没有说「验过了，是负的」。
这两句话对使用者的含义天差地别。

## 修法

判决板直接从**最新一份赛马报告**里读出现任权重的样本外表现，
并显式区分三种状态：已验收为正 / 已验收为负 / 从未验收。
"""
import json

from fastapi.testclient import TestClient

from aplus.webapp import create_app


def _setup(tmp_path, weights=None, race_table=None):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "evolution_log.json").write_text(
        json.dumps([{"date": "2026-10-11",
                     "decision": {"promoted": False, "reason": "显著性否决"},
                     "evaluations": []}]), encoding="utf-8")
    if weights is not None:
        (reports / "weights.json").write_text(json.dumps(weights), encoding="utf-8")
    if race_table is not None:
        (reports / "race_final_2y_v3.json").write_text(
            json.dumps(race_table), encoding="utf-8")
    return TestClient(create_app(data_root=tmp_path))


# **引用真实的 DEFAULT_WEIGHTS，不复制一份**（2026-10-14 修）。
# 复制的那份会与线上配置悄悄脱钩：默认权重翻转符号后，本文件仍写
# `{mom20:+1.0,...}`，而 `champion_health` 按真实默认权重找冠军 →
# 找不到 → 判决板退回 `unknown` / `champion_falsified=False`。
# 三条测试同时变红，读起来像「判决板坏了」，实际是测试在验一个
# 世界上已经不存在的配置。
from aplus.factor import DEFAULT_WEIGHTS as DEFAULT_W


def _race(weights, excess, t, hit):
    return {"window": ["2024-10-01", "2026-10-09"],
            "champion": weights,
            "table": [{"train": {"stats": {}, "objective": 0, "n": 0},
                       "test": {"stats": {"up_excess_vs_market": excess,
                                          "overall_hit_rate": hit,
                                          "excess_t": t,
                                          "breadth_t": t - 0.5},
                                "objective": excess, "n": 1940},
                       "oos_edge": 0.0,
                       "weights": weights}]}


def test_reports_live_weights_oos_performance(tmp_path):
    c = _setup(tmp_path, weights={"weights": DEFAULT_W, "source": "default"},
               race_table=_race(DEFAULT_W, -0.005978, -2.831, 0.4746))
    s = c.get("/api/evolution/summary").json()
    assert s["live_oos_t"] is not None and s["live_oos_t"] < -1.96
    assert s["live_oos_excess"] < 0
    assert abs(s["live_oos_hit"] - 0.4746) < 1e-3


def test_flags_live_weights_significantly_negative(tmp_path):
    """t < −1.96 必须显式标为 significantly_negative，界面据此报警。"""
    c = _setup(tmp_path, weights={"weights": DEFAULT_W, "source": "default"},
               race_table=_race(DEFAULT_W, -0.005978, -2.831, 0.4746))
    s = c.get("/api/evolution/summary").json()
    assert s["live_oos_verdict"] == "significantly_negative"


def test_flags_live_weights_significantly_positive(tmp_path):
    c = _setup(tmp_path,
               weights={"weights": DEFAULT_W, "source": "evolved",
                        "evidence": {"objective": 0.2, "promoted_on": "2026-10-11"}},
               race_table=_race(DEFAULT_W, 0.0091, 3.4, 0.56))
    s = c.get("/api/evolution/summary").json()
    assert s["live_oos_verdict"] == "significantly_positive"


def test_inconclusive_when_t_below_threshold(tmp_path):
    """t 在 ±1.96 内 = 与 0 不可区分，不能报成正或负。"""
    c = _setup(tmp_path, weights={"weights": DEFAULT_W, "source": "default"},
               race_table=_race(DEFAULT_W, 0.0006, 0.91, 0.51))
    s = c.get("/api/evolution/summary").json()
    assert s["live_oos_verdict"] == "inconclusive"


def test_no_race_report_leaves_oos_unknown(tmp_path):
    """没有赛马报告时必须说 unknown，不能静默当成没问题。"""
    c = _setup(tmp_path, weights={"weights": DEFAULT_W, "source": "default"})
    s = c.get("/api/evolution/summary").json()
    assert s["live_oos_verdict"] == "unknown"
    assert s["live_oos_t"] is None


def test_healthchecks_default_weights_when_never_promoted(tmp_path):
    """`weights.json` 从未生成时，体检**回退默认值**——那才是真正在出单的那组。

    2026-10-11 的真实情况：9 轮赛马 0 次晋级，`weights.json` 不存在。
    第一版判决板于是 `live_weights=None` → `live_oos_verdict=unknown`，
    于是「现任显著为负」这条最该被看见的事实**又一次被结构性地藏起来了**。

    缺陷的形状值得记住：**用「晋级结果文件」代表「线上在跑什么」，
    在从未晋级过时正好把线上真实在跑的东西变成空。**
    """
    c = _setup(tmp_path)          # 不写 weights.json
    (tmp_path / "reports" / "race_aaa.json").write_text(
        json.dumps(_race(DEFAULT_W, -0.005978, -2.831, 0.4746)), encoding="utf-8")
    s = c.get("/api/evolution/summary").json()
    assert s["weights_exist"] is False, "本测试的前提就是没有晋级文件"

    # ★ 本测试的核心断言是 `!= "unknown"`，不是某个具体的判决词。
    # 2026-10-15 实盘换仓周期改成 K=21 之后，赛马表里只有 K=1 那一行权重相同，
    # 三元组匹配不上 ⇒ 判决词从 `significantly_negative` 变成
    # **`mismatched_execution`**（而不是 `unknown`）。
    # 这个变化是**对的**：K=1 的 t 值确实不适用于线上跑的 K=21。
    #
    # ⚠️ 若这里退回 `unknown`，就是 2026-10-11 那起事故原地复发 ——
    # 「现任显著为负」这条最该被看见的事实被结构性地藏起来。
    assert s["live_oos_verdict"] != "unknown", (
        "判决再次变成 unknown —— 与 2026-11-11 同一起事故复发"
    )
    assert s["live_oos_verdict"] == "mismatched_execution", (
        f"实判决是 {s['live_oos_verdict']!r}；"
        "权重相同但执行参数不同应当报 mismatched_execution"
    )
    # t 值必须照报 —— 它仍然是最刺眼的那条事实
    assert s["live_oos_t"] < -1.96, (
        f"t={s['live_oos_t']}，样本外仍显著为负；这条数字不许被参数不匹配吞掉"
    )
    assert "K=" in (s.get("live_oos_note") or ""), (
        "必须说清差在哪一档 K —— 否则用户看到的是一个没有来由的陌生判决词"
    )


def test_weights_not_in_race_table_leaves_unknown(tmp_path):
    """权重对不上任何一行 → unknown。宁可说不知道，不许拿别的组的数据代替。"""
    other = {"mom20": -1.0}
    c = _setup(tmp_path, weights={"weights": DEFAULT_W, "source": "default"},
               race_table=_race(other, 0.02, 4.0, 0.6))
    s = c.get("/api/evolution/summary").json()
    assert s["live_oos_verdict"] == "unknown"


def test_survives_corrupt_race_report(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "race_final_2y_v3.json").write_text("{ broken", encoding="utf-8")
    c = TestClient(create_app(data_root=tmp_path))
    s = c.get("/api/evolution/summary").json()
    assert s["live_oos_verdict"] == "unknown"


def test_corrupt_newest_report_still_reads_older_valid_one(tmp_path):
    """最新一份损坏时，必须**跳过它**去读更早的有效报告，而不是整条挂掉。

    2026-10-11 的第一个版本在 `except` 里写的是 `raise`。它没被测出来，
    因为 `JSONDecodeError` 是 `ValueError` 的子类，被函数外层的
    `except (TypeError, ValueError)` 悄悄接住并返回 `blank`——
    于是一个损坏文件就等于「什么都读不到」。
    这里放「一份损坏 + 一份有效」，逼出真正的跳过语义。
    """
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "race_aaa.json").write_text(
        json.dumps(_race(DEFAULT_W, -0.005978, -2.831, 0.4746)), encoding="utf-8")
    (reports / "race_zzz.json").write_text("{ broken", encoding="utf-8")
    (reports / "weights.json").write_text(
        json.dumps({"weights": DEFAULT_W, "source": "default"}), encoding="utf-8")
    s = TestClient(create_app(data_root=tmp_path)).get("/api/evolution/summary").json()
    assert s["live_oos_verdict"] == "significantly_negative", "损坏的最新报告把有效数据一起吞了"


def test_picks_most_recently_written_report_not_lexicographic(tmp_path):
    """必须按**写入时间**取最新，不能按文件名字典序。

    ## 测试形状的坑（第一次写错了）

    第一版让两份报告装**不同**权重，靠「权重匹配」把旧的挡掉——
    于是字典序实现也能通过，变异体 H5 存活。那不是真测试。

    真正能区分的场景是：两份报告**都含**线上这组权重，但数值不同
    （早先跑的和刚跑的）。同一组权重、不同窗口/不同代码版本 → 不同结论，
    这正是必须取最新结果的理由。
    """
    import os
    import time
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    new = reports / "race_aaa.json"          # 新，但字典序最小
    stale = reports / "race_zzz.json"        # 旧，字典序最大
    # 同一组权重、同一条路径；旧报告说 +5%（乐观），新报告说 −0.6%（真实）
    new.write_text(json.dumps(_race(DEFAULT_W, -0.005978, -2.831, 0.4746)), encoding="utf-8")
    stale.write_text(json.dumps(_race(DEFAULT_W, 0.05, 5.0, 0.70)), encoding="utf-8")
    t = time.time() - 86400
    os.utime(stale, (t, t))                  # 明确标记为昨天写的
    (reports / "weights.json").write_text(
        json.dumps({"weights": DEFAULT_W, "source": "default"}), encoding="utf-8")
    s = TestClient(create_app(data_root=tmp_path)).get("/api/evolution/summary").json()
    assert s["live_oos_verdict"] == "significantly_negative", \
        "读到了旧报告（+5%）而不是刚跑出来的那份（−0.6%）"


# ── 现任是否被证伪 ────────────────────────────────────────────────
# 2026-10-11 20:09 的教训：六道闸只管「什么能进来」，不管「什么该被换掉」。
# 现任排第 10、没有候选过闸 → 裁决「维持现任」，看起来干净；
# 但它在两段独立窗口（752 个交易日）上全部显著为负 —— 是被证伪，不是不确定。

def _write_validation(reports, window, champion_test, champion_w):
    (reports / "independent_validation_2019_2023.json").write_text(json.dumps({
        "kind": "independent_validation", "window": window,
        "champion": champion_w,
        "rows": [{"weights": champion_w, "test": champion_test}],
    }), encoding="utf-8")


def test_champion_is_falsified_across_independent_windows(tmp_path):
    """两段互不重叠、现任都**显著**为负 → 必须判证伪。

    注意 fixture 里那三段的两段 t 分别是 −3.55 与 −2.83（都过 1.96），
    而 evolution_log 那一段 t=−0.37 **不显著**——它本来就不该计入。
    第一版把三段都放进 fixture 却期望证伪，是把「不显著的负」当成了
    「证伪的证据」。证伪要的是**显著**为负，否则只是没测出东西。
    """
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "evolution_log.json").write_text(json.dumps([
        {"date": "2026-10-11", "window": ["2025-12-16", "2026-10-08"],
         "decision": {"promoted": False},
         "evaluations": [{"weights": DEFAULT_W,
                          "stats": {"up_excess_vs_market": -0.0057,
                                    "excess_t": -0.37, "excess_days": 194}}]},
    ]), encoding="utf-8")
    (reports / "race_final_2y_v3.json").write_text(json.dumps({
        "window": ["2024-10-01", "2026-10-09"],
        "table": [{"weights": DEFAULT_W,
                   "test": {"stats": {"up_excess_vs_market": -0.0060,
                                      "excess_t": -2.83, "excess_days": 194}}}],
    }), encoding="utf-8")
    _write_validation(reports, ["2021-12-30", "2023-12-29"],
                      {"excess": -0.0049, "excess_t": -3.55, "days": 486}, DEFAULT_W)
    s = TestClient(create_app(data_root=tmp_path)).get("/api/evolution/summary").json()
    assert s["champion_falsified"] is True
    # 三段 → 去重后 2 段：evolution_log 的 [2025-12-16, 2026-10-08]
    # 被 race_v3 的 [2024-10-01, 2026-10-09] 完全包含，是重叠而非独立证据。
    assert s["champion_windows"] == 2


def test_independent_validation_report_is_not_silently_dropped(tmp_path):
    """独立验证报告也是 **dict**；写在 isinstance(dict) 之后会被静默吃掉。

    第一版把 `kind` 判断放在 `elif` 里，被前面的 `isinstance(data, dict)`
    抢先捕获 → `data.get("table")` 为空 → 那一段消失 →
    证伪判定永远只看到 1 段，`champion_falsified` 恒为 false。
    而且它**不报错**，只是安静地少算一段证据。
    """
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    _write_validation(reports, ["2019-01-02", "2023-12-29"],
                      {"excess": -0.0049, "excess_t": -3.55, "days": 486}, DEFAULT_W)
    s = TestClient(create_app(data_root=tmp_path)).get("/api/evolution/summary").json()
    assert s["champion_windows"] == 1, "独立验证那整段被丢弃了"
    assert s["champion_health"] == "significantly_negative"


# ── 第 20 号缺陷的第三个副本：_live_oos 也只按权重认现任 ──────────
# 2026-10-11 22:30 真浏览器截图发现：把 84 组复合赛马报告拷进 reports 后，
# 判决板的「线上这组权重」从「样本外显著为负 −0.598%/日 t=−2.83」
# 变成了「样本外无法区分于 0 · t=—」。
#
# 原因：`aplus/webapp.py::_live_oos` 按**权重**匹配，而现任权重在 84 组候选里
# 出现 **6 次**（K=1/5/21/63 × 有无缓冲）。它返回表里第一个匹配的行——
# 那是 K=63 的行（超额 −0.01%、t=+0.68），**不是线上真正在跑的日频行**。
#
# 与 `oos_decision` 的「现任只按权重认」是同一个错误的第三个副本。

def test_live_oos_matches_the_row_actually_being_run(tmp_path):
    from aplus.webapp import create_app
    from fastapi.testclient import TestClient
    import json as _json

    reports = tmp_path / "reports"
    reports.mkdir()
    # 表按训练目标分降序：K=63 的现任行排在日频现任行**前面**
    (reports / "race_composite.json").write_text(_json.dumps({
        "kind": "composite_race", "window": ["2024-01-01", "2026-01-01"],
        "table": [
            {"weights": {"mom20": 1.0}, "rebalance_every": 63, "buffer_multiple": None,
             "test": {"stats": {"up_excess_vs_market": -0.0001, "excess_t": 0.68,
                                "excess_days": 11, "n_ranked": 100,
                                "overall_hit_rate": 0.50}}},
            {"weights": {"mom20": 1.0}, "rebalance_every": 1, "buffer_multiple": None,
             "test": {"stats": {"up_excess_vs_market": -0.00598, "excess_t": -2.83,
                                "excess_days": 752, "n_ranked": 100,
                                "overall_hit_rate": 0.4746}}},
        ]}), encoding="utf-8")
    (reports / "weights.json").write_text(_json.dumps(
        {"weights": {"mom20": 1.0}, "evidence": None,
         "execution": {"rebalance_every": 1, "buffer_multiple": None}}),
        encoding="utf-8")

    app = create_app(tmp_path)
    with TestClient(app) as c:
        s = c.get("/api/evolution/summary").json()
    assert s["live_oos_t"] == -2.83, (
        f"拿到 t={s['live_oos_t']}——线上跑的是日频（K=1），"
        f"不该报 K=63 那行的成绩")
    assert s["live_oos_verdict"] == "significantly_negative"


def test_live_oos_falls_back_to_weights_only_for_legacy_rows(tmp_path):
    """旧报告没有 rebalance_every 字段 → 按日频处理，别判成找不到。"""
    from aplus.webapp import create_app
    from fastapi.testclient import TestClient
    import json as _json

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "race_legacy.json").write_text(_json.dumps({
        "kind": "composite_race", "window": ["2024-01-01", "2026-01-01"],
        "table": [{"weights": {"mom20": 1.0},
                   "test": {"stats": {"up_excess_vs_market": -0.006, "excess_t": -3.0,
                                      "n_ranked": 100}}}]}), encoding="utf-8")
    (reports / "weights.json").write_text(_json.dumps(
        {"weights": {"mom20": 1.0}, "evidence": None,
         "execution": {"rebalance_every": 1, "buffer_multiple": None}}),
        encoding="utf-8")

    app = create_app(tmp_path)
    with TestClient(app) as c:
        s = c.get("/api/evolution/summary").json()
    assert s["live_oos_t"] == -3.0


def test_live_oos_reads_the_flat_test_stats_shape(tmp_path):
    """**第三种形状**（2026-10-11 22:35 真浏览器截图发现）。

    赛马报告有两种行布局：

    | 写入方 | 布局 |
    |---|---|
    | `evolve_race_fast.py` | `row["test"]["stats"]` |
    | `evolve_race_composite.py` | `row["test_stats"]`（顶层扁平） |

    `_live_oos` 只读前者，于是对 84 组复合赛马报告**一直返回 t=None**，
    判决板显示「样本外无法区分于 0 · t=—」——
    而那批报告里现任的日频行 t=−7.56。

    与 `champion_health.collect_windows` 里记录的「两个报告族用不同键名
    表示同一个量」完全同族，只是这里多了一种布局。
    """
    from aplus.webapp import create_app
    from fastapi.testclient import TestClient
    import json as _json

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "race_composite.json").write_text(_json.dumps({
        "kind": "composite_race", "window": ["2019-01-01", "2026-01-01"],
        "table": [{"weights": {"mom20": 1.0}, "rebalance_every": 1,
                   "buffer_multiple": None,
                   "test_stats": {"up_excess_vs_market": -0.0085, "excess_t": -7.56,
                                  "excess_days": 752, "n_ranked": 4805,
                                  "overall_hit_rate": 0.47}}]}), encoding="utf-8")
    (reports / "weights.json").write_text(_json.dumps(
        {"weights": {"mom20": 1.0}, "evidence": None,
         "execution": {"rebalance_every": 1, "buffer_multiple": None}}),
        encoding="utf-8")

    app = create_app(tmp_path)
    with TestClient(app) as c:
        s = c.get("/api/evolution/summary").json()
    assert s["live_oos_t"] == -7.56, (
        f"拿到 t={s['live_oos_t']}——复合赛马报告用顶层 test_stats 布局，"
        f"只读 test.stats 会永远返回 None")
    assert s["live_oos_verdict"] == "significantly_negative"
