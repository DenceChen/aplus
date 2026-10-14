"""同步流水线：多源容灾 + 交叉校验 + 隔离。

判定矩阵（README 第四节）：
    双源成功且校验通过 → ok       写主库
    仅单源成功           → degraded 写主库（可信度降级，cross_checked=False）
    双源成功但校验不过   → quarantined 隔离区，主库不动
    全部失败             → RuntimeError（上层重试/告警）

## 源优先级是「顺序」，不是「谁先成功」

`self.sources` 的顺序即优先级：`sources[0]` 是主源，其余是备源。
单源存活时分两种情况，调用方必须能分辨：

    存活的是主源   → 正常写库，reason 只报故障原因（现状不变）
    存活的是备源   → 降级源写入：照常写库（容灾语义），但 reason 必须写明
                     是哪条备源顶上的，且主库按保列合并（依赖 store 的
                     `_merge_preserving_fidelity`：缺列数据不得销毁已有完整列）

两种情况都写库，所以不存在「静默丢弃」；真正不写主库的只有隔离分支，
它带 reason 落隔离区，同样可追溯。
"""
from dataclasses import dataclass, field

from . import log as _log
from .crosscheck import compare_frames, sanity_check


def _emit(event: str, **fields) -> None:
    """结构化信号：仅在调用方已 `setup_logging` 时落盘。

    直接调 `get_logger` 会在未配置日志根时默认写仓库的 `data/logs`，
    单元测试跑一次就在真实数据目录里留一条假事件——那正是「可观测信号」
    最容易变成噪音的地方。这里读 `_log._ACTIVE_ROOT` 做守卫（同包内的约定，
    log.py 不在本模块的改动面内）。
    """
    if getattr(_log, "_ACTIVE_ROOT", None) is None:
        return
    _log.get_logger("pipeline").event(event, **fields)


@dataclass
class SyncResult:
    code: str
    status: str  # ok / degraded / quarantined
    sources_used: list = field(default_factory=list)
    degraded_from: list = field(default_factory=list)
    cross_checked: bool = False
    mismatch_ratio: float = 0.0
    reason: str = ""
    source_errors: dict = field(default_factory=dict)
    degraded_source_write: bool = False  # 写入主库的是备源（主源失败）


class SyncPipeline:
    def __init__(self, sources, store, mismatch_threshold: float = 0.02, coverage: str = "strict"):
        self.sources = list(sources)
        self.store = store
        self.mismatch_threshold = mismatch_threshold
        self.coverage = coverage

    def sync_daily(self, code: str, start: str, end: str) -> SyncResult:
        frames = {}
        errors = {}
        for src in self.sources:
            try:
                fetched = src.fetch_daily(code, start, end)
                if fetched is None or fetched.empty:
                    # 空结果（未上市/已退市/区间无数据）≠ 成功——按源失败处理，
                    # 否则空 parquet 会让每日增量的 date.max() 变 NaN 崩溃
                    errors[src.name] = "empty result (no data in range)"
                else:
                    frames[src.name] = fetched
            except Exception as exc:  # noqa: BLE001 — 任一源故障都必须被降级吸收
                errors[src.name] = repr(exc)

        if not frames:
            raise RuntimeError(f"all sources failed for {code}: {errors}")

        degraded_from = [s.name for s in self.sources if s.name in errors]
        names = list(frames)  # 采集顺序即优先级，故 names[0] 是优先级最高的存活源
        primary = frames[names[0]]

        sanity_issues = sanity_check(primary)
        if sanity_issues:
            reason = f"sanity check failed: {'; '.join(sanity_issues)}"
            self.store.quarantine_daily(code, primary, reason=reason)
            return SyncResult(code, "quarantined", names, degraded_from, False, reason=reason)

        if len(frames) >= 2:
            report = compare_frames(
                frames[names[0]], frames[names[1]],
                mismatch_threshold=self.mismatch_threshold, coverage=self.coverage,
            )
            if not report.passed:
                reason = f"cross-check failed: {report.summary()}"
                self.store.quarantine_daily(code, primary, reason=reason)
                return SyncResult(code, "quarantined", names, degraded_from, True, report.mismatch_ratio, reason, errors)
            self.store.upsert_daily(code, primary)
            return SyncResult(code, "ok", names, degraded_from, True, report.mismatch_ratio, source_errors=errors)

        # ── 单源存活：分清「主源存活」与「备源顶上」 ──────────────────────
        self.store.upsert_daily(code, primary)   # 保列由 store 保证：备源的窄列不得销毁主库完整列
        failed_text = "; ".join(f"{k}: {v[:80]}" for k, v in errors.items())
        primary_name = self.sources[0].name
        if names[0] == primary_name:
            # 存活的就是主源（只是后面的备源挂了）：保持现状，不谎称降级源写入
            return SyncResult(code, "degraded", names, degraded_from, False,
                              reason=failed_text, source_errors=errors)

        reason = (f"降级源写入: 备源 {names[0]} 顶上（主源 {primary_name} 失败）；"
                  f"写入按保列合并，主库已有完整列不被缺列数据销毁 | {failed_text}")
        _emit("degraded_source_write", code=code, source=names[0], primary=primary_name,
              columns=len(primary.columns), rows=len(primary), reason=failed_text)
        return SyncResult(code, "degraded", names, degraded_from, False,
                          reason=reason, source_errors=errors, degraded_source_write=True)
