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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 现任健康度的判定逻辑住在 `aplus.champion_health` 而不是这里——
# `scripts/run_daily.py` 要用同一份，不能各写一套（2026-10-11 21:35 抽出）。
from .champion_health import champion_health, live_down_leg
from .evolve import candidate_key
from .factor_ic import weight_sign_conflicts
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
        parts.append(
            f"累计 {len(hist)} 条判定：总命中率 {stats['overall_hit_rate']:.1%}，"
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


def create_app(data_root, llm=None) -> FastAPI:
    data_root = Path(data_root)
    reports_dir = data_root / "reports"
    db_path = data_root / "chat.db"

    if llm is None:
        from .llm import default_llm_chain

        llm = default_llm_chain()  # glm 代理（主）→ MiniMax（备）自动降级

    app = FastAPI(title="aplus 量化平台")
    app.state.llm = llm

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
        }

    @app.get("/api/predictions/latest")
    def predictions_latest():
        lp = latest_prediction_date(reports_dir)
        if lp is None:
            raise HTTPException(404, "尚无预测记录")
        up, down = load_predictions(reports_dir, lp)
        return {"date": lp, "up": _json_safe(up), "down": _json_safe(down)}

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
        return alloc

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
            "down_leg_check": live_down_leg(reports_dir, (execution or {}).get(
                "rebalance_every", 1)),
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
            reports = sorted(reports_dir.glob("race_*.json"), key=lambda p: p.stat().st_mtime)
            for path in reversed(reports):
                try:
                    rows = json.loads(path.read_text(encoding="utf-8")).get("table") or []
                except (json.JSONDecodeError, OSError, AttributeError):
                    continue
                for r in rows:
                    # **按三元组匹配，不��按权重**（2026-10-11 22:30，第 20 号缺陷的第三个副本）。
                    # 复合候选池里同一组权重会出现 6 次（K=1/5/21/63 × 有无缓冲），
                    # 只按权重匹配会返回**表里第一个**匹配行——实测就是 K=63 那行，
                    # 于是判决板拿季频的成绩去描述一个日频策略，
                    # 「样本外显著为负 −0.598% t=−2.83」被显示成「无法区分于 0 t=—」。
                    got = candidate_key({"weights": r.get("weights") or {},
                                         "rebalance_every": r.get("rebalance_every", 1),
                                         "buffer_multiple": r.get("buffer_multiple")})
                    if got != want:
                        continue
                    st = (r.get("test_stats")
                          or ((r.get("test") or {}).get("stats"))
                          or {})
                    t = st.get("excess_t")
                    verdict = "inconclusive"
                    if t is not None and abs(t) >= 1.96:
                        verdict = "significantly_positive" if t > 0 else "significantly_negative"
                    return {"live_oos_excess": st.get("up_excess_vs_market"),
                            "live_oos_t": t,
                            "live_oos_hit": st.get("overall_hit_rate"),
                            "live_oos_verdict": verdict}
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

    static_dir = Path(__file__).parent / "web"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
