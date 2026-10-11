import asyncio
import itertools
import json
import logging
import os
import urllib.parse
from contextlib import asynccontextmanager

import httpx
from ddgs import DDGS
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from sources import (
    search_arxiv,
    search_github,
    search_hackernews,
    search_reddit,
    search_semantic_scholar,
    search_youtube,
)

logger = logging.getLogger("searchrouter")
logging.basicConfig(level=logging.INFO)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
BRAVE_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
EXA_API_KEY = os.getenv("EXA_API_KEY", "")
SEARXNG_FALLBACK_URL = os.getenv("SEARXNG_FALLBACK_URL", "")
DEFAULT_COUNT = int(os.getenv("SEARCH_RESULT_COUNT", "10"))
MODE = os.getenv("SEARCH_MODE", "priority")  # priority or round_robin
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10.0"))
MAX_SNIPPET_LENGTH = int(os.getenv("MAX_SNIPPET_LENGTH", "3000"))
DEFAULT_SOURCE = os.getenv("SEARCH_DEFAULT_SOURCE", "web")

_providers = []
if EXA_API_KEY:
    _providers.append("exa")
if TAVILY_API_KEY:
    _providers.append("tavily")
if BRAVE_API_KEY:
    _providers.append("brave")
if SEARXNG_FALLBACK_URL:
    _providers.append("searxng")
_providers.append("duckduckgo")  # always available

_cycle = itertools.cycle(_providers)
_cycle_lock = asyncio.Lock()


def _next_provider_round_robin():
    return next(_cycle)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True)
    logger.info("searchrouter starting with providers: %s", _providers)
    yield
    await app.state.client.aclose()


app = FastAPI(title="Search Router", lifespan=lifespan)


def _truncate(text: str, length: int = MAX_SNIPPET_LENGTH) -> str:
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0] + " ..."


def _normalize_url(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url.lower().strip().rstrip("/"))
        # keep only scheme, netloc, path; strip www. and query/fragment
        netloc = p.netloc.removeprefix("www.")
        return f"{p.scheme}://{netloc}{p.path}"
    except Exception:
        return url.lower().strip().rstrip("/")


def _dedup_results(results: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in results:
        key = _normalize_url(r.get("url", ""))
        if not key:
            continue
        existing = seen.get(key)
        if existing is None or r.get("score", 0) > existing.get("score", 0):
            seen[key] = r
    return list(seen.values())


def _to_results(provider: str, raw, count: int):
    results = []
    items = []
    if provider == "tavily":
        items = raw.get("results", [])[:count]
    elif provider == "brave":
        items = raw.get("web", {}).get("results", [])[:count]
    elif provider == "exa":
        items = raw.get("results", [])[:count]
    elif provider == "searxng":
        items = raw.get("results", [])[:count]
    elif provider == "duckduckgo":
        items = raw[:count]

    for idx, item in enumerate(items):
        if provider == "tavily":
            title = item.get("title", "")
            link = item.get("url", "")
            snippet = item.get("content", "") or item.get("raw_content", "")
        elif provider == "brave":
            title = item.get("title", "")
            link = item.get("url", "")
            snippet = item.get("description", "")
        elif provider == "exa":
            link = item.get("url", "")
            title = item.get("title", "") or link
            highlights = item.get("highlights", [])
            if highlights:
                snippet = " ".join(str(h) for h in highlights[:3])
            else:
                snippet = item.get("text", "") or item.get("summary", "")
            snippet = _truncate(snippet)
        elif provider == "searxng":
            title = item.get("title", "")
            link = item.get("url", "")
            snippet = item.get("content", "")
        else:  # duckduckgo
            title = item.get("title", "")
            link = item.get("href", "")
            snippet = item.get("body", "")

        if not link:
            continue

        score = max(0.0, 1.0 - (idx * 0.05))
        results.append({
            "url": link,
            "title": title,
            "content": snippet,
            "score": score,
            "engine": provider,
        })
    return results


async def _search_tavily(client: httpx.AsyncClient, q: str, count: int):
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": q,
        "search_depth": "basic",
        "max_results": count,
        "include_answer": False,
        "include_raw_content": False,
    }
    r = await client.post("https://api.tavily.com/search", json=payload)
    r.raise_for_status()
    return r.json()


async def _search_brave(client: httpx.AsyncClient, q: str, count: int):
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY,
    }
    params = {"q": q, "count": count, "offset": 0, "search_lang": "en"}
    r = await client.get("https://api.search.brave.com/res/v1/web/search", headers=headers, params=params)
    r.raise_for_status()
    return r.json()


async def _search_exa(client: httpx.AsyncClient, q: str, count: int):
    headers = {
        "Content-Type": "application/json",
        "x-api-key": EXA_API_KEY,
    }
    payload = {
        "query": q,
        "numResults": count,
        "type": "auto",
        "contents": {"text": True, "highlights": True},
    }
    r = await client.post("https://api.exa.ai/search", headers=headers, json=payload)
    r.raise_for_status()
    return r.json()


async def _search_searxng(client: httpx.AsyncClient, q: str, count: int):
    base = SEARXNG_FALLBACK_URL.split("?")[0].rstrip("/")
    params = {"q": q, "format": "json", "pageno": 1}
    r = await client.get(base, params=params)
    r.raise_for_status()
    return r.json()


async def _search_duckduckgo(q: str, count: int):
    def _run():
        with DDGS() as ddgs:
            return list(ddgs.text(q, max_results=count))
    return await asyncio.to_thread(_run)


async def _search_one(client: httpx.AsyncClient, provider: str, q: str, count: int):
    try:
        if provider == "tavily":
            raw = await _search_tavily(client, q, count)
        elif provider == "brave":
            raw = await _search_brave(client, q, count)
        elif provider == "exa":
            raw = await _search_exa(client, q, count)
        elif provider == "searxng":
            raw = await _search_searxng(client, q, count)
        else:
            raw = await _search_duckduckgo(q, count)
        return _to_results(provider, raw, count)
    except Exception as e:
        logger.warning("%s search failed for %r: %s", provider, q, e)
        return []


async def _pick_providers():
    if MODE == "round_robin" and len(_providers) > 1:
        async with _cycle_lock:
            first = _next_provider_round_robin()
        # order: first then the rest
        ordered = [first]
        for p in _providers:
            if p not in ordered:
                ordered.append(p)
        return ordered
    return _providers


_SOURCE_HANDLERS = {
    "web": None,
    "youtube": search_youtube,
    "arxiv": search_arxiv,
    "hackernews": search_hackernews,
    "github": search_github,
    "reddit": search_reddit,
    "semanticscholar": search_semantic_scholar,
}


async def _search_web(q: str, count: int):
    client: httpx.AsyncClient = app.state.client
    providers = await _pick_providers()

    for provider in providers:
        results = await _search_one(client, provider, q, count)
        if results:
            logger.info("served %s results from %s for %r", len(results), provider, q)
            return results

    logger.warning("all providers returned empty for %r", q)
    return []


@app.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    count: int = Query(DEFAULT_COUNT, ge=1, le=50),
    source: str = Query(DEFAULT_SOURCE, pattern=r"^(web|youtube|arxiv|hackernews|github|reddit|semanticscholar|all)$"),
):
    if source == "web":
        results = await _search_web(q, count)
        return JSONResponse({"results": results})

    if source == "all":
        tasks = [(_search_web, q, count)]
        per_source = max(3, count // 2)
        for name, handler in _SOURCE_HANDLERS.items():
            if name != "web":
                tasks.append((handler, q, per_source))
        results_by_source = await asyncio.gather(
            *(fn(q, n) for fn, q, n in tasks), return_exceptions=True
        )
        merged = []
        for res in results_by_source:
            if isinstance(res, list):
                merged.extend(res)
        merged = _dedup_results(merged)
        merged.sort(key=lambda x: x.get("score", 0), reverse=True)
        return JSONResponse({"results": merged[:count]})

    handler = _SOURCE_HANDLERS.get(source)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown source {source}")

    results = await handler(q, count)
    return JSONResponse({"results": results})


@app.get("/health")
async def health():
    return {"status": "ok", "providers": _providers, "mode": MODE, "sources": list(_SOURCE_HANDLERS.keys())}
