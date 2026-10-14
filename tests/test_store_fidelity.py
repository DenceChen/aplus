"""主库完整性防线测试（TDD RED 先行）。

## 要钉死的是什么

主库 `master/daily/{code}.parquet` 的正常形态是 **12 列**（含 `turn` 换手率、`isST`、
`amount`）。2026-10-11 全市场体检发现 **1693 只股票缺列**、**1598 只首行被截断**——
但根因不在抓取，在存储层的合并规则：

    df = pd.concat([old, df]).drop_duplicates(subset=["date"], keep="last")

`keep="last"` 让新到的行**无条件**覆盖旧行。当主源（baostock，12 列）失败、只有备源
（腾讯，仅 OHLCV 7 列）成功时，pipeline 把降级帧当正常数据写进主库；concat 之后这些
行的另外 5 列变成 NaN——**用缺列数据销毁了主库已有的完整数据**。

所以这里钉的是一条结构性不变量：**不管调用方是谁、以什么顺序调用，完整度只能增加，
不能减少**。调用方（pipeline、补数脚本）本来就会犯错，防线必须在最后一层。

同时钉住**反向**约束——这不是把主库冻结：信息量相同时新数据必须能覆盖，
否则复权修正、口径订正都会失效，而那正是这道防线最初被绕开时想保住的东西。
"""
import pandas as pd

from aplus.store import LocalStore

# baostock 主源：12 列
FULL_COLUMNS = ("date", "code", "open", "high", "low", "close", "volume",
                "amount", "tradestatus", "isST", "turn", "pctChg")
# 腾讯备源：只有 OHLCV，7 列（convert.py:81 明确不产出 amount）
NARROW_COLUMNS = ("date", "code", "open", "high", "low", "close", "volume")

DATES = ["2026-01-05", "2026-01-06", "2026-01-07"]


def _frame(dates, columns=FULL_COLUMNS, *, code="600000.SH", **overrides):
    """造日线帧。

    价格**只由日期决定**（不是行序号），这样同一 date 在主源帧和备源帧里数值一致，
    每个用例只需盯一个属性，不被价格噪声干扰。
    """
    dates = list(dates)
    n = len(dates)
    closes = [10.0 + (pd.Timestamp(d) - pd.Timestamp("2026-01-01")).days * 0.1 for d in dates]
    volumes = [1e6 + (pd.Timestamp(d) - pd.Timestamp("2026-01-01")).days * 1_000 for d in dates]
    data = {
        "date": dates,
        "code": [code] * n,
        "open": closes,
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
        "volume": volumes,
        "amount": [c * 1e6 for c in closes],
        "tradestatus": [1] * n,
        "isST": [0] * n,
        "turn": [1.5 + i for i, _ in enumerate(dates)],
        "pctChg": [0.0] * n,
    }
    df = pd.DataFrame({k: v for k, v in data.items() if k in columns})
    for col, value in overrides.items():
        df[col] = value
    return df


# ── 根因 B：降级窄帧不得销毁主库完整性 ─────────────────────────────────

def test_narrow_frame_cannot_shrink_master_schema(tmp_path):
    """根因 B 第一症状：备源（腾讯 7 列）写进已有 12 列的主库，列不得变窄。"""
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _frame(DATES))
    assert list(store.read_daily("600000.SH").columns) == list(FULL_COLUMNS)

    store.upsert_daily("600000.SH", _frame(DATES[-1:], NARROW_COLUMNS))  # 降级窄帧
    back = store.read_daily("600000.SH")
    assert set(FULL_COLUMNS) <= set(back.columns)
    assert len(back) == 3


def test_narrow_frame_cannot_null_out_existing_values(tmp_path):
    """根因 B 第二症状：turn/isST/amount 的**已填充值**必须存活。

    这一条比「列还在」更要命：列名留着、全是 NaN，比列直接消失更隐蔽——
    下游读得到列名，过滤条件却把股票静默剔除。
    """
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _frame(DATES))
    before = store.read_daily("600000.SH").set_index("date")

    store.upsert_daily("600000.SH", _frame(DATES[-1:], NARROW_COLUMNS))
    after = store.read_daily("600000.SH").set_index("date")

    row = "2026-01-07"
    for col in ("turn", "isST", "amount", "tradestatus", "pctChg"):
        assert after.loc[row, col] == before.loc[row, col], f"{col} 被降级帧抹掉了"
    assert after["turn"].notna().all()


def test_narrow_frame_cannot_clobber_existing_prices(tmp_path):
    """降级源的**数值**同样不能覆盖主库完整行：7 列帧信息量更低，
    它的 close 就不该被当成订正。"""
    store = LocalStore(tmp_path)
    truth = _frame(DATES)
    store.upsert_daily("600000.SH", truth)
    store.upsert_daily("600000.SH", _frame(["2026-01-07"], NARROW_COLUMNS, close=[99.0]))
    back = store.read_daily("600000.SH").set_index("date")
    assert back.loc["2026-01-07", "close"] == truth.set_index("date").loc["2026-01-07", "close"]


def test_narrow_frame_can_still_add_new_dates(tmp_path):
    """不能用「窄帧信息量少」当借口整帧拒写：窄帧带来的**新日期**仍然必须入库，
    只是已有日期的完整行不被降级数据冲掉。容灾语义不能被保列规则改掉。"""
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _frame(DATES[:2]))
    store.upsert_daily("600000.SH", _frame(DATES[1:], NARROW_COLUMNS))
    back = store.read_daily("600000.SH")
    assert back["date"].tolist() == DATES
    assert set(FULL_COLUMNS) <= set(back.columns)
    # 新日期本来就没有 turn——缺失要如实缺失，不能从别处编一个出来
    assert pd.isna(back.loc[back["date"] == "2026-01-07", "turn"].iloc[0])


# ── 反向约束：真实更新的数据必须仍能覆盖 ────────────────────────────────

def test_equal_information_lets_new_row_win(tmp_path):
    """对照组：两帧信息量相同（都是 12 列全填）时**新数据胜出**。

    没有这一条，「保列」就退化成「永远保旧」——复权/口径修正全部失效，
    主库被永久冻结，那不叫修复叫损坏。
    """
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _frame(DATES))
    corrected = _frame(DATES)
    corrected["close"] = corrected["close"] + 0.5   # 重取后的修正收盘价
    corrected["turn"] = corrected["turn"] + 0.5
    store.upsert_daily("600000.SH", corrected)
    back = store.read_daily("600000.SH")
    assert back["close"].tolist() == corrected["close"].tolist()
    assert back["turn"].tolist() == corrected["turn"].tolist()


def test_fuller_new_row_repairs_empty_cells(tmp_path):
    """自愈方向不能被堵死：主库某列是 NaN（历史降级留下的坑）、新帧该列有值时，
    必须写进去。否则「择多」会把这个坑永久钉死，缺口永远补不上。"""
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _frame(DATES, isST=float("nan")))
    store.upsert_daily("600000.SH", _frame(DATES))
    back = store.read_daily("600000.SH")
    assert back["isST"].notna().all()
    assert back["isST"].tolist() == [0, 0, 0]


def test_tie_keeps_new_row_but_backfills_its_holes(tmp_path):
    """择多的另一半：被淘汰的那行**不丢弃**，只用来逐格补骨架行的空单元格。

    构造平手局——old 的 turn 是历史坑（11 格有值），new 的 amount 是历史坑（11 格有值）：
    平手时新行当骨架，所以 close 的修正要生效；两边各有一个空位，都得被对方补上。
    """
    store = LocalStore(tmp_path)
    truth = _frame(DATES)
    store.upsert_daily("600000.SH", truth.assign(turn=float("nan")))
    corrected = truth.assign(amount=float("nan"))
    corrected["close"] = corrected["close"] + 0.5
    store.upsert_daily("600000.SH", corrected)
    back = store.read_daily("600000.SH")
    assert back["turn"].tolist() == truth["turn"].tolist()        # 旧行补回空位
    assert back["amount"].tolist() == truth["amount"].tolist()    # 新行补回空位
    assert back["close"].tolist() == (truth["close"] + 0.5).tolist()  # 平手新胜


def test_factor_update_still_overwrites(tmp_path):
    """因子表走同一个 `_upsert`：复权因子修正后必须能改写（两帧都全填 → 平手新胜）。"""
    store = LocalStore(tmp_path)
    store.upsert_factor("600000.SH", pd.DataFrame({"date": DATES[:2], "adjust_factor": [1.0, 1.1]}))
    store.upsert_factor("600000.SH", pd.DataFrame({"date": DATES[1:], "adjust_factor": [1.25, 1.3]}))
    back = store.read_factor("600000.SH")
    assert back["date"].tolist() == DATES
    assert back["adjust_factor"].tolist() == [1.0, 1.25, 1.3]


def test_duplicate_dates_inside_incoming_frame_keep_last(tmp_path):
    """既有语义不许动：合并时**帧内**重复 date 仍是 keep="last"（上游重试/拼接会产生）。
    （首写没有可合并对象时不去重，这是既有行为，此处不改动它。）"""
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _frame(["2026-01-04"]))
    dup = pd.concat([_frame(DATES[:2]), _frame([DATES[1]])], ignore_index=True)  # 01-06 出现两次
    store.upsert_daily("600000.SH", dup)
    back = store.read_daily("600000.SH")
    assert back["date"].tolist() == ["2026-01-04"] + DATES[:2]
    assert len(back) == 3


def test_column_order_keeps_master_schema_then_appends_new(tmp_path):
    """列顺序 = 主库既有列序 + 新增列追加，避免下游按列号取值时整体错位。"""
    store = LocalStore(tmp_path)
    store.upsert_daily("600000.SH", _frame(DATES[:1], NARROW_COLUMNS))
    store.upsert_daily("600000.SH", _frame(DATES))
    back = store.read_daily("600000.SH")
    expected = list(NARROW_COLUMNS) + ["amount", "tradestatus", "isST", "turn", "pctChg"]
    assert list(back.columns) == expected
    assert back["turn"].tolist() == _frame(DATES)["turn"].tolist()