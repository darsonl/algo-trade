import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


@dataclass
class Config:
    schwab_app_key: str = os.getenv("SCHWAB_APP_KEY", "")
    schwab_app_secret: str = os.getenv("SCHWAB_APP_SECRET", "")
    schwab_callback_url: str = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    discord_token: str = os.getenv("DISCORD_TOKEN", "")
    discord_channel_id: int = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    min_dividend_yield: float = float(os.getenv("MIN_DIVIDEND_YIELD", "0.02"))
    max_pe_ratio: float = float(os.getenv("MAX_PE_RATIO", "25.0"))
    min_earnings_growth: float = float(os.getenv("MIN_EARNINGS_GROWTH", "0.05"))
    max_rsi: float = float(os.getenv("MAX_RSI", "70.0"))

    max_position_size_usd: float = float(os.getenv("MAX_POSITION_SIZE_USD", "500.0"))

    scan_hour: int = int(os.getenv("SCAN_HOUR", "9"))
    scan_minute: int = int(os.getenv("SCAN_MINUTE", "0"))

    db_path: str = os.getenv("DB_PATH", str(Path(__file__).parent / "algo_trade.db"))

    def validate(self):
        """Call this at startup (in main.py) to fail fast if credentials are missing."""
        if not self.schwab_app_key:
            raise ValueError("SCHWAB_APP_KEY is required in .env")
        if not self.schwab_app_secret:
            raise ValueError("SCHWAB_APP_SECRET is required in .env")
        if not self.discord_token:
            raise ValueError("DISCORD_TOKEN is required in .env")
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required in .env")
        if not self.discord_channel_id:
            raise ValueError("DISCORD_CHANNEL_ID is required in .env")


config = Config()
