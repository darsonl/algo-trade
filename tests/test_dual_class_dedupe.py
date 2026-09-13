"""One ticker per company in the S&P ranking.

Dual-class shares have identical EPS and ROE, so they rank next to each other
and spent two of TOP_SP500_COUNT slots on one company -- two analyst calls and
potentially two recommendations for Alphabet on the 2026-09-14 scan.

Identity is the SEC CIK from the same Wikipedia table the universe already comes
from. Checked live on 2026-09-13: 503 rows give exactly three shared CIKs
(GOOGL/GOOG, FOXA/FOX, NWSA/NWS), and no two distinct CIKs share a name.
"""
import datetime as dt
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

import screener.universe as u
from screener.universe import one_per_company

# ─── The pure rule ───────────────────────────────────────────────────────────

IDS = {"GOOGL": "1652044", "GOOG": "1652044", "AAPL": "320193", "MSFT": "789019"}


def test_the_more_traded_class_is_kept_wherever_it_ranked():
    ranked = ["GOOG", "GOOGL", "AAPL"]  # GOOG ranked first (EPS 19.93 vs 19.94 can flip)
    volumes = {"GOOG": 19.8e6, "GOOGL": 29.9e6, "AAPL": 50e6}
    assert one_per_company(ranked, IDS, volumes) == ["GOOGL", "AAPL"]


def test_missing_volume_loses_to_a_known_one():
    volumes = {"GOOG": 1.0, "GOOGL": None}
    assert one_per_company(["GOOGL", "GOOG"], IDS, volumes) == ["GOOG"]


def test_without_any_volume_the_higher_ranked_class_is_kept():
    assert one_per_company(["GOOG", "GOOGL"], IDS, {}) == ["GOOG"]


def test_tickers_without_a_cik_are_never_merged():
    # A watchlist-style or unknown ticker has no CIK; merging on "no id" would
    # collapse every such ticker into one.
    assert one_per_company(["X", "Y", "AAPL"], IDS, {}) == ["X", "Y", "AAPL"]


def test_order_is_otherwise_preserved():
    ranked = ["AAPL", "GOOG", "MSFT", "GOOGL"]
    volumes = {"GOOG": 2.0, "GOOGL": 1.0}
    assert one_per_company(ranked, IDS, volumes) == ["AAPL", "GOOG", "MSFT"]


# ─── Wiring into the ranking ────────────────────────────────────────────────

def _rank(monkeypatch, data, company_ids, count):
    def mock_ticker(t):
        m = MagicMock()
        m.info = data[t]
        return m

    saved = {}
    u._top_sp500_cache = {}
    monkeypatch.setattr(u, "_load_top_cache", lambda: None)
    monkeypatch.setattr(u, "_save_top_cache", lambda ranked, deduped: saved.update(ranked=ranked, deduped=deduped))
    monkeypatch.setattr(u, "get_sp500_tickers", lambda: list(data))
    monkeypatch.setattr(u, "get_sp500_company_ids", lambda: company_ids)
    monkeypatch.setattr(u.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(u.yf, "Ticker", mock_ticker)
    return u.get_top_sp500_by_fundamentals(SimpleNamespace(top_sp500_count=count)), saved


DATA = {
    "GOOGL": {"trailingEps": 19.94, "returnOnEquity": 0.49, "averageVolume": 29.9e6},
    "GOOG":  {"trailingEps": 19.93, "returnOnEquity": 0.49, "averageVolume": 19.8e6},
    "AAPL":  {"trailingEps": 7.0,   "returnOnEquity": 0.30, "averageVolume": 50e6},
    "MSFT":  {"trailingEps": 6.0,   "returnOnEquity": 0.20, "averageVolume": 20e6},
}


def test_the_freed_slot_goes_to_the_next_company(monkeypatch):
    top, saved = _rank(monkeypatch, DATA, IDS, count=3)
    assert top == ["GOOGL", "AAPL", "MSFT"]
    assert saved == {"ranked": ["GOOGL", "AAPL", "MSFT"], "deduped": True}


def test_no_company_ids_ranks_without_dedupe_and_says_so(monkeypatch, caplog):
    # Fail OPEN: a duplicate costs one analyst call; a failed universe costs the scan.
    top, saved = _rank(monkeypatch, DATA, {}, count=3)
    assert top == ["GOOGL", "GOOG", "AAPL"]
    assert saved["deduped"] is False
    assert "share classes" in caplog.text


# ─── Caches ─────────────────────────────────────────────────────────────────

@pytest.fixture
def top_cache(monkeypatch, tmp_path):
    path = tmp_path / "sp500_top_cache.json"
    monkeypatch.setattr(u, "_TOP_CACHE_PATH", path)
    return path


def test_a_top_cache_from_before_dedupe_is_rebuilt(top_cache):
    # The live cache on 2026-09-13 holds both GOOGL and GOOG in its top 50.
    top_cache.write_text(json.dumps({"fetched_at": dt.datetime.now().isoformat(),
                                     "ranked": ["GOOGL", "GOOG"]}), encoding="utf-8")
    assert u._load_top_cache() is None


def test_a_top_cache_built_without_dedupe_is_not_trusted_after_restart(top_cache):
    u._save_top_cache(["GOOGL", "GOOG"], deduped=False)
    assert u._load_top_cache() is None


def test_a_deduped_top_cache_is_served(top_cache):
    u._save_top_cache(["GOOGL", "AAPL"], deduped=True)
    assert u._load_top_cache()["ranked"] == ["GOOGL", "AAPL"]


@pytest.fixture
def sp500_cache(monkeypatch, tmp_path):
    path = tmp_path / "sp500_cache.json"
    monkeypatch.setattr(u, "_CACHE_PATH", path)
    return path


def _fresh(path, **payload):
    path.write_text(json.dumps({"fetched_at": dt.datetime.now().isoformat(), **payload}), encoding="utf-8")


def test_company_ids_come_from_a_fresh_cache_without_a_fetch(sp500_cache, monkeypatch):
    _fresh(sp500_cache, tickers=["GOOGL", "GOOG"], cik={"GOOGL": "1652044", "GOOG": "1652044"})
    monkeypatch.setattr(u, "_fetch_sp500_from_wikipedia",
                        lambda: (_ for _ in ()).throw(AssertionError("must not fetch")))
    assert u.get_sp500_company_ids() == {"GOOGL": "1652044", "GOOG": "1652044"}


def test_a_ticker_cache_from_before_ciks_triggers_one_fetch(sp500_cache, monkeypatch):
    _fresh(sp500_cache, tickers=["GOOGL", "GOOG"])
    monkeypatch.setattr(u, "_fetch_sp500_from_wikipedia",
                        lambda: (["GOOGL", "GOOG"], {"GOOGL": "1652044", "GOOG": "1652044"}))
    assert u.get_sp500_company_ids()["GOOG"] == "1652044"
    assert json.loads(sp500_cache.read_text())["cik"]["GOOGL"] == "1652044"


def test_a_failed_fetch_yields_no_ids_rather_than_raising(sp500_cache, monkeypatch):
    def boom():
        raise OSError("wikipedia down")
    monkeypatch.setattr(u, "_fetch_sp500_from_wikipedia", boom)
    assert u.get_sp500_company_ids() == {}


# ─── Parsing the Wikipedia table ────────────────────────────────────────────

def test_table_parsing_normalises_cik_and_tolerates_a_missing_column():
    table = pd.DataFrame({"Symbol": ["googl", "GOOG", "BRK.B"], "CIK": [1652044, "0001652044", None]})
    tickers, cik = u._parse_sp500_table(table)
    assert tickers == ["GOOGL", "GOOG", "BRK.B"]
    assert cik == {"GOOGL": "1652044", "GOOG": "1652044"}  # leading zeros and NaN handled

    tickers, cik = u._parse_sp500_table(pd.DataFrame({"Symbol": ["AAPL"]}))
    assert tickers == ["AAPL"] and cik == {}
