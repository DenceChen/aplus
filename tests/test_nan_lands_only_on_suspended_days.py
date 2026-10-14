"""★ 数据损伤的**边界**必须被钉住，而不是被「大概没事」盖过（2026-10-15 实测）

## 这个仓里 `turn` 的 NaN 有两族，而**两族都无害** —— 这是第一版写错的地方

| 族 | 判据 | 行数 | 语义 | 对判决 |
|---|---|---|---|---|
| 停牌族 | `tradestatus == 0` | `turn` 29153 + `pctChg` 14380 = **43533** | 无成交 ⇒ 无换手，**语义正确** | 无 |
| 幽灵行 | `tradestatus == 1` **且** `volume <= 0` | **13 行，全在 `000043.SZ`** | **根本没有成交**，是补数填出来的占位行 | **可证为零** |

⚠️ **第一版把第二族写成「真损伤：声称在交易，却给不出换手率」，
并断言它影响 `turn20`/`turnratio`「在该票这 13 个日期上的取值」——
两句都错。** 2026-10-15 逐行实测把那 13 行拆开看：

```text
1995-02-06  open=high=low=close=7.75   volume=0  amount=0  tradestatus=1  turn=NaN
1995-02-07  open=high=low=close=7.75   volume=0  amount=0  tradestatus=1  turn=NaN
…（1995-02-06~02-10 连续 5 行）… 1999-05-21
```

**`open = high = low = close` 且 `volume = 0` 且 `amount = 0`** ——
这一天**没有成交**，价格是原样搬运的。所以错的是 `tradestatus=1` 这个标记本身，
`turn` 算不出来只是**症状**，不是损害。

⇒ 第二族不是「真损伤」，是**幽灵行**。全池实测：
`tradestatus == 1 且 volume <= 0` 共 **13 行 1 只**，
其中 `turn` **有值**的（即换手率被凭空造出来的）**0 行**。

## ★ 为什么还是要有守卫：不是因为它危险，是因为它**可证无害**

「无害」是这条数据当时的位置决定的**结论**，不是它的**性质**：

- `000043.SZ` 全文件 **6127 行，1994-09-28 → 2019-12-16**，
  **2021-01-01 之后 0 行** ⇒ 对判决窗口 `[2021-01-01, 2026-10-12)`
  的行数贡献是 **0**；
- 末行 `2019-12-16` 是 `tradestatus=0, volume=0`，
  而 12-13 还正常成交 ⇒ 该票当日停牌且未复牌，**不是数据洞**。

⇒ 那 13 行在窗口外，所以对任何判决贡献**可证的零**。
**这条推论会在有人把该票补进窗口时立刻失效** ——
而失效那天，没有任何机制会喊。所以守卫钉的是**这个前提**，不是那 13 行。

⚠️ 顺带纠正第一版的另一句错话：「`close`/`pctChg` 完好 ⇒ 收益口径不受影响」。
`pctChg=0.0` 确实是 0，但 `close` 在这 13 行上是**搬运来的假价**
（5 天完全相同）—— 若这 13 行落在窗口内，收益口径**是会**受影响的。
「不受影响」当时成立，**只因为它们在窗口外**，不是因为它们本身干净。

## 代价

实测 **21.50 s**（9 条守卫共用一次全池扫描，`_CACHE`；三遍 22.51 / 22.03 / 22.35 s）。
⚠️ 这个数字是**量出来的**，不是估的：第一版这里写的是「约 60–90 s」，
实测差了 4–5 倍 ⇒ **在文件里写没量过的数字，本身就是要治的毛病**。
（第二版加 `volume` 列与窗口守卫后重测为 21.50 s，也在文首留了痕迹。）
它守的是「引用任何绝对数字之前必须先过的关」，这类关不该便宜到可以随手跳过。
"""
from __future__ import annotations

import pathlib

import pandas as pd

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DAILY = _ROOT / "data" / "master" / "daily"

_COLS = ["turn", "pctChg", "tradestatus", "volume"]

#: 判决窗口。★ 与 `scripts/backtest_walk_forward_weights.py` 的 `--start/--end` 同值。
#: 「幽灵行无害」这个结论**只在这个窗口下成立**，换窗口必须重判。
_WINDOW = ("2021-01-01", "2026-10-12")

# ★ 冻结边界：`tradestatus=1` 却零成交的行，只允许出现在这些文件，且不超过这些行数。
#   新增文件 ⇒ 有人引入了新的幽灵行 ⇒ 必须当场解释。
#   行数变多 ⇒ 同一个洞在扩大。变少 ⇒ 有人修了（好），但要同步更新上限。
_LIVE_NAN_ALLOWLIST = {"000043.SZ": 13}

#: 冻结边界：`tradestatus=1` 且 `volume<=0` 的行数。**必须等于**上面的 allowlist ——
#: 两者本该是同一批行（第一版只按 `tradestatus` 分族，把它叫成「真损伤」；
#: 按成交量分族才知道 `tradestatus` 本身是错的）。数量不等 ⇒ 出现了新的形态。
_ZERO_VOL_LIVE_ALLOWLIST = {"000043.SZ": 13}


def _live_nan(d: "pd.DataFrame") -> dict[str, int]:
    """把一帧切成「标记为在交易、却没有成交」的行上的 NaN。

    ★ 判决逻辑单独拿出来，是为了能测它。
    第一版这里是「tradestatus==1 的 NaN」，没有 `volume` ——
    于是它把**幽灵行**误判成「数据在说谎」，而说谎的其实是 `tradestatus`。
    """
    is_live = d["tradestatus"] == 1
    return {c: int((d[c].isna() & is_live).sum())
            for c in ("turn", "pctChg") if (d[c].isna() & is_live).any()}


def _ghost(d: "pd.DataFrame"):
    """「幽灵行」：标记为在交易、当天却零成交。返回 (行数, 其中 turn 有值的行数)。"""
    is_live = d["tradestatus"] == 1
    no_trade = is_live & (d["volume"].fillna(0) <= 0)
    n = int(no_trade.sum())
    fabricated = int((no_trade & d["turn"].notna()).sum())
    return n, fabricated


def _scan():
    """一次扫描同时算完两族 + 幽灵行判据 —— **不写第二个扫描器**（形状 17）。"""
    live: dict[str, dict[str, int]] = {}
    susp = {"turn": 0, "pctChg": 0}
    ghost: dict[str, int] = {}
    fabricated = 0
    n_files = 0
    for p in sorted(_DAILY.glob("*.parquet")):
        try:
            d = pd.read_parquet(p, columns=_COLS)
        except Exception:
            continue
        n_files += 1
        is_live = d["tradestatus"] == 1
        for col in ("turn", "pctChg"):
            m = d[col].isna()
            susp[col] += int((m & ~is_live).sum())
        hit = _live_nan(d)
        if hit:
            live[p.stem] = hit
        g, f = _ghost(d)
        if g:
            ghost[p.stem] = g
        fabricated += f
    return n_files, live, susp, ghost, fabricated


_CACHE: tuple | None = None


def _cached():
    global _CACHE
    if _CACHE is None:
        _CACHE = _scan()
    return _CACHE


# ── 正控 ────────────────────────────────────────────────────────

def test_the_live_day_classifier_tells_the_two_families_apart():
    """★ 判决逻辑的**合成帧**正控 —— 下面两条不变式守卫的牙齿在代码层就靠它。

    构造一个最小帧，四种组合各一行：

    | tradestatus | volume | turn | pctChg | 归属 |
    |---|---|---|---|---|
    | 1 | 0 | NaN | 0.0 | **幽灵行**（声称在交易却零成交 ⇒ `tradestatus` 才是错的） |
    | 1 | 100 | 0.5 | NaN | **真损伤**（真在交易，收益口径有洞） |
    | 0 | 0 | NaN | NaN | 停牌族（无成交，可解释） |
    | 1 | 100 | 0.5 | 0.0 | 干净 |

    ⇒ 前两行必须被抓出来，后两行**必须不算**。
    最后一行是关键：**只抓 NaN 而不排除停牌日的分类器是错的**
    （那会把 43533 行正常停牌行全报成损伤）。
    """
    d = pd.DataFrame({
        "tradestatus": [1.0, 1.0, 0.0, 1.0],
        "volume":      [0.0, 100.0, 0.0, 100.0],
        "turn":        [float("nan"), 0.5, float("nan"), 0.5],
        "pctChg":      [0.0, float("nan"), float("nan"), 0.0],
    })
    hit = _live_nan(d)
    assert hit == {"turn": 1, "pctChg": 1}, (
        f"★ 分类器判决错误：得到 {hit}，应为 {{'turn': 1, 'pctChg': 1}}"
        f"（停牌行与干净行都不该被算进来）"
    )


def test_the_ghost_detector_separates_ghost_rows_from_real_trades():
    """★ 正控之二：幽灵判据必须看 **`volume`**，不能只看 `tradestatus`。

    四个样本，第 1、3 行才是幽灵（第 2 行真成交、第 4 行根本没标记在交易）：

    | # | tradestatus | volume | turn | 幽灵？ | 凭空换手率？ |
    |---|---|---|---|---|---|
    | 1 | 1 | 0   | NaN | ✅ | 否（本来就是空的） |
    | 2 | 1 | 100 | 0.5 | ❌ 真成交 | — |
    | 3 | 1 | 0   | 1.5 | ✅ | ★ **是**：零成交却给出换手率 |
    | 4 | 0 | 0   | NaN | ❌ 未标在交易 | — |

    ⇒ `(幽灵行数, 凭空换手率)` 必须**恰好**是 `(2, 1)`。
    第 2 行是牙齿所在：**只按 `tradestatus` 分族的检测器会把它也算进去**，
    而它是最正常的一行 —— 那样的检测器会把 5914 行正常交易全报成幽灵。

    ⚠️ 第一版这里把期望值写成 `(1, 1)`（数漏了第 1 行），
    差点去改**正确的检测器**去迁就写错的断言 —— 见 memory
    `an-over-strict-guard-is-as-harmful-as-a-tautological-one`：
    守卫红了先问「它红得对吗」，再问「代码错了吗」。
    """
    d = pd.DataFrame({
        "tradestatus": [1.0, 1.0, 1.0, 0.0],
        "volume":      [0.0, 100.0, 0.0, 0.0],
        "turn":        [float("nan"), 0.5, 1.5, float("nan")],
    })
    n, fab = _ghost(d)
    assert (n, fab) == (2, 1), (
        f"★ 幽灵判据判决错误：得到 (行数, 凭空换手率)=({n}, {fab})，应为 (2, 1)"
        f" —— 第 3 行零成交却有 turn，正是必须抓出来的「凭空换手率」；"
        f"   第 2 行真成交，绝不能被算成幽灵"
    )


def test_the_scanner_actually_read_the_pool():
    """★ 没有这条，下面几条可能因为「一个文件都没读到」而**恒绿**。"""
    if not _DAILY.is_dir():
        import pytest
        pytest.skip(f"★ 主库不在 {_DAILY}")
    n_files, _, _, _, _ = _cached()
    assert n_files > 5000, f"★ 只读到 {n_files} 个文件（应 >5000），扫描器多半瞎了"


# ── 硬不变式 ────────────────────────────────────────────────────

def test_pct_chg_is_never_missing_on_a_trading_day():
    """★ `pctChg` 在「真在交易」的日子**零** NaN —— 这是硬不变式，没有例外。

    `pctChg` 直接进收益口径；它在真实成交日缺失 = 那天没有收益可算。
    实测 2026-10-15：14380 个 `pctChg` NaN **全部**落在停牌日。
    """
    _, live, _, _, _ = _cached()
    offenders = {k: v for k, v in live.items() if v.get("pctChg")}
    assert not offenders, (
        f"★ `pctChg` 在「标记为在交易」的日子出现 NaN：{offenders}\n"
        f"   ⇒ 若这些行 `volume>0`（真成交），收益口径有洞。"
        f"   若 `volume==0`，那是下面的幽灵行守卫该管的事。"
    )


def test_live_day_nan_stays_inside_the_frozen_allowlist():
    """★ 幽灵行只允许是已知的 13 行，且不许扩散到第二个文件。"""
    _, live, _, _, _ = _cached()
    actual = {k: v.get("turn", 0) for k, v in live.items()}
    assert actual == _LIVE_NAN_ALLOWLIST, (
        f"★ 「标记为在交易」却 `turn` 为 NaN 的行越界了。\n"
        f"   冻结值：{_LIVE_NAN_ALLOWLIST}\n"
        f"   实际值：{actual}\n"
        f"   ⇒ 新文件 = 新的形态，必须当场解释；"
        f"   行数变多 = 同一个洞在扩大；变少 = 有人修了，请同步更新冻结值。"
    )


def test_zero_volume_days_claiming_to_trade_are_only_the_known_ghost_rows():
    """★ 按**成交量**分族：这就是那 13 行的真身。

    ⚠️ 两条冻结名单必须**数量相等**：第一版只按 `tradestatus` 分族，
    把这 13 行叫成「真损伤」（数据在说谎）；加上 `volume` 才看清
    **说谎的是 `tradestatus` 本身** —— 那天根本没有成交。
    """
    _, _, _, ghost, _ = _cached()
    assert ghost == _ZERO_VOL_LIVE_ALLOWLIST, (
        f"★ 「声称在交易、当天零成交」的行越界了。\n"
        f"   冻结值：{_ZERO_VOL_LIVE_ALLOWLIST}\n"
        f"   实际值：{ghost}\n"
        f"   ⇒ 与 `_LIVE_NAN_ALLOWLIST` 不相等 ⇒ 出现了「零成交但 turn 有值」的新形态。"
    )


def test_no_day_anywhere_claims_to_trade_with_a_fabricated_turnover():
    """★ 真正危险的那一格：**零成交却给出换手率** —— 因子会拿它当真实流动性用。

    实测 2026-10-15：**0 行**。这条守卫的作用是**守住这个 0**：
    哪天补数把某个占位行的 `turn` 填上了，它必须响。
    """
    _, _, _, _, fabricated = _cached()
    assert fabricated == 0, (
        f"★ 全池有 {fabricated} 行「零成交却有换手率」⇒ 流动性因子在用凭空数字。"
        f"   这比幽灵行本身危险得多：幽灵行的 `turn` 是空的，会被自然跳过。"
    )


def test_the_known_ghost_rows_stay_outside_the_judging_window():
    """★ 这才是「无害」的**可重验**形式 —— 守卫钉的是**前提**，不是那 13 行。

    「那 13 行对判决贡献零」不是它的**性质**，是它**当前的位置**决定的结论：
    `000043.SZ` 在窗口内一行都没有。哪天有人把它补进窗口，
    这个结论立刻失效，而那天没有任何别的东西会喊。

    ⇒ 顺带钉住「该文件在窗口内 0 行」这个更根本的事实：
    末行 `2019-12-16` 是 `tradestatus=0, volume=0`，而 12-13 还正常成交
    ⇒ 该票当日停牌且未复牌，**不是数据洞**（真洞是「最后一行在交易且有成交」）。

    ⚠️ 窗口末端取 `2026-10-12`（本次重跑钉的值）。若不传 `--end`，
    脚本用数据最大日 `2026-10-14` —— 对本守卫**无影响**：
    该文件 2019 年就停了，两天之差不可能跨过它。
    """
    lo, hi = (pd.Timestamp(x) for x in _WINDOW)
    report = {}
    for stem in sorted(_LIVE_NAN_ALLOWLIST):
        p = _DAILY / f"{stem}.parquet"
        if not p.is_file():
            report[stem] = {"缺文件": True}
            continue
        d = pd.read_parquet(p, columns=["date", "turn", "tradestatus"])
        dt = pd.to_datetime(d["date"])
        inw = (dt >= lo) & (dt < hi)
        ghost_in_window = int((inw & (d["turn"].isna()) & (d["tradestatus"] == 1)).sum())
        report[stem] = {"窗口内总行数": int(inw.sum()), "窗口内幽灵行": ghost_in_window}

    offenders = {k: v for k, v in report.items()
                 if v.get("缺文件") or v["窗口内总行数"] or v["窗口内幽灵行"]}
    assert not offenders, (
        f"★ 幽灵行已经落进判决窗口 {_WINDOW} ⇒ 「无害」这个结论已失效，\n"
        f"   必须重算依赖它的每一个数字。\n   实测：{report}"
    )


def test_the_suspended_day_nan_is_recorded_not_merely_tolerated():
    """★ 停牌族要被**记下来**，因为它证明「停牌日无成交 ⇒ 无换手」这条语义成立。

    哪天停牌日的 `turn` 突然变成 0.0（而不是 NaN），它会响 ——
    那意味着有人把「无成交」写成了「换手为零」，两者在因子上不是一回事。
    """
    _, _, susp, _, _ = _cached()
    assert susp["turn"] > 1000 and susp["pctChg"] > 1000, (
        f"★ 停牌日 NaN 规模异常小：{susp}。"
        f"要么数据被整体改写过，要么这个分类已经不再成立 —— 两种都要解释。"
    )
