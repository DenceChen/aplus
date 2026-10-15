"""`no_data` 重判链路的守卫（预注册 `PREREG_NO_DATA_REJUDGE_2026-10-16.md` §3）。

## 这个文件为什么存在

实测 `verification.parquet` 里 8 条 `no_data` 分成**两类**：
7 条是「判定当天行情库还没到」⇒ **暂时**，现在重判应当能补上；
1 条（600825.SH，daily 末日期 2026-10-12，而同日对照票末日期 2026-10-15）
是**真停牌** ⇒ 永远补不上。

⇒ 判据必须能分辨「该重试」与「该放弃」。分辨不了的后果是对称的：
- 只认「暂时」⇒ 停牌那一天**每天重判一次，永不停歇**；
- 只认「永久」⇒ 那 7 条**永远不补**（这正是 2026-10-16 实测的现状）。

## 六条判据的映射

| 守卫 | 判据 | 咬住什么 |
|---|---|---|
| `test_a_partially_judged_day_is_still_pending` | **C1** | 部分覆盖必须回 pending |
| `test_a_partially_judged_day_fixture_is_genuinely_partial` | **C3** | 防夹具失效 ⇒ C1 变恒真 |
| `test_a_permanently_unjudgeable_row_does_not_block_its_day` | **C4** | 永久标记不阻塞 |
| `test_a_halted_stock_with_a_current_store_is_permanent` | **C4** | 判别器正向 |
| `test_a_stock_merely_awaiting_the_store_is_not_permanent` | **C5** | 判别器反向（库落后） |
| `test_the_detector_is_not_tautological` | **C3** | `return True` 的探测器会让 C1/C5 全绿而看起来守住 |
| `test_the_writer_never_marks_a_judged_row_however_full_the_date_map_is` | **C4 契约层** | 「只有 `no_data` 行可被标记」**无论 `stock_max` 里有什么**（变异 K5 存活逼出来的） |
| `test_rejudging_a_day_twice_leaves_judged_rows_bitwise_identical` | **C6** | 重判幂等 |
| `test_permanence_is_decided_by_bar_dates_not_by_file_mtime` | **C7** | 锚是日期，不是 mtime |

⚠️ **C4 与 C5 必须成对**：只写 C4，`classify_no_data` 退化成 `return "halt"` 时
C1 与 C5 仍全绿 —— 症状和「守对了」一模一样（第 112 层双向筛子）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import pytest

from aplus.reports import append_verification, pending_prediction_dates
from aplus.verify import classify_no_data, mark_unjudgeable

# 🚨 永久标记的列名与取值。写在测试里而不是 import 源码常量：
# 否则改常量名会让守卫跟着改而零信息量（第 79 层：只钉存在不钉性质）。
MARK_COL = "unjudgeable_reason"
MARK_HALT = "halt"


def _reports(tmp_path: Path, days, ver_rows) -> Path:
    """造一个最小的 reports 目录：名单只需**文件名**被 glob 到。"""
    reports = tmp_path / "reports"
    preds = reports / "predictions"
    preds.mkdir(parents=True)
    for d in days:
        pd.DataFrame({
            "code": ["A", "B"],
            "direction": ["up", "down"],
            "pred_date": [d, d],
        }).to_parquet(preds / f"pred_{d}.parquet", index=False)
    pd.DataFrame(ver_rows).to_parquet(reports / "verification.parquet", index=False)
    return reports


# ------------------------------------------------------------------ C1


def test_a_partially_judged_day_is_still_pending(tmp_path):
    """**C1**：`no_data` 的那行没有 `exit_date`，只要那天还有别的行判成了，
    这一天就必须回到 `pending` —— 否则那行永远不会被重判（实测 7 条 10-12 的 `no_data`）。
    """
    reports = _reports(tmp_path, ["2026-10-12"], [
        {"code": "A", "pred_date": "2026-10-12", "exit_date": "2026-10-14",
         "result": "hit", "actual_pct": 0.01},
        {"code": "B", "pred_date": "2026-10-12", "exit_date": None,
         "result": "no_data", "actual_pct": None},
    ])

    assert pending_prediction_dates(reports) == ["2026-10-12"], (
        "部分覆盖的日不在 pending ⇒ 那些 no_data 行永远不会被重判")


# ------------------------------------------------------------------ C3


def test_a_partially_judged_day_fixture_is_genuinely_partial(tmp_path):
    """**C3**：上面那条守卫的夹具**必须真的**是部分覆盖。

    🚨 若夹具退化成「整天都没判」或「整天都判了」，
    `pending_prediction_dates` 返回该日会变成**恒真** —— 而恒真的症状
    与「守住了」一模一样（第 117 层）。
    """
    reports = _reports(tmp_path, ["2026-10-12"], [
        {"code": "A", "pred_date": "2026-10-12", "exit_date": "2026-10-14", "result": "hit"},
        {"code": "B", "pred_date": "2026-10-12", "exit_date": None, "result": "no_data"},
    ])
    ver = pd.read_parquet(reports / "verification.parquet")
    ok = ver["exit_date"].notna()
    assert len(ver) == 2, f"夹具行数 {len(ver)} ≠ 2"
    assert int(ok.sum()) == 1 and int((~ok).sum()) == 1, (
        f"夹具不是「一半有 exit_date」，实测 {int(ok.sum())}/{len(ver)} ⇒ "
        "C1 会退化成「整天未判」或「整天已判」两种平凡情形之一")
    assert MARK_COL not in ver.columns, (
        "夹具里不许预置永久标记 —— C1 考的是**无标记**时的重试行为")


def test_the_detector_is_not_tautological():
    """**C3 探测器的正控**：`classify_no_data` 不得对什么都返回 `"halt"`。

    一个恒返回 `"halt"` 的探测器会让 C1 与 C5 一起变绿，
    而产物上看起来「停牌被正确识别了」。
    """
    # 正常票：有窗口内的 bar，库也够新
    assert classify_no_data("2026-10-15", "2026-10-15", "2026-10-13", "2026-10-14") is None, (
        "有 bar 的正常票被判成永久不可判定 ⇒ 探测器恒真")
    # 库落后：还没到退出日
    assert classify_no_data("2026-10-12", "2026-10-12", "2026-10-13", "2026-10-14") is None, (
        "库都没到退出日就判永久 ⇒ 数据滞后被误当成停牌")


# ------------------------------------------------------------------ C4


def test_a_halted_stock_with_a_current_store_is_permanent():
    """**C4 正向**：实测 600825.SH 的形状 —— 该股末 bar 停在 2026-10-12，
    而库里同时刻已经有 2026-10-15 ⇒ 不是库落后，是这只票停牌。
    """
    got = classify_no_data("2026-10-12", "2026-10-15", "2026-10-13", "2026-10-14")
    assert got == MARK_HALT, f"实得 {got!r}，停牌票没有被识别成永久不可判定"


def test_a_stock_merely_awaiting_the_store_is_not_permanent():
    """**C5 反向**：**同样一只票**，但库里只到 2026-10-13（还没到退出日）
    ⇒ 它只是**暂时**拿不到 bar，必须继续重试。
    🚨 这条与 C4 只差 `store_max` 一个入参，缺了它 C4 就是恒真的。
    """
    got = classify_no_data("2026-10-12", "2026-10-13", "2026-10-13", "2026-10-14")
    assert got is None, (
        f"实得 {got!r} —— 库尚未越过退出日时判成永久，"
        "那些暂时性 no_data 会被一次性丢掉")


def test_a_permanently_unjudgeable_row_does_not_block_its_day(tmp_path):
    """**C4 端到端，带对照臂**：带永久标记的行不再让那一天停在 `pending`
    —— 否则停牌日会被**每天重判一次、永不停歇**。

    🚨 **对照臂是这条守卫的全部意义**：去掉标记，同一份夹具**必须**回到 pending。
    没有对照臂时，`any` 语义（把标记整个无视、因为那天有一行有 `exit_date`
    就判为已判定）也会让本条**绿** —— 症状与「守住了」一模一样
    （断言对、成因错，第 168 层同族）。
    """
    marked = _reports(tmp_path / "marked", ["2026-10-10"], [
        {"code": "A", "pred_date": "2026-10-10", "exit_date": "2026-10-14",
         "result": "hit", "actual_pct": 0.01, MARK_COL: None},
        {"code": "B", "pred_date": "2026-10-10", "exit_date": None,
         "result": "no_data", "actual_pct": None, MARK_COL: MARK_HALT},
    ])
    unmarked = _reports(tmp_path / "unmarked", ["2026-10-10"], [
        {"code": "A", "pred_date": "2026-10-10", "exit_date": "2026-10-14",
         "result": "hit", "actual_pct": 0.01, MARK_COL: None},
        {"code": "B", "pred_date": "2026-10-10", "exit_date": None,
         "result": "no_data", "actual_pct": None, MARK_COL: None},
    ])

    assert pending_prediction_dates(unmarked) == ["2026-10-10"], (
        "对照臂失效：去掉标记后仍然不 pending ⇒ 本条守卫测的不是标记")
    assert pending_prediction_dates(marked) == [], (
        "带永久标记的行仍阻塞该日 ⇒ 停牌日会被无限重判")


# ------------------------------------------------------- 写入端：纯函数

def _res(rows):
    return pd.DataFrame(rows)


def test_the_writer_marks_only_the_halted_row_of_a_mixed_day():
    """**C4 写入端**：一日之内，停牌的那条带标记，判得动的那几条**不带**。

    🚨 这是 `classify_no_data` 之上的一层：`mark_unjudgeable` 必须**按行**判，
    不是「这一天有停牌 ⇒ 全部标记」，也不是「这一天判得动 ⇒ 全部不标记」。
    """
    res = _res([
        {"code": "HIT1", "result": "hit"},
        {"code": "HALT", "result": "no_data"},
        {"code": "HIT2", "result": "miss"},
        {"code": "BARRED", "result": "no_data"},
    ])
    out = mark_unjudgeable(
        res,
        stock_max={"HIT1": "2026-10-15", "HALT": "2026-10-12",
                   "HIT2": "2026-10-15", "BARRED": "2026-10-15"},
        store_max="2026-10-15",
        required_entry="2026-10-13", required_exit="2026-10-14")

    assert out.iloc[1] == MARK_HALT, f"停牌行未标记：{out.iloc[1]!r}"
    assert out.iloc[3] is None, (
        f"这只票的 bar 一直到 2026-10-15（已越过买入日）⇒ 它有窗口，"
        f"不该被标成永久：{out.iloc[3]!r}")
    assert out.iloc[0] is None and out.iloc[2] is None, (
        f"已判出结果的行被标记了：{out.tolist()} ⇒ 下游会把『量到了』读成『没量到』")


def test_the_writer_never_marks_a_judged_row_however_full_the_date_map_is():
    """🚨 **契约层**：`stock_max` 里**装着被判行自己的 code**，也只有 `no_data` 行能被标记。

    ## 为什么它不是上一条守卫的重复（变异 K5 存活逼出来的）
    上一条里 `HIT1` **确实在** `stock_max` 中，但它的末日期 `2026-10-15`
    **不早于** `required_entry=2026-10-13` ⇒ `classify_no_data` 的第二道闸
    （「有买入窗口 ⇒ 不是永久」）替写入端兜住了。
    而那道闸问的是「**这只票**有没有窗口」，与「**这一行**判没判出结果」无关。

    ⇒ 结果：把 `mark_unjudgeable` 里的 `if res != "no_data":` 删掉
    （变异 K5），**输出逐位不变**，14 条守卫全绿。
    ⇒ 已有的守卫钉的是「**在这个输入下**只标一行」，不是「**无论输入如何**只标 no_data 行」。
    这就是 [[guards-that-pin-existence-not-property]] 的形状。

    ## 这个输入是**可达**的，不是为了造变异硬凑的
    `required_entry = max(所有行的 entry_date)`，而停牌票的首个 bar 晚于全池，
    会把这个 max 推高。只要错位超过持有期，那些**正常判出结果的行**的
    `stock_max` 就落在 `required_entry` 之前 —— 此时判别器对它们会返回 `halt`。

    ## 正控钉的是「夹具有没有判别力」
    断言 ① 证明判别器在**本夹具这组日期上确实会返回 `halt`**。
    哪天有人改 `classify_no_data` 的闸门把它变成恒返 `None`，
    断言 ① 先红并明说「是夹具失效、不是守卫失效」——
    否则本条会安静退化成「断言一条永远不会被标记的输入」（第 146 层）。
    """
    judged_dates = {"HIT": "2026-10-15", "MISS": "2026-10-15", "FLAT": "2026-10-15"}
    store_max, req_entry, req_exit = "2026-10-24", "2026-10-20", "2026-10-24"

    # ① 正控：这个日期组合必须**能**判成 halt，否则下面的断言没有牙齿
    assert classify_no_data("2026-10-15", store_max, req_entry, req_exit) == MARK_HALT, (
        "夹具失效：判别器已不再把『末日期早于买入日、库已到退出日』判成永久 ⇒ "
        "本条守卫对变异 K5 失去判别力，且症状与『守住了』完全相同")

    res = _res([
        {"code": "HIT", "result": "hit"},
        {"code": "MISS", "result": "miss"},
        {"code": "FLAT", "result": "flat"},
        {"code": "LATE", "result": "no_data"},
    ])
    out = mark_unjudgeable(
        res, stock_max={**judged_dates, "LATE": "2026-10-15"},
        store_max=store_max, required_entry=req_entry, required_exit=req_exit)

    assert out.iloc[3] == MARK_HALT, f"真正停牌的那行未标记：{out.tolist()}"
    assert out.iloc[:3].isna().all(), (
        f"已判出结果的行带着『永久不可判定』：{out.tolist()} ⇒ "
        f"产物这一列对那三行在说假话（docstring 明写『hit/miss/flat 一律 None』）")

    # ② 对照臂：同一份日期表、只去掉 no_data 行 ⇒ 一条都不许标
    only_judged = _res([{"code": "HIT", "result": "hit"},
                        {"code": "MISS", "result": "miss"},
                        {"code": "FLAT", "result": "flat"}])
    out2 = mark_unjudgeable(
        only_judged, stock_max=judged_dates,
        store_max=store_max, required_entry=req_entry, required_exit=req_exit)
    assert out2.isna().all(), (
        f"对照臂失效：没有 no_data 行却仍标出了 {out2.to_dict()} ⇒ "
        f"本条可能只是被『这张表里恰好没有可标的行』满足")


def test_the_writer_never_marks_a_row_whose_library_has_not_caught_up():
    """**C5 写入端**：库整体落后时，一条 `no_data` 也不许被标成永久。

    与上一条只差 `store_max` —— 缺了它，写入端会变成「见 no_data 就标 halt」，
    而那正是把 7 条暂时性证据一次性扔掉的写法。
    """
    res = _res([{"code": "A", "result": "no_data"}, {"code": "B", "result": "no_data"}])
    out = mark_unjudgeable(
        res, stock_max={"A": "2026-10-12", "B": "2026-10-12"},
        store_max="2026-10-13",
        required_entry="2026-10-13", required_exit="2026-10-14")

    assert out.isna().all(), f"库落后却仍有行被标成永久：{out.to_dict()}"


def test_the_writer_leaves_the_column_aligned_to_the_result_rows():
    """按行对齐是契约：`results` 的行序与索引变了，标记必须跟着走。

    只钉「有一个标记」会被空集合满足（第 117 层）；这里钉**逐位对齐**。
    """
    res = _res([
        {"code": "X", "result": "no_data"},
        {"code": "Y", "result": "no_data"},
        {"code": "Z", "result": "no_data"},
    ])
    res.index = [7, 11, 19]
    out = mark_unjudgeable(
        res, stock_max={"X": "2026-10-12", "Y": "2026-10-15", "Z": "2026-10-12"},
        store_max="2026-10-15",
        required_entry="2026-10-13", required_exit="2026-10-14")

    assert list(out.index) == [7, 11, 19], f"索引错位：{list(out.index)}"
    assert out.loc[7] == MARK_HALT
    assert out.loc[11] is None
    assert out.loc[19] == MARK_HALT


# ------------------------------------------------------------------ C6


def test_rejudging_a_day_twice_leaves_judged_rows_bitwise_identical(tmp_path):
    """**C6**：重判必须幂等 —— `append_verification` 按 `(pred_date, code)` 覆盖，
    所以第二次写同一批行，**已判对的行一个字段都不许变**（`verified_on` 除外）。
    """
    reports = tmp_path / "reports"
    day = "2026-10-12"
    rows = pd.DataFrame([
        {"code": "A", "pred_date": day, "exit_date": "2026-10-14",
         "result": "hit", "actual_pct": 0.0312, "cost": 0.003,
         "verified_on": "2026-10-14"},
        {"code": "B", "pred_date": day, "exit_date": None,
         "result": "no_data", "actual_pct": None, "cost": None,
         "verified_on": "2026-10-14"},
    ])
    append_verification(reports, rows)
    first = pd.read_parquet(reports / "verification.parquet").sort_values("code")

    append_verification(reports, rows.assign(verified_on="2026-10-16"))
    second = pd.read_parquet(reports / "verification.parquet").sort_values("code")

    assert len(second) == len(first) == 2, (
        f"行数从 {len(first)} 变成 {len(second)} ⇒ 重判在追加而不是覆盖")
    cols = ["code", "pred_date", "exit_date", "result", "actual_pct", "cost"]
    for c in cols:
        assert first[c].equals(second[c]), (
            f"重判后 `{c}` 变了 ⇒ 不幂等")
    assert second["verified_on"].astype(str).eq("2026-10-16").all(), (
        "verified_on 应当被更新 —— 它是『这一行什么时候判的』，重判就该刷新")


# ------------------------------------------------------------------ C7


def test_permanence_is_decided_by_bar_dates_not_by_file_mtime(tmp_path):
    """**C7**：永久与否只由 **bar 里的日期**决定，与 parquet 文件的 mtime 无关。

    🚨 判据用 mtime 而不是真相（日期）是过严守卫的头号成因（第 112 层）：
    它会让「拷贝/备份/重新写入但内容未变」的文件被判成别的状态。
    ⇒ 这里**主动把 mtime 推到远古**，判定必须**逐字不变**。
    """
    reports = tmp_path / "reports"
    preds = reports / "predictions"
    preds.mkdir(parents=True)
    pd.DataFrame({"code": ["B"], "pred_date": ["2026-10-10"]}).to_parquet(
        preds / "pred_2026-10-10.parquet", index=False)
    ver = pd.DataFrame([
        {"code": "A", "pred_date": "2026-10-10", "exit_date": "2026-10-14", "result": "hit"},
        {"code": "B", "pred_date": "2026-10-10", "exit_date": None,
         "result": "no_data", MARK_COL: MARK_HALT},
    ])
    vpath = reports / "verification.parquet"
    ver.to_parquet(vpath, index=False)
    before = pending_prediction_dates(reports)
    assert before == [], (
        f"夹具前置失效：带永久标记时该日本就不该 pending，实得 {before} ⇒ "
        "后面那个『没变』什么都证明不了")

    ancient = time.time() - 10 * 365 * 24 * 3600
    os.utime(vpath, (ancient, ancient))
    for f in preds.iterdir():
        os.utime(f, (ancient, ancient))
    after = pending_prediction_dates(reports)

    assert before == after == [], (
        f"把 mtime 推到 10 年前改变了判定：{before} → {after} ⇒ "
        "判据锚在 mtime 上而不是 bar 日期上")


def test_permanence_is_still_decided_by_bar_dates_after_an_mtime_change(tmp_path):
    """**C7 的对照臂**：把 mtime 推到远古之后，**无标记**的那一份必须**仍然** pending。

    🚨 与上一条互为反向：上一条钉「标记在场时改 mtime 不改判」，
    这一条钉「标记不在场时改 mtime 也不改判」。
    只写上一条时，一个 `return []` 式的读取器（只看 mtime、不看标记）
    会让上一条变绿 —— 因为它对两份夹具都返回 `[]`。
    """
    reports = tmp_path / "reports"
    preds = reports / "predictions"
    preds.mkdir(parents=True)
    pd.DataFrame({"code": ["B"], "pred_date": ["2026-10-10"]}).to_parquet(
        preds / "pred_2026-10-10.parquet", index=False)
    pd.DataFrame([
        {"code": "A", "pred_date": "2026-10-10", "exit_date": "2026-10-14", "result": "hit"},
        {"code": "B", "pred_date": "2026-10-10", "exit_date": None,
         "result": "no_data", MARK_COL: None},
    ]).to_parquet(reports / "verification.parquet", index=False)

    before = pending_prediction_dates(reports)
    ancient = time.time() - 10 * 365 * 24 * 3600
    for f in list(reports.rglob("*.parquet")):
        os.utime(f, (ancient, ancient))
    after = pending_prediction_dates(reports)

    assert before == after == ["2026-10-10"], (
        f"无标记的日应当始终 pending，实得 {before} → {after} ⇒ "
        "读取器在看 mtime 而不是看 bar 日期与标记")


# ------------------------------------------------------------------ 兼容性


@pytest.mark.parametrize("day,exit_date,expected", [
    ("2026-10-12", None, ["2026-10-09", "2026-10-12"]),
    ("2026-10-12", "2026-10-14", ["2026-10-09"]),
])
def test_the_pre_existing_caliber_contract_still_holds(tmp_path, day, exit_date, expected):
    """预注册 **C2**：现有两条守卫钉的那两个行为**一个字都不能变**。

    复刻 `tests/test_measurement_window_identity.py` 的两个夹具：
    ① 行缺 `exit_date` 列 ⇒ 整档重开；② 该日已按锚定口径判过 ⇒ 不重复判定。
    """
    reports = tmp_path / "reports"
    preds = reports / "predictions"
    preds.mkdir(parents=True)
    for d in ("2026-10-09", "2026-10-12"):
        # 现有守卫刻意把名单写成纯文本（不读内容），这里照抄
        (preds / f"pred_{d}.parquet").write_text(d, encoding="utf-8")
    row = {"code": "600001.SH", "pred_date": "2026-10-12",
           "direction": "up", "actual_pct": 0.01, "result": "hit"}
    if exit_date is not None:
        row["exit_date"] = exit_date
    (reports / "verification.parquet").write_bytes(
        pd.DataFrame([row]).to_parquet(index=False))

    assert pending_prediction_dates(reports) == expected
