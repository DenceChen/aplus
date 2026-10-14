"""Vintage 累积（出路 B）——**把「PIT 尚未建立」变成「正在建立」的唯一途径**。

## 为什么必须是这个，而不是别的

`financial.py` 的实测结论：当前源（akshare 东财）每个报告期只存一行、
**没有版本维度**，所以它服务的一定是**后修值**。
出路 A（只留 `UPDATE_DATE == NOTICE_DATE` 的记录）已被
`scripts/diag_financial_lookahead.py` 实测否决——白名单率与报告年龄强相关
（2025 年报 97.4%、2024 年 0.4%），筛出来的是「最新」不是「可信」。

**只剩这一条路。** 而它有本项目其他任务都没有的性质：

> **价值随延迟毁灭。今天不落盘，那一天的原始值就永久丢失。**

不是难补，是**补不回来**——源站只保留当前值，昨天服务过什么，
除了当时有人记下来，否则永远无从得知。

## 这个模块的产出**不是 PIT**，是 PIT 的原材料

攒够之前，任何声称「已做 PIT」的结论都是假的。
`span()` 如实报告覆盖到哪天，`pit_frame()` 对没有快照的日子**报错而不是返回空帧**。
理由在 `tests/test_vintage.py` 里逐条钉住。

## 三个不可让步的实现约定

1. **只写当天那一份，绝不触碰历史**。覆盖任何一天 = 销毁一段不可再生的记录。
2. **读只向后回退，绝不向前取**。`pit_frame(2026-10-12)` 在只有 10-13 快照时
   必须报错——拿 10-13 凑合，产出的帧和直接读当前缓存**一样地正常**，
   前视偏差原封不动地回来了，只多了一个「已做 PIT」的标签。
3. **manifest append-only，且记内容摘要**。账本第 78 条的教训：
   我曾用一个不可信的字段当证据。**不能自证未被改写的采集数据等于没采集。**
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

#: 快照根目录。按 `date=YYYY-MM-DD/` 分区，一天一份 parquet。
VINTAGE_ROOT = Path("data/reports/financial_vintage")

PARTITION_NAME = "financial.parquet"
MANIFEST_NAME = "_manifest.jsonl"

#: 返回帧上记录的「**实际**用了哪一天的快照」。回退发生时它 ≠ 请求日期。
SNAPSHOT_DATE_ATTR = "snapshot_date"


# ── 路径 ────────────────────────────────────────────────────────────

def snapshot_path(root: Path | str, day: str) -> Path:
    """某一天的快照文件路径。"""
    return Path(root) / f"date={day}" / PARTITION_NAME


def manifest_path(root: Path | str) -> Path:
    return Path(root) / MANIFEST_NAME


# ── 写 ──────────────────────────────────────────────────────────────

def _digest(df: pd.DataFrame) -> str:
    """内容摘要，**不是文件字节**。

    parquet 的字节不保证可复现（页脚元数据、写入顺序都可能变），
    拿文件字节做摘要会让「同一天合法重跑」也被报成篡改——那种假警报
    只需要发生一次，之后就没人看这个校验了。
    所以摘要取自**数据本身**：列名 + 逐行哈希。
    """
    h = hashlib.sha256()
    h.update("\x1f".join(map(str, df.columns)).encode())
    h.update(pd.util.hash_pandas_object(df, index=True).values.tobytes())
    return h.hexdigest()


def write_snapshot(root: Path | str, day: str, df: pd.DataFrame,
                   amend: bool = True) -> Path:
    """落盘某一天的快照。**只写这一天。**

    ## 为什么存整帧，不做列裁剪

    落盘时没抓的字段，**过去那一天就永远补不回来了**——源站只服务当前值。
    所以这里不做任何「只用得到这些列」的优化；
    存不下是存储问题，存漏了是**数据永久损失**。

    ## `amend=False` 只给测试用

    用来模拟「有人绕过本模块直接改了文件」，好让 `verify_integrity` 能抓到。
    生产路径永远走默认的 `amend=True`。
    """
    root = Path(root)
    out = df.copy()
    out["snapshot_date"] = day          # 幂等：同一天重跑不会多出一列
    path = snapshot_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)

    if amend:
        # 摘要必须取自**落盘之后读回来的那个文件**，不是写之前的内存帧。
        #
        # 初版摘要的是内存帧，结果 manifest 与文件对不上，完整性校验永远红。
        # 根因是 pandas 的 NA/NaN 在 parquet 往返里会换表示
        # （`StringDtype` 的 `NA`、`object` 的 `None`、float 的 `NaN`），
        # 于是「写出去的」与「存下来的」不是同一个对象。
        # 真实财报帧有 15 个 StringDtype 列 + 27 个 object 列，必然踩中。
        #
        # 而我最初的测试全用玩具帧——它们恰好能无损往返，
        # **所以这个 bug 一次也没被测出来**。
        #
        # 改成读回再摘要，manifest 就成了关于**产物**的陈述，
        # `verify_integrity` 才可能只凭文件本身复现它。
        persisted = pd.read_parquet(path).drop(
            columns=["snapshot_date"], errors="ignore")
        entry = {
            "snapshot_date": day,
            "written_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "n_rows": int(len(persisted)),
            "n_cols": int(persisted.shape[1]),
            "sha256": _digest(persisted),
        }
        # **追加，不覆盖。** 覆盖就抹掉了「这一天的值后来变过」这个事实，
        # 而那个事实正是 vintage 唯一要回答的问题。
        with manifest_path(root).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


# ── 读 ──────────────────────────────────────────────────────────────

def available_days(root: Path | str) -> list[str]:
    """**实际存在的**快照日期（看文件系统，不看 manifest）。

    manifest 可能有多条同日记录（重跑），也可能缺条目（外部拷贝进来）。
    判断「那天有没有数据」只能看文件在不在。
    """
    root = Path(root)
    if not root.is_dir():
        return []
    days = []
    for d in root.glob("date=*"):
        if (d / PARTITION_NAME).is_file():
            days.append(d.name.split("=", 1)[1])
    return sorted(days)


def latest_on_or_before(root: Path | str, as_of: str) -> str | None:
    """`<= as_of` 的最后一份快照。**绝不返回晚于 `as_of` 的日期。**"""
    cands = [d for d in available_days(root) if d <= as_of]
    return max(cands) if cands else None


def read_manifest(root: Path | str) -> list[dict]:
    """manifest 全部记录，**按写入顺序**（同日多条保留先后）。"""
    p = manifest_path(root)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def span(root: Path | str = VINTAGE_ROOT) -> dict:
    """vintage 实际覆盖到哪天——**如实报，不外推**。

    `calendar_days` 是「声称覆盖」的天数，`n_days` 是「真的有快照」的天数。
    两者不等就说明中间有缺口，而缺口里的历史是补不回来的。
    """
    days = available_days(root)
    if not days:
        return {"first": None, "last": None, "n_days": 0, "calendar_days": 0,
                "gaps": []}
    a, b = datetime.strptime(days[0], "%Y-%m-%d"), \
        datetime.strptime(days[-1], "%Y-%m-%d")
    cal = (b - a).days + 1
    gaps = []
    for prev, cur in zip(days, days[1:]):
        p = datetime.strptime(prev, "%Y-%m-%d")
        c = datetime.strptime(cur, "%Y-%m-%d")
        missing = (c - p).days - 1
        if missing > 0:
            gaps.append({"after": prev, "before": cur, "missing_days": missing})
    return {"first": days[0], "last": days[-1], "n_days": len(days),
            "calendar_days": cal, "gaps": gaps}


def pit_frame(root: Path | str, as_of: str) -> pd.DataFrame:
    """取 `as_of` 那天**源站当时服务的**那一帧。

    ## 为什么找不到快照时必须报错

    若退而求其次拿「最新的一份」凑数，产出的 PIT 帧与直接读当前缓存
    **在行为上完全一致**——都是后修值——却多了一个「已做 PIT」的标签。
    那比不做 PIT 更糟，因为它让前视偏差**免于被审查**。

    若返回空帧，下游会把「那天全市场没有财报」当真——那是彻底的错误结论。

    所以两条路都堵死：**只有报错**。

    ## 缺口怎么回退

    向前回退（用更早的那份）允许——那在 PIT 意义上是保守的：
    少用信息，但绝不多用。回退发生时 `attrs["snapshot_date"]`
    会记下**实际**用了哪天，调用方据此判断信息有多旧。
    """
    day = latest_on_or_before(root, as_of)
    if day is None:
        raise ValueError(
            f"没有 {as_of} 或更早的快照（现有：{available_days(root) or '无'}）。"
            f"**拒绝用更新的快照代替**——那会把前视偏差请回来。")
    df = pd.read_parquet(snapshot_path(root, day))
    df.attrs[SNAPSHOT_DATE_ATTR] = day
    df.attrs["requested_date"] = as_of
    return df


# ── 完整性 ──────────────────────────────────────────────────────────

def verify_integrity(root: Path | str = VINTAGE_ROOT) -> dict:
    """逐份核对快照是否仍与当初落盘时一致。

    取该日期**最后一条** manifest 记录比对——同一天合法重跑会追加新条目，
    旧的保留为历史。文件与最后一条对不上，就是有人事后动过。

    账本第 78 条：那份数据的价值全部来自「它在当时就是那样」，
    一旦被改过，**它就不再是证据**。
    """
    root = Path(root)
    latest: dict[str, dict] = {}
    for e in read_manifest(root):
        latest[e["snapshot_date"]] = e      # 后写覆盖前写，但旧条目仍在文件里

    tampered, missing = [], []
    for day, e in sorted(latest.items()):
        p = snapshot_path(root, day)
        if not p.is_file():
            missing.append(day)
            continue
        got = _digest(pd.read_parquet(p).drop(columns=["snapshot_date"],
                                              errors="ignore"))
        if got != e["sha256"]:
            tampered.append(day)
    return {
        "ok": not tampered and not missing,
        "checked": len(latest),
        "tampered": tampered,
        "missing": missing,
        "span": span(root),
    }
