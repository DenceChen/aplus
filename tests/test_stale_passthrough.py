"""**陈旧透传**：把早已失效的末根价格当成「今天的价格」继续拿去排名。

## 这不是「陈旧股票还在池子里」，是「陈旧股票在**用旧价格出信号**」

横截面 `panel.cross_section` 用 `searchsorted(side="right") - 1` 定位「信号日 D
的最后一根 K 线」。若该股在 D 当天没有 bar，这一行指向的就是**D 之前某天**的
价格，而 `filter_universe` 原本只用 `max_stale_days=30`（**自然日**）兜底——
于是最多 30 个自然日（约 21 个交易日）里，一只已经停笔的股票会带着
**冻结的因子值**与当天真实有 bar 的股票同场排名。

## 缺陷边界：它不是「可能性」，是**实测 100% 的纯噪声**

2026-10-14 对 `data/reports/*.parquet` 四个已落盘回测产物做全量核对
（N = 33,760 条篮子记录）：

| 末根滞后 | 篮子记录数 | 其中产出收益（result != no_data） |
|---|---|---|
| lag = 0 | 33,682 | 33,682 |
| lag ∈ [1, 30] | **78** | **0** |

78 条陈旧透传记录，**一条都没成交**，全部 `no_data`。同时它们**一条都没有
D+1 bar**——所以它们不是「可能买不进」，而是**必然买不进**：篮子成分被一个
dead price 占住，收益分布的分母被动变小。

`max_stale_days=30` 因此是一个**只放行噪声、从不放行真实交易**的阈值。

## 逐产物的污染分布（2026-10-14，`/tmp/aplus_stale/contam.py`）

| 产物 | 篮子记录 | 陈旧透传 | 占比 | 其中 no_data |
|---|---|---|---|---|
| `backtest.parquet` | 9,660 | 17 | 0.176% | 17/17 |
| `backtest_2y.parquet` | 10,800 | 50 | 0.463% | 50/50 |
| `backtest_6m.parquet` | 2,500 | 11 | 0.440% | 11/11 |
| `backtest_6m_remeasured.parquet` | 2,500 | 11 | 0.440% | 11/11 |
| `backtest_reversal_2y.parquet` | 10,800 | 0 | 0% | — |
| `verification.parquet` | 20 | 0 | 0% | — |

**78 条陈旧记录 100% 落在 `down` 侧**（`backtest_2y` 实测 50/50 down、0 up），
即陈旧透传不是「占个名额」，而是**凭空造出一个看空信号**：塞进跌篮子的
是一个早已失效的价格，却按真实空头计入收益统计。

## 为什么收益数字修前修后**完全相同**

因为陈旧行 100% `no_data`，从来进不了任何收益均值：
`avg_up_return` / `up_excess_vs_market` 在三个产物上修前修后逐位相同
（如 `backtest_2y` 均为 up=-0.5444%、多空价差=-0.6672%）。
污染**只落在分母/覆盖率**上——`no_data` 计数 69→19（2y）、24→7、12→1，
即「涨/跌篮子各 10 名」的契约实际被噪声稀释。

这不是「所以不用修」：**没有 D+1 bar 的成分占着名额，本该进篮子的第 11 名
被挤掉**，且修复前这个稀释在任何报告里都**看不见**（见下节）。

## 决定性证据：修前篮子**超出 20 名契约**（60 日全池实跑）

抽样框：全池 5460 只，最后 60 个交易日，真实 `build_cross_sections` +
`build_predictions`（`/tmp/aplus_stale/repro.py`，输出
`/tmp/aplus_stale/repro/basket.parquet`）。

| 每日篮子成员数 | 修前（天） | 修后（天） |
|---|---|---|
| 20 | 35 | **60** |
| 21 | 3 | 0 |
| 22 | 6 | 0 |
| 23 | **16** | 0 |

**修前 60 天里有 25 天（41.7%）篮子超过 20 名**，最多的一天塞进 23 名。
`TopN=10 / BottomN=10` 的契约被静默破坏：多出来的每一行都是一只
滞后 1~30 天、永远不可能成交的股票。修后 **60 天全部恰好 20 名**。

**适用范围（勿夸大）**：`data/reports/*.parquet` 四个**已落盘**产物里
每日前后名都恰好是 10（`backtest.parquet` 483/483 天、`backtest_2y`
540/540 天、两个 6m 各 125/125 天），**没有**这个溢出。溢出出现在本轮
60 日窗口（2026-07~10，摘牌密集期）。所以这是**该窗口的缺陷形态**，
不是所有历史产物的形态——但它证明了一件事：**当陈旧股足够多时，
20 名契约确实会被撑破**，且撑破时没有任何告警。

这也说明「修前篮子成员 0 条陈旧」（`repro` 输出）**是个误导性结论**——
它统计的是 `build_predictions` 之后的两侧篮子，而污染发生在更早的
`filter_universe`：陈旧股**留在池子里**，占掉了名额把真实股票挤出去，
同时自己也进了篮子。剔除后 2026-07-21 补进的 688192.SH（综合分 2.616412）
正是被陈旧行挤掉的真实候选——这才是「剔除可观测」的实际含义：
不是少了几行噪声，而是**真实的第 20 名重新拿到了自己的位置**。

## 已知接线缺口：实盘路径仍不报剔除数（本轮范围外）

`scripts/run_daily.py:346` 是 `filter_universe(cross, unknown_st=unknown_st)`
——**既不传 `stale` 也不传 `unfillable`**。所以实盘出单时陈旧剔除依然是静默的。
回测侧不受影响（`backtest.evaluate_weights` 自己收集 `_unfillable` 并外露到
`result.attrs["unfillable"]`），`run_daily.py` 不在本轮允许改动的文件清单内，
故只记录、不代改。补法是给它加一个 `stale: dict = {}` 并打印，语义与
`unknown_st` 的告警完全一致。

## 修法：信号日必须有该股自己的那一根 K 线

`last_date` 必须**等于**横截面自身最新的 `last_date`。理由不是「30 天太久」，
而是结构性的：横截面把每只股票在 D 的因子值排在一起比，排序的前提是
**每只股票的因子都算到同一天**。缺 D 那根 bar 的股票，它的 `mom20` 是
D-1（或更早）的，跟别人不是同一个时点——排出来的名次没有意义。

## 为什么基准取横截面自身的最新 `last_date`，而不是 `as_of`

`as_of` **可能不是交易日**：实盘 cron 在周末/节假日照常触发（2026-10-11 就是
周日）。拿 `as_of` 当基准会让「停一天」的股票在周一被判成滞后 2 天，
连续几个长假则整池清空——fail-closed 在这条路径上等于**平台直接不可用**。
取横截面自身的最新 `last_date` 既安全（最大值必然存活）、又与实盘/回测
两条路径天然一致（它们都由面板给出同一列）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aplus.panel import FactorPanel, build_panel
from aplus.predict import build_predictions, filter_universe
from aplus.store import LocalStore

DATES = pd.bdate_range("2024-01-02", periods=90).strftime("%Y-%m-%d").tolist()
DEAD = "600006.SH"          # 倒数第 3 根 K 线后彻底停笔的那只


def _cross(last_dates):
    """构造横截面：本组测试只关心 `code` / `name` / `last_date` 三列。"""
    n = len(last_dates)
    return pd.DataFrame({
        "code": [f"{600000 + i:06d}.SH" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "mom20": np.linspace(0.01, 0.01 + 0.01 * n, n),
        "rev5": 0.0, "vol20": 0.2, "vsur": 1.0, "rsi": 50.0,
        "isST": 0, "listed_days": 200, "volume": 1e6,
        "last_date": list(last_dates),
    })


def _daily(code, n, start="2024-01-02", drift=0.0005):
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    closes = 10.0 * (1 + drift) ** np.arange(n)
    return pd.DataFrame({
        "date": dates, "code": code, "open": closes, "high": closes * 1.01,
        "low": closes * 0.99, "close": closes, "volume": [1e6] * n,
        "amount": [1e7] * n, "tradestatus": [1] * n, "isST": [0] * n,
        "turn": [1.0] * n, "pctChg": [0.0] * n,
    })


def _seed_store(root, n_stocks=6, n_bars=90):
    """n_stocks 只股票；最后一只（`DEAD`）在倒数第 3 根 K 线后彻底停笔。

    `DEAD` 的动量**最高**（drift 最大），所以只要它还在池子里就一定霸占
    涨篮子的第一名——过滤器若失效，本测试立刻失败。这让「剔除」可证伪，
    而不是「反正它也选不上」。
    """
    store = LocalStore(root)
    for i in range(n_stocks):
        code = f"{600000 + i + 1:06d}.SH"
        is_dead = i == n_stocks - 1
        df = _daily(code, n_bars, drift=0.05 if is_dead else 0.02 - i * 0.002)
        if is_dead:
            df = df.iloc[:-3].copy()            # 停笔 → 最后 3 天恒为陈旧
        store.upsert_daily(code, df)
    return store


def _daily_map(store):
    return {p.stem: store.read_daily(p.stem)
            for p in sorted((store.root / "master" / "daily").glob("*.parquet"))}


# ── 1. 阈值边界：lag=0 放行，lag=1 起剔除 ──────────────────────────────────

def test_stock_with_a_bar_on_the_signal_day_is_kept():
    df = _cross([DATES[-1], DATES[-1]])
    assert set(filter_universe(df)["code"]) == {"600000.SH", "600001.SH"}


def test_lag_one_is_already_stale_passthrough():
    """**边界值 1**：只差一根 K 线就已经是陈旧透传。

    这是本组测试的落点。旧判据（滞后 ≤30 自然日）会放行它，而实测里
    78 条滞后 1~30 天的篮子记录**没有一条成交过**。
    """
    lag1 = DATES[-2]          # 上一个交易日 = 滞后 1 个自然日（最小正滞后）
    kept = filter_universe(_cross([DATES[-1], lag1]))
    assert set(kept["code"]) == {"600000.SH"}, \
        f"滞后 1 天的股票还在池子里（last_date={kept['last_date'].tolist()}）"


@pytest.mark.parametrize("lag", [1, 15, 29, 30, 31, 400])
def test_no_positive_lag_survives_the_universe_filter(lag):
    """滞后 1~400 天一律剔除——**包括旧判据放行的 1~30 天窗口**。

    参数化把旧判据 `max_stale_days=30` 的边界（29/30/31）显式钉在测试里：
    门槛两侧各一例，防止日后有人把判据悄悄放宽回去。
    """
    stale = (pd.Timestamp(DATES[-1]) - pd.Timedelta(days=lag)).strftime("%Y-%m-%d")
    kept = filter_universe(_cross([DATES[-1], stale]))
    assert "600001.SH" not in set(kept["code"]), f"滞后 {lag} 天的股票没被剔除"


# ── 2. 陈旧剔除必须**可观测**，不能静默 ────────────────────────────────────

def test_stale_removal_is_counted_not_silent():
    """剔除不可见 = 没修：一次口径变更在报告里完全看不出来。

    与 `unfillable`（买不进）、`unknown_st`（ST 盲区）同一纪律——那两处
    都已经按日计数上报，陈旧剔除却一个数都没有。
    """
    stale: dict = {}
    filter_universe(_cross([DATES[-1], DATES[-45]]), stale=stale)
    assert stale == {"600001.SH": 1}, f"陈旧剔除没有计数：{stale}"


def test_stale_removal_falls_back_into_unfillable_when_no_separate_dict():
    """没单独要 `stale` 分账时，陈旧剔除必须落进 `unfillable`。

    它们**确实买不进**（实测 78/78 条没有 D+1 bar），语义上就是 unfillable；
    而 `unfillable` 是 `aplus/backtest.py` 已经接到
    `result.attrs["unfillable"]` 上、**本轮不得改动**的唯一通道。
    记在这里，才不需要动 backtest.py 就能被读到。
    """
    unfillable: dict = {}
    filter_universe(_cross([DATES[-1], DATES[-45]]), unfillable=unfillable)
    assert unfillable == {"600001.SH": 1}, f"陈旧剔除没有落到 unfillable：{unfillable}"


def test_stale_is_not_double_counted_when_both_dicts_are_given():
    """两个 dict 都给时各记一次，不是把同一个数记两遍。"""
    unfillable: dict = {}
    stale: dict = {}
    filter_universe(_cross([DATES[-1], DATES[-45]]),
                    unfillable=unfillable, stale=stale)
    assert stale == {"600001.SH": 1}
    assert "600001.SH" not in unfillable, \
        "给了独立的 stale 分账就不该再往 unfillable 里记——同一个数被算两遍"


def test_fresh_stock_is_never_counted_as_stale_or_unfillable():
    """反向：新鲜的票不该出现在任何剔除计数里。"""
    stale: dict = {}
    unfillable: dict = {}
    filter_universe(_cross([DATES[-1]] * 3), unfillable=unfillable, stale=stale)
    assert stale == {} and unfillable == {}


def test_removal_reaches_backtest_result_attrs(tmp_path):
    """端到端：陈旧剔除必须出现在 `result.attrs["unfillable"]` 上。

    「剔除必须可观测」的验收标准是**出单口能读到**，不是函数内部记了个数。
    """
    from aplus.backtest import build_cross_sections, evaluate_weights

    store = _seed_store(tmp_path)
    dates = store.read_daily("600001.SH")["date"].tolist()
    xs = build_cross_sections(store, dates[60], dates[-1])
    assert xs["_unfillable"].get(DEAD, 0) > 0, "陈旧票的剔除没有进计数"
    res = evaluate_weights(xs, {"mom20": 1.0}, top_n=10, bottom_n=10)
    assert res.attrs.get("unfillable", 0) > 0, \
        "剔除数没有外露到 result.attrs——这是一次静默的口径变更"


# ── 3. 实盘与回测必须给出**相同**判定 ──────────────────────────────────────

def test_live_and_backtest_agree_on_the_same_stale_stock(tmp_path):
    """同一个陈旧输入，回测横截面（面板）与实盘参照实现（逐股重算）必须同判。

    两条路径分叉过一次（`snapshot.COLUMNS` 缺 `buyable`），那次的教训是：
    对同一批数据给出不同判断，就是每一类事故的共同形状。
    """
    from aplus.snapshot import cross_rows_from_map

    store = _seed_store(tmp_path)
    daily_map = _daily_map(store)
    as_of = str(store.read_daily("600001.SH")["date"].iloc[-1])
    end = (pd.Timestamp(as_of) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    panel = build_panel(store, DATES[40], end, daily_map=daily_map, factors_map={})
    back = panel.cross_section(len(panel.dates) - 1)
    live = cross_rows_from_map(daily_map, as_of=as_of)

    verdict_back = set(filter_universe(back)["code"])
    verdict_live = set(filter_universe(live)["code"])
    assert verdict_back == verdict_live, (
        f"两条路径对同一批数据给出不同判定：回测多留 {sorted(verdict_back - verdict_live)}，"
        f"实盘多留 {sorted(verdict_live - verdict_back)}"
    )
    assert DEAD not in verdict_back and DEAD not in verdict_live, \
        "停笔的票在两条路径上都还在池子里"


def test_non_trading_day_as_of_must_not_empty_the_pool():
    """`as_of` 落在非交易日时**不得**把整池清空。

    实盘 cron 在周末/节假日照常触发（2026-10-11 是周日）。基准若取 `as_of`，
    一个长假就能让所有股票「滞后 3 天」→ 全池被剔 → 平台不可用。
    基准必须取横截面自身的最新 `last_date`。
    """
    kept = filter_universe(_cross([DATES[-1]] * 5), as_of="2024-04-08")
    assert len(kept) == 5, "as_of 落在非交易日时整池被清空——fail-closed 打死了平台"


def test_whole_pool_uniformly_stale_keeps_the_freshest_cohort():
    """全池一起陈旧时（增量更新失败），最新那一批仍可排名。

    基准取横截面最大值 ⇒ 最大值必然存活；池子不会因为「整体陈旧」而变空，
    「数据截至 X、已陈旧 N 天」的告警由 `scripts/run_daily.py` 负责。
    """
    assert len(filter_universe(_cross(["2024-01-05"] * 4))) == 4


def test_stale_rows_cannot_overflow_the_topn_bottomn_contract(tmp_path):
    """**决定性回归**：陈旧行不得撑破 `TopN=10 / BottomN=10` 的 20 名契约。

    60 日全池实跑（修前）发现：25/60 天篮子里塞进了 21~23 名，多出来的
    每一行都是一只滞后 1~30 天、永远不可能成交的股票。契约被静默破坏，
    而任何按「10+10」口径做的统计都在拿一个不成立的分母。

    更糟的方向是：这些陈旧行**占着名额**，把综合分更高的真实股票挤出
    20 名之外。剔除后被挤掉的真实候选重新回到自己的位置
    （实测 2026-07-21 补进 688192.SH，综合分高于被它顶掉的那只）。

    注：已落盘的 `data/reports/*.parquet` 每日前后名都恰好 10，本测试守护的
    是**摘牌密集期**这个更危险的形态；它的价值在于「撑破时没有任何告警」。
    """
    from aplus.backtest import build_cross_sections

    store = _seed_store(tmp_path, n_stocks=12, n_bars=90)
    dates = store.read_daily("600001.SH")["date"].tolist()
    xs = build_cross_sections(store, dates[60], dates[-1])
    checked = 0
    for day, universe in xs.items():
        if day.startswith("_"):          # 元数据键（_dates / _market / …）
            continue
        if universe is None or universe.empty:
            continue
        up, down = build_predictions(universe, top_n=10, bottom_n=10,
                                    weights={"mom20": 1.0})
        n = len(set(up["code"]) | set(down["code"]))
        assert n <= 20, f"{day}：篮子 {n} 名，超出 10+10 契约——陈旧行占了名额"
        checked += 1
    assert checked >= 2, "样本天数不足，测试没打到点"


def test_missing_last_date_column_is_a_no_op_not_a_wipeout():
    """横截面**没有** `last_date` 列时，陈旧判据必须是空操作。

    与 `buyable` 同一纪律：缺列时 fail-closed 等于把整池清空。
    """
    df = _cross([DATES[-1]] * 3).drop(columns=["last_date"])
    assert len(filter_universe(df)) == 3


def test_unparseable_last_date_is_treated_as_stale_not_as_fresh():
    """`last_date` 解析不出日期 → 判陈旧（不可知 ≠ 可买）。"""
    kept = filter_universe(_cross([DATES[-1], None]))
    assert set(kept["code"]) == {"600000.SH"}, "坏日期被当成新鲜数据放行了"


# ── 4. 端到端：陈旧票不得进入任何一侧的篮子 ────────────────────────────────

def test_delisted_stock_never_reaches_either_basket(tmp_path):
    """陈旧票从**涨、跌两边**都排除——塞进跌篮子等于把缺失当成看空信号。

    与「买不进」的纪律完全一致（`tests/test_tradability.py` 第 9 节）：
    两边一减，凭空造出虚假的多空价差。
    """
    from aplus.backtest import build_cross_sections

    store = _seed_store(tmp_path)
    dates = store.read_daily("600001.SH")["date"].tolist()
    xs = build_cross_sections(store, dates[60], dates[-1])
    # DEAD 的数据停在 dates[-4]（末根被砍掉 3 根），从那天起每一天对它都陈旧
    seen = 0
    for day in dates[-3:]:
        universe = xs.get(day)
        if universe is None or universe.empty:
            continue
        seen += 1
        assert DEAD not in set(universe["code"]), f"{day}：陈旧票还在池子里"
        up, down = build_predictions(universe, top_n=10, bottom_n=10,
                                     weights={"mom20": 1.0})
        assert DEAD not in set(up["code"]) and DEAD not in set(down["code"]), \
            f"{day}：陈旧票进了篮子"
    # 窗口是半开区间 [start, end)，`dates[-1]` 不在里面，故只落到 2 天
    assert seen >= 2, "样本天数不足，测试没打到点"
    # 反证：就在停笔前一天，它确实还在池子里且是涨篮子第一名
    before = xs.get(dates[-4])
    assert before is not None and DEAD in set(before["code"]), \
        "对照组失效：停笔前一天它本该在池子里——测试证明不了剔除是否生效"
    up_before, _ = build_predictions(before, top_n=10, bottom_n=10,
                                     weights={"mom20": 1.0})
    assert up_before["code"].iloc[0] == DEAD, \
        "对照组失效：动量最高的它本该霸占涨篮子第一名"


def test_panel_last_date_stays_the_real_last_bar():
    """钉住面板侧的既有修复：新判据不得回退 `last_date` 的真实性。"""
    dm = {
        "600001.SH": _daily("600001.SH", 90),
        "600002.SH": _daily("600002.SH", 60),          # 第 60 根退市
    }
    cross = FactorPanel(dm, None, DATES).cross_section(len(DATES) - 1)
    last = dict(zip(cross["code"], cross["last_date"]))
    assert last["600002.SH"] == DATES[59], \
        f"面板把退市股的 last_date 伪造成了 {last.get('600002.SH')}"


# ── 5. `max_stale_days` 判据本身不得被删掉或放宽 ───────────────────────────

def test_max_stale_days_is_still_enforced_against_as_of():
    """显式 `as_of` 远在数据之后时，`max_stale_days` 仍必须把整池判陈旧。

    新判据（对齐最新 `last_date`）让 `max_stale_days` 在常规路径上变得冗余
    ——本测试钉住它**没有被删掉、也没有被放宽**，否则未来有人可能把它当
    死代码清掉。
    """
    df = _cross(["2026-06-01"] * 3)
    assert len(filter_universe(df, as_of="2026-10-10")) == 0
    assert len(filter_universe(df, as_of="2026-06-20", max_stale_days=30)) == 3


def test_max_stale_days_still_takes_no_effect_of_a_larger_value():
    """把 `max_stale_days` 调大**不能**让陈旧票回来。

    陈旧透传是「时点不可比」，不是「太旧」——放宽天数上限不构成豁免。
    """
    kept = filter_universe(_cross([DATES[-1], DATES[-45]]), max_stale_days=10_000)
    assert set(kept["code"]) == {"600000.SH"}, \
        "放宽 max_stale_days 就能让陈旧票复活——判据被架空了"