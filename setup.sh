#!/usr/bin/env bash
# macOS Bash 3.2+ and Linux. Installs Serena, Headroom, Mem0 and Ponytail; excludes Graft.
# Run as the ordinary login user, outside ~/.codex. Remove with: bash remove.sh
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/toolkit.sh"
case "${1:-}" in
  --help|-h) usage; exit 0 ;;
  --llm) configure_llm; exit 0 ;;
  --resume|'') ;;
  *) die "Unknown option: $1" ;;
esac
preflight
acquire_lock
snapshot_configuration
bootstrap
install_packages
configure_core
install_mem0
configure_headroom
install_plugins
finish
