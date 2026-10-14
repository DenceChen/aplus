"""命中率诊断的守卫（2026-10-15）。

## 为什么这个模块的守卫特别要紧

它报告的数字**直接决定项目往哪个方向投人力**。一个把「顺大盘更好」
说成「信号有效」的诊断，会把整个项目推进一条死路，而它自己一路绿灯。

所以这里的守卫钉的不是「函数返回了字典」，而是**读报告的人会不会被误导**。
"""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import diagnose_hit_rate as dhr  # noqa: E402

VER = ROOT / "data/reports/verification.parquet"


def _frame(rows):
    return pd.DataFrame(rows)


def _one(direction="up", result="hit", actual=0.01, market=0.0,
         score=None, date="2026-10-09", code="000001.SZ"):
    return {"code": code, "direction": direction, "result": result,
            "actual_pct": actual, "market_pct": market,
            "adjusted_score": score, "pred_date": date}


# ══════════════ 口径：排除项必须被计数，不能无声消失 ═══════════════

def test_no_data_and_flat_are_excluded_but_still_counted():
    """`no_data` / `flat` 不计命中，但**排除量必须一起报出来**。

    否则样本量会在无声中被削减：报「30/69 命中率 43.5%」的人，
    不会知道还有 11 条被排除；而如果排除量随时间上升，
    结论会随着被排除的样本一起漂移。
    """
    df = _frame([_one(result="hit", code=f"h{i}.SZ") for i in range(3)]
                + [_one(result="no_data", code=f"9{i}.SZ") for i in range(2)]
                + [_one(result="flat", actual=0.0, code=f"8{i}.SZ") for i in range(2)])
    out = dhr.diagnose_verdicts(df)
    assert out["verdicts"] == 3, "只有 hit/miss 进分母"
    assert out["excluded"]["no_data"] == 2
    assert out["excluded"]["flat"] == 2


def test_diagnose_verdicts_refuses_an_all_excluded_file_instead_of_dividing_by_zero():
    """全被排除时必须报错，**不能**返回 0.0 命中率——那是「看起来有结论」。"""
    df = _frame([_one(result="no_data")])
    with pytest.raises(ValueError, match="没有可判定"):
        dhr.diagnose_verdicts(df)


# ══════════════ 顺/逆大盘：防止「顺大盘更好」被误读成「信号有效」 ═══════════════

def test_a_with_market_advantage_alone_does_not_earn_the_word_signal():
    """本模块存在的**首要**理由：禁止把 beta 优势说成 alpha 优势。

    构造一个纯粹靠押大盘取胜的信号：顺大盘的臂全中、逆大盘的臂全错。
    它的命中率很高，但如果只看总数就会得出「信号有效」。
    ⇒ 诊断**必须**同时给出 beta 剔除后的超额，且超额为负。
    """
    rows = []
    for i in range(12):                      # 顺大盘：up + 大盘涨，全中
        #        actual 要**低于** market 才让 up 臂超额为负（与下面 down 臂一致），
        #        这样「合计为负」才是两臂共同推出来的，而不是被另一臂拖出来的。
        rows.append(_one("up", "hit", actual=0.01, market=0.02, code=f"a{i}.SZ"))
    for i in range(12):                      # 逆大盘：down + 大盘**涨**，全错
        #        注意 market 必须与臂**相反**才是逆；用负的 market 会让它变顺。
        #        方向对（跌）但**跑赢**大盘：大盘只涨 2% 而这只涨 1.5%，
        #        ⇒ 超额 = (0.015-0.02)×(-1) = +0.005 看似正，
        #        所以这里要 actual **高于** market 才让反号后的超额为负。
        rows.append(_one("down", "miss", actual=0.03, market=0.02, code=f"b{i}.SZ"))
    out = dhr.diagnose_verdicts(_frame(rows))
    assert out["overall"]["rate"] == pytest.approx(0.5)
    assert out["with_market"]["顺大盘"]["rate"] == 1.0
    assert out["with_market"]["逆大盘"]["rate"] == 0.0
    # 关键：扣掉大盘涨跌之后，这种信号没有任何正贡献
    assert out["beta_adjusted_excess"]["合计"]["日均超额pct"] < 0, \
        "纯 beta 信号扣掉大盘后不可能是正超额；算成正数说明公式用错了臂"
    # ⚠️ 变异台实测 D2（`sign` 的 down 臂符号用错）在这里**存活**过：
    #   两组恰好等量，反号后合计仍为负 ⇒ 合计层抓不住它。
    #   ⇒ 必须单独钉 down 臂自己的符号，否则这个错会静默通过。
    assert out["beta_adjusted_excess"]["down"]["日均超额pct"] < 0, \
        "down 臂的超额必须按大盘**反号**减；符号写错会让做空臂的超额翻正"


def test_the_report_carries_the_reading_contract_that_forbids_the_misreading():
    """报告里必须带那句禁令，否则下一个读它的人会重新发明这个错误结论。"""
    df = _frame([_one("up", "hit", 0.01, 0.02), _one("down", "miss", 0.02, -0.02)])
    out = dhr.diagnose_verdicts(df)
    assert "读取的判据契约" in out
    assert "增加同构前瞻样本" in out["读取的判据契约"], \
        "必须写明「攒更多同构样本不会改变方向」，否则下一个人会选最省事的路"


# ══════════════ 打分排序力：区分「标定问题」与「设计问题」 ═══════════════

def test_a_non_monotonic_score_is_reported_as_lacking_ranking_power():
    """score 高的不更准 ⇒ 这是**设计问题**，调权重不会改变命中率。

    把它报成「排名力弱」是错的（暗示还能调）；必须报成
    `monotonic_increasing=False`，让读的人知道该改的是结构。
    """
    rows = [_one("up", r, score=s, code=f"c{i}.SZ")
            for i, (r, s) in enumerate([("hit", 0.1), ("miss", 0.2),
                                        ("hit", 0.3), ("miss", 0.4),
                                        ("hit", 0.5), ("hit", 0.6),
                                        ("miss", 0.7), ("hit", 0.8),
                                        ("miss", 0.9)])]
    out = dhr.diagnose_verdicts(_frame(rows))
    assert out["score_ranking_power"]["monotonic_increasing"] is False


def test_a_monotonic_score_is_reported_as_ranked_not_as_proof():
    """即使单调为真，也**不能**据此说信号有效——它只说明排序有信息量。

    构造：低分全中、高分全错 ⇒ 命中率随分数**递减**，所以
    `is_monotonic_increasing` 必须是 False。这正是「打分方向反了」的样子，
    而它长得和「噪声」很像——所以守卫必须把两种情况都钉出来，
    而不是只测一种就以为覆盖了。
    """
    rows = [_one("up", "hit", score=s, code=f"d{i}.SZ")
            for i, s in enumerate([0.1, 0.2, 0.3, 0.4])]
    rows += [_one("up", "miss", score=s, code=f"e{i}.SZ")
             for i, s in enumerate([0.5, 0.6, 0.7, 0.8])]
    out = dhr.diagnose_verdicts(_frame(rows))
    assert out["score_ranking_power"]["monotonic_increasing"] is False, \
        "分数越高越不准 ⇒ 单调递增为 False；若这里为 True，说明判据反了"

    # 真单调的对照组：高分全中、低分全错
    rows2 = [_one("up", "miss", score=s, code=f"g{i}.SZ")
             for i, s in enumerate([0.1, 0.2, 0.3, 0.4])]
    rows2 += [_one("up", "hit", score=s, code=f"h{i}.SZ")
              for i, s in enumerate([0.5, 0.6, 0.7, 0.8])]
    out2 = dhr.diagnose_verdicts(_frame(rows2))
    assert out2["score_ranking_power"]["monotonic_increasing"] is True


# ══════════════ 证据强度必须分级（不能一律叫「可作方向性证据」）══════════════

def test_a_tiny_sample_is_labelled_thin_rather_than_directional():
    """n<30 时 CI 宽到不能当结论用，措辞必须降级。

    这与本仓「依据：字节证据 / ⚠️ 仅 commit 走查」是同一条纪律：
    两个数量级的证据不许长得一样。
    """
    out = dhr.diagnose_verdicts(_frame([_one("up", "hit")] * 5
                                       + [_one("up", "miss", code=f"f{i}.SZ") for i in range(5)]))
    assert out["overall"]["evidence"].startswith("薄")
    assert out["overall"]["ci95"][0] < 0.5 < out["overall"]["ci95"][1], \
        "小样本的 CI 必然跨过 0.5，这就是为什么它薄"


def test_the_real_artifact_is_read_and_stays_consistent_with_itself():
    """真产物自洽性：分母 = 各臂之和，排除量 + 判定数 = 总行数。

    这条守的是**口径漂移**：若某天有人把 `flat` 也算进分母，
    命中率会凭空变好看，而其它断言都发现不了。
    """
    if not VER.is_file():
        pytest.skip("verification.parquet 尚未生成")
    df = pd.read_parquet(VER)
    out = dhr.diagnose_verdicts(df)
    assert out["overall"]["n"] == sum(v["n"] for v in out["by_direction"].values())
    assert (out["overall"]["n"] + out["excluded"]["no_data"]
            + out["excluded"]["flat"]) == len(df), \
        "判定 + 排除必须恰好等于总行数，缺一条就是口径漏了"


def test_the_cli_reports_a_missing_file_instead_of_silently_producing_zero():
    """缺档必须报错，绝不回落成 0%。"""
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts/diagnose_hit_rate.py"),
         "--verification", "/tmp/definitely_not_here_zzz.parquet"],
        capture_output=True, text=True)
    assert p.returncode != 0
    assert "缺档" in p.stdout
