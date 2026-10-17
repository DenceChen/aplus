"""`steps[name] == "ok"` 必须真的意味着「这一步把欠的交付物交了」。

## 事故形状（P0，2026-10-17 发现）

`data/guard.log` 里 2026-10-13 / 10-14 各留了一条活证据：

    [predict] 全市场回填进行中（bootstrap.lock），跳过预测——数据文件正在改写…

而那两天的 `run_status.json` 记的是 `status="ok"`。链条四环全部在位：

| 环 | 位置 | 行为 |
|---|---|---|
| 1 | `run_daily.py::step_predict_and_allocate` 锁判据 | 裸 `return`，**没出名单** |
| 2 | `run_daily.py::run_step` | `fn()` 正常返回 ⇒ `steps[name] = "ok"` |
| 3 | `run_daily.py::_pipeline` 归约 | `all(v == "ok")` ⇒ `status = "ok"` |
| 4 | `guard_daily.py:37` | 唯一判据是 `status == "ok"` ⇒ 打「已成功执行，无需补跑」 |

⇒ **D1 的交付物（当日 10 涨 10 跌名单）一个字节都没落盘，
日更守卫却认为它成功了。**

⚠️ **诚实标注——这是「地雷已埋」，不是「事故已发生」**：
`grep -c '跳过预测' data/guard.log` = 2 ⇒ 跳过路径**真的触发过 2 次**；
但 `pred_2026-10-09..10-16` 全部存在 ⇒ **D1 迄今没有真的丢单**。
那两天守卫之所以补跑，是因为 `status.date` 陈旧，**不是被这个 ok 骗过**。

## 为什么这不是「改判据」（第 91 层那条纪律）

1. **`skipped` 早就是本仓既有词汇**：`run_daily.py::run_day_gate` 对非交易日
   写的就是 `"status": "skipped"`。本测试用的是同一个词，不是新造的。
2. **`guard_daily.py:37` 的判据一个字都不改**，它只是收到了不同的值
   ⇒ 下面 `test_guard_daily_never_reads_steps` 把这条缝钉死。
3. 改的是**一个在说谎的字段**：`status="ok"` 声称「所有步骤都成功」，
   而预测一步没成功。**不是门槛、不是判据臂、不是样本窗口。**

## 为什么要有「合法空转」这一档

裸 `return` 有两种含义，不能一律判成「跳过」：

- **`step_verify_yesterday` 无待判定预测日** / **`step_bond_paper` 等待首个交易日**
  ⇒ 这一步**把它该做的事做完了**（确认过没东西可做），`ok` 是真的。
- **锁被占而没出名单 / 库为空而没写摘要** ⇒ **欠的交付物没交**，`ok` 是假的。

判据是「交付物有没有落」，不是「函数有没有正常返回」。
下面 `test_legitimate_idle_verify_stays_ok` 把前者钉成 `ok`，
免得下一个读者把这两处也一并「修」掉。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import run_daily as rd  # noqa: E402


class _FakeStore:
    def __init__(self, root):
        self.root = root


def _call_predict(tmp_path):
    """调真实调用点，把结果/异常统一成一个「结果对象」。"""
    try:
        out = rd.step_predict_and_allocate(
            _FakeStore(tmp_path), tmp_path, "2026-10-12", 50000, {})
    except Exception as exc:  # noqa: BLE001
        return exc
    return out


# ── 锚点：这条在修复前必须**因为它自己的断言**而红 ──────────────────────────

def test_predict_lock_busy_is_not_reported_as_ok(tmp_path, capsys):
    """锁被占 ⇒ 预测**没出名单** ⇒ 绝不能被记成 `ok`。

    这是本文件存在的全部理由。修复前 `step_predict_and_allocate` 裸 `return`
    ⇒ 结果是 `None` ⇒ `run_step` 记 `"ok"` ⇒ 上面那张表的四环闭合。
    """
    (tmp_path / "bootstrap.lock").write_text("999999")

    got = _call_predict(tmp_path)

    assert got is not None, (
        "bootstrap.lock 被占而裸 return ⇒ 被 run_step 记成 ok ⇒ "
        "status 归约成 'ok' ⇒ 守卫打「已成功执行，无需补跑」，"
        "而 D1 当日名单一个字节都没落盘")
    assert "回填" in capsys.readouterr().out, "跳过必须说明原因，不能静默"


# ── 对照臂：没有这条，上面那条可以靠「无论有没有锁都失败」来恒绿 ────────────
#
# 🚨 这一条改过一次。原版跑的是整条预测链（`_call_predict`），而它的夹具是
# 「无锁 + 空池」——**两组构造成员差了两样**（锁、池子），实测打出的
# `[predict] 池子 0 只 → 涨 0 / 跌 0` 同样意味着「没出名单」。
# ⇒ 红了之后无法判断是判据错还是夹具错，而唯一能写的修法有两种：
#    改判据方向去迁就夹具（🚫 第 112 层），或把判别力收窄到单一维度。
# 取后者：直接测 `predict_block_reason` 这个纯函数，
# 两组的**构造成员不同（锁）、条目数相同**（各调一次、断言一条）。
#
# 这正是 `a-ratio-whose-two-halves-count-different-things` 那条教训的形状：
# 两半必须数同一个东西，同批条目重排才会暴露判据没在守。

def test_predict_block_reason_is_two_valued_on_lock_membership(tmp_path):
    """判别力只来自「锁在不在」——这是本文件唯一合法的对照组形状。

    锁不在场 ⇒ `None`（不阻断）；锁在场 ⇒ 含「回填」的原因串（阻断）。
    ⚠️ **不许**在这里顺带断言「池子空 / 数据缺失也不阻断」：
    那些格子各有各的语义（池子建不起来 ≠ 此刻数据不可信），
    混进来就又变成两组差多维度的构造了。
    """
    assert rd.predict_block_reason(tmp_path) is None, (
        "锁不在场却判成阻断 ⇒ 判据在守别的东西（对照臂必须绿）")

    (tmp_path / "bootstrap.lock").write_text("999999")

    reason = rd.predict_block_reason(tmp_path)
    assert reason is not None, (
        "锁在场却没判成阻断 ⇒ 上面那条锚点测试靠别的原因恒绿")
    assert "回填" in reason, f"阻断必须说明原因，日志里不能是一行没有信息的话：{reason}"


def test_lock_busy_is_classified_as_skipped_not_failed(tmp_path):
    """锁占用是**条件不满足**，不是崩溃 ⇒ 记 `skipped:` 而不是 `failed:`。

    记成 `failed:` 会把一个良性且每日可见的事件报成 error 级告警
    （`run_step` 按分类选 `log.event` 的 level），这是错分类而非风格问题。
    """
    (tmp_path / "bootstrap.lock").write_text("999999")
    with pytest.raises(rd.StepSkipped) as ei:
        rd.step_predict_and_allocate(
            _FakeStore(tmp_path), tmp_path, "2026-10-12", 50000, {})
    assert rd.classify_step(ei.value).startswith("skipped:"), (
        "锁占用被记成 failed: ⇒ 良性事件被报成 error")


# ── 分类函数本身（抽成可 import 的纯函数，否则判决逻辑无任何东西能挡）───────

def test_classify_step_no_exception_is_ok():
    assert rd.classify_step(None) == "ok"


def test_classify_step_real_exception_is_failed():
    got = rd.classify_step(ValueError("炸了"))
    assert got.startswith("failed:"), got


def test_classify_step_skipped_is_neither_ok_nor_failed():
    got = rd.classify_step(rd.StepSkipped("库为空"))
    assert got != "ok", "跳过被并进 ok ⇒ 就是这版 P0"
    assert got.startswith("skipped:"), got
    assert "库为空" in got, "跳过必须带原因，否则日志里是一行没有信息的话"


def test_skipped_step_forces_run_status_partial():
    """`_pipeline` 的归约：遇 `skipped:` ⇒ `partial` ⇒ 守卫补跑。

    🚨 第一版这里**在测试文件里重写了一遍** `all(v == "ok" …)` ——
    于是它证明的是「那个表达式在这个文件里成立」，而生产那一份被改坏它照样绿。
    ⇒ 现在直接调 `rd.reduce_run_status`（生产自己用的那个纯函数）。
    """
    steps = {"update": "ok", "predict": rd.classify_step(rd.StepSkipped("锁占")), "stats": "ok"}
    assert rd.reduce_run_status(steps) == "partial", (
        "归约把 skipped 当成了 ok ⇒ 守卫不会补跑")


def test_all_ok_reduces_to_ok_and_the_guard_accepts_it():
    """正控：全绿仍必须是 `ok` —— 否则上面那条可以靠「恒判 partial」恒红/恒绿。

    没有这条，「归约恒返回 partial」会让上面那条**永远绿**，
    而真正被守的性质（跳过⇒partial、全绿⇒ok）一条都没被测。
    """
    assert rd.reduce_run_status({"update": "ok", "predict": "ok"}) == "ok"


def test_a_real_failure_also_forces_partial():
    """`failed:` 与 `skipped:` 必须**同一档**地逼出 `partial`。

    否则会出现「跳过⇒partial 但崩溃⇒ok」这种方向相反的写法，
    而它让守卫在**真正出事**的那天放行（守错了失效方向，第 136 层）。
    """
    steps = {"update": rd.classify_step(ValueError("炸了")), "predict": "ok"}
    assert rd.reduce_run_status(steps) == "partial"


# ── 「合法空转」必须仍然是 ok（判据是交付物，不是返回值）───────────────────

def test_legitimate_idle_verify_stays_ok(tmp_path, capsys):
    """无待判定的预测日 ⇒ verify **做完了它该做的事** ⇒ 不得判成 skipped。

    这条防止下一个读者把「一切裸 return 都改成 raise」。
    """
    (tmp_path / "reports").mkdir()
    assert _call_verify(tmp_path) is None, "夹具前提：确实无事可做"
    assert "无待判定的预测日" in capsys.readouterr().out


def _call_verify(tmp_path):
    try:
        return rd.step_verify_yesterday(_FakeStore(tmp_path), tmp_path, "2026-10-12")
    except Exception as exc:  # noqa: BLE001
        return exc


# ── 耦合守卫：本修复靠「守卫只看 status」这条缝，缝断了修复会静默失效 ────────

def test_guard_daily_never_reads_steps():
    """`guard_daily.py` 里**一次都不许出现** `steps`。

    它是本修复的接收面：判据只读 `date` + `status`，所以只要
    `status` 不再是 `"ok"`，19:30 的补跑就会自动发生。
    哪天有人改成读 `steps["predict"] == "ok"`，本文件全部测试仍然全绿
    而修复失效——所以这条守卫守的是**耦合本身**。
    """
    src = (ROOT / "scripts" / "guard_daily.py").read_text(encoding="utf-8")
    assert "steps" not in src, (
        "guard_daily 开始读 steps 了 ⇒ 与 classify_step 的三态分类脱钩，"
        "本文件的修复会静默失效，需同时改两处判据")
    assert 'status.get("status") == "ok"' in src, "夹具前提：守卫判据仍是这一条"
