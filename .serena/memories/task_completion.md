# Completion checks

Run from the repository root with the installed toolkit interpreter:

- `~/.codex/toolkit/venv/bin/python check_configure.py`
- `~/.codex/toolkit/venv/bin/python check_graft.py`
- `~/.codex/toolkit/venv/bin/python check_mem0_session.py`
- `~/.codex/toolkit/venv/bin/python mem0_import/check_import_memories.py`
- `git diff --check`

For README-only changes, all four offline checks still validate that documented commands and flows remain supported. Do not perform live package installation, login, Oracle writes, or cluster changes merely to verify a documentation edit.