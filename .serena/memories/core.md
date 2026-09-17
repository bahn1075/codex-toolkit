# Project map

- Personal Codex installation bundle for macOS/Linux; user entrypoints: `setup.sh` and `update.sh`.
- Shared installer flow: `toolkit.sh`; configuration and doctor logic: `configure.py`.
- Graft startup wrapper: `graft_mcp.py`.
- Mem0 MCP and session hooks: `mem0_mcp.py`, `mem0_session.py`; document import: `mem0_import/`.
- Offline checks: `check_configure.py`, `check_graft.py`, `check_mem0_session.py`, `mem0_import/check_import_memories.py`.
- README is the operator guide and must match script behavior.

Read `mem:tech_stack` for runtime constraints, `mem:conventions` for installer invariants, `mem:suggested_commands` for operator commands, and `mem:task_completion` for completion checks.