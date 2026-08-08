"""Tests for the web search module."""
from unittest.mock import MagicMock, patch

import pytest

from services.aios_core.web_search import (
    SearchResult,
    SearchResponse,
    needs_web_search,
    _build_search_query,
    search_and_summarize,
)


def test_needs_web_search_triggers_on_latest():
    """Queries about 'latest' should trigger web search."""
    assert needs_web_search("What is the latest llama.cpp release?") is True


def test_needs_web_search_triggers_on_model_version():
    """Queries about specific model versions should trigger web search."""
    assert needs_web_search("Is Qwen 3.8 worth looking at?") is True
    assert needs_web_search("What about llama.cpp b4230?") is True


def test_needs_web_search_triggers_on_today():
    """Queries about current events should trigger web search."""
    assert needs_web_search("What happened in AI news recently?") is True
    assert needs_web_search("What's the current version of Ollama?") is True


def test_needs_web_search_does_not_trigger_for_codebase():
    """Questions about AIOS's own codebase should not trigger web search."""
    assert needs_web_search("How does the red team gate work?") is False
    assert needs_web_search("What does the system_state endpoint do?") is False


def test_needs_web_search_does_not_trigger_for_greetings():
    """Short greetings should not trigger web search."""
    assert needs_web_search("hi") is False
    assert needs_web_search("hey") is False
    assert needs_web_search("thanks") is False


def test_needs_web_search_triggers_on_no_memory_hits():
    """If memory retrieval found nothing and it's an external question, search."""
    # General knowledge questions should NOT trigger search (LLM knows these)
    assert needs_web_search("What is the capital of France?", memory_hits=[]) is False
    # But time-sensitive questions with no memory should search
    assert needs_web_search("What is the current price of Bitcoin?", memory_hits=[]) is True


def test_needs_web_search_does_not_trigger_with_memory_hits():
    """If memory retrieval found results, don't search (unless trigger words)."""
    assert needs_web_search("What is RAG?", memory_hits=[{"content": "RAG is..."}]) is False


def test_build_search_query_strips_question_prefix():
    """Search query should strip question prefixes."""
    q = _build_search_query("What is the latest Qwen model?")
    assert "what is" not in q
    assert "qwen model" in q


def test_build_search_query_adds_year():
    """Search query should add 2026 if asking about latest/recent."""
    q = _build_search_query("What is the latest llama.cpp release?")
    assert "2026" in q


def test_build_search_query_worth_looking_at():
    """'Worth looking at' should be transformed to 'review'."""
    q = _build_search_query("Is Qwen 3.8 worth looking at?")
    assert "review" in q


def test_search_and_summarize_with_mock():
    """Test the full search flow with mocked search and LLM."""
    mock_results = [
        SearchResult(title="Test Result", url="https://example.com/test", snippet="Test snippet"),
    ]
    mock_llm = MagicMock(return_value="This is a test summary.")

    with patch("services.aios_core.web_search.search_ddg", return_value=mock_results), \
         patch("services.aios_core.web_search.fetch_page_content", return_value="Test page content"):
        result = search_and_summarize("test query", mock_llm)

    assert result.searched is True
    assert "test summary" in result.summary.lower()
    assert len(result.sources) == 1
    mock_llm.assert_called_once()


def test_search_and_summarize_no_results():
    """When search returns no results, return a helpful message."""
    mock_llm = MagicMock()
    with patch("services.aios_core.web_search.search_ddg", return_value=[]):
        result = search_and_summarize("nonexistent query", mock_llm)

    assert "couldn't find" in result.summary.lower()
    mock_llm.assert_not_called()
