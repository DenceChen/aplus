"""基本面族 F1–F5 的**因子定义**与**逐日对齐**（轮次 B 用）。

预注册：`docs/PREREG_FUNDAMENTAL_FAMILY_2026-10-20.md` §2.1 / §3.1 / §3.3
       + `docs/PREREG_FUNDAMENTAL_FAMILY_ROUNDB_2026-10-21.md` §3

## 这个模块只做一件事：把「某只票在某一天的值是多少」变成一个**可复算的数**

**五个因子，一个字不改**（2026-10-20 预注册 §2.1）：

| ID | 因子 | 定义 | 方向 |
|---|---|---|---|
| F1 | `bm` | `log(BPS / close)` | 越大越便宜 |
| F2 | `ep` | `log(EPSJB / close)`（年报 EPS，**不是 TTM**） | 越大越便宜 |
| F3 | `roe` | `ROEJQ`（季报加权 ROE，**不是 ROE(TTM)**） | 越大越好 |
| F4 | `size` | `-log(close × a_shares)` | 越小越好 |
| F5 | `q` | `MGJYXJJE / BPS`（每股经营现金流 ÷ 每股净资产，**代理变量**） | 越大越好 |

## 🚨 为什么这个模块**不**改 `aplus/`

委托方明令「不许改 `aplus/`」。而 `aplus/factor_fundamental.py::annual_from_frame`
**只留 BPS**（它的 `_KEPT_FIN_COLS`），拿不到 `EPSJB / ROEJQ / MGJYXJJE`
⇒ F2/F3/F5 的分子**不在生产模块里**。

⇒ 本模块的做法是：**用 `aplus` 的函数决定「哪一份年报在那天可见」**，
再把同一帧里的另外三个字段**按同一键**取回来（A3 产物的同一手法），
最后**逐位对拍** `aplus` 那个函数的输出（共享列全等 ⇒ 口径没漂）。

## 🚨 为什么不用 `financial.visible_as_of`

它按 `REPORT_DATE` **全局**去重、不带股票键 ⇒ 5,460 只的年报经它只剩 **14 行**
（2026-10-20 预注册 §3.2）。本模块自己按 (code, report_date) 去重。

## 🚨 `a_shares` 的口径

`volume × 100 / turn`（预注册 §3.3；**不用** `float_shares`）。
停牌日（无成交量/无换手率）**沿用该票最后一根有效 bar**，不填 0、不填 NaN
（`aplus/capweight.py` 模块 docstring 的同一条规矩）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

#: 预注册 §2.1 的五个冻结因子。**顺序即 §2.1 的顺序**，一个不许加一个不许减。
FACTORS = ("bm", "ep", "roe", "size", "q")

#: 2026-10-20 预注册 §2.5/§5.2 的族 A（F4 size 是**对照因子**，不进族 A）。
FAMILY_A_PREREG = ("bm", "ep", "roe", "q")

#: **长腿方向**：`+1` = 取因子值最大的那一端，`-1` = 取最小的那一端。
#: 依据 §2.1 的方向列：bm/ep「越大越便宜」⇒ 买大的一端；
#: roe/q「越大越好」⇒ 买大的一端；size「越小越好」⇒ 买小的一端。
LONG_SIGN = {"bm": +1, "ep": +1, "roe": +1, "size": -1, "q": +1}

#: 因子值的字符方向说明（进产物，防止读者自己猜）。
LONG_LEGEND = {
    "bm": "越大越便宜 ⇒ 长腿取**最大**的一端",
    "ep": "越大越便宜 ⇒ 长腿取**最大**的一端",
    "roe": "越大越好 ⇒ 长腿取**最大**的一端",
    "size": "越小越好 ⇒ 长腿取**最小**的一端（§2.5：对照因子）",
    "q": "越大越好 ⇒ 长腿取**最大**的一端（代理变量）",
}

#: 可用样本少于这个数就不报 IC（沿用 `scripts/fundamental_ic.py` 的口径）。
MIN_CROSS_SECTION = 60

#: 披露日至少要晚于报告期末这么多天（`scripts/fundamental_factor.py::MIN_LAG_DAYS`）。
MIN_LAG_DAYS = 30

#: 早于这一天的披露日一律丢弃（实测有 `1900-01-01` 的坏值）。
MIN_NOTICE = "1980-01-01"

#: 年报口径（`REPORT_TYPE`）。
ANNUAL_REPORT_TYPE = "年报"


def _annual_gates(a: pd.DataFrame) -> pd.DataFrame:
    """年报口径 + 完整性闸（**逐条照抄** `scripts/fundamental_factor.py::load_annual`）。

    闸：REPORT_TYPE == 年报；披露日可解析且 ≥ 1980-01-01；
    披露日 − 报告期末 ≥ 30 天（剔 `NOTICE_DATE = 1900-01-01` 之类脏值）。
    """
    a = a.loc[a["REPORT_TYPE"].astype(str).str.strip() == ANNUAL_REPORT_TYPE].copy()
    a["code"] = a["SECURITY_CODE"].astype(str).str.zfill(6)
    a["notice_date"] = pd.to_datetime(a["NOTICE_DATE"], errors="coerce")
    a["report_date"] = pd.to_datetime(a["REPORT_DATE"], errors="coerce")
    bad = a["notice_date"].isna() | (a["notice_date"] < MIN_NOTICE)
    bad |= (a["notice_date"] - a["report_date"]) < pd.Timedelta(days=MIN_LAG_DAYS)
    a = a.loc[~bad].copy()
    return a


def annual_series_from_cache(cache) -> pd.DataFrame:
    """臂 R 的 500 只缓存 ⇒ 逐票年报**序列**（每只票所有历史年报）。

    与 `scripts/fundamental_factor.py::load_annual` **逐条一致**，
    区别只在于**多留三列**（`EPSJB` / `ROEJQ` / `MGJYXJJE`）与
    **不去重到每票最新**（历史对齐需要整条序列）。

    🚨 去重口径必须与 `load_annual` 一致：按 `notice_date` 排序后
    对 (code, report_date) 取最后一条 ⇒ 两边留下的**是同一行**。
    """
    raw = pd.read_parquet(cache)
    a = _annual_gates(raw)
    a["bps"] = pd.to_numeric(a["BPS"], errors="coerce")
    a["eps"] = pd.to_numeric(a["EPSJB"], errors="coerce")
    a["roe"] = pd.to_numeric(a["ROEJQ"], errors="coerce")
    a["cfps"] = pd.to_numeric(a["MGJYXJJE"], errors="coerce")
    a["report_year"] = a["report_date"].dt.year
    out = a[["code", "notice_date", "report_date", "report_year",
             "bps", "eps", "roe", "cfps"]]
    # 🚨 排序键必须是 `(code, notice_date, report_date)` —— 与
    # `aplus/factor_fundamental.py::annual_from_frame` **逐字一致**：
    # 同一只票、同一披露日有多行时，aplus 取**报告期最大**的那一行
    # （`sort_values(["code","notice_date","report_date"])` 后 keep="last"）。
    # 只按 notice_date 排 ⇒ 并列的取舍变成「文件原序」⇒ 与 aplus 不一致
    # （实测：报告期与 bps 两列同时对不上）。
    out = (out.sort_values(["code", "notice_date", "report_date"])
              .drop_duplicates(subset=["code", "report_date"], keep="last")
              .reset_index(drop=True))
    return out


def annual_series_from_pit(frame: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """PIT 帧 ⇒ 逐票年报**序列**（口径与 `aplus` 的 `annual_from_frame` 逐条一致）。

    `aplus/factor_fundamental.py::annual_from_frame` 只留 BPS 且**每票只留最新一份**
    ⇒ 历史对齐需要整条序列 ⇒ 本函数保留全部年报，
    但**可见性判据与去重键完全照抄**它。

    🚨 对拍（`scripts/measure_fundamental_mechanism_armR.py` 里执行）：
    本函数按 (code, notice_date) 取每票最新 ⇒ 必须与
    `aplus.factor_fundamental.annual_from_frame(frame, as_of)` **逐位相等**。
    """
    from aplus.factor_fundamental import annual_from_frame, bare_code

    ref = annual_from_frame(frame, as_of)          # aplus 的基准（每票最新一份）
    a = _annual_gates(frame)
    a["bps"] = pd.to_numeric(a["BPS"], errors="coerce")
    a["eps"] = pd.to_numeric(a["EPSJB"], errors="coerce")
    a["roe"] = pd.to_numeric(a["ROEJQ"], errors="coerce")
    a["cfps"] = pd.to_numeric(a["MGJYXJJE"], errors="coerce")
    a["report_year"] = a["report_date"].dt.year
    out = a[["code", "notice_date", "report_date", "report_year",
             "bps", "eps", "roe", "cfps"]]
    out = (out.sort_values(["code", "notice_date", "report_date"])
              .drop_duplicates(subset=["code", "report_date"], keep="last")
              .reset_index(drop=True))
    # 🚨 对拍：与 aplus 的「每票最新一份」逐位对齐（共享列全等）。
    # 🚨 **比较必须与行序无关**：`merge(how="outer")` 会按键排序，
    # 而 `mine` 是按 notice_date 排的 ⇒ 直接 `.equals()` 会因为**顺序**不同
    # 判红，而口径其实是对的（第一版就是这样误报的）。
    mine = (out.sort_values(["code", "notice_date", "report_date"])
                .drop_duplicates(subset=["code"], keep="last")
                .reset_index(drop=True))
    shared = ["code", "notice_date", "report_date", "report_year", "bps"]
    a_ = mine[shared].sort_values("code").reset_index(drop=True)
    b_ = ref[shared].sort_values("code").reset_index(drop=True)
    problems = []
    if len(a_) != len(b_):
        problems.append(f"行数 {len(a_)} vs aplus {len(b_)}")
    elif not a_["code"].equals(b_["code"]):
        problems.append("代码集合不同")
    else:
        for col in ("notice_date", "report_date", "bps"):
            x, y = a_[col], b_[col]
            same = bool(((x == y) | (x.isna() & y.isna())).all())
            if not same:
                problems.append(f"列 {col} 不一致")
    if problems:
        raise ValueError(
            "PIT 年报序列与 aplus.factor_fundamental.annual_from_frame 不一致："
            + "；".join(problems) + " ⇒ 口径漂了（去重键或可见性判据被改过）")
    return out


def price_and_shares_on(daily_map: dict, days, carry_stale: bool = True) -> pd.DataFrame:
    """逐 (day, code) 的**不复权 close** 与 **a_shares**。

    - close：那天（或**之前最后一根**）的收盘价（停牌沿用上一根，**不**填 0）。
    - a_shares：`volume × 100 / turn`（预注册 §3.3），**只有有效 bar 才有值**；
      停牌日沿用该票**最后一根有效 bar**的股本（`aplus/capweight.py` 的同一条规矩）。

    🚨 为什么价格取「之前最后一根」：A 股停牌日没有收盘价，
    而**填 0 会把 BM 变成 −inf、填 NaN 会让那一行凭空消失**
    （两个方向都是「假装知道」）。

    🚨🚨 `carry_stale=False` = **旧臂 R 的口径**（`scripts/fundamental_factor.py`
    的 `_closes_on`：**只取当天那根 bar**，停牌日 ⇒ 该票当天**没有因子值**）。
    生产侧（`aplus/factor_fundamental.py::close_on`）是**沿用上一根**。
    ⇒ 两套口径**差在停牌日的样本**上（实测：bm@K=21 的平均 IC 差 3e-5）
    ⇒ 本模块**两个口径都实现**，产物里各自标明 ⇒ 「复现」与「生产口径」不混为一谈。
    """
    from aplus.capweight import implied_a_shares_full

    days = list(days)
    rows = []
    for code, df in (daily_map or {}).items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        d = df["date"].astype(str).to_numpy()
        close = pd.to_numeric(df["close"], errors="coerce").to_numpy(float)
        a_sh = implied_a_shares_full(df).to_numpy(float)
        # 有效 bar：有 close>0 且 a_shares>0（turn<=0 / volume<=0 的日子没有股本）
        valid = np.isfinite(close) & (close > 0) & np.isfinite(a_sh) & (a_sh > 0)
        if not valid.any():
            continue
        if carry_stale:
            # 「最后一个 ≤ pos 的有效下标」的后向填充（停牌沿用上一根）
            idx = np.where(valid, np.arange(len(d)), -1)
            prev = np.maximum.accumulate(idx)
        for day in days:
            pos = int(np.searchsorted(d, day, side="right")) - 1
            if pos < 0:
                continue
            if carry_stale:
                j = int(prev[pos])
            else:
                if d[pos] != day or not valid[pos]:
                    continue
                j = pos
            if j < 0:
                continue
            rows.append({"day": day, "code": code,
                         "close": float(close[j]), "a_shares": float(a_sh[j])})
    if not rows:
        return pd.DataFrame(columns=["day", "code", "close", "a_shares"])
    return pd.DataFrame(rows)


def factor_values(annual_active: pd.DataFrame, px: pd.DataFrame) -> pd.DataFrame:
    """因子值：F1–F5（**逐字照抄** 2026-10-20 预注册 §2.1 的定义）。

    `annual_active` 已经是「那天可见的那一份年报」（含 bps/eps/roe/cfps），
    `px` 是 (day, code, close, a_shares)。

    🚨🚨 **join 必须走裸 6 位码**（`aplus/factor_fundamental.py::bare_code` 的
    同一条坑）：财务帧的 `SECURITY_CODE` 是**裸 6 位**（`000001`），
    而面板的 `xs[day]["code"]` 是**带交易所后缀**（`000001.SZ`）
    ⇒ 直接 merge **零行命中**，而 merge 静默返回空表
    ⇒ 因子「看起来存在」但一个数都没有（第一版就是这样：全 NaN 而不报错）。
    ⇒ 两边都过 `bare_code`，输出**还原成面板的原始键**。
    """
    from aplus.factor_fundamental import bare_code

    a = annual_active.rename(columns={"code": "bare_code"}).copy()
    a["bare"] = a["bare_code"].map(bare_code)
    p = px.rename(columns={"code": "px_code"}).copy()
    p["bare"] = p["px_code"].map(bare_code)
    m = a.merge(p, on=["day", "bare"], how="inner")
    m["code"] = m["px_code"]
    with np.errstate(divide="ignore", invalid="ignore"):
        m["bm"] = np.log(m["bps"].where(m["bps"] > 0) / m["close"])
        m["ep"] = np.log(m["eps"].where(m["eps"] > 0) / m["close"])
        m["q"] = m["cfps"] / m["bps"].where(m["bps"] > 0)
        m["size"] = -np.log((m["close"] * m["a_shares"]).where(m["a_shares"] > 0))
    return m


def active_annual_by_day(annual: pd.DataFrame, days) -> pd.DataFrame:
    """「每一天，每只票，当时可见的最新一份年报」。

    实现：逐票 `searchsorted(notice_date ≤ day, side='right') - 1`
    ⇒ **后向 asof**（与 `scripts/fundamental_factor.py::attach_to_cross_sections`
    的 `merge_asof(direction="backward")` **同一语义**）。
    """
    out = []
    days = list(days)
    for code, g in annual.groupby("code", sort=False):
        g = g.sort_values("notice_date")
        nd = g["notice_date"].to_numpy()
        pos = np.searchsorted(nd, np.array(days, dtype="datetime64[ns]"),
                              side="right") - 1
        ok = pos >= 0
        if not ok.any():
            continue
        rows = g.iloc[pos[ok]]
        rows = rows.assign(day=[days[i] for i in np.where(ok)[0]])
        out.append(rows[["day", "code", "notice_date", "report_date",
                         "report_year", "bps", "eps", "roe", "cfps"]])
    if not out:
        return pd.DataFrame(columns=["day", "code", "notice_date",
                                     "report_date", "report_year",
                                     "bps", "eps", "roe", "cfps"])
    return pd.concat(out, ignore_index=True)


def attach_factors(xs: dict, annual: pd.DataFrame, days, daily_map: dict,
                   factors=FACTORS, carry_stale: bool = True) -> dict:
    """把 F1–F5 **就地**挂进 `xs[day]`（沿用 `attach_to_cross_sections` 的形状）。

    只算**给定的那几天**（非重叠网格上的信号日 + 少数对照日）⇒
    全池臂下这是 74 天而不是 1,214 天，省一个量级。

    🚨🚨 **`size`（F4）不���要财报** ⇒ 它必须对**所有有价格的票**都算。
    第一版把它和 bm/ep/roe/q 一起从「当年可见的年报」那一侧算
    ⇒ 全池臂上它的覆盖率跟着财报塌成 18 只（本该是 5,460 只）
    ⇒ **对照因子在它最该说话的那条臂上失声**。
    ⇒ 现在：`size` 走**价格侧**（全部有价格的票），
    其余四个走**年报侧**（当年可见最新一份年报的票）。

    `carry_stale` 的两个口径见 `price_and_shares_on` 的 docstring。
    """
    days = list(days)
    px = price_and_shares_on(daily_map, days, carry_stale=carry_stale)
    annual_factors = [f for f in factors if f != "size"]
    parts = []
    if annual_factors:
        active = active_annual_by_day(annual, days)
        if not active.empty and not px.empty:
            fv = factor_values(active, px)
            keep = ["day", "code"] + [f for f in annual_factors
                                      if f in fv.columns]
            parts.append(fv[keep])
    if "size" in factors and not px.empty:
        sz = px.copy()
        with np.errstate(divide="ignore", invalid="ignore"):
            sz["size"] = -np.log((sz["close"] * sz["a_shares"])
                                 .where(sz["a_shares"] > 0))
        parts.append(sz[["day", "code", "size"]])
    if not parts:
        return xs
    # 🚨🚨 两部分必须按 (day, code) **合并**，不许 `concat`：
    # `concat` 会让同一个 code 出现两行（年报侧一行 + 价格侧一行）
    # ⇒ `set_index("code")` 后索引重复 ⇒ `join` 把那一行**复制成两行**
    # ⇒ 横截面里同一只票出现两次 ⇒ 它的收益被算两遍。
    fv = parts[0]
    for extra in parts[1:]:
        fv = fv.merge(extra, on=["day", "code"], how="outer")
    for day in days:
        uni = xs.get(day)
        if uni is None or uni.empty:
            continue
        sub = fv.loc[fv["day"] == day]
        if sub.empty:
            continue
        add = sub.set_index("code")
        cols = [f for f in factors if f in add.columns]
        if not cols:
            continue
        # 🚨 先把**已有**的同名因子列摘掉再挂：同一份横截面被两个口径
        # （生产 / 旧臂 R）各挂一次时，第二次 `join` 会撞列名 ⇒ 崩。
        uni = uni.drop(columns=[c for c in cols if c in uni.columns])
        uni = uni.join(add[cols], on="code")
        xs[day] = uni
    return xs


def attach_forward_factors(xs: dict, root=None, daily_map=None,
                           factors=("ep", "roe", "size", "q")) -> dict:
    """把前瞻臂的 F2/F3/F4/F5 挂进 `xs[day]`（**F1 `bm` 由 `aplus` 的
    `attach_pit_bm` 负责，这里不碰它**）。

    为什么需要它：`aplus/factor.py::composite_score` 对**缺失的因子列会 KeyError**
    ⇒ 前瞻台账里一旦登记了 `{ep: 1.0}` 这类候选，而前瞻面板没有 `ep` 列，
    **崩的是整个前瞻验证**（连不带这些因子的老批次一起陪葬）。

    口径与 `aplus/factor_fundamental.py::attach_pit_bm` **同构**：
    - 财务侧只从 `vintage.pit_frame(root, day)` 取（预注册 §3.1）；
    - 那天**没有可用快照** ⇒ 该列**全为 NaN**（不是 0、不是缺列）
      ⇒ `composite_score` 的 `min_count` 规则把那天全部剔掉 ⇒ **那天不出信号**；
    - 价格用**不复权 close**，`a_shares` = `volume × 100 / turn`。

    🚨 **不许抛异常**：这个函数跑在**每晚的前瞻验证**里，
    一个异常会连带杀掉全部批次 ⇒ 失败时退化成「全 NaN 列」并在返回值里报出来。
    """
    from aplus import vintage
    from aplus.factor_fundamental import snapshot_day

    days = list(xs.get("_dates") or [])
    if root is None:
        root = vintage.VINTAGE_ROOT
    if daily_map is None:
        daily_map = xs.get("_daily_map") or {}

    report = {"days": [], "unavailable_days": [], "n_days": len(days),
              "n_with_factors": 0, "errors": []}
    px_all = None
    for day in days:
        uni = xs.get(day)
        if uni is None or uni.empty:
            continue
        cols = {f: np.nan for f in factors}
        try:
            used = snapshot_day(root, day)
            if used is None:
                report["unavailable_days"].append(day)
            else:
                if px_all is None:
                    px_all = price_and_shares_on(daily_map, days)
                px = px_all.loc[px_all["day"] == day]
                if not px.empty:
                    frame = vintage.pit_frame(root, day)
                    ann = annual_series_from_pit(frame, day)
                    ann = ann.loc[ann["notice_date"] <= pd.Timestamp(day)]
                    act = (ann.sort_values("notice_date")
                              .drop_duplicates(subset=["code"], keep="last"))
                    act = act.assign(day=day)
                    fv = factor_values(act, px)
                    for f in factors:
                        if f == "size":
                            # 🚨 `size` 不需要财报 ⇒ 对**所有**有价格的票都给
                            # （否则前瞻面板的 `size` 只覆盖「有年报的那批票」
                            #  ⇒ 对照因子在它最该说话的地方失声）
                            # 🚨 `.to_dict()` 必须**最后**调用：
                            # `-np.log(s).to_dict()` 会先 `.to_dict()` 再取负
                            # ⇒ `TypeError: bad operand type for unary -: 'dict'`
                            # 🚨 索引必须是 **code**：直接对 `px` 的行号序列
                            # 取 dict ⇒ 键是 0,1,2 ⇒ `uni["code"].map(...)`
                            # **一个都命中不了** ⇒ 该列全 NaN 且不报错
                            with np.errstate(divide="ignore", invalid="ignore"):
                                cap = px["close"] * px["a_shares"]
                                sz = pd.Series(
                                    -np.log(cap.where(cap > 0)).to_numpy(),
                                    index=px["code"].to_numpy())
                            cols[f] = sz.to_dict()
                        elif f in fv.columns:
                            cols[f] = fv.set_index("code")[f].to_dict()
        except Exception as exc:                      # noqa: BLE001
            report["errors"].append({"day": day, "error": repr(exc)})
            report["unavailable_days"].append(day)
        uni = uni.copy()
        for f in factors:
            if f not in uni.columns:
                uni[f] = np.nan
            else:
                uni[f] = uni[f].astype(float)
            if isinstance(cols[f], dict):
                uni[f] = uni["code"].map(cols[f])
        xs[day] = uni
        if day not in report["unavailable_days"]:
            report["days"].append(day)
            report["n_with_factors"] += 1
    return report


__all__ = [
    "FACTORS", "FAMILY_A_PREREG", "LONG_SIGN", "LONG_LEGEND",
    "MIN_CROSS_SECTION", "MIN_LAG_DAYS", "MIN_NOTICE",
    "annual_series_from_cache", "annual_series_from_pit",
    "price_and_shares_on", "factor_values", "active_annual_by_day",
    "attach_factors", "attach_forward_factors",
]
