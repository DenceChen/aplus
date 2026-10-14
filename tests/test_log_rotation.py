"""`data/*.log` 的轮转（TDD）——launchd 落盘日志不能无限长。

## 在守什么

`rotate_logs` 原本只 `sink_dir.glob("*.jsonl")`（log.py:209），即只管
`data/logs/*.jsonl`。而本仓全部常驻进程由 launchd 托管，stdout/stderr 落在
`data/*.log`（plist 的 StandardOutPath / StandardErrorPath）
⇒ 这批文件**无人轮转**，只增不减。

`tests/test_audit_logging.py::test_rotate_logs_leaves_launchd_stdout_logs_untouched`
原先把这个缺陷**钉成红的**（断言老 .log 原地不动），本文件是它的续集。

## 两个设计决定（先写在这里，测试钉的是它们）

**① 截断，不删除。** 磁盘占用小（本仓实测 `data/*.log` 最大 **21 KB**，
`du -sh data/*.log` 全部加起来不到 100 KB）——**这不是紧急问题**。
而日志的价值恰恰在排查：**本仓铁律「不信退出码、要靠产物指纹」**，
2026-10-15 已因此误判过一次。把老日志删掉等于当场抹掉排查线索，
省下的那 20 KB 不值一提。所以：**清掉超期内容，留一条短尾巴 + 轮转标记**。

**② 判龄用 mtime，不用文件名。** `data/*.log` 的真实名字是
`run_daily.log` / `evolve_handover_run.log` / `web_agent.err.log`
——**一个日期都没有**。若沿用 jsonl 那套 `date.fromisoformat(f.stem)`，
每个文件都会抛 ValueError 然后 `continue`，**修了个寂寞**。
可用的唯一信号是 mtime（最后一次写入 = 最后一次还在被追加）。

> ⚠️ 由此产生一条**必须诚实写明的局限**：常驻进程的 .log 每天都在追加，
> 它的 mtime **永远是今天** ⇒ **mtime 轮转对「正在增长的日志」永远不会触发**。
> 真要封住磁盘增长得靠按大小轮转，不在本次范围内。
> 这里补的是**「已经不再被写的老日志」**这一段（实测 `data/evolve_handover_run.log`
> 的 mtime 停在 10-14，而 `data/` 下确有一批停更的老文件）。

## 判据预先声明

| 断言 | 钉住什么 |
|---|---|
| 超期 .log 内容被清、尾巴留下 | 轮转确实发生（不是空操作） |
| 未超期 .log **一字不动** | 不误伤 |
| 老 .log **文件仍在**（只被截断） | 删除优先于截断 ⇒ 变红 |
| 非 .log（`notes.txt`）不被碰 | `.log` 后缀判定被变异掉 ⇒ 变红 |
| 超期 jsonl 仍被删 | 原有行为不回退 |
| 跑两遍结果相同 | 幂等 |

## 变异验证（已做，3/3 全部变红）

(a) 天数判定恒假 → (b) `.log` 后缀判定去掉 → (c) `logs/*.jsonl` 分支删掉。
每一次都先 `grep` 确认改动真落进文件（哈希变了 + 标记串恰好 1 次），再看测试。
"""
import datetime
import os
from pathlib import Path

import pytest

from aplus.log import rotate_logs


TODAY = "2026-10-14"
OLD_TS = datetime.datetime(2020, 1, 1).timestamp()
FRESH_TS = datetime.datetime(2026, 10, 13).timestamp()   # keep_days=1 的窗口内


def _log(root: Path, name: str, text: str, ts: float = OLD_TS) -> Path:
    p = root / name
    p.write_text(text, encoding="utf-8")
    os.utime(p, (ts, ts))
    return p


def _run(root: Path):
    rotate_logs(root, keep_days=1, today=TODAY)


# ── ① 超期 .log 被截断，且留下尾巴 ──

def test_expired_log_content_is_truncated(tmp_path):
    """超期 .log 的超期内容必须被清掉——这是本次修的那件事本身。

    写 40 KB（> 任何合理的 tail 尺寸），断言**文件显著变小**。
    """
    p = _log(tmp_path, "run_daily.log", "A" * 40_000)
    before = p.stat().st_size

    _run(tmp_path)

    after = p.stat().st_size
    assert after < before, f"超期日志没被截断：{before} → {after}"


def test_expired_log_keeps_a_short_tail(tmp_path):
    """截断**不是抹除**：最后一段必须还在，且带轮转标记。

    这是与「直接删」的分水岭。排查看的是「它最后是怎么死的」，
    尾巴就是那句话。删除优先于截断 ⇒ 本条变红。
    """
    p = _log(tmp_path, "run_daily.log", "HEAD\n" + "B" * 40_000 + "\nTAIL_MARKER\n")

    _run(tmp_path)

    body = p.read_text(encoding="utf-8", errors="replace")
    assert "TAIL_MARKER" in body, "超期日志的尾部证据被抹掉了"
    assert "HEAD" not in body, "头部没清掉 ⇒ 没在截断"
    assert "rotate_logs" in body, "缺少轮转标记，过后没人知道这文件被清过"


def test_expired_log_file_is_not_deleted(tmp_path):
    """**截断而非删除**——文件本身必须还在。

    理由（本仓铁律「不信退出码、靠产物指纹」）：日志被删 = 排查线索被当场抹掉，
    而实测这些文件最大才 21 KB，省下的磁盘不值这条线索。
    """
    p = _log(tmp_path, "web_agent.err.log", "x" * 40_000)

    _run(tmp_path)

    assert p.exists(), "超期 .log 被删了——本仓约定是截断留证，不是删除"


# ── ② 未超期的一律保留（不误伤）──

def test_fresh_log_is_left_untouched(tmp_path):
    """窗口内的 .log **一个字节都不许动**。

    正控臂：证明上一条不是因为「.log 一律被处理」，而是判龄真的在工作。
    """
    text = "最近一次还在追加的日志\n" * 500
    p = _log(tmp_path, "run_daily.log", text, ts=FRESH_TS)
    before = p.stat().st_size

    _run(tmp_path)

    assert p.stat().st_size == before, "窗口内的日志被截断了"
    assert p.read_text(encoding="utf-8") == text, "窗口内的日志内容被改了"


def test_boundary_log_is_kept(tmp_path):
    """恰好等于 cutoff 的当天**保留**（判定是 `<`，不是 `<=`）。

    这是刻意的：保留期是「至少留 N 天」，等于 N 天的那天还在期内。
    """
    cutoff_ts = datetime.datetime(2026, 10, 13).timestamp()
    p = _log(tmp_path, "run_daily.log", "y" * 40_000, ts=cutoff_ts)

    _run(tmp_path)

    assert p.stat().st_size == 40_000, "cutoff 当天被截断了（`<` 被写成了 `<=`）"


def test_expired_log_smaller_than_tail_is_left_intact(tmp_path):
    """超期但**比尾巴还小**的 .log：一个字节都不动。

    这条是写实现时才发现的边界，也是 `tests/test_audit_logging.py` 里
    那个 fixture 一度失败的真实原因：轮转保留 8 KB 尾巴，
    清一个 100 字节的文件只会**白丢数据、换不来任何磁盘**。
    ⇒ 判据落在这条上，而不是靠 fixture 恰好够大蒙混过去。
    """
    text = "小日志\n" * 10                     # ~40 字节，远小于 LOG_TAIL_BYTES
    p = _log(tmp_path, "run_daily.log", text)

    _run(tmp_path)

    assert p.read_text(encoding="utf-8") == text, (
        "比尾巴还小的超期日志被动了 ⇒ 白丢数据、零磁盘收益"
    )


# ── ③ `.log` 后缀判定：不是任意顶层文件都该被清 ──

def test_non_log_top_level_file_is_untouched(tmp_path):
    """`data/notes.txt` 这种**非 .log** 的顶层文件不在管辖内。

    变异 (b) 把 `.log` 后缀判定去掉后，本条变红。
    """
    p = _log(tmp_path, "notes.txt", "z" * 40_000)

    _run(tmp_path)

    assert p.stat().st_size == 40_000, "非 .log 的顶层文件被截断了 ⇒ 后缀判定失效"


def test_logs_subdir_is_not_swept_by_the_log_pass(tmp_path):
    """`data/logs/` 里的文件归 jsonl 那条分支管，**不被 .log 那条重复扫**。

    `logs/vintage_2026-10-14.log` 是既有非缺陷登记里的样本：
    非日期命名 ⇒ ValueError ⇒ 原样保留。这里钉住它不会被 .log 分支误伤。
    """
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    p = logs / "vintage_2026-10-14.log"
    p.write_text("q" * 40_000, encoding="utf-8")
    os.utime(p, (OLD_TS, OLD_TS))

    _run(tmp_path)

    assert p.stat().st_size == 40_000, "logs/ 下的文件被 .log 分支误扫了"


# ── ④ 原有行为不回退（回归守卫）──

def test_expired_jsonl_is_still_deleted(tmp_path):
    """`logs/*.jsonl` 的原有行为：**超期照删**。

    变异 (c) 删掉 jsonl 分支 ⇒ 本条变红。
    """
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    old = logs / "2020-01-01.jsonl"
    old.write_text('{"a":1}', encoding="utf-8")

    _run(tmp_path)

    assert not old.exists(), "超期 jsonl 没被删 ⇒ jsonl 分支回退了"


def test_fresh_jsonl_is_kept(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    keep = logs / "2026-10-13.jsonl"
    keep.write_text('{"a":1}', encoding="utf-8")

    _run(tmp_path)

    assert keep.exists(), "窗口内的 jsonl 被误删"


def test_jsonl_and_log_both_handled_in_one_pass(tmp_path):
    """一轮里两类文件各按各的规矩处理，互不干扰。"""
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / "2020-01-01.jsonl").write_text('{"a":1}', encoding="utf-8")
    lg = _log(tmp_path, "run_daily.log", "w" * 40_000)

    _run(tmp_path)

    assert not (logs / "2020-01-01.jsonl").exists(), "jsonl 该删"
    assert lg.exists() and lg.stat().st_size < 40_000, ".log 该截断且该留着"


# ── ⑤ 幂等 + 坏文件不拖垮整轮 ──

def test_rotation_is_idempotent(tmp_path):
    """跑两遍结果必须一致——轮转会被 cron 反复调。

    第二遍不动它，是因为第一遍已经写过文件、mtime 变成今天 ⇒ 自然落在窗口内。
    """
    _log(tmp_path, "run_daily.log", "e" * 40_000)

    _run(tmp_path)
    once = (tmp_path / "run_daily.log").read_text(encoding="utf-8", errors="replace")
    size_once = (tmp_path / "run_daily.log").stat().st_size

    _run(tmp_path)
    twice = (tmp_path / "run_daily.log").read_text(encoding="utf-8", errors="replace")

    assert size_once < 40_000, "第一遍就没截断 ⇒ 后面两条都是空转"
    assert once == twice, "第二遍又动了 ⇒ 不幂等"


def test_one_unreadable_file_does_not_abort_the_round(tmp_path):
    """单个文件坏掉不得让整轮失败——与既有 `except ValueError: continue` 同族。

    造一个**点不进去的目录**冒充 .log：`root/"broken.log"` 是目录，
    stat 得得到、但截断时会 IsADirectoryError。后面那个好文件必须照样被处理。
    """
    (tmp_path / "broken.log").mkdir()          # 目录冒充日志文件
    good = _log(tmp_path, "run_daily.log", "g" * 40_000)

    _run(tmp_path)                             # 不抛异常即通过

    assert good.stat().st_size < 40_000, (
        "一个坏文件就让整轮中断了 ⇒ 轮转不是幂等健壮的"
    )


def test_missing_dirs_are_not_an_error(tmp_path):
    """`logs/` 与 data_root 都不存在时安静返回（既有语义）。"""
    empty = tmp_path / "nothing"
    empty.mkdir()
    rotate_logs(empty, keep_days=1, today=TODAY)   # 不抛异常即通过


def test_unparsable_jsonl_name_is_skipped_not_raised(tmp_path):
    """非日期命名的 jsonl ⇒ ValueError ⇒ 跳过（既有语义不许回退）。"""
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    other = logs / "vintage_2026-10-14.jsonl"
    other.write_text('{"a":1}', encoding="utf-8")

    _run(tmp_path)

    assert other.exists(), "解析不了文件名的 jsonl 被删了"
