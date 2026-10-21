"""验证闭环：T+1 实际涨跌 vs 预测判定 + 滚动统计。

这是平台迭代的核心抓手（PLATFORM.md §3）：所有判定结果永久留存，命中率是
调参、加因子、改池子规则的唯一依据。平局（恰好 0.00%）单列 flat 不计命中。
"""
import numpy as np
import pandas as pd

from .adjust import hfq_close

# 一个篮子至少要有几只成分才允许进入日度聚合与 t 检验。
#
# `.mean()` 默认跳过 NaN，所以「篮子平均收益」可以只由一只股票构成，
# 而调用方看不出来。实测 2026-06-04 的跌篮子 10 只里 9 只没有次日 bar
# （退市/长期停牌），多空价差被虚报到 +9.84%、t=7.75——
# **看起来比任何真 alpha 都显著**。
#
# 取 3：低于此值时那一天衡量的根本不是「一个篮子」，而是个别标的的运气。
MIN_BASKET = 3

# 一条 `no_data` 判定被标成「永久不可判定」时写进产物的标记值。
# 见 `classify_no_data`。
UNJUDGEABLE_HALT = "halt"
# 该标记在判定档里的**列名**。
UNJUDGEABLE_REASON = "unjudgeable_reason"

# 判定档里记「这一行是用哪套**信号**产生的」的那一列的列名。
#
# 🚨 **与 `aplus.reports.WEIGHTS_HASH_COLUMN` 不是一回事**：过滤只改池、
# 不改权重 ⇒ 权重指纹**逐位相同** ⇒ 它**区分不了**信号变了。
#
# ⚠️ 这里**独立定义**而不是 `from .reports import ...`：
# `aplus/reports.py` **import 本模块**（`from .verify import UNJUDGEABLE_REASON`）
# ⇒ 反向 import 会成环（第 189 层：两份副本之间没有守卫 ⇒
# 同一个列名在两处各写一遍，改一处就静默分叉）。
# ⇒ 这里与 `aplus/reports.py` 的同名常量**逐字相同**，
# 由 `tests/test_production_config_2026_10_21.py::test_the_signal_id_column_name_is_identical_in_both_modules`
# 钉住（两处**必须**相等）。
SIGNAL_ID_COLUMN = "signal_id"


def classify_no_data(stock_max: str | None, store_max: str | None,
                     required_entry: str, required_exit: str) -> str | None:
    """一条 `no_data` 是**永久**不可判定，还是**暂时**拿不到 bar？

    🚨 预注册 `docs/PREREG_NO_DATA_REJUDGE_2026-10-16.md` §3 的 C4/C5。
    两者是一对**反向**判据，缺一不可：只实现「永久」会让暂时性的那些被一次性丢掉，
    只实现「暂时」会让停牌日**每天重判一次、永不停歇**。

    ## 判据只看**日期**，不看文件 mtime（C7）

    mtime 不是真相：拷贝、备份、重写但内容不变都会改它。
    用它当锚是过严守卫的头号成因——症状与「守对了」一模一样（第 112 层）。

    ## 为什么 `store_max` 是必需入参

    「这只票没有 bar」有两种完全不同的原因，**后果相反**：

    | 形状 | `stock_max` | `store_max` | 判决 |
    |---|---|---|---|
    | 库还没更新（暂时） | < `required_entry` | **< `required_exit`** | `None` ⇒ 下轮重判 |
    | 这只票停牌（永久） | < `required_entry` | **>= `required_exit`** | `"halt"` ⇒ 不再阻塞该日 |

    实测 600825.SH：daily 末日期 2026-10-12，而同日两只对照票（600241.SH /
    601398.SH）末日期 2026-10-15，daily 抽样 40 只中 32 只 = 2026-10-15
    ⇒ **库是当前的，是这只票没有** ⇒ 永久。

    ⚠️ `stock_max >= required_entry` 时该行本就判得出（会有 `exit_date`），
    这里仍返回 `None` 而不是 `UNJUDGEABLE_HALT` —— 有窗口就是可判定，不是「永久」。
    """
    if not stock_max or not store_max:
        return None
    if str(stock_max) >= str(required_entry):
        return None                      # 有买价 ⇒ 该行判得出，不存在「永久」
    if str(store_max) < str(required_exit):
        return None                      # 库还没到退出日 ⇒ 暂时，不是停牌
    return UNJUDGEABLE_HALT


def mark_unjudgeable(results: pd.DataFrame, stock_max: dict,
                     store_max: str | None, required_entry: str,
                     required_exit: str) -> pd.Series:
    """给判定结果打上「永久不可判定」标记（预注册 §3 的 C4/C5）。

    纯函数：日期与结果都在入参里，**不碰库**。这样它能被双向夹具直接夹住，
    而不必起一个 5460 份 parquet 的库（面板载入是 GB 级 / 10 分钟量级）。

    ⚠️ **只有 `no_data` 行可能被标记**：`hit` / `miss` / `flat` 一律 `None`。
    一个 `hit` 行带上「永久不可判定」会让下游把它当成「没量到」，
    而它明明量到了 —— 这是比漏标记更坏的方向（第 10 层：错在乐观方向）。
    """
    out = []
    for code, res in zip(results["code"], results["result"]):
        if res != "no_data":
            out.append(None)
            continue
        out.append(classify_no_data(stock_max.get(code), store_max,
                                    required_entry, required_exit))
    return pd.Series(out, index=results.index, dtype="object")


def judge(prediction, actual_pct) -> str:
    """单条判定：方向对且非平 = hit，反向 = miss，恰好 0 = flat，无行情 = no_data。"""
    if pd.isna(actual_pct):
        return "no_data"
    if actual_pct == 0:
        return "flat"
    is_up_prediction = prediction["direction"] == "up"
    return "hit" if (actual_pct > 0) == is_up_prediction else "miss"


def last_two_bars(store, code: str, factors_map: dict | None = None,
                  after_date: str | None = None,
                  _allow_unanchored_for_test: bool = False):
    """实盘判定用：**信号日之后**那两根 K 线的后复权收盘价（买价 / 卖价）。

    ## 为什么必须锚定 `after_date`（2026-10-14 修复）

    原实现返回 `closes[-1] / closes[-2]`——**库里最后两根**，
    与「预测那天之后第几根」毫无关系。后果：

        信号 D 收盘出 → 实盘量的是 D+1 收盘 / D 收盘
        回测量的是  D+2 收盘 / D+1 收盘（`backtest._next_bars`, entry_lag=1）

    两者**差一根 K 线**，于是网页上给用户看的命中率，和回测量化的，
    不是同一个量。实测同一批推荐，换一天判定就从 20.0% 变成 70.6%（12/17）——
    一个能被一天之差翻转的数字，不能用来决定加不加仓。

    `backtest.py:78-91` 早就把这个口径命名为 bug（「窗口起点落在信息集之内」）
    并注明 `entry_lag=0` 任何判定都不得使用。**但这里正是那个活着的
    `entry_lag=0`。** 修好的那份和还在跑的那份并存在一个仓库里。

    所以 `after_date` 是**必填**，且**不给默认值**——原 bug 的成因正是
    API 允许省略锚点，于是调用方省略了它。

    ## 为什么必须复权

    除权除息当天不复权价直接跳空，用它算就是凭空记一笔从未发生的亏损：
    实测 `002594.SZ` 2025-07-29 不复权 −66.94%、交易所口径 +0.37%。

    买不到卖价（信号日就是最新一天）返回 None——不成交，
    绝不退化成「拿最后一根凑」，那量的是一笔无法执行的交易。

    ## 为什么必须报出 `entry_date` / `exit_date`

    成交窗口是**信号日之后第一根买、第二根卖**，而信号日不必是交易日。
    实测 2026-10-09（周五）/ 10-10（周六）/ 10-11（周日）三次出名单，
    周末主库没有 bar，三次的 `searchsorted` **落到同一根 10-12 买入、
    10-13 卖出**——三天是同一个成交窗口。

    不报出这两个日期，下游就无从知道这件事，`pred_date` 会被当成观测日，
    复利把同一天的收益连乘三次。实测三个篮子真实加权日收益 +0.67%，
    连乘后是 −1.79%，再年化报出 `−78.06%`——**符号整个翻过来**。
    """
    import numpy as np

    if after_date is None and not _allow_unanchored_for_test:
        raise TypeError(
            "last_two_bars 必须给 after_date（信号日）。不锚定就是量「库里最后两根」，"
            "与回测的 entry_lag=1 差一根 K 线——2026-10-14 实测同一个数字能因此"
            "从 20.0% 变成 70.6%。")
    df = store.read_daily(code)
    if df is None or len(df) < 2:
        return None
    events = (factors_map or {}).get(code) if factors_map else store.read_factor(code)
    sorted_df = df.sort_values("date", kind="stable")
    closes = hfq_close(sorted_df, events).to_numpy(float)
    if after_date is None:                      # 仅供回归测试对比旧行为
        return {"code": code, "close": float(closes[-1]),
                "prev_close": float(closes[-2]),
                "adjusted": bool(events is not None and len(events) > 0)}

    dates = sorted_df["date"].to_numpy(dtype="U10")
    # 与 `backtest._next_bars` 同一行代码：首根 > 信号日
    entry = int(np.searchsorted(dates, after_date, side="right"))
    exit_i = entry + 1
    if entry >= len(closes) or exit_i >= len(closes):
        return None                             # 卖价不存在 → 不成交
    return {"code": code, "close": float(closes[exit_i]),
            "prev_close": float(closes[entry]),
            "entry_date": str(dates[entry]),
            "exit_date": str(dates[exit_i]),
            "adjusted": bool(events is not None and len(events) > 0)}


def verify_predictions(predictions: pd.DataFrame, next_day_bars: pd.DataFrame,
                       require_cost: bool = False,
                       default_cost: float | None = None) -> pd.DataFrame:
    """批量验证：next_day_bars 需含 code/close/prev_close（买价 / 卖价）。

    ## `signal_id`：**只透传，不造标签**（2026-10-21 修复）

    底稿 `verification.parquet` 是**追加式**的 ⇒ 可买性过滤落地后，
    同一本底稿里会**同时**装着两个信号产生的行
    ⇒ 「这个打分器有没有排序力」必须**只问当前信号的行**
    ⇒ 每一行必须记住**自己是用哪套信号产生的**。

    ### 这一列的**唯一真相源**：该 `pred_date` 的**预测文件自带的 `signal_id` 列**

    `aplus/reports.py::save_predictions` 在**出单当天**把它写进预测文件
    ⇒ 本函数**只透传**：文件有 ⇒ 验证行有；文件没有 ⇒ 验证行没有。

    🚨🚨 **本函数不许造标签**（2026-10-21 修掉的缺陷，根因报告见
    `docs/TODO_VERIFY_RESIGNAL_ID_2026-10-21.md`）：

    原实现有一个 `signal_id` **入参**，在「整批没有该列」时用它补上；
    而调用点传的是 `current_signal_id(reports_dir)`（**当前**权重的 id）⇒
    早于该列落地时写下的**旧名单**（文件里根本没有这一列）
    被盖上了**重判当天**的 id
    ⇒ 「这份名单由该信号产生」这个**证据**变成了**编造**
    ⇒ D1 的独立成交窗口数（W）被高估一倍（2 而真值是 1）。

    ⇒ **参数已删除**（不是改成「只校验不补」）：
    留着它就留着一条「调用方声明一个标签」的通道
    ⇒ 下一个调用点（或将来的人）还会传 ⇒ 缺陷**原样复发**
    （两份副本之间没有守卫 ⇒ 改一处就静默分叉）。
    ⇒ 「只校验不补」也不合格：那一支在「整批没有该列」时**恒真**
    ⇒ 它在**恰恰出缺陷的那一格**没有判别力。

    ⚠️ **既有行不许回填**（追加式纪律）：历史行**没有** `signal_id`
    ⇒ 下游读到它们时**必须显式说「这些行不属于任何已知信号」**
    （`aplus/signal_scope.py` 的归属状态：`MISSING_SIGNAL_ID_COLUMN` /
    `NO_MATCHING_SIGNAL` ⇒ 不可判定，**不是**「没有排序力」；
    老底稿没有该列时它退到 `weights_hash` **逐行**判归属
    ⇒ `ATTRIBUTED_BY_WEIGHTS_HASH`）。

    ⇒ **没有正确来源的行就该「不可归属」**（预注册
    `docs/PREREG_FIX_RESIGNAL_ID_2026-10-21.md` R3）：
    不许事后手工贴一个从 git 反推出来的 id（那是**编造标签**）。

    ## 输出契约：`actual_pct` 是**毛**收益，成本另立 `cost` 列

    这不是命名洁癖。回测侧 `evaluate_weights` → `rolling_stats` 一直按
    「`actual_pct` 为毛、`cost` 独立成列、`rolling_stats` 里扣一次」消费
    （见 `tests/test_cost_reporting.py`：`gross_excess = net_excess + cost`）。
    实盘链路若在这里就把成本乘进 `actual_pct`，同一个字段在两条链路上
    含义相反，而 `rolling_stats` 会**再扣一次** —— 净收益被扣两遍，
    实盘报告会系统性低报约 0.6 个百分点往返，且不会有任何报错。

    所以 `verify_predictions` 只做**测量**（毛涨跌 + 成本列），
    扣减交给唯一的下游 `rolling_stats`。口径集中在一处。

    ## 成本必须显式（2026-10-14 修复）

    原实现里 `if "cost" in up.columns` 落空就**静默按毛收益算**，
    `rolling_stats` 于是写出 `avg_cost_drag: 0.0` ——
    那个数读起来像「成本不花钱」，实际含义是「成本没测」。

    实测 `pred_*.parquet` 确实没有 cost 列，而判定结果落进
    `verification.parquet` 时也没带 `cost`，于是 `rolling_stats`
    读到的 `up.columns` 里**永远没有 cost**——这条静默路径是**常态**。
    实测那个毛 +0.190% 的「超额」，按回测口径 0.003 计应是净 +0.110%。

    现在 `require_cost=True` 时「既无实测列、也未申报 `default_cost`」
    直接抛错。**实测优先于申报**：有 `cost` 列就用实测的。

    ## 成本只算涨腿

    跌腿是**回避清单**，不是一笔做过的仓位——扣它的成本等于凭空
    惩罚一个不存在的交易，`down_excess` 会被系统性高估。
    `rolling_stats` 一直是这个口径（`avg_down` 直接用 `actual_pct`），
    这里必须与它一致，否则两处算出来的数对不上。
    """
    cols = ["code", "close", "prev_close"]
    has_cost = "cost" in next_day_bars.columns
    if has_cost:
        cols.append("cost")
    elif default_cost is None and require_cost:
        raise KeyError(
            "pred/next_day_bars 缺 cost 列且未申报 default_cost："
            "实盘判定必须计入交易成本，否则 stats 里的 avg_cost_drag=0.0 "
            "会被读成「成本不花钱」，实际是「成本没测」。"
            "请传入实测成本列，或显式传 default_cost 说明口径。")
    bars = next_day_bars[cols].copy()
    bars["gross_pct"] = bars["close"] / bars["prev_close"] - 1
    out = predictions.merge(
        bars[["code", "gross_pct"]], on="code", how="left")
    # 成本落到每一行：涨腿取实测/申报值，跌腿恒为 0（没做过这笔交易）。
    if has_cost:
        out["cost"] = out["code"].map(bars.set_index("code")["cost"]).fillna(0.0)
    elif default_cost is not None:
        out["cost"] = default_cost
    else:
        # 两者皆无且未 `require_cost`：老调用方（纯排序研究）确实不建模成本。
        out["cost"] = 0.0
    out.loc[out["direction"] != "up", "cost"] = 0.0
    # 判定用毛值：成本是摩擦，不是方向信息。扣掉成本再判会把
    # 「恰好平盘」判成 miss，等于用手续费凭空造出方向判断。
    out["actual_pct"] = out["gross_pct"]
    out["result"] = out.apply(lambda row: judge(row, row["gross_pct"]), axis=1)
    # ── `signal_id`：**只透传，不造标签**（2026-10-21 修复）────────────
    # 预测文件自带的 `signal_id` 列经上面的 merge **原样**带进验证行
    # （merge 以 `predictions` 为左表 ⇒ 左表的列全部保留）。
    # 🚨 **这里不再有任何「补列」的逻辑** ⇒ 调用方**无法**声明一个标签
    # ⇒ 「这份名单由该信号产生」的证据**只可能**来自那份预测文件。
    # ⚠️ `SIGNAL_ID_COLUMN` 这个常量**保留**：它是跨模块的**列名契约**
    #   （`aplus/reports.py` 与本模块各写一遍 ⇒ 靠守卫钉住，
    #   见 `tests/test_production_config_2026_10_21.py`）。
    return out


def rolling_stats(history: pd.DataFrame) -> dict:
    """滚动统计：分方向命中率、涨/跌篮子的净收益与超额，以及排序的**广度/偏度**。

    ## 为什么要新出三个数（2026-10-11）

    平台每天输出 10 涨 + 10 跌，但此前**只统计涨腿**——「我们能不能看空」
    这件事从未被测量过。

    跌腿没有直接收益（不做空），它的价值是**回避**：`市场 − 该股收益`，
    并且它是涨腿的**天然对照组**：两端来自同一次排序。

    ## 新出的两个量，各自回答不同的问题

        广度 breadth = up_excess + down_excess = E[r_up] − E[r_down]   ← 多空价差
        偏度 tilt   = up_excess − down_excess

    **广度对大盘方向免疫。** 涨腿超额 `E[r_up] − E[r_mkt]` 在大盘上涨时会自动
    变大——选股毫无本事、只是踩在 beta 上，也能拿到一个漂亮的正数。多空价差把
    市场项消掉了，衡量的才是排序本身把两端分开的能力。这也是它必须做 t 检验的
    原因（`aplus.evolve_race.breadth_tstat`）：`+0.03%` 这种数字在样本外早已
    被证明与随机权重不可区分。

    **偏度用于披露市场暴露。** `E[r_up] + E[r_down] − 2 E[r_mkt] ≠ 0` 说明排序
    两端**合起来**就偏离了市场，即名单整体含有 beta / 市值等系统性暴露。
    纯 beta 排序正是如此——这不是 bug，是用户按名单建仓时必须知情的风险。

    注意：这两个数**不能**区分「单因子」与「真本事」——纯单因子排序同样有正的
    多空价差。
    """
    zero = {
        "hit_rate_up": 0.0,
        "hit_rate_down": 0.0,
        "overall_hit_rate": 0.0,
        "avg_up_return": 0.0,
        "avg_down_return": 0.0,
        "up_excess_vs_market": 0.0,
        "down_excess_vs_market": 0.0,
        "excess_breadth": 0.0,
        "excess_tilt": 0.0,
        "up_basket_n": 0,
        "down_basket_n": 0,
        "judged": 0,
        "avg_cost_drag": 0.0,
        "gross_up_excess": 0.0,
        "cost_share": 0.0,
    }
    if history is None or history.empty or "direction" not in history.columns:
        return zero

    def _rate(sub: pd.DataFrame) -> float:
        judged = sub["result"].isin(["hit", "miss"]).sum()
        return float((sub["result"] == "hit").sum() / judged) if judged else 0.0

    up = history[history["direction"] == "up"]
    down = history[history["direction"] == "down"]
    # 篮子成分数：`.mean()` 默认跳过 NaN，所以「篮子平均收益」可能只由**一只**
    # 股票构成——而调用方完全看不出来。2026-10-11 实测：某日跌篮子 10 只里
    # 9 只没有次日 bar（退市/长期停牌），`.mean()` 静默按剩下的 1 只计算，
    # 再被日度汇总平均，多空价差虚报到 +9.84%。
    #
    # 这里把有效成分数一并报出；逐日聚合与 t 检验用 `MIN_BASKET` 丢弃
    # 成分不足的日子——**宁可少几天样本，不可用一只股票代表一个篮子**。
    up_net = ((up["actual_pct"] - up["cost"]) if "cost" in up.columns
              else up["actual_pct"])
    n_up = int(up_net.notna().sum())
    n_down = int(down["actual_pct"].notna().sum()) if len(down) else 0
    avg_up = float(up_net.mean()) if n_up else 0.0
    # 跌腿**不扣成本**：它是我们没做过的仓位（回避清单），扣了就等于凭空
    # 惩罚一个不存在的交易，指标会系统性偏保守。
    avg_down = float(down["actual_pct"].mean()) if n_down else 0.0
    market = float(history["market_pct"].mean()) if "market_pct" in history and len(history) else 0.0
    up_excess = avg_up - market
    down_excess = market - avg_down          # 正 = 回避有效（挑出的确实跑输市场）

    # ── ★ 独立成交窗口数：样本量的真正分母（2026-10-14）────────────
    # 周末/节假日出单会让**多个信号日落到同一个成交窗口**：
    #     2026-10-09 ┐
    #     2026-10-10 ├─ 全部量在 2026-10-12→2026-10-13 这一笔
    #     2026-10-11 ┘
    # 这不是 bug（周末没有交易，名单只能等到下一个交易日），
    # 但它让「累计 80 条判定」实际只有 **2 次独立交易**。
    # 同一笔交易被数三遍 ⇒ 命中率的分母虚增 3 倍，
    # 而三份记录同时对、同时错 ⇒ 命中率数字不会因重复而更可靠。
    #
    # ⇒ 报告必须同时给两个数：条目数（信息量）与独立窗口数（样本量）。
    # 只报条目数，就是把重复当成证据。
    # ⚠️ 按**窗口本身**去重，不是按 pred_date ——
    #   后者会把「3 个信号日 → 1 个窗口」仍然算成 3，正是要防的那个错。
    n_windows = None
    if {"entry_date", "exit_date"} <= set(history.columns):
        _w = history.dropna(subset=["entry_date", "exit_date"])[["entry_date", "exit_date"]]
        n_windows = int(len(_w.drop_duplicates()))
    elif "entry_date" in history.columns:
        _w = history.dropna(subset=["entry_date"])[["entry_date"]]
        n_windows = int(len(_w.drop_duplicates()))

    # ── 成本必须被**报出来**，不只是被扣掉 ──────────────────────────
    # 净超额已经扣过成本（上面 avg_up 用的是 actual_pct − cost），但报告里
    # 看不出扣了多少。于是这两种策略在表里一模一样：
    #     毛 +0.190% − 成本 0.080% = 净 +0.110%
    #     毛 +0.111% − 成本 0.001% = 净 +0.110%
    # 它们不是同一个策略。5 万本金 / 10 只持仓时单只 5000 元，
    # 日频换手 27.5% 的年成本量级在 20% 上下——**和 20% 的年化目标同量级**，
    # 所以「成本吃掉毛超额的几成」是决定策略能不能活的数，不是脚注。
    cost_drag_avg = float(up["cost"].mean()) if "cost" in up.columns and len(up) else 0.0
    gross_up_excess = up_excess + cost_drag_avg
    if gross_up_excess > 0:
        cost_share = cost_drag_avg / gross_up_excess
    elif abs(cost_drag_avg) <= 0:
        cost_share = 0.0                 # 成本确为 0（纯排序研究）：占比就是 0
    else:
        cost_share = None                # 毛超额 ≤ 0 → 比值无意义，不给数
    return {
        "hit_rate_up": _rate(up),
        "hit_rate_down": _rate(down),
        "overall_hit_rate": _rate(history),
        "avg_up_return": avg_up,
        "avg_down_return": avg_down,
        "up_excess_vs_market": up_excess,
        "down_excess_vs_market": down_excess,
        "excess_breadth": up_excess + down_excess,
        "excess_tilt": up_excess - down_excess,
        "up_basket_n": n_up,
        "down_basket_n": n_down,
        # ★ 样本量的真分母：独立成交窗口数。见上方注释。
        #   `n_up`/`n_down` 是条目数，**不是**样本量 ——
        #   多个信号日共用一个窗口时它们会虚增。
        "n_predictions": int(len(history)),
        "n_independent_windows": n_windows,
        "judged": int(history["result"].isin(["hit", "miss"]).sum()) if "result" in history else 0,
        "avg_cost_drag": cost_drag_avg,
        "gross_up_excess": gross_up_excess,
        "cost_share": cost_share,
    }


# ---------- 复利口径（2026-10-14）----------

TRADING_DAYS_PER_YEAR = 252

# 少于此天数不给年化。理由见 `compounded_stats` 里那段实测记录：
# 单日收益的 252 次方不是业绩，是噪音。
MIN_DAYS_FOR_ANNUALISATION = 2


def daily_portfolio_returns(history: pd.DataFrame) -> pd.Series:
    """把逐笔判定摊成**逐日等权组合收益**（涨腿、已扣成本）。

    ## 为什么必须有这把尺子

    用户的年化 20% 目标只有一种算法能回答它：把日收益真正连乘，再对年数
    开根号（CAGR）。此前全项目**没有任何一个累计或年化字段**——
    `rolling_stats` 只出均值，从不连乘。唯一存在的 `annualized_return` 在
    `bond_excess_analysis.json`，那是债券策略，且是训练段的。

    ## 三个不能省的判断

    **1. 只用涨腿。** 跌腿是**回避清单**，我们从未持有过它——按 `rolling_stats`
    的既有约定它连成本都不扣。把它复利成一条曲线等于凭空做空。

    **2. 成分不足的日子丢弃，不填 0。** `.mean()` 默认跳过 NaN，所以一天里
    「10 只里只有 1 只有次日行情」会算出一个**看起来正常**的平均值，而它
    衡量的其实是个别标的的运气。填 0 更糟——那是宣称「那天组合没动」，
    是编造。低于 `MIN_BASKET` 的日子整天丢掉，并让 `n_days` 如实反映天数。

    **3. 成本只扣一次。** 净收益 = `actual_pct − cost`。

    ## 返回空的 Series（而不是全 0）是有意的

    空输入返回空 Series，由 `compounded_stats` 转成 `None`。
    「0% 收益」和「没测过」必须能被下游区分开。

    ## 4. 按 `exit_date` 分组，不是 `pred_date`（2026-10-14）

    **观测日是收益实现的那一天，不是发名单的那一天。**

    实测 2026-10-09（五）/ 10-10（六）/ 10-11（日）三次出名单，
    周末主库没有 bar，三次的成交窗口**完全相同**（10-12 买、10-13 卖）。
    按 `pred_date` 分组会把「同一天的三个篮子」当成三天连乘：

        真实加权日收益   +0.67%
        连乘三天累计     −1.79%      ← 三个因子的乘积，不是任何真实收益
        再年化 (÷84次方) −78.06%     ← 符号翻过来了

    `n_days=3` 还会让 `MIN_DAYS_FOR_ANNUALISATION` 放行，把单日噪音
    包装成「−78% 的年化」。这是本次补齐判定时**实测撞上的**，
    不是假想：补判前 1 天，补判后 3 天，数字就是这么来的。

    缺 `exit_date` 的历史行（旧判定路径产出）退回 `pred_date` 分组，
    绝不整个丢掉——那等于抹掉上线以来攒下的全部证据。
    """
    if (history is None or history.empty
            or "direction" not in history.columns
            or "actual_pct" not in history.columns):
        return pd.Series(dtype=float)

    up = history[history["direction"] == "up"].copy()
    if up.empty:
        return pd.Series(dtype=float)

    up["net"] = up["actual_pct"].astype(float)
    if "cost" in up.columns:
        up["net"] = up["net"] - up["cost"].astype(float)

    # 观测日：优先 `exit_date`（收益真正实现的那天），缺失则退回 `pred_date`。
    day_col = None
    if "exit_date" in up.columns and up["exit_date"].notna().any():
        up["_day"] = up["exit_date"].astype(str)
        # 混档时（有 exit_date 的新行 + 没有的旧行）按 pred_date 兜底，
        # 避免旧行被算进「不知道哪天实现」的伪分组里。
        legacy = up["exit_date"].isna()
        if legacy.any():
            if "pred_date" not in up.columns:
                return pd.Series(dtype=float)
            up.loc[legacy, "_day"] = "legacy:" + up.loc[legacy, "pred_date"].astype(str)
        day_col = "_day"
    elif "pred_date" in up.columns:
        day_col = "pred_date"
    else:
        return pd.Series(dtype=float)

    by_day = up.groupby(day_col)["net"]
    daily = by_day.mean()
    size = by_day.count()
    daily = daily[size >= MIN_BASKET]
    return daily.astype(float).sort_index()


def compounded_stats(history: pd.DataFrame) -> dict:
    """复利口径的收益统计 —— **对标「年化 20%」的唯一尺子**。

    ## 算术年化会把结果说反

        算术 − 复利 ≈ σ²/2 × 252

    波动越大差得越远。实测本项目 10 只等权组合日波动 3.44%（年化 54.6%）：

        算术 − 复利 = 0.0344²/2 × 252 = **14.91%/年**

    这是**量级**问题不是精度问题。实测现任权重的回测：

        日均净收益（算术）  -0.68224%
        算术×252          -171.92%   ← 报告里的口径，物理上不可能
        复利累计           -96.96%
        真实 CAGR          -84.70%   ← 复利口径

    年化跌幅超过 100% 一眼就能看出口径错了，而它确实印在报告里。

    ## 为什么 `cagr` 在接近 -100% 时仍然可用

    `CAGR = (1 + total)^(1/years) − 1`。total = -0.9696、years ≈ 2.0 时，
    底数 0.0304 开方后仍有定义。真正会炸的是 total ≤ -1（本金亏光），
    那种情况返回 `None` 而不是抛异常——归零是**策略死了**，
    不是「年化收益特别特别负」。

    ## 与 `rolling_stats` 的口径差异是刻意的

    `rolling_stats` 的 `avg_up_return` 是**逐笔**平均（每天成分数不同，
    权重不同）；这里 `arith_daily` 是**先按日等权、再对日等权**。
    后者才是「10 只等权组合一天赚多少」的正确读数，也是 CAGR 的输入。
    两者不一致是正常的，前者是统计量，后者是组合曲线。
    """
    none_stats = {
        "total_return": None, "cagr": None, "arith_daily": None,
        "arith_ann": None, "ann_vol": None, "vol_drag": None,
        "n_days": 0, "years": None,
    }
    daily = daily_portfolio_returns(history)
    if daily.empty:
        return none_stats

    r = daily.to_numpy(dtype=float)
    n = len(r)
    total = float(np.prod(1.0 + r) - 1.0)
    years = n / TRADING_DAYS_PER_YEAR
    arith_daily = float(r.mean())

    # 单日样本：所有**跨期**的量一律 None（2026-10-14）。
    #
    # 实测 `/api/status` 在 n_days=1 时返回过 `arith_ann = -681.93%`、
    # `cagr = -99.90%`、`vol_drag = -582.03%`。这三个数**数学上全对**、
    # **解读上全错**——它们是单日收益的 252 次方，量到的是噪音不是业绩，
    # 而它们真的会显示在概览页上。
    #
    # `total_return` 仍然给：那一天的涨跌是**事实**，不是外推。
    if n < MIN_DAYS_FOR_ANNUALISATION:
        return {
            "total_return": total,
            "cagr": None,
            "arith_daily": arith_daily,
            "arith_ann": None,
            "ann_vol": None,
            "vol_drag": None,
            "n_days": n,
            "years": years,
        }

    arith_ann = arith_daily * TRADING_DAYS_PER_YEAR
    ann_vol = float(r.std(ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR)

    # 本金亏光时 CAGR 无定义。归零是策略死了，不能报成一个有限的负数。
    if total <= -1.0 or years <= 0:
        cagr = None
    else:
        cagr = float((1.0 + total) ** (1.0 / years) - 1.0)

    return {
        "total_return": total,
        "cagr": cagr,
        "arith_daily": arith_daily,
        "arith_ann": arith_ann,
        "ann_vol": ann_vol,
        "vol_drag": (arith_ann - cagr) if cagr is not None else None,
        "n_days": n,
        "years": years,
    }

