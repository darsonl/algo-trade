import pytest
from discord_bot.bot import compute_share_quantity


def test_exact_division_gives_whole_shares():
    assert compute_share_quantity(price=100.0, max_position_usd=500.0) == 5


def test_floors_to_whole_shares():
    # 500 / 333 = 1.5 → floor to 1
    assert compute_share_quantity(price=333.0, max_position_usd=500.0) == 1


def test_returns_zero_when_price_exceeds_max():
    assert compute_share_quantity(price=600.0, max_position_usd=500.0) == 0


def test_returns_zero_for_zero_price():
    # Guard against division by zero — treat as unaffordable
    assert compute_share_quantity(price=0.0, max_position_usd=500.0) == 0


def test_large_position_many_shares():
    assert compute_share_quantity(price=10.0, max_position_usd=1000.0) == 100


def test_fractional_share_scenario():
    # 500 / 199.99 = 2.5 → floor to 2
    assert compute_share_quantity(price=199.99, max_position_usd=500.0) == 2
