#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# 「导出静态页 + 推到阿里云」的**唯一入口**（一条命令可重放）。
#
# 用户 2026-10-21 原话：「静态页面定期推送服务器就行」。
#
# 🚨 **导出失败就不推**（&& 链）⇒ 宁可服务器上留着昨天的页面，
#    也不推一个刚生成的空页面/半成品页面。
# 🚨 **不许后台化**：后台化会制造「已完成」的假象（本仓第 8 层）。
#    本脚本同步跑完再退出；退出码 = 最后一条命令的退出码。
#
# 用法：
#     bash scripts/export_and_deploy_progress_page.sh
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="/Users/airboom/opencode/aplus"
cd "${ROOT}"

.venv/bin/python scripts/export_progress_page.py
bash scripts/deploy_progress_page.sh
