"""第十轮（2026-10-14 08:50）灌两条：跌腿闸（修正产品承诺的覆盖缺口）+ 变异测试崩溃安全。

幂等——标题已存在就跳过。

## 这一轮的性质

不是「找到了更强的信号」，而是**给一个此前没人检查过的产品承诺补上闸**。

产品每天推「10 涨 10 跌」。晋级链有七道闸，逐条看过之后发现：
`excess_t` 测涨腿、股票池闸看覆盖面、方向闸看污染、
显著性闸校正比较次数——**没有任何一道闸看跌腿**。
`breadth_tstat` 这个函数早就写好了，文档里还写着它回答
「排序两端有没有真实区分度」，但它从来没接进闸门。

更要紧的是它当场就抓到了东西：现任权重的跌腿
`avg_down_return = +0.126%/日`——它推的「10 只��跌」平均每天**在涨**。

## 一个必须一起记的反直觉结论

第一版按「跑输大盘」设闸，是**问错了问题**。实测：

    hit_rate_down > 50%            97 / 97   ← 「会不会跌」几乎恒真
    down_excess_vs_market > 0      45 / 97   ← 「跑赢大盘」约等于随机

5 万本金做不了空，用户要的是「这 10 只别买」，不是「它们比大盘跌得多」。
按后者设闸会否掉 52/97 的典型候选——**否掉多数的闸没有判别力**。

最终判据 `avg_down_return < 0`：它是一条**契约闸**，不是排序闸。
它抓不了「谁更好」，但它抓得住「明目张胆地违约」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aplus.ledger import ledger_path, load_ledger, record_improvement  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
D = "2026-10-14"

ENTRY_GATE = dict(
    title="晋级链新增跌腿闸：七道闸里终于有一道看「10 跌」（并把广度警告写进理由）",
    category="correctness", severity="high",
    metric="晋级链中被检验的产品承诺占比", higher_is_better=True,
    before=0.5, after=1.0,
    note=("产品承诺是「10 涨 10 跌」，七道闸里 `excess_t` 只测涨腿，"
          "跌腿无任何闸——`breadth_tstat` 写好了却没接进去。\n\n"
          "实测证据（`scripts/candidate_block_audit.py`，7.8 年 / 测试段 753 日）：\n\n"
          "    候选 {mom20:-1.0} K=63   涨腿 t=+4.757（11/11 块全正）\n"
          "                             跌腿相对大盘 t=-0.865（4/11 正）\n"
          "                             广度   t=+1.005  < Bonferroni 3.47\n"
          "    现任权重 K=1             avg_down_return = **+0.126%/日**\n\n"
          "**现任推的「10 跌」平均每天在涨**，而判决词对此一言不发。\n\n"
          "判据选 `avg_down_return < 0`（绝对下跌）而不是「跑输大盘」，\n"
          "因为实测 `down_excess_vs_market > 0` 只有 45/97 ≈ 随机，\n"
          "按它设闸会否掉 52/97 的典型候选。5 万本金做不了空，\n"
          "用户要证的是「这 10 只别买」。\n\n"
          "晋级理由同步：广度不显著时挂警告（**不否决**，\n"
          "为了次要特征否掉真 alpha 是另一种错，但必须说出来）。\n\n"
          "真实赛马表回放：判决结论不变（K=63 的 avg_down_return=-0.040%，"
          "过闸），但理由里第一次出现广度警告。"),
    invalidates=["「七道闸已经覆盖产品的全部承诺」——2026-10-14 之前成立，"
                 "实测七道闸里没有一道看跌腿"],
    evidence="data/reports/candidate_block_audit.json；"
             "tests/test_evolve_race.py::test_oos_decision_blocks_candidate_whose_down_leg_rises；"
             "scripts/candidate_block_audit.py",
)

ENTRY_RESTORE = dict(
    title="变异测试加崩溃安全：SIGTERM 曾把源文件永久留在变异态（IC 符号写死成 +1）",
    category="correctness", severity="critical",
    metric="变异测试被中断后源文件的可恢复性", higher_is_better=True,
    before=0.0, after=1.0,
    note=("今天用 `task_stop` 掐掉了跑到一半的变异测试。还原写在 `finally` 里，"
          "**`finally` 挡得住 Python 异常，挡不住 SIGTERM**——\n"
          "`scripts/ic_guided_race.py` 停在了变异体版本：\n\n"
          "    emit(\"ic_sign_all\", {f: 1.0 for f in usable})   ← 变异体\n\n"
          "那个文件在变异态下**报告一切正常**：不报错、测试不红、赛马照跑。\n"
          "只是「让符号来自数据」这个脚本存在的唯一理由被摘掉了。\n"
          "`aplus` 不在 git 版本控制下，没有任何兜底能找回来。\n\n"
          "**发现它纯属侥幸**：下一轮变异测试报「源串未命中」。\n\n"
          "修复（两道保险）：\n"
          "  1. SIGTERM/SIGINT 处理器：先还原在途源码再退出\n"
          "  2. 改动前把原件**落盘**到 `.mutation_backup/`，"
          "下次启动发现残留就自动还原——连 SIGKILL 也救得回来\n\n"
          "`tests/test_mutation_restore.py` 5 条测试钉死，"
          "含一条端到端「任何『无法安放』都算失败」。"),
    invalidates=["「变异测试跑完源码一定回到原样」——2026-10-14 之前成立，"
                 "被外部信号打断时源码会停在变异态"],
    evidence="tests/test_mutation_restore.py；"
             "scripts/mutation_check.py::_recover_pending / _install_signal_guards",
)


def main() -> int:
    for entry in (ENTRY_GATE, ENTRY_RESTORE):
        ledger = load_ledger(ROOT / "data")
        if entry["title"] in {r.get("title") for r in ledger}:
            print(f"[跳过 ] {entry['title']}（台账里已有）")
            continue
        row = record_improvement(ROOT / "data", dict(entry, date=D))
        print(f"[{row['direction']:>6}] {entry['title']}\n"
              f"         {entry['metric']}: {entry['before']} → {entry['after']}")
    total = load_ledger(ROOT / "data")
    print(f"\n台账现有 {len(total)} 条 → {ledger_path(ROOT / 'data')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
