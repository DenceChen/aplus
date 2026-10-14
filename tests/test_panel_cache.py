"""横截面缓存的溯源守卫。

## 这模块是被一次**假重跑**逼出来的

2026-10-14 我修了市场基准污染（`3c846ff`），然后重跑
`scripts/_verify_reversal_freq.py` 验证结论。那张表给出
「净收益 +109.1%、t=+11.62」——全项目最漂亮的一个数。

**但它没有重跑。** 脚本里：

    cache = Path("/tmp/reversal_panel_cache.pkl")
    if cache.exists() and not args.rebuild:
        xs = pickle.loads(cache.read_bytes())      # ← 复用 10-11 的横截面

缓存的 `_market` 均值是 **−0.00402**，与代码注释里记的**修复前**污染值
逐位相同；五年复合下来是 **−99%**（真实 A 股基准同段是 **+8.96%/年**）。

**一次重跑，带着 10-11 的输入，穿着 10-14 的命令行。**

## 为什么报告溯源还不够

`aplus/provenance.py` 管的是**输出**。这次坏在**输入**：
输出报告是刚写的、盖了章、看起来全新，而它的数据源头是三天前的。

**缓存比报告更危险**——报告至少能看出生成时间，缓存的时间藏在文件名
和 mtime 里，而没人会去查。

## 关键设计：盖在旁边的市场指纹

缓存有 1.7 GB，为判定它新不新而把它整个读进来是不划算的。
所以 sidecar 里存一个**基准指纹**（`_market` 的均值）：

    指纹对不上 → 基准已经变了 → 缓存作废

这一条让上面那次事故在**读第一行数据之前**就能被抓住。
而且即使代码版本号看起来「没动」（比如从 stash 恢复、从旧容器跑），
指纹仍然会说话——**它量的是数据本身，不是版本标签。**
"""
import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from aplus import panel_cache as PC


# ── 夹具：真仓库 + 小缓存 ────────────────────────────────────────────

def _fake_panel(dates, market=None):
    """造一个结构上与真横截面一致的最小对象。"""
    return {"_dates": list(dates),
            "_market": {d: (market[i] if market else 0.001) for i, d in enumerate(dates)}}


def _write_cache(path: Path, dates, market=None):
    import pickle
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(_fake_panel(dates, market), fh, protocol=4)
    return path


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "aplus").mkdir(parents=True)
    for cmd in ("git init -q", "git config user.email t@t", "git config user.name t"):
        subprocess.run(cmd, shell=True, cwd=r, check=True, capture_output=True)
    (r / "aplus" / "backtest.py").write_text("# v1\n")

    def commit(msg):
        subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=r, check=True,
                       capture_output=True)
        return PC.code_sha(r)
    commit("first")
    return {"root": r, "commit": commit}


# ── 核心：没盖章的缓存一律拒绝 ───────────────────────────────────────

def test_a_cache_with_no_stamp_is_refused(repo, tmp_path):
    """**这条是整个模块存在的理由。**

    现存所有 .pkl 缓存都没有 sidecar——它们全都会被这里拦下。
    拦下是刻意的：这些缓存的基准是修复前的（均值 −0.00402），
    而它们生成的每一份报告都还在 data/reports 里。
    """
    p = _write_cache(tmp_path / "old.pkl", ["2019-01-02", "2019-01-03"])
    with pytest.raises(PC.StaleCache) as ei:
        PC.load_cache(p, root=repo["root"])
    assert "sidecar" in str(ei.value)
    assert "--rebuild" in str(ei.value), "拒绝时必须给出可操作的下一步"


def test_refusal_happens_before_the_cache_is_even_read(repo, tmp_path):
    """1.7 GB 的缓存，为判定它新不新而整个读进来不划算。

    更要紧的是：如果判定要靠「读完再比」，那么**第一次事故已经发生**
    （数据已进内存，结论已算出）。指纹必须让拒绝发生在读取之前。
    """
    p = _write_cache(tmp_path / "old.pkl", ["2019-01-02"])
    # 文件被换成垃圾：只要拒绝发生在读取前，就与内容无关
    p.write_bytes(b"NOT A PICKLE")
    with pytest.raises(PC.StaleCache):
        PC.load_cache(p, root=repo["root"])


# ── 版本变了 ────────────────────────────────────────────────────────

def test_a_cache_built_before_a_change_to_the_generator_is_refused(repo, tmp_path):
    p = _write_cache(tmp_path / "c.pkl", ["2019-01-02"])
    PC.stamp_cache(p, "aplus/backtest.py", start="2019-01-02", end="2019-12-31",
                   root=repo["root"], n_dates=1)
    (repo["root"] / "aplus" / "backtest.py").write_text("# v2 修基准污染\n")
    repo["commit"]("fix: 基准污染")
    with pytest.raises(PC.StaleCache) as ei:
        PC.load_cache(p, root=repo["root"])
    assert "aplus/backtest.py" in str(ei.value)


def test_an_unrelated_change_does_not_refuse_the_cache(repo, tmp_path):
    """**这是让这条告警还愿意被听的那一半。**

    改了 `vintage.py` 不该作废面板缓存。若任何提交都作废，
    红线几天内就会变成背景噪声——**静音的守卫等于没有守卫**。
    """
    p = _write_cache(tmp_path / "c.pkl", ["2019-01-02"])
    PC.stamp_cache(p, "aplus/backtest.py", start="2019-01-02", end="2019-12-31",
                   root=repo["root"], n_dates=1)
    (repo["root"] / "aplus" / "vintage.py").write_text("# 无关\n")
    repo["commit"]("feat(vintage): 快照")
    assert PC.load_cache(p, root=repo["root"])["_dates"] == ["2019-01-02"]


# ── 窗口对不上 ──────────────────────────────────────────────────────

def test_a_cache_whose_window_does_not_cover_the_request_is_refused(repo, tmp_path):
    """**要 2024-2026，拿到 2019-2023 —— 这是静默的，且结果「看着正常」。**

    `lowfreq_plausibility.py` 有 `--start/--end`，做法是
    `sorted(d for d in xs["_dates"] if start <= d <= end)`：
    缓存不含目标区间时这里得到空列表，于是**跑出一张全零的表**，
    而不是报错。缓存区间与请求区间必须显式对齐。
    """
    p = _write_cache(tmp_path / "c.pkl", ["2019-01-02", "2019-01-03"])
    PC.stamp_cache(p, "aplus/backtest.py", start="2019-01-02", end="2019-01-03",
                   root=repo["root"], n_dates=2)
    with pytest.raises(PC.StaleCache) as ei:
        PC.load_cache(p, root=repo["root"], start="2018-06-01", end="2026-10-01")
    msg = str(ei.value)
    assert "2019-01-03" in msg and "2026-10-01" in msg, msg
    assert "2019-01-02" in msg and "2018-06-01" in msg, msg


def test_a_request_inside_the_cached_window_is_accepted(repo, tmp_path):
    p = _write_cache(tmp_path / "c.pkl", ["2024-01-02", "2025-01-02"])
    PC.stamp_cache(p, "aplus/backtest.py", start="2024-01-02", end="2025-01-02",
                   root=repo["root"], n_dates=2)
    assert PC.load_cache(p, root=repo["root"], start="2024-06-01",
                         end="2024-09-01")["_dates"]


# ── 基准指纹：即使版本号看起来没动 ───────────────────────────────────

def test_a_changed_market_benchmark_is_caught_even_when_the_generator_did_not_move(
        repo, tmp_path):
    """**指纹量的是数据本身，不是版本标签。**

    从 stash 恢复、从镜像容器跑、手工改过缓存再盖章——这些情形下
    `git log` 会说「生成模块没动过」。但基准从 −0.004 变成 +0.004
    这件事，只有指纹能看见。
    """
    p = _write_cache(tmp_path / "c.pkl", ["2024-01-02"], market=[-0.00402])
    PC.stamp_cache(p, "aplus/backtest.py", start="2024-01-02", end="2024-01-02",
                   root=repo["root"], n_dates=1)
    # 缓存被换成了另一份（基准已修正），但没有重新盖章
    _write_cache(p, ["2024-01-02"], market=[+0.00367])
    with pytest.raises(PC.StaleCache) as ei:
        PC.load_cache(p, root=repo["root"])
    assert "基准" in str(ei.value)


def test_the_stamp_records_the_benchmark_fingerprint(repo, tmp_path):
    """指纹必须落在 sidecar 里——sidecar 是唯一不用读 1.7 GB 就能查的地方。"""
    p = _write_cache(tmp_path / "c.pkl", ["2024-01-02", "2024-01-03"],
                     market=[-0.004, 0.002])
    sc = PC.stamp_cache(p, "aplus/backtest.py", start="2024-01-02",
                        end="2024-01-03", root=repo["root"], n_dates=2)
    rec = json.loads(sc.read_text(encoding="utf-8"))
    assert rec["market_mean"] == pytest.approx(-0.001, abs=1e-9)
    assert rec["market_n"] == 2
    assert rec["window"] == ["2024-01-02", "2024-01-03"]
    assert rec["code_sha"]


# ── 正常路径 ────────────────────────────────────────────────────────

def test_a_fresh_in_range_cache_loads(repo, tmp_path):
    p = _write_cache(tmp_path / "c.pkl", ["2024-01-02"], market=[0.001])
    PC.stamp_cache(p, "aplus/backtest.py", start="2024-01-02", end="2024-01-02",
                   root=repo["root"], n_dates=1)
    assert PC.load_cache(p, root=repo["root"])["_dates"] == ["2024-01-02"]


def test_inspect_reads_only_the_sidecar(repo, tmp_path):
    """`inspect` 是**不读缓存本体**的廉价体检。

    长任务启动前跑一次，1.7 GB 都不用碰。载入前的这一次检查能把
    「先跑三小时才发现跑的是三天前的数据」变成一秒。
    """
    p = tmp_path / "c.pkl"
    p.write_bytes(b"garbage")                      # 本体根本不是 pickle
    PC.stamp_cache(p, "aplus/backtest.py", start="2024-01-02", end="2024-01-03",
                   root=repo["root"], n_dates=2)
    assert PC.inspect(p, root=repo["root"])["ok"] is True
    assert PC.inspect(tmp_path / "none.pkl", root=repo["root"])["ok"] is False


def test_every_problem_is_reported_not_just_the_first(repo, tmp_path):
    """既没盖章、又没盖对生成器 —— 两条都要说。

    只报第一条的话，补完第一条下次还是被拒，而线索没了。
    """
    p = tmp_path / "c.pkl"
    sc = PC.sidecar_path(p)
    sc.parent.mkdir(parents=True, exist_ok=True)
    sc.write_text(json.dumps({"generator": "aplus/nonexistent.py",
                              "window": ["1990-01-01", "1990-01-02"]}),
                  encoding="utf-8")
    with pytest.raises(PC.StaleCache) as ei:
        PC.load_cache(p, root=repo["root"], start="2024-01-01", end="2024-01-02")
    assert len(ei.value.problems) >= 2, ei.value.problems


def test_a_dirty_worktree_at_build_time_is_recorded(repo, tmp_path):
    """和报告溯源同一件事：脏的时候 sha 描述不了真实代码状态。"""
    p = _write_cache(tmp_path / "c.pkl", ["2024-01-02"])
    (repo["root"] / "aplus" / "backtest.py").write_text("# 未提交\n")
    rec = json.loads(PC.stamp_cache(p, "aplus/backtest.py", start="2024-01-02",
                                    end="2024-01-02", root=repo["root"],
                                    n_dates=1).read_text(encoding="utf-8"))
    assert rec["code_dirty"] is True


# ── 写盘与盖章必须同生共死 ──────────────────────────────────────────

def test_build_and_stamp_writes_the_cache_and_its_stamp_together(repo, tmp_path):
    """**最坏的组合是「新缓存 + 旧章」**——指纹会指向一份不存在的数据，
    而守卫会信它。盖章必须紧跟在写盘成功之后。"""
    p = tmp_path / "c.pkl"
    PC.build_and_stamp(p, _fake_panel(["2024-01-02", "2024-01-03"], market=[0.001, 0.002]),
                       start="2024-01-02", end="2024-01-03", root=repo["root"])
    assert p.is_file() and PC.sidecar_path(p).is_file()
    assert json.loads(PC.sidecar_path(p).read_text(encoding="utf-8"))["market_n"] == 2


def test_a_failed_build_leaves_no_stamp(repo, tmp_path):
    """写盘炸了却盖了章 → 下次体检读到章、缓存却不存在或残缺。

    章比缓存活得久，于是**下一次**运行会拿到一个看似合格的入口。
    """
    class Exploding:
        def __reduce__(self):
            raise RuntimeError("模拟写盘中途崩溃")

    p = tmp_path / "c.pkl"
    with pytest.raises(RuntimeError):
        PC.build_and_stamp(p, Exploding(), start="2024-01-02", end="2024-01-02",
                           root=repo["root"])
    assert not PC.sidecar_path(p).exists(), "写盘失败却盖了章——章会活过缓存"
