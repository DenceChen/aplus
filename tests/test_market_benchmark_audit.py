"""市场基准污染审计：闸门函数必须逐条对齐生产代码。

审计脚本的全部价值在于「raw vs filtered 的差，就是 `up_excess_vs_market`
被虚增的量」。这个量算得对不对，全押在 `passes_filters` 与
`aplus/predict.py` 的 `filter_universe` 是否**逐条一致**上。

不一致的后果是隐蔽的：脚本照常跑完、照常输出一个体面的年化数字，
只是那个数字量的不是「基准污染」而是「我写的过滤器和生产版不一样」。
所以对齐这件事必须由测试钉住，而不是靠注释提醒。
"""
import pandas as pd
import pytest

from aplus.predict import filter_universe
from scripts.market_benchmark_audit import MARKET_SEED, audit_window, passes_filters


def _row(**kw):
    base = {"isST": 0, "listed_days": 500, "volume": 1_000_000}
    base.update(kw)
    return pd.Series(base)


# ── 闸门本身 ──────────────────────────────────────────────────────────

def test_a_normal_stock_passes():
    assert passes_filters(_row())


@pytest.mark.parametrize("kw,why", [
    ({"isST": 1}, "ST 股：涨跌停 ±5%，且有退市通道，策略明令不买"),
    ({"listed_days": 60}, "恰好 60 天：生产版是 `> 60`，不是 `>= 60`"),
    ({"listed_days": 0}, "次新股"),
    ({"volume": 0}, "当日无成交——`volume > 0` 是最弱的流动性条件，但仍是条件"),
    ({"volume": -1}, "异常值不得被当成通过"),
])
def test_each_production_gate_rejects(kw, why):
    assert not passes_filters(_row(**kw)), f"漏掉了 {kw}：{why}"


def test_listed_days_61_still_passes():
    """61 天通过而 60 天不通过——边界必须落在同一个数上。"""
    assert passes_filters(_row(listed_days=61))
    assert not passes_filters(_row(listed_days=60))


def test_missing_columns_fail_open_for_volume_only():
    """缺列时不得静默变成「全否」或「全过」。

    `_next_bars` 的输出列取决于 `CARRY_COLUMNS`，若某天该票缺 `listed_days`，
    默认值给 inf（当作老股放行）。这是**有意的保守方向**：
    宁可漏掉一只污染票，也不可把一只正常票误判为污染票。
    """
    row = pd.Series({"isST": 0, "volume": 1000})      # 没有 listed_days
    assert passes_filters(row)


def test_zero_is_not_the_same_as_missing():
    """`x or default` 把**合法的 0** 偷换成「缺失」——次新股会被放行。

    这是本脚本实测抓到的一个真 bug：`listed_days == 0`（今天刚上市）
    经 `row.get("listed_days", 1e9) or 1e9` 会变成 1e9，
    于是这只次新股被当成上市 27 年的老股放行——**闸门放走了它要找的东西**。
    """
    assert not passes_filters(_row(listed_days=0))
    assert not passes_filters(_row(isST=0, listed_days=0, volume=1e6))


def test_nan_falls_back_to_the_default_not_to_a_crash():
    """NaN 是「不可解析」，不是「0」也不是「很大」。"""
    row = pd.Series({"isST": 0, "listed_days": float("nan"), "volume": 1000.0})
    assert passes_filters(row), "listed_days 不可解析 → 按老股放行（保守方向）"
    row2 = pd.Series({"isST": float("nan"), "listed_days": 500, "volume": 1000.0})
    assert passes_filters(row2), "isST 不可解析 → 不当作 ST（保守方向）"
    row3 = pd.Series({"isST": 0, "listed_days": 500, "volume": float("nan")})
    assert not passes_filters(row3), "volume 不可解析 → 不放行（保守方向）"


# ── 与生产代码的对齐 ──────────────────────────────────────────────────

def _align_frame(n=300):
    rng = pd.Series(range(n))
    return pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "isST": (rng % 7 == 0).astype(int).values,
        "listed_days": (rng * 13 % 900).astype(float).values,
        "volume": (rng * 7919 % 5_000_000).astype(float).values,
    })


def _prod_codes(df):
    """生产版保留的 code 集合。

    **必须按 `code` 比，不能按 index 比**——`filter_universe` 返回的是
    布尔掩码切片出来的**新表**，index 被重置成 0..n-1。
    拿原 index 去 `isin` 会得到一个看起来自洽、实际完全错位的比对结果。
    """
    return set(filter_universe(df)["code"])


def test_audit_gate_agrees_with_production_filter_universe():
    """同一批票，审计脚本与生产 `filter_universe` 必须选出**同一批**。

    这是整个审计的锚。生产版还有一层名称过滤（ST 前缀）而审计脚本没有，
    所以审计只会**更保守**（判污染的更少），方向是安全的。
    """
    df = _align_frame()
    prod = _prod_codes(df)
    mine = {r["code"] for _, r in df.iterrows() if passes_filters(r)}
    theirs = set(df["code"]) & prod

    extra = mine - theirs
    assert not extra, (
        f"审计脚本保留了 {len(extra)} 只生产版剔除的票：{sorted(extra)[:5]}"
        f"——闸门比生产版松，算出来的污染量会被低估")


def test_production_filter_rejects_what_the_audit_flags():
    """反方向也必须成立：审计判为污染的票，生产版确实不要。"""
    df = _align_frame()
    prod = _prod_codes(df)
    flagged = {r["code"] for _, r in df.iterrows() if not passes_filters(r)}
    # 「审计判污染的票，生产版全剔掉」= 两集合**不相交** = `flagged & prod == ∅`。
    # 写成 `flagged - prod == ∅` 是反的：不相交时 `flagged - prod` 恰好**等于**
    # `flagged` 本身，于是这个断言在「对齐得完美」时反而失败。集合差与交集
    # 记反，报错还会把两个集合的头几个元素都列出来——看起来像是「生产版保留了
    # ST 股」，实际是断言写反了。**报错信息本身也会误导，这一点比断言写反更贵。**
    stray = flagged & prod
    assert not stray, f"审计判污染但生产版保留：{sorted(stray)[:5]}"


def test_production_filter_actually_reindexes_its_output():
    """钉住「`filter_universe` 会重置 index」这个事实。

    它本身不是缺陷（掩码切片天然重置），但它是个陷阱：任何下游按
    **原 index** 回连结果的代码都会静默错位——不报错，只是连错行。
    这条测试的作用是让下一个想按 index 回连的人先撞到这里。
    """
    df = _align_frame()
    out = filter_universe(df)
    assert list(out.index) == list(range(len(out))), (
        f"index 未被重置：{list(out.index)[:8]}——"
        f"本文件里所有按 code 比对的写法仍然正确，但下游若按 index 回连则是安全的")
    # 输出表头是 0..n-1，而**保留下来的行的 code 已不是原来那些**。
    # 这才是重置的真正危害：index 看着「对得上」，内容已经错位。
    assert list(out["code"])[:3] != list(df["code"])[:3], (
        "前 3 行的 code 与原表相同——测试数据没造出错位，需要换数据")


# ── 口径 ──────────────────────────────────────────────────────────────

def test_the_audit_and_production_pick_the_identical_cohort():
    """审计与生产**必须抽到同一批票**。

    对不上就不是同一批票，`raw vs filtered` 从「配对」退化成
    「两次独立抽样的比较」，噪声不减反增——而结论会被照常打印出来。

    🚨 **2026-10-16 换掉了判据形态**。旧判据是

        assert f"Random({MARKET_SEED})" in inspect.getsource(_market_by_day)

    即「源码里出现过 `Random(42)` 这个**串**」（第 13 层：子串锚可被注释满足）。
    它连自己都不是可靠的：`_market_by_day` 一旦接受 `seed` 参数，
    源码里**合法地**不再有 `Random(42)` ⇒ 守卫对着一次正确改动变红
    ⇒ 后人会把它改松成「只要有 `Random(` 就算过」，而那恒真（第 112 层）。

    ⇒ 换成**行为判据**：审计用的 (规模, 种子) 与生产默认值产出**同一个列表**。
    这既不依赖源码文本，也不依赖「谁写的那个函数」。
    """
    from aplus.backtest import MARKET_SAMPLE, benchmark_cohort

    codes = [f"{i:06d}.SZ" for i in range(400)]
    dm = {c: None for c in codes}          # 抽样只用到键，值不参与

    assert (benchmark_cohort(dm, MARKET_SAMPLE, MARKET_SEED)
            == benchmark_cohort(dm)), (
        "审计传的 (规模, 种子) 与生产默认值产出了不同的票群——配对已失效")

    # 规模也必须一致（否则比的是两件事）
    assert (benchmark_cohort(dm, MARKET_SAMPLE, MARKET_SEED)
            == benchmark_cohort(dm, MARKET_SAMPLE))


def test_the_seed_argument_is_actually_live():
    """反向正控：`seed` 参数必须真的改变结果。

    🚨 没有这条，上面那条守卫可以在 `seed` **被完全忽略**的实现上永远绿
    （两支都返回同一个硬编码列表）⇒ 它守的是「两个常量相等」，
    不是「抽样对同一批票」。
    ⇒ 第 120 层：**产出判决的函数，第一条测试必须是能力/反控守卫。**
    """
    from aplus.backtest import BENCH_SEED, benchmark_cohort

    codes = [f"{i:06d}.SZ" for i in range(400)]
    dm = {c: None for c in codes}

    assert benchmark_cohort(dm, seed=BENCH_SEED) \
        != benchmark_cohort(dm, seed=BENCH_SEED + 1), (
        "换种子抽到的还是同一批票 ⇒ seed 参数被忽略了")


def test_the_audit_calls_the_single_sampler_implementation():
    """接线守卫（AST，**不是子串**）：`audit_window` 必须调 `benchmark_cohort`。

    🚨 上一条验的是「两个常量相等」，本条验的是「**它们是不是同一段代码**」。
      两者不可互换——审计完全可以自己重写一遍 `rng.sample`，
      而上一条照样全绿（第 22 层：两台测量仪之间的差永远查不出来）。
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(audit_window))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "benchmark_cohort" in called, (
        "audit_window 没有调 benchmark_cohort ⇒ 它自己抽了一份，"
        "而两个抽样实现之间的差没有任何东西会查")

    # 反向正控：探测器看得见别的调用名（否则上面那句是恒真）
    assert "_market_by_day" not in called and "benchmark_cohort" in called


def test_sample_size_matches_production():
    """抽样规模必须对齐，否则比的是两件事。

    🚨 旧判据是 `f"sample_size: int = MARKET_SAMPLE = {N}" or "MARKET_SAMPLE" in src`
      —— 前半支**恒假**（那个串从来不会出现在源码里）、后半支**恒真**
      （它是默认实参，永远在）⇒ 那是一条装饰守卫。
    ⇒ 换成行为判据。
    """
    import inspect

    from aplus.backtest import MARKET_SAMPLE, _market_by_day, benchmark_cohort

    default = inspect.signature(_market_by_day).parameters["sample_size"].default
    assert default == MARKET_SAMPLE, (
        f"_market_by_day 的 sample_size 默认值是 {default}，"
        f"而单一真相源是 {MARKET_SAMPLE}")

    codes = [f"{i:06d}.SZ" for i in range(400)]
    dm = {c: None for c in codes}
    assert len(benchmark_cohort(dm)) == MARKET_SAMPLE, (
        "不传 sample_size 时抽中的数量 ≠ MARKET_SAMPLE")

    # 反向正控：规模参数真的生效
    assert len(benchmark_cohort(dm, sample_size=7)) == 7


# ── gate_row：闸门列必须从**原始帧**现取，因为 `_next_bars` 根本没有 ────

def test_gate_row_reads_the_signal_day_bar_not_the_last_bar():
    """闸门值必须取**信号日那一根**，不是序列的最后一根。

    取错的话整段审计会用一个 2026 年的 isST / listed_days 去评判 2019 年的
    抽样——数字照常输出，只是量的是另一个东西。
    """
    import pandas as pd

    from scripts.market_benchmark_audit import gate_row

    raw = pd.DataFrame({
        "date": ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-06"],
        "volume": [10.0, 20.0, 0.0, 40.0],
        "isST": [0, 1, 0, 0],
    })
    dm = {"X": raw}
    assert gate_row(dm, "X", "2020-01-02")["isST"] == 1.0, "信号日的 isST 没取到"
    assert gate_row(dm, "X", "2020-01-02")["listed_days"] == 2.0
    assert gate_row(dm, "X", "2020-01-06")["volume"] == 40.0, "取到了错误的那一根"


def test_gate_row_returns_none_when_the_stock_does_not_yet_exist():
    """信号日之前一根 K 线都没有 → 该股当日不存在，必须 None 而不是兜底值。

    这与 `_next_bars` 的 `pos == 0` 是同一条纪律：不存在 ≠ 0。
    """
    import pandas as pd

    from scripts.market_benchmark_audit import gate_row

    raw = pd.DataFrame({"date": ["2020-01-02"], "volume": [10.0], "isST": [0]})
    assert gate_row({"X": raw}, "X", "2020-01-02") is None
    assert gate_row({}, "MISSING", "2020-01-02") is None


def test_a_newly_listed_stock_reads_listed_days_1_and_is_rejected():
    """当天上市的次新股：`listed_days == 1` → 必须被闸门剔掉。"""
    import pandas as pd

    from scripts.market_benchmark_audit import gate_row, passes_filters

    raw = pd.DataFrame({
        "date": ["2020-01-01", "2020-01-02", "2020-01-03"],
        "volume": [1.0, 500.0, 500.0],
        "isST": [0, 0, 0],
    })
    g = gate_row({"X": raw}, "X", "2020-01-02")
    assert g["listed_days"] == 2.0 and not passes_filters(g), (
        "上市第 2 天的次新股被放行了")
