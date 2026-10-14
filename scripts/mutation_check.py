"""变异测试：证明防护测试真的能杀人。

## 为什么需要它

绿测试不说明任何事。一条断言 `assert sd <= 0` 的测试，
在实现被改成 `assert sd <= 1e-9` 时**照样绿**——因为根本没测到那个形状。
反过来，「变异体全绿」通常意味着**变异体写成了 no-op**，
不是测试宽容。这一点在本项目已经吃过两次亏（见 `docs/MISTAKE_LOG_2026-10-11.md`）。

所以每个变异体都必须满足两件事：

1. 目标测试**失败**（被杀 = 变异体确实改变了行为）
2. 变异体**真的改了源码**（apply 后文件内容与原始不同）

## 防止自己骗自己

- `PYTHONDONTWRITEBYTECODE=1`：`.pyc` 缓存曾导致「还原源码但运行时还是旧的」，
  一度让 `center=True` 残留了三轮迭代（事故记录第 12 条）
- 还原时先删 `__pycache__` 再 `shutil.copy2`，顺序反了缓存就赢了
- 每个变异体单独跑一个 pytest 进程，不共享解释器状态
- 任一步异常都要保证源码被还原（`finally`）

用法：
    .venv/bin/python scripts/mutation_check.py
    .venv/bin/python scripts/mutation_check.py --only block_tstat
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (组名, 文件, 旧串, 新串, 说明, 目标测试)
MUTATIONS: list[tuple[str, str, str, str, str, list[str]]] = [
    # ── 分块 t 检验 ──────────────────────────────────────────────
    ("block_tstat", "aplus/evolve_race.py",
     "    n = (len(s) // b) * b\n",
     "    n = len(s)\n",
     "尾部零头不丢弃（不满的块均值方差更大，会污染标准差）",
     ["tests/test_block_tstat.py::test_incomplete_trailing_block_is_dropped"]),

    ("block_tstat", "aplus/evolve_race.py",
     "    return s.to_numpy()[:n].reshape(-1, b).mean(axis=1)\n",
     "    a = s.to_numpy()\n"
     "    return np.convolve(a, np.ones(b) / b, mode='valid')[:max(1, len(a) - b + 1)]\n",
     "改用重叠块（步长 1）——相邻块共享 b-1 天数据，同样制造假样本量",
     ["tests/test_block_tstat.py"]),

    ("block_tstat", "aplus/evolve_race.py",
     "    if not math.isfinite(sd) or sd <= 0 or sd <= abs(mu) * DEGENERATE_CV:\n",
     "    if not math.isfinite(sd) or sd <= 0:\n",
     "删掉变异系数护栏，退化为 2026-10-11 那个 t=3.1e16 的事故",
     ["tests/test_block_tstat.py::test_constant_series_returns_none_not_a_huge_t",
      "tests/test_block_tstat.py::test_near_constant_series_is_also_refused"]),

    ("block_tstat", "aplus/evolve_race.py",
     "    return _tstat(daily, block=rebalance_every)\n",
     "    return _tstat(daily, block=1)\n",
     "频率参数被吞掉——低频候选继续用虚高的逐日 t",
     ["tests/test_block_tstat.py::test_excess_tstat_forwards_frequency_to_blocking"]),

    # ── 复合候选 ────────────────────────────────────────────────
    ("composite", "aplus/evolve.py",
     "        buf = candidate.get(\"buffer_multiple\")\n",
     "        buf = candidate.get(\"buffer_multiple\", 3.0)\n",
     "复合候选的缓冲被静默默认开启——会改写所有历史结论且无人察觉",
     ["tests/test_composite_candidates.py"]),

    ("composite", "aplus/evolve.py",
     "    return (tuple(sorted((k, float(v)) for k, v in w.items())), int(every),\n"
     "            None if buf is None else round(float(buf), 9))\n",
     "    return (tuple(sorted((k, float(v)) for k, v in w.items())), 1, None)\n",
     "去重只认权重——把日频与月频当成同一组，抹掉 276 个百分点的差距",
     ["tests/test_composite_candidates.py::test_frequency_and_buffer_candidates_are_deduplicated_by_all_three_fields"]),

    ("composite", "aplus/evolve_race.py",
     "        if not wide and (every != 1 or buf is not None):\n",
     "        if False:\n",
     "带频率的候选遇 5 参 evaluate 时静默忽略频率（按日频跑却看不出来）",
     ["tests/test_race_composite.py::test_legacy_evaluate_rejects_frequency_candidate_loudly",
      "tests/test_race_composite.py::test_legacy_evaluate_rejects_buffer_candidate_loudly"]),

    ("composite", "aplus/evolve_race.py",
     "    champ = next((r for r in table if _row_key(r) == champ_key), None)\n",
     "    champ = next((r for r in table if r[\"weights\"] == champion_weights), None)\n",
     "现任只按权重认——同权重的月频行会冒名顶替日频现任",
     ["tests/test_race_composite.py"]),

    ("composite", "aplus/evolve_race.py",
     "    if _row_key(best) == champ_key:\n",
     "    if best[\"weights\"] == champion_weights:\n",
     "「最优仍是现任」只看权重——现任换个频率就被判成没变化",
     ["tests/test_race_composite.py"]),

    # ── 全表扫描（裁决层）────────────────────────────────────────
    ("scan", "aplus/evolve_race.py",
     "    for cand in table:\n        if _row_key(cand) == champ_key:\n"
     "            continue                      # 现任不能「晋级成自己」\n",
     "    for cand in table[:1]:\n",
     "只看训练段第 1 名——用全表的 Bonferroni 门槛去检验 1 组，被校正的比较大半没发生",
     ["tests/test_decision_scan.py::test_second_place_is_promoted_when_first_fails_significance",
      "tests/test_decision_scan.py::test_examined_count_never_exceeds_table_size"]),

    ("scan", "aplus/evolve_race.py",
     "        if _row_key(cand) == champ_key:\n            continue",
     "        if False:\n            continue",
     "不跳过现任——现任可能被判成「晋级成自己」",
     ["tests/test_decision_scan.py::test_champion_is_never_promoted_to_itself"]),

    ("scan", "aplus/evolve_race.py",
     "        if verdict.get(\"promote\"):\n            verdict[\"examined\"] = examined\n"
     "            return verdict\n",
     "        if verdict.get(\"promote\"):\n            return verdict\n",
     "过了不停——扫描退化成「扫完全表再返回」，选出来的不是第一个全过的",
     ["tests/test_decision_scan.py::test_scan_stops_at_the_first_candidate_that_passes"]),

    ("scan", "aplus/evolve_race.py",
     "    n_cmp = int(significance_lookup) if significance_lookup else len(table)\n",
     "    n_cmp = int(significance_lookup) if significance_lookup else 1\n",
     "Bonferroni 只按 1 组算——扫描放宽挑选面后门槛必须同步收紧，"
     "这里放宽等于给假阳性开门",
     ["tests/test_decision_scan.py::test_bonferroni_threshold_is_still_over_the_whole_table"]),

    # ── 实盘持仓状态机 ────────────────────────────────────────────
    # 注意：`is_due` 里那道 `if every <= 1: return True` **是真冗余**——
    # 通式 `days_since + 1 >= every` 在 every=1 时本就恒真，删掉它行为不变。
    # 拿它当变异体会得到一个「文本变了、效果是 no-op」的僵尸，
    # 只会污染存活率。故改为对通式做差一变异。
    ("live", "aplus/live_rebalance.py",
     "    return int(state.get(\"days_since_rebalance\", 0)) + 1 >= every\n",
     "    return int(state.get(\"days_since_rebalance\", 0)) + 1 >= every + 1\n",
     "调仓日差一天（通式多算一次）——月频会晚一天换手，实盘与回测错开",
     ["tests/test_live_rebalance_state.py::test_monthly_is_due_on_the_21st_day",
      "tests/test_live_rebalance_state.py::test_monthly_is_not_due_one_day_after_rebalance"]),

    ("live", "aplus/live_rebalance.py",
     "    if int(state.get(\"every\", 1)) != every:\n        return True\n",
     "",
     "配置变了不强制建仓——拿一套策略挑的持仓去跑另一套策略",
     ["tests/test_live_rebalance_state.py::test_frequency_change_forces_a_fresh_rebalance"]),

    ("live", "aplus/live_rebalance.py",
     "    every = max(int(every or 1), 1)\n    if every <= 1:\n        return \"\"\n",
     "    every = max(int(every or 1), 1)\n",
     "日频也打印持仓指令——凭空多出一段话，日频行为不再与今天之前逐字相同",
     ["tests/test_live_rebalance_state.py::test_daily_instruction_has_no_hold_section"]),

    ("live", "aplus/live_rebalance.py",
     "    except (json.JSONDecodeError, OSError, UnicodeDecodeError):\n        return None\n",
     "    except (json.JSONDecodeError, OSError, UnicodeDecodeError):\n        return {}\n",
     "坏状态返回空 dict 而不是 None——调用方拿到假状态，会按不存在的持仓算换手",
     ["tests/test_live_rebalance_state.py::test_corrupt_state_file_returns_none_not_a_partial"]),

    # ── n_ranked 取样（第六道闸的输入）───────────────────────────
    ("universe", "aplus/backtest.py",
     "    counts = sorted(scoreable_count(cross_sections[d], weights) for d in picks)\n"
     "    return int(counts[len(counts) // 2])\n",
     "    counts = sorted(scoreable_count(cross_sections[d], weights) for d in picks)\n"
     "    return int(sum(counts) / len(counts))\n",
     "中位数改回平均——大量坏日子会把可打分股票数系统性拉低",
     ["tests/test_n_ranked_sampling.py::test_median_not_mean_is_used"]),

    ("universe", "aplus/backtest.py",
     "    days = [d for d in (use_dates or []) if cross_sections.get(d) is not None]\n",
     "    days = [d for d in (use_dates or []) if cross_sections.get(d) is not None]\n"
     "    days = days[:1] or days\n",
     "退回只看第一天——数据起点那天一只都打不了分，报 0",
     ["tests/test_n_ranked_sampling.py::test_first_day_degenerate_does_not_report_zero_ranked"]),

    ("universe", "aplus/backtest.py",
     "    days = [d for d in (use_dates or []) if cross_sections.get(d) is not None]\n",
     "    days = list(use_dates or [])\n",
     "缺失的日子被算成 0 只可打分——把闸门输入拉低",
     ["tests/test_n_ranked_sampling.py::test_missing_day_is_skipped_not_counted_as_zero"]),

    ("live", "aplus/live_rebalance.py",
     "            or data[\"days_since_rebalance\"] < 0:\n        return None\n",
     "        return None\n",
     "计数非负校验被删——负数天数被当成有效持仓，换手会算成负的",
     ["tests/test_live_rebalance_state.py::test_state_with_invalid_counter_is_rejected"]),

    # ── IC 诊断（2026-10-12）──────────────────────────────────────────
    # 这一组的意义特殊：IC 是「因子到底有没有预测力」的唯一测量口径。
    # 它一旦偏乐观，我们会拿一张漂亮的表去改权重——而那张表量的不是同一件事。
    ("ic", "aplus/factor_ic.py",
     "        pos = int(np.searchsorted(dates, after_date, side=\"right\"))\n",
     "        pos = int(np.searchsorted(dates, after_date, side=\"left\"))\n",
     "searchsorted 边界从 right 改成 left——取到的是 D 当天而不是 D 之后第一根，"
     "整个前瞻窗口前移一根 K 线，数据泄露原样回来了（且看起来完全正常）",
     ["tests/test_factor_ic.py"]),

    ("ic", "aplus/factor_ic.py",
     "        if abs(ret) > max_abs_return:\n            continue\n",
     "        if abs(ret) > 1e9:\n            continue\n",
     "不可信价格闸门形同虚设——除权跳空会直接进 IC 序列，"
     "造出凭空的巨大相关性",
     ["tests/test_factor_ic.py::test_implausible_return_excluded"]),

    ("ic", "aplus/factor_ic.py",
     "    if int(ok.sum()) < min_pairs:\n        return None\n",
     "    if int(ok.sum()) < 3:\n        return None\n",
     "样本下限从 MIN_PAIRS 放宽到 3——小样本相关系数能轻松到 0.9，"
     "被当成显著信号",
     ["tests/test_factor_ic.py::test_rank_corr_too_few_pairs_returns_none"]),

    ("ic", "aplus/factor_ic.py",
     "    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))\n"
     "    if denom <= 0:\n        return None\n",
     "    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))\n"
     "    if denom < 0:\n        return None\n",
     "全并列的一侧不再返回 None，而是继续除法——"
     "「测不出来」被静默改写成「相关性为 0」",
     ["tests/test_factor_ic.py::test_rank_corr_constant_input_is_none_not_zero"]),

    ("ic", "aplus/factor_ic.py",
     "    return max(1, exit_off - enter)\n",
     "    return 1\n",
     "t 检验的分块单位被压成 1——hold21 的相邻 IC 共享 20 天数据，"
     "t 会虚高一个量级且不报任何错",
     ["tests/test_ic_guided_candidates.py"]),

    ("ic", "scripts/ic_guided_race.py",
     "    usable = {f: v for f, v in ic.items() if v and v[\"mean\"] is not None}\n",
     "    usable = {f: v for f, v in ic.items() if v}\n",
     "测不出 IC 的因子被默认成正权重——"
     "「我们测不出它，于是赌它正向」，把噪声当信号的那一步",
     ["tests/test_ic_guided_candidates.py::test_ic_entry_with_none_mean_but_populated_dict_is_skipped"]),

    ("ic", "scripts/ic_guided_race.py",
     "    emit(\"ic_sign_all\", {f: 1.0 if v[\"mean\"] > 0 else -1.0\n"
     "                         for f, v in usable.items()})\n",
     "    emit(\"ic_sign_all\", {f: 1.0 for f in usable})\n",
     "符号全部写死成 +1——脚本退化回「随手给个权重」，"
     "而它存在的唯一理由就是让符号来自数据",
     ["tests/test_ic_guided_candidates.py::test_candidates_flip_sign_to_match_ic"]),

    # ── 成本报告（2026-10-12）────────────────────────────────────────
    # 这一组守护的是 5 万本金的死活线：成本吃掉毛超额的几成。
    # 它变弱时报告**依然完整**——所有字段都在，只是那个数开始骗人。
    ("cost", "aplus/verify.py",
     "        cost_share = cost_drag_avg / gross_up_excess\n",
     "        cost_share = min(1.0, cost_drag_avg / gross_up_excess)\n",
     "成本占比被截断在 1.0——「毛超额全被手续费吃光」显示成「刚好 100%」，"
     "而真实值可以是 3 倍、10 倍",
     ["tests/test_cost_reporting.py::test_cost_share_above_one_means_the_edge_is_gone"]),

    ("cost", "aplus/verify.py",
     "    gross_up_excess = up_excess + cost_drag_avg\n",
     "    gross_up_excess = up_excess - cost_drag_avg\n",
     "毛超额算成净超额减成本——成本被扣两遍，毛超额系统性偏低，"
     "成本占比随之虚高",
     ["tests/test_cost_reporting.py::test_gross_excess_is_net_plus_cost",
      "tests/test_cost_reporting.py::test_cost_share_is_the_decision_number"]),

    ("cost", "aplus/verify.py",
     "        cost_share = None                # 毛超额 ≤ 0 → 比值无意义，不给数\n",
     "        cost_share = 0.0                 # 毛超额 ≤ 0 也当成「成本为零」\n",
     "毛超额为负时成本占比被写成 0——「成本吃掉毛超额的几成」"
     "变成「成本什么都没吃」，而实际是毛超额本身就不存在",
     ["tests/test_cost_reporting.py::test_cost_share_is_none_when_gross_excess_is_zero",
      "tests/test_cost_reporting.py::test_cost_share_none_when_gross_excess_is_negative"]),

    ("cost", "aplus/verify.py",
     "    cost_drag_avg = float(up[\"cost\"].mean()) if \"cost\" in up.columns and len(up) else 0.0\n",
     "    cost_drag_avg = float(history[\"cost\"].mean()) if \"cost\" in history.columns else 0.0\n",
     "成本从**整个历史**取平均——跌腿（回避清单，根本没交易）"
     "的 cost=0 也被算进来，成本占比被系统性稀释",
     ["tests/test_cost_reporting.py::test_down_leg_is_still_not_charged"]),

    ("forward", "aplus/forward.py",
     "    return str(latest) > str(registered_on)\n",
     "    return True\n",
     "空窗口也去建面板——`build_panel` 的区间是半开的 [start, end)，"
     "start==end 直接抛异常。定时任务会在攒够样本前每天崩一次，"
     "而没人盯着 stderr",
     ["tests/test_forward_registry.py"]),

    ("forward", "aplus/forward.py",
     "    ds = sorted(d for d in (dates or []) if str(d) > str(registered_on))\n"
     "    return ds[int(embargo_days):]\n",
     "    ds = sorted(d for d in (dates or []) if str(d) >= str(registered_on))\n"
     "    return ds[int(embargo_days):]\n",
     "窗口起点从「严格晚于」放宽成「含」——注册日当天的数据已经被看过"
     "（今天出单的依据就是它），拿它当验证样本等于用答案答自己出的卷",
     ["tests/test_forward_registry.py"]),

    ("forward", "aplus/forward.py",
     "    return ds[int(embargo_days):]\n",
     "    return ds\n",
     "隔离带被摘掉——注册后紧接着的头几天全部并入窗口，"
     "而那些日子里候选的表现正是我们「决定要不要注册它」的依据",
     ["tests/test_forward_registry.py"]),

    # `dedupe` 原本自己抄了一份 `every < 1` 校验，已删除（`split_weights`
    # 已有）。变异体随之改指**真正的那一份**——它现在是唯一防线。
    ("forward", "aplus/evolve.py",
     "    if every < 1:\n        raise ValueError(f\"rebalance_every 至少为 1，收到 {every}\")\n",
     "    if every < 0:\n        raise ValueError(f\"rebalance_every 至少为 1，收到 {every}\")\n",
     "唯一一份 `rebalance_every ≥ 1` 校验被放宽——频率 0 的候选能进前瞻台账，"
     "之后每次验证都拿它算一个**永远没有调仓日**的序列，结果是静默的全零",
     ["tests/test_forward_registry.py", "tests/test_composite_candidates.py"]),

    # ── 跌腿闸（2026-10-14）──────────────────────────────────────────
    # 这一组守护的是产品承诺的后半句。产品每天推「10 涨 10 跌」，
    # 而七道闸里原本**没有一道看跌腿**——`breadth_tstat` 写好了却没接进闸门。
    # 它的变弱方式非常安静：晋级理由照样完整、字段一个不少，
    # 只是「10 跌」那一半已经不对了，而判决词对此一言不发。
    ("downleg", "aplus/evolve_race.py",
     "    if td_ret >= max_test_down_return:\n",
     "    if td_ret >= float('inf'):\n",
     "跌腿闸永��成立——推的「10 跌」即使平均每天在涨也能晋级。"
     "实测现任权重就是这样：`avg_down_return = +0.126%/日`",
     ["tests/test_evolve_race.py::test_oos_decision_blocks_candidate_whose_down_leg_rises"]),

    ("downleg", "aplus/evolve_race.py",
     "    td_ret = td_stats.get(\"avg_down_return\")\n    if td_ret is None:\n",
     "    td_ret = td_stats.get(\"avg_down_return\", 0.0)\n    if td_ret is None:\n",
     "跌腿字段缺失被默认成「收益 0 = 不跌也不涨」——缺失变成合格，"
     "而缺数据按不可证明处理正是这套闸一贯的规矩",
     ["tests/test_evolve_race.py::test_missing_down_leg_data_is_treated_as_unprovable"]),

    ("downleg", "aplus/evolve_race.py",
     "    if breadth_t is not None and float(breadth_t) < t_crit:\n",
     "    if False:\n",
     "广度警告被静音——涨腿独撑的候选照样打出「六条腿全过」，"
     "读判决的人会以为两个半边产品一样强",
     ["tests/test_evolve_race.py::test_promotion_reason_flags_when_only_the_up_leg_carries_significance"]),

    ("downleg", "aplus/evolve_race.py",
     "                       f\"跌腿确实下跌 {td_ret:+.2%}/日、方向未被污染\"\n",
     "                       f\"方向未被污染\"\n",
     "晋级理由里不再出现跌腿——闸还在跑，但理由不提，"
     "读判决的人以为根本没检查过这一半",
     ["tests/test_direction_pollution.py::test_promotion_reason_counts_all_six_gates"]),

    # ── 复牌首日压缩（2026-10-12）────────────────────────────────────
    # 第一版实现就是「全局 clip(±10%)」，被既有的涨停测试打红。
    # 那说明这道闸的难点不在「要不要压」，而在**压哪一天**——
    # 压宽了会系统性削平所有真实波动，比不压更糟。
    ("resume_clamp", "aplus/panel.py",
     "    resume_day = (~suspended) & (suspended.shift(1).fillna(False).astype(bool))\n",
     "    resume_day = ret_raw.notna()\n",
     "压缩范围从「复牌首日」放宽成「每一个有收益的日子」——"
     "创业板 ±20% 涨停被压成 +10%，系统性削平所有真实信号",
     ["tests/test_momentum_clamp.py",
      "tests/test_implausible_return.py"]),

    ("resume_clamp", "aplus/panel.py",
     "    resume_day = (~suspended) & (suspended.shift(1).fillna(False).astype(bool))\n",
     "    resume_day = pd.Series(False, index=ret_real.index)\n",
     "压缩被整体关掉（退回修复前的原始价比值口径）——"
     "复牌首日 +593.72% 原样进 20 日窗口，动量冲到数倍，"
     "而 mom20 的 IC 是负的，这等于把策略送到最确定会亏的那一端",
     ["tests/test_momentum_clamp.py"]),

    ("resume_clamp", "aplus/panel.py",
     "        return ((1.0 + ret_mom).rolling(window, min_periods=_min_samples(window))\n",
     "        return ((1.0 + ret_mom).rolling(window + 1, min_periods=_min_samples(window))\n",
     "动量窗口差一根——`mom20` 实际变成 21 日、`rev5` 变成 6 日，"
     "不报错、不崩，只是因子从此不是它名字说的那个东西",
     ["tests/test_momentum_clamp.py",
      "tests/test_implausible_return.py"]),

    # ── 尾部空值体检（2026-10-12）────────────────────────────────────
    # 这条判据是 3342 只股票（61.2%）尾部缺 isST/turn 却一路判健康的根因。
    # 它一旦变弱，缺口会重新静默存在，而且**没有任何人会知道**。
    ("tail_nan", "aplus/coverage.py",
     "    window = df.tail(th.halt_run)\n",
     "    window = df.tail(th.halt_run * 4)\n",
     "窗口放大 4 倍——更早的空洞被算进尾部，误报范围成倍扩大",
     ["tests/test_coverage_tail_nan.py"]),

    ("tail_nan", "aplus/coverage.py",
     "    traded = vol.fillna(0.0) > 0\n",
     "    traded = vol.fillna(0.0) >= 0\n",
     "停牌判定从「有成交」放宽成「不是负数」——停牌日 volume=0 也算成交，"
     "长期停牌股整片误报，比原来的盲区更吵",
     ["tests/test_coverage_tail_nan.py::test_suspended_tail_not_flagged",
      "tests/test_coverage_tail_nan.py::test_suspended_and_traded_mixed_only_counts_traded_rows"]),

    ("tail_nan", "aplus/coverage.py",
     "        n = int((traded & values.isna()).sum())\n        if n:\n            out[col] = n\n",
     "        n = int((traded & values.isna()).sum())\n        out[col] = n\n",
     "计数为 0 也照样登记——空字典被误当成「有洞」，全市场被判不健康",
     ["tests/test_coverage_tail_nan.py::test_suspended_tail_not_flagged",
      "tests/test_coverage_tail_nan.py::test_healthy_frame_reports_nothing"]),

    # 原先这里放的是「删掉 `if not traded.any(): return {}` 护栏」。
    # 那个变异体**存活但是僵尸**：护栏删掉后 traded 全 False，逐列
    # `(traded & values.isna()).sum()` 本来就是 0，`if n:` 不成立，
    # 返回值仍是 `{}`——源码文本变了，效果没变。僵尸不能混入存活率统计。
    # 上面那条 `if n:` 才是这一段真正承重的判断。

    ("tail_nan", "aplus/coverage.py",
     "    for col in REQUIRED_EXTRA_COLUMNS:\n        if col not in window.columns:\n            continue\n",
     "    for col in (col for col in window.columns if col not in (\"date\", \"volume\")):\n",
     "查的列集从「baostock 专有列」变成「除日期与成交量外所有列」——"
     "把复权因子之类无关列也拖进健康判定",
     ["tests/test_coverage_tail_nan.py::test_tail_nan_only_checks_extra_columns"]),

    # ── 写库互斥（2026-10-12）──────────────────────────────────────
    # 四个脚本写同一批 parquet。锁一旦失效，后写覆盖先写，丢的列和行没人知道。
    ("writelock", "aplus/write_lock.py",
     "        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY\n",
     "        flags = os.O_CREAT | os.O_WRONLY\n",
     "O_EXCL 被摘掉——check-then-act 回归，两个进程会同时拿到锁",
     ["tests/test_write_lock.py::test_second_acquire_raises"]),

    ("writelock", "aplus/write_lock.py",
     "        if not self._held:\n            return\n        self._held = False\n",
     "        self._held = False\n",
     "释放的幂等护栏被删——同进程的 intruder 释放时会删掉别人持有的锁",
     ["tests/test_write_lock.py::test_release_is_idempotent",
      "tests/test_write_lock.py::test_release_does_not_steal_same_process_lock"]),

    ("writelock", "aplus/write_lock.py",
     "        if lock_holder(self.path) == str(os.getpid()):\n",
     "        if True:\n",
     "释放前不再核对锁的归属——进程被 kill 后 pid 复用/锁被换人时会误删活人的锁",
     ["tests/test_write_lock.py::test_release_is_idempotent",
      "tests/test_write_lock.py::test_release_does_not_steal_replaced_lock"]),

    ("writelock", "aplus/write_lock.py",
     "    def __exit__(self, exc_type, exc, tb):\n        self.release()\n        return False\n",
     "    def __exit__(self, exc_type, exc, tb):\n        if exc_type is None:\n            self.release()\n        return False\n",
     "异常退出不放锁——一次崩溃就把全市场锁死，第二天 18:30 静默跳过更新",
     ["tests/test_write_lock.py::test_released_after_exception",
      "tests/test_write_lock.py::test_update_all_releases_lock_even_when_pipeline_explodes"]),
    # ── ST 状态未知要看得见（2026-10-12）──────────────────────────────
    # 「缺数据」被折成「不是 ST」会让 ST 过滤对全市场静默失效。
    # 这三条变异体分别封住计数、按日累加、以及两处「如实传递」。
    ("unknown_st", "aplus/predict.py",
     "            unknown_st[code] = unknown_st.get(code, 0) + 1\n",
     "            unknown_st[code] = 1\n",
     "按日计数改成按次计 1——一只票连着 5 天未知只报 1，盲区规模被系统性低估",
     ["tests/test_unknown_st_visibility.py::test_unknown_counter_accumulates_across_days",
      "tests/test_unknown_st_visibility.py::test_unknown_st_is_counted_not_hidden"]),

    ("unknown_st", "aplus/predict.py",
     "        for code in df.loc[df[\"isST\"].isna(), \"code\"]:\n",
     "        for code in df.loc[df[\"isST\"].notna(), \"code\"]:\n",
     "统计口径取反——数的是「已知」的票，未知的反而一条不上报",
     ["tests/test_unknown_st_visibility.py::test_unknown_st_is_counted_not_hidden",
      "tests/test_unknown_st_visibility.py::test_known_non_st_is_not_counted"]),

    ("unknown_st", "aplus/panel.py",
     "            \"isST\": vals[keep, len(FACTOR_COLUMNS) + 1],\n",
     "            \"isST\": np.where(np.isnan(vals[keep, len(FACTOR_COLUMNS) + 1]),\n"
     "                             0, vals[keep, len(FACTOR_COLUMNS) + 1]).astype(int),\n",
     "快路径把未知折回 0——退回 2026-10-12 之前那个让 ST 过滤对 61% 股票失明的写法",
     ["tests/test_unknown_st_visibility.py::test_panel_keeps_unknown_isst_as_nan",
      "tests/test_unknown_st_visibility.py::test_panel_keeps_known_isst"]),

    ("unknown_st", "aplus/snapshot.py",
     "        isst = float(isst) if pd.notna(isst) else float(\"nan\")\n",
     "        isst = float(isst) if pd.notna(isst) else 0.0\n",
     "慢路径把未知折回 0——与快路径同一件事的两份实现只改一处，两条路径说法不一",
     ["tests/test_unknown_st_visibility.py::test_snapshot_keeps_unknown_isst_as_nan",
      "tests/test_snapshot.py::test_cross_section_tolerates_nan_isst"]),

    # ── 源站会话死亡的熔断（2026-10-12）──────────────────────────────
    # 首轮全量修复时 baostock 在第 2140 只后断开，之后 961 只全部走降级、
    # 全部填不上要补的五列。没有熔断就是白跑 16 分钟的网络往返。
    ("breaker", "scripts/repair_tail_cols.py",
     "                        if degrade_streak >= args.degrade_limit:\n",
     "                        if False:\n",
     "熔断条件永不成立——主源已死也照样一只只跑下去（实测白等 16 分钟）",
     ["tests/test_repair_tail_cols_breaker.py::test_stops_after_consecutive_degraded"]),

    ("breaker", "scripts/repair_tail_cols.py",
     "                        degrade_streak = (degrade_streak + 1\n"
     "                                          if result.status == \"degraded\" else 0)\n",
     "                        degrade_streak += 1\n",
     "成功不再清零——统计从「连续降级」变成「累计降级」，"
     "中间成功一次就足以误熔断（会话其实还活着）",
     ["tests/test_repair_tail_cols_breaker.py::test_success_resets_the_streak",
      "tests/test_repair_tail_cols_breaker.py::test_quarantine_is_not_session_death"]),

    ("breaker", "scripts/repair_tail_cols.py",
     "                        stats[\"fixed\"] += 1\n                        degrade_streak = 0\n",
     "                        stats[\"fixed\"] += 1\n",
     "成功时不再清零——降级/成功交替两轮就凑满阈值，误判会话死亡并放弃几百只",
     ["tests/test_repair_tail_cols_breaker.py::test_degraded_then_success_then_degraded_does_not_trip",
      "tests/test_repair_tail_cols_breaker.py::test_success_resets_the_streak"]),
]


def _clear_pycache(rel_path: str) -> None:
    d = (ROOT / rel_path).parent / "__pycache__"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


# ── 崩溃安全的还原（2026-10-14 补）────────────────────────────────────
#
# `finally` 能挡 Python 异常，**挡不住外部信号**。2026-10-14 就踩了：
# 我用 `task_stop` 掐掉了正在跑的变异测试，SIGTERM 直接终止进程，
# `finally` 没机会执行，`scripts/ic_guided_race.py` 被**留在变异态**——
# `emit("ic_sign_all", {f: 1.0 ...})`，IC 引导候选的符号全被写死成 +1。
#
# 那个文件当时**报告一切正常**：没有报错、没有测试变红，
# 只是「按 IC 符号决定权重」这个脚本存在的唯一理由被悄悄摘掉了。
# 再跑一次变异测试才暴露出来（源串未命中）。
#
# 所以现在有两道保险：
# 1. SIGTERM/SIGINT 处理器：先把所有在途源码还原，再退出
# 2. **落盘备份 + 启动自愈**：连 SIGKILL（处理器也来不及）都能在下次
#    运行时把文件恢复回去——备份文件还在，就说明上次没还原干净

BACKUP_DIR = ROOT / ".mutation_backup"

# rel_path -> 原始字节。进程内用于信号处理器兜底。
_PENDING: dict[str, bytes] = {}


def _backup_name(rel_path: str) -> Path:
    return BACKUP_DIR / (rel_path.replace("/", "__") + ".bak")


def _recover_pending() -> int:
    """启动自愈：把上次没还原干净的源文件恢复回去。

    返回恢复的文件数。**必须在任何变异体安放之前调用**，
    否则会拿一个已被变异过的文件当「原始文件」备份，把变异固化下来。
    """
    if not BACKUP_DIR.is_dir():
        return 0
    n = 0
    for bak in sorted(BACKUP_DIR.glob("*.bak")):
        rel = bak.name[:-4].replace("__", "/")
        src = ROOT / rel
        src.write_bytes(bak.read_bytes())
        _clear_pycache(rel)
        bak.unlink()
        n += 1
        print(f"⚠️  上次变异测试被中断，已还原：{rel}", flush=True)
    return n


def _install_signal_guards() -> None:
    def handler(signum, _frame):
        for rel, data in list(_PENDING.items()):
            try:
                (ROOT / rel).write_bytes(data)
                _clear_pycache(rel)
            except Exception:                      # 还原失败也要继续还原其余的
                pass
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):               # 非主线程等，装不上就算了
            pass


def run_group(group: str) -> tuple[int, int, list[str]]:
    rows = [m for m in MUTATIONS if m[0] == group]
    killed, notes = 0, []
    for _, rel, old, new, desc, tests in rows:
        src = ROOT / rel
        original = src.read_text(encoding="utf-8")
        if old not in original:
            notes.append(f"  ✗ 无法安放：{desc}（源串未命中，可能实现已变）")
            continue
        backup = original.encode("utf-8")
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        bak = _backup_name(rel)
        bak.write_bytes(backup)
        _PENDING[rel] = backup
        try:
            src.write_text(original.replace(old, new, 1), encoding="utf-8")
            _clear_pycache(rel)
            changed = src.read_text(encoding="utf-8")
            if changed == original:
                notes.append(f"  ✗ 变异体是 no-op：{desc}（写入后源码未变）")
                continue
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
                 *tests],
                cwd=ROOT, env=env, capture_output=True, text=True)
        finally:
            _clear_pycache(rel)
            src.write_bytes(backup)
            bak.unlink(missing_ok=True)
            _PENDING.pop(rel, None)
        if p.returncode == 0:
            notes.append(f"  ✗ 存活：{desc} —— 测试全绿，说明它没测到这个形状")
        else:
            killed += 1
            tail = [ln for ln in p.stdout.splitlines() if "failed" in ln or "error" in ln]
            notes.append(f"  ✓ 被杀：{desc}"
                         + (f"（{tail[-1].strip()}）" if tail else ""))
    return killed, len(rows), notes


def _survival_upper_bound(killed: int, total: int, alpha: float = 0.05) -> float:
    """零存活时，「真实存活率」的 95% 单侧上界（Clopper–Pearson）。

    ## 为什么必须报这个

    **「29/29 全杀」不等于「测试好使」，它只等于「这 29 个变异体被杀掉了」。**
    如果某个等价形态的变异体存在而没被造出来，它照样能活。

    0 存活 / n 次试验时，存活率的点估计是 0，但置信上界不是 0：

        p_upper = 1 - alpha ** (1 / n)

    n=29 → 上界 ≈ 9.8%。意思是：**同类变异体有至多一成没被这套变异清单覆盖**，
    这个数字比「全杀」有用得多，因为它诚实地说了这份清单的边界在哪。

    样本量小的时候上界会非常难看（n=3 → 63%），所以小分组必须标出来：
    **变异测试的功效是设计出来的，不能靠运气。**
    """
    if total <= 0:
        return 1.0
    if killed >= total:
        return 1.0 - alpha ** (1.0 / total)
    return (total - killed) / total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="只跑某一组（block_tstat / composite）")
    args = ap.parse_args()

    groups = [g for g in dict.fromkeys(m[0] for m in MUTATIONS) if args.only in (None, g)]
    # 先自愈再装守卫：顺序反了会把「已被变异过的文件」当成原件备份下来，
    # 等于把一次意外固化成永久改动。
    _recover_pending()
    _install_signal_guards()
    total_killed = total = 0
    per_group = []
    for g in groups:
        print(f"\n=== 变异组 {g} ===", flush=True)
        k, n, notes = run_group(g)
        for ln in notes:
            print(ln, flush=True)
        print(f"→ {k}/{n} 被杀", flush=True)
        per_group.append((g, k, n))
        total_killed += k
        total += n

    print(f"\n合计 {total_killed}/{total} 被杀")

    # 「全杀」的边界：真实存活率的置信上界
    bound = _survival_upper_bound(total_killed, total)
    print(f"存活率 95% 上界 ≈ {bound:.1%}"
          f"（n={total}，0 存活时的 Clopper–Pearson 单侧上界）")
    thin = [(g, n) for g, k, n in per_group if n < 3]
    if thin:
        print("⚠️ 以下分组变异体少于 3 个，功效不足，结论不稳："
              + "，".join(f"{g}（{n} 个）" for g, n in thin))
    if total and total_killed == total and bound > 0.20:
        print("⚠️ 全杀但样本量小——这份结论的运气成分不小，别把它当保证。")

    # 源码必须完好
    for g in groups:
        for _, rel, *_ in [m for m in MUTATIONS if m[0] == g]:
            _clear_pycache(rel)
    return 0 if total_killed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())