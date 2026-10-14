"""研究员端到端冒烟：真实 minimax 直连跑一次 → 看解析链路通不通。

设计意图：mcode 单元测试用 fake 双链路全过，但没有任何测试用真链路验过。
这一脚本用真实 minimax 跑一次研究员的"提候选权重"任务，确认：

  ① 默认直连链（minimax 主、glm 备）能起来
  ② 真实模型响应能进入 parse_candidates 的解析路径
  ③ validate_candidates 能从真响应里挑出合法候选
  ④ 整个链路对 prompt 模板的契约（JSON 数组）是守得住的

跑完会打印一段诊断摘要（候选数 / 失败原因），不写任何文件。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.evolve_researcher import (
    build_research_prompt,
    research_candidates,
)
from aplus.llm import ClaudePClient, default_llm_chain, load_secrets


EVIDENCE = {
    "current_weights": {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5, "vsur": 0.5},
    "baseline_stats": {"overall_hit_rate": 0.445, "hit_rate_up": 0.451,
                       "hit_rate_down": 0.438, "up_excess_vs_market": -0.0063,
                       "judged": 1253},
    "best_challenger": {"weights": {"mom20": 1.0, "rev5": 0.3, "vol20": 0.5,
                                     "vsur": 0.5, "turn20": -0.5},
                        "stats": {"overall_hit_rate": 0.512,
                                  "up_excess_vs_market": -0.0061}},
    "market_summary": "2026-07 至 10 月市场震荡，涨组合平均跑输市场 0.6%/日",
}


def _section(title: str) -> None:
    print(f"\n── {title} ──")


def main() -> int:
    print("== 研究员端到端冒烟 ==")
    secrets = load_secrets(Path.home() / ".aplus" / "llm.json")
    print(f"配置：primary={secrets.get('primary', 'glm')!r} · "
          f"researcher={secrets.get('researcher', 'claude-p')!r}")

    _section("0. 打印真实 prompt 头 30 行（人眼能看出契约清晰度）")
    prompt_text = build_research_prompt(EVIDENCE)
    print("\n".join(prompt_text.splitlines()[:30]))
    print(f"...（共 {len(prompt_text.splitlines())} 行）")

    _section("1. 直连链跑一次（minimax 主 / glm 备）")
    chain = default_llm_chain()
    print(f"链形态：{type(chain).__name__} · "
          f"primary={getattr(chain.primary, 'name', '?')} · "
          f"backup={getattr(chain.backup, 'name', '?')}")
    print("调用 research_candidates（这一步会真打到 minimax）...", flush=True)
    raw = research_candidates(EVIDENCE, claude_runner=None, direct=chain)
    print(f"研究员返回 {len(raw)} 组候选（经 validate）")
    for c in raw[:5]:
        print(f"  - {c}")

    _section("2. 假如用 claude -p 跑一次（验证子进程链路）")
    pclient = ClaudePClient(str(ROOT))
    try:
        raw2 = research_candidates(EVIDENCE, claude_runner=pclient.chat, direct=None)
        print(f"claude -p 返回 {len(raw2)} 组候选")
        for c in raw2[:5]:
            print(f"  - {c}")
    except Exception as e:
        print(f"claude -p 链路失败（不一定有 bug，子进程链路常挂）：{type(e).__name__}: {e}")

    _section("3. 总结")
    print(f"直连链：{'✅' if raw else '❌ 0 组（建议调 prompt 或放宽契约）'}")
    return 0 if raw else 1


if __name__ == "__main__":
    sys.exit(main())
