"""★ iPad 上图表 tooltip 必须能出读数 —— **用真的触摸事件驱动**。

## 这条测试存在的理由

2026-10-14 的前端审计发现：`lineChart` / `dualLineChart` 的 tooltip
只绑了 `mousemove` / `mouseleave`，全文件 `touchstart` / `pointermove`
出现 **0 次**。iPad 是本项目的硬需求，而这两张图（`tip-hit` 命中率、
`tip-exc` 累计超额）是回测结论**唯一的细节载体**（表格只给月度汇总）。

症状：**在 iPad 上把手指按在曲线上，什么都不出现。**

## 为什么已有的 iPad E2E 盖不住

`test_layout.py` 的三条 iPad 用例断言「整页不横向滚动」，
用的是 `set_viewport_size` —— **只改视口，不改输入设备**。
Chrome 在没有 `has_touch` 的 context 里，`page.mouse.move()` 照样触发
`mousemove`，所以鼠标路径是通的，那三条全绿。

> ★ **把视口调成 iPad 的尺寸，不等于让它成为 iPad。**
> 触摸能力由 context 的 `has_touch` / `is_mobile` 决定，与宽高无关。
> 于是「我按 iPad 尺寸测过了」可以与「触摸完全没测过」同时为真。

所以这里开一个**真的触摸 context**，并派发**真的触摸事件**。

## 断言的是「读数真的出现」，不是「事件被绑上了」

绑定检查属于单元测试（`tests/test_audit_frontend.py` 已覆盖）。
这里要证明端到端：手指落下 → tooltip 可见 → **里面有数字**。
中间任何一环断了（脚本没加载、渲染没跑、坐标算错、命中区间为空）都会红。
"""

from __future__ import annotations

import pytest

_IPAD = {"width": 1024, "height": 1366}
_TIPS = ["#tip-hit", "#tip-exc"]


@pytest.fixture()
def touch_page(browser):
    """★ 一个**真的** iPad 触摸 context —— `has_touch=True` 且 `is_mobile=True`。

    这两个开关是触摸事件能否被合成为 pointer 事件的**唯一**决定因素；
    只设 viewport 尺寸的话，Chrome 仍然只发鼠标事件。
    """
    ctx = browser.new_context(viewport=_IPAD, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()


def _goto_backtest(page, live_server):
    """进回测页，等两张图**真的画出来**。

    ⚠️ 绝不用 `sleep` 后「看一眼」—— 本仓最贵的一课是把「等慢」误读成「等坏」。
    一律轮询一个明确的完成条件。
    """
    page.goto(live_server, wait_until="domcontentloaded")
    page.click("#tab-backtest")
    # ⚠️ 等的是 **svg 里画出了图元**，不是 tooltip 本身 ——
    #   `.tooltip` 的初始样式就是 `display:none`（藏到指针进入才显形），
    #   拿它当「页面准备好了」的信号会一直等到超时。
    page.wait_for_function(
        "() => ['#tip-hit','#tip-exc'].every(s => {"
        "   const svg = document.querySelector(s)?.closest('.chart-box')?.querySelector('svg');"
        "   return !!svg && svg.querySelectorAll('path,polyline,line').length > 0; })",
        timeout=25000,
    )
    return page


def _touch_hold(page, x, y, dx=2):
    """把手指**按在** (x, y) 上并轻微移动 —— **保持按下状态**。

    ★ 为什么不能「tap 完再看」：触摸没有悬停状态。实测事件序列是

        pointerdown, touchstart, pointermove, pointerup, pointerleave, touchend

    `pointerleave` 在 `pointerup` 之后**立刻**触发并把 tooltip 收走，
    所以在 `touchEnd` 之后再断言，看到的永远是「已隐藏」——
    **那测的是抬手之后的状态，而用户看到读数是在手指按着的时候。**
    这个坑我第一版就踩了：断言写在 tap 之后，全绿的唯一原因是它压根没测到。

    ★ 另一个坑：`tap` 的事件序列里**没有 `pointermove`**
    （单点点击不产生移动）。只发 down+up，绑定在 move 上的处理器
    永远不会被调用 —— 看起来像「触摸路径没生效」，其实是没发移动。

    手指按下时轻微横移 2px，确保 `pointermove` 真的被合成出来。
    """
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent",
             {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    # 手指按住并轻微移动 —— 这一步才产生 pointermove
    cdp.send("Input.dispatchTouchEvent",
             {"type": "touchMove", "touchPoints": [{"x": x + dx, "y": y}]})


@pytest.mark.parametrize("tip_sel", _TIPS, ids=lambda s: s.lstrip("#"))
def test_a_touch_on_the_curve_produces_a_visible_reading(touch_page, live_server, tip_sel):
    """手指按在曲线上 ⇒ tooltip 可见**且含数字**。"""
    page = _goto_backtest(touch_page, live_server)

    box = page.evaluate(
        """(sel) => {
             const svg = document.querySelector(sel).closest('.chart-box').querySelector('svg');
             const r = svg.getBoundingClientRect();
             return {x: r.x, y: r.y, w: r.width, h: r.height};
           }""",
        tip_sel,
    )
    # 横向中段：避开左右两端，端点附近的坐标换算最容易越界
    x = box["x"] + box["w"] * 0.5
    y = box["y"] + box["h"] * 0.5
    _touch_hold(page, x, y)

    # ★ 在**手指仍然按着**的时候断言 —— 抬手后 tooltip 会被 pointerleave 收走。
    page.wait_for_function(
        """(sel) => { const t = document.querySelector(sel);
                     return !!t && getComputedStyle(t).display !== 'none'
                            && t.textContent.trim().length > 0; }""",
        arg=tip_sel, timeout=10000,
    )
    text = page.evaluate("(sel) => document.querySelector(sel).textContent.trim()", tip_sel)
    assert any(ch.isdigit() for ch in text), (
        f"{tip_sel} 可见但里面没有数字：{text!r} ⇒ "
        "手指落到了曲线上，但坐标换算没命中任何数据点"
    )
    # 抬手后 tooltip 必须收起 —— 触摸没有悬停，留着就是永久遮挡
    page.touchscreen.tap(x, y)
    page.wait_for_function(
        "(sel) => getComputedStyle(document.querySelector(sel)).display === 'none'",
        arg=tip_sel, timeout=5000,
    )


def test_the_charts_do_not_overflow_an_ipad_viewport(page, live_server):
    """iPad 视口下两张图不横向溢出。

    这条测的是**尺寸**，上面那条测的是**触摸** ——
    两者是不同的失败模式：前者是布局，后者是输入设备。都要有。
    """
    page.set_viewport_size(_IPAD)
    page.goto(live_server, wait_until="domcontentloaded")
    page.click("#tab-backtest")
    # 等 svg 画出来；tooltip 本身初始是 display:none，不能当就绪信号。
    page.wait_for_function(
        "() => { const svg = document.querySelector('#tip-hit')"
        "?.closest('.chart-box')?.querySelector('svg');"
        " return !!svg && svg.querySelectorAll('path,polyline,line').length > 0; }",
        timeout=25000,
    )
    overflow = page.evaluate(
        """() => Math.max(...['#tip-hit','#tip-exc'].map(sel => {
               const svg = document.querySelector(sel).closest('.chart-box').querySelector('svg');
               return svg.getBoundingClientRect().right - window.innerWidth;
           }))"""
    )
    assert overflow <= 1, f"图表右侧溢出视口 {overflow:.1f}px ⇒ iPad 上看不到当天读数"
