"""Unit tests for multi-source search (multi_search.py).

Tests cover:
- Source fetcher error handling (graceful degradation)
- URL deduplication
- Relevance scoring and sorting
- Result formatting for LLM
- Timeout handling

Traces to: Manifesto Pillar VII (Engineering Discipline — TDD),
UX-1.1 (voice assistant needs current information).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from services.aios_core.multi_search import (
    MultiSearchResult,
    multi_search,
    format_results_for_llm,
    _search_arxiv,
    _search_github,
    _search_reddit,
    _search_hackernews,
    _search_semantic_scholar,
    SOURCE_WEIGHTS,
    SOURCES,
)
from services.aios_core.web_search import SearchResult


def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _patch_sources(ddg=None, arxiv=None, github=None, reddit=None, hn=None, ss=None):
    """Patch the SOURCES dict with mock fetchers. Returns a context manager."""
    from contextlib import contextmanager
    import services.aios_core.multi_search as mod

    @contextmanager
    def ctx():
        original = dict(mod.SOURCES)
        if ddg is not None: mod.SOURCES["ddg"] = ddg
        if arxiv is not None: mod.SOURCES["arxiv"] = arxiv
        if github is not None: mod.SOURCES["github"] = github
        if reddit is not None: mod.SOURCES["reddit"] = reddit
        if hn is not None: mod.SOURCES["hackernews"] = hn
        if ss is not None: mod.SOURCES["semantic_scholar"] = ss
        try:
            yield
        finally:
            mod.SOURCES.clear()
            mod.SOURCES.update(original)
    return ctx()


class TestMultiSearchResult:
    """MultiSearchResult dataclass."""

    def test_creation(self):
        r = MultiSearchResult(title="Test", url="http://example.com", snippet="snip", source="ddg")
        assert r.title == "Test"
        assert r.source == "ddg"
        assert r.score == 0.0

    def test_with_score(self):
        r = MultiSearchResult(title="Test", url="http://example.com", snippet="snip", source="arxiv", score=3.5)
        assert r.score == 3.5


class TestSourceFetchers:
    """Each source fetcher must return [] on error, never raise."""

    def test_arxiv_returns_empty_on_error(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=Exception("network error"))
        results = _run(_search_arxiv("test query", client))
        assert results == []

    def test_github_returns_empty_on_error(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=Exception("network error"))
        results = _run(_search_github("test query", client))
        assert results == []

    def test_reddit_returns_empty_on_error(self):
        """Reddit search uses DDG under the hood — mock search_ddg to raise."""
        with patch("services.aios_core.multi_search.search_ddg", side_effect=Exception("network error")):
            results = _run(_search_reddit("test query", MagicMock()))
            assert results == []

    def test_hackernews_returns_empty_on_error(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=Exception("network error"))
        results = _run(_search_hackernews("test query", client))
        assert results == []

    def test_semantic_scholar_returns_empty_on_error(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=Exception("network error"))
        results = _run(_search_semantic_scholar("test query", client))
        assert results == []

    def test_arxiv_parses_valid_response(self):
        """arXiv returns Atom XML — verify parsing works."""
        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Efficient Attention Mechanisms for Large Language Models</title>
            <summary>We propose a novel attention mechanism that reduces memory usage.</summary>
            <id>http://arxiv.org/abs/2024.12345v1</id>
          </entry>
        </feed>"""
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = xml_response
        mock_resp.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=mock_resp)
        results = _run(_search_arxiv("attention mechanism", client))
        assert len(results) == 1
        assert "Efficient Attention" in results[0].title
        assert "arxiv.org" in results[0].url

    def test_github_parses_valid_response(self):
        """GitHub returns JSON — verify parsing works."""
        json_response = {
            "items": [
                {
                    "full_name": "openai/whisper",
                    "description": "Robust Speech Recognition via Large-Scale Weak Supervision",
                    "stargazers_count": 50000,
                    "html_url": "https://github.com/openai/whisper",
                }
            ]
        }
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_response
        mock_resp.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=mock_resp)
        results = _run(_search_github("whisper speech recognition", client))
        assert len(results) == 1
        assert "openai/whisper" in results[0].title
        assert "github.com" in results[0].url

    def test_reddit_parses_valid_response(self):
        """Reddit search uses DDG site:reddit.com — verify it passes through."""
        mock_results = [SearchResult(title="Best local LLM for coding?", url="https://reddit.com/r/LocalLLaMA/abc", snippet="r/LocalLLaMA")]
        with patch("services.aios_core.multi_search.search_ddg", return_value=mock_results):
            results = _run(_search_reddit("local LLM coding", MagicMock()))
            assert len(results) == 1
            assert "Best local LLM" in results[0].title
            assert "reddit.com" in results[0].url

    def test_hackernews_parses_valid_response(self):
        """HN/Algolia returns JSON — verify parsing works."""
        json_response = {
            "hits": [
                {
                    "title": "Show HN: I built a local-first AI assistant",
                    "points": 150,
                    "num_comments": 75,
                    "url": "https://example.com/blog/aios",
                    "objectID": "12345",
                }
            ]
        }
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_response
        mock_resp.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=mock_resp)
        results = _run(_search_hackernews("local AI assistant", client))
        assert len(results) == 1
        assert "Show HN" in results[0].title
        assert "150" in results[0].snippet

    def test_semantic_scholar_parses_valid_response(self):
        """Semantic Scholar returns JSON — verify parsing works."""
        json_response = {
            "data": [
                {
                    "title": "Attention Is All You Need",
                    "abstract": "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.",
                    "url": "https://www.semanticscholar.org/paper/123",
                    "citationCount": 90000,
                    "year": 2017,
                    "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
                }
            ]
        }
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_response
        mock_resp.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=mock_resp)
        results = _run(_search_semantic_scholar("attention transformer", client))
        assert len(results) == 1
        assert "Attention Is All You Need" in results[0].title
        assert "90000" in results[0].snippet


class TestSourceRegistry:
    """The source registry must have all 6 sources."""

    def test_all_sources_registered(self):
        assert set(SOURCES.keys()) == {
            "ddg", "arxiv", "github", "reddit", "hackernews", "semantic_scholar"
        }

    def test_all_sources_have_weights(self):
        for name in SOURCES:
            assert name in SOURCE_WEIGHTS, f"Source '{name}' missing weight"

    def test_academic_sources_weighted_higher(self):
        """arXiv and Semantic Scholar should have higher weight than Reddit."""
        assert SOURCE_WEIGHTS["arxiv"] > SOURCE_WEIGHTS["reddit"]
        assert SOURCE_WEIGHTS["semantic_scholar"] > SOURCE_WEIGHTS["reddit"]


class TestMultiSearchMerge:
    """The merge logic: dedup, score, sort."""

    def test_deduplication_by_url(self):
        """Same URL from two sources should only appear once."""
        dup_url = "https://example.com/same-article"
        async def mock_ddg(q, c): return [SearchResult(title="Same Article", url=dup_url, snippet="from DDG")]
        async def mock_reddit(q, c): return [SearchResult(title="Same Article", url=dup_url, snippet="from Reddit")]
        async def empty(q, c): return []
        with _patch_sources(ddg=mock_ddg, arxiv=empty, github=empty, reddit=mock_reddit, hn=empty, ss=empty):
            results = _run(multi_search("test query"))
            urls = [r.url for r in results]
            assert urls.count(dup_url) == 1, "Duplicate URL should appear only once"

    def test_results_sorted_by_score(self):
        """Results should be sorted by score descending."""
        async def mock_ddg(q, c): return [SearchResult(title="unrelated thing", url="http://a.com", snippet="no match here")]
        async def mock_arxiv(q, c): return [SearchResult(title="attention mechanism paper", url="http://b.com", snippet="attention mechanism research")]
        async def empty(q, c): return []
        with _patch_sources(ddg=mock_ddg, arxiv=mock_arxiv, github=empty, reddit=empty, hn=empty, ss=empty):
            results = _run(multi_search("attention mechanism"))
            assert len(results) >= 1
            # The arXiv result should score higher (title + snippet match + academic weight)
            assert results[0].source == "arxiv"
            assert results[0].score > 0

    def test_max_results_limit(self):
        """Should respect max_results parameter."""
        async def mock_ddg(q, c): return [SearchResult(title=f"Result {i}", url=f"http://ddg.com/{i}", snippet="test") for i in range(5)]
        async def mock_arxiv(q, c): return [SearchResult(title=f"Paper {i}", url=f"http://arxiv.com/{i}", snippet="test") for i in range(5)]
        async def empty(q, c): return []
        with _patch_sources(ddg=mock_ddg, arxiv=mock_arxiv, github=empty, reddit=empty, hn=empty, ss=empty):
            results = _run(multi_search("test", max_results=3))
            assert len(results) == 3

    def test_all_sources_timeout_returns_empty(self):
        """If all sources time out, return empty list (not error)."""
        async def slow_fetcher(query, client):
            await asyncio.sleep(100)
            return []
        with _patch_sources(ddg=slow_fetcher, arxiv=slow_fetcher, github=slow_fetcher, reddit=slow_fetcher, hn=slow_fetcher, ss=slow_fetcher):
            results = _run(multi_search("test query"))
            assert results == []

    def test_partial_failure_returns_remaining(self):
        """If one source fails, others should still return results."""
        async def mock_ddg(q, c): return [SearchResult(title="DDG result", url="http://ddg.com/1", snippet="test")]
        async def mock_arxiv(q, c): return [SearchResult(title="arXiv result", url="http://arxiv.com/1", snippet="test")]
        async def empty(q, c): return []
        with _patch_sources(ddg=mock_ddg, arxiv=mock_arxiv, github=empty, reddit=empty, hn=empty, ss=empty):
            results = _run(multi_search("test query"))
            assert len(results) == 2
            sources = {r.source for r in results}
            assert "ddg" in sources
            assert "arxiv" in sources


class TestFormatResults:
    """format_results_for_llm produces context for the LLM."""

    def test_empty_results(self):
        text = format_results_for_llm([])
        assert "No search results" in text

    def test_formats_with_source_labels(self):
        results = [
            MultiSearchResult(title="Paper Title", url="http://arxiv.org/123", snippet="Abstract here", source="arxiv"),
            MultiSearchResult(title="Repo Name", url="http://github.com/repo", snippet="Description here", source="github"),
        ]
        text = format_results_for_llm(results)
        assert "[1]" in text
        assert "[2]" in text
        assert "arXiv" in text
        assert "GitHub" in text
        assert "Paper Title" in text
        assert "Repo Name" in text

    def test_truncates_long_snippets(self):
        long_snippet = "A" * 500
        results = [
            MultiSearchResult(title="Test", url="http://example.com", snippet=long_snippet, source="ddg"),
        ]
        text = format_results_for_llm(results)
        # Snippet should be truncated to 300 chars in the output
        assert "A" * 301 not in text
