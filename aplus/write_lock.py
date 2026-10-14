"""主库写锁：同一时刻只许一个进程改 `data/master/daily`。

## 为什么必须有

`bootstrap_history` / `backfill_gaps` / `repair_tail_cols` / `daily_update`
四个脚本都写同一批 parquet。补锁之前 `daily_update` 一点锁都没有——
它和回填撞上时，两个进程同时对同一个文件做 concat + `to_parquet`，
后写的覆盖先写的，丢的列和行没有任何人会知道。

baostock 本身也禁并发：两个会话同时拉会互相踢。

## 为什么用 `O_EXCL`

`if path.exists(): 退出` 是典型的 check-then-act：两个进程可以同时看到
「不存在」然后都以为自己拿到了锁。`O_CREAT|O_EXCL` 由内核保证原子。

## 锁里写 pid，但**不自动**判活抢占

`is_stale()` 只提供信息，**不会**自动夺锁。自动抢占意味着两个进程同时
写文件；而把一个「其实还活着、只是慢」的进程误判成死的，比等它跑完
危险得多。宁可等人。

真确认是残留锁时，显式传 `force=True`——把这个决定交给人，
并且留痕在调用方的日志里。

用法：

    with WriteLock(root / "bootstrap.lock"):
        ...改主库...

    # 或者先问一句
    if WriteLock(path).is_stale():
        print(f"残留锁，pid={lock_holder(path)}")
"""
from __future__ import annotations

import os
from pathlib import Path


class LockBusy(RuntimeError):
    """锁已被别人占用。`pid` 是占用者的 pid（读不到为 None）。"""

    def __init__(self, path, pid=None):
        self.path = str(path)
        self.pid = pid
        who = f"（pid {pid}）" if pid else "（pid 未知）"
        super().__init__(f"写库锁已被占用：{path} {who}。"
                         f"确认对方已停止再跑，或确认是残留锁后用 force=True。")


def lock_holder(path) -> str | None:
    """读出锁里的 pid。锁不存在或内容是空/坏的就返回 None。"""
    try:
        text = Path(path).read_text().strip()
    except (OSError, ValueError):
        return None
    return text or None


def pid_alive(pid) -> bool:
    """pid 是否还活着。macOS/Linux 统一用 `kill(pid, 0)` 探测。"""
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


class WriteLock:
    """可当作上下文管理器用；也支持显式 `acquire()` / `release()`。"""

    def __init__(self, path, *, force: bool = False):
        self.path = Path(path)
        # ★ `path` 是**锁文件本身**，不是它所在的目录。
        #   传错成目录时，`os.open(..., O_CREAT|O_EXCL)` 因为目录已存在而抛
        #   `FileExistsError` ⇒ 报成「锁被占用 / pid 未知」——
        #   一个**假警报**，真相是「你传了个目录」。
        #   2026-10-15 实测踩中，白排查了一轮「谁持有锁」。
        #   ⇒ 这里直接拒掉并说清正确写法。
        if self.path.is_dir():
            raise NotADirectoryError(
                f"WriteLock 的参数是**锁文件路径**，不是数据目录：{self.path} 是目录。"
                f"正确写法：WriteLock({self.path} / 'bootstrap.lock')"
            )
        self.force = force
        self._held = False

    def is_stale(self) -> bool:
        """锁文件存在、但里面的进程已经不在了。**仅供查询，不自动夺。**"""
        pid = lock_holder(self.path)
        if pid is None:
            return False
        return not pid_alive(pid)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if self.force:
            # 显式抢占：先删再抢。删除与重建之间仍有极小的竞态窗口，
            # 但 force 本来就是「我确认那个进程死了」的人工决定，
            # 不假装它是原子的。
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        try:
            fd = os.open(self.path, flags, 0o644)
        except FileExistsError:
            raise LockBusy(self.path, lock_holder(self.path)) from None
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        self._held = True

    def release(self) -> None:
        """幂等：没持有也安全，连放两次不会炸、不会误删别人的锁。"""
        if not self._held:
            return
        self._held = False
        if lock_holder(self.path) == str(os.getpid()):
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
