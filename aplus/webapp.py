"""FastAPI Web 应用：仪表盘 API + 带记忆对话。

- 数据只读自 reports 目录（run_daily 的产物），Web 层不写数据
- 对话存储在 SQLite（data_root/chat.db），LLM 客户端可注入（测试用假客户端）
- 生产 LLM 走本机 glm-failover 代理（aplus.llm.GlmProxyClient）
"""
import json
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import MutableHeaders

# 现任健康度的判定逻辑住在 `aplus.champion_health` 而不是这里——
# `scripts/run_daily.py` 要用同一份，不能各写一套（2026-10-11 21:35 抽出）。
from .champion_health import champion_health, live_down_leg
from .evolve import LIVE_REBALANCE_EVERY, candidate_key, live_execution_status
from .factor import DEFAULT_WEIGHTS
from .factor_ic import weight_sign_conflicts
from .live_rebalance import held_since_info, load_state
from .provenance import report_jsons
from .reports import (
    latest_prediction_date,
    load_allocation,
    load_predictions,
    load_verification_history,
)
from .verify import compounded_stats, rolling_stats

SYSTEM_PROMPT = (
    "你是 aplus 量化平台的助手。用户不懂投资，回答用平实中文，解释术语，"
    "多用平台自己的数据说话。所有内容仅供研究参考，不构成投资建议。"
)


#: 命中率族**要报**的最小独立成交窗口数（W ≥ 10）。
#:
#: 🚨 判据出处（**事前**登记，不是本轮定的）：
#:    `docs/DELIVERY_DECISIONS_2026-10-18.md` §4「决策四：D1 设验收门槛
#:    —— 独立成交窗口 W ≥ 10」；`docs/DELIVERY_GATE.md` §D1 同款：
#:    「验收门槛 W ≥ 10 独立成交窗口 ⇒ **W < 10 时不许报命中率、不许给 CI**」。
#: ⇒ **不许**在这里另定一个数（改判据 = 第 91 层）。
MIN_INDEPENDENT_WINDOWS = 10

#: W 不足时**不许报**的比率族字段（缺就是缺，**不许回落成 0**）。
#:
#: ⚠️ 为什么**比率族整族**都缺、而不只缺命中率：
#:    这一族在页面上是**同一条判据**下的四个图元（命中率 / 涨篮子超额 /
#:    跌篮子超额 / 多空价差）⇒ 只砍命中率而留着超额，读者用超额照样能
#:    反推命中率 ⇒ **判据被自己的兄弟字段破掉**。
UNCITABLE_RATIO_FIELDS = (
    "hit_rate_up",
    "hit_rate_down",
    "overall_hit_rate",
    "avg_up_return",
    "avg_down_return",
    "up_excess_vs_market",
    "down_excess_vs_market",
    "excess_breadth",
    "excess_tilt",
    "gross_up_excess",
    "cost_share",
    "avg_cost_drag",
)

#: 年化族**要报**的最小跨度（年）。
#:
#: 🚨 判据出处（**不是**本轮定的）：
#:    `scripts/measure_portfolio_nav.py` 的**结构闸门**
#:    「样本过短，不给方向（`years < 1.0`）」——
#:    3 天的样本把 CAGR 开 252 次方得到的是**外推**，不是读数。
#:    ⇒ 与决策四的「W < 10 时**不许报命中率、不许给 CI**」并成**一道**闸：
#:    **年化 + CI 外推正落在「不许给 CI」那句话里。**
MIN_YEARS_FOR_CAGR = 1.0

#: 跨度不足时**不许报**的年化族字段（外推量，缺就是缺）。
#:
#: ⚠️ `total_return` 也在里面：它是**样本期内**的事实，但它的**兄弟**
#:    （cagr / arith_ann）都是外推 ⇒ 只留它、砍掉外推，
#:    读者会拿「3 天累计 +0.83%」自己外推 ⇒ 判据被自己的兄弟字段破掉。
UNCITABLE_ANNUALIZED_FIELDS = (
    "total_return",
    "cagr",
    "arith_daily",
    "arith_ann",
    "ann_vol",
    "vol_drag",
)


def _scoped_hist_for_api(reports_dir) -> pd.DataFrame:
    """按**当前信号**分桶后的底稿。

    🚨 2026-10-21：D1 闸门已按 `signal_id` 分桶，而这里若读整本底稿 ⇒
    「前端报 6 个窗口、闸门报 3 个窗口」⇒ **两者打架**，
    而打架的那块正是**用户读的那个数**。
    ⇒ 与判决脚本共用**同一个**入口（`aplus/signal_scope.py`）⇒ 不写第二份过滤。
    """
    from .signal_scope import scoped_history
    frame, _scope = scoped_history(reports_dir)
    return frame


def _signal_scope_payload(reports_dir) -> dict:
    """作用域状态 —— **归属不明时前端必须说「不可判定」**。

    🚨 **不许**把「作用域为空」渲染成「命中率 0%」：
    那是**一个看起来正常的假数**（第 23 层的形状：坏状态被静默变成空状态）。
    """
    from .signal_scope import scoped_history
    _frame, scope = scoped_history(reports_dir)
    return {
        "status": scope.status,
        "current_signal_id": scope.current_signal_id,
        "rows_in_scope": int(len(scope.frame)),
        "reason": scope.reason,
        "note": ("这批前瞻证据属于**当前信号**吗？"
                 "归属不明 ⇒ 命中率**不可报**（不许拿整本底稿的数字当当前信号的证据）"),
    }


def _stats_with_compounded(history: pd.DataFrame) -> dict:
    """`rolling_stats` + 复利口径。

    ## 为什么要合并而不是二选一

    `rolling_stats` 出的是**统计量**（命中率、逐笔平均、超额），回答
    「排序准不准」；复利口径出的是**组合曲线**（累计、CAGR、波动拖累），
    回答「照着名单建仓到底赚不赚钱」。用户的年化 20% 目标只对后者。

    两者的 `avg_up_return` 口径**故意不同**且都不算错：
    `rolling_stats` 是逐笔平均（每天成分数不同，权重不同），
    复利的 `arith_daily` 是先按日等权、再对日等权。后者才是
    「10 只等权组合一天赚多少」的读数，也是 CAGR 的输入。

    ## 为什么键是平铺而不是嵌套

    前端 `renderDocket(stats)` 直接读 `stats.hit_rate_up` 这类平铺键。
    嵌套一层会让所有既有读取点静默拿到 `undefined`——而 `undefined`
    在模板里不报错，只是**渲染成空白**。所以这里平铺，`cagr` 这类新键
    由新代码显式消费。

    ## 🚨 样本不足时**派生量不许吐**（2026-10-21，协调方两次追加）

    预注册（`docs/DELIVERY_DECISIONS_2026-10-18.md` §4「决策四」，
    `docs/DELIVERY_GATE.md` §D1 同款）：
    **独立成交窗口 W ≥ 10 才许报命中率、不许给 CI**。
    实测 W = 3 时页面已经挡住主卡，却仍把这些数递了出去：

    - **图形层**：tooltip 的正文里嵌着 `涨腿 0.62 · 跌腿 0.63`
      ⇒ 读屏软件（`aria-describedby` 指向的 sr-only 文本）**会念出来**
      ⇒ 「不许报」被自己的 tooltip 破了。
    - **API 层**：`/api/status` 的 `stats` 带着 `hit_rate_up = 0.6207`
      与 `cagr = 1.0058`（3 天的年化 = **+100.58%**）。
      ⇒ **年化 +100% 比命中率 62% 危险得多**（它是把 3 天**外推**到一年）。

    ⇒ 处置：**派生量（比率族 + 年化族）缺就是缺**（⚠️ **不许回落成 0** ——
    回落成 0 会被读成「查过了，命中率 0%」，第 89 层），
    并附 `citable` / `citable_reason` 两个字段说明为什么缺。
    ⇒ **计数类字段照给**（`n_independent_windows` / `judged` /
    `n_predictions` / `n_days` / `years`）—— 它们是读者判断样本量的依据。
    ⇒ ⚠️ **判据是两道闸取严**（不是默默挑一个）：
    **W ≥ 10**（决策四）**且** **years ≥ 1**（`measure_portfolio_nav.py`
    的结构闸门「样本过短，不给方向」）⇒ 两者**都**过才可引用。
    ⇒ **年化族为什么也归这道闸**：决策四那句「不许给 CI」里的 CI
    就是**外推的不确定度** ⇒ 把 3 天开 252 次方当读数，
    正落在那句话里 ⇒ 一并收口。
    """
    out = dict(rolling_stats(history))
    out.update(compounded_stats(history))
    out["min_windows"] = MIN_INDEPENDENT_WINDOWS
    out["min_years"] = MIN_YEARS_FOR_CAGR
    win = out.get("n_independent_windows")
    try:
        n_win = int(win)
    except (TypeError, ValueError):
        n_win = None
    years = out.get("years")
    try:
        y = float(years)
    except (TypeError, ValueError):
        y = None
    ok_windows = n_win is not None and n_win >= MIN_INDEPENDENT_WINDOWS
    ok_years = y is not None and y >= MIN_YEARS_FOR_CAGR
    citable = bool(ok_windows and ok_years)
    out["citable"] = citable
    if citable:
        out["citable_reason"] = None
    else:
        for key in UNCITABLE_RATIO_FIELDS + UNCITABLE_ANNUALIZED_FIELDS:
            out.pop(key, None)
        why = []
        if not ok_windows:
            why.append(
                "独立成交窗口 %s < %d（决策四：docs/DELIVERY_DECISIONS_2026-10-18.md "
                "§4 —— W < %d 时不许报命中率、不许给 CI）"
                % (n_win if n_win is not None else "未知", MIN_INDEPENDENT_WINDOWS,
                   MIN_INDEPENDENT_WINDOWS))
        if not ok_years:
            why.append(
                "样本跨度 %s 年 < %g（measure_portfolio_nav.py 的结构闸门："
                "样本过短，不给方向 —— 3 天的 CAGR 是**外推**不是读数）"
                % (years if years is not None else "未知", MIN_YEARS_FOR_CAGR))
        out["citable_reason"] = (
            "**两道闸取严**（W ≥ %d 且 years ≥ %g）⇒ 派生量**缺**（不是 0）：%s"
            % (MIN_INDEPENDENT_WINDOWS, MIN_YEARS_FOR_CAGR, "；".join(why)))
    return out


def _live_holdings_payload(reports_dir) -> dict:
    """**你现在持有的那 10 只** + 「还有几个交易日才换仓」+ 执行口径是不是声明的那个。

    ## 为什么必须有它（2026-10-17 实测缺口）

    `/api/status` 原本只递 5 个键，`holdings.json`（实盘成交流的唯一真值）
    与 `live_execution_status`（K 与缓冲区**是否真的被执行**的那道闸）
    **全仓零个消费点**。于是用户每天打开平台看到的是一张新的 10 涨 10 跌，
    而实盘是 `every=21`（`holdings.json` 实读）、**每 21 个交易日才换一次仓**。

    ⇒ 照着每天的新名单全买，年摩擦 **67.34%**（`delivery_caliber_friction::friction_by_turnover.at_measured_min`）；
       照 K=21 换一次是 **6.41%**（同产物 `friction_by_holding_period.K=21`）。
       **差 60.93pp/year，且与信号好坏无关。**
    ⇒ 这不是「优化」，是**交付 D1 时必须一起说清的口径**：
       名单每天出 ≠ 仓位每天换。

    ## 坏状态不许静默变成「空仓」

    `load_state` 任何异常都返回 `None`（= 首次建仓）。这里**原样递出 `None`**，
    不造一个空的 `{"up": [], "down": []}`——后者在界面上渲染成
    「你现在持有 0 只」，那是**一个看起来正常的假状态**，
    而真相是「状态读不出来，明天会按首次建仓重新买一遍」（形状 23 的方向）。
    ⇒ 前端必须把 `None` 渲染成「状态读不出来」，不是「空仓」。

    ## `days_to_rebalance` 由 `every` 现算，不落盘

    它是 `every − days_since_rebalance`，两个入参都在 `holdings.json` 里。
    自己再存一份就是第 137 层的手抄件，第二天就会漂。

    ## `as_of` ≠ `held_since`（2026-10-20 修的 P0）

    `as_of` 的语义是「**最后一次出单日**」——`run_daily.py` 每天**无条件**写
    `state["as_of"] = today`，不论当天是不是调仓日。
    `held_since` 的语义是「**持仓是哪天定的**」，只有换仓日才更新。

    ⇒ 概览页原来拿 `as_of` 当「持仓基准日」显示，是在告诉用户
    「你持有的是今天那份名单」。实测 2026-10-20：手上是 **10-15** 那份
    （与 `pred_2026-10-15.parquet` 的 up **10/10 逐只相同**），
    与 10-19 榜只交集 **4/10** ⇒ 照字面理解会买错票。

    ⇒ 现在三个字段一起递，**三态**不许压成一个日期：

    | 字段 | 含义 |
    |---|---|
    | `held_since` | `'YYYY-MM-DD'` 或 `null` —— 手上这份名单是哪天的 |
    | `held_since_is_fallback` | true = 从 `as_of` 回落（旧 state 文件没这个键） |
    | `held_since_reliable` | true = 从 run_daily 的真 `held_since` 落盘 |

    回落时前端必须显示「实际持仓日未知」，**不许**把 `as_of` 说成持仓日。
    """
    state = load_state(reports_dir)
    status = live_execution_status(reports_dir)
    # 「持仓基准日」：**不是** `as_of`。
    # `as_of` 由 run_daily 每天无条件写成「最后出单日」，
    # 而「持仓是哪天定的」是 `held_since`（旧 state 文件没有 ⇒ 回落成 as_of，
    # 并且**必须标出来**，不能让用户以为它一定可靠）。
    hs = held_since_info(state)
    out = {
        # `None` = 状态读不出来（或还没建仓）。**与空仓严格不同。**
        "state": state,
        "as_of": (state or {}).get("as_of"),
        # ★ 手上这份名单是**哪天的**（'YYYY-MM-DD' | null）。
        "held_since": hs["held_since"],
        # true = 这个值是从 `as_of` **回落**来的（旧 state 文件没这个键）⇒ 不可信。
        "held_since_is_fallback": hs["is_fallback"],
        # true = 它是从 run_daily 的真 `held_since` 落盘下来的 ⇒ 可信。
        "held_since_reliable": hs["reliable"],
        "up": (state or {}).get("up") or [],
        "down": (state or {}).get("down") or [],
        "up_names": (state or {}).get("up_names") or [],
        "down_names": (state or {}).get("down_names") or [],
        "every": (state or {}).get("every"),
        "days_since_rebalance": (state or {}).get("days_since_rebalance"),
        # `honored=False` 时 `reason` 逐字带着「回落默认权重未经赛马验证 /
        # 上一版排 81/87 名、t=−3.03」——前端必须把它显示出来，不是压成布尔。
        "execution_status": status,
        # `honored` 单独提出来，方便前端决定横幅的颜色；**不代替** `reason`。
        "honored": bool(status.get("honored")),
        "reason": status.get("reason"),
    }
    every = out["every"]
    days = out["days_since_rebalance"]
    out["days_to_rebalance"] = (
        None if not isinstance(every, int) or not isinstance(days, int) or every < 1
        else max(int(every) - int(days), 0)
    )
    # 🚨 2026-10-21：状态文件里的 `every` 是**上一次运行实际执行的频率**，
    # 而实盘常量可能已经改了（2026-10-21：21 → 63）。
    # ⇒ 不一致时**页面必须说出来**，否则同一个页面上会出现
    # 「持仓状态：每 21 个交易日换一次仓」与「实盘按每 63 个交易日换仓」
    # **两个互相矛盾的数**（实测：切换当天就是这样）。
    # ⇒ 且这不是「等下就一致」的小事：`aplus/live_rebalance.py::is_due`
    # 把「配置变了」当作**该调仓**（旧持仓是按旧配置挑的，继续拿着
    # 就是拿一套策略的仓位去跑另一套策略）⇒ **下一次运行会立刻调仓**。
    live_every = int(LIVE_REBALANCE_EVERY)
    out["live_rebalance_every"] = live_every
    out["every_matches_live"] = (every == live_every)
    out["every_mismatch_note"] = (
        None if out["every_matches_live"] else
        f"持仓状态记录的是 K={every}，而实盘已改成 K={live_every}"
        f" ⇒ **下一次运行会立刻调仓**"
        f"（`is_due` 把「配置变了」当作该调仓）")
    return out


def _alloc_rebalance_rows(rows) -> list:
    """把 `rebalance` 补成**固定五字段**的形状（`code/name/action/weight/amount`）。

    ## 为什么要在接口层补

    新产物（`aplus/alloc.py::build_allocation`）里每条已经带齐五字段。
    旧产物（2026-10-20 之前）里只有 `{code, action}` ⇒ 前端按契约读
    `r.name / r.weight / r.amount` 会拿到 `undefined`。

    ## 缺就是缺，**不填**

    不从 `positions` 里「猜」一个名字/权重补上去 ——
    `positions` 是**目标仓位**，`rebalance` 是**差异**，两者不是同一个口径；
    拿一个补另一个就是凭空造数（而且 `remove` 的条目在 `positions` 里
    本来就不存在）。⇒ 缺的填 `null`，前端显示「—」。
    """
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "code": r.get("code"),
            "name": r.get("name"),
            "action": r.get("action"),
            "weight": r.get("weight"),
            "amount": r.get("amount"),
        })
    return out


def _publish_gate_payload(reports_dir) -> dict:
    """**D1 上线闸门**的判决，原样递到首屏。

    ## 为什么要单独一个字段，而不是并进 `stats`

    判决板 `stats.*` 回答的是「方向猜得准不准」（命中率、组合超额）；
    闸门回答的是**另一个问题**——「这份名单**能不能拿去发布**」。
    两者**可以同时为真或同时为假**：命中率够高也可能因排序力测不出而被闸门拦住。
    ⇒ 并进同一个字典就会让读者以为一个数能同时回答两问（第 7 种形状）。

    ## 三态标签是判决的一部分，不是措辞偏好

    `NOT_PASSED_POWERLESS` = 「**测不出**排序力」，
    `NOT_PASSED_MEASURED` = 「测出来了，但没过线」。
    **两者行动含义相反**：前者只能攒前瞻期，后者必须换结构。
    授权它们的是**置换零分布 95 分位**（`null_abs_rho_q95_power_floor`，
    检出力下限），不是手写的「n 太小」。
    ⇒ 页面文案只许由 `verdict` 决定，**不许写死**——
    写死的那天底稿一追加就会变成一句假话（第 73 层）。

    ## 数字一律**现读**，不许进散文

    `rho / p / power_floor / n_independent_pred_dates` 会随
    `verification.parquet` 这个**追加式底稿**增长而变（2026-10-16 那次追加
    就把旧值 5 日 / 96 / 30 / 66 全作废了）。
    这里**逐次从产物读**，前端只负责渲染 ⇒ 追加后重跑闸门即自动跟着走。
    """
    path = reports_dir / "score_ranking_power.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        arm = doc["by_score_col"][doc["primary_score_col"]]["arms"]["up"]
        stats = arm["_stats"]
        score = doc["by_score_col"][doc["primary_score_col"]]
        calib = score.get("independence_caliber") or {}
    except (OSError, ValueError, KeyError, TypeError):
        # 🚨 **读不出来不许变成「通过」**。`publish_allowed` 缺字段时前端
        # 必须渲染成「闸门读不到」而不是静默收起横幅——那是一个看起来
        # 正常的假绿灯（形状 23 的方向）。
        return {"available": False, "publish_allowed": None, "verdict": None}

    return {
        "available": True,
        # `publish_allowed` 必须是**严格布尔**；`verdict` 是三态字符串。
        "publish_allowed": doc.get("publish_allowed"),
        "verdict": doc.get("verdict"),
        "gate": doc.get("gate"),
        "prereg": doc.get("prereg"),
        "primary_score_col": doc.get("primary_score_col"),
        "verification_source": doc.get("verification_source"),
        "reason": doc.get("verdict_reason"),
        "criterion": (score.get("criterion") or {}).get("rule"),
        "n_rows_total": score.get("n_rows_total"),
        "n_rows_judgeable": score.get("n_rows_judgeable"),
        "n_pred_dates": score.get("n_independent_pred_dates"),
        "n_entry_dates": calib.get("n_entry_dates"),
        "monotone": stats.get("monotone_nondecreasing"),
        "rho": stats.get("point_biserial_rho"),
        "p": stats.get("permutation_p_two_sided"),
        "power_floor": stats.get("null_abs_rho_q95_power_floor"),
        "detectable": stats.get("detectable_at_alpha05"),
        "cannot_prove": doc.get("what_this_cannot_prove"),
    }


def _json_safe(df: pd.DataFrame) -> list:
    """DataFrame → JSON 安全的 records：任何 NA 哨兵 → None（starlette 拒绝 NaN）。

    两代根因都在这里，都不是「少洗了一列」那么简单：

    1. 2026-10-10：Series.map 在数值列上把 None 强转回 NaN（dtype 保持 float64），
       必须 astype(object) 后再 where。
    2. 2026-10-11：按 `dtype.kind == "f"` 分支只洗浮点列，而生产预测 parquet 里
       news_reason / news_query 是 **str dtype 列却装着 NaN**（无消息即空），
       NaN 原样漏到 JSON → /api/predictions/latest 500 → 概览页预测区空白。

    所以这里不再猜 dtype：每列一律 astype(object) 后按 pd.notna 判定，
    object/str/float/int/bool 走同一条路径。真实值（'无消息'、True、0）不受影响。
    """
    clean = df.copy()
    for col in clean.columns:
        obj = clean[col].astype(object)
        clean[col] = obj.where(pd.notna(obj), None)
    return clean.to_dict(orient="records")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "title TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "conv_id INTEGER, role TEXT, content TEXT, created_at TEXT)"
    )
    return conn


def _platform_context(reports_dir: Path) -> str:
    parts = []
    lp = latest_prediction_date(reports_dir)
    if lp:
        loaded = load_predictions(reports_dir, lp)
        if loaded is not None:
            up, down = loaded
            ups = "、".join(f"{row['name']}({row['code']})" for _, row in up.iterrows())
            downs = "、".join(f"{row['name']}({row['code']})" for _, row in down.iterrows())
            parts.append(f"最新预测（{lp} 生成，预测次一交易日）：预测涨 {ups}；预测跌 {downs}")
            alloc = load_allocation(reports_dir, lp)
            if alloc:
                positions = "、".join(
                    f"{p['name']} {p['amount']:.0f} 元" for p in alloc.get("positions", [])
                )
                parts.append(
                    f"配置方案：{positions}；现金比例 {alloc.get('cash_weight', 0):.0%}；本金 {alloc.get('capital', 0):.0f} 元"
                )
    # 🚨 命中率**必须按当前信号分桶**（2026-10-21）：
    #   D1 闸门已按 `signal_id` 分桶，而这里若读整本底稿 ⇒
    #   「前端报 6 个窗口、闸门报 3 个窗口」⇒ **两者打架**，
    #   而打架的那块正是**用户读的那个数**。
    # ⇒ 与判决脚本共用**同一个**入口（`aplus/signal_scope.py`）⇒ 不写第二份过滤。
    from .signal_scope import scoped_history
    hist, _scope = scoped_history(reports_dir)
    stats = _stats_with_compounded(hist)
    if len(hist):
        # ★ 样本量必须同时给出「条目数」与「独立成交窗口数」。
        #   周末/节假日出单会让多个信号日落进同一个窗口
        #   （实测 3 个信号日 → 1 笔交易 = 80 条判定只有 2 次独立观测）。
        #   只报「80 条」会把一次交易数三遍，读起来像样本量很大。
        win = stats.get("n_independent_windows")
        win_txt = f"，其中 {win} 次独立成交" if win else "（独立成交次数未知）"
        # 🚨 W 不足时**不许报命中率**（预注册 §4 决策四）⇒ 比率族字段**缺**
        #   （不是 0）⇒ 这里必须**说清为什么没有**，不许拿别的东西顶替。
        if not stats.get("citable", True):
            parts.append(
                f"累计 {len(hist)} 条判定{win_txt}：**命中率、超额与年化都不可报**"
                f"（{stats.get('citable_reason') or '样本不足'}）"
            )
        else:
            parts.append(
                f"累计 {len(hist)} 条判定{win_txt}：总命中率 {stats['overall_hit_rate']:.1%}，"
                f"涨方向命中 {stats['hit_rate_up']:.1%}，涨组合超额收益 {stats['up_excess_vs_market']:+.2%}"
            )
            # 跌腿以前完全不报——平台天天输出 10 跌，能不能看空却从没被测量过。
            # 价差（涨篮子 − 跌篮子）对大盘方向免疫，比单看涨腿超额诚实。
            parts.append(
                f"跌方向命中 {stats['hit_rate_down']:.1%}，跌篮子回避超额 "
                f"{stats['down_excess_vs_market']:+.2%}，多空价差 {stats['excess_breadth']:+.2%}"
                f"（价差为正 = 排序两端确实分得开；涨腿超额会被大盘涨跌带偏，价差不会）"
            )
    else:
        parts.append("验证历史为空（平台刚启动，首个预测日待验证）")
    return "\n".join(parts)


class ChatIn(BaseModel):
    message: str
    conversation_id: int | None = None


class _UserFacing(Exception):
    """可以直接发给用户的中文错误（不暴露 traceback 细节）。"""


def _sse(event: str, data: dict) -> str:
    """一个 SSE 事件 = `event:` 行 + `data:` 行 + **空行**。

    少那个空行，浏览器会把相邻两个事件并成一块（这是 SSE 最经典的静默失效：
    不报错，只是前端丢一个事件）。`ensure_ascii=False` 让中文原样出行，
    配合 `charset=utf-8` 免得前端拿到 `\\uXXXX`。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SSE_HEADERS = {
    # 不缓存：每个事件都是一次性数据，被代理缓存等于前端拿到空流。
    "Cache-Control": "no-cache",
    # nginx 默认会缓冲整个响应再转发 ⇒ 流式看起来还是「一次性」。
    # 这是唯一能关掉它的响应头（应用层无能为力）。
    "X-Accel-Buffering": "no",
}


def _sse_response(iterator) -> StreamingResponse:
    # media_type 不带 charset：starlette 对 text/* 自动补 "; charset=utf-8"，
    # 写全反而会变成 "charset=utf-8; charset=utf-8"。
    return StreamingResponse(iterator, media_type="text/event-stream", headers=SSE_HEADERS)


def _chain_members(chain) -> list:
    """把注入的 LLM 对象拆成链的成员列表（按 `default_llm_chain` 的真实结构）。

    `default_llm_chain()` 返回 `FallbackChain(primary, backup)`——它**没有**
    候选列表属性，只有 `.primary` / `.backup` / `.last_served_by`。
    """
    members = [c for c in (getattr(chain, "primary", None), getattr(chain, "backup", None))
               if c is not None]
    return members or [chain]


def _chain_entry(client) -> dict:
    """**运行态事实**：模型名只能从活着的客户端对象上读。

    写死字符串等于把本仓记过的那条漂移再请回来一次
    （「代码是 3.1」≠「界面显示 3.1」）。取不到就 `null`，不回退。
    """
    model = getattr(client, "model", None)
    return {
        "name": getattr(client, "name", None) or type(client).__name__,
        "model": model if isinstance(model, str) else None,
        "kind": getattr(client, "kind", None),
    }


# ── 缓存策略（模块级，守卫必须 import 得到 —— 第 96 层）────────────────
#
# 🚨 为什么需要它：Starlette 的 `StaticFiles` **不设** `Cache-Control`。
# 缺这个头时浏览器**必须**走 RFC 9111 §4.2.2 的**启发式新鲜度**，
# 用 `10% × (Date − Last-Modified)` 当 freshness —— 实测 `index.html` 已改，
# 而 `<script src="/verdict.js">` 仍在执行提交 `3fdd92a` 的 **14881** 字节旧副本
# （`performance` 资源计时：`transferSize 0` / `deliveryType "cache"`，
#  同一次加载里 `fetch(no-store)` 拿到的是盘上的 **21985**）。
#
# ⚠️ **`no-cache` 不是 `no-store`**：副本仍然存，只是**每次用前必须回源验证**。
# 这里要的正是「验证」—— 静态资源带 etag，命中时是 304，代价几十字节。
#
# 🚨 范围是**全部 http 响应**而不只是静态资源：`/api/status` 同样**一个
# `Cache-Control` 都没有**，同样授权浏览器猜新鲜度 ⇒ 仪表盘可能显示陈旧读数。
# 只修静态会留下「静态修了、API 还猜」的半修状态，那比不修更难查。
# 预注册：`docs/PREREG_docket_visibility_2026-10-18.md` §3.1
# （🚨 该节明写「单修甲，卡片仍不上屏，不许事后当成修好了」）。

CACHE_CONTROL_VALUE = "no-cache"


class NoCacheMiddleware:
    """给**没有声明策略**的 http 响应补上 `Cache-Control`（纯 ASGI）。

    ⚠️ 刻意不用 `BaseHTTPMiddleware`：它对流式响应（`/api/chat/stream` 的 SSE）
    会缓冲整个 body。这里只改 `http.response.start` 那一份头，不碰 body
    ⇒ 零内存代价、零流式影响。

    ⚠️ 只**填空**不**覆盖**：已声明策略的响应原样保留
    ⇒ 本中间件是纯增量的，将来任何端点要自己声明 `no-store` 都不会被这里顶掉。
    """

    def __init__(self, app, value: str = CACHE_CONTROL_VALUE):
        self.app = app
        self.value = value

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":          # websocket / lifespan 不碰
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                if "cache-control" not in headers:
                    headers["cache-control"] = self.value
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ── 整手可买性（lot affordability）────────────────────────────────────
#
# 🚨 **为什么这个端点存在**（2026-10-20 实测）
#
# 今天的推荐里，看涨腿 10 只中有 **6 只**在每份 ¥5,000 的预算下**1 手就买不起**：
# - 其中 5 只是**科创板 200 股起买**：`688498.SH 源杰科技` 收盘 1010.35
#   ⇒ 1 手 ¥202,070 = 全部本金（¥50,000）的 **4 倍**；
#   `688383/688610/688300/688110` 一手也要 ¥11,804～¥20,644；
# - 第 6 只是 `300951.SZ 博硕科技` 收盘 59.20 ⇒ 1 手 ¥5,920（**创业板 100 股**）。
#
# 页面上仍然是「10 涨 10 跌」两张整齐的表 ⇒ 用户会以为照着买就行，
# 而实盘那 10 只里**只有 4 只**在每份 ¥5,000 的预算内。
# ⇒ 这不是信号问题，是**本钱问题**：名单再准，买不进就等于没信号。
#
# ⇒ 本端点**只**回答「买不买得起」：**不碰**收益率/胜率/命中率
# （那是 `/api/status` 与 `/api/d2-verdict` 的职责；
#   混进来 = 端点开始重算判决 = 第 137 层「两份必须同步的东西中间没有锁」）。

#: 科创板一手 **200** 股（`689` = 科创板 CDR）；其余一手 **100** 股。
#:
#: ⚠️ **不许**用「价格高就 200 股」这种推断 —— 那是把两个口径混成一条规则。
#: `688/689` 的判定**只认代码前缀**，与价格无关：
#: 源杰科技 1010 元是 200 股，埃科光电 80 元也是 200 股；
#: 反过来 300951 博硕科技 59 元是 100 股。
LOT_SHARES_STAR_MARKET = 200
LOT_SHARES_DEFAULT = 100


#: 板块 → 一手股数 + **交易权限门槛**。
#:
#: 🚨 **权限门槛是外部常识·本仓未核实** ——
#: 「50 万资产 + 2 年经验」这类门槛**全仓 grep 命中 0** ⇒
#: 本仓**没有**权限模型，也**无法**从任何仓内数据推断出用户有没有权限
#: ⇒ ⇒ 只能**标出来**，让用户自己去核 ⇒ **不许**据此把票自动剔除
#: （那是拿一条未核实的外部常识去改用户的持仓）。
#: ⇒ 唯一**确定**的是**一手多少股**（交易所规则，本仓按代码前缀实现）。
BOARD_RULES: dict[str, dict] = {
    "STAR_688_689": {
        "label": "科创板",
        "lot_shares": LOT_SHARES_STAR_MARKET,
        "permission": "需开通科创板权限（50 万资产 + 2 年经验）",
    },
    "CHINEXT_300_301": {
        "label": "创业板",
        "lot_shares": LOT_SHARES_DEFAULT,
        "permission": "需开通创业板权限（10 万资产 + 2 年经验）",
    },
    "BSE_8_4": {
        "label": "北交所",
        "lot_shares": LOT_SHARES_DEFAULT,
        "permission": "需开通北交所权限（50 万资产 + 2 年经验）",
    },
    "MAIN": {
        "label": "主板",
        "lot_shares": LOT_SHARES_DEFAULT,
        "permission": None,
    },
}


def board_of(code) -> str:
    """这只票在哪个板。**唯一一份**板块判定。

    🚨 前端**不许**自己按代码前缀再判一次板块
    （那是第二份真相源，第 97 层）⇒ 端点算好递过去，前端只渲染。
    """
    c = str(code or "").strip()
    if c[:3] in ("688", "689"):
        return "STAR_688_689"
    if c[:3] in ("300", "301"):
        return "CHINEXT_300_301"
    if c[:1] in ("8", "4"):
        return "BSE_8_4"
    return "MAIN"


def lot_shares_for(code) -> int:
    """一手多少股：**688/689 = 200，其余 = 100**。

    688xxx/689xxx = 科创板（申报数量 200 股起）；
    主板 600/601/603/605、深主板 000/001/002、创业板 300、
    北交所 8xxxxx/4xxxxx 都是 100 股一手。
    ⇒ **从 `board_of` 派生**（单一真相源）：板块规则只写一处。
    """
    return int(BOARD_RULES[board_of(code)]["lot_shares"])


def close_as_of(store, code, as_of):
    """主库里 `date <= as_of` 的**最后一根 bar**的 `(close, bar_date)`。

    ## 为什么锚 `as_of` 而不是 `entry_date`

    榜单是 `as_of`（= `pred_date`）**收盘后**生成的 ⇒ 那一刻能看见的收盘价
    就是 `as_of` 当天那根 bar；下一交易日（entry_date）的开盘价此刻还不存在。
    ⇒ 「entry_date 之前最后一根 bar」在 as_of 是交易日时**就是 as_of 那根**。

    ## 缺 bar ⇒ `(None, None)`，**不编**

    这只票不在主库里、或停牌到今天之后没有新 bar ⇒ 没有价 ⇒
    「买不买得起」**问不出来**。
    编一个 0 会让「买得起」恒真（0 ≤ 5,000）⇒ 假绿（形状 23 的方向）。
    """
    df = store.read_daily(code)
    if df is None or len(df) == 0:
        return None, None
    d = df.copy()
    d["date"] = d["date"].astype(str)
    d = d[d["date"] <= str(as_of)]
    if d.empty or "close" not in d.columns:
        return None, None
    d = d.sort_values("date")
    row = d.iloc[-1]
    close = row["close"]
    try:
        if close is None or pd.isna(close):
            return None, None
        close = float(close)
    except (TypeError, ValueError):
        return None, None
    return close, str(row["date"])


#: 仓位标签的**常量表**（显示串**不许手打**）。
#:
#: 🚨 起因（2026-10-21，协调方取证）：生产配置已从「半仓 5 只」改成
#:    「满仓 10 只 × ¥5,000」（`build_allocation` 的默认值
#:    `max_positions=10` / `per_stock_cap=0.1` ⇒ 敞口 **1.0**），
#:    而屏上手打的串还写着「半仓」⇒ **输出「半仓 100%」这种自相矛盾的话**。
#:
#: ⇒ 标签必须**从敞口数推出来**，不许写死。
#: ⇒ **判别力**（守卫 `tests/test_holdings_alert_wiring.py`）：
#:    `exposure = 1.0` 时标签**不许**是「半仓」；
#:    且 `position_label` 函数体里**不许**出现这两个中文字面量
#:    （出现 ⇒ 它是手打的，改数时不会跟着变）。
POSITION_LABELS = (
    (1.0, "满仓"),
    (0.5, "半仓"),
)

#: 标签匹配的容差（敞口是 `n_positions × per_stock_cap` 的乘积 ⇒ 浮点噪声）。
#: ⚠️ 精确相等会在 1 ulp 上给出假的标签（仓内已有先例：
#:    「精确相等探针在 1 ulp 上打印假的因果标签」）。
POSITION_LABEL_TOL = 1e-9


def position_label(exposure) -> str:
    """敞口 ⇒ 仓位标签。**从常量表查**，不许手打。

    查不到就回「仓位」（**中性词，不与任何数矛盾**）——
    宁可少一个形容词，也不许说一个与数矛盾的形容词。
    """
    try:
        e = float(exposure)
    except (TypeError, ValueError):
        return "仓位"
    for value, label in POSITION_LABELS:
        if abs(e - value) <= POSITION_LABEL_TOL:
            return label
    return "仓位"


def exposure_display(exposure) -> str:
    """敞口 ⇒ 屏上那一串（标签 + 百分比）。

    ⚠️ **标签不许手打**（`position_label` 查常量表）：
    手打的「半仓」在敞口变成 1.0 之后输出了**自相矛盾**的话（2026-10-21）。
    ⇒ 判别力（守卫）：`exposure_display(1.0)` 里**不许**出现「半仓」。
    """
    try:
        e = float(exposure)
    except (TypeError, ValueError):
        return "—"
    return "%s %g%%" % (position_label(e), e * 100)


def deployed_defaults() -> dict:
    """实盘配置的**生产默认值** —— 从签名读，**不手打**。

    🚨 **不许手打 0.5 / 5 / 0.1**：手打的数在默认值被改后仍然绿
    ⇒ 产品改了而仪表指着旧数（`guards-that-pin-existence-not-property`）。
    ⇒ 做法与 `scripts/deployed_account_nav.py::deployed_exposure` 完全一致：
    `inspect.signature(build_allocation).parameters[...].default`。

    ## 为什么读**签名**而不是 `build_allocation` 的**返回值**

    返回值里的 `cash_weight` 依赖**当天有几只候选**（候选不足 5 只 ⇒ 敞口更低）。
    而「实盘配置是什么」是**配置**问题，与当天名单无关 ⇒ 读签名默认值。
    （当天实际配置在 `/api/allocation/latest`，那是另一个端点、另一个量。）
    """
    import inspect

    from .alloc import build_allocation

    params = inspect.signature(build_allocation).parameters
    capital = params["capital"].default
    n_pos = params["max_positions"].default
    cap = params["per_stock_cap"].default
    if capital is None or n_pos is None or cap is None:
        raise ValueError(
            "build_allocation 的 capital/max_positions/per_stock_cap 必须有默认值："
            f"capital={capital!r} max_positions={n_pos!r} per_stock_cap={cap!r}。"
            "⇒ 生产可能显式传参 ⇒「读签名」这个前提失效，拒绝出数。")
    capital = float(capital)
    n_pos = int(n_pos)
    cap = float(cap)
    if n_pos <= 0:
        raise ValueError(f"build_allocation 的 max_positions 必须为正，实得 {n_pos!r}")
    return {
        "source": "inspect.signature(aplus.alloc.build_allocation).parameters[...].default",
        "caller": ("scripts/run_daily.py: build_allocation(up, capital=capital)"
                   " —— 只传 capital ⇒ 其余全部走默认值"),
        "capital": capital,
        "max_positions": n_pos,
        "per_stock_cap": cap,
        # 权益敞口 = 持仓数 × 单票上限（`build_allocation` 里的同一式子）
        "equity_weight": n_pos * cap,
        # 生产给每只的钱 = 本金 × 单票上限
        "per_position_amount": capital * cap,
        # 另一种分法：把本金平均分给 max_positions 只
        "capital_div_positions": capital / n_pos,
        # 现金 = 1 − 权益（`build_allocation` 里的同一式子）
        "cash_weight": 1.0 - n_pos * cap,
        "why_not_hand_typed": (
            "手打 0.5/5/0.1 会在默认值被改后仍然绿 ⇒ 产品改了而仪表指着旧数。"
            "这是「只钉存在不钉性质」的形态。"),
    }


def _read_json(path: Path):
    """读一个 JSON 产物 ⇒ `(doc, err)`。**err 非空就是没读到**，不许回落。"""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"产物不存在：{path.name}"
    except (OSError, ValueError) as exc:
        return None, f"产物读不出来：{path.name}（{exc!r}）"


def _find_key(obj, key):
    """深度优先找**第一个**叫 `key` 的键 ⇒ `(value, json_path)`。

    用途：`deployed_d2_verdict.json` 的**结构由另一个 agent 写**，
    本端点不猜它的 schema ⇒ 逐层找同名键，并把**找到它的 JSON 路径**一起递出去，
    让读者知道这个数是从哪一层读上来的（形状错一格时，
    `null` 与「闸是死的」**逐位同形** —— 第 144 层）。
    """
    stack = [(obj, "$")]
    while stack:
        cur, path = stack.pop(0)
        if isinstance(cur, dict):
            if key in cur:
                return cur[key], f"{path}.{key}"
            for k, v in cur.items():
                stack.append((v, f"{path}.{k}"))
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                stack.append((v, f"{path}[{i}]"))
    return None, None


#: 实盘档读数在产物里的**块名**（`scripts/measure_deployed_d2.py` 产的 `verdict` 块）。
#:
#: 🚨 **为什么必须锚块、不能满文档找键**
#:
#: 那份产物里**到处**都有 `cagr`（`arms` 每档一个、`account_legs`、
#: `control_cross_check`…）⇒ 满文档找第一个 `cagr`，
#: 很可能找到的是**满仓判决档**的那个 ⇒ 读数**看起来有数、其实读错了对象**
#: （「找到了 ≠ 找对了」）。
#: ⇒ 锚在 `verdict` 块上，并把它**在哪**（JSON 路径）一起递出去。
DEPLOYED_D2_VERDICT_BLOCK = "verdict"

#: 端点字段名 → 产物里**可能**的键名（按优先级）。
#:
#: 🚨 `verdict_mde_annual` 与 `mde_annual` 是**两个名字、同一件事**：
#: 端点的契约名是 `verdict_mde_annual`（与 `/api/d2-verdict` 对齐），
#: 而产物那一族写的是 `mde_annual`。
#: ⇒ 两个都认，**并把实际用上的那个键名**递出去（`found_key`），
#: 不让「端点字段名」与「产物字段名」之间的差别变成一条**没人看得见的**接缝。
DEPLOYED_D2_KEYS = {
    "cagr": ("cagr",),
    "verdict_mde_annual": ("verdict_mde_annual", "mde_annual"),
    "gap_to_target_pp": ("gap_to_target_pp",),
    "upper_one_sided_95": ("upper_one_sided_95", "verdict_upper_one_sided_95"),
    "target": ("target",),
    "measured_sign_determinable": ("measured_sign_determinable",),
    "cases": ("cases", "verdict_cases_fired"),
}


def _lookup_in_block(block, names):
    """在**一个块**里按优先级找键 ⇒ `(value, json_path, used_key)`。

    找不到 ⇒ `(None, None, None)`；**不编**。
    """
    for name in names:
        val, at = _find_key(block, name)
        if at is not None:
            return val, at, name
    return None, None, None


def lot_affordability_payload(data_root, reports_dir) -> dict:
    """今天这 20 只，**5 万买不买得起**（整手口径）。

    ## 它**只**回答「买不买得起」

    收益率/胜率/命中率是 `/api/status` 与 `/api/d2-verdict` 的职责。
    这里混进来就等于端点开始重算判决（第 137 层）。

    ## 价格**不许自己造**

    价格一律来自主库 `master/daily/{code}.parquet`
    （`aplus/store.py::LocalStore.read_daily`），
    取 `date <= as_of` 的最后一根 bar 的 `close`；
    **用哪一天的价格、复不复权**写在返回体的 `caliber` 块里。

    ## 一手多少股：**688/689 = 200，其余 = 100**

    科创板 200 股起买 ⇒ `688498.SH` 收盘 1010.35 ⇒ 1 手 ¥202,070
    = 全部本金的 4 倍。
    """
    from .store import LocalStore

    pred_date = latest_prediction_date(reports_dir)
    if not pred_date:
        raise HTTPException(404, "没有推荐产物（data/reports/predictions/ 下没有 pred_*.parquet）")
    up, down = load_predictions(reports_dir, pred_date)
    if up is None or down is None:
        raise HTTPException(404, f"推荐产物读不出来：pred_{pred_date}.parquet")

    budget = deployed_defaults()
    per_pos = budget["per_position_amount"]
    per_pos2 = budget["capital_div_positions"]

    store = LocalStore(data_root)
    items = []
    for frame in (up, down):
        for _, row in frame.iterrows():
            code = str(row["code"])
            close, bar_date = close_as_of(store, code, pred_date)
            shares = lot_shares_for(code)
            lot_cost = None if close is None else close * shares
            items.append({
                "code": code,
                "name": str(row.get("name", "")),
                "direction": str(row.get("direction", "")),
                "score": float(row["score"]) if row.get("score") is not None else None,
                "close": close,
                "close_date": bar_date,
                "lot_shares": shares,
                # 板块与**权限门槛**（🚨 外部常识·本仓未核实 ⇒ 只标不剔）
                "board": BOARD_RULES[board_of(code)]["label"],
                "permission_note": BOARD_RULES[board_of(code)]["permission"],
                "permission_verified": False,
                "lot_cost": lot_cost,
                "affordable_at_5000": None if lot_cost is None else lot_cost <= per_pos,
                "affordable_at_10000": None if lot_cost is None else lot_cost <= per_pos2,
                "capital_per_position": per_pos,
                # 差多少钱：买 1 手还缺多少（**不是**「差多少才划算」）
                "shortfall_at_5000": None if lot_cost is None else max(0.0, lot_cost - per_pos),
                "shortfall_at_10000": None if lot_cost is None else max(0.0, lot_cost - per_pos2),
                "price_missing_reason": (
                    None if close is not None
                    else f"主库里 {code} 在 {pred_date} 之前没有可用的收盘价"),
            })

    def _scope(rows):
        priced = [r for r in rows if r["lot_cost"] is not None]
        n = len(priced)
        aff1 = [r for r in priced if r["affordable_at_5000"]]
        aff2 = [r for r in priced if r["affordable_at_10000"]]
        return {
            "n": len(rows),
            "n_priced": n,
            "n_price_missing": len(rows) - n,
            "n_unaffordable": n - len(aff1),
            "investable_ratio": (len(aff1) / n) if n else None,
            "n_unaffordable_at_10000": n - len(aff2),
            "investable_ratio_at_10000": (len(aff2) / n) if n else None,
            "n_affordable": len(aff1),
        }

    up_rows = [r for r in items if r["direction"] == "up"]
    down_rows = [r for r in items if r["direction"] == "down"]
    up_scope = _scope(up_rows)
    down_scope = _scope(down_rows)

    # 最多能投出去多少（**仓位规模**，不是收益预测）
    max_deploy = up_scope["n_affordable"] * per_pos if up_scope["n_affordable"] else 0.0
    return {
        "source": f"pred_{pred_date}.parquet",
        "pred_date": pred_date,
        "budget": budget,
        "items": items,
        "summary": {
            "n_total": len(items),
            "n_up": len(up_rows),
            "n_down": len(down_rows),
            # ★ 主口径：**看涨腿**（要买的那几只）。看跌腿是回避清单，不买。
            "scope": "up",
            "n_unaffordable": up_scope["n_unaffordable"],
            "investable_ratio": up_scope["investable_ratio"],
            "n_unaffordable_at_10000": up_scope["n_unaffordable_at_10000"],
            "investable_ratio_at_10000": up_scope["investable_ratio_at_10000"],
            "n_affordable": up_scope["n_affordable"],
            "n_price_missing": up_scope["n_price_missing"] + down_scope["n_price_missing"],
            # 「只买买得起的那几只，按每份 ¥X 计，最多能投出去多少」
            # —— 这是**仓位规模**（本金能落地多少），**不是**收益率。
            "max_deployable_amount_up": max_deploy,
            "max_deployable_ratio_up": (max_deploy / budget["capital"]) if budget["capital"] else None,
            "by_direction": {"up": up_scope, "down": down_scope},
        },
        "caliber": {
            "price_source": ("主库 master/daily/{code}.parquet"
                             "（aplus/store.py::LocalStore.read_daily）—— 本端点**不造价格**"),
            "price_rule": "取 date <= as_of(=pred_date) 的最后一根 bar 的 close；实际用了哪一天见每条的 close_date",
            "price_adjusted": "不复权（主库 daily 存的就是不复权日线，见 aplus/store.py 的模块 docstring）",
            "why_not_entry_date": ("榜单是 pred_date 收盘后生成的 ⇒ 那一天能看见的收盘价就是"
                                   " pred_date 那根 bar；下一交易日（entry_date）的开盘价此刻还不存在。"
                                   "⇒ 「entry_date 之前最后一根 bar」在 pred_date 是交易日时**就是它自己**。"),
            "lot_rule": "688/689 = 200 股一手（科创板）；其余（主板/创业板/北交所）= 100 股一手",
            "budget_source": "inspect.signature(aplus.alloc.build_allocation).parameters[...].default",
            "budget_note": ("per_position_amount = capital × per_stock_cap（生产每份金额）；"
                            "capital_div_positions = capital ÷ max_positions（另一种分法）"),
            "field_names_carry_the_amount": ("字段名里的 5000 / 10000 是**当前生产默认值**下的金额；"
                                             "真实金额在 budget 块里（守卫钉着两者一致）"),
            "summary_scope": ("n_unaffordable / investable_ratio 的分母是**看涨腿**（要买的那几只）；"
                              "看跌腿是回避清单、不买 ⇒ 另列在 by_direction.down，不混进分母"),
            "this_is_not": ("不是收益预测、不是胜率、不是「该不该买」的判断 —— "
                            "只回答「本钱够不够买 1 手」"),
            "price_missing_is_not_zero": ("没有价 ⇒ affordable 给 null 而不是 true —— "
                                          "编一个 0 会让「买得起」恒真"),
        },
    }


def _deployed_d2_reading(reports_dir) -> dict:
    """**实盘档**的 D2 读数 —— 只认 `deployed_d2_verdict.json` 这一个产物。

    🚨 **不许回落成别的数**：`deployed_account_nav.json` 里也有一个半仓读数，
    但它自报 `role = "⚠️ 对照臂 / 推算，**不是判决**"` ⇒ **不是同一个量**。
    拿它顶替会让「实盘档读数」变成一句推算（第 7 层）。

    ⇒ 产物不存在 ⇒ 三个字段全 `null` + 一句「产物尚未生成」。
    """
    path = Path(reports_dir) / "deployed_d2_verdict.json"
    out = {
        "source": path.name,
        "available": False,
        "scope": None,
        "cagr": None,
        "verdict_mde_annual": None,
        "gap_to_target_pp": None,
        "upper_one_sided_95": None,
        "target": None,
        "measured_sign_determinable": None,
        "cases": None,
        "found_at": {},
        "found_key": {},
        "raw": None,
        "note": "产物尚未生成（`deployed_d2_verdict.json` 未落盘）—— 不拿判决档的数顶替",
    }
    doc, err = _read_json(path)
    if err is not None:
        out["note"] = f"产物尚未生成：{err} —— 不拿判决档的数顶替"
        return out
    if not isinstance(doc, dict):
        out["note"] = f"产物内容不是一个对象（异常）：{path.name}"
        return out
    out["available"] = True
    # 🚨 **锚在 `verdict` 块上**，不许满文档找键。
    # 那份产物里**到处**都有 `cagr`（`arms` 每档一个、`account_legs`、
    # `control_cross_check`…）⇒ 满文档找第一个 `cagr` 很可能找到的是
    # **满仓判决档**的那个 ⇒ 读数**看起来有数、其实读错了对象**
    # （「找到了 ≠ 找对了」）。⇒ 锚块 + 把 JSON 路径一起递出去。
    block = doc.get(DEPLOYED_D2_VERDICT_BLOCK)
    scope = block if isinstance(block, dict) else doc
    scope_path = f"$.{DEPLOYED_D2_VERDICT_BLOCK}" if isinstance(block, dict) else "$"
    out["scope"] = scope_path
    for key, names in DEPLOYED_D2_KEYS.items():
        val, at, used = _lookup_in_block(scope, names)
        out[key] = val
        if at:
            out["found_at"][key] = scope_path + at[1:]
            out["found_key"][key] = used
    text = path.read_text(encoding="utf-8")
    if len(text) <= 200_000:
        out["raw"] = doc
    out["note"] = (f"逐字透传 `{path.name}` 的 {scope_path} 块；本端点一个数都不算")
    return out


def deployed_config_payload(reports_dir) -> dict:
    """「判决配置 vs 实盘配置」**三层**差异 —— 逐字摊开。

    ## 三层是什么

    | 层 | 它是什么 | 它的数从哪来 |
    |---|---|---|
    | 判决侧 | `basket_nav_01_2026-10-16.json` 量的那套配置 | 产物字段（逐字） |
    | 实盘侧 | 生产**今天真的在跑**的那套配置 | 生产签名 + 常量（逐字） |
    | 实盘档读数 | 实盘配置下的 D2 年化读数 | `deployed_d2_verdict.json`（逐字） |

    ## 🚨 **判决侧的数不许说成「你的年化」**

    判决档是**满仓判决档**（`basket_nav_01_2026-10-16.json` 量的那套）；
    实盘侧是**生产今天真的在跑**的那套（敞口/仓数/K/因子数**逐字取
    `deployed_defaults()` 与生产常量**，标签由 `position_label(敞口)` 推）。
    两个数**不是同一个量**，**不可混引、不可同图比较**（口径台账纪律）。

    ⚠️ **仓数与仓位不许写死在这段散文里**（2026-10-21）：
    实盘配置改过一次（半仓 5 只 → 满仓 10 只），
    而手打的串还在输出「半仓」⇒ **屏上出现自相矛盾的话**。
    ⇒ 散文只说「哪一套」，**数与标签一律从 `deployed_dim` 推**。

    ## 🚨 **端点里一个数都不算**

    判决读数逐字取产物字段；实盘配置逐字取生产签名/常量；
    实盘档读数逐字取 `deployed_d2_verdict.json`。
    唯一带算式的是**现金占比**（`1 − 敞口`），它标着 `how`。
    """
    reports_dir = Path(reports_dir)
    verdict_path = reports_dir / "basket_nav_01_2026-10-16.json"
    doc, err = _read_json(verdict_path)
    if err is not None:
        raise HTTPException(404, f"判决产物读不出来：{err}（跑 scripts/measure_basket_nav_01.py 生成）")
    tiers = doc.get("tiers") or []
    if len(tiers) < 2 or not isinstance(tiers[1], dict):
        raise HTTPException(404, "判决产物里没有 tiers[1]（判决档）⇒ 拿不到判决档的读数")
    t1 = tiers[1]

    # ── 判决侧：配置维度从**产物链**上读（每个维度带自己的 source）──────
    # 判决产物的上游由它自己的 `artifact` 字段指认 ⇒ 顺着链走，不猜文件名。
    upstream_name = None
    art = doc.get("artifact")
    if isinstance(art, str) and art.endswith(".daily.parquet"):
        upstream_name = Path(art).name[: -len(".daily.parquet")] + ".json"
    upstream, up_err = ((None, None) if upstream_name is None
                        else _read_json(reports_dir / upstream_name))

    gap_doc, gap_err = _read_json(reports_dir / "d2_signal_requirement.json")
    eq = ((gap_doc or {}).get("caliber") or {}).get("equity_weight") if gap_doc else None

    verdict_dim = {
        "exposure": eq,
        "n_positions": (upstream or {}).get("top_n"),
        "signal_weights": (upstream or {}).get("factors"),
        "rebalance_every": (upstream or {}).get("rebalance_every"),
    }
    verdict_src = {
        "exposure": ("d2_signal_requirement.json::caliber::equity_weight"
                     if eq is not None else
                     (f"读不出来：{gap_err}" if gap_err else "读不出来")),
        "n_positions": (f"{upstream_name}::top_n" if upstream else
                         (f"读不出来：{up_err}" if up_err else "读不出来")),
        "signal_weights": (f"{upstream_name}::factors（权重逐年变，见 years[*].weights）"
                           if upstream else
                           (f"读不出来：{up_err}" if up_err else "读不出来")),
        "rebalance_every": (f"{upstream_name}::rebalance_every" if upstream else
                            (f"读不出来：{up_err}" if up_err else "读不出来")),
    }

    dd = deployed_defaults()
    deployed_dim = {
        "exposure": dd["equity_weight"],
        "n_positions": dd["max_positions"],
        "signal_weights": dict(DEFAULT_WEIGHTS),
        "rebalance_every": int(LIVE_REBALANCE_EVERY),
    }
    deployed_src = {
        "exposure": "inspect.signature(aplus.alloc.build_allocation).parameters[...]（持仓数 × 单票上限）",
        "n_positions": "inspect.signature(aplus.alloc.build_allocation).parameters['max_positions'].default",
        "signal_weights": "aplus/factor.py::DEFAULT_WEIGHTS（回落默认，从未赛马）",
        "rebalance_every": "aplus/evolve.py::LIVE_REBALANCE_EVERY",
    }

    # 判决侧的每份金额：判决档是**满仓**（敞口 1.0）× 本金 ⇒ 每只分到
    # capital / n_positions。本金取生产默认值（判决成本腿按 50_000 标定，
    # 产物自己的 caliber 逐字写着 `annual_friction(50_000, 10, …)`）。
    v_cap = dd["capital"]
    v_n = verdict_dim["n_positions"]
    v_amount = (v_cap / v_n) if isinstance(v_n, int) and v_n else None
    d_amount = dd["per_position_amount"]
    verdict_dim["per_position_amount"] = v_amount
    verdict_src["per_position_amount"] = (
        f"capital({v_cap:g}, 生产默认值) ÷ n_positions({v_n})"
        " —— 判决档的成本腿按 `annual_friction(50_000, 10, …)` 标定"
        if v_amount is not None else "读不出来")
    deployed_dim["per_position_amount"] = d_amount
    deployed_src["per_position_amount"] = (
        f"capital({v_cap:g}) × per_stock_cap({dd['per_stock_cap']:g})"
        " —— 生产给每只的钱")

    verdict_dim["cash_weight"] = (None if verdict_dim["exposure"] is None
                                  else 1.0 - verdict_dim["exposure"])
    verdict_src["cash_weight"] = "1 − 敞口(=1.0，满仓判决档)"
    deployed_dim["cash_weight"] = dd["cash_weight"]
    deployed_src["cash_weight"] = "1 − 敞口（aplus/alloc.py::build_allocation 的同一式子）"

    def _fmt_weights(side):
        w = side.get("signal_weights")
        if isinstance(w, dict):
            return "、".join(f"{k} {v:g}" for k, v in w.items())
        if isinstance(w, list):
            return "、".join(str(x) for x in w)
        return "—"

    verdict_display = {
        # ★ 标签**从数里推**（`position_label`），不许手打
        #   （2026-10-21：实盘侧的手打串输出过「半仓 100%」这种自相矛盾的话）。
        "exposure": (exposure_display(verdict_dim["exposure"])
                     if verdict_dim["exposure"] is not None else "—"),
        "n_positions": f"{verdict_dim['n_positions']} 只" if verdict_dim["n_positions"] else "—",
        "per_position_amount": (f"¥{verdict_dim['per_position_amount']:,.0f} / 只"
                                if verdict_dim["per_position_amount"] else "—"),
        "signal_weights": (f"{len(verdict_dim['signal_weights'])} 因子 · 逐年 IC 定权"
                           if isinstance(verdict_dim["signal_weights"], list) else "—"),
        "rebalance_every": (f"每 {verdict_dim['rebalance_every']} 个交易日"
                            if verdict_dim["rebalance_every"] else "—"),
        "cash_weight": ("0%（全仓）" if verdict_dim["cash_weight"] == 0 else "—"),
    }
    deployed_display = {
        # ★ 同上：标签从敞口推 ⇒ 敞口 1.0 时输出「满仓 100%」，
        #   **不可能**再输出「半仓 100%」。
        "exposure": exposure_display(deployed_dim["exposure"]),
        "n_positions": f"{deployed_dim['n_positions']} 只",
        "per_position_amount": f"¥{deployed_dim['per_position_amount']:,.0f} / 只",
        "signal_weights": f"{len(deployed_dim['signal_weights'])} 因子 · 固定权重",
        "rebalance_every": f"每 {deployed_dim['rebalance_every']} 个交易日",
        "cash_weight": f"{deployed_dim['cash_weight'] * 100:g}%",
    }
    labels = {
        "exposure": "权益敞口（投出去的钱占本金）",
        "n_positions": "持仓数",
        "per_position_amount": "每份金额",
        "signal_weights": "信号权重",
        "rebalance_every": "换仓频率",
        "cash_weight": "现金占比",
    }
    order = ("exposure", "n_positions", "per_position_amount",
             "signal_weights", "rebalance_every", "cash_weight")
    dimensions = []
    for dim in order:
        v = verdict_dim.get(dim)
        d = deployed_dim.get(dim)
        dimensions.append({
            "dim": dim,
            "label": labels[dim],
            "verdict": v,
            "deployed": d,
            "verdict_display": verdict_display[dim],
            "deployed_display": deployed_display[dim],
            "verdict_source": verdict_src[dim],
            "deployed_source": deployed_src[dim],
            "differs": (v != d) if (v is not None and d is not None) else None,
        })

    years = (upstream or {}).get("years") or []
    by_year = {}
    for y in years:
        if isinstance(y, dict) and y.get("weights") is not None:
            by_year[str(y.get("year"))] = y["weights"]

    deployed_d2 = _deployed_d2_reading(reports_dir)

    return {
        "verdict_side": {
            "source": verdict_path.name,
            "upstream_source": upstream_name,
            "upstream_artifact_pointer": art,
            "dimensions": verdict_dim,
            "sources": verdict_src,
            # ★ 同上：判决侧的仓位标签也从**数**推（两侧口径一致）。
            "position_label": position_label(verdict_dim["exposure"]),
            "exposure": verdict_dim["exposure"],
            "signal_weights_by_year": by_year or None,
            "signal_weights_source": ((upstream or {}).get("years") or [{}])[0].get("weights_source")
            if years else None,
            # 判决读数：逐字取产物字段（端点里一个数都不算）
            "readings": {
                "verdict_reading": doc.get("verdict_reading"),
                "verdict_mde_annual": doc.get("verdict_mde_annual"),
                "verdict_upper_one_sided_95": doc.get("verdict_upper_one_sided_95"),
                "target": doc.get("target"),
                "verdict_text": doc.get("verdict_text"),
                "verdict_rule": doc.get("verdict_rule"),
                "verdict_cases_fired": doc.get("verdict_cases_fired"),
                "verdict_tier_cagr": t1.get("cagr"),
                "verdict_tier_arith_ann": t1.get("arith_ann"),
                "verdict_tier_n_days": t1.get("n_days"),
                "verdict_tier_years": t1.get("years"),
                "verdict_tier_label": t1.get("tier"),
            },
            "caliber": doc.get("caliber"),
        },
        "deployed_side": {
            "source": ("inspect.signature(aplus.alloc.build_allocation).parameters[...].default"
                       " + aplus.evolve.LIVE_REBALANCE_EVERY + aplus.factor.DEFAULT_WEIGHTS"),
            "dimensions": deployed_dim,
            "sources": deployed_src,
            "budget": dd,
            "signal_weights": dict(DEFAULT_WEIGHTS),
            # ★ 仓位标签**从敞口推**（前端一个词都不许手打 ——
            #   2026-10-21：手打的「半仓」在敞口变成 1.0 后输出了自相矛盾的话）。
            "position_label": position_label(deployed_dim["exposure"]),
            "exposure": deployed_dim["exposure"],
        },
        "dimensions": dimensions,
        "deployed_d2_reading": deployed_d2,
        "caliber": {
            "the_two_sides_are_not_the_same_quantity": (
                "判决侧是**满仓判决档**（`basket_nav_01_2026-10-16.json`）；"
                "实盘侧是生产**今天真的在跑**的那套。"
                "⇒ 两边的年化**不是同一个量**，**不可混引、不可同图比较**。"),
            "the_verdict_reading_is_not_your_annual_return": (
                "⚠️ 判决侧的 cagr 是**判决档**的读数，**不是**「你的年化」。"
                "屏幕上看它时必须带着「哪一档」这个标签。"),
            "no_number_is_computed_here": (
                "判决读数逐字取产物字段；实盘配置逐字取生产签名/常量；"
                "实盘档读数逐字取 `deployed_d2_verdict.json`。"
                "唯一带算式的是现金占比（1 − 敞口），它标着 `how`。"),
            "deployed_d2_reading_has_no_fallback": (
                "实盘档读数**只**认 `deployed_d2_verdict.json`；"
                "产物不在 ⇒ 三个字段全 null + 一句「产物尚未生成」，"
                "**不许**回落成 `deployed_account_nav.json` 的推算数（它自报「不是判决」）。"),
            "verdict_side_sources_are_per_dimension": (
                "判决侧的每个维度带自己的 source：敞口来自 "
                "`d2_signal_requirement.json::caliber::equity_weight`，"
                "持仓数/权重/换仓频率来自判决产物的上游 "
                "`wf_weights_backtest_k21.json`（判决产物自己的 `artifact` 字段指认它）。"),
        },
    }


def _holdings_alert_payload(reports_dir) -> dict:
    """持仓级降级预警 —— **最新一份**产物，逐字递到页面上。

    ## 它回答什么

    「我手上这几只里，哪几只已经掉到最差那一档？」——
    每只一条动作（REDUCE / WATCH / HOLD / UNDECIDED）**与它自己的判据**。

    ## 三条纪律（页面层）

    ① **判据逐字透传**（`items[i].action_criterion`）：页面**不许**自己
       重新判定、不许只显示一个标签。
    ② **「未定」必须显示成「未定」**：`UNDECIDED` = 测不出
       （缺建仓分 / 不在当日全池），**不许**渲染成「持有」。
    ③ **产物不在就说没有**：没有产物 ⇒ `available=False` + 一句原因，
       **不许**拿别的东西顶替。

    ## 新鲜度必须一起递

    产物是**每天自动重跑**的 ⇒ 屏上的读数会过期
    ⇒ 页面必须能看见「这一份是什么时候生成的、还新不新」。
    """
    from . import provenance

    reports_dir = Path(reports_dir)
    # ⚠️ **sidecar 也叫 `holdings_alert_<日期>.json.provenance.json`** ⇒ 它**也**匹配
    #   `holdings_alert_*.json`（glob 只看结尾）⇒ 不排掉它就会把**章**当成产物读
    #   （实测：items 全空、question 为 null、freshness 判「没有 sidecar」）。
    files = sorted(p for p in reports_dir.glob("holdings_alert_*.json")
                   if not p.name.endswith(".provenance.json"))
    if not files:
        return {
            "available": False,
            "note": ("还没有持仓预警产物（每天跑完日频流水线后落盘："
                     "data/reports/holdings_alert_<信号日>.json）"),
            "items": [],
            "summary": None,
        }
    path = files[-1]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return {"available": False, "note": f"{path.name} 读不出来（{exc!r}）",
                "items": [], "summary": None}

    labels = {"REDUCE": "减仓（减持/卖出）", "WATCH": "观察",
              "HOLD": "持有", "UNDECIDED": "未定（测不出）"}
    items = []
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        action = it.get("action")
        items.append({
            "code": it.get("code"),
            "name": it.get("name"),
            "action": action,
            "action_label": labels.get(action, "—"),
            "criterion": it.get("action_criterion"),
            "score": it.get("score"),
            "rank_in_universe": it.get("rank_in_universe"),
            "percentile": it.get("percentile"),
            "is_in_bottom_decile": it.get("is_in_bottom_decile"),
            "is_in_bottom_quintile": it.get("is_in_bottom_quintile"),
            "entry_score": it.get("entry_score"),
            "relative_to_entry_score": it.get("relative_to_entry_score"),
        })
    fresh = {}
    try:
        f = provenance.freshness(path, root=Path(__file__).resolve().parents[1])
        fresh = {"verdict": f.get("verdict"), "reason": f.get("reason"),
                 "byte_evidence": f.get("byte_evidence"),
                 "stamped_at": f.get("stamped_at"),
                 "generator": f.get("generator")}
    except Exception as exc:      # noqa: BLE001 - 新鲜度读不出来不许带崩整页
        fresh = {"verdict": "unknown", "reason": f"新鲜度读不出来（{exc!r}）"}
    return {
        "available": True,
        "as_of": doc.get("as_of"),
        "report": path.name,
        "question": doc.get("question"),
        "items": items,
        "summary": doc.get("summary"),
        "population": doc.get("population"),
        "entry": doc.get("entry"),
        "criteria": doc.get("criteria"),
        "boundaries": doc.get("boundaries"),
        "what_this_cannot_prove": doc.get("what_this_cannot_prove"),
        "freshness": fresh,
        "note": None,
    }


#: 目标进度快照的文件名模式（与 `scripts/measure_goal_progress.py` 的输出同名）。
#: 🚨 走**模式**而不是逐个列名 —— 每天一份 ⇒ 逐名登记就是形状 17。
GOAL_PROGRESS_FILE_RE = re.compile(r"^goal_progress_(\d{4}-\d{2}-\d{2})\.json$")


def _goal_progress_payload(reports_dir) -> dict:
    """**目标达成进度**：最新快照 + 历日历史 + **缺档披露**。

    ## 🚨 本函数**一个数都不算**

    所有读数**逐字**来自快照产物（`scripts/measure_goal_progress.py` 写的
    `goal_progress_<日期>.json`）。本函数只做**搬运 + 排序 + 缺档披露**。
    ⇒ 「还差几倍」取哪一列、两个 D2 口径怎么并排，**全部在快照里已经定好**，
    前端不许自己决定（第 143 层）。

    ## 🚨 缺档的三种形态**分开说**

    | 形态 | 处置 |
    |---|---|
    | **一份都没有** | **404 + 一句原因**（空数组与「读不出来」长得一样 ⇒ 形状 23） |
    | **某一份读不出来** | 它**仍然出现在 `history` 里**，带 `readable: false` 与错误原文 ⇒ **不许静默跳过**（跳过 = 把「没跑到」读成「那天没变化」） |
    | **中间漏了几天** | 那些天进 `gaps` ⇒ 不许让时间轴看起来连续 |

    ## 🚨 `latest` 是**最新可读**的那一份

    若最新那一份读不出来 ⇒ `latest` 退到上一份可读的，
    并在 `latest_note` 里**明说退到了哪一天、为什么**
    ⇒ 不许让用户以为看到的是今天的进度。
    """
    files = []
    for p in sorted(Path(reports_dir).glob("goal_progress_*.json")):
        m = GOAL_PROGRESS_FILE_RE.match(p.name)
        if m:
            files.append((m.group(1), p))
    if not files:
        raise HTTPException(
            404,
            f"目标进度快照一份都没有（{Path(reports_dir).name}/goal_progress_<日期>.json）"
            "—— 跑 scripts/measure_goal_progress.py 生成（每晚 cron 会自动跑）")
    files.sort()

    history = []
    readable = []          # (date, doc)
    for day, path in files:
        entry = {"date": day, "file": path.name, "readable": False,
                 "error": None, "generated_at": None, "summary_line": None,
                 "change_line": None, "statuses": {}}
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            history.append(entry)
            continue
        if not isinstance(doc, dict):
            entry["error"] = "内容不是一个对象（异常）"
            history.append(entry)
            continue
        change = doc.get("change") or {}
        entry.update({
            "readable": True,
            "generated_at": doc.get("generated_at"),
            "summary_line": doc.get("summary_line"),
            "change_line": change.get("line"),
            "statuses": {str(g.get("id")): g.get("status")
                         for g in (doc.get("goals") or []) if isinstance(g, dict)},
        })
        history.append(entry)
        readable.append((day, doc))

    if not readable:
        raise HTTPException(
            404,
            "目标进度快照有 "
            f"{len(files)} 份，但**一份都读不出来** ⇒ "
            + "；".join(f"{e['date']}: {e['error']}" for e in history))
    latest_date, latest_doc = readable[-1]
    newest_date = files[-1][0]
    latest_note = None
    if newest_date != latest_date:
        latest_note = (
            f"⚠️ 最新一份（{newest_date}）读不出来 ⇒ 这里显示的是 "
            f"{latest_date} 那一份（**不是最新**）。")

    return {
        "status": "ok",
        "latest": latest_doc,
        "latest_date": latest_date,
        "latest_note": latest_note,
        "history": history,
        "gaps": _goal_progress_gaps([d for d, _ in files]),
        "n_snapshots": len(files),
    }


def _goal_progress_gaps(days: list) -> list:
    """历日快照之间**缺的日历日** —— 明写出来，不许让时间轴看起来连续。

    ⚠️ 缺一天可能是 cron 没跑、也可能是那天真的没跑成
    ⇒ 这里**只报缺哪几天**，不猜原因（原因在快照的 `status` 里）。
    """
    if len(days) < 2:
        return []
    from datetime import date as _date
    out = []
    for a, b in zip(days, days[1:]):
        try:
            d0 = _date.fromisoformat(a)
            d1 = _date.fromisoformat(b)
        except ValueError:
            continue
        cur = d0
        while cur < d1:
            cur = cur.fromordinal(cur.toordinal() + 1)
            if cur < d1:
                out.append(cur.isoformat())
    return out


def create_app(data_root, llm=None) -> FastAPI:
    data_root = Path(data_root)
    reports_dir = data_root / "reports"
    db_path = data_root / "chat.db"

    if llm is None:
        from .llm import default_llm_chain

        llm = default_llm_chain()  # glm 代理（主）→ MiniMax（备）自动降级

    app = FastAPI(title="aplus 量化平台")
    app.state.llm = llm
    app.add_middleware(NoCacheMiddleware)

    @app.get("/api/status")
    def status():
        daily_dir = data_root / "master" / "daily"
        universe = len(list(daily_dir.glob("*.parquet"))) if daily_dir.exists() else 0
        return {
            "as_of": date.today().isoformat(),
            "last_prediction_date": latest_prediction_date(reports_dir),
            "universe_size": universe,
            "stats": _stats_with_compounded(_scoped_hist_for_api(reports_dir)),
            # 🚨 作用域状态：让前端能说「不可判定」而不是渲染成 0%
            "signal_scope": _signal_scope_payload(reports_dir),
            "paper_mode": True,
            # 2026-10-17 接上。此前 `holdings.json` 与 `live_execution_status`
            # **零消费点**：用户看到每天 10 涨 10 跌，却看不到「实盘每 21 个
            # 交易日换一次仓」。差 60.93pp/年摩擦，且与信号好坏无关。
            "live": _live_holdings_payload(reports_dir),
            # 2026-10-18 接上。此前 `score_ranking_power.json` 的判决
            # **零消费点**（`index.html` / `verdict.js` 对这个词零命中）：
            # 页面上天天出 10 涨 10 跌，**却没有任何一处说这份名单不许发布**。
            # ⇒ 与 `live` 并排而不是并进 `stats`：见 `_publish_gate_payload` 开头。
            "publish_gate": _publish_gate_payload(reports_dir),
        }

    @app.get("/api/predictions/latest")
    def predictions_latest():
        """最新榜单 + **它是用哪组权重排出来的**。

        ## 为什么必须带上 `weights_check`

        2026-10-14 实测：概览页显示的榜单来自 `pred_2026-10-13.parquet`，
        那是默认权重符号**整体翻转之前**生成的。那份榜单的看涨前 6 名
        20 日动量全是正的（+164.5% / +122.8% / +71.7% / +105.7% / +98.0% / +79.2%），
        而实测 IC（`factor_ic_7y.json`）说 `mom20` 为负（全段 IC −0.0357）——
        符号修正后，看涨榜应该全是低动量股。用户要的是「明天会涨的 10 只」，
        屏幕上这张榜却是用一把实测会挑反的尺子排出来的。

        三态（`match` / `mismatch` / `unknown`）见 `reports.prediction_weights_verdict`。
        **`unknown` 必须被前端当报警处理**，理由是无法证明一致 ≠ 一致。

        ## 参考权重走 `load_weights`，不写死 DEFAULT_WEIGHTS

        `weights.json` 一旦落盘（赛马晋级），参考就该是晋级权重；
        写死默认值会在晋级那一刻把每张榜单都误报成 mismatch。

        ## 归档接口（`/api/predictions/history`）**不带**这个状态

        那个接口一次返回几十天。带着状态字段过去，「所有历史文件一起报警」
        才会成立——而那正是这套机制唯一站得住脚的地方：只判概览页正在
        显示的那一张。
        """
        from .evolve import load_weights
        from .reports import prediction_weights_verdict

        lp = latest_prediction_date(reports_dir)
        if lp is None:
            raise HTTPException(404, "尚无预测记录")
        up, down = load_predictions(reports_dir, lp)
        check = prediction_weights_verdict(reports_dir, lp, load_weights(reports_dir)["weights"])
        return {"date": lp, "up": _json_safe(up), "down": _json_safe(down),
                "weights_check": check}

    @app.get("/api/verification/history")
    def verification_history():
        hist = load_verification_history(reports_dir)
        return _json_safe(hist) if len(hist) else []

    @app.get("/api/allocation/latest")
    def allocation_latest():
        lp = latest_prediction_date(reports_dir)
        alloc = load_allocation(reports_dir, lp) if lp else None
        if alloc is None:
            raise HTTPException(404, "尚无配置方案")
        # ★ 优先用**产物内容里的** date（`save_allocation` 现在会写进去）。
        #
        # 历史：alloc_*.json 里**没有** date 字段 —— 实测扫遍 6 个文件的每一层：
        #   顶层键只有 capital / cash_weight / positions / rebalance / risk_notes，
        #   rebalance 元素只有 {code, action}。根因是写盘那一步
        #   `save_allocation(reports_dir, today, plan.to_dict())`（scripts/run_daily.py:579）
        #   把 date **只拿去拼文件名**，从不写进内容
        #   ⇒ 产物的身份被劈成两路：文件名有、内容没有。
        #   于是前端 `a.date ?? ''` 渲染成「生成于 」—— 一个不报错、不留痕、
        #   控制台干净的空值（E3：界面说的与事实不符，且没有任何报警）。
        # 根因已修（`aplus/reports.py:save_allocation` 用 setdefault 写进内容），
        # 新跑出的文件都有 `date`。下面这行只是**旧文件**的过渡回退。
        #
        # ⚠️ 回退值 `lp` 不是编的：它就是 `load_allocation` 刚刚用的文件名日期分量
        #    （`_alloc_path` → `alloc_{lp}.json`），构造上不会与内容分叉。
        # 🚨 但**内容有 date 而它 ≠ lp 时，必须以内容为准并让守卫抓到** ——
        #    那种分叉意味着写盘路径上有人绕过了 `save_allocation`。
        #    这里 `content_date or lp` 恰好做到：非空即用内容。
        # ⚠️ 用 `{**alloc, ...}` 而不是就地改 alloc：不让响应组装去污染
        #    读出来的数据结构，也保证键顺序稳定（守卫逐键比对）。
        #
        # ── 调仓差异（2026-10-20 修的 P0）────────────────────────────
        # 概览页原来只列「要买的 5 只」，看不出**哪些该留、哪些该卖**。
        # 根因：`run_daily.py` 调 `build_allocation(up, capital=…)` **没传**
        # `current_positions` ⇒ keep/remove **结构上不可能出现**，
        # 产物里只有 5 条 add，而产物与界面都看不出「没算」与「全买」的区别。
        # ⇒ 现在两个字段一起递：
        #   `rebalance`         = [{code, name, action, weight, amount}]
        #   `rebalance_source`  = 'current_positions' | 'none'
        #     'none' ⇒ **没传当前持仓** ⇒ 差异不可信（前端必须说出来）。
        # ⚠️ 旧产物（2026-10-20 之前）里没有 `rebalance_source`，
        #    且 rebalance 元素只有 {code, action} ⇒ 一律按 'none' 递，
        #    缺字段填 `null`（**不编**：不从 `positions` 里猜，那是另一个口径）。
        return {
            **alloc,
            "date": alloc.get("date") or lp,
            "rebalance": _alloc_rebalance_rows(alloc.get("rebalance") or []),
            "rebalance_source": alloc.get("rebalance_source") or "none",
        }

    @app.post("/api/chat")
    def chat(body: ChatIn):
        message = body.message.strip()
        if not message:
            raise HTTPException(400, "消息不能为空")
        with _connect(db_path) as conn:
            conv_id = body.conversation_id
            if conv_id is None:
                cur = conn.execute(
                    "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
                    (message[:24], time.strftime("%Y-%m-%d %H:%M:%S")),
                )
                conv_id = cur.lastrowid
            else:
                exists = conn.execute("SELECT 1 FROM conversations WHERE id=?", (conv_id,)).fetchone()
                if not exists:
                    raise HTTPException(404, "会话不存在")
            conn.execute(
                "INSERT INTO messages (conv_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                (conv_id, message, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conv_id=? ORDER BY id", (conv_id,)
            ).fetchall()
        history = [{"role": r, "content": c} for r, c in rows]
        system = f"{SYSTEM_PROMPT}\n\n平台当日状态（回答须以此为准）：\n{_platform_context(reports_dir)}"
        try:
            reply = llm.chat(history, system=system)
        except Exception as exc:  # noqa: BLE001 — 代理不可用时给用户明确错误
            raise HTTPException(502, f"模型连接失败：{exc}") from exc
        with _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO messages (conv_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                (conv_id, reply, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
        return {"conversation_id": conv_id, "reply": reply}

    @app.post("/api/chat/stream")
    def chat_stream(body: ChatIn):
        """SSE 真流式对话。事件契约（前端按此写死分支，一个字都不能改）：

        | event | data | 语义 |
        |---|---|---|
        | start | `{"conversation_id"}` | 消息已入库、开始调模型 |
        | thinking | `{"text": 增量}` | 思考增量，可以一个都没有 |
        | text | `{"text": 增量}` | 正文增量 |
        | done | `{"conversation_id", "reply"}` | 结束；text 增量拼接 == reply |
        | error | `{"message": 中文}` | 只发一次，之后结束，不发 done |

        ## 三条「不这样写就会静默出错」的纪律

        ① **一切错误都走 error 事件，不用 HTTP 4xx/5xx**：
        前端此时已经连上 SSE，拿到非流式响应只能显示空白。
        所以空消息 / 会话不存在 / 代理断线，返回的都是 200 + error。

        ② **assistant 在 done 之前一次性入库**：
        先发 done 再落库 ⇒ 刷新页面回复就没了，而前端**不报错**。
        （`tests/test_chat_stream.py` 在读到 done 的当口查库来钉这个顺序。）

        ③ **断流不入库**：模型吐了一半才断时**不**落 assistant，
        否则 `/api/conversations/{id}` 会原样返回半截正文，前端刷新后
        半句话永久留在对话里，且此后每轮都带着它进 history。
        """
        message = body.message.strip()
        if not message:
            return _sse_response(iter([_sse("error", {"message": "消息不能为空"})]))

        # 与非流式端点同样的三段式：连接内插 user + 读全量 history，连接外调模型。
        try:
            with _connect(db_path) as conn:
                conv_id = body.conversation_id
                if conv_id is None:
                    cur = conn.execute(
                        "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
                        (message[:24], time.strftime("%Y-%m-%d %H:%M:%S")),
                    )
                    conv_id = cur.lastrowid
                else:
                    exists = conn.execute(
                        "SELECT 1 FROM conversations WHERE id=?", (conv_id,)).fetchone()
                    if not exists:
                        raise _UserFacing("会话不存在")
                conn.execute(
                    "INSERT INTO messages (conv_id, role, content, created_at) "
                    "VALUES (?, 'user', ?, ?)",
                    (conv_id, message, time.strftime("%Y-%m-%d %H:%M:%S")),
                )
                rows = conn.execute(
                    "SELECT role, content FROM messages WHERE conv_id=? ORDER BY id", (conv_id,)
                ).fetchall()
            history = [{"role": r, "content": c} for r, c in rows]
            system = (
                f"{SYSTEM_PROMPT}\n\n平台当日状态（回答须以此为准）：\n"
                f"{_platform_context(reports_dir)}"
            )
        except _UserFacing as exc:
            return _sse_response(iter([_sse("error", {"message": str(exc)})]))
        except Exception as exc:  # noqa: BLE001 — 建上下文失败也要给人话，不能 500
            return _sse_response(iter([_sse("error", {"message": f"服务端错误：{exc}"})]))

        def gen():
            yield _sse("start", {"conversation_id": conv_id})
            parts: list = []
            try:
                for kind, delta in llm.chat_stream(history, system=system):
                    if kind == "text":
                        parts.append(delta)
                        yield _sse("text", {"text": delta})
                    elif kind == "thinking":
                        yield _sse("thinking", {"text": delta})
                    else:
                        # 不认识就喊出来。默认当 thinking/text 处理 = 静默吞掉一类事件。
                        raise ValueError(f"未知的增量类型：{kind!r}")
            except Exception as exc:  # noqa: BLE001 — 断连必须变成人话
                yield _sse("error", {"message": f"模型连接失败：{exc}"})
                return  # 不落半截 assistant，也绝不发 done
            reply = "".join(parts)
            with _connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO messages (conv_id, role, content, created_at) "
                    "VALUES (?, 'assistant', ?, ?)",
                    (conv_id, reply, time.strftime("%Y-%m-%d %H:%M:%S")),
                )
            yield _sse("done", {"conversation_id": conv_id, "reply": reply})

        return _sse_response(gen())

    @app.get("/api/llm/info")
    def llm_info():
        """**运行态**的链路事实，供界面显示「现在到底在跑哪个模型」。

        ## 为什么不允许写死模型名

        2026-10-14：`aplus/llm.py` 里 M3 客户端已被删除（只剩 M3.1），
        而 `web/index.html` 硬编码着「MiniMax M3（思考模式）主链」——
        界面显示的模型与实际跑的模型不一致，没有任何测试或告警。
        **硬编码的标签天生会漂移，因为它不受任何运行态约束。**
        所以这里每个值都从活着的客户端对象上读；取不到就是 `null`，
        **绝不回退到硬编码名字**——回退等于把要消灭的漂移又请回来一次。
        """
        members = _chain_members(app.state.llm)
        return {
            "chain": [_chain_entry(c) for c in members],
            "primary": _chain_entry(members[0])["name"],
            "last_served_by": getattr(app.state.llm, "last_served_by", None),
        }

    @app.get("/api/conversations/{conv_id}")
    def conversation(conv_id: int):
        with _connect(db_path) as conn:
            exists = conn.execute("SELECT 1 FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if not exists:
                raise HTTPException(404, "会话不存在")
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE conv_id=? ORDER BY id", (conv_id,)
            ).fetchall()
        return [
            {"role": r, "content": c, "created_at": t} for r, c, t in rows
        ]

    @app.get("/api/run-status")
    def run_status():
        from .reports import read_run_status

        s = read_run_status(reports_dir)
        return s if s else {"status": "never_run"}

    @app.get("/api/backtest")
    def backtest():
        summary_path = reports_dir / "backtest_summary.json"
        bars_path = reports_dir / "backtest.parquet"
        if not summary_path.exists():
            raise HTTPException(404, "尚无回测记录")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        daily = []
        if bars_path.exists():
            from .backtest import daily_series

            daily = daily_series(pd.read_parquet(bars_path))
        return {"summary": summary, "daily": daily}

    @app.get("/api/weights")
    def weights():
        from .evolve import load_weights

        loaded = load_weights(reports_dir)
        # `caveat` / `execution` 必须原样递出去（2026-10-14）。
        #
        # 原实现只回 weights/evidence/source，把两句关键信息都丢了：
        #   · `caveat` —— 「回落默认权重是**已知的坏配置**（81/87 名、t=−3.03）」。
        #     丢掉它之后前端写「未经赛马验收的默认值」，把「测过、是坏的」
        #     说成了「没测过」。方向完全相反。
        #   · `execution` —— 回落固定 K=1，而台账第 19 条实测 K=1 与 K=63
        #     差 **276 个百分点**（−168.9% vs +105.7%）。前端看不到 K，
        #     用户以为每天出一次单是免费的。
        #
        # `source` 也不再压成 `default`：`fallback` 才说得清「没落盘」，
        # `default` 读起来像「一份已知的默认配置」。
        #
        # `caveat_kind` 必须一起递出：它是**证据状态**，判决板靠它查表定标题。
        # 丢掉它，前端就只能硬编码标题，而硬编码的标题在默认权重符号翻转后
        # 已经和正文说反了话（正文「未验证」/ 标题「已知的坏配置」）。
        return {
            "weights": loaded["weights"],
            "evidence": loaded["evidence"],
            "execution": loaded["execution"],
            "caveat": loaded.get("caveat"),
            "caveat_kind": loaded.get("caveat_kind"),
            "source": "evolved" if loaded["evidence"] else loaded["source"],
        }

    @app.get("/api/predictions/history")
    def predictions_history():
        from .history import prediction_history

        df = prediction_history(reports_dir)
        if df is None or df.empty:
            return {"rows": [], "dates": []}
        dates = sorted(df["pred_date"].unique(), reverse=True)
        return {"rows": df.to_dict(orient="records"), "dates": list(dates)}

    @app.get("/api/evolution")
    def evolution():
        path = reports_dir / "evolution_log.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/evolution/ledger")
    def evolution_ledger():
        """改进台账：每次自进化**修好了什么、数字从多少变成多少**。

        与 `/api/evolution`（候选赛马战报）是两回事：
        那边记「试过哪些权重」，这边记「改掉了哪些缺陷、代价是多少」。

        `worse` 的条目必须照实显示。2026-10-11 修掉可交易性缺口后，
        涨篮子超额从 +0.1965% 掉到 −0.0067%——那是把虚高修成真实，
        把它筛掉或不显示，台账就变成了自我表扬。
        """
        from .ledger import ledger_summary, load_ledger

        return {"entries": load_ledger(data_root),
                "summary": ledger_summary(data_root)}

    @app.get("/api/evolution/summary")
    def evolution_summary():
        """进化 tab 的**判决板**：赛马跑了多少轮、晋级几次、线上权重有没有被验收过。

        ## 为什么必须单独端出来（2026-10-11）

        候选表里全是绿色（`--down` 绿，A 股红涨绿跌），用户据此问
        「是不是记录了每次自进化改进带来的提升」。

        真相是：赛马候选**全部被否决**、晋级 0 次、
        `weights.json` 根本不存在，线上跑的是从未经赛马验收的默认权重。
        这三件事一件都没画在界面上——最该被看见的，恰好是唯一没被看见的。

        `all_rejected` 严格区分「跑了但全否决」（true）与「还没跑」（false），
        两者的界面话术完全相反，混为一谈就成了「没在进化」。
        """
        path = reports_dir / "evolution_log.json"
        log = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        races = len(log)
        promotions = sum(1 for e in log if (e.get("decision") or {}).get("promoted"))
        best = None
        for e in log:
            for ev in e.get("evaluations") or []:
                v = (ev.get("stats") or {}).get("up_excess_vs_market")
                if v is not None and (best is None or v > best):
                    best = v
        wpath = reports_dir / "weights.json"
        live = json.loads(wpath.read_text(encoding="utf-8")) if wpath.exists() else None
        # 线上真正在跑的那组权重：**无论它来自晋级还是硬编码默认值**，
        # 都得体检。`weights.json` 从未生成过（9 轮赛马 0 次晋级），
        # 此时 `live` 是 None，但 `load_weights` 会回退到默认权重——
        # 那才是每天真的在出单的那组。体检它比体检一个空文件有意义得多。
        running = (live or {}).get("weights")
        execution = (live or {}).get("execution") or {}
        if running is None:
            from .evolve import load_weights
            cfg = load_weights(reports_dir)
            running = cfg.get("weights")
            execution = cfg.get("execution") or {}
        oos = _live_oos(reports_dir, running, execution)
        return {
            "races": races,
            "promotions": promotions,
            "all_rejected": races > 0 and promotions == 0,
            "best_test_excess": best,
            "weights_exist": live is not None,
            # 线上真正在跑的那组权重**有没有被赛马验收过**。
            # 没有 → 它只是一组硬编码默认值，不能被当成「进化成果」。
            "live_validated": bool(live and live.get("source") == "evolved" and live.get("evidence")),
            "live_source": (live or {}).get("source"),
            "live_weights": (live or {}).get("weights"),
            **oos,
            # 现任在**多个独立窗口**上的累计健康度。
            # 六道闸只管「什么能进来」，不管「什么该被换掉」——
            # 2026-10-11 20:09 出现「现任排第 10、没有候选过闸、于是维持现任」
            # 的局面，而现任在 3 段独立窗口（合计约 5 年）上全部显著为负。
            **champion_health(reports_dir, running),
            # 现任权重的**符号**有没有和实测 IC 相反。
            # 「已被证伪」是结果（历史上亏了），这里是原因（方向反了）——
            # 用户问「那到底该怎么改」时，只有结果是答不出来的。
            # 核对不出来时返回 unknown，前端必须**什么都不说**，
            # 打成「符号全部一致」就是一个从未做过的结论。
            "weight_sign_check": weight_sign_conflicts(reports_dir, running or {}),
            # 产品承诺是「10 涨 10 跌」。涨腿那一半由超额闸看着，
            # 跌腿那一半此前既没有闸也没有任何展示——实测现任权重
            # `avg_down_return = +0.126%/日`，它推的「10 跌」平均每天在涨。
            # 半年下来用户每天收 10 只「会跌」的股票，判决板一字未提。
            # unknown 时前端什么都不渲染。
            # ★ 传 `running` 而不是上面那行 `live_weights`：后者在
            # `weights.json` 不存在时是 **None**（9 轮赛马 0 次晋级），
            # 而 `running` 缺失时字面回落同一个 `load_weights()`，
            # 与 `run_daily.py` 出单用的是同一个量（第 7 种形状）。
            "down_leg_check": live_down_leg(reports_dir, (execution or {}).get(
                "rebalance_every", 1), running,
                (execution or {}).get("buffer_multiple")),
        }

    def _live_oos(reports_dir, live_weights, execution=None):
        """从最新一份赛马报告里挖出**线上这组权重**的样本外表现。

        ## 为什么判决板必须报它

        2026-10-11 ivol 泄露修好后重跑发现：线上跑的默认权重在 2 年样本外
        涨腿超额 **−0.5978%/日**、`excess_t = −2.83`、命中率 47.46%。

        而当时判决板只写「0 次晋级 / 未验收」——听起来像「谨慎地维持一个
        还行的策略」。`live_validated=False` 说的是「没验过」，
        **没有说「验过了，而且是负的」**。这两句话对使用者含义天差地别。

        三档判定（t 的 95% 临界取 1.96）：
        `significantly_negative` / `inconclusive` / `significantly_positive`。
        对不上任何一行、或报告损坏 → `unknown`。
        **宁可说不知道，也不许拿别的组的数据代替线上这组。**
        """
        blank = {"live_oos_excess": None, "live_oos_t": None,
                 "live_oos_hit": None, "live_oos_verdict": "unknown"}
        if not live_weights:
            return blank
        try:
            want = candidate_key({"weights": live_weights,
                                  "rebalance_every": (execution or {}).get("rebalance_every", 1),
                                  "buffer_multiple": (execution or {}).get("buffer_multiple")})
            # ★ 走 report_jsons()：排掉边车（见 provenance.report_jsons 的 docstring）
            reports = sorted(report_jsons(reports_dir, "race_*.json"),
                             key=lambda p: p.stat().st_mtime)
            for path in reversed(reports):
                try:
                    rows = json.loads(path.read_text(encoding="utf-8")).get("table") or []
                except (json.JSONDecodeError, OSError, AttributeError):
                    continue
                exact, by_weight = None, None
                for r in rows:
                    # **按三元组匹配，不只按权重**（2026-10-11 22:30，第 20 号缺陷的第三个副本）。
                    # 复合候选池里同一组权重会出现 6 次（K=1/5/21/63 × 有无缓冲），
                    # 只按权重匹配会返回**表里第一个**匹配行——实测就是 K=63 那行，
                    # 于是判决板拿季频的成绩去描述一个日频策略，
                    # 「样本外显著为负 −0.598% t=−2.83」被显示成「无法区分于 0 t=—」。
                    got = candidate_key({"weights": r.get("weights") or {},
                                         "rebalance_every": r.get("rebalance_every", 1),
                                         "buffer_multiple": r.get("buffer_multiple")})
                    if got == want:
                        exact = r
                        break
                    if got[0] == want[0] and by_weight is None:
                        by_weight = (r, got)
                # ⚠️ 权重对得上、**执行参数对不上**（2026-10-15）。
                #
                # 实盘换仓周期改成 `LIVE_REBALANCE_EVERY=21` 之后，赛马表里
                # 所有行仍是 K=1 ⇒ 三元组一个都匹配不上。若就此返回 `unknown`，
                # 就把「现任样本外显著为负 t=−2.83」**又一次结构性地藏起来** ——
                # 那正是 `tests/test_live_health.py` 记录的 2026-10-11 事故本身。
                #
                # ⇒ 报出那一行，但**明说它不是线上这个配置的判决**。
                # 拿 K=1 的成绩冒充 K=21 是撒谎；
                # 什么都不报、让最该被看见的警告消失，同样是撒谎。
                picked = exact or (by_weight[0] if by_weight else None)
                if picked is None:
                    continue
                st = (picked.get("test_stats")
                      or ((picked.get("test") or {}).get("stats")) or {})
                t = st.get("excess_t")
                base = {"live_oos_excess": st.get("up_excess_vs_market"),
                        "live_oos_t": t,
                        "live_oos_hit": st.get("overall_hit_rate")}
                if exact is None:
                    got = candidate_key({"weights": picked.get("weights") or {},
                                         "rebalance_every": picked.get("rebalance_every", 1),
                                         "buffer_multiple": picked.get("buffer_multiple")})
                    return {**base, "live_oos_verdict": "mismatched_execution",
                            "live_oos_note": (
                                f"赛马表里只有 K={got[1]}×缓冲={got[2]} 这一行权重相同，"
                                f"线上跑的是 K={want[1]}×缓冲={want[2]}"
                                " ⇒ 该 t 值**不适用于**线上配置；"
                                "线上这个配置从未被赛马测过")}
                verdict = "inconclusive"
                if t is not None and abs(t) >= 1.96:
                    verdict = "significantly_positive" if t > 0 else "significantly_negative"
                return {**base, "live_oos_verdict": verdict}
        except (TypeError, ValueError):
            return blank
        return blank

    @app.get("/api/logs")
    def logs(level: str | None = None, tail: int = 200):
        from .log import read_logs

        return read_logs(data_root, level=level, tail=tail)

    @app.get("/api/bond-paper")
    def bond_paper():
        """可转债纸面账户（路线 A 第四阶段）——20% 候选的样本外审判台。

        返回摘要 + 净值曲线（供绘图）+ 最新持仓/调仓差异。
        样本不足时 summary.verdict 为 null——前端必须显示「累积中」而不是空白，
        空白会被读成「没跑」。
        """
        from .reports import load_bond_paper, read_bond_paper_summary

        summary = read_bond_paper_summary(reports_dir) or {"status": "empty"}
        rows = load_bond_paper(reports_dir)
        curve, latest = [], {}
        if not rows.empty:
            keep = ["date", "arm", "mode", "phase", "nav", "is_signal_day",
                    "is_buy_day", "is_sell_day", "n_positions", "cost_paid",
                    "holdings", "add", "remove", "n_liquidated"]
            curve = rows[keep].to_dict(orient="records")
            # 🚨 `latest` 是**整行** dict（不是 `keep` 子集）⇒ 它多带一列
            #   `mean_entry_open`，而**卖出行那一列是 NaN**（当天已清仓 ⇒
            #   没有持仓价可平均）。starlette 的 JSONResponse 用
            #   `json.dumps(allow_nan=False)` ⇒ 一个 NaN 就让**整个响应 500**，
            #   前端转债页签直接显示「加载失败」。
            # ⇒ 走本文件**自己的** `_json_safe`（同款 NA→None），不要另写一份。
            latest = _json_safe(rows[rows["arm"] == "cheap_p0"].tail(1))[0]
        return {"summary": summary, "curve": curve, "latest": latest}

    @app.get("/api/d2-verdict")
    def d2_verdict():
        """**判决档**的年化 20% 判决 —— **逐字透传，不重算、不判断**。

        ## 为什么是「逐字」而不是「端点里算」

        判决产物里**已经有**三样现成的东西：
        `verdict_reading`（点估计 c）、`verdict_mde_annual`（带宽半宽 M）、
        `verdict_upper_one_sided_95`（**真的** 95% 单侧上界 U）、
        `verdict_cases_fired`（**真的**触发了哪几条规则）。
        ⇒ 端点里**再算一遍** `c − M` / `c + M` / CI 就是**第二份实现**：
        公式抄错时界面照常渲染、HTTP 200、零告警，
        而它说的是一件与产物不同的事（第 137 层：两份必须同步的东西中间没有锁）。
        ⇒ 端点只负责**把产物自带的值递出去**，前端照着画。

        ## 两条线不许混（本端点只给**判决档**这一族）

        | 端点 | 它给的是 | 口径 |
        |---|---|---|
        | `/api/d2-verdict`（本端点） | **判决档** `market_return + net_excess_bt003` | 1395 天 / 5.717 年 / 宽度 10 / K=21 |
        | `/api/d2-gap` | **5 万本金 + 交付档宽度 10** 的缺口台账 | 另一个量 |

        ⇒ **不是同一个量，绝不可混引、也不可放在同一张图上比较。**
        🚨 本端点**刻意不递** `gap_to_target_by_benchmark`（那是 5 万交付档那一族）
        与 `excess_legs_own_mde`（超额腿那一族）——递了就是邀请前端混用。

        ## 产物不在 ⇒ 404 + 一句话原因

        **不许返回空对象假装「没有数据」**：空对象在界面上与「判决读不出来」
        长得分毫不差，而后者要求人去看采集/重跑（形状 23 的方向）。
        """
        path = reports_dir / "basket_nav_01_2026-10-16.json"
        if not path.exists():
            raise HTTPException(
                404, f"判决产物不存在：{path.name}（跑 scripts/measure_basket_nav_01.py 生成）")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(404, f"判决产物读不出来：{exc!r}") from exc
        if not isinstance(doc, dict):
            raise HTTPException(404, "判决产物的内容不是一个对象（异常）")
        tiers = doc.get("tiers") or []
        if len(tiers) < 2 or not isinstance(tiers[1], dict):
            raise HTTPException(
                404, "判决产物里没有 tiers[1]（判决档）⇒ 拿不到判决档的数")
        t1 = tiers[1]
        # ★ 逐字：一个数都不算、一个口径都不转。
        # `verdict_mde_annual` 一并给出，是为了让前端能标出
        # 「带宽 = 点估计 ± MDE，上界取产物自带的 95% 单侧上界」——
        # 少了它，前端只能自己算一个 CI 出来（第 137 层）。
        return {
            "source": path.name,
            # —— 判决三件套 + 目标 + 规则（逐字）——
            "verdict_reading": doc.get("verdict_reading"),
            "verdict_mde_annual": doc.get("verdict_mde_annual"),
            "verdict_upper_one_sided_95": doc.get("verdict_upper_one_sided_95"),
            "target": doc.get("target"),
            "verdict_rule": doc.get("verdict_rule"),
            "verdict_text": doc.get("verdict_text"),
            "verdict_cases_fired": doc.get("verdict_cases_fired"),
            # —— 判决档自己的数（**另一个量**：算术年化 ≠ 几何年化）——
            "verdict_tier_arith_ann": t1.get("arith_ann"),
            "verdict_tier_cagr": t1.get("cagr"),
            "verdict_tier_label": t1.get("tier"),
            "verdict_tier_n_days": t1.get("n_days"),
            "verdict_tier_years": t1.get("years"),
            # 口径块：随数字一起离开脚本（`verdict_tier_is` / `this_is_not` / …）
            "caliber": doc.get("caliber"),
        }

    @app.get("/api/d2-gap")
    def d2_gap():
        """D2（年化 20%）的缺口台账 —— 首屏「达标」判决的天花板闸。

        ⚠️ 这个端点存在的唯一理由是**封一条活的假绿**：
        `/api/status` 的前瞻单笔底稿只要有 ≥30 个交易日、累计涨幅够，
        前端第 7 支就会说「年化 X%，达到 20% 目标」——而同一屏的台账逐字说
        账户年化天花板 **8.34%**（榜宽 10 · ¥5 万），离 20% 差 **11.66pp**。
        ⇒ 两个数字必须**同时**在屏上，达标支才不是假绿。

        🚨 **返回面只含百分数族**（`read_d2_gap` 里逐字段点名取数）——
        产物 `rows[*].measured_gross_annual` 是**分数**（0.0481）而
        `headline.*` 是**百分数**（4.8108），同一个量差 100 倍（第 182 层）。
        🚨 **缺档时 `status != "ok"` 且数值键为 `None`，绝不回落成 0** ——
        0 会被读成「缺口 0，已达标」，而「没有台账」与「缺口是 0」是相反的结论。
        """
        from .reports import read_d2_gap

        return {"ledger": read_d2_gap(reports_dir)}

    @app.get("/api/goal-progress")
    def goal_progress():
        """**目标达成进度** —— 最新快照 + 历日历史。

        用户 2026-10-21 的原话：「web 页面加一个 tab 页面 记录目标达成页面。
        记录目标达成进度，**不用每次我都问你**。」
        ⇒ 本端点是那个 tab 的**唯一**数据源，页面每天自己长一格。

        ## 返回面

        | 键 | 是什么 |
        |---|---|
        | `latest` | **最新一份**快照的全文（8 条目标逐条：状态 / 数 / 出处 / 判据） |
        | `history` | 历日快照列表（日期 + 生成时间 + 摘要 + 变化行 + 各目标状态） |
        | `gaps` | **历日之间缺的日历日**（cron 漏跑的那几天，明写出来） |
        | `n_snapshots` | 一共几份 |

        ## 🚨 缺档不许回落成空数组当正常

        * **一份快照都没有** ⇒ **404 + 一句原因**
          （空数组在界面上与「读不出来」长得分毫不差 ⇒ 形状 23 的方向）。
        * **某一份读不出来** ⇒ 它**仍然出现在 `history` 里**，
          带 `readable: false` 与错误原文 ⇒ **不许静默跳过**
          （跳过 = 把「没跑到」读成「那天没变化」）。
        * **中间漏了几天** ⇒ 那些天进 `gaps` ⇒ 不许让时间轴看起来连续。

        ## 🚨 端点里一个数都不算

        所有读数**逐字**来自快照产物；本端点只做**搬运 + 排序 + 缺档披露**。
        ⇒ 「还差几倍」取哪一列、两个 D2 口径怎么并排，**全部在快照里已经定好**，
        前端不许自己决定（第 143 层）。
        """
        return _goal_progress_payload(reports_dir)

    @app.get("/api/lot-affordability")
    def lot_affordability():
        """今天这 20 只，**5 万买不买得起**（整手口径）。

        🚨 **这个端点存在的唯一理由**：页面上一屏都是「10 涨 10 跌」，
        用户会以为照着买就行 —— 而实盘那 10 只里**只有 4 只**在每份
        ¥5,000 的预算内（688498.SH 收盘 1010.35，科创板一手 200 股
        ⇒ 1 手 ¥202,070 = 全部本金的 4 倍）。
        ⇒ **买不进不是信号问题，是本钱问题。**

        ## 它**只**回答「买不买得起」

        收益率/胜率是 `/api/status` 与 `/api/d2-verdict` 的职责；
        这里混进来就等于端点开始**重算判决**（第 137 层）。

        ## 价格**不许自己造**

        价格一律来自主库 `master/daily/{code}.parquet`
        （`LocalStore.read_daily`），取 `date <= as_of` 的最后一根 bar 的 `close`；
        **用哪一天的价格、复不复权**写在返回体的 `caliber` 块里。

        ## 一手多少股：**688/689 = 200，其余 = 100**（科创板 200 股起买）
        """
        return lot_affordability_payload(data_root, reports_dir)

    @app.get("/api/holdings-alert")
    def holdings_alert():
        """持仓级降级预警（2026-10-21 接线）。

        🚨 **它不预测涨跌、不算盈亏、不改权重、不改 K、不生成买卖单**：
        只回答**横截面位次**（今天它在全池里排第几、比建仓那天是好了还是差了）。
        ⇒ 页面上的标签必须带着这条边界显示。
        """
        return _holdings_alert_payload(reports_dir)

    @app.get("/api/deployed-config")
    def deployed_config():
        """「判决配置 vs 实盘配置」**三层**差异 —— 逐字摊开。

        | 层 | 它是什么 |
        |---|---|
        | 判决侧 | `basket_nav_01_2026-10-16.json` 量的那套（**满仓判决档**） |
        | 实盘侧 | 生产**今天真的在跑**的那套（敞口/仓数/K/因子数**逐字取生产签名与常量**；仓位标签由 `position_label(敞口)` 推，**不许手打**） |
        | 实盘档读数 | 实盘配置下的 D2 年化读数（`deployed_d2_verdict.json`，逐字透传） |

        🚨 **不许把满仓判决数说成「你的年化」** —— 两边的数**不是同一个量**，
        **不可混引、不可同图比较**（口径台账纪律）。
        🚨 **端点里一个数都不算**：判决读数逐字取产物字段、实盘配置逐字取
        生产签名/常量、实盘档读数逐字取 `deployed_d2_verdict.json`。
        🚨 **实盘档读数不许回落**：产物不在 ⇒ 三个字段全 `null` + 一句
        「产物尚未生成」，**不许**拿 `deployed_account_nav.json` 的推算数顶替。
        """
        return deployed_config_payload(reports_dir)

    static_dir = Path(__file__).parent / "web"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
