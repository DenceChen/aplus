"""PIT 版基本面因子（BM）—— **本项目第一个跨信息源的因子**。

## 为什么要有这个模块

线上 8 个因子（mom20/rev5/vol20/vsur/turn20/turnratio/rsi/ivol）**全部**从
`master/daily` 的同一份 OHLCV 派生。台账已判它们「实测为 null」——
在只有一个信息源时，这是数学上的必然。

BM（账面市值比）是**第一个跨信息源**的因子：它的分子来自财报，不来自行情。
`scripts/fundamental_ic.py` 实测 BM@K=21 的 IC-t = **+2.94**（过闸），
是本项目第一个跨信息源的显著结果。

## 🚨 但那个结果可能是 PIT 污染本身

BM 的历史 IC 是在**后修值**上算的：

- `scripts/fundamental_factor.py` 按 `NOTICE_DATE <= as_of` 对齐财报，
  贴的是**今天源站服务的那个值**——而源站每个报告期只存一行、无版本维度，
  服务的必然是被修订过的值；
- `aplus/financial.py` 的 docstring 因此写死「在白名单过滤
  （`is_never_revised`）**或**攒 vintage 之前，BM / ROE / FAR 一律不实现」。

**出路 A（`is_never_revised()` 白名单）已被实测证伪**：
`scripts/audit_pit_whitelist/exp_snapshot_diff.py` 用 2026-10-14 与 10-20
两份 PIT 快照直接差分 ⇒ 标称「从未被改动」的 21,657 行里**实际变了 28 行**，
且变了/没变的记录里 `UPDATE_DATE == NOTICE_DATE` 的比例几乎一样
（43.75% vs 33.16%）⇒ **`UPDATE_DATE` 标不出修订** ⇒ 那是**假白名单**。

⇒ **本模块走出路 B**：财务侧**只**从 `aplus/vintage.py::pit_frame` 取，
那里给的是 `as_of` 那天**源站当时服务的**那一帧。

## 三条不可让步的实现约定

1. **财务侧只能来自 `vintage.pit_frame(root, as_of)`**。
   绝不读当前缓存，绝不用 `financial.visible_as_of` 去贴后修值。
   `visible_as_of` 在本模块里**只**用于「已取到的 PIT 帧内部的披露日对齐」，
   那一层语义是干净的（帧本身已是当天的值）。
2. **日期不足时报错，不是降级**。
   `as_of` 早于第一份快照 ⇒ 抛 `NoPITSnapshotError`；
   解析到的快照晚于 `as_of` ⇒ 抛 `PITLookaheadError`。
   两者是**不同的异常类型**，因为「没有数据」与「用了未来的数据」
   是相反的失效方向，绝不能塌成同一个信号。
3. **不进实盘、不进判决链**。
   本模块只服务**研究与前瞻验证**。`aplus/factor.py::DEFAULT_WEIGHTS`
   （实盘出单那 4 个因子）与判决链的 8 因子口径**一个字都没动**。

## 与 `scripts/fundamental_factor.py` 的关系

那一份是**同一个因子的后修值版本**：口径（年报、不复权价、`log(B/P)`、
越大越便宜）逐字照抄，但数据源是当前缓存，因此**只能进诊断脚本**。
本模块是它的 PIT 版本，也是**唯一**允许进前瞻验证台账的那一份。
两份的数值**不可互相引用**（数据源不同，不是同一个量）。

## 口径

    BM = log(BPS / close)

- `BPS`：每股净资产（东财口径，元/股），取自**年报**、披露日 ≤ `as_of`
  的**最新一份**；
- `close`：**不复权**收盘价，取**同一 `as_of`** 那天的最后一根 bar。
  不复权是**对的**：除权会同时改价和 BPS 分子，用后复权价会让一次送股
  凭空改变 BM（10 送 10 时 BPS 减半、价格减半，比值本应不变，
  后复权价却不变 → BM 直接腰斩）。
- 取对数是为了让「贵 10 倍」与「便宜 10 倍」对称。
- **方向：越大越便宜。刻意不取负。**
  价值因子要的就是「买最便宜的」；取负会让 top-10 变成买最贵的。
  符号翻反过一次，翻反后**数字照样正常**（IC、t、覆盖率全都有值），
  只是选出了完全相反的一批票——这种错没有任何报错。
  守卫：`tests/test_factor_fundamental.py::test_bm_higher_means_cheaper`。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aplus import financial, vintage

#: 因子名。与 `scripts/fundamental_factor.py` 同名 ⇒ 前瞻台账里写 `bm`
#: 指的是**这个**版本，不是那份诊断版本。
FACTOR_NAME = "bm"

#: 每股净资产列（东财口径，元/股）。
BPS_COL = "BPS"

#: 报告类型列。年报口径。
REPORT_TYPE_COL = "REPORT_TYPE"

#: 证券代码列（裸 6 位）。
SECURITY_CODE_COL = "SECURITY_CODE"

#: 披露日至少要晚于报告期末这么多天。低于它是脏记录
#: （实测有 `NOTICE_DATE = 1900-01-01` 的坏值）。
#: 与 `scripts/fundamental_factor.py::MIN_LAG_DAYS` 同一常量值。
MIN_LAG_DAYS = 30

#: 早于这一天的披露日一律丢弃。
MIN_NOTICE = "1980-01-01"

#: 财务帧的最小必需列。缺任何一列 ⇒ 报错，不降级。
_REQUIRED_FIN_COLS = (financial.REPORT_DATE_COL, financial.NOTICE_DATE_COL,
                      BPS_COL)


class NoPITSnapshotError(ValueError):
    """`as_of` 之前**没有任何** PIT 快照可用。

    与 `PITLookaheadError` 分开，是因为两者的失效方向相反：
    本异常是「**没有**数据」，那一个是「**用了未来的**数据」。
    塌成同一个类型 ⇒ 读日志的人分不清「那天没数据」与「那天看了未来」。
    """


class PITLookaheadError(ValueError):
    """解析到的快照**晚于** `as_of` —— 前视偏差，必须炸。

    `vintage.pit_frame` 自己只向后回退，所以这条在本模块是**冗余防线**：
    它的作用是「即便上游哪天被改坏，这里也会响」，
    并且把「用了哪一天的快照」这一事实变成**本地可查**的。
    """


# ── 快照解析 ────────────────────────────────────────────────────────

def snapshot_day(root, as_of: str) -> str | None:
    """`as_of` 那天**实际可用**的快照日期（`None` = 一份都没有）。

    只用于**分类**错误与**报告**用了哪天，不用于取数——
    取数一律走 `vintage.pit_frame`。
    """
    days = vintage.available_days(root)
    earlier = [d for d in days if d <= as_of]
    return max(earlier) if earlier else None


def pit_frame(root, as_of: str) -> pd.DataFrame:
    """`as_of` 当天源站服务的财务帧——**本模块取数的唯一入口**。

    1. 只从 `vintage.pit_frame` 取（它找不到快照就报错，绝不拿更新的代替）；
    2. 查 `attrs["snapshot_date"]`：**晚于 `as_of` ⇒ 抛
       `PITLookaheadError`**（前视偏差，当场炸）。
    """
    try:
        frame = vintage.pit_frame(root, as_of)
    except ValueError as exc:
        # 只有「as_of 之前一份快照都没有」这一种情况才改分类。
        # `pit_frame` 别的 ValueError 原样透出，不被吞。
        if snapshot_day(root, as_of) is None:
            raise NoPITSnapshotError(
                f"{as_of} 之前没有任何 PIT 快照（现有："
                f"{vintage.available_days(root) or '无'}）。"
                f"**拒绝用更新的快照代替**——那会把前视偏差请回来。"
            ) from exc
        raise
    used = frame.attrs.get(vintage.SNAPSHOT_DATE_ATTR)
    if used is None:
        raise PITLookaheadError(
            f"PIT 帧没有记录用了哪一天的快照（{vintage.SNAPSHOT_DATE_ATTR}），"
            f"无法证明它不晚于 {as_of}。")
    if str(used) > str(as_of):
        raise PITLookaheadError(
            f"用了 {used} 的快照来回答 {as_of} 的问题——**前视偏差**。"
            f"必须报错，不许降级。")
    return frame


# ── 财务侧整理 ──────────────────────────────────────────────────────

#: 整理时真正用到的列。**只留这几列**：真实帧 65,391 行 × 142 列，
#: 全量 copy 会把内存推到几百 MB，而 30 天的前瞻窗口要反复读它。
_KEPT_FIN_COLS = (SECURITY_CODE_COL, financial.REPORT_DATE_COL,
                   financial.NOTICE_DATE_COL, REPORT_TYPE_COL, BPS_COL)


def annual_from_frame(frame: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """PIT 帧 → 每只票**当时可见的最新一份年报**（纯函数，不碰文件系统）。

    口径（与 `scripts/fundamental_factor.py::load_annual` 逐条一致，
    但数据源是 PIT 帧而不是当前缓存）：

    1. `REPORT_TYPE == 年报`；
    2. 披露日可解析、`>= 1980-01-01`、且 `NOTICE_DATE >= REPORT_DATE + 30 天`；
    3. 披露日 ≤ `as_of`（对齐列取 `financial.ALIGN_DATE_COL`，即本仓的
       披露日对齐协议；`UPDATE_DATE` **不参与任何判断**）；
    4. 同一 (code, report_date) 取最后一条；
    5. 同一 code 取**披露日最新**的那一份报告期。

    返回列：`code` / `notice_date` / `report_date` / `report_year` / `bps`。

    ## 🚨 为什么**不**调 `financial.visible_as_of`

    它的去重是 `drop_duplicates(subset=[REPORT_DATE_COL])`——**全局**按报告期去重，
    **不带股票键**。docstring 写的是「**同一报告期**若有重复披露日，取最后一条」，
    而「同一报告期」在多股票横截面上是**每只票各自**的。

    实测（2026-10-20 快照）：年报 **16,646 行 / 5,460 只** ⇒ 经
    `visible_as_of` 之后只剩 **14 行**（= 14 个不同的 `REPORT_DATE`）。
    ⇒ 整个横截面塌成「每个报告期一行」⇒ 因子**看起来存在**但几乎没有值。

    它在 `tests/test_financial_lookahead.py` 的夹具里看不出来，
    因为那些夹具只有一只票。本模块**不调用它**，改用同样的过滤条件
    （`ALIGN_DATE_COL`）+ **按 (code, report_date) 的去重**。
    ⇒ 这条缺陷**未修**（`aplus/financial.py` 本轮不动），
    但它已经被实测量化并记录在此。
    """
    missing = [c for c in _REQUIRED_FIN_COLS if c not in frame.columns]
    if missing:
        raise ValueError(
            f"PIT 财务帧缺少必需列 {missing}；实际列数 {len(frame.columns)}。"
            f"缺列不降级——那会让 BM 变成另一个量。")

    norm = financial.normalize_financial(
        frame.loc[:, [c for c in _KEPT_FIN_COLS if c in frame.columns]])
    ann = financial.annual_reports(norm)

    notice = ann[financial.NOTICE_DATE_COL]
    report = ann[financial.REPORT_DATE_COL]
    bad = notice.isna() | (notice < pd.Timestamp(MIN_NOTICE))
    bad |= (notice - report) < pd.Timedelta(days=MIN_LAG_DAYS)
    ann = ann.loc[~bad].copy()

    # 披露日对齐：**只**用 `ALIGN_DATE_COL`（= NOTICE_DATE）。
    # 过滤条件与 `financial.visible_as_of` 逐字相同；
    # 去重**不**用它（原因见 docstring）。
    aligned = ann[financial.ALIGN_DATE_COL]
    ann = ann.loc[aligned.notna() & (aligned <= pd.Timestamp(as_of))]
    if ann.empty:
        return pd.DataFrame(columns=["code", "notice_date", "report_date",
                                     "report_year", "bps"])

    out = pd.DataFrame({
        "code": ann[SECURITY_CODE_COL].astype(str).str.zfill(6),
        "notice_date": ann[financial.NOTICE_DATE_COL],
        "report_date": ann[financial.REPORT_DATE_COL],
        "bps": pd.to_numeric(ann[BPS_COL], errors="coerce"),
    })
    out["report_year"] = out["report_date"].dt.year
    # 同一 (code, report_date) 去重（源可能给多行），再取每只票「披露日最新」的那一份。
    out = (out.sort_values("notice_date")
              .drop_duplicates(subset=["code", "report_date"], keep="last")
              .sort_values(["code", "notice_date", "report_date"])
              .drop_duplicates(subset=["code"], keep="last")
              .reset_index(drop=True))
    return out[["code", "notice_date", "report_date", "report_year", "bps"]]


def pit_annual_frame(root, as_of: str) -> pd.DataFrame:
    """`pit_frame` + `annual_from_frame`。"""
    return annual_from_frame(pit_frame(root, as_of), as_of)


# ── 代码口径 ────────────────────────────────────────────────────────

def bare_code(code) -> str:
    """把代码归一成**裸 6 位**（去掉交易所后缀）。

    ## 为什么需要它（本仓踩过的坑）

    平台里代码有**两种格式**：

    | 来源 | 格式 | 例 |
    |---|---|---|
    | `master/daily` 文件名 / `daily_map` 键 / `xs[day]["code"]` | 带后缀 | `000001.SZ` |
    | 财务帧 `SECURITY_CODE`（东财） | 裸 6 位 | `000001` |

    两边直接 merge ⇒ **零行命中**，而 merge 静默返回空表 ⇒
    BM 全 NaN ⇒ 因子「看起来存在」但一个数都没有。

    实测本仓 `master/daily` 5460 个文件名的裸码**两两不重复**
    （`000001.SZ` 与 `000001.SH` 不同时存在）⇒ 归一化是**单射**，
    不会把两只票挤进同一个身份里。
    """
    s = str(code).strip()
    if "." in s:
        s = s.split(".", 1)[0]
    return s.zfill(6)


# ── 价格侧 ──────────────────────────────────────────────────────────

def close_on(daily_map: dict, as_of: str) -> pd.Series:
    """每只票在 `as_of` 那天的**不复权**收盘价。

    索引是 **`daily_map` 的原始键**（如 `000001.SZ`），
    与 `xs[day]["code"]` 同一格式 ⇒ 可以直接 `map` 上去，不必再转一次。

    ## 位置口径与生产代码**逐字**一致

    `aplus/factor_ic.py::forward_returns` 与
    `scripts/horizon_sweep.py::forward_return` 都用

        pos = int(np.searchsorted(dates, after_date, side="right")) - 1

    取「`as_of` 当天那根 bar」（含当天；当天没 bar 就取之前最后一根）。
    这里照抄那一句，不另发明一种「取当天」的语义。

    **不复权**：`close` 原值。BM 的分子 BPS 也是每股口径，
    用后复权价会让一次送股凭空改变 BM（见模块 docstring）。
    """
    out: dict[str, float] = {}
    for code, df in (daily_map or {}).items():
        if df is None or len(df) == 0 or "close" not in df.columns:
            continue
        s = df.sort_values("date", kind="stable")
        dates = s["date"].astype(str).to_numpy()
        pos = int(np.searchsorted(dates, str(as_of), side="right")) - 1
        if pos < 0:
            continue
        out[code] = float(s["close"].iloc[pos])
    return pd.Series(out, dtype=float)


# ── 因子 ────────────────────────────────────────────────────────────

def bm_from_annual(ann: pd.DataFrame, px: pd.Series) -> pd.Series:
    """年报 + 价格 → `code -> bm`。**唯一实现**（不许第二份副本）。

    `bm = log(BPS / close)`，**越大越便宜**。
    非正账面值 / 非正价格 ⇒ 丢弃，**不取绝对值、不取倒数**
    （负账面值取绝对值会把「资不抵债」变成「极度便宜」）。

    ## join 走**裸 6 位**码

    `ann["code"]` 是裸 6 位（东财 `SECURITY_CODE`），
    `px.index` 是 `daily_map` 的原始键（带交易所后缀）⇒ 直接 merge **零行命中**。
    ⇒ 两边都过 `bare_code` 再 join；返回值索引**还原成 `px` 的原始键**，
    这样调用方（`attach_pit_bm`）可以直接 `map` 到 `xs[day]["code"]`。
    """
    if ann is None or len(ann) == 0 or px is None or len(px) == 0:
        return pd.Series(dtype=float)
    a = pd.DataFrame({"bare": ann["code"].map(bare_code).to_numpy(),
                      "bps": ann["bps"].to_numpy()})
    p = pd.DataFrame({"bare": [bare_code(c) for c in px.index],
                      "px_key": list(px.index),
                      "close": px.to_numpy()})
    merged = a.merge(p, on="bare", how="left")
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = merged["bps"] / merged["close"]
        value = np.log(ratio.where(ratio > 0))
    vals = pd.Series(value.to_numpy(), index=merged["px_key"].to_numpy())
    vals = vals[np.isfinite(vals)]
    return vals[~vals.index.duplicated(keep="first")]


def price_index(daily_map: dict) -> dict:
    """把 `daily_map` 变成 `{code: (dates 数组, close 数组)}`（**排序只做一次**）。

    ## 为什么要有它

    `attach_pit_bm` 要对**每一天**取一次全池收盘价。
    若每一天都重排 5,460 个 DataFrame ⇒ 6 天就是 32,760 次排序
    （实测每轮几十秒 ⇒ 整个前瞻窗口要多花几分钟）。
    ⇒ 排一次、缓存数组，逐日只做 `searchsorted`。

    ⚠️ 排序**不假设**原文件已按日期有序（`aplus/panel.py` 同样自己排），
    用 `kind="stable"` 保持同日期行的原顺序。
    """
    out: dict = {}
    for code, df in (daily_map or {}).items():
        if df is None or len(df) == 0 or "close" not in df.columns:
            continue
        s = df.sort_values("date", kind="stable")
        out[code] = (s["date"].astype(str).to_numpy(),
                     s["close"].to_numpy(dtype=float))
    return out


def closes_from_index(index: dict, as_of: str) -> pd.Series:
    """`price_index` + 某个 `as_of` ⇒ 收盘价 Series（索引 = `daily_map` 原始键）。"""
    out: dict[str, float] = {}
    for code, (dates, closes) in index.items():
        pos = int(np.searchsorted(dates, str(as_of), side="right")) - 1
        if pos < 0:
            continue
        out[code] = float(closes[pos])
    return pd.Series(out, dtype=float)


def pit_bm_frame(root, as_of: str, daily_map: dict) -> pd.DataFrame:
    """`as_of` 当天的 PIT BM 横截面。

    返回 `DataFrame[code, bm]`，附带 `attrs`：

    - `snapshot_date`：**实际**用了哪一天的快照（回退时 ≠ `as_of`）
    - `requested_date`：请求的日期
    - `n_codes` / `n_with_bm`：覆盖数
    - `n_annual`：年报记录数

    ## 日期不足 ⇒ 报错

    `as_of` 早于第一份快照 ⇒ `NoPITSnapshotError`。
    **不返回空帧、不静默跳过、不拿更新的快照代替。**
    理由（`aplus/vintage.py` 的 docstring 逐字）：
    「拿最新的一份凑数 = 多了一个『已做 PIT』的标签，
    那比不做 PIT 更糟，因为它让前视偏差免于被审查。」
    """
    frame = pit_frame(root, as_of)
    ann = annual_from_frame(frame, as_of)
    vals = bm_from_annual(ann, close_on(daily_map, as_of))
    bm = pd.DataFrame({"code": vals.index.to_numpy(),
                       FACTOR_NAME: vals.to_numpy()})
    bm.attrs["requested_date"] = str(as_of)
    bm.attrs["snapshot_date"] = frame.attrs.get(vintage.SNAPSHOT_DATE_ATTR)
    bm.attrs["n_codes"] = int(bm["code"].nunique())
    bm.attrs["n_with_bm"] = int(len(bm))
    bm.attrs["n_annual"] = int(len(ann))
    return bm


def attach_pit_bm(xs: dict, root=None, daily_map: dict | None = None) -> dict:
    """把 PIT BM **挂进** `xs[day]` 的横截面（**就地**多一列 `bm`）。

    返回**报告**（不是把报告塞进 `xs`）：

        {"days": [...有 PIT BM 的日子...],
         "unavailable_days": [...没有 PIT 快照的日子...],
         "n_days": 总天数, "n_with_bm": 挂了 bm 的天数}

    ## 为什么**不**把报告写进 `xs["_pit_bm_*"]`

    `tests/test_cross_section_metadata_contract.py` 的规矩：
    `cross_sections` 的下划线元数据键，**读的每个键都必须有写的地方**，
    而写入点只认 `build_cross_sections` 的字典字面量与 `setdefault`。
    本模块是**后处理**步骤，往 `xs` 里塞新键会被那条守卫判成
    「只被读、从没被写过 ⇒ 调用方拿到的一直是默认值」——
    与 `_cost` 那种「假装能调」同形。

    ⇒ **改设计，不改守卫**（守卫是别人的，放宽它比改自己的代码贵得多）。
    ⇒ 报告走**返回值**：调用方拿不拿得到它，是它自己的事；
    但 `scripts/forward_validate.py` 会把它**打印出来**。

    ## 为什么挂在**每一个**日子上

    `aplus/factor.py::composite_score` 对缺失的因子列会 **KeyError**。
    若某天没有 `bm` 列，含 `bm` 的候选会当场崩——而崩的是**整个前瞻验证**，
    连不带 `bm` 的老批次一起陪葬。
    ⇒ 所以**每一天都有 `bm` 列**；没有 PIT 快照的日子，该列**全为 NaN**
    ⇒ `composite_score` 的 `min_count` 规则会把那天的股票全部剔掉
    ⇒ 那天**不出信号**，而不是**出零信号**。

    ## 但「全 NaN」不许静默

    哪些日子没有 PIT 快照，**必须**从返回值里读出来。
    「那天没有数据」与「那天信号为零」长得一模一样。

    ## 前视偏差不静默

    任何一天若解析出**晚于该天**的快照 ⇒ 抛 `PITLookaheadError`，
    绝不降级。
    """
    if root is None:
        root = vintage.VINTAGE_ROOT
    if daily_map is None:
        daily_map = xs.get("_daily_map") or {}

    days = list(xs.get("_dates") or [])
    unavailable: list[str] = []
    computed: list[str] = []
    # 缓存按**实际用的那一天**存**整理后的年报帧**（5 列 × 5,460 行 ≈ 200 KB）。
    #
    # 🚨 为什么不缓存**原始** PIT 帧：它 65,391 行 × 142 列 ≈ 几百 MB，
    # 而前瞻窗口是**逐日**的 ⇒ 缓存 N 天就是 N × 几百 MB
    # ⇒ 与 GB 级面板叠加会 OOM（本仓唯一的 OOM 来源）。
    # ⇒ 原帧读完即弃，只留整理后的小表。
    ann_cache: dict[str, pd.DataFrame] = {}
    # 价格索引排一次就够（逐日只做 `searchsorted`）——见 `price_index`。
    px_index = price_index(daily_map)

    for day in days:
        uni = xs.get(day)
        if uni is None or uni.empty:
            continue
        used = snapshot_day(root, day)
        if used is None:
            uni = uni.copy()
            uni[FACTOR_NAME] = np.nan
            xs[day] = uni
            unavailable.append(day)
            continue
        if used not in ann_cache:
            ann_cache[used] = annual_from_frame(pit_frame(root, day), day)
        ann = ann_cache[used]
        vals = bm_from_annual(ann, closes_from_index(px_index, day))
        uni = uni.copy()
        uni[FACTOR_NAME] = uni["code"].map(vals) if len(vals) else np.nan
        xs[day] = uni
        computed.append(day)

    return {"days": computed, "unavailable_days": unavailable,
            "n_days": len(days), "n_with_bm": len(computed)}


__all__ = [
    "FACTOR_NAME", "BPS_COL", "MIN_LAG_DAYS", "MIN_NOTICE",
    "NoPITSnapshotError", "PITLookaheadError",
    "snapshot_day", "pit_frame", "annual_from_frame", "pit_annual_frame",
    "close_on", "price_index", "closes_from_index", "bm_from_annual",
    "pit_bm_frame", "attach_pit_bm",
]
