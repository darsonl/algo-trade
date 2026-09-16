# Preregistration: is the analyst stage necessary?

**Registered 2026-09-16, before the 2026-09-16 cohort existed and before the 09-21 / 09-22
forward marks landed.** That timing is the point. Open item 2 of `HANDOFF-2026-09-15b.md` has
stood as *"analyst-removal criterion undecided — but now measurable"* since the first clean
session; measurable-and-undecided is the state in which a number arrives and the criterion gets
written to fit it.

This is a **new preregistration in the sense §5 of `2026-08-21-strategy-validation-design.md`
defines** — a separate registration with its own frozen parameters, not an edit to that one. It
governs Subsystem B (the forward shadow log) only; Subsystem A remains unbuilt.

---

## 0. The decision this authorises

One decision, with a pre-specified direction and a pre-specified action:

> **Null: keep the analyst.** The criterion fires only on evidence that the analyst is
> *actively destroying value*, and when it fires it authorises removing its veto.

**Why the null is "keep", stated plainly so it is not later mistaken for a finding:** the
analyst is close to free. All 46 calls on 2026-09-15 landed on `gemini-3.1-flash-lite` — free
tier, 500 RPD, no fallback engaged. Its cost is roughly three minutes of scan latency (~41
calls at `ANALYST_CALL_DELAY_S=4.0`) and the per-model quota machinery, not money. So the
question is **not** "does it earn its keep?" — it nearly keeps itself. The question is "is it
worse than nothing?", and only a large effect answers that.

**The criterion will usually not fire, and that is by construction, not by accident.** A reader
who finds "keep" unsatisfying should change the null here, in advance, as a new registration —
not reinterpret a non-firing result afterwards as though it were evidence of value. Failure to
fire is not evidence the analyst helps. It is the absence of evidence that it harms.

---

## 1. The estimand

Among **stock** candidates that reached the technical gate **and passed it**
(`technical_verdict = 'passed'`), the difference in **1-week excess return versus SPY**
between:

| Arm | Definition |
|---|---|
| **A — approved** | `analyst_signal = 'BUY'` |
| **B — stopped** | `analyst_signal IN ('HOLD', 'SKIP')` |

Excess return is `shadow_outcomes.return_pct - shadow_outcomes.benchmark_return_pct` for the
`1w` horizon, in percentage points. Both legs are total returns on one basis, corrected over the
window `(session_date, as_of]` per the split and dividend rules in CLAUDE.md; a mark taken late
therefore equals one taken promptly, which is what lets a delayed marking run still count.

Both arms cleared the fundamental gate *and* the technical gate. The **only** systematic
difference between them is the analyst's verdict. This is exactly the counterfactual the
`technical_verdict` column exists to support — CLAUDE.md's *"what would a pipeline without the
analyst have posted?"* — and it is why that column is recorded on `rejected_signal` rows as
well as on `recommended` ones.

**The estimand keys on `analyst_signal`, not on `outcome = 'recommended'`.** `recommended` also
encodes that a row was posted to Discord, and `human_action` encodes what a person then did
with it. Neither is the analyst's judgement. A criterion keyed on `outcome` would silently
measure the posting pipeline and the human alongside the model.

### 1.1 What this is not

* Not "is the strategy profitable" — the standing constraint is unchanged.
* Not "is the analyst's reasoning good" — only its BUY/not-BUY decision is measured.
* Not a claim about any individual recommendation.

---

## 2. Unit of independence: the ISO week

**This section is what makes the criterion honest, and the easiest part to get wrong.**

A naive reading treats each row as a data point: ~17 technical passers per session, ~85 a week,
significance in days. That is wrong twice over.

* **Cross-sectionally** — every name in one session shares one market window. Seventeen names
  screened on the same morning and marked on the same morning are close to one observation of
  "how did that week go", not seventeen.
* **Serially** — at a 1-week horizon, consecutive *sessions*' windows overlap by four trading
  days. Monday's window and Tuesday's differ in one day out of five.

So the analysis collapses to **one observation per ISO week**: the mean per-session spread
(arm B mean − arm A mean) across the sessions in that week. Monday-to-Monday windows in adjacent
weeks share only an endpoint, so weekly observations are approximately independent.

**A session contributes only if both arms are non-empty**, since a spread between a group and
nothing is undefined; a week contributes only if at least one of its sessions did. Sessions and
weeks dropped this way are **counted and reported** (§6), because a silent drop would let the
qualifying sample be shaped by which sessions happened to produce a BUY — the same selection
effect the screen-price rule removes from `reference_price`.

Empirically, on the only cohort with marks (2026-08-22, n = 50), the per-observation standard
deviation of 1w excess return is **2.53pp**. Under weekly aggregation, detectable effect sizes
are roughly:

| True effect | Weekly observations needed (80% power, α = .05) |
|---|---|
| 3pp | ~2 |
| **2pp** | **~4–8** |
| 1pp | ~25+ |
| < 1pp | out of reach for this system, permanently |

**The threshold in §3 sits where detection is actually possible.** A criterion demanding a 1pp
effect would be a criterion that never fires — a decision to keep the analyst forever,
disguised as a deferral.

---

## 3. The rule

> **FIRES when all three hold:**
>
> 1. the **mean weekly spread** (arm B − arm A) is **≥ +2.0 percentage points**;
> 2. there are **≥ 8 weekly observations** from qualifying sessions (§4);
> 3. a **bootstrap 90% interval** over the weekly observations **excludes zero**.
>
> **Direction is pre-specified.** Only B > A can fire. A result the other way — the analyst's
> picks beating its rejects — is reported and changes nothing. That is the null.

### 3.1 Checkpoints, and the honest admission about them

The rule is evaluated at **8, 12 and 16 weekly observations**, and at no other count. Reports
may be produced whenever marks land; *decisions* happen only at those three points.

Three sequential looks inflate type-I error above the nominal 10%. This is stated rather than
concealed, and tolerated for two specific reasons: the threshold is large relative to the
noise, and the action is cheap and fully reversible. Neither reason would survive a criterion
that authorised something irreversible.

If none of the three checkpoints fires, the decision is **keep**, and further pursuit requires
a new registration with a new null.

### 3.2 Action on firing

The analyst stops vetoing: candidates passing both gates are recommended directly. One
configuration change, trivially reversible.

**Accepted cost, named in advance:** recommendations per session rise from ~6 to ~17. CLAUDE.md
already identifies this as the real price of dropping the analyst — *"the cost of dropping the
analyst is alert fatigue and that is a per-day property"* — which is why `shadow_report.py`
prints the busiest session beside the total.

**Mandatory follow-up:** the same metric is re-run on the post-removal regime. Removal is
itself a rule boundary and therefore a new registration. If the spread does not close, the
analyst was not the cause, and the removal should be reconsidered on that evidence.

---

## 4. Which sessions count

**The first counting cohort is 2026-09-16.**

Two rule boundaries sit immediately behind that date:

| Date | Boundary |
|---|---|
| 2026-09-15 | volume gate removed; forward P/E gate; no dividend floor; one ticker per company |
| 2026-09-16 | news chain becomes Finnhub → Alpha Vantage → yfinance |

The second changes the analyst's **input distribution** — headline provenance moves from "Alpha
Vantage's relevance-ranked, sentiment-scored feed for the first 25 candidates in scan order,
yfinance for the rest" to "Finnhub's recency-ordered feed for effectively all". That is a change
to the treatment being tested, not merely to the population. A sample spanning it would be two
experiments averaged together.

Sessions **2026-08-22, 2026-09-14 and 2026-09-15** are reported as context and **cannot count**
toward the eight. 2026-09-14's `technical_verdict` values are additionally clock-determined and
unusable for any counterfactual.

### 4.1 Frozen parameters

A session counts only while these match the values registered here. Any difference disqualifies
the session and, per §5 of the 2026-08-21 spec, constitutes a new registration.

```
gate_config_json     {"max_forward_pe": 35.0, "max_rsi": 70.0,
                      "min_dividend_yield": 0.0, "min_earnings_growth": 0.05}
                     (no min_volume_ratio — its presence marks a pre-boundary row)
analyst chain        gemini/gemini-3.1-flash-lite  (daily limit 450)
                  -> gemini/gemini-3.7-flash       (daily limit 18)
                  -> deepseek/deepseek-flash       (daily limit 18)
news chain           finnhub -> alpha_vantage -> yfinance
universe             TOP_SP500_COUNT=50, dedupe cik-v1; watchlist.txt empty
scan schedule        SCAN_TIMES=09:35, ETF 10:00, SCAN_TIMEZONE=America/New_York
horizon              1w excess vs SPY, corrected per the total-return rules in CLAUDE.md
```

**The decision date is never hard-coded.** It is whenever eight qualifying weekly observations
exist, re-derived from the rows. Roughly mid-November 2026 at ~1 per week — that estimate is
not a commitment and must not be quoted forward as a date. This is the 08-28 lesson: a mark-due
date asserted in prose was quoted through two handoffs and a memory entry, was wrong by two
days, and running the command on the published date returned `0` with nothing to explain why.

---

## 5. Pre-commitments against the obvious cheats

Each forecloses a specific route to a preferred answer after seeing data.

* **Only 1w decides.** 1m, 3m and 6m are reported and can never trigger the rule. Four horizons
  would be four chances at significance.
* **Only the stock path.** ETFs skip the fundamental gate and have no technical gate, so no
  counterfactual exists for them; their `technical_verdict` is NULL by design.
* **No subgroup may trigger the decision** — not by confidence, sector, market cap, scan
  position or headline count. Subgroup results may be reported as hypotheses for a *future*
  registration. Note that `analyst_confidence` carries no information on the reject side in any
  case: all 57 HOLD and SKIP verdicts recorded through 2026-09-15 are `medium`, with `high`
  appearing only on BUYs.
* **Cache hits are excluded** (`cache_hit = 1`). A cached verdict is not a fresh judgement, and
  `analyst_cache` records neither provider nor model, so it cannot even be attributed to one.
  Currently zero rows are affected.
* **Rows with an unusable mark are excluded, never imputed** — `return_pct IS NULL` or
  `benchmark_return_pct IS NULL`. A NULL there means a price could not be read or a dividend
  could not be priced. SQLite has no NaN, and treating such a NULL as zero is precisely how the
  2026-08-30 marking run wrote 50 unusable marks.
* **No re-grading of history.** A candidate rejected at the fundamental gate never received an
  analyst call, so no amount of stored `.info` makes it gradable later. The pipeline branch is
  not recoverable.

---

## 6. Reporting format

Every report produced under this criterion states in its header what it measures:

> *the analyst's BUY/not-BUY decision among candidates that passed both gates, at one week,
> versus SPY; NOT the profitability of the deployed strategy.*

Per §3.2 of the 2026-08-21 spec, each report prints:

* the **weekly spread series**, and its mean with a bootstrap interval;
* the per-observation **median and IQR** for each arm — never the mean alone;
* **n for both arms and the number of weekly observations**, always together;
* the count of excluded rows, and why they were excluded.

No CAGR, Sharpe, Sortino, alpha or beta. That restriction is inherited, not re-litigated here.

---

## 7. What this will and will not establish

**Will:** whether the analyst's veto is *worse than not having one*, on entry timing, at a
magnitude large enough to matter, under one frozen configuration.

**Will not:** whether the analyst adds value. §2 is explicit that a real effect below ~1pp is
undetectable here at any sample this system will ever gather. A non-firing result is compatible
with the analyst helping slightly, harming slightly, or doing nothing at all, and this document
must not be cited as having ruled any of those out.

**Will not:** transfer to a different model. The chain in §4.1 is part of the treatment. Both
models on this path have been chosen on assumption and been wrong — Gemma parsed 0/3 for four
months, and DeepSeek's V4.1 routing silently enabled thinking and broke the tier. A verdict
about `gemini-3.1-flash-lite` is a verdict about `gemini-3.1-flash-lite`.

**The standing constraint is unchanged: every recommendation remains an unvalidated research
lead.**

---

## 8. Deliberately not built here

§5 of the 2026-08-21 spec sets the precedent that **a test asserts the runtime configuration
matches the preregistration, so a later tweak fails loudly rather than silently producing a
better-looking number.** The analogue — a query that refuses to count sessions whose
`gate_config_json` or analyst chain differs from §4.1 — is **not** built in this document.

The reason is timing: this had to be registered before the 2026-09-16 cohort existed, and code
lands more slowly than a document. Until that enforcement exists, §4.1 is checked by hand. That
is a known weakness of this registration, recorded here rather than discovered later.
