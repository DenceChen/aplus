#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把**目标进度快照**导出成一个**自包含**的静态 HTML 页。

用户 2026-10-21 原话：「静态页面定期推送服务器就行」＋「我不在家也可以看进度」。

## 这个脚本干什么

读 `data/reports/goal_progress_<日期>.json` ⇒ 产出一个**单文件** HTML
（`data/reports/static/index.html`）⇒ 丢到任何静态服务器就能看。

## 🚨 为什么这个页面**不许**有后端

* 服务器只有 **896MB 内存**，aplus 的主库（5460 只 × 8 年）是 **GB 级** ⇒ 传不过去，
  也不该传（用户原话：「只放页面」）。
* 页面上**每一个数**都来自快照产物 ⇒ 页面**一个数都不算**（第 109 层）。
* ⇒ 静态页 = 快照的**搬运**，不是新的测量。

## 🚨 三条「屏上不许」与它们在这份代码里的落点

| 禁令 | 落点 |
|---|---|
| 不许说「已达成 20%」 | 渲染层**不产生**任何判决词；状态标签**逐字**取自快照的 `status_label` |
| 屏上不出现那两个**名字与内容相反**的部署比例字段 | `REDACTIONS` 机械替换 + 页脚披露替换清单 |
| 「还差几倍」**只取同口径那一列** | `multiples.columns` 里 `is_the_gap == true` 的那一列**才**进模型；跨口径那一列**整列不进模型**（连 JSON 岛里都没有） |

## 🚨 为什么内联的 JSON 是**子集 + 替换过**的

页面上要内联快照 JSON（否则静态服务器上 `fetch` 不到任何东西 ⇒ 页面是空的）。
但把**整份**快照原样内联，就等于把跨口径那个倍数（`is_the_gap = false` 的那一列）
也印在页面的源码里 ⇒ 违反上表第三条。

⇒ 处置：内联的是**显示模型**（= 屏上真的显示的那些数），
不是快照原文；**跨口径那一列在进模型之前就被丢掉**。
页脚**明写**这一点，并给出完整快照的路径。

## 🚨 为什么每个数都带出处路径

* `at` 非空 ⇒ 「出处：`<产物路径> :: <JSON 路径>`」（逐字取自快照）。
* `at` 为空但 `sources` 非空（`kind = derived` 的推导数）⇒ 「出处：`<sources 列表>`」
  ＋「推导：`<derivation>`」。
* 两者都空 ⇒ **导出器直接报错退出**（rc=2），
  绝不渲染一个没有出处的数（第 109 层 / 第 167 层）。

## 用法

    .venv/bin/python scripts/export_progress_page.py
    .venv/bin/python scripts/export_progress_page.py --snapshot data/reports/goal_progress_2026-10-21.json
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORTS = ROOT / "data" / "reports"
DEFAULT_OUT = DEFAULT_REPORTS / "static" / "index.html"
DEFAULT_INDEX = ROOT / "aplus" / "web" / "index.html"

# 快照文件名模式（与 `aplus/webapp.py::GOAL_PROGRESS_FILE_RE` 同一条）
# 🚨 用**模式**而不是列名 —— 每天一份 ⇒ 列名就是形状 17。
SNAPSHOT_RE = re.compile(r"^goal_progress_(\d{4}-\d{2}-\d{2})\.json$")

# ── 文本型读数（长文本另起一行；判据是**后端给的 unit**，不是前端量长度）──
TEXT_UNITS = ("text", "lines", "json", "list")

# 🚨 **屏上不出现**的两个字段名（名字与内容相反，见
#    `docs/TODO_deployed_idle_cash_caliber_2026-10-21.md`）。
#    ⚠️ 产物自己的诚实边界第 7 条**逐字写着这两个词**
#    （「屏上不出现「投入率」「闲置比例」」）⇒ 逐字渲染就会把**禁令本身**印在屏上。
#    ⇒ 处置：**机械替换**成占位符，**不许改写句子**（改写 = 造一份没有出处的话），
#      并把替换清单印在页脚（占位符只指向口径台账文档，不复述被封禁的字段名）。
REDACTIONS = {
    "投入率": "⟨部署比例字段·口径台账封禁⟩",
    "闲置比例": "⟨闲置现金字段·口径台账封禁⟩",
}
REDACTION_DOC = "docs/TODO_deployed_idle_cash_caliber_2026-10-21.md"

# ── 从 `aplus/web/index.html` 借来的 CSS：只借**规则**，不借整份文件 ──
# 顶层选择器（精确匹配）
CSS_KEEP_EXACT = {
    ":root", "*", "html", "body", "::selection", "main",
    "button:focus-visible", "a:focus-visible",
    "summary:focus-visible", "[tabindex]:focus-visible",
}
# 选择器里**出现**即保留（子串）
CSS_KEEP_SUBSTRINGS = (
    ".brand", ".docket", ".panel", ".goal-", ".d2-gap-calibers",
    ".empty", ".skeleton",
)

# 🚨 静态页**专用**的布局类：只有布局，颜色/字号/圆角**一律用现有变量**。
#    ⇒ 不是「另起一套样式」，是同一套视觉语言下的三行布局。
STATIC_CSS = """
  /* ── 静态页专用（只用 index.html 的 CSS 变量，不另起配色） ── */
  .static-meta { color: var(--faint); font-size: 11.5px; line-height: 1.8;
                 font-variant-numeric: tabular-nums; }
  .static-foot { color: var(--faint); font-size: 11px; line-height: 1.8;
                 margin-top: 14px; border-top: 1px solid var(--line); padding-top: 8px; }
  .static-foot code { font-family: var(--mono); word-break: break-all; }
"""


# ══════════════════════════════════════════════════════════════════
# CSS：从 index.html 里挑出这一页用得到的规则
# ══════════════════════════════════════════════════════════════════

def strip_css_comments(css: str) -> str:
    """去掉 CSS 注释（注释里可能有 `{`/`}`，会让块切分错位）。"""
    out = []
    i, n = 0, len(css)
    while i < n:
        c = css[i]
        if c == "/" and i + 1 < n and css[i + 1] == "*":
            j = css.find("*/", i + 2)
            if j < 0:
                break
            i = j + 2
            continue
        if c in ("'", '"'):
            q = c
            j = i + 1
            while j < n:
                if css[j] == "\\":
                    j += 2
                    continue
                if css[j] == q:
                    j += 1
                    break
                j += 1
            out.append(css[i:j])
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def split_css_blocks(css: str) -> list[tuple[str, str]]:
    """顶层块 ⇒ `[(prelude, body)]`（prelude = 选择器或 at 规则头）。"""
    blocks = []
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            break
        prelude = css[i:j].strip()
        depth = 0
        k = j
        while k < n:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        blocks.append((prelude, css[j + 1:k]))
        i = k + 1
    return blocks


def selector_kept(selector: str) -> bool:
    for part in selector.split(","):
        part = part.strip()
        if not part:
            continue
        if part in CSS_KEEP_EXACT:
            return True
        if any(tok in part for tok in CSS_KEEP_SUBSTRINGS):
            return True
    return False


def filter_css(css: str) -> str:
    """只保留这一页用得到的规则；`@media` 里空了就整块丢掉。"""
    kept = []
    for prelude, body in split_css_blocks(css):
        if prelude.startswith("@"):
            at = prelude.split()[0].lower() if prelude.split() else ""
            if at in ("@media", "@supports"):
                inner = filter_css(body)
                if inner.strip():
                    kept.append("%s {\n%s\n}" % (prelude, inner))
            continue
        if selector_kept(prelude):
            kept.append("%s { %s }" % (prelude, " ".join(body.split())))
    return "\n".join(kept)


def read_page_css(index_html: Path) -> tuple[str, str]:
    """从 `aplus/web/index.html` 的 `<style>` 里取出这一页用的 CSS。

    返回 `(筛过的 CSS, :root 变量块原文)`。
    ⚠️ **取不到就报错退出** —— 交出一个没有配色的页面，比报错坏得多。
    """
    try:
        raw = index_html.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("读不到 %s：%s" % (index_html, exc))
    start = raw.find("<style")
    if start < 0:
        raise SystemExit("%s 里没有 <style> ⇒ 无法复用配色" % index_html)
    start = raw.index(">", start) + 1
    end = raw.find("</style>", start)
    if end < 0:
        raise SystemExit("%s 里的 <style> 没有闭合" % index_html)
    css = strip_css_comments(raw[start:end])
    blocks = split_css_blocks(css)
    root = ""
    for prelude, body in blocks:
        if prelude.strip() == ":root":
            root = ":root { %s }" % " ".join(body.split())
            break
    if not root:
        raise SystemExit("%s 的 CSS 里找不到 :root 变量块 ⇒ 无法复用配色" % index_html)
    kept = filter_css(css)
    if "--seal" not in kept:
        raise SystemExit("筛完的 CSS 里没有 --seal ⇒ 筛选逻辑坏了")
    return kept, root


# ══════════════════════════════════════════════════════════════════
# 文本处理
# ══════════════════════════════════════════════════════════════════

def redact(text: str) -> tuple[str, int]:
    """机械替换被封禁的字段名；返回 `(替换后, 替换了几处)`。

    ⚠️ 只返回**次数**，不返回被替换的词 —— 否则计数本身会把词带进页面。
    """
    total = 0
    for token, placeholder in REDACTIONS.items():
        n = text.count(token)
        if n:
            total += n
            text = text.replace(token, placeholder)
    return text, total


def redact_model(obj, counter: list) -> obj:
    """就地替换模型里**所有**字符串里的被封禁字面串（渲染层与 JSON 岛共用一份）。"""
    if isinstance(obj, dict):
        return {k: redact_model(v, counter) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_model(v, counter) for v in obj]
    if isinstance(obj, str):
        out, n = redact(obj)
        counter[0] += n
        return out
    return obj


def esc(text) -> str:
    """HTML 文本转义（`quote=False` ⇒ 属性值里的引号不转义，但我们不放属性里）。"""
    return html.escape("" if text is None else str(text), quote=False)


def prose(text) -> str:
    """转义 + 把产物里的 `**粗体**` 记号变成 `<b>`。

    ⚠️ 先转义再替换 ⇒ 占位符/正文里的尖括号不可能变成标签。
    """
    out = esc(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return out


# ══════════════════════════════════════════════════════════════════
# 显示模型（= 屏上真的显示的那些数；跨口径那一列在这里被丢掉）
# ══════════════════════════════════════════════════════════════════

def metric_view(m: dict, where: str) -> dict:
    """把一个读数压成显示模型；**没有出处就直接报错**。"""
    if not isinstance(m, dict):
        raise SystemExit("读数不是对象：%s" % where)
    at = m.get("at")
    sources = [s for s in (m.get("sources") or []) if s]
    if at:
        source = "出处：%s" % at
    elif sources:
        source = "出处：%s" % " · ".join(sources)
    else:
        raise SystemExit("读数**没有出处**（%s · %s）⇒ 拒绝渲染一个指不到产物路径的数" % (where, m.get("key")))
    view = {
        "key": m.get("key"),
        "label": m.get("label") or m.get("key") or "",
        "display": m.get("display"),
        "unit": m.get("unit"),
        "status": m.get("status"),
        "caliber": m.get("caliber"),
        "note": m.get("note"),
        "source": source,
        "derivation": m.get("derivation"),
        "is_text": (m.get("unit") in TEXT_UNITS),
    }
    return view


def build_goal_view(g: dict) -> dict:
    gid = g.get("id")
    metrics = [metric_view(m, "goal %s" % gid) for m in (g.get("metrics") or [])]
    calibers = []
    for c in (g.get("calibers") or []):
        calibers.append({
            "key": c.get("key"),
            "name": c.get("name"),
            "role": c.get("role"),
            "source": c.get("source"),
            "honest_boundary": c.get("honest_boundary"),
            "metrics": [metric_view(m, "goal %s / %s" % (gid, c.get("key")))
                        for m in (c.get("metrics") or [])],
        })

    # 🚨 「还差几倍」**只取同口径那一列**：跨口径那一列（台账自己标
    #    `is_the_gap = false`）**整列不进模型** —— 连 JSON 岛里都没有，
    #    所以它既不会被渲染，也不会被 grep 到源码里。
    gap_multiple = None
    mults = g.get("multiples")
    if isinstance(mults, dict):
        gap_cols = [c for c in (mults.get("columns") or [])
                    if isinstance(c, dict) and c.get("is_the_gap") is True]
        if len(gap_cols) != 1:
            raise SystemExit(
                "goal %s 的倍数族里 `is_the_gap == true` 的列**恰好要一列**，"
                "实得 %d 列 ⇒ 拒绝导出（宁可不导出，也不显示错的倍数）"
                % (gid, len(gap_cols)))
        col = gap_cols[0]
        gap_multiple = {
            "name": col.get("name"),
            "note": col.get("note"),
            "metric": metric_view(col.get("metric") or {},
                                  "goal %s / gap multiple" % gid),
        }

    acceptance = None
    acc = g.get("acceptance")
    if isinstance(acc, dict):
        acceptance = {
            "shape_note": acc.get("shape_note"),
            "w_note": acc.get("w_note"),
            "rows": [{"label": r.get("label"), "display": r.get("display")}
                     for r in (acc.get("rows") or [])],
        }

    auto = None
    a = g.get("automatable")
    if isinstance(a, dict):
        auto = {"what": a.get("what"), "why": a.get("why"), "caveat": a.get("caveat")}

    manual = []
    for m in (g.get("manual") or []):
        manual.append({
            "label": m.get("label"),
            "judgement": m.get("judgement") or "由人判断",
            "criteria": list(m.get("criteria") or []),
        })

    return {
        "id": gid,
        "title": g.get("title"),
        "goal_text": g.get("goal_text"),
        "registry_class": g.get("registry_class"),
        "registry_at": g.get("registry_at"),
        "status": g.get("status"),
        "status_label": g.get("status_label"),
        "reason": g.get("reason"),
        "criterion": g.get("criterion"),
        "honest_boundary": g.get("honest_boundary"),
        "metrics": metrics,
        "calibers": calibers,
        "gap_multiple": gap_multiple,
        "multiples_role": (mults or {}).get("role") if isinstance(mults, dict) else None,
        "acceptance": acceptance,
        "automatable": auto,
        "manual": manual,
    }


def build_timeline(reports_dir: Path) -> tuple[list, list]:
    """历日快照 + **缺的日历日**（缺一天可能是 cron 没跑 ⇒ 不许让时间轴看起来连续）。"""
    files = []
    for p in sorted(reports_dir.glob("goal_progress_*.json")):
        m = SNAPSHOT_RE.match(p.name)
        if m:
            files.append((m.group(1), p))
    files.sort()
    history = []
    for day, path in files:
        entry = {"date": day, "file": path.name, "readable": False,
                 "error": None, "summary_line": None, "change_line": None}
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("内容不是一个对象")
            change = doc.get("change") or {}
            entry.update(readable=True,
                         summary_line=doc.get("summary_line"),
                         change_line=change.get("line"))
        except (OSError, ValueError) as exc:
            entry["error"] = "%s: %s" % (type(exc).__name__, exc)
        history.append(entry)

    gaps = []
    days = [d for d, _ in files]
    for a, b in zip(days, days[1:]):
        try:
            d0 = datetime.strptime(a, "%Y-%m-%d").date()
            d1 = datetime.strptime(b, "%Y-%m-%d").date()
        except ValueError:
            continue
        cur = d0
        while cur < d1:
            cur = datetime.fromordinal(cur.toordinal() + 1).date()
            if cur < d1:
                gaps.append(cur.isoformat())
    return history, gaps


def build_model(snapshot: dict, snapshot_path: Path, reports_dir: Path,
                exported_at: str) -> dict:
    goals = [build_goal_view(g) for g in (snapshot.get("goals") or [])]
    history, gaps = build_timeline(reports_dir)
    model = {
        "schema": "aplus.progress_page/1",
        "generator": "scripts/export_progress_page.py",
        "snapshot_date": snapshot.get("snapshot_date"),
        "generated_at": snapshot.get("generated_at"),
        "exported_at": exported_at,
        "snapshot_file": snapshot_path.name,
        "snapshot_path": "data/reports/%s" % snapshot_path.name,
        "goal_registry": snapshot.get("goal_registry"),
        "summary_line": snapshot.get("summary_line"),
        "goals": goals,
        "honest_boundaries": list(snapshot.get("honest_boundaries") or []),
        "timeline": history,
        "gaps": gaps,
    }
    # 🚨 机械替换被封禁的字面串 —— **渲染层与 JSON 岛共用这一份模型**，
    #    所以两处都不会漏（两处各做一次 ⇒ 两份副本之间没有守卫，第 96 层的形状）。
    counter = [0]
    model = redact_model(model, counter)
    # ⚠️ 只记**次数**，不记被替换的词 ⇒ 计数本身不会把词带进页面。
    model["redactions"] = {"count": counter[0], "doc": REDACTION_DOC}
    return model


# ══════════════════════════════════════════════════════════════════
# 渲染
# ══════════════════════════════════════════════════════════════════

def metric_html(m: dict) -> str:
    missing = (m.get("status") == "missing") or (m.get("display") is None)
    val = "缺失" if missing else str(m.get("display"))
    cls = "goal-metric" + (" is-text" if m["is_text"] else "")
    vcls = "gm-val" + (" missing" if missing else "")
    html_parts = ['<div class="%s">' % cls,
                  '<span class="gm-label">%s</span>' % esc(m["label"]),
                  '<span class="%s">%s</span>' % (vcls, esc(val))]
    if m.get("caliber"):
        html_parts.append('<span class="gm-cal">口径：%s</span>' % prose(m["caliber"]))
    html_parts.append('<span class="gm-src">%s</span>' % prose(m["source"]))
    if m.get("derivation"):
        html_parts.append('<span class="gm-cal">推导：%s</span>' % prose(m["derivation"]))
    if m.get("note"):
        html_parts.append('<span class="gm-note">%s</span>' % prose(m["note"]))
    html_parts.append("</div>")
    return "\n".join(html_parts)


def caliber_html(c: dict) -> str:
    rows = "\n".join(metric_html(m) for m in c["metrics"])
    parts = ['<div class="goal-cal">',
             '<h5>%s</h5>' % prose(c.get("name")),
             '<div class="gc-role">%s</div>' % prose(c.get("role"))]
    if c.get("source"):
        parts.append('<div class="gm-src">%s</div>' % prose(c["source"]))
    parts.append('<div class="goal-metrics">%s</div>' % rows)
    if c.get("honest_boundary"):
        parts.append('<div class="goal-note" style="margin-top:6px">%s</div>'
                     % prose(c["honest_boundary"]))
    parts.append("</div>")
    return "\n".join(parts)


def goal_card_html(g: dict) -> str:
    parts = ['<div class="goal-card">',
             '<div class="goal-top">',
             '<span class="goal-status s-%s">%s</span>' % (esc(g.get("status")),
                                                           esc(g.get("status_label") or g.get("status"))),
             '<span class="goal-tag">目标 %s</span>' % esc(g.get("id")),
             '<span class="goal-tag">归档：%s</span>' % esc(g.get("registry_class") or ""),
             "</div>",
             "<h4>%s</h4>" % prose(g.get("title"))]
    if g.get("reason"):
        parts.append('<div class="goal-reason">%s</div>' % prose(g["reason"]))
    if g.get("criterion"):
        parts.append('<div class="goal-criterion">判据：%s</div>' % prose(g["criterion"]))
    parts.append('<div class="goal-metrics">%s</div>'
                 % "\n".join(metric_html(m) for m in g["metrics"]))
    if g["calibers"]:
        parts.append('<div class="goal-calibers">%s</div>'
                     % "\n".join(caliber_html(c) for c in g["calibers"]))
    if g.get("gap_multiple"):
        gm = g["gap_multiple"]
        parts.append(
            '<div class="goal-mults">'
            '<h5>还差几倍（倍数那一族）</h5>'
            '<div class="gc-role">%s</div>'
            '<div class="goal-mult is-gap">'
            '<span class="gm-label">%s</span>'
            '<span class="gm-val">%s</span>'
            '<span class="gm-flag">✅ 这一列是缺口</span>'
            '<span class="gm-cal">%s</span>'
            '<span class="gm-src">%s</span>'
            "</div></div>"
            % (prose(g.get("multiples_role")),
               prose(gm.get("name")),
               esc((gm["metric"].get("display") if gm["metric"].get("display") is not None
                    else "缺失")),
               prose(gm.get("note")),
               prose(gm["metric"]["source"])))
    if g.get("acceptance"):
        acc = g["acceptance"]
        rows = "".join(
            '<div class="goal-row"><span class="gr-label">%s</span>'
            '<span class="gr-val">%s</span></div>'
            % (prose(r.get("label")),
               esc("缺失" if r.get("display") is None else r.get("display")))
            for r in acc["rows"])
        parts.append(
            '<div class="goal-mults">'
            '<h5>验收（两道闸分别成立才算达成）</h5>'
            '<div class="gc-role">%s</div>'
            '<div class="goal-rows">%s</div>'
            '<div class="goal-note" style="margin-top:6px">%s</div>'
            "</div>"
            % (prose(acc.get("shape_note")), rows, prose(acc.get("w_note"))))
    if g.get("automatable"):
        a = g["automatable"]
        parts.append(
            '<div class="goal-mults">'
            '<h5>可自动化的部分</h5>'
            '<div class="gc-role">%s</div>'
            '<div class="goal-note">%s</div>'
            '<div class="goal-note">%s</div>'
            "</div>" % (prose(a.get("what")), prose(a.get("why")), prose(a.get("caveat"))))
    for m in g["manual"]:
        items = "".join("<li>%s</li>" % prose(c) for c in m["criteria"])
        parts.append('<div class="goal-manual"><h5>%s · %s</h5><ul>%s</ul></div>'
                     % (prose(m.get("label")), esc(m.get("judgement")), items))
    if g.get("honest_boundary"):
        parts.append('<div class="goal-note" style="margin-top:6px">%s</div>'
                     % prose(g["honest_boundary"]))
    parts.append("</div>")
    return "\n".join(parts)


def timeline_html(model: dict) -> str:
    items = []
    for h in reversed(model["timeline"]):
        if not h.get("readable"):
            items.append('<li><div class="tl-date">%s</div>'
                         '<div class="tl-bad">🚨 这一份读不出来：%s</div></li>'
                         % (esc(h["date"]), esc(h.get("error") or "原因未知")))
            continue
        items.append('<li><div class="tl-date">%s</div>'
                     '<div class="tl-sum">%s</div>'
                     '<div class="tl-chg">%s</div></li>'
                     % (esc(h["date"]), prose(h.get("summary_line")),
                        prose(h.get("change_line"))))
    gap_note = ""
    if model["gaps"]:
        gap_note = ('<div class="tl-gap">⚠️ 这几天的快照**缺失**'
                    '（cron 漏跑或那天没跑成）：%s</div>'
                    % esc("、".join(model["gaps"])))
    return '<ul class="goal-tl">%s</ul>%s' % ("".join(items), gap_note)


def read_favicon(index_html: Path) -> str:
    """把 favicon **内联**成 data URI ⇒ 页面一个外部请求都不发。

    ⚠️ 不是洁癖：浏览器**总会**去要 `/favicon.ico` ⇒ 不内联就有一次 404，
    而「页面自己带 favicon」是「零外部依赖」这条性质的最后一角。
    取不到就返回空串（favicon 不影响页面的内容正确性）。
    """
    fav = index_html.parent / "favicon.svg"
    try:
        raw = fav.read_bytes()
    except OSError:
        return ""
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")


def render_html(model: dict, css: str, favicon: str = "") -> str:
    goals = model["goals"]
    d2 = next((g for g in goals if g.get("registry_class") == "D2"), None)

    # ── D2 两个口径并排（屏上第一块） ──
    d2_block = ""
    if d2 and d2["calibers"]:
        gap = ""
        if d2.get("gap_multiple"):
            gm = d2["gap_multiple"]
            gap = (
                '<div class="goal-mults">'
                '<h5>还差几倍（同口径那一列）</h5>'
                '<div class="gc-role">%s</div>'
                '<div class="goal-mult is-gap">'
                '<span class="gm-label">%s</span>'
                '<span class="gm-val">%s</span>'
                '<span class="gm-flag">✅ 这一列是缺口</span>'
                '<span class="gm-cal">%s</span>'
                '<span class="gm-src">%s</span>'
                "</div></div>"
                % (prose(d2.get("multiples_role")),
                   prose(gm.get("name")),
                   esc(gm["metric"].get("display")
                       if gm["metric"].get("display") is not None else "缺失"),
                   prose(gm.get("note")),
                   prose(gm["metric"]["source"])))
        d2_block = (
            '<section class="panel">'
            '<div class="panel-head"><h2>D2 · 年化 20%%</h2>'
            '<span class="sub">两个口径并排 · 各带标签 · 不合并、不取极值</span>'
            '<span class="right">%s</span></div>'
            '<div style="padding:12px 20px 16px">'
            '<div class="goal-calibers">%s</div>%s'
            "</div></section>"
            % (esc(model.get("snapshot_date")),
               "\n".join(caliber_html(c) for c in d2["calibers"]),
               gap))

    bounds = "".join("<li>%s</li>" % prose(b)
                      for b in model["honest_boundaries"])

    cards = "\n".join(goal_card_html(g) for g in goals)

    # ── 页脚：这一页的边界（哪些是搬运、哪些被替换、完整产物在哪）──
    # ⚠️ 这里的**个数不许手打** ⇒ 用模型里算出来的替换次数。
    n_redacted = int((model.get("redactions") or {}).get("count") or 0)
    foot = (
        '<section class="panel"><div class="panel-head">'
        '<h2>这一页的边界</h2>'
        '<span class="sub">静态快照 · 只搬运 · 不测量</span></div>'
        '<div style="padding:12px 20px 16px">'
        '<div class="static-foot">'
        '① 本页是<b>静态快照</b>：页面上每个数都<b>冻结在上面的快照时间</b>，'
        '刷新页面不会得到新数（新的快照每晚随推送更新一次）。<br>'
        '② 页面上<b>没有一个数是本页算的</b> —— 每个数都带它的出处路径；'
        '带「推导」的是推导数（快照里 <code>kind = derived</code> 的那些），'
        '出处是它的输入产物。<br>'
        '③ 本页内联的 JSON 是快照的<b>子集</b>：'
        '「还差几倍」那一族里，<b>只有台账标 <code>is_the_gap = true</code> '
        '的同口径那一列</b>进了本页（跨口径那一列台账自己标了 '
        '<code>is_the_gap = false</code>，显示成缺口就是错的）；'
        '另有 <b>%d</b> 处<b>字面串</b>按口径台账封禁，'
        '原字段名见 <code>%s</code>。<br>'
        '④ 完整快照（含本页省略的那几列与原文）在仓内：'
        '<code>%s</code>；生成器 <code>%s</code>。'
        "</div></div></section>"
        % (n_redacted, esc(REDACTION_DOC), esc(model.get("snapshot_path")),
           esc(model.get("generator"))))

    body = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{favicon}">
<title>aplus 目标进度快照 · {date}</title>
<style>
/* ── 配色与版式：从 aplus/web/index.html 的 <style> 里挑出来的规则 ── */
{css}
/* ── 静态页专用（只有布局，颜色一律用上面这些变量） ── */
{static_css}
</style>
</head>
<body>
<div class="docket">
  <div class="docket-inner">
    <div class="verdict-line">
      <span class="verdict-word">目标进度 · 静态快照</span>
      <span class="verdict-why">{summary}</span>
    </div>
    <div class="docket-warn warn">
      <b>本页是静态快照。</b>
      <span>页面上每个数都冻结在下面的快照时间，刷新不会得到新数；
      新的快照每晚随推送更新一次。</span>
    </div>
    <div class="static-meta">
      快照时间 {generated_at} · 快照日期 {date} · 快照文件 <code>{snapshot_file}</code>
      · 本次导出 {exported_at}<br>
      目标登记表 {registry}
    </div>
  </div>
</div>
<div class="brand">
  <h1>aplus</h1>
  <span class="mode">目标进度</span>
  <span class="spacer"></span>
  <span class="run-dot"><i></i>静态页 · 无后端</span>
</div>
<main>
{d2_block}
<section class="panel">
  <div class="panel-head"><h2>8 条目标</h2>
    <span class="sub">逐条：状态 · 读数 · 出处路径</span>
    <span class="right">{n_goals} 条</span></div>
  <div style="padding:12px 20px 16px">
    <div class="goal-grid">{cards}</div>
  </div>
</section>
<section class="panel">
  <div class="panel-head"><h2>诚实边界</h2>
    <span class="sub">这一页不越过的那几条</span></div>
  <div style="padding:12px 20px 16px">
    <div class="goal-bounds"><b>诚实边界</b><ul>{bounds}</ul></div>
  </div>
</section>
<section class="panel">
  <div class="panel-head"><h2>时间线 · 历日快照</h2>
    <span class="sub">这就是「记录」：每晚长一格</span>
    <span class="right">{n_snapshots} 份</span></div>
  <div style="padding:12px 20px 16px">
    {timeline}
  </div>
</section>
{foot}
</main>
<script type="application/json" id="aplus-progress-snapshot">{payload}</script>
</body>
</html>
""".format(
        date=esc(model.get("snapshot_date")),
        css=css,
        favicon=favicon,
        static_css=STATIC_CSS,
        summary=prose(model.get("summary_line")),
        generated_at=esc(model.get("generated_at")),
        snapshot_file=esc(model.get("snapshot_file")),
        exported_at=esc(model.get("exported_at")),
        registry=prose(model.get("goal_registry")),
        d2_block=d2_block,
        n_goals=len(goals),
        cards=cards,
        bounds=bounds,
        timeline=timeline_html(model),
        n_snapshots=len(model["timeline"]),
        foot=foot,
        payload=json.dumps(model, ensure_ascii=False, sort_keys=True).replace("<", "\\u003c"),
    )
    return body


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

def latest_snapshot(reports_dir: Path) -> Path:
    files = []
    for p in sorted(reports_dir.glob("goal_progress_*.json")):
        m = SNAPSHOT_RE.match(p.name)
        if m:
            files.append((m.group(1), p))
    if not files:
        raise SystemExit(
            "%s 下一份目标进度快照都没有 —— 跑 scripts/measure_goal_progress.py 生成"
            % reports_dir)
    files.sort()
    return files[-1][1]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="把目标进度快照导出成自包含静态页")
    ap.add_argument("--snapshot", default=None,
                    help="快照路径（默认：%s 下最新的一份）" % DEFAULT_REPORTS)
    ap.add_argument("--reports-dir", default=str(DEFAULT_REPORTS),
                    help="快照目录（时间线从这里扫）")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="输出 HTML 路径（默认：%s）" % DEFAULT_OUT)
    ap.add_argument("--index", default=str(DEFAULT_INDEX),
                    help="借配色的页面（默认：%s）" % DEFAULT_INDEX)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    reports_dir = Path(args.reports_dir)
    snapshot_path = Path(args.snapshot) if args.snapshot else latest_snapshot(reports_dir)
    if not snapshot_path.exists():
        raise SystemExit("快照不存在：%s" % snapshot_path)
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("快照读不出来：%s: %s" % (type(exc).__name__, exc))
    if not isinstance(snapshot, dict) or not snapshot.get("goals"):
        raise SystemExit("快照内容不对（没有 goals）⇒ 拒绝导出")
    if not (snapshot.get("goals") or [])[0].get("id") == 1:
        raise SystemExit("快照的目标编号不是从 1 开始 ⇒ 拒绝导出")

    css, _root = read_page_css(Path(args.index))
    favicon = read_favicon(Path(args.index))
    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model = build_model(snapshot, snapshot_path, reports_dir, exported_at)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    page = render_html(model, css, favicon)

    # 🚨 落盘前自检：屏上不许出现的东西，一处都不许有。
    _self_check(page, model)

    out.write_text(page, encoding="utf-8")
    print("已导出静态页：%s（%d 字节 · 快照 %s · 快照时间 %s）"
          % (out, len(page.encode("utf-8")), model.get("snapshot_date"),
             model.get("generated_at")))
    return 0


def _self_check(page: str, model: dict) -> None:
    """导出前的最后一道闸：屏上不许出现的东西，一处都不许有。

    ⚠️ 这是**导出器自己**的闸（不是只有测试才查）⇒
    就算有人绕过测试直接跑导出器，也拿不到一个违规的页面。

    🚨 为什么倍数那一族用**数值**判而不是**子串**判：
       产物里另有一个**合法**读数 `95% 单侧上界 U = 22.3554%`，
       它的显示串里恰好含子串 `2.35` ⇒ 用裸子串判会把**合法的读数**判死
       （过严的守卫和恒真的守卫一样有害：它会让人去改本来正确的读数）。
       ⇒ 判据 = 「页面里没有任何数值等于跨口径那个倍数」＋
         「页面里每一处 `2.35` 都属于 `22.3554` 这个更长的数」。
    """
    for token in REDACTIONS:
        if token in page:
            raise SystemExit("页面里还留着被封禁的字面串 ⇒ 拒绝导出")
    if "已达成 20%" in page or "已达成20%" in page:
        raise SystemExit("页面里出现了「已达成 20%」⇒ 拒绝导出")
    if "2.3450" in page:
        raise SystemExit("页面里出现了跨口径那个倍数 ⇒ 拒绝导出")

    values = set()
    for tok in re.findall(r"-?\d+(?:\.\d+)?", page):
        try:
            values.add(float(tok))
        except ValueError:
            pass
    for banned in (2.35, 2.3450157528864404):
        if banned in values:
            raise SystemExit("页面里出现了跨口径那个倍数的数值 ⇒ 拒绝导出")
    if page.count("2.35") != page.count("22.3554"):
        raise SystemExit("页面里有一处 `2.35` 不属于合法读数 `22.3554` ⇒ 拒绝导出")

    # 🚨 每个读数都必须带出处路径（快照里 `kind = derived` 的那些走 `sources`）。
    for g in model["goals"]:
        for m in list(g["metrics"]) + [mm for c in g["calibers"] for mm in c["metrics"]]:
            if not m.get("source"):
                raise SystemExit("读数 %s 没有出处路径 ⇒ 拒绝导出" % m.get("key"))


if __name__ == "__main__":
    sys.exit(main())
