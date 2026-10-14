"""报告溯源与新鲜度——**「这份数字是哪个 commit 算出来的」必须可回答**。

## 这模块是被一次真实的错误逼出来的

2026-10-14 我读了 `data/reports/backtest_summary.json`（11:45 生成），
据此告诉用户「信号符号反了」。

而 14:02 的 commit `3c846ff` 修掉了市场基准污染（把基准从 +0.066%/日
拖到 −0.402%/日，虚增约 114 个百分点/年）。**那份报告作废了。**

数字本身看着完全正常——`−0.40%/日` 是个体面的数，
`excess_tilt = −0.005` 是个体面的数。**没有任何东西看起来是坏的。**

我之所以没发现，纯粹是因为我**没去看生成时间 vs 最后一次相关改动**。
那一步没有任何工具提示我必须做，于是我没做。

> **没有溯源的报告，等于没有来源的断言。**
> 而看起来正常的数字，恰恰是最难被质疑的那一种。

## 提供的三件事

1. `stamp(...)` —— 生成报告时把 `{code_sha, generator, at}` 写进 sidecar。
2. `freshness(...)` —— 判定一份报告相对**指定生成模块**是否过期。
3. `scan(...)` —— 扫整个 reports 目录，列出过期与**无溯源**的。

## 为什么判据是「生成模块」而不是「整个 commit 范围」

HEAD 每动一次就全盘报红，红色很快会变成背景噪声——和「永远红的测试」
是同一种失败：它教会所有人忽略告警。

只盯**真正产出这个数字的模块**，红才有信息量。
`backtest_summary.json` 该对 `backtest.py` 敏感，不该对 `vintage.py` 敏感。

## 无溯源 ≠ 过期，但也不等于可信

现存的老报告都没有 sidecar。它们被报成 `unknown` 而不是 `stale`：
**「不知道」和「已知坏了」是两回事**，混为一谈会让人连真的坏报告也忽略。
`unknown` 的处理方式是**不许引用**，而不是「大概没事」。
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SIDECAR_SUFFIX = ".provenance.json"

#: 指路时用的模块全名。**不能写 `__name__`**——`python -m aplus.provenance`
#: 时它是 `"__main__"`，于是提示变成「在 __main__ 的 DEFAULT_GENERATORS 补一行」，
#: 指了个根本不存在的地方。**指错路比不指路更糟。**
SELF_PATH = "aplus/provenance.py"

#: 报告 → 产出它的模块。**这张表是本模块的全部知识**，
#: 不知道来源的报告会被报成 unknown（不许引用），而不是默默放行。
#:
#: 键是 reports 目录下的文件名，值是 git 路径。
#: 表外的报告一律 `unknown` —— **漏登记比登记错更安全**，
#: 因为登记错只是漏报，漏登记会让人以为没人管。
DEFAULT_GENERATORS: dict[str, str] = {
    "backtest_summary.json": "aplus/backtest.py",
    "lowfreq_plausibility.json": "scripts/lowfreq_plausibility.py",
    "market_benchmark_audit.json": "aplus/backtest.py",
    # 赛马报告的判决逻辑在 evolve_race.oos_decision，不在驱动脚本
    "ic_guided_race_7y.json": "aplus/evolve_race.py",
    "ic_guided_race_v1.json": "aplus/evolve_race.py",
    # 2026-10-14 修正基准后的重跑。与首跑**同名不同源**：它换了数据根
    # （aplus_snap_1012 → aplus_snap_final）、换了基准、且今晨翻过
    # DEFAULT_WEIGHTS 的符号——**代码与基准双变**，所以它是新报告，
    # 不能与 `ic_guided_race_7y.json` 混为一谈，也不能覆盖它。
    #
    # ⚠️ 这份报告与首跑**不构成对照实验**，重跑同时改了两个东西：
    #   ① 基准污染修复（首跑的 up_excess_vs_market 虚增约 115pp/年）
    #   ② 冠军基准的符号翻转（首跑 champion 是 {mom20:1.0, vol20:0.5, vsur:0.5}，
    #      今晨改为 {mom20:-1.0, vol20:-0.5, vsur:-0.5}）
    # 窗口（2019-01-02~2026-10-12）与切分（train→2023-08-17 /
    # test 2023-08-25~2026-10-09）刻意保持一致，所以**只有这两项在变**——
    # 但正因如此，两者的效应在报告里**分不开**。引用它做「基准修复带来多少
    # 改善」的结论是不成立的。
    "ic_guided_race_7y_corrected.json": "aplus/evolve_race.py",
    "reversal_freq_2019_2023.json": "aplus/backtest.py",
    # 2026-10-14 作废：年成本放大 ~10 倍（篮子级逐日值用 .sum()）
    # + 净收益重复扣一次成本 + t 未按调仓周期分块。
    # **结论方向未变**（24 行年超额全负，那一列不受影响），
    # 变的是量级与「最优行」的identity——`best` 按含污染的 net 选出。
    # 重跑见 docs/TODO_rerun_reversal_report.md。
    "VOID_reversal_freq_2019_2023__cost_10x_and_double_deduct.json":
        "aplus/backtest.py",
    "regime_control_sweep.json": "aplus/backtest.py",
    "factor_ic_7y.json": "aplus/factor_ic.py",
    # 2026-10-14 重跑：`yearly` 补齐三档（原实现只聚合 `all`，
    # 是**口径不一致**——全段表出三档、逐年表只出一档）。
    # 判决见 docs/TODO_measure_sigma_r.md：**3/8 年不过月频门槛**。
    # 旧报告仍在册：它的**全截面逐因子符号**仍有效（v2 已复核同号），
    # 但**它的 yearly 缺低尾档，永远不许用来做月频判决**——
    # scripts/verdict_monthly_hurdle.py 对它会拒绝退出，测试钉着这件事。
    "factor_ic_7y_v2.json": "scripts/factor_ic_diagnostic.py",
    # σ_r 的**读面板口径**在脚本，纯函数在 aplus/xsec_sigma.py——
    # 公式不该跟着口径变（换池子/缩尾比例时只改脚本）。
    "xsec_sigma_r.json": "scripts/measure_xsec_sigma.py",
    "fundamental_ic.json": "aplus/factor_ic.py",
    "forward_registry.json": "aplus/forward.py",
    "financial_annual_cache.parquet": "aplus/financial.py",
    "evolution_ledger.json": "aplus/ledger.py",
}


# ── git ────────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def code_sha(root: Path = ".") -> str | None:
    """当前代码的 sha。取不到就返回 None——**不做降级猜测**。"""
    return _git(Path(root), "rev-parse", "HEAD")


def is_dirty(root: Path = ".") -> bool:
    """工作区有未提交改动。

    有未提交改动时 sha 不足以标识代码状态——**所以这个标记要跟着
    sidecar 一起存**，否则「干净时是 A、脏时其实是 B」这种混淆无法事后分辨。
    """
    out = _git(Path(root), "status", "--porcelain")
    return bool(out)


def commits_touching(root: Path, path: str, since: str | None) -> list[str]:
    """`since` 之后动过 `path` 的 commit（短 sha + 摘要）。

    `since` 为 None 时返回空列表——**没有基线就无从判断「之后」**，
    硬猜一个会让「无法判断」看起来像「没变化」，而那正是最危险的误判。
    """
    if not since:
        return []
    out = _git(Path(root), "log", f"{since}..HEAD", "--oneline", "--", path)
    return [ln for ln in (out or "").splitlines() if ln.strip()]


# ── sidecar ────────────────────────────────────────────────────────

def sidecar_path(report_path: Path | str) -> Path:
    p = Path(report_path)
    return p.with_name(p.name + SIDECAR_SUFFIX)


def stamp(report_path: Path | str, generator: str, root: Path = ".",
          extra: dict | None = None) -> Path:
    """给一份刚生成的报告盖上溯源章。

    写在**报告旁边**而不是报告内部：报告的 schema 是下游在读的，
    往里塞字段会污染它，也会在 `sha256` 比对时炸掉。
    """
    rec = {
        "code_sha": code_sha(root),
        "code_dirty": is_dirty(root),
        "generator": generator,
        "stamped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if extra:
        rec.update(extra)
    out = sidecar_path(report_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def read_stamp(report_path: Path | str) -> dict | None:
    p = sidecar_path(report_path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ── 判定 ───────────────────────────────────────────────────────────

def freshness(report_path: Path | str, generator: str | None = None,
              root: Path = ".") -> dict:
    """一份报告相对它的生成模块，是否还新鲜。

    返回 `verdict` ∈ {`fresh`, `stale`, `unknown`}：

    - `stale` —— 有溯源章，且**生成之后**动过生成模块。
    - `unknown` —— 没有溯源章，或章里的 sha 不是本仓库的 commit。
      **不是「大概没事」，是「不许引用」。**
    - `fresh` —— 有章，且生成模块此后未动。

    ## 脏工作区单独报出来

    脏的时候 sha 描述不了真实代码状态，**任何 `fresh` 结论都不可靠**——
    所以 `dirty=True` 时即使 verdict 是 fresh，也附 `trustworthy=False`。
    宁可多一次人工确认，也不要一个看起来干净实际不干净的结论。
    """
    root = Path(root)
    name = Path(report_path).name
    gen = generator or DEFAULT_GENERATORS.get(name)
    st = read_stamp(report_path)

    # **所有原因一次报全，不在第一个就 return。**
    #
    # 初版按「sidecar → generator → sha」逐个 return，于是又老又没登记的
    # 报告只报「没有 sidecar」——而**可操作**的那条（去 DEFAULT_GENERATORS
    # 补一行）被吞掉了。只报第一个原因，等于把能修的那条藏起来：
    # 人去补了 sidecar，下次还是 unknown，而且这次连线索都没了。
    problems: list[str] = []
    if st is None:
        problems.append("没有溯源 sidecar——生成它的 commit 无从得知")
    elif not st.get("code_sha"):
        problems.append("溯源章里没有 code_sha")
    if not gen:
        problems.append(f"不在 DEFAULT_GENERATORS 里，不登记来源就无法判定"
                        f"（在 {SELF_PATH} 的 DEFAULT_GENERATORS 补一行）")
    if problems:
        return {"report": name, "verdict": "unknown", "generator": gen,
                "reason": "；".join(problems),
                "problems": problems, "trustworthy": False, "commits": []}

    touched = commits_touching(root, gen, st["code_sha"])
    dirty = bool(st.get("code_dirty"))
    if touched:
        return {"report": name, "verdict": "stale", "generator": gen,
                "reason": f"生成后动过 {gen} {len(touched)} 次",
                "commits": touched, "trustworthy": False,
                "stamped_sha": st["code_sha"], "stamped_at": st.get("stamped_at")}
    return {"report": name, "verdict": "fresh", "generator": gen,
            "reason": "生成后生成模块未改动",
            "commits": [], "trustworthy": not dirty,
            "stamped_sha": st["code_sha"], "stamped_at": st.get("stamped_at"),
            "dirty_at_stamp": dirty}


def scan(reports_dir: Path | str = "data/reports",
         root: Path = ".") -> list[dict]:
    """扫全目录，按严重程度排序：**能判坏的排前面，不能判的排后面**。

    排序不是装饰——`stale` 是**已知的坏**，`unknown` 是**不知道**，
    前者必须先看。只按文件名排序会把这两类混在一起，
    然后人只看前几行，最要命的那条就永远没被看到。
    """
    out = []
    for p in sorted(Path(reports_dir).glob("*.json")):
        # sidecar 也叫 `.json`，glob 会把它当成一份「没有 sidecar 的报告」——
        # **告警自己制造告警**。这种噪音上线几天就会把输出静音，
        # 而静音的守卫等于没有守卫。
        if p.name.endswith(SIDECAR_SUFFIX):
            continue
        out.append(freshness(p, root=root))
    order = {"stale": 0, "unknown": 1, "fresh": 2}
    out.sort(key=lambda r: (order.get(r["verdict"], 9), r["report"]))
    return out


def summary(reports_dir: Path | str = "data/reports",
            root: Path = ".") -> dict:
    rows = scan(reports_dir, root)
    return {"n": len(rows),
            "stale": [r["report"] for r in rows if r["verdict"] == "stale"],
            "unknown": [r["report"] for r in rows if r["verdict"] == "unknown"],
            "fresh": [r["report"] for r in rows if r["verdict"] == "fresh"],
            "rows": rows}

# ── CLI ────────────────────────────────────────────────────────────

def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="报告新鲜度体检")
    ap.add_argument("--reports-dir", default="data/reports")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true", help="输出机器可读")
    a = ap.parse_args(argv)
    s = summary(a.reports_dir, a.root)

    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(f"HEAD {code_sha(a.root)}"
              f"{'  **工作区有未提交改动**' if is_dirty(a.root) else ''}")
        print(f"共 {s['n']} 份报告："
              f"过期 {len(s['stale'])} · 无溯源 {len(s['unknown'])} · "
              f"新鲜 {len(s['fresh'])}\n")
        for r in s["rows"]:
            mark = {"stale": "🔴 过期", "unknown": "⚠️ 无溯源",
                    "fresh": "🟢 新鲜"}[r["verdict"]]
            print(f"{mark}  {r['report']}")
            if r["verdict"] == "stale":
                print(f"          {r['reason']}")
                for c in r["commits"][:5]:
                    print(f"            · {c}")
            elif r["verdict"] == "unknown":
                print(f"          {r['reason']}")
    # **stale 非零，unknown 也非零。**
    # unknown 让 CI 变红看起来很烦，但「不知道」被当成「没事」才是真事故——
    # 2026-10-14 我就是这么引用了一份已经作废的报告。
    return 2 if s["stale"] else (3 if s["unknown"] else 0)


if __name__ == "__main__":
    raise SystemExit(_main())
