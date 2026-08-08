"""
Web Search Tool for AIOS

Enables AIOS to search the internet for current information when it doesn't
know the answer. Uses multi-source fan-out search (see multi_search.py) to
query 6 free sources in parallel: DuckDuckGo, arXiv, GitHub, Reddit, Hacker
News, and Semantic Scholar.

Flow:
1. Detect when a query needs web search (current events, recent releases, dates, "latest")
2. Fan out to 6 sources in parallel (multi_search.py)
3. Merge, deduplicate, and score results by keyword relevance + source authority
4. Summarize with the LLM
5. Return the summary + sources
6. Optionally ingest the result into RAG for future recall

This module provides the search trigger detection (needs_web_search) and the
DDG search backend. The multi-source fan-out is in multi_search.py.
"""
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

import requests

logger = logging.getLogger(__name__)

# Cache for web search results (5 minute TTL)
# Keyed by normalized query string
_search_cache = {}
_SEARCH_CACHE_TTL = 300  # 5 minutes


def _normalize_query(query: str) -> str:
    """Normalize a query for cache key purposes."""
    return re.sub(r"\s+", " ", query.strip().lower())[:200]


def _get_cached(query: str) -> Optional[str]:
    """Get cached search result if still valid."""
    key = _normalize_query(query)
    if key in _search_cache:
        entry = _search_cache[key]
        if time.time() - entry["time"] < _SEARCH_CACHE_TTL:
            logger.info(f"Web search cache hit for: {query[:60]}")
            return entry["summary"]
        else:
            del _search_cache[key]
    return None


def _set_cached(query: str, summary: str):
    """Cache a search result."""
    key = _normalize_query(query)
    _search_cache[key] = {"summary": summary, "time": time.time()}
    # Trim cache if it gets too large
    if len(_search_cache) > 50:
        oldest = min(_search_cache.items(), key=lambda x: x[1]["time"])
        del _search_cache[oldest[0]]

# Keywords that indicate a web search is needed
SEARCH_TRIGGERS = [
    "latest", "recent", "this week", "this month",
    "2024", "2025", "2026", "2027",
    "news", "released", "announce", "changelog", "roadmap",
    "worth looking at", "is it worth", "vs ", "compared to",
    "what's new", "what is new",
    "price of", "cost of", "stock", "weather",
    "who won", "what happened in",
    "trending",
    # Explicit web search requests
    "search the web", "search for", "look that up", "look it up",
    "google that", "google search", "can you search",
    "find online", "what's the latest news",
]

# Topics that should NOT trigger web search (we know these from memory)
NO_SEARCH_PATTERNS = [
    "what is aios", "how does aios", "aios core",
    "red team gate", "system_state endpoint",
    "what does the", "how does the",
]


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: str = ""  # full page text if fetched


@dataclass
class SearchResponse:
    query: str
    summary: str
    sources: List[dict] = field(default_factory=list)
    searched: bool = True


def needs_web_search(user_text: str, memory_hits: list = None, conversation_history: list = None) -> bool:
    """Determine if a query needs a web search.

    Returns True if:
    - The query contains search trigger keywords (latest, recent, today, etc.)
    - The query asks about something current/time-sensitive
    - Memory retrieval returned no relevant results
    - The conversation history contains a topic that was recently searched
      (so follow-up questions also get fresh data)
    """
    lower = user_text.lower().strip()

    # Too short - don't search (unless we have context from history)
    if len(lower) < 10 and not conversation_history:
        return False

    # Don't search for things we should know from our own codebase
    # (unless they're asking about something external/current)
    if any(p in lower for p in NO_SEARCH_PATTERNS) and len(lower) < 80:
        if not any(t in lower for t in ["latest", "recent", "2024", "2025", "2026", "news", "new"]):
            return False

    # Don't search for questions about the local machine/filesystem
    # These are local questions, not internet questions
    LOCAL_PATTERNS = [
        "my machine", "my computer", "my files", "my disk", "my drive",
        "on this box", "on this machine", "on this server", "on this system",
        "how many files", "how much space", "disk space", "disk usage",
        "my setup", "my config", "my environment", "my system",
        "locally installed", "what do i have", "what's installed",
        "my music", "my photos", "my documents", "my projects",
        "my code", "my repo", "my database",
        "do i have", "i have on", "on my",
    ]
    if any(p in lower for p in LOCAL_PATTERNS):
        return False

    # Check for explicit search triggers
    if any(trigger in lower for trigger in SEARCH_TRIGGERS):
        return True

    # Check for questions about specific products/models that might be recent
    model_pattern = re.search(r"(qwen|llama|mistral|gemini|gemma|gpt|claude|deepseek|llama\.cpp|ollama|phi|yi|solar)[\s\-]*[\d\.]", lower)
    if model_pattern:
        return True

    # Check for "release" or "version" or "changelog" queries
    if any(w in lower for w in ["release", "version", "changelog", "update", "upgrade", "benchmark"]):
        return True

    # If memory retrieval found nothing relevant, try web search for questions
    # that are clearly about external/current topics (not general knowledge or
    # personal/project questions). When memory_hits is None (speculative search
    # before memory is loaded), skip this check.
    if memory_hits is not None and len(memory_hits) == 0 and len(user_text) > 20:
        if "?" in user_text or any(w in lower for w in ["what", "who", "when", "where", "how many", "how much"]):
            # Exclude personal/project questions that shouldn't search the web
            personal_patterns = [
                "we work on", "we do", "i do", "i work", "we discuss",
                "yesterday", "last week", "last time", "earlier",
                "our project", "the project", "my code", "my setup",
                "you remember", "what did we", "what were we",
                "what have we", "what about our",
            ]
            # Exclude general knowledge questions - the LLM knows these
            general_knowledge_patterns = [
                "what is ", "what are ", "what was ", "what were ",
                "how does ", "how do ", "how did ",
                "why is ", "why do ", "why does ",
                "who is ", "who was ", "who are ",
                "where is ", "where are ",
                "what is the capital", "what is the meaning",
            ]
            if not any(p in lower for p in personal_patterns):
                # Only search for general knowledge if it might be time-sensitive
                if any(p in lower for p in general_knowledge_patterns):
                    # Check for time-sensitive indicators
                    if not any(t in lower for t in ["latest", "recent", "current", "new", "2024", "2025", "2026", "price", "cost", "today"]):
                        return False
                return True

    # Check conversation history: if a recent turn triggered a web search
    # (contains model names, tech terms, or search triggers), follow-ups
    # should also search so the LLM gets fresh data instead of stale memory.
    if conversation_history:
        # Look at the last 4 messages for context
        recent = conversation_history[-4:] if len(conversation_history) > 4 else conversation_history
        combined_context = " ".join(
            m.get("content", "") for m in recent if isinstance(m, dict)
        ).lower()
        # If recent context mentions a specific model/version, search
        if re.search(r"(qwen|llama|mistral|gemini|gemma|gpt|claude|deepseek)[\s\-]*[\d\.]", combined_context):
            return True
        # If recent context had search triggers, search
        if any(trigger in combined_context for trigger in ["latest", "recent", "news", "release", "version", "2026", "2025"]):
            return True

    return False


def search_ddg(query: str, max_results: int = 5) -> List[SearchResult]:
    """Search DuckDuckGo and return results."""
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(SearchResult(
                    title=r.get("title", ""),
                    url=r.get("href", r.get("url", "")),
                    snippet=r.get("body", r.get("snippet", "")),
                ))
        logger.info(f"DDG search '{query}': {len(results)} results")
        return results
    except Exception as e:
        logger.warning(f"DDG search failed: {e}")
        return []


def fetch_page_content(url: str, max_chars: int = 3000) -> str:
    """Fetch and extract text content from a URL."""
    try:
        resp = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AIOS/1.0"},
        )
        if resp.status_code != 200:
            return ""

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            return ""

        text = resp.text

        # Simple HTML text extraction (no heavy deps)
        # Remove scripts and styles
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Clean whitespace
        text = re.sub(r"\s+", " ", text).strip()
        # Decode common HTML entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")

        return text[:max_chars]
    except Exception as e:
        logger.debug(f"Failed to fetch {url}: {e}")
        return ""


def _build_search_query(user_text: str) -> str:
    """Transform a user question into a better search query.
    Removes filler words, adds context, and makes it search-engine friendly."""
    lower = user_text.lower().strip()

    # Remove common question prefixes
    for prefix in ["can you ", "could you ", "what is ", "what are ", "what's ",
                   "tell me about ", "is ", "are ", "should i ", "do you know ",
                   "do you have any info on ", "any news on ", "any info on "]:
        if lower.startswith(prefix):
            lower = lower[len(prefix):]
            break

    # Add "2026" if asking about latest/current/recent without a year
    year_words = ["latest", "recent", "new", "current", "today", "now"]
    if any(w in lower for w in year_words) and "202" not in lower:
        lower += " 2026"

    # For "is X worth looking at" -> "X review"
    if "worth looking at" in lower or "worth it" in lower:
        lower = lower.replace("worth looking at", "review").replace("worth it", "review")

    # For news questions, add "news"
    if "news" in lower or "happened" in lower:
        if "ai" not in lower:
            lower = "AI " + lower

    return lower[:200]  # cap length


def search_and_summarize(
    query: str,
    llm_call_fn: Callable[[str, str], str],
    max_pages: int = 3,
) -> SearchResponse:
    """Search the web, fetch top results, and summarize with the LLM.
    Results are cached for 5 minutes to avoid re-searching the same topic.

    Args:
        query: The user's question
        llm_call_fn: A function(system_prompt, user_prompt) -> response_text
        max_pages: How many pages to fetch and read

    Returns:
        SearchResponse with summary and sources
    """
    # Check cache first
    cached = _get_cached(query)
    if cached:
        return SearchResponse(query=query, summary=cached, searched=True)

    # Build a better search query
    search_query = _build_search_query(query)
    logger.info(f"Web search: '{query}' -> '{search_query}'")

    # Search
    results = search_ddg(search_query, max_results=max_pages + 2)
    if not results:
        return SearchResponse(query=query, summary="I couldn't find any search results for that.", searched=True)

    # Fetch top pages
    for r in results[:max_pages]:
        r.content = fetch_page_content(r.url)

    # Build context for LLM
    context_parts = []
    sources = []
    for i, r in enumerate(results[:max_pages]):
        if r.content:
            context_parts.append(f"--- Source {i+1}: {r.title} ({r.url}) ---\n{r.content[:2000]}")
        sources.append({"title": r.title, "url": r.url, "snippet": r.snippet})

    context = "\n\n".join(context_parts) if context_parts else "\n\n".join(
        f"--- Source {i+1}: {r.title} ---\n{r.snippet}" for i, r in enumerate(results[:max_pages])
    )

    system_prompt = (
        "You are AIOS, a local-first AI assistant. The user asked a question that requires "
        "current information from the web. Below are search results. Summarize the key findings "
        "and answer the user's question directly. Be concise (2-4 sentences). "
        "Cite sources by number [1], [2], etc. If the search results don't answer the question, "
        "say so honestly. Do not mention that you searched the web - just answer naturally."
    )
    user_prompt = f"Question: {query}\n\nSearch results:\n{context}"

    try:
        summary = llm_call_fn(system_prompt, user_prompt)
    except Exception as e:
        logger.error(f"LLM summarization failed: {e}")
        summary = f"I found some results but couldn't summarize them. Here are the sources:\n" + \
                  "\n".join(f"[{i+1}] {s['title']} - {s['url']}" for i, s in enumerate(sources))

    # Cache the result
    if summary:
        _set_cached(query, summary)

    return SearchResponse(query=query, summary=summary, sources=sources)
