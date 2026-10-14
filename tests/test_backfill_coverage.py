"""aplus/coverage.py 与 backfill_gaps 的体检判定单测。全部离线，不联网。

合成数据刻意复刻了真实库里最难分的那一组：`000001.SZ`（1991 上市）被砍成
17 行、`301699.SZ`（2026-09-09 上市）恰好也是 17 行且首行同为 2026-09-09。
两者首行日期、行数、列数全同，**只有上市登记能把它们分开**——所以测试里
truncated / real_ipo 这两只的 first 与 rows 被断言成完全一致。
"""
from datetime import date
from pathlib import Path
import sys

import pandas as pd
import pytest

from aplus.coverage import (
    BASELINE_COLUMNS,
    REQUIRED_COLUMNS,
    Thresholds,
    audit_directory,
    classify_file,
    classify_frame,
    coverage_gaps,
    missing_columns,
)
from aplus.pipeline import SyncPipeline
from aplus.store import LocalStore

# scripts/ 不是包，按路径把 backfill_gaps 的降级写入安全线拉进来直接测
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from backfill_gaps import _StagingStore  # noqa: E402

TARGET_START = "2019-01-02"
TARGET_END = "2026-10-09"
FULL_WINDOW = pd.bdate_range("2019-01-02", "2026-10-09")


def make_frame(dates, code="600000.SH", *, full=True, close=None, volume=None,
               tradestatus=None):
    """造一只股票的日线。`close` 不给就用无规则的锯齿价（避开连续涨停形态）。"""
    dates = pd.to_datetime(dates)
    n = len(dates)
    base = 10.0 + (pd.Series(range(n)) % 7) * 0.37
    closes = pd.Series(base.to_numpy()) if close is None else pd.Series(close)
    vols = pd.Series([1_000_000.0 + i * 10 for i in range(n)]) if volume is None \
        else pd.Series(volume)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "code": code,
        "open": closes.round(2),
        "high": (closes * 1.02).round(2),
        "low": (closes * 0.98).round(2),
        "close": closes.round(2),
        "volume": vols,
    })
    if full:
        df["amount"] = (closes * vols).round(2)
        df["tradestatus"] = 1 if tradestatus is None else pd.Series(tradestatus).to_numpy()
        df["isST"] = 0
        df["turn"] = 0.5
        df["pctChg"] = 0.0
    return df


def write(path: Path, df) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


# 复刻真实库里那组「首行同一天、行数相同」的孪生兄弟
TRUNC_FIRST, TRUNC_LAST = "2024-02-19", "2026-10-09"
IPO_FIRST = "2026-09-09"
IPO_DATE = "2026-09-09"          # 301699.SZ 的真实上市日
PINGAN_IPO = "1991-04-03"        # 000001.SZ 的真实上市日


# --------------------------------------------------------------------- 必需列


def test_missing_columns_empty_when_full():
    assert missing_columns(make_frame(FULL_WINDOW)) == []


def test_missing_columns_reports_tencent_shape():
    """腾讯备源只有 date,code,OHLCV——缺的这 5 列正是降级写入的指纹。"""
    df = make_frame(FULL_WINDOW, full=False)
    assert missing_columns(df) == ["amount", "tradestatus", "isST", "turn", "pctChg"]


def test_required_columns_holds_baseline_and_extra():
    for col in BASELINE_COLUMNS:
        assert col in REQUIRED_COLUMNS
    assert set(REQUIRED_COLUMNS) - set(BASELINE_COLUMNS) == {
        "amount", "tradestatus", "isST", "turn", "pctChg"}


# ----------------------------------------------------------------- 首行缺口判定


def test_healthy_file_has_no_gaps():
    r = coverage_gaps(make_frame(FULL_WINDOW), TARGET_START, TARGET_END,
                      listing_date="1999-11-10")
    assert r["rows"] == len(FULL_WINDOW)
    assert r["missing_head"] == 0 and r["missing_tail"] == 0
    assert r["gaps"] == []
    assert r["head_is_gap"] is False and r["tail_is_gap"] is False


def test_truncated_history_is_head_gap():
    dates = pd.bdate_range(TRUNC_FIRST, TRUNC_LAST)
    r = coverage_gaps(make_frame(dates), TARGET_START, TARGET_END, listing_date=PINGAN_IPO)
    assert r["head_is_gap"] is True
    assert r["is_new_listing"] is False
    # 自然日口径：首行距窗口起点的真实天数，不是拍脑袋的数
    assert r["missing_head"] == (date(2024, 2, 19) - date(2019, 1, 2)).days
    assert r["missing_head"] > 1800        # 五年多的洞，必须是量级最大的那类


def test_real_ipo_is_not_a_gap_while_twin_is():
    """孪生兄弟：同一起点、同样长度，只有上市登记不同，必须判出两种结果。"""
    trunc = make_frame(pd.bdate_range(TRUNC_FIRST, TRUNC_LAST), code="000001.SZ")
    ipo = make_frame(pd.bdate_range("2026-09-09", "2026-10-09"), code="301699.SZ")
    # 下面这条测试「必须成立」的前提：这两只长得几乎一样
    assert len(trunc["date"].iloc[-1]) == len(ipo["date"].iloc[-1])

    r_trunc = coverage_gaps(trunc, TARGET_START, TARGET_END, listing_date=PINGAN_IPO)
    r_ipo = coverage_gaps(ipo, TARGET_START, TARGET_END, listing_date=IPO_DATE)
    assert r_trunc["head_is_gap"] is True
    assert r_ipo["head_is_gap"] is False
    assert r_ipo["is_new_listing"] is True
    assert r_ipo["missing_head"] == 0


def test_twin_ipo_and_trunc_share_identical_first_date_and_length():
    """真实库里 000001.SZ 与 301699.SZ 首行同为 2026-09-09、同样 17 行。

    合成同样的一对：首行与行数完全一致，仅上市登记不同 —— 必须被分开。
    """
    dates = pd.bdate_range("2026-09-09", "2026-10-09")
    trunc = make_frame(dates, code="000001.SZ")
    ipo = make_frame(dates, code="301699.SZ")
    assert len(trunc) == len(ipo)
    assert trunc["date"].iloc[0] == ipo["date"].iloc[0] == "2026-09-09"

    r_trunc = coverage_gaps(trunc, TARGET_START, TARGET_END, listing_date=PINGAN_IPO)
    r_ipo = coverage_gaps(ipo, TARGET_START, TARGET_END, listing_date=IPO_DATE)
    assert (r_trunc["head_is_gap"], r_ipo["head_is_gap"]) == (True, False)
    assert r_trunc["missing_head"] > 0 and r_ipo["missing_head"] == 0


def test_new_listing_inside_grace_window_is_not_a_gap():
    """上市日就在窗口起点附近，首行之前本来就没有数据。"""
    dates = pd.bdate_range("2019-01-20", "2026-10-09")
    r = coverage_gaps(make_frame(dates), TARGET_START, TARGET_END, listing_date="2019-01-18")
    assert r["is_new_listing"] is True
    assert r["head_is_gap"] is False and r["missing_head"] == 0


def test_limit_up_streak_exempts_head_gap_without_registry():
    """降级启发式：A 股新股开板连涨停是独有形态，够长即可豁免。"""
    dates = pd.bdate_range("2021-04-28", "2026-10-09")
    closes = [10.0, 11.0, 12.1, 13.3, 14.6, 15.5, 14.9, 15.2, 14.4, 14.8]
    closes += [14.0 + (i % 5) * 0.3 for i in range(len(dates) - len(closes))]
    r = coverage_gaps(make_frame(dates, close=closes), TARGET_START, TARGET_END)
    assert r["is_new_listing"] is True
    assert r["head_is_gap"] is False


def test_head_gap_without_registry_is_conservative():
    """没有登记时宁可多报不可漏报：截断必须仍被判成缺口。"""
    dates = pd.bdate_range(TRUNC_FIRST, TRUNC_LAST)
    r = coverage_gaps(make_frame(dates), TARGET_START, TARGET_END)
    assert r["head_is_gap"] is True
    assert r["missing_head"] > 0


# ----------------------------------------------------------------- 中间断层判定


def test_interior_gap_detected():
    keep = [d for d in FULL_WINDOW if not (date(2022, 1, 1) <= d.date() <= date(2022, 3, 1))]
    r = coverage_gaps(make_frame(keep), TARGET_START, TARGET_END, listing_date="1999-11-10")
    assert len(r["gaps"]) == 1
    before, after = r["gaps"][0]
    assert before == "2021-12-31" and after == "2022-03-02"
    assert (date.fromisoformat(after) - date.fromisoformat(before)).days > 20


def test_spring_festival_break_is_not_a_gap():
    """A 股最长休市是春节（2023 年春节休市 1/23-1/27，跨 10 个自然日）。

    阈值 20 天必须放它过去，否则每个长假都会被误报成断层。
    """
    holiday = {date(2023, 1, d) for d in range(23, 28)}
    dates = [d for d in pd.bdate_range("2023-01-03", "2023-03-10") if d.date() not in holiday]
    # 先确认合成数据里确实有个 10 天的洞，否则本测试什么也没测
    assert (date(2023, 1, 30) - date(2023, 1, 20)).days == 10
    assert date(2023, 1, 20) in {d.date() for d in dates}
    assert date(2023, 1, 30) in {d.date() for d in dates}

    r = coverage_gaps(make_frame(dates), TARGET_START, TARGET_END, listing_date="2023-01-03")
    assert r["gaps"] == []


# ----------------------------------------------------------------- 尾端/退市判定


def test_delisted_tail_is_not_a_gap():
    dates = pd.bdate_range("2019-01-02", "2023-06-30")
    r = coverage_gaps(make_frame(dates), TARGET_START, TARGET_END,
                      listing_date="1999-11-10", delist_date="2023-07-01")
    assert r["is_delisted"] is True
    assert r["tail_is_gap"] is False
    assert r["missing_tail"] == 0


def test_stale_tail_without_delist_record_is_a_gap():
    """同一份数据，没有退市登记就必须报成停更——证明退市登记真的在起作用。"""
    dates = pd.bdate_range("2019-01-02", "2023-06-30")
    r = coverage_gaps(make_frame(dates), TARGET_START, TARGET_END, listing_date="1999-11-10")
    assert r["is_delisted"] is False
    assert r["tail_is_gap"] is True
    assert r["missing_tail"] == (date(2026, 10, 9) - date(2023, 6, 30)).days


def test_trailing_halt_signature_read_as_delisted():
    """尾端停更，但尾部连续 6 根 tradestatus=0 —— 判成退市停笔，不算缺口。"""
    dates = pd.bdate_range("2019-01-02", "2026-08-31")
    status = [1] * (len(dates) - 6) + [0] * 6
    df = make_frame(dates, tradestatus=status)
    r = coverage_gaps(df, TARGET_START, TARGET_END, listing_date="1999-11-10")
    assert r["last"] == "2026-08-31"
    assert r["is_delisted"] is True
    assert r["tail_is_gap"] is False and r["missing_tail"] == 0


def test_trailing_halt_absent_leaves_tail_as_gap():
    """同样停更，但没有停牌痕迹就必须报成缺口，证明上一条的痕迹确实在起作用。"""
    dates = pd.bdate_range("2019-01-02", "2026-08-31")
    r = coverage_gaps(make_frame(dates), TARGET_START, TARGET_END, listing_date="1999-11-10")
    assert r["is_delisted"] is False
    assert r["tail_is_gap"] is True
    assert r["missing_tail"] == (date(2026, 10, 9) - date(2026, 8, 31)).days


def test_current_file_is_not_stale():
    r = coverage_gaps(make_frame(FULL_WINDOW), TARGET_START, TARGET_END,
                      listing_date="1999-11-10")
    assert r["tail_is_gap"] is False
    assert r["missing_tail"] == 0


# ------------------------------------------------------------------- 日期格式


@pytest.mark.parametrize("target", ["20190102", "2019-01-02"])
def test_target_start_accepts_both_formats(target):
    """脚本命令行传的是 20190102，库里存的是 2019-01-02，两种都得认。"""
    r = coverage_gaps(make_frame(FULL_WINDOW), target, TARGET_END, listing_date="1999-11-10")
    assert r["rows"] == len(FULL_WINDOW)
    assert r["head_is_gap"] is False


def test_empty_frame_is_flagged_empty():
    r = coverage_gaps(make_frame([]), TARGET_START, TARGET_END)
    assert r["rows"] == 0
    assert r["first"] is None and r["last"] is None
    info = classify_frame("600000.SH", make_frame([]), TARGET_START, TARGET_END)
    # 空表但列是全的：只该报 empty，不该再叠一个缺列
    assert info["reasons"] == ["empty"]
    assert info["primary_reason"] == "empty"
    assert info["is_healthy"] is False


# --------------------------------------------------------------- classify_file


def test_classify_file_reads_from_disk(tmp_path):
    path = write(tmp_path / "600000.SH.parquet", make_frame(FULL_WINDOW))
    info = classify_file(path, TARGET_START, TARGET_END, listing_date="1999-11-10")
    assert info["code"] == "600000.SH"
    assert info["rows"] == len(FULL_WINDOW)
    assert info["first"] == FULL_WINDOW[0].strftime("%Y-%m-%d")
    assert info["last"] == FULL_WINDOW[-1].strftime("%Y-%m-%d")
    assert info["missing_cols"] == []
    assert info["gaps"] == []
    assert info["is_healthy"] is True
    assert info["primary_reason"] == "healthy"


# --------------------------------------------------------------- audit_directory


@pytest.fixture
def corpus(tmp_path):
    """一只病态样本集，覆盖体检要分清的全部类别。"""
    d = tmp_path / "master" / "daily"
    registry_ipo = {
        "600000.SH": "1999-11-10",   # 健康
        "600001.SH": "1999-11-10",   # 缺列
        "000001.SZ": PINGAN_IPO,     # 历史被截断
        "301699.SZ": IPO_DATE,       # 真新股（孪生兄弟）
        "600002.SH": "1999-11-10",   # 中间断层
        "600003.SH": "1999-11-10",   # 已退市（不算缺口）
        "600004.SH": "1999-11-10",   # 无登记的停更（算缺口）
    }
    registry_delist = {"600003.SH": "2023-07-01"}

    write(d / "600000.SH.parquet", make_frame(FULL_WINDOW, code="600000.SH"))
    write(d / "600001.SH.parquet", make_frame(FULL_WINDOW, code="600001.SH", full=False))
    write(d / "000001.SZ.parquet",
          make_frame(pd.bdate_range(TRUNC_FIRST, TRUNC_LAST), code="000001.SZ"))
    write(d / "301699.SZ.parquet",
          make_frame(pd.bdate_range(IPO_FIRST, "2026-10-09"), code="301699.SZ"))
    hole = [x for x in FULL_WINDOW if not (date(2022, 1, 1) <= x.date() <= date(2022, 3, 1))]
    write(d / "600002.SH.parquet", make_frame(hole, code="600002.SH"))
    write(d / "600003.SH.parquet",
          make_frame(pd.bdate_range("2019-01-02", "2023-06-30"), code="600003.SH"))
    write(d / "600004.SH.parquet",
          make_frame(pd.bdate_range("2019-01-02", "2023-06-30"), code="600004.SH"))
    return tmp_path / "master" / "daily", registry_ipo, registry_delist


def test_audit_directory_groups_and_counts(corpus):
    daily_dir, ipo, delist = corpus
    rep = audit_directory(daily_dir, TARGET_START, TARGET_END,
                          listing_dates=ipo, delist_dates=delist)

    assert rep["n_files"] == 7
    assert rep["n_healthy"] == 3          # 600000 / 301699 / 600003(退市不算缺口)
    assert rep["n_unhealthy"] == 4
    assert rep["n_new_listing"] == 1
    assert rep["n_delisted"] == 1

    # 互斥分组必须严丝合缝地加起来等于文件数
    assert sum(rep["primary_reason_counts"].values()) == rep["n_files"]
    assert rep["primary_reason_counts"] == {
        "healthy": 3, "missing_columns": 1, "head_truncated": 1,
        "interior_gap": 1, "tail_stale": 1,
    }
    # 重叠计数：缺列的那只同时缺列；被截断的 000001 列是全的，单独记在 head_truncated
    assert rep["reason_counts"]["missing_columns"] == 1
    assert rep["reason_counts"]["head_truncated"] == 1

    assert rep["needs_backfill"] == ["000001.SZ", "600001.SH", "600002.SH", "600004.SH"]


def test_audit_directory_never_flags_new_listing_or_delisted(corpus):
    daily_dir, ipo, delist = corpus
    rep = audit_directory(daily_dir, TARGET_START, TARGET_END,
                          listing_dates=ipo, delist_dates=delist)
    assert "301699.SZ" not in rep["needs_backfill"]     # 真新股
    assert "600003.SH" not in rep["needs_backfill"]      # 已退市
    assert "000001.SZ" in rep["needs_backfill"]         # 孪生兄弟必须被补


def test_audit_without_registry_still_catches_truncation(corpus):
    """退化成启发式后允许多报，但漏报一只都不行——静默的洞最致命。"""
    daily_dir, _, _ = corpus
    rep = audit_directory(daily_dir, TARGET_START, TARGET_END)
    assert "000001.SZ" in rep["needs_backfill"]
    assert sum(rep["primary_reason_counts"].values()) == rep["n_files"]
    # 真新股会被保守地多报一次，这是刻意接受的代价
    assert len(rep["needs_backfill"]) >= 5


def test_audit_survives_unreadable_file(tmp_path):
    d = tmp_path / "daily"
    write(d / "600000.SH.parquet", make_frame(FULL_WINDOW))
    (d / "broken.parquet").write_bytes(b"not a parquet file at all")
    rep = audit_directory(d, TARGET_START, TARGET_END)
    assert rep["n_files"] == 2
    assert rep["files"]["broken"]["primary_reason"] == "unreadable"
    assert rep["n_healthy"] == 1
    assert "broken" in rep["needs_backfill"]


def test_thresholds_are_configurable():
    """阈值集中在一个不可变对象里，调参不需要改判定逻辑。"""
    tight = Thresholds(max_calendar_gap_days=5)
    # 挖一个 17 天的洞：默认阈值 20 放行，阈值 5 判为断层
    keep = [d for d in FULL_WINDOW
            if not (date(2022, 1, 3) <= d.date() <= date(2022, 1, 14))]
    assert (date(2022, 1, 17) - date(2021, 12, 31)).days == 17
    assert date(2022, 1, 17) in {d.date() for d in keep}

    strict = coverage_gaps(make_frame(keep), TARGET_START, TARGET_END, thresholds=tight)
    default = coverage_gaps(make_frame(keep), TARGET_START, TARGET_END)
    assert strict["gaps"] == [("2021-12-31", "2022-01-17")]
    assert default["gaps"] == []


# ------------------------------------- backfill_gaps 的降级写入安全线（--allow-degraded）


class _FakeSource:
    """假源：`boom=True` 模拟主源挂掉；否则返回给定帧。"""

    def __init__(self, name, frame=None, boom=False):
        self.name = name
        self._frame = frame
        self._boom = boom

    def fetch_daily(self, code, start, end):
        if self._boom:
            raise RuntimeError("主源超时")
        return None if self._frame is None else self._frame.copy()

    def fetch_stock_list(self):
        return pd.DataFrame({"code": []})

    def fetch_adjust_factor(self, code, start, end):
        return None

    def close(self):
        pass


def _degraded_pipeline(store, backup_frame):
    """主源失败 + 备源成功 = status='degraded'，正是本次要拦的那条路径。"""
    return SyncPipeline(
        [_FakeSource("baostock", boom=True), _FakeSource("tencent", backup_frame)],
        store, coverage="overlap")


def test_degraded_write_never_reaches_disk(tmp_path):
    """核心安全线：备源数据只进内存暂存，不落主库。

    `SyncPipeline.sync_daily` 是在函数**内部**调 `store.upsert_daily` 的，
    返回 degraded 时数据早已写过——所以必须靠 _StagingStore 在存储层拦一道。
    """
    store = LocalStore(tmp_path)
    guard = _StagingStore(store)
    path = tmp_path / "master" / "daily" / "600000.SH.parquet"
    backup_frame = make_frame(pd.bdate_range("2024-02-19", "2026-10-09"), full=False)

    result = _degraded_pipeline(guard, backup_frame).sync_daily("600000.SH", "2019-01-02",
                                                              "2026-10-09")
    assert result.status == "degraded"
    assert not path.exists()               # 关键：还没 commit，主库是干净的

    guard.discard("600000.SH")             # 默认策略：降级就跳过
    assert not path.exists()               # 跳过之后仍然什么都没写


def test_committing_a_good_write_does_persist(tmp_path):
    """对照组：放行时数据确实落库——证明上一条不是因为「压根没写」而绿。"""
    store = LocalStore(tmp_path)
    guard = _StagingStore(store)
    path = tmp_path / "master" / "daily" / "600000.SH.parquet"
    frame = make_frame(pd.bdate_range("2019-01-02", "2026-10-09"))

    result = _degraded_pipeline(guard, frame).sync_daily("600000.SH", "2019-01-02",
                                                         "2026-10-09")
    assert result.status == "degraded"
    guard.commit("600000.SH")
    assert path.exists()
    assert len(pd.read_parquet(path)) == len(frame)


def test_guard_commits_are_idempotent_upsert(tmp_path):
    """commit 走 store.upsert_daily（合并去重），不是覆盖——补数不能弄丢旧数据。"""
    store = LocalStore(tmp_path)
    path = tmp_path / "master" / "daily" / "600000.SH.parquet"
    old = make_frame(pd.bdate_range("2019-01-02", "2020-12-31"))
    write(path, old)
    new = make_frame(pd.bdate_range("2020-01-02", "2022-12-30"))
    store.upsert_daily("600000.SH", new)
    merged = pd.read_parquet(path)
    assert len(merged) == len(set(old["date"]) | set(new["date"]))

# ══════════════════════════════════════════════════════════════════
# 真实库的 schema 守卫（2026-10-15）
# ══════════════════════════════════════════════════════════════════

# ★ 形状对齐 `test_measure_amount_availability.py` 里已有的 KNOWN_BAD：
#   **新增即红**，数据补上后**要求自己删条目**（`KNOWN_BAD - got` 必须为空）。
#   白名单是「登记已知例外」，不是「把有洞合法化」。
# ★ 白名单形状对齐 `test_measure_amount_availability.py` 里已有的 KNOWN_BAD：
#   **新增即红**，数据补上后**要求自己删条目**（`KNOWN_BAD - got` 必须为空）。
#   白名单是「登记已知例外」，不是「把有洞合法化」。
#
# 📅 历史：2026-10-15 曾登记 `000043.SZ`(221 行) / `300114.SZ`(640 行) 两只整列缺失。
#    根因不是「降级写入」，而是 `convert.py` 对**停牌日空串**抛 ValueError
#    ⇒ 主源报错 ⇒ 流水线降级 ⇒ 文件只剩 7 列，且**每次重试撞同一个错**（自我维持的洞）。
#    已修根因（停牌日结转上一收盘价）+ 全历史重拉 ⇒ 两只分别补到 6127 / 3511 行，
#    ⇒ 登记随之清空。**留空是正确状态，不是待办。**
KNOWN_BAD_SCHEMA: dict[str, str] = {}


def _store_dir():
    from pathlib import Path
    return Path(__file__).resolve().parents[1] / "data" / "master" / "daily"


def _files_missing_columns():
    """只读 parquet **元数据**（schema），不碰数据页 ⇒ 5460 个文件约 1.3 秒。

    ⚠️ 这一条是 `test_the_real_store_amount_has_no_null_values` 补不上的：
    那条量的是「**在的**列尾部有没有值」，而**整列不存在**时它连数都数不到。
    """
    import pyarrow.parquet as pq

    from aplus.coverage import REQUIRED_COLUMNS

    out = {}
    for p in sorted(_store_dir().glob("*.parquet")):
        have = set(pq.ParquetFile(p).schema.names)
        miss = [c for c in REQUIRED_COLUMNS if c not in have]
        if miss:
            out[p.stem] = miss
    return out


def _assert_schema_ok(got: dict, known_bad: dict) -> None:
    """★ 守卫的**纯逻辑**部分：与「扫库」彻底分开。

    拆开是为了让变异自测不依赖真实库的当前状态 ——
    白名单清空后（正确状态），靠 `next(iter(known_bad))` 取样本会直接 `StopIteration`。
    """
    unlisted = set(got) - set(known_bad)
    assert not unlisted, (
        f"{len(unlisted)} 个文件缺列且未登记：{sorted(unlisted)[:10]}"
        f"（样例缺 {got[sorted(unlisted)[0]]}）。"
        "先查 data/logs/ 里的 degraded_source_write 与 convert.py 的停牌日解析，"
        "别直接加白名单。"
    )
    # ★ 数据修好后，KNOWN_BAD 必须自己瘦下来 —— 否则它会变成永久合法的洞
    stale = set(known_bad) - set(got)
    assert not stale, (
        f"KNOWN_BAD 里 {sorted(stale)} 已经不缺列了 —— 数据补好了，"
        "请把条目删掉（白名单只登记**当前仍存在**的例外）"
    )
    # 登记在案的也要说清缺的是哪几列，防止「登记」被当成「解释」
    for code, cols in got.items():
        assert cols == ["amount", "tradestatus", "isST", "turn", "pctChg"], (
            f"{code} 缺的列变了：{cols} —— 若形态已变，请更新 KNOWN_BAD 的说明"
        )


def test_the_real_store_has_the_full_schema_on_every_file():
    """★ **整列缺失会让 `isST` 过滤对整份文件失明** —— 而此前无人看守。

    这两只是 2026-10-14 主源网络事故的**第三种形态**：
    前两种是「尾根缺 5 列」（53 只，已修）和「正常」，
    这一种是**整份文件只有 7 列**，日志里对应另外 2 条 `degraded_source_write`。

    ⚠️ 危害不对称：`amount` 空只影响成交额口径，
    **`isST` 空影响「这只票能不能进候选池」** —— 一个 ST 票会被当成正常票。
    """
    got = _files_missing_columns()
    _assert_schema_ok(got, KNOWN_BAD_SCHEMA)


def test_the_schema_guard_would_catch_a_new_degraded_file():
    """★ **守卫只有变异后算数**（本仓铁律）。

    🚨 第一版这条守卫**是恒真的装饰**：`KNOWN_BAD` 有 2 条，真实库也恰好缺 2 只，
    于是「未登记 == 空集」与「登记 == 实测」同时成立 —— 它只是在跟自己比。
    **测试通过 ≠ 在守东西**（本仓铁律）。

    ★ 数据修好后白名单清空了，若还按老写法 `next(iter(KNOWN_BAD))` 取变异样本，
    会直接 `StopIteration` —— 所以变异打在**纯逻辑**上，不依赖真实库当前状态。
    """
    got = {"688999.SH": ["amount", "tradestatus", "isST", "turn", "pctChg"]}

    # 未登记 ⇒ 必须红
    with pytest.raises(AssertionError, match="未登记"):
        _assert_schema_ok(got, {})

    # 登记了但实测不缺 ⇒ 必须红（白名单瘦不下来）
    with pytest.raises(AssertionError, match="已经不缺列"):
        _assert_schema_ok({}, {"000043.SZ": "早已修好"})

    # 登记在案且确实缺 ⇒ 绿（白名单是「登记已知例外」）
    _assert_schema_ok(got, {"688999.SH": "降级写入"})


def test_the_real_store_schema_scan_actually_sees_the_two_files_it_used_to():
    """★ 扫描器本身**不是恒真的** —— 它曾经真的看见过那两只。

    守卫可以因为「扫不出来东西」而全绿（扫描器坏了 / 路径错了 / 全被白名单吃掉）。
    本条用**两个仍然存在的对照**证明扫描器在工作：
      ① 主库里**确实有** parquet 文件被扫到（数量 > 5000）
      ② `REQUIRED_COLUMNS` 里**确实有**会被判缺的列（否则「缺列」永远为空）
    ③ 而当前扫描结果为空 —— 这意味着「**真的没有洞了**」，不是「扫不到」。
    """
    from aplus.coverage import REQUIRED_COLUMNS

    files = list(_store_dir().glob("*.parquet"))
    assert len(files) > 5000, f"只扫到 {len(files)} 个文件 —— 路径或过滤器不对"

    assert "isST" in REQUIRED_COLUMNS and "amount" in REQUIRED_COLUMNS, (
        "REQUIRED_COLUMNS 变了，缺列判据可能已失效"
    )

    got = _files_missing_columns()
    # 2026-10-15 修复后应当为空；非空说明洞还在（或又长出来了）
    assert got == {}, f"真实库仍有整列缺失的文件：{got}"
