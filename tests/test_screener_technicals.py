import pandas as pd
import pytest
from screener.technicals import compute_rsi, passes_technical_filter
from config import Config


@pytest.fixture
def cfg():
    c = Config()
    c.max_rsi = 70.0
    return c


def _make_rising_prices(n=30, start=100.0, step=1.0):
    """Generates a steadily rising price series — RSI will be near 100."""
    return pd.Series([start + i * step for i in range(n)])


def _make_falling_prices(n=30, start=130.0, step=1.0):
    """Generates a steadily falling price series — RSI will be near 0."""
    return pd.Series([start - i * step for i in range(n)])


def _make_flat_prices(n=30, value=100.0):
    return pd.Series([value] * n)


# --- RSI unit tests ---

def test_rsi_overbought_on_rising_prices():
    prices = _make_rising_prices()
    rsi = compute_rsi(prices)
    assert rsi > 70


def test_rsi_oversold_on_falling_prices():
    prices = _make_falling_prices()
    rsi = compute_rsi(prices)
    assert rsi < 30


def test_rsi_near_50_on_alternating_prices():
    # Alternating up/down gives RSI near 50
    prices = pd.Series([100 + (1 if i % 2 == 0 else -1) for i in range(30)])
    rsi = compute_rsi(prices)
    assert 40 < rsi < 60


def test_rsi_requires_at_least_period_plus_one_prices():
    with pytest.raises(ValueError):
        compute_rsi(pd.Series([100.0] * 5), period=14)


# --- passes_technical_filter unit tests ---

def make_ticker_data(rsi=55.0, price=110.0, ma50=100.0, volume=1_200_000, avg_volume=1_000_000):
    return {
        "rsi": rsi,
        "price": price,
        "ma50": ma50,
        "volume": volume,
        "avg_volume": avg_volume,
    }


def test_passes_when_all_technical_criteria_met(cfg):
    assert passes_technical_filter(make_ticker_data(), cfg) is True


def test_fails_when_rsi_overbought(cfg):
    assert passes_technical_filter(make_ticker_data(rsi=75.0), cfg) is False


def test_fails_when_price_below_ma50(cfg):
    assert passes_technical_filter(make_ticker_data(price=90.0, ma50=100.0), cfg) is False


def test_fails_when_volume_below_average(cfg):
    assert passes_technical_filter(
        make_ticker_data(volume=400_000, avg_volume=1_000_000), cfg
    ) is False


def test_passes_at_rsi_boundary(cfg):
    assert passes_technical_filter(make_ticker_data(rsi=70.0), cfg) is True


def test_fails_when_any_field_is_none(cfg):
    data = make_ticker_data()
    data["rsi"] = None
    assert passes_technical_filter(data, cfg) is False
