#!/usr/bin/env bash
# Shared implementation. No sudo, eval, shell-wide package upgrades or hook-trust bypass.
CT_HOME="$HOME/.codex"
CT_ROOT="$CT_HOME/toolkit"
CT_BACKUPS="$HOME/.codex-backups"
CT_FAILURES=0
CT_LOCK_HELD=0
CT_STAGE=preflight
CT_PY=''
CT_HEADROOM_MODE=${CT_HEADROOM_MODE:-}
# Honor trusted OS roots, including enterprise roots, on old and new uv versions.
export UV_SYSTEM_CERTS=${UV_SYSTEM_CERTS:-true}
export UV_NATIVE_TLS=${UV_NATIVE_TLS:-$UV_SYSTEM_CERTS}
# npm lifecycle scripts can download through Node directly, outside npm's CA settings.
if [ "$(uname -s)" = Linux ] && [ -z "${NODE_EXTRA_CA_CERTS:-}" ]; then
  for ct_ca in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt; do
    if [ -f "$ct_ca" ]; then export NODE_EXTRA_CA_CERTS="$ct_ca"; break; fi
  done
fi

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '\n%s\n' "$*"; }
usage() {
  cat <<'EOF'
Usage: bash setup.sh [--resume]
Default: archive ~/.codex, reinstall Codex, install/configure toolkit tools and Superpowers.
--resume: continue/reconcile without resetting config, login or history.
Close Codex, Codex app and VS Code before running. Do not run with sudo.
Optional environment settings (first installation):
  CT_HEADROOM_MODE=mcp             Headroom is available only as an MCP tool
  CT_AUTH=chatgpt                 this bundle uses manual ChatGPT login
Mem0 uses Oracle AI Vector Search and user-specified inference/embedding APIs. Setup prompts
for the Oracle wallet, database username/password, TNS alias, wallet password and model API URLs;
secrets stay in ~/.codex/mem0.json (mode 600) and are never written to install-state or logs.
EOF
}
preflight() {
  case "$(uname -s)" in Darwin|Linux) ;; *) die 'Only macOS and Linux are supported.' ;; esac
  [ "$(id -u)" != 0 ] || die 'Run as your normal user, not root/sudo.'
  [ "${CODEX_HOME:-$CT_HOME}" = "$CT_HOME" ] || die 'Custom CODEX_HOME detected. This bundle targets ~/.codex; unset it before setup.'
  [ "${CT_AUTH:-chatgpt}" = chatgpt ] || die 'This bundle is configured for ChatGPT login.'
  case "$HOME" in /*) ;; *) die 'HOME must be absolute.' ;; esac
  [ "$HOME" != / ] || die 'Refusing root HOME.'
  case "$HOME$SCRIPT_DIR" in *$'\n'*|*$'\r'*) die 'Newlines in paths are unsupported.' ;; esac
  case "$SCRIPT_DIR/" in "$CT_HOME/"*) die 'Extract this bundle outside ~/.codex before setup.' ;; esac
  [ ! -L "$CT_HOME" ] || die '~/.codex is a symlink; resolve its intended target before resetting.'
  for ct_cmd in brew npm node uv git python3 bun; do
    command -v "$ct_cmd" >/dev/null || die "Required command missing: $ct_cmd"
  done
  CT_BREW=$(command -v brew)
  CT_UV=$(command -v uv)
  CT_NPM=$(command -v npm)
  CT_NODE=$(command -v node)
  # Distro npm/node-gyp uses distro headers: do not pair it with an NVM Node binary.
  if [ "$(uname -s)" = Linux ] && [ "$CT_NPM" -ef /usr/bin/npm ]; then
    CT_NODE=/usr/bin/node
  fi
  "$CT_NODE" -e 'if(Number(process.versions.node.split(".")[0])<22)process.exit(1)' || die 'Node.js 22+ is required alongside the selected npm.'
  CT_PREFIX=$("$CT_BREW" --prefix)
  export PATH="$(dirname "$CT_NODE"):$CT_PREFIX/bin:$CT_PREFIX/sbin:$PATH"
  # Do not initialize/reset an actively running Codex process.
  if command -v pgrep >/dev/null && pgrep -u "$(id -u)" -x codex >/dev/null 2>&1; then
    die 'A codex process is running. Close Codex/VS Code sessions and run again.'
  fi
}
acquire_lock() {
  mkdir "$HOME/.codex-toolkit-setup.lock" 2>/dev/null || die 'Setup/update lock exists. Check for an active installer before removing the stale lock.'
  CT_LOCK_HELD=1
  trap cleanup EXIT
  trap 'printf "ERROR in stage %s (line %s). Re-run setup.sh --resume after fixing it.\n" "$CT_STAGE" "$LINENO" >&2' ERR
}
cleanup() {
  if [ "$CT_LOCK_HELD" = 1 ]; then rmdir "$HOME/.codex-toolkit-setup.lock" 2>/dev/null || true; fi
}
reset_codex() {
  CT_STAGE=reset
  note 'Archiving the previous Codex home; creating a clean ~/.codex.'
  mkdir -p "$CT_BACKUPS"
  chmod 700 "$CT_BACKUPS"
  CT_BACKUP=$(mktemp -d "$CT_BACKUPS/reset-$(date +%Y%m%d-%H%M%S)-XXXXXX")
  # Remove only a deployment created by this toolkit, before its managed config moves.
  if [ -f "$CT_ROOT/install-state.json" ] && [ -x "$CT_ROOT/bin/headroom" ]; then
    if [ -f "$HOME/.headroom/deploy/codex-toolkit/manifest.json" ]; then
      "$CT_ROOT/bin/headroom" install remove --profile codex-toolkit
    fi
  fi
  if [ -d "$CT_HOME" ]; then mv "$CT_HOME" "$CT_BACKUP/codex"; fi
  mkdir -p "$CT_HOME"
  chmod 700 "$CT_HOME"
  # Clear existing OS-credential-store login where an actual executable is available.
  ct_old_codex=$(type -P codex || true)
  if [ -n "$ct_old_codex" ]; then
    "$ct_old_codex" logout || printf '%s\n' 'Previous Codex logout failed; new setup will use a fresh file credential store.' >&2
  fi
  printf '%s\n' "$CT_BACKUP" > "$CT_HOME/previous-home.txt"
  # Remove only the active npm prefix's Codex package. Other prefixes are reported later.
  if [ -d "$("$CT_NPM" root -g)/@openai/codex" ]; then "$CT_NPM" uninstall -g @openai/codex; fi
  if "$CT_BREW" list --formula codex >/dev/null 2>&1; then "$CT_BREW" uninstall --formula codex; fi
  if "$CT_BREW" list --cask codex >/dev/null 2>&1; then
    "$CT_BREW" uninstall --cask codex
  fi
}
bootstrap() {
  CT_STAGE=bootstrap
  "$CT_BREW" update
  "$CT_BREW" install python@3.13
  CT_BOOT_PY="$CT_PREFIX/opt/python@3.13/bin/python3.13"
  [ -x "$CT_BOOT_PY" ] || die 'Homebrew Python 3.13 was not installed.'
  mkdir -p "$CT_ROOT/bin" "$CT_ROOT/repos" "$CT_ROOT/npm" "$CT_ROOT/logs" "$CT_ROOT/bundle"
  # Keep the installer/update bundle available after the downloaded copy is removed.
  if [ "$SCRIPT_DIR" != "$CT_ROOT/bundle" ]; then
    for ct_file in setup.sh update.sh toolkit.sh configure.py graft_mcp.py mem0_mcp.py mem0_session.py serena_projects.py policy.md README.md check_configure.py check_graft.py check_mem0_session.py; do
      cp "$SCRIPT_DIR/$ct_file" "$CT_ROOT/bundle/$ct_file"
    done
    mkdir -p "$CT_ROOT/bundle/mem0_import"
    cp "$SCRIPT_DIR/mem0_import/import_memories.py" "$CT_ROOT/bundle/mem0_import/import_memories.py"
    cp "$SCRIPT_DIR/mem0_import/check_import_memories.py" "$CT_ROOT/bundle/mem0_import/check_import_memories.py"
    cp "$SCRIPT_DIR/mem0_import/import_memories.sh" "$CT_ROOT/bundle/mem0_import/import_memories.sh"
  fi
  if [ ! -x "$CT_ROOT/venv/bin/python" ]; then
    "$CT_UV" venv --python "$CT_BOOT_PY" "$CT_ROOT/venv"
  fi
  "$CT_UV" pip install --python "$CT_ROOT/venv/bin/python" 'tomlkit>=0.13,<1'
  CT_PY="$CT_ROOT/venv/bin/python"
  export UV_TOOL_DIR="$CT_ROOT/uv-tools"
  export UV_TOOL_BIN_DIR="$CT_ROOT/bin"
  export PATH="$(dirname "$CT_NODE"):$CT_ROOT/bin:$CT_ROOT/npm/node_modules/.bin:$CT_PREFIX/bin:$PATH"
  CT_HEADROOM_MODE=${CT_HEADROOM_MODE:-mcp}
  [ "$CT_HEADROOM_MODE" = mcp ] || die 'CT_HEADROOM_MODE must be mcp.'
  export CT_HEADROOM_MODE CT_PREFIX CT_UV CT_NPM CT_NODE
  "$CT_PY" "$SCRIPT_DIR/configure.py" state-init
}
snapshot_configuration() {
  CT_STAGE=backup
  mkdir -p "$CT_BACKUPS"
  CT_BACKUP=$(mktemp -d "$CT_BACKUPS/update-$(date +%Y%m%d-%H%M%S)-XXXXXX")
  for ct_file in config.toml AGENTS.md hooks.json; do
    if [ -f "$CT_HOME/$ct_file" ]; then cp -p "$CT_HOME/$ct_file" "$CT_BACKUP/"; fi
  done
  cp "$CT_ROOT/install-state.json" "$CT_BACKUP/"
  if [ -f "$CT_ROOT/npm/package-lock.json" ]; then cp "$CT_ROOT/npm/package-lock.json" "$CT_BACKUP/"; fi
}
install_packages() {
  CT_STAGE=packages
  note 'Installing/updating Codex and Kubernetes MCP through Homebrew.'
  # Current official Codex cask advertises macOS and Linux binaries.
  if "$CT_BREW" list --cask codex >/dev/null 2>&1; then
    "$CT_BREW" upgrade --cask codex
  else
    "$CT_BREW" install --cask codex
  fi
  if "$CT_BREW" list --formula kubernetes-mcp-server >/dev/null 2>&1; then
    "$CT_BREW" upgrade --formula kubernetes-mcp-server
  else
    "$CT_BREW" install --formula kubernetes-mcp-server
  fi
  CT_CODEX="$CT_PREFIX/bin/codex"
  [ -x "$CT_CODEX" ] || die 'Homebrew Codex binary is missing.'
  "$CT_CODEX" --version
  # Official Python packages; isolated from the user's other uv tools.
  "$CT_UV" tool install --upgrade --python "$CT_BOOT_PY" serena-agent
  "$CT_UV" tool install --upgrade --python "$CT_BOOT_PY" 'headroom-ai[all]'
  # Oracle AI Vector Search support is not yet in the PyPI 1.0.11 release.
  "$CT_UV" pip install --python "$CT_ROOT/venv/bin/python" --upgrade \
    'mem0ai @ git+https://github.com/mem0ai/mem0.git@c7ee362aff94a369af70f13f2b4f853f6793ff4c' \
    'mcp>=1,<2' 'oracledb>=2.2,<3'
  # Official npm packages, private prefix: no npx download during each Codex launch.
  # Distro node-gyp imports gyp from system Python; Azure CLI/Homebrew Python cannot see it.
  CT_NPM_PY="$CT_BOOT_PY"
  if [ "$(uname -s)" = Linux ] && [ "$CT_NPM" -ef /usr/bin/npm ]; then
    CT_NPM_PY=/usr/bin/python3
  fi
  "$CT_NPM" uninstall --prefix "$CT_ROOT/npm" claude-mem >/dev/null 2>&1 || true
  "$CT_PY" "$SCRIPT_DIR/configure.py" npm-policy
  PYTHON="$CT_NPM_PY" "$CT_NPM" install --prefix "$CT_ROOT/npm" --save-exact --ignore-scripts=false --foreground-scripts \
    @nanonets/graft@latest @upstash/context7-mcp@latest
  # Also repair unchanged packages skipped by an earlier npm install or built with another Node.
  PYTHON="$CT_NPM_PY" "$CT_NPM" rebuild --prefix "$CT_ROOT/npm" --ignore-scripts=false --foreground-scripts
  ct_repo="$CT_ROOT/repos/ponytail"
  if [ -d "$ct_repo/.git" ]; then
    [ -z "$(git -C "$ct_repo" status --porcelain)" ] || die 'Ponytail checkout has local changes; update stopped to preserve them.'
    [ "$(git -C "$ct_repo" remote get-url origin)" = 'https://github.com/dietrichgebert/ponytail.git' ] || die 'Unexpected Ponytail origin.'
    git -C "$ct_repo" pull --ff-only
  else
    git clone --depth 1 https://github.com/dietrichgebert/ponytail.git "$ct_repo"
  fi
  [ -s "$ct_repo/AGENTS.md" ] || die 'Ponytail AGENTS.md is missing; upstream layout changed.'
  "$CT_ROOT/bin/serena" init
  "$CT_ROOT/uv-tools/serena-agent/bin/python" "$SCRIPT_DIR/serena_projects.py"
  "$CT_PY" "$SCRIPT_DIR/configure.py" record-packages
}
configure_core() {
  CT_STAGE=configuration
  note 'Applying shared CLI/IDE MCP config, global instructions and shell aliases.'
  "$CT_PY" "$SCRIPT_DIR/configure.py" remove-claude-mem
  "$CT_PY" "$SCRIPT_DIR/configure.py" configure
  "$CT_CODEX" mcp list
  # Use VS Code's official extension identifier. It shares ~/.codex/config.toml.
  if command -v code >/dev/null; then
    if ! code --install-extension openai.chatgpt --force; then
      printf '%s\n' 'VS Code extension installation failed; install/update it in VS Code.' >&2
      CT_FAILURES=$((CT_FAILURES + 1))
    fi
  fi
}
install_mem0() {
  CT_STAGE=mem0
  note 'Configuring Mem0 with Oracle AI Vector Search and the Tailscale mac model server.'
  "$CT_PY" "$SCRIPT_DIR/configure.py" mem0-settings
  "$CT_PY" "$SCRIPT_DIR/configure.py" mem0-hooks
  "$CT_PY" "$SCRIPT_DIR/configure.py" status mem0_install configured
}
configure_headroom() {
  CT_STAGE=headroom
  "$CT_PY" "$SCRIPT_DIR/configure.py" direct-provider
  if [ -f "$HOME/.headroom/deploy/codex-toolkit/manifest.json" ]; then
    "$CT_ROOT/bin/headroom" install remove --profile codex-toolkit
  fi
  "$CT_PY" "$SCRIPT_DIR/configure.py" status headroom_runtime mcp-only
}
install_plugins() {
  CT_STAGE=plugins
  note 'Installing Superpowers from the OpenAI curated marketplace.'
  if ! "$CT_CODEX" plugin add superpowers@openai-curated-remote; then
    printf '%s\n' \
      'Superpowers installation failed; continuing with the remaining toolkit steps.' \
      'After login and workspace admin approval, retry: codex plugin add superpowers@openai-curated-remote' >&2
  fi
}
finish() {
  CT_STAGE=verification
  "$CT_PY" "$SCRIPT_DIR/configure.py" validate
  "$CT_PY" "$SCRIPT_DIR/configure.py" verify-graft
  note 'Install/configuration pass finished. Authentication and runtime checks are separate.'
  printf '%s\n' \
    'Open a new terminal (or restart VS Code), then run: codex login' \
    'Run codex and start a new thread; Mem0 is available through /mcp.' \
    'Review and trust the Mem0 SessionStart/SessionEnd hooks in /hooks to enable automatic session memory.' \
    'MCP visibility: /mcp. Connection test: ~/.codex/toolkit/bin/codex-doctor' \
    'Update later: bash ~/.codex/toolkit/bundle/update.sh'
  if [ "$CT_FAILURES" -gt 0 ]; then
    printf 'Partial setup: %s stage(s) failed. Fix the reported cause, then bash setup.sh --resume.\n' "$CT_FAILURES" >&2
    exit 1
  fi
}
