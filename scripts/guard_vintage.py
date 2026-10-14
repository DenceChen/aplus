"""vintage 守卫：检查当天快照是否已登记，缺则自动补跑。

launchd: com.aplus.guardvintage

## 为什么必须是独立作业，不能挂在 `guard_daily` 里

`guard_daily` 负责给 `run_daily` 补跑。若把 vintage 也塞进去，
就建立了「run_daily 失败 → vintage 断」这条依赖——而采集断一天是
**永久损失**，代价与 `run_daily` 失败完全不对等。

所以：**两个守卫各自独立，谁也拖不垮谁。**

## 为什么需要它，而不是「22:30 跑一次就完了」

launchd 的日历触发**不补跑**：机器在触发时刻睡着或关机，那天就永久错过。
而「今天不落盘，源站那天服务过的原始值永久丢失」——
**没有任何补救手段能把它找回来**，这是本项目唯一一个不可逆的任务。

采集器自带 `--ensure`（重复触发是空操作），
所以守卫「今天已登记就跳过」的判据让一天被触发两次也只付一次网络往返。

## 它**不会**去补历史缺口

源站只服务当前值。今天去抓，拿到的永远是今天的数；
把它标成 10-13 的快照写进 manifest，就是一条**伪造的证据**——
「10-13 源站服务的是这个」，而它明明来自 10-14。

**缺口是已知的损失，伪造是未知的污染。** 后者更坏：
它会让将来的 PIT 判决建立在一个从不存在的时间切片上，
而且**表面上完全正常**。所以历史缺口只如实报，不假装补上。
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aplus.vintage import VINTAGE_ROOT, span  # noqa: E402


def registered_days(root: Path = VINTAGE_ROOT) -> set[str]:
    """**已经在 manifest 里登记过的日期**。

    注意判据用 manifest 而不是「分区文件在不在」：
    文件存在只说明写过，manifest 才有条目才说明**整轮抓完了**。
    半份快照冒充完整快照，比没有更坏（见采集器 `main()` 的闸）。

    ## 坏行必须跳过而不是抛异常

    manifest 是逐行追加的 jsonl，可能因为断电/并发写而留下半行。
    若这里抛异常，**守卫就等于没有**——而它抛异常的方式
    （launchd 记一条 stderr、退出码非 0）极容易被忽略，
    然后采集就静默地一天天漏下去。
    """
    p = Path(root) / "_manifest.jsonl"
    if not p.is_file():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            day = json.loads(line).get("snapshot_date")
        except Exception:
            continue                      # 半行/坏行：跳过，不崩
        if day:
            out.add(day)
    return out


def days_missed(root: Path = VINTAGE_ROOT, today: str | None = None) -> list[str]:
    """从第一天快照到今天之间，**缺登记的日期**。

    只查 `[first, today]`，不查未来——今天的快照要等 22:30 才该出现，
    而这个守卫自己在 23:30 跑，所以今天已由 `--ensure` 覆盖。

    周末/节假日**不算缺**：源站在非交易日同样服务「当前值」，
    而 PIT 关心的是「某个信号日当时看到什么」。
    非交易日要不要落盘，是另一个还没定的问题（见账本第 80 条的 open）。
    """
    days = registered_days(root)
    days.discard(None)
    if not days:
        return []
    today = today or date.today().isoformat()
    first = min(days)
    out, cur = [], _next(first)
    while cur <= today:
        if cur not in days:
            out.append(cur)
        cur = _next(cur)
    return out


def _next(day: str) -> str:
    from datetime import datetime, timedelta
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime(
        "%Y-%m-%d")


def main() -> int:
    # **根目录显式传下去**：`def f(root=VINTAGE_ROOT)` 的默认值在定义时绑定，
    # 改模块级路径对已定义好的默认参数**不起作用**——
    # 运维热改和测试替身都会踩到，且行为与预期不符。
    root = VINTAGE_ROOT
    today = date.today().isoformat()
    if today in registered_days(root):
        print(f"vintage {today} 已登记，无需补跑")
    else:
        print(f"vintage {today} 未登记 → 立即采集（只抓今天）")
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" /
                                 "collect_financial_vintage.py"), "--ensure"],
            cwd=ROOT, capture_output=True, text=True, timeout=5400)
        print(r.stdout[-2000:])
        if r.returncode != 0:
            print(r.stderr[-2000:])
            raise SystemExit(r.returncode)

    s = span(root)
    missed = days_missed(root)
    print(f"  覆盖 {s['first']} ~ {s['last']}，{s['n_days']}/{s['calendar_days']} 天")
    if missed:
        print(f"  ⚠️ {len(missed)} 天无登记：{missed[:10]}"
              f"{' …' if len(missed) > 10 else ''}")
        print("     **这些天永久丢失，补不回来**：源站只服务当前值，"
              "今天抓到的永远是今天的数。")
        print("     所以守卫**不会**去补历史——把今天的值标成过去的日期写进 "
              "manifest，那是伪造证据，比一个已知的缺口坏得多。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())