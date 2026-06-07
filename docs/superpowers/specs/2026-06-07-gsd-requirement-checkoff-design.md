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

**Root cause (precise):** the `transition.md` workflow's `evolve_project` step instructs
the LLM to move requirements Active → Validated **in PROJECT.md**, but **nothing ever
flips the `- [ ]` checkboxes in REQUIREMENTS.md**. The one step that could is LLM prose,
which is the exact manual-discipline path that has failed every milestone.

**Insight that makes this solvable deterministically:** the data already exists. Every
plan SUMMARY.md carries the completed requirement IDs in frontmatter:

```yaml
requirements-completed: [SIG-07, SIG-08]
```

and `gsd-tools.cjs summary-extract` already parses this field. The fix is to consume that
existing data mechanically and flip the checkboxes — removing the LLM-judgment step.

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
  checkbox.
- Does **not** edit the traceability table (those rows have no checkbox).
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
the workflows that already run (`transition.md`, `complete-milestone.md`) — **not** a
PostToolUse hook. (Hooks live in `settings.json`, outside GSD core, and would not be
covered by reapply-patches.) This is a deliberate shift from an earlier hook idea, made
to keep the entire solution inside the reapply-safe GSD-core boundary.

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

### Component 1 — `check-off` command (modify existing GSD-core files)

**Files modified:**
- `bin/lib/commands.cjs` — add `cmdCheckOff(cwd, { phase, all, ws }, raw)`.
- `bin/gsd-tools.cjs` — add `case 'check-off':` to the dispatch `switch` (~line 423),
  parsing `<phase>` positional, `--all`, and the existing `--cwd`/`--ws`/`--raw` flags.

**Interface:**
- `gsd-tools check-off <phase>` — tick reqs from one phase's SUMMARY files. Example:
  `check-off 15`, `check-off 14.1`.
- `gsd-tools check-off --all` — sweep every phase directory under `.planning/phases/`.

**Behavior:**
1. Resolve REQUIREMENTS.md via `planningPaths(cwd, ws).requirements`.
2. If REQUIREMENTS.md is absent → no-op, exit 0 (between milestones / non-GSD project).
3. Collect target SUMMARY.md files:
   - `<phase>` mode: all `*-SUMMARY.md` in the single phase dir whose **normalized phase
     number equals** the requested one — matched by parsing each dir name with the existing
     pattern `/^(\d+[A-Z]?(?:\.\d+)*)-?(.*)/` and comparing via `normalizePhaseName`, NOT a
     `<phase>*` prefix glob. (Critical: requesting `14` must match `14-trade-history-command`
     and must **not** match `14.1-spy-...`; requesting `14.1` matches only the latter.)
   - `--all` mode: all `*-SUMMARY.md` under every phase dir.
4. For each SUMMARY, read frontmatter via the existing `extractFrontmatter` helper.
   Read `requirements-completed`; **leniently** also accept `requirements` (the malformed
   key seen in `18-02-SUMMARY.md`). Normalize to a de-duplicated set of REQ-ID strings.
5. For each REQ-ID, in REQUIREMENTS.md replace `- [ ] **<REQ-ID>**:` with
   `- [x] **<REQ-ID>**:` (anchored to the GSD checkbox format `^- \[ \] \*\*`).
   - Already `- [x]` → counted as `already`, no change (idempotent).
   - REQ-ID not present in REQUIREMENTS.md → counted as `not_found`, stderr warning,
     continue (warns-not-fails).
6. Write REQUIREMENTS.md only if at least one box changed.
7. Output (respecting `--raw`/`output()` conventions) a structured result:
   `{ requirements_file_present, ticked: [...], already: [...], not_found: [...] }`
   plus a one-line human summary, and **always exit 0** (a tooling helper must never
   block a transition or milestone close).

**Reuse:** `planningPaths`, `extractFrontmatter`, `output`, `error`, phase-normalization
helpers, and the checkbox regex pattern already used by `cmdStats`
(`^- \[x\] \*\*` / `^- \[ \] \*\*`).

### Component 2 — workflow wiring (modify existing workflow files)

**`workflows/transition.md`** — in the `update_roadmap_and_state` step, adjacent to the
deterministic phase-complete delegation (NOT inside the LLM-prose `evolve_project` step):

```bash
node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs" check-off "${current_phase}" 2>&1 || true
```

Purpose: incremental accuracy — boxes get ticked as each phase lands.

**`workflows/complete-milestone.md`** — at the start of `verify_readiness`, before the
"N/M v1 requirements checked off" computation and before REQUIREMENTS.md is archived/
removed:

```bash
node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs" check-off --all 2>&1 || true
```

Purpose: **the guarantee.** Sweeps every phase so the readiness report and the archived
REQUIREMENTS.md reflect reality regardless of per-phase discipline. This is the step that
actually kills the 4×-recurring failure, placed at a hard gate.

### Component 3 — testing (TDD, no persisted files in GSD core)

Verify red→green by driving the real CLI against a **throwaway temp fixture** in the
system temp directory (never inside `get-shit-done/`):

Fixture: a temp dir containing `.planning/REQUIREMENTS.md` (a few `- [ ] **X-01**:` lines)
and `.planning/phases/15-foo/15-01-SUMMARY.md` (frontmatter with `requirements-completed`).
Drive via `gsd-tools check-off 15 --cwd <tmp>` and `--all`.

Test cases:
1. Single REQ-ID flips `[ ]` → `[x]`.
2. Multiple REQ-IDs across multiple summaries in a phase.
3. Idempotent: re-run produces no change, reports `already`.
4. Lenient key: a summary using `requirements:` instead of `requirements-completed:` ticks.
5. Unknown REQ-ID (in summary, not in REQUIREMENTS.md) → stderr warning, exit 0, others
   still ticked.
6. Missing REQUIREMENTS.md → no-op, exit 0.
7. `--all` sweep ticks reqs from every phase dir.
8. Non-matching/empty/malformed frontmatter → safe no-op, exit 0.

---

## Data Flow

```
/gsd-execute-phase 15 finishes
  -> transition.md update_roadmap_and_state
       -> gsd-tools check-off 15
            -> reads 15-01-SUMMARY.md: requirements-completed: [SIG-07, SIG-08]
            -> flips - [ ] **SIG-07**  and  - [ ] **SIG-08**  in REQUIREMENTS.md

/gsd-complete-milestone
  -> verify_readiness (start)
       -> gsd-tools check-off --all
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
- All workflow call sites use `|| true` so check-off can never abort a transition or
  milestone close.

## Maintenance / Update Path

- All edits are to shipped files → detected and backed up by `/gsd-update`, restored by
  `/gsd-reapply-patches`. After any GSD update: run `/gsd-reapply-patches`.
- GSD core VERSION at design time: 1.38.3.

---

## Success Criteria

- [ ] `gsd-tools check-off <phase>` ticks REQ-IDs from that phase's SUMMARY frontmatter.
- [ ] `gsd-tools check-off --all` ticks REQ-IDs across all phases.
- [ ] Idempotent, lenient (`requirements` and `requirements-completed`), warns-not-fails,
      no-ops without REQUIREMENTS.md, always exit 0.
- [ ] Wired into `transition.md` (per-phase) and `complete-milestone.md` (`--all` guarantee).
- [ ] Zero new files added to `get-shit-done/`; only existing files modified.
- [ ] All 8 test cases pass against a temp fixture via the real CLI.
- [ ] Manual end-to-end check: a stale REQUIREMENTS.md in a real project gets corrected by
      `check-off --all`.
