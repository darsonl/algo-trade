# Codex Project Review and Recommendations

Reviewed: 2026-08-14

## Executive assessment

The stock and ETF mechanisms are not sufficiently validated or safe for unattended live trading. The engineering and unit-test quality are respectable, but the system currently provides research candidates rather than proven investment recommendations.

The stock mechanism combines plausible fundamental and technical filters with an LLM judgment, but there is no historical or forward evidence that this combination generates excess risk-adjusted returns. The ETF mechanism is weaker because an LLM `BUY` response is accepted without a deterministic technical or expense-ratio gate.

Human approval reduces automation risk, but the present approval and brokerage implementation contains critical safety gaps that should be fixed before setting `DRY_RUN=false`.

## Prioritized findings

### 1. Critical: `PAPER_TRADING=true` does not provide paper trading

The `paper_trading` flag is defined in `config.py`, but it only affects the startup warning in `main.py`. Brokerage authentication always creates the same Schwab client, and order placement checks only `dry_run`.

Consequently, this configuration can place real orders without displaying the live-trading warning:

```env
DRY_RUN=false
PAPER_TRADING=true
```

Relevant locations:

- `config.py:61`
- `main.py:703`
- `discord_bot/bot.py:101`
- `schwab_client/auth.py:10`

Recommendation:

- Until a genuine simulated broker adapter exists, fail startup whenever `DRY_RUN=false` and `PAPER_TRADING=true`.
- Alternatively, remove the flag so it cannot communicate safety that the implementation does not provide.
- Add an explicit broker-mode abstraction with separate simulated and live implementations.
- Add an integration test proving that every non-live mode is structurally incapable of reaching `client.place_order`.

### 2. Critical: approvals are neither authorized nor revalidated

The Discord approval handlers do not inspect `interaction.user`. Any member who can access and interact in the configured channel can approve or reject a recommendation.

The database claim operation checks only that a recommendation is `pending`; it does not check `expires_at`. Persistent buttons can therefore approve an old recommendation until some later scan runs the expiration cleanup.

Quantity, portfolio exposure, trade records, and limit-order prices are also based on the price captured during the scan. No live price is fetched before approval.

Relevant locations:

- `discord_bot/bot.py:55`
- `discord_bot/bot.py:56`
- `discord_bot/bot.py:69`
- `discord_bot/bot.py:84`
- `database/queries.py:36`
- `database/queries.py:184`

Recommendation:

- Add `ALLOWED_DISCORD_USER_IDS` or an explicit Discord-role allowlist.
- Verify the expected guild and channel in every trade interaction.
- Atomically claim only recommendations that are pending and unexpired.
- Fetch a fresh quote before calculating quantity or submitting an order.
- Reject approval when the current price has moved beyond a configurable tolerance from the recommendation price.
- Recalculate portfolio exposure using current broker positions and prices immediately before order submission.
- Display the refreshed price and require a second confirmation if the change is material.

### 3. High: concurrent scans can create duplicate recommendations and buys

Manual slash commands start unrestricted background tasks. The duplicate check and recommendation insertion are separate operations, and there is no database uniqueness constraint covering an active recommendation.

Two manual scans, or a manual and scheduled scan, can both pass the duplicate check and produce separate pending recommendations for the same ticker. Each recommendation has its own idempotent button, so both can still be approved and place separate orders.

Relevant locations:

- `discord_bot/bot.py:386`
- `discord_bot/bot.py:394`
- `main.py:253`
- `main.py:553`
- `database/models.py:41`

Recommendation:

- Add a process-level `asyncio.Lock` around each scan pipeline.
- Reject a slash-command scan when the corresponding scan is already running.
- Add a database-level atomic reservation or partial unique index for active recommendations.
- Treat stock and ETF scans consistently when the same symbol can appear in both paths.

### 4. High: order acknowledgements are treated as completed fills

The system creates a trade and position immediately after the broker acknowledges an order. It does not verify whether the order filled, partially filled, remained open, or was cancelled.

Recorded trade prices are the scan-time displayed prices rather than actual broker execution prices. This affects both buys and sells. As a result:

- Limit orders can create phantom positions.
- A partially filled order can be recorded as fully filled.
- P&L and win-rate statistics can be wrong.
- Portfolio-limit calculations can use positions that do not exist at the broker.
- The sell pass can attempt to sell shares that were never acquired.

Relevant locations:

- `discord_bot/bot.py:124`
- `discord_bot/bot.py:129`
- `discord_bot/bot.py:141`
- `discord_bot/bot.py:240`
- `database/queries.py:69`
- `database/queries.py:92`

Recommendation:

- Model orders separately from trades and positions.
- Track states such as `submitted`, `working`, `partially_filled`, `filled`, `cancelled`, and `rejected`.
- Poll or consume broker order updates.
- Create or update positions only from confirmed executions.
- Record fill price, fill quantity, execution time, fees, and slippage.
- Make reconciliation capable of blocking unsafe follow-up actions, while keeping automatic corrections conservative.

### 5. High: ETF recommendations can reuse stale analysis

The ETF pipeline accepts any LLM `BUY` response and does not apply the stock pipeline's deterministic technical filter. The expense-ratio threshold only changes the Discord display.

The analyst cache key contains only the ticker and a hash of headlines. It omits:

- Price
- RSI
- Moving average
- MACD
- Expense ratio
- Macro context
- Model and provider
- Prompt version
- Analysis age

If the headline set is unchanged, an ETF can reuse an earlier `BUY` after its market conditions change. There is no cache TTL.

Relevant locations:

- `main.py:70`
- `main.py:175`
- `main.py:588`
- `main.py:592`
- `database/queries.py:194`
- `database/models.py:72`

Recommendation:

- Apply explicit ETF eligibility and technical rules after analysis.
- Make expense ratio a real rule where appropriate, not only a warning.
- Include the complete feature snapshot, model, and prompt version in the cache key.
- Add a short, explicit TTL for market-sensitive analyses.
- Prefer caching raw news retrieval rather than caching the final trading decision.

### 6. High: there is no evidence that either strategy generates alpha

The repository explicitly states that it has no backtesting and no evidence that the screening criteria are profitable. The local database contained no recommendations or trades at review time, so it also provides no forward-performance sample.

The 546 automated tests validate software behavior. They do not validate predictive power, profitability, risk-adjusted returns, or robustness across market regimes.

Relevant locations:

- `README.md:205`
- `README.md:206`

Recommendation:

- Treat every recommendation as an unvalidated research lead.
- Do not infer reliability from unit-test coverage.
- Require both a point-in-time backtest and a meaningful forward paper-trading period before considering live use.
- Benchmark results against passive alternatives and simple rules.

### 7. Medium-high: the stock universe ranking is financially weak

The added S&P 500 universe is ranked using raw trailing EPS and ROE rank sums. Raw EPS is not comparable across companies because it depends on share count, share price, splits, and capital structure. ROE can be inflated by leverage, buybacks, or a small equity base.

Missing values are converted to zero, and only the top-ranked names are screened. This can create unstable and economically arbitrary universe selection.

Relevant locations:

- `screener/universe.py:214`
- `screener/universe.py:241`
- `screener/universe.py:242`
- `screener/universe.py:262`

Recommendation:

- Use scale-independent factors such as earnings yield, free-cash-flow yield, ROIC, gross profitability, leverage, and normalized earnings growth.
- Winsorize or robustly rank outliers.
- Define a deliberate missing-data policy.
- Add sector-neutral ranking if sector concentration is not intended.
- Preserve point-in-time constituent and fundamental data for research; current S&P 500 membership creates survivorship bias in historical tests.

### 8. Medium-high: the stock fundamental filter has permissive missing-data behavior

Trailing P/E is mandatory, but dividend yield and earnings growth checks are skipped when the corresponding value is missing. A company with incomplete growth information can therefore pass a nominal growth filter.

The filter also lacks debt, cash-flow quality, liquidity, market-cap, and earnings-quality checks. P/E by itself behaves poorly across sectors and does not handle loss-making businesses consistently.

Relevant locations:

- `screener/fundamentals.py:29`
- `screener/fundamentals.py:46`
- `screener/fundamentals.py:53`

Recommendation:

- Track missing values explicitly rather than silently treating them as acceptable.
- Reject insufficient data or assign a documented uncertainty penalty.
- Add liquidity and financial-quality requirements.
- Use sector-aware valuation measures.
- Validate all yfinance field units with recorded fixtures and contract tests.

### 9. Medium-high: the exit mechanism does not protect losing positions

The sell trigger requires both:

- RSI above the configured overbought threshold; and
- MACD below its signal line.

This is a profit-taking/reversal pattern, not a complete risk-management policy. A stock that declines steadily may never become overbought and therefore may never reach the LLM sell analysis.

Relevant location:

- `screener/exit_signals.py:5`
- `screener/exit_signals.py:21`

Recommendation:

- Add a maximum loss or volatility-adjusted stop.
- Add trailing exits for profitable positions.
- Add maximum holding periods and thesis-review dates.
- Add earnings and material-event risk rules.
- Add portfolio-level drawdown, sector, and correlation controls.
- Test exit rules together with entry rules rather than in isolation.

### 10. Medium: portfolio exposure calculations can be stale

The approval guard prefers `positions.last_price`, but the position-summary function fetches live prices without persisting them. In normal operation, `last_price` is commonly `NULL`, causing exposure to fall back to historical average cost.

The new position is also valued using the scan price, not a fresh quote or actual expected execution price. Rising holdings or a rising candidate can therefore make actual exposure exceed the configured ceiling.

Relevant locations:

- `discord_bot/bot.py:69`
- `screener/positions.py:7`
- `screener/positions.py:18`
- `database/models.py:90`

Recommendation:

- Calculate pre-trade exposure from the broker's current positions and market values.
- Include working orders in reserved exposure.
- Apply a price/slippage buffer.
- Enforce per-symbol, per-sector, and correlated-exposure limits in addition to the total ceiling.

### 11. Medium: the ETF watchlist contains substantial overlapping exposure

The default ETF watchlist includes SPY, VOO, IVV, and VTI, which have substantial overlapping US equity exposure. QQQ and XLK can further concentrate technology exposure.

The current system assesses each ETF independently and has no portfolio construction layer. It can therefore recommend multiple near-substitutes without explaining the aggregate exposure.

Recommendation:

- Categorize ETFs by asset class, region, factor, sector, duration, and credit risk.
- Measure holdings overlap and return correlation.
- Select one representative ETF per intended exposure unless multiple funds serve a documented purpose.
- Consider assets already held before issuing a recommendation.
- Include liquidity, bid-ask spread, assets under management, tracking difference, and fund structure.

### 12. Medium: the LLM is not a reliable decision boundary

The LLM sees a small collection of headlines plus selected summary fields. It does not receive filings, complete articles, transcripts, source reliability, article timestamps, or a calibrated statistical model.

Headline text is interpolated directly into the prompt, which also creates a prompt-injection surface. Different providers and models can produce different decisions, and no temperature or deterministic seed is configured. The returned confidence value is self-reported and uncalibrated.

Relevant locations:

- `analyst/claude_analyst.py:121`
- `analyst/claude_analyst.py:135`
- `analyst/claude_analyst.py:192`
- `analyst/claude_analyst.py:216`
- `analyst/claude_analyst.py:295`

Recommendation:

- Keep the LLM as a summarization and explanation layer rather than the authoritative BUY/SELL gate.
- Delimit external content and explicitly instruct the model to treat it as untrusted data.
- Preserve headline source, publication time, URL, and relevance.
- Record the provider, exact model, prompt version, inputs, and response for every recommendation.
- Evaluate provider agreement and decision stability.
- Calibrate confidence against forward outcomes rather than trusting the model's label.

### 13. Medium: intraday volume filtering can depend heavily on scan time

Technical data uses the last daily volume bar and compares it with a 20-bar average. Around the market open, the current daily bar may be incomplete while historical bars represent full sessions. This can reject otherwise qualifying stocks solely because the scan ran early.

Relevant locations:

- `screener/technicals.py:96`
- `screener/technicals.py:119`
- `screener/technicals.py:120`

Recommendation:

- Use the most recent completed daily bar for daily-volume comparisons.
- Alternatively, compare intraday volume against time-of-day-normalized historical volume.
- Record the data timestamp and market-session state with every signal.

## Reliability assessment by component

| Component | Assessment | Reason |
|---|---|---|
| Unit-level software behavior | Good | Extensive mocked and pure-function tests; all 546 passed during review. |
| Operational error handling | Generally good | Retries, fallbacks, alerts, idempotent per-recommendation buttons, and reconciliation reporting are present. |
| Live-trading safety | Unsafe | Paper flag is ineffective; approvals lack authorization and freshness checks; fills are not confirmed. |
| Stock recommendation quality | Unproven | Plausible heuristics, but no backtest or forward sample and weak universe/factor construction. |
| ETF recommendation quality | Weak/unproven | LLM BUY is the primary gate, stale caching is possible, and portfolio overlap is ignored. |
| Sell/risk management | Incomplete | Overbought reversal rule does not constrain losses or portfolio drawdown. |
| Performance statistics | Unreliable for live evaluation | Stored prices are recommendation prices rather than confirmed fills. |
| Market data robustness | Suitable for prototyping | yfinance is convenient but is not a validated institutional data feed. |

## Recommended implementation roadmap

### Phase 1: prevent unsafe live operation

1. Make the application refuse `DRY_RUN=false` while `PAPER_TRADING=true` unless a real simulated broker exists.
2. Add Discord approver authorization.
3. Enforce expiry atomically during approval.
4. Fetch a current quote and recalculate quantity and exposure.
5. Add scan locks and database-level duplicate prevention.
6. Add explicit emergency controls such as `TRADING_ENABLED=false`, a daily notional ceiling, and a kill switch.

### Phase 2: build a correct execution ledger

1. Separate recommendations, orders, executions, trades, and positions.
2. Track the complete broker order lifecycle.
3. Build positions only from executions.
4. Record actual fill prices and quantities.
5. Derive performance from execution records.
6. Include open orders in risk calculations.

### Phase 3: create a validation framework

Build an event-driven, point-in-time research harness that:

- Uses historical constituent membership and point-in-time fundamentals.
- Avoids look-ahead and survivorship bias.
- Uses only information available as of each simulated decision time.
- Models dividends, splits, commissions, bid-ask spread, slippage, and rejected/unfilled orders.
- Separates training, validation, and untouched out-of-sample periods.
- Uses walk-forward evaluation across bull, bear, high-volatility, and rate-change regimes.
- Benchmarks against SPY, buy-and-hold, and simpler rules.

Report at least:

- CAGR and total return
- Annualized volatility
- Sharpe and Sortino ratios
- Maximum drawdown and recovery time
- Win rate, payoff ratio, and profit factor
- Turnover and estimated trading costs
- Market and sector exposure
- Alpha and beta relative to appropriate benchmarks
- Results by year and market regime
- Bootstrap confidence intervals or another uncertainty estimate

Backtests are still hypothetical and subject to hindsight and overfitting. They are a minimum validation step, not proof of future returns.

### Phase 4: redesign the signals

For stocks:

- Use explicit, scale-independent value, quality, momentum, and risk factors.
- Normalize factors cross-sectionally and, where appropriate, within sectors.
- Define missing-data and outlier handling.
- Add liquidity and tradability constraints.
- Make the final ranking deterministic and auditable.

For ETFs:

- Use an explicit allocation objective.
- Evaluate trend, volatility, drawdown, liquidity, spread, expense ratio, tracking, overlap, and asset-class role.
- Prevent redundant recommendations.
- Use different features for equity, bond, commodity, and international ETFs.

For exits:

- Combine thesis exits, protective loss limits, trailing exits, time stops, and portfolio controls.
- Validate entry and exit rules as a unified strategy.

The LLM can then explain deterministic results, flag contradictory news, and create a research brief without being the sole source of the trade decision.

### Phase 5: forward validation

1. Freeze the strategy and configuration before evaluation.
2. Run in dry-run or genuinely simulated mode for a meaningful period.
3. Save every candidate, rejection, recommendation, approval decision, feature snapshot, and hypothetical fill.
4. Evaluate all signals, not only executed or approved trades, to avoid selection bias.
5. Compare observed results with backtest expectations and investigate drift.
6. Define objective promotion and rollback criteria before enabling live orders.

## Testing and project-quality observations

Verification performed during this review:

- `pytest -q`: **546 passed**, with two warnings.
- Project-scoped Ruff check: **passed**.
- Python compilation check: **passed**.
- Working tree after review: **clean**.

Warnings observed:

- `pytest-asyncio` reports that `asyncio_default_fixture_loop_scope` is unset. Configure it explicitly to avoid future behavior changes.
- Dependency warnings relate to deprecated `audioop` and the legacy websockets API. Track these before moving to a Python version where they become breaking changes.

The test suite is broad, but it relies heavily on mocks and synthetic inputs. Important missing test categories include:

- Paper-versus-live broker isolation
- Authorized and unauthorized Discord users
- Expired-button approval
- Price movement between scan and approval
- Concurrent scan races
- Broker partial fills and cancellations
- Portfolio exposure based on live broker values
- Cache invalidation when market features change but headlines do not
- Real recorded yfinance response fixtures and field-unit contracts
- Historical or forward predictive-performance evaluation

## External data and research cautions

- yfinance's official documentation describes it as an open-source tool using Yahoo's publicly available APIs, intended for research and educational use, and not affiliated with or vetted by Yahoo. It is appropriate for prototyping but should not be assumed to provide an institutional data contract: <https://ranaroussi.github.io/yfinance/>
- The SEC has discussed how backtested results can be optimized with hindsight. A future backtest should therefore use strict point-in-time data, walk-forward design, and untouched out-of-sample evaluation: <https://www.sec.gov/file/ia-5407>

## Bottom line

The project has a solid software-testing foundation and several thoughtful operational safeguards. Nevertheless, its recommendation mechanisms are not yet reliable in the investment-performance sense, and the current live-order path contains critical safety defects.

Use it as a dry-run research assistant only. Fix the live-trading safety and execution-accounting issues first, then build a rigorous validation framework before deciding whether the stock or ETF signals deserve capital.
