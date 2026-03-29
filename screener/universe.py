def get_watchlist(path: str) -> list[str]:
    """Read tickers from a watchlist file, one per line. Skips comments and blanks."""
    with open(path, "r") as f:
        lines = f.readlines()
    tickers = []
    for line in lines:
        ticker = line.strip()
        if ticker and not ticker.startswith("#"):
            tickers.append(ticker.upper())
    return tickers


def get_universe(watchlist_path: str, extra_tickers: list[str] | None = None) -> list[str]:
    """Combine watchlist with any extra tickers (e.g. S&P 500), deduplicated."""
    watchlist = get_watchlist(watchlist_path)
    extra = [t.upper() for t in (extra_tickers or [])]
    seen: set[str] = set()
    result: list[str] = []
    for ticker in watchlist + extra:
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result


def get_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 constituents from Wikipedia via pandas."""
    import pandas as pd
    table = pd.read_html(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", header=0
    )
    return table[0]["Symbol"].str.upper().tolist()
