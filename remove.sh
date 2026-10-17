#!/usr/bin/env bash
# Remove toolkit-owned software and integration; retain Codex login/history.
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
case "${1:-}" in
  --help|-h) printf '%s\n' 'Usage: bash remove.sh [--dry-run]' 'Close Codex/VS Code before removal. Login/history and shared prerequisites are retained.'; exit 0 ;;
  --dry-run|'') ;;
  *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
esac
[ "$(id -u)" != 0 ] || { printf 'Run as your normal user, not root/sudo.\n' >&2; exit 1; }
[ "${CODEX_HOME:-$HOME/.codex}" = "$HOME/.codex" ] || { printf 'Custom CODEX_HOME is unsupported.\n' >&2; exit 1; }
if [ "${1:-}" != --dry-run ] && command -v pgrep >/dev/null && pgrep -u "$(id -u)" -x codex >/dev/null 2>&1; then
  printf 'Close Codex/VS Code before removal.\n' >&2; exit 1
fi
mkdir "$HOME/.codex-toolkit-setup.lock" 2>/dev/null || { printf 'Setup/update/remove lock exists.\n' >&2; exit 1; }
trap 'rmdir "$HOME/.codex-toolkit-setup.lock" 2>/dev/null || true' EXIT
REMOVE_PY="$HOME/.codex/toolkit/venv/bin/python"
[ -x "$REMOVE_PY" ] || REMOVE_PY=$(command -v python3)
"$REMOVE_PY" "$SCRIPT_DIR/remove.py" "$@"
