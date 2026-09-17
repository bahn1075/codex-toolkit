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

exec "$PYTHON" "$SCRIPT_DIR/import_memories.py" "$TARGET_DIR" --quiet-summary "$@"
