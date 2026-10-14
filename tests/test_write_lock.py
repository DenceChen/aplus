"""写库互斥锁：同一时刻只许一个进程改主库。

## 为什么必须有

三个脚本都会写 `data/master/daily/*.parquet`：
`bootstrap_history` / `backfill_gaps` / `repair_tail_cols` / `daily_update`。
2026-10-12 补锁之前 `daily_update` 一点锁都没有——它一旦和回填撞上，
两个进程同时 concat + `to_parquet` 同一批文件，后写覆盖先写，
丢的列和行都不会有人知道。

baostock 本身也禁并发：两个会话同时拉会互相踢。

## 为什么用 `O_EXCL` 而不是 `exists()`

`if path.exists(): 退出` 是典型的 check-then-act：两个进程可以同时看到
「不存在」然后都拿到锁。`O_CREAT|O_EXCL` 由内核保证原子性。

## 锁里写 pid，但不死认 pid

pid 用来**报告**「锁是谁占的」，不是为了判活。判活做成了单独的
`is_stale()` 而不是自动抢占：抢锁意味着两个进程同时写文件，
而识别一个「其实还活着只是慢」的进程，比等待它跑完危险得多。
宁可等人，不自动夺。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.write_lock import WriteLock, LockBusy, lock_holder, pid_alive  # noqa: E402


def test_acquire_and_release(tmp_path):
    lock = WriteLock(tmp_path / "w.lock")
    with lock:
        assert lock.path.exists(), "持锁期间锁文件必须在"
    assert not lock.path.exists(), "退出后必须清理，否则明天就再也锁不上"


def test_second_acquire_raises(tmp_path):
    path = tmp_path / "w.lock"
    with WriteLock(path):
        with pytest.raises(LockBusy) as exc:
            WriteLock(path).acquire()
    assert str(path) in str(exc.value), "报错要说清是谁占着、在哪"


def test_released_after_exception(tmp_path):
    path = tmp_path / "w.lock"
    with pytest.raises(ValueError):
        with WriteLock(path):
            raise ValueError("崩了")
    assert not path.exists(), "异常退出也必须放锁，否则一次崩溃锁死全市场"


def test_release_is_idempotent(tmp_path):
    lock = WriteLock(tmp_path / "w.lock")
    lock.acquire()
    lock.release()
    lock.release()          # 再放一次不能炸
    assert not lock.path.exists()


def test_reacquire_after_release(tmp_path):
    path = tmp_path / "w.lock"
    with WriteLock(path):
        pass
    with WriteLock(path):   # 放掉之后必须能再拿到
        pass


def test_lock_records_holder_pid(tmp_path):
    path = tmp_path / "w.lock"
    with WriteLock(path):
        assert path.read_text().strip() == str(os.getpid())


def test_lock_holder_reads_pid(tmp_path):
    path = tmp_path / "w.lock"
    path.write_text("4242")
    assert lock_holder(path) == "4242"
    assert lock_holder(tmp_path / "none.lock") is None
    assert lock_holder(tmp_path / "empty.lock") is None


def test_pid_alive_detects_self():
    assert pid_alive(os.getpid()) is True
    assert pid_alive(999_999) is False


def test_never_steals_a_live_lock(tmp_path):
    """就算锁里的 pid 是当前进程自己（重入），也必须拒绝——两次写同一批文件必坏。"""
    path = tmp_path / "w.lock"
    path.write_text(str(os.getpid()))
    with pytest.raises(LockBusy):
        WriteLock(path).acquire()


def test_release_does_not_steal_same_process_lock(tmp_path):
    """同进程第二个 WriteLock 释放时，**不能**删掉第一个的锁。

    pid 归属核对在这里救不了场：两个对象同属一个进程，pid 一样。
    真正承重的是「自己没持有就不放」那条护栏。
    （第一版测试只测了「连放两次」，那情形下 pid 核对已经拦住了，
    于是删掉护栏的变异体全绿存活——是测试弱，不是变异体无效。）
    """
    path = tmp_path / "w.lock"
    held = WriteLock(path)
    held.acquire()
    intruder = WriteLock(path)
    with pytest.raises(LockBusy):
        intruder.acquire()                       # 拿不到，但对象已存在
    intruder.release()                           # 它没持有，不该有任何副作用
    assert lock_holder(path) == str(os.getpid()), "别人的锁被同进程的 intruder 删了"


def test_release_does_not_steal_replaced_lock(tmp_path):
    """锁的内容已被换成人家的，释放时不许把它删掉。

    现实来源：持锁进程被 kill，锁文件残留；pid 复用后另一个进程拿走了它，
    老进程恢复执行再 release 就会误删活人的锁。
    """
    path = tmp_path / "w.lock"
    lock = WriteLock(path)
    lock.acquire()
    path.write_text(str(os.getpid() + 1))        # 锁被「换人」了
    lock.release()
    assert path.exists(), "释放时必须核对归属，不能删掉别人的锁"


def test_force_escapes_stale_lock(tmp_path):
    """确认是死锁残留时可显式强制抢占。"""
    path = tmp_path / "w.lock"
    path.write_text("999999")
    assert WriteLock(path).is_stale() is True
    lock = WriteLock(path, force=True)
    lock.acquire()
    assert lock_holder(path) == str(os.getpid())
