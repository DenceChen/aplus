"""赛马脚本的 --data-root 参数。

## 为什么需要这个开关

赛马要读全量 parquet 主库。主库不是原子写入（`LocalStore._upsert` 直接
`to_parquet` 覆盖），所以**当补数进程正在后台改写同一批文件时并发读，
可能读到半截文件**。要么等补数跑完再跑赛马，要么——先 rsync 一份快照，
让赛马跑在冻结的数据上。

「跑在快照上」必须是一条正式能力，不是临时 hack：
- 快照让一次赛马的结果可复现（数据冻结，事后别人能重现同一份输入）
- 快照让长赛马可以和补数/其他写入并行，不互相阻塞
- 快照让 A/B 对照实验只有「数据源」这一个变量可控

所以 `--data-root` 要能被测试，不能只是在 main() 里临时读一下 argv。
"""
from pathlib import Path

import pytest

from scripts.evolve_race_fast import ROOT, build_parser


def test_default_data_root_is_repo_data():
    """不传 --data-root 时行为必须与改动前完全一致：读仓库里的 data/。"""
    args = build_parser().parse_args([])
    assert args.data_root == ROOT / "data"


def test_data_root_accepts_snapshot(tmp_path: Path):
    """传快照目录时，日线主库与报告输出都必须落在快照里。"""
    snap = tmp_path / "snap"
    (snap / "reports").mkdir(parents=True)
    args = build_parser().parse_args(["--data-root", str(snap)])
    assert args.data_root == snap


def test_data_root_and_out_can_combine(tmp_path: Path):
    """快照 + 自定义报告名要能同时生效，否则快照跑出来的结果会覆盖正式报告。"""
    snap = tmp_path / "snap2"
    args = build_parser().parse_args(
        ["--data-root", str(snap), "--out", "race_probe_snapshot.json"])
    assert args.data_root == snap
    assert args.out == "race_probe_snapshot.json"


def test_missing_data_root_is_rejected_not_silently_defaulted(tmp_path: Path):
    """快照目录不存在时必须显式失败，而不是悄悄回落到仓库 data/。

    这是本测试最重要的一条：如果回落到仓库 data/，用户以为自己跑的是
    快照（不可复现、可能撞上半写文件），实际跑的是活动主库，结果会被误信。
    """
    from scripts.evolve_race_fast import resolve_data_root
    with pytest.raises(SystemExit):
        resolve_data_root(build_parser().parse_args(
            ["--data-root", str(tmp_path / "does_not_exist")]).data_root)


@pytest.mark.parametrize("arg", ["--months", "--start", "--end", "--top-n", "--bottom-n",
                                 "--train-ratio", "--embargo", "--entry-lag", "--promote"])
def test_existing_flags_still_parse(arg):
    """新增参数不能挤掉既有参数——回归保护。"""
    args = build_parser().parse_args([arg, "3"] if arg != "--promote" else [arg])
    assert args is not None
