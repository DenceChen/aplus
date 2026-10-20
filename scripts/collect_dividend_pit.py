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

#: **单次运行的调用配额闸**（baostock 日限 5 万次，超限黑名单 6 小时起）。
#:
#: 🚨 **为什么是 15,000 而不是更多**：
#: 本机 18:30 的日行情采集（`com.aplus.daily` → `run_daily.py`）走 baostock，
#: 要占 **5,460 次**。若分红一次吃满 43,680 ⇒ 合计 49,140 ⇒ **逼近 5 万上限**
#: ⇒ 一次超限就**黑名单 6 小时** ⇒ **打坏 D1 的每日交付**。
#: ⇒ **D1 的交付优先级高于数据采集** ⇒ 留足余量。
#:
#: 15,000 + 5,460 = 20,460 ⇒ **不到日限的一半** ⇒ 安全余量充足。
#: ⇒ 43,680 / 15,000 ⇒ **约 3 天采完**（3 片，每片 1,820 只 × 8 年 = 14,560 次）。
#:
#: 🚨 **配额闸是「超了当场停」，不是「今天多跑点明天少跑」** ——
#: 超限的后果是黑名单，不是「明天补回来」。
MAX_CALLS_PER_RUN = 15_000

#: 分片根的目录名模板。**分片自己一个根**，互不冲突。
SHARD_DIR_TEMPLATE = "shard={i}-of-{n}"

#: 「当天新公告」层的根名（见模块 docstring 的「两条腿」）。
ANNOUNCED_ROOT_NAME = "announced"

#: **单元账**（断点续采的真相源）的文件名 —— **全年份范围的那一份**。
#:
#: 为什么必须有它：一只票在某年「没有分红方案」（源站返回 0 行）与
#: 「还没采到这一格」在落盘的数据里**长得一模一样** ⇒ 续跑时无法区分
#: ⇒ 会**重采**（浪费额度）或者**漏采**（数据缺口）。
#: ⇒ 逐 (票, 年) 记状态。
#:
#: 🚨 **为什么它与「每日当年扫描」的账分成两个文件名**（这是一件我差点搞错的事）：
#: 「这只票**采到了没有**」这个问题，答案取决于**采了几个年份**。
#: - `_cells.json` = **全年份范围**（8 年）的那一份 ⇒ 「这只票采到了」= 真命题；
#: - `_cells_sweep.json` = **只有当年**的那一份 ⇒ 「这只票采到了当年」，
#:   **不**等于「这只票的历史也采到了」。
#: ⇒ 若把两份混在一个名字下，消费者（因子脚本）会把「只采了当年」的票
#: 判成「完全覆盖」⇒ 在 2021 年那种**需要 2019–2021 三年**的信号日上，
#: 它会被算出一个**偏小的 TTM 股息率**却标成「已观测」⇒ **静默的覆盖不足**。
#: ⇒ **分开命名 ⇒ 覆盖域只从全年份那一份推导**（默认方向是「宁可排除」）。
CELLS_LEDGER_NAME = "_cells.json"

#: 「每日当年扫描」的单元账。**与全年份的账分开**（理由见上）。
CELLS_SWEEP_LEDGER_NAME = "_cells_sweep.json"

#: **分片进度账**（追加式）的文件名。记录「哪片、哪天、采了多少、完没完」。
SHARD_PROGRESS_NAME = "_shard_progress.jsonl"

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
#: 少采一年 ⇒ 判决窗口头一年的 TTM 分子少一条；多采一年 ⇒ 成本 +12.5%。
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

def parse_shard(spec: str) -> tuple[int, int] | None:
    """`'1/3'` → `(1, 3)`；`'auto'` → `None`（由 `pick_shard_auto` 决定）。

    🚨 **分片号是 1 起的**（人读的分片号），不是 0。
    混用 0 起与 1 起 ⇒ 「第 1 片」会变成「第二片」⇒ **采漏一片而没人喊**。
    """
    s = str(spec).strip().lower()
    if s == "auto":
        return None
    if "/" not in s:
        raise ValueError(f"分片参数应为 'i/n' 或 'auto'，收到 {spec!r}")
    a, _, b = s.partition("/")
    i, n = int(a), int(b)
    if n < 1 or not (1 <= i <= n):
        raise ValueError(f"分片号必须落在 1..{n}，收到 {spec!r}")
    return i, n


def shard_codes(codes: list[str], i: int, n: int) -> list[str]:
    """**确定性**切分：排序后按 `i` 取连续一段。

    ⚠️ **必须是确定性的** ⇒ 同一份代码清单 ⇒ 同一片 ⇒
    **可复现、可断点续采**（第 1 片采到一半被打断，第二天接着采的还是同一片）。
    ⚠️ 切分**依赖代码清单本身** ⇒ 清单变了，切分就变了 ⇒
    ⇒ 切分结果与「代码清单的哈希」一起记进进度账（见 `main`）。
    """
    if n < 1 or not (1 <= i <= n):
        raise ValueError(f"分片号必须落在 1..{n}，收到 {i}/{n}")
    codes = sorted(codes)
    lo = (i - 1) * len(codes) // n
    hi = i * len(codes) // n
    return codes[lo:hi]


def shard_root(root: Path | str, i: int, n: int) -> Path:
    """某一片的快照根：`root/shard=<i>-of-<n>/`。

    **每片一个根** ⇒ 每片有自己的 `date=` 分区与 manifest ⇒
    **互不冲突**（两个分片写同一个分区会互相覆盖）。
    """
    return Path(root) / SHARD_DIR_TEMPLATE.format(i=i, n=n)


def daily_root(root: Path | str) -> Path:
    """「当时读到的」层的根：`root/daily/`。

    每天一份**全池当年视图**（全部 5,460 只 × 当年的预案公告年份）：

    - **完整性**：当日覆盖**全池**，不像分片那样只有 1/5；
    - **修订可观测**：当天把每一条当年方案重读一遍 ⇒
      「预案 → 股东大会改额」在**第二天**就能比出来；
    - **代价**：5,460 次/天（见 `MAX_CALLS_PER_RUN` 的余量说明）。
    """
    return Path(root) / "daily"


def announced_root(root: Path | str) -> Path:
    """「当天新公告」层的根：`root/announced/`。

    每天一份**只含当天新公告的方案**的分区（`date=YYYY-MM-DD/financial.parquet`）。
    ⇒ 这是「当时读到的」那一层的**最锐利的一格**：
    「D 那天到底公告了什么、当天读到的金额是多少」。
    """
    return Path(root) / ANNOUNCED_ROOT_NAME


def announced_root_old(root: Path | str, i: int, n: int) -> Path:
    """「当天新公告」层的根：`root/announced/shard=<i>-of-<n>/`。

    ## 这一层是什么

    **「当时读到的」那一层**（模块 docstring 的「两条腿」）：

    - 事件层（`shard=*/date=*/financial.parquet` 的全量历史）里读到的金额是
      「**今天读到的**」，**不是**「当时公告的**」**（预案 → 股东大会可能改额）；
    - 这一层按**天**记「**当天新公告了哪些方案、当天读到的金额是多少**」
      ⇒ 从今天起逐日累积 ⇒ 将来能观测到「某条方案的金额在公告后被改过」。

    ⇒ **两条腿不许混引**：事件层的历史可回溯但带修订风险；
    这一层逐日忠实但只覆盖从今天开始的窗口。
    """
    return Path(root) / ANNOUNCED_ROOT_NAME / SHARD_DIR_TEMPLATE.format(i=i, n=n)


# ── 并发锁（baostock **禁并发**）─────────────────────────────────────
#: 锁文件。放在分红根下 ⇒ 每台机器一把。
LOCK_NAME = ".lock"


def _pid_alive(pid: int) -> bool:
    """PID 是否还活着。**只用 `kill -0`**（内置、不产生可被 `pgrep -f` 匹配的进程名）。"""
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _proc_cmdline(pid: int) -> str:
    """进程的完整命令行（**身份证据**，不只是 PID 活着）。"""
    import subprocess
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _same_script(recorded: str, live: str) -> bool:
    """活着的那个进程**是不是同一个脚本**（PID 复用防护）。

    🚨 **PID 存活 ≠ 身份**（本仓第 115 层：PID 是可复用资源，
    macOS 回收 PID 给无关进程 ⇒ KeepAlive 服务会永久停摆）。

    ⇒ 判据：活进程的命令行里必须含有**持锁时记录的那个脚本路径**。
    ⚠️ 不许只判 PID 活着：那会把「PID 被回收给别的进程」当成「还持有」。
    ⚠️ 也不许只判「命令行里含 collect_dividend_pit」——
    **测试进程**跑的就是 `tests/test_dividend_pit.py`，
    按前者判会把持锁者认成「已死」⇒ 并发锁在测试里形同虚设。
    """
    if not recorded or not live:
        return False
    scripts = [t for t in recorded.split() if t.endswith(".py")]
    if not scripts:
        return recorded in live
    return any(t in live for t in scripts)


def acquire_lock(root: Path | str) -> Path | None:
    """取并发锁。**已有活着的持有者 ⇒ 返回 None**（调用方必须拒绝启动）。

    ## 为什么要有锁（本仓铁律：约定没强制力）

    🚨 baostock **禁并发** ⇒ 两个采集同时跑会：
    ① 撞源站的并发限制；
    ② 两次写**同一个** `date=` 分区 ⇒ 分区内容取决于谁先写完 ⇒ **不确定**；
    ③ 单元账互相覆盖 ⇒ 额度被用两次。

    ⚠️ **PID 存活 ≠ 身份**（本仓第 115 层：PID 是可复用资源）。
    ⇒ 判「还持有」要**三条取证**：PID 活着 **且** 命令行里含本脚本名
    **且** 锁文件里的 pid 与当前持有者一致。
    ⚠️ 锁文件存在但持有者已死 ⇒ **当过期处理**（直接接管），不许永久卡死。
    """
    import os
    import sys as _sys
    p = Path(root) / LOCK_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        try:
            holder = json.loads(p.read_text(encoding="utf-8"))
            pid = int(holder.get("pid", -1))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pid = -1
        live = _proc_cmdline(pid) if pid > 0 else ""
        if pid > 0 and _pid_alive(pid) and _same_script(
                str(holder.get("cmdline", "")), live):
            print(f"❌ **已有采集在跑**（pid {pid}）："
                  f"{live[:120]}\n"
                  f"   锁文件：{p}\n"
                  f"   baostock **禁并发** ⇒ 不许第二个采集同时跑。"
                  f"（若确认那个进程已经死了，删掉锁文件再跑。）",
                  file=_sys.stderr)
            return None
        print(f"  锁文件存在但持有者已不在（pid {pid}）⇒ 当过期处理，接管。")
    p.write_text(json.dumps({
        "pid": os.getpid(),
        "cmdline": " ".join(_sys.argv),
        "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False), encoding="utf-8")
    return p


def release_lock(lock: Path | None) -> None:
    """放锁。**只在持有者还是自己时**删（别删掉别人的）。"""
    import os
    import sys as _sys
    if lock is None:
        return
    try:
        holder = json.loads(lock.read_text(encoding="utf-8"))
        if int(holder.get("pid", -1)) != os.getpid():
            return
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    try:
        lock.unlink()
    except OSError:
        pass


def write_announced_layer(aroot: Path | str, day: str,
                          ann: pd.DataFrame) -> Path:
    """落「当天新公告」层的分区。**空分区也要落。**

    🚨 **为什么空分区也是记录**：
    「今天跑了但没有新公告」与「今天没跑」在文件系统上**长得一样**
    （目录里没有 `date=` 分区）⇒ 读的人无法区分 ⇒ 那一天就成了**静默的空洞**。
    ⇒ 落一个**空的**分区（`n_rows=0`）⇒ 「跑过、零条」成为可核对的事实。

    🚨 **空分区必须能被 `vintage.verify_integrity` 核对** ⇒ 走
    `vintage.write_snapshot`（不自己 `to_parquet`）。
    """
    aroot = Path(aroot)
    part = vintage.write_snapshot(aroot, day, ann)
    (part.parent / "_scope.json").write_text(
        json.dumps({"day": day, "kind": "announced_today",
                    "scope": "当天（预案公告日 == day）新公告的方案",
                    "n_rows": int(len(ann)),
                    "note": ("**空分区也是记录**：它表示「当天跑过、零条新公告」，"
                             "与「当天没跑」必须可区分。")},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return part


def shard_roots(root: Path | str) -> list[Path]:
    """`root` 下所有分片根（按片号排序）。**没有分片目录时返回 `[root]`。**

    ⇒ 兼容**未分片**的老布局（`root/date=*/`），
    使 `load_events` 对两种布局都成立。
    """
    root = Path(root)
    if not root.is_dir():
        return []
    found = []
    for d in root.glob("shard=*"):
        if d.is_dir():
            found.append(d)
    if not found:
        return [root] if root.is_dir() else []
    return sorted(found, key=lambda p: p.name)


def announced_roots(root: Path | str) -> list[Path]:
    """`root/announced/` 下的所有**根**（新布局：直接 `date=*/`；旧布局：`shard=*/`）。

    两种布局都支持 ⇒ 换布局时**不会静默丢数据**。
    """
    base = Path(root) / ANNOUNCED_ROOT_NAME
    if not base.is_dir():
        return []
    out = []
    if any(vintage.available_days(base)):
        out.append(base)
    out.extend(sorted([d for d in base.glob("shard=*") if d.is_dir()],
                      key=lambda p: p.name))
    return out


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
            max_calls: int = MAX_CALLS_PER_RUN,
            cells: dict | None = None,
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

    ## 🚨 配额闸（`max_calls`）

    **超了当场停**，不许「今天多跑点明天少跑」——
    超限的后果是**黑名单 6 小时**，不是「明天补回来」。
    ⇒ 每次调用**之前**检查闸；撞闸 ⇒ `quota_stopped=True` 且**立刻停**。
    ⇒ 撞闸后这一片**不算完成**（进度账 `complete=False`）⇒ 明天续跑。

    ## 续跑（`cells` 单元账）

    逐 (票, 年) 记状态；已记的**不再调**。
    🚨 没有单元账的话，「那票那年没分红」与「还没采」在数据里**同形**
    ⇒ 续跑会**重采**（烧额度）或**漏采**（数据缺口）。
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

    cells = dict(cells or {})
    chunks: list[pd.DataFrame] = []
    failed: list[dict] = []
    empty_cells: list[str] = []
    n_calls = 0
    n_amount_conflicts = 0
    n_skipped_resume = 0
    quota_stopped = False
    t0 = time.time()
    call_times: list[float] = []

    total = len(pool) * len(years)
    done = 0
    for code, bs_code in pool.items():
        for year in years:
            cell_key = f"{code}|{year}"
            # 🚨 **只跳过「成功」的单元**。
            # 状态为 `error` 的单元**必须重试** —— 那是「没查成」，
            # 不是「查过了、那票没分红」。
            # 把 error 也跳过 ⇒ 这一格**永远补不上** ⇒ 数据缺口永久留下
            # （而进度账会以为它跑过了）。
            if cells.get(cell_key) in ("ok", "empty"):
                # **已采过** ⇒ 不再调源站（省额度；且不许重采）。
                n_skipped_resume += 1
                done += 1
                continue
            if n_calls >= max_calls:
                quota_stopped = True
                print(f"  🚨 配额闸触发（{max_calls} 次）——**当场停**。"
                      f"这一片不算完成，明天续跑。", flush=True)
                break
            t1 = time.time()
            raw, status = fetch_code_year(bs_code, year, fetcher=fetcher)
            call_times.append(time.time() - t1)
            n_calls += 1
            done += 1
            if status.startswith("error:"):
                failed.append({"code": code, "bs_code": bs_code,
                               "year": int(year), "status": status})
                # ⚠️ **失败的单元也要记账**，但状态要标出来。
                # 不记账 ⇒ 明天续跑**不会**重试它 ⇒ 缺口永久留下。
                cells[cell_key] = "error"
            elif status == "empty":
                empty_cells.append(f"{code}:{year}")
                cells[cell_key] = "empty"
            else:
                chunks.append(normalize_events(
                    raw, bs_code, year,
                    collected_at=datetime.now(timezone.utc).isoformat(
                        timespec="seconds")))
                n_amount_conflicts += int(
                    chunks[-1].attrs.get("n_amount_conflicts", 0))
                cells[cell_key] = "ok"
            if done % 200 == 0 or done == total:
                print(f"  {done}/{total} 次调用  {time.time()-t0:.0f}s"
                      f"  失败 {len(failed)}", flush=True)
                # 🚨 **单元账周期落盘**：崩了最多丢 200 格（会重采），
                # 而不是丢全部（那要重采 1.4 万格）。**原子写**。
                write_cells(cells_path(root, day), cells,
                            meta={"day": day, "pool_size": len(pool),
                                  "years": list(years)})
            if sleep:
                time.sleep(sleep)
        if quota_stopped:
            break

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
        "n_skipped_resume": n_skipped_resume,
        "quota_stopped": quota_stopped,
        "cells": cells,
        "seconds": time.time() - t0,
        "call_times": call_times,
    }


# ── 账本：单元账（续跑）与分片进度账 ────────────────────────────────

def cells_path(shard_dir: Path | str, day: str, kind: str = "full") -> Path:
    """单元账路径：`<root>/date=<day>/_cells.json`（全年份）或 `_cells_sweep.json`（当年扫描）。

    ⚠️ **两个文件名不可合并**（理由见 `CELLS_LEDGER_NAME` 的注释）。
    """
    name = CELLS_LEDGER_NAME if kind == "full" else CELLS_SWEEP_LEDGER_NAME
    return vintage.snapshot_path(shard_dir, day).parent / name


def read_cells(path: Path | str) -> dict:
    """读单元账。**坏文件不许静默当空** —— 那会让续跑**重采一遍**（浪费额度）。"""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"单元账 {p} 读不出来（{exc}）。"
            f"**不许当成空的**——那会让这一片**从头重采**，白烧额度。"
            f"修法：手工核对后删掉它，或改名保留证据。") from exc
    cells = data.get("cells") if isinstance(data, dict) else None
    return cells if isinstance(cells, dict) else {}


def write_cells(path: Path | str, cells: dict, meta: dict | None = None) -> None:
    """写单元账（**原子写**：先写临时文件再 rename）。

    🚨 **原子写是必须的**：采集是长跑（~45 分钟），
    崩在写盘中途会留下半截 JSON ⇒ 下次续跑读不出来 ⇒ 只能重采。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(meta or {})
    payload["cells"] = cells
    payload["written_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(p)


def progress_path(root: Path | str = DIVIDEND_ROOT) -> Path:
    """分片进度账的路径。**追加式**（不覆盖）。"""
    return Path(root) / SHARD_PROGRESS_NAME


def read_progress(root: Path | str = DIVIDEND_ROOT) -> list[dict]:
    """读分片进度账（按写入顺序）。"""
    p = progress_path(root)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # 追加式账本里的一行坏了 ⇒ **不许整本作废**，
                # 也不许当它没坏 —— 记下来让人知道。
                out.append({"_unreadable_line": line[:200]})
    return out


def append_progress(root: Path | str, entry: dict) -> None:
    """**追加**一条分片进度。**绝不覆盖**（覆盖就抹掉了「这一片跑过几次」）。"""
    p = progress_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def pick_shard_auto(root: Path | str, day: str, n_shards: int) -> int:
    """`--shard auto` ⇒ 该跑哪一片。

    ## 三条规则（按顺序）

    1. **今天已经起过某片** ⇒ **继续那一片**。
       🚨 这是「同一天重跑不许把额度用两次」的落点：
       重复触发只能**续跑**那一片（只补没采的单元），
       **不许**当天再起一片（那会把当天的额度花掉两遍）。
    2. **还有没跑完的片** ⇒ 跑第一片没跑完的。
       「没跑完」= 有单元失败（⇒ 明天重试那几格）或撞了配额闸。
    3. **全跑完了** ⇒ 轮转跑**最久没跑**的那片。
       ⇒ 全池采完之后，作业**继续每日跑**，
       维持「当时读到的」那一层逐日累积（模块 docstring 的「两条腿」）。
    """
    prog = read_progress(root)
    # 🚨 **只认「同一种分片数」的记录**。
    # 片数一改，切分边界就变了 ⇒ 老的 `shard=1-of-5/` 变成**孤儿目录**
    # （新的跑在 `shard=1-of-3/`）⇒ 若把老的 `complete` 也算进来，
    # 「这一片跑过了」的判断就是**假的** ⇒ 那一段永远不会被重采 ⇒ **静默的覆盖空洞**。
    same = [e for e in prog if int(e.get("n_shards", -1)) == int(n_shards)]
    today = [e for e in same
             if e.get("day") == day and isinstance(e.get("shard_i"), int)]
    if today:
        return int(today[-1]["shard_i"])
    done = {int(e["shard_i"]) for e in same
            if e.get("complete") and isinstance(e.get("shard_i"), int)}
    for i in range(1, n_shards + 1):
        if i not in done:
            return i
    last_day: dict[int, str] = {}
    for e in same:
        if isinstance(e.get("shard_i"), int):
            last_day[int(e["shard_i"])] = str(e.get("day") or "")
    return min(range(1, n_shards + 1),
               key=lambda i: (last_day.get(i, ""), i))


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
    """读**全部**快照分区（**跨分片**），拼成事件表。**跨采集日去重，后写胜出。**

    为什么读全部而不是最后一份：事件库是**追加式**的 ——
    第 1 天采了 A/B 两只，第 2 天采了 C/D 两只，
    只读最后一份会把 A/B **丢掉** ⇒ 因子在某些票上凭空变缺。
    跨分片同理：**第 1 片与第 2 片是互补的两半**，只读一片 ⇒ 因子只覆盖半个池。
    """
    frames = []
    for sr in shard_roots(root):
        for day in vintage.available_days(sr):
            f = pd.read_parquet(vintage.snapshot_path(sr, day))
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


def load_announced(root: Path | str = DIVIDEND_ROOT, day: str | None = None
                   ) -> pd.DataFrame:
    """「当天新公告」层的事件表（**跨分片**）。

    `day=None` ⇒ 全部日子。`day='YYYY-MM-DD'` ⇒ 只看那一天新公告的方案。

    ⚠️ **这一层只覆盖从它开始存在的那天起** ——
    它是**逐日**记的，不是回溯补的。
    ⇒ 想知道 2021 年那天公告了什么，**只能问事件层**（带修订风险），
    事件层与这一层**不是同一个量**（第 7 层：两份各自都对的数不许混比）。
    """
    frames = []
    for ar in announced_roots(root):
        days = [day] if day else vintage.available_days(ar)
        for d in days:
            p = vintage.snapshot_path(ar, d)
            if not p.is_file():
                continue
            f = pd.read_parquet(p)
            if "snapshot_date" not in f.columns:
                f["snapshot_date"] = d
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
    p.add_argument("--shard", default="auto",
                   help="'i/n'（1 起的分片号）或 'auto'（由进度账决定）")
    p.add_argument("--shards", type=int, default=5,
                   help="'--shard auto' 时的总片数（默认 5）")
    p.add_argument("--no-lock", action="store_true",
                   help="跳过并发锁（**只给测试用**）")
    p.add_argument("--max-calls", type=int, default=MAX_CALLS_PER_RUN,
                   help=f"单次运行的调用配额闸（默认 {MAX_CALLS_PER_RUN}）")
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

    # 🚨 **并发锁**：baostock 禁并发 ⇒ 两个采集同时跑会撞源站限制、
    # 互相覆盖同一个 `date=` 分区、并把额度用两次。
    lock = None
    if not args.no_lock:
        lock = acquire_lock(root)
        if lock is None:
            return 3

    try:
        return _run(args, root, day, years, expect_bytes)
    finally:
        release_lock(lock)


def _run(args, root: Path, day: str, years: tuple[int, ...],
         expect_bytes: str | None, fetcher=None) -> int:
    """真正的采集流程（**`main()` 只做锁与委派**）。

    ⇒ 判决逻辑在模块级函数里，**可被守卫直接调用**（第 120 层：
    嵌套在 `main()` 里的函数结构上无法测试）。

    `fetcher` 可注入 ⇒ **测试不联网也能跑完整条链路**。
    🚨 这不是「为了测试而加的旁门」，是**唯一能测到「续跑路径」的办法**：
    续跑（本次 0 调用）恰恰是**第一天真采集测不出来**的那条分支，
    而它**恰恰是 launchd 每天都会走的**那条。
    """
    pool_all = universe()

    # ── 分片 ──────────────────────────────────────────────────────
    spec = parse_shard(args.shard)
    if spec is None:                      # auto
        n_shards = int(args.shards)
        shard_i = pick_shard_auto(root, day, n_shards)
    else:
        shard_i, n_shards = spec
    sroot = shard_root(root, shard_i, n_shards)

    # 🚨 **分片数变了 ⇒ 老的分片目录变成孤儿** ⇒ 显式喊出来。
    other = {int(e.get("n_shards", -1)) for e in read_progress(root)
             if isinstance(e.get("n_shards"), int)} - {int(n_shards)}
    if other:
        print(f"⚠️ 进度账里有**别的分片数**的记录：{sorted(other)}；"
              f"本次用 {n_shards} 片。⇒ 那些 `shard=*-of-{sorted(other)[0]}/` "
              f"目录是**孤儿**（不会被本次续跑覆盖，但 `load_events` 仍会读到它们）。"
              f"要清干净请手工核对后删除。")

    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        missing = want - set(pool_all)
        if missing:
            print(f"❌ 池里没有这些代码：{sorted(missing)}")
            return 2
        selected = sorted(want)
    else:
        selected = shard_codes(sorted(pool_all), shard_i, n_shards)
    if args.max_codes:
        selected = selected[:args.max_codes]
    pool = {c: pool_all[c] for c in selected}

    if args.dry_run:
        # ⚠️ 这里的 0.173 是**默认假设值**，不是实测值。
        # **权威数字在覆盖产物的 `::cost_account` 里**（它用当次跑出来的
        # `per_call_mean_s` 算）⇒ 决策时**不要**用这个 dry-run 的数。
        acct = cost_account(len(pool), len(years), 0.173, args.sleep)
        acct["per_call_is_assumed_not_measured"] = True
        acct["authoritative_number"] = (
            "覆盖产物的 `::cost_account.full_pool`（用当次实测的 "
            "`::scope.per_call_mean_s` 算）")
        acct["shard"] = f"{shard_i}/{n_shards}"
        acct["max_calls_gate"] = int(args.max_calls)
        acct["quota_gate_note"] = (
            f"这一片要 {len(pool) * len(years)} 次调用，"
            f"配额闸 {args.max_calls} ⇒ "
            + ("**装得下**" if len(pool) * len(years) <= args.max_calls
               else "🚨 **装不下** ⇒ 会被闸挡住，这一片不算完成，明天续跑"))
        print(json.dumps(acct, ensure_ascii=False, indent=1))
        return 0

    # ══ 采集 A：全池「当年」视图 —— "当时读到的"那一层 ══════════════
    # 每天把**全部 5,460 只**的当年方案重读一遍（5,460 次）：
    #   ① 当天的「当年视图」覆盖**全池**（分片只有 1/5）；
    #   ② 当天把每一条当年方案重读 ⇒ 「预案 → 股东大会改额」
    #      在**第二天**就能比出来。
    sweep_years = (int(day[:4]),)
    droot = daily_root(root)
    dcp = cells_path(droot, day, kind="sweep")
    dcells = read_cells(dcp)
    # ⚠️ `--codes` / `--max-codes` 是**白名单** ⇒ 它**同时**收窄采集 A 与采集 B。
    # 否则一个 5 只的试跑会顺手触发 5,460 次的全池扫描。
    whitelist = bool(args.codes or args.max_codes)
    sweep_pool = pool if whitelist else pool_all
    sweep_needed = len(sweep_pool) * len(sweep_years)
    sweep_cap = min(sweep_needed, int(args.max_calls))
    print(f"采集 A｜全池当年视图｜{len(sweep_pool)} 只 × {sweep_years} = "
          f"{sweep_needed} 次（配额上限 {sweep_cap}）", flush=True)
    dres = collect(sweep_pool, droot, day, years=sweep_years, sleep=args.sleep,
                   max_calls=sweep_cap, cells=dcells, fetcher=fetcher)
    write_cells(dcp, dres["cells"],
                meta={"day": day, "kind": "daily_sweep", "pool_size": len(sweep_pool),
                      "years": list(sweep_years),
                      "n_calls_this_run": dres["n_calls"],
                      "quota_stopped": dres["quota_stopped"]})
    ddf = dres["df"]
    if not ddf.empty:
        vintage.write_snapshot(droot, day, ddf, dup_dropped=dres["dup_dropped"])
        (vintage.snapshot_path(droot, day).parent / "_scope.json").write_text(
            json.dumps({"day": day, "kind": "daily_sweep",
                        "scope": ("**白名单**（试跑）" if whitelist
                                  else "全池 × 当年预案公告年份"),
                        "n_codes": len(sweep_pool), "years": list(sweep_years),
                        "n_cells": sweep_needed,
                        "quota_stopped": dres["quota_stopped"]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  采集 A 完成：{dres['n_calls']} 次，事件 {len(ddf)} 条，"
          f"失败 {len(dres['failed'])}", flush=True)

    # ══ 采集 B：分片全量历史（事件库 + 修订轨迹）══════════════════
    # **配额闸共享**：A 用掉的额度从 B 的额度里扣 ⇒ 全天合计 ≤ --max-calls。
    remaining = int(args.max_calls) - dres["n_calls"]
    if remaining <= 0:
        print(f"🚨 配额被采集 A 用完（{dres['n_calls']}/{args.max_calls}）"
              f"——**采集 B 今天不跑**（明天续）。")
        res = {"df": pd.DataFrame(columns=EVENT_COLUMNS), "failed": [],
               "empty_cells": [], "dup_dropped": 0, "n_calls": 0,
               "n_amount_conflicts": 0, "n_skipped_resume": 0,
               "quota_stopped": True, "cells": {}, "seconds": 0.0,
               "call_times": []}
        cp = cells_path(sroot, day)
        cells = {}
    else:
        cp = cells_path(sroot, day)
        cells = read_cells(cp)
        if cells:
            print(f"  续跑：单元账已有 {len(cells)} 格（不再调源站）", flush=True)
        print(f"采集 B｜片 {shard_i}/{n_shards} 全量历史｜"
              f"{len(pool)} 只 × {len(years)} 年 = "
              f"{len(pool) * len(years)} 次（配额闸还剩 {remaining}）", flush=True)
        res = collect(pool, sroot, day, years=years, sleep=args.sleep,
                      max_calls=remaining, cells=cells, fetcher=fetcher)
    df = res["df"]

    # 单元账最终落盘（**原子写**）。
    write_cells(cp, res["cells"],
                meta={"day": day, "shard": f"{shard_i}/{n_shards}",
                      "pool_size": len(pool), "years": list(years),
                      "n_calls_this_run": res["n_calls"],
                      "quota_stopped": res["quota_stopped"]})

    if df.empty and res["failed"]:
        print(f"❌ 全部失败（{len(res['failed'])} 次）——不落盘")
        return 1

    # ⚠️ **落盘走 `aplus.vintage.write_snapshot`**，不自己 `to_parquet`：
    # 它负责加 `snapshot_date` 列、写**追加式 manifest 账本**、记内容摘要
    # ⇒ `vintage.verify_integrity` 能自证「这份数据没被动过」。
    # 自己 `to_parquet` 的话，产物**对完整性校验是不可见的**。
    part = vintage.write_snapshot(sroot, day, df,
                                  dup_dropped=res["dup_dropped"])
    scope = {
        "day": day,
        "shard": f"{shard_i}/{n_shards}",
        "partial": bool(args.max_codes or args.codes),
        "n_codes": len(pool),
        "codes": sorted(pool),
        "years": list(years),
        "n_cells": len(pool) * len(years),
        "n_cells_attempted": len(res["cells"]),
        "failed_cells": res["failed"],
        "empty_cells_no_dividend": len(res["empty_cells"]),
        "quota_stopped": res["quota_stopped"],
        "note": ("这一份的**采集域**是上面这些票 × 上面这些年，"
                 "**不是全池**。读它的人必须先看这份 scope。"),
    }
    (part.parent / "_scope.json").write_text(
        json.dumps(scope, ensure_ascii=False, indent=1), encoding="utf-8")
    if res["failed"]:
        (part.parent / "_failed.json").write_text(
            json.dumps(res["failed"], ensure_ascii=False, indent=1),
            encoding="utf-8")

    # ── 「当天新公告」那一层（"当时读到的"）────────────────────
    # 这一层**逐日记**：当天的分区里放「**当天新公告**了哪些方案、
    # 当天读到的金额是多少」⇒ 从今天起累积 ⇒ 将来能观测到修订。
    # 来源 = 采集 A（**全池**当年）∪ 采集 B（分片全量历史）⇒ 取并集，
    # 方案身份去重（同方案后写胜出）。
    ann_frames = [f for f in (ddf, df) if f is not None and not f.empty]
    ann = pd.DataFrame(columns=EVENT_COLUMNS)
    if ann_frames:
        ann = pd.concat(ann_frames, ignore_index=True)
        ann = (ann.sort_values(EVENT_KEY, kind="stable")
                  .drop_duplicates(subset=EVENT_KEY, keep="last")
                  .reset_index(drop=True))
        ann = ann[ann["plan_announce_date"].astype(str) == day]
    aroot = announced_root(root)
    write_announced_layer(aroot, day, ann)
    print(f"  「当天新公告」层：{len(ann)} 条 → {aroot}/date={day}/", flush=True)

    # ── 分片进度账（追加式）────────────────────────────────────
    failed_cells = {(f["code"], f["year"]) for f in res["failed"]}
    cell_total = len(pool) * len(years)
    complete = (not res["quota_stopped"]) and (len(failed_cells) == 0)
    append_progress(root, {
        "day": day,
        "shard_i": int(shard_i),
        "n_shards": int(n_shards),
        "shard_dir": str(sroot.relative_to(ROOT)) if sroot.is_relative_to(ROOT)
        else str(sroot),
        "n_codes": len(pool),
        "n_cells_total": cell_total,
        "n_cells_attempted": len(res["cells"]),
        "n_calls_this_run": res["n_calls"],
        "n_calls_sweep": dres["n_calls"],
        "n_calls_total_today": dres["n_calls"] + res["n_calls"],
        "n_skipped_resume": res["n_skipped_resume"],
        "n_failed_cells": len(failed_cells),
        "quota_stopped": bool(res["quota_stopped"]),
        "max_calls_gate": int(args.max_calls),
        "complete": bool(complete),
        "seconds": res["seconds"],
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

    # ── 覆盖产物 ──────────────────────────────────────────────────
    ev = load_events(root)
    proof = pit_visibility_self_proof(ev)
    covered = {c for c in pool if not any(f["code"] == c for f in res["failed"])}
    cell_ok = cell_total - len(failed_cells)

    # ⚠️ **产物路径必须先算出来**（`prev_path` 下面要用它）。
    # 第一版把它放在 `per_call` 之后 ⇒ 续跑路径（本次 0 调用 ⇒ 走
    # 「沿用上一轮实测单次耗时」那个分支）**直接 UnboundLocalError**，
    # 而这条路径**恰恰是 launchd 每天都会走的**（单元账满了 ⇒ 0 新调用）。
    # ⇒ 🚨 **「跑到底才炸」的错又长了一个样**：不是网络、不是源站，
    # 是**代码顺序**，而它只在**续跑**时才炸 ⇒ 第一天（真采）测不出来。
    out = Path(args.out) if args.out else (
        ROOT / "data" / "reports"
        / f"dividend_pit_coverage_shard{shard_i}of{n_shards}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    prev_path = out

    per_call = (sum(res["call_times"]) / len(res["call_times"])
                if res["call_times"] else 0.0)
    max_call = max(res["call_times"]) if res["call_times"] else 0.0
    # 🚨 **续跑时本次调用数 ≈ 0** ⇒ `per_call_mean_s` 会算成 0 ⇒
    # 成本账会印出「预计 0 秒」这种**看起来对、其实是没测**的数。
    # ⇒ 本次没调用时，**沿用上一次运行的实测值**，并标明来源。
    per_call_source = "this_run"
    if per_call == 0.0:
        prev = None
        if prev_path.is_file():
            try:
                prev = json.loads(prev_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                prev = None
        if prev:
            per_call = float(prev.get("scope", {}).get("per_call_mean_s") or 0.0)
            per_call_source = "previous_run_carried_forward"

    report = {
        "generator": GENERATOR,
        "day": day,
        "shard": {
            "i": int(shard_i), "n": int(n_shards),
            "root": str(sroot.relative_to(ROOT)) if sroot.is_relative_to(ROOT)
            else str(sroot),
            "announced_root": str(aroot.relative_to(ROOT))
            if aroot.is_relative_to(ROOT) else str(aroot),
            "deterministic": ("按**排序后的代码清单**连续切片 ⇒ "
                              "同一份清单 ⇒ 同一片 ⇒ 可复现、可续采"),
            "n_codes_in_shard": len(pool),
            "n_codes_in_pool": len(pool_all),
        },
        "scope": {
            "n_codes": len(pool),
            "n_years": len(years),
            "years": list(years),
            "partial": bool(args.max_codes or args.codes),
            "n_calls": res["n_calls"],
            "n_calls_meaning": ("**本次**的调用数。续跑（单元账已满）时它接近 0；"
                                "判「累计采了多少」看 `n_cells_attempted_cumulative`。"),
            "n_cells_attempted_cumulative": len(res["cells"]),
            "n_skipped_resume": res["n_skipped_resume"],
            "quota_stopped": bool(res["quota_stopped"]),
            "max_calls_gate": int(args.max_calls),
            "seconds": res["seconds"],
            "per_call_mean_s": per_call,
            "per_call_mean_s_source": per_call_source,
            "per_call_max_s": max_call,
            "serial_no_concurrency": True,
            "sleep_between_calls_s": args.sleep,
            "two_phase": {
                "A_daily_sweep": {
                    "what": "全池 × 当年预案公告年份（「当时读到的」那一层）",
                    "n_codes": len(sweep_pool),
                    "years": list(sweep_years),
                    "n_calls": dres["n_calls"],
                    "n_failed": len(dres["failed"]),
                    "quota_stopped": bool(dres["quota_stopped"]),
                    "root": str(droot.relative_to(ROOT))
                    if droot.is_relative_to(ROOT) else str(droot),
                },
                "B_shard_full_history": {
                    "what": f"片 {shard_i}/{n_shards} × 全部年份（事件库 + 修订轨迹）",
                    "n_codes": len(pool),
                    "years": list(years),
                    "n_calls": res["n_calls"],
                    "n_failed": len(res["failed"]),
                    "quota_stopped": bool(res["quota_stopped"]),
                    "root": str(sroot.relative_to(ROOT))
                    if sroot.is_relative_to(ROOT) else str(sroot),
                },
                "total_calls_today": dres["n_calls"] + res["n_calls"],
                "gate_shared": ("两个阶段**共享**同一个配额闸 ⇒ "
                                "全天合计 ≤ --max-calls"),
            },
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
            "n_same_plan_amount_conflicts": res["n_amount_conflicts"],
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
            "this_shard": cost_account(len(pool), len(years), per_call,
                                       args.sleep),
            "full_pool": cost_account(len(pool_all), len(years), per_call,
                                      args.sleep),
            "quota_gate": {
                "max_calls_per_run": int(args.max_calls),
                "n_shards": int(n_shards),
                "calls_per_shard": len(pool) * len(years),
                "calls_daily_sweep": len(pool_all),
                "calls_total_per_day": len(pool_all) + len(pool) * len(years),
                "fits_in_gate": (len(pool_all) + len(pool) * len(years)
                                 <= int(args.max_calls)),
                "why": ("baostock 日限 5 万次、禁并发、超限黑名单 6 小时起；"
                        "本机 18:30 的日行情采集要占 5,460 次 ⇒ "
                        "分红一次吃满 43,680 会逼近上限 ⇒ 一次超限就**打坏 D1 的"
                        "每日交付** ⇒ **D1 的交付优先级高于数据采集** ⇒ "
                        "每日配额 ≤ 15,000。"),
            },
            "decision": ("按片采：每片 ≤ 15,000 次，跨日分批；"
                         "**不许**一天吃完全池。"),
        },
        "freshness_note": (
            "本产物**没有**登记进 `aplus/provenance.py::DEFAULT_GENERATORS`"
            "（本轮不许改 `aplus/` 下任何模块）⇒ `provenance.freshness()`"
            "**不传 generator 会判 `unknown`**。"
            "⇒ 验新鲜度必须**显式传生成器**："
            "`provenance.freshness(<本文件路径>, "
            "generator='scripts/collect_dividend_pit.py')`。"
            "🚨 `unknown` 与「数据坏了」长得一模一样 ⇒ 不传就等于没验。"),
        "two_layers": {
            "event_layer": ("`shard=*/date=*/financial.parquet` = 分片的全量历史"
                            "（可回溯到 2021，但金额是**今天读到的** ⇒ 带修订风险）"),
            "announced_layer": ("`announced/shard=*/date=*/financial.parquet` = "
                                "**当天新公告**的方案（逐日忠实，只覆盖从今天起）"),
            "rule": "**两条腿不许混引**（第 7 层：两份各自都对的数不许混比）",
        },
        "what_this_cannot_prove": [
            "本产物只证明**数据管道**的覆盖率与 PIT 可见性，不证明因子有效。",
            "**不许**把石川书里的多空数（IVOL 等权月均超额 1.27%，t=8.64，"
            "cn_factor_lines.txt:9885）当成本项目的可达目标 —— 空腿散户做不了。",
            "事件层读到的金额是「**今天读到的**」，不等于「当时公告的**」；"
            "「当时读到的」靠 `announced/` 那一层逐日累积（从今天起）。",
            "覆盖率低 ⇒ 因子在**未覆盖的票**上是**缺数据**，不是零股息；"
            "低覆盖率下的横截面排名会被系统性偏置（银行/煤炭这类高股息票最容易被采到）。",
        ],
        "gaps": [
            "本产物只覆盖**一片**；全池要等所有片跑完（进度见 "
            "`data/reports/dividend_pit/_shard_progress.jsonl`）。",
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
            "shard": f"{shard_i}/{n_shards}",
            "n_codes": len(pool),
            "n_years": len(years),
            "n_calls": res["n_calls"],
            "quota_stopped": bool(res["quota_stopped"]),
            "max_calls_gate": int(args.max_calls),
            "n_events": int(len(ev)),
            "data_fp_caliber": ("本脚本的输入是**抓取当时的 baostock 源站**；"
                                "章里的 data_fp 描述的是它**写入**的分红 PIT 存储，"
                                "不是它读进来的东西。价格侧（不复权 close）"
                                "本轮**没有**参与计算。"
                                "⚠️ `data_fp` 实测为 `null`：本存储是**分区式**"
                                "（parquet 在 `date=*/` 子目录里），"
                                "而 `provenance.data_fingerprint` 只扫一层 `*.parquet` "
                                "⇒ 它的内容摘要改由 `aplus.vintage` 的"
                                "**追加式 manifest** 承担"
                                "（`data/reports/dividend_pit/shard=*/_manifest.jsonl"
                                "::sha256`），"
                                "并由 `vintage.verify_integrity()` 逐份核对。"),
        })

    print(f"\n  事件 {len(ev)} 条 / {ev['code'].nunique() if not ev.empty else 0} 只")
    print(f"  本片单元覆盖 {cell_ok}/{cell_total} = "
          f"{(cell_ok / cell_total) if cell_total else 0:.4f}"
          + ("  🚨 配额闸触发" if res["quota_stopped"] else ""))
    print(f"  PIT 自证：{proof.get('ok')} "
          f"(查 {proof.get('n_checked')} 条，"
          f"锚日可见 {proof.get('visible_at_anchor')}，"
          f"锚日前不可见 {proof.get('invisible_before_anchor')})")
    print(f"  产物：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
