# Serena Project Language Refresh Design

## Goal

On `setup.sh --resume`, refresh Serena language-server selection for every project registered in Serena's global configuration.

## Scope

- The target set is only Serena's registered `projects` list.
- Re-detect each reachable project's supported source-language composition using Serena.
- Replace `language_servers` in the primary project configuration with the detected result.
- Remove a `language_servers` override from `project.local.yml`, so it cannot mask the refreshed result.
- Preserve all other project configuration, local overrides, memories, and caches.
- Report and continue after a per-project failure.

## Integration

`install_packages()` installs Serena and runs `serena init`; it then invokes a bundled refresh script with the same Python environment that installed Serena. The script uses Serena's configuration and language-detection APIs, avoiding a second language detector in this repository.

## Verification

An offline check creates a temporary registered Python project with stale primary and local language-server settings. It verifies that refresh selects Python, removes the local language override, and preserves an unrelated project setting.
