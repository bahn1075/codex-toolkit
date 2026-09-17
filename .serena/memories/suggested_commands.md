# Operator commands

- Fresh destructive setup: `bash setup.sh` (archives existing `~/.codex`).
- Non-destructive reconcile after failure/config change: `bash setup.sh --resume`.
- Update after setup: `codex-update` or `bash ~/.codex/toolkit/bundle/update.sh`.
- MCP handshake/tool-list diagnostics: `codex-doctor`.
- Switch Headroom to MCP-only: `CT_HEADROOM_MODE=mcp bash ~/.codex/toolkit/bundle/setup.sh --resume`; substitute `proxy` to restore persistent proxy mode.
- Retry queued Mem0 session jobs: `~/.codex/toolkit/bin/mem0-session --drain`.
- Import documents: `mem0-import --dry-run /path`, then `mem0-import /path`; interactive wrapper: `mem0-import-prompt`.
- Inspect local changes: `git status --short`, `git diff --check`, `git diff -- README.md`.