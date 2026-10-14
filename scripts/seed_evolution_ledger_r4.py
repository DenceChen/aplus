"""第四轮（2026-10-12 晚）灌两条**方法论**改进进台账。

幂等——标题已存在就跳过，可以反复执行。

## 为什么这两条不属于「让指标变好」

r3 那一轮全在补数据缺口，指标要么变差（体检翻红）要么持平。
这一轮的两条**一个指标都不碰**：

1. **末根元数据补齐** —— `turn` 末根可用率 62.0% → 95.6%，
   `turnratio` 38% → 98.8%。这不是「策略变强了」，而是
   **两个候选从此可以被公平比较**。它直接决定 7.8 年赛马里
   那两个 `turnratio` 候选会不会再被「股票池闸」以数据问题为由拦下。
2. **前瞻验证台账建立** —— 指标一个都没有，因为它测的是**未来**。
   它换来的东西是「注册即冻结」这条纪律从此可执行。

## 记录前者是为了让后者可被检验

台账里如果只有「修好了数据」，下一个看台账的人无法判断
「turnratio 候选到底有没有用」——他会以为那是已知结论。
必须把「当时的阻塞是数据、不是策略」这句话留在档上，
`turnratio` 的判决才有前提。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-12"

ENTRIES = [
    dict(
        title="末根元数据补齐：turn 可用率 62.0%→95.6%，turnratio 38%→98.8%",
        category="data_quality", severity="high",
        metric="末根元数据可用率（turn / turn20 / turn252 / turnratio）",
        higher_is_better=True,
        before=0.620, after=0.956,
        note=("末根是**昨天**的 K 线，也是实盘真正读到的那一根。"
              "它缺 `turn` 时，`turn20`/`turnratio` 在最新一日恒为 NaN，"
              "而 `composite_score` 用 `min_count=len(weights)` —— "
              "任一因子 NaN → 合成分 NaN → 该股直接掉出排名。\n\n"
              "实测（1200 只随机样本）：`turn` 62.0%→**95.6%**、"
              "`turnratio` 38%→**98.8%**、`turn20` **98.8%**、"
              "`turn252` 99.9%。修复脚本 `scripts/repair_tail_cols.py` "
              "一轮 fixed=2140 / unresolved=961，961 只全部 `degraded` "
              "（baostock 会话在第 2141 只断开，已加熔断）。\n\n"
              "**剩余 4.7% 不是 bug**：那些末根日期停在 2021–2024 的，"
              "是退市与长期停牌股。当前交易日全池只 1 只缺 `turn`，"
              "对可交易标的已基本闭合。\n\n"
              "这条的真正作用：`turnratio` 的两个候选曾因「股票池窄 9.2%」"
              "被第六道闸拦下——**那窄化来自数据缺失，不是策略缺陷**。"
              "补齐之后它们必须重跑赛马，否则会一直挂着一个已经作废的理由。"),
        invalidates=["「turnratio 候选被股票池闸拦下 = 该方向无效」——2026-10-12 之前成立，"
                     "当天起该拦下理由作废，须以补齐后的数据重跑"],
        evidence="scripts/repair_tail_cols.py（fixed=2140 / unresolved=961）；"
                 "tests/test_repair_tail_cols_breaker.py；"
                 "data/reports/coverage 末根统计（1200 只随机样本）",
    ),
    dict(
        title="前瞻验证台账：6 组候选在看到任何未来行情之前冻结",
        category="methodology", severity="high",
        metric="前瞻验证已注册的候选组数（窗口天数当前为 0）",
        higher_is_better=True,
        before=0, after=6,
        note=("此前所有「裁决」都发生在**同一段历史**上：训练段选权重、"
              "测试段验收，而测试段的数据在选权重时已经看过了。"
              "再严的隔离带也拦不住「我已经知道 2024 年发生了什么」。\n\n"
              "新增 `aplus/forward.py`（追加式台账）+ "
              "`scripts/register_forward_candidates.py` + "
              "`scripts/forward_validate.py`，三条硬规则：\n\n"
              "1. **注册即冻结**——不能删、不能改、不能加；要试新候选"
              "必须另开一份注册（否则「多试几次直到过关」无法与"
              "「一次就中」区分）。\n"
              "2. **窗口严格晚于注册日 + 隔离带**"
              f"（`FORWARD_EMBARGO_DAYS=2`）。\n"
              "3. **候选集越小门槛越紧**——6 组 → Bonferroni 临界 2.63，"
              "远低于 84 组赛马的 3.43。\n\n"
              "6 组已冻结于 2026-10-12，窗口自 2026-10-13 起算，"
              "其中含 7.8 年赛马唯一过五道闸的 "
              "`{mom20:1.0, rev5:0.3, turn20:-1.0}` K=63。\n\n"
              "**当前状态：窗口 0 天。** `forward_validate.py` "
              "如实报告「样本不足」而不是给一个看起来能用的 t 值——"
              "K=63 需 63×3 = 189 个交易日（约 9 个月）才够判决力。"
              "这是目前**唯一干净的晋级路径**，而它需要时间，"
              "无法用更多计算绕过。"),
        invalidates=["「样本外测试段通过 = 该候选可用」——2026-10-12 起降级为"
                     "「值得进入前瞻验证」，不再直接构成晋级依据"],
        evidence="aplus/forward.py；tests/test_forward_registry.py（13 项）；"
                 "data/reports/forward_registry.json（1 份注册 / 6 组候选）",
    ),
]


def main() -> int:
    existing = {r.get("title") for r in load_ledger(ROOT / "data")}
    added = 0
    for e in ENTRIES:
        if e["title"] in existing:
            print(f"[跳过 ] {e['title']}（台账里已有）")
            continue
        row = record_improvement(ROOT / "data", dict(e, date=D))
        added += 1
        print(f"[{row['direction']:>6}] {e['title']}\n"
              f"         {e['metric']}: {e['before']} → {e['after']}")
    print(f"\n新增 {added} 条 · 台账现有 {len(load_ledger(ROOT / 'data'))} 条 → "
          f"{ledger_path(ROOT / 'data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())