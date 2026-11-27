#!/usr/bin/env bash
# macOS Bash 3.2+ and Linux. Installs Codex, Antigravity CLI, Serena, Headroom, Mem0 and Ponytail; excludes Graft.
# Run as the ordinary login user, outside ~/.codex. Remove with: bash remove.sh
set -Eeuo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source "$SCRIPT_DIR/lib/toolkit.sh"
if [ "$#" = 1 ]; then
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --llm) configure_llm; exit 0 ;;
  esac
fi
ct_setup_mode=''
for option in "$@"; do
  case "$option" in
    --update) ct_setup_mode=--update ;;
    --cli-first|--cli-only) ;;
    *) die "Unknown option: $option" ;;
  esac
done
preflight --stop-codex
acquire_lock
snapshot_configuration
bootstrap
install_packages
configure_core
install_mem0 "$ct_setup_mode"
configure_headroom
install_plugins
finish
