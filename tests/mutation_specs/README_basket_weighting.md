# `basket_weighting` 的变异规格（2026-10-15 新增）

规格本体在 `test_basket_weighting.mut`；这里只放说明。

## 为什么先钉这一条

`trailing_sigma` 里的**泄露闸门**就是 `tmp["date"] <= upto` 那一行。
它今天是对的，但它的正确性**依赖于「frame 一定有 `date` 列」这个从未被写下来的契约**（第 114 层）。

原写法 `df["date"] if "date" in df.columns else df.index` 在契约断裂时
**不会报错，只会泄露**：

| 口径 | 过滤后行数 | 未来行 |
|---|---|---|
| 日期正常 | **14**/60 | 46 行**被挡住** |
| 回落 `df.index` | **60**/60 | 46 行**全被留下** |

而 `tail(window+1)` 取的是**末尾** ⇒ sd 全部来自未来，全程 exit 0。

⚠️ 实测全量 5460 份 daily parquet **全部有 `date`** ⇒ 这个变异体
在今天的真实数据上**是空操作**，所以只能靠 spec 把它钉成**永不绿**。
