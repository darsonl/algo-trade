from __future__ import annotations
import asyncio
import logging
import threading
import weakref
from dataclasses import replace
from datetime import datetime, timezone
from typing import Literal

import discord
from discord import app_commands
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import queries
from database.models import get_cursor, immediate_transaction
from database.order_accounting import BLOCKING_ORDER_STATUSES
from risk import kill_switch
from risk.scan_lock import scan_in_progress
from risk.kill_switch import TradingHalted
from risk.preflight import (
    BrokerSnapshot,
    Decision,
    TradeRequest,
    check_authorization,
    evaluate_trade,
)
from discord_bot.embeds import build_recommendation_embed, build_positions_embed, build_sell_embed, build_etf_recommendation_embed, build_stats_embed, build_history_embed
from schwab_client.orders import (
    build_limit_buy,
    build_marketable_sell,
    _call_place_order,
    SubmissionOutcome,
    classify_submission,
    collect_broker_snapshot,
)
from risk.resolution import report_unknown_submissions
from schwab_client.quotes import QuoteUnavailable, fetch_quote, marketable_sell_limit

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """One seam for the clock, so tests can pin time without freezegun."""
    return datetime.now(timezone.utc)


def _record_human_action(config: Config, rec_id: int, action: str, at: datetime) -> None:
    """Attach the human's click to its shadow observation. Never raises.

    Approval latency -- the gap between the signal and the click -- is the one
    quantity no retrospective study can recover, so it has to be recorded live,
    which means recording it on the order path itself.

    That makes this wrapper load-bearing rather than decorative, and for a
    different reason than `main._record_shadow`: `set_shadow_human_action` is a
    plain UPDATE with no guard of its own (unlike `shadow_log.observe`, which
    absorbs its own failures). An OperationalError escaping here would abort an
    approval AFTER the order had already been sent to the broker -- the caller
    would see a failure for a trade that actually exists.
    """
    try:
        queries.set_shadow_human_action(
            config.db_path, rec_id, action, at.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    except Exception:
        logger.exception(
            "Shadow human-action write failed for recommendation %s; continuing", rec_id
        )


def _chunk_message(text: str, limit: int = 1900) -> list[str]:
    """Split a long report into Discord-sized messages, on line boundaries.

    Discord rejects anything over 2000 characters. A resolution report grows
    with the number of candidates, so truncating would drop exactly the
    candidates an operator has not seen yet. Splitting on newlines keeps each
    order's block readable rather than cutting mid-line.
    """
    if not text:
        return [""]

    chunks, current = [], ""
    for line in text.split("\n"):
        # A single line longer than the limit is hard-split; nothing is dropped.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def _parse_stamp(value) -> datetime | None:
    """Read a SQLite UTC timestamp, or None if it is absent or unreadable.

    None means "no expiry recorded", which guard 3 treats as not-expired. That
    is the right reading here and only here: an absent expires_at is a schema
    fact about old rows, not a failed read of a live one.
    """
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# One approval lock PER RUNNING LOOP, for exactly the reason the submission
# gate is: a module-level asyncio.Lock binds to the first loop that *contends*
# on it and raises for every loop after, and acquire()'s uncontended fast path
# hides that from any test which never actually blocks. See
# risk/kill_switch.py::submission_gate and tests/test_kill_switch_gate.py.
#
# This is a WIDER lock than the submission gate. The gate spans the final
# kill-switch read through dispatch; this one spans the whole
# read -> evaluate -> claim -> submit sequence, so two approvals cannot
# interleave their broker reads and each evaluate against the other's
# pre-reservation view of the book.
_approval_gates: "weakref.WeakKeyDictionary[object, asyncio.Lock]" = weakref.WeakKeyDictionary()
_approval_gates_lock = threading.Lock()


def guard_snapshot(config) -> BrokerSnapshot:
    """The book the guards evaluate against.

    Live: the broker, failing closed (`None`, never `[]`).

    Dry run: the SIMULATED book, built from the positions table. The broker
    holds nothing in a dry run, so a broker-sourced guard 12 would refuse every
    simulated sell and guard 10 would never see a simulated holding — the guards
    would be running against a book that has nothing to do with the one the
    simulation is keeping. A dry run that guards against the wrong book
    rehearses nothing, and it also spares a disarmed bot a live API call.

    `working_orders` is empty in dry run because a simulated order is never
    submitted, so nothing is ever working.
    """
    if not getattr(config, "dry_run", False):
        return collect_broker_snapshot(config)

    positions = queries.get_open_positions(config.db_path)
    return BrokerSnapshot(
        positions=[
            {
                "symbol": p["ticker"],
                "quantity": p["shares"],
                "market_value": p["shares"] * (
                    p["last_price"] if p["last_price"] is not None else p["avg_cost_usd"]
                ),
                "avg_price": p["avg_cost_usd"],
            }
            for p in positions
        ],
        working_orders=[],
    )


def approval_gate() -> asyncio.Lock:
    """The lock serialising a whole approval, from first read to dispatch.

    Process-local by nature. It is NOT what makes the ceiling global — that is
    the `BEGIN IMMEDIATE` transaction, which serialises across processes too
    (round-4 finding 8: this lock alone cannot stop a cross-ticker cap breach
    between two processes during an overlapping restart). This one only closes
    the interleaving window between coroutines on one loop, and keeps the
    broker reads from crossing.
    """
    loop = asyncio.get_running_loop()
    with _approval_gates_lock:
        gate = _approval_gates.get(loop)
        if gate is None:
            gate = asyncio.Lock()
            _approval_gates[loop] = gate
        return gate

_retry = retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    reraise=True,
)


# The ops-alert CRUD takes a connection rather than a db_path, so that a caller
# can one day enqueue an alert inside the same transaction as the state change
# that warranted it. The bot has no such need, so these adapt it to the
# db_path-taking shape every other DB call here dispatches via to_thread.

def _enqueue_alert(db_path: str, message: str) -> int:
    with get_cursor(db_path) as conn:
        return queries.enqueue_ops_alert(conn, message)


def _mark_alert_delivered(db_path: str, alert_id: int) -> None:
    with get_cursor(db_path) as conn:
        queries.mark_ops_alert_delivered(conn, alert_id)


def _record_alert_failure(db_path: str, alert_id: int, error: str) -> None:
    with get_cursor(db_path) as conn:
        queries.record_ops_alert_failure(conn, alert_id, error)


def _pending_alerts(db_path: str, limit: int) -> list[dict]:
    with get_cursor(db_path) as conn:
        return queries.get_undelivered_ops_alerts(conn, limit=limit)


@_retry
async def _send_message(channel, embed, view):
    return await channel.send(embed=embed, view=view)


def is_authorized(config, user_id: int) -> bool:
    """Whether this Discord user may operate the kill switch.

    Empty allowlist authorizes nobody. An unparseable entry is skipped rather
    than treated as a wildcard — a typo must narrow access, never widen it.

    Both /halt AND /resume are guarded. The design's first version allowlisted
    only /halt, which protects the wrong direction: it left anyone able to
    clear a halt in the middle of an incident.
    """
    allowed = set()
    for part in str(getattr(config, "ops_user_ids", "") or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            allowed.add(int(part))
        except ValueError:
            logger.warning("Ignoring malformed OPS_USER_IDS entry %r", part)
    return user_id in allowed


class ApproveRejectView(discord.ui.View):
    """Discord UI view with Approve and Reject buttons for a pending buy recommendation."""

    def __init__(self, rec_id: int, ticker: str, price: float, config: Config, scan_time: str | None = None):
        super().__init__(timeout=None)
        self.rec_id = rec_id
        self.ticker = ticker
        self.price = price
        self.config = config
        self.scan_time = scan_time
        # Deterministic custom_ids (keyed by rec_id) make these buttons survive a bot
        # restart: the ids baked into the sent message match the ids on the view
        # re-registered at startup (see build_view_for_recommendation + setup_hook).
        # Without this, decorator buttons get a fresh random id each run, so clicks on
        # a pre-restart message route nowhere ("interaction failed").
        self.approve.custom_id = f"approve:{rec_id}"
        self.reject.custom_id = f"reject:{rec_id}"

    def _trade_request(self, interaction, expires_at=None) -> TradeRequest:
        return TradeRequest(
            side="buy", ticker=self.ticker, scan_price=self.price, rec_id=self.rec_id,
            expires_at=expires_at,
            user_id=getattr(interaction.user, "id", None),
            guild_id=getattr(interaction, "guild_id", None),
            channel_id=getattr(interaction, "channel_id", None),
        )

    def _reserve(self, request, quote, broker, trading_enabled, now):
        """Cap check, claim, and reservation as ONE `BEGIN IMMEDIATE` transaction.

        The write lock is taken before the reads, so two processes serialise
        rather than both reading the same daily total and each reserving
        against it (round-4 finding 8 — the in-process lock cannot stop that).

        The reservation IS the order row; there is no separate reservation
        table to keep consistent. Any rejection returns before the INSERT and
        the transaction rolls back, so a refusal leaves nothing behind.
        """
        with immediate_transaction(self.config.db_path) as conn:
            row = conn.execute(
                "SELECT expires_at FROM recommendations WHERE id = ?", (self.rec_id,)
            ).fetchone()
            expires_at = _parse_stamp(row["expires_at"]) if row else None
            request = replace(request, expires_at=expires_at)

            local_orders = queries.get_orders_by_status(conn, BLOCKING_ORDER_STATUSES)
            day_notional = queries.get_day_notional(conn, instant=now)

            decision = evaluate_trade(
                request, quote=quote, broker=broker, local_orders=local_orders,
                day_notional=day_notional, trading_enabled=trading_enabled,
                config=self.config, now=now,
            )
            if not decision.allowed:
                return decision, None

            if not queries.claim_recommendation_tx(conn, self.rec_id, "approved", instant=now):
                return Decision(
                    allowed=False, reason_code="already_handled",
                    message=f"This recommendation for {self.ticker} was already handled.",
                ), None

            if self.config.dry_run:
                # A simulated order must not reserve real capital: the row would
                # hold the ceiling against buys that never happened, and nothing
                # would ever resolve it.
                return decision, None

            order_id = queries.create_order(
                conn, recommendation_id=self.rec_id, ticker=self.ticker, side="buy",
                order_type="limit", requested_shares=decision.shares,
                reference_price=self.price, limit_price=decision.limit_price,
                instant=now,
            )
            return decision, order_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Guard 1 runs BEFORE defer: it is pure and instant, so an unauthorized
        # click gets a private reply rather than a public one, and costs no
        # broker calls. evaluate_trade re-checks it as guard 1 regardless.
        denial = check_authorization(self._trade_request(interaction), self.config)
        if denial is not None:
            await interaction.response.send_message(denial.message, ephemeral=True)
            return

        # Acknowledge inside Discord's 3s window; everything below is network.
        await interaction.response.defer()

        async with approval_gate():
            now = _utcnow()

            try:
                quote = await asyncio.to_thread(fetch_quote, self.ticker, self.config)
            except QuoteUnavailable as exc:
                # Guard 4 turns this into an operator-readable refusal. Raising
                # here instead would skip the table that exists to adjudicate it.
                logger.warning("No usable quote for %s: %s", self.ticker, exc)
                quote = None

            broker = await asyncio.to_thread(collect_broker_snapshot, self.config)
            trading_enabled = await asyncio.to_thread(
                kill_switch.is_enabled, self.config.db_path
            )

            decision, order_id = await asyncio.to_thread(
                self._reserve, self._trade_request(interaction), quote, broker,
                trading_enabled, now,
            )

            if not decision.allowed:
                logger.info(
                    "Buy refused for %s: %s — %s",
                    self.ticker, decision.reason_code, decision.message,
                )
                await interaction.followup.send(
                    f"**{self.ticker} not bought** ({decision.reason_code}): {decision.message}"
                )
                return

            # Past the guards means `_reserve` claimed the row, so this click IS
            # the approval — and only the first click ever gets here, because a
            # second one loses the claim and returns above as `already_handled`.
            #
            # It sits BEFORE the dry-run branch on purpose: that branch returns
            # without reaching the submission path, so recording further down
            # would stop measuring latency the day the bot is armed — exactly
            # when the number starts to matter. `now` is the same instant the
            # guards evaluated, so the funnel measures against one clock.
            await asyncio.to_thread(
                _record_human_action, self.config, self.rec_id, "approved", now
            )

            if self.config.dry_run:
                await asyncio.to_thread(self._record_position, decision, None)
                await interaction.followup.send(
                    f"[DRY RUN] Approved: {decision.shares:g} share(s) of {self.ticker} "
                    f"at a ${decision.limit_price:.2f} limit — all guards passed, "
                    "no order sent and nothing reserved."
                )
                self.stop()
                return

            outcome = await self._submit(decision, order_id)
            if outcome.status == "submitted":
                await asyncio.to_thread(
                    self._record_position, decision, outcome.broker_order_id
                )

        await self._report(interaction, decision, outcome)
        self.stop()

    async def _submit(self, decision, order_id):
        """Submit once and classify the result. Never raises.

        The submission gate spans the final kill-switch read through dispatch:
        without it /halt could land on an await boundary here, read ENABLED,
        and let this submit anyway after /halt had already replied "halted".
        """
        spec = build_limit_buy(
            self.ticker, int(decision.shares), f"{decision.limit_price:.2f}"
        )
        try:
            async with kill_switch.submission_gate():
                response = await asyncio.to_thread(
                    _call_place_order, None, self.config, spec
                )
            outcome = classify_submission(response=response)
        except TradingHalted as exc:
            # Nothing was dispatched, so this is a definitive non-submission.
            # Releasing the reservation is correct precisely because we KNOW
            # nothing exists — unlike every other failure below.
            logger.warning("Buy blocked for %s: %s", self.ticker, exc)
            outcome = SubmissionOutcome(
                status="submit_failed", broker_order_id=None,
                message=("Trading is halted, so no order was sent. The "
                         "recommendation stays open — approve again after /resume."),
            )
        except Exception as exc:
            outcome = classify_submission(error=exc)
            logger.error(
                "Buy submission for %s resolved as %s: %s",
                self.ticker, outcome.status, exc,
            )

        await asyncio.to_thread(self._settle, order_id, outcome)
        return outcome

    def _settle(self, order_id: int, outcome) -> None:
        """Record what the submission turned out to be.

        Only `submit_failed` reopens the recommendation. An ambiguous outcome
        leaves it `approved` on purpose: reopening invites a second human
        approval and a second real order for something that may already exist
        (spec §3). Guard 11 blocks new buys of the ticker meanwhile.
        """
        with get_cursor(self.config.db_path) as conn:
            if outcome.status == "submitted":
                queries.attach_broker_order_id(conn, order_id, outcome.broker_order_id)
            elif outcome.status == "submit_failed":
                queries.mark_order_submit_failed(conn, order_id, outcome.message)
            else:
                queries.mark_order_submit_unknown(conn, order_id, outcome.message)

        if outcome.status == "submit_failed":
            queries.update_recommendation_status(self.config.db_path, self.rec_id, "pending")

    def _record_position(self, decision, order_id: str | None) -> None:
        """Record the trade and position, as before this change.

        The ledger owns the CEILINGS; it does not yet own positions. The sell
        pass, /positions, /stats and the duplicate-recommendation check all
        still read the positions table, so removing this would silently strand
        every one of them. RISK-05 still applies — a GTC limit is recorded here
        on acknowledgement, not on fill, and `run_reconciliation` reports the
        drift rather than papering over it.
        """
        queries.create_trade(
            db_path=self.config.db_path, recommendation_id=self.rec_id,
            ticker=self.ticker, shares=decision.shares, price=self.price,
            order_id=order_id, limit_price=decision.limit_price, order_type="limit",
        )
        queries.upsert_position(
            self.config.db_path, self.ticker, decision.shares, self.price
        )

    async def _report(self, interaction, decision, outcome) -> None:
        elapsed = f" (scan at {self.scan_time})" if self.scan_time else ""
        if outcome.status == "submitted":
            await interaction.followup.send(
                f"Approved: buying {decision.shares:g} share(s) of {self.ticker} at a "
                f"${decision.limit_price:.2f} limit, GTC{elapsed}. "
                f"Order {outcome.broker_order_id}."
            )
        elif outcome.status == "submit_failed":
            await interaction.followup.send(
                f"**{self.ticker} not bought.** {outcome.message}"
            )
        else:
            await interaction.followup.send(
                f"⚠️ **{self.ticker} order outcome UNKNOWN.** {outcome.message}"
            )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        clicked = _utcnow()
        claimed = await asyncio.to_thread(
            queries.claim_recommendation, self.config.db_path, self.rec_id, "rejected"
        )
        if not claimed:
            await interaction.response.send_message(
                f"This recommendation for {self.ticker} was already handled.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(f"Rejected {self.ticker}.")
        # After the reply, not before: this handler never defers, so it owes
        # Discord an answer inside 3 seconds and instrumentation must not sit in
        # front of it. `clicked` was captured on entry, so the latency is still
        # measured from the click rather than from whenever this line runs.
        await asyncio.to_thread(
            _record_human_action, self.config, self.rec_id, "rejected", clicked
        )
        self.stop()


class SellApproveRejectView(discord.ui.View):
    """Discord UI view with Approve and Reject buttons for a pending sell recommendation."""

    def __init__(self, rec_id: int, ticker: str, shares: float, current_price: float, config: Config):
        super().__init__(timeout=None)
        self.rec_id = rec_id
        self.ticker = ticker
        self.shares = int(shares)  # Schwab expects int
        self.current_price = current_price
        self.config = config
        # Deterministic custom_ids (see ApproveRejectView) so a sell view re-registered
        # after restart matches the buttons on the original message.
        self.approve.custom_id = f"sell_approve:{rec_id}"
        self.reject.custom_id = f"sell_reject:{rec_id}"

    def _trade_request(self, interaction, expires_at=None) -> TradeRequest:
        return TradeRequest(
            side="sell", ticker=self.ticker, scan_price=self.current_price,
            rec_id=self.rec_id, shares=float(self.shares), expires_at=expires_at,
            user_id=getattr(interaction.user, "id", None),
            guild_id=getattr(interaction, "guild_id", None),
            channel_id=getattr(interaction, "channel_id", None),
        )

    def _reserve(self, request, quote, broker, trading_enabled, now):
        """Evaluate, claim, and record the sell as one transaction.

        A sell row reserves no buy capital — `remaining_buy_reservation`
        returns 0 for sells, because selling reduces exposure rather than
        consuming the ceiling. The row exists for the audit trail, and so that
        guard 11 can see an unresolved sell and refuse to compound it.
        """
        with immediate_transaction(self.config.db_path) as conn:
            row = conn.execute(
                "SELECT expires_at FROM recommendations WHERE id = ?", (self.rec_id,)
            ).fetchone()
            request = replace(
                request, expires_at=_parse_stamp(row["expires_at"]) if row else None
            )

            local_orders = queries.get_orders_by_status(conn, BLOCKING_ORDER_STATUSES)
            decision = evaluate_trade(
                request, quote=quote, broker=broker, local_orders=local_orders,
                day_notional=queries.get_day_notional(conn, instant=now),
                trading_enabled=trading_enabled, config=self.config, now=now,
            )
            if not decision.allowed:
                return decision, None

            if not queries.claim_recommendation_tx(conn, self.rec_id, "approved", instant=now):
                return Decision(
                    allowed=False, reason_code="already_handled",
                    message=f"This sell recommendation for {self.ticker} was already handled.",
                ), None

            if self.config.dry_run:
                return decision, None

            order_id = queries.create_order(
                conn, recommendation_id=self.rec_id, ticker=self.ticker, side="sell",
                order_type="limit", requested_shares=decision.shares,
                reference_price=self.current_price,
                limit_price=marketable_sell_limit(
                    quote.bid, getattr(self.config, "approval_slippage_buffer_pct", 0.5)
                ),
                instant=now,
            )
            return decision, order_id

    @discord.ui.button(label="Approve Sell", style=discord.ButtonStyle.danger, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        denial = check_authorization(self._trade_request(interaction), self.config)
        if denial is not None:
            await interaction.response.send_message(denial.message, ephemeral=True)
            return

        await interaction.response.defer()

        async with approval_gate():
            now = _utcnow()

            try:
                quote = await asyncio.to_thread(fetch_quote, self.ticker, self.config)
            except QuoteUnavailable as exc:
                # No usable bid means no validated worst case. There is no
                # market-order fallback on purpose: an unbounded market sell on
                # a stock already flagged as falling is the fill this instrument
                # exists to bound. Guard 4 turns this into a refusal.
                logger.warning("No usable quote for %s: %s", self.ticker, exc)
                quote = None

            broker = await asyncio.to_thread(guard_snapshot, self.config)
            trading_enabled = await asyncio.to_thread(
                kill_switch.is_enabled, self.config.db_path
            )

            decision, order_id = await asyncio.to_thread(
                self._reserve, self._trade_request(interaction), quote, broker,
                trading_enabled, now,
            )

            if not decision.allowed:
                logger.info(
                    "Sell refused for %s: %s — %s",
                    self.ticker, decision.reason_code, decision.message,
                )
                await interaction.followup.send(
                    f"**{self.ticker} not sold** ({decision.reason_code}): {decision.message}"
                )
                return

            if self.config.dry_run:
                await asyncio.to_thread(self._settle_position, None)
                await interaction.followup.send(
                    f"[DRY RUN] Approved: selling {self.shares} share(s) of "
                    f"{self.ticker} at ${self.current_price:.2f} — all guards passed, "
                    "no order sent."
                )
                self.stop()
                return

            outcome = await self._submit(decision, order_id, quote)
            if outcome.status == "submitted":
                await asyncio.to_thread(self._settle_position, outcome.broker_order_id)

        await self._report(interaction, outcome)
        self.stop()

    async def _submit(self, decision, order_id, quote):
        """Price from the quote the GUARDS saw, submit once, classify.

        The old path let `place_marketable_sell_order` fetch its own quote, so
        the price that was checked and the price that was sent could differ.
        One quote for both closes that gap.
        """
        limit_price = marketable_sell_limit(
            quote.bid, getattr(self.config, "approval_slippage_buffer_pct", 0.5)
        )
        spec = build_marketable_sell(self.ticker, int(decision.shares), f"{limit_price:.2f}")
        try:
            async with kill_switch.submission_gate():
                response = await asyncio.to_thread(
                    _call_place_order, None, self.config, spec
                )
            outcome = classify_submission(response=response)
        except TradingHalted as exc:
            logger.warning("Sell blocked for %s: %s", self.ticker, exc)
            outcome = SubmissionOutcome(
                status="submit_failed", broker_order_id=None,
                message=("Trading is halted, so no order was sent. The "
                         "recommendation stays open — approve again after /resume."),
            )
        except Exception as exc:
            outcome = classify_submission(error=exc)
            logger.error(
                "Sell submission for %s resolved as %s: %s",
                self.ticker, outcome.status, exc,
            )

        await asyncio.to_thread(self._settle, order_id, outcome)
        return outcome

    def _settle(self, order_id, outcome) -> None:
        if order_id is not None:
            with get_cursor(self.config.db_path) as conn:
                if outcome.status == "submitted":
                    queries.attach_broker_order_id(conn, order_id, outcome.broker_order_id)
                elif outcome.status == "submit_failed":
                    queries.mark_order_submit_failed(conn, order_id, outcome.message)
                else:
                    queries.mark_order_submit_unknown(conn, order_id, outcome.message)

        if outcome.status == "submit_failed":
            queries.update_recommendation_status(self.config.db_path, self.rec_id, "pending")

    def _settle_position(self, order_id: str | None) -> None:
        """Record the closing trade and close the position.

        Only ever called for a SUBMITTED (or dry-run) sell. An ambiguous
        outcome must leave the position open: we do not know that it sold, and
        closing it would hide a holding the account may still have.
        """
        cost_basis = None
        for pos in queries.get_open_positions(self.config.db_path):
            if pos["ticker"] == self.ticker:
                cost_basis = pos["avg_cost_usd"]
                break

        queries.create_trade(
            db_path=self.config.db_path, recommendation_id=self.rec_id,
            ticker=self.ticker, shares=self.shares, price=self.current_price,
            order_id=order_id, side="sell", cost_basis=cost_basis,
        )
        queries.close_position(self.config.db_path, self.ticker)

    async def _report(self, interaction, outcome) -> None:
        if outcome.status == "submitted":
            await interaction.followup.send(
                f"Approved: selling {self.shares} share(s) of {self.ticker} at "
                f"${self.current_price:.2f}. Order {outcome.broker_order_id}."
            )
        elif outcome.status == "submit_failed":
            await interaction.followup.send(f"**{self.ticker} not sold.** {outcome.message}")
        else:
            await interaction.followup.send(
                f"⚠️ **{self.ticker} sell outcome UNKNOWN.** {outcome.message} "
                "The position stays open until this is settled."
            )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.secondary, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        claimed = await asyncio.to_thread(
            queries.claim_recommendation, self.config.db_path, self.rec_id, "rejected"
        )
        if not claimed:
            await interaction.response.send_message(
                f"This sell recommendation for {self.ticker} was already handled.",
                ephemeral=True,
            )
            return
        await asyncio.to_thread(queries.set_sell_blocked, self.config.db_path, self.ticker)
        await interaction.response.send_message(
            f"Rejected sell for {self.ticker}. Position sell-blocked until RSI drops below threshold.",
        )
        self.stop()


def build_view_for_recommendation(rec, config: Config) -> discord.ui.View:
    """Reconstruct the persistent view that matches a stored recommendation row.

    SELL rows → SellApproveRejectView (shares read from the open position, or 0.0 if
    none — the approve handler's has_open_position guard makes that safe). BUY/ETF rows
    → ApproveRejectView. Used at startup to re-register views so Approve/Reject buttons
    posted before a restart keep working.
    """
    if rec["signal"] == "SELL":
        shares = 0.0
        for pos in queries.get_open_positions(config.db_path):
            if pos["ticker"] == rec["ticker"]:
                shares = pos["shares"]
                break
        return SellApproveRejectView(rec["id"], rec["ticker"], shares, rec["price"], config)
    return ApproveRejectView(rec["id"], rec["ticker"], rec["price"], config)


class TradingBot(discord.Client):
    """Discord client that posts stock recommendations and handles Approve/Reject button interactions."""

    def __init__(self, config: Config):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self._scan_callback = None  # Set by main.py after construction
        self._scan_etf_callback = None  # Set by main.py after construction
        self._reconcile_callback = None  # Set by main.py after construction
        self._cached_channel = None  # Memoized by _resolve_channel on first send

    async def setup_hook(self):
        """Register and sync the /scan and /positions slash commands on bot startup."""
        self.tree.add_command(
            app_commands.Command(
                name="scan",
                description="Trigger an immediate stock scan",
                callback=self._scan_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="positions",
                description="Show current open positions and estimated P&L",
                callback=self._positions_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="scan_etf",
                description="Trigger an immediate ETF scan",
                callback=self._scan_etf_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="stats",
                description="Show win rate and P&L stats for closed trades",
                callback=self._stats_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="history",
                description="Show the last 20 closed trades",
                callback=self._history_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="reconcile",
                description="Compare bot positions against the Schwab account",
                callback=self._reconcile_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="halt",
                description="Stop all new order submissions (durable, all processes)",
                callback=self._halt_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="resume",
                description="Re-enable order submissions after a halt",
                callback=self._resume_command,
            )
        )
        self.tree.add_command(
            app_commands.Command(
                name="resolve",
                description="Report on ambiguous submissions, or record how one was resolved",
                callback=self._resolve_command,
            )
        )
        await self.tree.sync()
        self._register_persistent_views()

    def _register_persistent_views(self) -> None:
        """Re-register persistent Approve/Reject views for still-pending recommendations.

        Called on startup so buttons posted before a restart keep routing to working
        handlers. discord.py matches incoming clicks to these views by the deterministic
        custom_ids set in each view's __init__.
        """
        try:
            pending = queries.get_pending_recommendations(self.config.db_path)
        except Exception as exc:
            logger.warning("Could not load pending recommendations for view restore: %s", exc)
            return
        restored = 0
        for rec in pending:
            message_id = rec["discord_message_id"]
            if not message_id:
                continue
            try:
                view = build_view_for_recommendation(rec, self.config)
                self.add_view(view, message_id=int(message_id))
                restored += 1
            except Exception as exc:
                logger.warning("Failed to restore view for recommendation %s: %s", rec["id"], exc)
        if restored:
            logger.info("Restored %d persistent recommendation view(s) after startup", restored)

    async def _scan_command(self, interaction: discord.Interaction):
        # Answered BEFORE the task is created, so the operator is told now
        # rather than watching for results from a scan that was silently
        # dropped. ONE lock covers both scan paths -- a symbol can appear in
        # the stock universe and in the ETF universe.
        if scan_in_progress():
            await self._reply_scan_busy(interaction)
            return
        try:
            await interaction.response.send_message("Scan triggered — results incoming...")
        except Exception:
            pass  # Interaction may have expired; still run the scan
        if self._scan_callback is not None:
            asyncio.create_task(self._scan_callback())

    async def _scan_etf_command(self, interaction: discord.Interaction):
        if scan_in_progress():
            await self._reply_scan_busy(interaction)
            return
        try:
            await interaction.response.send_message("ETF scan triggered — results incoming...")
        except Exception:
            pass
        if self._scan_etf_callback is not None:
            asyncio.create_task(self._scan_etf_callback())

    @staticmethod
    async def _reply_scan_busy(interaction: discord.Interaction) -> None:
        try:
            await interaction.response.send_message(
                "A scan is already running — this one was skipped rather than "
                "queued, so nothing is lost by letting the first one finish."
            )
        except Exception:
            pass  # the interaction may have expired; there is nothing to run

    async def _reconcile_command(self, interaction: discord.Interaction):
        """Handle /reconcile: compare DB positions against Schwab and report the result."""
        if self._reconcile_callback is None:
            await interaction.response.send_message("Reconciliation is not configured.")
            return
        # Defer: the Schwab account fetch can exceed Discord's 3s interaction window.
        await interaction.response.defer()
        result = await self._reconcile_callback()
        await interaction.followup.send(result)

    async def _positions_command(self, interaction: discord.Interaction):
        """Handle /positions slash command: show open holdings with P&L."""
        from screener.positions import get_position_summary
        summaries = await asyncio.to_thread(get_position_summary, self.config.db_path)
        if not summaries:
            await interaction.response.send_message("No open positions.")
            return
        embed = build_positions_embed(summaries)
        await interaction.response.send_message(embed=embed)

    async def _stats_command(self, interaction: discord.Interaction):
        """Handle /stats slash command: show win rate and P&L stats for closed trades."""
        from database.queries import get_trade_stats
        stats = await asyncio.to_thread(get_trade_stats, self.config.db_path)
        if stats is None:
            await interaction.response.send_message(
                "No closed trades yet — nothing to analyze."
            )
            return
        embed = build_stats_embed(stats)
        await interaction.response.send_message(embed=embed)

    async def _history_command(self, interaction: discord.Interaction):
        """Handle /history slash command: show last 20 closed trades as a code-block table."""
        from database.queries import get_closed_trades
        trades = await asyncio.to_thread(get_closed_trades, self.config.db_path)
        if not trades:
            await interaction.response.send_message("No closed trades yet.")
            return
        embed = build_history_embed(trades)
        await interaction.response.send_message(embed=embed)

    async def _resolve_channel(self):
        """Return the configured channel, fetching it from the API once and caching it.

        The send_* methods post many messages per scan and each previously called
        fetch_channel (an API round-trip). The channel object is stable for the
        bot's lifetime, so memoize the first fetch and reuse it (review item 5).
        getattr-with-default tolerates instances built via __new__ in tests.
        """
        if getattr(self, "_cached_channel", None) is None:
            self._cached_channel = await self.fetch_channel(self.config.discord_channel_id)
        return self._cached_channel

    async def send_recommendation(
        self,
        rec_id: int,
        ticker: str,
        signal: str,
        reasoning: str,
        price: float,
        dividend_yield: float | None,
        pe_ratio: float | None,
        confidence: str | None = None,
        earnings_date: str | None = None,   # NEW — Phase 16 SIG-05
        scan_time: str | None = None,       # NEW — Phase 17 RISK-04
    ) -> str:
        """Fetch the configured channel, post a recommendation embed with Approve/Reject buttons, and return the message id as a string."""
        channel = await self._resolve_channel()
        embed = build_recommendation_embed(ticker, signal, reasoning, price, dividend_yield, pe_ratio, confidence=confidence, earnings_date=earnings_date, scan_time=scan_time)
        view = ApproveRejectView(rec_id, ticker, price, self.config, scan_time=scan_time)
        msg = await _send_message(channel, embed, view)
        return str(msg.id)

    async def send_sell_recommendation(
        self,
        rec_id: int,
        ticker: str,
        reasoning: str,
        entry_price: float,
        current_price: float,
        pnl_pct: float,
        shares: float,
        rsi: float,
        confidence: str | None = None,
    ) -> str:
        """Post a sell recommendation embed with Approve/Reject buttons and return the message id."""
        channel = await self._resolve_channel()
        embed = build_sell_embed(ticker, reasoning, entry_price, current_price, pnl_pct, shares, rsi, confidence=confidence)
        view = SellApproveRejectView(rec_id, ticker, shares, current_price, self.config)
        msg = await _send_message(channel, embed, view)
        return str(msg.id)

    async def send_etf_recommendation(
        self,
        rec_id: int,
        ticker: str,
        signal: str,
        reasoning: str,
        price: float | None,
        rsi: float | None,
        ma50: float | None,
        expense_ratio: float | None,
        etf_max_expense_ratio: float | None = None,
        confidence: str | None = None,
    ) -> str:
        """Post an ETF recommendation embed with Approve/Reject buttons and return the message id."""
        channel = await self._resolve_channel()
        embed = build_etf_recommendation_embed(
            ticker,
            signal,
            reasoning,
            price,
            rsi,
            ma50,
            expense_ratio,
            etf_max_expense_ratio=etf_max_expense_ratio,
            confidence=confidence,
        )
        view = ApproveRejectView(rec_id, ticker, price or 0.0, self.config)
        msg = await _send_message(channel, embed, view)
        return str(msg.id)

    async def _halt_command(self, interaction, reason: str = "no reason given") -> None:
        """/halt — stop new order submissions, durably and across processes.

        Order of operations matters. The halt is PERSISTED FIRST, then the gate
        is awaited. Doing it the other way round would leave trading enabled
        for as long as an in-flight broker call takes, which is exactly the
        window an operator is trying to close. Persisting first means even a
        /halt still queued behind a slow submission has already stopped the
        next one.

        Awaiting the gate afterwards is what lets the reply be truthful: it
        returns only once no submission is mid-flight.
        """
        if not is_authorized(self.config, interaction.user.id):
            logger.warning("Unauthorized /halt from user %s", interaction.user.id)
            await interaction.response.send_message(
                "You are not authorized to halt trading.", ephemeral=True
            )
            return

        actor = f"discord:{interaction.user.id}"
        await asyncio.to_thread(
            kill_switch.halt, self.config.db_path, actor, reason
        )
        await interaction.response.send_message(
            f"Trading HALTED by <@{interaction.user.id}> ({reason}). "
            "Waiting for any in-flight submission to finish..."
        )

        async with kill_switch.submission_gate():
            pass  # returns once nothing is mid-flight

        await interaction.followup.send(
            "Halt complete: no submission is in flight. Note this cannot recall "
            "orders the broker has **already** accepted — check open orders in "
            "Schwab for anything outstanding. /resume re-enables trading."
        )

    async def _resume_command(self, interaction, reason: str = "no reason given") -> None:
        """/resume — re-enable submissions.

        Guarded by the same allowlist as /halt. A switch anyone can clear
        protects nothing.
        """
        if not is_authorized(self.config, interaction.user.id):
            logger.warning("Unauthorized /resume from user %s", interaction.user.id)
            await interaction.response.send_message(
                "You are not authorized to resume trading.", ephemeral=True
            )
            return

        actor = f"discord:{interaction.user.id}"
        await asyncio.to_thread(
            kill_switch.resume, self.config.db_path, actor, reason
        )
        await interaction.response.send_message(
            f"Trading RESUMED by <@{interaction.user.id}> ({reason})."
        )

    async def _resolve_command(
        self,
        interaction,
        order_id: int | None = None,
        resolution: Literal["adopt", "confirmed_absent", "keep_blocked"] | None = None,
        evidence: str = "",
        broker_order_id: str | None = None,
    ) -> None:
        """/resolve — the operator's only way out of an ambiguous submission.

        Two modes, and the split is the whole design:

        With no resolution it REPORTS: it searches the broker for orders that
        might be ours and shows how each differs from what we submitted. It
        never resolves anything, not even a single exact match, because
        matching fields establish shape and not provenance.

        With a resolution it WRITES, through the audited
        `resolve_order_manually` — actor, evidence and the transition are
        recorded. `adopt` and `confirmed_absent` both release the worst-case
        reservation, which is why this shares /halt's allowlist rather than
        being open to the channel.

        Every refusal from the database layer (unknown order, already resolved,
        adopt without a broker id, missing evidence) comes back as an ephemeral
        message. A stuck order is already an incident; a traceback in the
        channel does not help it.
        """
        if not is_authorized(self.config, interaction.user.id):
            logger.warning("Unauthorized /resolve from user %s", interaction.user.id)
            await interaction.response.send_message(
                "You are not authorized to resolve orders.", ephemeral=True
            )
            return

        # Report mode. An order_id WITHOUT a resolution also lands here on
        # purpose: half a write must never fall through to a default action.
        if resolution is None:
            await interaction.response.defer()
            try:
                report = await asyncio.to_thread(
                    report_unknown_submissions, self.config
                )
            except Exception as exc:
                logger.exception("/resolve report failed")
                await interaction.followup.send(
                    f"Could not build the resolution report: {exc}\n"
                    "Nothing was resolved. Capital stays reserved."
                )
                return
            for chunk in _chunk_message(report):
                await interaction.followup.send(chunk)
            return

        if order_id is None:
            await interaction.response.send_message(
                "A resolution needs `order_id`. Run `/resolve` with no arguments "
                "to see which orders are unresolved.", ephemeral=True
            )
            return

        actor = f"discord:{interaction.user.id}"

        def _write():
            with immediate_transaction(self.config.db_path) as conn:
                return queries.resolve_order_manually(
                    conn, order_id, resolution, actor=actor, evidence=evidence,
                    broker_order_id=broker_order_id,
                )

        try:
            await asyncio.to_thread(_write)
        except ValueError as exc:
            await interaction.response.send_message(f"Refused: {exc}", ephemeral=True)
            return
        except Exception as exc:
            logger.exception("/resolve write failed for order %s", order_id)
            await interaction.response.send_message(
                f"Could not resolve order {order_id}: {exc}", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Order #{order_id} resolved `{resolution}` by <@{interaction.user.id}>.\n"
            f"Evidence: {evidence}"
        )

    async def _deliver_ops_alert(self, alert_id: int, message: str) -> bool:
        """Attempt delivery of one already-persisted alert. True if it landed.

        Failure is recorded against the row instead of being swallowed, so the
        alert stays in the outbox for drain_ops_alerts to retry.
        """
        try:
            channel = await self._resolve_channel()
            await channel.send(f"[OPS ALERT] {message}")
        except Exception as exc:
            logger.error("Failed to send ops alert: %s", exc)
            await asyncio.to_thread(
                _record_alert_failure,
                self.config.db_path,
                alert_id,
                f"{type(exc).__name__}: {exc}",
            )
            return False
        await asyncio.to_thread(
            _mark_alert_delivered, self.config.db_path, alert_id
        )
        return True

    async def send_ops_alert(self, message: str) -> None:
        """Persist an operational alert, then try to deliver it to Discord.

        The persist-then-send ordering is the safety property. Several states
        are only safe because an operator finds out about them — stuck orders,
        unresolved submissions, reconciliation failures — and this method used
        to catch every delivery error and merely log it, which made a Discord
        outage indistinguishable from a quiet system. Now a failed send leaves
        a durable row behind.

        Still never raises: run_scan posts alerts from inside its per-ticker
        loop, so a raising alert would abort the scan it was reporting on.
        """
        alert_id = await asyncio.to_thread(
            _enqueue_alert, self.config.db_path, message
        )
        await self._deliver_ops_alert(alert_id, message)

    async def drain_ops_alerts(self, limit: int = 20) -> int:
        """Retry undelivered alerts, oldest first. Returns how many landed.

        Stops at the first alert that still fails: Discord being unreachable is
        not a per-alert condition, so continuing would burn attempts on the
        whole backlog and deliver it out of order once it recovered.
        """
        pending = await asyncio.to_thread(
            _pending_alerts, self.config.db_path, limit
        )
        delivered = 0
        for row in pending:
            if not await self._deliver_ops_alert(row["id"], row["message"]):
                break
            delivered += 1
        return delivered
