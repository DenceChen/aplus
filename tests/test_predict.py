"""预测引擎测试：TDD RED 先行。

设计（PLATFORM.md §2）：
- 股票池过滤：非 ST、上市 > 60 交易日、当日有成交（volume>0）
- 合成分 Top10 = 预测涨，Bottom10 = 预测跌。**短池三段**（2026-10-16 改）：
  池 ≤ 10 → 两腿都给全部（同批双向参考）；池 11~19 → 涨 10 只、跌腿**缩短**且
  **与涨腿不相交**；池 ≥ 20 → 各 10 只不相交。
  🚨 原文此处写的是「不足 10 取全部」，而那只描述了第一段 ——
  中间那一段（部分重叠）**两条都不是**，见下面那条判据测试。
- 每条预测带方向、分数、因子快照（可解释性）
"""
import pandas as pd
import pytest

from aplus.predict import build_predictions, filter_universe


def _cross_section(n_stocks, seed_base=0.0):
    """构造 n 只股票的横截面因子表。"""
    return pd.DataFrame({
        "code": [f"{600000 + i}.SH" for i in range(n_stocks)],
        "name": [f"股票{i}" for i in range(n_stocks)],
        "mom20": [seed_base + i * 0.1 for i in range(n_stocks)],
        "rev5": [0.0] * n_stocks,
        "vol20": [0.2] * n_stocks,
        "vsur": [1.0] * n_stocks,
        "isST": [0] * n_stocks,
        "listed_days": [100] * n_stocks,
        "volume": [1e6] * n_stocks,
    })


def test_filter_universe_drops_st_new_and_no_volume():
    df = _cross_section(5)
    df.loc[0, "isST"] = 1          # ST 剔除
    df.loc[1, "listed_days"] = 30  # 次新剔除
    df.loc[2, "volume"] = 0        # 停牌/无成交剔除
    kept = filter_universe(df)
    assert kept["code"].tolist() == ["600003.SH", "600004.SH"]


def test_filter_universe_drops_stale_data():
    """真实回归（2026-10-10 全池回测实证）：退市股最后一行行情停在摘牌日，
    as_of 切片后仍像「活股」，以冻结动量霸榜且次日必 no_data。
    股票池必须要求最新数据日距快照日 ≤ 30 个自然日（长停牌股本来也不该推荐）。

    **本用例的前提在 2026-10-14 被实测推翻并据此改写**（陈旧透传修复）：
    原断言是「9 月近停牌、10 月正常都保留」——即认为滞后 20 个自然日仍在
    `max_stale_days=30` 的合法窗口内。对 `data/reports/*.parquet` 四个已落盘
    回测产物全量核对（N = 33,760 条篮子记录）：

        | 末根滞后 | 篮子记录数 | 产出收益的 |
        |---|---|---|
        | lag = 0 | 33,682 | 33,682 |
        | lag ∈ [1, 30] | **78** | **0**（全部 no_data，且无一有 D+1 bar）|

    那 78 条「近停牌」的篮子记录**一条都没成交过**，也**一条都没有 D+1 bar**。
    所以「滞后 1~30 天」不是宽限窗口，它只放行噪声、从不放行真实交易：
    横截面排序的前提是所有成分算到同一天，缺信号日那一根 bar 的股票
    带着别处的时点值参与排名，名次没有意义。

    判据因此收紧为 **`last_date` 必须等于横截面自身最新的 `last_date`**；
    `max_stale_days=30` 的判定**未被放宽**（仍在 `as_of` 路径上生效）。
    下方两条断言即固定这个新语义；边界值（滞后 1 天 / 29 / 30 / 31）
    由 `tests/test_stale_passthrough.py` 参数化钉住。"""
    df = _cross_section(3)
    df["last_date"] = ["2026-06-01", "2026-09-20", "2026-10-09"]
    kept = filter_universe(df, as_of="2026-10-10")
    # 6 月退市股、9 月近停牌股都剔除：两者在信号日都没有自己的那一根 bar
    assert kept["code"].tolist() == ["600002.SH"]

    # 反证对照：同一批数据、同一个 as_of，只要三只都在信号日有 bar 就全保留
    ok = _cross_section(3)
    ok["last_date"] = ["2026-10-09"] * 3
    assert filter_universe(ok, as_of="2026-10-10")["code"].tolist() == \
        ["600000.SH", "600001.SH", "600002.SH"]


def test_build_predictions_top10_bottom10():
    df = _cross_section(30)
    up, down = build_predictions(df, top_n=10, bottom_n=10)
    assert len(up) == 10 and len(down) == 10
    # mom 单调递增，而 `mom20` 的实测 IC 为负（−0.0357，逐年 8/8 全负），
    # 所以**动量最高者分数最低** → 600029 进跌榜首位、600000 进涨榜首位。
    # 这条期望值在 2026-10-14 翻转默认权重符号时对调过一次：翻转前
    # `mom20:+1.0`，高分是 600029。方向由实测 IC 定，不由权重约定定。
    assert down["code"].iloc[0] == "600029.SH"
    assert up["code"].iloc[0] == "600000.SH"
    # up 与 down 无交集
    assert not set(up["code"]) & set(down["code"])
    # 每条带方向与分数
    assert (up["direction"] == "up").all()
    assert (down["direction"] == "down").all()
    assert up["score"].iloc[0] == pytest.approx(up["score"].max())


def test_build_predictions_small_universe_returns_all():
    df = _cross_section(6)
    up, down = build_predictions(df, top_n=10, bottom_n=10)
    assert len(up) == 6 and len(down) == 6
    assert set(up["code"]) == set(down["code"])  # 小池子时同批股票双向参考


def _overlap_count(up: pd.DataFrame, down: pd.DataFrame) -> int:
    """两腿的公共代码数。判据的唯一量具，两条守卫共用同一个实现。"""
    return len(set(up["code"]) & set(down["code"]))


def test_the_two_legs_are_either_identical_or_disjoint_but_never_partly_overlapping():
    """🚨 D1 交付形态的第三条缝（2026-10-16）。

    `build_predictions` 里 `up = ranked.head(top_n)` 与 `down = ranked.tail(bottom_n)`
    是**两个独立截断**，两者之间没有任何互斥检查 ⇒ 有第三种形态存在：

    | 池子（分数非 NaN 的票数） | 两腿 | 归属 |
    |---|---|---|
    | ≤ `min(top_n, bottom_n)` | **完全相同** | 既有设计，`test_..._small_universe_returns_all` 逐字钉着 |
    | **`min+1 ~ top+bottom-1`** | **部分重叠** | 🚨 **两条都不是它** |
    | ≥ `top_n + bottom_n` | 完全不相交 | D1 承诺（每天 10 涨 10 跌） |

    中间那一段（`top_n = bottom_n = 10` 时是池 11~19）在池 19 上实测：
    9 只只在涨腿、9 只只在跌腿、**1 只同时在两条腿里且排名不同** ——
    那一只被同时推荐「明天会涨」和「明天会跌」。
    它既不是「小池子时同批股票双向参考」（那种形态两腿完全相同），
    也不是「各 10 只不相交」；而 `save_predictions` 把两腿
    `concat(ignore_index=True)` ⇒ **文件结构无法表达这件事**，产出侧零告警。

    **判据（先于修法写下，不指定修法）**：任意池子下，两腿必须满足
    「完全相同」**或**「完全不相交」二者之一，**不许第三种部分重叠**。
    修法可以是「短池子时两腿强制不相交」，也可以是「短池子时退化成全对称」
    —— 两者都满足，故本条不锁机制。既有那条池 6 的守卫**一个字不许动**。

    ⚠️ **正控在验探测器、不验生产函数**（第 131 层）：合成一个 5 重叠的
    两腿，断言探测器确实看得见它 —— 否则上面那条 `all()` 可能在探测器
    坏掉时恒真（第 87 层：野自助守卫只有零假设臂没有正控臂）。
    「生产函数这边有没有牙」由变异台回答，不由正控回答。
    """
    top_n = bottom_n = 10
    partial = []
    for n in range(0, 41):
        up, down = build_predictions(_cross_section(n),
                                     top_n=top_n, bottom_n=bottom_n)
        ov = _overlap_count(up, down)
        identical = set(up["code"]) == set(down["code"])
        if not (identical or ov == 0):
            partial.append({"pool": n, "len_up": len(up), "len_down": len(down),
                            "overlap": ov})
    assert not partial, (
        f"池 11~19 出现了第三种形态（部分重叠）：{partial}\n"
        f"  ⇒ 同一只票被同时推荐「会涨」与「会跌」，而产物侧零告警"
    )

    # 正控：探测器必须看得见「部分重叠」，否则上面那条可能恒真。
    up = pd.DataFrame({"code": [f"A{i}" for i in range(10)]})
    down = pd.DataFrame({"code": [f"A{i}" for i in range(5, 15)]})
    ov = _overlap_count(up, down)
    assert ov == 5 and not (set(up["code"]) == set(down["code"])), (
        f"探测器坏了：5 重叠的合成两腿被判成 overlap={ov}，"
        f"identical={set(up['code']) == set(down['code'])}"
    )


def test_the_up_leg_always_delivers_exactly_top_n_whenever_the_pool_allows():
    """上面那条判据**分不出**两种修法：短池「强制不相交」与短池「全对称」
    都满足「相同 或 不相交」。⇒ 若不把选它的**理由**钉成可测性质，
    任何人把机制换成全对称，全部守卫照样绿，而**交付物形状被改了**。

    ★ 这里的外部锚不是本仓的散文，是**交付契约本身**：
    D1 = 每天推荐「会涨的 10 只」。所以只要池子够 10 只，
    涨腿就必须**恰好 10 只** —— 短池时该缩短的是**跌腿**（对照侧），
    绝不是涨腿。

    实测两条候选在池 19 处的分野：

    | 机制 | 涨腿 | 跌腿 |
    |---|---|---|
    | 强制不相交（本仓采用） | **10** | 9 |
    | 短池全对称 | **19** | 19 |

    ⇒ 后者把「10 涨」变成了「19 涨」。

    ⚠️ 正控仍只验探测器（第 131 层）：断言夹具里池 19 **确实够 10 只**，
    否则上面那条 `len(up) == top_n` 在池不足时会**假绿**。
    """
    top_n = bottom_n = 10
    shrunk = []
    for n in range(top_n, 41):
        up, _ = build_predictions(_cross_section(n), top_n=top_n, bottom_n=bottom_n)
        if len(up) != top_n:
            shrunk.append({"pool": n, "len_up": len(up)})
    assert not shrunk, (
        f"池 ≥ {top_n} 时涨腿没有恰好 {top_n} 只：{shrunk}\n"
        f"  ⇒ D1 的承诺是「10 涨」，短池时该缩的是跌腿而不是涨腿"
    )
    # 正控：池 19 确实够 10 只（否则上面那条在池不足时恒真）。
    assert len(_cross_section(19)) >= top_n


def test_build_predictions_carries_factor_snapshot():
    df = _cross_section(12)
    up, _ = build_predictions(df, top_n=3, bottom_n=3)
    for col in ("mom20", "rev5", "vol20", "vsur", "name"):
        assert col in up.columns


def test_build_predictions_drops_nan_scores():
    """真实回归：部分股票因子 NaN（脏数据/窗口不足）——排名必须剔除而非错位。"""
    df = _cross_section(12)
    df.loc[df["mom20"].idxmax(), "mom20"] = float("nan")  # 最高分者变 NaN
    up, down = build_predictions(df, top_n=5, bottom_n=5)
    assert not up["score"].isna().any()
    assert not down["score"].isna().any()
