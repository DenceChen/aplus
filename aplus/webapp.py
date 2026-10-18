"""FastAPI Web 应用：仪表盘 API + 带记忆对话。

- 数据只读自 reports 目录（run_daily 的产物），Web 层不写数据
- 对话存储在 SQLite（data_root/chat.db），LLM 客户端可注入（测试用假客户端）
- 生产 LLM 走本机 glm-failover 代理（aplus.llm.GlmProxyClient）
"""
import json
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
from .evolve import candidate_key, live_execution_status
from .factor_ic import weight_sign_conflicts
from .live_rebalance import load_state
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
    """
    out = dict(rolling_stats(history))
    out.update(compounded_stats(history))
    return out


def _live_holdings_payload(reports_dir) -> dict:
    """**你现在持有的那 10 只** + 「还有几个交易日才换仓」+ 执行口径是不是声明的那个。

    ## 为什么必须有它（2026-10-17 实测缺口）

    `/api/status` 原本只递 5 个键，`holdings.json`（实盘成交流的唯一真值）
    与 `live_execution_status`（K 与缓冲区**是否真的被执行**的那道闸）
    **全仓零个消费点**。于是用户每天打开平台看到的是一张新的 10 涨 10 跌，
    而实盘是 `every=21`（`holdings.json` 实读）、**每 21 个交易日才换一次仓**。

    ⇒ 照着每天的新名单全买，年摩擦 **80.81%**（`delivery_caliber_friction::at_min`）；
       照 K=21 换一次是 **6.41%**（同产物 `friction_by_holding_period.K=21`）。
       **差 74.4pp/year，且与信号好坏无关。**
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
    """
    state = load_state(reports_dir)
    status = live_execution_status(reports_dir)
    out = {
        # `None` = 状态读不出来（或还没建仓）。**与空仓严格不同。**
        "state": state,
        "as_of": (state or {}).get("as_of"),
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
    stats = _stats_with_compounded(load_verification_history(reports_dir))
    hist = load_verification_history(reports_dir)
    if len(hist):
        # ★ 样本量必须同时给出「条目数」与「独立成交窗口数」。
        #   周末/节假日出单会让多个信号日落进同一个窗口
        #   （实测 3 个信号日 → 1 笔交易 = 80 条判定只有 2 次独立观测）。
        #   只报「80 条」会把一次交易数三遍，读起来像样本量很大。
        win = stats.get("n_independent_windows")
        win_txt = f"，其中 {win} 次独立成交" if win else "（独立成交次数未知）"
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
            "stats": _stats_with_compounded(load_verification_history(reports_dir)),
            "paper_mode": True,
            # 2026-10-17 接上。此前 `holdings.json` 与 `live_execution_status`
            # **零消费点**：用户看到每天 10 涨 10 跌，却看不到「实盘每 21 个
            # 交易日换一次仓」。差 74.4pp/年摩擦，且与信号好坏无关。
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
        return {**alloc, "date": alloc.get("date") or lp}

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
            latest = rows[rows["arm"] == "cheap_p0"].iloc[-1].to_dict()
        return {"summary": summary, "curve": curve, "latest": latest}

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

    static_dir = Path(__file__).parent / "web"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
