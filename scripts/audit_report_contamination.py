"""基准污染的引用面审计——**谁在被污染的报告当证据用**。

## 为什么这个脚本必须存在，而不是一次排查

2026-10-14 14:02 的 `3c846ff` 修掉了 `_market_by_day` 把**上市前股票**
也采进市场基准的缺陷。修复前基准年化约 −126%，真实约 +16%——
偏差 **约 115 个百分点/年**，而 `up_excess_vs_market` 是本平台主指标，
于是**每一份在此之前生成的报告，其超额都被系统性虚增了同样的量**。

污染影响的是**引用**，不只是文件本身。一份作废的报告躺在
`data/reports/` 里没人看，没有危害；**被前瞻验证注册表当作候选来源**，
危害就落地了。

实测（2026-10-14）：含市场指标的 17 份报告里 **14 份作废**，
且三批前瞻验证候选**全部**引用了作废报告。

## 切割点从 git 推导，不写死时间戳

写死 `2026-10-14 14:02` 的问题是：下次污染修了，脚本还会按旧时间切，
于是新报告被误判为干净。真正的判据是**修复的代码本身**：

    git log -S "pos == 0" -- aplus/backtest.py

找不到时**必须报错退出，不许退回硬编码时间**——
「无法判断」伪装成「都没问题」，正是这个项目今天栽过三次的坑。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: 修复 `3c846ff` 引入的判据。用 `git log -S` 按**代码内容**找，
#: 而不是按时间戳——时间戳会在下一次修复后立刻变成错的。
GUARD_MARKER = "pos == 0"

#: 这些字段全部以市场基准为分母，全部被污染同步抬高。
CONTAM_KEYS = {"up_excess_vs_market", "down_excess_vs_market",
               "excess_breadth", "excess_tilt", "gross_up_excess", "market_pct"}


def fix_commit(root: Path = ROOT) -> tuple[int, str]:
    """基准污染修复的 (unix 时间戳, 短 sha)。

    找不到就抛——**不猜**。猜错一次，后面所有分类都建立在错的切割点上，
    而错的方向是「把作废的当干净」，也就是继续引用坏数字。
    """
    r = subprocess.run(
        ["git", "-C", str(root), "log", "-S", GUARD_MARKER,
         "--format=%ct %h", "--", "aplus/backtest.py"],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("git log 失败")
    hits = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not hits:
        raise RuntimeError(
            f"在 aplus/backtest.py 的历史里找不到 {GUARD_MARKER!r}——"
            "**无法确定污染修复的时刻**。不要退回硬编码时间戳，"
            "那会在下一次修复后静默失效。先把守卫加回去。")
    ts, sha = hits[0].split()[0], hits[0].split()[1]
    return int(ts), sha


def carries_market_metrics(obj, depth: int = 0) -> list[str]:
    """报告里出现的污染敏感字段名。

    **按内容判定，不按文件名猜。** 文件名会骗人——`stats.json`、
    `universe_isolation_7y.json` 都带着完整的市场字段。
    """
    hit: set[str] = set()
    if depth > 8:
        return []
    if isinstance(obj, dict):
        hit |= set(obj) & CONTAM_KEYS
        for v in obj.values():
            hit |= set(carries_market_metrics(v, depth + 1))
    elif isinstance(obj, list):
        for v in obj[:400]:
            hit |= set(carries_market_metrics(v, depth + 1))
    return sorted(hit)


def registry_citations(reports_dir: Path) -> dict[str, list[str]]:
    """哪份报告被哪一批前瞻候选引用。

    **这一项才是危害落地的地方。** 一份没人引用的作废报告只是垃圾；
    被注册表当证据用的作废报告，会把一组坏候选送进唯一的、不许偷看的前瞻实验。
    """
    path = reports_dir / "forward_registry.json"
    if not path.is_file():
        return {}
    try:
        batches = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, list[str]] = {}
    for i, b in enumerate(batches if isinstance(batches, list) else [], 1):
        ev = (b.get("evidence") or "") if isinstance(b, dict) else ""
        reg = b.get("registered_on", "?") if isinstance(b, dict) else "?"
        for token in ev.replace("（", " ").replace("）", " ").split():
            token = token.strip("()（）,，。/ ")
            if token.endswith(".json"):
                out.setdefault(token, []).append(f"批次{i}({reg})")
    return out


def audit(reports_dir: Path = ROOT / "data" / "reports",
          root: Path = ROOT) -> dict:
    cutoff, sha = fix_commit(root)
    cited = registry_citations(reports_dir)

    contaminated, clean = [], []
    for p in sorted(reports_dir.glob("*.json")):
        if p.name.endswith(".provenance.json"):
            continue                     # sidecar 不是报告——告警不许自己制造告警
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        keys = carries_market_metrics(obj)
        if not keys:
            continue
        mtime = p.stat().st_mtime
        row = {"report": p.name, "keys": keys, "mtime": mtime,
               "cited_by": cited.get(p.name, [])}
        (contaminated if mtime < cutoff else clean).append(row)

    live = [r for r in contaminated if r["cited_by"]]
    return {"cutoff_ts": cutoff, "fix_commit": sha,
            "n_contaminated": len(contaminated), "n_clean": len(clean),
            "contaminated": contaminated, "clean": clean,
            "contaminated_and_cited": live, "registry_citations": cited}


def main() -> int:
    ap = argparse.ArgumentParser(description="基准污染引用面审计")
    ap.add_argument("--reports-dir", type=Path, default=ROOT / "data" / "reports")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        rep = audit(a.reports_dir, a.root)
    except RuntimeError as e:
        print(f"无法审计：{e}", file=sys.stderr)
        return 3

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 2 if rep["contaminated_and_cited"] else 0

    import datetime as _dt
    t = _dt.datetime.fromtimestamp(rep["cutoff_ts"]).strftime("%m-%d %H:%M")
    print(f"基准污染修复 {rep['fix_commit']}（{t}）——此前生成的报告，其超额被虚增\n")
    print(f"含市场指标的报告：作废 {rep['n_contaminated']} · 干净 {rep['n_clean']}\n")
    if rep["contaminated_and_cited"]:
        print("🔴 **作废且正在被引用**——危害已经落地：")
        for r in rep["contaminated_and_cited"]:
            print(f"   {r['report']}  ←  {'、'.join(r['cited_by'])}")
        print()
    print(f"作废（{rep['n_contaminated']} 份，一律不许引用其超额数字）：")
    for r in rep["contaminated"]:
        mark = " ←被引用" if r["cited_by"] else ""
        print(f"   {_dt.datetime.fromtimestamp(r['mtime']):%m-%d %H:%M}  "
              f"{r['report']}{mark}")
    # **作废且被引用 → 非零。** 只报不拦的话，这份审计和没跑一样。
    return 2 if rep["contaminated_and_cited"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
