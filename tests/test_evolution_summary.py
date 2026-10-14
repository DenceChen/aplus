"""「进化」tab 必须把**最关键的事实**端出来，而不是只列候选表。

## 事故（2026-10-11）

用户看「进化」tab 后问：「是不是记录了每次自进化改进带来的提升？
我看全是绿色 都是亏损」。

真实状态是：**8 份赛马报告、1 条进化日志、晋级 0 次**，
`data/reports/weights.json` **不存在**，
线上跑的是 `source: default` 的硬编码权重、`evidence: null`（从未经赛马验收）。

而那个 tab 只做了一件事：把候选的超额列成表。
一屏全是绿色（`--down`，A 股红涨绿跌），**没有一行字说「这些全部被否决了、
线上那个才是真正在用的、而且它没被验收过」**。

这不是数据缺失，是**呈现缺失**：最该被看见的事实，恰好是唯一没被画出来的那件事。
用户把一屏绿色读成「有进展」，是界面在替他下结论。

## 修法

`/api/evolution/summary` 把判决板的数据端出来，前端必须显示它。
测试钉死三件事：晋级次数必须是真数；当前权重是否经过验收必须显式；
没有任何一次晋级时，`all_rejected` 必须为真。
"""
import json

from fastapi.testclient import TestClient

from aplus.webapp import create_app


def _write(log, tmp_path, weights=None):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "evolution_log.json").write_text(json.dumps(log), encoding="utf-8")
    if weights is not None:
        (reports / "weights.json").write_text(json.dumps(weights), encoding="utf-8")


def _client(tmp_path):
    return TestClient(create_app(data_root=tmp_path))


def _entry(promoted, date="2026-10-11"):
    return {
        "date": date,
        "decision": {"promoted": promoted, "reason": "超额硬闸否决"},
        "evaluations": [
            {"weights": {"mom20": 1.0}, "source": "builtin",
             "stats": {"overall_hit_rate": 0.51, "up_excess_vs_market": -0.002},
             "train_stats": {"overall_hit_rate": 0.52, "up_excess_vs_market": -0.003}}
        ],
    }


def test_summary_counts_promotions(tmp_path):
    _write([_entry(False), _entry(False), _entry(True)], tmp_path)
    s = _client(tmp_path).get("/api/evolution/summary").json()
    assert s["races"] == 3
    assert s["promotions"] == 1
    assert s["all_rejected"] is False


def test_summary_flags_all_rejected(tmp_path):
    """一次没晋级时必须显式说 all_rejected，不能让界面自己推。"""
    _write([_entry(False)], tmp_path)
    s = _client(tmp_path).get("/api/evolution/summary").json()
    assert s["promotions"] == 0
    assert s["all_rejected"] is True


def test_summary_marks_unvalidated_live_weights(tmp_path):
    """线上权重未经赛马验收时必须标出来——那正是真正在跑的东西。"""
    _write([_entry(False)], tmp_path)          # 没有 weights.json
    s = _client(tmp_path).get("/api/evolution/summary").json()
    assert s["weights_exist"] is False
    assert s["live_validated"] is False


def test_summary_reports_validated_live_weights(tmp_path):
    _write([_entry(True)], tmp_path,
           weights={"weights": {"mom20": 1.0}, "source": "evolved",
                    "evidence": {"objective": 0.2, "promoted_on": "2026-10-11"}})
    s = _client(tmp_path).get("/api/evolution/summary").json()
    assert s["weights_exist"] is True
    assert s["live_validated"] is True
    assert s["live_source"] == "evolved"


def test_summary_marks_default_weights_unvalidated(tmp_path):
    """**线上真实情况**：weights.json 存在但 source=default（硬编码默认值）。

    `weights_exist=True` 不等于「线上这组被验收过」。文件存在只说明有人写过它，
    而 2026-10-11 线上跑的就是 `source: default` / `evidence: null`。
    这个洞正是变异体 V3（`live_validated` 不看 source）能存活的原因——
    **缺了这一档，测试就分不清「有文件」和「验过收」**。
    """
    _write([_entry(False)], tmp_path,
           weights={"weights": {"mom20": 1.0}, "source": "default", "evidence": None})
    s = _client(tmp_path).get("/api/evolution/summary").json()
    assert s["weights_exist"] is True
    assert s["live_validated"] is False, "default 权重不是进化成果，不能算已验收"
    assert s["live_source"] == "default"


def test_summary_evolved_without_evidence_is_not_validated(tmp_path):
    """source=evolved 但没有 evidence，同样不算验收——只有自报来源不算数。"""
    _write([_entry(True)], tmp_path,
           weights={"weights": {"mom20": 1.0}, "source": "evolved", "evidence": None})
    s = _client(tmp_path).get("/api/evolution/summary").json()
    assert s["live_validated"] is False


def test_summary_survives_empty_log(tmp_path):
    """没跑过赛马时不能 500，必须返回全零且让界面说「还没跑」。"""
    s = _client(tmp_path).get("/api/evolution/summary").json()
    assert s["races"] == 0
    assert s["promotions"] == 0
    assert s["all_rejected"] is False   # 没跑 ≠ 全否决，别混淆
    assert s["weights_exist"] is False


def test_summary_best_test_excess_is_reported(tmp_path):
    """把最好的一档测试超额摆出来，避免只看到一屏绿的总体印象。"""
    log = _entry(False)
    log["evaluations"][0]["stats"]["up_excess_vs_market"] = 0.0123
    _write([log], tmp_path)
    s = _client(tmp_path).get("/api/evolution/summary").json()
    assert abs(s["best_test_excess"] - 0.0123) < 1e-12
