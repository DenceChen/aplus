"""自进化赛马：训练段选权重，测试段验收（防数据泄露）。

## 为什么必须切段

「在历史上挑权重」本身就是一种数据泄露——挑出来的权重已经**记住了**历史的
噪声，拿它去预测「未来」等于把答案抄进答卷。上一版 `maybe_promote` 的三道闸
（冷却期 / 样本量 / 改进幅度）全部作用在**同一个窗口**上，管的是换权频率，
管不住过拟合。

这里改成标准的样本外协议：

1. 窗口按时间切成 **训练段（前 60%）** 与 **测试段（后 40%）**，中间留
   **隔离带（embargo）**——动量等 20 日窗口因子与次日收益在边界附近仍有
   重叠，直接相接会互相污染
2. 每组候选在两段**各跑一次**
3. 排名只看**训练段**（这才是「可以拿来调参」的那部分）
4. 晋级要**两条腿都过**：训练段是赢家，且**测试段不劣于现任权重**
   ——训练段赢、测试段垮 = 纯噪声，当场淘汰

这样得到的「最优权重」才是真有可能迁移到未来的，而不是历史噪声的化石。
"""
from __future__ import annotations

import inspect
import math

import numpy as np

from .evolve import DEFAULT_REBALANCE_EVERY, candidate_key, split_weights

TRAIN_RATIO = 0.6
EMBARGO_DAYS = 5

# 变异系数低于此值即判「测不出差别」。见 `_tstat` 里那个 3.1e16 的事故。
DEGENERATE_CV = 1e-12

# 测试段至少要装得下几个调仓周期，候选才允许参与排名。见 `race` 里的排除逻辑。
MIN_TEST_PERIODS = 3

#: 否决条件名单。**这是本模块里唯一一处写下否决点数量/名称的地方。**
#:
#: 2026-10-14 发现 docstring 说「七道闸」、行内注释说「第五道闸：方向污染」、
#: 判决理由说「六条腿全过」——**三个数字互相矛盾，且都跟代码里的实际闸数不符**。
#: 三处都是手写的常量，每加一道闸都要人记得回头改三处，于是从 2026-07 起
#: 就没有一处是对的。这正是台账 55 的同一个形状：
#: **注释里的数字与代码不一致，而且没人会发现。**
#:
#: 修法不是把三个数字改对（下一个闸加进来又会漂），而是
#: **让数字由名单推出来**，并用一条测试把名单与代码里的否决点数量钉死。
#:
#: ## 为什么名单要分两类（这是我第二次在这件事上栽，条目 59）
#:
#: 第一版我手工数闸，得到 9——**无意识地跳过了「测不了」的分支**
#: （`td_ret is None` / `te_t is None` / `cand_n is None` / `cand_n <= 0`）。
#: 理由是「它们不评判好坏，只表示无法评判」。但它们**照样否决**，
#: 照样让 `promote` 变 False。
#:
#: 改完之后我又栽了一次，方向相反：把 **`breadth_t 显著` 写进了否决名单**，
#: 而那一行代码**只拼警告字符串、没有 `return`**——它是**披露**，不是否决。
#: 于是判决对外宣称「12 道闸全过」，其中一道**根本不否决**。
#:
#: 第一版错在**漏数**，第二版错在**多数**——两次都是
#: **「代码里出现的检查」被当成了「代码里生效的否决」**。
#: 唯一能同时防住两者的守卫不是长度计数，而是：
#: **名单里每一项都必须能在源码里指出它的 `return`**，
#: 且警告项必须显式登记在 `GATE_WARNINGS` 里，不能混进 `GATE_NAMES`。
#:
#: - **判优闸**：条件不满足 = 这个策略确实不合格。
#: - **可测性守卫**：某个量算不出来 = 无法证明它合格。
#:   「无法证明」在晋级上与「不合格」同价——**方向相同**。
#:   若哪天改成「缺数据就放行」，那是真正的政策变更，必须显眼地推翻这条。
GATE_NAMES: tuple[str, ...] = (
    "候选即现任",            # 短路：现任不能晋级成自己
    "训练段改进",            # 判优
    "超额硬闸",              # 判优
    "跌腿必须真跌",          # 判优
    "t 显著",                # 判优
    "样本外不垮",            # 判优
    "股票池可比",            # 判优
    "方向未污染",            # 判优
    "跌腿可测",              # 可测性
    "显著性可测",            # 可测性
    "股票池可测",            # 可测性（cand_n / champ_n is None）
    "股票池非空可比",        # 可测性（cand_n <= 0）
)

#: **只披露、不否决**的检查。它们出现在理由里，但**不参与** `N_GATES`。
#:
#: 混进 `GATE_NAMES` 会让判决宣称「N 道闸全过」，而实际少了一道——
#: 这是**对外的错误陈述**，比数字漂移更糟。
GATE_WARNINGS: tuple[str, ...] = (
    "breadth_t 显著",   # `evolve_race.py:645`：只拼 breadth_note，不 return
)

#: 实际生效的否决点数。判决理由里的「N 道闸全过」用它。
N_GATES = len(GATE_NAMES)


def split_window(dates: list, train_ratio: float = TRAIN_RATIO,
                 embargo_days: int = EMBARGO_DAYS) -> tuple[list, list]:
    """时间序列切分：训练段 / （隔离带丢弃）/ 测试段。顺序即时间，不可打乱。"""
    dates = list(dates)
    n = len(dates)
    if n == 0:
        return [], []
    cut = max(1, min(n - 1, int(n * train_ratio)))
    end_train = max(1, cut - embargo_days)
    return dates[:end_train], dates[cut:]


def _rank_key(row: dict) -> float:
    return float(row["train"]["objective"])


def _eval_accepts_execution_params(evaluate) -> bool:
    """evaluate 是否接受 `rebalance_every` / `buffer_multiple` 两个额外位置参数。

    历史测试与旧脚本都是 5 参 `(sections, w, dates, top_n, bottom_n)`，
    新的候选工厂是 7 参。这里**不**做静默兼容，而是在 `race` 里显式报错——
    见 `test_legacy_evaluate_rejects_frequency_candidate_loudly`。
    """
    try:
        sig = inspect.signature(evaluate)
    except (TypeError, ValueError):
        return False
    params = list(sig.parameters.values())
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return True
    positional = [p for p in params
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(positional) >= 7


def race(cross_sections: dict, candidates: list, evaluate, top_n: int = 10,
         bottom_n: int = 10, train_ratio: float = TRAIN_RATIO,
         embargo_days: int = EMBARGO_DAYS) -> dict:
    """候选赛马（evaluate(sections, weights, dates, top_n, bottom_n[, every, buf]) -> stats dict）。

    evaluate 须对**指定日期子集**求值，否则训练/测试切分形同虚设。

    ## 候选是复合的（2026-10-11）

    每个候选可以是纯权重 dict，也可以带 `rebalance_every` / `buffer_multiple`。
    行里原样保留这两个字段，供裁决层区分**同权重的不同执行方式**。

    evaluate 不接受这两个参数时，带频率或缓冲的候选**当场报错**：
    静默忽略等于把月频候选当天频跑，测出来的数字与实盘完全不符，
    而报告上一切正常——这正是「安静的错误输出」。
    """
    dates = cross_sections.get("_dates", [])
    train_dates, test_dates = split_window(dates, train_ratio, embargo_days)
    if not train_dates or not test_dates:
        return {"train_dates": train_dates, "test_dates": test_dates, "table": [],
                "error": "窗口太短，切不出训练/测试两段"}

    wide = _eval_accepts_execution_params(evaluate)
    table = []
    rejected = []
    for c in candidates:
        w, every, buf = split_weights(c)
        if not wide and (every != 1 or buf is not None):
            raise ValueError(
                f"候选 {c!r} 需要 evaluate 支持 rebalance_every / buffer_multiple，"
                f"但它只接受 5 个位置参数。静默忽略会让月频候选按日频执行——"
                f"测出来的数字与实盘不符且看不出来。请改用 7 参 evaluate"
                f"（或 `aplus.evolve.split_weights` 归一后手动调用）。")

        # 测试段至少要装得下 3 个调仓周期，否则这一格**无法测量**。
        # 2 年窗口测试段约 194 日，K=63 只剩 3 个周期，t 检验在 3 个观测上
        # 给不出可信结论。给它排个名次，前端就会显示「第 3 名 t=4.2」——
        # 那是拿 3 个数当证据。留痕而不是静默丢弃。
        periods = len(test_dates) // max(every, 1)
        if every > 1 and periods < MIN_TEST_PERIODS:
            rejected.append({"candidate": c, "rebalance_every": every,
                             "test_periods": periods,
                             "reason": (f"测试段仅 {len(test_dates)} 个交易日，"
                                        f"K={every} 只剩 {periods} 个调仓周期，"
                                        f"不足 {MIN_TEST_PERIODS} 个——"
                                        f"无法测量，不参与排名")})
            continue

        def run(days, _w=w, _every=every, _buf=buf):
            if wide:
                return evaluate(cross_sections, _w, days, top_n, bottom_n, _every, _buf)
            return evaluate(cross_sections, _w, days, top_n, bottom_n)

        row = {"weights": w, "rebalance_every": every, "buffer_multiple": buf,
               "candidate": c,
               "train": run(train_dates), "test": run(test_dates)}
        row["oos_edge"] = row["test"]["objective"] - row["train"]["objective"]
        table.append(row)
    table.sort(key=_rank_key, reverse=True)
    return {"train_dates": train_dates, "test_dates": test_dates,
            "table": table, "rejected": rejected}


def excess_tstat(results, rebalance_every: int = 1) -> tuple[float | None, int]:
    """逐日「涨篮子 − 市场基准」序列的 t 统计量与样本天数。

    ## 为什么必须有它

    「超额 > 0」这个判据在**多重比较**面前形同虚设：候选池里有 14 组时，
    总会有 1 组靠运气过关。实测证据（2026-10-11，entry_lag 修正后）——
    真实候选 1/14 过闸，**30 组随机权重里 1 组也过闸**，且最好的随机
    （+0.0801%）比真赢家（+0.0342%）还高；赢家 t = +0.60、p = 0.549。

    超额为正与「这个超额比随机强」是两件事，后者才对应可下注的 alpha。

    ## `rebalance_every` 为什么必须进这里（2026-10-11 20:40）

    K 日调仓时，一天里「选了谁」在 K 天内**不变**。把 K×n 个交易日当成
    K×n 个**独立观测**，等于把同一个决策数了 K 遍。
    实测指纹（2019-2023 独立验证，962 日）：

    | 纯反转 | 年毛超额 | 逐日 t |
    |---|---|---|
    | K=1 | +77.4% | 6.32 |
    | K=21 | +113.4% | 9.19 |
    | K=63 | +117.9% | **11.17** |

    毛超额几乎不动，t 涨了 77%——因为分母里的样本量是假的。
    按块聚合后，K=63 的独立观测只剩约 15 个，t 会掉回诚实的位置。
    **不修这一条，低频候选会带着虚高的显著性闯过显著性硬闸。**
    """
    daily = _daily_series(results, "up")
    return _tstat(daily, block=rebalance_every)


def _daily_series(results, direction: str, negate: bool = False):
    """逐日的单腿超额序列（t 检验的输入单位）。

    **必须逐日先聚合再检验**：逐笔样本的 t 值会把同一天 10 只股票当成 10 个
    独立观测，样本量虚增 10 倍，标准误被压到 1/√10——那是伪显著。
    交易机会按**天**计，不按笔计。

    **成分不足的日子必须丢掉**：`groupby().mean()` 默认跳过 NaN，
    于是一天里 10 只成分有 9 只没有次日 bar 时，「篮子超额」实际是
    剩下那一只的收益。实测 2026-06-04 就是这样把多空价差顶到 +9.84%
    的（2026-10-11）。`min_basket` 之内的一天衡量的不是篮子，是运气。
    """
    from .verify import MIN_BASKET

    if results is None or len(results) == 0 or "market_pct" not in results.columns:
        return None
    leg = results[results["direction"] == direction]
    if leg.empty:
        return None
    net = leg["actual_pct"]
    if direction == "up" and "cost" in leg.columns:
        net = net - leg["cost"]        # 只有实际建仓的涨腿付成本
    daily = net.groupby(leg["pred_date"]).mean() - leg.groupby(leg["pred_date"])["market_pct"].mean()
    count = net.groupby(leg["pred_date"]).count()
    daily = daily.where(count >= MIN_BASKET).dropna()
    if negate:
        daily = -daily
    return daily if len(daily) else None


def _block_means(daily, block: int):
    """把逐日序列切成**互不重叠的整块**，块内取均值。

    块均值才是「一次调仓决策」的观测单位：块内选股不变，
    选股带来的 alpha 成分在整块内是同一次抽样。

    ## 尾部零头必须丢掉

    不满一整块的尾巴，其块均值建立在更少的样本上，方差天然更大。
    混进方差估计会把整体标准差抬高、t 压低——方向随 block 大小变化，
    不是一个能事后解释掉的常数。宁可少几个观测，也不要一个不知道偏哪边的标准差。

    ## 不能用重叠块

    重叠块（步长 1）会让相邻块共享 K−1 天的数据，同样制造假样本量。
    """
    b = int(block or 1)
    if b <= 1:
        return daily
    s = daily.dropna()
    n = (len(s) // b) * b
    if n < b:
        return s.iloc[0:0]
    return s.to_numpy()[:n].reshape(-1, b).mean(axis=1)


def _tstat(daily, block: int = 1) -> tuple[float | None, int]:
    """t 统计量与**有效观测数**。

    `block=1`（默认）逐位等于分块前的老行为——所有历史结论不受影响，
    有测试钉死。`block=K>1` 时观测单位变成 K 日块的均值，
    理由见 `excess_tstat` 的说明。

    ## 退化序列必须返回 None（2026-10-11 20:45，修复的第二个真实缺陷）

    老代码写的是 `if sd <= 0`，可 **pandas 的 `std(ddof=1)` 对常数序列
    根本不返回精确 0**：实测 `pd.Series([0.02]*30).std(ddof=1)` ≈ 3.5e-18
    （浮点累加误差），于是 `sd <= 0` 永远不成立，
    `t = 0.02 / (3.5e-18/√30)` = **3.1e16** ——一个凭空的巨大 t 值，
    正好撞上显著性硬闸。

    而 `breadth_tstat` 的注释早就写明「标准差为 0 时必须返回 None……
    照常除法就会得到 `inf` 或凭空的巨大 t 值，那正是当初
    「30 组随机权重里也有 1 组能过闸」的同类陷阱」——
    **意图在，护栏没落地**。已有测试套件全绿，也没测到这个形状。

    正确的判据不是 `sd == 0`，而是**变异系数**：只要 `sd` 相对 `|mean|`
    可以忽略，`t` 就纯粹是浮点噪声除出来的，放行它等于放行一个
    「我测不出差别但我声称测出了」。
    """
    if daily is None:
        return None, 0
    s = _block_means(daily, block)
    n = len(s)
    if n < 3:
        return None, n
    mu = float(s.mean())
    sd = float(s.std(ddof=1))
    if not math.isfinite(sd) or sd <= 0 or sd <= abs(mu) * DEGENERATE_CV:
        return None, n
    return float(mu / (sd / math.sqrt(n))), n


def down_excess_tstat(results, rebalance_every: int = 1) -> tuple[float | None, int]:
    """跌腿「回避超额」的 t 值，正方向 = 挑出的确实跑输市场。

    与 `excess_tstat` 同口径、同样逐日聚合、同样按调仓周期分块。
    跌腿不扣交易成本（没有交易发生），但**持仓同样是跨周期持有的**，
    所以自相关一样存在。
    """
    return _tstat(_daily_series(results, "down", negate=True), block=rebalance_every)


def breadth_tstat(results, rebalance_every: int = 1) -> tuple[float | None, int]:
    """**排序广度**（多空价差 `E[r_up] − E[r_down]`）的 t 值。

    ## 为什么需要它

    涨腿超额 `E[r_up] − E[r_mkt]` 会被大盘方向污染：大盘涨 3%，选股毫无本事的
    组合也能拿到 +3% 的「超额」。多空价差把市场项消掉了，只剩下排序把两端
    分开的能力。

    注意它**不能**区分「单因子」与「真本事」——纯单因子排序同样有正的价差。
    它回答的是「排序两端有没有真实区分度，且这个区分度能否与随机区分」。

    ## ⚠️ 已知局限：这个 t 值**部分由门禁不对称生成**

    两条腿面对的门禁**天生不对称**（见 `backtest.py:_up_leg_exit`）：

    | 腿 | 门禁 | 一字跌停（≤−9.9%）时 |
    |---|---|---|
    | **up**（真持仓） | `can_buy` + `can_sell` | **不成交 → 不按 −10% 计入** |
    | **down**（回避清单） | 只判停牌 | **原样保留，−10% 照记** |

    于是在 `E[r_up] − E[r_down]` 里，**极端负值只出现在分母侧**，
    价差被机械地做大。

    **零信号基线实测为 `breadth_t ≈ +4.88`**
    （`tests/test_breadth_gate_asymmetry.py`：两腿收益**逐日完全相同**、
    只把 −9.9% 的尾放在 down 侧）。这**高于本项目的 Bonferroni 临界 3.55**。

    > **所以 `breadth_t` 不能单独当作排序能力的证据。**
    > 真实报告里最好一行是 `+3.77`——**低于零信号基线**。

    它现在位于 `GATE_WARNINGS`（只披露、不否决）而非 `GATE_NAMES`，
    所以它**不参与晋级判决**；但**引用它时必须带这个基线**。
    门禁不对称本身是**设计而非缺陷**（`backtest.py:172` 给了理由：
    对跌腿判 `can_sell` 等于凭空惩罚一笔没做过的交易）。
    要问的是「`breadth_t` 还能不能当证据」，不是「该不该改门禁」。

    ## 标准差为 0 时必须返回 None

    两端收益完全相同（排序根本没分开）时逐日广度恒为 0，均值与标准差都是 0。
    照常除法就会得到 `inf` 或凭空的巨大 t 值——那正是当初「30 组随机权重里
    也有 1 组能过闸」的同类陷阱。无法测量就说无法测量。
    """
    import pandas as pd

    if results is None or len(results) == 0 or "market_pct" not in results.columns:
        return None, 0
    up = _daily_series(results, "up")
    down = _daily_series(results, "down", negate=True)
    if up is None or down is None:
        return None, len(up) if up is not None else 0
    joined = pd.concat([up.rename("up"), down.rename("down")], axis=1).dropna()
    if joined.empty:
        return None, 0
    return _tstat(joined["up"] + joined["down"], block=rebalance_every)


def _t_p(t: float, n_obs: int) -> float:
    """双侧 p 值，仅用于**报告文本**——判定本身走 `t_crit` 阈值。

    `n_obs <= 1` 时无法定义自由度，返回 `nan` 让 `.1e` 打印出 `nan`，
    而不是谎报一个数。
    """
    from .dist import t_sf_two_sided

    return t_sf_two_sided(t, n_obs - 1)


def bonferroni_t(n_comparisons: int, df: int, alpha: float = 0.05) -> float:
    """Bonferroni 校正后的双侧 t 临界值，**按真实 t 分布**（2026-10-14 修）。

    候选越多门槛越高——这正是「14 组里总能捞到 1 组」的解药。
    候选数默认取候选表长度，即**本轮实际做了多少次比较**。

    ## 为什么 `df` 是必填，没有默认值

    原实现是 `NormalDist().inv_cdf(1 - α/(2n))`——**正态近似**，
    签名里压根没有 df，于是无从知道样本量。它只在 df 大时成立：

        df=751  真临界 3.49  正态近似 3.47   差 0.02  ← 无害
        df=10   真临界 5.03  正态近似 3.47   差 1.56  ← 致命
        df=4    真临界 10.23 正态近似 3.47   差 6.75

    事故：`ic_guided_race_7y.json` 的裁决放行了 `t=4.14 @ 11 块` 的候选，
    理由写着「t=4.14 > 临界 3.47（97 次比较 Bonferroni 校正）」。
    真实双侧 p = 2.0e-3，是阈值 5.2e-4 的 **3.9 倍**。

    给 `df` 一个默认值（比如 30「看着够大」）等于把 bug 装进函数签名里：
    调用方下次不传，又会静默用错。**不设默认值，是让每个调用点都必须
    想清楚自己的样本量。**

    ## 为什么这不构成「随 K 放松门槛」

    早先两条测试断言门槛与 K 无关，理由是「低频候选能靠多注册几组低频
    把门槛压低」。**方向反了**：df 变小使 t 尾巴变厚，临界值单调**变大**
    （5.03 > 3.49）。K 变小只会让门槛更**严**。要放松证据标准得动 `alpha`，
    不能靠把 n 悄悄做小。

    ## `df <= 0` 返回 `inf`

    两个观测无法证伪任何东西。此时门槛应当无穷高，而不是退回正态近似
    ——那正是这次事故的成因。
    """
    from .dist import t_crit_two_sided

    n = max(1, int(n_comparisons))
    # `t_crit_two_sided` 收的是**双侧 p 值**，所以这里不再除 2。
    # （旧实现除 2 是因为 `NormalDist.inv_cdf` 吃的是单侧尾概率。）
    return float(t_crit_two_sided(alpha / n, df))


def _expected_false_positives(n_comparisons: int, alpha: float = 0.05) -> float:
    """见 `race_reason.expected_false_positives`——恒等于 alpha。"""
    from .race_reason import expected_false_positives

    return expected_false_positives(n_comparisons, alpha)


def hurdle_note(gross_excess: float, rebalance_every: int | None,
                target: float | None = None,
                default_rebalance_every: int = 1) -> dict:
    """**这个候选够得着目标吗**——把「20% 净值」也算进判决。

    ## 为什么这 N 道闸不够

    `oos_decision` 的 N 道闸全过，只说明「它比现任强」。
    但目标是**年化 20% 净值**，而 K=1 日频的成本上限是 134.2%/年。

    完全可能：某候选毛超额 +30%、t=+5.4、全闸通过、`promote=True`——
    然后它在数学上离 20% 还差 96 个百分点。
    **判决语言（「比现任好」）与目标语言（「到 20%」）不是一件事**，
    而那 N 道闸全都只在说前者。

    晋级它，权重就被改写、日报照它出单、而实测净值远低于目标。

    ## 三条设计取舍

    1. **不改变 `promote`**，只附诊断。方向正确但幅度不够的候选仍是
       下一步的研究对象，只是不该现在就进实盘。
       **代码负责把话说清楚，不负责替人做选择。**
    2. **算术与 `aplus/hurdle` 同源**，两处漂移会被
       `tests/test_hurdle_aware_gate.py::test_the_note_does_not_change...` 抓住。
    3. **缺 `rebalance_every` 时回落到现任默认，绝不当 1**——
       当 1 会把门槛变成 154%，从而把一个够得着的候选误判为不可达。
    """
    from .hurdle import COST_REAL_50K, TARGET_NET, hurdle_rate, rounds_per_year

    k = rebalance_every if rebalance_every else default_rebalance_every
    h = hurdle_rate(TARGET_NET if target is None else target,
                    COST_REAL_50K, rounds_per_year(k))
    gap = h - float(gross_excess)
    reachable = gap <= 0
    if reachable:
        note = (f"K={k} 毛超额 {float(gross_excess):+.1%} ≥ 门槛 {h:.1%}"
                f"——**够得着 20% 净值**（年成本上限 {rounds_per_year(k) * COST_REAL_50K:.1%}）")
    else:
        note = (f"K={k} 毛超额 {float(gross_excess):+.1%}，"
                f"距 20% 净值门槛 {h:.1%} 还差 **{gap:+.1%}**"
                f"——**即使全闸通过也够不着目标**"
                f"（该频率年成本上限 {rounds_per_year(k) * COST_REAL_50K:.1%}）")
    return {"rebalance_every": k, "hurdle": h, "gap": gap,
            "reachable": reachable, "note": note}


def oos_decision(race_result: dict, champion_weights: dict, min_train_gain: float = 0.05,
                 min_test_edge: float = 0.0, min_test_excess: float = 0.0,
                 max_test_down_return: float = 0.0,
                 significance_lookup: int | None = None,
                 unverified_directions: set | None = None,
                 max_universe_gap: float = 0.02) -> dict:
    """晋级裁决：训练段赢 / 超额正 / t 显著 / 样本外不垮 / 股票池可比 / 方向未污染。

    额外闸：
    - 测试段目标分必须不低于现任权重（min_test_edge）
    - 测试段**对市场的超额**必须为正（min_test_excess）——命中率高不能抵消跑输大盘
    - **跌腿必须真的下跌**（`avg_down_return` < max_test_down_return）——
      产品承诺是「10 涨 10 跌」，跌腿是半个产品，而上一条只测涨腿。
      判据用**绝对收益**而不是「跑输大盘」：5 万本金做不了空，
      用户要证的是「这 10 只别买」，不是「它们比大盘跌得多」（2026-10-14 增补）
    - **测试段超额必须统计显著**：t 需超过 Bonferroni 临界值。
      超额为正 ≠ 有 alpha；候选池越大，偶然过关越容易。缺 `excess_t`
      字段的旧记录按**不显著**处理——无法证明就不能放行。
    - **方向污染闸**：因子方向若是在**看过本测试段之后**才决定的，即便前四条全过也不晋级。
    - **股票池可比闸**：候选与现任的可打分股票数差异超 `max_universe_gap` 即否决。

    ## 现任认的是**三元组**（权重 + 频率 + 缓冲），不是光认权重

    `{mom20:1.0} 月频` 与现任 `{mom20:1.0} 日频` 是两个不同的策略，
    净值差着一整个成本量级（实测日频净 −168.9% / 月频 +77.6%）。
    让月频行冒名顶替日频现任，等于让一个从未被授权的执行方式坐现任的席位。

    ## 全表扫描

    见下面循环处的注释：门槛按全表校正，被检验的也必须是全表。
    """
    table = race_result.get("table") or []
    if not table:
        return {"promote": False, "reason": race_result.get("error") or "无候选",
                "examined": 0}

    champ_key = candidate_key(champion_weights)
    champ = next((r for r in table if _row_key(r) == champ_key), None)
    if champ is None:
        return {"promote": False, "best": table[0], "examined": 0,
                "reason": "现任权重不在候选集内，无法比较——拒绝盲跳"}

    # ── 逐个过闸，而不是只看第 1 名（2026-10-11 21:15）────────────────
    # Bonferroni 门槛按 `len(table)` 算——**校正的是全部候选**。
    # 过去只看 `table[0]`，等于「用 84 组的门槛去检验 1 组」：
    # 门槛与被检验的对象不一致，被校正的比较有一大半根本没发生。
    #
    # 复合赛马把这点暴露得很清楚：84 组里训练目标分前 6 名有 4 个是 K=63，
    # 而它们的**毛超额只有 0.11–0.19%**，与 K=1 候选的 0.08–0.15% 基本持平——
    # 排名差距几乎全来自命中率，而命中率在 K=1 与 K=63 之间
    # **根本不是同一个量**（K=1 问「明天涨没涨」，K=63 问「这 63 天涨没涨」）。
    # 第一名是被一个跨频率不可比的量选出来的。
    #
    # 扫描**不放宽**挑选面：门槛一个字没改，只是让被检查的对象
    # 与被 Bonferroni 校正的对象一致。过了第一个就停。
    examined = 0
    first_verdict = None
    for cand in table:
        if _row_key(cand) == champ_key:
            continue                      # 现任不能「晋级成自己」
        verdict = _judge_candidate(cand, champ, champ_key, race_result,
                                   min_train_gain=min_train_gain,
                                   min_test_edge=min_test_edge,
                                   min_test_excess=min_test_excess,
                                   max_test_down_return=max_test_down_return,
                                   significance_lookup=significance_lookup,
                                   unverified_directions=unverified_directions,
                                   max_universe_gap=max_universe_gap)
        examined += 1
        if first_verdict is None:
            first_verdict = verdict       # 全都没过时，报最接近的那个
        if verdict.get("promote"):
            verdict["examined"] = examined
            return verdict

    if first_verdict is None:
        return {"promote": False, "best": champ, "examined": 0,
                "reason": f"训练段最优仍是现任（{_rank_key(champ):.3f}）"}
    first_verdict["examined"] = examined
    if examined > 1:
        first_verdict["reason"] += f"（已逐一检查 {examined} 组候选，无一全过）"
    return first_verdict


def _judge_candidate(best: dict, champ: dict, champ_key: tuple, race_result: dict,
                     min_train_gain: float = 0.05, min_test_edge: float = 0.0,
                     min_test_excess: float = 0.0,
                     max_test_down_return: float = 0.0,
                     significance_lookup: int | None = None,
                     unverified_directions: set | None = None,
                     max_universe_gap: float = 0.02) -> dict:
    """对**单个**候选跑 `N_GATES` 道闸。调用方（`oos_decision`）负责挑选与遍历。

    抽出来的原因只有一个：外层要按训练目标分顺序扫全表，
    而闸门链必须只有一份——两份实现迟早会漂移，
    然后你就再也不知道被检验的到底是哪一份。
    """
    table = race_result.get("table") or []

    train_gain = _rank_key(best) - _rank_key(champ)
    if _row_key(best) == champ_key:
        return {"promote": False, "best": best,
                "reason": f"训练段最优仍是现任（{_rank_key(champ):.3f}）"}

    if train_gain < min_train_gain:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "reason": f"训练段改进 {train_gain:+.3f} < 门槛 {min_train_gain}，不换"}

    # 硬闸：测试段**对市场的超额**必须为正。目标函数含命中率，一个「贴近市场 +
    # 命中率略高」的组合会赢下目标分却毫无 alpha——对「年化 20%」这种绝对收益
    # 目标，它只是低风险的 beta。命中率高不能抵消跑输大盘。
    te_excess = float(best["test"].get("stats", {}).get("up_excess_vs_market", 0.0))
    if te_excess <= min_test_excess:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "test_excess": te_excess,
                "reason": (f"**超额硬闸否决**：候选测试段对市场日均超额 {te_excess:+.2%}"
                           f" ≤ {min_test_excess:+.2%}——赢的是命中率不是 alpha，"
                           f"贴大盘的低风险 beta 换上去对「年化 20%」毫无帮助")}

    # 跌腿闸：**对称**于上面那道，检验产品承诺本身。
    #
    # 产品承诺是每天「10 涨 10 跌」。上面那道闸只测涨腿，此前没有一道看跌腿
    # ——`breadth_tstat` 早就写好了、文档也写明它回答「两端有没有真实区分度」，
    # 却从来没接进闸门（2026-10-14 补记，缺陷 25）。
    #
    # ## 判据为什么是「绝对下跌」而不是「跑输大盘」
    #
    # 5 万本金做不了空，跌腿对用户的价值是**别买这 10 只**，
    # 所以要证的是「这篮子真的会跌」，不是「它比大盘跌得多」。
    # 用 `down_excess_vs_market` 会问错问题：97 组里只有 45/97 跑赢大盘，
    # 约等于随机，按它设闸等于否掉 52/97 的典型候选——否掉多数的闸没有判别力。
    #
    # ## 但它也不是「选谁」的判据，只是「合不合格」的契约检查
    #
    # 实测 97/97 的 `hit_rate_down` 都 > 50%（中位 52.6%），也就是说
    # 跌腿的方向性对**所有**候选都成立，方差极小，**分不出好坏**。
    # 真正有判别力的是 `breadth_t`（两端是否被排序分开）：97 组里
    # 14 组过 3.47 的 Bonferroni 闸。所以这里只当契约闸用，
    # 并把广度一并报进裁决理由，让人看得见「是不是涨腿独撑」。
    #
    # ## 它立刻抓得到一个真东西
    #
    # 现任权重实测 `avg_down_return = +0.126%/日`——它推的「10 只��跌」
    # 平均每天**在涨**。这不是统计口径问题，是产品承诺没兑现。
    td_stats = best["test"].get("stats", {})
    td_ret = td_stats.get("avg_down_return")
    if td_ret is None:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "test_excess": te_excess, "test_down_return": None,
                "reason": ("**跌腿闸否决**：缺 `avg_down_return` 字段，"
                           "无法证明挑出的 10 只真的会跌。"
                           "缺数据按不可证明处理，不按「没问题」处理")}
    td_ret = float(td_ret)
    if td_ret >= max_test_down_return:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "test_excess": te_excess, "test_down_return": td_ret,
                "reason": (f"**跌腿闸否决**：候选测试段跌腿日均收益 {td_ret:+.2%}"
                           f" ≥ {max_test_down_return:+.2%}——它推的「10 跌」"
                           f"并不真的下跌，产品承诺兑现不了")}

    # 显著性硬闸：超额为正 ≠ 有 alpha。候选池越大，偶然过关越容易。
    n_cmp = int(significance_lookup) if significance_lookup else len(table)
    te_t = best["test"].get("stats", {}).get("excess_t")
    te_days = int(best["test"].get("stats", {}).get("excess_days", 0) or 0)
    # 门槛按**真实 t 分布**给，且必须带 df：分块后的观测数就是自由度。
    # 正态近似在这里放过 `t=4.14 @ 11 块` 的假阳性（见 bonferroni_t 文档）。
    t_crit = bonferroni_t(n_cmp, df=te_days - 1)
    if te_t is None:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "test_excess": te_excess, "test_t": None, "t_crit": t_crit,
                "reason": (f"**显著性否决**：测试段超额 {te_excess:+.2%} 为正，但没有 "
                           f"t 检验数据（excess_t 缺失，{te_days} 个交易日）。"
                           f"无法证明它比随机权重强，不放行")}
    if float(te_t) < t_crit:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "test_excess": te_excess, "test_t": float(te_t), "t_crit": t_crit,
                "reason": (f"**显著性否决**：测试段超额 {te_excess:+.2%} 虽为正，但 "
                           f"t = {te_t:+.2f}（{te_days} 个不重叠观测，df={te_days - 1}）"
                           f"低于 {n_cmp} 次比较的 Bonferroni 临界 {t_crit:.2f}"
                           f"（双侧 p = {_t_p(float(te_t), te_days):.1e} ≥ {0.05 / n_cmp:.1e}）"
                           f"——与 0 不可区分。"
                           f"本轮 {n_cmp} 次比较的期望假阳性只有 "
                           f"{_expected_false_positives(n_cmp):.2f} 组，"
                           f"闸门本身没有漏；缺的是样本量或效果，不是证据标准")}

    test_best = best["test"]["objective"]
    test_champ = champ["test"]["objective"]
    test_edge = test_best - test_champ
    breadth_t = best["test"].get("stats", {}).get("breadth_t")
    breadth_note = ""
    if breadth_t is not None and float(breadth_t) < t_crit:
        # 不否决——涨腿的 alpha 是真的，为了次要特征否掉真 alpha 是另一种错。
        # 但必须**说出来**：广度不显著 = 两端没被真正分开，
        # 「10 跌」那一半的证据强度明显弱于「10 涨」。
        breadth_note = (f"｜⚠️ 广度 t={float(breadth_t):+.2f} 未过 "
                        f"{n_cmp} 次 Bonferroni 临界 {t_crit:.2f}——"
                        f"本次显著性由**涨腿独撑**，「10 跌」那半证据偏弱")
    if test_edge < min_test_edge:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "test_edge": test_edge,
                "reason": (f"**样本外否决**：训练段 {train_gain:+.3f} 领先，但测试段 "
                           f"{test_best:.3f} vs 现任 {test_champ:.3f}（{test_edge:+.3f}）"
                           f"——训练赢、测试垮 = 过拟合特征，拒绝晋级")}

    # ── 股票池可比闸 ──
    # 报告正文里那句「各候选的可打分股票数不一致，排名只能作参考」**不是**裁决条件：
    # 不看它的结果是 `{turn20:-1.0}` 在 4021 只里选出的，与现任的 4185 只不可比，
    # 而这少掉的 164 只恰恰是**换手率数据缺失的那批**——不是随机缺失，是系统性缺失。
    #
    # 缺 `n_ranked` 一律按**无法核对**处理，绝不当作「一样多」。
    # 0 只打不了分更是先排除掉：那是「因子全 NaN」而不是「差得少」。
    cand_n = (best["test"].get("stats") or {}).get("n_ranked")
    champ_n = (champ["test"].get("stats") or {}).get("n_ranked")
    if cand_n is None or champ_n is None:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "reason": (f"**股票池无法核对**：候选/现任的可打分股票数"
                           f"（n_ranked={cand_n} / {champ_n}）缺失，"
                           f"无法判断两者是不是在同一批标的上排名——拒绝盲跳")}
    if cand_n <= 0:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "reason": (f"**股票池为空**：候选在测试段一只都打不了分"
                           f"（n_ranked={cand_n}），多半是所依赖的因子列全为 NaN")}
    # 池子**变大**只是候选因子更少、要求更松（`min_count=len(weights)`），
    # 是设计使然、可解释；池子**变小**才是数据缺失造成的系统性窄化。
    # 一刀切会把「把 4 个因子换成 1 个」这种正常候选拦掉，理由还写成
    # 「不可比」——用为别的缺陷造的闸去否决一件正常的事。
    # 2026-10-11 实测：反转族单因子 → 5441 vs 现任 4897（+11%）；
    # turnratio 候选 → 4541 vs 4897（−7.3%，真不可比）。
    if cand_n > champ_n:
        wider = (cand_n - champ_n) / champ_n
        # 记录不否决：供日志与人工交叉核对
        _UNIVERSE_NOTES.append({"cand": cand_n, "champ": champ_n,
                                "wider": round(wider, 4)})
    gap = (champ_n - cand_n) / max(champ_n, 1)
    if cand_n > champ_n:
        gap_wider = (cand_n - champ_n) / champ_n
    if gap > max_universe_gap:
        return {"promote": False, "best": best, "train_gain": train_gain,
                "universe_gap": gap,
                "reason": (f"**股票池不可比**：候选只能在 {cand_n} 只上排名，现任 "
                           f"{champ_n} 只，窄 {gap:.1%} > 门槛 "
                           f"{max_universe_gap:.1%}。被剔掉的标的集中在缺该因子列的"
                           f"那批股票（实测末根 turn 缺 62.0%），不是随机缺失，"
                           f"两者的 Top-N 天然不可比")}

    # ── 方向污染闸（`GATE_NAMES` 的最后一道）──
    # 训练/测试切分能挡住「在测试段上偷看某组权重」，**挡不住**
    # 「看完测试段的结论，再决定要试哪个方向」。2026-10-11 正是这样：
    # 我在 2 年窗口上看到 `{mom20:-1.0}` 优于现任（+0.03% vs −0.60%），
    # 又定位到涨榜把 10 个名额里的 2 个花在全市场仅有的 3 只极端动量股上，
    # 于是准备把反转族加进候选池——**这个方向来自测试段本身**。
    # 再用同一段行情的测试段去验收它，是自欺。
    # Bonferroni 校正也数不了这一条：它数的是「跑了几组」，
    # 数不了「事先已经知道该往哪猜」，后者让有效比较次数高于候选数。
    #
    # 因此这些候选即便四条腿全过，也只判**待前瞻验证**：
    # 必须等一段**没被看过的**交易日才能真正裁决。
    polluted = unverified_directions or set()
    if _weights_hit_direction(best["weights"], polluted):
        return {"promote": False, "best": best, "train_gain": train_gain,
                "test_edge": test_edge, "test_t": float(te_t), "t_crit": t_crit,
                "test_excess": te_excess, "needs_forward": True,
                "reason": (f"**方向待前瞻验证**：四条腿全过（超额 {te_excess:+.2%}、"
                           f"t={te_t:.2f} > 临界 {t_crit:.2f}），但候选方向"
                           f"（{'、'.join(sorted(polluted))}）是在**看过本测试段之后**"
                           f"才决定要试的，训练/测试切分与 Bonferroni 校正都挡不住这一种。"
                           f"不写入 weights.json，等新产生的交易日做前瞻验证")}

    # ── 最后一道诊断：**它够得着 20% 吗** ────────────────────────
    # 上面 N 道闸说的全是「比现任强」。目标说的却是「到 20% 净值」。
    # 两者之间隔着成本：K=1 日频一年的摩擦上限是 134.2%，
    # 所以一个毛超额 +30% 的候选可以全闸通过、promote=True，
    # **然后离目标还差 120 多个百分点。**
    #
    # 放在最后而不是最前：先确认它确实更强，再问够不够到目标。
    # 放最前会把「方向对、幅度不够」这类候选提前毙掉——
    # 它们仍然是下一步的研究对象，只是不该现在就进实盘。
    #
    # te_excess 是**日均**超额，与门槛的年化口径不同，所以乘 DAYS_PER_YEAR。
    # 这个换算是从秒级 bug 里学来的：口径不对时数字看着仍然体面。
    from .hurdle import DAYS_PER_YEAR
    hn = hurdle_note(te_excess * DAYS_PER_YEAR,
                     best.get("rebalance_every"),
                     default_rebalance_every=champ.get("rebalance_every") or 1)

    return {"promote": True, "best": best, "train_gain": train_gain,
            "test_edge": test_edge, "test_t": float(te_t), "t_crit": t_crit,
            "test_down_return": td_ret, "breadth_t": breadth_t,
            "universe_wider": round((cand_n - champ_n) / champ_n, 4) if cand_n > champ_n else None,
            "hurdle": hn,
            # 够不着目标要说在判决里，不能只躺在字段里等人翻。
            # `promote` 仍是 True —— **代码负责把话说清楚，不负责替人做选择**。
            "reason": (f"{N_GATES} 道闸全过：训练 {train_gain:+.3f}、样本外 {test_edge:+.3f}、"
                       f"超额 {te_excess:+.2%}、t={te_t:.2f} > 临界 {t_crit:.2f}"
                       f"（{n_cmp} 次比较 Bonferroni 校正）、"
                       f"跌腿确实下跌 {td_ret:+.2%}/日、方向未被污染"
                       f"{breadth_note}"
                       + ("" if hn["reachable"] else f"。⚠️ {hn['note']}"))}


# 方向标签 → 「命中」的判据：一个候选权重只要在对应因子上**符号与该方向一致**
# 且强度非零，就算命中。标签的语义写在 `candidate_weights` 里。
_UNIVERSE_NOTES: list = []   # 池子变宽的记录（记录不否决，供日志核对）


def _row_key(row: dict) -> tuple:
    """赛马表一行的身份三元组。缺字段的旧行按日频 / 无缓冲处理。"""
    return candidate_key({"weights": row["weights"],
                          "rebalance_every": row.get("rebalance_every",
                                                     DEFAULT_REBALANCE_EVERY),
                          "buffer_multiple": row.get("buffer_multiple")})

DIRECTION_TESTS = {
    # 负动量方向：mom20 权重为负（= 买超跌/反转）。这是 2026-10-11
    # 看完 2 年测试段之后才决定试的方向，故属污染。
    "negative_momentum": lambda w: float(w.get("mom20", 0) or 0) < 0,
    # 低 ivol 方向：ivol 权重为负（书 5.3 节口径做多最低 IVOL）。
    # 2026-10-11 的 ivol 结论曾被前视泄露污染过，也需前瞻复核。
    "low_ivol": lambda w: float(w.get("ivol", 0) or 0) < 0,
}


# ── 方向污染的**解除记录**（2026-10-11 19:42）────────────────────────
# 闸门不是焊死的门。它的目的是**要求独立证据**，不是永久禁止某个方向。
# 一旦在**与发现集时间不重叠**的数据上、过 Bonferroni 门槛地证实了，
# 就该解除——否则就成了因噎废食的僵化，而不是严谨。
#
# 这里的 negative_momentum 是第一条解除记录：
#   发现集 2024-10-01 ~ 2026-10-09 → 产生假设「反转可能更好」
#   验证集 2019-01-02 ~ 2023-12-29 → 从未看过任何一次赛马结果，t=+5.52、广度 t=+3.77
# 两段完全不重叠，是标准的发现集 / 验证集设计。
#
# `low_ivol` **故意不在表里**：它的历史成绩来自前视泄露期，
# 从未在干净数据上重验过。顺手把它一起解除是很自然、也完全错误的动作。
DIRECTION_VERDICTS = {
    "negative_momentum": {
        "discovery_window": ["2024-10-01", "2026-10-09"],
        "validation_window": ["2019-01-02", "2023-12-29"],
        "t": 5.52,
        "breadth_t": 3.77,
        "validation_excess": 0.0041,
        "champion_t": -3.55,
        "n_validation_days": 486,
        "evidence": "scripts/_verify_reversal.py → /tmp/reversal_verify.json",
        "note": ("2019-2023 从未参与过任何一次赛马裁决。训练 723 日 / 测试 486 日，"
                 "股票池 3362 vs 3374 可比。测试段 t(5.52) > 训练段 t(3.52)，"
                 "且现任在同一验证集上 t=−3.55 —— 双向确认，不是单边巧合。"),
    },
}


def evaluate_champion_health(windows) -> dict:
    """现任权重在多个**独立窗口**上的累计健康度。

    ## 为什么需要它

    六道闸只管「什么能进来」，不管「什么该被换掉」。于是 2026-10-11 20:09
    出现一个荒谬局面：现任排第 10（测试超额 −0.57%），没有候选过得了显著性门槛，
    于是裁决「维持现任」——**理由看起来很干净，但现任本身已被反复证伪**。

    拉长时间轴，现任在三个独立窗口上全是负的（合计约 5 年、跨两轮牛熊），
    而反转族在同样两段上是 +0.41%（t=+5.52）与 +0.02%：
    **都不显著，但都不是负的**。

    举证责任必须不对称：
    - 晋级新候选 → 需证明它**显著为正**
    - 维持现任 → 需现任**没有被证伪**

    ## 判定规则

    **两段以上互不重叠**的窗口都显著为负（t ≤ −1.96）即判定证伪。
    重叠窗口只算一段——同一段行情数两次会虚增信心。
    一段显著为负不够（可能是行情特殊），`t` 不显著的负值更不够（那只是没测出东西）。
    """
    rows = [w for w in (windows or []) if w.get("t") is not None]
    if not rows:
        return {"verdict": "unknown", "falsified": False, "negative_windows": 0,
                "windows": [], "note": "没有现任的历史窗口数据，无法判断"}

    # 重叠窗口只算一段：同一段行情数两次会虚增信心。
    # 必须处理**部分**重叠——`[2024-01-01,2024-12-31]` 与 `[2024-06-01,2025-06-01]`
    # 互相都不包含，但明显重叠。做法：按起点排序，与已保留窗口相交则丢弃。
    uniq: list[dict] = []
    for w in sorted(rows, key=lambda x: (x["window"][0], x["window"][1])):
        s0, s1 = w["window"]
        if any(not (s1 < o["window"][0] or s0 > o["window"][1]) for o in uniq):
            continue
        uniq.append(w)

    negative = [w for w in uniq if float(w["t"]) <= -1.96]
    positive = [w for w in uniq if float(w["t"]) >= 1.96]
    if negative and not positive and len(negative) >= 2:
        verdict, falsified = "significantly_negative", True
    elif positive:
        verdict, falsified = "significantly_positive", False
    elif negative:
        verdict, falsified = "significantly_negative", False
    else:
        verdict, falsified = "inconclusive", False

    note = ("现任在 %d 段互不重叠的窗口上全部显著为负（合计 %d 个交易日），"
            "**已被证伪**。继续使用它需要理由，不能拿「没有更好的候选」顶替——"
            "「没有更好的」与「现在这个是好的」不是一回事"
            % (len(negative), sum(int(w.get("n_days") or 0) for w in negative))
            ) if falsified else f"{verdict}：{len(uniq)} 段独立窗口下的汇总判断"
    return {"verdict": verdict, "falsified": falsified,
            "negative_windows": len(negative), "windows": uniq, "note": note}


def register_direction_verdict(name: str, discovery_window, validation_window,
                               t: float, note: str = "") -> dict:
    """登记一条方向解除记录。两个硬条件不过就拒绝登记。

    条件一：验证集与发现集**时间不重叠**。重叠即自证，不是独立验证。
    条件二：验证集上的 t 必须过 1.96，否则是「看起来正」。
    """
    d0, d1 = discovery_window
    v0, v1 = validation_window
    if not (v1 < d0 or v0 > d1):
        raise ValueError(
            f"发现集 [{d0}, {d1}] 与验证集 [{v0}, {v1}] 重叠——"
            f"重叠就不是独立验证，是拿同一段数据自证")
    if not abs(t) >= 1.96:
        raise ValueError(f"t={t} 未过 1.96，不能据此解除方向污染闸")
    rec = {"discovery_window": list(discovery_window),
           "validation_window": list(validation_window),
           "t": float(t), "note": note}
    DIRECTION_VERDICTS[name] = rec
    return rec


def direction_blocked(weights: dict, directions) -> bool:
    """候选权重是否命中「已被独立证据解除」以外的方向污染标签。"""
    if not directions or not weights:
        return False
    for name in directions:
        if name in DIRECTION_VERDICTS:
            continue                       # 已在独立数据上证实，闸门已开
        test = DIRECTION_TESTS.get(name)
        if test is not None and test(weights):
            return True
    return False


def _weights_hit_direction(weights: dict, directions) -> bool:
    """@deprecated 保留旧名；语义已迁到 `direction_blocked`（会读解除记录）。"""
    return direction_blocked(weights, directions)
