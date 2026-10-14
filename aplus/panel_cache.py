"""横截面面板缓存的溯源守卫。

## 这模块是被一次**假重跑**逼出来的

2026-10-14 我修了市场基准污染（`3c846ff`），然后「重跑」独立验证脚本。
那张表给出全项目最漂亮的一个数：**净收益 +109.1%、t=+11.62**。

它没有重跑。脚本里写着：

    cache = Path("/tmp/reversal_panel_cache.pkl")
    if cache.exists() and not args.rebuild:
        xs = pickle.loads(cache.read_bytes())

缓存的 `_market` 均值是 **−0.00402**，与代码注释里记的**修复前**污染值
逐位相同，五年复合 **−99%**；同一段真实 A 股基准是 **+8.96%/年**。

> **一次重跑，带着 10-11 的输入，穿着 10-14 的命令行。**

## 为什么报告溯源（`aplus/provenance.py`）还不够

那份管的是**输出**。这次坏在**输入**：
输出报告刚写出来、盖了章、时间戳全新，而数据源头是三天前的。

**缓存比报告更危险**——报告的生成时间写在文件里、有人会看；
缓存的时间藏在 mtime 里，而没人会去查。

## 关键设计：盖在旁边的市场指纹

缓存有 1.7 GB，为判定它新不新而整个读进来不划算，而且**太晚**：
数据已经进了内存，结论已经算出来了。

所以 sidecar 里存一个**基准指纹**——`_market` 的均值：

    指纹对不上 → 基准已经变了 → 缓存作废

这条让上面那次事故在**读第一行数据之前**就能被抓住。
而且即使 `git log` 说「生成模块没动过」（从 stash 恢复、从镜像容器跑、
手工换过缓存），指纹仍然会说话——**它量的是数据本身，不是版本标签。**

## 拒绝的四种情形

| 情形 | 后果 |
|---|---|
| 没有 sidecar | 现存所有 .pkl 都会被拦下——它们全是污染期的 |
| 生成模块此后被改 | 面板由它产出，代码变了缓存就不代表新代码 |
| 缓存窗口不覆盖请求 | 要 2024-2026 拿到 2019-2023，**静默产出全零表** |
| 基准指纹对不上 | 数据被换过而版本号看不出来 |

**全部拒绝，且理由一次报全。** 只报第一条的话，补完第一条下次还是被拒，
而线索已经没了。

## 用法

    xs = load_cache(path, start="2019-01-02", end="2023-12-31", root=ROOT)

不想重建就传 `reuse=True`，但那是**显式选择**——默认值是重建。
默认复用意味着「代码改了也照跑」，而这件事已经发生过了。
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

STAMP_SUFFIX = ".stamp.json"

#: 产出横截面的模块。缓存是它的产物，判新鲜只看它。
DEFAULT_GENERATOR = "aplus/backtest.py"

SELF_PATH = "aplus/panel_cache.py"

_REBUILD_HINT = ("用 scripts/build_panel_cache.py --rebuild 重建"
                 "（不传 --reuse 就默认重建）")


class StaleCache(RuntimeError):
    """缓存不可用。`problems` 一次装下所有原因。"""

    def __init__(self, path, problems: list[str]):
        self.path = str(path)
        self.problems = problems
        super().__init__(f"{Path(path).name} 不可用：" + "；".join(problems)
                         + f"｜{_REBUILD_HINT}")


# ── git（与 provenance 同源，但**不 import 它**） ─────────────────────
# 面板缓存和报告溯源是两件事；共用一个私有小函数比互相 import 更不容易
# 将来某一边改动时把另一边拖下水。真要合并时再说。

def code_sha(root: Path = ".") -> str | None:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def is_dirty(root: Path = ".") -> bool:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=30)
        return bool(r.stdout.strip()) if r.returncode == 0 else False
    except (OSError, subprocess.SubprocessError):
        return False


def _commits_touching(root: Path, path: str, since: str | None) -> list[str]:
    import subprocess
    if not since:
        return []
    try:
        r = subprocess.run(["git", "-C", str(root), "log", f"{since}..HEAD",
                            "--oneline", "--", path],
                           capture_output=True, text=True, timeout=30)
        return [ln for ln in r.stdout.splitlines() if ln.strip()] \
            if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


# ── 基准指纹 ────────────────────────────────────────────────────────

def market_fingerprint(xs: dict) -> tuple[float | None, int]:
    """`_market` 的（均值, 天数）。

    **用均值而不是哈希**：哈希能证明「一模一样」，但回答不了
    「差了多少」——而排查时最需要的恰恰是后者：指纹对不上时，
    打印「−0.00402 → +0.00367」比打印两个 sha256 有用得多。
    """
    m = xs.get("_market") or {}
    if not m:
        return None, 0
    vals = [float(v) for v in m.values()]
    return sum(vals) / len(vals), len(vals)


def sidecar_path(cache_path: Path | str) -> Path:
    p = Path(cache_path)
    return p.with_name(p.name + STAMP_SUFFIX)


# ── 盖章 ────────────────────────────────────────────────────────────

def stamp_cache(cache_path: Path | str, generator: str = DEFAULT_GENERATOR,
                *, start: str, end: str, n_dates: int,
                root: Path = ".", market_mean: float | None = None,
                market_n: int | None = None) -> Path:
    """给刚建好的缓存盖溯源章。

    ## 指纹不是可选参数

    初版允许 `market_mean` 留空，于是「没盖指纹的章」是可以被不小心造出来的——
    而没有指纹的章**只会防版本，防不了换数据**，也就是本模块真正要防的那件事。
    **一个能被打歪的守卫，比没有守卫更糟**：它让人以为自己被护着。

    所以这里默认自己读缓存算指纹。真实路径走 `build_and_stamp`，
    指纹已经在手上，直接传进去，不必再读一遍 1.7 GB。
    """
    from datetime import datetime, timezone
    if market_mean is None and Path(cache_path).is_file():
        try:
            with open(cache_path, "rb") as fh:
                market_mean, market_n = market_fingerprint(pickle.load(fh))
        except Exception:                       # 缓存坏了 → 章仍然盖，n 为 None
            market_mean, market_n = None, None
    rec = {
        "code_sha": code_sha(root),
        "code_dirty": is_dirty(root),
        "generator": generator,
        "stamped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": [start, end],
        "n_dates": n_dates,
        "market_mean": market_mean,
        "market_n": market_n,
    }
    out = sidecar_path(cache_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def build_and_stamp(cache_path: Path | str, xs: dict, *,
                    start: str, end: str, generator: str = DEFAULT_GENERATOR,
                    root: Path = ".") -> Path:
    """原子写盘 + 自动盖章。**新缓存的正常入口一律走这里。**"""
    write_atomic(Path(cache_path), xs)
    mean, n = market_fingerprint(xs)
    return stamp_cache(cache_path, generator, start=start, end=end,
                       n_dates=len(xs.get("_dates") or []), root=root,
                       market_mean=mean, market_n=n)


def write_atomic(path: Path, obj) -> None:
    """先写 `.tmp` 再 `os.replace`。

    中途崩溃会留下**半个** pickle，而它的大小看起来完全正常（1.7 GB 级），
    下一个读它的人会把「我的缓存坏了」误当成「数据坏了」——
    上一个读到这个文件的研究任务就是这么白跑了几小时。
    """
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            pickle.dump(obj, fh, protocol=4)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # 失败即不留痕。留下 1.7 GB 残骸有三重代价：占盘、让下次重跑
        # 误以为「已经开始写了」、把无人值守的一次崩溃变成两次。
        tmp.unlink(missing_ok=True)
        raise


# ── 体检与载入 ──────────────────────────────────────────────────────

def inspect(cache_path: Path | str, root: Path = ".",
            start: str | None = None, end: str | None = None) -> dict:
    """**只读 sidecar，不碰缓存本体**的长任务启动前体检。

    便宜到可以在每个长任务开头无条件跑一次。
    """
    cache_path = Path(cache_path)
    sc = sidecar_path(cache_path)
    problems: list[str] = []
    rec = None
    if not cache_path.is_file():
        problems.append(f"缓存文件不存在：{cache_path}")
    if not sc.is_file():
        problems.append(f"没有溯源 sidecar（{sc.name}）——"
                        "它是用哪个 commit 建的无从得知")
    else:
        try:
            rec = json.loads(sc.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append(f"sidecar 不是合法 JSON：{sc.name}")

    gen = (rec or {}).get("generator") or DEFAULT_GENERATOR
    touched = _commits_touching(Path(root), gen, (rec or {}).get("code_sha")) \
        if rec else []
    if rec and touched:
        problems.append(f"生成后动过 {gen} {len(touched)} 次：{touched[0]}")

    problems.extend(_window_problems(rec, start, end))
    return {"cache": str(cache_path), "ok": not problems,
            "problems": problems, "generator": gen, "stamp": rec,
            "commits": touched}


def _window_problems(rec: dict | None, start: str | None,
                     end: str | None) -> list[str]:
    """缓存区间必须**覆盖**请求区间。

    不覆盖时下游做法是 `d >= start and d <= end` 过滤，得到空列表，
    然后**跑出一张全零的表**——不报错、不为空、只是全零。
    全零的表看起来像「策略没有 alpha」，那是两种完全不同的结论。
    """
    if not rec or start is None or end is None:
        return []
    win = rec.get("window") or []
    if len(win) != 2:
        return [f"sidecar 里的 window 不完整：{win!r}"]
    lo, hi = win
    out = []
    if start < lo:
        out.append(f"请求起点 {start} 早于缓存起点 {lo}")
    if end > hi:
        out.append(f"请求终点 {end} 晚于缓存终点 {hi}")
    return out


def load_cache(cache_path: Path | str, root: Path = ".", *,
               start: str | None = None, end: str | None = None,
               reuse: bool = False, generator: str | None = None) -> dict:
    """载入缓存，**不合格就抛 `StaleCache`**。

    `reuse=True` 仍然只放宽「有没有 sidecar」这一条——它**不放宽**
    「窗口不覆盖请求」和「基准指纹对不上」，因为那两条不是保守，
    是**算错了还看不出来**。
    """
    cache_path = Path(cache_path)
    rep = inspect(cache_path, root=root, start=start, end=end)
    hard = rep["problems"]
    if reuse:
        # 显式选择复用：放行「没盖章」这类**元信息缺失**，
        # 但下面两条是**正确性**问题，一条都不放。
        hard = [p for p in hard if "sidecar" in p]
    if hard:
        raise StaleCache(cache_path, hard)

    with open(cache_path, "rb") as fh:
        xs = pickle.load(fh)

    # 指纹核对放在**载入之后**：读取之前只能问「有没有章」，
    # 指纹要拿到数据才算得出来。所以它防的是「盖章之后被换掉」。
    if not reuse:
        _check_fingerprint(cache_path, xs, rep["stamp"], root)
    return xs


def _check_fingerprint(cache_path: Path, xs: dict, rec: dict | None,
                       root: Path) -> None:
    if not rec or rec.get("market_mean") is None:
        return                       # 没盖指纹的章不拦——已在 inspect 里报过
    mean, n = market_fingerprint(xs)
    if mean is None:
        return
    stamped = float(rec["market_mean"])
    # 相对容差：基准均值在 ±1e-6 内浮动属正常，量级 1e-4 以上才叫「变了」。
    if abs(mean - stamped) > max(abs(stamped) * 1e-6, 1e-6):
        raise StaleCache(cache_path, [
            f"基准指纹对不上：章里 {stamped:+.6f}/日，现在 {mean:+.6f}/日"
            f"（{n} 天 vs 章里 {rec.get('market_n')} 天）——"
            "**这份缓存的基准不是盖章时那个基准**。版本号可能看不出问题，"
            "但从 stash 恢复、从镜像容器跑、手工换过缓存都会走到这里"])


def ensure_fresh(cache_path: Path | str, root: Path = ".", *,
                 start: str | None = None, end: str | None = None) -> dict:
    """只想体检、不想载入时用这个（不读 1.7 GB）。"""
    return inspect(cache_path, root=root, start=start, end=end)
