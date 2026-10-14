"""前瞻验证批次的**可用性**——证据作废，但批次本身不作废。

## 为什么这个脚本存在

2026-10-14 的基准污染审计查实：三批前瞻候选的 `evidence` 字段
所引用的报告，**全部**生成于污染修复（`3c846ff` @ 14:02）之前，
其 `up_excess_vs_market` 被系统性虚增约 115 个百分点/年。

于是注册表里出现了一个危险的状态：
**批次本身完全有效，但产生它的证据已经作废。**

## 为什么撤销是错的

四条理由，按重要性排：

1. **前瞻有效性不依赖历史 t。** 候选能否通过，看的是冻结之后
   2026-10-15 之后的真实行情，不是它当初凭什么被选中。
2. **问题是覆盖不是有效性。** 撤销会丢掉三批候选里可能真的有东西的部分；
   保留则下一批修正基准后的候选可以**增量**加进来（批次4）。
3. **零观测，不构成偷看。** `forward_results.json` 是空数组，
   任何加法都不可能泄漏未来。
4. **破 `sealed_sha256` 去写注记 = 作弊的外观。**
   哪怕本意良好，改封的动作本身就会让「冻结」这件事失去意义。

所以本脚本**只读不写**，且把三件事分开报：
**证据作废 / 批次有效 / 判决何时开始可能**。

## 怎么用

    python scripts/forward_batch_status.py

退出码：0 = 全部批次证据可引用；2 = 有批次证据作废或判不了。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_report_contamination import audit  # noqa: E402

REGISTRY = ROOT / "data" / "reports" / "forward_registry.json"
RESULTS = ROOT / "data" / "reports" / "forward_results.json"
FORWARD_EMBARGO_DAYS = 2


def evidence_state(report_names: list[str], rep: dict) -> dict:
    """一批候选的证据现在是干净、作废、还是判不了。"""
    by_name = {r["report"]: r for r in
               rep["contaminated"] + rep["clean"] + rep["unverified"]}
    out = {}
    for n in report_names:
        r = by_name.get(n)
        if r is None:
            out[n] = "unverified"          # 审计压根没看见 → 判不了，不是没事
        elif r["mtime"] < rep["cutoff_ts"]:
            out[n] = "contaminated"
        elif r.get("panel_market_mean") is not None:
            out[n] = "clean"
        else:
            out[n] = "unverified"
    return out


def verdict_start(registered_on: str, embargo: int = FORWARD_EMBARGO_DAYS) -> str:
    """最早可验证的前瞻日 = 注册日之后 embargo 天。

    embargo 是为了避开「注册当日/次日」这类结算与复权的时点噪声，
    它**不是**为了让窗口更晚、更难验证——不许放松。
    """
    d = _dt.date.fromisoformat(registered_on) + _dt.timedelta(days=embargo + 1)
    return d.isoformat()


def load(path: Path = REGISTRY) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def status(registry: list[dict] | None = None, reports_dir: Path | None = None,
           root: Path = ROOT, rep: dict | None = None) -> dict:
    registry = registry if registry is not None else load()
    reports_dir = reports_dir or (root / "data" / "reports")
    # `rep` 可注入：**「证据作废但批次有效」是立场，不该只能靠
    # 在真实目录里凑出一份被污染的报告来验证。**
    rep = rep if rep is not None else audit(reports_dir, root=root)

    batches = []
    for i, b in enumerate(registry if isinstance(registry, list) else [], 1):
        ev = b.get("evidence", "") or ""
        names = [t.strip("()（）,，。/ ") for t in
                 ev.replace("（", " ").replace("）", " ").split()
                 if t.strip("()（）,，。/ ").endswith(".json")]
        st = evidence_state(names, rep)
        reg = b.get("registered_on", "?")
        # `None` 而不是 `""`：**无 seal 与 seal 是空串，处置完全不同**
        # （前者更弱——那批候选根本没被冻结过）。压成空串会把两者混同。
        _seal = b.get("sealed_sha256")
        batches.append({
            "batch": i, "registered_on": reg,
            "sealed_sha256": _seal[:12] if _seal else None,
            "n_candidates": len(b.get("candidates") or []),
            "evidence": st,
            "evidence_verdict": ("clean" if all(v == "clean" for v in st.values())
                                 else "contaminated" if "contaminated" in st.values()
                                 else "unverified"),
            # **批次有效**：前瞻有效性不依赖历史 t，且零观测。
            "batch_valid": True,
            "earliest_verifiable_day": verdict_start(reg)
            if reg != "?" else "?",
        })

    n_obs = 0
    if RESULTS.is_file():
        try:
            n_obs = len(json.loads(RESULTS.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            n_obs = -1
    return {"batches": batches,
            "n_forward_observations": n_obs,
            "fix_commit": rep["fix_commit"],
            "cutoff_ts": rep["cutoff_ts"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="前瞻验证批次可用性")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        rep = status()
    except RuntimeError as e:
        print(f"无法判定：{e}", file=sys.stderr)
        return 3

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 2 if any(b["evidence_verdict"] != "clean" for b in rep["batches"]) else 0

    t = _dt.datetime.fromtimestamp(rep["cutoff_ts"]).strftime("%m-%d %H:%M")
    print(f"基准污染修复 {rep['fix_commit']}（{t}）\n")
    for b in rep["batches"]:
        mark = {"clean": "✅", "contaminated": "🔴", "unverified": "🟡"}[
            b["evidence_verdict"]]
        print(f"{mark} 批次{b['batch']}（注册 {b['registered_on']}，"
              f"{b['n_candidates']} 组，seal {b['sealed_sha256']}…）")
        print(f"   证据：{b['evidence_verdict']}"
              f" · 批次本身：{'有效' if b['batch_valid'] else '作废'}"
              f" · 最早可验证日 {b['earliest_verifiable_day']}")
        for n, v in b["evidence"].items():
            if v != "clean":
                print(f"      · {n} —— {v}")
    print(f"\n前瞻观测数：{rep['n_forward_observations']}"
          f"（0 = 任何判定都还没到期，说什么都太早）")
    print("\n批次有效 ≠ 证据可引用。前瞻有效性不依赖历史 t，"
          "所以不撤销批次；\n但被污染的报告不足以支撑下一批的选取依据——"
          "修正后的候选应**增量**注册为批次4。")
    return 2 if any(b["evidence_verdict"] != "clean" for b in rep["batches"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
