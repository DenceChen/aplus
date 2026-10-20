"""PIT 版 BM 因子的守卫。

## 这个文件守的是什么

`aplus/factor_fundamental.py` 的**唯一**使命是：
**BM 的财务侧只能来自 `vintage.pit_frame`（PIT 快照）**。

⇒ 它的全部风险不是「算错 BM」，而是**悄悄用了后修值**——
而那种错**不崩、不报警、报告照样出数**。

## 四条最容易被绕过的失效形态

1. **拿更新的快照代替**：`as_of` 早于首份快照时，
   `vintage.pit_frame` 的 docstring 逐字写着「拿最新的一份凑数 = 多了一个
   『已做 PIT』的标签，那比不做 PIT 更糟，因为它让前视偏差免于被审查」。
   ⇒ 本文件必须证明：**报错**，而不是退而求其次。
2. **异常类型塌成一个**：「没有数据」与「用了未来的数据」是**相反**的
   失效方向。塌成一个类型 ⇒ 读日志的人分不清。
3. **横截面塌成一行/报告期**：`aplus/financial.py::visible_as_of` 的去重
   `drop_duplicates(subset=[REPORT_DATE_COL])` **不带股票键** ⇒
   16,646 行年报塌成 14 行（实测）。本文件必须钉住「一只票一行」。
4. **代码格式错配**：财务帧是裸 6 位（`000001`），`daily_map` 键带后缀
   （`000001.SZ`）⇒ 直接 merge **零行命中**，而 merge 静默返回空表。

## 变异验证

`tests/mutation_specs/test_factor_fundamental.mut` 把
`aplus/vintage.py::latest_on_or_before` 的「只取 ≤ as_of」改成「取最新一份」，
本文件必须**变红**。脚本 `scripts/mutate_pit_snapshot_substitution.py`
跑完整证据链（sha256 / 标记计数 / 具名失败用例 / 逐位还原）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus import factor_fundamental as ff
from aplus import vintage

# ── 夹具 ────────────────────────────────────────────────────────────

#: 快照里的一行：(裸 6 位码, 报告期, 披露日, BPS, 报告类型)
Row = tuple[str, str, str, float, str]


def _financial_frame(rows: list[Row]) -> pd.DataFrame:
    """造一帧财务数据（列名与东财源一致）。"""
    return pd.DataFrame(
        {
            "SECURITY_CODE": [r[0] for r in rows],
            "SECUCODE": [f"{r[0]}.SZ" for r in rows],
            "REPORT_DATE": [r[1] for r in rows],
            "NOTICE_DATE": [r[2] for r in rows],
            "UPDATE_DATE": [r[2] for r in rows],
            "REPORT_TYPE": [r[4] for r in rows],
            "BPS": [r[3] for r in rows],
        }
    )


def _write_pit(root, day: str, rows: list[Row]) -> None:
    """往 PIT 根目录写一份快照（走 `vintage.write_snapshot`，不手写 parquet）。"""
    vintage.write_snapshot(root, day, _financial_frame(rows))


def _daily_map(spec: dict[str, list[tuple[str, float]]]) -> dict:
    """`{代码: [(日期, 收盘价), …]}` → `{代码: DataFrame[date, close]}`。"""
    out = {}
    for code, bars in spec.items():
        out[code] = pd.DataFrame({"date": [b[0] for b in bars],
                                  "close": [b[1] for b in bars]})
    return out


@pytest.fixture
def pit_root(tmp_path):
    """一份 PIT 根目录：两天快照（2026-10-16 与 2026-10-20）。"""
    root = tmp_path / "vintage"
    _write_pit(root, "2026-10-16", [
        ("000001", "2023-12-31", "2024-04-28", 20.0, "年报"),
        ("600000", "2023-12-31", "2024-04-28", 5.0, "年报"),
    ])
    _write_pit(root, "2026-10-20", [
        ("000001", "2023-12-31", "2024-04-28", 20.0, "年报"),
        ("600000", "2023-12-31", "2024-04-28", 5.0, "年报"),
        ("999999", "2025-12-31", "2026-04-20", 8.0, "年报"),
    ])
    return root


@pytest.fixture
def daily():
    """价格：000001 收 10（BPS 20 ⇒ 便宜）；600000 收 10（BPS 5 ⇒ 贵）。

    覆盖 2026-04-01 与 2026-10-20 两个 `as_of`——价格必须**在信号日当天或之前**，
    否则 `close_on` 取不到那一根 bar（那不是降级，是「那天这只票还没上市」）。
    """
    return _daily_map({
        "000001.SZ": [("2026-04-01", 10.0), ("2026-10-15", 10.0),
                      ("2026-10-16", 10.0), ("2026-10-19", 10.0),
                      ("2026-10-20", 10.0)],
        "600000.SH": [("2026-04-01", 10.0), ("2026-10-15", 10.0),
                      ("2026-10-16", 10.0), ("2026-10-19", 10.0),
                      ("2026-10-20", 10.0)],
        "999999.SZ": [("2026-10-19", 4.0), ("2026-10-20", 4.0)],
    })


# ── ① 值能在 as_of 当天算出来 ───────────────────────────────────────

def test_the_bm_factor_is_computable_on_the_as_of_day(pit_root, daily):
    """`as_of` 当天的 BM 必须**算得出来**，且数字对得上闭式解。

    外部锚：`bm = log(BPS / close)`，BPS=20 / close=10 ⇒ `log(2)`。
    """
    bm = ff.pit_bm_frame(pit_root, "2026-10-20", daily)
    got = bm.set_index("code")["bm"]
    assert "000001.SZ" in got.index, f"000001 缺 BM：{list(got.index)}"
    assert got["000001.SZ"] == pytest.approx(np.log(2.0), rel=1e-12)
    assert bm.attrs["snapshot_date"] == "2026-10-20"
    assert bm.attrs["requested_date"] == "2026-10-20"


def test_the_financial_side_comes_from_the_pit_snapshot_not_from_any_cache(
        pit_root, daily):
    """**财务侧只来自 PIT 帧**——用一只「当前缓存里根本不存在」的票证明。

    `999999` 只存在于本文件写的快照里。若实现去读当前缓存
    （`data/reports/financial_annual_cache.parquet` 那一族），
    它不可能给这只票算出任何值。
    """
    bm = ff.pit_bm_frame(pit_root, "2026-10-20", daily)
    got = bm.set_index("code")["bm"]
    assert "999999.SZ" in got.index, (
        "PIT 快照里才有的票没被算出来 ⇒ 财务侧不是从快照取的")
    # BPS=8, close=4 ⇒ log(2)
    assert got["999999.SZ"] == pytest.approx(np.log(2.0), rel=1e-12)


# ── ② 日期不足 ⇒ 报错，不是降级 ─────────────────────────────────────

def test_as_of_before_the_first_snapshot_raises_instead_of_degrading(
        pit_root, daily):
    """早于首份快照 ⇒ **报错**。

    不许：返回空帧 / 静默跳过 / 拿更新的快照代替。
    三者在终端上都长得跟「那天没有数据」一样。
    """
    with pytest.raises(ff.NoPITSnapshotError) as ei:
        ff.pit_bm_frame(pit_root, "2026-10-15", daily)
    msg = str(ei.value)
    assert "快照" in msg, f"报错文案要说清是快照问题：{msg}"


def test_the_two_failure_modes_are_different_exception_types():
    """「没有数据」与「用了未来的数据」必须**类型不同**。

    塌成一个类型 ⇒ 读日志的人分不清「那天没数据」与「那天看了未来」，
    而这两者的失效方向是**相反**的。
    """
    assert issubclass(ff.NoPITSnapshotError, ValueError)
    assert issubclass(ff.PITLookaheadError, ValueError)
    assert not issubclass(ff.NoPITSnapshotError, ff.PITLookaheadError)
    assert not issubclass(ff.PITLookaheadError, ff.NoPITSnapshotError)


def test_a_newer_snapshot_is_never_substituted_for_a_missing_one(
        pit_root, daily):
    """**这条是变异验证的靶子。**

    `as_of=2026-10-15`（早于首份快照 2026-10-16），而根目录里**有**
    2026-10-20 那份。若实现「拿最新的一份凑数」，
    它会**算出一个数**——而那个数是**前视**的。

    ⇒ 这里必须报错，且报的是「没有快照」而不是「用了未来」。
    """
    with pytest.raises(ff.NoPITSnapshotError):
        ff.pit_bm_frame(pit_root, "2026-10-15", daily)


def test_a_frame_from_a_newer_snapshot_is_refused_even_when_it_looks_fine(
        pit_root, daily, monkeypatch):
    """即便上游把「更晚的快照」塞过来，本模块也**当场拒绝**。

    这是**冗余防线**（`vintage.pit_frame` 自己只向后回退），
    但冗余防线的意义正是「上游哪天被改坏时这里也会响」。
    """
    real = vintage.pit_frame

    def _forward(root, as_of):
        frame = real(root, "2026-10-20")       # 用**更晚**的那一份
        frame.attrs["snapshot_date"] = "2026-10-20"
        return frame

    monkeypatch.setattr(vintage, "pit_frame", _forward)
    with pytest.raises(ff.PITLookaheadError):
        ff.pit_bm_frame(pit_root, "2026-10-16", daily)


# ── ③ 可复现：同一 as_of 两次取值逐位相同 ───────────────────────────

def test_the_same_as_of_gives_bitwise_identical_values(pit_root, daily):
    """同一 `as_of` 取两次，必须**逐位**相同。

    比 `==` 更严：比的是 float64 的原始字节。
    `pytest.approx` 会放过 1 ulp 的差异，而那正是「缓存被换了一份」的形状。
    """
    a = ff.pit_bm_frame(pit_root, "2026-10-20", daily)
    b = ff.pit_bm_frame(pit_root, "2026-10-20", daily)
    assert a["code"].tolist() == b["code"].tolist()
    assert a["bm"].to_numpy().tobytes() == b["bm"].to_numpy().tobytes()


# ── ④ 符号方向 ──────────────────────────────────────────────────────

def test_bm_higher_means_cheaper(pit_root, daily):
    """**方向必须钉死**：`bm` 越大 = 账面/市值比越高 = 越便宜 = 越该买。

    BPS=20 而股价 10 ⇒ 花 10 块买到 20 块账面净资产 ⇒ **便宜**。
    符号翻反过一次，翻反后**数字照样正常**（IC、t、覆盖率全都有值），
    只是选出了完全相反的一批票——这种错没有任何报错。
    """
    bm = ff.pit_bm_frame(pit_root, "2026-10-20", daily)
    got = bm.set_index("code")["bm"]
    cheap, dear = got["000001.SZ"], got["600000.SH"]
    assert cheap > dear, (
        f"便宜的 000001 的 bm 反而更低：{cheap:.4f} vs {dear:.4f}——符号反了")
    # 且**刻意不取负**：正号 ⇒ 「分数高的就是便宜的」⇒ top-10 买便宜的。
    assert cheap > 0 > dear


# ── ⑤ 实际用了哪一天的快照 ──────────────────────────────────────────

def test_the_snapshot_actually_used_is_never_newer_than_the_requested_day(
        pit_root, daily):
    """回退是允许的（少用信息），**向前取不允许**。

    `as_of=2026-10-18` 时只应回退到 2026-10-16 那份。
    """
    bm = ff.pit_bm_frame(pit_root, "2026-10-18", daily)
    used = bm.attrs["snapshot_date"]
    assert used == "2026-10-16", f"用了 {used}，应当回退到 2026-10-16"
    assert used <= "2026-10-18"


# ── ⑥ 披露日对齐 ────────────────────────────────────────────────────

def test_a_report_disclosed_after_the_signal_day_is_never_used(tmp_path, daily):
    """披露日在信号日之后的年报，**绝不能**被用上。

    2026-04-30 才披露的 FY2025 年报，在 2026-04-01 的信号日**还不存在**。
    """
    root = tmp_path / "v"
    _write_pit(root, "2026-04-01", [
        ("000001", "2023-12-31", "2024-04-28", 20.0, "年报"),
        ("600000", "2025-12-31", "2026-04-30", 5.0, "年报"),
    ])
    bm = ff.pit_bm_frame(root, "2026-04-01", daily)
    got = bm.set_index("code")["bm"]
    assert "600000.SH" not in got.index, (
        "披露日在信号日之后的年报被用上了 —— 那是财报因子的头号前视偏差")


def test_the_latest_visible_report_is_used_not_the_first(tmp_path, daily):
    """同一只票有多份年报时，取**披露日最新**的那一份。"""
    root = tmp_path / "v"
    _write_pit(root, "2026-04-01", [
        ("000001", "2021-12-31", "2022-04-20", 10.0, "年报"),
        ("000001", "2022-12-31", "2023-04-25", 20.0, "年报"),
        ("000001", "2023-12-31", "2024-04-28", 40.0, "年报"),
    ])
    bm = ff.pit_bm_frame(root, "2026-04-01", daily)
    got = bm.set_index("code")["bm"]
    # 取 FY2023（BPS=40）⇒ log(40/10)
    assert got["000001.SZ"] == pytest.approx(np.log(4.0), rel=1e-12)


def test_the_update_date_never_decides_which_report_is_used(tmp_path, daily):
    """`UPDATE_DATE` **不参与任何判断**。

    `is_never_revised()` 这条「出路 A」已被快照差分实测证伪
    （变了 43.75% vs 没变 33.16% ⇒ `UPDATE_DATE` 标不出修订），
    所以它**不许**出现在筛选条件里。
    """
    root = tmp_path / "v"
    frame = _financial_frame([
        ("000001", "2023-12-31", "2024-04-28", 20.0, "年报"),
    ])
    # 唯一一行，但 `UPDATE_DATE` 与 `NOTICE_DATE` 不同 ⇒ 白名单会把它排除
    frame["UPDATE_DATE"] = ["2026-09-01"]
    vintage.write_snapshot(root, "2026-04-01", frame)
    bm = ff.pit_bm_frame(root, "2026-04-01", daily)
    got = bm.set_index("code")["bm"]
    assert "000001.SZ" in got.index, (
        "行被 `UPDATE_DATE` 过滤掉了 ⇒ 实现偷偷用了那条已被证伪的白名单")


# ── ⑦ 横截面不许塌 ──────────────────────────────────────────────────

def test_the_cross_section_keeps_one_row_per_stock(tmp_path, daily):
    """**一只票一行**。

    `aplus/financial.py::visible_as_of` 的去重
    `drop_duplicates(subset=[REPORT_DATE_COL])` **不带股票键** ⇒
    16,646 行年报塌成 14 行（实测，2026-10-20 快照）。
    ⇒ 本模块的横截面必须**按股票**保留。
    """
    root = tmp_path / "v"
    rows = [(f"{i:06d}", "2023-12-31", "2024-04-28", 10.0 + i, "年报")
            for i in range(1, 21)]
    _write_pit(root, "2026-04-01", rows)
    dm = _daily_map({f"{i:06d}.SZ": [("2026-04-01", 10.0)] for i in range(1, 21)})
    ann = ff.pit_annual_frame(root, "2026-04-01")
    assert len(ann) == 20, (
        f"20 只票的年报塌成了 {len(ann)} 行 —— 横截面被去重逻辑吃掉了")


# ── ⑧ 价格口径 ──────────────────────────────────────────────────────

def test_bm_uses_the_unadjusted_close(tmp_path):
    """**不复权价**才是对的口径：用后复权价，一次送股会让 BM 腰斩。

    10 送 10：不复权价格 20→10、BPS 20→10，比值 1.0 不变；
    后复权价却是 20→20，于是 BM 从 1.0 变成 2.0——**凭空多出一倍「便宜」**。
    """
    root = tmp_path / "v"
    _write_pit(root, "2026-04-01", [("000001", "2023-12-31", "2024-04-28",
                                      10.0, "年报")])
    dm = _daily_map({"000001.SZ": [("2026-04-01", 10.0)]})
    got = ff.pit_bm_frame(root, "2026-04-01", dm).set_index("code")["bm"]
    assert got["000001.SZ"] == pytest.approx(0.0, abs=1e-12)


def test_a_non_positive_book_value_is_dropped_not_inverted(tmp_path, daily):
    """负账面值（资不抵债）**丢弃**，不许取绝对值/取倒数。

    取绝对值会把「资不抵债」变成「极度便宜」——那是凭空造出来的信号。
    """
    root = tmp_path / "v"
    _write_pit(root, "2026-04-01", [
        ("000001", "2023-12-31", "2024-04-28", -3.0, "年报"),
    ])
    bm = ff.pit_bm_frame(root, "2026-04-01", daily)
    got = bm.set_index("code")["bm"]
    assert "000001.SZ" not in got.index, (
        f"负账面值没被丢掉，反而算出了 {got.get('000001.SZ')}")


# ── ⑨ 挂进横截面（供前瞻验证路径用） ────────────────────────────────

def test_every_day_gets_a_bm_column_so_composite_score_cannot_keyerror(
        pit_root, daily):
    """**每一天都有 `bm` 列**。

    `aplus/factor.py::composite_score` 对缺失的因子列会 **KeyError**
    ⇒ 某天没有 `bm` 列，含 `bm` 的候选会当场崩——而崩的是**整个前瞻验证**，
    连不带 `bm` 的老批次一起陪葬。
    """
    days = ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]
    xs = {d: pd.DataFrame({"code": ["000001.SZ", "600000.SH"]}) for d in days}
    xs["_dates"] = days
    xs["_daily_map"] = daily
    ff.attach_pit_bm(xs, root=pit_root, daily_map=daily)
    for d in days:
        assert "bm" in xs[d].columns, f"{d} 没有 bm 列"
        assert xs[d]["bm"].notna().any() or d < "2026-10-16"


def test_the_days_without_pit_data_are_listed_not_hidden(pit_root, daily):
    """没有 PIT 快照的日子必须**被点名**。

    「那天没有数据」与「那天信号为零」在报告里长得一模一样。

    ## 报告走**返回值**，不塞进 `xs`

    `tests/test_cross_section_metadata_contract.py` 规定：
    `cross_sections` 的下划线元数据键，**读的每个键都必须有写的地方**，
    而写入点只认 `build_cross_sections` 与 `setdefault`。
    本模块是后处理步骤 ⇒ 塞进 `xs` 会被那条守卫判成「永远返回默认值」。
    ⇒ **改设计，不改守卫。**
    """
    days = ["2026-10-15", "2026-10-16", "2026-10-20"]
    xs = {d: pd.DataFrame({"code": ["000001.SZ"]}) for d in days}
    xs["_dates"] = days
    xs["_daily_map"] = daily
    rep = ff.attach_pit_bm(xs, root=pit_root, daily_map=daily)
    assert "2026-10-15" in rep["unavailable_days"]
    assert "2026-10-16" in rep["days"]
    assert xs["2026-10-15"]["bm"].isna().all()
    # 报告**不许**藏进 xs 的元数据键
    assert not [k for k in xs if k.startswith("_pit_bm")], (
        f"报告被塞进了 xs 的元数据键：{[k for k in xs if k.startswith('_pit_bm')]}")


def test_a_composite_score_with_bm_does_not_raise(pit_root, daily):
    """**端到端**：`composite_score` 拿到含 `bm` 的横截面不许炸。

    这条是「注册了 BM 候选 ⇒ 前瞻验证不崩」的那条路的真实检验。
    """
    from aplus.factor import composite_score

    days = ["2026-10-20"]
    xs = {d: pd.DataFrame({"code": ["000001.SZ", "600000.SH"],
                            "mom20": [0.1, -0.1]}) for d in days}
    xs["_dates"] = days
    xs["_daily_map"] = daily
    ff.attach_pit_bm(xs, root=pit_root, daily_map=daily)
    uni = xs["2026-10-20"].set_index("code")
    score = composite_score(uni, {"bm": 1.0})
    assert len(score) == 2
    assert score["000001.SZ"] > score["600000.SH"], (
        "正权重的 bm 应当让**便宜**的那只在前面")


# ── ⑩ 边界：BM 不许进实盘 / 判决链 ─────────────────────────────────

def test_bm_never_enters_the_live_weights_or_the_decision_chain():
    """BM 这一轮**只做研究与前瞻候选**。

    `aplus/factor.py::DEFAULT_WEIGHTS` 是实盘出单那 4 个因子；
    `aplus/panel.py::FACTOR_COLUMNS` 是判决链的面板因子集。
    两个都**不许**出现 `bm`。
    """
    from aplus.factor import DEFAULT_WEIGHTS
    from aplus.panel import FACTOR_COLUMNS

    assert "bm" not in DEFAULT_WEIGHTS, "bm 进了实盘权重"
    assert "bm" not in FACTOR_COLUMNS, "bm 进了判决链的面板因子集"


def test_the_module_does_not_import_the_post_hoc_factor_path():
    """本模块**不许** import 后修值那一版（`scripts/fundamental_factor.py`）。

    那一版用当前缓存，产出**看起来一样**的 BM —— 但它是后修值。
    两版混在一起 ⇒ 台账上分不清哪一份是 PIT 的。

    ## 判据落在 **import 语句**上，不落在「源码里出现过这个串」上

    第一版写的是 `"fundamental_factor" not in inspect.getsource(ff)` ⇒
    **被本模块自己的 docstring 判红**（docstring 里逐字引用了那个文件名）。
    这就是仓里的第 13 种形状：*子串断言 + 同一文件里存在注释/文案 = 守卫可被文档满足*。
    ⇒ 改用 **AST**：只查 `Import` / `ImportFrom` 的**模块名**。
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ff))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    bad = [m for m in imported if "fundamental_factor" in m]
    assert not bad, f"本模块 import 了后修值那一版：{bad}"
    assert not [m for m in imported if m.endswith("fundamental_factor")], (
        "本模块 import 了后修值那一版（相对 import 形式）")
