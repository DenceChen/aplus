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
     "跌腿闸永远成立——推的「10 跌」即使平均每天在涨也能晋级。"
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
     ["tests/test_direction_pollution.py::test_promotion_reason_states_the_real_number_of_gates"]),

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
      "tests/test_write_lock.py::test_released_after_exception"]),
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

    # ── 市值加权基准实验（预注册 docs/TODO_cap_weighted_benchmark_2026-10-15.md）──
    #
    # 这一组的意义和别处不同：**下面一半的变异体是这次实验里真实犯过的错**，
    # 不是为了测而造的形状。第 9、13 条是 V 对照臂与第一次真跑抓出来的
    # （2.4% 的篮子偏差、IndexError），它们证明这些守卫不是安慰剂。
    ("cap_weighted", "aplus/capweight.py",
     "from scripts.turn_integrity_audit import implied_a_shares  # noqa: F401\n",
     "def implied_a_shares(daily):  # noqa: D401\n"
     "    return daily[\"volume\"].astype(float) * 100.0 / daily[\"turn\"].astype(float)\n",
     "市值口径在 capweight 里重写一遍——审计那份 21/21 地面真值的验过结论"
     "从此与实际用的公式无关，而报告照样印着「21/21 验过」",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_cap_definition_is_bitwise_the_verified_audit_formula"]),

    ("cap_weighted", "aplus/capweight.py",
     "    out.loc[ok] = close.loc[ok] * a.loc[ok]\n",
     "    out.loc[ok] = close.loc[ok] * a.loc[ok] * 100.0\n",
     "市值口径整体差 100 倍——真实数据上 cap/close 对 a_shares 真值的中位比值立刻越界",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_cap_hits_ground_truth_on_the_real_shares_files"]),

    ("cap_weighted", "aplus/capweight.py",
     "            cap = pd.Series(cap).ffill().to_numpy(float)\n",
     "            cap = pd.Series(cap).fillna(0.0).to_numpy(float)\n",
     "停牌日市值不再沿用上一根——停牌的大盘股静默退出基准，"
     "等于偷偷改了抽样口径（预注册 §1「每日重新归一化」那一段）",
     ["tests/test_cap_weighted_benchmark.py::test_every_days_weights_sum_to_exactly_one",
      "tests/test_cap_weighted_benchmark.py::test_a_shares_on_is_index_aligned_with_the_date_array"]),

    ("cap_weighted", "aplus/capweight.py",
     "            self.stats[\"days_fallback_equal\"] += 1\n",
     "            self.stats[\"days_fallback_equal\"] += 0\n",
     "回退等权不再计数——数字照常出，报告看上去与正常跑一模一样",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_normalize_falls_back_to_equal_weight_and_says_so_when_no_caps_exist"]),

    ("cap_weighted", "aplus/capweight.py",
     "            shares = implied_a_shares_full(d).to_numpy(float)\n",
     "            shares = implied_a_shares(d).to_numpy(float)\n",
     "🚨 **本次真跑犯过的错**：直接用短序列当与日期等长的数组，"
     "下标整体前移——短的越界、长的读错票（真跑当场 IndexError: 487 vs 485）",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_a_shares_on_is_index_aligned_with_the_date_array"]),

    ("cap_weighted", "aplus/backtest.py",
     "        out[day] = float(ret.mean())\n",
     "        out[day] = float(ret.mean()) * 1.0000000000000002\n",
     "cap 通路关掉时的等权基准被悄悄改了 1 ulp——"
     "「加了个可选参数」已经动到历史结论，而报告上写的是同一个数",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_market_by_day_with_cap_table_off_is_bitwise_unchanged"]),

    ("cap_weighted", "aplus/backtest.py",
     "            cap_sink[day] = float(sum(w[c] * x for c, x in zip(market[\"code\"], ret))) \\\n",
     "            cap_sink[day] = float(sum(w[c] * x for c, x in zip(market[\"code\"], ret)) / len(ret)) \\\n",
     "加权后又除了一个 len(ret)——「市值加权基准」退化成按持仓数摊薄的东西",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_market_by_day_cap_weighted_matches_the_hand_computed_weighted_mean"]),

    ("cap_weighted", "aplus/backtest.py",
     "            w = cap_ctx.normalize(day, list(market[\"code\"]), channel=\"benchmark\") \\\n",
     "            w = cap_ctx.normalize(day, list(market[\"code\"]), channel=None) \\\n",
     "基准端不交出诊断——预注册 §3.1 的市值中位数比只能在共享路径里取",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_the_cap_diagnostic_rides_in_the_shared_path_not_a_second_pass"]),

    ("cap_weighted", "aplus/backtest.py",
     "        if not cross_sections.get(\"_market_cap\"):\n",
     "        if False:\n",
     "基准缺失时静默退回等权——`market.get(day, 0.0)` 在空字典上恒为 0.0，"
     "「超额」变成组合绝对收益，而三臂数字看上去仍然完全正常",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_benchmark_cap_never_silently_falls_back_to_equal_weight"]),

    ("cap_weighted", "aplus/backtest.py",
     "    cost = STOCK_COST_DEFAULT\n",
     "    cost = 0.00055\n",
     "成本在 evaluate_weights 里被就地改掉——预注册 §4「不改任何成本假设」当场破",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_evaluate_weights_still_reads_cost_from_the_single_source"]),

    ("cap_weighted", "aplus/evolve_race.py",
     "        daily = net.groupby(days).mean() - market\n",
     "        daily = net.groupby(days).mean() + market\n",
     "不传权重列的老口径被改成加市场——B 与 C 的差就不再只来自市值加权",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_daily_series_without_weights_col_is_bitwise_unchanged"]),

    ("cap_weighted", "aplus/evolve_race.py",
     "        ok = w.notna() & (w > 0) & net.notna()\n",
     "        ok = w.notna() & (w > 0)\n",
     "🚨 **本次真跑犯过的错**：加权篮子的分母不再只由贡献了分子的行构成。"
     "`verify_predictions` 用 merge(how=\"left\")，没有下一根 bar 的行 "
     "actual_pct 是 NaN 而权重不是 ⇒ 加权系统性低于等权（实测 2021 单年差 2.4%）",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_uniform_cap_weights_reproduce_equal_weight_even_with_missing_bars",
      "tests/test_cap_weighted_benchmark.py::"
      "test_the_weighted_basket_divides_only_by_contributing_rows"]),

    ("cap_weighted", "aplus/evolve_race.py",
     "        count = ok.groupby(days).sum()\n",
     "        count = w.groupby(days).count()\n",
     "分成分下限改回按权重列行数计——一天 200 只只有 5 只有市值时，"
     "MIN_BASKET 当初要拦的那件事重新发生",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_daily_series_cap_weighted_counts_only_capped_constituents"]),

    ("cap_weighted", "scripts/backtest_walk_forward_weights.py",
     "             benchmark_cap: bool = False, portfolio_cap: bool = False,\n",
     "             benchmark_cap: bool = False, portfolio_cap: bool = False,\n"
     "             cost_override=None,\n",
     "共享入口 run_year 长出成本开关——三臂之间的差就分不清来自信号还是这段代码",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_run_year_signature_carries_the_cap_switches_only"]),

    ("cap_weighted", "scripts/cap_weighted_benchmark.py",
     "from backtest_walk_forward_weights import (ENTRY_LAG, _rescale_check, _t,  # noqa: E402\n"
     "                                           run_year, weights_by_year)\n",
     "from backtest_walk_forward_weights import (ENTRY_LAG, _rescale_check, _t,  # noqa: E402\n"
     "                                           weights_by_year)\n",
     "实验脚本不再从共享路径 import run_year——它自己搭了一套回测，"
     "两台测量仪之间的差永远查不出来（第 22 层）",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_experiment_reuses_run_year_and_never_recomputes_daily_returns"]),

    ("cap_weighted", "scripts/cap_weighted_benchmark.py",
     "from factor_attribution import T_CRIT_5PCT, power_floor  # noqa: E402\n",
     "T_CRIT_5PCT = 1.96  # noqa: E402\n",
     "临界值在本脚本里被手打一遍——判据从此换了一个对象，而报告看上去一模一样",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_verdict_module_never_hardcodes_the_critical_value"]),

    ("cap_weighted", "scripts/cap_weighted_benchmark.py",
     "    if t_blocked is not None and t_blocked >= T_CRIT_5PCT:\n",
     "    if t_blocked is not None and t_blocked > T_CRIT_5PCT:\n",
     "判决边界「≥」改成「>」——预注册 §2 的分支表逐字抄错一个字，"
     "恰好落在 t_crit 上时就会换档",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_verdict_branches_are_the_preregistered_three"]),

    ("cap_weighted", "scripts/cap_weighted_benchmark.py",
     "    if gross_annual <= 0:\n",
     "    if gross_annual < -1.0:\n",
     "「≤ 0」这一档被改窄——C 臂毛超额落在 (−1, 0] 时会被错报成「正但不显著」",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_verdict_branches_are_the_preregistered_three"]),

    ("cap_weighted", "scripts/cap_weighted_benchmark.py",
     "BASKET = 200\n",
     "BASKET = 200\nMIN_BASKET = 3\n",
     "实验脚本自己定义 MIN_BASKET——成分下限从此有第二份真相",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_cost_assumptions_are_not_redefined_anywhere_in_the_experiment"]),

    ("cap_weighted", "scripts/cap_weighted_benchmark.py",
     "default=\"2021-01-01\",\n",
     "default=\"2019-01-01\",\n",
     "窗口起点从 2021 挪到 2019——2019/2020 是等权臂，混进来就是"
     "对两个不同的量求平均（第 7 种形状），而 A 臂就不再复现已发布数字",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_cost_assumptions_are_not_redefined_anywhere_in_the_experiment"]),

    ("cap_weighted", "aplus/provenance.py",
     "    \"cap_weighted_benchmark.json\": \"scripts/cap_weighted_benchmark.py\",\n",
     "",
     "报告没登记在 DEFAULT_GENERATORS——按本模块自己的规矩 unknown = 不许引用，"
     "而没有任何守卫会喊",
     ["tests/test_provenance.py::"
      "test_the_reports_we_actually_decide_by_are_registered"]),

    # ── 产物层：改**产物本身**（守卫读的就是它，改脚本对它零覆盖，第 96 层）──
    #
    # ⚠️ 这一组的锚是**产物里的字面量**，产物重跑后数字会变 ⇒ 锚会失配。
    #   失配不是静默的：`run_group` 报「无法安放（源串未命中）」且计入分母，
    #   于是整体退出码非 0。要重跑本组请同步这三行的 old/new。
    ("cap_weighted", "data/reports/cap_weighted_benchmark.json",
     "      \"gross_annual_pct\": 0.0694654436628111,\n",
     "      \"gross_annual_pct\": 0.0694654536628111,\n",
     "产物里 A 臂毛超额有一位被改错（Δ=1e-8）——A 臂必须复现已发布的 "
     "factor_width_mde N=200，否则三臂至少有一臂不是同一个量。"
     "⚠️ 幅度必须**大于守卫的容差 1e-9**：Δ=1e-16 的变异是**按设计存活**的，"
     "那是容差本身，不是洞（实测 A 臂与已发布数字的差恰为 0.0）",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_arm_a_reproduces_the_published_factor_width_number"]),

    ("cap_weighted", "data/reports/cap_weighted_benchmark.json",
     "    \"c_minus_b\": -0.024989395683218577,\n",
     "    \"c_minus_b\": -0.024999395683218577,\n",
     "`C − B` 被写成一个不是减出来的数（Δ=1e-5）——预注册 §1 强制输出分解值，"
     "而「印了一个看起来对的数」与「真的减了一遍」在报告上长得一样。"
     "⚠️ 幅度必须大于守卫的容差 1e-12（实测该值与 C − B **逐位**相同）",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_artifact_reports_the_mandatory_diagnostics_and_the_decomposition"]),

    ("cap_weighted", "data/reports/cap_weighted_benchmark.json",
     "    \"B\": {\n      \"portfolio_weighting\": \"equal\",\n      \"benchmark_weighting\": \"cap\",\n",
     "    \"B\": {\n      \"portfolio_weighting\": \"equal\",\n      \"benchmark_weighting\": \"equal\",\n",
     "产物自称 B 臂用的是等权基准——三臂口径标签被改，"
     "而三臂毛超额数字仍然是三个不同的数",
     ["tests/test_cap_weighted_benchmark.py::"
      "test_the_three_arms_are_actually_three_different_measurements"]),

    # ── 自愈本身：它一旦失灵，**后面所有组的证据全部作废** ──────────────
    # 这一组是 2026-10-15 那场事故的直接产物。第一轮变异跑报「12/24 存活」，
    # 根因就是自愈把 `mutation_check` 自己刚写的备份当成残留还原掉了。
    # 若不钉住，任何一组的「存活」都可能是**这份假绿的重演**。

    ("selfheal", "scripts/mutation_check.py",
     "        owner = _owner_name(rel)\n"
     "        if owner.is_file() and _owner_is_live(owner):\n"
     "            continue                        # 在飞，不是残留\n",
     "        owner = _owner_name(rel)\n"
     "        if False:                           # 变异：不再跳过在飞的变异体\n"
     "            continue\n",
     "自愈把这一轮正在飞的变异体当成残留还原掉 —— 守卫随后读到原件、"
     "全绿，被记成「存活」"
     "（实测：`cap_weighted` 组 21 个里 9 个这样假绿）",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_a_backup_owned_by_a_live_mutation_check_is_not_swallowed"]),

    ("selfheal", "scripts/mutation_check.py",
     "    return \"mutation_check\" in cmd\n",
     "    return True                            # 变异：认任何活着的进程当主人\n",
     "身份双证被拆掉——pid 会被复用，别的进程被当成主人后，"
     "备份会被**永远跳过**，2026-10-14 那次 SIGKILL 事故会静默复发。"
     "⚠️ 第一版这条变异挂在 `test_a_backup_owned_by_a_dead_run_is_still_recovered` 上，"
     "**它活着但被那次 `os.kill` 的死进程判定提前短路** ⇒ 空变异，"
     "报出来的「存活」是变异器的错不是守卫的错（第 14 种形状）",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_a_backup_owned_by_a_live_bystander_is_still_recovered"]),

    ("selfheal", "scripts/mutation_check.py",
     "    try:\n"
     "        raw = owner.read_text(encoding=\"utf-8\").strip()\n"
     "    except OSError:\n"
     "        return False                       # 没有主人文件 ⇒ 当残留处理\n",
     "    try:\n"
     "        raw = owner.read_text(encoding=\"utf-8\").strip()\n"
     "    except OSError:\n"
     "        return True                        # 变异：读不出主人就当在飞\n",
     "主人文件读不出时跳过还原 ⇒ 那份备份永远不会被读，"
     "源码永久停在变异态。"
     "⚠️ 第一版这条挂在「没有 owner 文件」的还原测试上，"
     "而 `owner.is_file() and ...` 短路让 `_owner_is_live` **根本没被调用** "
     "⇒ 空变异。"
     "⚠️ 第二版锚在旧的单行 `int(owner.read_text(...)` 上，"
     "而解析已改成多行 `lines[0]` ⇒ 又一次 0 命中的死锚点",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_a_backup_whose_owner_is_unreadable_is_still_recovered"]),

    ("selfheal", "scripts/mutation_check.py",
     "        _write_backup_atomic(bak, backup)\n",
     "        pass  # 变异：不登记磁盘备份\n",
     "`run_group` 不写 `.bak` ⇒ 被 SIGKILL 时磁盘上没有任何东西可还原，"
     "三层网里的第三层形同虚设（顺序判据同时会红）",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_the_run_group_wiring_keeps_the_owner_then_backup_then_mutant_order"]),

    # ── 身份三证（2026-10-15 P1-3 / P1-5，第 115 层）──────────────
    # pid 是**可复用资源**：双证（pid 存活 + cmdline 含 mutation_check）
    # 会被「另一个 mutation_check 复用了同一个 pid」同时满足 ⇒ 真残留
    # 永远不被还原，而没有任何东西会喊。第三证（启动时刻）不可复用。
    ("selfheal", "scripts/mutation_check.py",
     "    if len(lines) >= 2:\n"
     "        if _proc_start(pid) != lines[1]:\n"
     "            return False                   # pid 被复用 ⇒ 这不是那个主人\n",
     "    if False:\n"
     "        pass                              # 变异：第三证被摘掉，退回双证\n",
     "启动时刻这第三证被摘掉 ⇒ pid 复用的洞重新打开，"
     "真残留被永久跳过且静默（双证在 pid 复用下**同时**满足，"
     "所以它封不住这个洞）",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_a_reused_pid_is_not_mistaken_for_its_predecessor"]),

    ("selfheal", "scripts/mutation_check.py",
     "        if _proc_start(pid) != lines[1]:",
     "        if _proc_start(pid) == lines[1]:",
     "第三证的不等号写反 ⇒ 复用的判成活的（真残留被永久跳过）、"
     "真主人判成死的（**在飞的变异体会被还原**，源码冻回去）",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_a_reused_pid_is_not_mistaken_for_its_predecessor",
      "tests/test_interrupted_mutation_self_heals.py::"
      "test_a_live_owner_whose_start_token_matches_is_still_skipped"]),

    ("selfheal", "scripts/mutation_check.py",
     'env=dict(os.environ, LC_ALL="C", LANG="C"))',
     "env=dict(os.environ))",
     "读启动时刻时不钉 locale ⇒ 存的时候是 `Thu Oct 15 ...`、"
     "读的时候是 `四 10/15 ...`（本机默认 locale 的中文星期）⇒ "
     "**同一个进程两次读出来不相等** ⇒ 好主人被判成复用的坏主人",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_the_start_token_is_read_under_a_fixed_locale_and_is_byte_stable"]),

    ("selfheal", "scripts/mutation_check.py",
     '["ps", "-p", str(pid), "-o", "lstart="],',
     '["ps", "-p", str(pid), "-o", "start="],',
     "改用只有**分钟**分辨率的 `start=` ⇒ 同一分钟内的 pid 复用判不出来，"
     "第三证退化成半个证据",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_the_start_token_is_read_under_a_fixed_locale_and_is_byte_stable"]),

    ("selfheal", "scripts/mutation_check.py",
     "    try:\n        os.kill(pid, 0)                    # 0 号信号只探存活，不投递",
     "    if pid == os.getpid():\n"
     "        return True                        # 变异：自 pid 旁路回来了\n"
     "    try:\n        os.kill(pid, 0)                    # 0 号信号只探存活，不投递",
     "P1-5：把删掉的 `pid == os.getpid()` 旁路放回来 ⇒ "
     "`_recover_pending()` 在 `main()` 里、自愈之前就调，"
     "若残留的 pid 恰被**本进程**复用，cmdline 判据读到的就是 mutation_check 自己 ⇒ "
     "永久冻结且无告警",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_the_self_pid_shortcut_is_gone"]),

    ("selfheal", "scripts/mutation_check.py",
     '        print(f"⚠️  自愈判据降级：ps 不可用，`.owner`={owner.name} 的主人 "',
     '        pass  # 变异：降级变得静默（语法合法版；注意不能只删 `print(`，'
     '那会让后面的关键字实参非法，产出的是收集期语法错误而非守卫变红）\n'
     '        _unused = (f"⚠️  自愈判据降级：ps 不可用，`.owner`={owner.name} 的主人 "',
     "P1-3：`ps` 不可用时只剩 pid 一个证据（可被复用），"
     "而这条降级**必须可见**——静默降级等于在别的机器上把守卫悄悄废掉。"
     "与 provenance 的「依据：字节证据 / ⚠️ 仅 commit 走查」同一条纪律",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_the_ps_degradation_is_announced_rather_than_silent"]),

    ("selfheal", "scripts/mutation_check.py",
     "            f\"{os.getpid()}\\n{_proc_start(os.getpid()) or ''}\\n\", encoding=\"utf-8\")",
     "            f\"{os.getpid()}\\n\", encoding=\"utf-8\")",
     "写入端不写启动时刻 ⇒ 第三证对**全部现存备份**零覆盖，"
     "而这一改动完全不碰判据、只碰接线，最容易蒙混过关（跨过代码与产物之间那道缝）",
     ["tests/test_interrupted_mutation_self_heals.py::"
      "test_the_owner_file_actually_carries_the_start_token"]),

    # ── 第 137 层：CLAUDE.md 判决表的手抄件必须逐格对账 ──────────────
    # 🚨 这一组每个变异体都必须被**控制组**杀掉，而不是被主守卫杀掉：
    #   主守卫在正常态是绿的，「判据被抽掉」与「表被改坏」是两个独立的失效面。

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '    out: list[str] = []',
     '    return []\n' + '    out: list[str] = []',
     "判据恒假（永远说「一致」）⇒ 主守卫与正控全绿；"
     "只有那三条**用真实抄错的数做夹具**的反控能杀掉它",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '    out: list[str] = []',
     '    return ["恒红"]\n' + '    out: list[str] = []',
     "判据恒真（永远报不一致）⇒ 正控立刻红；与上一条方向相反",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '        want_gross = f"{w[\'gross_annual\'] * 100:.2f}%"\n        if row[i_gross].replace(" ", "") != want_gross:\n            out.append(f"N={n} 毛超额 {row[i_gross]} ≠ 产物 {want_gross}")',
     "        pass",
     "删掉**毛超额**那一列的比较 ⇒ 主守卫与三条反控**照样全绿**"
     "（它们改的全是「万」那一列）⇒ 只测一列的判据只是守着半个命题",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '        tgt = w["capital_for_target_net"]\n        want_tgt = "不存在" if tgt is None else f"{tgt / 1e4:.1f} 万"\n        if row[i_tgt].replace(" ", "") != want_tgt:\n            out.append(f"N={n} 到目标本金 {row[i_tgt]} ≠ 产物 {want_tgt}")',
     "        pass",
     "删掉「到 20% 净所需本金」那一列的比较 ⇒ 那一列可以整列写错而无人发现",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '    missing = sorted(set(widths) - seen)\n    if missing:\n        out.append(f"产物里的 N 没有出现在表中：{missing}")',
     "    pass",
     "删掉「产物里的 N 没有出现在表中」⇒ 产物新增一档时表格可以静默漏掉它，"
     "而逐行对照只看**表里有的** N",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '        m = re.fullmatch(r"([0-9.]+)\\s*万", row[i_cap])\n        want_cap = round(w["capital_for_breakeven"] / 1e4, 1)\n        if not m or float(m.group(1)) != want_cap:\n            got = m.group(1) if m else repr(row[i_cap])\n            out.append(f"N={n} 回本本金 {got} 万 ≠ 产物 {want_cap} 万")',
     "        pass",
     "删掉**回本本金**那一列的比较（这条守卫的全部存在理由）",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("wcf_out", "scripts/width_capital_feasibility.py",
     '    p.add_argument("--out", type=Path, default=DEFAULT_OUT)\n',
     '    p.add_argument("--out", type=Path, default=None)\n',
     "`--out` 退回 `default=None` ⇒ **裸跑只打印判决、不落盘**，"
     "而产物层守卫守的是**上游** `factor_width_mde.json` 的 `code_sha`，"
     "**不会**因此变红 ⇒ 「按正路重跑」变成一个看起来做过、"
     "实则什么都没做的动作，而没有任何东西会报（第 137 层）",
     ["tests/test_width_capital_feasibility.py::test_a_bare_run_actually_writes_the_report_not_just_prints_it"]),

    # ── 覆盖面那组（第 137 层的第二半：对账守卫自己也会有覆盖漏洞）──
    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '_STALE_MARK = "下面这张表已作废"',
     '_STALE_MARK = ""',
     "作废标记被清空 ⇒ `docs/TODO_basket_width_2026-10-15.md` 里那张**旧口径**的表"
     "变成「默默错着」，而这正是第 137 层本身要防的东西",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '        if _declares_stale(text, pos):\n            continue',
     '        if False:\n            continue',
     "「就地声明作废」这一支被删掉 ⇒ 与上一条方向相反："
     "那张**正确的** `CLAUDE.md` 表也会被判成「错」⇒ 守卫恒红，"
     "而红了之后人只会去改判据（第 112 层：过严的守卫比恒真的更危险）",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '    return any(_STALE_RE.match(line)\n               for line in md.splitlines()[:header_line])',
     '    return bool(_STALE_RE.search(md))',
     "作废声明**不再要求写在表之前** ⇒ 一张现行表可以被**文末**挂一句声明赦免。"
     "⚠️ 这条同时杀掉 `after.md` 反控",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '_STALE_RE = re.compile(r"^\\s*>\\s.*" + re.escape(_STALE_MARK), re.M)',
     '_STALE_RE = re.compile(re.escape(_STALE_MARK))',
     "作废声明不再要求是**引用行** ⇒ 散文里**提到**那六个字也算声明 ⇒ "
     "`CLAUDE.md` 第 137 层自己（它正好引用了这六个字）会把所有文件赦免",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '        found = (header, list(rows), pos)',
     '        pass',
     "★ 第三版真正缺的那一行：结果不再存进独立绑定 ⇒ 文档**末尾**的状态"
     "（几乎总是 `header=None, rows=[]`）被当成结果 ⇒ 17 条测试同时红，"
     "而红的原因是**循环变量当输出**（与第一版 `_find_table` 同一个错，"
     "只是这次写在重构的路上）",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '            if _HEADER_MARK in "".join(cand) and nxt and _is_separator(_cells(nxt.group(1))):',
     '            if _HEADER_MARK in "".join(cand):',
     "「表头后面必须跟分隔行」这条结构判据被删掉 ⇒ `CLAUDE.md` 文档索引表里"
     "任何**提到**「回本所需本金」的**数据行**都会被当成表头 ⇒ "
     "判据转而去报告「表头缺列」（第 119 层：红在一个错误的对象上）",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '_STRIKE_RE = re.compile(r"~~.*?~~", re.S)',
     '_STRIKE_RE = re.compile(r"~~", re.S)',
     "删除线只删掉两个波浪号、不删中间内容 ⇒ "
     "`docs/MISTAKE_LOG_2026-10-14.md` 那张「划掉旧值 + 补上新值」的表"
     "（它**本来就改对了**）被误判成错 ⇒ 主守卫红在一个正确的东西上",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '        c = _STRIKE_RE.sub("", c).replace("*", "").strip()',
     '        c = re.sub(r"(?!x)x", "", c).replace("*", "").strip()',
     "★ 反向对照：删除线解析被换成**恒不匹配**的正则 ⇒ `~~旧值~~ 新值` 原样留下。"
     "🚨 第一版这条变异写成「多定义一个 `_DISABLED` 但不改用点」，"
     "于是**它是个空操作** —— 而空操作会以「变异存活」的形式上报，"
     "让人以为守卫有洞（第 137 层那个 `--out default=None` 的同族："
     "看起来做过、实则什么都没做）。变异必须打在**用点**上。",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]',
     '        dirnames[:] = list(dirnames)',
     "剪枝被拆掉 ⇒ 扫描会走进 `.venv`/产物目录。"
     "⚠️ 本条只证明剪枝**有牙**，不证明它**必要**——"
     "仓里 `.venv` 下没有含该表头的 `.md`/`.py`，真拆了也不会红；"
     "但那正是「看不见」与「没有」长得一模一样的地方（第 119 层）",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),

    ("claude_capital", "tests/test_claude_md_capital_table_matches_artifact.py",
     '            label = Path(path.name)',
     '            label = path',
     "夹具路径（仓外）不再缩成文件名 ⇒ 反控拿到的是一条绝对路径 ⇒ 红。"
     "判据的**报错可读性**也是它的一部分：报一条没人看得懂的路径，"
     "等于让人学会无视这条消息",
     ["tests/test_claude_md_capital_table_matches_artifact.py"]),
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

# 🚨 **整轮变异检查的互斥锁**（2026-10-15，审查 agent 报的 P0-1）。
#   没有它，两个并发的 `mutation_check` 会这样毁掉仓：
#     A 备份**原件** → A 写变异体 A
#     B 启动，读到的 `src` **已经是 A 的变异体** → B 把它当「原件」备份
#     A 先收工：还原成**原件**
#     B 后收工：还原成 **A 的变异体**
#   ⇒ 仓里留下一个**永久变异体**，而所有守卫全绿——
#     之后每一个测量都是跑在改坏的代码上，且没有任何东西会说这件事。
#   ⇒ 病根是「备份」把「当前的磁盘内容」当成了原件；
#     互斥锁保证同一时刻磁盘上只有一个变异体的主人。
LOCK = BACKUP_DIR / ".run.lock"


class MutationLockBusy(RuntimeError):
    """另一个 `mutation_check` 正在跑。`pid` 是占用者（读不到为 None）。"""

    def __init__(self, path, pid=None):
        self.path = str(path)
        self.pid = pid
        who = f"（pid {pid}）" if pid else "（pid 未知）"
        super().__init__(f"变异检查互斥锁已被占用：{path} {who}。"
                         f"两个并发跑会把对方的变异体当成原件备份，"
                         f"收工时留下永久变异。等对方跑完再试；"
                         f"若确认对方已死，删掉该锁文件后重跑。")


def _lock_holder(lock: Path) -> str | None:
    try:
        return lock.read_text(encoding="utf-8").strip() or None
    except (OSError, ValueError):
        return None


def acquire_lock(lock: Path = LOCK) -> None:
    """取整轮互斥锁。已被占用时抛 `MutationLockBusy`。

    🚨 **不许**用 `if lock.exists(): 退出` —— 那是典型的 check-then-act：
    两个进程可以同时看到「不存在」然后都以为自己拿到了锁。
    `O_CREAT|O_EXCL` 由内核保证原子（同 `aplus/write_lock.py` 的理由）。
    """
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise MutationLockBusy(lock, _lock_holder(lock)) from None
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))


def release_lock(lock: Path = LOCK) -> None:
    """释放互斥锁。**只在锁确实是自己那个 pid 拿的时**才删。

    删别人的锁 = 让第三个人也能进来 ⇒ 并发窗口重新打开。
    """
    if _lock_holder(lock) != str(os.getpid()):
        return
    lock.unlink(missing_ok=True)


def _write_backup_atomic(bak: Path, data: bytes) -> None:
    """备份**必须原子落盘**（先写临时文件，再 `os.replace`）。

    🚨 原来的 `bak.write_bytes(backup)` 在 SIGKILL 落下时会留下一个
    **被截断的备份**，而自愈会**照样拿它还原** ⇒ 源码被写成半个文件，
    且没有 traceback。`os.replace` 在同一文件系统上是原子的，
    所以磁盘上只会出现「完整的旧备份」或「完整的新备份」。

    这是 P1-4 的正解，且**不改 `.owner` 的格式** ——
    加 sha256 校验要多一套格式与失败分支，原子写没有那些面。
    """
    tmp = bak.with_name(bak.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, bak)


def _backup_name(rel_path: str) -> Path:
    return BACKUP_DIR / (rel_path.replace("/", "__") + ".bak")


def _owner_name(rel_path: str) -> Path:
    return BACKUP_DIR / (rel_path.replace("/", "__") + ".owner")


def _proc_start(pid: int) -> str | None:
    """进程的**启动时刻**，取不到就是 `None`。pid 之外的第二身份要素。

    ⚠️ 必须钉 `LC_ALL=C`：本机默认 locale 下 `ps -o lstart=` 印的是
    `四 10/15 07:23:09 2026`（中文星期），而 `C` locale 下是
    `Thu Oct 15 07:23:09 2026`。**存的时候和读的时候 locale 必须一致**，
    否则同一个进程两次读出来不相等 ⇒ 好进程被当成复用的坏进程。

    ⚠️ 为什么用 `lstart=` 而**不是** `start=`：实测 `start=` 只有**分钟**
    分辨率（`7:23AM`），而 pid 复用完全可能发生在同一分钟内 ⇒ 判不出来。
    `lstart=` 到秒，且同一 pid 连续读 3 次**唯一值数为 1**（逐位稳定）。
    ⚠️ 本机 ps **不支持** `etimes=`（rc=1 且把帮助文本打到 stdout）。
    """
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, timeout=10,
            env=dict(os.environ, LC_ALL="C", LANG="C"))
    except Exception:
        return None
    return r.stdout.strip() or None


def _owner_is_live(owner: Path) -> bool:
    """这份 `.bak` 是不是**一个活着的 mutation_check 进程**刚写的。

    ## 为什么必须问这句话（2026-10-15 当场实测出来的真事故）

    `tests/conftest.py` 的自愈挂在**每个** pytest 会话开工前，
    而 `run_group` 自己就是「跑 pytest 子进程的那个人」——
    于是它每安放一个变异体，紧接着启动的 pytest 就把**它自己刚写的**
    磁盘备份当成「上次被中断的残留」还原掉了。

    **后果不是崩溃，是静默的假绿**：
    在**收集期就 import 了**被测模块的守卫仍会红（模块已在内存里），
    而**在测试体内才 import / 才 `read_text()` / 才 `inspect.getsource()`**
    的守卫读到的是已被还原的原件 ⇒ 全绿 ⇒ 被记成
    「存活：测试全绿，说明它没测到这个形状」。

    实测：`cap_weighted` 组 21 个变异体里 9 个这样被「杀掉」了自愈，
    报告说这 9 条守卫是安慰剂 —— **它们不是**，
    手动重跑同一个变异体每一个都真的变红。

    ⇒ 判据必须是「**这份备份归谁**」，不是「有没有备份」。

    ## 身份是**三证**，不是双证（2026-10-15 增补）

    `os.kill(pid, 0)` 单独认 pid，而 **pid 是会被复用的可复用资源**；
    加上 cmdline 也**封不住**复用 —— 因为 pid 复用给**另一个 mutation_check**
    时，cmdline 双证照样通过。三证里的第三证是**启动时刻**：

    | 要素 | 会被复用吗 | 单独够吗 |
    |---|---|---|
    | pid | **会** | 不够 |
    | cmdline 含 `mutation_check` | 不会 | 不够（另一个 mc 也含） |
    | **启动时刻 `lstart=`** | 不会（同一 pid 内不变） | **够** |

    ⇒ `.owner` 里存的是 `pid` 与 `lstart` **两行**。
    ⚠️ 旧格式（单行纯 pid）**读得出 pid 但读不出启动时刻** ——
    此时**不能**因为「拿不到第三证」就当残留（那会让在飞的变异体被还原，
    正是本函数存在要防的事故），而是退回双证并在**消息里说清降级**。

    🚨 **`pid == os.getpid()` 那条旁路已删**（原 docstring 自己写着
    「那是巧合不是判据」，而代码却无条件认）。它有一个**真实**的假阴性方向：
    上一次 mutation_check 被杀、pid 随后被**本进程**复用时，
    `_recover_pending()`（在 `main()` 里、自愈之前就会调）会判「在飞」
    ⇒ 那份真残留**永远不被还原**，而仓里没有任何东西会喊 ——
    与「备份了但没人负责读」同族，方向相反（这次不是没人读，是**读成了活的**）。
    """
    try:
        raw = owner.read_text(encoding="utf-8").strip()
    except OSError:
        return False                       # 没有主人文件 ⇒ 当残留处理
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    try:
        pid = int(lines[0]) if lines else 0
    except ValueError:
        return False                       # 主人文件损坏 ⇒ 当残留处理
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)                    # 0 号信号只探存活，不投递
    except OSError:
        return False                       # 主人已死 ⇒ 确实是残留

    # ── 第三证：启动时刻 ────────────────────────────────────────────
    # 判据是**不等**，不是相等：实测 pid 复用是真实存在的形态，
    # 而「启动时刻不相等 ⇒ 一定是别的进程」这条推理不需要知道正确答案。
    if len(lines) >= 2:
        if _proc_start(pid) != lines[1]:
            return False                   # pid 被复用 ⇒ 这不是那个主人
    # 旧格式（只有 pid）⇒ 退回双证，并**把降级说出来**。
    # ⚠️ 不能因为「拿不到第三证」就当残留：那会让在飞的变异体被还原，
    #   正是本函数存在的理由。
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        # 🚨 降级必须**可见**（与 provenance 的「依据：字节证据 / 仅 commit
        #   走查」同一条纪律）：ps 不可用时我们只剩 pid 一个证据，
        #   而 pid 会被复用 —— 静默降级等于把这条守卫悄悄废掉。
        print(f"⚠️  自愈判据降级：ps 不可用，`.owner`={owner.name} 的主人 "
              f"只验了 pid={pid}（pid 可被复用 ⇒ 双证变单证）。"
              f"此路径不会误删备份，但可能跳过一份真残留。",
              file=sys.stderr, flush=True)
        return True                        # 保守方向：宁可跳过，不可误删
    return "mutation_check" in cmd


def _recover_pending() -> int:
    """启动自愈：把上次没还原干净的源文件恢复回去。

    返回恢复的文件数。**必须在任何变异体安放之前调用**，
    否则会拿一个已被变异过的文件当「原始文件」备份，把变异固化下来。

    🚨 归**活着**的 `mutation_check` 所有的备份一律跳过 —— 见
    `_owner_is_live` 的 docstring：那不是残留，是这一轮正在飞的变异体。
    """
    if not BACKUP_DIR.is_dir():
        return 0
    n = 0
    for bak in sorted(BACKUP_DIR.glob("*.bak")):
        rel = bak.name[:-4].replace("__", "/")
        owner = _owner_name(rel)
        if owner.is_file() and _owner_is_live(owner):
            continue                        # 在飞，不是残留
        src = ROOT / rel
        data = bak.read_bytes()
        try:
            src.write_bytes(data)
        except OSError as e:
            # ★ 还原不了要**喊，不能抛**（2026-10-15 增补）。
            #   自愈现已挂在**每个** pytest 会话开工前，一次残留不该让
            #   此后每一场门禁都开不了跑 —— 那是把噪音升级成事故。
            #   同时**绝不删备份**：这串字节是原件唯一的拷贝；
            #   源文件被删/改名时更不能凭空 mkdir 造出一个脚本。
            print(f"⚠️  遗留备份还原失败（备份已保留）：{rel} —— {e}", flush=True)
            continue
        _clear_pycache(rel)
        bak.unlink()
        owner.unlink(missing_ok=True)
        n += 1
        print(f"⚠️  上次变异测试被中断，已还原：{rel}", flush=True)
    return n


def _install_signal_guards() -> None:
    def handler(signum, _frame):
        for rel, data in list(_PENDING.items()):
            try:
                (ROOT / rel).write_bytes(data)
                _clear_pycache(rel)
                _owner_name(rel).unlink(missing_ok=True)
            except Exception:                      # 还原失败也要继续还原其余的
                pass
        # 🚨 锁也必须释放：留在原地的话，**下一次**任何变异检查都会被挡住，
        #   而那条报错会让人以为是「有人正在跑」而不是「上次被 kill 了」。
        release_lock()
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
        owner = _owner_name(rel)
        # 🚨🚨 顺序必须是 **主人 → 备份 → 变异体**（2026-10-15 修正，
        #   旧守卫 `..._keeps_the_backup_then_owner_then_mutant_order`
        #   钉的恰恰是**这个危险顺序**）。
        #   `_recover_pending` 是遍历 `*.bak` 的 ⇒ **只有 `.bak` 才构成证据**。
        #   旧顺序（先 bak 后 owner）在两者之间留了一个窗口：那个窗口里起一个
        #   pytest 会看到 `.bak` 而 `.owner` 还没有 ⇒ 认成残留 ⇒ 还原并**删掉
        #   `.bak`**；随后本进程照样写下 `.owner` 与变异体 ⇒
        #   **`.owner` 在、主人活着 ⇒ 自愈永远跳过 ⇒ 永久冻结在变异态。**
        #   ⇒ 主人先写：`.owner` 早于 `.bak` 存在时是**无害的**
        #     （没有 `.bak` 就没有证据，自愈根本不会看它）。
        owner.write_text(
            f"{os.getpid()}\n{_proc_start(os.getpid()) or ''}\n", encoding="utf-8")
        _write_backup_atomic(bak, backup)
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
            owner.unlink(missing_ok=True)
            _PENDING.pop(rel, None)
        # 🚨🚨 退出码是**分层**的，不能只看「非零」（2026-10-15 第 116 层）：
        #   rc=1  测试跑了**并且红了** ⇒ 这才叫「被杀」，是变异体被抓住的唯一证据
        #   rc=0  测试跑了全绿        ⇒ 存活
        #   rc=4  用法错误（测试名拼错 / 文件不在）⇒ **一个测试都没跑**
        #   rc=5  收集到 0 个测试     ⇒ 同样什么都没跑
        # 旧写法 `if rc == 0: 存活 else: 被杀` 把 rc=4/5 也记成「被杀」——
        # **证据缺失被当成证据充分**：一条 `tests` 指向已改名/不存在的测试的
        # 变异体，会永远显示「守住了」，而实际没有任何守卫在场。
        # 本仓实测：86 行里有 4 行正是这种情况。
        if p.returncode == 0:
            notes.append(f"  ✗ 存活：{desc} —— 测试全绿，说明它没测到这个形状")
        elif p.returncode == 1:
            killed += 1
            tail = [ln for ln in p.stdout.splitlines() if "failed" in ln or "error" in ln]
            notes.append(f"  ✓ 被杀：{desc}"
                         + (f"（{tail[-1].strip()}）" if tail else ""))
        else:
            # 不可验证**不能算被杀**，也不单独算存活：分母不变、分母不变，
            # 于是它按「存活」计入存活率上界 —— 保守方向，宁可低估也不高估。
            why = "一个测试都没跑（rc=4 用法错误 / rc=5 收集为空）"
            notes.append(f"  ⛔ 不可验证：{desc} —— {why}，pytest rc={p.returncode}。"
                         f"多半是 tests 里的测试名已改名或已删除；"
                         f"**本条不构成守卫有效的证据**。"
                         + (f"\n     pytest: {p.stdout.strip().splitlines()[-1]}"
                            if p.stdout.strip() else ""))
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
    # 🚨 互斥锁必须**在自愈之前**取：自愈要动同一批 `.bak`/`.owner`，
    #   而另一个进程可能正在安放自己的变异体。两边同时动 = 上面那条 P0-1。
    try:
        acquire_lock()
    except MutationLockBusy as e:
        print(f"🚨 {e}", file=sys.stderr)
        return 3
    try:
        # 先自愈再装守卫：顺序反了会把「已被变异过的文件」当成原件备份下来，
        # 等于把一次意外固化成永久改动。
        _recover_pending()
        _install_signal_guards()
        return _run_groups(groups)
    finally:
        release_lock()


def _run_groups(groups: list[str]) -> int:
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