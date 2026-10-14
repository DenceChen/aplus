"""★ 第三组 E2E：视口与页签 —— 面向**用户拿到手的那台设备**。

前两组验的是「功能对不对」。这一组验的是「在 iPad 上还能不能用」，
而这一类故障有个特别难受的性质：**在开发机上永远是好的**。
桌面 1440px 下排得整整齐齐的表格，在 820px 上会把整页撑出横向滚动条，
而没有任何测试会红、没有任何 console 报错、pyright 也不会提示。

## 判据为什么不是「有没有 overflow-x: auto」

因为用户不看 CSS，只看**能不能用**。
横向溢出本身不是罪（宽表格允许横向滚动）；罪的是
**整个页面**被撑出横向滚动条 —— 也就是连侧栏、连页签条都要左右拖。

⇒ 判据钉在 `document.documentElement.scrollWidth` 与 `clientWidth` 的关系上，
   这是「用户能不能正常用」的判据，而不是「作者用了什么 CSS 属性」的判据。
"""

from __future__ import annotations

import pytest

from conftest import PageProbe  # noqa: F401  — 让读者知道收集器是谁挂的

# iPad 的真实宽度：竖屏 820（12.9"）与横屏 1024（12.9"）。
# 加上一个窄下限 768（小 iPad 竖屏），这三档覆盖了绝大多数真实持有者。
_VIEWPORTS = [
    ("ipad-portrait", 820, 1180),
    ("ipad-landscape", 1024, 1366),
    ("ipad-mini-portrait", 768, 1024),
]

_TABS = [
    ("#tab-overview", "#panel-overview"),
    ("#tab-chat", "#panel-chat"),
    ("#tab-history", "#panel-history"),
    ("#tab-backtest", "#panel-backtest"),
    ("#tab-evolution", "#panel-evolution"),
    ("#tab-bond", "#panel-bond"),
    ("#tab-logs", "#panel-logs"),
]


@pytest.mark.parametrize("name,width,height", _VIEWPORTS, ids=[v[0] for v in _VIEWPORTS])
def test_the_whole_page_does_not_scroll_sideways_on_an_ipad(page, probe, live_server,
                                                             name, width, height):
    """★ iPad 上**整页**不得出现横向滚动。

    症状：竖着拿 iPad 时，页面左右晃，连顶部页签条都要拖着走，
    聊天输入框被推到屏幕外——而这条界面上没有任何报错。
    """
    page.set_viewport_size({"width": width, "height": height})
    page.goto(live_server, wait_until="domcontentloaded")
    page.wait_for_function(
        "() => { const e = document.getElementById('llm-info');"
        "        return e && !e.textContent.includes('读取模型信息'); }",
        timeout=30_000,
    )
    page.wait_for_load_state("networkidle", timeout=30_000)

    over = page.evaluate(
        """() => {
             const d = document.documentElement;
             return { scroll: d.scrollWidth, client: d.clientWidth,
                      worst: null };
           }"""
    )
    # 留 2px 容差：亚像素缩放在真机上会产生 1px 的抖动，
    # 卡死在 0 容差上会造出一条会假红的守卫 —— 而假红的守卫所有人都会忽略。
    assert over["scroll"] <= over["client"] + 2, (
        f"{name}（{width}×{height}）：整页横向溢出 {over['scroll'] - over['client']}px —— "
        f"scrollWidth={over['scroll']} clientWidth={over['client']}。"
        "竖屏 iPad 上用户会被迫左右拖整页。"
    )

    console_errors, page_errors, _failed = probe.healthy()
    assert not page_errors, f"{name} 下有未捕获异常：{page_errors}"


@pytest.mark.parametrize("tab,panel", _TABS, ids=[t[0] for t in _TABS])
def test_every_tab_opens_and_fetches_without_error(page, probe, live_server, tab, panel):
    """★ 七个页签逐个点开，每个都必须**拿到自己的数据**且不报错。

    为什么逐个点而不是一次全点：多数页签是 `hidden`，不点开就不会发请求，
    于是它们的加载错误**永远不会发生**。今天的静态测试里，
    `panel-bond` / `panel-logs` / `panel-evolution` 的数据加载路径
    只有在点开之后才第一次被执行。

    判据是「面板可见 **且** 本次点击没产生新的 console error / 失败请求」，
    后半句是关键：只判可见的话，一个显示着「加载失败」的空面板也算过。
    """
    page.goto(live_server, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=30_000)

    before_console = len(probe.console_errors)
    before_failed = len(probe.failed_requests)

    page.click(tab)
    page.wait_for_selector(f"{panel}:not([hidden])", timeout=15_000)

    console_errors, page_errors, failed = probe.healthy()
    new_console = console_errors[before_console:]
    new_failed = [f for f in failed if "favicon" not in f][before_failed:]

    assert not new_console, f"{tab} 打开后产生了 console error：{new_console}"
    assert not page_errors, f"{tab} 打开后有未捕获异常：{page_errors}"
    assert not new_failed, f"{tab} 自己的数据请求失败了：{new_failed}"
