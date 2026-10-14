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
                    unknown_st: dict | None = None) -> pd.DataFrame:
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

    max_stale_days：最新数据日距快照日的自然日上限——退市股最后一行停在摘牌日，
    不剔除会以冻结动量霸榜且次日必 no_data（2026-10-10 全池回测实证）。
    as_of 缺省时取 cross_section 里最新的 last_date 作基准（实时路径自然成立）。

    `unfillable` 传入一个 dict 时，被判为买不进的 code 会按日累加计数——
    「剔除了多少」必须可见，否则这是一次静默的口径变更。
    """
    df = cross_section
    if "last_date" in df.columns:
        base = as_of or df["last_date"].max()
        fresh = (pd.Timestamp(base) - pd.to_datetime(df["last_date"])).dt.days <= max_stale_days
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
