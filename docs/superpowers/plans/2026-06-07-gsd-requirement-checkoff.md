# GSD Auto REQ-ID Check-off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GSD automatically tick `REQUIREMENTS.md` REQ-IDs from SUMMARY frontmatter, so the traceability file is never stale at milestone close.

**Architecture:** Reuse the existing tick primitive in `milestone.cjs` (`cmdRequirementsMarkComplete`). Extract its per-ID edit loop into a pure shared helper, add a new `requirements check-off [<phase>|--all]` subcommand that harvests REQ-IDs from phase SUMMARY frontmatter and calls the helper, then fix the broken `execute-plan.md` call site and add an `--all` sweep at the `complete-milestone.md` hard gate.

**Tech Stack:** Node.js (CommonJS `.cjs`) in `~/.claude/get-shit-done/`; GSD workflow markdown; bash for fixture-driven CLI tests.

---

## ⚠️ Execution environment notes (read first)

- **GSD core is NOT a git repo.** `~/.claude/get-shit-done/` is not version-controlled. Do
  **not** attempt `git commit` for the `.cjs` / workflow `.md` edits — there is nowhere to
  commit them. Each implementation task ends with a **test-green checkpoint** instead of a
  commit. The only git-committed artifacts are the spec and this plan, which live in the
  `algo trade` repo.
- **The real CLI is** `node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs"`. `gsd-sdk` is
  NOT installed; never rely on it.
- **Reapply after updates:** these edits modify shipped files; `/gsd-update` will detect and
  back them up, and `/gsd-reapply-patches` restores them. After any GSD update, run
  `/gsd-reapply-patches`.
- All file paths below are under `~/.claude/get-shit-done/` (written here with `$HOME`).
- Tests create throwaway fixtures under the system temp dir and delete them; nothing
  test-related is written into `get-shit-done/`.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `$HOME/.claude/get-shit-done/bin/lib/milestone.cjs` | modify | Add pure `markRequirementsComplete` helper; refactor `cmdRequirementsMarkComplete` onto it; add `cmdRequirementsCheckOff`; export it; import `normalizePhaseName`. |
| `$HOME/.claude/get-shit-done/bin/gsd-tools.cjs` | modify | Extend existing `case 'requirements':` dispatch to handle `check-off`. |
| `$HOME/.claude/get-shit-done/workflows/execute-plan.md` | modify | Replace broken `update_requirements` step. |
| `$HOME/.claude/get-shit-done/workflows/complete-milestone.md` | modify | Add `--all` sweep at start of `verify_readiness`. |

---

## Task 1: Extract pure `markRequirementsComplete` helper (refactor, regression-guarded)

**Files:**
- Modify: `$HOME/.claude/get-shit-done/bin/lib/milestone.cjs:11-87`

This is a behaviour-preserving refactor. The test is a **characterization test**: it must
pass on the current code (baseline) and still pass after the refactor.

- [ ] **Step 1: Write the characterization test script**

Create a throwaway test script (run from any dir):

> **Output note:** `output()` prints the human-summary string when called with `--raw`, and
> pretty-printed (2-space) JSON when called **without** `--raw`. So: omit `--raw` when you
> want to grep JSON arrays (use `grep -A1 '"<key>"'`), and assert real outcomes on the FILE
> and on STDERR. Do not grep for compact JSON.

```bash
cat > /tmp/req_test1.sh <<'SH'
set -u
GSD="$HOME/.claude/get-shit-done/bin/gsd-tools.cjs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/.planning"
cat > "$TMP/.planning/REQUIREMENTS.md" <<'MD'
## v1 Requirements
- [ ] **AUTH-01**: login
- [ ] **AUTH-02**: logout
- [x] **AUTH-03**: already done

## Traceability
| REQ-ID | Phase | Status |
|--------|-------|--------|
| AUTH-01 | Phase 1 | Pending |
| AUTH-02 | Phase 1 | Pending |
MD

# No --raw → pretty JSON in $out; assertions on file + JSON(-A1) + exit code.
out="$(node "$GSD" requirements mark-complete AUTH-01 AUTH-03 BOGUS-99 --cwd "$TMP")"; rc=$?
echo "$out"
fail=0
[ "$rc" -eq 0 ] || { echo "FAIL: exit code $rc"; fail=1; }
grep -q '^- \[x\] \*\*AUTH-01\*\*' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: AUTH-01 checkbox not ticked"; fail=1; }
grep -q '| AUTH-01 | Phase 1 | Complete |' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: AUTH-01 table not Complete"; fail=1; }
grep -q '^- \[ \] \*\*AUTH-02\*\*' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: AUTH-02 wrongly changed"; fail=1; }
grep -q '^- \[x\] \*\*AUTH-03\*\*' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: AUTH-03 lost"; fail=1; }
printf '%s' "$out" | grep -A1 '"already_complete"' | grep -q 'AUTH-03' || { echo "FAIL: AUTH-03 not already_complete"; fail=1; }
printf '%s' "$out" | grep -A1 '"not_found"' | grep -q 'BOGUS-99' || { echo "FAIL: BOGUS-99 not not_found"; fail=1; }
[ "$fail" -eq 0 ] && echo "ALL PASS" || echo "HAD FAILURES"
SH
```

- [ ] **Step 2: Run it against current code to capture the GREEN baseline**

Run: `bash /tmp/req_test1.sh`
Expected: prints `ALL PASS` (this is current behaviour — the baseline the refactor must preserve).

- [ ] **Step 3: Add the pure helper above `cmdRequirementsMarkComplete`**

In `milestone.cjs`, insert this function immediately before `function cmdRequirementsMarkComplete` (line 11):

```javascript
/**
 * Pure: mark each reqId complete in REQUIREMENTS.md content (checkbox + traceability
 * table). No file I/O. Returns modified content and classification arrays.
 */
function markRequirementsComplete(reqContent, reqIds) {
  const marked = [];
  const alreadyComplete = [];
  const notFound = [];

  for (const reqId of reqIds) {
    let found = false;
    const reqEscaped = escapeRegex(reqId);

    // Checkbox: - [ ] **REQ-ID** → - [x] **REQ-ID**
    const checkboxPattern = new RegExp(`(-\\s*\\[)[ ](\\]\\s*\\*\\*${reqEscaped}\\*\\*)`, 'gi');
    const afterCheckbox = reqContent.replace(checkboxPattern, '$1x$2');
    if (afterCheckbox !== reqContent) {
      reqContent = afterCheckbox;
      found = true;
    }

    // Traceability table: | REQ-ID | … | Pending | → | REQ-ID | … | Complete |
    const tablePattern = new RegExp(`(\\|\\s*${reqEscaped}\\s*\\|[^|]+\\|)\\s*Pending\\s*(\\|)`, 'gi');
    const afterTable = reqContent.replace(tablePattern, '$1 Complete $2');
    if (afterTable !== reqContent) {
      reqContent = afterTable;
      found = true;
    }

    if (found) {
      marked.push(reqId);
    } else {
      const doneCheckbox = new RegExp(`-\\s*\\[x\\]\\s*\\*\\*${reqEscaped}\\*\\*`, 'i');
      const doneTable = new RegExp(`\\|\\s*${reqEscaped}\\s*\\|[^|]+\\|\\s*Complete\\s*\\|`, 'i');
      if (doneCheckbox.test(reqContent) || doneTable.test(reqContent)) {
        alreadyComplete.push(reqId);
      } else {
        notFound.push(reqId);
      }
    }
  }

  return { content: reqContent, marked, alreadyComplete, notFound };
}
```

- [ ] **Step 4: Rewrite `cmdRequirementsMarkComplete` to use the helper**

Replace the body from line 34 (`let reqContent = fs.readFileSync(...)`) through the end of
the function (the original `output({...})` call) with:

```javascript
  const reqContent = fs.readFileSync(reqPath, 'utf-8');
  const { content, marked, alreadyComplete, notFound } = markRequirementsComplete(reqContent, reqIds);

  if (marked.length > 0) {
    atomicWriteFileSync(reqPath, content);
  }

  output({
    updated: marked.length > 0,
    marked_complete: marked,
    already_complete: alreadyComplete,
    not_found: notFound,
    total: reqIds.length,
  }, raw, `${marked.length}/${reqIds.length} requirements marked complete`);
}
```

(Leave lines 11-33 — the arg parsing, `reqIds` build, empty check, `reqPath` resolution,
and the `REQUIREMENTS.md not found` early-return — unchanged.)

- [ ] **Step 5: Re-run the characterization test to verify behaviour is unchanged**

Run: `bash /tmp/req_test1.sh`
Expected: `ALL PASS` (identical to the Step 2 baseline — refactor preserved behaviour).

- [ ] **Step 6: Checkpoint (no commit — GSD core is not a git repo)**

Confirm `ALL PASS`. The helper now exists and the existing command is unchanged in behaviour.

---

## Task 2: Add `cmdRequirementsCheckOff` (phase mode) + dispatch + export

**Files:**
- Modify: `$HOME/.claude/get-shit-done/bin/lib/milestone.cjs` (import + new function + export)
- Modify: `$HOME/.claude/get-shit-done/bin/gsd-tools.cjs` (existing `case 'requirements':`)

- [ ] **Step 1: Write the failing test script (phase mode + precision + lenient + warn + no-op)**

```bash
cat > /tmp/req_test2.sh <<'SH'
set -u
GSD="$HOME/.claude/get-shit-done/bin/gsd-tools.cjs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/.planning/phases/14-history" "$TMP/.planning/phases/14.1-spy" "$TMP/.planning/phases/15-foo"
cat > "$TMP/.planning/REQUIREMENTS.md" <<'MD'
- [ ] **OPS-02**: history
- [ ] **SPY-01**: spy signal
- [ ] **SIG-07**: pe direction
- [ ] **SIG-08**: eps trend
MD
# Phase 14 summary — canonical key
cat > "$TMP/.planning/phases/14-history/14-01-SUMMARY.md" <<'MD'
---
phase: 14
requirements-completed: [OPS-02, GHOST-99]
---
body
MD
# Phase 14.1 summary — must NOT be ticked when asking for phase 14
cat > "$TMP/.planning/phases/14.1-spy/14.1-01-SUMMARY.md" <<'MD'
---
phase: 14.1
requirements-completed: [SPY-01]
---
body
MD
# Phase 15 summary — lenient key 'requirements:' instead of 'requirements-completed:'
cat > "$TMP/.planning/phases/15-foo/15-01-SUMMARY.md" <<'MD'
---
phase: 15
requirements: [SIG-07, SIG-08]
---
body
MD

fail=0
# --- phase 14: ticks OPS-02, warns GHOST-99 to stderr, leaves SPY-01 (14.1) untouched ---
node "$GSD" requirements check-off 14 --cwd "$TMP" >/tmp/req_test2.out 2>/tmp/req_test2.err; rc=$?
echo "P14 OUT: $(cat /tmp/req_test2.out)"; echo "P14 ERR: $(cat /tmp/req_test2.err)"
[ "$rc" -eq 0 ] || { echo "FAIL: check-off 14 exit $rc"; fail=1; }
grep -q '^- \[x\] \*\*OPS-02\*\*' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: OPS-02 not ticked"; fail=1; }
grep -q '^- \[ \] \*\*SPY-01\*\*' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: SPY-01 wrongly ticked (14 vs 14.1 leak)"; fail=1; }
grep -q 'GHOST-99' /tmp/req_test2.err || { echo "FAIL: no stderr warning for GHOST-99"; fail=1; }
# --- phase 14.1: now ticks SPY-01 ---
node "$GSD" requirements check-off 14.1 --cwd "$TMP" >/dev/null 2>&1
grep -q '^- \[x\] \*\*SPY-01\*\*' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: SPY-01 not ticked for 14.1"; fail=1; }
# --- phase 15: lenient 'requirements:' key ---
node "$GSD" requirements check-off 15 --cwd "$TMP" >/dev/null 2>&1
grep -q '^- \[x\] \*\*SIG-07\*\*' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: SIG-07 lenient key not ticked"; fail=1; }
grep -q '^- \[x\] \*\*SIG-08\*\*' "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: SIG-08 lenient key not ticked"; fail=1; }
# --- idempotent re-run (file must be byte-identical) ---
cp "$TMP/.planning/REQUIREMENTS.md" "$TMP/before.md"
node "$GSD" requirements check-off 15 --cwd "$TMP" >/dev/null 2>&1
diff -q "$TMP/before.md" "$TMP/.planning/REQUIREMENTS.md" >/dev/null || { echo "FAIL: not idempotent"; fail=1; }
# --- missing REQUIREMENTS.md no-op, exit 0 ---
TMP2="$(mktemp -d)"; mkdir -p "$TMP2/.planning/phases/15-foo"
cp "$TMP/.planning/phases/15-foo/15-01-SUMMARY.md" "$TMP2/.planning/phases/15-foo/"
node "$GSD" requirements check-off 15 --cwd "$TMP2" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] || { echo "FAIL: missing REQUIREMENTS.md should exit 0"; fail=1; }
rm -rf "$TMP2"
[ "$fail" -eq 0 ] && echo "ALL PASS" || echo "HAD FAILURES"
SH
```

- [ ] **Step 2: Run it to verify it FAILS**

Run: `bash /tmp/req_test2.sh`
Expected: failure — the dispatch errors with `Unknown requirements subcommand. Available: mark-complete` (no `check-off` yet), so assertions fail / `HAD FAILURES`.

- [ ] **Step 3: Import `normalizePhaseName` into milestone.cjs**

In `milestone.cjs` line 7, add `normalizePhaseName` to the destructured `require('./core.cjs')`:

```javascript
const { escapeRegex, getMilestonePhaseFilter, extractOneLinerFromBody, normalizeMd, planningPaths, output, error, atomicWriteFileSync, normalizePhaseName } = require('./core.cjs');
```

- [ ] **Step 4: Add `cmdRequirementsCheckOff` after `cmdRequirementsMarkComplete`**

```javascript
/**
 * Harvest completed REQ-IDs from phase SUMMARY frontmatter and mark them complete.
 * opts: { phase: string|null, all: boolean }. Always exits 0 (helper, never blocks).
 */
function cmdRequirementsCheckOff(cwd, opts, raw) {
  const reqPath = planningPaths(cwd).requirements;
  if (!fs.existsSync(reqPath)) {
    output({ updated: false, reason: 'REQUIREMENTS.md not found' }, raw, 'no requirements file');
    return;
  }

  const phasesDir = planningPaths(cwd).phases;
  let phaseDirs;
  try {
    phaseDirs = fs.readdirSync(phasesDir, { withFileTypes: true })
      .filter(e => e.isDirectory())
      .map(e => e.name);
  } catch {
    output({ updated: false, reason: 'no phases directory' }, raw, 'no phases');
    return;
  }

  let targets;
  if (opts.all) {
    targets = phaseDirs;
  } else {
    const want = normalizePhaseName(opts.phase);
    targets = phaseDirs.filter(d => {
      const m = d.match(/^(\d+[A-Z]?(?:\.\d+)*)-?(.*)/i);
      return m && normalizePhaseName(m[1]) === want;
    });
  }

  const ids = [];
  let summariesScanned = 0;
  for (const dir of targets) {
    let files;
    try {
      files = fs.readdirSync(path.join(phasesDir, dir));
    } catch {
      continue;
    }
    for (const f of files) {
      if (!(f.endsWith('-SUMMARY.md') || f === 'SUMMARY.md')) continue;
      summariesScanned++;
      let fm;
      try {
        fm = extractFrontmatter(fs.readFileSync(path.join(phasesDir, dir, f), 'utf-8'));
      } catch {
        continue;
      }
      // Lenient: canonical key plus the malformed 'requirements' key.
      for (const val of [fm['requirements-completed'], fm['requirements']]) {
        if (!val) continue;
        const list = Array.isArray(val)
          ? val
          : String(val).replace(/[\[\]]/g, '').split(/[,\s]+/);
        for (const id of list) {
          const t = String(id).trim();
          if (t) ids.push(t);
        }
      }
    }
  }

  const uniqueIds = [...new Set(ids)];
  if (uniqueIds.length === 0) {
    output({
      updated: false, marked_complete: [], already_complete: [], not_found: [],
      summaries_scanned: summariesScanned, ids_seen: 0,
    }, raw, '0 requirements checked off');
    return;
  }

  const reqContent = fs.readFileSync(reqPath, 'utf-8');
  const { content, marked, alreadyComplete, notFound } = markRequirementsComplete(reqContent, uniqueIds);
  if (marked.length > 0) {
    atomicWriteFileSync(reqPath, content);
  }
  for (const id of notFound) {
    process.stderr.write(`warning: ${id} not found in REQUIREMENTS.md\n`);
  }

  output({
    updated: marked.length > 0,
    marked_complete: marked,
    already_complete: alreadyComplete,
    not_found: notFound,
    summaries_scanned: summariesScanned,
    ids_seen: uniqueIds.length,
  }, raw, `${marked.length} requirements checked off`);
}
```

- [ ] **Step 5: Export the new function**

In the `module.exports` block of `milestone.cjs`, add `cmdRequirementsCheckOff`:

```javascript
module.exports = {
  cmdRequirementsMarkComplete,
  cmdRequirementsCheckOff,
  cmdMilestoneComplete,
  cmdPhasesClear,
};
```

- [ ] **Step 6: Extend the dispatch in gsd-tools.cjs**

Find the existing `case 'requirements': {` block (around line 698) and replace it with:

```javascript
    case 'requirements': {
      const subcommand = args[1];
      if (subcommand === 'mark-complete') {
        milestone.cmdRequirementsMarkComplete(cwd, args.slice(2), raw);
      } else if (subcommand === 'check-off') {
        const all = args.includes('--all');
        const phase = args.slice(2).find(a => !a.startsWith('--')) || null;
        if (!all && !phase) {
          error('requirements check-off requires a <phase> or --all');
        }
        milestone.cmdRequirementsCheckOff(cwd, { phase, all }, raw);
      } else {
        error('Unknown requirements subcommand. Available: mark-complete, check-off');
      }
      break;
    }
```

- [ ] **Step 7: Run the test to verify it PASSES**

Run: `bash /tmp/req_test2.sh`
Expected: `ALL PASS`.

- [ ] **Step 8: Re-run Task 1's characterization test (no regression)**

Run: `bash /tmp/req_test1.sh`
Expected: `ALL PASS`.

- [ ] **Step 9: Checkpoint (no commit — GSD core is not a git repo)**

Both test scripts print `ALL PASS`.

---

## Task 3: Add `--all` sweep mode

The `--all` branch already exists in `cmdRequirementsCheckOff` (Task 2, Step 4). This task
adds an explicit test that it sweeps across multiple phase dirs.

**Files:** none (verification only — logic already implemented in Task 2).

- [ ] **Step 1: Write the `--all` test script**

```bash
cat > /tmp/req_test3.sh <<'SH'
set -u
GSD="$HOME/.claude/get-shit-done/bin/gsd-tools.cjs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/.planning/phases/14-history" "$TMP/.planning/phases/15-foo" "$TMP/.planning/phases/16-bar"
cat > "$TMP/.planning/REQUIREMENTS.md" <<'MD'
- [ ] **OPS-02**: history
- [ ] **SIG-07**: pe
- [ ] **SIG-05**: earnings
MD
printf -- '---\nrequirements-completed: [OPS-02]\n---\n' > "$TMP/.planning/phases/14-history/14-01-SUMMARY.md"
printf -- '---\nrequirements-completed: [SIG-07]\n---\n' > "$TMP/.planning/phases/15-foo/15-01-SUMMARY.md"
printf -- '---\nrequirements-completed: [SIG-05]\n---\n' > "$TMP/.planning/phases/16-bar/16-01-SUMMARY.md"

# No --raw → pretty JSON; grep the pretty-printed key (note the space after the colon).
out="$(node "$GSD" requirements check-off --all --cwd "$TMP")"; rc=$?
echo "ALL OUT: $out"
fail=0
[ "$rc" -eq 0 ] || { echo "FAIL: --all exit $rc"; fail=1; }
for id in OPS-02 SIG-07 SIG-05; do
  grep -q "^- \[x\] \*\*$id\*\*" "$TMP/.planning/REQUIREMENTS.md" || { echo "FAIL: $id not ticked by --all"; fail=1; }
done
printf '%s' "$out" | grep -q '"summaries_scanned": 3' || { echo "FAIL: expected 3 summaries scanned"; fail=1; }
[ "$fail" -eq 0 ] && echo "ALL PASS" || echo "HAD FAILURES"
SH
```

- [ ] **Step 2: Run it**

Run: `bash /tmp/req_test3.sh`
Expected: `ALL PASS` (logic from Task 2 already supports `--all`).

- [ ] **Step 3: Checkpoint**

`ALL PASS`.

---

## Task 4: Fix the broken `execute-plan.md` call site

**Files:**
- Modify: `$HOME/.claude/get-shit-done/workflows/execute-plan.md:420-428`

- [ ] **Step 1: Replace the `update_requirements` step body**

Replace lines 420-428 (the `<step name="update_requirements">` block) with:

```markdown
<step name="update_requirements">
Mark completed requirements by harvesting REQ-IDs from this phase's SUMMARY frontmatter
(`requirements-completed`) and ticking them in REQUIREMENTS.md. Deterministic — no manual
ID extraction. Idempotent and safe to run after every plan.

```bash
node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs" requirements check-off "${PHASE}" 2>&1 || true
```

If `.planning/REQUIREMENTS.md` is absent (between milestones), this is a silent no-op.
</step>
```

- [ ] **Step 2: Verify the old broken call is gone and the new one is present**

Run: `grep -n "gsd-sdk query requirements.mark-complete\|requirements check-off" "$HOME/.claude/get-shit-done/workflows/execute-plan.md"`
Expected: no `gsd-sdk query requirements.mark-complete` line; one `requirements check-off "${PHASE}"` line.

- [ ] **Step 3: Checkpoint**

Grep output confirms the swap.

---

## Task 5: Add the `--all` guarantee sweep to `complete-milestone.md`

**Files:**
- Modify: `$HOME/.claude/get-shit-done/workflows/complete-milestone.md` (`verify_readiness` step)

- [ ] **Step 1: Insert the sweep at the start of `verify_readiness`**

In `complete-milestone.md`, find the line `<step name="verify_readiness">` (around line 84).
Immediately after it, insert:

```markdown

**Auto check-off before readiness check (deterministic guarantee):**

Sweep every phase SUMMARY and tick all completed REQ-IDs so the readiness count and the
archived REQUIREMENTS.md reflect reality regardless of per-plan discipline:

```bash
node "$HOME/.claude/get-shit-done/bin/gsd-tools.cjs" requirements check-off --all 2>&1 || true
```

This runs before the "N/M v1 requirements checked off" computation below and before
REQUIREMENTS.md is archived/removed.

```

- [ ] **Step 2: Verify the sweep is present and positioned before archival**

Run: `grep -n "requirements check-off --all\|name=\"verify_readiness\"\|git rm .planning/REQUIREMENTS.md" "$HOME/.claude/get-shit-done/workflows/complete-milestone.md"`
Expected: the `requirements check-off --all` line appears AFTER `name="verify_readiness"` and BEFORE the `git rm .planning/REQUIREMENTS.md` line.

- [ ] **Step 3: Checkpoint**

Grep confirms ordering.

---

## Task 6: End-to-end verification (simulates the v1.3 failure)

**Files:** none (verification only).

- [ ] **Step 1: Build a fixture reproducing a stale milestone and run the sweep**

```bash
cat > /tmp/req_e2e.sh <<'SH'
set -u
GSD="$HOME/.claude/get-shit-done/bin/gsd-tools.cjs"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/.planning/phases/17-limit/" "$TMP/.planning/phases/18-tests/"
cat > "$TMP/.planning/REQUIREMENTS.md" <<'MD'
## v1 Requirements
- [ ] **RISK-01**: limit buy
- [ ] **RISK-02**: flag
- [ ] **TEST-09**: fallback
- [ ] **TEST-10**: config
## Traceability
| REQ-ID | Phase | Status |
|--------|-------|--------|
| RISK-01 | Phase 17 | Pending |
| TEST-09 | Phase 18 | Pending |
MD
printf -- '---\nrequirements-completed: [RISK-01, RISK-02]\n---\n' > "$TMP/.planning/phases/17-limit/17-01-SUMMARY.md"
printf -- '---\nrequirements-completed: [TEST-09, TEST-10]\n---\n' > "$TMP/.planning/phases/18-tests/18-01-SUMMARY.md"

echo "BEFORE:"; grep -c '^- \[ \]' "$TMP/.planning/REQUIREMENTS.md"
node "$GSD" requirements check-off --all --cwd "$TMP" --raw
echo "AFTER unchecked count (expect 0):"; grep -c '^- \[ \]' "$TMP/.planning/REQUIREMENTS.md" || true
echo "AFTER table Pending count (expect 0):"; grep -c '| Pending |' "$TMP/.planning/REQUIREMENTS.md" || true
SH
bash /tmp/req_e2e.sh
```

- [ ] **Step 2: Confirm the result**

Expected: `BEFORE` shows 4 unchecked; after the sweep the unchecked count is `0` and the
table `Pending` count is `0` — the exact stale-at-close failure is eliminated.

- [ ] **Step 3: Clean up test scripts**

Run: `rm -f /tmp/req_test1.sh /tmp/req_test2.sh /tmp/req_test2.out /tmp/req_test2.err /tmp/req_test3.sh /tmp/req_e2e.sh`

- [ ] **Step 4: Commit the plan doc (in the algo-trade repo only)**

```bash
cd "C:/Users/Darson/Projects/algo trade"
git add docs/superpowers/plans/2026-06-07-gsd-requirement-checkoff.md
git commit -m "docs: implementation plan for GSD auto REQ-ID check-off"
```

---

## Self-Review Notes

- **Spec coverage:** helper extraction (Task 1), `check-off <phase>` (Task 2),
  `--all` (Task 3), execute-plan fix (Task 4), complete-milestone sweep (Task 5),
  E2E + phase precision + lenient key + idempotency + missing-file + warn (Tasks 2/3/6).
  All 10 spec test cases are covered across the task test scripts.
- **No git commits for GSD core** — intentional; `~/.claude` is not a repo. Checkpoints are
  test-green confirmations.
- **Type consistency:** `markRequirementsComplete` returns `{ content, marked,
  alreadyComplete, notFound }`; both callers destructure exactly those names. Dispatch
  passes `{ phase, all }`; `cmdRequirementsCheckOff` reads `opts.phase` / `opts.all`.
- **Post-update reminder:** run `/gsd-reapply-patches` after any `/gsd-update`.
