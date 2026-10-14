"""把候选冻结进前瞻台账（2026-10-12）。

**在看到任何前瞻数据之前**登记——这是它成立的全部前提。
登记之后只能追加新批次，不能改这一批。

登记日期取**今天**：注册日当天的行情我已经看得到
（今天 18:30 出单用的就是它），所以前瞻窗口从 10-13 起算。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aplus.forward import register  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "data" / "reports"
ON = "2026-10-12"

CHAMP = {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5}
CANDS = [
    # 基准：现任。它在 3 段互不重叠窗口上全部显著为负（t −2.83 / −7.56 / −3.55），
    # 已被证伪。放进台账是为了让前瞻验证能算出「它相对新候选差多少」，
    # 而不是拿新候选去跟一段记忆里的数字比。
    {"weights": CHAMP, "rebalance_every": 1, "buffer_multiple": None},

    # 全历史赛马唯一过五道闸的候选。广度 t=+2.90 为正——
    # 与现任在低频下「超额为正但广度为负」（那是 beta）不同，这是真排序能力。
    {"weights": {"mom20": 1.0, "rev5": 0.3, "vol20": 0.0, "vsur": 0.0, "turn20": -1.0},
     "rebalance_every": 63, "buffer_multiple": None},

    # 独立验证（2019-2023）里 t 最高的两个反转候选。
    # 它们在 7.8 年赛马中被**第六道闸拦下**（股票池窄 9.2%），
    # 而那个窄化是 turnratio 列缺失造成的——今天已补到 98.8%。
    {"weights": {"mom20": -0.5, "turnratio": -0.5},
     "rebalance_every": 21, "buffer_multiple": None},
    {"weights": {"mom20": -0.5, "turnratio": -0.5},
     "rebalance_every": 5, "buffer_multiple": None},

    # 已解除方向污染的纯 20 日反转（DIRECTION_VERDICTS 有独立验证记录）。
    {"weights": {"mom20": -1.0}, "rebalance_every": 1, "buffer_multiple": None},

    # 反转 + 低换手，K=5：实测成本 98.2% → 78.1%（加缓冲），是缓冲唯一真正起作用的档位。
    {"weights": {"mom20": -1.0}, "rebalance_every": 5, "buffer_multiple": 3.0},
]

rec = register(
    REPORTS, ON, CANDS,
    note=("第一批前瞻验证候选。**在看到任何 2026-10-13 之后的行情之前**冻结。\n"
          "候选来自三处已完成的检验：2019-2023 独立验证、全历史 7.8 年赛马、"
          "以及现任基准。6 组 → Bonferroni 临界 2.63，远低于 84 组赛马的 3.43，"
          "门槛更紧、偶然过关更少。"),
    evidence=("race_composite_full_7y.json / race_composite_v1.json / "
              "independent_validation_2019_2023.json / frequency_block_t_2019_2023.json"),
)
print(f"已登记 {len(rec['candidates'])} 组 · 注册日 {rec['registered_on']}")
for c in rec["candidates"]:
    print(f"  K={c['rebalance_every']:<3} buf={str(c['buffer_multiple']):<5} {c['weights']}")
