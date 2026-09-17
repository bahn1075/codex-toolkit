# Project invariants

- `setup.sh` with no option intentionally archives and recreates global `~/.codex`; `--resume` and `update.sh` must preserve login/history and user settings.
- Never run installer as root/sudo; refuse custom `CODEX_HOME`, a symlinked `~/.codex`, or a bundle placed inside `~/.codex`.
- Keep managed artifacts under `~/.codex/toolkit`; preserve unrelated project `.codex/config.toml`, `AGENTS.md`, existing MCP limits/approval settings, and non-managed hooks.
- Secrets belong only in mode-600 `~/.codex/mem0.json`; never copy secret values to install state, logs, README, or command lines.
- Configuration edits use `tomlkit` and managed marker blocks; shell RC files are backed up and symlink targets preserved.
- Graft auto-builds only inside Git repositories and uses repo-local exclusion without modifying `.gitignore`/`.ignore`.
- Kubernetes integration uses the existing kubeconfig/current context; it must not auto-select or switch clusters.
- Keep implementation minimal and reuse existing helpers; non-trivial changes require one small runnable check.