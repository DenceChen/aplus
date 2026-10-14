"""`market_pct` 必须逐日**真的不同** —— 一个被复制的标量会让「超额」恒为假。

## 这条守卫挡的是什么（2026-10-14 实测）

`verification.parquet` 里：

    pred_date      market_pct
    2026-10-09     0.001196     ┐
    2026-10-10     0.001196     ├─ 精确到 6 位小数完全相同
    2026-10-11     0.001196     ┘  nunique() == 1

三天不同日期市场报了同一个基准。**根因**：`rng.sample(codes, N)` 在
`random.Random(42)` + 排序稳定的 `codes` 下，每次抽中**同一批股票**；
而 `last_two_bars(..., after_date)` 取的是「信号日之后第一根」——
早判时次日数据尚未落库，于是三天都退化到同一批 bar。
实测 60 只样本里 58 只在 10-09/10-10/10-11 三天拿到**逐位相同**的 K 线。

## 为什么这条比看上去严重

`market_pct` 被 **11 个模块**消费，其中包括：

- `aplus/evolve_race.py:251` —— **自进化主循环用它选参数**
- `aplus/portfolio.py:24` —— 日收益
- `aplus/backtest.py:920-921` —— `up_excess` / `down_excess`
- `scripts/run_daily.py:229` —— 写入点
- 另有 6 个 sweep 脚本

⇒ 假基准污染的不只是展示，是**自进化的判据**。
第 7 条（口径混比）与第 1 条（假绿）的合流：
`up_excess = actual_pct − market_pct`，减一个常量得到的「超额」
只是把收益平移了固定量，**没有任何信息**。

## 判据

**外部锚**：一个覆盖不同交易日的样本集，其均值收益**不可能逐位相同**。
市场在动，样本在变，两天的等权收益相同只有两种可能——
① 基准坏了，或 ② 两天真的完全一样（10-09/10-10/10-11 连着三个交易日）。
两种都不该被默默接受。

⚠️ **本守卫不禁止「某两天恰好相同」**（那是合法的），
只禁止**多天全部相同** —— 那是复制，不是巧合。
"""

from __future__ import annotations

import pytest

# `_market_pct` 只被 run_daily 当脚本用，从源文件抽取函数体来测，
# 不 import 整个脚本（import 会拉起 GB 级面板依赖）。
import re
from pathlib import Path

_RUN_DAILY = Path(__file__).resolve().parents[1] / "scripts" / "run_daily.py"


def _market_pct_body() -> str:
    src = _RUN_DAILY.read_text(encoding="utf-8")
    m = re.search(r"^def _market_pct\(.*?(?=^def )", src, re.S | re.M)
    assert m, "_market_pct 不见了 —— 守卫比产品活得久，说明它被重命名/删除"
    return m.group(0)


def _code_only(src: str) -> str:
    """剥掉 docstring 与行注释，只留**代码**。

    ⚠️ 剥 docstring 不能用 `re.sub(r'\"\"\".*?\"\"\"')` —— 非贪婪会在
    docstring 内部遇到引号时**提前收尾**，留下后半截说明文字当作代码，
    于是一条正确的实现被守卫判成「注释满足断言」（第 13 条的变种：
    这次是**剥离失败**造成的假红）。
    ⇒ 用行级扫描，只认真正独占一行的三引号对。
    """
    out, in_doc = [], False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if not in_doc:
                # 开引号独占一行 ⇒ 收尾也在独占一行（docstring 风格）
                in_doc = not (stripped.endswith('"""') and len(stripped) > 3)
                continue
            in_doc = False
            continue
        if in_doc:
            continue
        out.append(re.sub(r"#.*", "", line))
    return "\n".join(out)


def test_market_pct_must_distinguish_missing_from_flat():
    """「样本全缺」必须与「市场平盘」可区分。

    ⚠️ 我第一版写的是「不许 `return 0.0`」——**那条判据是错的**：
    样本为空时返回 0.0 是必须的兜底，否则调用方拿到 None 会炸。
    真正的缺陷不是「返回了 0.0」，而是**返回 0.0 时没有任何标记**，
    下游无法区分「今天没抽到样本」与「市场真的没动」。
    """
    body = _market_pct_body()
    code = _code_only(body)
    # 兜底路径必须伴随一个可观察的信号（打印 / 标记 / 计数）
    tail = code[code.rfind("return"):] if "return" in code else code
    assert re.search(r"None|print|warn|NO_SAMPLE|ok\s*=|count|n_used", tail), (
        "样本为空时的兜底返回没有任何标记 ⇒ 下游把『缺档』"
        "当成『市场平盘 0.0%』，多空超额凭空归零。"
        "这是第 12 条（缺档必须报错，绝不回落）的同族。"
    )
    # ★ 真正的不变量：缺档走 `None`，不回落数值
    assert re.search(r"None if|is None|return None", code), (
        "_market_pct 必须在缺档时返回 None（缺档必须可见），"
        "而不是回落 0.0 —— 0.0 与「市场平盘」在下游不可区分"
    )


def test_market_pct_records_the_measurement_window():
    """★ 核心守卫：**必须存在一个能报出测量窗口的函数**。

    这不是风格要求。「早判退化到同一批 bar」这个 bug 的本质是
    **测量的窗口没有被记录**，于是退化不可见。

    ⚠️ 我第一版在 `_market_pct` 里搜 `window|窗口`，被判成假红 ——
    因为那是 docstring 里的字，而且剥 docstring 的正则提前收尾。
    现在改为：**窗口由独立的 `_market_window` 提供**，
    守卫锚在「它存在」+「它返回 entry/exit」上，不做子串搜索。
    """
    src = _RUN_DAILY.read_text(encoding="utf-8")
    assert re.search(r"^def _market_window\(", src, re.M), (
        "没有 `_market_window` —— 测量窗口无处可查，"
        "退化到同一批 bar 时下游完全看不见"
    )
    body = re.search(r"^def _market_window\(.*?(?=^def )", src, re.S | re.M)
    assert body, "_market_window 函数体提取失败"
    code = _code_only(body.group(0))
    assert "entry_date" in code and "exit_date" in code, (
        "_market_window 必须把 entry_date/exit_date 放进返回值，"
        "否则窗口只存在于内存、落库后无从复核"
    )


def test_market_pct_sample_must_not_be_deterministic_across_dates():
    """采样不得恒定。

    ⚠️ 不能只断言「函数签名里有日期」—— `after_date` **本来就有**，
    它被用来锚定 K 线，却**没有**参与选样，所以恒定照样发生。
    ⇒ 真正的不变量：**种子必须由日期派生**。
    """
    src = _RUN_DAILY.read_text(encoding="utf-8")
    body = re.search(r"^def _market_window\(.*?(?=^def )", src, re.S | re.M)
    assert body, "_market_window 没了"
    code = _code_only(body.group(0))

    seed_arg = re.search(r"Random\(\s*([^)]*)\)", code)
    assert seed_arg, "找不到随机种子 —— 采样不可复现"
    seed_expr = seed_arg.group(1)
    assert re.search(r"after_date|day|date|strftime|isoformat", seed_expr), (
        f"种子是 {seed_expr!r}，**不含日期** ⇒ 每天抽中同一批股票，"
        "基准退化成少数几只的平均。种子必须由日期派生："
        "同一日期重跑同值（可复现），不同日期不同样本。"
    )


def test_no_published_report_claims_excess_against_an_unrecorded_window():
    """★ 已落库的判定数据里，任何 pred_date 的 market_pct 都不得是常量。

    这是**产物级**守卫，不看实现，只看 `verification.parquet`
    —— 就算明天有人把 `_market_pct` 改回去，这条仍然拦得住。
    """
    import pandas as pd

    path = (Path(__file__).resolve().parents[1] / "data" / "reports"
            / "verification.parquet")
    if not path.exists():
        pytest.skip("尚无验证数据")

    df = pd.read_parquet(path)
    by_day = df.groupby("pred_date")["market_pct"].first()
    if by_day.nunique() <= 1:
        # 只有一个交易日时无法证伪 —— 不是缺陷，别假装能判
        pytest.skip("仅一个交易日样本，无法判定是否为常量")
    if len(by_day) < 3:
        pytest.skip(f"仅 {len(by_day)} 个交易日，判定『多日全同』的样本量不足")

    assert by_day.nunique() == len(by_day), (
        "market_pct 有多日取到同一值：\n"
        f"{by_day.to_string()}\n"
        "⇒ 基准退化成了复制值，所有基于它的「超额」都无信息量。"
        "修复 _market_pct 后需要重跑补判。"
    )


def test_every_market_pct_consumer_must_handle_a_missing_benchmark():
    """消费方必须能接受「基准缺失」，而不是静默当 0。

    `market_pct=0.0` 与「市场确实平盘」在下游不可区分，
    于是缺档会静默变成一个假的零超额。
    """
    consumers = [
        ("aplus/portfolio.py", "market"),
        ("aplus/backtest.py", "up_excess"),
        ("aplus/evolve_race.py", "market_pct"),
    ]
    root = Path(__file__).resolve().parents[1]
    missing = [f"{f}:{name}" for f, name in consumers
               if not (root / f).exists() or name not in (root / f).read_text(encoding="utf-8")]
    assert not missing, f"消费方消失了，需要重新评估：{missing}"


def test_hit_rate_must_not_count_overlapping_windows_as_independent():
    """★ 命中率的分母必须是**独立成交窗口**数，不是条目数。

    ## 实测（2026-10-14）

        pred_date      entry       exit
        2026-10-09     2026-10-12  2026-10-13  ┐
        2026-10-10     2026-10-12  2026-10-13  ├ 同一窗口
        2026-10-11     2026-10-12  2026-10-13  ┘
        2026-10-12     2026-10-13  2026-10-14

    80 条判定实际只有 **2 个独立观测**。
    而 `run_daily.py` 打印「累计 80 条判定：总命中 43.5%」，
    等于把同一次交易数了三遍。

    ## 为什么共用窗口本身不是 bug

    周末出单落到下一个交易日是**正确的**（`run_daily.py` 注释已注明）。
    错的是**报告口径**——它把条目数当成了样本量。
    三份记录同时对、同时错，命中率的分母因此虚增 3 倍。

    这与 CLAUDE.md 第 2 条同族：**同一份数被当成两回事**。
    这里更糟：一份数被当成三份。
    """
    import pandas as pd

    path = (Path(__file__).resolve().parents[1] / "data" / "reports"
            / "verification.parquet")
    if not path.exists():
        pytest.skip("尚无验证数据")
    df = pd.read_parquet(path)
    if "entry_date" not in df or "exit_date" not in df:
        pytest.skip("落库缺 entry/exit，无法判定窗口重叠")

    windows = df.dropna(subset=["entry_date"]).groupby("pred_date").apply(
        lambda g: (g["entry_date"].iloc[0], g["exit_date"].iloc[0]),
        include_groups=False)
    uniq = windows.nunique()

    assert len(windows) > uniq, (
        f"{len(windows)} 个信号日只落在 {uniq} 个独立成交窗口上"
        f"（{windows.to_dict()}）\n"
        "⇒ 命中率的分母必须按**独立窗口**计。"
        "同一笔交易被数多次会虚增样本量，"
        "让 t 检验与命中率都显得比实际更有信息量。"
    )


def test_rolling_stats_must_report_independent_window_count():
    """★ 报表必须同时报出「条目数」与「独立窗口数」，后者才是样本量。

    ## 为什么这条改成**在运行层**断言

    第一版写的是「`verify.py` 里必须出现 `window` 字样」，
    变异验证时**接成按 `pred_date` 分组**（功能完全错误：
    3 个信号日 → 1 个窗口仍会算成 3）—— **守卫照样全绿**。

    ⇒ 这是第 23 条「守卫是安慰剂」的当场重演：
    **搜代码形状的守卫，挡不住形状对但算错的做法。**
    必须造一个已知答案的数据，让实现算出**具体的数**来比。
    """
    import pandas as pd

    from aplus.verify import rolling_stats

    # 构造：3 个信号日全部落到同一个窗口 + 1 个独立窗口
    # 正确答案 = 2，条目数 = 4。错实现（按 pred_date 分组）会给 4。
    rows = []
    for i, pdte in enumerate(["2026-10-09", "2026-10-10", "2026-10-11"]):
        for code, res in [("A", "hit"), ("B", "miss")]:
            rows.append({"code": code, "direction": "up", "pred_date": pdte,
                         "result": res, "actual_pct": 0.01, "cost": 0.0,
                         "market_pct": 0.0,
                         "entry_date": "2026-10-12", "exit_date": "2026-10-13"})
    for code, res in [("C", "hit"), ("D", "miss")]:
        rows.append({"code": code, "direction": "up", "pred_date": "2026-10-12",
                     "result": res, "actual_pct": 0.01, "cost": 0.0,
                     "market_pct": 0.0,
                     "entry_date": "2026-10-13", "exit_date": "2026-10-14"})

    df = pd.DataFrame(rows)
    s = rolling_stats(df)

    assert s["n_predictions"] == 8, f"条目数算错：{s['n_predictions']} ≠ 8"
    assert s["n_independent_windows"] == 2, (
        f"独立窗口数算成 {s['n_independent_windows']}，正确答案 2。"
        "3 个信号日共用一个成交窗口 ⇒ 只算 1 次独立交易。"
        "若这里等于 4，说明实现按 pred_date 分组了 —— "
        "重复观测被当成独立样本。"
    )


def test_rolling_stats_handles_a_missing_window_column():
    """没有 entry/exit 时必须如实给 None，不许编一个数字。"""
    import pandas as pd

    from aplus.verify import rolling_stats

    df = pd.DataFrame([
        {"code": "A", "direction": "up", "pred_date": "2026-10-09",
         "result": "hit", "actual_pct": 0.01, "cost": 0.0, "market_pct": 0.0},
    ])
    s = rolling_stats(df)
    assert s["n_independent_windows"] is None, (
        f"缺 entry/exit 却给出 {s['n_independent_windows']} —— "
        "缺档必须表现为缺失，不能编一个（与「市场平盘 0.0」同族的问题）"
    )


def test_user_facing_stats_must_show_the_sample_size_denominator():
    """★ 用户看的那一行必须报出**独立成交次数**。

    网页与日志原先都只写「累计 80 条判定」，
    而 80 条实际只有 **2 次独立交易**（3 个信号日共用一个窗口）。
    这是**对你可见**的口径问题，不是内部统计问题。
    """
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[1]
    for rel, marker in [("aplus/webapp.py", "n_independent_windows"),
                        ("scripts/run_daily.py", "n_independent_windows")]:
        src = (root / rel).read_text(encoding="utf-8")
        assert marker in src, (
            f"{rel} 仍只报条目数，不报独立成交次数 —— "
            "把一次交易数成三遍会直接误导判断"
        )
        # 必须真的**取值并插入输出**，不能只是名字出现过
        assert re.search(r'stats\.get\(\s*"' + marker, src), (
            f"{rel} 里 {marker} 只是出现了名字，没有从 stats 取值 —— "
            "它多半没被插进用户可见的输出"
        )
