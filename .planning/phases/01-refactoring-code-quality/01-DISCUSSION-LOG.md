# Phase 1: Refactoring & Code Quality - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-31
**Phase:** 01-refactoring-code-quality
**Areas discussed:** Function signature changes, Config singleton removal, DB column migration, Test impact from import changes

---

## Function Signature Changes

| Option | Description | Selected |
|--------|-------------|----------|
| Pass as required parameter | Change both signatures to accept `yf_ticker: yf.Ticker`. run_scan creates one object and passes it to both. Cleanest — no hidden state, easy to test. | ✓ |
| Make it optional with fallback | Add `yf_ticker: yf.Ticker \| None = None` — backwards-compatible but keeps old behavior as footgun. | |
| Create a combined fetch function | Merge both fetchers into `fetch_ticker_data(ticker)` returning both datasets. | |

**User's choice:** Pass as required parameter
**Notes:** Consistent with the pure-function, explicit-parameter pattern already used everywhere in the codebase.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Already optional — just create once in run_scan | `analyze_ticker(ticker, client=None)` already accepts a client. No signature change needed. | ✓ |
| Make it required param too | Remove `client=None` default — force callers to always provide the client. | |

**User's choice:** Already optional — just create once in run_scan
**Notes:** The existing optional parameter pattern is sufficient. `run_scan` creates `anthropic.Anthropic()` once and passes it.

---

## Config Singleton Removal

| Option | Description | Selected |
|--------|-------------|----------|
| Validate immediately after creation in main() | `config = Config()` then `config.validate()` as first thing in main(). | ✓ |
| Keep validate() in Config.__post_init__ | Auto-validate on construction. | |

**User's choice:** Validate immediately after creation in main()
**Notes:** Preserves fast-fail behavior while moving ownership to main().

---

| Option | Description | Selected |
|--------|-------------|----------|
| No change needed | Tests already do `Config()` with env overrides. Removing singleton doesn't affect them. | ✓ |
| Need to audit and update | Verify no test imports `from config import config`. | |

**User's choice:** No change needed
**Notes:** Confirmed — only `main.py` imports the singleton instance.

---

## DB Column Migration

| Option | Description | Selected |
|--------|-------------|----------|
| ALTER TABLE with IF NOT EXISTS guard | `ALTER TABLE recommendations ADD COLUMN earnings_growth REAL` in try/except. Safe for fresh + existing DBs. | ✓ |
| DROP + recreate in init_db | Drop and recreate table on startup. Destroys existing data. | |
| Separate migration script | One-off migrate.py users run manually. Operational friction. | |

**User's choice:** ALTER TABLE with IF NOT EXISTS guard (try/except OperationalError)
**Notes:** Zero data loss. SQLite raises OperationalError on duplicate column name — catch and ignore.

---

| Option | Description | Selected |
|--------|-------------|----------|
| NULL is fine | Historical rows won't have earnings_growth. Column is REAL (nullable). | ✓ |
| Backfill with 0.0 | UPDATE existing rows. Misleading — 0.0 implies zero growth, not unknown. | |

**User's choice:** NULL is fine
**Notes:** NULL correctly represents "not recorded at time of recommendation".

---

## Test Impact from Import Changes

| Option | Description | Selected |
|--------|-------------|----------|
| No comment needed | Standard mock.patch behavior. A comment would be noise. | ✓ |
| Add a comment near the import | `# Tests: mock with @mock.patch('screener.fundamentals.yf')` | |

**User's choice:** No comment needed
**Notes:** Existing tests are unaffected — they only test pure functions. Phase 4 tests will use standard mock.patch paths.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Direct await in main() | Replace dispatch with direct async call. Cleaner. | ✓ |
| Keep dispatch, document it | Add comment explaining the custom event pattern. | |

**User's choice:** Direct await in main()
**Notes:** Eliminates the non-standard `bot.dispatch('manual_scan')` pattern entirely.

---

*Discussion completed: 2026-03-31*
