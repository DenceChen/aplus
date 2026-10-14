"""预测引擎：横截面因子 → 涨/跌 TopN 名单。

- filter_universe：剔 ST / 次新（上市≤60 交易日）/ 停牌无成交 / 数据过期 /
  **买不进**（一字板 / 涨停收盘 / 停牌，见 `aplus.panel.can_buy`）
- build_predictions：合成分排名，TopN = 预测涨，BottomN = 预测跌；
  每条带方向、分数、因子快照——可解释是硬要求（用户看不懂模型，但要看懂理由）
"""
import re

import pandas as pd

from .factor import FACTOR_NAMES, composite_score

# A 股风险警示股票简称的全部形态：ST / *ST / SST / S*ST（未完成股改）。
# 大小写不敏感——源站简称在不同接口里大小写不统一。
# 末尾的 \S 是必需的：光秃秃的 "ST" / "*ST"（前缀后一个字符都没有）不是任何
# 真实简称，只可能是截断脏数据。判成 ST 就会白白丢掉一只正常股票。
ST_NAME_RE = re.compile(r"^(S\*?)?\*?ST\S", re.IGNORECASE)


def is_st_name(name) -> bool:
    """股票简称是否带 ST/*ST 前缀。

    ## 为什么需要它（2026-10-11 事故）

    `isST` 列**对 88.2% 的股票不可判定**：1693 只压根没有这列（腾讯备源写的
    文件），3112 只最新值是 NaN（`_upsert` 把有列与无列的源 concat 出来的）。
    而 `panel.cross_section` 把 NaN 填成 0 ——「不知道」被当成「不是 ST」。

    实测后果：用户当日推荐名单里出现了 `*ST岭南` 和 `*ST住亭`。交叉验证
    （`data/names.json` 的官方简称）显示 310 只 ST 股里有 **178** 只因此
    被当作普通股票推荐，另有 45 只 `isST` 列写着 0 但名称是 ST——**列本身
    也是错的**。

    名称是独立于行情表的来源，`*ST` 前缀在 A 股无歧义，故作为兜底防线。

    名称缺失时返回 False（不当成 ST）：那会把整池误杀，比漏掉 ST 更糟。
    真正的风险是「两个信号都没有」，所以名称缺失比例需要单独监控。
    """
    if name is None:
        return False
    try:
        if pd.isna(name):
            return False
    except (TypeError, ValueError):
        pass
    return bool(ST_NAME_RE.match(str(name).strip()))


def filter_universe(cross_section: pd.DataFrame, as_of: str | None = None,
                    max_stale_days: int = 30, unfillable: dict | None = None,
                    unknown_st: dict | None = None,
                    stale: dict | None = None) -> pd.DataFrame:
    """剔 ST / 次新（≤60 交易日）/ 停牌无成交 / 数据过期（退市冻结）/ **买不进**。

    **ST 用两个独立信号取「或」剔除**：(isST == 1) 或 (名称带 ST/*ST 前缀)。
    绝不能要求两个信号都说是才剔——那样 178 只照样进来；也绝不能要求
    `isST == 0` 才保留——那会砍掉 88% 的正常股票（详见 `is_st_name`）。

    ## 买不进（`buyable` 列，来自 `aplus.panel.can_buy`）

    推荐一只买不进的股票是**无效输出**。实测当日实盘名单里 `600825.SH 新华传媒`
    连续 9 个一字板还挂在「看涨首选」第一位，综合分 7.56、二十日动量 +141.9%。

    这些股票是「买不到」，**不是「会跌」**：它们必须从涨篮子和跌篮子
    **两边都排除**。塞进跌篮子等于把一个缺失当成一个看空信号，
    两边一减凭空造出虚假的多空价差——见 `build_cross_sections` 的 `unfillable` 计数。

    与 ST 同一条纪律：**信号缺失时不能默认放行**。`buyable` 由面板整段向量化
    预计算（`FactorPanel.cross_section`），判据是 `can_buy` 的四条：
    停牌 / 一字板（封在限价上）/ 涨停收盘 / prev 缺失。

    **但整列缺失时不得 fail-closed**：实盘慢路径 `cross_rows_from_map`
    不产出该列（`aplus/snapshot.py` 不在本文件的改动范围内），
    在那条路径上 fail-closed 等于把整池清空、平台直接不可用。
    这是**已知的接线缺口**，不得用 fail-closed 的形式伪装成已生效。
    补齐方式是给 `cross_rows_from_map` 的 COLUMNS 加上同一列。

    ## 陈旧透传（2026-10-14 修复）

    横截面 `panel.cross_section` 用 `searchsorted(side="right") - 1` 取「信号日 D
    的最后一根 K 线」。该股在 D 当天没有 bar 时，这一行指向的是 **D 之前**
    某天的价格——它的因子值（mom20 / vol20 / …）算到那一天，与同场其他股票
    **不是同一个时点**。横截面排序的前提正是「所有成分都算到同一天」，
    时点不齐的名次没有意义。此前这里只有 `max_stale_days=30`（**自然日**）
    兜底，于是 30 个自然日（约 21 个交易日）的窗口里，停笔股一直带着
    冻结的因子值在池子里，直到滞后超限才被剔除。

    实测这个窗口**只放行噪声，从不放行真实交易**（2026-10-14，
    `data/reports/*.parquet` 四个落盘产物，N = 33,760 条篮子记录）：

    | 末根滞后 | 篮子记录 | 产出收益的 |
    |---|---|---|
    | lag = 0 | 33,682 | 33,682 |
    | lag ∈ [1, 30] | **78** | **0**（全部 no_data，且无一有 D+1 bar） |

    所以判据是：**`last_date` 必须等于横截面自身最新的 `last_date`**。
    这不是「30 天太久」，是「时点不可比」——把 `max_stale_days` 调大
    不构成豁免（`tests/test_stale_passthrough.py` 有专门一条钉住）。

    **基准取横截面自身的最大值，不取 `as_of`**：`as_of` 可能不是交易日
    （实盘 cron 在周末/节假日照常触发，2026-10-11 就是周日），拿它当基准
    会让停一天的公司在周一被判成滞后 2 天，一个长假就能把整池清空——
    fail-closed 在这条路径上等于平台直接不可用。取最大值则**必然有股票
    存活**，且实盘（`build_cross_section`）与回测（`build_cross_sections`）
    两条路径都经由面板给出同一列，判定天然一致。

    **整列缺失/全不可解析时不做处理**（与 `buyable` 同一纪律）：把整池清空
    比放过几天更糟，而「数据整体陈旧」由 `scripts/run_daily.py` 的
    「数据截至 X，已陈旧 N 天」告警负责暴露。

    ## 计数：剔除必须可见

    `stale` 传入 dict 时，陈旧剔除按日累加。**未单独传入时自动落到
    `unfillable`**——它们确实买不进（实测 78/78 条没有 D+1 bar），
    语义一致；而 `unfillable` 是 `aplus/backtest.py`（本轮不得改动）
    已经接到 `result.attrs["unfillable"]` 上的唯一通道。
    """
    df = cross_section
    if "last_date" in df.columns:
        parsed = pd.to_datetime(df["last_date"], errors="coerce")
        signal_day = parsed.max()
        if pd.notna(signal_day):
            base = as_of or signal_day
            fresh = (pd.Timestamp(base) - parsed).dt.days <= max_stale_days
            # 陈旧透传：该股在信号日没有自己的那一根 bar（lag > 0）一律剔除。
            fresh &= parsed.eq(signal_day)
            if stale is None:
                stale = unfillable          # 未单独分账 → 计入 unfillable
            if stale is not None:
                for code in df.loc[~fresh, "code"]:
                    stale[code] = stale.get(code, 0) + 1
            df = df[fresh]
    mask = pd.Series(True, index=df.index)
    if "buyable" in df.columns:
        buyable = df["buyable"].fillna(False).astype(bool)
        if unfillable is not None:
            for code in df.loc[~buyable, "code"]:
                unfillable[code] = unfillable.get(code, 0) + 1
        mask &= buyable
    st_by_name = df["name"].map(is_st_name) if "name" in df.columns else False
    if unknown_st is not None:
        # **「ST 状态未知」必须按日计数上报**，而不是被 `!= 1` 悄悄放行。
        # 放行本身在数据有洞时是对的（docstring：要求 `isST == 0` 会砍掉 88% 的
        # 正常股票），但「有多少只处于这个状态」必须看得见——
        # 否则一次降级写入把 isST 削空，ST 过滤对全市场失效，**没有任何告警**。
        # 真实事故：2026-10-11 的 20 只推荐里 6 只（30%）落在盲区。
        for code in df.loc[df["isST"].isna(), "code"]:
            unknown_st[code] = unknown_st.get(code, 0) + 1
    mask &= (df["isST"] != 1) & ~st_by_name & (df["listed_days"] > 60) & (df["volume"] > 0)
    return df[mask].reset_index(drop=True)


# 预测输出的列契约。`build_predictions` 与 `aplus/backtest.py` 的
# `_held_predictions`（调仓周期的非调仓日沿用上期持仓）都必须产出这个形状，
# 否则下游 `_next_bars` / `verify_predictions` 会对不上列。
PRED_COLUMNS = ["code", "name", "direction", "score"] + list(FACTOR_NAMES)


def build_predictions(cross_section: pd.DataFrame, top_n: int = 10, bottom_n: int = 10, weights=None):
    """返回 (up_df, down_df)：up 按分数降序，down 按分数升序（最看空在前）。

    weights 注入自进化：load_weights() 的结果沿 run_daily → 此处贯通。
    """
    df = cross_section.copy()
    df["score"] = composite_score(df, weights)
    df = df[df["score"].notna()].reset_index(drop=True)  # 因子不完整者剔除
    ranked = df.sort_values("score", ascending=False).reset_index(drop=True)

    up = ranked.head(top_n).copy()
    up["direction"] = "up"
    down = ranked.tail(bottom_n).iloc[::-1].copy()
    down["direction"] = "down"

    # 注意：只有因子列才按「df 里有没有」过滤。`direction` 是在 up/down 上
    # **之后**设的，df 里根本没有这一列——跟着 df 过滤会把它悄悄丢掉，
    # 下游 verify_predictions 直接 KeyError。（2026-10-11 接线时踩过。）
    keep = ["code", "name", "direction", "score"] + \
        [c for c in FACTOR_NAMES if c in df.columns]
    return up[keep].reset_index(drop=True), down[keep].reset_index(drop=True)
