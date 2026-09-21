#!/usr/bin/env bash
# ============================================================================
# sync-qwenpaw-skills.sh — QwenPaw 技能/人格同步唯一实现
# ============================================================================
# 供 deploy/{update,deploy,full-deploy}.sh 调用。禁止在调用方再写一份
# 「curl 127.0.0.1:8088 + bash quantbot_init」——Web 一键更新跑在 bridge
# 网络的 updater 容器里，127.0.0.1 不是宿主，会导致「升级成功但技能未同步」。
#
# 硬性规则：探活与 quantbot_init 一律经 docker exec 进 qwenpaw 容器执行
# （容器内 127.0.0.1:8088 才是 QwenPaw 自身；scripts/skills 已 bind mount）。
#
# 用法：
#   bash deploy/sync-qwenpaw-skills.sh [--step-label "5/5"]
# 环境：
#   QUANTMIND_PROJECT_DIR / PROJECT_DIR  项目根（默认本脚本上一级）
#   QUANTMIND_SKIP_SKILLS=true          跳过
#   QWENPAW_AGENT_ID                    默认 default
# 退出码：永远 0（失败只告警，不阻断部署主流程）
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${QUANTMIND_PROJECT_DIR:-${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
STEP_LABEL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --step-label) STEP_LABEL="${2:-}"; shift 2 ;;
        *) shift ;;
    esac
done
PREFIX="${STEP_LABEL:+$STEP_LABEL }"

log() { printf '[sync-qwenpaw] %s\n' "$*"; }

notify() {
    local level="${1:-info}" title="${2:-}" message="${3:-}"
    local helper="$PROJECT_DIR/deploy/notify-event.sh"
    if [[ -f "$helper" ]]; then
        bash "$helper" "$level" "$title" "$message" >/dev/null 2>&1 || true
    else
        log "事件留痕跳过（缺 $helper）：[$level] $title"
    fi
}

if [[ "${QUANTMIND_SKIP_SKILLS:-false}" == "true" ]]; then
    log "${PREFIX}跳过 QwenPaw 技能与人格同步（QUANTMIND_SKIP_SKILLS=true）"
    exit 0
fi

log "${PREFIX}同步 QwenPaw 技能与人格（经 docker exec qwenpaw）"

if ! docker ps --format '{{.Names}}' | grep -qx qwenpaw; then
    log '  qwenpaw 未运行，跳过（启动后手动：bash scripts/quantbot_init.sh）'
    notify warning 'QwenPaw 技能/人格未同步' 'qwenpaw 容器未运行'
    exit 0
fi

# 探活：只认容器内环回，不依赖宿主端口映射（updater / 防火墙都不影响）。
ready=false
for attempt in $(seq 1 30); do
    if docker exec qwenpaw curl --fail --silent --max-time 5 \
        http://127.0.0.1:8088/health >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 2
done
if [[ "$ready" != "true" ]]; then
    log '  qwenpaw 容器内 /health 60s 未就绪，跳过（稍后手动：bash scripts/quantbot_init.sh）'
    notify warning 'QwenPaw 技能/人格未同步' 'qwenpaw 容器内 /health 60s 内未就绪'
    exit 0
fi

sync_out=""
if ! sync_out="$(docker exec \
    -e QWENPAW_BASE_URL=http://127.0.0.1:8088 \
    -e QWENPAW_AGENT_ID="${QWENPAW_AGENT_ID:-default}" \
    qwenpaw bash /app/scripts/quantbot_init.sh 2>&1)"; then
    printf '%s\n' "$sync_out" | tail -20
    log '  QwenPaw 技能/人格同步失败（不阻断；稍后手动：bash scripts/quantbot_init.sh）'
    notify warning 'QwenPaw 技能/人格同步失败' \
        "$(printf '%s' "$sync_out" | tail -5 | tr '\n' ' ')"
    exit 0
fi

printf '%s\n' "$sync_out" | tail -5
docker restart qwenpaw >/dev/null
sleep 5
stat="$(docker exec qwenpaw qwenpaw skills list 2>/dev/null | tail -1 || true)"
log "  技能与人格同步完成：${stat:-状态未知，请手动确认（docker exec qwenpaw qwenpaw skills list）}"
notify info 'QwenPaw 技能/人格同步完成' "${stat:-状态未知}"
exit 0
