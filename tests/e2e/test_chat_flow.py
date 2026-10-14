"""★ 第二组 E2E：追问这条链路**在故障时**的行为。

第一组验的是「正常时页面是活的」。这一组验的是**坏掉时用户看到什么** ——
而后者才是本项目最贵的那一类：`except: pass`、半截入库、按钮永久锁死，
全都属于「不报错，但用户从此没法用了」。

| E2E | 它能抓到、pytest 抓不到的 |
|---|---|
| `…thirteen_rapid_attacks_produce_one_turn` | 连点/回车把一次提问变成 N 条消息（要读**数据库**才算证据） |
| `…send_button_is_released_after_a_mid_stream_failure` | `finally` 漏复位 ⇒ 按钮**永久锁死**而页面零报错 |
| `…a_mid_stream_failure_is_shown_not_swallowed` | 错误被 `catch` 后静默丢弃 ⇒ 用户只看到半句话 |
| `…a_half_stream_is_never_persisted_to_the_db` | 半截 assistant 入库 ⇒ 刷新后半句话**永久**留在历史里 |

## 为什么「按钮锁死」要对着真服务器验

它有一个特别恶劣的性质：**它不是错，它是对的**。
`try { … } catch { /* 忽略 */ }` 之后不写 `finally`，在单元测试里看不出任何异常，
代码读起来也完全正常。只有当**流真的半路死掉**（真实故障，今天实测过 RST）时，
它才把发送框锁到刷新为止。

而单元测试很难造出这个状态：mock 掉的 `requests` 抛异常是**同步**的，
异常照样会走进 `finally`。**真实 socket 的半死不会 resolve 也不会 reject。**
"""

from __future__ import annotations

import os
import sqlite3

import pytest


def _open_chat(page, url):
    page.goto(url, wait_until="domcontentloaded")
    page.click("#tab-chat")
    page.wait_for_selector("#panel-chat:not([hidden])", timeout=15_000)


def _messages(db_root):
    with sqlite3.connect(db_root / "chat.db") as conn:
        return list(conn.execute("SELECT conv_id, role, content FROM messages ORDER BY id"))


# ── ① 连点闸：对着真服务器打 13 次 ──────────────────────────────────

def test_thirteen_rapid_attacks_produce_exactly_one_turn(page, probe, fake_llm,
                                                         live_server, e2e_data_root):
    """★ 13 次攻击（10 次点按钮 + 3 次回车）⇒ 服务端只该看到**一次**提问。

    为什么必须读数据库才算证据：页面上的 DOM 可能在防连点之后就不再更新，
    于是「界面上只有一条」并不能证明「服务端只收到一条」——
    多余的那几条可能已经写进库里，只是没显示。
    本仓今天的教训正相反：conv 10 看起来像有 6 条消息，
    **时间线证明那是三次相隔 13 分钟的独立测试**，不是重复发送。
    ⇒ 判据 = 网络请求数 + 模型调用数 + 库里的行数，三处对齐。
    """
    _open_chat(page, live_server)
    page.fill("#chat-text", "明天涨哪些")

    posted: list[str] = []
    page.on("request", lambda r: posted.append(r.url)
            if "/api/chat/stream" in r.url else None)

    # force=True：按钮在第一次点击后就被 disable 了，而**这正是要测的攻击**。
    # 不 force 的话 playwright 会等按钮重新可点，攻击根本发不出去，
    # 测试变成一个永远为真的摆设。
    for _ in range(10):
        page.click("#send", force=True)
    page.click("#chat-text", force=True)
    for _ in range(3):
        page.keyboard.press("Enter")

    page.wait_for_function(
        """() => {
             const t = document.getElementById('msgs');
             return t && t.textContent.includes('段6');
           }""",
        timeout=20_000,
    )
    page.wait_for_function("() => !document.getElementById('send').disabled",
                           timeout=20_000)

    assert len(posted) == 1, f"前端发了 {len(posted)} 次 /api/chat/stream：{posted}"
    assert len(fake_llm.calls) == 1, f"模型被调了 {len(fake_llm.calls)} 次"

    rows = _messages(e2e_data_root)
    roles = [r for _c, r, _t in rows]
    assert roles == ["user", "assistant"], \
        f"库里的角色序列是 {roles} —— 一次提问应当正好是一问一答两行"
    assert rows[0][2] == "明天涨哪些"
    assert rows[1][2] == "段1段2段3段4段5段6"

    console_errors, page_errors, _failed = probe.healthy()
    assert not page_errors, f"有未捕获异常：{page_errors}"
    assert not console_errors, f"console 里有 error：{console_errors}"


# ── ② inFlight 闸：它是承重的那一道，前一条够不着 ──────────────────

def test_a_new_question_typed_mid_flight_still_produces_one_turn(page, fake_llm,
                                                                  live_server, e2e_data_root):
    """★ 这条存在的理由是**变异验证发现的洞**，不是我推想出来的。

    把 ① 那条跑一遍还不够：上面那条 13 连点的守卫，把 `inFlight` 整行删掉之后
    **依然全绿**。原因是 `send()` 开头同步做了 `box.value = ''`，
    于是第二次点击进来时文本框已空，被 `if (!text) return` 提前拦下 ——
    **`inFlight` 从来不是那条路径上的承重墙**。

    它什么时候才是承重的？当**输入框在飞行途中非空**的时候：
    - 将来有人为了「让用户能趁回答时先打下一句」而放开 `readOnly`；
    - 或者粘贴/自动填充绕过 `readOnly`（它是 UI 层防护，不是安全边界）。

    那两条都是很可能发生的改动，而改动之后**没有任何测试**会发现防连点已经失效。
    ⇒ 这里直接构造那个状态：飞行途中把新问题塞回输入框，再按回车。
    """
    _open_chat(page, live_server)
    page.fill("#chat-text", "明天涨哪些")
    page.click("#send")

    # 等到**已经开始吐**（此刻请求确实在飞），而不是刚点完就塞 ——
    # 塞早了 send() 还没跑到清空那一步，测的就不是「飞行中」了。
    page.wait_for_function(
        """() => {
             const t = document.getElementById('msgs');
             return t && /段[1-3]/.test(t.textContent);
           }""",
        timeout=20_000,
    )

    page.evaluate(
        """() => {
             const box = document.getElementById('chat-text');
             box.readOnly = false;          // 模拟「readOnly 被放开 / 粘贴漏过去」
             box.value = '换个问题';
           }"""
    )
    page.keyboard.press("Enter")

    page.wait_for_function(
        """() => {
             const t = document.getElementById('msgs');
             return t && t.textContent.includes('段6');
           }""",
        timeout=20_000,
    )
    page.wait_for_function("() => !document.getElementById('send').disabled",
                           timeout=20_000)

    assert len(fake_llm.calls) == 1, \
        f"飞行途中塞进去的新问题被发出去了，模型被调 {len(fake_llm.calls)} 次 —— inFlight 闸失效"
    rows = _messages(e2e_data_root)
    assert [r for _c, r, _t in rows] == ["user", "assistant"], \
        f"库里是 {rows} —— 飞行途中塞进去的第二个问题也入库了"


# ── ③ 断流：按钮必须解锁，错误必须可见，半截不入库 ────────────────

def test_the_send_button_is_released_after_a_model_failure(page, probe, server_for,
                                                           e2e_data_root):
    """★ 模型在第 3 个增量处抛异常（`fail_at=2`）。

    守的是 `send()` 的 `finally`：`inFlight = false` + `setBusy(false)`。
    漏了任何一个的症状都不是「报错」，而是——
    **「发送按钮从此点不动了」**，且页面上没有任何错误提示。

    ★ 这条只有对着真服务器才验得出来：mock 掉的 HTTP 层抛异常会**同步**
    走进 `finally`，于是漏写 `finally` 在单元测试里完全看不出来。

    同时守「断流不入库」：半截 assistant 落库 ⇒ 刷新页面后这半句话
    **永久**留在历史里，且此后每轮都带着它进 history 继续算。
    """
    with server_for(fail_at=2, text_deltas=6, delay=0.10) as url:
        _open_chat(page, url)
        page.fill("#chat-text", "明天涨哪些")
        page.click("#send")

        page.wait_for_selector(".turn.a.fail", timeout=20_000)
        page.wait_for_function("() => !document.getElementById('send').disabled",
                               timeout=20_000)

        assert page.locator("#send").is_enabled(), \
            "失败之后发送按钮仍是禁用的 —— finally 漏了复位，用户从此发不出第二条"
        assert page.inner_text("#send") == "发送", \
            f"按钮文案没复位：{page.inner_text('#send')!r}"
        assert page.locator("#chat-text").get_attribute("readonly") is None, \
            "输入框仍是只读 —— setBusy(false) 没跑到"

        rows = _messages(e2e_data_root)
        assert [r for _c, r, _t in rows] == ["user"], \
            f"失败之后库里还有 {rows} —— 半截 assistant 入库了"

        console_errors, page_errors, _failed = probe.healthy()
        assert not page_errors, f"有未捕获异常：{page_errors}"


def test_a_model_side_failure_is_labelled_as_a_model_problem(page, server_for):
    """★ 归因要说对层。

    ★ 这条第一版我写反了，把「正确」读成了「缺陷」——
    我期望提示里出现「连接中断」，实际拿到的是「这次没答出来　模型连接失败：…」，
    于是断言变红。查下去发现：**代码是对的，判据是错的**。
    模型在流里抛异常 ⇒ 后端按设计把它变成 `error` 事件（webapp.py:317 的契约：
    「一切错误都走 error 事件」）⇒ 前端把 `error` 事件映射成 `label='model'`。
    「这次没答出来」是**唯一正确**的说法：用户该去查上游，不是查网线。

    今天已经栽在这上面 5 次（第 6 次），所以把它连同理由一起写进断言的 docstring ——
    让下一个想改它的人先读到为什么。
    """
    with server_for(fail_at=2, text_deltas=6, delay=0.10) as url:
        _open_chat(page, url)
        page.fill("#chat-text", "明天涨哪些")
        page.click("#send")

        page.wait_for_selector(".turn.a.fail", timeout=20_000)
        fault = page.inner_text(".turn.a.fail .fault")

    assert fault.strip(), "失败了但 .fault 是空的 —— 用户看到的是半句话加一片空白"
    assert "这次没答出来" in fault, \
        f"模型侧的失败被说成了 {fault!r}，用户会去查错的地方"
    assert "可以直接重发一次" in fault, "没告诉用户下一步该做什么"


@pytest.mark.slow
def test_the_watchdog_frees_the_ui_when_upstream_goes_silent(page, server_for):
    """★ 全套件里最贵、也最该有的一条。

    `hang_at=2` 让上游**静默**：连接还活着，字节一个都不吐。
    这是今天实测到的真实故障（RST 前后、或上游卡死），
    它的恶劣之处在于**它不抛异常**：浏览器里的 `reader.read()`
    既不 resolve 也不 reject，于是 `finally` 永远不跑。

    没有看门狗的症状：页面**零报错**，只有「发送按钮从此点不动」，
    看起来像「按钮坏了」。

    ★ 为什么这条要等 45 秒：`IDLE_ABORT_MS = 45000`（`index.html:1469`）写死在
    前端常量里，我不愿为了测试速度去改产品代码。慢就慢 ——
    它换来的是「按钮永久锁死」这个故障第一次有了会红的守卫。
    需要跳过时：`APLUS_E2E_SKIP_SLOW=1 pytest …`。
    """
    if os.environ.get("APLUS_E2E_SKIP_SLOW"):
        pytest.skip("APLUS_E2E_SKIP_SLOW 已设置，跳过 45 秒级的看门狗用例")

    with server_for(hang_at=2, text_deltas=6, delay=0.10) as url:
        _open_chat(page, url)
        page.fill("#chat-text", "明天涨哪些")
        page.click("#send")

        # 静默 45 秒后前端主动 abort ⇒ 走 label='link' ⇒ 「连接中断」
        page.wait_for_selector(".turn.a.fail", timeout=90_000)
        page.wait_for_function("() => !document.getElementById('send').disabled",
                               timeout=20_000)

        fault = page.inner_text(".turn.a.fail .fault")

    assert page.locator("#send").is_enabled(), \
        "上游静默后按钮永久锁死 —— 看门狗没生效，用户再也没法发第二条"
    assert "连接中断" in fault, \
        f"上游静默被说成了 {fault!r}。这是**网络层**的事，用户会去查模型/上游"
    assert "秒没有新内容" in fault, \
        f"提示里没有说清是超时，拿到 {fault!r} —— 用户不知道发生了什么、也不知道等多久"
