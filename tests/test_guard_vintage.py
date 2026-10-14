"""vintage 守卫：漏掉一天是**永久损失**，所以守卫本身不能出错。

## 为什么这个守卫要用测试钉住，而不是「看一眼逻辑对就行」

采集是本项目唯一一个**不可逆**的任务：今天不落盘，源站那天服务过的
原始值永久丢失，没有补救手段。

于是守卫有两种失败模式，两种都很坏：
1. **该补跑时没跑** —— 一天永久丢失，且**没有任何异常**；
2. **不该跑时跑了** —— 白占 45 分钟机器，把当天真正的日更挤掉。

第 2 种不会丢数据，但它会**制造**第 1 种：一天补跑两次，
第二天同一天又被触发，机器永远被占着，真正的采集反而排不进来。
所以「不误触发」和「不漏触发」同等重要。
"""
import json

import pandas as pd
import pytest

from aplus.vintage import write_snapshot
from scripts.guard_vintage import days_missed, registered_days


def _register(root, days, rows=1):
    for d in days:
        write_snapshot(root, d, pd.DataFrame({
            "SECURITY_CODE": [f"00000{i}" for i in range(rows)],
            "REPORT_DATE": ["2021-12-31"] * rows}))


# ── 判据：看 manifest，不看文件在不在 ────────────────────────────────

def test_a_day_counts_as_done_only_when_the_manifest_says_so(tmp_path):
    """**半份快照冒充完整快照，比没有更坏。**

    分区文件存在只说明「写过」，只有 manifest 有条目才说明「整轮抓完了」。
    判据若看文件，中断的分区会被当成已完成——**那正是要补跑的那一种**。
    """
    root = tmp_path / "v"
    write_snapshot(root, "2026-10-14", pd.DataFrame({"A": [1]}))
    assert "2026-10-14" in registered_days(root), (
        "write_snapshot 会登记 manifest，判据本身没错")

    # 只落文件、不登记 manifest（等价于写到一半被打断）
    root2 = tmp_path / "v2"
    p = root2 / "date=2026-10-14" / "financial.parquet"
    p.parent.mkdir(parents=True)
    pd.DataFrame({"A": [1]}).to_parquet(p, index=False)
    assert "2026-10-14" not in registered_days(root2)


def test_registered_days_ignores_malformed_entries(tmp_path):
    """manifest 是 jsonl，一条坏行不该让整个守卫抛异常——
    **守卫崩了就等于没守卫**，而它崩的方式会很难被注意到。"""
    root = tmp_path / "v"
    _register(root, ["2026-10-12"])
    with (root / "_manifest.jsonl").open("a", encoding="utf-8") as f:
        f.write("{ 这不是 json\n")
        f.write(json.dumps({"written_at": "x"}) + "\n")   # 缺 snapshot_date
    assert registered_days(root) == {"2026-10-12"}


# ── 缺口检测 ────────────────────────────────────────────────────────

def test_a_gap_inside_the_span_is_detected(tmp_path):
    """日历覆盖范围内，缺登记的日子必须被抓出来。"""
    root = tmp_path / "v"
    _register(root, ["2026-10-12", "2026-10-15"])
    cal = {"2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"}
    missed = days_missed(root, today="2026-10-15", cal=cal)
    assert missed == ["2026-10-13", "2026-10-14"], (
        f"缺口没被识别：{missed}——**那两天的原始值永久丢失了**")


def test_no_gap_means_nothing_to_rerun(tmp_path):
    root = tmp_path / "v"
    _register(root, ["2026-10-12", "2026-10-13", "2026-10-14"])
    cal = {"2026-10-12", "2026-10-13", "2026-10-14"}
    assert days_missed(root, today="2026-10-14", cal=cal) == []


def test_an_empty_collection_is_not_reported_as_a_giant_gap(tmp_path):
    """一天快照都没有时**不该报缺口**——那是「还没开始」，不是「漏了」。
    从第一天到今天整段报成缺口，会让守卫第一次运行时喊几百天。"""
    cal = {"2026-10-13", "2026-10-14"}
    assert days_missed(tmp_path / "empty", today="2026-10-14", cal=cal) == []


def test_todays_absence_is_not_yet_a_gap_before_collection_time(tmp_path):
    """守卫 23:30 跑，今天 22:30 已该有快照。

    但 `days_missed` 本身不该对「今天」有意见——
    **判据只回答「历史上有没有洞」**，是否需要补跑是 `main()` 的职责。
    混在一起会让同一个函数既当账本又当触发器，改一处必然错另一处。
    """
    root = tmp_path / "v"
    _register(root, ["2026-10-12", "2026-10-13"])
    cal = {"2026-10-12", "2026-10-13"}
    assert "2026-10-14" in days_missed(root, today="2026-10-14", cal=cal)


def test_guard_main_does_not_rerun_when_today_is_registered(tmp_path,
                                                           monkeypatch):
    """**不误触发**：已登记就一次网络请求都不发。

    一天补跑两次不是「多做了一件好事」——它把机器占满 45 分钟，
    于是第二天真正的采集反而排不进来，**制造**出真正的漏采。
    """
    import scripts.guard_vintage as g
    root = tmp_path / "v"
    _register(root, ["2026-10-14"])
    calls = []
    monkeypatch.setattr(g, "VINTAGE_ROOT", root)
    monkeypatch.setattr(g, "span", lambda *a, **k: {
        "first": "2026-10-14", "last": "2026-10-14",
        "n_days": 1, "calendar_days": 1, "gaps": []})
    monkeypatch.setattr(g, "days_missed", lambda *a, **k: [])
    monkeypatch.setattr(g.subprocess, "run",
                        lambda *a, **k: calls.append(a) or _R0())
    g.main()
    assert calls == [], "已登记却仍然发起了补跑"


def test_guard_main_reruns_when_today_is_missing(tmp_path, monkeypatch):
    """**不漏触发**：今天没登记必须补跑。"""
    import scripts.guard_vintage as g
    root = tmp_path / "v"
    _register(root, ["2026-10-13"])
    calls = []
    monkeypatch.setattr(g, "VINTAGE_ROOT", root)
    monkeypatch.setattr(g, "span", lambda *a, **k: {
        "first": "2026-10-13", "last": "2026-10-13",
        "n_days": 1, "calendar_days": 1, "gaps": []})
    monkeypatch.setattr(g, "days_missed", lambda *a, **k: [])
    monkeypatch.setattr(g.subprocess, "run",
                        lambda *a, **k: calls.append(a) or _R0())
    g.main()
    assert len(calls) == 1
    assert "--ensure" in calls[0][0], (
        "补跑必须带 --ensure，否则重复触发会白跑 45 分钟")


def test_the_guard_is_independent_of_run_daily():
    """**两个守卫不能互相拖垮。**

    若 vintage 的补跑挂在 `guard_daily` 里，就建立了
    「run_daily 失败 → vintage 断」这条依赖，而断一天是永久损失。
    """
    import inspect

    import scripts.guard_vintage as g
    # 只查 `main` 的源码：模块 docstring 里**必须**解释为什么不依赖 run_daily，
    # 那段解释里当然会出现 run_daily。判据要看**行为**，不是看词。
    body = inspect.getsource(g.main)
    assert "run_daily" not in body, "守卫里调用了 run_daily——依赖方向错了"
    assert "com.aplus.guardvintage" in inspect.getsource(g)


class _R0:
    returncode = 0
    stdout = ""
    stderr = ""

def test_the_guard_never_recollects_a_past_day_under_a_past_date(
        tmp_path, monkeypatch):
    """**本守卫最重要的一条纪律。**

    源站只服务当前值。今天去抓，拿到的永远是今天的数；
    把它标成 10-13 的快照写进 manifest，就是一条**伪造的证据**——
    「10-13 源站服务的是这个」，而它明明来自 10-14。

    **缺口是已知的损失，伪造是未知的污染。**
    后者更坏：它会让将来的 PIT 判决建立在一个从不存在的时间切片上，
    而且表面上完全正常。

    所以：历史有缺口时，守卫**仍然只抓今天**，绝不传 `--date`。
    """
    import scripts.guard_vintage as g
    root = tmp_path / "v"
    _register(root, ["2026-10-12", "2026-10-14"])       # 故意缺 10-13
    calls = []
    monkeypatch.setattr(g, "VINTAGE_ROOT", root)
    monkeypatch.setattr(g, "span", lambda *a, **k: {
        "first": "2026-10-12", "last": "2026-10-14",
        "n_days": 2, "calendar_days": 3, "gaps": [{"missing_days": 1}]})
    monkeypatch.setattr(g.subprocess, "run",
                        lambda *a, **k: calls.append(a) or _R0())
    g.main()
    for c in calls:
        assert "--date" not in c[0], (
            f"守卫试图用过去的日期重新采集：{c[0]}——"
            f"那会把今天的值写成一个从未存在过的历史时间切片")


def test_a_past_gap_is_reported_as_permanent_loss(tmp_path, monkeypatch,
                                                 capsys):
    """缺口必须**出现在 stdout**——cron 场景下没人看退出码。"""
    import scripts.guard_vintage as g
    root = tmp_path / "v"
    _register(root, ["2026-10-12", "2026-10-14"])
    monkeypatch.setattr(g, "VINTAGE_ROOT", root)
    monkeypatch.setattr(g, "span", lambda *a, **k: {
        "first": "2026-10-12", "last": "2026-10-14",
        "n_days": 2, "calendar_days": 3, "gaps": [{"missing_days": 1}]})
    monkeypatch.setattr(g, "days_missed",
                        lambda *a, **k: ["2026-10-13"])
    monkeypatch.setattr(g.subprocess, "run", lambda *a, **k: _R0())
    g.main()
    out = capsys.readouterr().out
    assert "2026-10-13" in out and "永久丢失" in out, (
        "缺口没有出现在输出里——在 launchd 下没有人会看到它")


# ── 交易日历：周末/节假日不该被报成缺口 ──────────────────────────────

def test_the_trading_calendar_is_the_union_of_daily_dates(tmp_path):
    """**单只股票的日期集不是交易日历。**

    实测：000005.SZ 只有 1290 天（上市晚 + 停牌），而全市场是 1884 天。
    只看一只票，上市前的日子会被误判成「没采集」——
    而那恰恰是一个不该报警的噪声源。
    """
    import scripts.guard_vintage as g
    d = tmp_path / "daily"
    d.mkdir()
    pd.DataFrame({"date": ["2026-10-09", "2026-10-12", "2026-10-13"]}).to_parquet(
        d / "000001.SZ.parquet", index=False)
    pd.DataFrame({"date": ["2026-10-13", "2026-10-14", "2026-10-15"]}).to_parquet(
        d / "000002.SZ.parquet", index=False)      # 停牌 → 缺 10-12
    got = g.trading_days(daily_dir=d, refresh=True)
    assert got == {"2026-10-09", "2026-10-12", "2026-10-13",
                   "2026-10-14", "2026-10-15"}


def test_an_unknown_date_is_treated_as_a_trading_day(tmp_path):
    """**误判方向不对称，判据必须朝「宁可多采」偏。**

    日历来自日线数据，而日线由 `run_daily`（18:30）推进。
    所以采集器在 22:30 跑时，日历通常**还停在昨天**——
    「今天是不是交易日」这件事当时根本无从得知。
    若把未知当作非交易日，就等于在最需要采集的那天主动跳过它。
    """
    import scripts.guard_vintage as g
    d = tmp_path / "daily"
    d.mkdir()
    pd.DataFrame({"date": ["2026-10-13"]}).to_parquet(
        d / "000001.SZ.parquet", index=False)
    cal = g.trading_days(daily_dir=d, refresh=True)
    assert g.is_gap_day("2026-10-14", cal=cal), (
        "日历之外的日期被当成了非交易日——那是最容易丢数据的一天")


def test_a_known_non_trading_day_is_not_a_gap(tmp_path):
    """反过来也必须成立，否则每个周末都会报一次「永久丢失」——
    **告警疲劳会让防永久损失的探测器在几周内失效**。"""
    import scripts.guard_vintage as g
    cal = {"2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"}
    assert not g.is_gap_day("2026-10-10", cal=cal)   # 周六
    assert not g.is_gap_day("2026-10-11", cal=cal)   # 周日
    assert g.is_gap_day("2026-10-13", cal=cal)


def test_weekends_are_not_reported_as_missed_days(tmp_path):
    """端到端：10-09(五) 与 10-12(一) 都登记，中间那个周末不算缺口。"""
    import scripts.guard_vintage as g
    root = tmp_path / "v"
    _register(root, ["2026-10-09", "2026-10-12"])
    cal = {"2026-10-09", "2026-10-12"}
    assert g.days_missed(root, today="2026-10-12", cal=cal) == []


def test_a_real_gap_inside_trading_days_is_still_reported(tmp_path):
    """**过滤非交易日不能把真缺口一起滤掉。**"""
    import scripts.guard_vintage as g
    root = tmp_path / "v"
    _register(root, ["2026-10-09", "2026-10-13"])   # 缺 10-12(周一)
    cal = {"2026-10-09", "2026-10-12", "2026-10-13"}
    assert g.days_missed(root, today="2026-10-13", cal=cal) == ["2026-10-12"]


def test_the_real_calendar_covers_the_last_three_months():
    """拿真实数据验一遍：日历本身不能是空的或短得离谱。"""
    import scripts.guard_vintage as g
    cal = g.trading_days()
    assert len(cal) > 1000, f"交易日历只有 {len(cal)} 天——来源坏了"
    assert max(cal) >= "2026-09-01", (
        f"日历最新只到 {max(cal)}，落后太多，缺口判定会失效")
