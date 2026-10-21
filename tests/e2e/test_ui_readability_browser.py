"""概览页可读性守卫 · **浏览器级**（2026-10-21）。

## 为什么这一份在 `tests/e2e/` 而不是 `tests/`

★ **不是「因为它要浏览器」这么简单** —— 是因为**本仓 e2e 那套 harness
已经有一个 session 级的 playwright 会话**，而它用完之后
**主线程上会留下一个「正在运行」的 asyncio 循环**（playwright 的同步 API
用 greenlet 驱动事件循环，`run_until_complete` 在 greenlet 切换点被中断
⇒ 那个循环一直处于 running 状态）。

⇒ 后果：**同一进程里再开第二个 `sync_playwright()` 会被 playwright 自己拒绝**
（`Error: It looks like you are using Playwright Sync API inside the asyncio loop`）。

⇒ 所以：浏览器级守卫必须**复用** `tests/e2e/conftest.py` 的
`browser` / `live_server` 夹具（**同一份 session 级实例**），
而不是自己再开一个。放在 `tests/` 下的文件**看不到** `tests/e2e/conftest.py`
的夹具 ⇒ 自己开 ⇒ 撞上那个残留的循环 ⇒ 整批 ERROR。

⇒ 静态级 / node 级的守卫在 `tests/test_ui_readability.py`（快、无依赖、
进每一次门禁）；**这一份是第二层**：真 Chromium、真视口矩阵。

## 判据的纪律

- **行为层**：量的是**真页面上的真盒子**（`getBoundingClientRect`、
  `getComputedStyle`、`documentElement.scrollWidth`），不是「源码里有没有某句话」。
- **不许用子串断言钉 HTML**（第 13 种形状）：这里没有一条断言是
  「源码里出现了某个串」。
- 视口矩阵：1440（桌面）/ 820（iPad 竖屏）/ 768（iPad mini）/ 390（手机）。
"""

from __future__ import annotations

import pytest

_VIEWPORTS = [
    ("desktop", 1440, 900),
    ("ipad-portrait", 820, 1180),
    ("ipad-mini", 768, 1024),
    ("phone", 390, 844),
]

_ALL_TABS = ["overview", "goal", "history", "backtest",
             "evolution", "bond", "chat", "logs"]


def _open(browser, url: str, width: int, height: int):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    page.goto(url + "#/overview", wait_until="domcontentloaded")
    # 轮询一个**明确的完成条件**，绝不 sleep 之后「看一眼」
    page.wait_for_function(
        "() => { const e = document.getElementById('pred-rows-up');"
        "        return e && e.children.length > 0; }",
        timeout=30_000,
    )
    page.wait_for_load_state("networkidle", timeout=30_000)
    return ctx, page


# 页面里「被裁掉且滚不到」的内容长什么样。
# ⚠️ 判据要**行为层**：元素的内容宽度超过它**最近的裁切祖先**的可见宽度，
#    而那个祖先是 `overflow:hidden`（裁掉）**不是** `auto/scroll`（能滚到）。
_CLIP_PROBE = """
(panelId) => {
  const panel = document.getElementById(panelId);
  const bad = [];
  panel.querySelectorAll('*').forEach(el => {
    // 元素自己是滚动容器 ⇒ 它的内容**到得了**，不算缺陷
    const selfOv = getComputedStyle(el).overflowX;
    if (selfOv === 'auto' || selfOv === 'scroll') return;
    // SVG 内部坐标与 CSS 盒模型不是一回事 ⇒ 不拿它判「被裁掉」
    if (el.tagName === 'svg' || el.ownerSVGElement) return;
    let p = el.parentElement, clip = null;
    while (p && p !== panel) {
      const o = getComputedStyle(p);
      if (o.overflowX === 'hidden' || o.overflowX === 'clip') { clip = p; break; }
      if (o.overflowX === 'auto' || o.overflowX === 'scroll') return;   // 上面有可滚的 ⇒ 到得了
      p = p.parentElement;
    }
    if (!clip) return;
    const cw = clip.clientWidth;
    if (el.scrollWidth > cw + 1) {
      bad.push({
        el: el.tagName + '.' + String(el.className || '').slice(0, 40),
        need: el.scrollWidth, have: cw,
        clipper: clip.tagName + '.' + String(clip.className || '').slice(0, 30),
      });
    }
  });
  return bad;
}
"""


@pytest.mark.parametrize("name,width,height", _VIEWPORTS, ids=[v[0] for v in _VIEWPORTS])
def test_the_whole_page_never_scrolls_sideways(browser, live_server, name, width, height):
    """★ 整页**不得**出现横向滚动（用户被迫左右拖）。

    ⚠️ 宽表格**内部**横滚是允许的（那是本仓 e2e 明确认可的形态：
       「横向溢出本身不是罪，罪的是整个页面被撑出横向滚动条」）。
    ⇒ 判据钉在 `documentElement.scrollWidth` 与 `clientWidth` 的关系上。
    """
    ctx, page = _open(browser, live_server, width, height)
    try:
        for tab in _ALL_TABS:
            page.click(f"#tab-{tab}")
            page.wait_for_timeout(250)
            m = page.evaluate("() => ({ s: document.documentElement.scrollWidth,"
                              " c: document.documentElement.clientWidth })")
            assert m["s"] <= m["c"] + 2, (
                f"{name}（{width}×{height}）在「{tab}」页上整页横向溢出 "
                f"{m['s'] - m['c']}px")
    finally:
        ctx.close()


@pytest.mark.parametrize("name,width,height", _VIEWPORTS, ids=[v[0] for v in _VIEWPORTS])
def test_no_content_is_cut_off_out_of_reach_in_any_tab(browser, live_server, name, width, height):
    """★ **被裁掉且滚不到**的内容 = 用户永远读不到。

    🚨 2026-10-21 实测（390px / 768px 视口）：
       「实盘配置 vs 判决配置」表 829px 装在 298px 的 `overflow:hidden` 容器里
       ⇒ 「实盘配置」整列 + 「来源」整列**永久不可达**；
       「买得起吗」表的名称列被压成**逐字竖排**；
       转债臂表与进化候选表同样被面板裁掉。
    🚨 而且**四个视口全中**（含 1440 桌面）：改进台账里有一条 `P.md-p` 要 **1427px**，
       而那个面板只有 1130px ⇒ 一条长路径把整块撑出去、**又滚不到**。
    ⇒ 判据：元素的**内容宽度**超过它最近裁切祖先的**可见宽度**，
       而那个祖先是 `hidden`/`clip`（不是 `auto`/`scroll`）⇒ 红。
    ⇒ **所有页签**都查一遍：只查概览页 ⇒ 别的页签继续裁。
    """
    ctx, page = _open(browser, live_server, width, height)
    try:
        for tab in _ALL_TABS:
            page.click(f"#tab-{tab}")
            page.wait_for_timeout(400)
            bad = page.evaluate(_CLIP_PROBE, f"panel-{tab}")
            assert not bad, (
                f"{name}（{width}×{height}）在「{tab}」页上："
                f"{len(bad)} 处内容被裁掉且滚不到，例如 {bad[:3]}")
    finally:
        ctx.close()


def test_the_goal_progress_tab_shows_no_raw_markdown(browser, live_server):
    """★ 目标进度那段的文案带 Markdown（实测 143 处加粗 + 118 处行内代码），
    页面**不许原样打印**裸标记。

    ⚠️ 这是静态级（node 真跑渲染器）那条的**浏览器版复核**：
       静态那条跑的是渲染函数，这里验的是**真页面上的真文本**
       （含时间线与诚实边界那几处）。
    """
    ctx, page = _open(browser, live_server, 1440, 900)
    try:
        page.click("#tab-goal")
        page.wait_for_function(
            "() => { const e = document.getElementById('goal-progress-body');"
            "        return e && e.querySelectorAll('.goal-card').length > 0; }",
            timeout=30_000,
        )
        text = page.inner_text("#panel-goal")
        assert "**" not in text, "目标进度页上还有裸的加粗标记"
        assert "`" not in text, "目标进度页上还有裸的行内代码标记"
    finally:
        ctx.close()


def test_the_four_states_are_distinguishable_by_text_and_colour(browser, live_server):
    """★ 四态：**文字 + 颜色**双通道（不只靠颜色）。

    ⚠️ 视觉可达性：只靠颜色 ⇒ 色觉障碍与黑白打印下四态全糊。
    ⇒ 这里量**计算色**，并把设计契约写成断言：
       「计数为 0 的那个数字**变暗**（安静，不喊人）；
        计数不为 0 的那个数字用**它自己状态的颜色**；
        「未定」永远不参与变暗（「测不出」不是「零」）」。
    ⚠️ 判据是**关系**（哪个颜色对应哪种情形），不是「四个颜色互不相同」——
       实测数据是 0/5/0/0 ⇒ 三个 0 本就该同色（安静），
       钉「互不相同」会是一条**永远红**的守卫（第 112 层）。
    """
    ctx, page = _open(browser, live_server, 1440, 900)
    try:
        page.click("#tab-overview")
        page.wait_for_function(
            "() => { const e = document.getElementById('holdings-alert-body');"
            "        return e && e.querySelectorAll('.ha-chip').length >= 4; }",
            timeout=30_000,
        )
        got = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('#holdings-alert-body .ha-chip').forEach(c => {
              const b = c.querySelector('b');
              out.push({ cls: c.className, text: c.innerText.trim(),
                         n: parseInt(b.textContent, 10),
                         color: getComputedStyle(b).color });
            });
            return out;
        }""")
        assert len(got) >= 4, f"四态没渲染出来：{got}"
        labels = [g["text"] for g in got]
        for label in ("减仓", "观察", "持有"):
            assert any(label in t for t in labels), f"四态里没有 {label!r}：{labels}"
        assert any("测不出" in t for t in labels), f"「未定」没有写出「测不出」：{labels}"

        tokens = page.evaluate("""() => {
            const out = {};
            ['--up','--down','--seal','--void','--faint'].forEach(t => {
                const c = document.createElement('span');
                c.style.color = 'var(' + t + ')';
                document.body.appendChild(c);
                out[t] = getComputedStyle(c).color;
                c.remove();
            });
            return out;
        }""")
        state_color = {"REDUCE": tokens["--up"], "WATCH": tokens["--seal"],
                       "HOLD": tokens["--down"], "UNDECIDED": tokens["--void"]}
        for g in got:
            states = [s for s in state_color if f"s-{s}" in g["cls"]]
            assert states, f"这个科目没有状态类：{g}"
            st = states[0]
            if st == "UNDECIDED":
                want = tokens["--void"]
            elif g["n"] == 0:
                want = tokens["--faint"]
            else:
                want = state_color[st]
            assert g["color"] == want, (
                f"状态 {st}（计数 {g['n']}）的读数颜色是 {g['color']}，"
                f"按设计应当是 {want}"
                "（零计数要安静、非零用状态色、「测不出」永不参与变暗）")
    finally:
        ctx.close()


def test_reduced_motion_actually_stops_the_animations(browser, live_server):
    """★ `prefers-reduced-motion: reduce` 下**不许**还有动画在跑。

    🚨 这一页有两处动效：页签面板的淡入（`.18s`）与聊天那根记账线（`.7s`）。
    ⇒ 媒体查询写的是 `no-preference`（只在「不介意动效」时才给），
    判据是**计算出来的** `animation-name`，不是「源码里有没有那个媒体查询」
    （后者会被注释满足）。
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                              reduced_motion="reduce")
    page = ctx.new_page()
    try:
        page.goto(live_server + "#/overview", wait_until="domcontentloaded")
        page.wait_for_function(
            "() => { const e = document.getElementById('pred-rows-up');"
            "        return e && e.children.length > 0; }",
            timeout=30_000,
        )
        names = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('[role="tabpanel"], .turn .rule').forEach(el => {
                const a = getComputedStyle(el).animationName;
                if (a && a !== 'none') out.push(el.className + ' => ' + a);
            });
            return out;
        }""")
        assert not names, f"reduce 模式下仍有动画在跑：{names}"
    finally:
        ctx.close()


def test_the_hero_is_a_comparison_strip_not_a_second_copy_of_row_one(browser, live_server):
    """★ 首屏那两张「首选卡」必须是**一行对照**，不许又抄一遍第 01 行。

    🚨 2026-10-21 实测：原来每张 ~230px，而它们的每一项都在下面的表里出现过
       （名称 / 代码 / 20 日涨跌 / 综合分 / 消息面 = 第 01 行）⇒
       首屏被它吃掉 ~180px，而首屏要回答的是「今天看哪 10 只」。
    ⇒ 压成一行，只留「并排比较」这件事本身。
    ⚠️ 判据落在**结构**上（不许再有第二行/第三行的块），不是像素高度 ——
       像素高度随本机字体变，钉死它会造出一条会假红的守卫（第 112 层）。
    """
    ctx, page = _open(browser, live_server, 1440, 900)
    try:
        got = page.evaluate("""() => {
            const heroes = document.querySelectorAll('#heroes .hero');
            return {
                n: heroes.length,
                extra: document.querySelectorAll('#heroes .hero .why, #heroes .hero .news').length,
                texts: [...heroes].map(h => h.innerText.replace(/\\s+/g, ' ').trim()),
            };
        }""")
        assert got["n"] == 2, f"首屏那两张对照卡不见了：{got}"
        assert got["extra"] == 0, (
            f"对照卡里又出现了第 01 行的副本（{got['extra']} 个 .why/.news 块）"
            " ⇒ 首屏被重复内容吃掉，名单的第一行被推到首屏之外")
        for t in got["texts"]:
            assert t, "对照卡是空的"
    finally:
        ctx.close()


def test_the_first_daily_row_is_already_on_the_first_screen(browser, live_server):
    """★ 名单的**第一行**必须在首屏里（不必先滚一屏才看到今天看什么）。

    🚨 2026-10-21 实测（1440×900）：判决板 521px + 两张首选卡 ~230px
       ⇒ 名单第一行落在 y = 1040 ⇒ **首屏一行名单都看不到**。
    ⇒ 判据是「第一行的**上沿**在视口内」，不是某个写死的坐标
       （坐标会随文案长度与视口宽度过期 ⇒ 第 140 层）。
    """
    ctx, page = _open(browser, live_server, 1440, 900)
    try:
        got = page.evaluate("""() => {
            const tr = document.querySelector('#pred-rows-up tr[data-rank]');
            if (!tr) return null;
            const b = tr.getBoundingClientRect();
            return { top: Math.round(b.top), vh: window.innerHeight };
        }""")
        assert got, "名单第一行没渲染出来"
        assert got["top"] < got["vh"], (
            f"名单第一行在 y = {got['top']}，视口高 {got['vh']} ⇒ 首屏看不到今天看什么")
    finally:
        ctx.close()


def test_the_keyboard_can_reach_the_daily_list_and_the_focus_is_visible(browser, live_server):
    """★ 键盘**够得到**名单，且焦点**看得见**。

    ⚠️ 名单 20 行密排数字 ⇒ 表格用 roving tabindex（整张表一个停靠点，
       上下用方向键）⇒ 判据是「焦点落在某一行上」且「那一行有可见轮廓」。
    """
    ctx, page = _open(browser, live_server, 1440, 900)
    try:
        page.focus("#pred-rows-up tr[tabindex='0']")
        focused = page.evaluate("""() => {
            const a = document.activeElement;
            if (!a || !a.matches || !a.matches('#pred-rows-up tr[tabindex="0"]')) return null;
            const cs = getComputedStyle(a);
            return { tag: a.tagName, outline: cs.outlineStyle, width: cs.outlineWidth,
                     row: a.getAttribute('data-rank') };
        }""")
        assert focused, "名单第一行拿不到焦点 ⇒ 键盘用户够不到这份交付物"
        assert focused["outline"] != "none" and focused["width"] not in ("0px", ""), (
            f"聚焦的那一行没有可见轮廓：{focused}")
    finally:
        ctx.close()


@pytest.mark.parametrize("name,width,height", _VIEWPORTS, ids=[v[0] for v in _VIEWPORTS])
def test_the_d2_readings_never_break_inside_a_word(browser, live_server, name, width, height):
    """★ D2 的主读数**不许在词中间折行**。

    🚨 2026-10-21 实测（390px）：「+10.4245%　缺口 9.5755pp」被拆成
       「…+10.4245%　缺」/「口 9.5755pp」两行 ⇒ **中文在词中间断了**，
       读者会把「缺」和「口」当成两件事。
    ⇒ 判据：**一行**（`getClientRects()` 只有一个行盒）。
       ⚠️ 断的是**渲染结果**，不是源码 ⇒ 只能在浏览器里量。
    """
    ctx, page = _open(browser, live_server, width, height)
    try:
        got = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('#d2-gap-calibers .d2c-num').forEach(el => {
                out.push({ text: el.innerText.replace(/\\s+/g, ' ').trim(),
                           lines: el.getClientRects().length });
            });
            return out;
        }""")
        assert got, "首屏那两个 D2 读数没渲染出来"
        for g in got:
            assert g["lines"] == 1, (
                f"「{g['text']}」被折成了 {g['lines']} 行 ⇒ 中文在词中间断了")
    finally:
        ctx.close()
