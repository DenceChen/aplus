#!/usr/bin/env python
"""**目标达成进度** —— 每日一份快照，页面自己长一格。

用户 2026-10-21 的原话：

    「web 页面加一个 tab 页面 记录目标达成页面。记录目标达成进度，
      **不用每次我都问你**。」

⇒ 交付物是**一个常驻的 tab**，内容是**目标达成进度**，而且**自己会更新**。

## 它是什么

一份**每日快照产物** `data/reports/goal_progress_<日期>.json`
⇒ `/api/goal-progress` 递「最新 + 历日历史」
⇒ 页面「目标进度」tab 渲染它。

⇒ 用户不用问，页面每天自己长一格。

## 铁律（本文件存在的理由）

1. 🚨 **每个数都直读产物**，并带 `产物路径 :: JSON 路径`（`cited()`）。
   ⇒ **不许手抄、不许用公式重算**（第 109 层）。
2. 🚨 **缺档 ⇒ `value: null` + 原因**，**绝不回落成 0**
   （第 89 层：拒绝出判决 ≠ 判决为空）。
3. 🚨 **「测不出」不许写成「达不到」/「接近」**
   ⇒ `measured_sign_determinable = false` 时目标 1 的状态**必须**是
   `UNDECIDABLE`，不许是「未达」也不是「达成」。
4. 🚨 **两个 D2 口径并排，各带标签**
   （实盘档 / 理想档）⇒ 不许合并、不许取极值、不许拿一个顶替另一个（第 143 层）。
5. 🚨 **倍数那一族里只有 `same_caliber` 是缺口**
   （台账自己标 `cross_caliber.is_the_gap = false`）⇒ 屏上的「还差几倍」
   **必须**取 `same_caliber`。
6. 🚨 **目标 4/5/6 没有可自动化的度量就不造一个数**
   ⇒ 「可自动化的部分」与「**由人判断**的部分」分开写，
   「不知道」不许塌成 0 或假装有（第 7 层）。
7. 🚨 **不许算「8 条目标的达成率」**
   ⇒ 归档表已经把分母定死 = **D1 + D2 两项**；手段类**不产生分数**
   （`docs/DELIVERY_DECISIONS_2026-10-18.md §1`）。

## 口径纪律（哪些字段**不许**出现在屏上）

* 🚨 **「投入率」「闲置比例」一个都不端出去**
  ⇒ `deployed_d2_verdict.json` 里 `idle_cash_ratio_mean`（累计现金 ÷ 原始本金）
  与 `lot_investable_rate`（**名字与内容相反**）都不可用
  （`docs/TODO_deployed_idle_cash_caliber_2026-10-21.md`）。
  ⇒ 本文件**不读**它们，屏上只给定性的一句。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aplus import provenance  # noqa: E402

# 🚨 D1 的验收分母门槛（W ≥ 10 独立成交窗口）的**唯一实现**在
# `aplus/webapp.py::MIN_INDEPENDENT_WINDOWS` ⇒ 这里 **import 它**，
# 不许另抄一份（形状 17：同一语义被处理 N−1 次 ⇒ 每份副本都会各自腐烂）。
# 判据出处（**事前**登记）：`docs/DELIVERY_DECISIONS_2026-10-18.md §4`
# （同 `docs/DELIVERY_GATE.md §D1`）。
from aplus.webapp import MIN_INDEPENDENT_WINDOWS  # noqa: E402

GENERATOR = "scripts/measure_goal_progress.py"
SCHEMA = "aplus.goal_progress/1"

#: 8 条目标的**唯一出处**（逐条归档表）。
GOAL_REGISTRY = "docs/DELIVERY_DECISIONS_2026-10-18.md §1"

#: 目标 7 声明的唯一代码位置（用户原话）。
DECLARED_REPO_ROOT = "/Users/airboom/opencode/aplus"

#: 目标 8 的授权出处（用户原话「全部你来决定，不再确认任何东西」）。
GOAL8_AUTHORITY_AT = ("docs/DELIVERY_DECISIONS_2026-10-18.md §1"
                      "（8 条目标表的第 8 行 · 归档 = 授权）")

# ── 状态词表 ────────────────────────────────────────────────────────
#: 四态。**「不可判定」不许被压成「未达」**（第 89 层 + 判据不许改）。
STATUS_ACHIEVED = "ACHIEVED"
STATUS_NOT_ACHIEVED = "NOT_ACHIEVED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_UNDECIDABLE = "UNDECIDABLE"

STATUS_LABELS = {
    STATUS_ACHIEVED: "达成",
    STATUS_NOT_ACHIEVED: "未达",
    STATUS_IN_PROGRESS: "进行中",
    STATUS_UNDECIDABLE: "不可判定（测不出）",
}

#: 允许出现在状态里的四态全集（守卫按这张表查，不许出现第五个）。
ALL_STATUSES = tuple(STATUS_LABELS)

#: 提交归类统计的口径 = `scripts/daily_review.py::collect_commits`（只读标题首词）。
COMMIT_CLASSES = ("交付", "手段", "收尾", "未归类")

#: **产物根** —— 所有读数都从这里取。
#:
#: ⚠️ 默认 = 仓根；测试可以把它指向临时目录
#: ⇒ 守卫就能在**不碰生产路径**的前提下造「缺档」场景
#: （第 132 层：测试不许把自己的输出写进生产产物路径）。
ARTIFACT_ROOT = ROOT


class ArtifactMissing(Exception):
    """产物**读不出来**（不存在 / 坏 JSON / 字段缺失）。

    🚨 与「产物读出来、值是 null」**不是同一件事**：
    前者是「没有这份证据」，后者是「有证据，证据说这个数不存在」。
    ⇒ 本文件的每个读数都带 `status`（`ok` / `missing`）与 `reason`。
    """


# ── 读产物（**只读不重算**） ─────────────────────────────────────────

def read_json(rel: str):
    """读**整份**产物。读不出来 ⇒ `ArtifactMissing`（不回落）。"""
    p = ARTIFACT_ROOT / rel
    if not p.exists():
        raise ArtifactMissing(f"产物不存在：{rel}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactMissing(f"产物读不出来：{rel}（{type(exc).__name__}: {exc}）")


def dig(doc, path):
    """按 `a.b[0].c` 取值。取不到 ⇒ `ArtifactMissing`（不回落）。"""
    node = doc
    for seg in path:
        try:
            if isinstance(node, list):
                node = node[int(seg)]
            else:
                node = node[seg]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ArtifactMissing(
                f"字段缺失：{'.'.join(path)}（{type(exc).__name__}）") from exc
    return node


def safe(rel: str, path=None):
    """取一个字段；取不到 ⇒ `None`（调用点必须自己说「缺」，不许当 0）。"""
    try:
        doc = read_json(rel)
    except ArtifactMissing:
        return None
    if path is None:
        return doc
    try:
        return dig(doc, list(path))
    except ArtifactMissing:
        return None


def format_value(value, unit: str, null_display: str = "缺失") -> str:
    """**唯一**的显示格式化入口。

    ⚠️ 显示串**不许**手打：前端只渲染 `display`，一个数都不算
    （与 `/api/d2-gap` 同一纪律）。
    ⚠️ `None` 的显示**不是 `0`**（第 89 层）。
    """
    if value is None:
        return null_display
    if unit == "fraction":
        return f"{float(value) * 100:.4f}%"
    if unit == "pct":
        return f"{float(value):.4f}%"
    if unit == "pp":
        return f"{float(value):.4f} pp"
    if unit == "multiple":
        return f"{float(value):.4f}×"
    if unit == "count":
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, int):
            return str(value)
        f = float(value)
        return str(int(f)) if f.is_integer() else str(value)
    if unit == "bool":
        return "是" if value else "否"
    if unit == "lines":
        if isinstance(value, (list, tuple)):
            return "\n".join(str(v) for v in value)
        return str(value)
    if unit == "json":
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if unit == "list":
        if isinstance(value, (list, tuple)):
            return "、".join(str(v) for v in value) if value else "无"
        return str(value)
    return str(value)


def cited(rel: str, path, *, key: str, label: str, unit: str = "text",
          caliber: str | None = None, note: str | None = None,
          null_display: str = "缺失") -> dict:
    """读**一个**产物字段 ⇒ 一条带出处的读数。

    * 读得到 ⇒ `status: "ok"`，`value` 是产物字段的**原值**。
    * 读不到 ⇒ `status: "missing"`，`value: null`，`reason` 说清缺哪一档。
      ⇒ **绝不回落成 0**（0 会被读成「缺口 0，已达标」）。
    """
    segs = list(path)
    src = f"{rel} :: {'.'.join(segs)}"
    try:
        value = dig(read_json(rel), segs)
    except ArtifactMissing as exc:
        return {
            "key": key, "label": label, "value": None, "display": "缺失",
            "unit": unit, "at": src, "kind": "artifact", "status": "missing",
            "reason": str(exc), "caliber": caliber, "note": note,
        }
    return {
        "key": key, "label": label, "value": value,
        "display": format_value(value, unit, null_display), "unit": unit,
        "at": src, "kind": "artifact", "status": "ok",
        "caliber": caliber, "note": note,
    }


def derived(key: str, label: str, value, *, sources, derivation: str,
            unit: str = "count", caliber: str | None = None,
            note: str | None = None, verify: dict | None = None) -> dict:
    """一个**本脚本算出来的**数（不是产物自报的）。

    🚨 与 `cited()` **不许混列** —— 两者读起来同样确定，
    而一个是「上游搬过来的」、一个是「我算的」（第 109 层）。
    ⇒ `sources` 说数据来自哪儿，`derivation` 说怎么算的，
    `verify`（可选）说**怎么独立复核**（守卫按它重算一遍）。
    """
    return {
        "key": key, "label": label, "value": value,
        "display": format_value(value, unit), "unit": unit, "at": None,
        "kind": "derived", "status": "ok", "sources": list(sources),
        "derivation": derivation, "verify": verify,
        "caliber": caliber, "note": note,
    }


def criterion(key: str, label: str, value, *, at: str, unit: str = "count",
              note: str | None = None) -> dict:
    """一条**判据**（门槛）—— **不是测量值**。

    🚨 判据与测量必须分开标（第 7 层）：把 `W ≥ 10` 与 `W = 3` 混在一起，
    读者会以为 3 是某次测出来的门槛。
    """
    return {
        "key": key, "label": label, "value": value,
        "display": format_value(value, unit), "unit": unit, "at": at,
        "kind": "criterion", "status": "ok", "note": note,
    }


def manual(key: str, label: str, judgement: str, *, criteria: list) -> dict:
    """「**由人判断**」的一格 —— 没有自动度量就**明说没有**。

    🚨 第 7 层：「不知道」不许塌成 0、不许假装有。
    ⇒ `value` 恒为 `null`，`display` 恒为「由人判断」。
    """
    return {
        "key": key, "label": label, "value": None, "display": "由人判断",
        "unit": "text", "at": None, "kind": "manual", "status": "manual",
        "judgement": judgement, "criteria": list(criteria),
    }


# ── 目标 1 · D2（5 万本金年化 20%） ─────────────────────────────────

#: D2 判定的**分支名** —— 屏上「哪一条判据落的」与状态一起给出。
#:
#: 🚨 为什么要有它：状态「不可判定」有**两条不同的路径**
#:    （符号不可定 / c < T ≤ U）⇒ 只钉状态的话，
#:    「把符号那一支的判据写坏」这条变异会**存活**（它落进另一条同样诚实的分支）。
#: ⇒ 分支名把「走了哪一支」变成**可断言的标签**。
D2_BRANCH_UNREADABLE = "UNREADABLE"
D2_BRANCH_SIGN_NOT_DETERMINABLE = "SIGN_NOT_DETERMINABLE"
D2_BRANCH_C_AT_OR_ABOVE_TARGET = "C_AT_OR_ABOVE_TARGET"
D2_BRANCH_U_BELOW_TARGET = "U_BELOW_TARGET"
D2_BRANCH_C_BELOW_TARGET_WITHIN_U = "C_BELOW_TARGET_WITHIN_U"

D2_BRANCHES = (
    D2_BRANCH_UNREADABLE,
    D2_BRANCH_SIGN_NOT_DETERMINABLE,
    D2_BRANCH_C_AT_OR_ABOVE_TARGET,
    D2_BRANCH_U_BELOW_TARGET,
    D2_BRANCH_C_BELOW_TARGET_WITHIN_U,
)


def _judge_d2(cagr, mde, upper, target, sign_ok, cases) -> tuple[str, str, str]:
    """D2 状态 + 理由 + **分支名** —— **机械执行产物自带的判据**。

    🚨 这里**不做任何四舍五入、不做任何外推**：
    判据的四个输入全部直读 `deployed_d2_verdict.json::verdict`。
    """
    if cagr is None or mde is None or target is None:
        return STATUS_UNDECIDABLE, (
            "🚨 判决档读不出来（`deployed_d2_verdict.json::verdict` 的 "
            "cagr / mde_annual / target 缺一）⇒ **不给方向**，"
            "也不拿别的口径顶替。"), D2_BRANCH_UNREADABLE
    if sign_ok is False:
        return STATUS_UNDECIDABLE, (
            "🚨 **不可判定**：|c| < M ⇒ **连符号都定不了** ⇒ "
            "既不许说「未达」，也不许说「接近」，更不许说「已达成」。"
            + (f" 产物逐字：{cases}" if isinstance(cases, (list, tuple)) else "")
        ), D2_BRANCH_SIGN_NOT_DETERMINABLE
    if float(cagr) >= float(target):
        return (STATUS_ACHIEVED,
                "点估计已达目标（符号可定且 c ≥ T）。",
                D2_BRANCH_C_AT_OR_ABOVE_TARGET)
    if upper is not None and float(upper) < float(target):
        return (STATUS_NOT_ACHIEVED,
                "🚨 **未达**：符号可定，且 95% 单侧上界 U 低于目标 T ⇒ "
                "目标落在上界之外。",
                D2_BRANCH_U_BELOW_TARGET)
    return (STATUS_UNDECIDABLE,
            "🚨 **不可判定（测不出）**：c < T ≤ U ⇒ 既不许说「达不到」，也不许说「接近」。"
            + (f" 产物逐字：{cases}" if isinstance(cases, (list, tuple)) else ""),
            D2_BRANCH_C_BELOW_TARGET_WITHIN_U)


def build_goal_1() -> dict:
    rel_v = "data/reports/deployed_d2_verdict.json"
    rel_l = "data/reports/d2_signal_requirement.json"

    deployed_metrics = [
        cited(rel_v, ["verdict", "cagr"], key="account_cagr_annual",
              label="账户年化 c", unit="fraction",
              caliber="实盘档 · LIVE_DEFAULT|ARM_A|K63|E100（生产配置那一格）"),
        cited(rel_v, ["verdict", "gap_to_target_pp"], key="gap_to_target_pp",
              label="点估计距 20%", unit="pp", caliber="实盘档"),
        cited(rel_v, ["verdict", "mde_annual"], key="mde_annual",
              label="年化 MDE", unit="fraction", caliber="实盘档"),
        cited(rel_v, ["verdict", "measured_sign_determinable"],
              key="measured_sign_determinable", label="符号可定", unit="bool",
              caliber="实盘档",
              note="🚨 `false` ⇒ **连符号都定不了** ⇒ 只支持形状陈述"),
        cited(rel_v, ["verdict", "upper_one_sided_95"], key="upper_one_sided_95",
              label="95% 单侧上界 U", unit="fraction", caliber="实盘档"),
        cited(rel_v, ["verdict", "target"], key="target", label="目标 T",
              unit="fraction", caliber="实盘档"),
    ]
    ideal_metrics = [
        cited(rel_l, ["headline", "required_gross_at_50k_deliverable"],
              key="required_gross_at_50k_deliverable", label="所需毛超额（5 万交付档）",
              unit="pct", caliber="理想档 · 满仓 100% 部署假设下的公式上界"),
        cited(rel_l, ["headline", "account_annual_ceiling_pct",
                      "at_deliverable_width_and_50k"],
              key="account_annual_at_50k_pct", label="账户年化天花板（榜宽 10 · 5 万）",
              unit="pct", caliber="理想档"),
        cited(rel_l, ["headline", "account_annual_ceiling_pct",
                      "gap_to_target_at_deliverable_width_and_50k_pp"],
              key="gap_to_target_at_deliverable_width_and_50k_pp",
              label="天花板距 20%", unit="pp", caliber="理想档"),
        cited(rel_l, ["headline", "account_annual_ceiling_pct",
                      "that_width_is_undeliverable_at_50k"],
              key="that_width_is_undeliverable_at_50k", label="哪一档才可交付",
              unit="text", caliber="理想档"),
    ]

    # ── 状态：**复用产物自己的判据**，不自造 ──────────────────────
    # 产物 `verdict.cases` 逐字写着两条分支：
    #   「`|c| < M` ⇒ 连符号都定不了」
    #   「`c < T ≤ U` ⇒ 测不出：既不许说『达不到』，也不许说『接近』」
    # ⇒ 判据**一个字不改**，只把它机械执行。
    cagr = safe(rel_v, ["verdict", "cagr"])
    mde = safe(rel_v, ["verdict", "mde_annual"])
    upper = safe(rel_v, ["verdict", "upper_one_sided_95"])
    target = safe(rel_v, ["verdict", "target"])
    sign_ok = safe(rel_v, ["verdict", "measured_sign_determinable"])
    cases = safe(rel_v, ["verdict", "cases"])

    status, reason, branch = _judge_d2(cagr, mde, upper, target, sign_ok, cases)

    multiples = {
        "role": "🚨 **这不是第三个口径** —— 它是「**倍数**」那一族。"
                "⇒ 屏上那个「还差几倍」**必须**取 `same_caliber` 那一列"
                "（第 7 层：两个各自都对的数放一起比）。",
        "columns": [
            {
                "key": "same_caliber",
                "name": "同口径 · K=63 · 生产臂 LIVE_DEFAULT · 配置层 ARM_A · 榜宽 10 · 5 万 · 满仓 · 等权市场腿",
                "is_the_gap": True,
                "metric": cited(rel_l, ["headline",
                                        "multiple_needed_at_50k_deliverable_by_caliber",
                                        "same_caliber", "multiple_needed"],
                                key="multiple_needed_same_caliber",
                                label="还差几倍（同口径）", unit="multiple",
                                caliber="同口径 · 分子 = K=63 的要求、分母 = K=63 生产臂的实测毛超额"),
                "note": "✅ **同口径那一列才是「还差几倍」**",
            },
            {
                "key": "cross_caliber",
                "name": "跨口径 · K=21 研究臂",
                "is_the_gap": False,
                "metric": cited(rel_l, ["headline",
                                        "multiple_needed_at_50k_deliverable_by_caliber",
                                        "cross_caliber", "multiple_needed"],
                                key="multiple_needed_cross_caliber",
                                label="还差几倍（跨口径 · 不是缺口）", unit="multiple",
                                caliber="跨口径 · 分子 = K=63 的要求、分母 = K=21 研究臂的实测"),
                "note": "❌ **不是缺口** —— 台账显式判它 `is_the_gap = false`"
                        "（K 与信号两处口径都不同）⇒ **不许**显示成缺口",
            },
        ],
    }

    return {
        "id": 1,
        "title": "5 万本金投 A 股，目标年化 20%",
        "goal_text": "理解我的意图，我有 5 万块钱投资 A 股实现目标年化收益 20%",
        "registry_class": "D2",
        "registry_at": GOAL_REGISTRY,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "verdict_branch": branch,
        "reason": reason,
        "criterion": (
            "产物自带的四条穷举（`measure_basket_nav_01.py::judge`）："
            "`|c| < M` ⇒ 连符号都定不了；`c < T ≤ U` ⇒ 测不出；"
            "`c ≥ T` ⇒ 达成；`U < T` ⇒ 未达。"
            "🚨 **判据一个字不改**，本脚本只把它机械执行。"),
        "metrics": deployed_metrics,
        "calibers": [
            {
                "key": "deployed_live_k63",
                "name": "实盘档（生产配置 · 满仓 10 只 · K=63 · 整手 · 逐笔成本 · 闲置现金 0 计息）",
                "role": "🚨 **屏上给用户的那一档** —— 平台真正会执行的那个账户的模拟净值",
                "source": f"{rel_v} :: verdict",
                "metrics": deployed_metrics,
                "honest_boundary": (
                    "🚨 `measured_sign_determinable = false` ⇒ **连符号都定不了** ⇒ "
                    "**既不许说「达不到」，也不许说「接近」**（只支持形状陈述）。"),
            },
            {
                "key": "ideal_ceiling_k63",
                "name": "理想档（满仓 100% 部署假设下的**公式上界**）",
                "role": "⚠️ 可并排给出，但**必须标明它是上界**（不含整手与闲置现金的代价）",
                "source": f"{rel_l} :: headline",
                "metrics": ideal_metrics,
                "honest_boundary": (
                    "🚨 这一族是**公式上界**，而**不是**平台真会执行的账户；"
                    "只给这一档 ⇒ 用户看到的缺口少了整手 + 闲置现金那一截。"),
            },
        ],
        "multiples": multiples,
        "honest_boundary": (
            "🚨 两个口径**不是同一个量**：K 相同，但理想档不含整手与闲置现金。"
            "⇒ **并排、各带标签**，不许合并、不许取极值（第 143 层）。"),
    }


# ── 目标 2 · D1（每天 10 涨 / 10 跌） ───────────────────────────────

def build_goal_2() -> dict:
    pred_dir = ARTIFACT_ROOT / "data" / "reports" / "predictions"
    files = sorted(pred_dir.glob("pred_*.parquet")) if pred_dir.exists() else []
    rel_srp = "data/reports/score_ranking_power.json"

    metrics: list[dict] = []
    up = down = rows = None
    pred_date = None
    if not files:
        metrics.append({
            "key": "prediction_file", "label": "最新名单文件", "value": None,
            "display": "缺失", "unit": "text", "at": "data/reports/predictions/",
            "kind": "artifact", "status": "missing",
            "reason": "目录里没有任何 pred_*.parquet",
        })
    else:
        latest = files[-1]
        pred_date = latest.stem.removeprefix("pred_")
        rel_pred = f"data/reports/predictions/{latest.name}"
        is_today = (pred_date == date.today().isoformat())
        import pandas as pd
        df = pd.read_parquet(latest)
        rows = int(len(df))
        up = int((df["direction"] == "up").sum())
        down = int((df["direction"] == "down").sum())
        metrics.append(derived(
            "prediction_date", "名单日期", pred_date, sources=[rel_pred],
            derivation="最新一份 `pred_*.parquet` 的文件名日期分量",
            unit="text",
            verify={"kind": "pred_filename_date", "file": rel_pred},
            note=(None if is_today else
                  f"⚠️ **不是当天的交付** —— 最新一份是 {pred_date}，"
                  f"今天（{date.today().isoformat()}）没有名单文件"
                  "（不同时刻的观测不是同一个事实）"),
        ))
        metrics.append(derived(
            "up_count", "会涨的只数", up, sources=[rel_pred],
            derivation="按 `direction` 列数 `== 'up'` 的行数",
            verify={"kind": "count_by_column", "file": rel_pred,
                    "column": "direction", "value": "up"}))
        metrics.append(derived(
            "down_count", "会跌的只数", down, sources=[rel_pred],
            derivation="按 `direction` 列数 `== 'down'` 的行数",
            verify={"kind": "count_by_column", "file": rel_pred,
                    "column": "direction", "value": "down"}))
        metrics.append(derived(
            "rows_total", "名单总行数", rows, sources=[rel_pred],
            derivation="parquet 行数",
            verify={"kind": "row_count", "file": rel_pred}))

    metrics += [
        cited(rel_srp, ["verdict"], key="gate_verdict", label="D1 上线闸门判决",
              unit="text", caliber="G4 / S3（与收益高低无关）",
              note="🚨 `POWERLESS` = **测不出**，**不是**「打分没有排序力」"),
        cited(rel_srp, ["publish_allowed"], key="publish_allowed",
              label="是否许发布", unit="bool", caliber="G4 / S3"),
        cited(rel_srp, ["by_score_col", "score", "n_independent_pred_dates"],
              key="n_independent_pred_dates", label="独立出单日", unit="count",
              caliber="判决臂按 `pred_date` 分组（出单日 ≠ 成交日）"),
        cited(rel_srp, ["by_score_col", "score", "independence_caliber",
                        "n_entry_dates"], key="n_independent_windows",
              label="W · 独立成交窗口", unit="count",
              caliber="✅ **验收分母**（按当前信号分桶后）",
              note="🚨 判据出处：`docs/DELIVERY_DECISIONS_2026-10-20.md §14`"
                   "（更正文：验收分母是这一档，**不是**全底稿那一档）"),
        cited("data/reports/stats.json", ["n_independent_windows"],
              key="n_independent_windows_unscoped",
              label="独立成交窗口（全底稿 · 未按当前信号分桶）", unit="count",
              caliber="⚠️ **不是验收分母**（§14 已更正）",
              note="🚨 与上面那一个**不是同一个量** ⇒ 两个数不许混引（第 7 层）"),
    ]

    criteria = [
        criterion("min_independent_windows", "验收门槛 W ≥ 10 独立成交窗口",
                  MIN_INDEPENDENT_WINDOWS,
                  at="docs/DELIVERY_DECISIONS_2026-10-18.md §4（决策四）"
                     " · aplus/webapp.py::MIN_INDEPENDENT_WINDOWS",
                  note="🚨 **W < 10 时不许报命中率、不许给 CI**；"
                       "该门槛**不许换算成日历 ETA**"),
    ]

    w = safe(rel_srp, ["by_score_col", "score", "independence_caliber",
                       "n_entry_dates"])
    gate = safe(rel_srp, ["verdict"])
    pub = safe(rel_srp, ["publish_allowed"])
    shape_ok = (up == 10 and down == 10)

    acceptance = {
        "shape_10_10": bool(shape_ok),
        "shape_note": ("✅ 名单形态是 10 涨 / 10 跌（**逐行数出来的**，不是手打的）"
                       if shape_ok else
                       f"🚨 名单形态**不是** 10/10（实测 up={up} / down={down}）"),
        "gate_verdict": gate,
        "publish_allowed": pub,
        "w": w,
        "w_min": MIN_INDEPENDENT_WINDOWS,
        "w_met": bool(w is not None and int(w) >= MIN_INDEPENDENT_WINDOWS),
        "w_note": (
            f"⚠️ **W = {w} < {MIN_INDEPENDENT_WINDOWS}** ⇒ "
            "按决策四，**命中率族一个都不许报**（不许报命中率、不许给 CI）。"
            "🚨 **不许**把这个门槛换算成日历 ETA（同一处禁令）。"
            if w is not None and int(w) < MIN_INDEPENDENT_WINDOWS else
            "（W 读不出来 ⇒ 明说缺，不回落成 0。）"),
        # 🚨 **显示串在这里生成** ⇒ 前端一个数都不算（与 `/api/d2-gap` 同纪律）。
        "rows": [
            {"label": "名单形态（10 涨 / 10 跌）", "value": bool(shape_ok),
             "display": "达标" if shape_ok else "未达标"},
            {"label": "闸门判决（G4 / S3）", "value": gate,
             "display": format_value(gate, "text")},
            {"label": "是否许发布", "value": pub,
             "display": (("是" if pub else "否") if isinstance(pub, bool)
                         else "缺失")},
            {"label": "W · 独立成交窗口", "value": w,
             "display": format_value(w, "count")},
            {"label": "验收门槛 W ≥ 10 独立成交窗口", "value": MIN_INDEPENDENT_WINDOWS,
             "display": format_value(MIN_INDEPENDENT_WINDOWS, "count")},
        ],
    }

    if not files:
        status = STATUS_UNDECIDABLE
        reason = "🚨 没有任何名单产物 ⇒ D1 无从判起（**不是**「没做到」，是**没有证据**）。"
    elif shape_ok and not acceptance["w_met"]:
        status = STATUS_IN_PROGRESS
        reason = (
            f"🚨 **形态在出，验收未过**：名单形态达标（10 涨 / 10 跌），"
            f"但闸门判决 `{gate}`、`publish_allowed = {pub}`、"
            f"W = {w} < {MIN_INDEPENDENT_WINDOWS} ⇒ "
            "**不许发布**，先攒前瞻期。")
    elif shape_ok and acceptance["w_met"] and pub:
        status = STATUS_ACHIEVED
        reason = "形态达标 + 闸门放行。"
    else:
        status = STATUS_NOT_ACHIEVED
        reason = f"🚨 名单形态未达标（up={up} / down={down}）。"

    return {
        "id": 2,
        "title": "每天推荐下个交易日会涨 10 只、会跌 10 只",
        "goal_text": "每天推荐下个交易日会涨 10 只、会跌 10 只股票，细节你自己完善好",
        "registry_class": "D1",
        "registry_at": GOAL_REGISTRY,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "reason": reason,
        "criterion": (
            "① 形态：名单逐行数出 up=10 / down=10；"
            "② 验收：`score_ranking_power.json::verdict` 放行且 "
            f"W ≥ {MIN_INDEPENDENT_WINDOWS} 独立成交窗口（决策四）。"
            "🚨 两道闸**分别**成立才算达成。"),
        "metrics": metrics,
        "criteria": criteria,
        "acceptance": acceptance,
        "honest_boundary": (
            "🚨 形态达标 ≠ 验收达标 ⇒ 屏上**两个都写**，不许只写前者。"),
    }


# ── 目标 3 · 自进化（回测 / deep-research / 金融数据） ─────────────

def build_goal_3() -> dict:
    rel_fr = "data/reports/forward_registry.json"
    rel_ev = "data/reports/evolution_log.json"
    rel_arm = "data/reports/fundamental_mechanism_armR_fullpool_2026-10-21.json"
    rel_dv = "data/reports/dividend_lowvol_longleg.json"

    metrics: list[dict] = []

    # 前瞻注册表（**注册即冻结**：候选数与登记日）
    fr = safe(rel_fr)
    if isinstance(fr, list):
        metrics.append(derived(
            "forward_batches", "前瞻注册批次数", len(fr), sources=[rel_fr],
            derivation="`forward_registry.json` 的批次条数",
            verify={"kind": "list_len", "file": rel_fr}))
        metrics.append(derived(
            "forward_candidates", "前瞻注册候选总数",
            sum(len(b.get("candidates") or []) for b in fr),
            sources=[rel_fr],
            derivation="各批次 `candidates` 长度之和",
            verify={"kind": "sum_candidates", "file": rel_fr}))
        metrics.append(derived(
            "forward_registered_on", "各批登记日",
            [b.get("registered_on") for b in fr],
            sources=[rel_fr], derivation="各批次 `registered_on` 字段",
            unit="lines", verify={"kind": "registered_on", "file": rel_fr}))
    else:
        metrics.append({
            "key": "forward_batches", "label": "前瞻注册批次数", "value": None,
            "display": "缺失", "unit": "count", "at": rel_fr, "kind": "artifact",
            "status": "missing", "reason": f"读不出来：{rel_fr}",
        })

    # 进化日志
    ev = safe(rel_ev)
    if isinstance(ev, list):
        metrics.append(derived(
            "evolution_entries", "进化日志条目数", len(ev), sources=[rel_ev],
            derivation="`evolution_log.json` 的条目数",
            verify={"kind": "list_len", "file": rel_ev}))
    else:
        metrics.append({
            "key": "evolution_entries", "label": "进化日志条目数", "value": None,
            "display": "缺失", "unit": "count", "at": rel_ev, "kind": "artifact",
            "status": "missing", "reason": f"读不出来：{rel_ev}",
        })

    # 红利 PIT 分片覆盖件
    # ⚠️ glob 会把 **sidecar**（`…shard1of5.json.provenance.json`）也吃进来
    # ⇒ 必须按**严格文件名**再筛一道（`provenance.report_jsons` 也是这么分的）。
    shard_rels = sorted(
        f"data/reports/{p.name}" for p in (ARTIFACT_ROOT / "data" / "reports").glob(
            "dividend_pit_coverage_shard*of*.json")
        if re.fullmatch(r"dividend_pit_coverage_shard\d+of\d+\.json", p.name))
    shard_rows, ok_sum, tot_sum = [], 0, 0
    for rel in shard_rels:
        cov = safe(rel, ["coverage"])
        if not isinstance(cov, dict):
            continue
        ok, tot = cov.get("cells_ok"), cov.get("cells_total")
        if isinstance(ok, int):
            ok_sum += ok
        if isinstance(tot, int):
            tot_sum += tot
        shard_rows.append({
            "shard": rel.rsplit("/", 1)[-1],
            "at": f"{rel} :: coverage",
            "cells_ok": ok,
            "cells_total": tot,
            "cell_coverage_pct": cov.get("cell_coverage_pct"),
        })
    if shard_rows:
        metrics.append(derived(
            "dividend_pit_shards", "红利 PIT 分片数", len(shard_rows),
            sources=shard_rels,
            derivation="`dividend_pit_coverage_shard*of*.json` 的份数",
            verify={"kind": "glob_len",
                    "pattern": "dividend_pit_coverage_shard*of*.json"}))
        metrics.append(derived(
            "dividend_pit_cells", "红利 PIT 单元格覆盖（ok / 总）",
            f"{ok_sum} / {tot_sum}", sources=shard_rels,
            derivation="各分片 `coverage.cells_ok` 之和 / `coverage.cells_total` 之和",
            unit="text",
            verify={"kind": "shard_cells_sum"}))
        metrics.append(derived(
            "dividend_pit_cell_coverage_pct", "红利 PIT 单元格覆盖率",
            (ok_sum / tot_sum * 100.0) if tot_sum else None,
            sources=shard_rels,
            derivation="Σcells_ok ÷ Σcells_total（**比率**，不是百分数字段）",
            unit="pct",
            verify={"kind": "shard_cells_ratio"}))
    else:
        metrics.append({
            "key": "dividend_pit_shards", "label": "红利 PIT 分片数", "value": None,
            "display": "缺失", "unit": "count",
            "at": "data/reports/dividend_pit_coverage_shard*of*.json",
            "kind": "artifact", "status": "missing",
            "reason": "没有任何红利 PIT 分片覆盖件",
        })

    # 基本面族判决（臂 R）
    metrics += [
        cited(rel_arm, ["verdict", "calibers", "primary", "cells_pass_family_a"],
              key="fundamental_cells_passed", label="基本面族过线格数",
              unit="count", caliber="族 A（臂 R · 全池）",
              note="🚨 0 = 一格都没过线"),
        cited(rel_arm, ["verdict", "calibers", "primary", "state_counts"],
              key="fundamental_state_counts", label="基本面族各状态计数",
              unit="json", caliber="族 A（臂 R · 全池）",
              note="🚨 `FUNDAMENTAL_POWERLESS` = **测不到**，不是「没有效应」"),
        cited(rel_arm, ["n_A", "primary", "n_A"], key="fundamental_n_a",
              label="族 A 的候选数 n_A", unit="count", caliber="族 A（臂 R · 全池）"),
        cited(rel_dv, ["suite_verdict", "label"], key="dividend_lowvol_verdict",
              label="红利/低波整套判决", unit="text",
              caliber="红利 + 低波族", null_display="未出判决",
              note="🚨 `null` = **未出判决**，不是「判决为否」"),
        cited(rel_dv, ["suite_verdict", "invalidated_by"],
              key="dividend_lowvol_invalidated_by", label="整套判决被谁作废",
              unit="list", caliber="红利 + 低波族", null_display="无"),
    ]

    return {
        "id": 3,
        "title": "用未来+历史数据回测、deep-research 调研、金融数据迭代自进化",
        "goal_text": "利用未来和历史数据回测、deep-research 调研、金融数据完成迭代自进化，"
                     "不达目的誓不罢休",
        "registry_class": "手段",
        "registry_at": GOAL_REGISTRY,
        "status": STATUS_IN_PROGRESS,
        "status_label": STATUS_LABELS[STATUS_IN_PROGRESS],
        "reason": (
            "🚨 **自进化在跑**：前瞻注册表已冻结若干批候选、进化日志在长、"
            "红利 PIT 在分片采集、基本面族已出判决。"
            "⇒ 状态是「**进行中**」，**不是**「达成」。"
            "⚠️ 截至今日**没有一族过线**：基本面族各格全是「测不出」，"
            "红利/低波那一族**未出判决** ⇒ 只支持形状陈述，不支持水平陈述。"),
        "criterion": (
            "🚨 归档表把这一条归为**手段**，**不产生分数** ⇒ "
            "本页**不给**它算「达成百分比」，只报**可核对的进度读数**。"),
        "metrics": metrics,
        "honest_boundary": (
            "🚨 「跑了多少」**不等于**「有效果」：注册表条数、分片覆盖率、"
            "日志条目数都只量**过程**；**效果**看各族的判决字段。"),
    }


# ── 目标 4 / 5 / 6 · 手段类 ─────────────────────────────────────────
# 🚨 这三条**没有**可自动化的效果度量 ⇒ 只报**提交归类统计**（唯一的可自动化切片），
#    其余明写「**由人判断**」并给判据（第 7 层：「不知道」不许塌成 0）。

#: 三条手段目标各自**不可自动化**的部分与判据。
_MANUAL_BLOCKS = {
    4: (
        "充分利用技能、测试驱动开发、完善日志系统",
        "判据（由人判断）：① 新功能是否先有**会失败的**测试再写实现；"
        "② 改动是否让**日志更好排查**（能定位到失败那一步）；"
        "③ 是否用了已安装的技能（而不是手搓一遍）。"
        "🚨 这三条**没有自动度量**：仓里没有任何产物记录「本轮是否先写了测试」。",
    ),
    5: (
        "不要迷失目标，长时间没进展要及时复盘",
        "判据（由人判断）：① 连续无进展时是否**主动复盘**并留下记录；"
        "② 复盘是否回答了「今天用户能打开什么」「离 D2 的缺口变了没有」。"
        "🚨 「是否主动」**没有自动度量**：能自动查到的只有「复盘机制在不在跑」。",
    ),
    6: (
        "简洁高效正确可读且方便维护的代码，定期梳理清晰项目架构",
        "判据（由人判断）：① 新代码是否与既有风格一致、可读；"
        "② 架构是否仍然说得清（模块职责不重叠、口径不散落）；"
        "③ 定期梳理是否真的做了（而不是写在待办里）。"
        "🚨 「可读」「清晰」**没有自动度量**：仓里没有任何产物给代码质量打分。",
    ),
}

_GOAL_TEXTS = {
    4: "充分利用我给你安装的技能，测试驱动开发，完善日志系统好排查问题",
    5: "迭代过程中不要迷失目标，长时间没有进展要及时学会复盘，聚焦目标",
    6: "编写简洁高效正确可读且方便维护的代码，定期梳理清晰项目架构",
}


def build_goal_4_5_6(gid: int) -> dict:
    rel_dr = "data/reports/daily_review.json"
    title, manual_text = _MANUAL_BLOCKS[gid]

    metrics = []
    for cls in COMMIT_CLASSES:
        metrics.append(cited(rel_dr, ["commits", "counts", cls],
                              key=f"commits_{cls}", label=f"提交归类 · {cls}",
                              unit="count",
                              caliber="`scripts/daily_review.py::collect_commits` 的口径"
                                      "（只读提交标题首词）"))
    metrics.append(cited(rel_dr, ["commits", "total"], key="commits_total",
                          label="当天提交总数", unit="count",
                          caliber="collect_commits 口径"))
    metrics.append(cited(rel_dr, ["commits", "drift_rate"], key="drift_rate",
                          label="跑偏率（收尾档 / 当天总提交）", unit="fraction",
                          caliber="DELIVERY_GATE §6 的字面公式",
                          note="🚨 这是**防跑偏**的读数，**不是**成就"))

    review_date = safe(rel_dr, ["review_date"])
    extra = []
    if gid == 5:
        extra = [
            cited(rel_dr, ["review_date"], key="review_date",
                  label="最近一次复盘的日期", unit="text",
                  caliber="每晚 cron 的产物"),
            cited(rel_dr, ["vs_previous", "has_previous"],
                  key="review_has_previous", label="是否已有上一份可比的复盘",
                  unit="bool", caliber="每晚 cron 的产物",
                  note="🚨 「复盘机制在跑」**不等于**「复盘做得好」"),
        ]

    return {
        "id": gid,
        "title": title,
        "goal_text": _GOAL_TEXTS[gid],
        "registry_class": "手段",
        "registry_at": GOAL_REGISTRY,
        "status": STATUS_IN_PROGRESS,
        "status_label": STATUS_LABELS[STATUS_IN_PROGRESS],
        "reason": (
            "🚨 这一条**分两半**："
            "① **可自动化的部分** = 提交归类统计（交付/手段/收尾/跑偏率）"
            "—— 它量的是**注意力去了哪儿**，**不是**目标达没达成；"
            "② **不可自动化的部分** = 明写「**由人判断**」，并给判据。"
            "⇒ 状态给「**进行中**」：**没有一个自动读数能判它达成**。"),
        "criterion": (
            "🚨 归档表把这一条归为**手段**，**不产生分数** ⇒ "
            "本页**不给**它算「达成百分比」。"),
        "metrics": metrics + extra,
        "automatable": {
            "what": "提交归类统计（交付 / 手段 / 收尾 / 未归类 + 跑偏率）",
            "why": "它是仓里**唯一**能自动读出来的「注意力去了哪儿」的量；"
                   "口径 = `scripts/daily_review.py::collect_commits`（只读标题首词）。",
            "caveat": (
                "🚨 **跑偏率是防跑偏的读数，不是成就**；"
                "「收尾档占比低」**不**等于「目标更近了」。"
                + (f" 最近一次复盘日期：{review_date}。" if review_date else "")),
        },
        "manual": [manual(
            "manual_judgement", f"{title} · 效果", "由人判断",
            criteria=[manual_text]),
        ],
        "honest_boundary": (
            "🚨 本条**没有**效果度量 ⇒ 页面上「由人判断」**不许**被渲染成一个分数、"
            "一个百分比或一个勾（第 7 层：「不知道」不许塌成 0）。"),
    }


# ── 目标 7 · 唯一代码位置 ───────────────────────────────────────────

def build_goal_7() -> dict:
    try:
        top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
        git_top = top.stdout.strip() if top.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        git_top = None

    same = (git_top is not None
            and str(Path(git_top).resolve()) == str(Path(DECLARED_REPO_ROOT).resolve()))
    status = STATUS_ACHIEVED if same else STATUS_NOT_ACHIEVED

    return {
        "id": 7,
        "title": "项目唯一代码位置 /Users/airboom/opencode/aplus",
        "goal_text": "项目唯一代码位置 /Users/airboom/opencode/aplus",
        "registry_class": "铁律",
        "registry_at": GOAL_REGISTRY,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "reason": (
            "✅ **本次运行的仓库顶层 == 声明位置** ⇒ 唯一真相源成立。"
            if same else
            f"🚨 声明位置 `{DECLARED_REPO_ROOT}` 与本次运行的仓库顶层 "
            f"`{git_top}` **不是同一个** ⇒ 唯一代码位置**不成立**。"),
        "criterion": (
            "判据（可自动问）：`git rev-parse --show-toplevel` == "
            f"`{DECLARED_REPO_ROOT}`。"
            "🚨 这条量的是「**本仓自己跑在哪**」，不是「别处没有副本」——"
            "后者没有可自动化读数。"),
        "metrics": [
            derived("repo_root", "本次运行的仓库顶层", git_top,
                    sources=["git rev-parse --show-toplevel"],
                    derivation="在仓内执行 `git rev-parse --show-toplevel`",
                    unit="text",
                    verify={"kind": "git_toplevel"}),
        ],
        # ⚠️ **声明**不是**测量** ⇒ 不许混进 `metrics`（那一族全是「可复核的读数」）。
        "declaration": {
            "key": "declared_repo_root",
            "label": "声明的唯一代码位置",
            "value": DECLARED_REPO_ROOT,
            "display": DECLARED_REPO_ROOT,
            "kind": "declaration",
            "at": GOAL_REGISTRY,
            "note": "这是**声明**（用户目标第 7 条），不是测量值",
        },
        "honest_boundary": (
            "🚨 本条量的是「**本仓自己跑在哪**」；"
            "「别处没有第二份副本」**没有可自动化读数** ⇒ 由人判断。"),
    }


# ── 目标 8 · 常设决策（不再向用户确认） ─────────────────────────────

def build_goal_8() -> dict:
    rel = "docs/DELIVERY_DECISIONS_2026-10-18.md"
    exists = (ROOT / rel).is_file()
    status = STATUS_ACHIEVED if exists else STATUS_NOT_ACHIEVED
    return {
        "id": 8,
        "title": "全部细节由我决定，不再向用户确认",
        "goal_text": "高效准确尽快产出，全部你来决定，我给你目标了你自己决定所有大小细节，"
                     "不要再我确认任何东西",
        "registry_class": "授权",
        "registry_at": GOAL8_AUTHORITY_AT,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "reason": (
            "✅ 授权**已登记**在决策登记里 ⇒ 「不再向用户确认」是一条**常设决策**，"
            "不是每轮重新问一遍。"
            if exists else
            "🚨 决策登记文件不在 ⇒ 授权**没有落纸**。"),
        "criterion": (
            "判据（可自动问）：决策登记文件在不在 + 授权条目在不在其中。"
            "🚨 「实际执行时确实没有再回头问」**没有可自动化读数** ⇒ 由人判断。"),
        "metrics": [
            derived("decision_registry", "决策登记文件", rel,
                    sources=[rel], derivation="用户目标第 8 条的登记出处",
                    unit="text",
                    verify={"kind": "file_exists", "file": rel}),
        ],
        "manual": [manual(
            "manual_judgement", "是否真的没有再回头向用户确认", "由人判断",
            criteria=[
                "🚨 「实际执行时有没有回头确认」**没有可自动化读数** ⇒ 由人判断。",
                "判据（由人判断）：最近一轮迭代里是否**一次都没**回头问过"
                "「要不要这么做」；大小细节是否都由我直接定下来。",
            ])],
        "honest_boundary": (
            "🚨 本条量的是「**授权已落纸**」；执行层面的遵守**由人判断**。"),
    }


# ── 汇总 ────────────────────────────────────────────────────────────

_HONEST_BOUNDARIES = [
    "🚨 **本页不给「8 条目标的达成百分比」** —— 归档表把分母定死 = D1 + D2 两项，"
    "手段类不产生分数（`docs/DELIVERY_DECISIONS_2026-10-18.md §1`）。",
    "🚨 **每个数都直读产物并带 `产物路径 :: JSON 路径`** —— "
    "页面上没有一个数是手抄的，也没有一个是前端算的。",
    "🚨 **缺档 = 明说缺**，绝不回落成 0 —— 「没有台账」与「缺口是 0」是相反的结论。",
    "🚨 **`|c| < M` ⇒ 连符号都定不了** ⇒ 目标 1 的状态是「**不可判定**」，"
    "既不许说「未达」，也不许说「接近」，更不许说「已达成」。",
    "🚨 **两个 D2 口径不是同一个量** ⇒ 并排、各带标签，不许合并、不许取极值。",
    "🚨 **「还差几倍」只取同口径那一列** —— 跨口径那一列台账自己标 "
    "`is_the_gap = false`，显示成缺口就是错的。",
    "🚨 **D1 的 W < 10 时不许报命中率、不许给 CI**（决策四）；"
    "**也不许**把门槛换算成日历 ETA。",
    "🚨 **屏上不出现「投入率」「闲置比例」** —— 那两个字段名字与内容相反"
    "（`docs/TODO_deployed_idle_cash_caliber_2026-10-21.md`）。",
    "🚨 **目标 4/5/6 的效果没有自动度量** ⇒ 明写「由人判断」，"
    "不许塌成 0、不许假装有。",
]


def render_summary(goals: list) -> str:
    """一句话摘要 —— **由状态码生成**，不许手打（第 73 层：手打的数会静默过期）。

    🚨 这行**不含任何测量值**（只有交付物编号 `D1`/`D2` 与状态词）
    ⇒ 数字漂移不会跟着漂进散文。
    """
    by_id = {g["id"]: g for g in goals}
    return (f"D1 {by_id[2]['status_label']}；D2 {by_id[1]['status_label']}；"
            f"自进化 {by_id[3]['status_label']}；"
            "其余目标按归档表归为手段 / 铁律 / 授权，本页只报可核对的读数与判据。")


def render_change_line(changed: list) -> str:
    """一句话变化 —— **只说哪些读数变了**，不带数值。

    🚨 数值在 `changed[*].before/after` 里（各有出处），这行只做**索引**。
    ⇒ 手打数值进散文这件事在这条线上没有发生。
    """
    if not changed:
        return "与上一份快照相比，可核对的读数没有变化。"
    keys = sorted({c["key"] for c in changed})
    return "与上一份快照相比，发生变化的读数：" + "、".join(keys) + "。"


def previous_snapshot(snapshot_date: str):
    """上一份快照（日期 < 今天）—— 读**落盘的那份**，不重算。"""
    files = sorted((ARTIFACT_ROOT / "data" / "reports").glob("goal_progress_*.json"))
    prior = []
    for p in files:
        m = re.fullmatch(r"goal_progress_(\d{4}-\d{2}-\d{2})\.json", p.name)
        if m and m.group(1) < snapshot_date:
            prior.append((m.group(1), p))
    if not prior:
        return None
    prior.sort()
    date_str, path = prior[-1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {"date": date_str, "path": path, "doc": doc}


def _metric_index(goals: list) -> dict:
    """`目标号:读数键` → (读数, 它在快照 JSON 里的路径)。

    ⚠️ 路径**必须可解析**（守卫要能顺着 `产物 :: JSON 路径` 独立复核）
    ⇒ 这里记的是**快照文件内**的路径（`goals[i].metrics[j].value`），
    而快照里的读数又各自指向产物 ⇒ 链条闭合。
    """
    out = {}
    for gi, g in enumerate(goals):
        for mi, m in enumerate(g.get("metrics") or []):
            if m.get("kind") == "artifact" and m.get("status") == "ok":
                out[f"{g['id']}:{m['key']}"] = (m, f"goals[{gi}].metrics[{mi}].value")
    return out


def build_change(goals: list, snapshot_date: str) -> dict:
    """与上一份快照比：**哪些读数变了**。

    🚨 变化量是**两个快照读数之差**，不是「用公式重算产物」
    ⇒ 每个变化的量都带 `before_at` / `after_at`（指向**快照文件**里的路径）
    ⇒ 而快照里的每个读数又各自指向产物 ⇒ 链条闭合。
    """
    prev = previous_snapshot(snapshot_date)
    if prev is None:
        return {
            "vs_previous_date": None,
            "vs_previous_at": None,
            "line": "这是第一份快照，还没有可比的上一份。",
            "changed": [],
            "unchanged_keys": [],
        }
    cur = _metric_index(goals)
    old = _metric_index(prev["doc"].get("goals") or [])
    changed, unchanged = [], []
    today_rel = f"data/reports/goal_progress_{snapshot_date}.json"
    for key in sorted(cur):
        if key not in old:
            continue
        a, a_path = old[key]
        b, b_path = cur[key]
        if a.get("value") == b.get("value"):
            unchanged.append(key)
            continue
        before, after = a.get("value"), b.get("value")
        delta = None
        if isinstance(before, (int, float)) and isinstance(after, (int, float)) \
                and not isinstance(before, bool) and not isinstance(after, bool):
            delta = after - before
        changed.append({
            "key": key,
            "before": before,
            "after": after,
            "delta": delta,
            "before_display": a.get("display"),
            "after_display": b.get("display"),
            "before_at": f"{prev['path'].relative_to(ARTIFACT_ROOT)} :: {a_path}",
            "after_at": f"{today_rel} :: {b_path}",
        })
    return {
        "vs_previous_date": prev["date"],
        "vs_previous_at": str(prev["path"].relative_to(ARTIFACT_ROOT)),
        "line": render_change_line(changed),
        "changed": changed,
        "unchanged_keys": unchanged,
    }


def build_snapshot(snapshot_date: str) -> dict:
    goals = [
        build_goal_1(),
        build_goal_2(),
        build_goal_3(),
        build_goal_4_5_6(4),
        build_goal_4_5_6(5),
        build_goal_4_5_6(6),
        build_goal_7(),
        build_goal_8(),
    ]
    payload = {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "snapshot_date": snapshot_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "goal_registry": GOAL_REGISTRY,
        "goals": goals,
        "honest_boundaries": list(_HONEST_BOUNDARIES),
    }
    payload["summary_line"] = render_summary(goals)
    payload["change"] = build_change(goals, snapshot_date)
    return payload


# ── CLI ─────────────────────────────────────────────────────────────

def snapshot_path(snapshot_date: str) -> Path:
    return ARTIFACT_ROOT / "data" / "reports" / f"goal_progress_{snapshot_date}.json"


def main(argv=None) -> int:
    # 🚨 生成器字节冻结（第 125 层）：**开跑第一条**就抓一次。
    #    放在盖章前一行就等于「拿开跑的字节盖开跑的章」⇒ 恒真（第 131 层）。
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)

    ap = argparse.ArgumentParser(description="目标达成进度 · 每日快照")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--out", default=None,
                    help="输出路径（默认 data/reports/goal_progress_<日期>.json）")
    args = ap.parse_args(argv)

    payload = build_snapshot(args.date)

    out = Path(args.out) if args.out else snapshot_path(args.date)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    provenance.stamp(out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
                     extra={"snapshot_date": args.date,
                            "n_goals": len(payload["goals"]),
                            "statuses": {str(g["id"]): g["status"]
                                         for g in payload["goals"]}})

    print(f"[goal-progress] {args.date} → {out}")
    for g in payload["goals"]:
        print(f"  目标 {g['id']} · {g['status_label']} · {g['title']}")
    print(f"  {payload['summary_line']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
