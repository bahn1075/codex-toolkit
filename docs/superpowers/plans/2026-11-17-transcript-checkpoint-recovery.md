# Transcript checkpoint recovery implementation plan

**Goal:** Recover ucloud-wsl's blocked Mem0 queue and tolerate transcript reserialization without accepting changed conversation evidence.

**Scope:** The user's 2026-11-17 request; `lib/mem0_session.py`, existing Mem0 regression checks, and the operating instructions in `README.md`.

**Architecture:** Keep raw byte checkpoints for normal operation and add a hash/count of normalized evidence. On a byte mismatch, relocate only an identical evidence prefix in the same session, refresh queued byte boundaries, and retain stable batch identities so interrupted inserts remain idempotent. Legacy mismatches without evidence fingerprints still require verified manual recovery.

## Work

- [x] Confirm the remote failure and match the stored Oracle task to a unique completed turn; back up state/transcript and relocate the legacy cursor under the worker lock.
- [x] Add failing regressions for shortened/expanded serialization, stale queue boundaries, changed/deleted evidence, wrong session, and insert retries during rewrites.
- [x] Add evidence fingerprints and conservative relocation; refresh captured same-session jobs before committing a relocated cursor. Preserve pending facts and summaries.
- [x] Run the Mem0 checks and the repository's documented checks; document behavior and legacy limits.
- [x] Install the tested module on ucloud-wsl with backups, restart the retry worker, and verify actual queue progress and saved records.

## Review focus

- Repeated identical user requests must not make relocation ambiguous: validate the entire normalized prefix.
- A shorter rewritten file must not let stale jobs skip new evidence or loop on obsolete byte ends.
- Changed tool evidence, deleted history, and a different session must fail closed.
- Retrying after a committed Oracle insert must reuse its batch identity.
- Old checkpoints and pending files must keep their existing safety checks.

## Review decisions

- Queue snapshots need their own raw prefix hash: committed evidence alone cannot detect suffix-only rewrites. Hash in bounded-memory chunks; repeated tail bytes are not a sufficient fingerprint.
- Detect a raw mismatch before parsing up to an obsolete offset, so a shortened file's incomplete tail cannot block relocation of intact committed evidence.
- The malformed-snapshot fixture updates both its deliberately invalid end and hash; the reader must still reject it without advancing the checkpoint.
- Independent review found no further important defects after these changes. Live mutation during processing is not covered by the rewrite regressions.
- A copy of the real transcript exposed nested tool JSON key ordering. Canonicalize tool evidence, archive version-2 partial caches, and replay their uncommitted source with version-3 progress instead of reusing incompatible character offsets.

## Verification

- All 14 documented check scripts pass locally; final Mem0 checks are also run remotely.
- Twelve rewrite regressions cover queue snapshots, canonical nested tool JSON, partial tails, legacy state, content edits and insert retry identity.
- The installed module resumes the same 25-message next task from a reserialized copy of the actual affected transcript, with identical evidence hashes and different byte offsets.
- The remote installed module and repository source have the same SHA-256. Forty-two existing cursors were upgraded after exact raw-prefix verification.
- Operational backups: `~/.codex/toolkit/mem0-transcript-recovery-20261117-154425` and `~/.codex/toolkit/transcript-fix-20261117/before*`.
- The queue decreased from 15 jobs to 3 during recovery; full background draining is still in progress, not claimed complete.
