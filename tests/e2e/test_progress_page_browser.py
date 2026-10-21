"""目标进度**静态页**的可读性守卫 · **浏览器级**（2026-10-21 第二轮）。

## 为什么这一份在 `tests/e2e/` 而不是 `tests/`

★ **不是「因为它要浏览器」这么简单** —— `tests/e2e/conftest.py` 已经有一个
**session 级**的 playwright 会话，而它用完之后**主线程上会留下一个
「正在运行」的 asyncio 循环** ⇒ 同一进程里再开第二个 `sync_playwright()`
会被 playwright 自己拒绝。

⇒ 浏览器级守卫必须**复用** `tests/e2e/conftest.py` 的 `browser` 夹具
（**同一份 session 级实例**），而不是自己再开一个。

静态级 / node 级的守卫在 `tests/test_progress_page_export.py`（快、无依赖、
进每一次门禁）；**这一份是第二层**：真 Chromium、真视口矩阵。

## 这一份量的是什么

| 量 | 判据 | 阈值从哪来 |
|---|---|---|
| **首屏必含** | 一句话结论 + 快照时间 + **两个口径**（各带文字标签与出处路径）的**底边** | **1200px**（需求给的数）**且** ≤ 3 屏（需求给的理由：「用户在手机上 3 次滑动内看完主要结论」） |
| **横向溢出** | `documentElement.scrollWidth − clientWidth` | 必须 **= 0**（需求：「横向溢出 = 0」） |
| **总高度** | `documentElement.scrollHeight` | ≤ **4 屏**（3 屏给主结论 ＋ 1 屏给 8 条读数行；见下面的推导） |
| **折叠区** | 全部展开后**一个字不少** | 需求：「折叠区展开后内容一个字不少」 |
| **8 条目标** | 每条都有**可见的**状态标签（不展开也看得见） | 需求：「8 条目标每条都有状态标签（不许变成散文段落）」 |

## ⚠️ 为什么「总高度 ≤ 4 屏」而不是「≤ 3 屏」

* **3 屏**是需求给的理由所指的那个量：**主要结论**（结论 + 快照时间 +
  两个口径）⇒ 本文件用 `≤ 1200px 且 ≤ 3 屏` **两条一起**钉住它。
* **整页**是**另一个量**（第 7 层：两个各自都对的数不能当同一个量比）。
  整页必须常驻 ① 一句话结论 ② 快照时间 ③ D2 三块
  ④ **8 条目标的读数行**（每行**必须**带判据 —— 这是需求的结构要求）
  ⑤ 两个折叠面板的头 ⑥ 页脚。
  实测 ④ 在 **390px 宽的手机上要 1.9 屏** ⇒ 「整页 ≤ 3 屏」在手机上
  **结构上不可达**（差 379px）⇒ 整页的上限取 **4 屏**。
* ⇒ **两个阈值分开判、各自写明理由** —— 不许拿「3 屏」去冒充「整页 ≤ 3 屏」。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts" / "export_progress_page.py"
REPORTS = ROOT / "data" / "reports"

# 视口矩阵：桌面 / iPad 竖屏 / 手机
_VIEWPORTS = [
    ("desktop", 1440, 900),
    ("ipad-portrait", 820, 1180),
    ("phone", 390, 844),
]

# 首屏必含区的上限（px）—— 需求给的数
FIRST_SCREEN_PX = 1200
# 首屏必含区 ≤ 3 屏 —— 需求给的理由
FIRST_SCREEN_SCREENS = 3
# 整页总高 ≤ 4 屏 —— 推导见模块 docstring
TOTAL_SCREENS = 4


@pytest.fixture(scope="module")
def exported_page(tmp_path_factory):
    """真跑一次导出器，拿到一个 **file://** 页面（静态页不需要服务器）。"""
    out = tmp_path_factory.mktemp("static") / "index.html"
    r = subprocess.run(
        [sys.executable, str(EXPORTER), "--out", str(out),
         "--reports-dir", str(REPORTS)],
        cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, "导出器 rc=%s：%s" % (r.returncode, r.stderr)
    return out.as_uri()


_MEASURE = """
() => {
  const d = document.documentElement;
  const bottom = (sel) => {
    const e = document.querySelector(sel);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return Math.round(r.bottom + window.scrollY);
  };
  const cards = Array.from(document.querySelectorAll(
    '.d2-gap-calibers > .d2c:not(.d2c-wide)'));
  const wide = Array.from(document.querySelectorAll(
    '.d2-gap-calibers > .d2c-wide'));
  const folds = Array.from(document.querySelectorAll('.goal-fold'));
  return {
    scrollHeight: d.scrollHeight,
    innerHeight: window.innerHeight,
    clientWidth: d.clientWidth,
    overflowX: d.scrollWidth - d.clientWidth,
    conclusionBottom: bottom('.static-conclusion'),
    metaBottom: bottom('.static-meta'),
    caliberBottoms: cards.map(e =>
      Math.round(e.getBoundingClientRect().bottom + window.scrollY)),
    nCalibers: cards.length,
    caliberVisible: cards.map(e =>
      e.checkVisibility ? e.checkVisibility() : true),
    nWide: wide.length,
    wideBottoms: wide.map(e =>
      Math.round(e.getBoundingClientRect().bottom + window.scrollY)),
    nGoalFolds: folds.length,
    nStatus: document.querySelectorAll(
      '.goal-fold > summary .goal-status').length,
    statusTexts: Array.from(document.querySelectorAll(
      '.goal-fold > summary .goal-status')).map(e => e.innerText.trim()),
    statusVisible: Array.from(document.querySelectorAll(
      '.goal-fold > summary .goal-status')).map(
        e => e.checkVisibility ? e.checkVisibility() : true),
    foldTexts: Array.from(document.querySelectorAll('details')).map(
      d => d.textContent),
  };
}
"""


def _open(browser, url: str, width: int, height: int):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    return ctx, page


@pytest.fixture(scope="module")
def measurements(browser, exported_page):
    """三个视口各量一次（**不 sleep 之后「看一眼」**——量的是真盒子）。"""
    out = {}
    for name, w, h in _VIEWPORTS:
        ctx, page = _open(browser, exported_page, w, h)
        try:
            out[name] = (w, h, page.evaluate(_MEASURE))
        finally:
            ctx.close()
    return out


# ── 首屏必含 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [n for n, _, _ in _VIEWPORTS])
def test_the_first_screen_holds_the_conclusion_the_snapshot_time_and_both_calibers(
        measurements, name):
    """首屏里必须有：① 一句话结论 ② 快照时间 ③ **两个口径**。

    🚨 三个视口**都**要过 —— 「在桌面上过了」不等于「在手机上也过了」。
    """
    w, h, m = measurements[name]
    assert m["conclusionBottom"] is not None, "页面上没有一句话结论"
    assert m["metaBottom"] is not None, "页面上没有快照时间"
    assert m["nCalibers"] == 2, "首屏的口径不是 2 个：%d" % m["nCalibers"]
    # 🚨 「在 DOM 里」≠「看得见」：口径若被折进 <details>，元素**还在**，
    #    但 `getBoundingClientRect()` 变成 0 ⇒ 只量位置会**假绿**。
    assert all(m["caliberVisible"]), \
        "有一个口径**看不见**（被折进折叠区了？）" 
    assert m["nWide"] == 1, "首屏没有「还差几倍（同口径那一列）」那一行"

    limit_px = min(FIRST_SCREEN_PX, FIRST_SCREEN_SCREENS * h)
    for what, bottom in (("一句话结论", m["conclusionBottom"]),
                         ("快照时间", m["metaBottom"])):
        assert bottom <= limit_px, \
            "%s 在 y=%d，超出首屏上限 %d（%s）" % (what, bottom, limit_px, name)
    # 两个口径**各自**都要在首屏（不许一个在首屏、另一个在下面）
    for i, bottom in enumerate(m["caliberBottoms"], 1):
        assert bottom <= limit_px, \
            "第 %d 个口径的底边在 y=%d，超出首屏上限 %d（%s）" % (
                i, bottom, limit_px, name)
    # 「还差几倍」那一行也必须在首屏（它是需求点名的「最关键的数」之一）
    for bottom in m["wideBottoms"]:
        assert bottom <= limit_px, \
            "「还差几倍」那一行的底边在 y=%d，超出首屏上限 %d（%s）" % (
                bottom, limit_px, name)


# ── 横向溢出 = 0 ─────────────────────────────────────────────────

@pytest.mark.parametrize("name", [n for n, _, _ in _VIEWPORTS])
def test_the_page_never_scrolls_sideways(measurements, name):
    """横向溢出必须是 **0**。

    ⚠️ 「横向滚动本身不是罪，罪的是**整个页面**被撑出横向滚动条」
    ⇒ 这里判的是 `documentElement` 的溢出，不是某个内部容器。
    """
    w, h, m = measurements[name]
    assert m["overflowX"] == 0, \
        "%s 视口下整页横向溢出 %dpx（宽度 %d）" % (name, m["overflowX"], w)


# ── 总高度上限 ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", [n for n, _, _ in _VIEWPORTS])
def test_the_page_height_stays_within_the_declared_budget(measurements, name):
    """整页总高 ≤ **4 屏**（阈值与推导见模块 docstring）。"""
    w, h, m = measurements[name]
    limit = TOTAL_SCREENS * h
    assert m["scrollHeight"] <= limit, \
        "%s 视口下整页 %dpx > 上限 %dpx（%d 屏）" % (
            name, m["scrollHeight"], limit, TOTAL_SCREENS)


# ── 8 条目标：每条都有**看得见的**状态标签 ───────────────────────

@pytest.mark.parametrize("name", [n for n, _, _ in _VIEWPORTS])
def test_all_eight_goals_show_their_status_label_without_expanding(
        measurements, name):
    """8 条目标各有一个**看得见**的状态标签（不点开也看得见）。"""
    w, h, m = measurements[name]
    assert m["nGoalFolds"] == 8, "目标行不是 8 条：%d" % m["nGoalFolds"]
    assert m["nStatus"] == 8, "状态标签不是 8 个：%d" % m["nStatus"]
    assert all(m["statusVisible"]), "有状态标签看不见（在折叠体里）"
    assert all(t for t in m["statusTexts"]), "有状态标签是空的"


# ── 折叠区展开后**一个字不少** ───────────────────────────────────

def test_opening_every_fold_keeps_every_word(browser, exported_page):
    """把每个 `<details>` 展开 ⇒ 折叠区里的内容**一个字不少**地出现。

    ⚠️ 判据是**展开后的可见文本**包含折叠前抓到的**原文** ——
    不是「折叠体还在 DOM 里」（那对「被 `display:none` 藏起来」是恒真的）。
    """
    ctx, page = _open(browser, exported_page, 1440, 900)
    try:
        before = page.evaluate(
            "() => Array.from(document.querySelectorAll('details'))"
            ".map(d => d.textContent)")
        assert before, "页面上一个折叠区都没有 ⇒ 守卫坏了"
        page.evaluate(
            "() => document.querySelectorAll('details').forEach(d => d.open = true)")
        after = page.evaluate("() => document.body.innerText")

        def norm(s: str) -> str:
            # ⚠️ **连空白一起去**：textContent 与 innerText 的空白本来就不同
            #    （前者没有元素之间的换行，后者按块级插入换行）
            #    ⇒ 用「压成单空格」比会**恒假**（实测差一个空格就判红）。
            #    这一条比的是「**这句话在不在**」，不是「两个字节串逐位一样」。
            return re.sub(r"\s+", "", s)

        after_n = norm(after)
        missing = []
        for i, t in enumerate(before):
            t_n = norm(t)
            if t_n and t_n not in after_n:
                missing.append(i)
        assert not missing, "展开后这些折叠区的内容不见了：%r" % missing
        # 正控：展开后**确实变长了**（否则上面那条可能是恒真的）
        closed_len = page.evaluate(
            "() => { document.querySelectorAll('details').forEach("
            "d => d.open = false); return document.body.innerText.length; }")
        assert len(after) > closed_len, \
            "展开后可见文本没有变长 ⇒ 「一个字不少」这条守卫是恒真的"
    finally:
        ctx.close()


# ── 首屏**不许**有「未达 / 接近」这种把「测不出」说死的措辞 ───────

def test_the_visible_page_never_says_the_d2_target_was_not_reached(browser,
                                                                  exported_page):
    """「不可判定（测不出）」不许被写成「未达 / 接近」。

    ⚠️ **只查状态标签与结论行**，不查全文 ——
    产物自己的**判据原文**里就有「c ≥ T ⇒ 达成；U < T ⇒ 未达」这句
    （那是四条穷举里的一条，**不是**对当前状态的结论）
    ⇒ 全文查「未达」会把一条**正确的**渲染判死（第 112 层：过严的守卫
    和恒真的守卫一样有害）。
    """
    ctx, page = _open(browser, exported_page, 1440, 900)
    try:
        texts = page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "'.goal-status, .static-conclusion, .gl-lab, .gl-val'))"
            ".map(e => e.innerText)")
        assert texts, "没抓到任何状态/结论/读数 ⇒ 守卫坏了"
        for t in texts:
            for bad in ("未达", "未达成", "接近", "差一点", "已达成"):
                assert bad not in t, "屏上出现了 %r：%r" % (bad, t)
    finally:
        ctx.close()
