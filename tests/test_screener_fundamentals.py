import pytest
from screener.fundamentals import passes_fundamental_filter
from config import Config


@pytest.fixture
def cfg():
    c = Config()
    c.min_dividend_yield = 0.02
    c.max_pe_ratio = 25.0
    c.min_earnings_growth = 0.05
    return c


def make_info(pe=15.0, div_yield=0.03, earnings_growth=0.10):
    return {
        "trailingPE": pe,
        "dividendYield": div_yield,
        "earningsGrowth": earnings_growth,
    }


def test_passes_when_all_criteria_met(cfg):
    assert passes_fundamental_filter(make_info(), cfg) is True


def test_fails_when_pe_too_high(cfg):
    assert passes_fundamental_filter(make_info(pe=30.0), cfg) is False


def test_fails_when_dividend_yield_too_low(cfg):
    assert passes_fundamental_filter(make_info(div_yield=0.005), cfg) is False


def test_fails_when_earnings_growth_too_low(cfg):
    assert passes_fundamental_filter(make_info(earnings_growth=0.02), cfg) is False


def test_fails_when_pe_is_none(cfg):
    info = make_info()
    info["trailingPE"] = None
    assert passes_fundamental_filter(info, cfg) is False


def test_passes_when_dividend_yield_is_none(cfg):
    # dividendYield=None skips the yield check — non-dividend payers are allowed
    info = make_info()
    info["dividendYield"] = None
    assert passes_fundamental_filter(info, cfg) is True


def test_passes_when_earnings_growth_is_none(cfg):
    # earningsGrowth=None skips the growth check — analyst decides instead
    info = make_info()
    info["earningsGrowth"] = None
    assert passes_fundamental_filter(info, cfg) is True


def test_passes_at_exact_boundary(cfg):
    # Exactly at limits should pass
    assert passes_fundamental_filter(
        make_info(pe=25.0, div_yield=0.02, earnings_growth=0.05), cfg
    ) is True
