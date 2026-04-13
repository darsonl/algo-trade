"""Tests for ETF-09: expense ratio flagging in Config and build_etf_recommendation_embed."""
import importlib
import os
import pytest

from config import Config
from discord_bot.embeds import build_etf_recommendation_embed


# ---------------------------------------------------------------------------
# Task 1: Config.etf_max_expense_ratio
# ---------------------------------------------------------------------------

def test_config_default():
    """Config.etf_max_expense_ratio defaults to 0.005 when env var is unset."""
    c = Config()
    assert c.etf_max_expense_ratio == 0.005


def test_config_env_override(monkeypatch):
    """ETF_MAX_EXPENSE_RATIO env var overrides the default."""
    monkeypatch.setenv("ETF_MAX_EXPENSE_RATIO", "0.01")
    import config as config_module
    reloaded = importlib.reload(config_module)
    assert reloaded.Config().etf_max_expense_ratio == 0.01
    # Restore module to default state after test
    importlib.reload(config_module)


# ---------------------------------------------------------------------------
# Task 2: build_etf_recommendation_embed — expense ratio flag logic
# ---------------------------------------------------------------------------

def _make_etf_embed(**kwargs):
    """Build a minimal ETF embed with sensible defaults, overridable via kwargs."""
    defaults = dict(
        ticker="SPY",
        signal="BUY",
        reasoning="test",
        price=100.0,
        rsi=50.0,
        ma50=98.0,
        expense_ratio=0.003,
        etf_max_expense_ratio=0.005,
    )
    defaults.update(kwargs)
    return build_etf_recommendation_embed(**defaults)


def _expense_field(embed):
    """Return the value of the Expense Ratio field."""
    return next(f.value for f in embed.fields if f.name == "Expense Ratio")


def test_flag_above_threshold():
    """expense_ratio > etf_max_expense_ratio => '⚠️ 0.0075 (High)'."""
    embed = _make_etf_embed(expense_ratio=0.0075, etf_max_expense_ratio=0.005)
    assert _expense_field(embed) == "⚠️ 0.0075 (High)"


def test_no_flag_below_threshold():
    """expense_ratio < etf_max_expense_ratio => plain format, no warning."""
    embed = _make_etf_embed(expense_ratio=0.003, etf_max_expense_ratio=0.005)
    assert _expense_field(embed) == "0.0030"


def test_no_flag_equal_threshold():
    """expense_ratio == etf_max_expense_ratio => plain format (strict > comparison)."""
    embed = _make_etf_embed(expense_ratio=0.005, etf_max_expense_ratio=0.005)
    assert _expense_field(embed) == "0.0050"


def test_no_flag_threshold_none():
    """etf_max_expense_ratio=None => no flagging regardless of expense_ratio (backward-compat)."""
    embed = _make_etf_embed(expense_ratio=0.0075, etf_max_expense_ratio=None)
    assert _expense_field(embed) == "0.0075"


def test_expense_ratio_none():
    """expense_ratio=None => 'N/A', no error, no flag."""
    embed = _make_etf_embed(expense_ratio=None, etf_max_expense_ratio=0.005)
    assert _expense_field(embed) == "N/A"


def test_default_no_param():
    """Calling without etf_max_expense_ratio => behaves as None case (backward-compat)."""
    embed = build_etf_recommendation_embed(
        ticker="QQQ",
        signal="BUY",
        reasoning="test",
        price=400.0,
        rsi=55.0,
        ma50=395.0,
        expense_ratio=0.002,
    )
    assert _expense_field(embed) == "0.0020"
