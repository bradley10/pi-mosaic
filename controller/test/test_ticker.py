"""Covers the Yahoo Finance poller behind the DNA and GOLD pages.

Gold in particular: it used to call two spot APIs that are both gone, and on
failure fell back to a hardcoded $2050 that was rendered as though it were a
real quote. It's now the front-month COMEX future on the same endpoint as the
stocks - see the source note in `ticker.py`."""

import pytest

from controller.programs.ticker import _ENTRIES, _YAHOO_SYMBOLS, Ticker


def chart_response(price: float, prev_close: float, closes: list):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "chartPreviousClose": prev_close,
                    },
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


class FakeSession:
    """Answers by Yahoo symbol, so a test can fail one symbol and not another."""

    def __init__(self, by_symbol):
        self._by_symbol = by_symbol
        self.requested = []

    def get(self, url, **kwargs):
        symbol = url.rsplit("/", 1)[-1]
        self.requested.append(symbol)
        return self._by_symbol[symbol]


@pytest.fixture
def ticker():
    return Ticker()


def test_gold_is_a_yahoo_symbol_and_has_a_page():
    assert _YAHOO_SYMBOLS["GOLD"] == "GC=F"
    assert any(entry["key"] == "GOLD" for entry in _ENTRIES)


def test_quotes_are_stored_under_the_page_key_not_the_yahoo_symbol(ticker):
    # The page looks up "GOLD"; storing under "GC=F" would leave it forever
    # on "Loading GOLD...".
    ticker._session = FakeSession(
        {
            "DNA": FakeResponse(chart_response(31.0, 30.0, [30.0, 31.0])),
            "GC=F": FakeResponse(chart_response(4325.6, 4407.3, [4400.0, 4325.6])),
        }
    )
    ticker._poll_yahoo()

    assert set(ticker._quotes) == {"DNA", "GOLD"}
    assert ticker._quotes["GOLD"]["price"] == pytest.approx(4325.6)


def test_direction_comes_from_the_previous_close(ticker):
    ticker._session = FakeSession(
        {
            "DNA": FakeResponse(chart_response(31.0, 30.0, [30.0, 31.0])),
            "GC=F": FakeResponse(chart_response(4325.6, 4407.3, [4400.0, 4325.6])),
        }
    )
    ticker._poll_yahoo()

    assert ticker._quotes["DNA"]["is_up"] is True
    # Gold below its previous close must show a down arrow - the old code
    # hardcoded is_up=True and could only ever point up.
    assert ticker._quotes["GOLD"]["is_up"] is False


def test_history_is_the_real_series_with_gaps_dropped(ticker):
    ticker._session = FakeSession(
        {
            "DNA": FakeResponse(chart_response(31.0, 30.0, [30.0, None, 31.0])),
            "GC=F": FakeResponse(
                chart_response(4325.6, 4407.3, [4400.0, None, 4380.0, 4325.6])
            ),
        }
    )
    ticker._poll_yahoo()

    # Nulls are holes in the session, not prices - they'd drag the sparkline
    # to the floor if they survived.
    assert ticker._quotes["GOLD"]["history"] == [4400.0, 4380.0, 4325.6]
    assert ticker._quotes["DNA"]["history"] == [30.0, 31.0]


def test_one_symbol_failing_does_not_stop_the_others(ticker):
    ticker._session = FakeSession(
        {
            "DNA": FakeResponse(error=RuntimeError("429 Too Many Requests")),
            "GC=F": FakeResponse(chart_response(4325.6, 4300.0, [4300.0, 4325.6])),
        }
    )
    ticker._poll_yahoo()

    assert "DNA" not in ticker._quotes
    assert ticker._quotes["GOLD"]["price"] == pytest.approx(4325.6)


def test_a_failed_poll_keeps_the_last_good_quote(ticker):
    good = FakeSession(
        {
            "DNA": FakeResponse(chart_response(31.0, 30.0, [30.0, 31.0])),
            "GC=F": FakeResponse(chart_response(4325.6, 4300.0, [4300.0, 4325.6])),
        }
    )
    ticker._session = good
    ticker._poll_yahoo()

    ticker._session = FakeSession(
        {
            "DNA": FakeResponse(error=RuntimeError("boom")),
            "GC=F": FakeResponse(error=RuntimeError("boom")),
        }
    )
    ticker._poll_yahoo()

    # A stale price beats dropping the page back to "Loading".
    assert ticker._quotes["GOLD"]["price"] == pytest.approx(4325.6)
    assert ticker._quotes["DNA"]["price"] == pytest.approx(31.0)


def test_no_fabricated_price_when_gold_has_never_been_fetched(ticker):
    ticker._session = FakeSession(
        {
            "DNA": FakeResponse(error=RuntimeError("boom")),
            "GC=F": FakeResponse(error=RuntimeError("boom")),
        }
    )
    ticker._poll_yahoo()

    # The old code invented $2050 here and drew it like a real quote; the page
    # should say "Loading GOLD..." instead.
    assert "GOLD" not in ticker._quotes
    assert ticker._render() is not None
