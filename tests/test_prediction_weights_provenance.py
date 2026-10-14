"""预测文件的权重出处：三态判定（2026-10-14）。

## 这次要修的是什么

2026-10-14 实测确认的静默失败：

- 概览页榜单来自 `data/reports/predictions/pred_2026-10-13.parquet`（10-13 20:21）。
- 那份榜单生成于 `DEFAULT_WEIGHTS` **符号整体翻转之前**
  （`{mom20:+1.0, rev5:+0.3, vol20:+0.5, vsur:+0.5}` →
  `{mom20:-1.0, rev5:+0.3, vol20:-0.5, vsur:-0.5}`）。
- 该文件「看涨」前 6 名的 20 日动量**全是正的**
  （+164.5% / +122.8% / +71.7% / +105.7% / +98.0% / +79.2%），
  而实测 IC（`factor_ic_7y.json`）说 `mom20` 是**负的**（全段 IC −0.0357；
  8 因子 × 8 年逐年符号 **63/63** 一致——`turnratio` 缺 2019 只有 7 条逐年
  记录，缺口不补也不外推，分母是有记录的那 63 条）。
  符号修正后看涨榜**应该全是低动量股**。

也就是说：**用户要的是「明天会涨的 10 只」，屏幕上这张榜是用一把实测会挑反的
尺子排出来的，而页面上没有任何一处说明它早于当前权重。**

根因在 `aplus/reports.py::save_predictions`：它只写 `pred_date`，
**不记录自己用了哪组权重**。于是任何权重变更都会让概览页静默显示过期榜单——
HTTP 200、字段类型正确、前端不报错。这是教科书式的静默失败。

## 三态，以及为什么 `unknown` 是第三态而不是第四态

    match     —— 榜单记录的指纹 == 当前权重的指纹
    mismatch  —— 两者的指纹都能读出来，且不相等
    unknown   —— **读不出榜单的指纹**（老文件没有 weights_hash 列）

`unknown` **必须报警**，绝不能当成 `match`。
理由一句话：**无法证明一致 ≠ 一致。**

一个缺失的列如果被静默当成「一致」，那这个字段就退化成了摆设——
所有权重变更都还是静默通过，只是多了一个从不报警的列。

## 为什么不给老文件补写 hash（写在这里，防止将来「顺手修好」）

`pred_2026-10-13.parquet` 确实是用旧权重生成的。给它补一个当前的
`weights_hash` 会让页面显示 `match` —— 那是**伪造证据**：把一把没量过的
尺子标成量过的尺子。而且这个伪造是**永久的**，没有任何痕迹表明它被猜过。

本文件末尾有一条测试专门钉住这个事实：老文件必须**继续**是 `unknown`。

## 「太吵」的问题不成立

报警的只有 `/api/predictions/latest` —— 它只服务**一张**榜单，
就是概览页正在显示的那张。历史归档走 `/api/predictions/history`，
不带这个状态字段（见 `test_history_endpoint_carries_no_weights_verdict`）。
所以不存在「所有历史文件一起报警」——被比的永远只有屏幕上那一份。
"""
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from aplus.factor import DEFAULT_WEIGHTS
from aplus.reports import (
    load_prediction_weights_hash,
    latest_prediction_date,
    prediction_weights_verdict,
    save_predictions,
    weights_fingerprint,
)
from aplus.webapp import create_app


OLD_WEIGHTS = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}   # 翻转前
NEW_WEIGHTS = {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5}  # 翻转后


def _updown():
    up = pd.DataFrame({"code": ["600000.SH"], "name": ["甲"], "direction": ["up"],
                       "score": [2.0], "mom20": [1.645]})
    down = pd.DataFrame({"code": ["600001.SH"], "name": ["乙"], "direction": ["down"],
                         "score": [-2.0], "mom20": [-0.31]})
    return up, down


# ---------- 指纹：稳定 + 与 dict 顺序无关 ----------

def test_fingerprint_ignores_dict_insertion_order():
    """同样一组权重的两种插入顺序必须得到同一个指纹。

    ## 为什么这条必须先钉

    指纹的用途是**跨进程、跨天**比较「是不是同一组权重」。它一旦对
    dict 顺序敏感，`save_predictions` 里 `load_weights` 返回的顺序
    和 `/api/weights` 里的顺序稍有不同就会算出两个不同的指纹，
    于是每次都报 `mismatch` —— 一个永远在响的假警报。

    假警报的杀伤力不比漏报小：用户会学会忽略它，然后连真的那条一起忽略。

    排序发生在 **hash 之前**（`json.dumps(..., sort_keys=True)`），
    不是在 dict 上排序后取 items —— 后者在 Python 里同样可行但更啰嗦，
    且 `ensure_ascii=False` 那一层就不必再解释为什么排序不改变编码。
    """
    a = {"mom20": -1.0, "rev5": 0.3, "vol20": -0.5, "vsur": -0.5}
    b = {"vsur": -0.5, "vol20": -0.5, "rev5": 0.3, "mom20": -1.0}
    assert list(a) != list(b), "本测试的前提失效：两个 dict 插入顺序相同"
    assert weights_fingerprint(a) == weights_fingerprint(b)


def test_fingerprint_is_twelve_hex_chars():
    """12 位十六进制——够短以便在页面上显示，够长以免撞车。"""
    fp = weights_fingerprint(NEW_WEIGHTS)
    assert len(fp) == 12
    assert all(c in "0123456789abcdef" for c in fp), f"不是小写十六进制：{fp}"


def test_fingerprint_changes_when_a_sign_flips():
    """`mom20` 从 +1.0 翻到 -1.0 必须换指纹——这正是本次事故的那一下。"""
    assert weights_fingerprint(OLD_WEIGHTS) != weights_fingerprint(NEW_WEIGHTS)


def test_fingerprint_changes_when_only_magnitude_changes():
    """幅度也必须进指纹。`-1.0` 和 `-0.3` 方向一致、幅度不同，榜单不一样。"""
    assert weights_fingerprint({"mom20": -1.0}) != weights_fingerprint({"mom20": -0.3})


def test_fingerprint_is_stable_across_calls():
    """同一份权重重复算必须同一个值——它要跨进程/跨天当身份证用。"""
    assert weights_fingerprint(NEW_WEIGHTS) == weights_fingerprint(dict(NEW_WEIGHTS))


def test_fingerprint_of_empty_weights_is_none():
    """空权重没有指纹——返回 None 而不是某个字符串。

    空 dict 的 `json.dumps` 是合法的 `"{}"`，会算出一个**看起来合法的指纹**。
    拿它去和真权重的指纹比，必然 mismatch，而真相是「压根没有权重可谈」。
    """
    assert weights_fingerprint(None) is None
    assert weights_fingerprint({}) is None


def test_fingerprint_survives_unicode_keys():
    """`ensure_ascii=False` 那一层不能省——中文因子名的编码必须一致。"""
    assert weights_fingerprint({"动量": -1.0}) == weights_fingerprint({"动量": -1.0})
    assert len(weights_fingerprint({"动量": -1.0})) == 12


# ---------- 落盘：指纹进 parquet ----------

def test_save_predictions_records_the_weights_fingerprint(tmp_path):
    save_predictions(tmp_path, "2026-10-14", *_updown(), weights=NEW_WEIGHTS)
    raw = pd.read_parquet(tmp_path / "predictions" / "pred_2026-10-14.parquet")
    assert "weights_hash" in raw.columns, "预测文件没有记下自己用了哪组权重"
    assert set(raw["weights_hash"]) == {weights_fingerprint(NEW_WEIGHTS)}


def test_every_row_carries_the_fingerprint(tmp_path):
    """涨腿跌腿**每一行**都带——只标一部分就等于没标。"""
    up = pd.DataFrame({"code": ["A", "B"], "name": ["甲", "乙"], "direction": ["up", "up"],
                       "score": [1.0, 2.0], "mom20": [0.1, 0.2]})
    down = pd.DataFrame({"code": ["C"], "name": ["丙"], "direction": ["down"],
                         "score": [-1.0], "mom20": [-0.1]})
    save_predictions(tmp_path, "2026-10-14", up, down, weights=NEW_WEIGHTS)
    raw = pd.read_parquet(tmp_path / "predictions" / "pred_2026-10-14.parquet")
    assert len(raw) == 3
    assert raw["weights_hash"].notna().all(), "有行没记上指纹"
    assert len(set(raw["weights_hash"])) == 1, "同一份文件里出现了两个指纹"


def test_save_predictions_without_weights_writes_no_column(tmp_path):
    """没传 weights 就**不写这一列**——不留空串、不留全 NaN 的假列。

    留一个全空的列看起来「有字段」，实际读出来还是不可知。
    与其制造一个每次都要额外判空的僵尸列，不如干脆没有。
    """
    save_predictions(tmp_path, "2026-10-14", *_updown())
    raw = pd.read_parquet(tmp_path / "predictions" / "pred_2026-10-14.parquet")
    assert "weights_hash" not in raw.columns
    assert load_prediction_weights_hash(tmp_path, "2026-10-14") is None


def test_roundtrip_through_disk_preserves_the_fingerprint(tmp_path):
    """落盘再读回来指纹不变——否则比的是两个不同的东西。"""
    save_predictions(tmp_path, "2026-10-14", *_updown(), weights=NEW_WEIGHTS)
    assert load_prediction_weights_hash(tmp_path, "2026-10-14") == weights_fingerprint(NEW_WEIGHTS)


# ---------- 读出处的健壮性：读不出就是读不出 ----------

def test_legacy_file_without_the_column_reads_as_unknown_not_match(tmp_path):
    """老文件（2026-10-13 那种）读出来必须是 None —— 出处不可知。"""
    save_predictions(tmp_path, "2026-10-13", *_updown())
    assert load_prediction_weights_hash(tmp_path, "2026-10-13") is None


def test_missing_file_reads_as_none(tmp_path):
    assert load_prediction_weights_hash(tmp_path, "1999-01-01") is None


def test_corrupt_file_reads_as_none_rather_than_raising(tmp_path):
    """文件损坏时抛异常会让 `/api/predictions/latest` 整个 500。

    而出处读不出来**本来就是一个合法结论**（unknown），不是故障。
    把故障和结论混在一起，页面就只剩一片空白。
    """
    d = tmp_path / "predictions"
    d.mkdir(parents=True)
    (d / "pred_2026-10-14.parquet").write_bytes(b"not a parquet file at all")
    assert load_prediction_weights_hash(tmp_path, "2026-10-14") is None


def test_file_with_conflicting_fingerprints_reads_as_none(tmp_path):
    """一份文件里两行写了不同的指纹 —— 读不出「这份文件用的哪组权重」。

    这在正常流程里不会发生（`save_predictions` 每行写同一个值）。
    但它一旦发生，唯一诚实的答案是 unknown：**矛盾等于不可知**，
    取第一个或取最后一个都是替它做主。
    """
    save_predictions(tmp_path, "2026-10-14", *_updown(), weights=NEW_WEIGHTS)
    p = tmp_path / "predictions" / "pred_2026-10-14.parquet"
    raw = pd.read_parquet(p)
    raw.loc[raw.index[-1], "weights_hash"] = weights_fingerprint(OLD_WEIGHTS)
    raw.to_parquet(p, index=False)
    assert load_prediction_weights_hash(tmp_path, "2026-10-14") is None


# ---------- 三态判定 ----------

def _verdict(tmp_path, date, current_weights):
    return prediction_weights_verdict(tmp_path, date, current_weights)


def test_same_weights_give_match(tmp_path):
    save_predictions(tmp_path, "2026-10-14", *_updown(), weights=NEW_WEIGHTS)
    v = _verdict(tmp_path, "2026-10-14", NEW_WEIGHTS)
    assert v["status"] == "match", v
    assert v["board_hash"] == v["current_hash"]


def test_sign_flip_gives_mismatch(tmp_path):
    """本次事故本身：榜单用翻转前的权重，现在跑的是翻转后的。"""
    save_predictions(tmp_path, "2026-10-13", *_updown(), weights=OLD_WEIGHTS)
    v = _verdict(tmp_path, "2026-10-13", NEW_WEIGHTS)
    assert v["status"] == "mismatch", v
    assert v["board_hash"] == weights_fingerprint(OLD_WEIGHTS)
    assert v["current_hash"] == weights_fingerprint(NEW_WEIGHTS)


def test_legacy_file_gives_unknown_and_not_match(tmp_path):
    """**本文件最核心的一条。**

    缺列 → unknown。它**不是** match。
    把 unknown 归到 match 那一档，等于让这个字段在所有权重变更后
    继续静默通过——那就没有任何修复。
    """
    save_predictions(tmp_path, "2026-10-13", *_updown())
    v = _verdict(tmp_path, "2026-10-13", NEW_WEIGHTS)
    assert v["status"] == "unknown", (
        f"缺列的老文件被当成了 {v['status']} —— "
        "无法证明一致被当成了「一致」，整个字段就白加了")
    assert v["board_hash"] is None
    assert v["current_hash"] == weights_fingerprint(NEW_WEIGHTS)


def test_no_current_weights_gives_unknown(tmp_path):
    """当前权重读不出来时不能报 match —— 比的两边都得在。"""
    save_predictions(tmp_path, "2026-10-14", *_updown(), weights=NEW_WEIGHTS)
    assert _verdict(tmp_path, "2026-10-14", None)["status"] == "unknown"
    assert _verdict(tmp_path, "2026-10-14", {})["status"] == "unknown"


def test_verdict_exposes_both_fingerprints_for_triage(tmp_path):
    """排查时要能一眼看出「哪把尺子 vs 哪把尺子」，不只是一个布尔。"""
    save_predictions(tmp_path, "2026-10-13", *_updown(), weights=OLD_WEIGHTS)
    v = _verdict(tmp_path, "2026-10-13", NEW_WEIGHTS)
    assert len(v["board_hash"]) == 12 and len(v["current_hash"]) == 12
    assert v["board_hash"] != v["current_hash"]
    assert v["current_weights"] == NEW_WEIGHTS, "排查需要看到当前权重本身"


def test_verdict_only_looks_at_the_named_file(tmp_path):
    """判定只看被点名的那一份——归档各判各的，不牵连。

    这条钉住「太吵」那个担心是无根据的：接口每次只服务一张榜单。
    """
    save_predictions(tmp_path, "2026-10-12", *_updown(), weights=OLD_WEIGHTS)   # 老的、有指纹
    save_predictions(tmp_path, "2026-10-13", *_updown())                          # 新的、没指纹
    assert latest_prediction_date(tmp_path) == "2026-10-13"
    assert _verdict(tmp_path, "2026-10-13", NEW_WEIGHTS)["status"] == "unknown"
    # 单独看归档那份，它自己是对得上的 —— 不会因为旁边有个 unknown 就被拖下水
    assert _verdict(tmp_path, "2026-10-12", OLD_WEIGHTS)["status"] == "match"


# ---------- API：把三态递到前端 ----------

@pytest.fixture
def client(tmp_path):
    reports = tmp_path / "reports"
    fake = _FakeLLM()
    return TestClient(create_app(tmp_path, llm=fake)), reports, tmp_path


class _FakeLLM:
    def chat(self, messages, system=""):
        return "ok"


def test_latest_predictions_reports_match(tmp_path):
    reports = tmp_path / "reports"
    save_predictions(reports, "2026-10-14", *_updown(), weights=NEW_WEIGHTS)
    tc = TestClient(create_app(tmp_path, llm=_FakeLLM()))
    body = tc.get("/api/predictions/latest").json()
    assert body["weights_check"]["status"] == "match", body["weights_check"]


def test_latest_predictions_reports_mismatch(tmp_path):
    reports = tmp_path / "reports"
    save_predictions(reports, "2026-10-13", *_updown(), weights=OLD_WEIGHTS)
    tc = TestClient(create_app(tmp_path, llm=_FakeLLM()))
    body = tc.get("/api/predictions/latest").json()
    assert body["weights_check"]["status"] == "mismatch", body["weights_check"]


def test_latest_predictions_reports_unknown_not_match(tmp_path):
    """老文件经 API 出去仍然是 unknown —— 中间任何一层都不得把它洗成 match。"""
    reports = tmp_path / "reports"
    save_predictions(reports, "2026-10-13", *_updown())
    tc = TestClient(create_app(tmp_path, llm=_FakeLLM()))
    body = tc.get("/api/predictions/latest").json()
    assert body["weights_check"]["status"] == "unknown", (
        f"老文件在 API 层被当成了 {body['weights_check']['status']}")
    assert body["weights_check"]["board_hash"] is None


def test_api_uses_live_weights_as_the_reference(tmp_path):
    """「当前」必须是 `load_weights` 的结果，不能写死默认值。

    `weights.json` 一旦落盘（赛马晋级），参考就是晋级权重。
    写死 DEFAULT_WEIGHTS 会在晋级后立刻把每张榜单都误报成 mismatch。
    """
    reports = tmp_path / "reports"
    save_predictions(reports, "2026-10-14", *_updown(), weights=NEW_WEIGHTS)
    (reports / "weights.json").write_text(json.dumps({
        "weights": OLD_WEIGHTS, "evidence": {"promoted_on": "2026-10-14"},
        "execution": {"rebalance_every": 1},
    }), encoding="utf-8")
    tc = TestClient(create_app(tmp_path, llm=_FakeLLM()))
    body = tc.get("/api/predictions/latest").json()
    assert body["weights_check"]["status"] == "mismatch", (
        "参考权重没走 load_weights —— 落盘晋级后每张榜单都会误报")
    assert body["weights_check"]["current_weights"] == OLD_WEIGHTS


def test_latest_predictions_still_returns_the_boards(tmp_path):
    """加字段不能把原有的 up/down 挤掉——那是这个接口存在的理由。"""
    reports = tmp_path / "reports"
    save_predictions(reports, "2026-10-14", *_updown(), weights=NEW_WEIGHTS)
    tc = TestClient(create_app(tmp_path, llm=_FakeLLM()))
    body = tc.get("/api/predictions/latest").json()
    assert [r["code"] for r in body["up"]] == ["600000.SH"]
    assert [r["code"] for r in body["down"]] == ["600001.SH"]


def test_history_endpoint_carries_no_weights_verdict(tmp_path):
    """归档接口**不带**这个状态——只有屏幕上那一张需要被判。

    带着它会让「所有历史文件一起报警」成立，那才是真的吵。
    """
    reports = tmp_path / "reports"
    save_predictions(reports, "2026-10-12", *_updown(), weights=OLD_WEIGHTS)
    save_predictions(reports, "2026-10-13", *_updown())
    tc = TestClient(create_app(tmp_path, llm=_FakeLLM()))
    body = tc.get("/api/predictions/history").json()
    assert "weights_check" not in body
    assert "weights_status" not in json.dumps(body), "归档里混进了出处状态"


# ---------- 接线：run_daily 必须把权重传下去 ----------

def test_run_daily_passes_the_weights_it_used_to_save_predictions(tmp_path, monkeypatch):
    """`step_predict_and_allocate` 已经在算 `weights`，必须传进 `save_predictions`。

    这条是整条链子的最后一环：`save_predictions` 支持了指纹、
    API 也会判三态了，而调用方不传 —— 那就还是一份无名榜单。
    """
    import scripts.run_daily as rd

    data_root = tmp_path / "data"
    (data_root / "reports").mkdir(parents=True)
    reports = data_root / "reports"

    up = pd.DataFrame({"code": ["600000.SH"], "name": ["甲"], "direction": ["up"],
                       "score": [2.0], "mom20": [0.4]})
    down = pd.DataFrame({"code": ["600001.SH"], "name": ["乙"], "direction": ["down"],
                         "score": [-2.0], "mom20": [-0.4]})

    monkeypatch.setattr(rd, "build_cross_section", lambda *a, **k: pd.DataFrame())
    # `step_predict_and_allocate` 在 save_predictions 之后还要用 universe
    # 组持仓状态，所以这里给一个带 code/name 的最小横截面而不是空表。
    monkeypatch.setattr(rd, "filter_universe",
                        lambda *a, **k: pd.DataFrame({"code": ["600000.SH", "600001.SH"],
                                                       "name": ["甲", "乙"]}))
    monkeypatch.setattr(rd, "build_predictions", lambda *a, **k: (up, down))

    from aplus.store import LocalStore

    rd.step_predict_and_allocate(LocalStore(data_root), reports, "2026-10-14", 50000, {})

    fp = load_prediction_weights_hash(reports, "2026-10-14")
    assert fp is not None, (
        "run_daily 出的名单没有记下用了哪组权重 —— "
        "概览页拿到的仍然是一份出处不可知的榜单")
    from aplus.evolve import load_weights

    assert fp == weights_fingerprint(load_weights(reports)["weights"])


# ---------- 诚实纪律：老文件必须继续是 unknown ----------

PROD_PRED = "data/reports/predictions/pred_2026-10-13.parquet"


def test_the_production_legacy_board_was_not_backfilled():
    """**不要**给 `pred_2026-10-13.parquet` 补写 `weights_hash`。

    ## 这条是干什么的

    那份榜单确实是**符号翻转之前**生成的。把当前指纹补进去，页面会显示
    `match` —— 那是伪造证据：给一把没量过的尺子贴上「量过了」的标签。

    而且这个伪造是**永久且无声**的：parquet 里没有任何东西能区分
    「当时记下的」和「后来猜的」。将来谁看到 `match` 都会相信它。

    所以老文件**就该**是 `unknown`，而且这条测试会在有人「顺手修好」时变红。
    """
    import pathlib

    p = pathlib.Path(PROD_PRED)
    if not p.exists():
        pytest.skip(f"生产榜单不在 {p}（别的机器上跳过）")
    raw = pd.read_parquet(p)
    assert "weights_hash" not in raw.columns, (
        "pred_2026-10-13.parquet 被补写了 weights_hash —— "
        "它是用翻转前的权重生成的，标成 match 是伪造证据")


def test_the_production_legacy_board_reads_as_unknown():
    from pathlib import Path as _P

    if not _P(PROD_PRED).exists():
        pytest.skip(f"生产榜单不在 {PROD_PRED}（别的机器上跳过）")
    v = prediction_weights_verdict(
        _P("data/reports"), "2026-10-13", DEFAULT_WEIGHTS)
    assert v["status"] == "unknown", (
        f"线上那份榜单当前被判成 {v['status']}，"
        "按实测 IC mom20 为负、它的看涨前 6 名动量全为正，它确实是旧尺子排的")
