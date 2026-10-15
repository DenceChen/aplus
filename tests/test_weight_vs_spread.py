"""权重的目标函数 vs 尾部价差的守卫（2026-10-15）。

## 为什么这一组特别要紧

它回答的是「**权重投给了谁**」。而这个答案与「策略赚不赚钱」是两件事：

    权重投错了 ⇒ 合成分数没有排序力 ⇒ 但报告里只看得见一个
                  `monotonic_increasing=False`

⇒ 两种病因（换因子 / 换目标函数）的**症状完全一样**，
而修法完全不同。所以守卫必须钉住**它量的是哪一层**。
"""
import json
import subprocess
import sys
from math import sqrt
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import weight_vs_spread as wvs  # noqa: E402

ART = ROOT / "data/reports/weight_vs_spread.json"
IC_REPORT = ROOT / "data/reports/factor_ic_7y_v2.json"
WF_REPORT = ROOT / "data/reports/wf_weights_backtest_k21.json"


# ══════════ 判据阈值必须**算**且取保守侧，不许抄表 ══════════

def test_the_two_critical_value_paths_disagree_and_the_conservative_one_wins():
    """n=8 上精确置换表（0.7386）与 t 近似（0.7065）**不一致**，且必须取大的那个。

    ## 为什么这不是「抄错了一个数」而是「两条推导路径真的不同」

    `t = ρ√(n−2)/√(1−ρ²)` 这个近似在 df=6 上给出的 t 是 **2.684**，
    而 df=6 的精确双侧 0.025 分位数是 **2.4469** ⇒ 近似把门槛**抬高** 9.7%
    ⇒ 反解出的 ρ 偏小 ⇒ **更容易判显著**。

    ★ 错误方向：t 近似**反保守**。取小的那个等于凭空发放显著性
    —— 与第 116 层（rc=4「没跑」被算成「跑红了」）是**同一个错误方向**。

    ⇒ 所以断言不只是「取 max」，还要断言**不一致这件事被暴露出来**
    （`crit_paths_agree is False`）。若有人把两条路「统一」成一条，
    这条会红 —— 那说明有人为了让它看起来一致而改了其中一个。
    """
    exact = wvs.spearman_crit(8)
    approx = wvs.spearman_crit_t_approx(8)
    assert abs(exact - 0.7386) < 1e-4, "精确置换表的 n=8 值被改了"
    assert abs(approx - 0.7065) < 1e-3, "t 近似的值漂了（推导路径被改过）"
    assert exact > approx, "取保守侧意味着必须取精确表；取小的那个是反保守"
    assert wvs.spearman_crit(8) == max(exact, approx)


def test_the_critical_value_table_cannot_silently_shift_by_one_row():
    """这张表有个**陷阱位**：n=7 是 0.9643、n=8 是 0.7386（突然掉一大截）。

    少读一行就变成 7×8 的表 ⇒ n=8 拿到 0.9643 ⇒ 门槛高 31%
    ⇒ **判决会整个翻过来**（本仓实测 ρ = −0.9286 仍能过，
    但任何 ρ 落在 (0.7386, 0.9643] 的因子会被错判成不显著）。

    ⇒ 断言 n=7 与 n=8 **不相等**，且都等于表里的那个数。
    """
    assert wvs._SPEARMAN_TABLE[7] == 0.9643
    assert wvs._SPEARMAN_TABLE[8] == 0.7386
    assert wvs.spearman_crit(7) != wvs.spearman_crit(8)


@pytest.mark.parametrize("n", [1, 2, 3])
def test_too_small_n_is_refused_rather_than_extrapolated(n):
    """n<4 拒绝而不是外插。

    ⚠️ **永远红的守卫会把人推向错误的修法**（第 112 层），
    而这里更糟：外插出来的临界值是一个**看起来合理**的假数字，
    它不会让任何东西变红，只会让判决悄悄用错门槛。
    """
    with pytest.raises(ValueError, match="退化"):
        wvs.spearman_crit(n)


def test_n_outside_the_exact_table_is_refused_not_interpolated():
    """表外的 n 不许插值 —— 插值同样是「造一个看起来合理的假数字」。"""
    with pytest.raises(ValueError, match="不在精确表里"):
        wvs.spearman_crit(60)


# ══════════ 「重叠」与「前视」是两个**独立**的维度 ══════════

def test_a_leaky_window_is_never_usable_even_when_it_has_no_overlap():
    """🚨🚨 这一条钉的是**我自己写完当场犯的错**（形状 7）。

    `lag0_leaky` 的前瞻窗口是 `(-1, 0)` ⇒ 重叠长度
    `max(0 - (-1), 1) = 1` ⇒ **它的 overlap_factor 也是 1.0**。

    第一版的候选筛选只写了 `overlap_factor == 1.0` ⇒ leak 窗口**进了候选池**
    ⇒ 再用 `min()` 挑「最干净」的那个 ⇒ **按字母序挑中了 leak**
    ⇒ 判决建立在一个**用了信号日之前数据**的窗口上。

    ★ 而它**不报错、不崩**，还产出一个完整的判决字典。
    这是最坏的一类：证据齐整、口径有毒。

    ⇒ 构造一个「leak 窗口**零重叠**、字母序在前」的产物：
    若筛选只看重叠，leak 窗口会进 `usable` ⇒ 红。

    ## 这条守卫的牙在**集合成员**，不在 cond1（第一版的自我描述过头了）

    第一版写「若筛选只看重叠，判决条件会被 leak 窗口的数满足 ⇒ 必须红」。
    🚨 **那是假的**：新夹具里两个窗口的 signed ρ 都是 −1.0 ⇒ cond1 都是 True
    ⇒ 把 leak 放进来 `all()` **照样是 True** ⇒ 通过 cond1 **观察不到**筛选坏没坏。
    ⇒ 牙只有两处，都在本条断言里：`usable_windows` 与 `per_window_verdict` 的成员。
    ⚠️ 记下来是因为「守卫的自我描述不可信，就是守卫本身不可信的信号」——
    而这一条是**我自己**又犯了一次（第一次犯在 `test_all_windows_...` 上）。
    """
    ic = {"windows": ["lag0_leaky", "lag1_real"],
          "fwd_windows": {"lag0_leaky": [-1, 0], "lag1_real": [0, 1]},
          # leak 窗口的 mom20 低尾命中率拉到 0.95（价差最大 ⇒ IC 最负），
          # 形状与其他窗口同构 —— 它**只**该因为含前视而被排除，不该因为别的
          "table": _ic_table(extra_leak=0.95)}
    wf = _wf()
    res = wvs.diagnose(_write_ic(ic), _write_wf(wf))
    assert "lag0_leaky" not in res["usable_windows"], \
        "含前视的窗口进了可用集合 —— 这会把判决建在用了未来数据的口径上"
    assert res["usable_windows"] == ["lag1_real"]
    assert "lag0_leaky" not in res["per_window_verdict"], \
        "leak 窗口的判决条件不该被算出来 —— 算了就说明它进了判决链"
    # leak 窗口**零重叠**（fwd=(-1,0) ⇒ 重叠长度 max(0−(−1),1)=1 ⇒ 倍数 1.0），
    # 所以只看重叠的筛选**放它过** ⇒ 这条断言确实卡的是「前视」这一维
    assert res["windows"]["lag0_leaky"]["overlap_factor"] == pytest.approx(1.0)
    assert res["per_window_verdict"]["lag1_real"]["cond1_ic_antispread"] is True


def test_the_leak_test_is_a_negative_index_not_a_name_match():
    """判据是 `fwd[0] < 0`，**不是** `fwd[0] < 1`，也不是按名字匹配。

    ⚠️ 我第二版写成 `fwd[0] < 1` ⇒ 四个窗口**全部**被判成有前视
    ⇒ `usable` 为空 ⇒ 判决拒绝作出。
    那是**过严**，而过严的守卫会把人推向「去改本来正确的 `FWD_WINDOWS`」
    —— 第 112 层的同一个坑。

    ⇒ 正控：`hold4 = (0, 4)` 的第一维是 0，**必须**判为无前视。
    """
    assert int((0, 4)[0]) >= 0, "入口滞后由 entry_lag 加在因子上，不在窗口里"
    out = wvs.diagnose(_write_ic(_ic_fixture()), _write_wf(_wf()))
    assert "hold4" in out["windows"]
    assert out["windows"]["hold4"]["lookahead"] is False, \
        "(0,4) 被判成有前视 ⇒ 判据写成了 fwd[0] < 1，那是错的"


def test_overlap_and_lookahead_are_reported_as_two_separate_numbers():
    """两个维度必须**各自**落在产物里，不能合成一个「干净度」分数。

    合成之后就没法审计了 —— 而「到底哪个窗口被判了、为什么」
    正是读判决时唯一需要的东西。
    """
    out = wvs.diagnose(_write_ic(_ic_fixture()), _write_wf(_wf()))
    for name, v in out["windows"].items():
        assert isinstance(v["lookahead"], bool), f"{name} 缺 lookahead 标记"
        assert v["overlap_factor"] > 0, f"{name} 缺重叠倍数"
        assert v["fwd_window"], f"{name} 没把前瞻窗口本身印出来（口径要跟着数字走）"


# ══════════ 重叠修正：t 值被放大了多少，必须印出来 ══════════

def test_the_hold21_window_reports_only_a_twenty_first_of_its_observations():
    """hold21：1801 天 ⇒ 独立观测只有 ~86 个，t 被放大约 √21。

    产物里 `hold21` 的 `t` 高达 24.05（rev5 低尾）——
    不报重叠就会被读成强证据，而它其实只是**重叠**的强证据。
    """
    assert wvs.effective_independent(1801, (0, 21)) == pytest.approx(1801 / 21)
    assert wvs.effective_independent(1821, (0, 1)) == 1821.0
    out = wvs.diagnose(_write_ic(_ic_fixture()), _write_wf(_wf()))
    h = out["windows"]["hold21"]
    assert h["overlap_factor"] == pytest.approx(21.0)
    assert h["effective_independent_obs"] < 100


# ══════════ 秩相关本身：并列必须取平均秩 ══════════

def test_tied_values_get_average_ranks_not_arbitrary_positions():
    """尾部价差在 n=8 上**很容易并列**（两个因子低尾都是 0.5000）。

    若用「按值排序后编号」当秩：
      —— 排序稳定时两列会把并列项排到相邻位置，产生虚假的秩差
      —— 且 `sorted` 的稳定性依赖输入顺序 ⇒ 换一天数据就换一个答案
    ⇒ 平均秩是 Spearman 的定义。
    """
    a = [1.0, 2.0, 3.0, 4.0]
    assert wvs._ranks(a) == [1.0, 2.0, 3.0, 4.0]
    tied = wvs._ranks([1.0, 2.0, 2.0, 4.0])
    assert tied == [1.0, 2.5, 2.5, 4.0], "并列项必须取平均秩"
    # 自反性：两列全并列 ⇒ 秩方差为 0 ⇒ 分母为 0 ⇒ 必须返回 NaN 而不是 1.0
    assert wvs.spearman([1.0, 1.0], [5.0, 6.0]) != wvs.spearman([1.0, 1.0], [5.0, 6.0])
    import math
    assert math.isnan(wvs.spearman([1.0, 1.0], [5.0, 6.0])), \
        "两列完全并列时相关系数未定义，返回 1.0 会凭空造出一个「完全正相关」"


def test_spearman_matches_a_hand_computed_value():
    """手算对拍：n=4 完全单调 ⇒ ρ 恰为 1。"""
    assert wvs.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert wvs.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


# ══════════ 判决：两个条件缺一不可 ══════════

def test_the_verdict_requires_both_conditions_not_just_one():
    """只有①（IC 与尾部反向）没有② ⇒ 权重其实已经纠正了 ⇒ 不判失配。

    这是**反向对照臂**：它证明判决不是一个恒真式。
    构造：IC 与尾部反向，但权重**完美跟着尾部走**（|w| 排序 == 尾部排序）。
    """
    ic = _ic_fixture()
    # 让 |w| 严格正比于尾部价差 ⇒ cond2 为假
    wf = _wf()
    spread = {r["factor"]: r["tail_low_hit"] - r["tail_high_hit"]
              for r in ic["table"] if r["window"] == "lag1_real"}
    wf["years"][0]["weights"] = {f: spread[f] for f in spread}
    res = wvs.diagnose(_write_ic(ic), _write_wf(wf))
    assert res["cond_ic_antispread"] is True, "正控臂的①必须成立，否则这条测不到东西"
    assert res["cond_weight_not_following_spread"] is False
    assert res["verdict"] != "WEIGHT_TARGET_MISMATCH", \
        "只有①没有②就判失配 ⇒ 判决是恒真的"


def test_all_windows_must_agree_not_just_one_of_them():
    """🚨🚨 变异台实测 **W8 存活**：`all()` 换成 `any()` 17 条测试全绿。

    ## 为什么曾经活下来

    第一版的筛选是 `not lookahead AND overlap_factor == 1.0`，
    真产物里**只剩一个**合格窗口（`lag1_real`）⇒ 在 n=1 上
    `all(...)` 与 `any(...)` **恒等** ⇒ 那条分支**从未分岔过**
    ⇒ 断言它时用的输入没有区分力（第 9 种形状：「跑过了」≠「那条分支跑过了」）。

    ## 为什么现在还需要夹具

    筛选已经修好（重叠不再排除窗口），真产物有三个合格窗口，
    现场已能分岔。但**现场数据的形状不由测试决定**——
    夹具能失效的方式就是「夹具被改小」，所以这条必须保留。

    ## 本条的判据

    构造**两个**合格窗口：一个满足 cond1、另一个不满足。
    ⇒ `all()` 必须为 **False**（结构主张不得只在部分窗口上成立）
    ⇒ `any()` 会为 True ⇒ 变异当场变红。

    ⚠️ 这条**只**钉住 `cond1`。`cond2` 的 `all()` 换 `any()`
    在真产物上是空操作（三个窗口 cond2 全 True）⇒ 变异 W14 曾存活
    ⇒ 由 `test_cond2_must_also_require_every_window_not_just_one` 单独钉。
    ⇒ 教训：**一条守卫的自我描述不可信，就是守卫本身不可信的信号**
    （我当时写「同时钉住 cond2」，而它并没有）。
    """
    ic = _ic_fixture()
    # 加第二个**合格**窗口（无前视 + 零重叠），且它的 cond1 为假：
    # 令**带符号** IC 与尾部价差**同向** ⇒ ρ = +1.0 ⇒ `rho <= -crit` 不成立。
    # ⚠️ 价差必须**严格单调**（第一版手抄成八个都差 0.01 ⇒ 全是并列
    #   ⇒ 秩相关退化 ⇒ ρ = NaN，而 `NaN <= -crit` 是 False ⇒ 正控当场亮红灯）。
    same_order = [("mom20", 0.60), ("rev5", 0.50), ("vol20", 0.40), ("vsur", 0.30),
                  ("turn20", 0.20), ("turnratio", 0.15), ("ivol", 0.10), ("rsi", 0.05)]
    for f, sp in same_order:
        ic["table"].append({"factor": f, "window": "lag2_real", "all": sp,
                            "all_t": 1.0, "tail_high_hit": 0.30,
                            "tail_low_hit": 0.30 + sp, "n_days": 1820})
    ic["windows"].append("lag2_real")
    ic["fwd_windows"]["lag2_real"] = [0, 2]

    res = wvs.diagnose(_write_ic(ic), _write_wf(_wf()))
    # ⚠️ 不断言 `usable` 的**集合形状**：夹具带几个窗口是无关事实，
    #   写死集合会让「多加一个窗口」这种正常改动变成红灯，
    #   而红灯会诱使人去收紧筛选（第 112 层，最糟的方向）。
    #   只钉**有区分力的那一对**。
    assert len(res["usable_windows"]) >= 2, \
        f"正控失效：需要 ≥2 个合格窗口，实际 {res['usable_windows']}"
    assert res["per_window_verdict"]["lag1_real"]["cond1_ic_antispread"] is True
    assert res["per_window_verdict"]["lag2_real"]["cond1_ic_antispread"] is False, \
        "第二个窗口必须**不**满足 cond1，否则 all/any 恒等、分不出"
    assert res["cond_ic_antispread"] is False, \
        "只有一个窗口满足就判成立 ⇒ 判决用的是 any() ⇒ 结构主张可以只在部分窗口上成立"


def test_the_real_artifact_has_more_than_one_usable_window_so_all_and_any_differ():
    """诚实记账：真产物里**必须**有 ≥2 个合格窗口，all/any 才有区分力。

    ## 这条守卫自己的历史（两代都错，记下来）

    **第一代**：筛选条件是 `not lookahead AND overlap_factor == 1.0`
    ⇒ 真产物只剩 `lag1_real` **一个** ⇒ `all()` 与 `any()` **恒等**
    ⇒ 变异台 W8（`all`→`any`）**存活**，17 条测试全绿。
    ⇒ 这就是**过严的筛选（第 112 层）**在生产里的样子：
    **它让守卫变成安慰剂，而且症状和「守卫写对了」一模一样。**

    **第二代**：把重叠从筛选里拿掉（重叠不使窗口无效，只让估计更噪）。
    ⇒ 合格窗口 = `lag1_real` + `hold4` + `hold21` 三个
    ⇒ `all()` 在**现场**与 `any()` 分岔，不再依赖夹具。
    ⇒ 上面那条夹具守卫仍保留：夹具能失效的方式是「夹具被改小」，
    而现场数据的形状不由测试决定。

    ⚠️ 这条**必须**钉 `>= 2` 而不是 `== 3`：窗口表是会被加窗口的，
    写死 3 会让「新增一个窗口」这种正常改动变成红灯，
    而红灯会诱使人把筛选改回过严版本（更糟，且看起来像在修问题）。
    """
    if not ART.is_file():
        pytest.skip("weight_vs_spread.json 尚未生成")
    d = json.loads(ART.read_text(encoding="utf-8"))
    assert len(d["usable_windows"]) >= 2, \
        (f"合格窗口只有 {len(d['usable_windows'])} 个 ⇒ all/any 现场恒等，"
         "W8 会复活。若新加的窗口被判为前视或被过严的规则筛掉，"
         "**别收紧筛选**——那是把守卫变回安慰剂。")
    # 判据是 fwd[0] >= 0，不含前视的窗口都必须留下。
    for k in d["windows"]:
        if not d["windows"][k]["lookahead"]:
            assert k in d["usable_windows"], \
                f"{k} 不含前视却不在 usable 里 ⇒ 筛选里混进了第二条规则"


def test_a_window_that_fails_the_bar_must_weaken_the_verdict_not_be_dropped():
    """🚨 判决变弱时**不许**靠「把它筛掉」来实现 —— 那是第 7 种形状。

    真产物里 `hold21` 的 |ρ| = 0.6429 不过 0.7386 的门槛
    ⇒ `all()` 为 False ⇒ 判决降级为 `DIRECTION_CONSISTENT_BUT_BAR_NOT_CLEARED_EVERYWHERE`。
    ⇒ 三条同时钉：
      ① `hold21` **仍在** `usable_windows` 里（没被偷偷丢掉）
      ② 它在 `per_window_verdict` 里且 cond1 为 False
      ③ 判决是「一致但未处处越过门槛」，**不是** `WEIGHT_TARGET_MISMATCH`

    ⚠️ 反过来的诱因很真实：看到判决变弱，最省事的「修法」是
    往筛选里加一条规则把那个窗口排掉 —— 判决立刻变强，测试立刻变绿，
    而**结论一个字节都没变**。
    """
    if not ART.is_file():
        pytest.skip("weight_vs_spread.json 尚未生成")
    d = json.loads(ART.read_text(encoding="utf-8"))
    weak = [k for k, v in d["per_window_verdict"].items()
            if not v["cond1_ic_antispread"]]
    for k in weak:
        assert k in d["usable_windows"], (
            f"{k} 的 cond1 为 False 却在 usable 之外 ⇒ "
            "判决变弱不是被数据判出来的，是被筛选藏起来的")
    if weak and all(d["per_window_verdict"][k]["cond2_weight_not_following"]
                    for k in d["usable_windows"]):
        assert d["verdict"] != "WEIGHT_TARGET_MISMATCH", \
            (f"有窗口没过门槛（{weak}）却仍报 WEIGHT_TARGET_MISMATCH ⇒ "
             "把「一致」当成了「显著」")
        # ⚠️ 这里**不再**钉死是哪一个「弱」标签：
        #   2026-10-15 起 ρ 的方向有两个含义完全相反的分支
        #   （反向 = 有病灶 / 同向 = 本就一致），而**哪一个分支**
        #   取决于带符号 ρ 的正负，由 `test_the_middle_verdict_label_requires_a_
        #   negative_correlation` 单独钉。
        #   钉死标签会把「换个窗口」这种数据变化变成红灯，
        #   而红灯会诱使人去动筛选（第 112 层，最糟的方向）。
    assert d["rho_sign_consistent_across_all_windows"] is True, \
        "各窗口的 ρ 符号一致；这条若不成立，说明窗口数据变了，须重判"


def test_the_verdict_is_reported_together_with_the_conditions_that_made_it():
    """判决与它的两个条件必须一起落进产物。

    ⚠️ 只印判决不印条件，下一个人就得回去猜「凭什么」——
    而**判据必须自证**是本仓第一条纪律（形状 22 的教训：
    一句声称「判据已预注册」的注释，指向一个不存在的登记）。
    """
    out = wvs.diagnose(_write_ic(_ic_fixture()), _write_wf(_wf()))
    for key in ("verdict", "cond_ic_antispread", "cond_weight_not_following_spread",
                "usable_windows", "per_window_verdict", "crit_why_max"):
        assert key in out, f"产物缺 {key} —— 判决没有自证"
    assert "G1" in out["读法"] and "G6" in out["读法"], \
        "读法必须说清本判决**只覆盖「权重投给了谁」**，不覆盖「赚不赚钱」"


# ══════════ 缺档必须报错 ══════════

def test_a_missing_artifact_errors_out_instead_of_producing_an_empty_verdict():
    for which in ("ic", "wf"):
        p = (ROOT / f"data/reports/definitely_absent_{which}.json")
        args = ["--ic" if which == "ic" else "--wf", str(p)]
        r = subprocess.run([sys.executable, str(ROOT / "scripts/weight_vs_spread.py"),
                            *args], capture_output=True, text=True)
        assert r.returncode != 0, f"{which} 缺档时退出码仍是 0"
        assert "缺档" in r.stdout, f"{which} 缺档没有明确报错"


def test_a_window_with_the_opposite_sign_must_make_the_direction_inconsistent():
    """🚨 变异台实测 **W11 存活**：`rho_sign_consistent... = True` 写死，21 passed。

    ## 为什么活下来了

    真产物里四个窗口的 ρ **恰好全是负的** ⇒ 那个判断在真数据上**恒等于 True**
    ⇒ 任何「直接写 True」的变异都是**空操作**。
    对账守卫也救不了它 —— **值没变**，对账当然通过。
    ⇒ 这是第 9 种形状的又一例：**必须造出让值分岔的输入**。

    ## 本条的判据

    加一个 ρ 为**正**的窗口（|IC| 与尾部价差同向）⇒ 符号发散
    ⇒ `rho_sign_consistent_across_all_windows` 必须为 **False**
    ⇒ 写死 True 的变异当场变红。
    """
    ic = _ic_fixture()
    # ⚠️ 价差必须**严格单调**，且与**带符号** IC 同向递减（两个序列一起动）：
    #   第一版八个价差全相等 ⇒ 全是并列 ⇒ 秩相关退化
    #   ⇒ ρ ≈ 0（正控当场亮红灯，这正是正控的用途）。
    #   第二、三版单调对了却在 `|IC|` 约定下校准 ——
    #   而 2026-10-15 起判决走**带符号** IC（本条是该修正的第一个受害者）。
    #   ⇒ 现在：`tail_low_hit` 与 `all` **同时**随 rank 递减 ⇒ ρ_signed = +1.0。
    #   ⇒ 同一夹具在 `|IC|` 约定下给 ρ = −1.0 ⇒ **两个字段符号相反**，
    #     这正是形状 119 在夹具层的自证。
    order = [("mom20", -0.20), ("rev5", -0.30), ("vol20", -0.40), ("vsur", -0.50),
             ("turn20", -0.60), ("turnratio", -0.70), ("ivol", -0.80), ("rsi", -0.90)]
    for rank, (f, icv) in enumerate(order):
        ic["table"].append({"factor": f, "window": "lag3_pos", "all": icv,
                            "all_t": 1.0, "tail_high_hit": 0.50,
                            "tail_low_hit": 0.50 + (8 - rank) * 0.01,
                            "n_days": 1820})
    ic["windows"].append("lag3_pos")
    ic["fwd_windows"]["lag3_pos"] = [0, 1]

    res = wvs.diagnose(_write_ic(ic), _write_wf(_wf()))
    assert res["windows"]["lag3_pos"]["rho_signedIC_vs_spread"] > 0, \
        "正控失效：这个窗口本该 ρ_signed > 0"
    assert res["windows"]["lag3_pos"]["rho_absIC_vs_spread"] < 0, \
        ("本夹具 |IC| 与价差反向、带符号 IC 与价差同向 —— "
         "两者符号相反正是这条守卫要钉的分岔点")
    assert res["rho_sign_consistent_across_all_windows"] is False, \
        "有窗口符号相反却报「方向一致」⇒ 中间态判决会被错误地打开"


def test_cond2_must_also_require_every_window_not_just_one():
    """🚨 变异台实测 **W14 存活**：`cond2` 的 `all()` 换 `any()`，21 passed。

    ## 为什么活下来了

    真产物里 cond2 在三个窗口上**全是 True** ⇒ `any()` 与 `all()` **恒等**
    ⇒ 又是「值没变」的空操作（第 9 种形状）。
    ⚠️ 而 `test_all_windows_must_agree_not_just_one_of_them` 的 docstring 里
    写着「这条同时钉住 cond2」—— **它并没有**。那句话当时是假的。
    ⇒ 守卫的自我描述不可信，就是守卫本身不可信的信号。

    ## 本条的判据

    构造一个 cond2 为**假**的窗口（|w| 与尾部价差强正相关 ⇒ ρ ≥ crit）
    ⇒ `all()` 必须为 **False** ⇒ `any()` 会为 True ⇒ 变异当场变红。

    ⚠️ 夹具**从权重算顺序**，不手抄：第一版我手写了一个八元组去 zip
    权重序，正控立刻报「夹具错位：权重序 turnratio vs 序位 mom20」。
    ⇒ **手抄顺序必错**；让数据自己推出顺序，别让测试去核对人的记性。
    """
    ic = _ic_fixture()
    by_absw = sorted(_wf()["years"][0]["weights"].items(), key=lambda kv: abs(kv[1]))
    assert len(by_absw) == 8, f"夹具前提不成立：权重只有 {len(by_absw)} 个"
    for rank, (f, _w) in enumerate(by_absw):
        ic["table"].append({"factor": f, "window": "lag4_align", "all": -(0.9 - rank * 0.1),
                            "all_t": 1.0, "tail_high_hit": 0.50,
                            "tail_low_hit": 0.50 - (7 - rank) * 0.02,
                            "n_days": 1820})
    ic["windows"].append("lag4_align")
    ic["fwd_windows"]["lag4_align"] = [0, 1]

    res = wvs.diagnose(_write_ic(ic), _write_wf(_wf()))
    assert res["windows"]["lag4_align"]["rho_absW_vs_spread"] > 0, \
        "正控失效：这个窗口本该 ρ(|w|, spread) > 0"
    assert res["per_window_verdict"]["lag4_align"]["cond2_weight_not_following"] is False
    assert res["cond_weight_not_following_spread"] is False, \
        "只有一个窗口满足就判 cond2 成立 ⇒ 用的是 any() ⇒ " \
        "「权重不跟随尾部价差」只需在部分窗口上成立就被认了"


# ══════════ 真产物：自洽 + 判决与现场一致 ══════════

def test_the_committed_artifact_is_what_the_current_code_produces():
    """🚨🚨 **代码与已落盘产物必须对账** —— 这条守卫是被三条存活的变异逼出来的。

    ## 变异台实测：W11 / W12 / W14 全存活，20 passed

    三条都**只改判决**（方向一致性判定、三态中间态、cond2 的 all/any），
    而所有真产物守卫都是 `json.loads(ART.read_text())` ——
    **读一个冻结的快照，不重算**。
    ⇒ 改代码对它们**不可见** ⇒ 「20 passed」只证明
    **那个 json 文件**合规，与**此刻的代码**毫无关系。

    ⚠️ 这是比「守卫写弱了」更坏的一种绿：它不是没问，是**问了旧的东西**。
    同类风险在别处也有 —— 产物一旦落盘，代码与产物的关系就断了，
    除非有人每次都重新对账。

    ## 本条的判据

    从**真实输入**重算，与落盘产物的判决相关字段**逐项相等**。
    ⇒ 任何改动判决的变异立刻变红（W11/W12/W14）；
    ⇒ 代码改了没重算产物，也立刻变红 —— 这是故意的：
    逼人重算，而不是让一个**过期的判决**继续冒充当天的结论。
    """
    if not ART.is_file():
        pytest.skip("weight_vs_spread.json 尚未生成")
    fresh = wvs.diagnose(wvs.IC_REPORT, wvs.WF_REPORT)
    disk = json.loads(ART.read_text(encoding="utf-8"))
    for key in ("verdict", "cond_ic_antispread", "cond_weight_not_following_spread",
                "usable_windows", "rho_sign_consistent_across_all_windows",
                "per_window_verdict"):
        assert fresh[key] == disk[key], (
            f"产物里的 {key} 与当前代码算出来的不一致 ⇒ "
            f"落盘的是**旧判决**（落盘 {disk[key]!r}，现算 {fresh[key]!r}）。"
            "请重跑 scripts/weight_vs_spread.py，**不要**改这条断言 —— "
            "改它等于让过期判决冒充当天结论。")


def test_the_real_artifact_is_consistent_with_its_own_inputs():
    """真产物自洽：`usable` = **恰好**那些不含前视的窗口，一个不多一个不少。

    ## 这里换过判据，两代都记下来

    **第一代**要求 `not lookahead AND overlap_factor == 1.0`。
    ⇒ 守的是一个**过严**的规则（第 112 层）：重叠不使窗口无效，只让估计更噪。
    ⇒ 后果是 `usable` 只剩 1 个窗口 ⇒ `all()` 退化成装饰 ⇒ 变异 W8 存活。

    **第二代**：判据改成 `not lookahead`，重叠降级为**报告项**。
    ⇒ 这条测试随之改成双向等价（`usable` 必须**恰好**等于不含前视的窗口集）。

    ⚠️ 双向是刻意的：只钉 `<=` 会让「筛选退化成全都要」无人喊（防空真），
    只钉 `>=` 会让「把没过门槛的窗口悄悄丢掉」无人喊（第 7 种形状）。
    两个方向都得有人守，而**同一条测试**守两个方向最省事。

    ⚠️ 保留 `len(usable) < len(windows)`：leak 窗口必须被排除，
    全都要就是筛选消失。
    """
    if not ART.is_file():
        pytest.skip("weight_vs_spread.json 尚未生成")
    d = json.loads(ART.read_text(encoding="utf-8"))
    clean = {k for k, v in d["windows"].items() if not v["lookahead"]}
    assert set(d["usable_windows"]) == clean, (
        "usable 必须**恰好**等于不含前视的窗口集。"
        f"多了：{set(d['usable_windows']) - clean}（不该收）；"
        f"少了：{clean - set(d['usable_windows'])}（含前视的窗口被偷偷藏起来了）")
    assert len(d["usable_windows"]) < len(d["windows"]), \
        "所有窗口都「可用」⇒ 筛选没筛掉任何东西（leak 窗口必须被排除）"
    assert d["spearman_crit_used"] == d["spearman_crit_exact_table"], \
        "保守侧必须落在精确表上（t 近似反保守）"
    assert d["crit_paths_agree"] is False, \
        "两条路径确实不一致；若哪天变成一致，说明有人改了其中一条"


def test_the_real_artifact_reports_the_measured_facts_it_verdicts_on():
    """真产物必须带上判决所依据的那两个数本身。

    只印 `verdict` 而不印 `rho_*`，判决就成了一句没人能查的话。
    """
    if not ART.is_file():
        pytest.skip("weight_vs_spread.json 尚未生成")
    d = json.loads(ART.read_text(encoding="utf-8"))
    for k in d["usable_windows"]:
        v = d["windows"][k]
        assert -1.0 <= v["rho_absIC_vs_spread"] <= 1.0
        assert -1.0 <= v["rho_absW_vs_spread"] <= 1.0
        assert len(v["per_factor"]) == 8, "八个因子的明细必须齐全"


# ══════════ 判决量必须是**带符号** IC（形状 119）══════════

def test_the_verdict_must_use_the_signed_ic_not_its_magnitude():
    """🚨 **第 119 层**：判决量曾是被诊断对象**根本不用**的那个量。

    ## 错在哪

    `weight_vs_spread.py` 第一版写

        abs_ic = [abs(r["all"]) for r in rows]      # ← |IC|
        rho_ic = spearman(abs_ic, spread_v)

    而生产权重是 `w ∝ R⁻¹·s_f`，其中的 `s_f` **就是带符号 IC**。
    ⇒ 诊断脚本在用 `|IC|` 去量一个**按带符号 IC 排序**的系统。

    ★ **实测把符号整个翻了**（2026-10-15，训练段 2019-01-02~2023-08-17）：
    ρ(带符号 IC, 尾部价差) = **+1.0000**，ρ(|IC|, 尾部价差) = **−1.0000**。
    ⇒ 「IC 与尾部价差错配、权重系统性投给了尾部最弱的因子」这个结论，
    **是在一个生产代码里不存在的量上量出来的**。

    这比「诊断算错了」更隐蔽：两边都自洽、都出数、都不崩，
    而且结论**方向明确**（「该换目标函数」）—— 最像真发现的那种错。

    ## 本条的判据：**同一份数据，两种约定，结论相反**

    夹具让 8 个 IC 全负且 `all = −spread`：
    - 按带符号 IC ⇒ ρ = −1.0 ⇒ `cond1_ic_antispread = True`
    - 按 |IC|       ⇒ ρ = +1.0 ⇒ `cond1_ic_antispread = False`
    ⇒ 一行改动（`abs(r["all"])` → `r["all"]`）就能翻掉 cond1 ⇒ 变异必红。
    """
    out = wvs.diagnose(_write_ic(_ic_fixture()), _write_wf(_wf()))
    v = out["windows"]["lag1_real"]
    assert v["rho_signedIC_vs_spread"] == pytest.approx(-1.0), \
        f"夹具的带符号 ρ 应恰为 −1.0，实得 {v['rho_signedIC_vs_spread']}"
    assert v["rho_absIC_vs_spread"] == pytest.approx(+1.0), \
        "本夹具下 |IC| 与价差**同向** —— 两个字段必须符号相反，否则夹具无区分力"
    assert v["per_factor"], "逐因子明细缺失"
    # 判决必须落在**带符号**那一侧
    assert out["cond_ic_antispread"] is True, \
        "带符号 IC 与尾部价差反向（ρ=−1）却没判 cond1 ⇒ 判决读的是 |IC|"
    assert out["per_window_verdict"]["lag1_real"]["cond1_ic_antispread"] is True


def test_the_middle_verdict_label_requires_a_negative_correlation():
    """`DIRECTION_CONSISTENT_...` 这个名字在说「反向」，**同向不许落进来**。

    ## 为什么必须拆

    第一版只判「符号一致与否」，于是 ρ = **+1.0**（一致**同向**）
    与 ρ = −0.64（一致**反向**但没过门槛）落进**同一个标签**。
    ⇒ 标签在说「反向」而实测是「同向」⇒ **标签变成一句假话**，
    而读判决的人正是靠这句话决定要不要去改目标函数。

    ⇒ 两个方向的含义完全相反：
      · ρ < 0 ⇒ 权重系统性投给了尾部最弱的因子 ⇒ **有病灶**
      · ρ > 0 ⇒ 权重排序与策略下注方向**本就一致** ⇒ **没有病灶可修**

    ## 判据：ρ 全为正 ⇒ 必须是 `NO_MISMATCH_AT_MEASURED_WIDTH`
    """
    ic = _ic_fixture()
    for r in ic["table"]:                 # 把所有 IC 翻正 ⇒ ρ_signed 全为 +1
        r["all"] = -r["all"]
    out = wvs.diagnose(_write_ic(ic), _write_wf(_wf()))
    assert all(v["rho_signedIC_vs_spread"] > 0
               for v in out["windows"].values()), "夹具失效：ρ 应全为正"
    assert out["cond_ic_antispread"] is False
    assert out["corr_direction"] == "not_negative"
    assert out["verdict"] == "NO_MISMATCH_AT_MEASURED_WIDTH", \
        (f"ρ 同向（权重与价差本就一致）却报 {out['verdict']} ⇒ "
         "标签在说「反向」而实测是「同向」")


def test_the_middle_verdict_label_is_reachable_and_means_what_it_says():
    """🚨 变异台实测 **W12 存活**：中间态判决被并进「无错配」，25 条全绿。

    ## 为什么活下来了

    修完第 119 层之后，真产物里 `rho_signedIC_vs_spread` **全为正**
    ⇒ `corr_negative = False` ⇒ `DIRECTION_CONSISTENT_...` 那个分支
    **在真产物上根本不可达** ⇒ W12 是**空操作**。
    而测试里**没有任何一条**断言过这个标签 ⇒ 25 条全绿。
    ⇒ 第 9 种形状的又一次：那条分支**从未被任何输入分岔过**。

    这与 [[guards-only-count-after-mutation]] 同源但更靠前：
    **代码在、真产物上可达性为 0、守卫也照抄同一份不可达的真产物**
    ⇒ 三层一起绿。

    ## 本条的判据

    构造一个**夹具**：全部窗口 ρ_signed < 0（⇒ `corr_negative` 为真），
    但有一个窗口 |ρ| 没过 0.7386 的门槛（⇒ `cond1` 为假）
    ⇒ 判决必须落在**中间态标签**上。

    ⚠️ 排列是搜出来的，不是手抄的：`(3,7,8,5,6,4,2,1)` 给 ρ = **−0.5952**
    （离门槛 0.7386 留了 0.14 的余量 —— 贴着门槛的排列 −0.7381 会让守卫
    一旦换表就变成永远红/永远绿，**比没有守卫更坏**）。
    ⇒ 断言把 ρ 本身钉住（正控），夹具退化时当场亮红灯。
    """
    ic = _ic_fixture()
    ic["windows"].append("lag4_weak")
    ic["fwd_windows"]["lag4_weak"] = [0, 4]
    ic["table"].extend(_weak_window_rows())
    out = wvs.diagnose(_write_ic(ic), _write_wf(_wf()))
    assert out["windows"]["lag4_weak"]["rho_signedIC_vs_spread"] == pytest.approx(-0.5952), \
        ("夹具退化：弱窗口的 ρ 必须精确落在 (−0.7386, 0) 内 —— "
         "贴着门槛会让守卫在换表时变成永远红或永远绿")
    assert out["cond_ic_antispread"] is False, "有窗口没过门槛 ⇒ cond1 必须为假"
    assert out["corr_direction"] == "negative"
    assert out["verdict"] == "DIRECTION_CONSISTENT_BUT_BAR_NOT_CLEARED_EVERYWHERE", \
        (f"反向一致、有窗口没过门槛，却报 {out['verdict']} ⇒ "
         "中间态标签不可达 ⇒ 变异 W12 会变成空操作")


def _weak_window_rows(window="lag4_weak"):
    """一个 ρ_signed = −0.5952 的窗口：**反向但不过门槛**。

    排列 `(3,7,8,5,6,4,2,1)` 是**搜**出来的（18951 个排列里最接近 −0.60 的），
    不是手抄的 ⇒ 手抄必错，而错的排列可能落在门槛另一侧。
    `spread` 秩 1..8 按 `FACT` 顺序分配，`all` 按 IC 秩**降序**赋值。
    """
    hi = 0.30
    spread = {"mom20": 0.60, "rev5": 0.50, "vol20": 0.40, "vsur": 0.30,
              "turn20": 0.20, "turnratio": 0.15, "ivol": 0.10, "rsi": 0.05}
    order = ["mom20", "rev5", "vol20", "vsur", "turn20", "turnratio", "ivol", "rsi"]
    ic_rank = dict(zip(order, (3, 7, 8, 5, 6, 4, 2, 1)))   # spread 秩 → IC 秩
    # IC 秩 1 最高（值最小）……秩 8 最低。全为负 ⇒ |IC| 的排序恰好相反。
    by_ic_rank = sorted(range(8), key=lambda k: ic_rank[order[k]])
    val = {order[k]: round(-0.01 * (by_ic_rank.index(k) + 1), 3) for k in range(8)}
    return [{"factor": f, "window": window, "all": val[f], "all_t": 1.0,
             "tail_high_hit": hi, "tail_low_hit": hi + spread[f], "n_days": 1818}
            for f in order]


# ══════════ 测试夹具 ══════════

def _ic_table(extra_leak=None):
    """八因子 × 四个窗口。**数是自造的，形状是刻意设计的**（形状 119 的修正）。

    ## 设计目标：让「带符号」与「|IC|」两种约定给出**相反**的 cond1

    `all = −spread` 且 `all` **全为负** ⇒

    | 排序依据 | ρ vs 尾部价差 | `cond1_ic_antispread` |
    |---|---|---|
    | 带符号 IC | **−1.0** | **True** |
    | \\|IC\\| | **+1.0** | False |

    ⇒ 改一行（`abs(r["all"])` → `r["all"]`）就能把 cond1 整个翻过来
    ⇒ 守卫对这条约定有牙。

    ⚠️ **为什么必须全为负**：带符号排序与 |IC| 排序只有在 IC **有正有负**时才分岔。
    夹具若混着正负号，两种约定可能同时为真或同时为假 ⇒ 这条守卫就成了安慰剂
    （第 9 种形状：没有区分力的断言等于没有断言）。

    ⚠️ 真实数据恰好就是这个形状 —— 8 个因子里 7 个 IC 为负 ——
    所以「取绝对值翻转符号」不是夹具的人造巧合，是**现场的实情**。
    """
    hi = 0.30
    spread = {"mom20": 0.60, "rev5": 0.50, "vol20": 0.40, "vsur": 0.30,
              "turn20": 0.20, "turnratio": 0.15, "ivol": 0.10, "rsi": 0.05}
    rows = []
    for win, scale, n in (("lag1_real", 1.0, 1821), ("hold4", 1.2, 1818),
                          ("hold21", 1.5, 1801)):
        for f, sp in spread.items():
            rows.append({"factor": f, "window": win, "all": -sp * scale,
                         "all_t": 1.0, "tail_high_hit": hi,
                         "tail_low_hit": hi + sp, "n_days": n})
    leak_hi = extra_leak or 0.90        # mom20 在 leak 窗口的低尾命中率
    for f, sp in spread.items():
        lo = leak_hi if f == "mom20" else hi + sp
        rows.append({"factor": f, "window": "lag0_leaky", "all": -(lo - hi),
                     "all_t": 1.0, "tail_high_hit": hi, "tail_low_hit": lo,
                     "n_days": 1822})
    return rows


def _ic_fixture():
    return {"windows": ["lag1_real", "lag0_leaky", "hold4", "hold21"],
            "fwd_windows": {"lag0_leaky": [-1, 0], "lag1_real": [0, 1],
                            "hold4": [0, 4], "hold21": [0, 21]},
            "table": _ic_table()}


def _wf():
    return {"kind": "wf_weights_backtest",
            "years": [{"year": 2021,
                       "weights": {"mom20": 0.1936, "rev5": 0.4126, "vol20": 0.3228,
                                   "vsur": 0.4004, "turn20": 0.2339, "turnratio": 0.1035,
                                   "ivol": 0.9304, "rsi": 0.3157}}]}


def _write_ic(obj):
    p = Path("/tmp/_wvs_ic_fixture.json")
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _write_wf(obj):
    p = Path("/tmp/_wvs_wf_fixture.json")
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p
