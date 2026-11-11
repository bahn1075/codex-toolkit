# Completion checks

Run checks from the repository root with ~/.codex/toolkit/venv/bin/python. Check scripts are under tests/ and use tests/_paths.py for imports.

- Execute tests/check_*.py directly for the full suite; tests/check_layout.py covers installed bundle copy, source-independent launchers, copied checks/importer and removal dry-run in temporary homes.
- Use tests/check_serena_projects.py only when the installed Serena runtime is available.
- Run bash -n on setup.sh, update.sh, remove.sh, lib/toolkit.sh and tools/mem0_import/import_memories.sh.
- Run git diff --check.

Choose checks appropriate to the change. Do not perform live package installation, login, Oracle writes or cluster changes merely for repository verification.
