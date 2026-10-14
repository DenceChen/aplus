"""`amount` 可用性实测脚本。

## 为什么「测出来是 0」也要钉住

`backtest.py` 的 `_bars_tradable` 有一条 `amount <= 0 → 判停牌` 的 clause。
2026-10-14 实测全库 8,715,863 行：NaN 零、矛盾零、`amount==0` 与
`volume==0` **严格等价**——那个 clause 是冗余的防御，不是经验必需。

于是这里测的不是「今天好不好」，而是**判据还能不能识破劣化**：
把退化数据喂进去，它必须喊出来。否则这条诊断就退化成第二个化石，
和一个已经假掉的注释没有区别。
"""
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from scripts import measure_amount_availability as M


def _store(tmp_path, spec: dict[str, dict]):
    d = tmp_path / "daily"
    d.mkdir(parents=True, exist_ok=True)
    for code, cols in spec.items():
        pd.DataFrame(cols).to_parquet(d / f"{code}.parquet", index=False)
    return d


def test_a_clean_store_reports_full_availability(tmp_path):
    d = _store(tmp_path, {"000001.SZ": {
        "date": ["2026-10-12", "2026-10-13"],
        "amount": [1.0e8, 2.0e8], "volume": [1000.0, 2000.0]}})
    m = M.measure([d / "000001.SZ.parquet"], verbose=False)
    assert m["nan_rows"] == 0
    assert m["amount0_but_volume_gt0"] == 0
    assert m["volume0_but_amount_gt0"] == 0


def test_the_zero_volume_stop_is_detected_as_self_consistent(tmp_path):
    """停牌日 `amount==0` 且 `volume==0` —— **不算矛盾**，不许误报。

    误报比盲区更吵：长期停牌股会被整片刷屏，
    而刷屏会让人对真正的缺口也麻木。
    """
    d = _store(tmp_path, {"000001.SZ": {
        "date": ["2026-10-12", "2026-10-13"],
        "amount": [0.0, 2.0e8], "volume": [0.0, 2000.0]}})
    m = M.measure([d / "000001.SZ.parquet"], verbose=False)
    assert m["zero_rows"] == 1
    assert m["amount0_and_volume0"] == 1
    assert m["amount0_but_volume_gt0"] == 0


def test_traded_but_zero_amount_is_flagged_as_a_contradiction(tmp_path):
    """**这条是整个脚本存在的理由。**

    `amount == 0` 而 `volume > 0`：有成交却记成交额为 0。
    `_bars_tradable` 会把这批 bar 判成停牌，于是**可交易的票被静默剔除**
    ——而且不报任何错。

    只查 NaN 查不到它：退化也可能写 0 而不是空值。
    """
    d = _store(tmp_path, {"000001.SZ": {
        "date": ["2026-10-12"], "amount": [0.0], "volume": [5000.0]}})
    m = M.measure([d / "000001.SZ.parquet"], verbose=False)
    assert m["amount0_but_volume_gt0"] == 1, "有钱成交却成交额为 0，没被抓出来"


def test_the_reverse_contradiction_is_also_seen(tmp_path):
    """`volume == 0` 但 `amount > 0`：没有成交却有成交额。

    这个方向的害处不同：它让停牌日看起来有流动性，
    而 `volume <= 0` 仍会拦住它，所以后果较轻——但**方向相反意味着
    判据不能只看一边**，只看一边会把另外一边永远漏掉。
    """
    d = _store(tmp_path, {"000001.SZ": {
        "date": ["2026-10-12"], "amount": [9.9e8], "volume": [0.0]}})
    m = M.measure([d / "000001.SZ.parquet"], verbose=False)
    assert m["volume0_but_amount_gt0"] == 1


def test_nan_amount_is_counted_and_the_file_named(tmp_path):
    """NaN 要报**数**也要报**哪个文件**——只报总数会让人无法定位。"""
    d = _store(tmp_path, {
        "000001.SZ": {"date": ["2026-10-12"], "amount": [np.nan], "volume": [10.0]},
        "000002.SZ": {"date": ["2026-10-12"], "amount": [1e8], "volume": [10.0]}})
    m = M.measure(sorted(d.glob("*.parquet")), verbose=False)
    assert m["nan_rows"] == 1
    assert m["files_with_nan"] == ["000001.SZ"]
    assert m["n_files_with_nan"] == 1


def test_placeholder_constants_are_visible(tmp_path):
    """退化也可能写**固定值**而不是空值——NaN 检查对它是瞎的。

    唯一值几乎等于样本数 → 真实测量值；
    唯一值远小于样本数 → 常量填充，字段形同虚设。
    """
    n = 5000
    dates = [f"2026-01-{(i % 28) + 1:02d}" for i in range(n)]
    real = pd.DataFrame({"date": dates,
                         "amount": np.arange(1, n + 1, dtype="float64"),
                         "volume": np.arange(1, n + 1, dtype="float64")})
    filled = pd.DataFrame({"date": dates,
                           # 占位值必须落在真实值区间**之外**，否则它
                           # 不产生新的唯一值，检测会被自己的数据骗过去
                           # （首版取 1234.0 而真实值是 1..5000，正好撞上）
                           "amount": np.full(n, 123_456.0),
                           "volume": np.arange(1, n + 1, dtype="float64")})
    d = tmp_path / "d"
    d.mkdir()
    real.to_parquet(d / "000001.SZ.parquet", index=False)
    filled.to_parquet(d / "000002.SZ.parquet", index=False)
    m = M.measure(sorted(d.glob("*.parquet")), verbose=False)
    # 两票混在一起：真实票 5000 个不同值 + 占位票只贡献 1 个 → 5001 个唯一
    assert m["uniq_of_200k"] == 5001, (
        "占位常量检测失效——字段被写成常数而 NaN 检查完全看不出来")


def test_sampling_is_reproducible(tmp_path):
    """**数字要能被复查。** 同样的种子必须抽到同样的票。

    否则每次跑出来的数字都不一样，没人会相信第二个数，
    诊断本身就失去了证据价值。
    """
    d = tmp_path / "daily"
    d.mkdir()
    for i in range(50):
        pd.DataFrame({"date": ["2026-10-12"], "amount": [1.0e8],
                      "volume": [10.0]}).to_parquet(d / f"{i:06d}.SZ.parquet",
                                                    index=False)
    a = M.pick_files(d, 10)
    b = M.pick_files(d, 10)
    assert a == b, "抽样不可复现——同一份代码两次跑出不同样本"
    assert len(a) == 10


def test_the_exit_code_flags_a_contradiction(tmp_path, capsys):
    """矛盾要能变成**非零退出码**——否则接进 launchd 也是一声不吭。"""
    d = _store(tmp_path, {"000001.SZ": {
        "date": ["2026-10-12"], "amount": [0.0], "volume": [5000.0]}})
    assert M.main(["--daily-dir", str(d), "--sample", "0"]) == 2
    assert "矛盾" in capsys.readouterr().out

    d2 = _store(tmp_path / "clean", {"000001.SZ": {
        "date": ["2026-10-12"], "amount": [1e8], "volume": [10.0]}})
    assert M.main(["--daily-dir", str(d2), "--sample", "0"]) == 0


#: 真实库里**已知**的 amount==0 但 volume>0 的行。
#: 见下 `_known_contradictions_in_the_real_store`。
KNOWN_BAD = {
    ("002336.SZ", "2020-06-17"),
    ("002500.SZ", "2020-06-17"),
}


def _contradictions():
    """全库扫出 (代码, 日期) 对——白名单比对要精确到行，不能只比个数。"""
    out = set()
    for p in sorted(M.DEFAULT_DAILY.glob("*.parquet")):
        if not {"amount", "volume"} <= set(pq.read_schema(p).names):
            continue                      # 缺列由 coverage 判，不是这里的事
        t = pq.read_table(p, columns=["amount", "volume", "date"]).to_pandas()
        a = t["amount"].to_numpy("float64")
        v = t["volume"].to_numpy("float64")
        for j in ((a == 0) & (v > 0)).nonzero()[0]:
            out.add((p.stem, str(t["date"].iloc[j])))
    return out


def test_the_real_store_has_no_amount_contradiction():
    """拿真实库跑一遍。

    ## 为什么是白名单而不是断言「恒为 0」

    真实库里**确实有** 2 行这种矛盾（2020-06-17，两只票：成交量和换手率都是真的，
    成交额却是 0）。所以断言恒为 0 会让这条测试**永远红**——
    而永远红的测试会教所有人忽略测试，比没有测试更坏。

    白名单是另一种形状：**今天绿着（缺陷被写在明面上），
    明天多出一行就红**。它把「已知且有界」和「新增」区分开，
    这正是这条判据需要的分辨力。

    ## 这个缺陷的形状（比量级重要）

    `_bars_tradable` 先看 `volume > 0` 放行，再看 `amount` 是否「已知且 <= 0」
    → 判停牌。所以这 2 根**明明成交了的 bar 被静默剔除**，不报任何错。

    量级微不足道（2 / 8,715,863），但「可交易的 bar 被当成没成交」正是这套
    系统最隐蔽的失败形状：它不抛异常、不打日志，只是让某些日子少几个候选。
    """
    if not list(M.DEFAULT_DAILY.glob("*.parquet")):
        pytest.skip("主库还没有日线数据")
    got = _contradictions()
    new = got - KNOWN_BAD
    assert not new, (
        f"新增 {len(new)} 处「有成交却记成交额为 0」：{sorted(new)[:10]}——"
        "`_bars_tradable` 会把这些 bar 判成停牌，可交易的票被静默剔除。"
        "若确认是源站口径，请在 KNOWN_BAD 里显式登记，不要直接改断言。"
    )
    gone = KNOWN_BAD - got
    assert not gone, (
        f"白名单里的 {sorted(gone)} 已经不存在了——说明数据补过了，"
        "把它从 KNOWN_BAD 里删掉，别让已知缺陷名单只增不减")


def test_the_real_store_amount_has_no_null_values():
    """`amount` 的空值率必须为 0 —— 那是那条注释里 59.4% 的来源，已被修掉。

    缺列的 2 只由 coverage 判 `missing_columns`，是**独立**问题；
    这里只要求它们被**报出来**，而不是被静默跳过（见脚本 docstring）。
    """
    files = M.pick_files(M.DEFAULT_DAILY, None)      # 全库，不用抽样
    m = M.measure(files, verbose=False)
    assert m["nan_rows"] == 0, (
        f"{m['n_files_with_nan']} 只股票的 amount 有空值："
        f"{m['files_with_nan'][:5]}")
    # 缺列文件必须被点名，且不多不少就是那 2 只
    assert m["n_files_without_column"] == 2, (
        f"缺 amount 列的文件变成 {m['n_files_without_column']} 只："
        f"{m['files_without_column']}——若变多，去查 coverage 的 missing_columns")
    assert m["volume0_but_amount_gt0"] == 0, "没有成交却有成交额，方向反了"
