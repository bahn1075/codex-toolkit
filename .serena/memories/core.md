# Project map

- Personal Codex installation bundle for macOS/Linux; stable root entrypoints: setup.sh, update.sh, remove.sh.
- lib/toolkit.sh: shared installer. lib/configure.py: configuration/doctor. Remaining runtime modules live together in lib/.
- tests/check_*.py: directly runnable checks; tests/_paths.py resolves lib/ and tools/mem0_import/.
- tools/mem0_import/: document importer, interactive shell wrapper and SQL.
- docs/policy.md: managed instruction text. docs/superpowers/: design and implementation records.
- Installed ~/.codex/toolkit/bundle mirrors this layout. bootstrap switches SCRIPT_DIR to the installed bundle before configuration so launchers survive deletion of downloaded sources.
- README.md documents operator and verification commands. Do not run a real installer/remover to validate repository-only changes.
