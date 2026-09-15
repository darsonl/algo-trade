"""A stale Schwab login must become a clean refusal, never an interactive prompt.

`get_client` used schwab-py's `easy_client`. A token older than 6.5 days makes
that discard the token and run `client_from_login_flow(interactive=True)`, which
calls `input('Press ENTER...')`. The bot runs unattended, and the Approve button
fetches a quote EVEN IN DRY RUN -- so the first click after a week would crash on
EOF (not a QuoteUnavailable, so the guard table never adjudicated it) or hang
holding `approval_gate()`, queueing every later approval behind it. On
2026-09-13 the token was 155 days old.

The same investigation found the sink was never given a client at all: both
approval views call `_call_place_order(None, ...)`, the deleted `place_*`
wrappers used to build one, and every approval test patches the sink out. A
live approval would have raised AttributeError -> `submit_unknown`: nothing
sent, capital reserved, ticker blocked, and a message saying the order "may or
may not exist at Schwab".
"""
import json
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from risk import kill_switch
from risk.preflight import BrokerSnapshot
from schwab_client import auth, orders
from schwab_client.auth import SchwabLoginRequired
from schwab_client.quotes import Quote, QuoteUnavailable, fetch_quote

_NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)   # 11:00 ET Mon
DAY = 24 * 3600


def _token(tmp_path, age_s=None, raw=None):
    path = tmp_path / "schwab_token.json"
    if raw is not None:
        path.write_text(raw)
    elif age_s is not None:
        created = int(_NOW.timestamp() - age_s)
        path.write_text(json.dumps({"creation_timestamp": created,
                                    "token": {"refresh_token": "x"}}))
    return str(path)


def _cfg():
    c = MagicMock()
    c.schwab_app_key = "key"
    c.schwab_app_secret = "secret"
    return c


# --- get_client never logs in ---

def _no_login():
    """Any path into an interactive login fails the test loudly."""
    boom = AssertionError("the bot must never start a Schwab login")
    return (patch("schwab.auth.easy_client", side_effect=boom),
            patch("schwab.auth.client_from_login_flow", side_effect=boom),
            patch("schwab.auth.client_from_manual_flow", side_effect=boom))


def _get_client(path):
    patches = _no_login() + (
        patch.object(auth, "get_token_path", return_value=path),
        patch("schwab.auth.client_from_token_file", return_value="CLIENT"),
    )
    for p in patches:
        p.start()
    try:
        return auth.get_client(_cfg(), now=_NOW)
    finally:
        for p in patches:
            p.stop()


def test_a_fresh_token_loads_from_the_file(tmp_path):
    assert _get_client(_token(tmp_path, age_s=DAY)) == "CLIENT"


def test_a_token_past_easy_clients_cutoff_still_loads(tmp_path):
    """6.5 days is easy_client's PROACTIVE cutoff; the refresh token itself is
    good for 7. Refusing at 6.5 would throw away half a day of a working login."""
    assert _get_client(_token(tmp_path, age_s=6.8 * DAY)) == "CLIENT"


def test_an_expired_token_raises_instead_of_logging_in(tmp_path):
    with pytest.raises(SchwabLoginRequired, match="scripts/schwab_login.py"):
        _get_client(_token(tmp_path, age_s=155 * DAY))


def test_a_missing_token_raises_instead_of_logging_in(tmp_path):
    with pytest.raises(SchwabLoginRequired):
        _get_client(str(tmp_path / "nope.json"))


@pytest.mark.parametrize("raw", ["not json", "{}", '{"creation_timestamp": "soon"}'])
def test_an_unreadable_token_raises_instead_of_guessing(tmp_path, raw):
    """Its age is unknown, so whether it works is unknown. Fail closed with the
    instruction that fixes it, rather than let an HTTP call find out."""
    with pytest.raises(SchwabLoginRequired):
        _get_client(_token(tmp_path, raw=raw))


def test_no_production_code_can_start_a_login():
    """Structural, like the place_order sink test: pin the property rather than
    today's callers, so a NEW `easy_client` somewhere also fails. The login
    script is the one place a human is present to complete it."""
    # Parsed, not grepped: the modules that replaced easy_client explain in
    # their docstrings WHY it is gone, and a text match cannot tell prose from
    # a call. Names, attributes and imports are what can actually run.
    import ast
    forbidden = {"easy_client", "client_from_login_flow", "client_from_manual_flow"}
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        parts = path.parts
        if ".venv" in parts or "tests" in parts or any(p.startswith(".") for p in parts):
            continue
        if path.parent.name == "scripts" and path.name == "schwab_login.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            used = (node.attr if isinstance(node, ast.Attribute)
                    else node.id if isinstance(node, ast.Name)
                    else next((a.name for a in node.names if a.name in forbidden), None)
                    if isinstance(node, ast.ImportFrom) else None)
            if used in forbidden:
                offenders.append(f"{path.relative_to(root)}:{node.lineno}: {used}")
    assert offenders == []


def test_the_structural_check_would_catch_a_real_call(tmp_path):
    """Guard the guard: the AST walk must flag a call, or the test above passes
    vacuously -- which a docstring-tolerant rewrite could easily make it do."""
    import ast
    src = "import schwab.auth as a\nc = a.easy_client('k', 's', 'u', 'p')\n"
    attrs = {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
    assert "easy_client" in attrs


# --- the warning ---

def test_no_warning_for_a_fresh_token(tmp_path):
    assert auth.schwab_login_warning(_token(tmp_path, age_s=DAY), now=_NOW) is None


def test_a_token_in_its_last_day_warns_with_the_time_left(tmp_path):
    msg = auth.schwab_login_warning(_token(tmp_path, age_s=6.5 * DAY), now=_NOW)
    assert "12h" in msg
    assert "scripts/schwab_login.py" in msg


def test_an_expired_token_warns_that_approvals_are_refused(tmp_path):
    msg = auth.schwab_login_warning(_token(tmp_path, age_s=8 * DAY), now=_NOW)
    assert "expired" in msg.lower()
    assert "approve" in msg.lower()


def test_a_missing_token_warns(tmp_path):
    assert auth.schwab_login_warning(str(tmp_path / "nope.json"), now=_NOW)


# --- the quote: a login failure is a missing quote ---

def test_a_required_login_is_quote_unavailable():
    """Approve catches QuoteUnavailable and lets guard 4 refuse. Anything else
    escaped the handler and left the Discord interaction hanging."""
    with patch("schwab_client.auth.get_client",
               side_effect=SchwabLoginRequired("Schwab login expired")):
        with pytest.raises(QuoteUnavailable, match="login expired"):
            fetch_quote("AAPL", MagicMock())


def test_a_token_revoked_mid_life_is_quote_unavailable():
    """A token can be revoked before its 7 days are up; authlib then raises
    from inside the request, which the age check cannot see coming."""
    client = MagicMock()
    client.get_quote.side_effect = Exception(
        "invalid_grant: Refresh token is invalid, expired or revoked")
    with pytest.raises(QuoteUnavailable, match="revoked"):
        fetch_quote("AAPL", MagicMock(), client=client)


# --- the sink builds its own client, AFTER the checks that may refuse ---

def _live(db_path):
    c = MagicMock()
    c.execution_mode = "live"
    c.dry_run = False
    c.db_path = db_path
    c.schwab_account_hash = "hash"
    return c


@pytest.fixture
def db_path(tmp_path):
    from database.models import initialize_db
    path = str(tmp_path / "t.db")
    initialize_db(path)
    return path


SPEC = {"orderType": "LIMIT", "price": "100.00"}


def test_the_sink_builds_a_client_when_given_none(db_path):
    kill_switch.init(db_path, env_default=True)
    client = MagicMock()
    with patch("schwab_client.auth.get_client", return_value=client):
        orders._call_place_order(None, _live(db_path), SPEC)
    client.place_order.assert_called_once_with("hash", SPEC)


def test_a_halted_sink_does_no_auth_work(db_path):
    """The mode and the switch refuse first. A halted bot should not touch the
    token at all -- let alone raise a login error in place of 'halted'."""
    kill_switch.init(db_path, env_default=False)
    with patch("schwab_client.auth.get_client") as get_client:
        with pytest.raises(kill_switch.TradingHalted):
            orders._call_place_order(None, _live(db_path), SPEC)
    get_client.assert_not_called()


def test_a_required_login_at_the_sink_raises_before_dispatch(db_path):
    kill_switch.init(db_path, env_default=True)
    with patch("schwab_client.auth.get_client",
               side_effect=SchwabLoginRequired("expired")):
        with pytest.raises(SchwabLoginRequired):
            orders._call_place_order(None, _live(db_path), SPEC)


# --- the approval: a login failure at submission is a definitive non-submission ---

def _config(db_path):
    from config import Config
    c = Config()
    c.db_path = db_path
    c.dry_run = False
    c.execution_mode = "live"
    c.allowed_discord_user_ids = "1001"
    c.discord_guild_id = 0
    c.discord_channel_id = 0
    c.max_daily_notional_usd = 20000.0
    c.approval_price_tolerance_pct = 2.0
    c.approval_slippage_buffer_pct = 0.5
    c.max_position_size_usd = 500.0
    c.max_portfolio_usd = 20000.0
    c.schwab_account_hash = "hash"
    return c


def _interaction():
    i = MagicMock()
    i.user.id = 1001
    i.guild_id = 0
    i.channel_id = 0
    i.response.send_message = AsyncMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    return i


def _recommendation(db_path):
    from database.queries import create_recommendation
    rec_id = create_recommendation(db_path, "AAPL", "BUY", "t", 100.0, None, None)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE recommendations SET expires_at = ? WHERE id = ?",
                     ((_NOW + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"), rec_id))
    return rec_id


def _order(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT status, broker_order_id FROM orders").fetchone()


async def _approve_buy(db_path, get_client):
    from discord_bot.bot import ApproveRejectView
    kill_switch.init(db_path, env_default=True)
    rec_id = _recommendation(db_path)
    view = ApproveRejectView(rec_id, "AAPL", 100.0, _config(db_path))
    interaction = _interaction()
    quote = Quote(symbol="AAPL", bid=99.5, ask=100.0, last=100.0,
                  quote_time=_NOW - timedelta(seconds=1))
    patches = (
        patch("discord_bot.bot.fetch_quote", return_value=quote),
        patch("discord_bot.bot.collect_broker_snapshot",
              return_value=BrokerSnapshot([], [])),
        patch("discord_bot.bot._utcnow", return_value=_NOW),
        patch("schwab_client.auth.get_client", get_client),
    )
    for p in patches:
        p.start()
    try:
        await view.approve.callback.callback(view, interaction, MagicMock())
    finally:
        for p in patches:
            p.stop()
    sent = " ".join(str(c.args[0]) for c in interaction.followup.send.call_args_list if c.args)
    return rec_id, sent


@pytest.mark.asyncio
async def test_a_live_buy_actually_reaches_the_broker(db_path):
    """The end-to-end check no approval test made: the REAL sink, given None,
    dispatches. Before the fix this recorded submit_unknown."""
    client = MagicMock()
    client.place_order.return_value = MagicMock(
        status_code=201, headers={"Location": "https://x/orders/oid-7"})
    await _approve_buy(db_path, MagicMock(return_value=client))
    client.place_order.assert_called_once()
    assert dict(_order(db_path)) == {"status": "submitted", "broker_order_id": "oid-7"}


@pytest.mark.asyncio
async def test_a_login_failure_at_submission_releases_and_says_so(db_path):
    """Nothing was dispatched -- the client could not even be built -- so this
    is the TradingHalted case, not the ambiguous one: release the reservation,
    reopen the recommendation, and do not send the operator to check Schwab."""
    rec_id, sent = await _approve_buy(
        db_path, MagicMock(side_effect=SchwabLoginRequired("Schwab login expired")))
    assert _order(db_path)["status"] == "submit_failed"
    with sqlite3.connect(db_path) as conn:
        status = conn.execute("SELECT status FROM recommendations WHERE id=?",
                              (rec_id,)).fetchone()[0]
    assert status == "pending"
    assert "login" in sent.lower()
    assert "may or may not" not in sent.lower()


# --- the scan alerts, on both paths ---

def _scan_patches(warning, error=None):
    import main
    login = ({"side_effect": error} if error else {"return_value": warning})
    return (
        patch.object(main, "schwab_login_warning", **login),
        patch.object(main, "_drain_ops_outbox", new=AsyncMock()),
        patch.object(main, "alert_stuck_orders", new=AsyncMock()),
        patch.object(main, "sweep_terminal_recommendations", new=AsyncMock()),
        patch.object(main.outcomes, "mark_due_outcomes", new=AsyncMock(return_value=0)),
        patch.object(main, "get_top_sp500_by_fundamentals", return_value=[]),
        patch.object(main, "get_universe", return_value=[]),
        patch.object(main, "partition_watchlist", side_effect=lambda t, i=None: ([], [])),
        patch.object(main, "fetch_macro_context", return_value={}),
    )


async def _scan(kind, warning, tmp_path, error=None):
    import main
    from config import Config
    from database.models import initialize_db
    cfg = Config()
    cfg.db_path = str(tmp_path / "s.db")
    cfg.dry_run = True
    initialize_db(cfg.db_path)
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    patches = _scan_patches(warning, error)
    for p in patches:
        p.start()
    try:
        await (main.run_scan if kind == "stock" else main.run_scan_etf)(bot, cfg)
    finally:
        for p in patches:
            p.stop()
    return [c.args[0] for c in bot.send_ops_alert.call_args_list]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stock", "etf"])
async def test_both_scans_post_the_login_warning(kind, tmp_path):
    """Repeated every scan, like the stuck-order alert: a warning nobody
    repeats is an expiry nobody sees."""
    alerts = await _scan(kind, "Schwab login expires in 12h", tmp_path)
    assert "Schwab login expires in 12h" in alerts


@pytest.mark.asyncio
async def test_a_fresh_login_posts_nothing(tmp_path):
    alerts = await _scan("stock", None, tmp_path)
    assert not any("Schwab login" in a for a in alerts)


@pytest.mark.asyncio
async def test_a_failing_login_check_does_not_abort_the_scan(tmp_path):
    alerts = await _scan("stock", None, tmp_path, error=RuntimeError("boom"))
    assert "Scan complete: 0 recommendations posted." in alerts


# --- when is the NEXT scheduled scan? (the warning's deadline) ---
#
# The warning is delivered BY A SCAN, and scans only run on trading sessions.
# Asking "is the token old?" therefore answers the wrong question: a token that
# expires on a Sunday is never old on a day anything is running to say so. The
# question that matters is "will this token outlive the next scan?".

def _sched(stock=("09:35",), etf=("10:00",), tz="America/New_York"):
    from config import Config
    c = Config()
    c.scan_times = list(stock)
    c.etf_scan_times = list(etf)
    c.scan_timezone = tz
    return c


def test_the_next_scan_is_the_earliest_time_after_the_instant():
    """2026-09-16 is a Wednesday. 09:35 ET == 13:35 UTC, 10:00 ET == 14:00 UTC."""
    import main
    after_stock = datetime(2026, 9, 16, 13, 35, tzinfo=timezone.utc)
    assert main.next_scheduled_scan_utc(_sched(), after_stock) == \
        datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)


def test_the_next_scan_after_fridays_last_skips_the_weekend():
    """THE bug. Fails if the walk steps one calendar day: Saturday has no scan,
    so a token dying on Sunday is never announced by anything.

    `main.is_trading_session` is restored to the real calendar because
    conftest's autouse fixture pins it True -- which would make a naive
    implementation pass by answering Saturday.
    """
    import main
    import market_time
    friday_etf = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    with patch("main.is_trading_session", market_time.is_trading_session):
        assert main.next_scheduled_scan_utc(_sched(), friday_etf) == \
            datetime(2026, 9, 21, 13, 35, tzinfo=timezone.utc)


def test_the_next_scan_skips_a_market_holiday_that_is_not_a_federal_one():
    """Good Friday 2026-04-03 is closed but is a weekday, so a `mon-fri` rule
    answers it. The next scan after Thursday's last is Monday 2026-04-06."""
    import main
    import market_time
    thursday_etf = datetime(2026, 4, 2, 14, 0, tzinfo=timezone.utc)
    with patch("main.is_trading_session", market_time.is_trading_session):
        assert main.next_scheduled_scan_utc(_sched(), thursday_etf) == \
            datetime(2026, 4, 6, 13, 35, tzinfo=timezone.utc)


def test_an_unreadable_calendar_means_the_next_scan_is_unknown_not_a_crash():
    """The deadline is a nice-to-have; the warning is not. A calendar outage
    must degrade to "unknown", so the caller can fall back on token age --
    never propagate into `alert_schwab_login` and swallow an EXPIRED alert."""
    import main
    with patch("main.is_trading_session", side_effect=RuntimeError("calendar down")):
        assert main.next_scheduled_scan_utc(_sched(), _NOW) is None


def test_no_scheduled_scans_means_no_next_scan():
    import main
    assert main.next_scheduled_scan_utc(_sched(stock=(), etf=()), _NOW) is None


def test_the_next_scan_is_a_market_time_across_dst():
    """09:35 ET is 13:35 UTC in September and 14:35 UTC in December. Fails if
    the walk pins a fixed UTC offset."""
    import main
    winter = datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc)   # Tue after the close
    assert main.next_scheduled_scan_utc(_sched(), winter) == \
        datetime(2026, 12, 2, 14, 35, tzinfo=timezone.utc)


# --- the warning's deadline is the next scan, not the token's age ---
#
# Oracle: the token live on this machine at the time of writing. Created
# 2026-09-13 14:42 UTC, so it expires SUNDAY 2026-09-20 14:42 UTC. Under the
# >=6-day rule the warn window opened Saturday 09-19 14:42 and the first scan
# that could read it was Monday 09-21 09:35 ET -- by which point the token was
# already dead. The operator got NO warning at all, which is what this fixes.

_LIVE_CREATED = datetime(2026, 9, 13, 14, 42, tzinfo=timezone.utc)


def _token_created(tmp_path, created):
    path = tmp_path / "schwab_token.json"
    path.write_text(json.dumps({"creation_timestamp": int(created.timestamp()),
                                "token": {"refresh_token": "x"}}))
    return str(path)


def test_a_token_that_dies_before_the_next_scan_warns_on_this_one(tmp_path):
    """Friday's last scan is the last chance to say anything. Fails under the
    >=6-day rule: the token is only 4.97 days old here, so nothing warns."""
    import main
    friday_etf = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    with patch("main.is_trading_session", __import__("market_time").is_trading_session):
        next_scan = main.next_scheduled_scan_utc(_sched(), friday_etf)
    msg = auth.schwab_login_warning(_token_created(tmp_path, _LIVE_CREATED),
                                    now=friday_etf, next_scan=next_scan)
    assert msg is not None
    assert "2026-09-20 14:42" in msg          # when it dies
    assert "2026-09-21 13:35" in msg          # the scan it will not reach
    assert "scripts/schwab_login.py" in msg


def test_a_token_that_outlives_the_next_scan_is_not_warned_about(tmp_path):
    """Today's scan is not the last chance, so it stays quiet. Without this the
    rule degenerates to warning on every scan, which is a warning nobody reads."""
    import main
    wednesday_stock = datetime(2026, 9, 16, 13, 35, tzinfo=timezone.utc)
    next_scan = main.next_scheduled_scan_utc(_sched(), wednesday_stock)
    assert auth.schwab_login_warning(_token_created(tmp_path, _LIVE_CREATED),
                                     now=wednesday_stock, next_scan=next_scan) is None


def test_the_next_scan_rule_never_silences_the_age_warning(tmp_path):
    """The two rules are a UNION, so the new one can only ADD warnings. A token
    in its last day still warns even though it survives the next scan an hour
    from now -- fails if the age floor was replaced rather than joined."""
    soon = _NOW + timedelta(hours=1)
    msg = auth.schwab_login_warning(_token(tmp_path, age_s=6.5 * DAY),
                                    now=_NOW, next_scan=soon)
    assert msg is not None and "12h" in msg


def test_an_unknown_next_scan_falls_back_to_the_age_rule(tmp_path):
    """A calendar outage leaves next_scan None. Degrading to today's behaviour
    is the fail-safe direction: an extra warning costs nothing, a missed one
    costs the Monday scan."""
    assert auth.schwab_login_warning(_token(tmp_path, age_s=6.5 * DAY),
                                     now=_NOW, next_scan=None) is not None


@pytest.mark.asyncio
async def test_the_scan_alert_measures_the_token_against_the_next_scan(tmp_path):
    """End to end through the REAL warning and the REAL calendar, with only the
    token file and the clock pinned: Friday's scan must announce a token that
    dies on Sunday. Fails if `alert_schwab_login` still asks only about age.

    conftest pins `main.schwab_login_warning` to None for every other test in
    the suite, so this restores the real one -- the same override the calendar
    fixture documents.
    """
    import main
    import market_time
    bot = MagicMock()
    bot.send_ops_alert = AsyncMock()
    friday_etf = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    with patch("main.schwab_login_warning", auth.schwab_login_warning), \
         patch("schwab_client.auth.get_token_path",
               return_value=_token_created(tmp_path, _LIVE_CREATED)), \
         patch("main.is_trading_session", market_time.is_trading_session):
        await main.alert_schwab_login(bot, _sched(), now=friday_etf)
    bot.send_ops_alert.assert_awaited_once()
    assert "2026-09-21 13:35" in bot.send_ops_alert.await_args[0][0]
