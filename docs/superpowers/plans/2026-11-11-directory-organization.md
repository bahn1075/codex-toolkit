# Directory Organization Implementation Plan

**Goal:** Separate implementation, checks, tools and documentation while preserving shell entrypoints.

**Architecture:** Root entrypoints resolve implementation under lib/. Installation copies the same layout. Checks share a small path setup so they remain directly runnable.

**Tech Stack:** Bash and Python; existing dependencies only.

**Spec:** [Design](../specs/2026-11-11-directory-organization-design.md)

## Constraints and review focus

Preserve user state and entrypoint commands. Do not execute a real installation or removal. Verify paths with spaces, bundle copies, generated launchers, direct check execution and importer imports. Existing installed launchers must resolve the new bundle after update.

## Tasks

- [x] Move implementation to lib/, checks to tests/, importer to tools/mem0_import/, policy to docs/.
- [x] Update entrypoints, bundle copying, generated launchers, importer imports and test fixtures together.
- [x] Document the layout and revised development commands; update project map.
- [x] Run existing offline checks, shell syntax checks and a copied-bundle regression check; review diff and remaining references.

## Execution decisions

User explicitly requested uninterrupted implementation. Work directly in the requested repository and leave changes reviewable without committing or changing global installation state.

## Verification and review

All 15 directly runnable checks passed, including bundle copying/reentry and copied importer/removal dry-run. Python compilation, Bash syntax and whitespace checks passed. Independent read-only review found no important defect introduced by the reorganization. No live installation, commit or push was performed.

Existing limitation observed during review: preflight rejects source paths under ~/.codex, including direct installed-bundle setup/update; that existing behavior was left outside this directory-only change.
