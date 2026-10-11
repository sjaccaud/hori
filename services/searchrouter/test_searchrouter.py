"""Unit tests for the searchrouter FastAPI fallback chain — the ddgs
scrape-backend ordering added 2026-10-11 so OWUI web search survives
upstream engine rate-limiting."""
import asyncio
import pathlib
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import main as sr


def _run(coro):
    return asyncio.run(coro)


class _FakeDDGS:
    """ddgs.DDGS stub recording backends tried; returns per-backend results."""
    results = {}
    errors = {}
    tried = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def text(self, q, backend=None, max_results=None):
        type(self).tried.append(backend)
        if backend in type(self).errors:
            raise type(self).errors[backend]
        return type(self).results.get(backend, [])


def test_scrape_tries_backends_in_order_and_returns_first_nonempty():
    _FakeDDGS.tried = []
    _FakeDDGS.errors = {}
    _FakeDDGS.results = {
        "bing": [],
        "yahoo": [{"title": "T", "href": "u", "body": "b"}],
    }
    with patch.object(sr, "DDGS", _FakeDDGS):
        rs = _run(sr._search_duckduckgo("q", 5))
    assert _FakeDDGS.tried == ["bing", "yahoo"]
    assert rs and rs[0]["title"] == "T"


def test_scrape_survives_backend_errors():
    _FakeDDGS.tried = []
    _FakeDDGS.errors = {"bing": ConnectionError("reset")}
    _FakeDDGS.results = {"yahoo": [{"title": "T", "href": "u", "body": "b"}]}
    with patch.object(sr, "DDGS", _FakeDDGS):
        rs = _run(sr._search_duckduckgo("q", 5))
    assert rs and _FakeDDGS.tried == ["bing", "yahoo"]


def test_scrape_all_dead_returns_empty():
    _FakeDDGS.tried = []
    _FakeDDGS.errors = {b: ConnectionError() for b in sr._SCRAPE_BACKENDS}
    _FakeDDGS.results = {}
    with patch.object(sr, "DDGS", _FakeDDGS):
        rs = _run(sr._search_duckduckgo("q", 5))
    assert rs == [] and _FakeDDGS.tried == list(sr._SCRAPE_BACKENDS)


def test_search_response_shape_matches_searxng():
    """OWUI parses {results:[{url,title,content,...}]} like a searx reply."""
    items = [{"title": "T", "href": "http://x", "body": "snippet"}]
    out = sr._to_results("duckduckgo", items, 5)
    assert out[0]["url"] == "http://x" and out[0]["title"] == "T"
    assert "content" in out[0] and "score" in out[0]
