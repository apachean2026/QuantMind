#!/usr/bin/env bash
# ============================================================================
# notify-event.sh — 部署/同步结果统一留痕入口
# ============================================================================
# 用途：把「QwenPaw 技能/人格同步」等非致命结果同时写到两处，避免出现
#       「升级显示成功、实际没刷」却事后无从追溯的情况：
#         1. $PROJECT_DIR/data/update.log —— 宿主机直接可看，不依赖 DB
#         2. system_events 表          —— 管理后台「最近事件」可见
#
# 唯一实现，供 deploy/{update,deploy,full-deploy}.sh 共用（与 req-fingerprint.sh
# 同属「跨脚本共用 helper」，避免口径漂移）。
#
# 用法：
#   bash deploy/notify-event.sh <level> <title> [message]
#     level: info | warning | error | critical
#
# 约定：本脚本永远以 0 退出 —— 留痕失败绝不能反过来影响部署主流程。
# ============================================================================
set -uo pipefail

PROJECT_DIR="${QUANTMIND_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

LEVEL="${1:-info}"
TITLE="${2:-}"
MESSAGE="${3:-}"

if [[ -z "$TITLE" ]]; then
    echo "用法: bash deploy/notify-event.sh <level> <title> [message]" >&2
    exit 0
fi

case "$LEVEL" in
    info|warning|error|critical) ;;
    *) LEVEL=info ;;
esac

# 1) 落盘 data/update.log（宿主机排障用；data/ 不存在时静默跳过）
mkdir -p "$PROJECT_DIR/data" 2>/dev/null || true
printf '[%s] [%s] %s %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$LEVEL" "$TITLE" "$MESSAGE" \
    >> "$PROJECT_DIR/data/update.log" 2>/dev/null || true

# 2) 写 system_events（管理后台展示）。DB 未就绪 / 无 docker 时静默跳过。
sql_escape() {
    printf '%s' "$1" | sed "s/'/''/g" | head -c 4000
}
pg_user="$(grep -E '^[[:space:]]*DB_USER=' "$PROJECT_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"' " || true)"
pg_user="${pg_user:-quantmind}"
docker exec -e PGUSER="$pg_user" quantmind-db psql -U "$pg_user" -v ON_ERROR_STOP=0 \
    -c "INSERT INTO system_events (event_type, level, source, title, message) VALUES ('system_update', '$(sql_escape "$LEVEL")', 'updater', '$(sql_escape "$TITLE")', '$(sql_escape "$MESSAGE")')" \
    >/dev/null 2>&1 || true

exit 0
