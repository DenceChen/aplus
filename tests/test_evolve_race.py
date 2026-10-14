"""自进化赛马的样本外协议（TDD）——防「用历史挑权重」这种数据泄露。"""
from aplus.evolve_race import oos_decision, race, split_window


def _mk(n):
    return [f"d{i:03d}" for i in range(n)]


def _k(w):
    """权重 dict → 可哈希键。"""
    return tuple(sorted(w.items()))


def _mk_ev(train_dates, spec):
    """构造 evaluate(sections, w, dates, top_n, bottom_n) -> stats。

    spec = {权重: (训练目标, 测试目标, 测试涨腿超额[, 测试跌腿日均收益])}。
    用**显式训练日期集合**判定段，不靠日期长度猜（长度判据在短窗口上会串段）。

    跌腿日均收益缺省给**负值**：真实 `rolling_stats` 一定产出 `avg_down_return`，
    不给会让「跌腿闸」在所有既有用例里都被字段缺失触发，测不出它本来要拦的东西。
    """
    tset = set(train_dates)

    def ev(sections, w, dates, top_n, bottom_n):
        item = spec[_k(w)]
        tr_obj, te_obj, te_exc = item[0], item[1], item[2]
        te_down = item[3] if len(item) > 3 else -0.001
        is_train = bool(dates) and dates[0] in tset
        return {"objective": float(tr_obj if is_train else te_obj),
                "judged": 1000,
                "stats": {"up_excess_vs_market": float(0.01 if is_train else te_exc),
                          "avg_down_return": float(-0.001 if is_train else te_down),
                          "overall_hit_rate": 0.51}}
    return ev


CHAMP, CAND = {"mom20": 1.0}, {"mom20": 2.0}


def _race(spec, weights=(CHAMP, CAND), n=100):
    xs = {"_dates": _mk(n)}
    tr, _ = split_window(xs["_dates"])
    return race(xs, list(weights), _mk_ev(tr, spec))


def _with_t(spec, weights=(CHAMP, CAND), n=100, t_train=0.2, t_test=0.6,
            breadth_t=None):
    r = _race(spec, weights, n)
    for row in r["table"]:
        row["train"]["stats"]["excess_t"] = t_train
        row["test"]["stats"]["excess_t"] = t_test
        row["test"]["stats"]["excess_days"] = 266
        if breadth_t is not None:
            row["test"]["stats"]["breadth_t"] = breadth_t
        # 2026-10-11 新增「股票池可比闸」：候选与现任的可打分股票数必须可比，
        # 缺失即视为无法核对并否决。真实 race 结果带 n_ranked（v3 报告实测 4897），
        # 这里给相同值表示两者可比；判别力由 tests/test_universe_comparability.py 负责。
        row["test"]["stats"]["n_ranked"] = 4897
    return r


# ---------- 切段 ----------

def test_split_window_is_time_ordered_and_disjoint():
    d = _mk(100)
    tr, te = split_window(d, train_ratio=0.6, embargo_days=5)
    assert tr and te
    assert max(tr) < min(te)          # 时间顺序，绝不可打乱
    assert not set(tr) & set(te)


def test_split_window_leaves_embargo_gap():
    d = _mk(100)
    tr, te = split_window(d, train_ratio=0.6, embargo_days=5)
    assert len(te) == 40              # 100 - 60
    assert len(tr) == 55              # 60 - 5 隔离带
    assert len([x for x in d if max(tr) < x < min(te)]) == 5


def test_split_window_degenerate_inputs_do_not_crash_or_overlap():
    assert split_window([]) == ([], [])
    for n in (1, 2, 3, 6):
        tr, te = split_window(_mk(n))
        assert not (set(tr) & set(te))


# ---------- 赛马排序：只看训练段 ----------

def test_race_ranks_by_train_not_by_test():
    a, b = {"mom20": 1.0}, {"mom20": 3.0}
    # a 训练赢但测试垮；b 训练平庸但测试好 → 排名必须仍以 a 为先
    r = _race({_k(a): (0.9, 0.0, 0.01), _k(b): (0.5, 0.8, 0.01)}, weights=(a, b))
    assert r["table"][0]["weights"] == a


def test_race_reports_too_short_window_instead_of_silently_ranking():
    xs = {"_dates": _mk(1)}           # 切不出两段 → 显式报错，不得静默排名
    r = race(xs, [CHAMP], _mk_ev(["d000"], {_k(CHAMP): (1.0, 1.0, 0.0)}))
    assert r["table"] == [] and r["error"]


# ---------- 晋级裁决 ----------

def test_oos_decision_rejects_train_winner_that_fails_out_of_sample():
    """训练赢、测试目标分垮 = 过拟合特征，当场否决。这正是数据泄露的防线。"""
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.002), _k(CAND): (0.90, 0.40, 0.002)},
                t_test=6.0)   # 显著，让它走到「样本外否决」那一关
    d = oos_decision(r, CHAMP)
    assert not d["promote"]
    assert "样本外否决" in d["reason"]


def test_oos_decision_promotes_only_when_both_legs_win():
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.002), _k(CAND): (0.90, 0.70, 0.002)},
                t_test=6.0)
    d = oos_decision(r, CHAMP)
    assert d["promote"]
    assert "样本外" in d["reason"] and "Bonferroni" in d["reason"]


def test_oos_decision_blocks_tiny_train_gain():
    r = _race({_k(CHAMP): (0.500, 0.500, 0.002), _k(CAND): (0.501, 0.501, 0.002)})
    d = oos_decision(r, CHAMP, min_train_gain=0.05)
    assert not d["promote"] and "不换" in d["reason"]


def test_oos_decision_keeps_champion_when_it_is_train_best():
    r = _race({_k(CHAMP): (0.9, 0.9, 0.002)}, weights=(CHAMP,))
    assert not oos_decision(r, CHAMP)["promote"]


def test_oos_decision_refuses_when_champion_absent():
    """现任不在候选集 → 拒绝盲跳（否则等于拿没比较过的东西换权）。"""
    c = {"mom20": 9.0}
    d = oos_decision(_race({_k(c): (9.0, 9.0, 0.002)}, weights=(c,)), CHAMP)
    assert not d["promote"] and "无法比较" in d["reason"]


# ---------- 硬闸：不得晋级「跑输市场」的策略 ----------

def test_oos_decision_blocks_negative_out_of_sample_excess():
    """目标函数含命中率 → 「贴近市场 + 命中率略高」的组合会赢目标分，
    但**测试段跑输市场**。对「年化 20%」这种绝对收益目标，这种策略毫无意义：
    必须设超额硬闸——命中率高不能抵消跑输大盘。"""
    r = _race({_k(CHAMP): (0.50, 0.50, 0.0006), _k(CAND): (0.60, 0.90, -0.0006)})
    d = oos_decision(r, CHAMP, min_test_excess=0.0)
    assert not d["promote"]
    assert "超额硬闸" in d["reason"]


def test_oos_decision_allows_small_positive_out_of_sample_excess():
    # 超额小但**统计显著**时仍应放行——显著性闸拦的是「不显著」，不是「小」
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.0005), _k(CAND): (0.60, 0.90, 0.0005)},
                t_test=6.0)
    assert oos_decision(r, CHAMP, min_test_excess=0.0)["promote"]


# ---------- 跌腿闸：不得晋级「跌腿其实在涨」的候选（2026-10-14 增补） ----------
#
# 背景：产品承诺是**每天 10 涨 10 跌**，跌腿是半个产品。
# 但七道闸里 `excess_t` 只测涨腿，没有任何一道闸看跌腿——
# `breadth_tstat` 早就写好了、文档里还写明它回答「两端有没有真实区分度」，
# 只是**从来没接进闸门**（缺陷 25）。
#
# 判据为什么用**绝对收益**而不是「跑输大盘」：5 万本金做不了空，
# 跌腿对用户的价值是「这 10 只别买」，不是「它们比大盘跌得多」。
# 实测 97 组里只有 45/97 的跌腿跑赢大盘（约等于随机），
# 按那个设闸等于否掉 52/97 的典型候选——否掉多数的闸没有判别力。
#
# ## 这道闸立刻抓得到一个真东西
#
# 现任权重实测 `avg_down_return = +0.126%/日`：
# 它推的「10 只会跌」平均每天**在涨**。不是统计口径问题，是承诺没兑现。

def test_oos_decision_blocks_candidate_whose_down_leg_rises():
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.002),
                 _k(CAND): (0.90, 0.70, 0.008, +0.00126)},
                t_test=6.0)
    d = oos_decision(r, CHAMP)
    assert not d["promote"], "跌腿在涨 = 「10 跌」那一半是假的，绝不能晋级"
    assert "跌腿" in d["reason"]


def test_missing_down_leg_data_is_treated_as_unprovable():
    """跌腿字段缺失不得当作「没问题」放行——与 n_ranked / excess_t 同一处理原则。

    更要紧的是**理由必须说「无法证明」**：把缺失折成 0 再拿 0 去比，
    拦截照样成立（0 ≥ 0），但理由会变成「测得它并不真的下跌」——
    拿一个没测过的数去下一个测过的结论。这套闸的老规矩是
    「证据缺失一律沉默」，判决词也不例外。
    """
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.002), _k(CAND): (0.90, 0.70, 0.008)},
                t_test=6.0)
    for row in r["table"]:
        row["test"]["stats"].pop("avg_down_return", None)
    d = oos_decision(r, CHAMP)
    assert not d["promote"] and "跌腿" in d["reason"]
    assert "无法证明" in d["reason"], \
        f"缺字段时理由必须说「无法证明」，实得：{d['reason']}"


def test_measured_flat_down_leg_is_reported_as_measured_not_missing():
    """反方向：跌腿**测得**是 0（不跌）也要拦，但理由不能说「无法证明」。

    它确实测了，只是测出来不下跌。把这条和上面那条混为一谈，
    就再也不知道报告里的否决到底是「测出来的」还是「没测」。
    """
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.002),
                 _k(CAND): (0.90, 0.70, 0.008, 0.0)},
                t_test=6.0)
    d = oos_decision(r, CHAMP)
    assert not d["promote"], "跌腿测得是 0 = 不下跌，承诺未兑现，必须拦"
    assert "无法证明" not in d["reason"], \
        f"这一支测过了，理由不该说「无法证明」：{d['reason']}"
    assert "并不真的下跌" in d["reason"]


def test_candidate_winning_both_legs_still_promotes():
    """反方向：涨跌两腿都成立时必须仍能晋级——证明跌腿闸不是无脑全否。"""
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.002),
                 _k(CAND): (0.90, 0.70, 0.008, -0.001188)},
                t_test=6.0)
    assert oos_decision(r, CHAMP)["promote"] is True


def test_promotion_reason_flags_when_only_the_up_leg_carries_significance():
    """广度不显著时**不否决**（涨腿 alpha 是真的），但必须在理由里说出来。

    否决真 alpha 是另一种错；让人误以为两个半边产品一样强则是第一等的错。
    实测 `{mom20:-1.0}` K=63：涨腿 t=+4.76 过闸，广度 t 只有 +1.005。
    """
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.002),
                 _k(CAND): (0.90, 0.70, 0.008, -0.001188)},
                t_test=6.0, breadth_t=1.005)
    d = oos_decision(r, CHAMP)
    assert d["promote"] is True
    assert "广度" in d["reason"] and "涨腿独撑" in d["reason"]


def test_promotion_reason_omits_breadth_warning_when_breadth_is_significant():
    """反方向：广度显著时不得再挂警告——否则警告就成了噪声。"""
    r = _with_t({_k(CHAMP): (0.50, 0.50, 0.002),
                 _k(CAND): (0.90, 0.70, 0.008, -0.001188)},
                t_test=6.0, breadth_t=5.2)
    d = oos_decision(r, CHAMP)
    assert d["promote"] is True and "涨腿独撑" not in d["reason"]


# ---------- 显著性硬闸（2026-10-11 增补） ----------
#
# 背景：A/B 实跑发现修好成交前视后，旧口径 0/14 过闸变成新口径 1/14 过闸，
# 裁决「两段双赢」差点把权重写进线上配置。随后 30 组**随机权重**对照显示：
# 1/30 也能过同一道闸，且最好的随机（+0.0801%）比真赢家（+0.0342%）还高。
# 赢家的测试段 t = +0.60、双侧 p = 0.549，与 0 不可区分。
#
# 也就是说：原来的三道闸（训练领先 / 超额为正 / 不劣于现任）在
# **多重比较**面前形同虚设——14 组里总会有 1 组靠运气过关。
# 缺的是「这个超额是不是比随机权重强」。

def test_candidate_failing_significance_is_rejected_even_if_excess_positive():
    """超额为正、训练也领先，但 t 检验与 0 不可区分 → 必须否决。"""
    spec = {_k(CHAMP): (0.10, 0.10, 0.001), _k(CAND): (0.50, 0.30, 0.0004)}
    dec = oos_decision(_with_t(spec), CHAMP)
    assert dec["promote"] is False, "t=0.6 不显著，绝不能晋级"
    assert "显著" in dec["reason"] or "t 检验" in dec["reason"]


def test_significant_candidate_can_still_promote():
    """反方向：t 远超阈值时仍要能晋级——证明不是无脑全否。"""
    spec = {_k(CHAMP): (0.10, 0.10, 0.001), _k(CAND): (0.50, 0.30, 0.008)}
    dec = oos_decision(_with_t(spec, t_test=6.0), CHAMP)
    assert dec["promote"] is True


def test_bonferroni_threshold_accounts_for_candidate_count():
    """候选越多，显著性门槛越高——否则大池子里总能捞到一个。"""
    spec = {_k(CHAMP): (0.10, 0.10, 0.001), _k(CAND): (0.50, 0.30, 0.0004)}
    # t=2.5：2 组比较下 Bonferroni 临界 2.241 → 过关；10 组下临界 2.807 → 不过关
    small = oos_decision(_with_t(spec, t_test=2.5), CHAMP, significance_lookup=2)
    many = oos_decision(_with_t(spec, t_test=2.5), CHAMP, significance_lookup=10)
    assert small["promote"] is True, f"2 组比较下 t=2.5 应过关，实得 {small}"
    assert many["promote"] is False, "10 组比较下 t=2.5 必须被 Bonferroni 拦下"


def test_missing_significance_data_is_treated_as_not_significant():
    """老记录没有 excess_t 字段时，不得因为「字段缺失」就默认放行。"""
    spec = {_k(CHAMP): (0.10, 0.10, 0.001), _k(CAND): (0.50, 0.30, 0.0004)}
    dec = oos_decision(_race(spec, [CHAMP, CAND]), CHAMP)     # 无 excess_t
    assert dec["promote"] is False, "缺显著性数据 = 不可证明 = 否决"
