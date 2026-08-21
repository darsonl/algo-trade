# Forward Shadow Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record every candidate the scan pipeline evaluates — with the full information set as of the decision, the analyst's verdict, the human's action, and the realised forward prices — so the strategy's actual performance can eventually be measured.

**Architecture:** A new `research/` package holding a pure observation builder and a fail-safe recorder, two new SQLite tables in the existing database, hooks at each exit point of both scan loops, and a scheduled job that fills in forward price marks. The recorder never gates and never raises: a research instrument that can abort a scan is a liability.

**Tech Stack:** Python 3.12 (`.venv` only), SQLite via the repo's `get_cursor` / `immediate_transaction` helpers, pandas + yfinance for forward marks, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-strategy-validation-design.md` (§2 covers this plan; §3–5 are Subsystem A, a separate plan)

## Global Constraints

- **Always use `.venv`.** Run `.venv/Scripts/python.exe -m pytest -q`, never bare `pytest`. System Python is 3.14 and cannot install the lock.
- **The recorder must never abort a scan.** Every write is wrapped; exceptions are logged and swallowed, following `send_ops_alert`'s precedent.
- **The recorder never gates.** No shadow-log read may influence whether a recommendation is posted.
- **It runs in dry run.** Dry-run scans produce genuine screening decisions; excluding them discards most of the sample.
- **Session dates use `market_time.market_session_date()`**, never `date('now')` or `date(..., 'localtime')`. Every time-dependent function takes an optional `instant` so tests can pin the clock.
- **TDD.** No production code without a failing test first. Mutation-test the load-bearing tests: two tests passed vacuously last cycle and were found only by mutating the code and watching nothing fail.
- **Commit a checkpoint before mutation-testing.** `git checkout <file>` reverts to HEAD and has silently deleted uncommitted work once.
- Run `.venv/Scripts/python.exe -m ruff check .` before each commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `research/__init__.py` | Package marker. |
| `research/shadow_log.py` | Stage/outcome enums, `ShadowObservation` dataclass, pure `build_observation`, fail-safe `record`. |
| `research/outcomes.py` | Forward price marks at 1w/1m/3m/6m against SPY. |
| `database/models.py` | Two new tables + indexes (modify). |
| `database/queries.py` | `record_shadow_observation`, `set_shadow_human_action`, `pending_shadow_marks`, `record_shadow_outcome` (modify). |
| `analyst/claude_analyst.py` | Add `model_used` to the result dict; add optional `on_response` callback (modify). |
| `main.py` | Hooks at each scan exit point, both loops (modify). |
| `discord_bot/bot.py` | Record the human action and its latency (modify). |
| `scripts/shadow_report.py` | Read-only funnel report. |

---

### Task 1: Schema — the two shadow tables

**Files:**
- Modify: `database/models.py`
- Test: `tests/test_shadow_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: tables `shadow_observations` and `shadow_outcomes`; function `_create_shadow_tables(conn) -> None` called from `initialize_db`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shadow_schema.py`. Note it builds a PRE-EXISTING database first — the repo's convention, because a migration that only works on a fresh file is not a migration.

```python
"""The shadow tables, created against a PRE-EXISTING database.

Migrations in this repo are always tested against a database that already
exists and lacks the new objects, because that is the only case that occurs in
production. A test that creates a fresh file proves nothing about upgrade.
"""
import sqlite3

from database.models import initialize_db


def _preexisting_db(path):
    """A database that predates the shadow tables but is otherwise REAL.

    `status` is included deliberately. It has been in `CREATE TABLE
    recommendations` since this project's first commit, and
    `_create_active_recommendation_index` builds a PARTIAL index whose
    `WHERE status IN ('pending','approved')` clause is parsed against the table
    at creation time. A fixture without it describes a database that has never
    existed -- and forces production to carry a migration for a state that
    cannot occur, which is the wrong direction of fix.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE recommendations ("
        "    id INTEGER PRIMARY KEY,"
        "    ticker TEXT,"
        "    status TEXT NOT NULL DEFAULT 'pending'"
        ")"
    )
    conn.commit()
    conn.close()


def test_shadow_tables_are_created_on_an_existing_database(tmp_path):
    db = str(tmp_path / "pre.db")
    _preexisting_db(db)

    initialize_db(db)

    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "shadow_observations" in names
    assert "shadow_outcomes" in names


def test_shadow_observation_columns(tmp_path):
    db = str(tmp_path / "a.db")
    initialize_db(db)
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_observations)")}
    assert {
        "id", "session_date", "observed_at", "ticker", "scan_kind",
        "stage_reached", "outcome", "reject_reason",
        "fundamentals_json", "technicals_json", "headlines_json", "macro_json",
        "analyst_provider", "analyst_model", "analyst_signal",
        "analyst_confidence", "analyst_prompt_sha256", "analyst_raw_response",
        "cache_hit", "recommendation_id", "reference_price",
        "human_action", "human_action_at",
    } <= cols


def test_one_outcome_row_per_observation_and_horizon(tmp_path):
    """Re-marking the same horizon must not create a second row: the marking job
    reruns on every scan and would otherwise multiply rows without bound."""
    db = str(tmp_path / "b.db")
    initialize_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome) VALUES"
        " ('2026-08-20','2026-08-20T13:45:00Z','AAPL','stock','analyst','recommended')"
    )
    conn.execute(
        "INSERT INTO shadow_outcomes (observation_id, horizon, as_of, price)"
        " VALUES (1,'1w','2026-08-27',1.0)"
    )
    try:
        conn.execute(
            "INSERT INTO shadow_outcomes (observation_id, horizon, as_of, price)"
            " VALUES (1,'1w','2026-08-27',2.0)"
        )
        raise AssertionError("duplicate (observation_id, horizon) was accepted")
    except sqlite3.IntegrityError:
        pass


def test_initialize_db_is_idempotent_for_shadow_tables(tmp_path):
    db = str(tmp_path / "c.db")
    initialize_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome) VALUES"
        " ('2026-08-20','2026-08-20T13:45:00Z','MSFT','stock','universe','skipped_open_position')"
    )
    conn.commit()
    conn.close()

    initialize_db(db)  # second startup

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM shadow_observations").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_schema.py -q`
Expected: FAIL — `sqlite3.OperationalError: no such table: shadow_observations`.

- [ ] **Step 3: Write minimal implementation**

Add to `database/models.py`, above `initialize_db`:

```python
def _create_shadow_tables(conn) -> None:
    """Research tables: every candidate the pipeline saw, and its forward marks.

    Separate from `recommendations` on purpose. That table is operational -- the
    approval path, the dupe guard and `idx_active_rec_per_ticker` all read it,
    and it holds only candidates that survived every filter. Research needs the
    opposite: every candidate INCLUDING the rejected ones, because a conversion
    rate whose denominator omits the rejects is not a conversion rate.

    `human_action` lives here rather than in its own table because it is
    one-to-one with the observation and never re-stated.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shadow_observations (
               id                    INTEGER PRIMARY KEY AUTOINCREMENT,
               session_date          TEXT NOT NULL,
               observed_at           TEXT NOT NULL,
               ticker                TEXT NOT NULL,
               scan_kind             TEXT NOT NULL,
               stage_reached         TEXT NOT NULL,
               outcome               TEXT NOT NULL,
               reject_reason         TEXT,
               fundamentals_json     TEXT,
               technicals_json       TEXT,
               headlines_json        TEXT,
               macro_json            TEXT,
               analyst_provider      TEXT,
               analyst_model         TEXT,
               analyst_signal        TEXT,
               analyst_confidence    TEXT,
               analyst_prompt_sha256 TEXT,
               analyst_raw_response  TEXT,
               cache_hit             INTEGER NOT NULL DEFAULT 0,
               recommendation_id     INTEGER,
               reference_price       REAL,
               human_action          TEXT,
               human_action_at       TEXT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_shadow_obs_session
               ON shadow_observations(session_date, ticker)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS shadow_outcomes (
               id                   INTEGER PRIMARY KEY AUTOINCREMENT,
               observation_id       INTEGER NOT NULL REFERENCES shadow_observations(id),
               horizon              TEXT NOT NULL,
               as_of                TEXT NOT NULL,
               price                REAL,
               return_pct           REAL,
               benchmark_price      REAL,
               benchmark_return_pct REAL,
               UNIQUE(observation_id, horizon)
           )"""
    )
    conn.commit()
```

Then call it from `initialize_db`, on the line immediately after `_backfill_intended_session_dates(conn)`:

```python
    _create_shadow_tables(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_schema.py -q`
Expected: 4 passed.

Then the whole suite, to confirm nothing regressed:
Run: `.venv/Scripts/python.exe -m pytest -q` — expect the existing count plus 4.

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python.exe -m ruff check .
git add database/models.py tests/test_shadow_schema.py
git commit -m "feat: shadow-log tables for forward strategy evidence"
```

---

### Task 2: Pure observation builder

**Files:**
- Create: `research/__init__.py`, `research/shadow_log.py`
- Test: `tests/test_shadow_observation.py`

**Interfaces:**
- Consumes: nothing (deliberately pure — no DB, no clock, no network).
- Produces:
  - `STAGES: tuple[str, ...]` and `OUTCOMES: tuple[str, ...]`
  - `@dataclass ShadowObservation` with fields matching the table columns
  - `build_observation(ticker, scan_kind, stage, outcome, *, session_date, observed_at, reject_reason=None, fundamentals=None, technicals=None, headlines=None, macro=None, analysis=None, cache_hit=False, recommendation_id=None, reference_price=None) -> ShadowObservation`

- [ ] **Step 1: Write the failing test**

Create `tests/test_shadow_observation.py`:

```python
"""The observation builder is pure: no DB, no clock, no network, no mocks."""
import json

import pytest

from research.shadow_log import (
    OUTCOMES,
    STAGES,
    ShadowObservation,
    build_observation,
)


def _obs(**kw):
    base = dict(
        ticker="AAPL",
        scan_kind="stock",
        stage="fundamental",
        outcome="rejected_fundamental",
        session_date="2026-08-20",
        observed_at="2026-08-20T13:45:00Z",
    )
    base.update(kw)
    return build_observation(**base)


def test_minimal_observation_carries_the_funnel_position():
    o = _obs()
    assert isinstance(o, ShadowObservation)
    assert (o.ticker, o.scan_kind) == ("AAPL", "stock")
    assert (o.stage_reached, o.outcome) == ("fundamental", "rejected_fundamental")
    assert o.session_date == "2026-08-20"


def test_unknown_stage_is_rejected():
    """A typo'd stage would silently create a funnel bucket nobody reads."""
    with pytest.raises(ValueError, match="stage"):
        _obs(stage="fundamentals")


def test_unknown_outcome_is_rejected():
    with pytest.raises(ValueError, match="outcome"):
        _obs(outcome="nope")


def test_dicts_are_serialised_to_json_text():
    o = _obs(fundamentals={"trailingPE": 34.2}, technicals={"rsi": 53.8},
             macro={"vix_level": 14.1})
    assert json.loads(o.fundamentals_json) == {"trailingPE": 34.2}
    assert json.loads(o.technicals_json) == {"rsi": 53.8}
    assert json.loads(o.macro_json) == {"vix_level": 14.1}


def test_headlines_are_serialised_as_a_list():
    o = _obs(headlines=["a", "b"])
    assert json.loads(o.headlines_json) == ["a", "b"]


def test_absent_payloads_stay_none_not_empty_json():
    """None and {} are different: None means the stage was never reached, {}
    means it was reached and produced nothing. Collapsing them loses the
    distinction the funnel is FOR."""
    o = _obs()
    assert o.fundamentals_json is None
    assert o.technicals_json is None
    assert o.headlines_json is None


def test_empty_dict_is_recorded_as_empty_json():
    o = _obs(fundamentals={})
    assert o.fundamentals_json == "{}"


def test_analysis_is_unpacked_into_attribution_columns():
    """provider AND model, because Gemini meters per model and neither the
    analyst cache nor the analyst's return dict has ever recorded which model
    answered."""
    o = _obs(
        stage="analyst",
        outcome="rejected_signal",
        analysis={
            "signal": "HOLD",
            "confidence": "medium",
            "provider_used": "gemini",
            "model_used": "gemini-3.1-flash-lite",
            "raw_response": "SIGNAL: HOLD",
            "prompt_sha256": "abc123",
        },
    )
    assert o.analyst_signal == "HOLD"
    assert o.analyst_confidence == "medium"
    assert o.analyst_provider == "gemini"
    assert o.analyst_model == "gemini-3.1-flash-lite"
    assert o.analyst_raw_response == "SIGNAL: HOLD"
    assert o.analyst_prompt_sha256 == "abc123"


def test_analysis_missing_model_does_not_raise():
    """An older analyst result without model_used must still record."""
    o = _obs(stage="analyst", outcome="rejected_signal",
             analysis={"signal": "HOLD", "provider_used": "gemini"})
    assert o.analyst_model is None
    assert o.analyst_signal == "HOLD"


def test_cache_hit_is_stored_as_int():
    assert _obs(cache_hit=True).cache_hit == 1
    assert _obs(cache_hit=False).cache_hit == 0


def test_every_stage_and_outcome_constant_is_a_plain_string():
    assert all(isinstance(s, str) for s in STAGES)
    assert all(isinstance(o, str) for o in OUTCOMES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_observation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'research'`.

- [ ] **Step 3: Write minimal implementation**

Create `research/__init__.py` (empty file).

Create `research/shadow_log.py`:

```python
"""Forward shadow log: every candidate the pipeline saw, and what became of it.

The recorder exists because no retrospective backtest of this strategy is
possible -- two of its three entry gates cannot be reconstructed historically
(see specs/2026-08-21-strategy-validation-design.md). Forward recording is the
only way to gather evidence about the fundamental filter, the analyst and the
human approver.

This module is PURE. Building an observation touches no database, no clock and
no network, so the funnel's semantics can be tested without mocks. The clock and
the database live in the caller.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# Ordered: a candidate reaches stages left to right and stops where it fails.
STAGES = (
    "universe",      # entered the loop; skipped before any data was fetched
    "fundamental",   # fundamentals fetched
    "analyst",       # headlines fetched and the analyst consulted
    "technical",     # technical data fetched
    "recommended",   # posted to Discord
)

OUTCOMES = (
    "skipped_recommended_today",
    "skipped_open_position",
    "skipped_active_recommendation",
    "rejected_fundamental",
    "skipped_quota_exhausted",
    "rejected_signal",      # analyst returned HOLD or SKIP
    "rejected_technical",   # analyst said BUY; the technical filter refused
    "recommended",
    "error",
)


@dataclass
class ShadowObservation:
    """One candidate's exit from the pipeline. Field names match the columns."""

    session_date: str
    observed_at: str
    ticker: str
    scan_kind: str
    stage_reached: str
    outcome: str
    reject_reason: str | None = None
    fundamentals_json: str | None = None
    technicals_json: str | None = None
    headlines_json: str | None = None
    macro_json: str | None = None
    analyst_provider: str | None = None
    analyst_model: str | None = None
    analyst_signal: str | None = None
    analyst_confidence: str | None = None
    analyst_prompt_sha256: str | None = None
    analyst_raw_response: str | None = None
    cache_hit: int = 0
    recommendation_id: int | None = None
    reference_price: float | None = None


def _dumps(payload) -> str | None:
    """None stays None; {} becomes '{}'.

    The two mean different things -- "the stage was never reached" versus "it
    was reached and produced nothing" -- and the funnel exists to tell them
    apart, so they must not collapse. `default=str` keeps a stray Timestamp or
    Decimal from raising inside a recorder that must never raise.
    """
    if payload is None:
        return None
    return json.dumps(payload, default=str)


def build_observation(
    ticker: str,
    scan_kind: str,
    stage: str,
    outcome: str,
    *,
    session_date: str,
    observed_at: str,
    reject_reason: str | None = None,
    fundamentals: dict | None = None,
    technicals: dict | None = None,
    headlines: list | None = None,
    macro: dict | None = None,
    analysis: dict | None = None,
    cache_hit: bool = False,
    recommendation_id: int | None = None,
    reference_price: float | None = None,
) -> ShadowObservation:
    """Assemble one observation, validating the funnel position.

    `stage` and `outcome` are checked against the enums because a typo would
    silently create a bucket no report reads, and the error would surface as a
    quietly missing row rather than a failure.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; expected one of {OUTCOMES}")

    analysis = analysis or {}
    return ShadowObservation(
        session_date=session_date,
        observed_at=observed_at,
        ticker=ticker,
        scan_kind=scan_kind,
        stage_reached=stage,
        outcome=outcome,
        reject_reason=reject_reason,
        fundamentals_json=_dumps(fundamentals),
        technicals_json=_dumps(technicals),
        headlines_json=_dumps(headlines),
        macro_json=_dumps(macro),
        analyst_provider=analysis.get("provider_used"),
        analyst_model=analysis.get("model_used"),
        analyst_signal=analysis.get("signal"),
        analyst_confidence=analysis.get("confidence"),
        analyst_prompt_sha256=analysis.get("prompt_sha256"),
        analyst_raw_response=analysis.get("raw_response"),
        cache_hit=1 if cache_hit else 0,
        recommendation_id=recommendation_id,
        reference_price=reference_price,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_observation.py -q`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python.exe -m ruff check .
git add research/ tests/test_shadow_observation.py
git commit -m "feat: pure observation builder for the shadow log"
```

---

### Task 3: Analyst attribution — which model actually answered

**Files:**
- Modify: `analyst/claude_analyst.py` (`_run_with_fallbacks`, three tier blocks)
- Test: `tests/test_analyst_attribution.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_run_with_fallbacks(...)` result dict gains `"model_used": str`, `"raw_response": str` and `"prompt_sha256": str`. Existing keys (`signal`, `reasoning`, `confidence`, `provider_used`) are unchanged.

**Why:** `_run_with_fallbacks` returns `provider_used` but not the model, and `analyst_cache` records neither. Both tiers of the chain are provider `gemini`, so `provider_used` alone cannot say which model answered — the same conflation PR #34 removed from quota accounting. Research data must not inherit it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_analyst_attribution.py`:

```python
"""The analyst result must say WHICH MODEL answered, not just which provider.

Both gemini tiers are provider 'gemini', so `provider_used` cannot distinguish
the 500-RPD primary from the 20-RPD fallback. PR #34 removed exactly this
conflation from quota accounting; the result dict still had it.
"""
from analyst.claude_analyst import _run_with_fallbacks
from config import Config


class _Client:
    """Returns a fixed parseable body, or raises, per construction."""

    def __init__(self, text=None, boom=False):
        self._text, self._boom = text, boom
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        if self._boom:
            raise RuntimeError("provider down")

        class _R:
            choices = [type("C", (), {"message": type(
                "M", (), {"content": self._text})()})()]
        return _R()


def _config():
    c = Config()
    c.analyst_provider = "gemini"
    c.analyst_model = "gemini-3.1-flash-lite"
    c.analyst_fallback_provider = "gemini"
    c.analyst_fallback_model = "gemini-3.7-flash"
    c.analyst_fallback2_provider = "deepseek"
    c.analyst_fallback2_model = "deepseek-v4-flash"
    return c


_BODY = "SIGNAL: BUY\nREASONING: cheap\nCONFIDENCE: high"


def test_primary_success_reports_the_primary_model():
    r = _run_with_fallbacks(
        "prompt", _config(), _Client(_BODY), None, None, "AAPL")
    assert r["provider_used"] == "gemini"
    assert r["model_used"] == "gemini-3.1-flash-lite"


def test_fallback_success_reports_the_FALLBACK_model_not_the_primary():
    """The mutation this kills: reporting config.analyst_model unconditionally.
    Both tiers are provider 'gemini', so only the model distinguishes them."""
    r = _run_with_fallbacks(
        "prompt", _config(), _Client(boom=True), _Client(_BODY), None, "AAPL")
    assert r["provider_used"] == "gemini"
    assert r["model_used"] == "gemini-3.7-flash"


def test_fallback2_success_reports_the_fallback2_model():
    r = _run_with_fallbacks(
        "prompt", _config(), _Client(boom=True), _Client(boom=True),
        _Client(_BODY), "AAPL")
    assert r["provider_used"] == "deepseek"
    assert r["model_used"] == "deepseek-v4-flash"


def test_raw_response_and_prompt_hash_are_returned():
    r = _run_with_fallbacks(
        "the-prompt", _config(), _Client(_BODY), None, None, "AAPL")
    assert r["raw_response"] == _BODY
    assert len(r["prompt_sha256"]) == 64


def test_prompt_hash_is_stable_and_differs_per_prompt():
    a = _run_with_fallbacks("p1", _config(), _Client(_BODY), None, None, "A")
    b = _run_with_fallbacks("p1", _config(), _Client(_BODY), None, None, "A")
    c = _run_with_fallbacks("p2", _config(), _Client(_BODY), None, None, "A")
    assert a["prompt_sha256"] == b["prompt_sha256"]
    assert a["prompt_sha256"] != c["prompt_sha256"]


def test_existing_keys_are_unchanged():
    r = _run_with_fallbacks(
        "prompt", _config(), _Client(_BODY), None, None, "AAPL")
    assert r["signal"] == "BUY"
    assert r["confidence"] == "high"
    assert "reasoning" in r
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_analyst_attribution.py -q`
Expected: FAIL — `KeyError: 'model_used'`.

- [ ] **Step 3: Write minimal implementation**

In `analyst/claude_analyst.py`, add `import hashlib` at the top if absent, then add this helper above `_run_with_fallbacks`:

```python
def _attribute(result: dict, provider: str, model: str, prompt: str, text: str) -> dict:
    """Stamp a parsed result with what produced it.

    `provider_used` alone is not attribution: both gemini tiers are provider
    'gemini', so it cannot distinguish the 500-RPD primary from the 20-RPD
    fallback. The prompt is stored as a hash rather than in full -- the text is
    reconstructable from the inputs and would multiply the row size.
    """
    result["provider_used"] = provider
    result["model_used"] = model
    result["raw_response"] = text
    result["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return result
```

Then in each of the three tier blocks, replace the line
`result["provider_used"] = <provider>` followed by `return result` with a single call. Primary block:

```python
        text = _call_api(client, model, prompt)
        result = parse_claude_response(text)
        return _attribute(result, config.analyst_provider, model, prompt, text)
```

First fallback block — same shape, using `config.analyst_fallback_provider` and `fallback_model`:

```python
        text = _call_api(fallback_client, fallback_model, prompt)
        result = parse_claude_response(text)
        return _attribute(result, config.analyst_fallback_provider,
                          fallback_model, prompt, text)
```

Second fallback block — `config.analyst_fallback2_provider` and `fallback2_model`:

```python
        text = _call_api(fallback2_client, fallback2_model, prompt)
        result = parse_claude_response(text)
        return _attribute(result, config.analyst_fallback2_provider,
                          fallback2_model, prompt, text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_analyst_attribution.py -q`
Expected: 6 passed.

Then the full suite — this modifies a hot path used by buy, sell and ETF analysis:
Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: no failures. If a test asserts the exact result dict with `==`, update it to check the keys it cares about rather than the whole dict.

- [ ] **Step 5: Mutation-test the fallback attribution**

Change the first-fallback block to pass `model` instead of `fallback_model`, then run:
`.venv/Scripts/python.exe -m pytest tests/test_analyst_attribution.py -q`
Expected: `test_fallback_success_reports_the_FALLBACK_model_not_the_primary` FAILS. If it passes, the test is vacuous — fix it before continuing. Revert with `git checkout analyst/claude_analyst.py` (safe: nothing uncommitted is at risk if Task 2 was committed).

- [ ] **Step 6: Commit**

```bash
.venv/Scripts/python.exe -m ruff check .
git add analyst/claude_analyst.py tests/test_analyst_attribution.py
git commit -m "feat: the analyst result records which MODEL answered"
```

---

### Task 4: Fail-safe write path

**Files:**
- Modify: `database/queries.py`, `research/shadow_log.py`
- Test: `tests/test_shadow_recorder.py`

**Interfaces:**
- Consumes: `ShadowObservation` (Task 2), the tables (Task 1).
- Produces:
  - `queries.record_shadow_observation(db_path: str, obs: ShadowObservation) -> int`
  - `queries.set_shadow_human_action(db_path, recommendation_id, action, at) -> None`
  - `research.shadow_log.record(config, obs) -> int | None` — never raises.
  - `research.shadow_log.observe(config, ticker, scan_kind, stage, outcome, **kw) -> int | None` — builds and records in one call; never raises. This is what `main.py` calls.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shadow_recorder.py`:

```python
"""The recorder writes, and above all it never raises.

A research instrument that can abort a scan is a liability, not an instrument.
Same rule as the ops-alert outbox: neither send nor drain may raise, because
both run inside scans that must not be aborted by their own reporting.
"""
import sqlite3

from config import Config
from database import queries
from database.models import initialize_db
from research import shadow_log


def _config(tmp_path):
    c = Config()
    c.db_path = str(tmp_path / "s.db")
    initialize_db(c.db_path)
    return c


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM shadow_observations").fetchall()


def test_observe_writes_one_row(tmp_path):
    cfg = _config(tmp_path)
    rid = shadow_log.observe(
        cfg, "AAPL", "stock", "fundamental", "rejected_fundamental",
        fundamentals={"trailingPE": 99.0},
    )
    rows = _rows(cfg.db_path)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["outcome"] == "rejected_fundamental"
    assert rid == rows[0]["id"]


def test_observe_stamps_the_market_session_date_not_the_local_date(tmp_path):
    """20:04 ET on the 20th is the 20th's session even though it is the 21st in
    Taipei, where this host lives. Bare local dates split one US session across
    two buckets -- the bug market_time.py exists to prevent."""
    from datetime import datetime, timezone as _tz
    cfg = _config(tmp_path)
    instant = datetime(2026, 8, 21, 0, 4, tzinfo=_tz.utc)  # 20:04 ET on the 20th
    shadow_log.observe(cfg, "AAPL", "stock", "universe",
                       "skipped_open_position", instant=instant)
    assert _rows(cfg.db_path)[0]["session_date"] == "2026-08-20"


def test_a_write_failure_does_not_raise(tmp_path):
    """The load-bearing property. A scan must survive its own recorder."""
    cfg = _config(tmp_path)
    cfg.db_path = str(tmp_path / "does" / "not" / "exist.db")
    result = shadow_log.observe(cfg, "AAPL", "stock", "universe",
                                "skipped_open_position")
    assert result is None  # reported as "not recorded", not as an exception


def test_an_invalid_stage_does_not_raise_either(tmp_path):
    """build_observation raises on a bad stage by design; observe must absorb
    it. A typo in a call site must not take the scan down with it."""
    cfg = _config(tmp_path)
    assert shadow_log.observe(cfg, "AAPL", "stock", "nonsense", "recommended") is None
    assert _rows(cfg.db_path) == []


def test_human_action_is_attached_to_the_observation(tmp_path):
    cfg = _config(tmp_path)
    shadow_log.observe(cfg, "AAPL", "stock", "recommended", "recommended",
                       recommendation_id=42)
    queries.set_shadow_human_action(cfg.db_path, 42, "approved",
                                    "2026-08-20T14:00:00Z")
    row = _rows(cfg.db_path)[0]
    assert row["human_action"] == "approved"
    assert row["human_action_at"] == "2026-08-20T14:00:00Z"


def test_human_action_for_an_unknown_recommendation_is_a_noop(tmp_path):
    cfg = _config(tmp_path)
    queries.set_shadow_human_action(cfg.db_path, 999, "approved",
                                    "2026-08-20T14:00:00Z")
    assert _rows(cfg.db_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_recorder.py -q`
Expected: FAIL — `AttributeError: module 'research.shadow_log' has no attribute 'observe'`.

- [ ] **Step 3: Write minimal implementation**

Add to `database/queries.py`:

```python
def record_shadow_observation(db_path: str, obs) -> int:
    """Insert one shadow observation and return its id.

    Takes the dataclass rather than 20 positional arguments so a new column is
    one edit here and one in the dataclass, not a signature change rippling
    through every call site.
    """
    with get_cursor(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO shadow_observations
                   (session_date, observed_at, ticker, scan_kind, stage_reached,
                    outcome, reject_reason, fundamentals_json, technicals_json,
                    headlines_json, macro_json, analyst_provider, analyst_model,
                    analyst_signal, analyst_confidence, analyst_prompt_sha256,
                    analyst_raw_response, cache_hit, recommendation_id,
                    reference_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (obs.session_date, obs.observed_at, obs.ticker, obs.scan_kind,
             obs.stage_reached, obs.outcome, obs.reject_reason,
             obs.fundamentals_json, obs.technicals_json, obs.headlines_json,
             obs.macro_json, obs.analyst_provider, obs.analyst_model,
             obs.analyst_signal, obs.analyst_confidence,
             obs.analyst_prompt_sha256, obs.analyst_raw_response,
             obs.cache_hit, obs.recommendation_id, obs.reference_price),
        )
        return cursor.lastrowid


def set_shadow_human_action(db_path: str, recommendation_id: int,
                            action: str, at: str) -> None:
    """Attach the human's click to the observation that produced it."""
    with get_cursor(db_path) as conn:
        conn.execute(
            """UPDATE shadow_observations
                  SET human_action = ?, human_action_at = ?
                WHERE recommendation_id = ?""",
            (action, at, recommendation_id),
        )
```

Add to `research/shadow_log.py` (new imports at the top: `import logging`, `from datetime import datetime, timezone`):

```python
logger = logging.getLogger(__name__)


def record(config, obs: ShadowObservation) -> int | None:
    """Persist one observation. Never raises."""
    from database import queries  # local: keeps this module importable pure
    try:
        return queries.record_shadow_observation(config.db_path, obs)
    except Exception:
        logger.exception("Shadow log write failed for %s; continuing", obs.ticker)
        return None


def observe(config, ticker: str, scan_kind: str, stage: str, outcome: str,
            *, instant=None, **kw) -> int | None:
    """Build and record one observation. NEVER RAISES -- this is the contract.

    Every failure mode is absorbed, including a bad stage/outcome from a typo at
    a call site. The scan is the product; this is instrumentation, and
    instrumentation that can abort the thing it measures is worse than none.

    `instant` is threaded through for the session date so tests can pin the
    clock, matching every other time-dependent function in this repo.
    """
    from market_time import market_session_date
    try:
        now = instant or datetime.now(timezone.utc)
        obs = build_observation(
            ticker, scan_kind, stage, outcome,
            session_date=market_session_date(now),
            observed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            **kw,
        )
    except Exception:
        logger.exception("Shadow log build failed for %s; continuing", ticker)
        return None
    return record(config, obs)
```

**Note for the implementer:** confirm `market_session_date`'s parameter name by reading `market_time.py` before wiring it; if it takes the instant positionally, call it positionally as shown.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_recorder.py -q`
Expected: 6 passed.

- [ ] **Step 5: Mutation-test the never-raises contract**

Temporarily remove the `try`/`except` from `observe` and run
`.venv/Scripts/python.exe -m pytest tests/test_shadow_recorder.py -q`.
Expected: `test_a_write_failure_does_not_raise` and
`test_an_invalid_stage_does_not_raise_either` both FAIL. Restore with
`git checkout research/shadow_log.py`.

- [ ] **Step 6: Commit**

```bash
.venv/Scripts/python.exe -m ruff check .
git add database/queries.py research/shadow_log.py tests/test_shadow_recorder.py
git commit -m "feat: fail-safe shadow-log recorder that cannot abort a scan"
```

---

### Task 5: Wire both scan loops

**Files:**
- Modify: `main.py` (`_run_scan_locked` and `_run_scan_etf_locked`)
- Test: `tests/test_shadow_scan_wiring.py`

**Interfaces:**
- Consumes: `research.shadow_log.observe` (Task 4).
- Produces: a shadow row at every exit point of both scan loops.

**Exit points in `_run_scan_locked`, in order** (verified against the code, 2026-08-21):

| Guard | stage | outcome |
|---|---|---|
| `ticker_recommended_today` | `universe` | `skipped_recommended_today` |
| `has_open_position` | `universe` | `skipped_open_position` |
| `has_active_recommendation` | `universe` | `skipped_active_recommendation` |
| `not passes_fundamental_filter` | `fundamental` | `rejected_fundamental` |
| `analysis is None` | `analyst` | `skipped_quota_exhausted` |
| signal is not BUY | `technical` | `rejected_signal` |
| technical filter refuses | `technical` | `rejected_technical` |
| recommendation posted | `recommended` | `recommended` |
| `except Exception` | `universe` | `error` |

**Why the outcome is split rather than reading `should_recommend`:** `should_recommend` returns one bool for two different refusals (signal, then technicals). The funnel needs them apart, so the hook re-checks both explicitly. That duplicates two lines of logic; the alternative — changing `should_recommend`'s return type — would alter a function on the live path for a research need, which is the wrong trade.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shadow_scan_wiring.py`:

```python
"""Every exit point of both scan loops leaves a shadow row.

Rejections matter more than recommendations here: a conversion rate whose
denominator omits the rejects is not a conversion rate.

The ETF assertions instrument `partition_watchlist`, NOT `get_universe` -- the
ETF path never calls `get_universe`, and a test that patches it passes
vacuously. That mistake has already been made once in this repo.
"""
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from config import Config
from database.models import initialize_db


def _config(tmp_path):
    c = Config()
    c.db_path = str(tmp_path / "s.db")
    c.dry_run = True
    initialize_db(c.db_path)
    return c


def _outcomes(db_path):
    conn = sqlite3.connect(db_path)
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT ticker, outcome FROM shadow_observations ORDER BY id")]


@pytest.mark.asyncio
async def test_open_position_skip_is_recorded(tmp_path):
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    with patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]), \
         patch.object(main, "get_universe", return_value=["AAPL"]), \
         patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: (["AAPL"], [])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main.queries, "has_open_position", return_value=True):
        await main.run_scan(bot, cfg)
    assert ("AAPL", "skipped_open_position") in _outcomes(cfg.db_path)


@pytest.mark.asyncio
async def test_fundamental_rejection_is_recorded(tmp_path):
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    with patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]), \
         patch.object(main, "get_universe", return_value=["XOM"]), \
         patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: (["XOM"], [])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main, "fetch_fundamental_info", return_value={"trailingPE": 900.0}), \
         patch.object(main, "passes_fundamental_filter", return_value=False):
        await main.run_scan(bot, cfg)
    assert ("XOM", "rejected_fundamental") in _outcomes(cfg.db_path)


@pytest.mark.asyncio
async def test_the_etf_scan_records_too(tmp_path):
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    with patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: ([], ["SPY"])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main.queries, "has_open_position", return_value=True):
        await main.run_scan_etf(bot, cfg)
    rows = _outcomes(cfg.db_path)
    assert ("SPY", "skipped_open_position") in rows


@pytest.mark.asyncio
async def test_a_recorder_failure_does_not_abort_the_scan(tmp_path):
    """The scan's own instrumentation must not be able to end it."""
    cfg = _config(tmp_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    with patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]), \
         patch.object(main, "get_universe", return_value=["AAPL"]), \
         patch.object(main, "partition_watchlist",
                      side_effect=lambda t, i=None: (["AAPL"], [])), \
         patch.object(main, "fetch_macro_context", return_value={}), \
         patch.object(main, "alert_stuck_orders", new=AsyncMock()), \
         patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()), \
         patch.object(main, "_drain_ops_outbox", new=AsyncMock()), \
         patch.object(main.queries, "has_open_position", return_value=True), \
         patch.object(main.shadow_log, "observe", side_effect=RuntimeError("boom")):
        await main.run_scan(bot, cfg)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_scan_wiring.py -q`
Expected: FAIL — no `shadow_observations` rows, and `AttributeError` on `main.shadow_log`.

- [ ] **Step 3: Write minimal implementation**

Add the import near main.py's other local imports:

```python
from research import shadow_log
```

In `_run_scan_locked`, replace each bare `continue` with a recorded one. The three universe guards:

```python
        if queries.ticker_recommended_today(config.db_path, ticker):
            shadow_log.observe(config, ticker, "stock", "universe",
                               "skipped_recommended_today")
            continue
        if queries.has_open_position(config.db_path, ticker):
            logger.debug("Skipping %s: open position exists", ticker)
            shadow_log.observe(config, ticker, "stock", "universe",
                               "skipped_open_position")
            continue
        if queries.has_active_recommendation(config.db_path, ticker):
            logger.debug("Skipping %s: an active recommendation already exists", ticker)
            shadow_log.observe(config, ticker, "stock", "universe",
                               "skipped_active_recommendation")
            continue
```

The fundamental gate:

```python
            if not passes_fundamental_filter(info, config):
                shadow_log.observe(config, ticker, "stock", "fundamental",
                                   "rejected_fundamental", fundamentals=info,
                                   macro=macro_context)
                continue
```

The quota gate:

```python
            analysis = await analyze_with_cache(config, ticker, headlines, _analyze_buy)
            if analysis is None:
                shadow_log.observe(config, ticker, "stock", "analyst",
                                   "skipped_quota_exhausted", fundamentals=info,
                                   headlines=headlines, macro=macro_context)
                continue  # all providers quota-exhausted
```

The signal and technical gates — replacing the single `should_recommend` branch:

```python
            tech_data = await asyncio.to_thread(fetch_technical_data, yf_ticker)
            if not should_recommend(analysis["signal"], tech_data, config):
                # Two different refusals share one bool; the funnel needs them
                # apart, so re-check rather than change should_recommend's
                # return type on the live path for a research need.
                outcome = ("rejected_signal" if analysis["signal"] != "BUY"
                           else "rejected_technical")
                shadow_log.observe(config, ticker, "stock", "technical", outcome,
                                   fundamentals=info, technicals=tech_data,
                                   headlines=headlines, macro=macro_context,
                                   analysis=analysis,
                                   reference_price=tech_data.get("price"))
                continue
```

After `recommendations_posted += 1`:

```python
            shadow_log.observe(config, ticker, "stock", "recommended", "recommended",
                               fundamentals=info, technicals=tech_data,
                               headlines=headlines, macro=macro_context,
                               analysis=analysis, recommendation_id=rec_id,
                               reference_price=tech_data["price"])
```

In the `except Exception` handler, before `continue`:

```python
            shadow_log.observe(config, ticker, "stock", "universe", "error",
                               reject_reason=f"{type(exc).__name__}: {exc}")
```

Apply the equivalent hooks in `_run_scan_etf_locked`, passing `"etf"` as `scan_kind`. The ETF loop has no fundamental gate, so it has no `rejected_fundamental` outcome.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_scan_wiring.py -q`
Expected: 4 passed.

Then the full suite:
Run: `.venv/Scripts/python.exe -m pytest -q`

**Expect breakage here.** The handoff records that adding a call into the scan loop broke 22 tests that patch `queries` function-by-function, and that two harnesses append patches at the end specifically to keep positional mock indices stable and say so in a comment. Do not insert into the middle of those patch lists. Fixes, in preference order:

1. Add an autouse fixture to the affected test module that patches
   `main.shadow_log.observe` to a no-op — the shadow log is not what those
   tests are about.
2. If the module is `test_main.py`, note it is at Python's 20-block nesting
   limit: `with A, B:` is not available. Use the autouse fixture, or
   `contextlib.ExitStack`.

```python
@pytest.fixture(autouse=True)
def _silence_shadow_log():
    with patch("main.shadow_log.observe", return_value=None):
        yield
```

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python.exe -m ruff check .
git add main.py tests/
git commit -m "feat: record every scan exit point in the shadow log"
```

---

### Task 6: Human action and latency

**Files:**
- Modify: `discord_bot/bot.py` (`ApproveRejectView.approve` and `.reject`)
- Test: `tests/test_shadow_human_action.py`

**Interfaces:**
- Consumes: `queries.set_shadow_human_action` (Task 4).
- Produces: `human_action` ∈ {`approved`, `rejected`} and `human_action_at` on the observation.

**Why it matters:** approval latency is the gap between the signal and the trade, and it is the single quantity no retrospective backtest can recover. Recording it forward is the whole point.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shadow_human_action.py`:

```python
"""The human's click, and when it happened.

Approval latency is unrecoverable retrospectively -- historical data cannot
reveal whether or when someone would have clicked Approve. Recording it forward
is the only way this quantity ever exists.
"""
import sqlite3
from datetime import datetime, timezone

from config import Config
from database import queries
from database.models import initialize_db
from research import shadow_log


def _config(tmp_path):
    c = Config()
    c.db_path = str(tmp_path / "s.db")
    initialize_db(c.db_path)
    return c


def test_approval_latency_is_derivable_from_the_stored_timestamps(tmp_path):
    cfg = _config(tmp_path)
    posted = datetime(2026, 8, 20, 13, 45, tzinfo=timezone.utc)
    shadow_log.observe(cfg, "AAPL", "stock", "recommended", "recommended",
                       recommendation_id=7, instant=posted)
    queries.set_shadow_human_action(cfg.db_path, 7, "approved",
                                    "2026-08-20T14:15:00Z")

    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM shadow_observations").fetchone()
    t0 = datetime.strptime(row["observed_at"], "%Y-%m-%dT%H:%M:%SZ")
    t1 = datetime.strptime(row["human_action_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert (t1 - t0).total_seconds() == 30 * 60
    assert row["human_action"] == "approved"


def test_rejection_is_recorded_as_rejected(tmp_path):
    cfg = _config(tmp_path)
    shadow_log.observe(cfg, "XOM", "stock", "recommended", "recommended",
                       recommendation_id=8)
    queries.set_shadow_human_action(cfg.db_path, 8, "rejected",
                                    "2026-08-20T14:15:00Z")
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM shadow_observations").fetchone()
    assert row["human_action"] == "rejected"


def test_no_click_leaves_the_action_null(tmp_path):
    """A recommendation nobody touched must be distinguishable from a rejected
    one -- non-response is data, and lumping it in with rejection would
    overstate how often the human said no."""
    cfg = _config(tmp_path)
    shadow_log.observe(cfg, "MSFT", "stock", "recommended", "recommended",
                       recommendation_id=9)
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM shadow_observations").fetchone()
    assert row["human_action"] is None
    assert row["human_action_at"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_human_action.py -q`
Expected: the first two FAIL (`human_action` is `None`), the third passes.

Note: the third passing immediately is expected and correct — it asserts the default state. Keep it; it pins that non-response stays distinguishable from rejection.

- [ ] **Step 3: Write minimal implementation**

In `discord_bot/bot.py`, add near the top:

```python
from datetime import datetime, timezone
```

In `ApproveRejectView.approve`, immediately after the recommendation is successfully claimed (and inside the same success path that already records the trade), add:

```python
        queries.set_shadow_human_action(
            self.config.db_path, self.rec_id, "approved",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
```

In `ApproveRejectView.reject`, after the rejection is persisted:

```python
        queries.set_shadow_human_action(
            self.config.db_path, self.rec_id, "rejected",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
```

**Implementer note:** read the two handlers before editing to confirm the attribute names (`self.rec_id`, `self.config`) and to place the calls on the success paths only — a refused approval must not be recorded as a click that led to a trade. If either call could raise, wrap it in `try/except Exception` with a `logger.exception`, matching the recorder's contract.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_human_action.py tests/test_discord_buttons.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python.exe -m ruff check .
git add discord_bot/bot.py tests/test_shadow_human_action.py
git commit -m "feat: record the human's click and its latency"
```

---

### Task 7: Forward outcome marks

**Files:**
- Create: `research/outcomes.py`
- Modify: `database/queries.py`, `main.py`
- Test: `tests/test_shadow_outcomes.py`

**Interfaces:**
- Consumes: the tables (Task 1).
- Produces:
  - `queries.pending_shadow_marks(db_path, horizon, cutoff_session_date) -> list[sqlite3.Row]`
  - `queries.record_shadow_outcome(db_path, observation_id, horizon, as_of, price, return_pct, benchmark_price, benchmark_return_pct) -> None`
  - `research.outcomes.HORIZONS: dict[str, int]` — `{"1w": 7, "1m": 30, "3m": 90, "6m": 180}` in calendar days
  - `research.outcomes.compute_return(entry: float, exit_: float) -> float` — pure
  - `async research.outcomes.mark_due_outcomes(config, instant=None) -> int` — never raises

- [ ] **Step 1: Write the failing test**

Create `tests/test_shadow_outcomes.py`:

```python
"""Forward marks: what the price actually did after each observation."""
import sqlite3
from datetime import datetime, timezone

import pytest

from config import Config
from database import queries
from database.models import initialize_db
from research import outcomes


def _config(tmp_path):
    c = Config()
    c.db_path = str(tmp_path / "s.db")
    initialize_db(c.db_path)
    return c


def _observe(cfg, ticker, session_date, price):
    conn = sqlite3.connect(cfg.db_path)
    conn.execute(
        "INSERT INTO shadow_observations (session_date, observed_at, ticker,"
        " scan_kind, stage_reached, outcome, reference_price)"
        " VALUES (?,?,?,'stock','recommended','recommended',?)",
        (session_date, session_date + "T13:45:00Z", ticker, price),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_compute_return_is_a_percentage():
    assert outcomes.compute_return(100.0, 110.0) == pytest.approx(10.0)
    assert outcomes.compute_return(100.0, 90.0) == pytest.approx(-10.0)


def test_compute_return_on_zero_entry_returns_none():
    """A zero or missing entry price cannot yield a return. Returning 0.0 would
    silently enter a fake flat trade into the sample."""
    assert outcomes.compute_return(0.0, 110.0) is None
    assert outcomes.compute_return(None, 110.0) is None


def test_only_matured_horizons_are_due(tmp_path):
    """A 1-week mark is not due after two days. Marking early would record a
    two-day return in the one-week column.

    NOTE the argument is a CUTOFF (`now - horizon_days`), not `now`. Evaluated
    on 2026-08-22 the 1w cutoff is 2026-08-15, and a 2026-08-20 session has not
    matured; evaluated on 2026-08-28 the cutoff is 2026-08-21, and it has.
    """
    cfg = _config(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)
    # as if now = 2026-08-22  ->  cutoff = 2026-08-15
    assert queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-15") == []
    # as if now = 2026-08-28  ->  cutoff = 2026-08-21
    assert len(queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-21")) == 1


def test_an_already_marked_horizon_is_not_due_again(tmp_path):
    cfg = _config(tmp_path)
    oid = _observe(cfg, "AAPL", "2026-08-20", 100.0)
    queries.record_shadow_outcome(cfg.db_path, oid, "1w", "2026-08-27",
                                  110.0, 10.0, 500.0, 1.0)
    # cutoff 2026-08-21 WOULD match on date; the recorded mark is what excludes it
    assert queries.pending_shadow_marks(cfg.db_path, "1w", "2026-08-21") == []


def test_recording_the_same_horizon_twice_does_not_duplicate(tmp_path):
    cfg = _config(tmp_path)
    oid = _observe(cfg, "AAPL", "2026-08-20", 100.0)
    queries.record_shadow_outcome(cfg.db_path, oid, "1w", "2026-08-27",
                                  110.0, 10.0, 500.0, 1.0)
    queries.record_shadow_outcome(cfg.db_path, oid, "1w", "2026-08-27",
                                  111.0, 11.0, 500.0, 1.0)
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT COUNT(*) FROM shadow_outcomes").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_a_price_fetch_failure_does_not_raise(tmp_path, monkeypatch):
    """Same contract as the recorder: this runs inside a scan."""
    cfg = _config(tmp_path)
    _observe(cfg, "AAPL", "2026-08-20", 100.0)

    def _boom(*a, **kw):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(outcomes, "_close_on_or_before", _boom)
    n = await outcomes.mark_due_outcomes(
        cfg, instant=datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert n == 0  # nothing marked, nothing raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_outcomes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'research.outcomes'`.

- [ ] **Step 3: Write minimal implementation**

Add to `database/queries.py`:

```python
def pending_shadow_marks(db_path: str, horizon: str,
                         cutoff_session_date: str) -> list:
    """Observations whose `horizon` has matured and is not yet recorded.

    The cutoff is passed in rather than computed here so the caller owns the
    clock, matching every other time-dependent query in this module.
    """
    with get_cursor(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """SELECT o.id, o.ticker, o.session_date, o.reference_price
                 FROM shadow_observations o
                WHERE o.session_date <= ?
                  AND o.reference_price IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM shadow_outcomes s
                                   WHERE s.observation_id = o.id
                                     AND s.horizon = ?)""",
            (cutoff_session_date, horizon),
        ).fetchall()


def record_shadow_outcome(db_path: str, observation_id: int, horizon: str,
                          as_of: str, price, return_pct,
                          benchmark_price, benchmark_return_pct) -> None:
    """Write one forward mark. Idempotent per (observation, horizon)."""
    with get_cursor(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO shadow_outcomes
                   (observation_id, horizon, as_of, price, return_pct,
                    benchmark_price, benchmark_return_pct)
               VALUES (?,?,?,?,?,?,?)""",
            (observation_id, horizon, as_of, price, return_pct,
             benchmark_price, benchmark_return_pct),
        )
```

Create `research/outcomes.py`:

```python
"""Forward price marks for shadow observations.

Every observation is marked against SPY over the identical window, so each row
carries its own market-relative result and no later analysis has to re-derive
one. Absolute return alone would mostly measure the market.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import yfinance as yf

from database import queries

logger = logging.getLogger(__name__)

# Calendar days, not trading days: the mark is "the last close on or before
# this date", so weekends and holidays resolve backwards to a real bar.
HORIZONS = {"1w": 7, "1m": 30, "3m": 90, "6m": 180}

BENCHMARK = "SPY"


def compute_return(entry, exit_) -> float | None:
    """Percentage return, or None when it cannot be computed.

    A zero or absent entry price yields None rather than 0.0: booking a fake
    flat trade would put a fabricated observation into the sample, which is the
    same failure mode as booking a zero fill for an order that really filled.
    """
    if not entry or exit_ is None:
        return None
    return (exit_ - entry) / entry * 100.0


def _close_on_or_before(ticker: str, as_of: str) -> float | None:
    """Last close at or before `as_of` (YYYY-MM-DD), or None."""
    end = datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=1)
    start = end - timedelta(days=10)  # enough to clear a long weekend
    hist = yf.Ticker(ticker).history(start=start.date(), end=end.date())
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


async def mark_due_outcomes(config, instant=None) -> int:
    """Fill in every matured, unrecorded mark. Returns the count. NEVER RAISES.

    Runs at scan start, so it inherits the same contract as the recorder: a
    research job must not be able to abort the scan it runs inside. Failures are
    per observation, so one delisted ticker cannot stop every other mark -- the
    same rule the terminal-order sweep follows.
    """
    now = instant or datetime.now(timezone.utc)
    marked = 0
    for horizon, days in HORIZONS.items():
        cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            due = queries.pending_shadow_marks(config.db_path, horizon, cutoff)
        except Exception:
            logger.exception("Could not list pending %s marks; continuing", horizon)
            continue
        for row in due:
            as_of = (datetime.strptime(row["session_date"], "%Y-%m-%d")
                     + timedelta(days=days)).strftime("%Y-%m-%d")
            try:
                price = await asyncio.to_thread(
                    _close_on_or_before, row["ticker"], as_of)
                bench = await asyncio.to_thread(
                    _close_on_or_before, BENCHMARK, as_of)
                bench_entry = await asyncio.to_thread(
                    _close_on_or_before, BENCHMARK, row["session_date"])
                queries.record_shadow_outcome(
                    config.db_path, row["id"], horizon, as_of, price,
                    compute_return(row["reference_price"], price),
                    bench, compute_return(bench_entry, bench),
                )
                marked += 1
            except Exception:
                logger.exception(
                    "Could not mark %s for %s at %s; continuing",
                    horizon, row["ticker"], as_of)
                continue
    return marked
```

Wire it into `_run_scan_locked`, immediately after the terminal-order sweep block:

```python
    # Research marks. Same contract as the sweep: never fatal to the scan.
    try:
        await outcomes.mark_due_outcomes(config)
    except Exception:
        logger.exception("Shadow outcome marking failed; continuing the scan")
```

with `from research import outcomes` added to main.py's imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_outcomes.py -q`
Expected: 6 passed.

Run the full suite: `.venv/Scripts/python.exe -m pytest -q`

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python.exe -m ruff check .
git add research/outcomes.py database/queries.py main.py tests/test_shadow_outcomes.py
git commit -m "feat: forward price marks for shadow observations"
```

---

### Task 8: The funnel report

**Files:**
- Create: `scripts/shadow_report.py`
- Test: `tests/test_shadow_report.py`

**Interfaces:**
- Consumes: both tables.
- Produces: `build_funnel(rows) -> dict[str, int]` (pure) and a CLI that prints the funnel plus per-horizon marks.

**Why:** the data is only useful if the denominators are visible. This is the artifact that makes "0 recommendations" interpretable — the outcome the first live scan produced, which took manual tracing to understand.

- [ ] **Step 1: Write the failing test**

Create `tests/test_shadow_report.py`:

```python
"""The funnel report. Pure aggregation, tested without a database."""
import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "shadow_report",
    Path(__file__).resolve().parent.parent / "scripts" / "shadow_report.py",
)
report = importlib.util.module_from_spec(_SPEC)
sys.modules["shadow_report"] = report
_SPEC.loader.exec_module(report)


def test_funnel_counts_every_outcome():
    rows = [
        {"outcome": "rejected_fundamental"},
        {"outcome": "rejected_fundamental"},
        {"outcome": "rejected_signal"},
        {"outcome": "recommended"},
    ]
    assert report.build_funnel(rows) == {
        "rejected_fundamental": 2, "rejected_signal": 1, "recommended": 1}


def test_funnel_of_nothing_is_empty_not_an_error():
    assert report.build_funnel([]) == {}


def test_unknown_outcomes_are_still_counted():
    """A row written by an older version must not vanish from the totals --
    a denominator that silently drops rows is the defect this guards."""
    assert report.build_funnel([{"outcome": "from_the_future"}]) == {
        "from_the_future": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_report.py -q`
Expected: FAIL — the file does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/shadow_report.py`:

```python
"""Read-only funnel report over the shadow log.

Answers the question the first live scan could not: "0 recommendations" is
correct and "0 recommendations" is a silent failure look identical without the
denominators. Tracing that outcome by hand took a session; this prints it.

    .venv/Scripts/python.exe scripts/shadow_report.py
    .venv/Scripts/python.exe scripts/shadow_report.py --since 2026-08-01
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402


def build_funnel(rows) -> dict:
    """Count outcomes. Unknown values are counted, never dropped."""
    return dict(Counter(r["outcome"] for r in rows))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="0000-00-00",
                    help="earliest session date to include (YYYY-MM-DD)")
    args = ap.parse_args()

    config = Config()
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM shadow_observations WHERE session_date >= ?",
        (args.since,)).fetchall()

    funnel = build_funnel(rows)
    total = sum(funnel.values())
    print(f"shadow observations since {args.since}: {total}\n")
    if not total:
        print("  nothing recorded yet")
        return 0
    for outcome, n in sorted(funnel.items(), key=lambda kv: -kv[1]):
        print(f"  {outcome:34} {n:5}  ({n / total:5.1%})")

    marks = conn.execute(
        """SELECT horizon, COUNT(*) n, AVG(return_pct) r,
                  AVG(benchmark_return_pct) b
             FROM shadow_outcomes GROUP BY horizon""").fetchall()
    if marks:
        print("\nforward marks (mean %, vs SPY over the same window):")
        for m in marks:
            spread = (m["r"] or 0) - (m["b"] or 0)
            print(f"  {m['horizon']:4} n={m['n']:4}  "
                  f"ret {m['r'] or 0:+6.2f}  spy {m['b'] or 0:+6.2f}  "
                  f"spread {spread:+6.2f}")
        print("\nMean is shown for orientation only. At these trade counts it is")
        print("not evidence -- see the spec's metric restrictions before quoting it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_shadow_report.py -q`
Expected: 3 passed.

Then run it for real against the live database:
Run: `.venv/Scripts/python.exe scripts/shadow_report.py`
Expected: "nothing recorded yet" — no scan has run since the recorder landed.

- [ ] **Step 5: Full suite, then commit**

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
git add scripts/shadow_report.py tests/test_shadow_report.py
git commit -m "feat: funnel report over the shadow log"
```

---

## Verification

After Task 8, before declaring the subsystem done:

- [ ] `.venv/Scripts/python.exe -m pytest -q` — all pass, count is the pre-plan total + ~37
- [ ] `.venv/Scripts/python.exe -m ruff check .` — clean
- [ ] `.venv/Scripts/python.exe scripts/check_ops_ids.py` — unchanged behaviour
- [ ] Run one real dry-run scan and confirm `shadow_report.py` shows a funnel whose total equals the universe size. **This is the acceptance test** — every other check verifies the parts.
- [ ] Confirm `algo_trade.db` gained both tables and that the kill switch is still `HALTED` with its original audit-event count.

## Notes for the executor

- **Do not let the recorder gate anything.** If a shadow-log call ever appears in an `if`, that is a bug.
- **The spec forbids certain metrics** (§3.2) — that restriction binds Subsystem A. This plan's report deliberately prints a mean with a warning attached; do not add Sharpe or CAGR here either.
- `watchlist.txt` is currently empty, so a real scan's stock universe comes entirely from the S&P 500 ranking. That is expected and not a bug in this work.
