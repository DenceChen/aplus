"""财务数据层：**披露日对齐**协议（前视偏差的第一道闸门）。

## 这个模块存在的唯一理由

A 股财报有两个日期，含义完全不同：

- `NOTICE_DATE` —— **首次披露日**。这天市场才第一次看到这份财报。
- `UPDATE_DATE` —— **最近一次修正日**。这天财报被改过（可能就是今天）。

因子在 `as_of` 那天只能看见「`as_of` 之前已经公开」的财报。
所以对齐**只能**用 `NOTICE_DATE`：

> **用 `UPDATE_DATE` 对齐 = 回测提前知道了财报的修正结果 = 前视偏差。**

`docs/PLATFORM.md:18` 已写死「后修财务 → 禁止入回测」，
本模块把那条散文变成可执行、可测试的代码。

## ⚠️ 本模块**不**解决 PIT，只把 PIT 的边界标出来

`docs/factor_spec_cn.md` §1.5.1 实测结论：当前数据源（akshare 东财）
**每个报告期只存一行、没有版本维度**，因此被服务的值**必然是后修值**。

于是本模块能给出的，只是一个诚实的过滤：

    `NOTICE_DATE <= as_of`  →  **用诚实的披露时间戳，贴在一个事后修订过的数值上**

它既不是 PIT，也不是「始终采用最新数据」。**两者都不是。**
所以 §1.5.1 判定：在白名单过滤（`is_never_revised`）或攒 vintage 之前，
**BM / ROE / FAR 一律不实现**。

`is_never_revised()` 是 §1.5.1 提的**出路 A**：
只保留「披露后从未被改动」的记录，此时今天服务的值 == 首次披露值，
PIT 才**由构造成立**。代价是丢掉约 20% 的报告期（实测数字见
`scripts/diag_financial_lookahead.py`）。

## 口径（诚实声明）

本模块是**纯函数、纯内存**，不联网、不落盘、不缓存 vintage。
出处是 vintage 的累积性质：**今天不落盘，那天的原始值就永久丢失**（出路 B）。
"""
from __future__ import annotations

import pandas as pd

#: 报告期（会计期间结束日）。这是**会计概念**，不是时间轴概念——
#: 把它当对齐日用是另一个常见错误（等于说「财报在期末就公开了」）。
REPORT_DATE_COL = "REPORT_DATE"

#: 首次披露日。**这是唯一合法的因子对齐基准。**
NOTICE_DATE_COL = "NOTICE_DATE"

#: 最近修正日。用于**诊断**（算修订滞后、标记被改过的记录），
#: **禁止**用于对齐。见模块 docstring 与 `tests/test_financial_lookahead.py`。
UPDATE_DATE_COL = "UPDATE_DATE"

#: 因子对齐只认这一列。写成模块级常量，是为了让「改用修正日」变成
#: 一处显眼的、可被测试钉死的改动，而不是散落各处的字符串。
ALIGN_DATE_COL = "NOTICE_DATE"

#: 对齐必需的列。缺 `NOTICE_DATE` 时**报错**而非降级——
#: 「降级」意味着拿修正日冒充披露日，那正是本模块要防的事。
_REQUIRED_COLS = (REPORT_DATE_COL, NOTICE_DATE_COL)


def normalize_financial(df: pd.DataFrame) -> pd.DataFrame:
    """把原始财务帧规范成「日期列是 datetime、脏行已剔除」的形态。

    akshare 的 `stock_financial_analysis_indicator_em` 返回的日期是
    `'2020-04-28 00:00:00'` 这样的**字符串**。不解析成 datetime，
    后续比较就退化成字符串比较——那既慢又脆。

    **丢弃日期不可解析的行**是刻意的：静默当成 1970 会造出一条
    「50 年前就已披露」的记录，比缺行危险得多。

    缺失 `REPORT_DATE` / `NOTICE_DATE` 直接 `ValueError`。
    `UPDATE_DATE` 缺失不报错——它只用于诊断，缺失时滞后记为 NaN。
    """
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"财务帧缺少必需列 {missing}；实际列为 {list(df.columns)[:20]}…"
        )

    out = df.copy()
    for col in (REPORT_DATE_COL, NOTICE_DATE_COL, UPDATE_DATE_COL):
        if col in out.columns:
            # format="mixed"：真实帧里混着 'YYYY-MM-DD HH:MM:SS' 与
            # 测试里故意塞的 'not-a-date'，不指定时 pandas 会逐元素回退到
            # dateutil 猜测，既慢又会刷 UserWarning。
            out[col] = pd.to_datetime(out[col], errors="coerce", format="mixed")

    # 只有披露日与报告期必须可用；修正日缺失不影响对齐，留 NaN 供诊断。
    out = out.dropna(subset=[REPORT_DATE_COL, NOTICE_DATE_COL])
    return out.reset_index(drop=True)


#: 股票标识列的候选名。财务源历史上叫过好几种：
#: `SECUCODE`（东财带后缀，如 `600519.SH`）、`SECURITY_CODE`（6 位纯数字）、
#: `code`（平台内部口径）。**只用来判断一个帧是单票还是多票**，不是改主键。
_STOCK_KEY_CANDIDATES = ("SECUCODE", "SECURITY_CODE", "code")


def visible_as_of(df: pd.DataFrame, as_of) -> pd.DataFrame:
    """**因子对齐的唯一入口**：返回 `as_of` 当天已经公开的报告期。

    过滤条件是 `NOTICE_DATE <= as_of` —— 按 `ALIGN_DATE_COL` 取列，
    写死走披露日。`UPDATE_DATE` 在本函数中**不参与任何判断**，
    只作为诊断列原样透出（诊断代码要用它算滞后）。

    同一报告期若有重复披露日，取最后一条（`keep="last"`）——
    注意源里通常**没有**重复（§1.5.1 实测 `REPORT_DATE` 重复数 = 0），
    这里只是防御。

    🚨 **去重的键必须含股票键（帧里有才带）。**
    原来只按 `REPORT_DATE` 去重 ⇒ **同一报告期的所有股票行被塌成 1 行**：
    实测 `financial_annual_cache.parquet`（9,329 行 / **500 只票** / 38 个报告期）
    过这个函数后只剩 **38 行** ⇒ **多股票横截面直接消失**。
    那个函数当时**没有任何生产调用方**（只有测试在用），而测试夹具
    （`tests/test_financial_lookahead.py::_raw`）**本身就是多票的**
    （600519 + 000001）却**报告期各不相同** ⇒ **测试是碰巧过的**。
    ⇒ 现在按 (股票键, REPORT_DATE) 去重；单票帧（没有股票键列）行为**逐位不变**。
    """
    if df.empty:
        return df.copy()
    aligned = df[ALIGN_DATE_COL]
    mask = aligned.notna() & (aligned <= pd.Timestamp(as_of))
    out = df.loc[mask]
    key_cols = [c for c in _STOCK_KEY_CANDIDATES if c in out.columns]
    subset = [key_cols[0], REPORT_DATE_COL] if key_cols else [REPORT_DATE_COL]
    return out.sort_values(ALIGN_DATE_COL).drop_duplicates(
        subset=subset, keep="last"
    ).reset_index(drop=True)


def revision_lag_days(df: pd.DataFrame) -> pd.Series:
    """修订滞后天数 = `UPDATE_DATE - NOTICE_DATE`。

    0 天 = 从未被改动；> 0 天 = 被改过，且改得越晚越说明今天的值
    与当初披露的值差得越远。

    **负值不做 `abs()`**：倒填（修正日早于披露日）在现实里不存在，
    真出现了应当被看见，而不是被一个绝对值悄悄抹平方向。
    修正日缺失时为 NaN（`int64` 会变成浮点）。
    """
    lag = (df[UPDATE_DATE_COL] - df[NOTICE_DATE_COL]).dt.days
    return lag.astype("float64")


def is_never_revised(df: pd.DataFrame) -> pd.Series:
    """`UPDATE_DATE == NOTICE_DATE` 白名单 —— §1.5.1 的**出路 A**。

    只保留这批记录，今天服务的值就等于首次披露的值，PIT 由构造成立。

    修正日缺失的记录**不算**「从未被改动」——无法证明它没被改过，
    按 `PLATFORM.md`「不可得即禁止」的规矩，宁可排除。
    """
    return (df[UPDATE_DATE_COL].notna()
            & (df[UPDATE_DATE_COL] == df[NOTICE_DATE_COL]))


def annual_reports(df: pd.DataFrame) -> pd.DataFrame:
    """只取**年报**。

    因子里的 ROE(TTM) / BM / F-Score 用的都是年报口径；
    混进一季报/中报会让 `NOTICE_DATE` 的分布整体前移（季报披露早得多），
    通过率会算出一个偏乐观的假数。
    """
    if "REPORT_TYPE" not in df.columns:
        raise ValueError("财务帧缺少 REPORT_TYPE，无法区分年报/季报")
    return df.loc[df["REPORT_TYPE"].astype(str).str.strip() == "年报"].reset_index(drop=True)
