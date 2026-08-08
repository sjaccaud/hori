"""
Multi-Source Search for AIOS

Fans out a single query across 6 free, no-API-key sources in parallel,
merges and deduplicates the results, and returns them for LLM summarization.

Sources (all free, no API key required):
  - DuckDuckGo (general web, blogs, news)
  - arXiv (academic preprints, CS/ML/Physics) — rate limited, retries on 429
  - GitHub (code repositories, issues, READMEs)
  - Reddit (via DDG site:reddit.com filter — Reddit's JSON API now blocks
    unauthenticated access with 403)
  - Hacker News (tech industry discussions, via Algolia API)
  - Semantic Scholar (academic papers with citation counts) — rate limited,
    retries on 429

All sources are queried concurrently via asyncio.gather with a per-source
timeout. If a source is slow or down, the others still return — the timeout
source is simply dropped. This is more resilient than a single-source search.

Traces to: Manifesto Pillar IV (Seamless Voice & Remote Interaction),
UX-1.1 (voice assistant needs current information to be useful).

Future: Gemini Grounding Augment — add a `_search_gemini()` source that uses
the Gemini API with `google_search` tool for Google Search quality. Activated
when GEMINI_API_KEY is set in environment. See docs/roadmap.md → Future Expansion.
"""
import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List
from urllib.parse import quote_plus, urlencode

import httpx

from .web_search import SearchResult, search_ddg

logger = logging.getLogger(__name__)

# Per-source timeout: if a source doesn't respond in this many seconds,
# we drop it and keep the rest. 5s is generous — most sources respond in <1s.
SOURCE_TIMEOUT = 5.0

# Source authority weights for relevance scoring.
# Higher = results from this source are ranked higher in the merged list.
# These are heuristic — academic papers and code repos are generally more
# authoritative for technical queries than random Reddit threads.
SOURCE_WEIGHTS = {
    "arxiv": 1.3,
    "semantic_scholar": 1.3,
    "github": 1.2,
    "hackernews": 1.0,
    "reddit": 0.9,
    "ddg": 1.0,
}


@dataclass
class MultiSearchResult:
    """A search result from any source, with source attribution."""
    title: str
    url: str
    snippet: str
    source: str  # "ddg", "arxiv", "github", "reddit", "hackernews", "semantic_scholar"
    score: float = 0.0  # relevance score after merge
    content: str = ""  # full text if fetched (not fetched in multi-search)


# --- Source fetchers ---
# Each fetcher takes a query string and returns List[SearchResult].
# They use httpx.AsyncClient for concurrent execution.
# All fetchers are defensive: on any error, they return [] (empty list).

async def _search_ddg(query: str, client: httpx.AsyncClient) -> List[SearchResult]:
    """DuckDuckGo general web search (uses existing sync search_ddg in a thread)."""
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: search_ddg(query, max_results=5))
        return results
    except Exception as e:
        logger.debug(f"DDG search failed: {e}")
        return []


async def _search_arxiv(query: str, client: httpx.AsyncClient) -> List[SearchResult]:
    """arXiv preprint server — CS, ML, Physics, Math papers.
    Rate limited to ~1 req per 3 sec for unauthenticated users."""
    try:
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": 5,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        # Retry once after 3s if rate limited (429)
        for attempt in range(2):
            resp = await client.get(
                "https://export.arxiv.org/api/query",
                params=params,
                timeout=SOURCE_TIMEOUT,
            )
            if resp.status_code != 429:
                break
            if attempt == 0:
                await asyncio.sleep(3)
        resp.raise_for_status()

        # Parse Atom XML feed
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            summary_el = entry.find("atom:summary", ns)
            id_el = entry.find("atom:id", ns)
            if title_el is None or id_el is None:
                continue
            title = " ".join(title_el.text.split()) if title_el.text else ""
            snippet = " ".join(summary_el.text.split())[:300] if summary_el is not None and summary_el.text else ""
            url = id_el.text.strip() if id_el.text else ""
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        logger.info(f"arXiv search '{query[:40]}': {len(results)} results")
        return results
    except Exception as e:
        logger.debug(f"arXiv search failed: {e}")
        return []


async def _search_github(query: str, client: httpx.AsyncClient) -> List[SearchResult]:
    """GitHub repository search."""
    try:
        params = {"q": query, "sort": "stars", "order": "desc", "per_page": 5}
        resp = await client.get(
            "https://api.github.com/search/repositories",
            params=params,
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=SOURCE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("items", [])[:5]:
            title = item.get("full_name", "")
            desc = item.get("description", "") or ""
            stars = item.get("stargazers_count", 0)
            snippet = f"⭐ {stars} — {desc[:200]}"
            url = item.get("html_url", "")
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        logger.info(f"GitHub search '{query[:40]}': {len(results)} results")
        return results
    except Exception as e:
        logger.debug(f"GitHub search failed: {e}")
        return []


async def _search_reddit(query: str, client: httpx.AsyncClient) -> List[SearchResult]:
    """Reddit post search via DDG site:reddit.com filter.
    Reddit's public JSON API now blocks unauthenticated access (403),
    so we use DuckDuckGo to search Reddit content instead."""
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: search_ddg(f"site:reddit.com {query}", max_results=5)
        )
        # Mark these as reddit source for scoring
        logger.info(f"Reddit (via DDG) search '{query[:40]}': {len(results)} results")
        return results
    except Exception as e:
        logger.debug(f"Reddit search failed: {e}")
        return []


async def _search_hackernews(query: str, client: httpx.AsyncClient) -> List[SearchResult]:
    """Hacker News search via Algolia API."""
    try:
        params = {
            "query": query,
            "tags": "story",
            "hitsPerPage": 5,
            "numericFilters": "points>5",
        }
        resp = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params=params,
            timeout=SOURCE_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for hit in data.get("hits", [])[:5]:
            title = hit.get("title", hit.get("story_title", ""))
            points = hit.get("points", 0)
            num_comments = hit.get("num_comments", 0)
            snippet = f"↑{points} 💬{num_comments}"
            # Algolia returns either "url" (external) or "objectID" (HN post)
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        logger.info(f"Hacker News search '{query[:40]}': {len(results)} results")
        return results
    except Exception as e:
        logger.debug(f"Hacker News search failed: {e}")
        return []


async def _search_semantic_scholar(query: str, client: httpx.AsyncClient) -> List[SearchResult]:
    """Semantic Scholar — academic papers with citation counts.
    Rate limited to ~1 req/sec for unauthenticated users."""
    try:
        params = {
            "query": query,
            "limit": 5,
            "fields": "title,abstract,url,citationCount,year,authors",
        }
        # Retry once after 2s if rate limited (429)
        for attempt in range(2):
            resp = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params,
                timeout=SOURCE_TIMEOUT,
            )
            if resp.status_code != 429:
                break
            if attempt == 0:
                await asyncio.sleep(2)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for paper in data.get("data", [])[:5]:
            title = paper.get("title", "")
            abstract = (paper.get("abstract", "") or "")[:300]
            url = paper.get("url", "")
            citations = paper.get("citationCount", 0)
            year = paper.get("year", "")
            authors = ", ".join(a.get("name", "") for a in (paper.get("authors", []) or [])[:3])
            snippet = f"({year}) cited by {citations} — {authors}: {abstract}"
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        logger.info(f"Semantic Scholar search '{query[:40]}': {len(results)} results")
        return results
    except Exception as e:
        logger.debug(f"Semantic Scholar search failed: {e}")
        return []


# Source registry: maps source name to fetcher function
SOURCES = {
    "ddg": _search_ddg,
    "arxiv": _search_arxiv,
    "github": _search_github,
    "reddit": _search_reddit,
    "hackernews": _search_hackernews,
    "semantic_scholar": _search_semantic_scholar,
}


# --- Source selection ---
# Not every query needs all 6 sources. Querying arXiv for motherboard
# recommendations or GitHub for news wastes 4 HTTP requests and adds
# latency for zero benefit. This classifier picks the right sources
# based on simple keyword matching.
#
# Source sets:
#   ALL_SOURCES: everything (default for tech/academic queries)
#   GENERAL_SOURCES: DDG + Reddit + HN (product, news, opinion, how-to)
#   ACADEMIC_SOURCES: DDG + arXiv + Semantic Scholar (papers, research)

ALL_SOURCES = frozenset(SOURCES.keys())
GENERAL_SOURCES = frozenset({"ddg", "reddit", "hackernews"})
ACADEMIC_SOURCES = frozenset({"ddg", "arxiv", "semantic_scholar"})

# Keywords that indicate a product/recommendation/news query — skip
# academic and code sources for these.
_PRODUCT_KEYWORDS = {
    "buy", "price", "cost", "cheap", "best", "recommend", "review",
    "vs", "versus", "alternative", "deal", "budget", "expensive",
    "affordable", "worth it", "motherboard", "cpu", "gpu", "ram",
    "ssd", "laptop", "phone", "headphones", "keyboard", "monitor",
    "router", "psu", "case", "fan", "cooler", "desktop", "build",
}

# Keywords that indicate an academic/research query — skip general web
_ACADEMIC_KEYWORDS = {
    "paper", "research", "study", "arxiv", "citation", "benchmark",
    "algorithm", "model architecture", "training", "fine-tuning",
    "inference", "quantization", "attention", "transformer",
    "embedding", "retrieval", "rag", "evaluation", "dataset",
}


def _select_sources(query: str) -> frozenset:
    """Pick which sources to query based on query type.

    Returns a subset of SOURCES.keys(). Falls back to ALL_SOURCES
    if the query doesn't match any specific category.
    """
    lower = query.lower()
    if any(kw in lower for kw in _PRODUCT_KEYWORDS):
        return GENERAL_SOURCES
    if any(kw in lower for kw in _ACADEMIC_KEYWORDS):
        return ACADEMIC_SOURCES
    return ALL_SOURCES


async def multi_search(query: str, max_results: int = 10) -> List[MultiSearchResult]:
    """Search multiple sources in parallel and merge results.

    Fans out to all 6 sources concurrently, deduplicates by URL, scores by
    keyword relevance + source authority weight, and returns the top N.

    Args:
        query: The search query
        max_results: Maximum number of results to return

    Returns:
        List of MultiSearchResult sorted by relevance score (descending)
    """
    start = time.time()

    # Select sources based on query type — not every query needs all 6.
    selected = _select_sources(query)
    if selected != ALL_SOURCES:
        logger.info(f"Source selection: {sorted(selected)} (query: {query[:60]!r})")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(SOURCE_TIMEOUT + 2, connect=5.0),
        follow_redirects=True,
    ) as client:
        # Fan out to selected sources concurrently
        tasks = {
            name: asyncio.create_task(fetcher(query, client))
            for name, fetcher in SOURCES.items() if name in selected
        }

        # Wait for all with per-source timeout
        results_by_source = {}
        for name, task in tasks.items():
            try:
                results = await asyncio.wait_for(task, timeout=SOURCE_TIMEOUT)
                results_by_source[name] = results
            except asyncio.TimeoutError:
                logger.info(f"Source '{name}' timed out after {SOURCE_TIMEOUT}s")
            except Exception as e:
                logger.debug(f"Source '{name}' failed: {e}")

    # Merge into MultiSearchResult list
    merged = []
    seen_urls = set()
    query_lower = query.lower()
    query_terms = set(re.findall(r"\w+", query_lower)) - {"the", "a", "an", "is", "of", "to", "in", "for", "and", "or", "what", "how", "why", "with"}

    for source_name, results in results_by_source.items():
        weight = SOURCE_WEIGHTS.get(source_name, 1.0)
        for r in results:
            # Deduplicate by URL
            if r.url in seen_urls:
                continue
            seen_urls.add(r.url)

            # Score: keyword match in title + snippet, weighted by source authority
            title_lower = r.title.lower()
            snippet_lower = r.snippet.lower()
            title_matches = sum(1 for t in query_terms if t in title_lower)
            snippet_matches = sum(1 for t in query_terms if t in snippet_lower)
            score = (title_matches * 2.0 + snippet_matches * 0.5) * weight

            merged.append(MultiSearchResult(
                title=r.title,
                url=r.url,
                snippet=r.snippet,
                source=source_name,
                score=score,
            ))

    # Sort by score descending, take top N
    merged.sort(key=lambda x: x.score, reverse=True)
    result = merged[:max_results]

    elapsed = time.time() - start
    source_counts = {name: len(results) for name, results in results_by_source.items()}
    logger.info(
        f"Multi-search '{query[:40]}': {len(result)} results from "
        f"{len(results_by_source)}/{len(selected)} sources in {elapsed:.2f}s "
        f"({source_counts})"
    )
    return result


def format_results_for_llm(results: List[MultiSearchResult]) -> str:
    """Format multi-search results into a context block for the LLM.

    Each result is numbered [1], [2], etc. with title, source, and snippet.
    """
    if not results:
        return "No search results found."
    parts = []
    for i, r in enumerate(results):
        source_label = {
            "ddg": "Web",
            "arxiv": "arXiv",
            "github": "GitHub",
            "reddit": "Reddit",
            "hackernews": "Hacker News",
            "semantic_scholar": "Semantic Scholar",
        }.get(r.source, r.source)
        parts.append(
            f"[{i+1}] ({source_label}) {r.title}\n"
            f"    {r.snippet[:300]}\n"
            f"    URL: {r.url}"
        )
    return "\n\n".join(parts)
