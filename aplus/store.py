"""本地 parquet 存储：主库（master）+ 隔离区（quarantine）。

目录结构：
    root/master/daily/{code}.parquet   校验通过的不复权日线
    root/master/factor/{code}.parquet  复权因子事件表
    root/quarantine/daily/{code}.parquet 校验不达标数据（带原因，不污染主库）

## 合并规则为什么不是 drop_duplicates(keep="last")

2026-10-11 全市场体检：1693 只股票缺列、1598 只首行被截断。抓取层是干净的，
坏在合并层——`keep="last"` 让新到的行**无条件**覆盖旧行，于是：

    主源 baostock（12 列）失败 → 只有腾讯备源（7 列 OHLCV）成功
    → pipeline 把降级帧当正常数据写进主库
    → concat 后这些行的 amount/tradestatus/isST/turn/pctChg 全变 NaN
    → 用缺列数据销毁了主库已有的完整数据

结果是「列还在、值全空」——比列直接消失更隐蔽：下游读得到列名，过滤条件却把
股票静默剔除，含换手率因子的候选池凭空少掉几千只。

调用方（pipeline、补数脚本）本来就会犯错，所以防线必须落在最后一层：
`_upsert` 对**完整度只增不减**做结构性保证，与调用方是谁、以什么顺序调用无关。
详见 `_merge_preserving_fidelity`。
"""
from pathlib import Path

import pandas as pd

# 合并时的内部哨兵列（双下划线，避免与任何真实行情列撞名）
_SRC = "__merge_src__"
_FILL = "__merge_fill__"


def _merge_preserving_fidelity(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """按 date 合并新旧两帧，**完整度只增不减**。

    ## 择多规则（可测量、可复现）

    1. **列取并集**：顺序 = `old` 的列序，其后追加 `new` 独有的列。列只能增不能减，
       且保持主库既有列序，避免下游按列号取值整体错位。
    2. **信息量 = 非空单元格数**：某一行在并集列上有几个格子不是空，就算它有几分。
       `date` 恒非空、对两行等权计入，不影响比较结果。
    3. **骨架取信息量多的那行**：同一 date 出现两行时，以信息量**较大**的行为骨架；
       **信息量相同时新行胜出**（与既有 `keep="last"` 语义一致）——真实更新的数据
       必须能覆盖（复权修正、口径订正都靠它），否则这道防线就成了把主库冻结。
    4. **落选行不丢弃**：它只用来逐格填补骨架行的空单元格（逐格 combine_first
       语义），规则是「择多 + 少者填空」，不是「二选一扔一整个」。
    5. **帧内重复 date** 各自先 `keep="last"` 去重（既有语义）。

    三条推论：降级窄帧覆盖不了完整行；完整新帧能修好主库里的历史 NaN 坑；
    窄帧带来的新日期照常入库（缺列如实缺失，不编值）。
    """
    old = old.drop_duplicates(subset=["date"], keep="last")
    new = new.drop_duplicates(subset=["date"], keep="last")
    columns = list(old.columns) + [c for c in new.columns if c not in set(old.columns)]

    tagged = pd.concat(
        [old.assign(**{_SRC: 0}), new.assign(**{_SRC: 1})],
        ignore_index=True,
    ).reindex(columns=columns + [_SRC])
    tagged[_FILL] = tagged[columns].notna().sum(axis=1)

    # 组内按 (date, 信息量, 来源) 升序：最后一个就是「信息量最大者，平手取新行」
    tagged = tagged.sort_values(["date", _FILL, _SRC], kind="stable")
    skeleton = tagged.drop_duplicates(subset=["date"], keep="last").drop(columns=[_FILL])
    rest = tagged[tagged.duplicated(subset=["date"], keep="last")].drop(columns=[_FILL])

    # 骨架优先，骨架的空格子才由落选行填（= combine_first 语义，但快 4 倍：
    # combine_first 每次都要把两侧 align 一遍，全历史 1900 行时是整段合并的瓶颈）
    skeleton = skeleton.set_index("date")
    rest = rest.set_index("date")
    merged = skeleton.where(skeleton.notna(), rest)   # date 已升为索引，不再回填
    merged = merged.reindex(columns=[c for c in columns if c != "date"]).reset_index()
    return merged.sort_values("date").reset_index(drop=True)


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)
        for sub in ("master/daily", "master/factor", "quarantine/daily"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def _master_path(self, kind: str, code: str) -> Path:
        return self.root / "master" / kind / f"{code}.parquet"

    def _quarantine_path(self, code: str) -> Path:
        return self.root / "quarantine" / "daily" / f"{code}.parquet"

    def _upsert(self, path: Path, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return  # 空帧拒写：已有数据保持原样，新代码不落空文件（NaN 日期崩溃根因）
        df = df.copy()
        df["date"] = df["date"].astype(str)  # 存储边界类型归一，杜绝 str/datetime 混杂
        if path.exists():
            old = pd.read_parquet(path)
            old["date"] = old["date"].astype(str)
            df = _merge_preserving_fidelity(old, df)
        df = df.sort_values("date").reset_index(drop=True)
        df.to_parquet(path, index=False)

    def upsert_daily(self, code: str, df: pd.DataFrame) -> None:
        self._upsert(self._master_path("daily", code), df)
        # 入库成功即清除隔离区残留（重跑修复场景：同一股票不应两处并存）
        quarantine = self._quarantine_path(code)
        if quarantine.exists():
            quarantine.unlink()

    def read_daily(self, code: str):
        path = self._master_path("daily", code)
        return pd.read_parquet(path) if path.exists() else None

    def upsert_factor(self, code: str, df: pd.DataFrame) -> None:
        self._upsert(self._master_path("factor", code), df)

    def read_factor(self, code: str):
        path = self._master_path("factor", code)
        return pd.read_parquet(path) if path.exists() else None

    def quarantine_daily(self, code: str, df: pd.DataFrame, reason: str) -> None:
        df = df.copy()
        df["quarantine_reason"] = reason
        df.to_parquet(self._quarantine_path(code), index=False)

    def read_quarantine_daily(self, code: str):
        path = self._quarantine_path(code)
        return pd.read_parquet(path) if path.exists() else None
