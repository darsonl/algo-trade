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


@dataclass
class Config:
    schwab_app_key: str = os.getenv("SCHWAB_APP_KEY", "")
    schwab_app_secret: str = os.getenv("SCHWAB_APP_SECRET", "")
    schwab_callback_url: str = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
    schwab_account_hash: str = os.getenv("SCHWAB_ACCOUNT_HASH", "")
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    discord_token: str = os.getenv("DISCORD_TOKEN", "")
    discord_channel_id: int = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    analyst_provider: str = os.getenv("ANALYST_PROVIDER", "claude")
    analyst_api_key: str = os.getenv("ANALYST_API_KEY", "")
    analyst_model: str = os.getenv("ANALYST_MODEL", "")
    analyst_fallback_provider: str = os.getenv("ANALYST_FALLBACK_PROVIDER", "")
    analyst_fallback_api_key: str = os.getenv("ANALYST_FALLBACK_API_KEY", "")
    analyst_fallback_model: str = os.getenv("ANALYST_FALLBACK_MODEL", "")

    min_dividend_yield: float = float(os.getenv("MIN_DIVIDEND_YIELD", "0.02"))
    max_pe_ratio: float = float(os.getenv("MAX_PE_RATIO", "35.0"))
    min_earnings_growth: float = float(os.getenv("MIN_EARNINGS_GROWTH", "0.05"))
    max_rsi: float = float(os.getenv("MAX_RSI", "70.0"))
    sell_rsi_threshold: float = float(os.getenv("SELL_RSI_THRESHOLD", "70.0"))
    analyst_daily_limit: int = int(os.getenv("ANALYST_DAILY_LIMIT", "18"))
    min_volume_ratio: float = float(os.getenv("MIN_VOLUME_RATIO", "0.5"))
    etf_max_expense_ratio: float = float(os.getenv("ETF_MAX_EXPENSE_RATIO", "0.005"))

    max_position_size_usd: float = float(os.getenv("MAX_POSITION_SIZE_USD", "500.0"))
    max_portfolio_usd: float = float(os.getenv("MAX_PORTFOLIO_USD", "20000.0"))

    scan_hour: int = int(os.getenv("SCAN_HOUR", "9"))
    scan_minute: int = int(os.getenv("SCAN_MINUTE", "0"))
    scan_times: list = field(default_factory=_parse_scan_times)
    etf_scan_hour: int = int(os.getenv("ETF_SCAN_HOUR", "9"))
    etf_scan_minute: int = int(os.getenv("ETF_SCAN_MINUTE", "30"))
    etf_scan_times: list = field(default_factory=_parse_etf_scan_times)
    top_sp500_count: int = int(os.getenv("TOP_SP500_COUNT", "10"))
    analyst_call_delay_s: float = float(os.getenv("ANALYST_CALL_DELAY_S", "12.0"))

    alpha_vantage_api_key: str = os.getenv("ALPHA_VANTAGE_API_KEY", "")

    db_path: str = os.getenv("DB_PATH", str(Path(__file__).parent / "algo_trade.db"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

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

