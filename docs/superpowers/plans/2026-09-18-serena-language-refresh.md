# Serena Project Language Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh registered Serena projects' language servers whenever setup runs.

**Architecture:** A bundled Python script runs under Serena's own Python environment. It reuses Serena's project-language detector and atomically updates only `language_servers` in each registered project's primary configuration, removing the corresponding local override. `toolkit.sh` invokes it after `serena init`.

**Tech Stack:** Bash 3.2, Python 3.13, installed `serena-agent`, Serena YAML utilities.

**Spec:** `docs/superpowers/specs/2026-09-18-serena-language-refresh-design.md`

## Global Constraints

- Scan only projects registered in `~/.serena/serena_config.yml`.
- Keep non-language project settings, memories, and caches unchanged.
- Continue after one project's refresh failure and return failure only after reporting all failures.
- Do not add dependencies.

---

### Task 1: Refresh script and offline check

**Files:**
- Create: `serena_projects.py`
- Create: `check_serena_projects.py`

**Interfaces:**
- Produces: `python serena_projects.py`, which reads Serena's registered projects and exits nonzero if any refresh fails.

- [ ] **Step 1: Write the failing offline check**

Create a temporary HOME and registered Python project whose `.serena/project.yml` and `.serena/project.local.yml` both contain `language_servers: [bash]`; assert that running the script selects `[python]`, removes the local override, and leaves `read_only: true` intact.

- [ ] **Step 2: Run the check to verify it fails**

Run: `~/.codex/toolkit/venv/bin/python check_serena_projects.py`

Expected: failure because `serena_projects.py` does not exist.

- [ ] **Step 3: Implement the refresh script**

Use `SerenaConfig`, `ProjectConfig._determine_project_language_servers`, and Serena's YAML load/save utilities. For each registered path, replace only the primary `language_servers` value and delete the local `language_servers` key.

- [ ] **Step 4: Run the check to verify it passes**

Run: `~/.codex/toolkit/venv/bin/python check_serena_projects.py`

Expected: PASS with the Python project configured for `python`.

### Task 2: Installer integration

**Files:**
- Modify: `toolkit.sh`
- Modify: `check_configure.py`

**Interfaces:**
- Consumes: `serena_projects.py` from the copied installer bundle and the installed Serena Python interpreter.

- [ ] **Step 1: Write the failing installer-copy assertion**

Extend the existing bootstrap fixture to require `serena_projects.py` in the copied bundle list.

- [ ] **Step 2: Run the configuration check to verify it fails**

Run: `~/.codex/toolkit/venv/bin/python check_configure.py`

Expected: failure because bootstrap does not copy the refresh script.

- [ ] **Step 3: Invoke refresh after `serena init`**

Copy `serena_projects.py` into the bundle and run it using `$CT_ROOT/uv-tools/serena-agent/bin/python` immediately after `$CT_ROOT/bin/serena init`.

- [ ] **Step 4: Run both checks**

Run: `~/.codex/toolkit/venv/bin/python check_serena_projects.py && ~/.codex/toolkit/venv/bin/python check_configure.py`

Expected: both checks pass.
