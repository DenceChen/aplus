"""采集器本身：续跑与去重。

## 为什么这个脚本的测试全在盯着「重复行」

vintage 是**只能往前攒、不能回头补**的数据：今天不落盘，那天永久丢失。
这种数据一旦被污染，污染也是永久的——而且**看不见**：

一份行数翻倍的快照，行数、IC 权重、截面分位数全是「正常」的数字，
没有任何一处会报错。首版就真的落了一份 1170 行 / 5 只的分区，
而日志里照常打印「续跑：已有 5 只」。

所以这里的测试全部倒过来写：**构造一个必然出错的续跑场景，断言它不出错。**
"""
import sys

import pandas as pd
import pytest

import scripts.collect_financial_vintage as m
from aplus.vintage import write_snapshot


# ── 假源 ────────────────────────────────────────────────────────────

def _fake_source(monkeypatch, calls: list):
    """每只票 4 个报告期，行数固定，方便核对。

    **参数是 SECUCODE（带后缀）**，与生产一致——这正是首版栽的地方：
    请求侧和存储侧的代码格式不同，测试若两边都用同一种格式，
    就把 bug 一起盖住了。
    """
    def fake(secucode):
        calls.append(secucode)
        code = secucode.split(".")[0]
        return pd.DataFrame({
            "SECURITY_CODE": [code] * 4,
            "REPORT_DATE": ["2021-12-31", "2022-03-31",
                            "2022-06-30", "2022-09-30"],
            "BPS": [float(i) for i in range(4)],
        })
    monkeypatch.setattr(m, "_fetch", fake)
    monkeypatch.setattr(m, "SLEEP_BETWEEN_CALLS", 0)


@pytest.fixture
def daily(tmp_path, monkeypatch):
    """一个小股票池，文件名带交易所后缀——与生产一致。"""
    d = tmp_path / "daily"
    d.mkdir()
    for stem in ("000001.SZ", "000002.SZ", "600000.SH", "600001.SH"):
        (d / f"{stem}.parquet").write_bytes(b"x")
    monkeypatch.setattr(m, "DAILY_DIR", d)
    return d


# ── 1. 键空间：后缀不剥，续跑必失效 ─────────────────────────────────

def test_universe_keeps_both_code_formats(daily):
    """**根因测试。**

    日线文件名是 `000001.SZ.parquet`，`Path.stem` 给出 `000001.SZ`；
    源站返回帧里是裸 `000001`；而请求参数 `filter=(SECUCODE="…")`
    **又必须带后缀**（实测传裸代码返回 0 行）。

    所以正确答案是**同时保留两种格式**：字典的键是身份（裸代码），
    值是请求串（SECUCODE）。初版只有一个列表，于是两头都对不上——
    `c.zfill(6)` 对 9 位串是空操作，续跑每轮都失效，
    且静默写出行数翻倍的快照。
    """
    assert m.universe() == {
        "000001": "000001.SZ", "000002": "000002.SZ",
        "600000": "600000.SH", "600001": "600001.SH",
    }


def test_the_identity_key_and_the_request_string_are_different_formats(daily):
    """身份用于判续跑，请求串用于敲源站——混淆任何一个都会静默出错。"""
    pool = m.universe()
    assert all(len(k) == 6 and k.isdigit() for k in pool), "身份键必须是裸 6 位"
    assert all("." in v and v.split(".")[0] == k for k, v in pool.items()), (
        "请求串必须带交易所后缀——akshare 的 filter 是 (SECUCODE=\"…\")，"
        "传裸代码源站返回空帧")


def test_a_colliding_stem_pair_is_rejected_not_merged(tmp_path, monkeypatch):
    """两个不同文件剥成同一个代码时必须**炸出来**。

    A 股 6 位代码跨所唯一是事实，不是保证。而一个把两只票
    挤进同一个键的采集器，会静默地只留下一只——
    且丢的是哪一只取决于排序，不可复现。
    """
    d = tmp_path / "daily"
    d.mkdir()
    (d / "000001.SZ.parquet").write_bytes(b"x")
    (d / "000001.SH.parquet").write_bytes(b"x")
    monkeypatch.setattr(m, "DAILY_DIR", d)
    with pytest.raises(ValueError, match="重码"):
        m.universe()


# ── 2. 续跑：必须真的跳过 ───────────────────────────────────────────

def test_resume_skips_codes_already_in_the_partition(tmp_path, daily, monkeypatch):
    """抓完 4 只 → 再抓一轮，**一次网络请求都不该发**。"""
    calls: list = []
    _fake_source(monkeypatch, calls)
    root = tmp_path / "v"
    m.collect(m.universe(), root, "2026-10-13")

    assert len(calls) == 4
    calls.clear()
    res = m.collect(m.universe(), root, "2026-10-13")

    assert calls == [], f"续跑又发了 {len(calls)} 次请求：{calls}"
    assert len(res["df"]) == 16, "续跑把已有数据丢了"


def test_a_partial_sweep_is_completed_not_restarted(tmp_path, daily, monkeypatch):
    """断在中途（已落 2 只）→ 续跑只抓剩下 2 只，且总行数不变。"""
    root = tmp_path / "v"
    calls: list = []
    _fake_source(monkeypatch, calls)
    full = m.universe()
    done_first = dict(list(full.items())[:2])       # 000001 / 000002
    todo = sorted(s for c, s in full.items() if c not in done_first)
    m.collect(done_first, root, "2026-10-13")
    assert len(calls) == 2

    calls.clear()
    res = m.collect(m.universe(), root, "2026-10-13")
    assert sorted(calls) == todo, (
        f"续跑抓错了集合：{sorted(calls)}——应该只抓没做过的 {todo}")
    assert len(res["df"]) == 16, f"续跑后行数 {len(res['df'])}，应为 4 只 × 4 期"


def test_rerunning_never_duplicates_rows(tmp_path, daily, monkeypatch):
    """**首版真实发生的事**：585 行 → 1170 行，日志一切正常。"""
    _fake_source(monkeypatch, [])
    root = tmp_path / "v"
    a = m.collect(m.universe(), root, "2026-10-13")
    b = m.collect(m.universe(), root, "2026-10-13")
    assert len(a["df"]) == len(b["df"]) == 16
    assert not b["df"].duplicated(subset=m.ROW_KEY).any()


def test_a_broken_resume_still_cannot_duplicate_rows(tmp_path, daily,
                                                     monkeypatch):
    """**去重是第二道防线。**

    假设续跑判据又坏了（键空间再次不一致、续跑标记丢失、
    有人手工重跑并回填了分区）：重复行照样不许进分区。
    第一道防线被绕过时，正是这一道在拦。
    """
    _fake_source(monkeypatch, [])
    root = tmp_path / "v"
    m.collect(m.universe(), root, "2026-10-13")

    # 绕过续跑：强制把已完成的两只重新塞回去
    part = m.snapshot_path(root, "2026-10-13")
    have = pd.read_parquet(part)
    polluted = pd.concat([have, have[have["SECURITY_CODE"] == "000001"]],
                         ignore_index=True)
    polluted.to_parquet(part, index=False)
    assert len(pd.read_parquet(part)) == 20, "构造前提失败"

    res = m.collect(m.universe(), root, "2026-10-13")
    assert len(res["df"]) == 16
    assert res["dup_dropped"] == 4, "重复行被丢掉了却没有被计数"
    assert not res["df"].duplicated(subset=m.ROW_KEY).any()


def test_historical_duplicates_are_cleaned_not_inherited(tmp_path, daily,
                                                         monkeypatch):
    """已落盘分区里的重复行必须被清掉，而不是一代代继承下去。"""
    _fake_source(monkeypatch, [])
    root = tmp_path / "v"
    m.collect(m.universe(), root, "2026-10-13")
    part = m.snapshot_path(root, "2026-10-13")
    have = pd.read_parquet(part)
    pd.concat([have, have.head(3)], ignore_index=True).to_parquet(part, index=False)

    res = m.collect([], root, "2026-10-13")
    assert len(res["df"]) == 16, "续跑时没有顺手清理历史重复行"


# ── 3. manifest：重复不许被登记成证据 ────────────────────────────────

def test_a_source_that_duplicates_a_report_period_is_reported_not_just_cleaned(
        tmp_path, monkeypatch):
    """**这道门必须开在抓回来的那一帧上，否则它是死代码。**

    首版把闸放在 `main()` 里检查「去重后是否仍有重复」，
    而 `collect` 已经先去过一次重了 —— 那道闸**永远不可能触发**，
    却看起来像一层安全网。源站坏掉时它一声不吭。

    正确位置是抓回来的原始帧：那里的重复才真的是源站的问题。
    """
    def fake(code):
        return pd.DataFrame({
            "SECURITY_CODE": [code, code],
            "REPORT_DATE": ["2021-12-31", "2021-12-31"],   # 源站自己重了
            "BPS": [1.0, 2.0],
        })
    monkeypatch.setattr(m, "_fetch", fake)
    monkeypatch.setattr(m, "SLEEP_BETWEEN_CALLS", 0)

    res = m.collect({"000001": "000001.SZ", "000002": "000002.SZ"},
                    tmp_path / "v", "2026-10-13")
    assert res["source_dup"] == {"000001": 1, "000002": 1}, (
        "源站自身的重复没有被单独记账——main 的闸因此形同虚设")
    # 同时落盘的分区仍然必须干净：不能因为要报错就写出一份坏分区
    assert not res["df"].duplicated(subset=m.ROW_KEY).any()


def test_a_clean_source_reports_no_source_duplication(tmp_path, daily,
                                                       monkeypatch):
    _fake_source(monkeypatch, [])
    res = m.collect(m.universe(), tmp_path / "v", "2026-10-13")
    assert res["source_dup"] == {}


def test_main_refuses_to_register_a_manifest_when_the_source_duplicates():
    """`main()` 的闸门路径必须在源码里，且真的以 `return 2` 退出。"""
    import inspect
    src = inspect.getsource(m.main)
    assert 'res["source_dup"]' in src
    assert "return 2" in src


# ── 4. 契约 ──────────────────────────────────────────────────────────

def test_the_module_still_refuses_to_register_a_partial_sweep():
    """半份快照冒充完整快照比没有更坏——manifest 只在整轮跑完后写。"""
    import inspect
    src = inspect.getsource(m.main)
    assert "一只都没抓到" in src
    assert src.index("一只都没抓到") < src.index("write_snapshot")


def test_the_key_columns_are_the_ones_vintage_actually_needs():
    """去重键写错就等于没去重：代码 + 报告期才是这只票那一行。"""
    assert m.ROW_KEY == ["SECURITY_CODE", "REPORT_DATE"]

# ── 5. 保留窗口：存不下就等于全丢 ────────────────────────────────────

def _long_history(n_periods=40):
    """一只票，40 个报告期，倒序无关地打乱行序。"""
    dates = pd.date_range("2015-03-31", periods=n_periods, freq="QE").strftime(
        "%Y-%m-%d")
    return pd.DataFrame({
        "SECURITY_CODE": ["000001"] * n_periods,
        "REPORT_DATE": dates,
        "BPS": [float(i) for i in range(n_periods)],
    })


def test_only_the_most_recent_periods_are_kept():
    df = _long_history(40)
    got = m.keep_recent(df, 12)
    assert len(got) == 12
    assert got["REPORT_DATE"].max() == df["REPORT_DATE"].max(), (
        "保留的不是最新的一批")


def test_the_window_is_applied_per_code_not_to_the_whole_frame():
    """**首版这里写的是整帧取前 N 行**，而 `universe()` 按代码排序——
    于是后抓到的票会被整帧截断吃掉，一半票的数据凭空消失。

    三只票各 40 期，各留 12 期 = 36 行；整帧取 36 行则可能全是前两只。
    """
    parts = []
    for code in ("000001", "000002", "000003"):
        d = _long_history(40)
        d["SECURITY_CODE"] = code
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    got = m.keep_recent(df, 12)
    assert got["SECURITY_CODE"].value_counts().to_dict() == {
        "000001": 12, "000002": 12, "000003": 12}


def test_the_window_survives_a_shuffled_row_order():
    """**抓掩码错位那条 bug 的测试。**

    `sort_values` 保留索引标签但打乱行序，`cumcount()` 的 Series
    内部顺序跟的是乱序——直接 `.to_numpy()` 当掩码就会行与掩码错位。
    错位后**每行仍是某个合法报告期、行数也对**，没有任何异常可查。
    只有打乱行序再断言「每只票最新 12 期」才抓得到。
    """
    parts = []
    for code in ("000001", "000002", "000003"):
        d = _long_history(40)
        d["SECURITY_CODE"] = code
        parts.append(d)
    df = pd.concat(parts, ignore_index=True).sample(frac=1, random_state=3)
    df = df.reset_index(drop=True)

    got = m.keep_recent(df, 12)
    for code, sub in got.groupby("SECURITY_CODE"):
        want = sorted(df[df["SECURITY_CODE"] == code]["REPORT_DATE"],
                      reverse=True)[:12]
        assert sorted(sub["REPORT_DATE"], reverse=True) == want, (
            f"{code} 保留的不是它自己最新的 12 期——掩码与行序错位")


def test_a_new_listing_keeps_every_period_it_has():
    """次新股本来就没有 12 期历史，**不许去别处凑数**。"""
    df = _long_history(5)
    assert len(m.keep_recent(df, 12)) == 5


def test_the_retention_policy_is_explicit_and_justified_in_the_source():
    """丢数据必须是写明的、被量过的决定，不是实现里的一个数字。

    65 GB/年 vs 6.8 GB/年 的账，和「为什么丢掉的是不可能被读到的部分」，
    都要留在文件里——否则将来有人看到一个 `keep_periods=12`，
    会以为那是个可以随手调大的参数。
    """
    import inspect
    src = inspect.getsource(m)
    assert "KEEP_PERIODS" in src
    assert "GB/年" in src and "存不下" in src
    assert "pit_frame" in src, "没有说明 vintage 在结构上只能回答今天之后的问题"


def test_the_documented_default_matches_the_constant():
    import inspect
    doc = inspect.getsource(m.collect)
    assert "KEEP_PERIODS" in doc
    assert m.KEEP_PERIODS == 12


# ── 6. 幂等闸：补跑作业必须零成本 ────────────────────────────────────

def test_ensure_is_a_no_op_once_today_is_registered(tmp_path, daily, monkeypatch):
    """**补跑是这个模块唯一能补救的动作，所以它必须是免费的。**

    日历触发不会补跑——机器睡着就永久错过一天。所以挂了两个触发点，
    代价是同一天可能被触发两次。若第二次还要跑 45 分钟，
    两个点就把机器占满了，而真正的日更反而排不进来。
    """
    calls: list = []
    _fake_source(monkeypatch, calls)
    root = tmp_path / "v"
    write_snapshot(root, "2026-10-13", pd.DataFrame({
        "SECURITY_CODE": ["000001"], "REPORT_DATE": ["2021-12-31"]}))

    monkeypatch.setattr(sys, "argv",
                        ["collect_financial_vintage.py", "--ensure",
                         "--root", str(root), "--date", "2026-10-13"])
    assert m.main() == 0
    assert calls == [], "已登记却仍然发起了抓取——补跑闸没生效"


def test_ensure_still_collects_when_today_is_missing(tmp_path, daily,
                                                     monkeypatch):
    """闸只拦「已完成」，不能把**该跑的那一次**也拦掉。"""
    root = tmp_path / "v"
    calls: list = []
    _fake_source(monkeypatch, calls)
    monkeypatch.setattr(m, "MIN_FREE_GB", 0)
    monkeypatch.setattr(sys, "argv",
                        ["collect_financial_vintage.py", "--ensure",
                         "--root", str(root), "--date", "2026-10-13",
                         "--sample"])
    assert m.main() == 0
    # `daily` 夹具只有 4 个 stem，`--sample` 取前 5 也只有 4
    assert len(calls) == 4, "闸把没跑的那次也拦掉了"


def test_a_full_disk_stops_collection_without_deleting_anything(
        tmp_path, daily, monkeypatch):
    """**采集的失败必须可见但无害。**

    写满磁盘会连累 `run_daily` 和整个仓库；缺一天 vintage 的损失
    远小于这一条。所以水位不足时**停止采集，不删任何已有数据**。
    """
    calls: list = []
    _fake_source(monkeypatch, calls)
    monkeypatch.setattr(m, "MIN_FREE_GB", 10 ** 9)      # 假装盘满了
    monkeypatch.setattr(sys, "argv",
                        ["collect_financial_vynthetic.py", "--root",
                         str(tmp_path / "v"), "--date", "2026-10-13"])
    assert m.main() == 4
    assert calls == [], "磁盘不足却仍然发起了抓取"
