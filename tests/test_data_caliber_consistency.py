"""E1 / E2 / E3 守卫 —— 「界面上显示的东西与事实不符，而界面上没有任何报警」。

三条**同族**缺陷，一次修完，三组守卫：

| 节点 | 缺陷 | 症状 |
|---|---|---|
| E1 | 预测标题以**浏览器当天**为基准 | 标题说「10月16日预测」，榜单其实是 10-14 生成、说 10-15 的 |
| E2 | `run-dot` 只按 `status` 分支 | 昨天那次 ok 的 run，今天顶着**绿点**显示「今日已跑」 |
| E3 | `/api/allocation/latest` 的 alloc 顶层没有 `date` | 渲染成「生成于 」，尾巴上什么都没有 |

共同形状：**断言的是一个关于「时间/身份」的陈述，而支撑它的是另一个量**
（当天 vs `pred.date`；`status` vs `s.date`；内容 vs 文件名）。
断言本身永远成立，**不崩、不报警、控制台干净**。

---

## 三条硬要求，逐条对照

① **锚在句法位置，不锚在子串。**
   `index.html` 里有近 80 行中文注释，子串断言会被注释喂饱（本仓第 13 种形状）。
   本文件里**没有一条** `assert 'xxx' in html`：
   所有前端断言都经 `_body()`（花括号配平 + 跳过字符串与注释）抠出函数体，
   E1 更是用正则抓 `$('pred-title').textContent =` 的**右值表达式**再拿 node 跑。

② **`node --check` + 真跑。**
   引用 `test_audit_frontend.py` 的 `_DOM_STUB` / `_prelude` / `_run_js` /
   `_body` / `_fn` / `_code_block`（那套是被变异验证打磨过的）。
   探针里的 JS **从 index.html 抠原文**，不手抄副本 ——
   手抄的那份就是被测的其实是副本（同一文件 314 行的教训）。

③ **变异验证**：每条守卫的 docstring 记了「改坏什么 → 变红」。

---

## ★ 为什么 E1 的守卫不能写成「标题 == 10月15日」

E1 是**时间相关**的缺陷。今天流水线跑完之后 `pred.date == 今天`，
当天基准与 `pred.date` 基准**给出同一个答案**，缺陷自动消失。

⇒ 任何形如「断言标题等于某个月日」的守卫，**在流水线跑完之后会永远绿**，
而缺陷会在下一个「早上打开、当天还没跑」的窗口原样复发。

⇒ 本文件对 E1 用的是**不变性**：固定 `pred.date`，换两个不同的「今天」，
标题必须一模一样。旧代码在两个今天下必然给出不同答案
（10月16日 vs 下一交易日 10月21日）⇒ **守卫在任何日期都有效**。
"""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from aplus.reports import latest_prediction_date, save_allocation, save_predictions
from aplus.webapp import create_app

# 复用同一套被变异验证打磨过的 node 探针工具（不重抄一份，避免两份实现分叉）
from test_audit_frontend import (  # noqa: E402
    _DOM_STUB, _body, _code_block, _fn, _html, _line_of, _node, _prelude, _run_js,
)
from test_chat_wiring import _inline_scripts  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_REPORTS = _ROOT / "data" / "reports"


# ── 通用：把「今天」冻住 ───────────────────────────────────────────────
#
# ⚠️ 冻时钟必须用 `globalThis.Date` 先把真 Date 拿出来再遮蔽，
#    直接 `new Date(...)` 会撞 TDZ（`Cannot access 'Date' before initialization`）——
#    第一版就这么写的，node 报的是这个 ReferenceError，看起来像被测代码坏了。

def _freeze(today_iso: str) -> str:
    """把「无参 `new Date()` / `Date.now()`」钉在 `today_iso` 那天的 09:30。"""
    y, m, d = (int(x) for x in today_iso.split("-"))
    return (
        "const _D = globalThis.Date;\n"
        f"const __NOW = new _D({y}, {m - 1}, {d}, 9, 30, 0);\n"
        "const Date = class extends _D {\n"
        "  constructor(...a) { if (a.length === 0) super(__NOW); else super(...a); }\n"
        "  static now() { return __NOW.getTime(); }\n"
        "};\n"
    )


def _fns(src: str, *headers: str) -> str:
    """把几个顶层函数从 index.html 抠成可直接 eval 的原文（去注释）。"""
    return "\n".join(_code_block(_fn(_body(src, h))) for h in headers)


def _js_literal(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _run_js_in_tz(tmp_path, script: str, tz: str) -> str:
    """同一个探针，指定 `TZ` 跑一遍。

    为什么必须显式给 TZ：JS 的 `new Date('YYYY-MM-DD')` 按 **ISO 规则解析成
    UTC 零点**，而 `getDay()` 读本地时间。于是在 UTC+8（Asia/Shanghai）与
    UTC+13 上它「碰巧」落在正确的日子上，在 UTC 以西（America/Los_Angeles）
    落成前一天。
    ⇒ 一条只在开发机（UTC+8）上跑的守卫**看不见这个洞**。
    本仓就实测过：`const boardDay = new Date(pred.date)` 这个变异
    在 UTC+8 下 29 条守卫全绿。
    """
    p = tmp_path / "probe_tz.cjs"
    p.write_text(_DOM_STUB + script, encoding="utf-8")
    r = subprocess.run([_node(), str(p)], capture_output=True, text=True,
                       env={**os.environ, "TZ": tz})
    assert r.returncode == 0, f"探针在 TZ={tz} 下跑挂了：\n{r.stderr[:900]}"
    return r.stdout


# ══════════════════════════════════════════════════════════════════════
# E1 · 预测标题的基准必须是 `pred.date`，不是浏览器当天
# ══════════════════════════════════════════════════════════════════════

#: 榜单自己的日历日。2026-10-14 是**周三**（实测 `date.weekday() == 2` / `getDay() == 3`），
#: 它的下一个交易日是 **2026-10-15 周四** —— 这是手算得出的外部锚，不问实现。
_PRED_DATE = "2026-10-14"
_EXPECTED_TITLE = "下一交易日 10月15日预测"

#: 两个刻意差得很远的「今天」：缺陷当天，以及一周之后。
_TODAY_A = "2026-10-15"   # 缺陷当天：当天基准 → 10月16日，pred.date 基准 → 10月15日
_TODAY_B = "2026-10-20"   # 一周后：当天基准 → 10月21日，pred.date 基准 → 10月15日

#: 只用来定位行号，不用来断言「这个串出现过」（那是子串断言的老毛病）。
_TITLE_ASSIGN = "$('pred-title')"

_TITLE_RE = re.compile(r"\$\(\s*'pred-title'\s*\)\.textContent\s*=\s*([^;]+);")


def _pred_title_chunk(src: str):
    """抠出 `loadOverview` 里**从声明到赋值**的那一段原文，返回 `(chunk, rhs)`。

    ⚠️ 为什么是「一段」而不是「右值表达式」——
    第一版只抠右值（`boardDay ? nextTradingDayLabel(boardDay) …`），
    然后在探针里**自己**写一行 `const boardDay = parseTradingDay(pred.date);`。
    于是变异验证实测到：`parseTradingDay(pred.date)` 被改成
    `new Date(pred.date)` 时，**31 条守卫全绿** ——
    被测代码里那一步压根没被跑到，探针替它做完了。

    ⇒ 那是「手抄副本」的**镜像**：手抄是副本比实现老，
    这里是自己造的那份比实现**新**。两种都让守卫守着一段没执行的代码。
    ⇒ 所以整条数据流（`pred.date` → 日历日 → 标题）必须由**文件里的原文**执行。
    """
    body = _code_block(_body(src, "async function loadOverview("))
    hits = list(_TITLE_RE.finditer(body))
    assert len(hits) == 1, (
        f"`loadOverview` 里给 `pred-title` 赋值的语句应当恰好 1 条，实得 {len(hits)} 条"
        f"（{[m.group(1) for m in hits]}）—— 0 条=结构变了该守卫失效，"
        "2+ 条=有第二处竞争同一个节点"
    )
    hit = hits[0]
    # 往前吃掉紧邻 `const` 声明：右值里引用的变量就是它声明的
    start = body.rindex("const", 0, hit.start())
    return body[start:hit.end()], hit.group(1).strip()


def _pred_title_rhs(src: str) -> str:
    """`loadOverview` 里给 `#pred-title` 赋值的**那一条语句的右值**。

    锚在**调用形式**上（`$('pred-title').textContent = …`），
    不是 `assert 'pred-title' in html` —— 后者被一句注释就能满足。
    同时断言它**恰好出现一次**：0 次说明结构变了（守卫在空断言上打转），
    2 次说明有两处竞争同一个节点（守卫只钉住了其中一处）。
    """
    return _pred_title_chunk(src)[1]


def _probe_title(tmp_path, src: str, pred_date, today_iso: str, *, tz=None):
    """跑 `loadOverview` 里**从日历日声明到标题赋值**的那一段原文。

    跑完读 DOM 里的 `#pred-title` —— 不是问探针「你算出了什么」，
    而是问「用户屏幕上是什么」。
    """
    chunk, _rhs = _pred_title_chunk(src)
    script = (
        "(async () => {\n"
        + _freeze(today_iso)
        + "const $ = id => global.document.getElementById(id);\n"
        + _fns(src, "function parseTradingDay(", "function localMidnight(",
               "function nextTradingDayLabel(")
        + f"\nconst pred = {{ date: {_js_literal(pred_date)} }};\n"
        + chunk + "\n"
        + "console.log(global.__text('pred-title'));\n"
        "})();\n"
    )
    if tz is None:
        return _run_js(tmp_path, script).strip()
    return _run_js_in_tz(tmp_path, script, tz).strip()


def test_e1_the_pred_title_never_reads_the_browser_clock():
    """★ 句法守卫：标题的右值里不许出现「当天」。

    变异：`nextTradingDayLabel(boardDay)` → `nextTradingDayLabel()` ⇒ 红。
    变异：右值里塞回 `new Date()` ⇒ 红。

    ⚠️ 只看**去注释后**的代码（`_code_block`）：否则我在自己新加的中文注释里
    写一句「不能用 `new Date()`」就能把自己喂成红的 —— 第 13 种形状的**镜像**
    （断言被子串喂饱），方向反过来了也一样是假绿/假红的来源。
    """
    rhs = _pred_title_rhs(_html())
    assert "new Date" not in rhs, (
        f"标题右值 {rhs!r} 里出现 `new Date` —— 基准又回到浏览器当天了，"
        "这正是 E1 缺陷本身"
    )
    assert not re.search(r"nextTradingDayLabel\(\s*\)", rhs), (
        f"标题右值 {rhs!r} 调用了**零参** `nextTradingDayLabel()`，"
        "而它的默认参数就是 `new Date()` ⇒ 基准是打开页面那一天，不是 `pred.date`"
    )
    assert "pred" in rhs or "boardDay" in rhs, (
        f"标题右值 {rhs!r} 里既没有 pred 也没有 boardDay —— "
        "那么它凭什么知道该显示哪一天？"
    )


@pytest.mark.parametrize(
    "today_iso", [_TODAY_A, _TODAY_B],
    ids=["缺陷当天", "一周之后"],
)
def test_e1_the_pred_title_names_the_day_after_the_prediction_date(tmp_path, today_iso):
    """★ 行为守卫：跑 index.html 里**那一整段真实代码**，冻结今天为 `today_iso`。

    执行的是 `loadOverview` 里从 `const boardDay = …` 到
    `$('pred-title').textContent = …` 的**原文**，读回 DOM 里的标题。

    期望值是**手算的外部锚**：2026-10-14 是周三（`date.weekday()==2`），
    它的下一个交易日是 2026-10-15 周四。断言里写死
    `'下一交易日 10月15日预测'`，不去问 `nextTradingDayLabel()` 的实现 ——
    否则守卫守的是「实现与自己一致」，而实现整体错一天时它照样绿。

    变异：`nextTradingDayLabel(boardDay)` → `nextTradingDayLabel()` ⇒ 红。
    变异：`parseTradingDay(pred.date)` → `new Date(pred.date)` ⇒ 红（见时区那条）。
    """
    src = _html()
    out = _probe_title(tmp_path, src, _PRED_DATE, today_iso)
    assert out == _EXPECTED_TITLE, (
        f"第 {_line_of(src, _TITLE_ASSIGN)} 行："
        f"在「今天＝{today_iso}」下，标题算成了 {out!r}，应为 {_EXPECTED_TITLE!r}。"
        "榜单是 2026-10-14 收盘后生成的，说的是**下一个交易日 10月15日**。"
    )


def test_e1_the_pred_title_is_invariant_to_the_day_the_page_is_opened(tmp_path):
    """★ 不变性守卫（E1 的主门禁）：固定 `pred.date`，换「今天」，标题必须不变。

    这是本文件里**唯一一条不依赖今天是哪天**的 E1 守卫，也是唯一在
    「流水线今天已经跑完」的窗口里仍然有效的守卫 ——
    那种窗口下当天基准与 `pred.date` 基准恰好相等，缺陷**隐形**，
    写死月日的守卫会跟着一起睡过去。

    变异：`nextTradingDayLabel(boardDay)` → `nextTradingDayLabel()` ⇒ 两个今天
    分别给出「10月16日」与「下一交易日 10月21日」⇒ 不等 ⇒ 红。
    """
    src = _html()
    a = _probe_title(tmp_path, src, _PRED_DATE, _TODAY_A)
    b = _probe_title(tmp_path, src, _PRED_DATE, _TODAY_B)
    assert a == b, (
        f"同一份榜单（pred.date={_PRED_DATE}），在今天={_TODAY_A} 与 今天={_TODAY_B} "
        f"下标题分别是 {a!r} 与 {b!r} ⇒ 标题**跟着打开页面的那天变**，"
        "而榜单是固定的 ⇒ 它拿错了基准（E1）"
    )
    assert a == _EXPECTED_TITLE, (
        f"不变的那一档是 {a!r}，应为 {_EXPECTED_TITLE!r}"
    )


def test_e1_the_pred_title_does_not_depend_on_the_browser_timezone(tmp_path):
    """★ 时区不变性守卫 —— 这条是被变异验证**逼出来的**，不是想出来的。

    变异验证实测：`const boardDay = parseTradingDay(pred.date)` 改成
    `new Date(pred.date)` 时，**31 条守卫全绿**。差异为零的理由：
    开发机 TZ=Asia/Shanghai(UTC+8)，而 `new Date('2026-10-14')` 解析成
    UTC 零点 = 本地 10-14 08:00 —— **碰巧还是同一个日子、同一个星期几**。

    ⇒ 那是一个**只在开发机上隐形**的洞：换个时区的用户，
    标题会整整错一天（实测 TZ=America/Los_Angeles 下落成 10-13 周二）。

    ⇒ 守卫必须**跨时区**：同一段代码在 UTC+8 与 UTC-7 下必须给出同一个标题。
    「交易日」是**市场属性**，不是浏览器的属性。

    变异：`parseTradingDay(pred.date)` → `new Date(pred.date)` ⇒ 红。
    """
    src = _html()
    east = _probe_title(tmp_path, src, _PRED_DATE, _TODAY_A, tz="Asia/Shanghai")
    west = _probe_title(tmp_path, src, _PRED_DATE, _TODAY_A, tz="America/Los_Angeles")
    assert east == west, (
        f"同一份榜单（pred.date={_PRED_DATE}），UTC+8 下标题是 {east!r}、"
        f"UTC-7 下是 {west!r} ⇒ 标题**跟着浏览器时区变**。"
        "交易日是市场属性；`new Date('YYYY-MM-DD')` 走 UTC 零点，"
        "在 UTC 以西会退回前一天。"
    )
    assert east == _EXPECTED_TITLE, (
        f"UTC+8 下是 {east!r}、UTC-7 下是 {west!r}；两者一致但都不是"
        f"应有的 {_EXPECTED_TITLE!r}"
    )


def test_parse_trading_day_parses_the_string_as_a_local_calendar_day(tmp_path):
    """★ `parseTradingDay` 本身跨时区返回**同一个日子**。

    钉住被测函数的定义，而不是它对 E1 的间接影响 ——
    上面那条若哪天因为别的原因变红，这条会立刻指出根因在解析这一步。
    """
    src = _html()
    script = (
        "(async () => {\n"
        + _fns(src, "function parseTradingDay(")
        + "for (const s of ['2026-10-14', '2026-01-01', '2026-07-04']) {\n"
        + "  const d = parseTradingDay(s);\n"
        + "  console.log(s + ' -> ' + d.getFullYear() + '-' + (d.getMonth()+1)\n"
        + "              + '-' + d.getDate() + ' getDay=' + d.getDay());\n"
        + "}\n"
        + "for (const bad of ['', null, '2026-02-30', '2026-13-01', 'not-a-date']) {\n"
        + "  console.log(JSON.stringify(bad) + ' -> ' + parseTradingDay(bad));\n"
        + "}\n"
        "})();\n"
    )
    east = _run_js_in_tz(tmp_path, script, "Asia/Shanghai").strip()
    west = _run_js_in_tz(tmp_path, script, "America/Los_Angeles").strip()
    assert east == west, f"解析结果随时区变了：\nUTC+8\n{east}\nUTC-7\n{west}"
    good = [l for l in east.split("\n") if "->" in l and "null" not in l]
    # 星期几是手算的外部锚（Python `date.weekday()` 换算成 JS `getDay()`）：
    #   2026-10-14 周二 → 3   2026-01-01 周三 → 4   2026-07-04 周五 → 6
    assert good == [
        "2026-10-14 -> 2026-10-14 getDay=3",
        "2026-01-01 -> 2026-1-1 getDay=4",
        "2026-07-04 -> 2026-7-4 getDay=6",
    ], f"三个正常日期解析结果不对：{good}"
    assert east.count("-> null") == 5, (
        f"5 个坏输入应当全部返回 null，实际输出：\n{east}"
    )


def test_e1_an_unreadable_prediction_date_never_falls_back_to_today(tmp_path):
    """★ 「判不出来」不许渲染成一个**编出来的日子**。

    `pred.date` 缺失时若退回当天基准，页面上就会出现一个自信的、
    却与事实无关的日期 —— 那不是降级，那是把 E1 又演一遍。
    ⇒ 判不出来就说判不出来。

    变异：把三元的 else 分支改回 `nextTradingDayLabel() + '预测'` ⇒ 红。
    """
    src = _html()
    rows = []
    for bad in ("", None, "2026-02-30", "not-a-date"):
        out = _probe_title(tmp_path, src, bad, _TODAY_A)
        rows.append((bad, out))
    assert len(rows) == 4, f"探针没跑满 4 个坏输入（拿到 {len(rows)} 行）—— 守卫会误判"
    for raw, out in rows:
        assert "10月" not in out, (
            f"pred.date={raw!r}（判不出来）时标题渲染成了 {out!r}，"
            f"里面**有具体月日** ⇒ 又编了一个日子出来"
        )
        assert "未知" in out, (
            f"pred.date={raw!r} 时标题是 {out!r}，应当明说「未知」而不是给一个日期"
        )


# ══════════════════════════════════════════════════════════════════════
# E2 · run-dot 必须比较 `s.date` 与今天，不能只看 `status`
# ══════════════════════════════════════════════════════════════════════


def _probe_run_dot(src: str, payload: dict, today_iso: str) -> str:
    """跑 index.html 里**真正的** `loadRunDot`，只换数据源与冻结的今天。"""
    body = _code_block(_fn(_body(src, "async function loadRunDot(")))
    return (
        "(async () => {\n"
        + _freeze(today_iso)
        + _prelude(src)
        + "global.fetch = async () => ({ ok: true, status: 200, json: async () => ("
        + _js_literal(payload) + ") });\n"
        + _fns(src, "function parseTradingDay(", "function localMidnight(")
        + body + "\n"
        + "await loadRunDot();\n"
        + "console.log(JSON.stringify([global.__text('run-text'), store['run-dot'].className]));\n"
        "})();\n"
    )


def _run_dot(tmp_path, payload, today_iso):
    out = _run_js(tmp_path, _probe_run_dot(_html(), payload, today_iso)).strip()
    rows = [r for r in out.split("\n") if r.strip()]
    assert len(rows) == 1, f"探针输出应恰好一行 JSON，实得 {len(rows)} 行：{rows}"
    return json.loads(rows[0])


def _ok_run(date: str) -> dict:
    return {"date": date, "status": "ok", "updated_at": f"{date} 20:00:17",
            "steps": {"predict": "ok", "verify": "ok"}}


@pytest.mark.parametrize("age_days", [1, 2, 6],
                         ids=["昨天", "前天", "六天前"])
def test_e2_a_run_from_an_earlier_day_never_says_it_ran_today(tmp_path, age_days):
    """★ 行为守卫（E2 主门禁）：`ok` + 陈旧日期 ⇒ 不许出现「今日已跑」。

    用的正是今天盘上的真实形状：`data/reports/run_status.json` 现在是
    `date=2026-10-14 / status=ok`，而今天是 2026-10-15 ⇒ 陈旧 1 天。
    旧代码只按 `status` 分支 ⇒ 输出「今日已跑 · 20:00:17」+ **绿点**。

    变异：把 `age === 0` 的判断删掉（回到只按 `status` 分支）⇒ 红。
    """
    src = _html()
    y, m, d = (int(x) for x in _TODAY_A.split("-"))
    stale = (datetime.date(y, m, d) - datetime.timedelta(days=age_days)).isoformat()
    text, cls = _run_dot(tmp_path, _ok_run(stale), _TODAY_A)
    assert "今日已跑" not in text, (
        f"run 的日期是 {stale}（今天 {_TODAY_A} 之前 {age_days} 天），"
        f"界面却显示 {text!r} —— 「今日」是一个关于**时间**的断言，"
        "而它是由 `status`（关于结果）撑着的（E2）"
    )
    assert stale in text, (
        f"陈旧的 run 必须把**真实日期**说出来才有用，实际渲染 {text!r}，"
        f"里面没有 {stale}"
    )
    assert cls != "run-dot ok", (
        f"陈旧的 run 顶着 {cls!r}（绿色）—— 绿灯在说「正常」，"
        "而事实是今天没跑（E2：界面与事实不符且无报警）"
    )


def test_e2_the_run_dot_says_today_only_when_the_run_date_really_is_today(tmp_path):
    """★ 正控臂：**没有**红字乱枪。日期 == 今天时必须照常显示「今日已跑」+ 绿点。

    这条存在的理由是第 87 层「野自助守卫只有零假设臂」：
    上一条若用一个「一律不许显示今日已跑」的粗糙断言来写，它照样是绿的 ——
    而一个把所有输入都判成陈旧的界面同样完全坏掉，却会让守卫通过。
    ⇒ 必须同时钉住「该亮的时候要亮」。
    """
    text, cls = _run_dot(tmp_path, _ok_run(_TODAY_A), _TODAY_A)
    assert "今日已跑" in text, f"日期就是今天（{_TODAY_A}），应当显示「今日已跑」，实得 {text!r}"
    assert cls == "run-dot ok", f"今日已跑应当配绿点，实得 {cls!r}"


def test_e2_a_skipped_run_never_says_it_ran_today(tmp_path):
    """★ 合成对照臂：**同一个日子，没跑成就不能说跑成了**。

    与上一条正控臂严格配对：日期**同样**是「今天」，唯一差别是
    `status="skipped"`（`steps.run_day_gate="WEEKEND"`，pipeline 合法跳过）。
    ⇒ 把两条并排读：「今日已跑」由 `status === 'ok'` **和** `age === 0`
    **两个条件合取**撑起，缺任何一个都不许亮。

    🚨 **这条存在的理由是一次存活的变异**（2026-10-18，预注册 §3）：
    把 `s.status === 'ok' && ` 从第一条判定摘掉，31 条守卫**全绿**。
    逐项取证：变异确实落盘（sha256 变了 + 标记串恰好 1 次 + 旧串消失，
    三项均为真）⇒ 不是空操作；而全部兄弟守卫的 `status` 都是 `"ok"`
    ⇒ **这一格此前零覆盖**。
    ⚠️ 真实数据那条（`…real_run_status_on_disk…`）本该杀掉它，
    但它读盘上载荷，而盘上 `date` 是**昨天** ⇒ 被改的 `age === 0` 分支
    **根本没进** ⇒ 真实数据守卫只覆盖「盘上状态恰好落在哪一格」，
    换一天就换一条臂（预注册 §5 自己写下的时间局部性，原样兑现）。

    ⇒ 按 `guards-only-count-after-mutation`：**没被变异杀掉的守卫一条都不算数**，
    而这条正是把「`status` 那半个合取项」变成**每天都验**而不是「看盘上心情」的地方。
    """
    payload = {
        "date": _TODAY_A,                      # 与正控臂同一个日子
        "status": "skipped",                   # 唯一的差别
        "updated_at": f"{_TODAY_A} 18:57:47",
        "steps": {"run_day_gate": "WEEKEND"},
    }
    text, cls = _run_dot(tmp_path, payload, _TODAY_A)
    assert "今日已跑" not in text, (
        f"运行日期就是今天（{_TODAY_A}）而 status={payload['status']!r}"
        f"（不是 ok，steps={payload['steps']!r}）—— 今天没有出新名单，"
        f"界面却显示 {text!r}。"
        "『跑的是哪天』不等于『跑成了』；不许为此改生产，去看 loadRunDot 的 "
        "`status === 'ok' && age === 0`"
    )
    assert cls != "run-dot ok", (
        f"status={payload['status']!r} 不是 ok，界面却配了绿点 {cls!r} —— "
        "绿灯在说「正常」，而今天没有出单"
    )


@pytest.mark.parametrize("bad_date", [None, "", "not-a-date", "2026-02-30"],
                         ids=["缺失", "空串", "非日期", "日历上不存在的日子"])
def test_e2_an_unreadable_run_date_never_renders_as_today(tmp_path, bad_date):
    """★ 「日期判不出来」不许渲染成「今日」。

    这是 E2 的**最深一层**：把「不知道」渲染成「是」，与把昨天的 run
    说成今天的，是同一种撒谎，只是更隐蔽 —— 因为连 `s.date` 都没有，
    连「陈旧」都谈不上。绿点 + 「今日已跑」在这里纯属捏造。

    变异：`runDay == null` 的分支改成 `age = 0` ⇒ 红。
    """
    payload = _ok_run("2026-10-14")
    if bad_date is None:
        payload.pop("date")
    else:
        payload["date"] = bad_date
    text, cls = _run_dot(tmp_path, payload, _TODAY_A)
    assert "今日已跑" not in text, (
        f"run 的日期是 {bad_date!r}（判不出来），界面却显示 {text!r} —— "
        "「不知道」被渲染成了「是」"
    )
    assert cls != "run-dot ok", f"日期判不出来时不该给绿点，实得 {cls!r}"


def test_e2_a_future_run_date_is_not_treated_as_today(tmp_path):
    """★ 反向对照：`s.date` 晚于今天（时钟漂移 / 文件被写坏）⇒ 同样不许说「今日」。

    没有这条，上一个守卫可以靠「凡是日期不等于今天就报陈旧」蒙混过关 ——
    未来的日期会让 `age < 0`，一个只写了 `age > 0` 的实现会把它
    悄悄归到别的分支上，而那条分支在页面上同样可能是绿的。
    """
    text, cls = _run_dot(tmp_path, _ok_run("2026-10-18"), _TODAY_A)
    assert "今日已跑" not in text, f"运行日期在未来（2026-10-18 > 今天 {_TODAY_A}）却显示 {text!r}"
    assert cls != "run-dot ok", f"未来日期不该配绿点，实得 {cls!r}"


def test_e2_never_run_still_says_never_run(tmp_path):
    """★ 正控臂：`never_run` 分支没被我改坏。"""
    text, cls = _run_dot(tmp_path, {"status": "never_run"}, _TODAY_A)
    assert "尚未运行" in text, f"实得 {text!r}"
    assert cls == "run-dot bad", f"实得 {cls!r}"


def test_e2_the_real_run_status_on_disk_agrees_with_what_the_dot_would_render(tmp_path):
    """★ 真实数据守卫：盘上那份 `run_status.json` 与渲染结果必须自洽。

    判据是**两条不变式**而不是某个具体日期，各自可空：
    ① 「若 `s.date` ≠ 今天，则界面上不许出现『今日已跑』」；
    ② 「若 `s.status ≠ 'ok'`，则界面上不许出现『今日已跑』」。

    🚨 ② 曾经被**反向**断言（`date == today ⇒ 必出现「今日已跑」），
    而那个方向在**每个周末必红**：pipeline 合法跳过时
    （`status="skipped"` / `steps.run_day_gate="WEEKEND"`）
    `date` 就是今天而 `status` 必然不是 `ok`。
    实测那次红：`AssertionError: 盘上 date=2026-10-17 就是今天，
    应当显示「今日已跑」，实得 '部分完成 · 2026-10-17 · run_day_gate'`。

    ⇒ **`loadRunDot` 的真实条件是 `status==='ok' && age===0`**，
    `date == 今天` **不是**充分条件；把两者当同一个量 ⇒ 要求生产说谎。
    预注册与逐条依据：`docs/PREREG_RUN_DOT_E2_2026-10-18.md`（**补记，非事前登记**）。

    ⚠️ 失败文案**不许**写「应当显示『今日已跑』」——
    那会把下一个人推向去改本来正确的生产代码（第 112/140 层）。

    这是唯一一条**明天也仍然有效**的 E2 断言（写死「今天是 2026-10-15」
    的断言在明天会变成一个恒真的陪衬）。
    """
    path = _REPORTS / "run_status.json"
    if not path.exists():
        pytest.skip("盘上没有 run_status.json（无真实数据可核）")
    payload = json.loads(path.read_text(encoding="utf-8"))
    today = datetime.date.today().isoformat()
    text, cls = _run_dot(tmp_path, payload, today)

    # ① 陈旧方向：`s.date` 不是今天，就不许说「今日」
    if payload.get("date") != today:
        assert "今日已跑" not in text, (
            f"盘上 run_status.json 的 date={payload.get('date')!r} 而今天是 {today}，"
            f"界面却显示 {text!r} —— E2 在真实数据上复现了"
        )

    # ② 没跑成方向：`status` 不是 ok（周末/节假日跳过、失败、部分完成），
    #    当天**没有新名单**，「今日已跑」+ 绿点就是撒谎。
    if payload.get("status") != "ok":
        assert "今日已跑" not in text, (
            f"盘上 run_status.json 的 status={payload.get('status')!r}（不是 ok）、"
            f"steps={payload.get('steps')!r}，界面却显示「今日已跑」"
            f"（实得 {text!r}）—— 『跑的是哪天』不等于『跑成了』，"
            "跳过的日子 date 可以等于今天；不许为此改生产，去看 loadRunDot 的 "
            "`status === 'ok' && age === 0`"
        )
        assert cls != "run-dot ok", (
            f"status={payload.get('status')!r} 不是 ok，界面却配了绿点 {cls!r} —— "
            "绿灯在说「正常」，而今天没有出单"
        )


def test_e2_load_run_dot_actually_reads_the_date_field():
    """★ 句法守卫：`loadRunDot` 的函数体里 `s.date` 必须**被读过**。

    这是 E2 缺陷的**字面形状**：旧版函数体里 `s.date` 一次都没出现。
    锚在函数体内（不是全文子串），且要求它是**被读取**的形式。
    """
    body = _code_block(_fn(_body(_html(), "async function loadRunDot(")))
    assert re.search(r"\bs\.date\b", body), (
        f"第 {_line_of(_html(), 'async function loadRunDot(')} 行起："
        "`loadRunDot` 的函数体里根本没有读过 `s.date` —— "
        "只按 `status` 分支就注定了看不到陈旧（E2）"
    )


# ══════════════════════════════════════════════════════════════════════
# E3 · alloc 的日期必须由后端补，且真的到了 DOM
# ══════════════════════════════════════════════════════════════════════

#: 与生产同形状：**payload 里没有 date**。日期只在文件名里
#: （实测 `alloc_2026-10-14.json` 顶层键 = capital / cash_weight / positions /
#: rebalance / risk_notes，逐层扫过，**没有任何**日期字段）。
_ALLOC_DATE = "2026-10-09"
#: 端到端 JS 探针的 preamble（**唯一一份**，各处测试共用 —— 不许各处重写）。
#: ⚠️ 用**三引号原始字符串**：这个 JS 里同时有 `'` 与 `"`，
#:    放进普通 Python 字符串就得层层转义 ⇒ 转义一乱就是 SyntaxError，
#:    而**红在一个语法错误上**而不是语义错误上（第 98 层的教训）。
_JS_PRE = """const $ = id => global.document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
"""

_ALLOC_PAYLOAD = {
    "capital": 50000,
    "positions": [{"code": "600641.SH", "name": "先导基电", "weight": 0.1,
                   "amount": 5000.0, "score": 2.35}],
    "cash_weight": 0.5,
    "rebalance": [{"code": "600641.SH", "action": "add"}],
    "risk_notes": ["单票止损 -8%"],
}


@pytest.fixture
def alloc_client(tmp_path):
    reports = tmp_path / "reports"
    up = pd.DataFrame({"code": ["600641.SH"], "name": ["先导基电"], "direction": ["up"],
                       "score": [2.35], "mom20": [0.42], "rev5": [0.0],
                       "vol20": [0.2], "vsur": [1.0]})
    down = pd.DataFrame({"code": ["600076.SH"], "name": ["康欣新材"], "direction": ["down"],
                         "score": [-0.91], "mom20": [-0.31], "rev5": [0.0],
                         "vol20": [0.2], "vsur": [1.0]})
    save_predictions(reports, _ALLOC_DATE, up, down)
    save_allocation(reports, _ALLOC_DATE, json.loads(json.dumps(_ALLOC_PAYLOAD)))
    return TestClient(create_app(tmp_path))


def test_e3_allocation_latest_carries_the_date_of_the_file_it_came_from(alloc_client):
    """★ 后端契约守卫：`/api/allocation/latest` 必须带 `date`，且它等于文件名日期。

    锚在**接口返回体**上，不是源码子串 —— 这里问的是「用户真的能拿到吗」。

    变异：把 `webapp.py` 里的 `{**alloc, "date": lp}` 改回 `return alloc` ⇒ 红。
    """
    r = alloc_client.get("/api/allocation/latest")
    assert r.status_code == 200, f"接口没通：HTTP {r.status_code}"
    body = r.json()
    assert "date" in body, (
        f"返回体顶层没有 `date`，实得键 {sorted(body)} —— "
        "前端 `#alloc-date` 因此渲染成「生成于 」（E3）"
    )
    # 它不是随便填的：必须等于**这个文件自己的**日期分量
    assert body["date"] == _ALLOC_DATE, (
        f"date={body['date']!r}，但这份 alloc 是按 {_ALLOC_DATE} 落盘的 —— "
        "补的日期必须来自文件名，不能是别的东西"
    )
    # 其余字段不许因为加了 date 而丢
    for k in _ALLOC_PAYLOAD:
        assert k in body, f"补 date 之后丢了原有的键 {k!r}"


def test_e3_the_stamped_date_equals_the_filename_component(alloc_client, tmp_path):
    """★ 口径守卫：补出来的 `date` == 文件名 `alloc_<date>.json` 的那个分量。

    这条钉的是「补的日期**不是编的**」：它是 `_alloc_path(reports_dir, lp)`
    刚刚用的那个文件名日期，与内容**在构造上不可能分叉**。
    """
    body = alloc_client.get("/api/allocation/latest").json()
    lp = latest_prediction_date(tmp_path / "reports")
    path = tmp_path / "reports" / "allocation" / f"alloc_{lp}.json"
    assert path.is_file(), f"找不到 {path} —— 守卫的前提不成立"
    assert path.stem == f"alloc_{lp}", f"文件名与 latest_prediction_date 不符：{path.name}"
    assert body["date"] == lp, f"接口 date={body['date']!r} != 文件名日期 {lp!r}"


def test_e3_the_alloc_panel_renders_a_non_empty_generation_date(alloc_client, tmp_path):
    """★ 端到端口径守卫：**同一串字节**从后端走到 DOM。

    把接口的返回体原样喂给 index.html 里真正的 `renderAlloc`，
    断言 `#alloc-date` 里确实出现了那个日期。

    这条堵的是「口径必须跟着数字离开脚本」：只测后端字段存在，
    测不到「前端拿的是不是同一个字段」；只测前端渲染，
    测不到后端到底给了什么。两端各测一半时，**改错一端仍然全绿**。

    变异：前端把 `a.date` 换成 `a.rebalance` ⇒ 红。
    """
    payload = alloc_client.get("/api/allocation/latest").json()
    src = _html()
    body = _code_block(_fn(_body(src, "function renderAlloc(")))
    script = (
        "(async () => {\n"
        + _JS_PRE
        + body + "\n"
        + "renderAlloc(" + _js_literal(payload) + ");\n"
        + "console.log(global.__text('alloc-date'));\n"
        "})();\n"
    )
    out = _run_js(tmp_path, script).strip()
    assert out == f"生成于 {_ALLOC_DATE}", (
        f"#alloc-date 渲染成 {out!r}，应为 {f'生成于 {_ALLOC_DATE}'!r}"
    )


def test_e3_a_missing_date_renders_unknown_not_a_bare_label(tmp_path):
    """★ 「判不出来」不许渲染成「生成于 」（一个尾巴上什么都没有的标签）。

    旧写法是 `` `生成于 ${a.date ?? ''}` `` —— 空串渲染出来，
    而那与「这一栏还没加载」在页面上**长得一模一样**，且不报错、不留痕。

    变异：`a.date ? … : '生成日期未知'` 的 else 分支删掉 ⇒ 红。
    """
    src = _html()
    body = _code_block(_fn(_body(src, "function renderAlloc(")))
    payload = dict(_ALLOC_PAYLOAD)          # 故意**不给** date
    script = (
        "(async () => {\n"
        + _JS_PRE
        + body + "\n"
        + "renderAlloc(" + _js_literal(payload) + ");\n"
        + "console.log(global.__text('alloc-date'));\n"
        "})();\n"
    )
    out = _run_js(tmp_path, script).strip()
    assert out.strip() != "生成于", (
        f"#alloc-date 渲染成 {out!r} —— 一个不报错、不留痕的空值（E3）"
    )
    assert "未知" in out, f"缺 date 时应明说未知，实得 {out!r}"


def test_e3_a_non_rebalance_day_says_do_not_touch_anything(tmp_path):
    """🚨 非调仓日必须**明说「今天不动」**。

    实盘每 K 个交易日（现 K=63）才换一次仓 ⇒ **非调仓日 `rebalance` 是空的**。
    ⇒ 若让它落进通用分支，页面会渲染成「卖出 0 · 买入 0 · 保留 0」，
    那会被读成「**没事要做**」；而真相是「**今天不该动**」
    —— 这两件事的**行动含义完全相反**（前者是「照做」，后者是「别动」）。

    ⇒ 这条守的是**分支**，不是文案：把 `a.is_rebalance_day === false`
    那个分支删掉 ⇒ 红。
    """
    src = _html()
    body = _code_block(_fn(_body(src, "function renderAlloc(")))
    payload = dict(_ALLOC_PAYLOAD)
    payload.update({
        "is_rebalance_day": False,
        "held_since": "2026-10-15",
        "days_since_rebalance": 3,
        "rebalance_every": 63,
        "rebalance": [],            # 非调仓日**没有订单**
    })
    script = (
        "(async () => {\n"
        + _JS_PRE
        + body + "\n"
        + "renderAlloc(" + _js_literal(payload) + ");\n"
        # ⚠️ 用 `__html`（读 innerHTML）而不是 `__text`（读 textContent）——
        #    `renderAlloc` 写的是 `$('alloc-body').innerHTML` ⇒ `__text` 读到的
        #    永远是空串 ⇒ 断言会**红在一个「探针读错地方」上**，
        #    而不是一个语义错误上（第 98 层的教训：红的原因必须是我要验的那件事）。
        + "console.log(global.__html('alloc-body'));\n"
        "})();\n"
    )
    out = _run_js(tmp_path, script).strip()
    assert "不是调仓日" in out, (
        f"非调仓日没有明说「今天不动」，实得 {out[:200]!r}")
    assert "卖出 0" not in out, (
        "非调仓日落进了通用分支（渲染成「卖出 0 · 买入 0 · 保留 0」）"
        " ⇒ 会被读成「没事要做」，而真相是「今天不该动」")
    assert "2026-10-15" in out, "非调仓日必须说清持仓沿用哪一份名单"


def test_e3_render_alloc_reads_the_date_field_of_the_payload():
    """★ 句法守卫：`renderAlloc` 渲染的必须是 `a.date`，不是别的字段。

    锚在 `alloc-date` 的**赋值语句**上，不是全文子串。
    """
    body = _code_block(_fn(_body(_html(), "function renderAlloc(")))
    hits = re.findall(r"\$\(\s*'alloc-date'\s*\)\.textContent\s*=\s*([^;]+);", body)
    assert len(hits) == 1, f"`alloc-date` 赋值语句应恰好 1 条，实得 {len(hits)}：{hits}"
    assert "a.date" in hits[0] or ".date" in hits[0], (
        f"`alloc-date` 的右值是 {hits[0]!r}，里面没有 date —— 渲染的是别的东西（E3）"
    )


# ══════════════════════════════════════════════════════════════════════
# 门禁自身的前置检查：抽取器不许在空断言上打转
# ══════════════════════════════════════════════════════════════════════


def test_every_inline_script_block_still_parses():
    """★ `node --check` 逐块解析，**不允许 skip**。

    同名 `const` 是**解析期**错误：浏览器丢弃整块脚本、页面永远停在「加载…」，
    而后端接口全 200、其余 pytest 全绿、控制台**一条错误都没有**。
    （本仓第 0.1 条纪律。本文件新增了 `parseTradingDay` / `localMidnight`
    两个顶层声明，正是最容易撞上这个坑的地方。）

    与 pytest 正交：`node --check` 问「这段是不是合法的程序」，
    pytest 问「这个函数的行为对不对」。前者红、后者绿，是完全可能且危险的状态。
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("没有 node，跳过 JS 解析检查")

    blocks = _inline_scripts(_html())
    assert blocks, "一个内联脚本块都没抽到 —— 提取逻辑坏了，这条守卫在空断言上打转"
    for i, code in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                         encoding="utf-8") as f:
            f.write(code)
            path = f.name
        try:
            r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        finally:
            import os
            os.unlink(path)
        assert r.returncode == 0, (
            f"第 {i} 个内联脚本块解析失败 ⇒ 浏览器会丢弃整块，页面静默停在加载态：\n"
            f"{r.stderr.strip()[:800]}"
        )


@pytest.mark.parametrize("header", [
    "function parseTradingDay(",
    "function localMidnight(",
    "function nextTradingDayLabel(",
    "async function loadRunDot(",
    "function renderAlloc(",
    "async function loadOverview(",
])
def test_every_header_these_guards_depend_on_really_exists(header):
    """★ 防呆：`_body` 对**不存在的** header 必须抛异常。

    否则「守卫在空断言上打转」——把 `function parseTradingDay(` 整个删掉，
    `_body` 抛 `ValueError`，测试**报错**（红）而不是**通过**，
    这一点靠下面这条钉住。反过来，若哪天 `_body` 被改成返回空串，
    上面每一条依赖它的守卫都会静默通过 ⇒ 这条必须先绿。
    """
    with pytest.raises((ValueError, AssertionError)):
        _body(_html(), "function thisHeaderDoesNotExist(")
    body = _body(_html(), header)
    assert body.strip(), f"{header} 抠出来是空的 —— 守卫会在空断言上打转"
