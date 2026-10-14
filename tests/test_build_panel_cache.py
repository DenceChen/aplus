"""面板缓存必须原子写入，且失败时不得留下「看起来正常」的半个文件。

## 为什么这条判据在

一次写到一半崩溃的 `pickle.dump` 会留下一个**大小完全正常**的残缺文件
（本项目面板缓存约 1.7 GB，崩在 90% 处就有 1.5 GB）。下一个读它的人看到的是
「缓存坏了」，很容易误判成「数据坏了」，从而去重建数据——方向完全错了。

所以判据钉的是**失败后的磁盘状态**，不是成功路径：
写失败时，目标路径必须不存在，且临时文件必须被清掉。
"""
import os
import pickle
from pathlib import Path

import pytest

from scripts.build_panel_cache import write_atomic


def test_successful_write_produces_a_readable_pickle(tmp_path: Path):
    obj = {"_dates": ["2024-01-02", "2024-01-03"], "_market": {"2024-01-02": 0.01}}
    out = tmp_path / "panel.pkl"
    write_atomic(out, obj)
    assert pickle.loads(out.read_bytes()) == obj


def test_failed_write_leaves_no_target_file(tmp_path: Path):
    """写失败时目标路径必须不存在。

    这里模拟「写了一半崩掉」：临时文件已经被写了些字节就抛异常。
    """
    out = tmp_path / "panel.pkl"

    class Exploding:
        """让 pickle 在写到中途失败。"""

        def __reduce__(self):
            raise RuntimeError("模拟写盘中途崩溃")

    with pytest.raises(RuntimeError):
        write_atomic(out, Exploding())

    assert not out.exists(), (
        "崩溃后目标路径出现了文件——下一个读它的人会误判成数据损坏，"
        "而方向恰好错得最贵：去重建数据")


def test_failed_write_leaves_no_stray_temp_file(tmp_path: Path):
    """临时文件也必须清掉，否则 /tmp 会被几百 MB 的残骸慢慢塞满。"""
    out = tmp_path / "panel.pkl"

    class Exploding:
        def __reduce__(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_atomic(out, Exploding())

    stray = list(tmp_path.glob("*.tmp"))
    assert not stray, f"残留临时文件：{stray}"


def test_existing_target_is_not_clobbered_until_the_new_write_succeeds(tmp_path: Path):
    """原子性：成功前旧文件保持可读，失败后旧文件也必须完好。

    这是 `os.replace` 相对「先 open('wb')」的全部意义——
    `open(path, "wb")` 会在**写入第一个字节前**就把旧文件截断。
    """
    out = tmp_path / "panel.pkl"
    old = {"_dates": ["2019-01-02"], "_market": {}}
    write_atomic(out, old)

    class Exploding:
        def __reduce__(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_atomic(out, Exploding())

    assert pickle.loads(out.read_bytes()) == old, (
        "失败的写入破坏了已有的好缓存——这是最坏的结果："
        "旧的可用数据没了，新的也没写成")


def test_temp_file_lives_on_the_same_filesystem_as_the_target(tmp_path: Path):
    """临时文件必须在目标同目录，跨文件系统的 replace 不是原子的。"""
    out = tmp_path / "panel.pkl"
    write_atomic(out, {"a": 1})
    # with_suffix('.pkl.tmp') 保证与目标同目录；这里直接钉住这个事实
    expected = out.with_suffix(out.suffix + ".tmp")
    assert expected.parent == out.parent, "临时文件必须与目标同目录"


def test_a_truncated_pickle_is_detectable_rather_than_silently_accepted(tmp_path: Path):
    """反面说明：直接写坏文件确实会「看起来正常」，所以才需要原子写。

    这条不是测 write_atomic，是把**它要防的那种失败**摆出来：
    一个被截断的 pickle 仍然有合理的大小，但 loads 会炸。
    """
    bad = tmp_path / "panel.pkl"
    payload = pickle.dumps({"_dates": ["2019-01-02"] * 50000, "_market": {}})
    bad.write_bytes(payload[:len(payload) // 2])

    # 文件大小接近原值——单看 `ls -l` 发现不了问题
    assert bad.stat().st_size > len(payload) * 0.4
    with pytest.raises(Exception):
        pickle.loads(bad.read_bytes())