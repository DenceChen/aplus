#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# 把 aplus 的**目标进度静态页**推到阿里云服务器（47.95.13.9）。
#
# 用户 2026-10-21 原话：「静态页面定期推送服务器就行」＋「我不在家也可以看进度」。
#
# 🚨 **这台机器 2026-11-08 到期**（试用一个月）⇒ 本脚本必须是
#    **「一条命令可重放」**的：跑一遍就把目录、文件、systemd 单元、冒烟全做完。
#
# 🚨 **不许碰这台机器上的另一个服务**（它有它自己的家目录、它自己的
#    systemd 单元、它自己的 venv）：
#    · 落点是**新目录** `/opt/aplus-progress/`
#    · 单元是**新的** `aplus-progress.service`（不 restart、不 enable 别的单元）
#    · 端口是 **8080**（不是 8000）
#    ⇒ 本文件里**不许**出现别的服务的目录名（有守卫逐字钉着）。
#
# 🚨🚨 **2026-10-21 23:1x：加一个「对外」单元 `aplus-progress-public`（80 端口）**
#    · **为什么加**：用户实测「http://47.95.13.9:8080/ 打不开」
#      ⇒ 实测：**80 端口在安全组里是放行的**（8080 不通、80 通 ⇒ 拦在阿里云安全组）
#      ⇒ 而 80 上**没有任何进程在听** ⇒ ⇒ 挂上去就通了 ⇒
#      ⇒ **用户不必去控制台放行 8080**（他只要点开就能看）。
#    · 80 是**特权端口** ⇒ 该单元以 root 跑；8080 那个仍以 admin 跑（两单元职责不同）。
#    · ⚠️ **两个单元都只读同一个目录** ⇒ 部署时**都要**装、**都要**冒烟。
#    ⇒ 本文件里**不许**出现别的服务的目录名（有守卫逐字钉着）。
#
# 🚨 **只传静态页**：那台机器只有 896MB 内存，aplus 的主库是 GB 级 ⇒
#    只 rsync `data/reports/static/`（几十 KB 的 HTML），**不传任何数据**。
#
# 🚨 **不许后台化**：后台化会让「已完成」变成假象（本仓第 8 层）。
#    本脚本**同步**跑完再退出；cron 里也只有它自己一条命令。
#
# 用法：
#     bash scripts/deploy_progress_page.sh
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="/Users/airboom/opencode/aplus"
STATIC_DIR="${ROOT}/data/reports/static"
UNIT_SRC="${ROOT}/deploy/aplus-progress.service"
UNIT_SRC_PUBLIC="${ROOT}/deploy/aplus-progress-public.service"

REMOTE_IP="47.95.13.9"
REMOTE_HOST="admin@${REMOTE_IP}"
REMOTE_DIR="/opt/aplus-progress"
REMOTE_UNIT="/etc/systemd/system/aplus-progress.service"
UNIT_NAME="aplus-progress.service"
PORT="8080"
# 🚨 **对外的那个**（80）：安全组放行了 80、没放行 8080 ⇒ 用户能打开的是这个。
REMOTE_UNIT_PUBLIC="/etc/systemd/system/aplus-progress-public.service"
UNIT_NAME_PUBLIC="aplus-progress-public.service"
PORT_PUBLIC="80"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy] ✗ %s\n' "$*" >&2; exit 1; }

# ── 0. 本机前置检查 ────────────────────────────────────────────────
[ -f "${STATIC_DIR}/index.html" ] || die \
  "本机没有 ${STATIC_DIR}/index.html ⇒ 先跑：.venv/bin/python scripts/export_progress_page.py"
[ -f "${UNIT_SRC}" ] || die "本机没有 systemd 单元 ${UNIT_SRC}"
[ -f "${UNIT_SRC_PUBLIC}" ] || die "本机没有 systemd 单元 ${UNIT_SRC_PUBLIC}"
command -v rsync >/dev/null 2>&1 || die "本机没有 rsync"

log "本机静态页：${STATIC_DIR}/index.html（$(wc -c < "${STATIC_DIR}/index.html" | tr -d ' ') 字节）"

# ── 1. 远端目录（独立目录，**不是**别人的家目录）──────────────────
log "远端建目录 ${REMOTE_DIR}"
ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "sudo mkdir -p '${REMOTE_DIR}' && sudo chown -R admin:admin '${REMOTE_DIR}'"

# ── 2. 推文件（只有静态页）────────────────────────────────────────
log "rsync → ${REMOTE_HOST}:${REMOTE_DIR}/"
# `--delete` 的目标是**我们自己这个独立目录**，删不到任何别人的东西。
rsync -az --delete "${STATIC_DIR}/" "${REMOTE_HOST}:${REMOTE_DIR}/"

# ── 3. systemd 单元（**新单元**）：8080 内部 + 80 对外 ─────────────
log "安装 systemd 单元 ${UNIT_NAME}（${PORT}）"
ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "sudo tee '${REMOTE_UNIT}' > /dev/null" < "${UNIT_SRC}"
log "安装 systemd 单元 ${UNIT_NAME_PUBLIC}（${PORT_PUBLIC} · 对外）"
ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "sudo tee '${REMOTE_UNIT_PUBLIC}' > /dev/null" < "${UNIT_SRC_PUBLIC}"
ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "sudo systemctl daemon-reload && sudo systemctl enable --now '${UNIT_NAME}' '${UNIT_NAME_PUBLIC}'"

# ── 4. 冒烟：先问「起来了吗」，再问「对不对」─────────────────────
# ⚠️ **不把管道放在被观测的命令上**（本仓第 8/107 层：管道吞退出码 ⇒ 假绿）。
#    下面每一条都是「取**值**再判」，不是「看退出码」⇒ 值本身才是判据。
log "冒烟：等 ${UNIT_NAME} 起来"
active="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "for i in \$(seq 1 20); do
     state=\$(systemctl is-active '${UNIT_NAME}' 2>/dev/null || true)
     if [ \"\${state}\" = active ]; then break; fi
     sleep 1
   done
   echo \"\${state}\"")"
log "systemctl is-active → ${active}"
[ "${active}" = "active" ] || die "冒烟失败：${UNIT_NAME} 不是 active（实得 ${active}）"

log "冒烟：HTTP 状态码"
code="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:${PORT}/' || true")"
log "http://127.0.0.1:${PORT}/ → ${code}"
[ "${code}" = "200" ] || die "冒烟失败：期望 200，实得 ${code}"

log "冒烟：对外端口 ${PORT_PUBLIC}（用户真正能打开的那个）"
# ⚠️ **两个端口都要冒烟**：8080 是内部/备用，**80 才是用户在外面访问的**。
#    ⇒ 2026-10-21 之前只冒烟 8080 ⇒ 于是「部署成功」与「用户打不开」**同时成立**。
pub_code="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:${PORT_PUBLIC}/' || true")"
log "http://127.0.0.1:${PORT_PUBLIC}/ → ${pub_code}"
[ "${pub_code}" = "200" ] || die "冒烟失败（对外 ${PORT_PUBLIC}）：期望 200，实得 ${pub_code}"
pub_active="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "systemctl is-active '${UNIT_NAME_PUBLIC}' 2>/dev/null || true")"
[ "${pub_active}" = "active" ] || die "冒烟失败：${UNIT_NAME_PUBLIC} 不是 active（实得 ${pub_active}）"

log "冒烟：页面内容（内联快照标记 + 快照时间）"
# ⚠️ 远端用 `case` 判，**不接管道** ⇒ 判的是内容本身，不是某个管道的退出码
marker="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "${REMOTE_HOST}" \
  "page=\$(curl -s 'http://127.0.0.1:${PORT}/' || true)
   case \"\${page}\" in
     *aplus-progress-snapshot*) m=1 ;;
     *) m=0 ;;
   esac
   case \"\${page}\" in
     *快照时间*) t=1 ;;
     *) t=0 ;;
   esac
   echo \"\${m}\${t}\"")"
log "内联快照标记=${marker:0:1} · 快照时间=${marker:1:1}"
[ "${marker:0:1}" = "1" ] || die "冒烟失败：页面里没有内联快照标记"
[ "${marker:1:1}" = "1" ] || die "冒烟失败：页面上没有快照时间"

log "✓ 静态页已上线：**http://${REMOTE_IP}/**（80 端口 · 对外 · 安全组已放行）"
log "  （内部/备用：http://${REMOTE_IP}:${PORT}/ · 8080 **未**在安全组放行 ⇒ 外网打不开）"
exit 0
