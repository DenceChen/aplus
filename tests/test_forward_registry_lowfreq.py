"""低频候选（K=5 周频 / K=21 月频）进入前瞻台账。

## 为什么要有这批测试

2026-10-12 冻结的前两批共 10 组里，只有 3 组是 K=63。
按判决力要求（`MIN_TEST_PERIODS = 3` 个独立调仓周期），
K=63 要 189 个交易日才出得了第一个 t 值——**近 9 个月里策略一个版本都不动**。
K=5 只要 15 个交易日、K=21 只要 63 个，判决路径快 3~12 倍。

提速只能靠**降低 K**，不能靠降低证据标准。这批测试就是钉住这一点：
把「门槛随 K 缩放」写成断言，防止有人日后用「放松门槛」的方式
把 9 个月缩成 9 天——那会让前瞻验证退化成又一轮自证。

## 覆盖的四条

1. 低频候选能注册，且**注册即冻结**（事后改权重会被拒）
2. 窗口严格晚于注册日，否则拒
3. **判决力门槛随 K 缩放**：K=5 严格快于 K=63（本次改动的意义所在）
4. 同一批次脚本重复运行不重复插入（幂等）
"""
import json

import pytest


def _cand(w, every=1, buf=None):
    return {"weights": dict(w), "rebalance_every": every, "buffer_multiple": buf}


REV = {"mom20": -1.0}
REV_LOWTURN = {"mom20": -0.5, "turnratio": -0.5}
IC_ALL = {"mom20": -1.0, "rev5": 1.0, "vol20": -1.0, "vsur": -1.0,
          "turn20": -1.0, "turnratio": -1.0, "ivol": -1.0, "rsi": -1.0}


def _cal(n, start="2026-10-14"):
    """造 n 个连续交易日（跳过周末）。"""
    from datetime import date, timedelta
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# ── 1. 低频候选注册成功，且注册即冻结 ────────────────────────────

def test_lowfreq_candidates_register_and_land_in_ledger(tmp_path):
    """K=5 / K=21 必须真的写进台账，且频率原样保留。

    频率若被静默改成默认值（1），低频候选会以日频被实测——
    实测数字与实盘执行完全不符，而报告上一切正常。
    """
    from aplus.forward import load_registry, register
    rec = register(tmp_path, "2026-10-14",
                   [_cand(REV, 5), _cand(IC_ALL, 21)],
                   note="第三批：低频")
    assert [c["rebalance_every"] for c in rec["candidates"]] == [5, 21]
    rows = load_registry(tmp_path)
    assert len(rows) == 1
    assert {c["rebalance_every"] for c in rows[0]["candidates"]} == {5, 21}


def test_registered_batch_is_frozen_against_weight_edit(tmp_path):
    """注册即冻结：事后改权重必须被拒。

    这条规则原先只由**约定**维持（`register()` 只追加），
    但没有任何机制阻止有人直接编辑 `forward_registry.json`。
    本次给每份注册算一个内容指纹（`sealed_sha256`），
    改动任何字节都验不过——**规则从约定变成机制**。
    """
    from aplus.forward import registry_tamper_report, register
    register(tmp_path, "2026-10-14", [_cand(REV, 5)], note="第三批")

    p = tmp_path / "forward_registry.json"
    assert registry_tamper_report(tmp_path)["tampered"] == [], \
        "刚注册完就应当是干净的"

    rows = json.loads(p.read_text(encoding="utf-8"))
    rows[0]["candidates"][0]["weights"] = {"mom20": 1.0}   # 事后改成现任
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    rep = registry_tamper_report(tmp_path)
    assert rep["tampered"] == [0], "改了权重却没报出来——冻结形同虚设"


def test_frozen_rejects_frequency_edit_too(tmp_path):
    """把 K=5 事后改成 K=63 同样必须被抓到。

    只校验权重是不够的：频率是候选身份三元组之一
    （`candidate_key`），改频率等于换成另一个策略。
    """
    from aplus.forward import registry_tamper_report, register
    register(tmp_path, "2026-10-14", [_cand(REV, 5)], note="第三批")
    p = tmp_path / "forward_registry.json"
    rows = json.loads(p.read_text(encoding="utf-8"))
    rows[0]["candidates"][0]["rebalance_every"] = 63
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    assert registry_tamper_report(tmp_path)["tampered"] == [0]


def test_unsealed_legacy_registration_is_reported_not_silently_trusted(tmp_path):
    """没有指纹的旧注册（2026-10-12 两批）报 `unsealed`，**不当成通过**。

    它们注册时指纹机制还不存在，无法证明此后没被改过。
    把「无法证明」当成「没问题」处理，正是本项目反复吃亏的那类错。
    """
    from aplus.forward import registry_tamper_report, register
    register(tmp_path, "2026-10-14", [_cand(REV, 5)], note="第三批")
    p = tmp_path / "forward_registry.json"
    rows = json.loads(p.read_text(encoding="utf-8"))
    rows.insert(0, {"registered_on": "2026-10-12", "note": "第一批",
                    "evidence": "", "candidates": [_cand(REV, 63)]})
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    rep = registry_tamper_report(tmp_path)
    assert rep["unsealed"] == [0]
    assert rep["tampered"] == []
    assert rep["sealed"] == 1


# ── 2. 窗口必须晚于注册日 ────────────────────────────────────────

def test_window_after_registration_day_is_excluded_for_lowfreq(tmp_path):
    """注册日当天的行情我已经看得到，不能算验证样本。"""
    from aplus.forward import forward_window, register
    register(tmp_path, "2026-10-14", [_cand(REV, 5)], note="x")
    dates = _cal(20, start="2026-10-10")
    w = forward_window(dates, "2026-10-14")
    assert w and min(w) > "2026-10-14"
    assert "2026-10-14" not in w


def test_registration_on_future_date_yields_empty_window(tmp_path):
    """注册日定在未来 → 窗口必空 → 不可测。

    否则会拿「还没发生的日子」当样本，或者更糟：
    为了让窗口非空而把注册日往前挪到已有数据上。
    """
    from aplus.forward import forward_status, register
    register(tmp_path, "2026-12-01", [_cand(REV, 5)], note="未来")
    st = forward_status(tmp_path, as_of="2026-10-20", dates=_cal(20))
    assert st["candidates"][0]["window_days"] == 0
    assert st["candidates"][0]["measurable"] is False


def test_lowfreq_candidate_is_not_measurable_before_window_opens(tmp_path):
    """低频也不能破例：窗口没攒够就明说不够，不给 t。"""
    from aplus.forward import forward_status, register
    register(tmp_path, "2026-10-14", [_cand(REV, 5)], note="x")
    st = forward_status(tmp_path, as_of="2026-10-16",
                        dates=_cal(3, start="2026-10-14"))
    row = st["candidates"][0]
    assert row["measurable"] is False
    assert row["test_periods"] < row["needed_periods"]


# ── 3. 判决力门槛随 K 缩放（本次改动的意义）──────────────────────

def test_threshold_scales_with_k_and_k5_is_far_cheaper_than_k63(tmp_path):
    """K=5 需要的交易日必须**严格少于** K=63，且按 MIN_TEST_PERIODS × K 缩放。

    这是整件事的命门：提速只能来自降低 K。
    一旦有人改成「K 与门槛无关」，本测试立刻红。
    """
    from aplus.forward import MIN_TEST_PERIODS, forward_status, register
    register(tmp_path, "2026-10-14",
             [_cand(REV, 5), _cand(REV, 21), _cand(REV, 63)], note="频率轴")
    # 窗口恰好 15 个交易日：17 个交易日扣掉「注册日当天 + 2 日隔离带」
    # ——隔离带吃掉的天数也是证据的一部分，不能凭空多算。
    st = forward_status(tmp_path, as_of="2026-11-06",
                        dates=_cal(17, start="2026-10-15"))
    rows = {c["rebalance_every"]: c for c in st["candidates"]}
    assert rows[5]["window_days"] == 15
    assert rows[5]["test_periods"] == 3
    assert rows[5]["measurable"] is True
    assert rows[21]["test_periods"] == 0 and rows[21]["measurable"] is False
    assert rows[63]["test_periods"] == 0 and rows[63]["measurable"] is False
    assert MIN_TEST_PERIODS == 3


def test_days_to_verdict_is_min_test_periods_times_k(tmp_path):
    """判决所需交易日 = MIN_TEST_PERIODS × K，**不是**任何拍脑袋的常数。"""
    from aplus.forward import MIN_TEST_PERIODS, days_to_verdict
    assert days_to_verdict(5) == MIN_TEST_PERIODS * 5 == 15
    assert days_to_verdict(21) == MIN_TEST_PERIODS * 21 == 63
    assert days_to_verdict(63) == MIN_TEST_PERIODS * 63 == 189


def test_k5_reaches_verdict_about_12x_faster_than_k63():
    """把「快多少倍」写成断言——K=5 约为 K=63 的 1/12.6，K=21 约为 1/3。"""
    from aplus.forward import days_to_verdict
    assert days_to_verdict(63) / days_to_verdict(5) == pytest.approx(12.6, abs=0.01)
    assert days_to_verdict(63) / days_to_verdict(21) == pytest.approx(3.0, abs=0.01)
    assert days_to_verdict(5) < days_to_verdict(21) < days_to_verdict(63)


def test_shrinking_k_never_relaxes_the_t_threshold(tmp_path):
    """降 K 只缩短**等待时间**，不放松**显著性门槛**。

    Bonferroni 门槛只由候选数决定，与 K 无关。
    少了这一条，「提速」就可能退化成「降低证据标准」——
    那正是本任务的命门。
    """
    from aplus.evolve_race import bonferroni_t
    from aplus.forward import days_to_verdict, forward_status, register
    register(tmp_path, "2026-10-14",
             [_cand(REV, 5), _cand(REV, 21), _cand(REV, 63)], note="频率轴")
    st = forward_status(tmp_path, as_of="2026-11-06",
                        dates=_cal(15, start="2026-10-14"))
    assert len({c["t_crit"] for c in st["candidates"]}) == 1, \
        "同一批里不同 K 的显著性门槛必须一致"
    assert st["candidates"][0]["t_crit"] == pytest.approx(bonferroni_t(3), abs=1e-9)
    # 门槛没变，但等待时间差 12.6 倍
    assert days_to_verdict(5) < days_to_verdict(63)


def test_bonferroni_threshold_does_not_depend_on_k(tmp_path):
    """同一候选集下，把某个候选从 K=63 改成 K=5 不改变 Bonferroni 临界。

    如果门槛跟着 K 走，低频候选就能靠「多注册几组低频」把门槛压低——
    候选数才是唯一应该影响门槛的量。
    """
    from aplus.forward import forward_status, register
    register(tmp_path, "2026-10-14", [_cand(REV, 5), _cand(REV, 63)], note="a")
    st_a = forward_status(tmp_path, as_of="2026-11-06", dates=_cal(15))
    crit_a = st_a["candidates"][0]["t_crit"]

    import shutil
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    register(tmp2, "2026-10-14", [_cand(REV, 5), _cand(IC_ALL, 5)], note="b")
    st_b = forward_status(tmp2, as_of="2026-11-06", dates=_cal(15))
    assert st_b["candidates"][0]["t_crit"] == crit_a


# ── 4. 幂等：同一批次重复运行不重复插入 ──────────────────────────

def test_reregistering_the_same_batch_is_idempotent(tmp_path):
    """同一批（注册日 + 候选集完全相同）跑第二遍，台账长度不许变。

    实测：现有两个注册脚本都**直接调 `register()` 追加**，
    重复运行会把同一批候选插进台账两次，Bonferroni 门槛随之虚高，
    而台账看上去只是「多了一批」。
    """
    from aplus.forward import load_registry, register
    batch = [_cand(REV, 5), _cand(IC_ALL, 21)]
    register(tmp_path, "2026-10-14", batch, note="第三批")
    register(tmp_path, "2026-10-14", batch, note="第三批")
    register(tmp_path, "2026-10-14", batch, note="第三批")
    assert len(load_registry(tmp_path)) == 1


def test_idempotent_rerun_does_not_change_candidate_count_or_t_crit(tmp_path):
    """重复运行连候选数与门槛都不许变——否则等于给自己加了比较次数。"""
    from aplus.forward import forward_status, load_registry, register
    batch = [_cand(REV, 5), _cand(IC_ALL, 21)]
    register(tmp_path, "2026-10-14", batch, note="第三批")
    before = forward_status(tmp_path, as_of="2026-11-06", dates=_cal(20))
    register(tmp_path, "2026-10-14", batch, note="第三批")
    after = forward_status(tmp_path, as_of="2026-11-06", dates=_cal(20))
    assert before["n_candidates"] == after["n_candidates"] == 2
    assert before["candidates"][0]["t_crit"] == after["candidates"][0]["t_crit"]
    assert len(load_registry(tmp_path)) == 1


def test_different_candidate_set_on_same_day_is_a_new_registration(tmp_path):
    """幂等只挡**完全相同**的批次；换了候选集就是新的一批，必须放行。"""
    from aplus.forward import load_registry, register
    register(tmp_path, "2026-10-14", [_cand(REV, 5)], note="第三批")
    register(tmp_path, "2026-10-14", [_cand(IC_ALL, 21)], note="第四批")
    assert len(load_registry(tmp_path)) == 2


def test_idempotency_ignores_candidate_order_within_batch(tmp_path):
    """同一批内候选顺序不同仍算同一批——顺序不影响候选集合。"""
    from aplus.forward import load_registry, register
    register(tmp_path, "2026-10-14",
             [_cand(REV, 5), _cand(IC_ALL, 21)], note="第三批")
    register(tmp_path, "2026-10-14",
             [_cand(IC_ALL, 21), _cand(REV, 5)], note="第三批")
    assert len(load_registry(tmp_path)) == 1


def test_resealing_after_idempotent_rerun_still_valid(tmp_path):
    """重复运行后指纹必须仍然自洽——幂等不能顺手把台账改成验不过。"""
    from aplus.forward import register, registry_tamper_report
    batch = [_cand(REV, 5), _cand(IC_ALL, 21)]
    register(tmp_path, "2026-10-14", batch, note="第三批")
    register(tmp_path, "2026-10-14", batch, note="第三批")
    assert registry_tamper_report(tmp_path)["tampered"] == []