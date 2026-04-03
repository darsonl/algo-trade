---
phase: 5
slug: position-monitoring
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-03
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | none — existing pytest setup |
| **Quick run command** | `pytest tests/test_database.py tests/test_positions.py -v` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_database.py tests/test_positions.py -v`
- **After every plan wave:** Run `pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | POS-01 | unit | `pytest tests/test_database.py -k position -v` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | POS-01 | unit | `pytest tests/test_database.py -k position -v` | ❌ W0 | ⬜ pending |
| 05-01-03 | 01 | 1 | POS-02 | unit | `pytest tests/test_positions.py -k create_position -v` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 2 | POS-02 | integration | `pytest tests/test_discord_buttons.py -v` | ✅ | ⬜ pending |
| 05-02-02 | 02 | 2 | POS-05 | unit | `pytest tests/test_discord_buttons.py -k exposure -v` | ❌ W0 | ⬜ pending |
| 05-03-01 | 02 | 2 | POS-06 | unit | `pytest tests/test_run_scan.py -k open_position -v` | ❌ W0 | ⬜ pending |
| 05-04-01 | 03 | 3 | POS-03 | unit | `pytest tests/test_positions.py -k summary -v` | ❌ W0 | ⬜ pending |
| 05-04-02 | 03 | 3 | POS-04 | integration | `pytest tests/test_discord_bot.py -k positions_command -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_positions.py` — stubs for POS-01, POS-02, POS-03, POS-04, POS-05
- [ ] Extend `tests/test_database.py` — stubs for positions CRUD
- [ ] Extend `tests/test_run_scan.py` — stub for POS-06 skip guard
- [ ] Extend `tests/test_discord_buttons.py` — stubs for exposure check (POS-05)
- [ ] Extend `tests/test_discord_bot.py` — stub for `/positions` command (POS-04)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `/positions` embed renders correctly in Discord | POS-04 | Visual layout requires Discord client | Run bot in dry-run, invoke `/positions`, confirm embed shows ticker/shares/avg cost/P&L% columns |
| Exposure warning appears in Discord interaction | POS-05 | Warning message is ephemeral Discord response | Approve a buy that would exceed MAX_POSITION_SIZE_USD; confirm warning sent to approver |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
