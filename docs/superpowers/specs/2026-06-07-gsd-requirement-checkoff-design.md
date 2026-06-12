# Design: Auto check-off of REQ-IDs from SUMMARY.md (GSD core)

**Date:** 2026-06-07
**Status:** Approved — ready for implementation planning
**Scope:** Global (GSD core, `~/.claude/get-shit-done/`)
**Author:** Trading Bot Dev (with Claude)

---

## Problem

Across 4 GSD milestones (v1.0–v1.3) the same failure recurred: requirements were
shipped but their checkboxes in `.planning/REQUIREMENTS.md` were never ticked. At
milestone close the traceability file was stale (e.g. v1.3 closed with 7 of 12 reqs
still `- [ ]` despite all being shipped and listed as Validated in PROJECT.md).

**Root cause (precise) — REVISED after reading GSD core:** the automation *partially
exists but is broken*. `execute-plan.md`'s `update_requirements` step (lines 420-428)
already calls `gsd-sdk query requirements.mark-complete ${REQ_IDS}`, and a robust tick
primitive already exists at `milestone.cjs:cmdRequirementsMarkComplete`. It has failed
every milestone for three compounding reasons:

1. **Wrong/uninstalled binary:** it invokes `gsd-sdk`, which is **not installed** on this
   machine. The actually-installed CLI is `node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs"`.
   The call silently does nothing.
2. **Manual variable:** `${REQ_IDS}` is never assigned by any command — "extract the IDs
   from frontmatter" is LLM prose. This is the manual-discipline step that fails.
3. **Wrong source of truth:** it reads `PLAN.md`'s `requirements:` field, whereas the
   canonical completed-set lives in `SUMMARY.md`'s `requirements-completed` (which
   `audit-milestone.md` already treats as authoritative).

**Insight that makes this solvable deterministically:** the data already exists. Every
plan SUMMARY.md carries the completed requirement IDs in frontmatter:

```yaml
requirements-completed: [SIG-07, SIG-08]
```

and `gsd-tools.cjs summary-extract` already parses this field. The fix is to (a) add one
deterministic command that reads that frontmatter and reuses the existing tick primitive
— eliminating the manual `${REQ_IDS}` step — and (b) call it from the actually-installed
binary at the points that already run.

**Existing assets to reuse (DRY):**
- `milestone.cjs:cmdRequirementsMarkComplete(cwd, reqIds, raw)` — takes REQ-IDs and flips
  `- [ ] **REQ-ID**` → `- [x] **REQ-ID**` AND updates the traceability table
  `| REQ-ID | … | Pending |` → `Complete`. Idempotent (`already_complete`), no-ops without
  REQUIREMENTS.md, atomic write, reports `marked_complete`/`already_complete`/`not_found`.
- `frontmatter.cjs:extractFrontmatter`, `core.cjs:planningPaths/escapeRegex/output/error`,
  and the phase-dir parse pattern used by `cmdStats`.

---

## Goals

- When a phase completes, REQ-IDs listed in its SUMMARY frontmatter are automatically
  ticked in REQUIREMENTS.md.
- At milestone close, **guarantee** REQUIREMENTS.md is never stale, regardless of whether
  per-phase check-off ran.
- Global: applies to every GSD project, not just one.
- Survive `/gsd-update` (be reapply-patches-safe).

## Non-Goals (YAGNI)

- Does **not** edit PROJECT.md's "Validated" prose — that semantic/prose evolution stays
  the LLM's job in `transition.md`. This tool owns only the mechanical REQUIREMENTS.md
  updates (checkbox + traceability-table status, both handled by the reused primitive).
- Does **not** rebuild the tick logic — it reuses `cmdRequirementsMarkComplete`.
- Does **not** add a PostToolUse / git hook (see Trigger decision).
- Does **not** create new files inside `get-shit-done/` (see Constraints).

---

## Key Decisions

### Scope: global, inside GSD core
Chosen over project-local. The check-off logic lives in GSD core so all projects benefit.
Accepted tradeoff: `/gsd-update` can overwrite the modified files, requiring
`/gsd-reapply-patches` afterward.

### Trigger: workflow wiring, not a hook
Because the logic lives in GSD core, the trigger is a deterministic CLI call wired into
the workflows that already run — **not** a PostToolUse hook. (Hooks live in `settings.json`,
outside GSD core, and would not be covered by reapply-patches.) This is a deliberate shift
from an earlier hook idea, made to keep the entire solution inside the reapply-safe
GSD-core boundary. Two wiring points (see Component 2): the **existing** (broken)
`execute-plan.md:update_requirements` call site is fixed to fire per plan, and a new
`--all` sweep is added at the `complete-milestone.md` hard gate as the guarantee.
`transition.md` is intentionally NOT wired — per-plan coverage already ticks within each
phase, and the milestone sweep is the catch-all; adding a third site would be redundant.

### Reuse the existing tick primitive (DRY)
The new command does not reimplement checkbox/table editing. The per-ID tick loop inside
`cmdRequirementsMarkComplete` is refactored into a shared, pure helper
`markRequirementsComplete(reqContent, reqIds)` returning
`{ content, marked, alreadyComplete, notFound }`. Both `cmdRequirementsMarkComplete`
(explicit IDs) and the new `cmdRequirementsCheckOff` (IDs harvested from SUMMARY
frontmatter) call it. This keeps a single source of truth for the edit semantics and means
the new command inherits the table update, idempotency, and atomic write for free.

### Reapply-safety: modify existing files only, add zero new files
`/gsd-update` detects changes via SHA-256 hash comparison **against shipped files** and
backs them up for reapply. A brand-new file is not a "modification" of any shipped file,
so it may not be tracked and can be lost on the "wipe and reinstall." Therefore **all
changes are edits to existing shipped files**; no new files are added to `get-shit-done/`.

### Invocation form: `gsd-tools.cjs`, not `gsd-sdk`
Workflows reference `gsd-sdk query ...`, but `gsd-sdk` is not installed on this machine;
the actually-installed CLI is `node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs" <cmd>`.
Workflow wiring uses the `gsd-tools.cjs` form so it actually executes.

---

## Components

### Component 1 — `requirements check-off` subcommand (modify existing GSD-core files)

**Files modified:**
- `bin/lib/milestone.cjs` — (a) refactor the per-ID tick loop out of
  `cmdRequirementsMarkComplete` into a shared pure helper
  `markRequirementsComplete(reqContent, reqIds)`; (b) add
  `cmdRequirementsCheckOff(cwd, { phase, all }, raw)` that harvests REQ-IDs from SUMMARY
  frontmatter and calls that helper.
- `bin/gsd-tools.cjs` — extend the **existing** `case 'requirements':` block (~line 698)
  to handle a `check-off` subcommand alongside `mark-complete`, parsing the `<phase>`
  positional and `--all`.

**Interface (subcommand under the existing `requirements` command):**
- `gsd-tools requirements check-off <phase>` — tick reqs from one phase's SUMMARY files.
  Example: `requirements check-off 15`, `requirements check-off 14.1`.
- `gsd-tools requirements check-off --all` — sweep every phase directory under
  `.planning/phases/`.

**`markRequirementsComplete(reqContent, reqIds)` helper (pure):**
- Extracted verbatim from the current `cmdRequirementsMarkComplete` loop (lines 39-74):
  the checkbox regex `(-\s*\[)[ ](\]\s*\*\*${esc}\*\*)` → `$1x$2`, the table
  `… | Pending |` → `| Complete |` regex, and the already-complete detection.
- Returns `{ content, marked: [...], alreadyComplete: [...], notFound: [...] }`. Does no
  file I/O (caller writes).
- `cmdRequirementsMarkComplete` is rewritten to: parse IDs (unchanged) → call helper →
  `atomicWriteFileSync` if `marked.length` → `output(...)` (unchanged shape). Behaviour
  identical; this is a pure refactor verified by the existing command's outputs.

**`cmdRequirementsCheckOff(cwd, { phase, all }, raw)` behaviour:**
1. Resolve REQUIREMENTS.md via `planningPaths(cwd).requirements` (workstream auto-resolved
   via the `GSD_WORKSTREAM` env var that `main()` already sets). If absent → output
   `{ updated:false, reason:'REQUIREMENTS.md not found' }`, exit 0.
2. Resolve phases dir via `planningPaths(cwd).phases`. Collect target SUMMARY.md files:
   - `<phase>` mode: read `phasesDir`, match the single dir whose **normalized phase number
     equals** the requested one — parse each dir name with `/^(\d+[A-Z]?(?:\.\d+)*)-?(.*)/`
     and compare via `normalizePhaseName(m[1]) === normalizePhaseName(phase)`, NOT a
     `<phase>*` prefix glob. (Critical: `14` matches `14-trade-history-command` and must
     **not** match `14.1-spy-...`; `14.1` matches only the latter.) Then take all files
     ending `-SUMMARY.md` (or `SUMMARY.md`) in that dir.
   - `--all` mode: every `*-SUMMARY.md` under every dir in `phasesDir`.
3. For each SUMMARY, `extractFrontmatter`. Read `requirements-completed`; **leniently** also
   accept `requirements` (the malformed key in `18-02-SUMMARY.md`). Each value may be an
   array or a bracket/comma string; normalize to a flat de-duplicated REQ-ID list.
4. Call `markRequirementsComplete(reqContent, allIds)`. Write via `atomicWriteFileSync`
   only if `marked.length > 0`.
5. For each `notFound` ID, emit a `process.stderr.write` warning (catches typos / wrong
   milestone) but never throw.
6. `output({ updated: marked.length>0, marked_complete: marked, already_complete,
   not_found, summaries_scanned, ids_seen }, raw, '<n> requirements checked off')`.
   **Always exit 0** — a tooling helper must never block a transition or milestone close.

**Reuse:** `markRequirementsComplete` (new shared helper), `planningPaths`,
`extractFrontmatter`, `escapeRegex`, `atomicWriteFileSync`, `normalizePhaseName`,
`output`, `error`.

### Component 2 — workflow wiring (modify existing workflow files)

**`workflows/execute-plan.md`** — replace the broken `update_requirements` step
(lines 420-428), which today runs `gsd-sdk query requirements.mark-complete ${REQ_IDS}`
with an unassigned `${REQ_IDS}` and an uninstalled binary. New content:

```bash
node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs" requirements check-off "${PHASE}" 2>&1 || true
```

Purpose: deterministic per-plan accuracy. After each plan's SUMMARY is written, re-scan the
phase and tick whatever is complete (idempotent, so re-running per plan is safe). No manual
`${REQ_IDS}` extraction. `${PHASE}` is already in scope in execute-plan.md.

**`workflows/complete-milestone.md`** — at the start of `verify_readiness` (before the
"N/M v1 requirements checked off" computation and before REQUIREMENTS.md is archived/
removed):

```bash
node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs" requirements check-off --all 2>&1 || true
```

Purpose: **the guarantee.** Sweeps every phase so the readiness report and the archived
REQUIREMENTS.md reflect reality regardless of per-plan discipline. This is the step that
actually kills the 4×-recurring failure, placed at a hard gate.

### Component 3 — testing (TDD, no persisted files in GSD core)

Verify red→green by driving the real CLI against a **throwaway temp fixture** in the
system temp directory (never inside `get-shit-done/`):

Fixture: a temp dir containing `.planning/REQUIREMENTS.md` (a few `- [ ] **X-01**:` lines
plus a traceability table with `| X-01 | Phase 15 | Pending |` rows) and
`.planning/phases/15-foo/15-01-SUMMARY.md` (frontmatter with `requirements-completed`).
Drive via `gsd-tools requirements check-off 15 --cwd <tmp>` and `--all`.

Test cases:
1. Single REQ-ID flips `[ ]` → `[x]` AND its table row `Pending` → `Complete` (table
   update inherited from the reused primitive).
2. Multiple REQ-IDs across multiple summaries in a phase.
3. Idempotent: re-run produces no change, reports `already_complete`.
4. Lenient key: a summary using `requirements:` instead of `requirements-completed:` ticks.
5. Unknown REQ-ID (in summary, not in REQUIREMENTS.md) → stderr warning, exit 0, others
   still ticked.
6. Missing REQUIREMENTS.md → no-op, exit 0.
7. `--all` sweep ticks reqs from every phase dir.
8. Non-matching/empty/malformed frontmatter → safe no-op, exit 0.
9. Phase-match precision: `requirements check-off 14` ticks only phase-14 reqs, leaving
   phase-14.1 reqs untouched (and vice-versa).
10. Refactor parity: `requirements mark-complete X-01` still behaves exactly as before
    (regression guard on the extracted helper).

---

## Data Flow

```
/gsd-execute-phase 15 -> per-plan execute-plan.md update_requirements
       -> gsd-tools requirements check-off 15
            -> reads 15-01-SUMMARY.md: requirements-completed: [SIG-07, SIG-08]
            -> flips - [ ] **SIG-07** / **SIG-08** + table Pending->Complete in REQUIREMENTS.md

/gsd-complete-milestone
  -> verify_readiness (start)
       -> gsd-tools requirements check-off --all
            -> sweeps every phase SUMMARY -> all shipped REQ-IDs ticked
       -> "N/M requirements checked off" now accurate
       -> REQUIREMENTS.md archived accurately, then removed
```

---

## Error Handling

- Missing REQUIREMENTS.md → silent no-op, exit 0.
- REQ-ID not found in REQUIREMENTS.md → stderr warning, exit 0 (catches typos / wrong
  milestone without blocking).
- Unreadable/malformed SUMMARY frontmatter → skip that file, continue, exit 0.
- All workflow call sites use `|| true` so check-off can never abort plan execution or
  milestone close.

## Maintenance / Update Path

- All edits are to shipped files → detected and backed up by `/gsd-update`, restored by
  `/gsd-reapply-patches`. After any GSD update: run `/gsd-reapply-patches`.
- GSD core VERSION at design time: 1.38.3.

---

## Success Criteria

- [ ] `markRequirementsComplete` helper extracted; `cmdRequirementsMarkComplete` refactored
      onto it with identical behaviour (regression-guarded).
- [ ] `gsd-tools requirements check-off <phase>` ticks REQ-IDs (checkbox + table) from that
      phase's SUMMARY frontmatter.
- [ ] `gsd-tools requirements check-off --all` ticks REQ-IDs across all phases.
- [ ] Idempotent, lenient (`requirements` and `requirements-completed`), warns-not-fails,
      no-ops without REQUIREMENTS.md, always exit 0.
- [ ] Phase matching is exact (`14` ≠ `14.1`).
- [ ] `execute-plan.md` broken `gsd-sdk … ${REQ_IDS}` call replaced with the working
      `gsd-tools requirements check-off "${PHASE}"`.
- [ ] `complete-milestone.md` `verify_readiness` runs `requirements check-off --all` before
      the readiness report and archival (the guarantee).
- [ ] Zero new files added to `get-shit-done/`; only existing files modified.
- [ ] All 10 test cases pass against a temp fixture via the real CLI.
- [ ] Manual end-to-end check: a stale REQUIREMENTS.md in a real project gets corrected by
      `requirements check-off --all`.
