"""前瞻验证台账：唯一干净的晋级路径。

## 为什么必须是「注册」而不是「跑一次看看」

到这里为止，**平台上每一段可用行情都被我看过了**：

- 2019-01 ~ 2023-12：独立验证（反转方向的来源）
- 2024-10 ~ 2026-10：2 年赛马，看过三轮
- 2019-01 ~ 2026-10：全历史赛马，测试段与 2 年赛马重叠

再跑一次赛马，无论换多好的协议，都不是独立验证——
是**拿同一段数据再自证一次**。方向污染闸数不了这一种：
它数「跑了几组」，数不了「事先就知道该往哪猜」。

唯一干净的路是：**从今天起把候选冻结下来，让新产生的交易日去检验它们。**

## 三条硬规则

1. **注册即冻结**。注册之后不能删、不能改、不能加。
   要加新候选 = 开一份**新的**注册，从那天起单独计时。
   （否则「每次看到新结果就换一个候选」会伪装成前瞻验证。）
2. **窗口严格晚于注册日**，并留隔离带。注册日当天的数据我已经看得到。
3. **候选集越小，门槛越紧**。前瞻台账通常只有个位数候选，
   Bonferroni 会比 84 组赛马宽得多——这是优势，不是缺陷。

## 与七道闸的关系

前瞻验证**复用** `oos_decision` 的闸，不另造一套。
区别只在于窗口（`[注册日+隔离带, 最新]`）与候选池规模。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REGISTRY_NAME = "forward_registry.json"

# 隔离带：动量等 20 日窗口因子与注册日的数据仍有重叠
FORWARD_EMBARGO_DAYS = 2

# 测试段至少要装下几个调仓周期才允许给出 t（与 race 的 MIN_TEST_PERIODS 一致）
MIN_TEST_PERIODS = 3

# 冻结指纹的字段名。**不算进指纹本身**。
SEAL_FIELD = "sealed_sha256"


def days_to_verdict(rebalance_every) -> int:
    """攒够判决力需要多少个**交易日**。

    判决力以**独立调仓周期**计（`MIN_TEST_PERIODS`），
    K 日调仓的候选一个周期就是 K 个交易日——
    逐日 t 在低频下虚高 1.0~2.0 倍（见 `frequency_block_t_2019_2023.json`），
    所以低频的判决力天生更贵，代价是**等待更久**。

        K=5  → 15 个交易日（约 3 周）
        K=21 → 63 个交易日（约 3 个月）
        K=63 → 189 个交易日（约 9 个月）

    ## 这是「提速只能靠降低 K」那句话的全部数学

    降低 K 缩短的是**等待时间**。它**不会**削弱证据标准——
    需要的独立周期数恒为 `MIN_TEST_PERIODS`，不足就不给结论（`measurable=False`）。

    降 K 确实会让显著性**门槛数值**变松（同为 97 组比较）：
    周期数变多 → t 自由度变大 → 临界值下降（n=11 的 5.03 → n=150 的 3.55）。
    这是 t 分布的正确行为，不是放水：**检验的显著性水平 α 一分没变**，
    变的是同一 α 在不同样本量下对应的 t 阈值。
    同一段日历窗口里，K 小的一方能切出更多个**互不重叠**的块，
    证据是真的更多，不是同一份证据被重复数。

    两者由不同的函数管：`MIN_TEST_PERIODS` 管「够不够测」，
    `bonferroni_t` 管「测出来够不够显著」。物理上无法互相冒充。
    """
    k = max(int(rebalance_every or 1), 1)
    return MIN_TEST_PERIODS * k


def registry_path(reports_dir) -> Path:
    return Path(reports_dir) / REGISTRY_NAME


def _seal_payload(rec: dict) -> str:
    """被指纹覆盖的内容——除指纹字段外的**全部**字段。

    覆盖 `weights` 与 `rebalance_every` 是必须的：候选身份是三元组
    （`candidate_key`），改频率等于换成另一个策略，
    只校验权重等于放行「把月频候选偷偷改成日频去实测」。
    `note`/`evidence` 一并覆盖：它们是「为什么选这组」的凭据，
    事后再补一段「其实我早就知道」比改权重更致命。
    """
    body = {k: v for k, v in rec.items() if k != SEAL_FIELD}
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def compute_seal(rec: dict) -> str:
    """一份注册的内容指纹。同一份注册任何时候算出来都一样。"""
    return hashlib.sha256(_seal_payload(rec).encode("utf-8")).hexdigest()


def _batch_identity(registered_on: str, candidates: list) -> tuple:
    """批次的身份：`注册日 + 候选三元组集合`（**与顺序无关**）。

    幂等靠它判定「这是不是同一批」。
    换了任何一个候选、或换了注册日，就是**新的一批**，必须放行——
    批次不同意味着窗口起点不同，是一次独立的预注册。
    """
    from .evolve import candidate_key

    return (str(registered_on).strip(),
            tuple(sorted(candidate_key(c) for c in candidates or [])))


def registry_tamper_report(reports_dir) -> dict:
    """逐份核对冻结指纹。

    返回 `{"sealed": n, "unsealed": [...], "tampered": [...]}`，
    下标对应 `load_registry()` 里的顺序。

    ## 为什么必须区分「没封」与「验不过」

    2026-10-12 的前两批注册于指纹机制存在之前，**无法证明此后没被改过**。
    把它们报成「通过」等于替一件无法证明的事背书；
    而整个台账的价值就建立在「注册之后没人动过」上。
    所以 `unsealed` 单列一栏：如实说「这条我验不了」，
    既不冤枉它，也不替它背书。
    """
    rows = load_registry(reports_dir)
    sealed, unsealed, tampered = 0, [], []
    for i, rec in enumerate(rows):
        got = rec.get(SEAL_FIELD)
        if not got:
            unsealed.append(i)
        elif str(got) == compute_seal(rec):
            sealed += 1
        else:
            tampered.append(i)
    return {"sealed": sealed, "unsealed": unsealed, "tampered": tampered,
            "total": len(rows)}


def load_registry(reports_dir) -> list[dict]:
    """读注册台账。坏文件返回空列表——**不抛错**，
    但调用方必须把「空」理解成「没有前瞻验证在进行」，而不是「一切正常」。"""
    p = registry_path(reports_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def register(reports_dir, registered_on: str, candidates: list, note: str = "",
             evidence: str = "") -> dict:
    """登记一份**冻结**的候选集。返回登记的那一份。

    追加式：已有的登记一个字都不改。要加候选就再登记一次——
    「事后往里塞一个表现更好的」会把前瞻验证变成又一轮自证。

    ## 幂等：同一批跑第二遍不会插第二次

    注册脚本是要被人反复运行的（补跑、定时、手动重跑）。
    每次都追加一份的话，同一批候选会在台账里出现多次，
    候选数虚增 → **Bonferroni 门槛被自己抬高**，
    而台账看上去只是「多了一批」——没人说得清多出来的是什么。

    批次身份 = `注册日 + 候选三元组集合`（`_batch_identity`，与顺序无关）。
    命中已有批次就**原样返回、不落第二次**；
    换了候选集或换了日期则是新的一批，照常追加。

    ## 冻结：写下时就算指纹

    指纹（`sealed_sha256`）在**写入的同一刻**算好，不是事后补的——
    事后补的指纹只能证明「从补签那天起没变过」，
    而那正是它要防的事本身。
    """
    if not str(registered_on or "").strip():
        raise ValueError("注册日期不能为空：没有起始日的台账不叫前瞻")
    cands = dedupe(candidates)
    if not cands:
        raise ValueError("候选为空：登记一份空的台账等于没登记")
    rows = load_registry(reports_dir)

    ident = _batch_identity(registered_on, cands)
    for existing in rows:
        if _batch_identity(existing.get("registered_on") or "",
                           existing.get("candidates") or []) == ident:
            return existing                    # 同一批：不再插第二次

    rec = {"registered_on": str(registered_on),
           "note": note, "evidence": evidence,
           "candidates": cands}
    rec[SEAL_FIELD] = compute_seal(rec)
    rows.append(rec)
    p = registry_path(reports_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def dedupe(candidates) -> list:
    """按三元组去重。**校验由 `split_weights` 承担**，这里不重复一遍。

    为什么要在校验「写入之前」拦下 `rebalance_every=0` 的候选：
    这样的候选进了台账，之后每次前瞻验证都会拿它去算一个
    **永远算不出调仓日**的序列，结果是静默的全零。

    ## 为什么这里不再写一遍 `if every < 1`

    `split_weights` 已经有这条校验（`aplus/evolve.py`），而且它离
    调用点只有一行。同一个判断写两遍，20 行之后必然漂移——
    届时这里拦得住、别处拦不住，而两边的消息还不一样。
    本项目已经为「同一件事两个实现」付过两次代价
    （`_weights_path`、`_champion_health`）。

    证据：把这里的校验删掉后，`rebalance_every=0` 依然进不去台账
    （`tests/test_forward_registry.py::test_invalid_rebalance_rejected`），
    对应变异体被工具判为 **equivalent（变异体无效）**，不是「测试弱」。
    """
    from .evolve import candidate_key, split_weights

    seen, out = set(), []
    for c in candidates or []:
        _w, every, buf = split_weights(c)      # 校验在这里，不在下面
        k = candidate_key(c)
        if k in seen:
            continue
        seen.add(k)
        out.append({"weights": dict(c["weights"]),
                    "rebalance_every": every, "buffer_multiple": buf})
    return out


def forward_window(dates, registered_on: str,
                   embargo_days: int = FORWARD_EMBARGO_DAYS) -> list:
    """严格晚于注册日、再扣掉隔离带的日期。

    注册日当天的数据**已经**被看过了——今天出单的依据就是它。
    拿它当验证样本，等于用答案去答自己出的卷。
    """
    ds = sorted(d for d in (dates or []) if str(d) > str(registered_on))
    return ds[int(embargo_days):]


def needs_forward_panel(registered_on, latest) -> bool:
    """窗口里至少得有一天，才值得为它建面板。

    ## 为什么要有这个判断

    `build_cross_sections` 的区间是**半开**的 `[start, end)`，
    而面板起点取的是注册日。注册当天刚注册完、还没收盘时，
    `start == end == latest`，`build_panel` 会直接抛
    `start < end`。于是定时任务在窗口攒够之前**每天崩一次**，
    而没人盯着 stderr——直到某天它突然开始工作，没人知道之前空转了多久。

    这里返回 False 而不是让调用方撞异常：窗口为空是**正常状态**
    （第一批候选就是今天注册的），正常状态不该以异常的形式出现。
    """
    if not registered_on or not latest:
        return False
    return str(latest) > str(registered_on)


def candidate_periods(candidate: dict, window_days: int) -> int:
    every = max(int(candidate.get("rebalance_every") or 1), 1)
    return window_days // every


def forward_status(reports_dir, as_of: str, dates=None) -> dict:
    """每个注册候选攒到了多少**独立观测**，够不够测。

    够不够用 `MIN_TEST_PERIODS` 判。不够就明说不够——
    **不给一个看起来能用的 t 值**，那正是第 18 号缺陷
    （`n_ranked=0` 的 8 行）当初骗人的方式。
    """
    from datetime import date as _date

    from .evolve_race import bonferroni_t

    rows = load_registry(reports_dir)
    days = sorted(dates or [])
    out_regs, total = [], 0
    for rec in rows:
        reg_on = rec.get("registered_on") or ""
        win = forward_window(days, reg_on)
        raw = rec.get("candidates") or []
        cands = []
        for c in raw:
            periods = candidate_periods(c, len(win))
            every = int(c.get("rebalance_every") or 1)
            # Bonferroni 门槛按**本份注册的候选数**校正——
            # 每份注册是一次独立的预注册，候选池各自独立。
            #
            # 门槛同时依赖**样本量**（2026-10-14 修）：分块后的观测数就是
            # t 分布的自由度。同一份注册里候选数相同，但各候选的 K 不同、
            # 攒到的周期数不同，于是临界值不同：
            #
            #     97 组比较下   n=11 → 5.03     n=150 → 3.55     n=752 → 3.49
            #
            # 不可测时按 `MIN_TEST_PERIODS`（刚够测）给门槛，并标 `provisional`
            # ——窗口变长后这个数只会**下降**（观测变多、临界值变松）。
            n_for_crit = max(periods, MIN_TEST_PERIODS)
            t_crit = bonferroni_t(len(raw), df=n_for_crit - 1)
            cands.append({"weights": c.get("weights"),
                          "rebalance_every": every,
                          "buffer_multiple": c.get("buffer_multiple"),
                          "window_days": len(win),
                          "test_periods": periods,
                          "measurable": periods >= MIN_TEST_PERIODS,
                          "needed_periods": MIN_TEST_PERIODS,
                          # 判决时用多少个不重叠观测判显著性
                          "t_crit_n": n_for_crit,
                          # 攒够判决力还需多少个交易日（= MIN_TEST_PERIODS × K）
                          "days_to_verdict": days_to_verdict(every),
                          "t_crit": t_crit,
                          # 窗口还没攒够时这个门槛是暂定值，别当最终口径引用
                          "t_crit_provisional": periods < MIN_TEST_PERIODS})
        total += len(cands)
        out_regs.append({"registered_on": reg_on, "note": rec.get("note", ""),
                         "evidence": rec.get("evidence", ""),
                         "window_days": len(win),
                         "window_start": win[0] if win else None,
                         "window_end": win[-1] if win else None,
                         "candidates": cands})
    return {"as_of": as_of, "registrations": len(rows),
            "n_candidates": total, "registrations_detail": out_regs,
            # 取**最早**那一注册的窗口——它积累得最久，也最接近判决力的起点
            "days_in_window": max((r["window_days"] for r in out_regs), default=0),
            "days_since_registration": (
                (_date.fromisoformat(str(as_of)) - _date.fromisoformat(str(rows[0]["registered_on"]))).days
                if rows and rows[0].get("registered_on") and as_of else 0),
            "candidates": [c for r in out_regs for c in r["candidates"]]}


def merge_results(reports_dir, results: list) -> Path:
    """把前瞻评估结果追加落盘（同样追加式，不覆盖历史）。

    ## 空批次**不落盘**，但文件仍会被创建

    实测（2026-10-13）：窗口因隔离带仍有 0 天时，每跑一次就 append
    一个空列表，一天下来文件里堆了 5 个 `[]`。它们既不说明「跑过了
    但没数据」，也不说明「从没跑过」——只是噪声，而且会一直增长。

    窗口为空是**正常状态**（每天收盘后都这样，直到攒够隔离带），
    它属于 `--status-only` 与控制台输出，不属于结果台账。
    """
    p = Path(reports_dir) / "forward_results.json"
    old = []
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(old, list):
                old = []
        except (json.JSONDecodeError, OSError):
            old = []
    if results:
        old.append(results)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(old, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    return p