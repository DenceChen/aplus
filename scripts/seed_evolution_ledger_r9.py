"""第九轮（2026-10-13 07:50）灌一条 **flat**：股票池闸拦下的差异，实测不改变任何判决。

幂等——标题已存在就跳过。

## 这条的性质

它不是「修好了什么」，也不是「变差了什么」，而是
**把一个一直靠猜的地方变成了有实测的地方**。

闸门在过去两天里扮演了一个尴尬角色：它拦下了 `{mom20:-0.5, turnratio:-0.5}`
（测试 t=+4.35/+5.04，**比过闸赢家的 +4.14 还高**），
理由写的是「数据缺失导致系统性窄化」——而 `FINDINGS` §3.7 已经确认
`turn` 历史数据完整，那一栏的**理由是错的**，代价却是真的。

今天把「代价是真的」这件事量化了：6.5% 的收窄不改变任何闸门判决。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-13"

ENTRY = dict(
    title="隔离实验：6.5% 的池子收窄不改变任何一个闸门判决（闸门阈值不是从证据来的）",
    category="correctness", severity="high",
    metric="宽池 vs 窄池测试超额的相对差（中位数）", higher_is_better=False,
    before=0.06, after=0.06,
    note=("`composite_score` 的 `min_count=len(weights)` 按**键数**算，"
          "于是 `{mom20:-1.0, turnratio:0.0}` 与 `{mom20:-1.0}` 的排名逻辑逐位相同、"
          "权重完全相同，唯一变量是 turnratio 非空带来的股票池收缩。"
          "零权重扰动的纯池子隔离，不需要改 `filter_universe`。\n\n"
          "实测（2019-01~2026-10，训练 1124 日 / 测试 753 日）：\n\n"
          "    K=63  宽池 +0.1265%  窄池 +0.1221%   t 4.76 → 4.20   **都过**\n"
          "    K=21  宽池 +0.1329%  窄池 +0.1325%   t 4.36 → 3.85   **都过**\n"
          "    K=5   宽池 +0.1466%  窄池 +0.1338%   t 2.87 → 2.51   都不过\n"
          "    K=1   宽池 +0.0988%  窄池 +0.0917%   t 1.83 → 1.73   都不过\n\n"
          "**四个档位，两个池子的判决完全一致。** 闸门在这四档上拦下的\n"
          "是一个不改变任何结论的差异。窄池从未更好，方向一律略低（0.91~1.00 倍）。\n\n"
          "顺带量到：`mom20` K=63 现在 t=**4.76**，而昨天赛马报告里是 **4.14**，\n"
          "唯一差别是 `panel.py` 复牌压缩修复落地时间（赛马 22:12 起、修 22:19）。\n"
          "**方向一致、量级同阶**，印证了昨天「影响中位数 1.000、结论不受影响」的记录。\n\n"
          "**处置：仍然不改闸门。** 但「前提不成立」现在变成了一个明确的待决形式——\n"
          "闸门现在是一个**固定百分比阈值**，而实测显示 6.5% 不改变判决，"
          "说明这个阈值不是从证据来的。该改成按「池子差异是否改变结论」判定。\n"
          "在那个新判据被前瞻窗口检验过之前，不动它。"),
    invalidates=["「股票池闸的百分比阈值来自证据」——2026-10-13 起不成立，"
                 "实测显示 6.5% 的收窄不改变任何闸门判决"],
    evidence="data/reports/universe_isolation_7y.json（4 档 × 双池 · 443s）；"
             "scripts/universe_isolation.py",
)


def main() -> int:
    existing = {r.get("title") for r in load_ledger(ROOT / "data")}
    if ENTRY["title"] in existing:
        print(f"[跳过 ] {ENTRY['title']}（台账里已有）")
        return 0
    row = record_improvement(ROOT / "data", dict(ENTRY, date=D))
    print(f"[{row['direction']:>6}] {ENTRY['title']}\n"
          f"         {ENTRY['metric']}: {ENTRY['before']} → {ENTRY['after']}")
    print(f"\n台账现有 {len(load_ledger(ROOT / 'data'))} 条 → {ledger_path(ROOT / 'data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())