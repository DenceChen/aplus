"""★ 数据损伤的**边界**必须被钉住，而不是被「大概没事」盖过（2026-10-15 实测）

## 先说清楚这个仓库里 NaN 有两族，**性质完全相反**

| 族 | 落在哪 | 行数 | 语义 |
|---|---|---|---|
| 停牌族 | `tradestatus == 0` | `turn` 29153 + `pctChg` 14380 = **43533** | **可解释**：当日无成交，baostock 返回空串 |
| 损伤族 | `tradestatus == 1` | **13 行，全在 `000043.SZ`** | **真损伤**：声称在交易，却没有换手率 |

⇒ 「全池 43546 个 NaN」这个数字**没有分母就没有意义** ——
99.97% 是正常的，0.03% 是我今天 02:25 那次修复自己留下的。

## 为什么必须分开报

`turn` 的 NaN 落在停牌日是**语义正确**（没有成交就没有换手）；
落在交易日则是**数据在说谎**（说 `tradestatus=1` 却给不出换手）。
两者混在一个计数里，就会出现两种坏结局：

- 报「4.9 万个 NaN，触目惊心」⇒ 去「修」那些**本来正确**的停牌行；
- 报「0.03%，可忽略」⇒ 真损伤被这个分母吃掉。

## ★ 那 13 行为什么在那里

`000043.SZ` 与 `300114.SZ` 是 2026-10-15 02:25 补历史的两只。
补出来的行形如 `turn=NaN, pctChg=0.0, tradestatus=1.0, volume=0.0` ——
`tradestatus` 填了 1、`volume` 填了 0，**唯独 `turn` 没算出来**。
`volume=0` 而 `tradestatus=1` 本身就自相矛盾，是这次补数的指纹。

## 影响面（先量再写）

- **`close` / `pctChg` 完好 ⇒ 收益口径不受影响**（`pctChg=0.0` 而非 NaN）；
- 受影响的是**流动性因子**（`turn20` / `turnratio`）在该票这 13 个日期上的取值；
- 因此本守卫**不**要求清零（清了要重跑全部下游产物），
  它要求的是：**这个数字不许变大，且不许出现在第二个文件里**。

⚠️ **代价**：实测 **17.17 s**（4 条守卫共用一次扫描，`_CACHE`）——
本仓较贵的守卫之一。
⚠️ 这个数字是**量出来的**，不是估的：第一版这里写的是「约 60–90 s」，
而实测差了 4–5 倍 ⇒ **在文件里写没量过的数字，本身就是要治的毛病**。
它守的是「引用任何绝对数字之前必须先过的关」，这类关不该便宜到可以随手跳过。
"""
from __future__ import annotations

import pathlib

import pandas as pd

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DAILY = _ROOT / "data" / "master" / "daily"

_COLS = ["turn", "pctChg", "tradestatus"]

# ★ 冻结边界：正常交易日的 NaN 只允许出现在这些文件，且不超过这些行数。
#   新增文件 ⇒ 有人引入了新的真损伤 ⇒ 必须当场解释。
#   行数变多 ⇒ 同一个洞在扩大。变少 ⇒ 有人修了（好），但要同步更新上限。
_LIVE_NAN_ALLOWLIST = {"000043.SZ": 13}


def _live_nan(d: "pd.DataFrame") -> dict[str, int]:
    """把一帧切成「正常交易日的 NaN」——判决逻辑单独拿出来，是为了能测它。

    ★ 为什么必须单独测：`pctChg` 在正常交易日**当前是零 NaN**，
    所以 `test_pct_chg_is_never_missing_on_a_trading_day` 在今天的数据下
    是**被数据空洞满足**的 —— 它只在数据变坏时才响（这正是不变式守卫该有的样子，
    但也意味着「变异代码」测不出它的牙：摘掉判断仍然是绿的，因为集合本来就是空的）。
    ⇒ 给判决逻辑配一条**合成帧**的正控，牙齿才在代码层也成立。
    """
    is_live = d["tradestatus"] == 1
    return {c: int((d[c].isna() & is_live).sum()) for c in ("turn", "pctChg") if (d[c].isna() & is_live).any()}


def _scan():
    """返回 (扫描文件数, {文件: {列: 正常交易日NaN行数}}, 停牌NaN 计数)"""
    live: dict[str, dict[str, int]] = {}
    susp = {"turn": 0, "pctChg": 0}
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
    return n_files, live, susp


_CACHE: tuple | None = None


def _cached():
    global _CACHE
    if _CACHE is None:
        _CACHE = _scan()
    return _CACHE


# ── 正控 ────────────────────────────────────────────────────────

def test_the_live_day_classifier_tells_the_two_families_apart():
    """★ 判决逻辑的**合成帧**正控 —— 上面两条不变式守卫的牙齿在代码层就靠它。

    构造一个最小帧，四种组合各一行：

    | tradestatus | turn | pctChg | 归属 |
    |---|---|---|---|
    | 1 | NaN | 0.0 | **真损伤**（声称在交易，却给不出换手） |
    | 1 | 0.5  | NaN | **真损伤**（收益口径有洞） |
    | 0 | NaN | NaN | 停牌族（无成交，可解释） |
    | 1 | 0.5  | 0.0 | 干净 |

    ⇒ 前两行必须被抓出来，后两行**必须不算**。
    最后一行是关键：**只抓 NaN 而不排除停牌日的分类器是错的**
    （那会把 43533 行正常停牌行全报成损伤）。
    """
    d = pd.DataFrame({
        "tradestatus": [1.0, 1.0, 0.0, 1.0],
        "turn":        [float("nan"), 0.5, float("nan"), 0.5],
        "pctChg":      [0.0, float("nan"), float("nan"), 0.0],
    })
    hit = _live_nan(d)
    assert hit == {"turn": 1, "pctChg": 1}, (
        f"★ 分类器判决错误：得到 {hit}，应为 {{'turn': 1, 'pctChg': 1}}"
        f"（停牌行与干净行都不该被算进来）"
    )


def test_the_scanner_actually_read_the_pool():
    """★ 没有这条，下面两条可能因为「一个文件都没读到」而**恒绿**。"""
    if not _DAILY.is_dir():
        import pytest
        pytest.skip(f"★ 主库不在 {_DAILY}")
    n_files, _, _ = _cached()
    assert n_files > 5000, f"★ 只读到 {n_files} 个文件（应 >5000），扫描器多半瞎了"


# ── 硬不变式 ────────────────────────────────────────────────────

def test_pct_chg_is_never_missing_on_a_trading_day():
    """★ `pctChg` 在正常交易日**零** NaN —— 这是硬不变式，没有例外。

    `pctChg` 直接进收益口径；它在交易日缺失 = 那天没有收益可算。
    实测 2026-10-15：14380 个 `pctChg` NaN **全部**落在停牌日。
    """
    _, live, _ = _cached()
    offenders = {k: v for k, v in live.items() if v.get("pctChg")}
    assert not offenders, (
        f"★ `pctChg` 在正常交易日出现 NaN：{offenders}\n"
        f"   ⇒ 收益口径有洞。这不是「停牌日没有成交」的正常缺失。"
    )


def test_live_day_nan_stays_inside_the_frozen_allowlist():
    """★ 真损伤只允许是已知的 13 行，且不许扩散到第二个文件。

    ⚠️ 本守卫**不**要求清零：`close`/`pctChg` 完好 ⇒ 收益口径不受影响，
    受损的只是流动性因子。清零要改数据，改数据要重跑全部下游产物 ——
    那是一个独立的、需要单独排期的决定，不该由一条守卫顺手做掉。
    """
    _, live, _ = _cached()
    actual = {k: v.get("turn", 0) for k, v in live.items()}
    assert actual == _LIVE_NAN_ALLOWLIST, (
        f"★ 正常交易日的 `turn` NaN 越界了。\n"
        f"   冻结值：{_LIVE_NAN_ALLOWLIST}\n"
        f"   实际值：{actual}\n"
        f"   ⇒ 新文件 = 新的真损伤，必须当场解释；"
        f"   行数变多 = 同一个洞在扩大；变少 = 有人修了，请同步更新冻结值。"
    )


def test_the_suspended_day_nan_is_recorded_not_merely_tolerated():
    """★ 停牌族要被**记下来**，因为它证明「停牌日无成交 ⇒ 无换手」这条语义成立。

    这条断言的价值在于：哪天停牌日的 `turn` 突然变成 0.0（而不是 NaN），
    它会响 —— 那意味着有人把「无成交」写成了「换手为零」，
    两者在因子上不是一回事。
    """
    _, _, susp = _cached()
    assert susp["turn"] > 1000 and susp["pctChg"] > 1000, (
        f"★ 停牌日 NaN 规模异常小：{susp}。"
        f"要么数据被整体改写过，要么这个分类已经不再成立 —— 两种都要解释。"
    )
