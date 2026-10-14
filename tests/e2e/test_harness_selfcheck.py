"""★ E2E 夹具的**自检** —— 不开浏览器，秒级跑完。

## 为什么需要这一组

今天栽在这里一次，而且是**最贵的一种栽法**：看门狗那条 E2E 超时了 90 秒，
我读成「前端的看门狗没生效」——于是一个根本不存在的故障，写进了一份
「复现过的缺陷」清单。

真因是**夹具的旋钮转不到那一档**：`hang_at=2` 的判断写在正文循环内部，
而 thinking 段默认先吐 3 条，进入正文循环时计数器已经 ≥3，
`hang_at=2` 永远匹配不上 ⇒ 模型**正常跑完** ⇒ 测试在等一个不会出现的 `.fail`。

⇒ 教训落成守卫：**夹具的每个旋钮都要有一条独立于被测系统的测试**。
它验证的是「我真的能造出那个故障」，而不是「我以为我能」。

这和仓库里已有的那条同源：
`test_chat_wiring.py::test_every_helper_extractor_rejects_a_header_that_is_absent`
—— 提取器要先被提取一次。
"""

from __future__ import annotations

import threading

import pytest

from conftest import FakeLLM


def _drain(gen, limit=50):
    out = []
    for _ in range(limit):
        try:
            out.append(next(gen))
        except StopIteration:
            break
    return out


def test_the_default_fake_emits_both_kinds_then_stops():
    llm = FakeLLM(delay=0)
    got = _drain(llm.chat_stream([], system=""))
    kinds = [k for k, _ in got]
    assert kinds.count("thinking") == 3
    assert kinds.count("text") == 6
    assert "".join(p for k, p in got if k == "text") == "段1段2段3段4段5段6"


def test_turning_off_thinking_really_turns_it_off():
    """对照臂的前提守卫：下面那条 E2E 靠 `thinking_deltas=0` 隔离出正文。"""
    got = _drain(FakeLLM(thinking_deltas=0, delay=0).chat_stream([], system=""))
    assert [k for k, _ in got] == ["text"] * 6


def test_fail_at_raises_after_exactly_that_many_deltas():
    """`fail_at=2` ⇒ 吐 2 条之后抛，**不是**第 2 条之前抛。

    差一位不会让测试变绿，只会让它测到一条**别的**故障路径。
    """
    llm = FakeLLM(fail_at=2, delay=0)
    gen = llm.chat_stream([], system="")
    assert next(gen) == ("thinking", "想1…")
    assert next(gen) == ("thinking", "想2…")
    with pytest.raises(RuntimeError):
        next(gen)


@pytest.mark.parametrize("hang_at", [1, 2, 4, 9])
def test_hang_at_really_hangs_after_exactly_that_many_deltas(hang_at):
    """★ 这条就是看门狗用例的地基。

    做法：在子线程里跑生成器，主线程超时即判定「它没挂住」。
    **不用 sleep 去猜它挂了没** —— 那正是今天栽跟头的地方：
    一个会「正常跑完」的夹具，配上一句 `time.sleep(5)` 式的等待，
    在测试里长得和「真的挂住了」一模一样。

    ⇒ 判据是「能不能在 0.4 秒内拿到第 hang_at+1 条」，与时间无关地精确。
    """
    got: list = []
    err: list = []

    def run():
        try:
            for item in FakeLLM(hang_at=hang_at, delay=0).chat_stream([], system=""):
                got.append(item)
        except Exception as exc:                       # noqa: BLE001
            err.append(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=0.4)

    # 先钉精确的：挂住**之前**吐了几条。这条与线程存活无关，是硬判据。
    assert len(got) == hang_at, f"挂住之前吐了 {len(got)} 条，期望 {hang_at} 条：{got}"
    assert not err, f"生成器抛了：{err}"
    # 再钉「它还挂着」：join 超时返回时线程仍活着，才说明真的挂住了。
    # ⚠️ 第一版我把这条写成了 `assert not t.is_alive()`，配的失败消息却写着
    # 「线程还活着，说明挂住了（这才是期望）」—— 自相矛盾，于是 4 条全红。
    # 这是今天第 7 次把失败方向读反。**断言与它的失败消息必须互相印证**，
    # 否则你读到消息会以为是自己写反了守卫，而不是守卫真的没守住。
    assert t.is_alive(), \
        f"hang_at={hang_at} 之后线程自己退出了 —— 旋钮没转到那一档，本条守卫在验一个不存在的故障"
