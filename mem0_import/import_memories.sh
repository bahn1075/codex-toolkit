#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
read -r -p 'Mem0로 가져올 디렉토리 경로: ' TARGET_DIR
TARGET_DIR=${TARGET_DIR/#\~/$HOME}

if [ ! -d "$TARGET_DIR" ]; then
  printf '디렉토리를 찾을 수 없습니다: %s\n' "$TARGET_DIR" >&2
  exit 2
fi

PYTHON=${MEM0_IMPORT_PYTHON:-}
if [ -z "$PYTHON" ]; then
  if [ -x "$HOME/.codex/toolkit/venv/bin/python" ]; then
    PYTHON="$HOME/.codex/toolkit/venv/bin/python"
  else
    PYTHON=python3
  fi
fi

LOG_DIR="$HOME/.codex/toolkit/mem0-sessions"
mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR"
STATUS_FILE=$(mktemp "$LOG_DIR/manual-import-status.XXXXXX")
LOG_FILE=$(mktemp "$LOG_DIR/manual-import-errors.XXXXXX")

nohup "$PYTHON" "$SCRIPT_DIR/import_memories.py" "$TARGET_DIR" "$@" \
  --status-file "$STATUS_FILE" --error-log "$LOG_FILE" \
  > /dev/null 2>>"$LOG_FILE" < /dev/null &
PID=$!
disown "$PID" 2>/dev/null || true

printf 'Mem0 import가 백그라운드에서 시작되었습니다.\n'
printf 'PID: %s\n이상 로그: %s\n' "$PID" "$LOG_FILE"
exec "$PYTHON" "$SCRIPT_DIR/import_memories.py" --follow-status "$STATUS_FILE" --worker-pid "$PID"
