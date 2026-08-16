import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _parse_scan_times() -> list[str]:
    """Parse SCAN_TIMES env var (comma-separated HH:MM). Falls back to SCAN_HOUR:SCAN_MINUTE."""
    raw = os.getenv("SCAN_TIMES", "").strip()
    if raw:
        return [t.strip() for t in raw.split(",") if t.strip()]
    hour = int(os.getenv("SCAN_HOUR", "9"))
    minute = int(os.getenv("SCAN_MINUTE", "0"))
    return [f"{hour:02d}:{minute:02d}"]


def _parse_etf_scan_times() -> list[str]:
    """Parse ETF scan schedule from ETF_SCAN_HOUR:ETF_SCAN_MINUTE.

    Mirrors _parse_scan_times but does not support a multi-time env var yet
    (per Phase 12 D-02). Defaults to 09:30 (30-minute offset from stock scan).
    """
    hour = int(os.getenv("ETF_SCAN_HOUR", "9"))
    minute = int(os.getenv("ETF_SCAN_MINUTE", "30"))
    return [f"{hour:02d}:{minute:02d}"]


# Field helpers: each returns a dataclass field whose default is read from the
# environment at Config() CONSTRUCTION time, not at module import. Plain
# `= os.getenv(...)` defaults froze the values when config.py was first
# imported, so monkeypatch.setenv in tests (and any runtime env change) had no
# effect without an importlib.reload — that bit us twice; don't reintroduce it.

def _env_str(name: str, default: str = ""):
    return field(default_factory=lambda: os.getenv(name, default))


def _env_int(name: str, default: str):
    return field(default_factory=lambda: int(os.getenv(name, default)))


def _env_float(name: str, default: str):
    return field(default_factory=lambda: float(os.getenv(name, default)))


def _env_bool(name: str, default: str):
    return field(default_factory=lambda: os.getenv(name, default).lower() == "true")


_DEFAULT_DB_PATH = str(Path(__file__).parent / "algo_trade.db")


@dataclass
class Config:
    schwab_app_key: str = _env_str("SCHWAB_APP_KEY")
    schwab_app_secret: str = _env_str("SCHWAB_APP_SECRET")
    schwab_callback_url: str = _env_str("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
    schwab_account_hash: str = _env_str("SCHWAB_ACCOUNT_HASH")
    paper_trading: bool = _env_bool("PAPER_TRADING", "true")
    dry_run: bool = _env_bool("DRY_RUN", "true")

    discord_token: str = _env_str("DISCORD_TOKEN")
    discord_channel_id: int = _env_int("DISCORD_CHANNEL_ID", "0")
    # Comma-separated Discord user IDs permitted to run /halt and /resume.
    # Empty means nobody, on purpose: the opposite default would hand the kill
    # switch to every channel member the moment this went unconfigured.
    ops_user_ids: str = _env_str("OPS_USER_IDS", "")
    # Seeds the kill switch ONLY on a database that has never been written.
    # After that the persisted state wins, so this cannot silently undo an
    # operator's halt at the next restart. Defaults to false so a brand-new
    # deployment must be armed deliberately with /resume.
    trading_enabled: bool = _env_bool("TRADING_ENABLED", "false")
    # How far THROUGH the bid a marketable sell is priced. It bounds how bad
    # the fill may be, not whether it happens.
    approval_slippage_buffer_pct: float = _env_float("APPROVAL_SLIPPAGE_BUFFER_PCT", "0.5")
    # A quote older than this is refused rather than priced against. A stale
    # quote is more dangerous than a missing one because it looks usable.
    # Applies during regular hours only — after hours the last close is the only
    # quote there is, and the limit price becomes the binding control.
    quote_max_age_s: int = _env_int("QUOTE_MAX_AGE_S", "30")

    # --- approval preflight guard table (risk/preflight.py) ---
    # Who may approve a trade. Separate from ops_user_ids: halting is a safety
    # action anyone trusted should be able to take, spending money is not.
    # Empty means nobody, never everybody.
    allowed_discord_user_ids: str = _env_str("ALLOWED_DISCORD_USER_IDS", "")
    # 0 disables the guild check, for a bot that lives in exactly one server.
    discord_guild_id: int = _env_int("DISCORD_GUILD_ID", "0")
    # How far the market may have moved since the scan before approval is
    # refused. The analyst reasoned about the scan price; past this the
    # recommendation is about a different security than the one being bought.
    approval_price_tolerance_pct: float = _env_float("APPROVAL_PRICE_TOLERANCE_PCT", "2.0")
    # Ceiling on committed buy notional per TRADING SESSION — bucketed by
    # orders.intended_session_date, not by submission time. See market_time.
    max_daily_notional_usd: float = _env_float("MAX_DAILY_NOTIONAL_USD", "2000.0")

    # --- ambiguous submissions (/resolve, spec v4 step 12 / §11) ---
    # How long an unresolved order may sit before every scan starts nagging.
    # Guard 11 blocks new orders for its ticker the whole time, so the alert is
    # the only thing standing between an ambiguous submission and a symbol that
    # is blocked silently and indefinitely.
    stuck_approval_alert_h: int = _env_int("STUCK_APPROVAL_ALERT_H", "24")
    # How far back before our submission to search for candidate broker orders.
    # Erring wide is the safe direction: an extra candidate over-reserves, which
    # rejects a legitimate trade; a missed one under-reserves, which does not.
    resolve_lookback_min: int = _env_int("RESOLVE_LOOKBACK_MIN", "30")

    anthropic_api_key: str = _env_str("ANTHROPIC_API_KEY")

    analyst_provider: str = _env_str("ANALYST_PROVIDER", "claude")
    analyst_api_key: str = _env_str("ANALYST_API_KEY")
    analyst_model: str = _env_str("ANALYST_MODEL")
    analyst_fallback_provider: str = _env_str("ANALYST_FALLBACK_PROVIDER")
    analyst_fallback_api_key: str = _env_str("ANALYST_FALLBACK_API_KEY")
    analyst_fallback_model: str = _env_str("ANALYST_FALLBACK_MODEL")
    analyst_fallback2_provider: str = _env_str("ANALYST_FALLBACK2_PROVIDER")
    analyst_fallback2_api_key: str = _env_str("ANALYST_FALLBACK2_API_KEY")
    analyst_fallback2_model: str = _env_str("ANALYST_FALLBACK2_MODEL")

    min_dividend_yield: float = _env_float("MIN_DIVIDEND_YIELD", "0.02")
    max_pe_ratio: float = _env_float("MAX_PE_RATIO", "35.0")
    min_earnings_growth: float = _env_float("MIN_EARNINGS_GROWTH", "0.05")
    max_rsi: float = _env_float("MAX_RSI", "70.0")
    sell_rsi_threshold: float = _env_float("SELL_RSI_THRESHOLD", "70.0")
    analyst_daily_limit: int = _env_int("ANALYST_DAILY_LIMIT", "18")
    min_volume_ratio: float = _env_float("MIN_VOLUME_RATIO", "0.5")
    etf_max_expense_ratio: float = _env_float("ETF_MAX_EXPENSE_RATIO", "0.005")

    max_position_size_usd: float = _env_float("MAX_POSITION_SIZE_USD", "500.0")
    max_portfolio_usd: float = _env_float("MAX_PORTFOLIO_USD", "20000.0")

    scan_hour: int = _env_int("SCAN_HOUR", "9")
    scan_minute: int = _env_int("SCAN_MINUTE", "0")
    # IANA timezone for the scan schedule (e.g. "America/New_York" to stay
    # market-aligned across DST). Empty = machine-local time (legacy behavior).
    scan_timezone: str = _env_str("SCAN_TIMEZONE")
    scan_times: list = field(default_factory=_parse_scan_times)
    etf_scan_hour: int = _env_int("ETF_SCAN_HOUR", "9")
    etf_scan_minute: int = _env_int("ETF_SCAN_MINUTE", "30")
    etf_scan_times: list = field(default_factory=_parse_etf_scan_times)
    top_sp500_count: int = _env_int("TOP_SP500_COUNT", "10")
    analyst_call_delay_s: float = _env_float("ANALYST_CALL_DELAY_S", "12.0")

    alpha_vantage_api_key: str = _env_str("ALPHA_VANTAGE_API_KEY")

    db_path: str = _env_str("DB_PATH", _DEFAULT_DB_PATH)
    log_level: str = _env_str("LOG_LEVEL", "INFO")

    def validate(self):
        """Call this at startup (in main.py) to fail fast if credentials are missing."""
        if not self.schwab_app_key:
            raise ValueError("SCHWAB_APP_KEY is required in .env")
        if not self.schwab_app_secret:
            raise ValueError("SCHWAB_APP_SECRET is required in .env")
        if not self.discord_token:
            raise ValueError("DISCORD_TOKEN is required in .env")
        if self.analyst_provider == "claude":
            if not (self.analyst_api_key or self.anthropic_api_key):
                raise ValueError("ANTHROPIC_API_KEY (or ANALYST_API_KEY) is required when ANALYST_PROVIDER=claude")
        else:
            if not self.analyst_api_key:
                raise ValueError(f"ANALYST_API_KEY is required when ANALYST_PROVIDER={self.analyst_provider}")
        if not self.discord_channel_id:
            raise ValueError("DISCORD_CHANNEL_ID is required in .env")
        if not self.schwab_account_hash:
            raise ValueError("SCHWAB_ACCOUNT_HASH is required in .env")
