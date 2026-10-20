#!/usr/bin/env python
"""**信号作用域**（signal scoping）：前瞻证据「属于哪个信号」的**唯一入口**。

## 为什么需要它（预注册：`docs/PREREG_SIGNAL_SCOPING_2026-10-21.md`）

前瞻底稿 `data/reports/verification.parquet` 是**追加式**的。
若 D1 的**信号定义**变了（改权重 / 改榜宽 / 改可买性过滤），
同一本底稿里就会**同时**装着**两个信号**产生的行。

⇒ 「这个打分器有没有排序力」这个问题，**必须**只问**当前信号**的行。
读整本底稿 = **判的是另一个信号**。

## ★ 归属靠**逐行证据**，不靠假设（四态）

| status | 何时 | 作用域内的行 |
|---|---|---|
| `OK` | 底稿**有** `signal_id` 列，**且**至少一行属于当前信号 | 该信号的全部行 |
| **`ATTRIBUTED_BY_WEIGHTS_HASH`** | 底稿**没有** `signal_id` 列，**但有** `weights_hash` 列 ⇒ **逐行**用权重指纹判归属 | `weights_hash` == 当前信号权重指纹的行 |
| `NO_MATCHING_SIGNAL` | 有 `signal_id` 列，但**没有一行**属于当前信号 | **空** |
| `MISSING_SIGNAL_ID_COLUMN` | **无任何归属证据**（两列都没有 / 列在但算不出当前信号 id） | **空** |

### 🚨 「列不存在 ⇒ 当作全部都是当前信号」这个禁止项**仍然成立**

`ATTRIBUTED_BY_WEIGHTS_HASH` **不是**假定，是**逐行拿证据比**：
不匹配的行**剔出并单独计数**（`rows_unmatched`）。
⇒ 它与 `MISSING_SIGNAL_ID_COLUMN` 的区别**就是「有没有证据」**。

### ⚠️ `weights_hash` 基准的**边界**（必须随数字离开产物）

`weights_hash` 只记**权重** ⇒ 它证明「**同一组权重**」，
**不**证明「**同一个信号**」（`top_n`/`bottom_n` 变了它**不变**）。
⇒ 归到 `attribution.note` 里，读者必须知道这条证据覆盖到哪一层。

## 🚨 本模块**不重写**任何指纹

| 指纹 | 提供方 | 本模块的动作 |
|---|---|---|
| 信号指纹（8 位） | `aplus/reports.py::signal_fingerprint` / `current_signal_id` | **只调用** |
| 权重指纹（12 位） | `aplus/reports.py::weights_fingerprint` | **只调用** |

自己实现就是第二份真相源（第 97 层：两份副本之间没有守卫）。

## 🚨 缺证据 ⇒ 响亮失败，绝不回落

「算不出当前信号 id」时**不许**退回「就当全部都是当前信号」；
「算不出当前权重指纹」时**不许**退回「就当全部行都匹配」。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 底稿里记「这一行是用哪套信号产生的」的那一列。
SIGNAL_ID_COLUMN = "signal_id"
#: 底稿里**早就存在**的那一列：「这一行是用哪组权重排出来的」。
#: 它是 `aplus/reports.py::WEIGHTS_HASH_COLUMN` 的同一个名字（12 位十六进制）。
WEIGHTS_HASH_COLUMN = "weights_hash"

#: —— 四态（见模块 docstring）——
STATUS_OK = "OK"
STATUS_ATTRIBUTED_BY_WEIGHTS_HASH = "ATTRIBUTED_BY_WEIGHTS_HASH"
STATUS_NO_MATCHING_SIGNAL = "NO_MATCHING_SIGNAL"
STATUS_MISSING_SIGNAL_ID_COLUMN = "MISSING_SIGNAL_ID_COLUMN"

#: 归属依据（写进产物，让读者知道「证据覆盖到哪一层」）。
BASIS_SIGNAL_ID = "signal_id"
BASIS_WEIGHTS_HASH = "weights_hash"
BASIS_NONE = "none"

#: 判决层的「不可判定」标签。
#: 🚨 它**不是** `PASS`，**不是** `NOT_PASSED_MEASURED`，**不是** `POWERLESS` ——
#: 它说的是「**这份证据不可归属**」，**不是**「当前信号没有排序力」。
VERDICT_NOT_DECIDABLE = "NOT_DECIDABLE_NO_SIGNAL_SCOPE"


@dataclass(frozen=True)
class ScopedLedger:
    """一次作用域解析的**全部**结果（过滤后的帧 + 归属依据 + 口径 + 缺失说明）。

    🚨 `frame` 在 `status` 不是 `OK` / `ATTRIBUTED_BY_WEIGHTS_HASH` 时是**空帧**，
    绝不是整本底稿。
    """

    frame: pd.DataFrame
    status: str
    attribution_basis: str = BASIS_NONE
    current_signal_id: str | None = None
    current_weights_fingerprint: str | None = None
    ledger_signal_ids: list[str] = field(default_factory=list)
    rows_matched: int = 0
    rows_unmatched: int = 0
    rows_unattributable: int = 0
    coverage: dict = field(default_factory=dict)
    reason: str = ""

    @property
    def has_evidence(self) -> bool:
        """**有没有可用的归属证据**。

        ⇒ `False` 时判决层**必须**写 `NOT_DECIDABLE_NO_SIGNAL_SCOPE`
        （那是「不可归属」，**不是**「测不出」）。
        """
        return self.attribution_basis != BASIS_NONE

    @property
    def usable(self) -> bool:
        """作用域**非空** ⇒ 可以正常测量。"""
        return (self.status in (STATUS_OK, STATUS_ATTRIBUTED_BY_WEIGHTS_HASH)
                and len(self.frame) > 0)

    @property
    def empty(self) -> bool:
        return self.frame is None or len(self.frame) == 0


@dataclass(frozen=True)
class AttributionContext:
    """当前信号身份的两个候选基准（都**可能**拿不到）。"""

    signal_id: str | None = None
    weights_fingerprint: str | None = None
    signal_id_error: str | None = None
    weights_fingerprint_error: str | None = None


# ——————————————————————————————————————————————————————————————
# 内部：口径统计
# ——————————————————————————————————————————————————————————————

def _date_col(frame: pd.DataFrame, col: str) -> pd.Series:
    """取一列日期并**归一成字符串**（`NaT`/缺失 ⇒ 空串）。

    ⚠️ 归一是必须的：`pred_date` 在不同来源里可能是 `str` 也可能是 `Timestamp`，
    而作用域块要能**逐位比较** ⇒ 统一成 `YYYY-MM-DD` 字符串。
    """
    if col not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype="object")
    raw = frame[col]
    out = raw.astype("string").str.slice(0, 10).fillna("")
    return out.astype("object")


def _stats(frame: pd.DataFrame) -> dict:
    """一份底稿（或其中一段）的**口径统计**。

    `entry_dates` = **独立成交窗口数**（出单日 ≠ 成交日，见
    `scripts/score_ranking_power.py::_independence_caliber`）。
    """
    n = int(len(frame))
    if n == 0:
        return {
            "rows": 0, "pred_dates": 0, "entry_dates": 0,
            "pred_date_min": None, "pred_date_max": None,
            "entry_date_min": None, "entry_date_max": None,
        }
    pred = _date_col(frame, "pred_date")
    entry = _date_col(frame, "entry_date")
    pred_nz = pred[pred != ""]
    entry_nz = entry[entry != ""]
    return {
        "rows": n,
        "pred_dates": int(pred_nz.nunique()),
        "entry_dates": int(entry_nz.nunique()),
        "pred_date_min": (str(pred_nz.min()) if len(pred_nz) else None),
        "pred_date_max": (str(pred_nz.max()) if len(pred_nz) else None),
        "entry_date_min": (str(entry_nz.min()) if len(entry_nz) else None),
        "entry_date_max": (str(entry_nz.max()) if len(entry_nz) else None),
    }


def _normalized(frame: pd.DataFrame, column: str) -> pd.Series:
    """把一列归一成字符串（空/NaN ⇒ `""`）。

    ⚠️ 归一成 `""` 而不是留 `NaN`，是为了让「空」和「缺失」在计数时
    **只有一种表示** ⇒ 不会出现两份副本各数一次（第 17 层的形状）。
    """
    return frame[column].astype("string").str.strip().fillna("").astype("object")


# ——————————————————————————————————————————————————————————————
# ★ 唯一的过滤实现
# ——————————————————————————————————————————————————————————————

def scope_ledger(frame: pd.DataFrame, signal_id: str | None = None,
                 weights_fingerprint: str | None = None) -> ScopedLedger:
    """把一份底稿**按信号身份**切开，返回**当前信号的那一段**。

    ## 纯函数

    不碰文件系统、不读生产配置 ⇒ **1 ms 可测、可变异**。
    两个基准由调用方从 `resolve_attribution_context()` 拿。

    ## 基准的选择（**按底稿带着什么证据**）

    | 底稿带着 | 用哪个基准 |
    |---|---|
    | `signal_id` 列 **且** 当前信号 id 算得出 | `signal_id`（更强的证据，优先） |
    | `weights_hash` 列 **且** 当前权重指纹算得出 | `weights_hash`（**逐行**比对） |
    | 都没有 | `none` ⇒ **无任何归属证据** |

    🚨 **绝不在没有证据时放行整本底稿。**
    🚨 **绝不**在基准不可用时默默换成「全部匹配」。
    """
    frame = frame if frame is not None else pd.DataFrame()
    ledger = _stats(frame)

    # ── 基准一：`signal_id`（优先） ──
    if SIGNAL_ID_COLUMN in frame.columns and signal_id:
        return _scope_by_column(frame, ledger, SIGNAL_ID_COLUMN, str(signal_id),
                                BASIS_SIGNAL_ID,
                                current_signal_id=str(signal_id),
                                current_weights_fingerprint=weights_fingerprint,
                                no_target_reason=(
                                    "底稿有 `signal_id` 列，但**当前信号 id 算不出来**"
                                    "⇒ 这一列无法用来判归属。"))

    # ── 基准二：`weights_hash`（逐行） ──
    if WEIGHTS_HASH_COLUMN in frame.columns and weights_fingerprint:
        return _scope_by_column(frame, ledger, WEIGHTS_HASH_COLUMN,
                                str(weights_fingerprint), BASIS_WEIGHTS_HASH,
                                current_signal_id=(str(signal_id) if signal_id else None),
                                current_weights_fingerprint=str(weights_fingerprint),
                                no_target_reason=(
                                    "底稿有 `weights_hash` 列，但**当前权重指纹算不出来**"
                                    "⇒ 这一列无法用来判归属。"))

    # ── 无任何归属证据 ──
    has_sid_col = SIGNAL_ID_COLUMN in frame.columns
    has_wh_col = WEIGHTS_HASH_COLUMN in frame.columns
    if has_sid_col and not signal_id:
        why = ("底稿**有** `signal_id` 列，但**当前信号 id 算不出来**"
               "（契约未落地 / 报错）")
    elif has_wh_col and not weights_fingerprint:
        why = ("底稿**有** `weights_hash` 列，但**当前权重指纹算不出来**")
    else:
        why = (f"底稿**既没有** `{SIGNAL_ID_COLUMN}` 列，**也没有** "
               f"`{WEIGHTS_HASH_COLUMN}` 列")
    return ScopedLedger(
        frame=frame.iloc[0:0].copy(),
        status=STATUS_MISSING_SIGNAL_ID_COLUMN,
        attribution_basis=BASIS_NONE,
        current_signal_id=(str(signal_id) if signal_id else None),
        current_weights_fingerprint=(
            str(weights_fingerprint) if weights_fingerprint else None),
        coverage=_coverage(ledger, ledger, 0, 0, 0),
        reason=(
            f"{why} ⇒ **无任何归属证据** ⇒ "
            f"**不可用于当前信号的判决**。"
            f"它覆盖的范围：{_range_text(ledger)}。"
            "🚨 **这不是回落**：绝不允许「无证据 ⇒ 当作全部都是当前信号」。"),
    )


def _scope_by_column(frame: pd.DataFrame, ledger: dict, column: str,
                     target: str, basis: str, *,
                     current_signal_id: str | None,
                     current_weights_fingerprint: str | None,
                     no_target_reason: str) -> ScopedLedger:
    """按**一列**逐行判归属（`signal_id` 与 `weights_hash` 共用这一份实现）。

    ⚠️ 两列**共用**这一段代码 ⇒ 不可能有「两份副本各自漂移」（第 97 层）。
    """
    norm = _normalized(frame, column)
    present = norm != ""
    matches = present & (norm == target)

    in_scope = frame[matches.to_numpy()]
    unattributed = int((~present).sum())
    unmatched = int((present & ~matches).sum())
    seen = sorted({str(v) for v in norm[present].unique()})

    scope = _stats(in_scope)
    coverage = _coverage(ledger, scope, len(in_scope), unattributed, unmatched)

    if len(in_scope) == 0:
        if unattributed and not unmatched:
            reason = (
                f"底稿有 `{column}` 列，但**没有一行的值等于当前值 {target!r}**"
                f"（{unattributed} 行的该列为空 ⇒ **不可归属**）"
                f" ⇒ 当前信号在这个底稿里**一个窗口都没有** ⇒ **不可用于判决**。"
                f"底稿覆盖范围：{_range_text(ledger)}。")
        else:
            reason = (
                f"底稿的 `{column}` 取值是 {seen}，当前值是 {target!r} ⇒ "
                f"**当前信号在这个底稿里一个窗口都没有** ⇒ **不可用于判决**。"
                f"底稿覆盖范围：{_range_text(ledger)}。")
        return ScopedLedger(
            frame=in_scope,
            status=STATUS_NO_MATCHING_SIGNAL,
            attribution_basis=basis,
            current_signal_id=current_signal_id,
            current_weights_fingerprint=current_weights_fingerprint,
            ledger_signal_ids=seen,
            rows_matched=0, rows_unmatched=unmatched,
            rows_unattributable=unattributed,
            coverage=coverage,
            reason=reason,
        )

    if basis == BASIS_WEIGHTS_HASH:
        note = (
            f"本底稿**没有 `{SIGNAL_ID_COLUMN}` 列**（它写在信号分桶机制落地之前）"
            f"⇒ 归属用 `{WEIGHTS_HASH_COLUMN}` **逐行**判定。")
        bound = (
            "⚠️ `weights_hash` 只记**权重** ⇒ 它证明「**同一组权重**」，"
            "**不**证明「**同一个信号**」（`top_n`/`bottom_n` 变了它**不变**）。")
    else:
        note = (f"归属用 `{SIGNAL_ID_COLUMN}` **逐行**判定。")
        bound = ""
    return ScopedLedger(
        frame=in_scope,
        status=(STATUS_OK if basis == BASIS_SIGNAL_ID
                else STATUS_ATTRIBUTED_BY_WEIGHTS_HASH),
        attribution_basis=basis,
        current_signal_id=current_signal_id,
        current_weights_fingerprint=current_weights_fingerprint,
        ledger_signal_ids=seen,
        rows_matched=len(in_scope), rows_unmatched=unmatched,
        rows_unattributable=unattributed,
        coverage=coverage,
        reason=(
            f"当前值 {target!r} 在这份底稿里有 {scope['rows']} 行 / "
            f"{scope['pred_dates']} 个出单日 / {scope['entry_dates']} 个独立成交窗口"
            f"（整本底稿 {ledger['rows']} 行 / {ledger['pred_dates']} 出单日 / "
            f"{ledger['entry_dates']} 成交窗口；"
            f"另有 {unattributed} 行不可归属、{unmatched} 行不匹配）。"
            + (" " + note if note else "") + (" " + bound if bound else "")),
    )


def _coverage(ledger: dict, scope: dict, n_scope: int,
              unattributed: int, other: int) -> dict:
    """作用域块的内容（口径随数字离开脚本）。"""
    return {
        "ledger_rows_total": ledger["rows"],
        "ledger_pred_dates": ledger["pred_dates"],
        "ledger_entry_dates": ledger["entry_dates"],
        "ledger_date_range": {
            "pred_date_min": ledger["pred_date_min"],
            "pred_date_max": ledger["pred_date_max"],
            "entry_date_min": ledger["entry_date_min"],
            "entry_date_max": ledger["entry_date_max"],
        },
        "rows_in_scope": int(n_scope),
        "pred_dates_in_scope": scope["pred_dates"],
        "entry_dates_in_scope": scope["entry_dates"],
        "date_range_in_scope": {
            "pred_date_min": scope["pred_date_min"],
            "pred_date_max": scope["pred_date_max"],
            "entry_date_min": scope["entry_date_min"],
            "entry_date_max": scope["entry_date_max"],
        },
        "rows_unattributed": int(unattributed),
        "rows_other_signals": int(other),
        "independent_unit": "entry_date（独立成交窗口）——出单日 ≠ 成交日",
    }


def _range_text(stats: dict) -> str:
    """人读的日期范围（一行）。**失败态也必须给**（让读者知道它是什么）。"""
    if stats["rows"] == 0:
        return "（空底稿）"
    pd_txt = (f"{stats['pred_date_min']} … {stats['pred_date_max']}"
              if stats["pred_date_min"] else "（无 pred_date）")
    ed_txt = (f"{stats['entry_date_min']} … {stats['entry_date_max']}"
              if stats["entry_date_min"] else "（无 entry_date）")
    return (f"{stats['rows']} 行、{stats['pred_dates']} 个出单日"
            f"（{pd_txt}）、{stats['entry_dates']} 个独立成交窗口（{ed_txt}）")


# ——————————————————————————————————————————————————————————————
# 产物里的两块：作用域 / 归属依据
# ——————————————————————————————————————————————————————————————

def scoped_ledger_frame(reports_dir, verification_path) -> pd.DataFrame:
    """读一份底稿并**按归属切好** —— 给**对账守卫**用。

    ## 为什么需要它

    产物层的守卫（「产物与一次新鲜重算逐项相等」）必须与**生产者**
    读**同一个口径**的样本 —— 否则它比的是两个不同的量（第 7 层）。
    ⇒ 守卫**必须**用它，而不是自己 `pd.read_parquet` 整本底稿。

    ⚠️ 它**不**重新实现归属：过滤**只**走 `scope_ledger`（第 97 层）。
    """
    ctx = resolve_attribution_context(reports_dir)
    frame = pd.read_parquet(Path(verification_path))
    return scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint).frame


def scope_block(scoped: ScopedLedger) -> dict:
    """作用域块（写进产物）。**口径随数字离开脚本**。"""
    block = {
        "status": scoped.status,
        "current_signal_id": scoped.current_signal_id,
        "current_weights_fingerprint": scoped.current_weights_fingerprint,
        "attribution_basis": scoped.attribution_basis,
        "signal_ids_in_ledger": list(scoped.ledger_signal_ids),
        "reason": scoped.reason,
        "verdict_if_not_attributable": VERDICT_NOT_DECIDABLE,
        "note": (
            "🚨 **归属不对 ⇒ 判据根本没资格跑。** "
            "`status` 不是 `OK` / `ATTRIBUTED_BY_WEIGHTS_HASH` 时，"
            "本产物**不给**任何关于当前信号的判决，"
            "只报告「这份底稿是什么」与「它为什么不可用」。"
            "（判定「测不出」不许写成「没有」。）"),
    }
    block.update(scoped.coverage)
    return block


def attribution_block(scoped: ScopedLedger) -> dict:
    """**归属依据**块（写进产物）。

    🚨 读者必须知道「这份判决的证据覆盖到哪一层」：
    `weights_hash` 只证明「同一组权重」，**不**证明「同一个信号」。
    """
    if scoped.attribution_basis == BASIS_WEIGHTS_HASH:
        note = (f"本底稿没有 `{SIGNAL_ID_COLUMN}` 列（它写在信号分桶机制落地之前）"
                f"⇒ 归属用 `{WEIGHTS_HASH_COLUMN}` 逐行判定。")
        bound = ("⚠️ `weights_hash` 只记**权重** ⇒ 它证明「**同一组权重**」，"
                 "**不**证明「**同一个信号**」（`top_n`/`bottom_n` 变了它**不变**）。")
    elif scoped.attribution_basis == BASIS_SIGNAL_ID:
        note = f"归属用 `{SIGNAL_ID_COLUMN}` 逐行判定（**最强的证据**）。"
        bound = ""
    else:
        note = ("**无任何归属证据** ⇒ 本产物**不给**关于当前信号的判决。")
        bound = ""
    return {
        "basis": scoped.attribution_basis,
        "current_weights_fingerprint": scoped.current_weights_fingerprint,
        "current_signal_id": scoped.current_signal_id,
        "rows_matched": int(scoped.rows_matched),
        "rows_unmatched": int(scoped.rows_unmatched),
        "rows_unattributable": int(scoped.rows_unattributable),
        "note": (note + " " + bound).strip(),
    }


def not_decidable_note(scoped: ScopedLedger) -> str:
    """CLI 用的🚨横幅（人读）。"""
    return (
        f"\n🚨🚨🚨 **不可判定：{scoped.status}** 🚨🚨🚨\n"
        f"  {scoped.reason}\n"
        f"  ⇒ 本产物**不给**关于当前信号的判决。"
        f"（`{VERDICT_NOT_DECIDABLE}` ≠ 「当前信号没有排序力」）\n")


# ——————————————————————————————————————————————————————————————
# 当前身份的两个基准（**只调 `aplus/reports.py` 的原语**）
# ——————————————————————————————————————————————————————————————

def resolve_current_signal_id(reports_dir) -> str:
    """从**生产的真实配置**算当前信号 id。

    🚨 **薄包装**：只调 `aplus.reports.current_signal_id`，**不自己实现指纹**。
    🚨 **算不出来就抛**，绝不回落成「就当全部都是当前信号」。
    """
    from aplus import reports  # 契约的所在地

    fn = getattr(reports, "current_signal_id", None)
    if fn is None:
        raise RuntimeError(
            "🚨 `aplus/reports.py` 里**还没有** `current_signal_id` —— "
            "信号作用域契约**未落地**。\n"
            "   本模块**故意不自己实现**它：那会变成第二份真相源，"
            "而两份真相源之间没有守卫（第 97 层）。")
    sid = fn(reports_dir)
    if sid is None or not str(sid).strip():
        raise RuntimeError(
            f"🚨 `current_signal_id({reports_dir!r})` 返回了 {sid!r} —— "
            "算不出当前信号 id。\n"
            "   ⇒ **不许回落**成「就当整本底稿都是当前信号」。")
    return str(sid).strip()


def resolve_current_weights_fingerprint(reports_dir) -> str:
    """当前信号**权重**的指纹（12 位）。

    🚨 **薄包装**：只调 `aplus/reports.py::weights_fingerprint`，
    权重取自 `aplus.evolve.load_weights(reports_dir)["weights"]` ——
    **与 `current_signal_id` 的默认来源同一个**（同一份真相源，不是第二份）。

    ⚠️ `weights_fingerprint` 只覆盖**权重**，不覆盖 `top_n`/`bottom_n`
    ⇒ 它的证明力**弱于**信号指纹（见 `attribution_block` 的 `note`）。
    """
    from aplus.evolve import load_weights
    from aplus.reports import weights_fingerprint

    weights = load_weights(reports_dir)["weights"]
    fp = weights_fingerprint(weights)
    if not fp:
        raise RuntimeError(
            f"🚨 `weights_fingerprint(load_weights({reports_dir!r})['weights'])` "
            f"返回了 {fp!r} —— 算不出当前权重指纹。\n"
            "   ⇒ **不许回落**成「就当全部行都匹配」。")
    return str(fp).strip()


def resolve_attribution_context(reports_dir) -> AttributionContext:
    """把两个基准**都**算出来；**任一失败都不抛**，只记录原因。

    ⚠️ 为什么失败不抛：底稿可能**只带得动**另一个基准
    （例如老底稿没有 `signal_id` 列 ⇒ 用 `weights_hash` 逐行判归属）。
    ⇒ 失败被**记进产物**（`attribution` 块 + `reason`），
    而**不是**被悄悄忽略。

    🚨 但**两个都失败** ⇒ `scope_ledger` 会给出
    `MISSING_SIGNAL_ID_COLUMN`（**无任何归属证据**）⇒ 判决层不可判定。
    """
    signal_id = None
    sid_err = None
    try:
        signal_id = resolve_current_signal_id(reports_dir)
    except Exception as exc:  # noqa: BLE001 — 契约缺失/报错 ⇒ 换基准，不是回落
        sid_err = f"{type(exc).__name__}: {exc}"

    wf = None
    wf_err = None
    try:
        wf = resolve_current_weights_fingerprint(reports_dir)
    except Exception as exc:  # noqa: BLE001
        wf_err = f"{type(exc).__name__}: {exc}"

    return AttributionContext(signal_id=signal_id, weights_fingerprint=wf,
                              signal_id_error=sid_err,
                              weights_fingerprint_error=wf_err)


def scoped_history(reports_dir) -> tuple["pd.DataFrame", "ScopedLedger"]:
    """读底稿并**按当前信号分桶**，返回 `(分桶后的帧, 作用域说明)`。

    ## 为什么要有这个共用入口（而不是调用方各写一遍）

    生产（`aplus/webapp.py` 的 `/api/status`）与判决脚本
    （`scripts/score_ranking_power.py` 等）**必须用同一套口径**
    ⇒ 否则会出现「**前端报 6 个窗口、闸门报 3 个窗口**」⇒ **两者打架**
    ⇒ 而打架的那一块正是**用户读的那个数**（第 97 层：两份副本之间没有守卫）。

    ## 缺归属证据 ⇒ 帧**为空**

    ⇒ 底稿**既没有** `signal_id` **也没有** `weights_hash` 列时，
    `status` = `MISSING_SIGNAL_ID_COLUMN` ⇒ 帧为空 ⇒
    **调用方必须据此说「不可判定」，不许渲染成「命中率 0%」**
    （那是**一个看起来正常的假数**）。
    """
    from aplus.reports import load_verification_history

    frame = load_verification_history(reports_dir)
    ctx = resolve_attribution_context(reports_dir)
    scoped = scope_ledger(frame, ctx.signal_id, ctx.weights_fingerprint)
    return scoped.frame, scoped


def upstream_scope_verdict(upstream_doc: Any, signal_id: str | None,
                           weights_fingerprint: str | None = None) -> dict:
    """上游产物的**作用域检查**（给不读 parquet、只读上游产物的那两个脚本用）。

    ## 三种「不许继续」

    | 上游 | 下游行为 |
    |---|---|
    | 没有 `signal_scope` 块 | **拒绝外推**（老产物 ⇒ 不可知） |
    | 块在，但 `status` 不在 {`OK`, `ATTRIBUTED_BY_WEIGHTS_HASH`} | **拒绝外推**（上游本身就不可判定） |
    | 块在且可用，但**能比的**身份与当前**不一致** | **拒绝外推**（上游是**别的信号**的判决） |

    ## 比什么（**从强到弱**）

    1. `current_signal_id`（**两边都算得出**时）
    2. `current_weights_fingerprint`（**信号 id 算不出**时 ⇒ 退到同一份证据）

    🚨🚨 **两边都算不出 ⇒ 拒绝外推**（**不许**把「两个都是 `None`」
    读成「它们一样」——那是**查不到 ≠ 没有**，第 123 层）。

    🚨 「块缺失」**不许**回落成「按上游的老口径继续用」⇒ 那是第 6 种形状。
    """
    doc = upstream_doc if isinstance(upstream_doc, dict) else {}
    block = doc.get("signal_scope")
    if not isinstance(block, dict):
        return {
            "ok": False,
            "reason": (
                "🚨 上游产物**没有 `signal_scope` 块** ⇒ 它的作用域**不可知** ⇒ "
                "**拒绝外推**。\n"
                "   （老产物没有这个块；「不可知」不许回落成「按老口径继续用」。）"),
            "upstream_scope": None,
        }
    status = block.get("status")
    if status not in (STATUS_OK, STATUS_ATTRIBUTED_BY_WEIGHTS_HASH):
        return {
            "ok": False,
            "reason": (
                f"🚨 上游产物的作用域状态是 {status!r}"
                f"（可用的是 {STATUS_OK!r} / {STATUS_ATTRIBUTED_BY_WEIGHTS_HASH!r}）"
                f"⇒ 上游**本身就不可判定** ⇒ **拒绝外推**。"),
            "upstream_scope": block,
        }

    up_sid = block.get("current_signal_id")
    if signal_id and up_sid:
        if up_sid != signal_id:
            return {
                "ok": False,
                "reason": (
                    f"🚨 上游产物的信号是 {up_sid!r}，当前信号是 {signal_id!r} ⇒ "
                    f"**不是同一个信号** ⇒ **拒绝外推**。"),
                "upstream_scope": block,
            }
        return {
            "ok": True,
            "reason": f"上游产物的作用域与当前信号 {signal_id!r} 一致。",
            "upstream_scope": block,
        }

    up_wf = block.get("current_weights_fingerprint")
    if weights_fingerprint and up_wf:
        if up_wf != weights_fingerprint:
            return {
                "ok": False,
                "reason": (
                    f"🚨 上游产物的权重指纹是 {up_wf!r}，当前是 "
                    f"{weights_fingerprint!r} ⇒ **不是同一组权重** ⇒ "
                    f"**拒绝外推**。"),
                "upstream_scope": block,
            }
        return {
            "ok": True,
            "reason": (
                f"上游产物与当前配置的**权重指纹**一致（{weights_fingerprint!r}）"
                "（当前信号 id 算不出 ⇒ 退到同一份证据；⚠️ 权重指纹**不**覆盖 "
                "`top_n`/`bottom_n`）。"),
            "upstream_scope": block,
        }

    return {
        "ok": False,
        "reason": (
            "🚨 上游产物与当前配置的**身份无法比对**"
            f"（上游 `current_signal_id`={up_sid!r}、`current_weights_fingerprint`"
            f"={up_wf!r}；当前 {signal_id!r} / {weights_fingerprint!r}）⇒ "
            "**不可知** ⇒ **拒绝外推**。\n"
            "   🚨 两个都算不出时**不许**读成「它们一样」（查不到 ≠ 没有）。"),
        "upstream_scope": block,
    }
