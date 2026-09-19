# docs/superpowers — what is in here, and which file to read

## Start here

**The newest handoff is the only one with current status: [`HANDOFF-2026-09-19.md`](HANDOFF-2026-09-19.md).**

Every other handoff has been superseded *for status* and its "current state" block is stale by
construction. They are kept because each one is still the reference for specific reasoning that was
never written down anywhere else — the table below says which.

## How the handoff chain works

Each handoff supersedes the previous one **for status only**, and its header names what the
predecessor is still the reference for. That convention is deliberate: status has exactly one home,
reasoning accumulates. Two rules follow from it.

- **Never read an old handoff for state.** Git SHAs, test counts, open PRs and the "still open" list
  are only correct in the newest file. An old one will confidently tell you the wrong thing.
- **Do read an old handoff for *why*.** The reasoning sections were written while the decision was
  being made and usually contain the measurements that justified it.

Durable design rationale is progressively migrated into `CLAUDE.md` *Key Design Decisions*. When the
two disagree, CLAUDE.md wins — it is maintained; a handoff is a snapshot.

## Handoff index

Read top-down: newest first.

| File | Still the reference for |
|---|---|
| [`HANDOFF-2026-09-19.md`](HANDOFF-2026-09-19.md) | **CURRENT STATUS.** #62 merged and the Schwab login moved to a weekly Saturday rhythm; **why `creation_timestamp` is the only proof a real login happened**; and a CI watch that reported silence because its filter could not tell "nothing yet" from "I cannot see" |
| [`HANDOFF-2026-09-18.md`](HANDOFF-2026-09-18.md) | The 25-hour stuck process: **a slept-through exit discarded by the 1-second misfire default**, and `on_ready` re-running on every gateway RESUME; why the grace change is scoped to the exits and not the scans; **the recovery code sitting behind the line that threw**; the 09-17 exit claim corrected |
| [`HANDOFF-2026-09-17.md`](HANDOFF-2026-09-17.md) | The 2026-09-17 session verified (§1b passed 10/10 — **#61 confirmed live**); the ETF `info.regularMarketPrice` label and the first use of `_SCREEN_PRICE_FIELDS`' second entry; **`OPS_USER_IDS` set**; the four locks as a chain; **why the Schwab refresh token cannot self-renew** |
| [`HANDOFF-2026-09-16b.md`](HANDOFF-2026-09-16b.md) | The 2026-09-16 session verified (§1a passed 9/9, the #60 sample start is VALID); **#61 the ETF `reference_price` fix** and its ETF-only rule boundary; the two ETF items deliberately left unbuilt; the corrected 53-line credential baseline |
| [`HANDOFF-2026-09-16.md`](HANDOFF-2026-09-16.md) | #59 the expiry deadline; #60 the analyst preregistration; the Saturday-row delete; **why the 09-16 §1b check was a precondition** |
| [`HANDOFF-2026-09-15b.md`](HANDOFF-2026-09-15b.md) | The 2026-09-15 session verified live (all 7 checks); #57 key redaction; #58 news provider chain; **the §1b checklist itself**, which 09-16 points back to rather than restating |
| [`HANDOFF-2026-09-15.md`](HANDOFF-2026-09-15.md) | #53–#56: the volume gate's removal and the 4-year study behind it, the post-scan exit, the 09-15 rule boundary |
| [`HANDOFF-2026-09-13c.md`](HANDOFF-2026-09-13c.md) | The 2026-09-14 scan checklist as originally written, and the run-up to that session |
| [`HANDOFF-2026-09-13b.md`](HANDOFF-2026-09-13b.md) | §2 the #45–#48 reasoning; §3 the 2026-09-14 rule boundary |
| [`HANDOFF-2026-09-13.md`](HANDOFF-2026-09-13.md) | §2 **the wedged scheduler** (cited from `CLAUDE.md`); §3 the orphaned-process and `StopAtDurationEnd` defects; §4 the session lifecycle |
| [`HANDOFF-2026-08-30.md`](HANDOFF-2026-08-30.md) | §1 **the NaN/NULL trap**; §2b **why `cp` is unsafe on this WAL database** |
| [`HANDOFF-2026-08-23.md`](HANDOFF-2026-08-23.md) | What the total-return fix and the gate-provenance change actually **do** |
| [`HANDOFF-2026-08-22c.md`](HANDOFF-2026-08-22c.md) | The 78% fix's design, and the Codex review that reshaped it |
| [`HANDOFF-2026-08-22b.md`](HANDOFF-2026-08-22b.md) | The shadow log's build (Tasks 1–8) and the acceptance scan |
| [`HANDOFF-2026-08-22.md`](HANDOFF-2026-08-22.md) | The `SCAN_TIMEZONE` fix, the analyst-chain probes, and the strategy-validation finding |
| [`HANDOFF-2026-08-21.md`](HANDOFF-2026-08-21.md) | The spec-v4 build sequence, the first live run, the analyst-chain rebuild. Cited as **source** by `specs/2026-08-21-strategy-validation-design.md` |
| [`HANDOFF-2026-08-16.md`](HANDOFF-2026-08-16.md) | **Why the design rounds were abandoned**, and the GitHub/uv workflow quirks |
| [`HANDOFF-2026-08-15.md`](HANDOFF-2026-08-15.md) | **Phase 0 ledger internals**, and (with 08-16) why the design rounds were abandoned |

## Removed

Two handoffs have been deleted. Both remain in git history in full:

```bash
git log --all --diff-filter=D -- docs/superpowers/HANDOFF-2026-08-14.md   # find the SHA
git show <sha>^:docs/superpowers/HANDOFF-2026-08-14.md                    # read it
```

- **`HANDOFF-2026-08-14.md`** (removed 2026-09-15) — the only file in the chain its successor
  described as carrying no retained value: *"describes an approach that has since been abandoned"*.
  Nothing cited it but 08-15's supersession line.
- **`HANDOFF-2026-08-28.md`** (removed 2026-09-15) — existed only for its §1, the correction of the
  marks-due date. That correction is now a **banner at the head of both files that depended on it**,
  `HANDOFF-2026-08-22c.md` and `HANDOFF-2026-08-23.md`, so each reads correctly standalone; its
  durable lesson is in `CLAUDE.md` (*a mark-due date must be re-derived from the rows, never copied
  from the last document that mentioned it*).

**Before deleting a handoff, grep for its informal name too** — `08-28`, `the 08-16 file` — not just
`HANDOFF-2026-08-28.md`. Two live references to 08-28 sat in `HANDOFF-2026-08-30.md` under the
informal form and a filename search missed both; they were repointed at `CLAUDE.md` before the
delete. This is the same failure the deleted file itself documents: a fact quoted forward by name
rather than re-derived from the source.

## The other directories

| Path | What |
|---|---|
| `specs/` | Accepted designs, one per subsystem — the *intended* behaviour at the time it was agreed. Written before implementation, not updated after |
| `plans/` | Task-by-task implementation plans derived from the specs. A plan being written is **not** a plan being executed — `2026-08-14-screener-determinism.md` Task 2 sat unexecuted for a month while the bug it described shipped |
| `reviews/` | Prompts used to drive external review rounds |
| `codex_recommendations.md` | The Codex review backlog these rounds worked through |

## Adding a handoff

Name it `HANDOFF-YYYY-MM-DD.md`, suffixed `b`, `c`, … for a second or third session on the same day.
The header must say which file it supersedes **and what that file is still the reference for** — the
table above is built from those lines, so an omission loses the predecessor's reason to exist.
Then update the table here, and repoint `MEMORY.md`'s START HERE line at the new file.
