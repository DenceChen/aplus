"""交叉校验：双源比对 + 单源合理性规则。

## 容差口径（2026-10-11 事故后修订）

原依据是「跨源实测成交量/成交额 100% 一致 → 严格比对（相对容差 1e-4）」。
**那条依据只在大成交量上成立。** 腾讯 kline 的成交量按 **100 股（1 手）取整**，
baostock 给精确股数，取整误差上界 ±50 股，于是相对误差随成交量下降而放大：

| 成交量 | 相对误差上界 | vs `tol_rel = 1e-4` |
|---|---|---|
| 8,397,166 股 | 6.0e-6 | 通过 |
| 300,000 股 | **1.7e-4** | **不通过** |

2026-10-11 全量补数实测：**513 个失配行，最大相对误差 0.0864%，
100% 都能被 ±100 股吸收**，失配行成交量中位数 300,300 股。

后果不是「多隔离几只」：23 只被隔离，其中 **13 只的 master 文件根本不存在**——
它们的数据只在隔离区，等于从股票池里被静默删除。

## 为什么是绝对容差而不是放宽相对容差

放宽相对容差会让**真正的价格错配**蒙混过关——那正是这道闸门的本职。
正确的做法是给**按手取整的字段**加一个与成交量无关的绝对容差：

    bound = |a| * tol_rel + abs_tol[字段] + 1e-9

价格字段**不加**绝对容差：它们没有量化问题，加了就是纯粹的放松。
`test_price_fields_get_no_absolute_tolerance` 把这条钉死。
"""
from dataclasses import dataclass, field

import pandas as pd

COMPARE_FIELDS = ("open", "high", "low", "close", "volume", "amount")
DEFAULT_TOL_REL = 1e-4
DEFAULT_MISMATCH_THRESHOLD = 0.02  # 不匹配行比例超过 2% → 整批不通过

# A 股 1 手 = 100 股。腾讯 kline 的成交量按手取整，与精确值最多差 ±50 股。
DEFAULT_ABS_TOL_VOLUME = 100.0

# 只给**按手取整**的字段开绝对容差。价格字段不在其中：它们没有量化问题。
ABS_TOL_BY_FIELD = {"volume": DEFAULT_ABS_TOL_VOLUME}


@dataclass
class CrossCheckReport:
    n_rows_a: int = 0
    n_rows_b: int = 0
    common_dates: int = 0
    missing_in_a: list = field(default_factory=list)
    missing_in_b: list = field(default_factory=list)
    field_mismatches: dict = field(default_factory=dict)
    mismatch_ratio: float = 0.0
    passed: bool = True

    def summary(self) -> str:
        fields = ", ".join(f"{k}={v}" for k, v in self.field_mismatches.items() if v)
        return (
            f"rows a/b={self.n_rows_a}/{self.n_rows_b} common={self.common_dates} "
            f"missing_a={len(self.missing_in_a)} missing_b={len(self.missing_in_b)} "
            f"mismatch_ratio={self.mismatch_ratio:.4f} [{fields}] passed={self.passed}"
        )


def compare_frames(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    tol_rel: float = DEFAULT_TOL_REL,
    compare_fields=COMPARE_FIELDS,
    mismatch_threshold: float = DEFAULT_MISMATCH_THRESHOLD,
    coverage: str = "strict",
    abs_tol: dict | None = None,
) -> CrossCheckReport:
    """coverage="strict"：任一侧缺日期即不通过（每日增量场景，窗口短、双方都应全覆盖）。
    coverage="overlap"：只对交集日期严格比对（长窗口回填场景——备源如腾讯仅有近 640
    交易日，窗口外的缺失是能力边界而非数据错误）；缺失日期仍报告供人工核查。

    `abs_tol` 缺省取 `ABS_TOL_BY_FIELD`：按手取整的字段带一个与量级无关的绝对容差，
    否则备源的量化误差会被误判成数据错误，进而**否决主源已经拿到的好数据**。
    """
    tol = ABS_TOL_BY_FIELD if abs_tol is None else abs_tol
    a = df_a.set_index("date")
    b = df_b.set_index("date")
    common = a.index.intersection(b.index)

    report = CrossCheckReport(
        n_rows_a=len(a),
        n_rows_b=len(b),
        common_dates=len(common),
        missing_in_a=sorted(b.index.difference(a.index).tolist()),
        missing_in_b=sorted(a.index.difference(b.index).tolist()),
        field_mismatches={f: 0 for f in compare_fields},
    )

    mismatch_rows: set = set()
    for f in compare_fields:
        if f not in a.columns or f not in b.columns:
            continue
        va = pd.to_numeric(a.loc[common, f])
        vb = pd.to_numeric(b.loc[common, f])
        diff = (va - vb).abs()
        bound = va.abs() * tol_rel + float(tol.get(f, 0.0)) + 1e-9
        bad = diff > bound
        report.field_mismatches[f] = int(bad.sum())
        mismatch_rows.update(common[bad.to_numpy()].tolist())

    report.mismatch_ratio = len(mismatch_rows) / len(common) if len(common) else 0.0
    coverage_ok = not report.missing_in_a and not report.missing_in_b
    report.passed = (coverage_ok or coverage == "overlap") and report.mismatch_ratio <= mismatch_threshold
    return report


def sanity_check(df: pd.DataFrame) -> list:
    """单源合理性规则：OHLC 不变式 + 价格量为非负。返回问题描述列表，空即通过。

    对缺失列（如腾讯源无 amount）自动跳过——不同源 schema 可有可无的列不视为错误。
    """
    issues: list = []
    n = len(df)

    def _flag(mask, msg):
        count = int(mask.sum())
        if count:
            issues.append(f"{msg} on {count}/{n} rows")

    if {"high", "low", "open", "close"} <= set(df.columns):
        _flag(df["high"] < df["low"], "high < low")
        oc_max = df[["open", "close"]].max(axis=1)
        oc_min = df[["open", "close"]].min(axis=1)
        _flag(df["high"] < oc_max, "high < max(open, close)")
        _flag(df["low"] > oc_min, "low > min(open, close)")
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            _flag(df[col] <= 0, f"non-positive {col}")
    for col in ("volume", "amount"):
        if col in df.columns:
            _flag(df[col] < 0, f"negative {col}")
    return issues
