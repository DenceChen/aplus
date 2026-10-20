#!/usr/bin/env python
"""**每晚复盘** —— 把 `docs/DELIVERY_GATE.md` §3/§5/§6 变成一个会自己跑的东西。

## 为什么它存在（本仓自己的防跑偏机制曾经是离线的

`docs/DELIVERY_GATE.md` §5 写着复盘节律：

| 频率 | 只回答 |
|---|---|
| 每轮结束 | §3 两问 |
| **每晚定时**（cron `66c6887a`，20:13） | 今天 D1/D2 有无变化；今天有没有产生 §4 类绿灯 |
| 每 7 天 | 重算 D2 缺口；累计跑偏次数 |

🚨 **那个 cron 挂在某个 Claude 会话的调度上** ⇒ 会话结束它就没了
⇒ 实测 2026-10-21：`crontab -l` = 「no crontab for airboom」，
`~/.claude/jobs/` 下也没有 `66c6887a` ⇒ **复盘节律已经离线**
⇒ 仓里最贵的那条防跑偏机制，靠的是一个**不属于它的**进程。

⇒ 修法：**复盘变成仓自有的产物 + 系统 crontab**，
不依赖任何会话生命周期。

## 它只回答两件事（§3 收工闸）

1. **今天用户能拿到什么** —— 文件名 / 数字，不是「几条守卫绿了」
2. **离 D2 的缺口是多少** —— 与上一轮相比变了没有

⚠️ **复盘不许读仓库里多了什么，只许读用户今天能拿到什么。**
⇒ 本脚本**只读产物与 git 提交标题**，**不扫源码、不数守卫、不数测试**。

## 纪律

- 🚨 **只读产物字段，绝不重算**（第 109 层：用公式重算而不是读字段 = 核对产物时的
  头号错误源）⇒ 每个数都带 `产物路径::JSON 路径`
- 🚨 **缺产物 / 陈旧 ⇒ 显式说缺**，不许回落成别的数
- 🚨 **不许把「测不出」写成「没有」**
- ⚠️ **提交归类统计只读提交标题的首词**（§6 的三档标签），不许去猜「这次提交算不算交付」
  —— 猜不出来就归「未归类」，而不是替它归到某一档
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

GENERATOR = "scripts/daily_review.py"

#: §6 的三档提交标签。首词匹配（`交付: …` / `手段(服务于 D1): …` / `收尾: …`）。
#: ⚠️ `手段` 允许带括号补充说明 ⇒ 用前缀匹配，不用等值。
CLASSES = ("交付", "手段", "收尾")

#: §4「绿灯作废清单」的档位 —— 它们**不计入进展**。
#: ⇒ 跑偏率 = 收尾档占比；另单独报「未归类」占比（**归不出档 = 它不属于任何档**）。
VOID_CLASS = "收尾"


def _read_json(rel: str) -> tuple[dict | None, str | None]:
    """读产物。返回 `(数据, 缺失原因)`；**缺就是缺，不回落**。"""
    p = ROOT / rel
    if not p.exists():
        return None, f"产物不存在：{rel}"
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return None, f"产物读不出来：{rel}（{exc!r}）"


def _dig(obj, path: str):
    """按 `a::b::c` 取值。任一段缺失 ⇒ `None`（**不回落、不猜**）。"""
    cur = obj
    for key in path.split("::"):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def _cell(obj, path: str) -> dict:
    """一个「数 + 它的出处」。**出处必须能指到字段**，否则这一格是空的。"""
    val = _dig(obj, path)
    return {"value": val, "at": path}


def _as_rel(p: Path) -> str:
    """把路径变成「相对仓根」或「绝对路径」。

    ⚠️ 归档目录**可能在仓外**（测试用 tmp_path、用户用别处）⇒
    `Path.relative_to` 在那种情况下**抛 ValueError**（本仓踩过：
    路径假设写死在仓内 ⇒ 一个测试就能把它逼出来）。
    ⇒ 这里**不许**假设仓根是祖先目录。
    """
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def collect_d1(today: str) -> dict:
    """D1（每天 10 涨 + 10 跌）的今日交付面。"""
    out: dict = {"delivered": False, "notes": []}

    rs, miss = _read_json("data/reports/run_status.json")
    if rs is None:
        out["notes"].append(miss)
    else:
        out["run_status"] = {
            "date": rs.get("date"),
            "status": rs.get("status"),
            "outcome": rs.get("outcome"),
            "outcome_reason": rs.get("outcome_reason"),
            "at": "data/reports/run_status.json::date/status/outcome/outcome_reason",
        }
        out["delivered"] = rs.get("outcome") == "ok"

    # 名单形态：**优先取复盘日期那一份**（`pred_<review_date>.parquet`）。
    # 🚨 「最新一份」在补跑/跨日时**不是同一份文件** ——
    # 复盘 10-21 却读 `pred_2026-10-20` ⇒ **不同时刻的观测不是同一个事实**。
    # ⇒ 取不到当天的就**明说回落到哪一份**，不许悄悄用旧的。
    pred_dir = ROOT / "data/reports/predictions"
    want = pred_dir / f"pred_{today}.parquet"
    preds = sorted(pred_dir.glob("pred_*.parquet"))
    chosen: Path | None = None
    fallback_used = False
    if want.exists():
        chosen = want
    elif preds:
        chosen = preds[-1]
        fallback_used = True
    if chosen is not None:
        import pandas as pd
        df = pd.read_parquet(chosen)
        up = int((df["direction"] == "up").sum())
        down = int((df["direction"] == "down").sum())
        out["list"] = {
            "file": f"data/reports/predictions/{chosen.name}",
            "is_the_review_date": (not fallback_used),
            "fallback_note": ("⚠️ **当天没有名单文件**，这里用的是最新的一份 ⇒ "
                              "**不是当天的交付**（不同时刻的观测不是同一个事实）")
            if fallback_used else None,
            "rows": int(len(df)),
            "up": up,
            "down": down,
            "shape_ok_10_10": (up == 10 and down == 10),
        }
        if fallback_used:
            out["notes"].append(
                f"当天（{today}）没有 pred_*.parquet ⇒ 回落到 {chosen.name}")
    else:
        out["notes"].append("没有任何 pred_*.parquet")

    st, miss = _read_json("data/reports/stats.json")
    if st is None:
        out["notes"].append(miss)
        # 🚨 **块永远在**，缺失就是 `value: None` + 原因。
        # ⇒ 若产物缺失时**不建这个块**，下游看到的是「键不存在」，
        # 而「键不存在」与「键在但值是 None」在报告里**长得一样**
        # ⇒ 「这个数是缺失还是没算」永远分不清（本仓反复吃亏的形状）。
        out["sample"] = {
            "n_independent_windows": {"value": None, "at": "n_independent_windows"},
            "overall_hit_rate": {"value": None, "at": "overall_hit_rate"},
            "n_predictions": {"value": None, "at": "n_predictions"},
            "missing_reason": miss,
            "note": ("W（独立成交窗口）是 D1 的验收分母；"
                     "W < 10 ⇒ 不许报命中率结论（DELIVERY_DECISIONS §4）"),
        }
    else:
        out["sample"] = {
            "n_independent_windows": _cell(st, "n_independent_windows"),
            "overall_hit_rate": _cell(st, "overall_hit_rate"),
            "n_predictions": _cell(st, "n_predictions"),
            "note": ("W（独立成交窗口）是 D1 的验收分母；"
                     "W < 10 ⇒ 不许报命中率结论（DELIVERY_DECISIONS §4）"),
        }

    sr, miss = _read_json("data/reports/score_ranking_power.json")
    if sr is None:
        out["notes"].append(miss)
        out["gate"] = {
            "verdict": {"value": None, "at": "verdict"},
            "publish_allowed": {"value": None, "at": "publish_allowed"},
            "power_floor_up": {
                "value": None,
                "at": "by_score_col::score::arms::up::_stats::null_abs_rho_q95_power_floor"},
            "missing_reason": miss,
            "note": ("判决臂是 up（GATE_ARM）；down 是并列报告，非闸门。"
                     "POWERLESS = 测不出，**不是**「打分没有排序力」"),
        }
    else:
        out["gate"] = {
            "verdict": _cell(sr, "verdict"),
            "publish_allowed": _cell(sr, "publish_allowed"),
            "power_floor_up": _cell(
                sr, "by_score_col::score::arms::up::_stats::null_abs_rho_q95_power_floor"),
            "note": ("判决臂是 up（GATE_ARM）；down 是并列报告，非闸门。"
                     "POWERLESS = 测不出，**不是**「打分没有排序力」"),
        }
    return out


def collect_d2() -> dict:
    """D2（5 万本金年化 20%）的读数。**每一档都带口径，不许混比。**"""
    out: dict = {"notes": []}

    bn, miss = _read_json("data/reports/basket_nav_01_2026-10-16.json")
    if bn is None:
        out["notes"].append(miss)
        out["verdict_arm"] = {
            "what": "判决档（满仓 / 涨腿 10 只 / 8 因子逐年 IC 定权）",
            "cagr": {"value": None, "at": "tiers::1::cagr"},
            "mde_annual": {"value": None, "at": "verdict_mde_annual"},
            "upper_one_sided_95": {"value": None, "at": "verdict_upper_one_sided_95"},
            "target": {"value": None, "at": "target"},
            "missing_reason": miss,
            "at": "data/reports/basket_nav_01_2026-10-16.json",
        }
        out["verdict_cases_fired"] = None
    else:
        out["verdict_arm"] = {
            "what": "判决档（满仓 / 涨腿 10 只 / 8 因子逐年 IC 定权）",
            "cagr": _cell(bn, "tiers::1::cagr"),
            "mde_annual": _cell(bn, "verdict_mde_annual"),
            "upper_one_sided_95": _cell(bn, "verdict_upper_one_sided_95"),
            "target": _cell(bn, "target"),
            "at": "data/reports/basket_nav_01_2026-10-16.json",
        }
        out["verdict_cases_fired"] = bn.get("verdict_cases_fired")

    sig, miss = _read_json("data/reports/d2_signal_requirement.json")
    if sig is None:
        out["notes"].append(miss)
        out["deliverable_50k"] = {
            "what": "理想档（5 万可交付档：宽度 10、**满仓假设**、真实摩擦）"
                   " —— ⚠️ **公式上界**：假设 **100% 部署**，"
                   "**不含**整手与闲置现金的代价 ⇒ 比实盘档**乐观**",
            "account_annual_at_50k_pct": {
                "value": None,
                "at": "headline::account_annual_ceiling_pct::at_deliverable_width_and_50k"},
            "gap_to_target_pp": {
                "value": None,
                "at": "headline::account_annual_ceiling_pct::gap_to_target_at_deliverable_width_and_50k_pp"},
            "required_gross_at_50k_deliverable": {
                "value": None, "at": "headline::required_gross_at_50k_deliverable"},
            "measured_gross_at_deliverable_width": {
                "value": None, "at": "headline::measured_gross_best::at_deliverable_width"},
            "missing_reason": miss,
            "at": "data/reports/d2_signal_requirement.json::headline",
        }
    else:
        out["deliverable_50k"] = {
            "what": "理想档（5 万可交付档：宽度 10、**满仓假设**、真实摩擦）"
                   " —— ⚠️ **公式上界**：假设 **100% 部署**，"
                   "**不含**整手与闲置现金的代价 ⇒ 比实盘档**乐观**",
            "account_annual_at_50k_pct": _cell(
                sig, "headline::account_annual_ceiling_pct::at_deliverable_width_and_50k"),
            "gap_to_target_pp": _cell(
                sig, "headline::account_annual_ceiling_pct::gap_to_target_at_deliverable_width_and_50k_pp"),
            "required_gross_at_50k_deliverable": _cell(
                sig, "headline::required_gross_at_50k_deliverable"),
            "measured_gross_at_deliverable_width": _cell(
                sig, "headline::measured_gross_best::at_deliverable_width"),
            "at": "data/reports/d2_signal_requirement.json::headline",
        }

    # ── 实盘档（生产**今天真的在跑**的那一档）────────────────────────
    # 🚨 2026-10-21 补：原来这份复盘只报**理想档**（台账的公式上界，
    #   假设 100% 部署 ⇒ **不含**整手与闲置现金的代价）
    #   ⇒ 用户看到的「离 20% 差多少」**偏乐观**。
    # ⇒ 实盘档 = `deployed_d2_verdict.json::verdict`（满仓 10 只 × ¥5,000 +
    #   实盘 4 因子 + 配置层整手过滤 + K=63 + 逐笔成本 + 闲置现金 0 计息）。
    dep, miss2 = _read_json("data/reports/deployed_d2_verdict.json")
    dep_what = ("实盘档（生产配置：满仓 10 只 × ¥5,000 + 实盘 4 因子 "
                "+ 配置层整手过滤 + K=63 + 逐笔成本 + 闲置现金 0 计息）")
    if dep is None:
        out["notes"].append(miss2)
        out["deployed_live"] = {
            "what": dep_what,
            "cagr": {"value": None, "at": "verdict::cagr"},
            "mde_annual": {"value": None, "at": "verdict::mde_annual"},
            "upper_one_sided_95": {"value": None, "at": "verdict::upper_one_sided_95"},
            "gap_to_target_pp": {"value": None, "at": "verdict::gap_to_target_pp"},
            "measured_sign_determinable": {
                "value": None, "at": "verdict::measured_sign_determinable"},
            "missing_reason": miss2,
            "at": "data/reports/deployed_d2_verdict.json",
        }
    else:
        dv = dep.get("verdict") or {}
        out["deployed_live"] = {
            "what": dep_what,
            "cagr": _cell(dv, "cagr"),
            "mde_annual": _cell(dv, "mde_annual"),
            "upper_one_sided_95": _cell(dv, "upper_one_sided_95"),
            "gap_to_target_pp": _cell(dv, "gap_to_target_pp"),
            "measured_sign_determinable": _cell(dv, "measured_sign_determinable"),
            "cases": dv.get("cases"),
            "at": "data/reports/deployed_d2_verdict.json::verdict",
        }
    return out


def collect_commits(since: str | None) -> dict:
    """当天提交按 §6 三档归类。**只读提交标题首词，不猜。**"""
    fmt = "%H%x1f%s%x1f%ad%x1f" + "%s"
    cmd = ["git", "-C", str(ROOT), "log", f"--format={fmt}", "--date=iso"]
    if since:
        cmd.append(f"--since={since}")
    try:
        raw = subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"git log 失败：{exc!r}", "counts": {}, "items": []}

    items: list[dict] = []
    counts: dict[str, int] = {c: 0 for c in CLASSES}
    counts["未归类"] = 0
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 4:
            continue
        sha, subj, _dt, rest = parts[0], parts[1], parts[2], "\x1f".join(parts[3:])
        head = rest.split(":", 1)[0].strip()
        cls = next((c for c in CLASSES if head.startswith(c)), "未归类")
        counts[cls] += 1
        items.append({"sha": sha[:8], "class": cls, "subject": rest.splitlines()[0][:120]})
    total = sum(counts.values())
    void = counts.get(VOID_CLASS, 0)
    classified = total - counts.get("未归类", 0)
    return {
        "counts": counts,
        "total": total,
        "drift_rate": (void / total) if total else None,
        "drift_rate_note": ("跑偏率 = 收尾档 / 当天总提交数（DELIVERY_GATE §6 的字面公式）；"
                            "目标 < 40%"),
        # ⚠️ 另给一个**只算已归类**的版本：§6 的公式分母含「未归类」，
        # 而未归类多半是**归类闸上线之前**的老提交 ⇒ 它们的出现会把跑偏率**稀释**。
        # ⇒ 两个都给，**不许**只报好看的那个。
        "drift_rate_over_classified": (void / classified) if classified else None,
        "drift_rate_over_classified_note": ("跑偏率 = 收尾档 / **已归类**提交数。"
                                            "与上面的字面公式并列 ⇒ 归不出档的老提交"
                                            "不再稀释读数。"),
        "items": items,
    }


def _archive_files(archive_dir: Path, prefix: str) -> list[Path]:
    """归档文件（按日期排序）。

    ⚠️ **周班的归档前缀是 `review_weekly`** ⇒ 必须排除掉，
    否则夜班会拿「上周的周班」当上一份夜班 ⇒ 比出来的 Δ 是**两个不同量的差**。
    """
    return sorted(p for p in archive_dir.glob(f"{prefix}_*.json")
                  if not p.name.startswith(f"{prefix}_weekly_"))


def compare_with_previous(today: dict, archive_dir: Path, prefix: str = "review",
                           today_str: str | None = None) -> dict:
    """与**上一份**复盘比：缺口变了没有？（§3 第二问）"""
    prevs = _archive_files(archive_dir, prefix)
    if today_str:
        # 今天自己那份**不算**「上一份」（否则重跑同一天会拿自己跟自己比）
        prevs = [p for p in prevs
                 if not p.stem.endswith(today_str)]
    if not prevs:
        return {"has_previous": False,
                "note": "没有上一份复盘 ⇒ 本轮是基线，不许说「缺口变了」"}
    prev, miss = _read_json(_as_rel(prevs[-1]))
    if prev is None:
        return {"has_previous": False, "note": miss}
    out = {"has_previous": True, "previous_file": str(prevs[-1].name)}
    for label, path in (("d1_windows", "d1::sample::n_independent_windows::value"),
                        ("d2_gap_pp", "d2::deliverable_50k::gap_to_target_pp::value"),
                        ("d2_account_annual_pct",
                         "d2::deliverable_50k::account_annual_at_50k_pct::value")):
        a = _dig(today, path)
        b = _dig(prev, path)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[label] = {"now": a, "before": b, "delta": a - b,
                          "changed": (a != b)}
        else:
            out[label] = {"now": a, "before": b, "delta": None,
                          "changed": None,
                          "note": "两侧有一个不是数 ⇒ 不可比（不许说变了）"}
    return out


def build(today: str, archive_dir: Path, since: str | None,
          archive_prefix: str = "review") -> dict:
    d1 = collect_d1(today)
    d2 = collect_d2()
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "review_date": today,
        "questions": {
            "q1_what_can_the_user_open_today": (
                f"data/reports/predictions/（今日名单 up {d1.get('list', {}).get('up')}"
                f" / down {d1.get('list', {}).get('down')}）"
                f" + data/reports/deployed_d2_verdict.json::verdict.cagr"
                f" = {d2.get('deployed_live', {}).get('cagr', {}).get('value')}"
                f"（**实盘档**：生产今天真的在跑的那一档）"),
            "q2_how_far_from_20pct": (
                f"{d2.get('deliverable_50k', {}).get('gap_to_target_pp', {}).get('value')} pp"
                f"（**理想档**：公式上界，假设 100% 部署 ⇒ 不含整手与闲置现金的代价）"
                f"｜"
                f"{d2.get('deployed_live', {}).get('gap_to_target_pp', {}).get('value')} pp"
                f"（**实盘档**：含整手与闲置现金 ⇒ 用户真正拿到的那个）"),
        },
        "d1": d1,
        "d2": d2,
        "commits": collect_commits(since),
        "honest_boundaries": [
            "本复盘只回答 §3 的两问；它**不评价**做得对不对。",
            "D2 的数**不是点估计**：|c| < M ⇒ 连符号都定不了 ⇒ "
            "**不许**说「接近」也不许说「达不到」。",
            "D1 的命中率在 W < 10 时**不许报**（W = 独立成交窗口，不是条目数）。",
            "「收尾」档提交不计入进展（§4 绿灯作废清单）⇒ 跑偏率是**防跑偏**的读数，"
            "**不是**成就。",
            "提交归类**只读标题首词**；归不出档就记「未归类」，不替它归档。",
            "「离 20% 差多少」有**两个口径**（理想档 = 公式上界 / 实盘档 = "
            "含整手与闲置现金）⇒ **两个都要报，并标明是哪个**；"
            "**只报理想档 = 偏乐观**（2026-10-21 修）。",
        ],
    }
    payload["vs_previous"] = compare_with_previous(
        payload, archive_dir, prefix=archive_prefix, today_str=today)
    return payload


def main(argv=None) -> int:
    expect_bytes = provenance.code_file_sha256(ROOT, GENERATOR)
    ap = argparse.ArgumentParser(description="每晚复盘（DELIVERY_GATE §3/§5/§6）")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--since", default=None,
                    help="只看这个时间之后的提交（默认：今天 00:00）")
    ap.add_argument("--out", default="data/reports/daily_review.json")
    ap.add_argument("--archive-prefix", default="review",
                    help="归档文件名前缀（落在 data/reports/ 下 ⇒ 平铺，"
                         "因为 .gitignore 的 !data/reports/*.json 只对平铺文件生效）")
    args = ap.parse_args(argv)

    # 🚨 归档目录 = **输出文件所在目录**（不是写死的 data/reports/）
    # ⇒ 测试把 --out 指到 tmp_path 时，归档也跟着进 tmp_path
    # ⇒ **测试不会写进生产目录**（本仓第 132 层）。
    archive = Path(args.out).parent
    archive.mkdir(parents=True, exist_ok=True)
    archive_prefix = args.archive_prefix
    since = args.since or f"{args.date} 00:00:00"

    payload = build(args.date, archive, since, archive_prefix=archive_prefix)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    arch_path = archive / f"{archive_prefix}_{args.date}.json"
    arch_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    provenance.stamp(out, GENERATOR, root=ROOT, expect_bytes=expect_bytes,
                     extra={"review_date": args.date,
                            "d2_gap_pp": _dig(payload, "d2::deliverable_50k::gap_to_target_pp::value"),
                            "d1_windows": _dig(payload, "d1::sample::n_independent_windows::value")})

    # 人能读的一行结论（**只两问**）
    q = payload["questions"]
    print(f"[review] {args.date}")
    print(f"  Q1 用户能打开：{q['q1_what_can_the_user_open_today']}")
    print(f"  Q2 离 20%：{q['q2_how_far_from_20pct']}")
    vp = payload["vs_previous"]
    if vp.get("has_previous"):
        g = vp.get("d2_gap_pp", {})
        print(f"     与上一份比：缺口 {g.get('before')} → {g.get('now')}"
              f"（Δ={g.get('delta')}）")
    else:
        print(f"     与上一份比：{vp.get('note')}")
    c = payload["commits"]
    print(f"  提交归类：{c.get('counts')}；跑偏率={c.get('drift_rate')}")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
