import asyncio
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import httpx
from cachetools import TTLCache
from ddgs import DDGS
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

logger = logging.getLogger("searchrouter.sources")

GITHUB_API_KEY = os.getenv("GITHUB_API_KEY", "")
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")

MAX_SNIPPET_LENGTH = int(os.getenv("MAX_SNIPPET_LENGTH", "1500"))

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "as", "into", "through", "during", "before", "after", "above",
    "below", "between", "under", "and", "but", "or", "yet", "so", "if",
    "because", "although", "though", "while", "where", "when", "that",
    "which", "who", "whom", "whose", "what", "this", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "our", "their", "its", "how", "why",
    "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "not", "only", "own", "same", "than", "too", "very",
    "just", "also", "now", "then", "here", "there", "every", "more",
    "most", "much", "many", "such",
}

USER_AGENT = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- YouTube transcript settings --------------------------------------------------

YOUTUBE_TRANSCRIPT_TTL = int(os.getenv("YOUTUBE_TRANSCRIPT_TTL", "3600"))
YOUTUBE_TRANSCRIPT_COOLDOWN = int(os.getenv("YOUTUBE_TRANSCRIPT_COOLDOWN", "1800"))
YOUTUBE_COOLDOWN_AFTER_FAILS = int(os.getenv("YOUTUBE_COOLDOWN_AFTER_FAILS", "5"))
YOUTUBE_COOLDOWN_WINDOW = int(os.getenv("YOUTUBE_COOLDOWN_WINDOW", "300"))
YOUTUBE_PROXY = os.getenv("YOUTUBE_PROXY", "")
YOUTUBE_COOKIE_FILE = os.getenv("YOUTUBE_COOKIE_FILE", "")
YOUTUBE_PO_TOKEN = os.getenv("YOUTUBE_PO_TOKEN", "")
YOUTUBE_VISITOR_DATA = os.getenv("YOUTUBE_VISITOR_DATA", "")

_transcript_cache = TTLCache(maxsize=1000, ttl=YOUTUBE_TRANSCRIPT_TTL)
_transcript_fail_cache = TTLCache(maxsize=1000, ttl=YOUTUBE_TRANSCRIPT_COOLDOWN)
_youtube_fail_times: list[float] = []


def _record_youtube_failure():
    now = time.time()
    _youtube_fail_times.append(now)
    while _youtube_fail_times and _youtube_fail_times[0] < now - YOUTUBE_COOLDOWN_WINDOW:
        _youtube_fail_times.pop(0)
    if len(_youtube_fail_times) >= YOUTUBE_COOLDOWN_AFTER_FAILS:
        logger.warning(
            "YouTube transcript fetching paused for %d seconds after %d failures",
            YOUTUBE_TRANSCRIPT_COOLDOWN,
            len(_youtube_fail_times),
        )


def _youtube_cooldown_active() -> bool:
    if len(_youtube_fail_times) < YOUTUBE_COOLDOWN_AFTER_FAILS:
        return False
    return time.time() - _youtube_fail_times[-1] < YOUTUBE_TRANSCRIPT_COOLDOWN


def _parse_video_id(url_or_id: str) -> str | None:
    """Extract YouTube video id from url or return it if already an id."""
    if re.match(r"^[A-Za-z0-9_-]{11}$", url_or_id):
        return url_or_id
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url_or_id)
        if m:
            return m.group(1)
    return None


def _truncate(text: str, length: int = MAX_SNIPPET_LENGTH) -> str:
    text = re.sub(r"\[[^\]]+\]", " ", text)  # drop [Music], [Applause], etc.
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0] + " ..."


def _transcript_snippet(full_text: str, q: str, max_len: int = MAX_SNIPPET_LENGTH) -> str:
    """Return a query-relevant window of the transcript, capped at max_len."""
    full_text = re.sub(r"\[[^\]]+\]", " ", full_text)
    full_text = re.sub(r"\s+", " ", full_text).strip()
    if len(full_text) <= max_len:
        return full_text

    # pick the longest meaningful query term
    terms = [
        t for t in re.findall(r"[a-zA-Z0-9]+", q)
        if t.lower() not in _STOPWORDS and len(t) > 2
    ]
    terms.sort(key=len, reverse=True)
    pos = -1
    for term in terms:
        pos = full_text.lower().find(term.lower())
        if pos != -1:
            break

    if pos == -1:
        return _truncate(full_text, max_len)

    half = max_len // 2
    start = max(0, pos - half)
    end = min(len(full_text), pos + half)
    window = full_text[start:end]
    return _truncate(window.strip(), max_len)


def _srt_to_text(srt: str) -> str:
    """Convert SRT subtitle text to plain text."""
    blocks = re.split(r"\n\s*\n", srt.strip())
    lines: list[str] = []
    for block in blocks:
        parts = block.splitlines()
        if len(parts) >= 3:
            lines.extend(parts[2:])
    text = " ".join(lines)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fetch_ytdlp_transcript(vid: str) -> str | None:
    try:
        import yt_dlp
    except ImportError:
        return None

    url = f"https://www.youtube.com/watch?v={vid}"
    outtmpl = f"/tmp/yt_{vid}.%(ext)s"
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "txt",
        "skip_download": True,
        "outtmpl": outtmpl,
    }
    if YOUTUBE_COOKIE_FILE:
        opts["cookiefile"] = YOUTUBE_COOKIE_FILE

    extractor_args: dict[str, dict[str, str]] = {}
    youtube_args = extractor_args.setdefault("youtube", {})
    if YOUTUBE_PO_TOKEN:
        youtube_args["po_token"] = YOUTUBE_PO_TOKEN
    if YOUTUBE_VISITOR_DATA:
        youtube_args["visitor_data"] = YOUTUBE_VISITOR_DATA
    if youtube_args:
        opts["extractor_args"] = extractor_args

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.debug("yt-dlp download failed for %s: %s", vid, e)
        return None

    txt_path = f"/tmp/yt_{vid}.en.txt"
    srt_path = f"/tmp/yt_{vid}.en.srt"
    try:
        with open(txt_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        try:
            with open(srt_path) as f:
                return _srt_to_text(f.read())
        except FileNotFoundError:
            return None


def _get_youtube_transcript(vid: str) -> str | None:
    if _youtube_cooldown_active():
        logger.debug("YouTube transcript fetch in cooldown")
        return None
    if vid in _transcript_cache:
        return _transcript_cache[vid]
    if vid in _transcript_fail_cache:
        return None

    # Try youtube-transcript-api first, with optional proxy
    try:
        proxy_config = None
        if YOUTUBE_PROXY:
            proxy_config = GenericProxyConfig(http_url=YOUTUBE_PROXY, https_url=YOUTUBE_PROXY)
        api = YouTubeTranscriptApi(proxy_config=proxy_config)
        transcript_obj = api.fetch(vid)
        text = re.sub(r"\[[^\]]+\]", " ", " ".join(s.text for s in transcript_obj))
        text = re.sub(r"\s+", " ", text).strip()
        _transcript_cache[vid] = text
        return text
    except Exception as e:
        logger.debug("youtube-transcript-api failed for %s: %s", vid, e)

    # Fallback to yt-dlp with optional cookies / po_token
    try:
        text = _fetch_ytdlp_transcript(vid)
        if text:
            _transcript_cache[vid] = text
            return text
    except Exception as e:
        logger.debug("yt-dlp failed for %s: %s", vid, e)

    _transcript_fail_cache[vid] = True
    _record_youtube_failure()
    return None


def _youtube_search_sync(q: str, count: int) -> list[dict]:
    try:
        with DDGS() as ddgs:
            items = list(ddgs.text(f"{q} site:youtube.com", max_results=count * 2))
    except Exception as e:
        logger.warning("YouTube DDG search failed: %s", e)
        return []

    results = []
    for item in items:
        link = item.get("href", "")
        title = item.get("title", "")
        vid = _parse_video_id(link)
        if not vid:
            continue
        url = f"https://www.youtube.com/watch?v={vid}"
        transcript = _get_youtube_transcript(vid)
        if transcript:
            transcript = _transcript_snippet(transcript, q)
        results.append({
            "url": url,
            "title": title or url,
            "content": transcript or title,
            "score": max(0.0, 1.0 - (len(results) * 0.05)),
            "engine": "youtube",
        })
        if len(results) >= count:
            break
    return results


async def search_youtube(q: str, count: int) -> list[dict]:
    return await asyncio.to_thread(_youtube_search_sync, q, count)


async def search_arxiv(q: str, count: int) -> list[dict]:
    params = {"search_query": q, "start": 0, "max_results": count, "sortBy": "relevance"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://export.arxiv.org/api/query", params=params)
            r.raise_for_status()
    except Exception as e:
        logger.warning("arXiv search failed: %s", e)
        return []

    root = ET.fromstring(r.text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    results = []
    for idx, entry in enumerate(root.findall(".//atom:entry", ns)):
        title = entry.findtext("atom:title", default="", namespaces=ns).strip()
        summary = entry.findtext("atom:summary", default="", namespaces=ns).strip()
        id_el = entry.find("atom:id", ns)
        arxiv_id = id_el.text if id_el is not None else ""
        # prefer ar5iv HTML for full text
        html_url = arxiv_id.replace("http://arxiv.org/abs/", "https://ar5iv.org/html/") if arxiv_id else ""
        results.append({
            "url": html_url or arxiv_id,
            "title": title or arxiv_id,
            "content": _truncate(summary),
            "score": max(0.0, 1.0 - (idx * 0.05)),
            "engine": "arxiv",
        })
    return results


async def search_hackernews(q: str, count: int) -> list[dict]:
    params = {"query": q, "tags": "story", "hitsPerPage": count}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://hn.algolia.com/api/v1/search", params=params)
            r.raise_for_status()
    except Exception as e:
        logger.warning("Hacker News search failed: %s", e)
        return []

    data = r.json()
    results = []
    for idx, hit in enumerate(data.get("hits", [])[:count]):
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        title = hit.get("title", "") or url
        snippet = hit.get("story_text", "") or ""
        if not snippet:
            snippet = f"{hit.get('points', 0)} points, {hit.get('num_comments', 0)} comments | author {hit.get('author')}"
        results.append({
            "url": url,
            "title": title,
            "content": _truncate(snippet),
            "score": max(0.0, 1.0 - (idx * 0.05)),
            "engine": "hackernews",
        })
    return results


async def search_github(q: str, count: int) -> list[dict]:
    params = {"q": q, "sort": "updated", "order": "desc", "per_page": count}
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_API_KEY:
        headers["Authorization"] = f"Bearer {GITHUB_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://api.github.com/search/repositories", params=params, headers=headers)
            r.raise_for_status()
    except Exception as e:
        logger.warning("GitHub search failed: %s", e)
        return []

    data = r.json()
    results = []
    for idx, item in enumerate(data.get("items", [])[:count]):
        snippet = item.get("description") or ""
        extras = f"stars: {item.get('stargazers_count', 0)}, language: {item.get('language') or 'unknown'}, updated: {item.get('updated_at')}"
        results.append({
            "url": item.get("html_url", ""),
            "title": item.get("full_name", ""),
            "content": _truncate(f"{snippet}\n\n{extras}"),
            "score": max(0.0, 1.0 - (idx * 0.05)),
            "engine": "github",
        })
    return results


def _reddit_search_sync(q: str, count: int) -> list[dict]:
    try:
        with DDGS() as ddgs:
            items = list(ddgs.text(f"{q} site:reddit.com", max_results=count * 2))
    except Exception as e:
        logger.warning("Reddit DDG search failed: %s", e)
        return []

    results = []
    for item in items:
        link = item.get("href", "")
        title = item.get("title", "")
        body = item.get("body", "")
        if not link or "reddit.com" not in link:
            continue
        results.append({
            "url": link,
            "title": title or link,
            "content": _truncate(body or title),
            "score": max(0.0, 1.0 - (len(results) * 0.05)),
            "engine": "reddit",
        })
        if len(results) >= count:
            break
    return results


async def search_reddit(q: str, count: int) -> list[dict]:
    return await asyncio.to_thread(_reddit_search_sync, q, count)


async def search_semantic_scholar(q: str, count: int) -> list[dict]:
    fields = "title,authors,year,abstract,openAccessPdf,externalIds,url,influentialCitationCount"
    params = {"query": q, "fields": fields, "limit": count}
    headers = {}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, headers=headers)
            r.raise_for_status()
    except Exception as e:
        logger.warning("Semantic Scholar search failed: %s", e)
        return []

    data = r.json()
    results = []
    for idx, paper in enumerate(data.get("data", [])[:count]):
        authors = ", ".join(a.get("name", "") for a in paper.get("authors", [])[:3])
        abstract = paper.get("abstract", "")
        year = paper.get("year")
        pdf = (paper.get("openAccessPdf") or {}).get("url", "")
        url = pdf or paper.get("url") or ""
        title = paper.get("title", "")
        snippet = f"{title}\nAuthors: {authors}\nYear: {year}\n\n{abstract}".strip()
        results.append({
            "url": url,
            "title": title or url,
            "content": _truncate(snippet),
            "score": max(0.0, 1.0 - (idx * 0.05)),
            "engine": "semanticscholar",
        })
    return results
