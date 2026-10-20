"""股息率 PIT 数据管道 —— 从 baostock 采分红，落成**可按信号日回溯**的追加式快照。

## 为什么是这一条（背景，见 `docs/RESEARCH_50K_A_SHARE_EDGES_2026-10-20.md` §3.1）

本仓 8 个价量因子已被 `NO_LEVER` 判决封口
（`data/reports/factor_attribution.json::verdict`），多头毛超额天花板 ~11%/年
（`data/reports/bridge_error.json::years[*].measured_long_vs_mkt_annual_by_q`）。
**股息率是本仓唯一「有独立信息源（分红）」而尚未测过的股票因子**，
而红利/低波正是石川书里 A 股有正向实证的家族。

## 🚨 本仓最贵的一条教训，写死在文件顶上（不要往下读就忘了）

**石川书里的因子超额是「多空」口径（顶 − 底），不是长腿。**

- `data/books/cn_factor_lines.txt:9885`：IVOL 等权月均超额 **1.27%，t=8.64**
  —— 那是「做多最低 IVOL 组 + 做空最高 IVOL 组」。
- 空腿在 A 股散户**做不了**（融券不可得）⇒ **空腿那部分收益对本项目不可达**。
- 本仓上一轮实测：多空 108.34%/年，长腿只有 ~11%/年，**差的那部分整份落在空头腿**。

⇒ **本管道只负责数据。本项目将来要判的是「长腿（涨腿 top-N 相对市场）」，
不许把书里的多空数当成本项目的可达目标。**
预注册在 `docs/PREREG_DIVIDEND_LOWVOL_LONGLEG_2026-10-21.md`。

## PIT 锚：为什么是 `dividPlanAnnounceDate`（预案公告日）

分红有两个日期，**必须分清**：

| 日期 | 字段 | 含义 | 能不能当 PIT 锚 |
|---|---|---|---|
| 预案公告日 | `dividPlanAnnounceDate` | 分红方案**首次公开** | ✅ **本管道用这个** |
| 除权除息日 | `dividOperateDate` | 股份登记/除权当天 | ❌ 它**晚于**公告日 |

信号日 D 只应看得见「**D 之前已公告**」的方案 ⇒ 锚必须是公告日。
用除权日当锚会**把整个方案往后推一个季度**（丢掉公告日到除权日之间的信息），
用「实际到账日」当锚更错。

⚠️ `dividPreNoticeDate`（预披露日）实测在样本里 **74/74 全空**，
而且**源站没有任何字段能说明「金额在预披露那天是否已公开」**
⇒ 即便它非空也不能用（用它是**猜**，而 PIT 错的方向是乐观的）。
⇒ **本管道只用 `dividPlanAnnounceDate`，并把「只用公告日」这件事写成可测的守卫。**

## 本管道与「财报 vintage」的关键差别：分红 PIT **可回溯**，不必等

`aplus/vintage.py` 的财报快照是「源站当天服务的整帧」⇒ **今天采不到昨天**，
只能从今天起一天一天攒（出路 B）。

分红不一样：**公告日是记录自带的字段**。所以今天采到的全量历史，
配上「公告日 ≤ D」的过滤，**就能重建任意历史日 D 的 PIT 帧**。

⚠️ 但这**不是**「没有 PIT 问题」，而是把问题换了一个形状：

| | 财报 vintage | 分红（本文） |
|---|---|---|
| 金额会不会被改 | 会（整张报表重述） | **会**（预案 → 股东大会可能改额） |
| 今天能不能重建历史 PIT | **不能** | **能**（公告日过滤） |
| 但重建出来的是「**当时公告的金额**」吗 | —— | ❌ **不一定**，是**今天读到的金额** |

⇒ **两条腿都要**：
1. **事件层（本管道的 `pit_dividend_frame`）**：按公告日过滤 ⇒ 今天就能回测 2021–2026。
2. **快照层（`aplus.vintage` 的 `date=` 分区）**：每天存一份**当天源站服务的全量**，
   从今天起逐日累积 ⇒ **将来**能观测到「某条方案的金额在公告后被改过」，
   也能在**任意 as_of 日**取到「当时源站在服务什么」。

🚨 **这两层不许混引**（第 7 层：两个各自都对的数放一起比）：
事件层的「今天读到的金额」与快照层的「当时读到的金额」**不是同一个量**。

## 落盘形状：**照抄仓里的 vintage 布局，不另造一套**

    data/reports/dividend_pit/
      date=YYYY-MM-DD/financial.parquet     ← 分区文件名沿用 aplus.vintage.PARTITION_NAME
      _manifest.jsonl                        ← 追加式账本（aplus.vintage 写、a plus.vintage 验）

为什么分区文件叫 `financial.parquet`（**不是** `dividend.parquet`）：
因为读写、摘要、完整性校验**整条复用 `aplus.vintage`**（`write_snapshot` /
`pit_frame` / `available_days` / `span` / `verify_integrity`），
分区文件名是该模块的模块级常量。**另起一套就得另写一套摘要与完整性校验**，
而那份校验是本仓目前唯一能自证「这份数据没被动过」的机制。
目录名 `dividend_pit` 已经把内容说清楚了。

⚠️ 代价要说清：仓里出现了两个叫 `financial.parquet` 的东西（财报的那个、
分红的这个），**它们在不同的根目录下**（`data/reports/financial_vintage/` vs
`data/reports/dividend_pit/`）。**引用时必须带根路径**，不许只说文件名。

## 频控铁律：串行、单连接、限速

baostock 有日调用上限，且**禁并发**；超限会被拉黑（小时级）。
⇒ 本管道**一次只发一个请求**，请求之间**强制 sleep**。
`tests/test_dividend_pit.py` 里有一条**能力守卫**用假 fetcher 量「最大并发数」，
把它钉成 1 —— 不是靠注释声称。

## 成本账：先算再采

实测（本轮，50 只 × 8 年 = 400 次调用）：单次调用均 **0.173 s**、最大 **0.691 s**。
⇒ 全池 5,460 只 × 8 年 = **43,680 次调用** ⇒ 见覆盖产物里的 `cost_account`。
🚨 **本轮只跑 50 只的小样本**；全池规模由决策者按成本账决定。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from aplus import provenance, vintage  # noqa: E402
from aplus.factor_fundamental import bare_code  # noqa: E402

# ── 常量 ────────────────────────────────────────────────────────────

#: 溯源章上的生成器名。**必须**与脚本路径逐字一致，
#: 否则 `provenance.freshness()` 解析不出生成器 ⇒ 判 `unknown`（= 不许引用），
#: 而 `unknown` 与「数据坏了」长得一模一样（第 153 层）。
GENERATOR = "scripts/collect_dividend_pit.py"

#: 分红 PIT 快照根。**复用 `aplus.vintage` 的整套读写协议**（见模块 docstring）。
DIVIDEND_ROOT = ROOT / "data" / "reports" / "dividend_pit"

#: 平台真实股票池目录（`000001.SZ.parquet` 一只一个）。
DAILY_DIR = ROOT / "data" / "master" / "daily"

#: baostock 请求之间的强制间隔（秒）。**禁并发 + 限速**。
SLEEP_BETWEEN_CALLS = 0.05

#: 采集的「预案公告年份」范围。
#:
#: 判决窗口是 2021-01-01 → 2026-10-12，而 TTM 股息率需要 (D−365, D] 内的
#: 除权除息记录 ⇒ 最早要覆盖 **2020 年初**的除权（由 2019/2020 年公告的方案产生）。
#: 上界取当年（2026）⇒ 覆盖到 2026-10 之前公告的方案（含 2026 中期分红）。
#:
#: ⚠️ 这个范围是**按语义推的**，不是按「多采点保险」堆的 ——
#: 少采一年 ⇒ 判决窗口头一年�� TTM 分子少一条；多采一年 ⇒ 成本 +12.5%。
DEFAULT_YEARS = tuple(range(2019, 2027))

#: 判定「这条记录能不能进 PIT 帧」的锚字段（源站原名）。
PLAN_ANNOUNCE_COL = "dividPlanAnnounceDate"
#: 除权除息日（源站原名）。**不是 PIT 锚**，只用来定位 TTM 窗口。
OPERATE_COL = "dividOperateDate"
#: 每股税前现金分红（源站原名）。
CASH_BEFORE_TAX_COL = "dividCashPsBeforeTax"
#: 每股税后现金分红（源站原名）。**只作留档，不进计算** ——
#: 实测它的值带中文（"0.288或0.32"），不是一个可解析的数。
CASH_AFTER_TAX_COL = "dividCashPsAfterTax"

#: **方案身份键** = (票, 预案公告日, 除权除息日)。
#:
#: 🚨 **不包含金额** —— 金额是**方案的一个属性**，不是身份的一部分。
#: 包含金额的后果（本轮实测踩到）：源站把**同一个方案返回两遍**
#: （一遍带金额、一遍金额字段为空）⇒ 两行都活着 ⇒
#: TTM 分子**重复计入**，而其中一行还是 NaN。
#: 实测 50 只 × 8 年的样本里命中 2 例（002403 / 300261 的 2021 年度方案）。
EVENT_KEY = ["code", "plan_announce_date", "operate_date"]

#: 现金金额的三种解析状态。**「没有现金」与「读不出来」必须是两个值。**
CASH_PARSED = "parsed"            # 税前字段可解析 ⇒ 金额是实测值
CASH_NO_CASH = "no_cash"          # 方案里**没有现金分红**（纯送/转）⇒ 0.0 是事实
CASH_UNREADABLE = "unreadable"    # 方案**有**现金但数字读不出来 ⇒ None（缺数据）

#: TTM 窗口长度（天）。
TTM_DAYS = 365

#: 事件层 `status` 的三态取值。**「缺数据」与「真的是零」必须是两个值。**
STATUS_OBSERVED = "observed"                 # 采到了，且窗口里有分红
STATUS_COLLECTED_NO_PAYOUT = "collected_no_payout_in_window"  # 采到了，窗口里确实没分红
STATUS_NOT_COLLECTED = "not_collected"       # **没采到** ⇒ 缺数据，不是零
STATUS_UNREADABLE_CASH = "unreadable_cash"   # 窗口里有一条分红的金额读不出来 ⇒ 缺数据


# ── 源站访问（可注入，便于测试不联网）────────────────────────────────

class BaostockError(RuntimeError):
    """源站访问失败。**与「源站说没有分红」严格分开**（见 `fetch_code_year`）。"""


def baostock_code(stem: str) -> str:
    """`000001.SZ` → `sz.000001`；`600000.SH` → `sh.600000`。

    baostock 的代码格式是 `sh.600000` / `sz.000001`。
    ⚠️ **不是** `600000.SH` —— 传错格式源站返回 0 行，且**不报错**。
    """
    s = str(stem).strip()
    if "." not in s:
        raise ValueError(f"代码缺交易所后缀：{s!r}")
    num, suf = s.split(".", 1)
    suf = suf.upper()
    if suf not in ("SH", "SZ", "BJ"):
        raise ValueError(f"未知交易所后缀：{s!r}")
    return f"{suf.lower()}.{num}"


def parse_cash_ps(raw) -> float | None:
    """`dividCashPsBeforeTax` → float。**解析不出来就返回 None，不返回 0。**

    🚨 **返回 0 是本管道最危险的一种编造**：
    「解析失败」与「这家公司没分红」会变成同一个数，
    而 0 股息率在选股里**看起来像「零股息」**（会被当成一个合法的低分值参与排序），
    而真实语义是「**没查到**」。⇒ 缺就是 None，缺口进产物。

    实测源站的值域：`"0.32"` / `"0.6"` / `"1.045102"` / `"14.539"` / `""`。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "" or s.lower() in ("none", "nan", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


#: baostock 的登录状态。**必须登录才能查** —— 未登录时
#: `query_dividend_data` 会打印 "you don't login." 并返回错误码，
#: **400 次调用会全部失败**（本轮首次实跑踩到：22 秒跑完，400 个失败）。
#: 登录**只做一次**（baostock 是单连接会话，反复 login 会打断连接）。
_LOGGED_IN = False


def ensure_login() -> None:
    """登录 baostock（幂等）。**失败就抛**，不许让 400 次调用白跑。"""
    global _LOGGED_IN
    if _LOGGED_IN:
        return
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise BaostockError(f"baostock 登录失败：{lg.error_code} {lg.error_msg}")
    _LOGGED_IN = True


#: 方案描述里出现这个字 ⇒ **有现金分红**。
#:
#: 实测 295 条的描述形状（`10` 后面是每 10 股的份额）：
#: | 形状 | 条数 | 有现金？ |
#: |---|---|---|
#: | `N派N元（含税，扣税后N或N元）` | 256 | ✅ |
#: | `N转N派N元（…）` | 36 | ✅ |
#: | `N派N元` | 1 | ✅ |
#: | `N送N派N元（…）` | 1 | ✅ |
#: | `N转N` | 1 | ❌ **纯转增，没有现金** |
#:
#: ⇒ 唯一无现金的形状是「只有送/转、没有派」。
#: 🚨 **不许用「解析不出来就当 0」** —— 那会把「读不出来」变成「零股息」。
CASH_KEYWORD = "派"


def resolve_cash(before_tax_raw, desc) -> tuple[float | None, str]:
    """每股税前现金分红 → `(金额, 状态)`。

    | 状态 | 金额 | 什么时候 |
    |---|---|---|
    | `parsed` | 实测值 | `dividCashPsBeforeTax` 可解析 |
    | `no_cash` | **0.0** | 税前字段读不出，但描述里**没有「派」** ⇒ 纯送/转 |
    | `unreadable` | **None** | 描述里有「派」⇒ 确实有现金，但数字读不出来 ⇒ **缺数据** |

    🚨 **`no_cash` 与 `unreadable` 差着一个数量级的后果**：
    `no_cash` 的 0.0 是**事实**（这家公司这次没派现金），
    `unreadable` 的 0.0 会把一条**真的**分红记成零 ⇒ 少算 ⇒ 选股时被低估。
    反过来若把 `unreadable` 报成 0，TTM 分子会**偏小** ⇒ 股息率**偏低** ⇒
    高股息票被系统性看低 ⇒ 这个方向的偏差会**掩盖**因子的效果。
    ⇒ 宁可整只票标成缺数据。
    """
    v = parse_cash_ps(before_tax_raw)
    if v is not None:
        return v, CASH_PARSED
    s = str(desc or "")
    if CASH_KEYWORD in s:
        return None, CASH_UNREADABLE
    if s.strip():
        return 0.0, CASH_NO_CASH
    return None, CASH_UNREADABLE


def _default_fetcher(bs_code: str, year: int) -> tuple[pd.DataFrame, str]:
    """真·源站访问。**一次一个请求**（禁并发铁律）。

    返回 `(frame, status)`，`status` ∈ `{"ok", "empty", "error:<msg>"}`。
    ⚠️ `empty` 与 `error` **必须分开**：前者是「那年这票没有分红方案」，
    后者是「没查成」。把两者合并 ⇒ 没查成的票会被当成「零股息」。
    """
    import baostock as bs

    ensure_login()
    rs = bs.query_dividend_data(code=bs_code, year=str(year), yearType="report")
    if rs.error_code != "0":
        return pd.DataFrame(), f"error:{rs.error_code}:{rs.error_msg}"
    rows: list[list[str]] = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return pd.DataFrame(), "empty"
    return pd.DataFrame(rows, columns=rs.fields), "ok"


def fetch_code_year(bs_code: str, year: int,
                    fetcher=None) -> tuple[pd.DataFrame, str]:
    """取一只票在某「预案公告年份」的全部分红方案。

    `yearType="report"` = **按预案公告年份归集**（源站 docstring 逐字：
    「yearType，默认为"report":预案公告年份，可选项"operate":除权除息年份」）。
    ⇒ 源站这一维**天然就是 PIT 锚那一维**，不需要在下游重新分桶。

    ⚠️ 一年**可能有多行**（年度方案 + 中期方案）—— 实测 600519 在 2024 年有 2 行
    （2024-04-03 年度 + 2024-11-09 中期）。**不许假设一年一行。**
    """
    fetcher = fetcher or _default_fetcher
    frame, status = fetcher(bs_code, year)
    if status.startswith("error:"):
        return pd.DataFrame(), status
    if frame is None or frame.empty:
        return pd.DataFrame(), "empty"
    missing = [c for c in (PLAN_ANNOUNCE_COL, OPERATE_COL, CASH_BEFORE_TAX_COL)
               if c not in frame.columns]
    if missing:
        return pd.DataFrame(), f"error:missing_columns:{missing}"
    return frame, "ok"


# ── 归一化 ──────────────────────────────────────────────────────────

#: 落盘的事件表列。**一列都不裁**（`aplus/vintage.py` 的理由：
#: 落盘时没抓的字段，过去那一天就永远补不回来了）。
EVENT_COLUMNS = [
    "code", "bs_code", "announce_year",
    "plan_announce_date", "agm_date", "operate_date", "pay_date",
    "cash_ps_before_tax", "cash_status", "cash_ps_after_tax_raw",
    "stock_ps", "reserve_to_stock_ps", "cash_stock_desc",
    "collected_at",
]


def normalize_events(raw: pd.DataFrame, bs_code: str, year: int,
                     collected_at: str) -> pd.DataFrame:
    """源站帧 → 事件表。**日期一律转成 `YYYY-MM-DD` 字符串或 None。**

    ⚠️ **空日期不是 0、不是空串** —— 统一成 `None`。
    空串与 `None` 在 pandas 里行为不同（空串会被当字符串参与比较），
    而本管道的 PIT 过滤是**字符串比较**（`plan_announce_date <= as_of`），
    空串会**小于任何日期** ⇒ 一条没有公告日的记录会被当成「D 之前就已知」⇒ **前视**。
    ⇒ 统一成 None，并在 PIT 过滤里**显式排除 None**。
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    def _date(series: pd.Series) -> pd.Series:
        s = series.astype(str).str.strip()
        out = s.where(s.str.len() > 0, other=None)
        return out.where(out.str.match(r"^\d{4}-\d{2}-\d{2}$"), other=out)

    out = pd.DataFrame({
        "code": bare_code(bs_code.split(".")[1]),
        "bs_code": bs_code,
        "announce_year": int(year),
        "plan_announce_date": _date(raw[PLAN_ANNOUNCE_COL]),
        "agm_date": _date(raw.get("dividAgmPumDate", pd.Series(dtype=str))),
        "operate_date": _date(raw.get(OPERATE_COL, pd.Series(dtype=str))),
        "pay_date": _date(raw.get("dividPayDate", pd.Series(dtype=str))),
        "cash_ps_before_tax": [resolve_cash(v, d)[0] for v, d in
                               zip(raw[CASH_BEFORE_TAX_COL].tolist(),
                                   raw.get("dividCashStock",
                                           pd.Series([""] * len(raw))).tolist())],
        "cash_status": [resolve_cash(v, d)[1] for v, d in
                        zip(raw[CASH_BEFORE_TAX_COL].tolist(),
                            raw.get("dividCashStock",
                                    pd.Series([""] * len(raw))).tolist())],
        # 税后值**只留档**：实测形如 "0.288或0.32"，不可解析 ⇒ 不进计算。
        "cash_ps_after_tax_raw": raw.get(CASH_AFTER_TAX_COL,
                                         pd.Series([""] * len(raw))).astype(str),
        "stock_ps": raw.get("dividStocksPs", pd.Series([""] * len(raw))).astype(str),
        "reserve_to_stock_ps": raw.get("dividReserveToStockPs",
                                       pd.Series([""] * len(raw))).astype(str),
        "cash_stock_desc": raw.get("dividCashStock",
                                   pd.Series([""] * len(raw))).astype(str),
        "collected_at": collected_at,
    })
    out = _resolve_one_row_per_plan(out)
    return out[EVENT_COLUMNS]


def _resolve_one_row_per_plan(out: pd.DataFrame) -> pd.DataFrame:
    """**一个方案只留一行。**

    ## 为什么必须做（实测踩到）

    源站把**同一个分红方案返回两遍**：一遍 `dividCashPsBeforeTax = "0.075"`，
    一遍该字段**为空**（描述文字完全相同：`10派0.75元（含税，扣税后0.675或0.75元）`）。
    实测 50 只 × 8 年样本里命中 **2 例**（002403 / 300261 的 2021 年度方案）。

    🚨 **不去重 ⇒ TTM 分子重复计入** ⇒ 股息率虚高 ⇒ 因子被高估。
    🚨 而其中一行还是 NaN ⇒ `sum(min_count=1)` 会跳过它 ⇒ 数字**看着正常**。

    ## 合并规则

    - 金额：**优先取可解析的那个**；
    - 若多个**不同**的可解析值 ⇒ 取**最小**（保守：不夸大股息率），
      冲突数由 `n_same_plan_amount_conflicts` 报出来；
    - 其余字段：取第一个非空。
    """
    if out.empty:
        return out
    rows = []
    n_conflicts = 0
    for _, g in out.groupby(EVENT_KEY, dropna=False, sort=False):
        parsed = g.loc[g["cash_status"] == CASH_PARSED, "cash_ps_before_tax"]
        if len(parsed):
            amount = float(parsed.min())
            status = CASH_PARSED
            if parsed.nunique(dropna=False) > 1:
                # 同一个方案、两个**不同**的可解析金额 ⇒ 源站有两版。
                # 取**最小**（保守：不夸大股息率），并把冲突数报出来。
                n_conflicts += 1
        elif (g["cash_status"] == CASH_NO_CASH).any():
            amount, status = 0.0, CASH_NO_CASH
        else:
            amount, status = None, CASH_UNREADABLE
        first = g.iloc[0].to_dict()
        first["cash_ps_before_tax"] = amount
        first["cash_status"] = status
        for col in ("agm_date", "pay_date", "cash_ps_after_tax_raw",
                    "stock_ps", "reserve_to_stock_ps", "cash_stock_desc"):
            if pd.isna(first.get(col)) or str(first.get(col) or "").strip() == "":
                nz = g[col].dropna()
                nz = nz[nz.astype(str).str.strip() != ""]
                if len(nz):
                    first[col] = nz.iloc[0]
        rows.append(first)
    out2 = pd.DataFrame(rows)
    out2.attrs["n_amount_conflicts"] = int(n_conflicts)
    return out2


# ── 宇宙与采集 ──────────────────────────────────────────────────────

def universe(daily_dir: Path | str = DAILY_DIR) -> dict[str, str]:
    """`{裸6位代码: baostock代码}`。

    **身份用裸 6 位**（与 `aplus/factor_fundamental.bare_code` 同一约定），
    请求用 baostock 格式。两边不同格式 ⇒ merge 零行 ⇒ 因子全 NaN
    而**不报错**（`aplus/factor_fundamental.py::bare_code` 的实测记录）。

    ⚠️ 单射校验：两个不同 stem 剥成同一个裸码时**炸**，
    不许让两所的票挤进同一个身份里（丢掉哪只取决于排序，不可复现）。
    """
    out: dict[str, str] = {}
    for p in sorted(Path(daily_dir).glob("*.parquet")):
        stem = p.stem
        code = bare_code(stem)
        bs_code = baostock_code(stem)
        if out.setdefault(code, bs_code) != bs_code:
            raise ValueError(f"去掉交易所后缀后出现重码：{out[code]} 与 {bs_code}")
    return out


def collect(pool: dict[str, str], root: Path, day: str,
            years: tuple[int, ...] = DEFAULT_YEARS,
            sleep: float = SLEEP_BETWEEN_CALLS,
            fetcher=None) -> dict:
    """抓一轮并落盘。**只写 `day` 那一份分区**（`aplus/vintage` 的铁律）。

    ## 两种「没有」必须分开记账

    | 情形 | status | 语义 |
    |---|---|---|
    | 源站说那年没有方案 | `empty` | **真的没有分红** |
    | 源站报错 / 网络失败 | `error:...` | **没查成** ⇒ 该 (票, 年) 是**缺数据** |

    🚨 把两者合并 ⇒ 没查成的票会变成「零股息」，
    而零股息在选股里**看起来像一个合法的低分值** ⇒ 前视 + 主动编造。
    ⇒ `failed` 单独记账，且**下游按「缺数据」处理**。

    ## 串行

    循环里**没有并发**：一次一个请求 + 强制 sleep。
    `tests/test_dividend_pit.py` 用假 fetcher 量最大并发数，钉成 1。
    """
    root = Path(root)
    part = vintage.snapshot_path(root, day)
    part.parent.mkdir(parents=True, exist_ok=True)

    # 真·源站：**先登录**。未登录时每次查询都返回错误码 ⇒
    # 几百次调用会**全部失败**而日志看着像「跑完了」（本轮踩到：22 秒 400 个失败）。
    # 注入假 fetcher 时不碰网络（测试不许联网）。
    if fetcher is None:
        ensure_login()

    have = pd.DataFrame()
    if part.is_file():                      # 续跑：先读回已完成的
        have = pd.read_parquet(part)
        # 🚨 **schema 演进**：老分区可能没有 `cash_status` 这一列
        # （本管道 2026-10-21 改过方案级去重）。**不许因为缺列就丢掉老数据**，
        # 也不许让缺列一路传下去（下游会读到 NaN 的状态）。
        # ⇒ 从已有的金额与描述**重新推**出来。
        if not have.empty and "cash_status" not in have.columns:
            have["cash_status"] = [
                resolve_cash(v, d)[1] for v, d in
                zip(have["cash_ps_before_tax"], have["cash_stock_desc"])]
        print(f"  续跑：已有 {len(have)} 行", flush=True)

    chunks: list[pd.DataFrame] = []
    failed: list[dict] = []
    empty_cells: list[str] = []
    n_calls = 0
    n_amount_conflicts = 0
    t0 = time.time()
    call_times: list[float] = []

    total = len(pool) * len(years)
    done = 0
    for code, bs_code in pool.items():
        for year in years:
            t1 = time.time()
            raw, status = fetch_code_year(bs_code, year, fetcher=fetcher)
            call_times.append(time.time() - t1)
            n_calls += 1
            done += 1
            if status.startswith("error:"):
                failed.append({"code": code, "bs_code": bs_code,
                               "year": int(year), "status": status})
            elif status == "empty":
                empty_cells.append(f"{code}:{year}")
            else:
                chunks.append(normalize_events(
                    raw, bs_code, year,
                    collected_at=datetime.now(timezone.utc).isoformat(
                        timespec="seconds")))
                n_amount_conflicts += int(
                    chunks[-1].attrs.get("n_amount_conflicts", 0))
            if done % 200 == 0 or done == total:
                print(f"  {done}/{total} 次调用  {time.time()-t0:.0f}s"
                      f"  失败 {len(failed)}", flush=True)
            if sleep:
                time.sleep(sleep)

    frames = [f for f in ([have] + chunks) if f is not None and not f.empty]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=EVENT_COLUMNS)
    if not df.empty:
        # 跨调用重复（续跑失效 / 源站重复）：**后写胜出**，并计数。
        before = len(df)
        df = (df.sort_values(EVENT_KEY, kind="stable")
                .drop_duplicates(subset=EVENT_KEY, keep="last")
                .reset_index(drop=True))
        dup_dropped = before - len(df)
    else:
        dup_dropped = 0

    return {
        "df": df,
        "failed": failed,
        "empty_cells": empty_cells,
        "dup_dropped": dup_dropped,
        "n_calls": n_calls,
        "n_amount_conflicts": n_amount_conflicts,
        "seconds": time.time() - t0,
        "call_times": call_times,
    }


# ── 读侧：事件层 PIT ────────────────────────────────────────────────

def pit_dividend_frame_from_events(events: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """`pit_dividend_frame` 的纯函数内核（不碰文件系统）。**过滤规则只有这一份。**

    拆出来有两个理由：
    1. 让「自证」可以在**内存里**跑（不必反复读盘）；
    2. 🚨 保证**自证用的过滤规则与生产用的过滤规则是同一份代码** ——
       另写一份过滤，自证就可能自证了一个**没被使用**的规则，
       而那种自证照样全绿（第 7 层 / 第 182 层：两份副本之间没有守卫）。

    ## 可见性规则（只有一条）

        `plan_announce_date` **存在** 且 **≤ as_of**

    🚨 **公告日缺失的记录一律排除**，不是当成「早就知道了」。
    空日期若当成「D 之前已公告」⇒ 那条记录在 D 之前就带着**未来的金额**出现 ⇒ 前视。
    🚨 **不许用 `fillna("")` 之类把缺失压成最小值** ——
    空串在字符串比较里小于任何日期，正好会**通过** `≤ as_of` 这道闸。
    """
    if events is None or events.empty:
        return events
    anchor = events["plan_announce_date"]
    visible = anchor.notna() & (anchor.astype(str) <= str(as_of))
    return events.loc[visible].copy().reset_index(drop=True)


def load_events(root: Path | str = DIVIDEND_ROOT) -> pd.DataFrame:
    """读**全部**快照分区，拼成事件表。**跨采集日去重，后写胜出。**

    为什么读全部而不是最后一份：事件库是**追加式**的 ——
    第 1 天采了 A/B 两只，第 2 天采了 C/D 两只，
    只读最后一份会把 A/B **丢掉** ⇒ 因子在某些票上凭空变缺。
    """
    root = Path(root)
    frames = []
    for day in vintage.available_days(root):
        f = pd.read_parquet(vintage.snapshot_path(root, day))
        if "snapshot_date" not in f.columns:
            f["snapshot_date"] = day
        frames.append(f)
    if not frames:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df = (df.sort_values(EVENT_KEY, kind="stable")
            .drop_duplicates(subset=EVENT_KEY, keep="last")
            .reset_index(drop=True))
    return df


def pit_dividend_frame(as_of: str,
                       root: Path | str = DIVIDEND_ROOT) -> pd.DataFrame:
    """`as_of` 那天的**分红 PIT 帧** —— 事件层的唯一入口。

    ## 可见性规则（只有一条）

        `plan_announce_date` **存在** 且 **≤ as_of**

    🚨 **公告日缺失的记录一律排除**，不是当成「早就知道了」。
    空日期若当成「D 之前已公告」⇒ 那条记录在 D 之前就带着**未来的金额**出现 ⇒ 前视。
    🚨 **不许用 `fillna("")` 之类把缺失压成最小值** ——
    空串在字符串比较里小于任何日期，正好会**通过** `≤ as_of` 这道闸。

    ## ⚠️ 这个帧里的金额是「**今天读到的**金额」，不是「当时公告的金额」

    见模块 docstring 的「两条腿」。要「当时读到的」用
    `vintage.pit_frame(root, as_of)`（需 `as_of` ≥ 首次采集日）。
    """
    df = load_events(root)
    if df.empty:
        return df
    # 🚨 **过滤规则只有一份实现**（`pit_dividend_frame_from_events`）。
    # 自证若用另一份拷贝，就可能自证了一个**生产根本没在用**的规则 ——
    # 而那种自证照样全绿（第 7 层 / 第 182 层：两份副本之间没有守卫）。
    out = pit_dividend_frame_from_events(df, as_of)
    out.attrs["as_of"] = str(as_of)
    out.attrs["n_events_total"] = int(len(df))
    out.attrs["n_events_visible"] = int(len(out))
    return out


def ttm_cash_dividend(events: pd.DataFrame, as_of: str,
                       covered_codes: set[str] | None = None,
                       codes: set[str] | None = None) -> pd.DataFrame:
    """截至 `as_of`、**近 12 个月**已实施的每股税前现金分红合计。

    ## 归属规则（跨年问题在这里定死）

    **归属 = 除权除息日（`operate_date`）落在滚动窗口 `(as_of − 365d, as_of]` 内。**
    🚨 **与方案所属的会计年度无关。**
    「2025 年度方案在 2026 年公告」⇒ 它的除权除息日在 2026 年中，
    于是它在 2026 年的窗口里、**不在 2025 年的窗口里**。
    这正是为什么不能用「财年」分桶：财年会把一条 2026 年除权的方案
    记到 2025 年去，而 2025 年那天**这个方案还没公告** ⇒ 前视。

    ⚠️ 窗口是**精确 365 天**，不是「上一个自然年」。
    跨闰年时两者差一天（2020-06-15 → 2021-06-15 正好 365 天，
    而 2020-03-01 → 2021-03-01 是 366 天）。**口径写死，不许含糊。**

    ## 三态（缺数据与零必须分开）

    | status | 含义 |
    |---|---|
    | `observed` | 窗口里确实有分红 ⇒ 数值是实测和 |
    | `collected_no_payout_in_window` | 采到了、这票有分红历史、但窗口里没分红 ⇒ **0 是事实** |
    | `not_collected` | **没采到** ⇒ **NaN，不是 0** |

    🚨 **写 0 就是编造**：0 股息率在横截面里**看起来像一个合法的低分值**，
    而真实语义是「没查到」⇒ 它会被排序逻辑当成「零股息股」处理。

    ## 两个集合参数，一个是「谁采到了」，一个是「要报告谁」

    - `covered_codes`：**采到了**的票。`None` ⇒ 事件表里出现过的票都算采到了。
    - `codes`：**输出域**（要报告哪些票）。`None` ⇒ `covered_codes ∪ 事件表里的票`。

    🚨 **两者不能合成一个**：因子要在**全池**上出数，
    而「池里有这只票」与「这只票采到了」是**两个事实** ——
    只报 `covered_codes` ⇒ 没采到的票**从横截面里消失**，
    而它们恰恰是**高股息候选**（银行/煤炭这类最容易被采到），
    ⇒ 采样偏差会**系统性抬高**样本的平均股息率。
    """
    as_of_dt = pd.Timestamp(str(as_of))
    win_lo = as_of_dt - pd.Timedelta(days=TTM_DAYS)

    covered = set(covered_codes) if covered_codes is not None else None
    event_codes = set() if events is None or events.empty else \
        set(events["code"].astype(str).unique())
    if codes is not None:
        domain = {str(c) for c in codes}
    elif covered is not None:
        domain = set(covered) | event_codes
    else:
        domain = set(event_codes)
    domain = sorted(domain)

    if events is None or events.empty:
        return pd.DataFrame({
            "code": domain,
            "ttm_cash_ps": [float("nan")] * len(domain),
            "status": [STATUS_NOT_COLLECTED] * len(domain),
            "n_plans_in_window": [0] * len(domain),
        })

    ev = events.copy()
    ev["code"] = ev["code"].astype(str)
    op = ev["operate_date"]
    in_win = op.notna() & (pd.to_datetime(op, errors="coerce") > win_lo) & \
             (pd.to_datetime(op, errors="coerce") <= as_of_dt)
    sub = ev.loc[in_win]
    sums = sub.groupby("code")["cash_ps_before_tax"].sum(min_count=1)
    counts = sub.groupby("code").size()
    # 🚨 **窗口里只要有一条分红的金额读不出来，这只票的 TTM 就是「不知道」。**
    # `sum(min_count=1)` 会**跳过** NaN ⇒ 报出来的数是**部分和**，
    # 而它**低于真值** ⇒ 股息率**偏低** ⇒ 高股息票被系统性看低
    # ⇒ 这个方向的偏差会**掩盖**因子的效果。
    # ⇒ 部分和**不许**当数报出来 ⇒ NaN + `unreadable_cash`。
    unreadable = set(sub.loc[sub["cash_status"] == CASH_UNREADABLE, "code"])

    has_history = event_codes
    rows = []
    for c in domain:
        if covered is not None and c not in covered:
            rows.append((c, float("nan"), STATUS_NOT_COLLECTED, 0))
            continue
        if c in unreadable:
            rows.append((c, float("nan"), STATUS_UNREADABLE_CASH,
                         int(counts.get(c, 0))))
            continue
        n = int(counts.get(c, 0))
        if c in sums.index:
            rows.append((c, float(sums[c]), STATUS_OBSERVED, n))
        elif c in has_history:
            rows.append((c, 0.0, STATUS_COLLECTED_NO_PAYOUT, 0))
        else:
            # 采到了这只票，但**一条分红记录都没有** ⇒ 分不清
            # 「这票从不分红」与「源站压根没这条票的数据」⇒ **仍然算缺数据**。
            # 编 0 会让它看起来像「零股息」⇒ 被动持有。
            rows.append((c, float("nan"), STATUS_NOT_COLLECTED, 0))
    return pd.DataFrame(rows, columns=["code", "ttm_cash_ps", "status",
                                        "n_plans_in_window"])


def dividend_yield(events: pd.DataFrame, closes, as_of: str,
                   covered_codes: set[str] | None = None) -> pd.DataFrame:
    """股息率 = **TTM 每股税前现金分红 / 不复权收盘价**。

    ## 分母为什么是**不复权** close

    - 分子是**名义现金**（元/股）。配后复权价 ⇒ 分母被历史分红抬高
      ⇒ 收益率被系统性压低，而那个抬高与「今天这家公司多便宜」无关。
    - 与全市场口径一致：沪深 300 股息率 = 近 12 月分红总额 / **当前总市值**
      ⇒ 分母是**当前**价格，不是历史调整价。
    - 送股会同时改价与「每股口径」，这与 `aplus/factor_fundamental.py` 里
      BM 用不复权价是**同一个理由**（那里逐字写着「用后复权价会让一次送股
      凭空改变 BM」）。

    ## 🚨 缺数据 ⇒ NaN，不是 0

    `ttm_cash_dividend` 的三态原样透出。**`dividend_yield` 绝不把缺数据压成 0**。

    ## 输出域 = **价格域**（`closes` 的键）∪ 采集域 ∪ 事件表

    🚨 **输出域必须是全池，不是「采到了的那些」** ——
    否则没采到的票会**从横截面里消失**，而它们恰恰是高股息候选
    （银行/煤炭这类最容易被采到）⇒ 采样偏差会**系统性抬高**样本的平均股息率。
    ⇒ 没采到的票**必须出现**，值是 NaN。

    `closes` 可以是 `pd.Series`（索引 = 代码，值 = `as_of` 当天的**不复权**收盘价）
    或 `{代码: 价}` 映射。**代码两种格式都收**（`000001.SZ` 与 `000001`），
    内部统一走 `bare_code`。
    """
    if isinstance(closes, pd.Series):
        close_map = {bare_code(k): float(v) for k, v in closes.items()}
    else:
        close_map = {bare_code(k): float(v) for k, v in dict(closes).items()}
    domain = set(close_map)
    if covered_codes is not None:
        domain |= {str(c) for c in covered_codes}
    if events is not None and not events.empty:
        domain |= set(events["code"].astype(str).unique())

    ttm = ttm_cash_dividend(events, as_of, covered_codes=covered_codes,
                            codes=domain)
    ttm["close"] = ttm["code"].map(close_map)
    ttm["dividend_yield"] = ttm["ttm_cash_ps"] / ttm["close"]
    # **缺数据仍是 NaN**：close 缺失 ⇒ NaN；ttm 缺失 ⇒ NaN。
    # 只有两边都齐才是数。
    ttm.loc[ttm["close"].isna() | ttm["ttm_cash_ps"].isna(),
            "dividend_yield"] = float("nan")
    return ttm


# ── PIT 自证（跑在真实采到的数据上，不是夹具）────────────────────────

def pit_visibility_self_proof(events: pd.DataFrame) -> dict:
    """在**真实采到的**事件上自证 PIT 可见性。

    对每一条事件取 `A = plan_announce_date`：
    - 查 `D = A` ⇒ 该条**必须可见**（正向对照：否则守卫是恒真的装饰）；
    - 查 `D = A − 1 天` ⇒ 该条**必须不可见**（这才是要证明的那一条）。

    ⚠️ 为什么要两条都要：**只查「不可见」会假绿** ——
    如果过滤器把所有东西都滤掉，「D−1 不可见」照样成立。
    ⇒ 正向对照是判据能不能用的**能力守卫**（形状 120）。
    """
    if events is None or events.empty:
        return {"ok": False, "reason": "no_events", "n_checked": 0}
    ev = events.dropna(subset=["plan_announce_date"]).copy()
    ev["plan_announce_date"] = ev["plan_announce_date"].astype(str)
    if ev.empty:
        return {"ok": False, "reason": "no_anchor", "n_checked": 0}

    n_checked = 0
    visible_at_anchor = 0
    invisible_before_anchor = 0
    failures: list[dict] = []
    for _, row in ev.iterrows():
        a = row["plan_announce_date"]
        before = (pd.Timestamp(a) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        n_checked += 1
        at = pit_dividend_frame_from_events(ev, a)
        ok_at = len(at.loc[at["plan_announce_date"].astype(str) == a]) > 0
        bef = pit_dividend_frame_from_events(ev, before)
        ok_bef = len(bef.loc[bef["plan_announce_date"].astype(str) == a]) == 0
        visible_at_anchor += int(ok_at)
        invisible_before_anchor += int(ok_bef)
        if not (ok_at and ok_bef) and len(failures) < 10:
            failures.append({"code": row.get("code"), "anchor": a,
                             "visible_at_anchor": ok_at,
                             "invisible_before_anchor": ok_bef})
        if n_checked >= 200:      # 抽样上限：自证要快，不许变成全量扫描
            break
    return {
        "ok": bool(n_checked) and visible_at_anchor == n_checked
        and invisible_before_anchor == n_checked,
        "n_checked": n_checked,
        "visible_at_anchor": visible_at_anchor,
        "invisible_before_anchor": invisible_before_anchor,
        "failures": failures,
    }


def _n_multi_amount_plans(events: pd.DataFrame) -> int:
    """**落盘后还剩下几个「一个方案多行」的组。**

    正常情况下必须是 **0** —— 方案级去重在 `normalize_events` 里已经做完。
    这个数不为 0 ⇒ 要么去重没生效，要么**跨采集日**来了两版
    （后者是**修订轨迹**，是信号不是缺陷）。
    """
    if events is None or events.empty:
        return 0
    g = events.groupby(EVENT_KEY, dropna=False).size()
    return int((g > 1).sum())


# ── CLI ─────────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="股息率 PIT 采集")
    p.add_argument("--root", default=str(DIVIDEND_ROOT),
                   help="分红 PIT 快照根（默认仓内 data/reports/dividend_pit）")
    p.add_argument("--day", default=None,
                   help="快照分区日期（默认今天）")
    p.add_argument("--years", default=",".join(str(y) for y in DEFAULT_YEARS),
                   help="预案公告年份，逗号分隔")
    p.add_argument("--max-codes", type=int, default=None,
                   help="只采前 N 只（按代码序）")
    p.add_argument("--codes", default=None,
                   help="逗号分隔的裸 6 位代码（白名单）")
    p.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_CALLS)
    p.add_argument("--out", default=None, help="覆盖产物路径")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印成本账，不采")
    return p.parse_args(argv)


def cost_account(n_codes: int, n_years: int, per_call_s: float,
                 sleep_s: float) -> dict:
    """全池成本账。**先算再采**（本仓铁律：采集前把账算出来）。

    公式：`n_codes × n_years × (per_call_s + sleep_s)`。
    ⚠️ `per_call_s` 必须是**实测**值（不是估计），否则账就是编的。
    """
    n_calls = n_codes * n_years
    per = per_call_s + sleep_s
    return {
        "n_codes": int(n_codes),
        "n_years": int(n_years),
        "n_calls": int(n_calls),
        "per_call_measured_s": float(per_call_s),
        "sleep_between_calls_s": float(sleep_s),
        "per_call_total_s": float(per),
        "estimated_seconds": float(n_calls * per),
        "estimated_hours": float(n_calls * per / 3600.0),
        "baostock_daily_call_cap_note": (
            "baostock 有日调用上限且禁并发；本估算按**单日跑完全池**计算，"
            "若超过日上限必须跨日分批（每日一个 date= 分区，追加式，不冲突）。"),
    }


def main(argv=None) -> int:
    args = _parse_args(argv)
    # 第 125 层：**开跑时的字节**。盖章时比对，不一致就**拒绝盖章**。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    root = Path(args.root)
    day = args.day or datetime.now().strftime("%Y-%m-%d")
    years = tuple(int(y) for y in args.years.split(",") if y.strip())

    pool_all = universe()
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        pool = {c: v for c, v in pool_all.items() if c in want}
        missing = want - set(pool)
        if missing:
            print(f"❌ 池里没有这些代码：{sorted(missing)}")
            return 2
    else:
        pool = pool_all
    if args.max_codes:
        pool = dict(list(pool.items())[:args.max_codes])

    if args.dry_run:
        # ⚠️ 这里的 0.173 是**默认假设值**，不是实测值。
        # **权威数字在覆盖产物的 `::cost_account` 里**（它用当次跑出来的
        # `per_call_mean_s` 算）⇒ 决策时**不要**用这个 dry-run 的数。
        acct = cost_account(len(pool), len(years), 0.173, args.sleep)
        acct["per_call_is_assumed_not_measured"] = True
        acct["authoritative_number"] = (
            "覆盖产物的 `::cost_account.full_pool`（用当次实测的 "
            "`::scope.per_call_mean_s` 算）")
        print(json.dumps(acct, ensure_ascii=False, indent=1))
        return 0

    print(f"采集 {day} 的分红 PIT：{len(pool)} 只 × {len(years)} 年 "
          f"= {len(pool) * len(years)} 次调用（串行，禁并发）", flush=True)
    res = collect(pool, root, day, years=years, sleep=args.sleep)
    df = res["df"]

    if df.empty and res["failed"]:
        print(f"❌ 全部失败（{len(res['failed'])} 次）——不落盘")
        return 1

    # ⚠️ **落盘走 `aplus.vintage.write_snapshot`**，不自己 `to_parquet`：
    # 它负责加 `snapshot_date` 列、写**追加式 manifest 账本**、记内容摘要
    # ⇒ `vintage.verify_integrity` 能自证「这份数据没被动过」。
    # 自己 `to_parquet` 的话，产物**对完整性校验是不可见的**。
    #
    # 🚨 **但「小样本」这件事必须显式记下来**：manifest 的 `n_rows` 只说
    # 「这一份有多少行」，不说「这一份覆盖了全池还是 50 只」。
    # ⇒ 另写 `_scope.json` 把**采集域**（哪些票 × 哪些年）与失败单元钉死。
    # 财报 vintage 的规矩是「半份快照不许登记 manifest」（那里半份会冒充全池）；
    # **分红是事件库，半份不冒充全池**（缺的就是缺，事件是加性的）⇒
    # 登记 manifest + 记 scope 才是对的。
    partial = bool(args.max_codes or args.codes)
    part = vintage.write_snapshot(root, day, df,
                                  dup_dropped=res["dup_dropped"])
    scope = {
        "day": day,
        "partial": partial,
        "n_codes": len(pool),
        "codes": sorted(pool),
        "years": list(years),
        "n_cells": len(pool) * len(years),
        "failed_cells": res["failed"],
        "empty_cells_no_dividend": len(res["empty_cells"]),
        "note": ("这一份的**采集域**是上面这些票 × 上面这些年，"
                 "**不是全池**。读它的人必须先看这份 scope。"),
    }
    (part.parent / "_scope.json").write_text(
        json.dumps(scope, ensure_ascii=False, indent=1), encoding="utf-8")
    if res["failed"]:
        (part.parent / "_failed.json").write_text(
            json.dumps(res["failed"], ensure_ascii=False, indent=1),
            encoding="utf-8")
    if partial:
        print(f"  小样本：已落盘 {part}（采集域见 _scope.json）")

    # ── 覆盖产物 ──────────────────────────────────────────────────
    ev = load_events(root)
    proof = pit_visibility_self_proof(ev)
    per_call = (sum(res["call_times"]) / len(res["call_times"])
                if res["call_times"] else 0.0)
    max_call = max(res["call_times"]) if res["call_times"] else 0.0

    covered = {c for c in pool if not any(f["code"] == c for f in res["failed"])}
    # 覆盖率必须**按 (票, 年) 单元**算，不是按票算：
    # 一只票 8 年里失败 1 年 ⇒ 它的 TTM 分子**可能缺一条** ⇒ 不能算「采到了」。
    failed_cells = {(f["code"], f["year"]) for f in res["failed"]}
    cell_total = len(pool) * len(years)
    cell_ok = cell_total - len(failed_cells)

    out = Path(args.out) if args.out else (
        ROOT / "data" / "reports" / "dividend_pit_coverage_2026-10-21.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generator": GENERATOR,
        "day": day,
        "scope": {
            "n_codes": len(pool),
            "n_years": len(years),
            "years": list(years),
            "partial": partial,
            "n_calls": res["n_calls"],
            "seconds": res["seconds"],
            "per_call_mean_s": per_call,
            "per_call_max_s": max_call,
            "serial_no_concurrency": True,
            "sleep_between_calls_s": args.sleep,
        },
        "coverage": {
            "n_events": int(len(ev)),
            "n_codes_with_events": int(ev["code"].nunique()) if not ev.empty else 0,
            "cells_total": cell_total,
            "cells_ok": cell_ok,
            "cells_failed": len(failed_cells),
            "cells_empty_no_dividend": len(res["empty_cells"]),
            "cell_coverage_pct": (cell_ok / cell_total) if cell_total else None,
            "failed_cells": res["failed"][:200],
            "n_failed_codes": len({f["code"] for f in res["failed"]}),
            # **完全采到的票**（它所有年份的单元都成功）——
            # 与「有分红事件的票」是**两个数**（前者是覆盖，后者是数据）。
            "n_codes_fully_covered": len(covered),
            "dup_dropped": res["dup_dropped"],
            "events_missing_announce_date": int(
                ev["plan_announce_date"].isna().sum()) if not ev.empty else 0,
            "events_missing_operate_date": int(
                ev["operate_date"].isna().sum()) if not ev.empty else 0,
            "events_unparsable_cash": int(
                (ev["cash_status"] == CASH_UNREADABLE).sum())
            if not ev.empty else 0,
            "events_no_cash_plan": int(
                (ev["cash_status"] == CASH_NO_CASH).sum())
            if not ev.empty else 0,
            "cash_status_counts": (
                ev["cash_status"].value_counts().to_dict() if not ev.empty else {}),
            # 同一个 (票, 公告日, 除权日) 出现**不同金额** ⇒ 源站有两版。
            # 这不是错误，是**修订轨迹的线索**（也可能是源站的重复行）。
            # 同一个方案、两个**不同**的可解析金额 ⇒ 源站有两版（取最小）。
            "n_same_plan_amount_conflicts": res["n_amount_conflicts"],
            # 落盘后应当**一个方案一行**（源站会把同一方案返回两遍）。
            "n_plan_identity_duplicates_left": _n_multi_amount_plans(ev),
        },
        "pit_anchor": {
            "field": PLAN_ANNOUNCE_COL,
            "field_cn": "预案公告日",
            "rule": "plan_announce_date 存在 且 <= as_of",
            "why": ("分红有「预案公告日」与「除权除息日」两个日期；"
                    "信号日 D 只应看得见 D 之前已公告的方案 ⇒ 锚必须是公告日。"
                    "用除权日当锚会把整个方案往后推一个季度。"),
            "rejected_field": "dividPreNoticeDate（预披露日）",
            "why_rejected": ("源站没有任何字段能说明「金额在预披露日是否已公开」；"
                             "实测样本里该列 74/74 全空。用它当锚 = 猜，"
                             "而 PIT 错的方向是乐观的。"),
        },
        "yield_caliber": {
            "formula": "dividend_yield(D) = TTM_cash_ps(D) / close_unadjusted(D)",
            "numerator": ("近 12 个月内**已实施**（除权除息日落在 (D-365, D]）的"
                          "每股**税前**现金分红合计；"
                          "PIT 过滤：plan_announce_date <= D"),
            "numerator_source_field": CASH_BEFORE_TAX_COL,
            "tax": ("**税前**。理由：(a) 源站区分税前/税后，而 A 股红利税是"
                    "**持股期限**相关的差别税率（持股 ≤1 月 20%，1 月–1 年 10%，"
                    ">1 年免征）⇒ 税后口径引入一个与股价无关的持股期变量；"
                    "(b) 实测税后字段形如 '0.288或0.32'，**不可解析**；"
                    "(c) 本项目是**选股**不是算到手多少钱。"
                    "⚠️ 税前 ≠ 到手，引用时必须带这句话。"),
            "denominator": ("**不复权** close（D 当天）。理由：分子是名义现金，"
                            "后复权价被历史分红抬高 ⇒ 收益率被系统性压低；"
                            "且与全市场口径（近 12 月分红总额 / 当前总市值）一致。"),
            "window": "滚动 365 天，按**除权除息日**归集；与方案所属会计年度无关",
            "cross_year_rule": ("「2025 年度方案在 2026 年公告」⇒ 除权日在 2026 年，"
                                "于是它落在 2026 年的窗口、**不在 2025 年的窗口**。"
                                "用财年分桶会把一条 2026 年除权的方案记到 2025 年，"
                                "而 2025 年那天它还没公告 ⇒ 前视。"),
            "missing_data_rule": ("三态：observed / collected_no_payout_in_window / "
                                   "not_collected。**缺数据一律 NaN，不许编 0** —"
                                   "编 0 会让它看起来像「零股息」⇒ 被动持有。"),
        },
        "pit_self_proof": proof,
        "cost_account": {
            "sample": cost_account(len(pool), len(years), per_call, args.sleep),
            "full_pool": cost_account(len(pool_all), len(years), per_call,
                                      args.sleep),
            "decision": ("**本轮只跑小样本**；全池规模由决策者按 cost_account 决定。"
                         "🚨 本轮**不许**擅自跑全池 5,460 只。"),
        },
        "freshness_note": (
            "本产物**没有**登记进 `aplus/provenance.py::DEFAULT_GENERATORS`"
            "（本轮不许改 `aplus/` 下任何模块）⇒ `provenance.freshness()`"
            "**不传 generator 会判 `unknown`**。"
            "⇒ 验新鲜度必须**显式传生成器**："
            "`provenance.freshness('data/reports/dividend_pit_coverage_2026-10-21.json', "
            "generator='scripts/collect_dividend_pit.py')`。"
            "🚨 `unknown` 与「数据坏了」长得一模一样 ⇒ 不传就等于没验。"),
        "what_this_cannot_prove": [
            "本产物只证明**数据管道**的覆盖率与 PIT 可见性，不证明因子有效。",
            "**不许**把石川书里的多空数（IVOL 等权月均超额 1.27%，t=8.64，"
            "cn_factor_lines.txt:9885）当成本项目的可达目标 —— 空腿散户做不了。",
            "事件层读到的金额是「**今天读到的**」，不等于「当时公告的**」；"
            "「当时读到的」要靠 `aplus.vintage` 的 date= 分区逐日累积（从今天起）。",
            "覆盖率低 ⇒ 因子在**未覆盖的票**上是**缺数据**，不是零股息；"
            "低覆盖率下的横截面排名会被系统性偏置（银行/煤炭这类高股息票最容易被采到）。",
        ],
        "gaps": [
            "本轮**未**采全池（5,460 只）：只采了小样本，全池成本账见 cost_account。",
            "分红 PIT 的**修订轨迹**从今天起才能观测（需要第二份快照）。",
        ],
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    # 溯源章。`data_root` = 本管道**读不到**本地日线（价格侧下一轮才接），
    # 所以章里的 data_fp 必须说清它量的不是「这次跑的输入」。
    provenance.stamp(
        out, GENERATOR, root=ROOT, data_root=root, daily_subdir="",
        expect_bytes=expect_bytes,
        extra={
            "day": day,
            "n_codes": len(pool),
            "n_years": len(years),
            "n_calls": res["n_calls"],
            "n_events": int(len(ev)),
            "partial_sample": partial,
            "data_fp_caliber": ("本脚本的输入是**抓取当时的 baostock 源站**；"
                                "章里的 data_fp 描述的是它**写入**的分红 PIT 存储，"
                                "不是它读进来的东西。价格侧（不复权 close）"
                                "本轮**没有**参与计算。"
                                "⚠️ `data_fp` 实测为 `null`：本存储是**分区式**"
                                "（parquet 在 `date=*/` 子目录里），"
                                "而 `provenance.data_fingerprint` 只扫一层 `*.parquet` "
                                "⇒ 它的内容摘要改由 `aplus.vintage` 的"
                                "**追加式 manifest** 承担"
                                "（`data/reports/dividend_pit/_manifest.jsonl::sha256`），"
                                "并由 `vintage.verify_integrity()` 逐份核对。"),
        })

    print(f"\n  事件 {len(ev)} 条 / {ev['code'].nunique() if not ev.empty else 0} 只")
    print(f"  单元覆盖 {cell_ok}/{cell_total} = "
          f"{(cell_ok / cell_total) if cell_total else 0:.4f}")
    print(f"  PIT 自证：{proof.get('ok')} "
          f"(查 {proof.get('n_checked')} 条，"
          f"锚日可见 {proof.get('visible_at_anchor')}，"
          f"锚日前不可见 {proof.get('invisible_before_anchor')})")
    print(f"  产物：{out}")
    print(f"  ⚠️ 本轮**未**跑全池；全池成本账见产物的 cost_account。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
