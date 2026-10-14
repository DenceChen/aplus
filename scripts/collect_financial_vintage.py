"""每日财报 vintage 采集（出路 B）——**PIT 的原材料**。

## 为什么必须马上开始，而不是「等有空再说」

`aplus/financial.py`：源站每个报告期只存一行、无版本维度，服务的是后修值。
出路 A 已被 `scripts/diag_financial_lookahead.py` 实测否决。
**只剩出路 B，而它的价值随延迟毁灭**：

> 昨天源站服务过什么，除了当时有人记下来，否则永远无从得知。

所以本脚本不是「一个可选的数据增强」，它是**唯一一个错过就永久损失的任务**。
2026-10-14 之前的历史无法补——从今天起，每一天都是新的第一天。

## 为什么它是独立的 launchd 作业，不进 `run_daily`

实测 0.54 秒/只 × 5460 只 = **全量扫描约 49 分钟**。挂进主流程的后果：

- `run_daily` 一旦变长或失败，采集跟着断——而**断一天就是丢一天**；
- 采集本身不该有能力让生产日更变红。**它的失败必须可见但无害。**

所以：独立 plist，每天 22:30。`run_daily` 不依赖它，它也不依赖 `run_daily`。

## 落盘语义

一天一份 parquet，存**源站那天服务的整帧**（不做列裁剪——见 `vintage.py` 的理由）。
中途按批 flush，**只写当天那一份**，所以随时可断、随时可续，
而历史分区永远不被触碰。

中断后重跑：`--resume` 会读当天已有的分区、跳过已完成的股票，
继续补齐。**只有全部抓完才写 manifest 条目**——
半份快照冒充完整快照，比没有更坏。
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from aplus.vintage import (  # noqa: E402
    VINTAGE_ROOT, available_days, read_manifest, snapshot_path, span,
    verify_integrity, write_snapshot,
)

#: 东财源礼貌限速，禁高并发。`diag_financial_lookahead.py` 实测同一量级。
SLEEP_BETWEEN_CALLS = 0.15

#: 每抓多少只 flush 一次。中断时最多损失这一批的进度。
FLUSH_EVERY = 100

#: 每只票保留最近 N 个报告期。**这不是「少存一点」，是量出来的必须。**
#:
#: 实测（5 只样本外推全池 5460 只，每只 12 期 = 110 MB/天）：
#: - 存全部 117 期 = 266 MB/天 = **65 GB/年** → 磁盘可用 112 GB，**跑不满一年**
#: - 存最近 12 期   = 110 MB/天 = **27 GB/年** → 约 4 年余量
#:
#: 注意省下来的比行数比例少：早期报告期本已大面积稀疏、压成近零字节，
#: **最近的 12 期才是贵的那些**。所以「按比例砍」会严重低估收益。
#:
#: 27 GB/年仍不是小数目。触发复审的条件写在 `MIN_FREE_GB` 里：
#: 水位告警时该重新考虑分档存储（全部列每周存一次、常用列每天存一次），
#: 而不是等磁盘写满才发现。
#:
#: ## 为什么丢掉的是「不可能被读到」的那部分
#:
#: `vintage.pit_frame(as_of)` 在没有 ≤ as_of 的快照时**报错**。
#: 所以本模块在结构上**只能回答今天之后的问题**——
#: 2026-10-14 之前的任何一天都没有快照，2005 年的年报不可能被用来
#: 为某个历史日期构造 PIT 帧。存着它没有读者。
#:
#: ## 为什么 12 期够
#:
#: - 任意未来时点的 PIT 帧取的是**当时已知的最新一份**报告，最多落后 4 个季度；
#: - 重述集中在披露后一年内，3 年窗口足以观测完整的修订轨迹；
#: - 想研究「某份年报刚披露时是什么样」，需要的是**披露后**的快照，
#:   那时它正好在窗口内。
#:
#: ## 列一律不裁
#:
#: 实测 141 列里 27 列在这 5 只上全空，但全空列在 parquet 里几乎不占字节
#: （object 类合计仅占 0.5%）。**成本在行数上，不在列数上**，
#: 所以砍列省不到东西，只会永久丢失字段。
KEEP_PERIODS = 12

#: 磁盘余量低于这个数就**停止采集**（不删已有数据，只是不再新增）。
#: 写满磁盘会连累 `run_daily` 和整个仓库——采集的失败必须**可见但无害**，
#: 而「把盘写满」既不无害也不可见。
MIN_FREE_GB = 20

#: 一只票在一个报告期上**只应有一行**。重复行只有两个来源，都不是好事：
#: 续跑失效（同一只被抓了两遍），或源站自身重复。两种都让快照失去证据价值。
ROW_KEY = ["SECURITY_CODE", "REPORT_DATE"]

DAILY_DIR = Path("data/master/daily")


def universe() -> dict[str, str]:
    """平台真实股票池：`裸 6 位代码 -> 东财 SECUCODE`。

    **返回字典而不是列表，因为「同一个代码」在两个地方格式不同：**

    | 用途 | 格式 | 来源 |
    |---|---|---|
    | 请求参数 `SECUCODE` | `"000001.SZ"`（带后缀） | akshare 默认值就是 `301389.SZ` |
    | 帧里的 `SECURITY_CODE` | `"000001"`（裸 6 位） | 东财返回 |

    源站的 filter 是 `(SECUCODE="{symbol}")`，**必须带后缀**；
    传裸代码实测返回 0 行。而日线文件名是 `000001.SZ.parquet`，
    `Path.stem` 恰好给出 `000001.SZ`。

    初版只维护一个列表，于是两头都对不上：拿去请求（首版带后缀，能通）
    和拿去判续跑（首版也带后缀，对不上裸代码帧）**用的是同一个串**——
    结果 `c.zfill(6)` 对 9 位串是空操作，续跑每轮都失效，
    且静默写出行数翻倍的快照。

    ## 单射校验
    6 位代码跨所唯一是事实，不是保证。两个不同 stem 剥成同一个键时必须炸，
    而不是让两所的票挤进同一个身份里——丢掉哪只取决于排序，不可复现。
    """
    out: dict[str, str] = {}
    for stem in sorted(p.stem for p in DAILY_DIR.glob("*.parquet")):
        code = stem.split(".", 1)[0]
        if out.setdefault(code, stem) != stem:
            raise ValueError(
                f"去掉交易所后缀后出现重码：{out[code]} 与 {stem} 都映射到 {code}。"
                f"不要在这里猜，保留后缀当身份。")
    return out


def _fetch(secucode: str):
    """**参数必须是带交易所后缀的 SECUCODE。**

    传裸代码源站返回空帧（实测 0 行）——而 `main()` 的门只检查
    「一只都没抓到」，所以它会一路跑到最后才失败。
    """
    import akshare as ak
    try:
        return ak.stock_financial_analysis_indicator_em(
            symbol=secucode, indicator="按报告期")
    except Exception:
        return None


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """数值列降为 float32。

    **不是精度妥协**：财务比率本来就只有 7 位有效数字，float32 给足。
    而行数 × 列数 × 8 字节直接决定一年要落多少盘——存不下就只好少存，
    少存就是永久损失。所以在这里省。
    """
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == "float64":
            out[c] = out[c].astype("float32")
    return out


def keep_recent(df: pd.DataFrame, keep: int = KEEP_PERIODS) -> pd.DataFrame:
    """每只票只保留最近 `keep` 个报告期。

    **按 `REPORT_DATE` 降序取前 N，逐票独立**——不能对整帧取前 N 行，
    否则先抓到的票会吃掉配额（`universe()` 按代码排序，等于按代码截断）。

    取不满 `keep` 的票**原样保留**：新股、科创板次新股本来就没有那么长的历史，
    为了凑数去别处补行才是真正的造假。
    """
    if keep is None or keep <= 0 or df.empty:
        return df
    if "REPORT_DATE" not in df.columns:
        return df
    key = df[["SECURITY_CODE", "REPORT_DATE"]].copy()
    key["_ord"] = key["REPORT_DATE"].astype(str)
    ordered = key.sort_values(["SECURITY_CODE", "_ord"], ascending=[True, False])
    # `sort_values` 只保留索引标签，**行序被打乱**。`cumcount()` 返回的 Series
    # 内部顺序跟的是那个乱序，直接 `.to_numpy()` 当掩码就是**行与掩码错位**——
    # 而且错位后每行仍是某个合法报告期，行数也对，**没有任何异常可查**。
    # 必须 reindex 回原始行序。
    rank = ordered.groupby("SECURITY_CODE").cumcount().reindex(key.index)
    return df[(rank < keep).to_numpy()].reset_index(drop=True)


def _normalize_code(s) -> pd.Series:
    """代码统一成 6 位零填充字符串。

    两处调用必须走同一个函数：`universe()` 给的代码和源站给的
    `SECURITY_CODE` 一旦落在不同键空间，续跑就静默失效。
    """
    return s.astype(str).str.strip().str.zfill(6)


def dedupe_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """`(SECURITY_CODE, REPORT_DATE)` 去重，**保留最后一次**。

    ## 为什么 `keep="last"`

    重跑同一只票时，源站可能在两次调用之间刷新过同一行。
    后写的更接近「刚刚源站在服务什么」，所以后写胜出。
    这不是「用新覆盖旧的历史」——两者都来自**当天**，
    而「跨天改历史」是本模块第一号禁令，`write_snapshot` 层面已经堵死。

    ## 返回被丢掉的行数

    这个数必须能被看见。首版把重复行直接 concat 过去，
    于是 585 行变 1170 行，而日志里「续跑：已有 5 只」照常打印——
    **一个不会报错的续跑等于没有续跑**。
    """
    if df.empty or not set(ROW_KEY) <= set(df.columns):
        return df, 0
    out = df.drop_duplicates(subset=ROW_KEY, keep="last")
    return out.reset_index(drop=True), int(len(df) - len(out))


def _merge(have: pd.DataFrame, chunks: list[pd.DataFrame]) -> pd.DataFrame:
    """合成当天的完整分区：已落盘的 + 本次抓到的，然后去重。"""
    parts = [p for p in ([have] + chunks) if p is not None and not p.empty]
    if not parts:
        return pd.DataFrame()
    return dedupe_rows(pd.concat(parts, ignore_index=True))[0]


def collect(pool: dict[str, str], root: Path, day: str,
            flush_every: int = FLUSH_EVERY,
            sleep: float = SLEEP_BETWEEN_CALLS,
            keep: int = KEEP_PERIODS) -> dict:
    """抓一轮并落盘。**只写 `day` 那一份分区。**

    `pool` 是 `universe()` 的 `{裸代码: SECUCODE}`——请求用后者，身份用前者。

    ## 两种重复必须分开记账

    | 类型 | 含义 | 处置 |
    |---|---|---|
    | **跨调用重复** | 同一只票被抓了两遍（续跑失效） | 静默去重 + 计数上报 |
    | **单次调用内重复** | 源站对同一报告期返回多行 | **拒绝登记 manifest** |

    首版把两者混为一谈，在 `main` 里放了一道「去重后仍有重复就拒绝」的闸——
    而 `collect` 已经先去过重了，**那道闸永远不会触发**，
    于是它看起来像一道安全网，实际是死代码。
    门必须开在能看见问题的那个位置，也就是**抓回来的那一帧本身**。
    """
    part = snapshot_path(root, day)
    part.parent.mkdir(parents=True, exist_ok=True)
    have = pd.DataFrame()
    dup_dropped = 0
    todo = dict(pool)
    if part.is_file():                       # 续跑：先读回已完成的
        have = pd.read_parquet(part)
        if "SECURITY_CODE" in have.columns:
            # 已落盘的那份自己也先去重，否则历史重复会被一直继承下去
            have, dup_dropped = dedupe_rows(have)
            done = set(_normalize_code(have["SECURITY_CODE"]))
            # ← 判据两边都是**裸代码**。这是首版栽的地方。
            todo = {c: s for c, s in todo.items() if c not in done}
            print(f"  续跑：已有 {len(done)} 只，本次需抓 {len(todo)} 只"
                  + (f"，历史重复行已清 {dup_dropped} 条" if dup_dropped else ""),
                  flush=True)

    fetched, failed, chunks, source_dup = [], [], [], {}
    total = len(todo)
    t0 = time.time()
    for i, (code, secucode) in enumerate(todo.items(), 1):
        df = _fetch(secucode)
        if df is None or df.empty:
            failed.append(code)
        else:
            d = df.copy()
            # 身份一律归一到裸代码，请求格式不留在数据里
            d["SECURITY_CODE"] = _normalize_code(d["SECURITY_CODE"])
            # **在去重之前**查：这是唯一能看见源站自身重复的时刻
            if set(ROW_KEY) <= set(d.columns):
                n = int(d.duplicated(subset=ROW_KEY).sum())
                if n:
                    source_dup[code] = n
            # 保留窗口必须在去重**之后**、落盘之前。
            # 反过来的话：117 期里有重复时，先截 12 期会截到重复行，
            # 去重后不足 12 期——**窗口大小会被数据质量悄悄改变**。
            d = dedupe_rows(_coerce_numeric(d))[0]
            fetched.append(keep_recent(d, keep))
        if fetched and len(fetched) % flush_every == 0:
            chunks.append(pd.concat(fetched, ignore_index=True))
            fetched = []
            merged = _merge(have, chunks)
            merged.to_parquet(part, index=False)
            el = time.time() - t0
            print(f"  {i}/{total}  已落盘 {len(merged)} 行  "
                  f"{el/60:.1f} 分钟  预计剩余 "
                  f"{el/i*(total-i)/60:.0f} 分钟", flush=True)
        time.sleep(sleep)

    if fetched:
        chunks.append(pd.concat(fetched, ignore_index=True))
    raw_n = sum(len(c) for c in chunks) + len(have)
    full = _merge(have, chunks)
    dup_dropped += max(raw_n - len(full), 0)
    # 函数收尾也要落盘。否则跑不满一批的试跑（`--max-codes 50`）结果全丢，
    # 而 `flush_every` 只有在攒够数量时才触发——**试跑正是最容易丢的那一档**。
    if not full.empty:
        full.to_parquet(part, index=False)
    return {"day": day, "df": full, "failed": failed,
            "n_codes": total, "dup_dropped": dup_dropped,
            "source_dup": source_dup, "seconds": time.time() - t0}


def main() -> int:
    free_gb = shutil.disk_usage(Path.cwd()).free / 1024 ** 3
    if free_gb < MIN_FREE_GB:
        print(f"❌ 磁盘余量 {free_gb:.1f} GB < {MIN_FREE_GB} GB —— "
              f"**停止采集，不删任何已有数据**。")
        print(f"   写满磁盘会连累 run_daily 和整个仓库；"
              f"缺一天 vintage 的损失远小于这一条。")
        return 4

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=VINTAGE_ROOT)
    ap.add_argument("--date", default=None, help="默认今天")
    ap.add_argument("--max-codes", type=int, default=None,
                    help="只抓前 N 只（试跑用）")
    ap.add_argument("--sample", action="store_true",
                    help="只抓少量样本，用于验证链路")
    ap.add_argument("--skip", default="",
                    help="确认取不到的代码：逗号串，或一个每行一个代码的文件。"
                         "**必须同时给 --skip-reason**")
    ap.add_argument("--skip-reason", default="",
                    help="跳过原因，会写进 _skipped.json 与 manifest 邻接目录")
    ap.add_argument("--ensure", action="store_true",
                    help="今天已登记就直接退出（0）。**补跑作业必须带这个**，"
                         "否则重复触发会白跑 45 分钟")
    args = ap.parse_args()

    day = args.date or pd.Timestamp.today().strftime("%Y-%m-%d")

    # ── 幂等闸：今天已经登记过就什么都不做 ──
    # 日历触发**不会补跑**：机器在触发时刻睡着或关机，那天就永久错过了。
    # 而「今天不落盘就永久丢失」是本模块存在的唯一理由，
    # 所以补跑必须是显式的一等公民——同一天挂多个触发点，
    # 由这一行把重复的变成零成本的空操作。
    if args.ensure:
        for e in read_manifest(args.root):
            if e.get("snapshot_date") == day:
                print(f"{day} 已登记（{e['n_rows']} 行），跳过。")
                return 0

    pool = universe()

    # 跳过名单必须带原因。跳过本身会**永久造成一个洞**，
    # 而 vintage 恰恰是靠「每天都是完整的」才成立的。
    # 没有原因记录的跳过，等于让未来的自己对着一个洞猜原因。
    skip: set[str] = set()
    if args.skip:
        if not args.skip_reason:
            print("❌ --skip 必须同时给 --skip-reason")
            return 1
        src = (Path(args.skip) if Path(args.skip).is_file()
               else None)
        skip = set(
            (src.read_text(encoding="utf-8").split() if src
             else args.skip.split(",")))
        skip = {s.split(".", 1)[0].strip().zfill(6) for s in skip if s.strip()}
        unknown = sorted(skip - set(pool))
        if unknown:
            print(f"❌ --skip 里有 {len(unknown)} 个不在股票池中的代码："
                  f"{unknown[:8]}{' …' if len(unknown) > 8 else ''}")
            return 1
        pool = {c: s for c, s in pool.items() if c not in skip}
        print(f"跳过 {len(skip)} 只（{args.skip_reason}）")

    if args.sample:
        pool = dict(list(pool.items())[:5])
    elif args.max_codes:
        pool = dict(list(pool.items())[:args.max_codes])

    partial = bool(args.sample or args.max_codes)
    print(f"采集 {day} 的财务 vintage：{len(pool)} 只"
          + ("（**试跑：不会登记 manifest**）" if partial else ""), flush=True)
    res = collect(pool, args.root, day)
    df = res["df"]

    if df.empty:
        print("⚠️ 一只都没抓到——不写 manifest（半份快照会冒充完整快照）")
        return 1

    # 试跑（`--sample` / `--max-codes`）**不许**登记 manifest。
    # 首版在这里栽了：试跑抓满 5 只 → 非空帧 → 走完整路径登记成功，
    # manifest 里留下一条「2026-10-14 采集完成」的记录，
    # 而真实股票池是 5460 只。
    # **「成功」不等于「完整」**——而代码只检查了前者。
    if partial:
        print(f"  试跑：{len(pool)} 只已落盘到 {args.root}，**不登记 manifest**。")
        return 0

    full_pool = set(universe()) - skip
    got = set(df["SECURITY_CODE"].astype(str).str.zfill(6))
    if got != full_pool:
        missing = sorted(full_pool - got)
        print(f"❌ 覆盖 {len(got)}/{len(full_pool)} 只，缺 {len(missing)} 只"
              f"——**这不是完整快照**，不登记 manifest。")
        print(f"     缺：{missing[:8]}{' …' if len(missing) > 8 else ''}")
        print(f"     若确认这些票确实取不到（退市/长期停牌/源站无数据），"
              f"改用 `--skip` 并给 --skip-reason。")
        return 3

    # 源站对同一报告期返回多行 = 源坏了，不是采集器坏了。
    # 这不是能默默带过的瑕疵：行数、IC 权重、截面分位数全都会被污染，
    # 而且**永久**污染——vintage 不能重抓那一天。
    if res["source_dup"]:
        n = sum(res["source_dup"].values())
        print(f"❌ 源站返回了 {n} 行重复的 (SECURITY_CODE, REPORT_DATE)，"
              f"涉及 {len(res['source_dup'])} 只。不登记 manifest："
              f"**这一份一旦记进去就成了伪证据。**")
        for code, k in list(res["source_dup"].items())[:5]:
            print(f"     {code}  重复 {k} 行")
        print(f"     …共 {len(res['source_dup'])} 只")
        return 2

    if res["dup_dropped"]:
        print(f"  ⚠️ 丢弃重复行 {res['dup_dropped']} 条（续跑失效，已计入 dup_dropped）")

    if skip:
        (Path(args.root) / f"date={day}" / "_skipped.json").write_text(
            json.dumps({"day": day, "reason": args.skip_reason,
                        "codes": sorted(skip)}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"  跳过记录已写出（{len(skip)} 只，原因：{args.skip_reason}）")

    # 只有整轮抓完才登记 manifest。中途失败的那几只也要记进去，
    # 否则将来没人知道那天缺了谁。
    write_snapshot(args.root, day, df)
    print(f"\n  {day}: {len(df)} 行 / {df['SECURITY_CODE'].nunique()} 只 / "
          f"{df.shape[1]} 列")
    print(f"  耗时 {res['seconds']/60:.1f} 分钟，失败 {len(res['failed'])} 只")
    if res["failed"]:
        (Path(args.root) / f"date={day}" / "_failed.json").write_text(
            json.dumps(sorted(res["failed"]), ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"  失败清单已写出（{len(res['failed'])} 只）")

    s = span(args.root)
    print(f"\n  vintage 覆盖：{s['first']} ~ {s['last']}，"
          f"{s['n_days']}/{s['calendar_days']} 天有快照，"
          f"缺口 {len(s['gaps'])} 处")
    print(f"  ⚠️ 攒够之前**这不是 PIT**，只是 PIT 的原材料。"
          f"可回答窗口的上限是 {s['first']} 之后。")

    rep = verify_integrity(args.root)
    print(f"  完整性：{'OK' if rep['ok'] else '异常'}（核对 {rep['checked']} 份）")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
