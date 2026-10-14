"""Vintage 累积：把「PIT 未建立」变成「PIT 正在建立」的唯一途径。

## 为什么这个模块的测试比它本身还长

`financial.py` 写明：当前源每个报告期只存一行、无版本维度，**服务的后修值**。
出路 A（只留从未被改动的记录）已被 `diag_financial_lookahead.py` 实测否决。
**只剩出路 B：从今天起每日落盘攒 vintage。**

而出路 B 有个别的任务都没有的性质：**价值随延迟毁灭**。
今天不落盘，那天的原始值永久丢失——不是难补，是补不回来。

所以这个模块全部的风险都集中在两件事上：

1. **写错了历史**——覆盖掉某一天的快照 = 永久销毁一段不可再生的记录；
2. **读出了未来**——vintage 读取器一旦回退到「最新可用快照」，
   就把整套采集要消除的前视偏差又请回来了，而且**看起来完全正常**。

第 2 条是最险的：一个会偷看未来的 vintage 读取器，
产出的 PIT 帧和现在直接读缓存**一模一样地正常**。
"""
import numpy as np
import pandas as pd
import pytest

from aplus.vintage import (
    VINTAGE_ROOT, available_days, latest_on_or_before, pit_frame,
    read_manifest, snapshot_path, span, verify_integrity, write_snapshot,
)


# ── 造快照 ─────────────────────────────────────────────────────────

def _raw(day, code="000001", bps=10.0, notice="2022-04-28"):
    """一天的全市场快照。`bps` 就是「**那天**源站服务的账面值」。"""
    return pd.DataFrame({
        "SECURITY_CODE": [code],
        "REPORT_DATE": ["2021-12-31"],
        "REPORT_TYPE": ["年报"],
        "NOTICE_DATE": [notice],
        "UPDATE_DATE": [notice],
        "BPS": [bps],
        "ROEJQ": [0.12],
        "snapshot_date": [day],
    })


def _write(root, day, **kw):
    return write_snapshot(root, day, _raw(day, **kw))


# ── 1. 写：绝不能动到历史 ───────────────────────────────────────────

def test_writing_today_leaves_yesterday_byte_identical(tmp_path):
    """**本模块最贵的一条。** 覆盖掉任何一天的历史 = 永久销毁一段记录。

    所以断言的是**文件内容与 mtime 都不变**，不只是「还在」。
    """
    root = tmp_path / "v"
    _write(root, "2026-10-12", bps=10.0)
    p = snapshot_path(root, "2026-10-12")
    before_bytes = p.read_bytes()
    before_mtime = p.stat().st_mtime_ns

    _write(root, "2026-10-13", bps=10.0)

    assert p.read_bytes() == before_bytes, "前一天的快照被改写了"
    assert p.stat().st_mtime_ns == before_mtime, "前一天的快照被重写了"


def test_rewriting_the_same_day_replaces_only_that_day(tmp_path):
    """同一天重跑必须幂等（否则重试会写出重复行，污染计数）。"""
    root = tmp_path / "v"
    _write(root, "2026-10-13", bps=10.0)
    _write(root, "2026-10-13", bps=11.0)
    assert len(pit_frame(root, "2026-10-13")) == 1
    assert pit_frame(root, "2026-10-13").iloc[0]["BPS"] == 11.0


def test_a_day_snapshot_carries_every_row_the_source_served(tmp_path):
    """少抓一列 = 那一列的历史永久丢失。所以存的是**整帧**，不做列裁剪。"""
    root = tmp_path / "v"
    df = pd.DataFrame({"SECURITY_CODE": ["000001", "000002"],
                       "BPS": [10.0, 20.0],
                       "EPSJB": [1.1, 2.2]})
    write_snapshot(root, "2026-10-13", df)
    got = pit_frame(root, "2026-10-13")
    assert set(got["SECURITY_CODE"]) == {"000001", "000002"}
    assert "EPSJB" in got.columns


# ── 2. 读：绝不能看见未来 ───────────────────────────────────────────

def test_a_future_snapshot_is_never_used_for_an_earlier_date(tmp_path):
    """**整套采集要消除的东西，就在这条测试里。**

    只有 2026-10-13 一份快照，却问 2026-10-12 的 PIT 帧。
    正确行为是**拒绝**，而不是拿 10-13 那份凑合——
    拿它凑合产出的 PIT 帧，和直接读当前缓存**一样地正常**，
    于是前视偏差原封不动地回来了，只是多了一个「已做 PIT」的标签。
    """
    root = tmp_path / "v"
    _write(root, "2026-10-13", bps=10.0)
    with pytest.raises(ValueError, match="没有.*2026-10-12"):
        pit_frame(root, "2026-10-12")


def test_an_earlier_snapshot_is_used_when_the_exact_day_is_missing(tmp_path):
    """缺口**只能向前回退，绝不向后取**。

    10-12 缺一份而问 10-13：能用的只有 10-11 那份。
    这在 PIT 意义上是保守的（少用了信息，不会前视），所以允许，
    但必须把**实际用了哪一天**告诉调用方。
    """
    root = tmp_path / "v"
    _write(root, "2026-10-11", bps=10.0)
    got = pit_frame(root, "2026-10-13")
    assert len(got) == 1
    assert got.attrs["snapshot_date"] == "2026-10-11", (
        "回退时没有标明实际用的快照日——调用方无法知道它拿到的是哪一刻的值")


def test_same_day_is_preferred_over_the_earlier_one(tmp_path):
    root = tmp_path / "v"
    _write(root, "2026-10-12", bps=10.0)
    _write(root, "2026-10-13", bps=99.0)
    got = pit_frame(root, "2026-10-13")
    assert got.iloc[0]["BPS"] == 99.0
    assert got.attrs["snapshot_date"] == "2026-10-13"


def test_latest_on_or_before_never_returns_a_later_day(tmp_path):
    root = tmp_path / "v"
    for d in ("2026-10-09", "2026-10-11", "2026-10-13"):
        _write(root, d)
    assert latest_on_or_before(root, "2026-10-12") == "2026-10-11"
    assert latest_on_or_before(root, "2026-10-08") is None


def test_the_window_covered_is_bounded_by_real_snapshots(tmp_path):
    """**攒够之前，「哪些窗口可回答」必须如实说，不能乐观外推。**

    只有 3 天快照，就不能声称覆盖了一个月——声称了，将来就会拿它去
    回测一个没有 vintage 的区间，而那正是本模块存在的理由。
    """
    root = tmp_path / "v"
    for d in ("2026-10-11", "2026-10-12", "2026-10-13"):
        _write(root, d)
    s = span(root)
    assert s["first"] == "2026-10-11" and s["last"] == "2026-10-13"
    assert s["n_days"] == 3
    assert s["calendar_days"] == 3, (
        f"声称覆盖 {s['calendar_days']} 个自然日，实际只有 {s['n_days']} 份快照")


# ── 3. 完整性：能否证明「没被悄悄改过」 ────────────────────────────

def test_the_manifest_records_a_digest_for_every_snapshot(tmp_path):
    """账本第 78 条的教训：我用一个不可信的字段（`UPDATE_DATE`）当证据，
    还差点当成结论。**采集数据若不能自证未被改写，就等于没采集。**

    所以每落一份快照就往 append-only 的 manifest 追加一条摘要，
    事后可以逐份核对：文件内容是否仍与当初落盘时一致。
    """
    root = tmp_path / "v"
    _write(root, "2026-10-12")
    _write(root, "2026-10-13")
    m = read_manifest(root)
    assert [e["snapshot_date"] for e in m] == ["2026-10-12", "2026-10-13"]
    assert all(len(e["sha256"]) == 64 for e in m)
    assert all(e["n_rows"] == 1 for e in m)


def test_integrity_check_passes_on_an_untouched_collection(tmp_path):
    root = tmp_path / "v"
    _write(root, "2026-10-12")
    _write(root, "2026-10-13")
    rep = verify_integrity(root)
    assert rep["ok"] and rep["checked"] == 2 and rep["tampered"] == []


def test_integrity_check_catches_a_silently_edited_snapshot(tmp_path):
    """有人事后改了某一天的快照——必须被抓到，而不是安静地照用。

    这是与账本第 78 条同源的风险：那份数据的价值全部来自
    「它在当时就是那样」，一旦被改过，**它就不再是证据**。
    """
    root = tmp_path / "v"
    _write(root, "2026-10-12", bps=10.0)
    _write(root, "2026-10-13")
    write_snapshot(root, "2026-10-12", _raw("2026-10-12", bps=999.0),
                   amend=False)      # 绕过 manifest，像一次外部篡改
    rep = verify_integrity(root)
    assert not rep["ok"]
    assert "2026-10-12" in rep["tampered"]


def test_the_manifest_is_append_only_when_a_day_is_rewritten(tmp_path):
    """重跑同一天要在 manifest 留**两条**，不能原地覆盖。

    原地覆盖就抹掉了「这一天的值在 10-13 变过」这个事实——
    而那个事实正是 vintage 唯一要回答的问题。
    """
    root = tmp_path / "v"
    _write(root, "2026-10-13", bps=10.0)
    _write(root, "2026-10-13", bps=11.0)
    m = read_manifest(root)
    assert len(m) == 2, "manifest 覆盖了旧记录——重跑痕迹被抹掉了"
    assert m[0]["n_rows"] == m[1]["n_rows"]
    assert latest_on_or_before(root, "2026-10-13") == "2026-10-13"


def _realistic_frame(n=200):
    """**真实财报帧的形状**：pandas StringDtype、object、float32、全 NaN 列。

    为什么必须专门造这种数据：初版 `write_snapshot` 的摘要取自**内存帧**，
    而校验取自**文件**。玩具帧恰好能无损往返，所以那个 bug 一次也没被测出来；
    真实数据有 15 个 StringDtype 列 + 27 个 object 列，
    `NA`/`None`/`NaN` 在 parquet 往返里换表示 → 摘要**永远对不上**，
    完整性校验恒红，而红的原因和数据本身无关。
    """
    rng = np.random.default_rng(7)
    return pd.DataFrame({
        "SECURITY_CODE": pd.array([f"{i:06d}" for i in range(n)],
                                  dtype="string"),
        "NOTICE_DATE": pd.array([None] * (n // 2)
                                + ["2022-04-28"] * (n - n // 2),
                                dtype="string"),
        "BPS": rng.choice([np.nan, 1.5, 20.0], n).astype("float32"),
        "ROEJQ": rng.choice([np.nan, 0.12], n).astype("float32"),
        "NAME": [None] * (n // 2) + [f"股{i}" for i in range(n - n // 2)],
        "EMPTY": [np.nan] * n,
    })


def test_integrity_holds_immediately_after_writing_a_realistic_frame(tmp_path):
    """**这条会抓住摘要取错对象那个 bug。**

    写完就必须能校验通过——如果 manifest 记的是内存帧而校验读的是文件，
    这里会红，且红得莫名其妙。
    """
    root = tmp_path / "v"
    write_snapshot(root, "2026-10-13", _realistic_frame())
    rep = verify_integrity(root)
    assert rep["ok"], (
        f"刚落盘就校验失败：tampered={rep['tampered']} missing={rep['missing']}——"
        f"摘要取的不是落盘产物")


def test_the_manifest_digest_is_reproducible_from_the_file_alone(tmp_path):
    """manifest 必须是关于**产物**的陈述，只凭文件就能复现。"""
    from aplus.vintage import _digest
    root = tmp_path / "v"
    write_snapshot(root, "2026-10-13", _realistic_frame())
    want = read_manifest(root)[-1]["sha256"]
    got = _digest(pd.read_parquet(snapshot_path(root, "2026-10-13"))
                  .drop(columns=["snapshot_date"], errors="ignore"))
    assert got == want


# ── 4. 契约：不许被当成「已经有 PIT 了」 ────────────────────────────

def test_the_module_states_that_vintage_must_accumulate_first():
    """攒够之前，本模块**不能**被当成「PIT 已解决」。

    模块 docstring 必须自己说这句，而不是等别人来问。
    """
    import inspect

    import aplus.vintage as m
    src = inspect.getsource(m)
    assert "不是 PIT" in src or "PIT 尚未建立" in src
    assert "永久丢失" in src


def test_default_root_points_at_the_reports_directory():
    assert VINTAGE_ROOT.parts[-2:] == ("reports", "financial_vintage")


def test_pit_frame_never_invents_rows_for_a_day_with_no_snapshot(tmp_path):
    """没有快照的日子返回不了数据，也**不许**返回空帧冒充「那天就是这样」。

    空帧会被下游当成「那天全市场没有财报」，那是彻底的错误结论。
    """
    root = tmp_path / "v"
    _write(root, "2026-10-13")
    with pytest.raises(ValueError):
        pit_frame(root, "2020-01-02")


def test_the_manifest_shape_matches_the_file_on_disk(tmp_path):
    """**manifest 的 `n_cols` 必须是文件的真实列数。**

    `write_snapshot` 自己加了一列 `snapshot_date`，而摘要为了可复现
    把它 `drop` 掉。初版把 drop 之后的 shape 直接写进 manifest，
    于是 manifest 记 141、文件实际 142。

    `n_rows` 侥幸没错（删列不改行数），所以这个 off-by-one
    **躲过了所有交叉检查**——两个字段里只有一个错，比两个都错更难发现。

    后果不是「算错了」，是**审计记录不自证**：打开文件数一遍列，
    和 manifest 对不上。在一份专门用来让人相信「这份数据没被动过」
    的账本里，一条对不上的记录足以让人开始怀疑整本账。
    """
    import json

    from aplus.vintage import manifest_path, write_snapshot
    df = pd.DataFrame({"SECURITY_CODE": ["000001", "000002"],
                       "REPORT_DATE": ["2026-06-30"] * 2,
                       "ROE": [0.11, 0.22]})
    write_snapshot(tmp_path, "2026-10-14", df)

    entry = json.loads(manifest_path(tmp_path).read_text(
        encoding="utf-8").strip())
    written = pd.read_parquet(
        next(tmp_path.rglob("date=*/financial.parquet")))
    assert entry["n_cols"] == written.shape[1], (
        f"manifest 记 {entry['n_cols']} 列，文件实际 {written.shape[1]} 列——"
        "审计记录与它描述的产物对不上")
    assert entry["n_rows"] == written.shape[0]


def test_the_digest_still_excludes_the_snapshot_column(tmp_path):
    """摘要是**排除** `snapshot_date` 算的，且这个规则必须留在原地。

    上一条测试把 `n_cols` 改回真实列数，容易顺手把 drop 一起删掉——
    那样 `sha256` 就变了，`verify_integrity` 会永远红。
    所以单独钉住：**形状用真实帧，摘要用剔除帧。**
    """
    import json

    from aplus.vintage import (manifest_path, verify_integrity,
                               write_snapshot)
    df = pd.DataFrame({"SECURITY_CODE": ["000001"],
                       "REPORT_DATE": ["2026-06-30"],
                       "ROE": [0.11]})
    write_snapshot(tmp_path, "2026-10-14", df)
    write_snapshot(tmp_path, "2026-10-14", df)      # 同日重跑：追加而非覆盖
    entry = json.loads(manifest_path(tmp_path).read_text(
        encoding="utf-8").strip().splitlines()[-1])
    written = pd.read_parquet(
        next(tmp_path.rglob("date=*/financial.parquet")))
    # 文件里有这一列，但摘要的范围里没有——所以两者不能相等地来自同一帧
    assert "snapshot_date" in written.columns
    assert verify_integrity(tmp_path)["ok"], (
        "摘要在被篡改后没被抓到——drop 掉 snapshot_date 的规则被破坏了")
