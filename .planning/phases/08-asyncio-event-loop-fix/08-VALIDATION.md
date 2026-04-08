---
phase: 8
slug: asyncio-event-loop-fix
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-09
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| **Config file** | none — asyncio mode detected via plugin default (STRICT) |
| **Quick run command** | `pytest tests/test_run_scan.py tests/test_sell_scan.py -q` |
| **Full suite command** | `pytest -q` |
| **Estimated runtime** | ~10 seconds (252 tests) |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_run_scan.py tests/test_sell_scan.py -q`
- **After every plan wave:** Run `pytest -q`
- **Before `/gsd-verify-work`:** Full suite must be green (252+ tests)
- **Max feedback latency:** ~10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 8-01-01 | 01 | 1 | ASYNC-01 | — | N/A | integration | `pytest tests/test_run_scan.py -q` | ✅ | ⬜ pending |
| 8-01-02 | 01 | 1 | ASYNC-02 | — | N/A | integration | `pytest tests/test_sell_scan.py -q` | ✅ | ⬜ pending |
| 8-01-03 | 01 | 1 | ASYNC-03 | — | N/A | integration | `pytest tests/test_run_scan.py::test_run_scan_excludes_etfs_from_stock_universe -q` | ✅ | ⬜ pending |
| 8-01-04 | 01 | 1 | ASYNC-04 | — | N/A | manual | grep audit comments in main.py | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Optional new test file for explicit `asyncio.to_thread` call-site verification:
- [ ] `tests/test_async_wrapping.py` — spy on `asyncio.to_thread` to verify wrapping for ASYNC-01, ASYNC-02 (optional — existing tests cover functional behavior; planner to decide)

**Note:** Existing tests in `test_run_scan.py` and `test_sell_scan.py` patch at `main.*` namespace and continue to work correctly after `asyncio.to_thread` wrapping — mock interception is preserved through `to_thread`. No existing test rewrites needed. [VERIFIED: experimental confirmation in research session]

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Audit comments present on `get_top_sp500_by_fundamentals` (line 62), `partition_watchlist` (lines 70, 289) | ASYNC-04 | Comment presence is not a runtime behavior | `grep -n "P8-audit" main.py` — expect 3 matches |
| No heartbeat timeout during live scan | All ASYNC | Requires live Discord bot + scheduler run | Run bot, trigger scan, observe gateway logs for heartbeat warnings |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
