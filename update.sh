#!/usr/bin/env bash
# Update only this toolkit's packages; never reset Codex or its login/history.
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/toolkit.sh"
case "${1:-}" in
  --help|-h) printf '%s\n' 'Usage: bash update.sh  (close active Codex/VS Code sessions first)'; exit 0 ;;
  '') ;;
  *) die "Unknown option: $1" ;;
esac
preflight
acquire_lock
[ -f "$CT_ROOT/install-state.json" ] || die 'Run setup.sh first.'
bootstrap
snapshot_configuration
install_packages
configure_core
install_mem0
configure_headroom
install_plugins
finish
