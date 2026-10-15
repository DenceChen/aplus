"""频率门槛——**把「要不要降频」从策略争论变成一道算术题**。

## 为什么它必须是个模块

「日频太贵，降频试试」这句话在策略讨论里被反复重开，
每次重开都带着新的假设、新的换手模型、新的「这次不一样」。

但它其实只取决于两个数：目标净值、年调仓次数 × 每轮成本。
写成函数之后：

- 「日频能不能做」不需要讨论，答案是**算出来的**
- 成本口径一改，所有频率的门槛自动跟着变，不存在过期的结论
- 任何宣称「差一点到 20%」的策略，都能立刻核对它差在成本还是在 alpha

## 这道题对用户目标的直接回答

5 万本金、A 股、**年化 20% 净值**、每天 10 涨 10 跌。

**「每天推荐」和「每天交易」是两件事。**
推荐可以日频——每天都要给名单，这是产品需求。
交易频率是策略变量，而它单独就决定了目标是否可能。

在 5 万本金的真实成本下（日频口径双边约 0.55%，
见 `tests/test_costs.py::test_the_backtest_constant_understates_the_real_cost_at_50k`）：

    日频  244 轮 → 年成本 134.2% → 毛超额须 ≥ 154.2%
    周频 48.8 轮 → 年成本  26.8% → 毛超额须 ≥  46.8%
    月频 12.2 轮 → 年成本   6.7% → 毛超额须 ≥  26.7%
    季频  4.1 轮 → 年成本   2.2% → 毛超额须 ≥  22.2%

日频那一行不是「不划算」，是**毛超额要超过 100%/年**——
A 股上不存在这种东西，而任何声称存在的方法
都得先解释它的实际换手为什么远低于上面的上限。

再降频收益也有限：门槛已经贴近目标本身，
省下的那点成本填不平策略本身的不足。**成本不是瓶颈，信号才是。**

## 口径说明

`cost_per_round` 用**收益率比例**（与 `aplus.backtest.cost_drag` 同口径），
不是金额。这样门槛与本金、仓位数解耦，
而 `STOCK_COST_DEFAULT` 那种平费率常量可以直接代入。

年成本上限 = `rounds_per_year × cost_per_round`，对应**每期全换仓**
（换手率 = 1）。实际换手通常更低，所以这是**上限**，
真实门槛在 `hurdle_rate` 与实测年成本之间。
"""
from __future__ import annotations

DAYS_PER_YEAR = 244

#: 用户的目标：年化 20% 净值。
TARGET_NET = 0.20

#: 本项目在用的几个调仓周期（交易日数），与
#: `scripts/_verify_reversal_freq.py` 的 `FREQS` 一致。
FREQS = (1, 5, 21, 63)

#: 5 万本金下每轮双边成本（收益率比例）。
#: 回测常量 0.003 **低于**实况 0.0055——差额见 `aplus/backtest.py`
#: 的 `STOCK_COST_DEFAULT` 与 tests/test_costs.py 的 characterization test。
#: 这里两个都留：报门槛时用实况口径，审计旧回测时用回测口径。
COST_BACKTEST = 0.003
COST_REAL_50K = 0.0055


def hurdle_rate(target: float = TARGET_NET, cost_per_round: float = COST_REAL_50K,
                rounds_per_year: float = DAYS_PER_YEAR) -> float:
    """达到 ``target`` 净值所需的**年毛超额**。

        净 = 毛超额 − 年成本   ⇒   毛超额 = target + rounds × cost

    ## ★ 上面的恒等式**省略了基准项**（2026-10-15 实测，预注册
    ## `docs/TODO_nav_vs_excess_caliber_2026-10-15.md`）

    真实的账户净值是

        账户净值 = **基准** + 超额 − 摩擦

    而上面那式**只在基准年化 = 0 时成立**。用户的目标字面上是
    「年化 20%」= **账户净值**，本函数给的是**要拿到多少超额**——
    基准若年化 +10%，净值口径的门槛就比这里算出来的**低整整一个基准**。

    ⚠️ **本函数一个字都不改**，因为它有 8 个消费者，
    全仓判决都挂在它上面（`width_capital_feasibility` /
    `factor_width_mde` / `verdict_monthly_hurdle` /
    `backtest_walk_forward_weights` / `verdict_composite` /
    `ic_to_return` / `evolve_race` …）。
    ⇒ 改法必须是**先把判决窗口上的基准收益测出来**，
      再决定是加基准项还是保留超额口径（预注册 §4 G3 三分支）。
    ⇒ 在那之前调低门槛 = **把目标调低**，不是「找到了能赚钱的办法」
      （第 129 层：降 K 是提速不是达标）。

    ⚠️ 但这**不是**说超额口径本身是错的：石川在
    `data/books/cn_factor_lines.txt:565`/`:571` 明确用超额衡量因子有效性，
    而同一本书 `:9877`–`:9878` 把**绝对收益**与**超额收益**并列。
    ⇒ 「因子有效/无效」用超额判（**现有因子判决不必改**），
      「用户的钱会不会涨」用净值判（**这一句本函数答不了**）。
    ⇒ 两者可以并存，把它们合并成一个是本函数真正的缺陷。

    注意参数是**年调仓次数**，不是 K。K 是「每 K 个交易日调仓一次」，
    所以 `rounds = DAYS_PER_YEAR / K`。第一版在这里把两者搞混，
    于是日频那行印出了季频的门槛——**同一个模块里两套频率语义**，
    比没有这个模块更危险。传 K 进来的请先除。

    负成本直接报错：它会让门槛低于目标，**读起来像「调仓越多越好」**。
    这种错误不会让代码崩溃，只让结论整个反号——比崩溃危险得多。
    """
    if cost_per_round < 0:
        raise ValueError(f"每轮成本不得为负，收到 {cost_per_round}")
    if rounds_per_year < 0:
        raise ValueError(f"年调仓次数不得为负，收到 {rounds_per_year}")
    return float(target) + float(rounds_per_year) * float(cost_per_round)


def rounds_per_year(k: int) -> float:
    """K（每 K 个交易日调仓一次）→ 年调仓次数。**全仓唯一换算入口。**"""
    if k <= 0:
        raise ValueError(f"K 必须为正，收到 {k}")
    return DAYS_PER_YEAR / k


def feasible_k(gross_excess: float, cost_per_round: float = COST_REAL_50K,
               target: float = TARGET_NET, freqs=FREQS) -> list[int]:
    """给定毛超额，返回**够得着**目标的调仓周期 K。

    毛超额为负时返回空列表——这不是「没有频率可用」的委婉说法，
    是「这个信号在任何频率下都到不了 20%」的直接结论。
    """
    return [k for k in freqs
            if gross_excess >= hurdle_rate(target, cost_per_round,
                                           rounds_per_year(k))]


def max_affordable_daily(gross_excess: float, cost_per_round: float = COST_REAL_50K,
                         target: float = TARGET_NET, freqs=FREQS) -> int:
    """够得着目标的**最高**调仓频率（日频 = K 1）。

    返回 0 表示一次都不调仓才够得着——注意这通常意味着
    「信号不够强到值得交易」，而不是「那就别交易」。
    """
    ok = feasible_k(gross_excess, cost_per_round, target, freqs)
    return min(ok) if ok else 0


def table(target: float = TARGET_NET, freqs=FREQS,
          cost_per_round: float = COST_REAL_50K) -> list[dict]:
    """给人看的那张表。**报告与测试共用同一份算术**，避免两处漂移。"""
    rows = []
    for k in freqs:
        n = rounds_per_year(k)
        rows.append({"k": k, "rounds_per_year": round(n, 1),
                     "annual_cost": round(n * cost_per_round, 4),
                     "gross_excess_needed":
                         round(hurdle_rate(target, cost_per_round, n), 4)})
    return rows


# 频率档位，**阈值严格递减**。
# ★ **这个顺序是判据，不是排版**（CLAUDE.md「全仓判决分支审计」通则）：
#   `n = DAYS_PER_YEAR / k` 是**递减**于 K 的量，而这里的判定是**首命中**，
#   所以阈值必须按降序排列，「年化次数越多 ⇒ 标签越靠前」才成立。
#   阈值一旦换序，K=1（n=365）会先命中「季频」——日频被标成季频。
#   守卫见 `tests/test_hurdle_rate.py::test_the_frequency_tiers_are_in_descending_threshold_order`。
FREQ_TIERS: tuple[tuple[str, float], ...] = (
    ("日频", 200.0),
    ("周频", 30.0),
    ("双周", 15.0),
    ("月频", 6.0),
    ("季频", 2.5),
)


def freq_label(k: int) -> str:
    """把 K（每年调仓次数）标成中文频率名。

    ★ 标签**必须由 `DAYS_PER_YEAR / k` 推出**，不能按 K 值查表。
    第一版按 `k` 查 `{1: 日频, 5: 周频…}`，于是 K=63（季频）那一行
    标签写着「季频」、数据却是日频的 —— **行标签与行数据两套口径，
    比没有这张表更坏**。
    """
    n = DAYS_PER_YEAR / k
    for name, thr in FREQ_TIERS:
        if n >= thr:
            return name
    return f"K={k}"


if __name__ == "__main__":
    print(f"目标 {TARGET_NET:.0%} 净值 · 每轮双边 {COST_REAL_50K:.2%}"
          f"（年成本上限按每期全换仓算）\n")
    print(f"{'K':>4}{'周期':<8}{'年调仓':>8}{'年成本':>10}{'需毛超额':>12}")
    print("─" * 46)
    for r in table():
        print(f"{r['k']:>4}{freq_label(r['k']):<8}"
              f"{r['rounds_per_year']:>8.1f}{r['annual_cost']:>10.1%}"
              f"{r['gross_excess_needed']:>12.1%}")
