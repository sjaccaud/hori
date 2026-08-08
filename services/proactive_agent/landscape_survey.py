"""
Proactive Opportunity Agent - Landscape Survey

Surveys the landscape for opportunities relevant to the user's projects and interests.
Gathers data from:
- GitHub trending repos (via web scraping, no API key needed)
- Hacker News top stories (via public API)
- RSS feeds for AI/ML news

The survey is grounded in the user's interests (from user_model.json) and
active projects (from project_state.json) so it only surfaces relevant opportunities.

Usage:
    PYTHONPATH=. ./venv/bin/python3 -m services.proactive_agent.landscape_survey

Output is written to core/state/opportunities.json for the Opportunity Proposer
to review and push through the red-team gate.
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_MODEL_PATH = PROJECT_ROOT / "core" / "state" / "user_model.json"
PROJECT_STATE_PATH = PROJECT_ROOT / "core" / "state" / "project_state.json"
OPPORTUNITIES_PATH = PROJECT_ROOT / "core" / "state" / "opportunities.json"

# LLM endpoint for scoring relevance
LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:8080/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen3.6-27B")


@dataclass
class Opportunity:
    title: str
    source: str  # "github", "hackernews", "rss"
    url: str
    description: str
    relevance_score: float = 0.0  # 0-1, how relevant to user's interests
    relevance_reason: str = ""
    discovered_at: str = ""
    tags: List[str] = field(default_factory=list)


def load_user_interests() -> List[str]:
    """Load user interests from user_model.json."""
    defaults = ["AI", "local-first", "Python", "llama.cpp", "ROCm"]
    try:
        data = json.loads(USER_MODEL_PATH.read_text())
        interests = data.get("active_interests", [])
        skills = data.get("skills", [])
        result = interests + skills
        return result if result else defaults
    except Exception:
        return defaults


def load_active_projects() -> List[str]:
    """Load active project names from project_state.json."""
    try:
        data = json.loads(PROJECT_STATE_PATH.read_text())
        return [p.get("name", "") for p in data.get("active_projects", []) if p.get("name")]
    except Exception:
        return ["AIOS"]


def survey_github_trending() -> List[Opportunity]:
    """Scrape GitHub trending page for trending repos."""
    opportunities = []
    try:
        resp = requests.get(
            "https://github.com/trending?since=daily",
            headers={"Accept": "text/html"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"GitHub trending returned {resp.status_code}")
            return []

        # Extract repo names and descriptions from HTML
        repo_pattern = re.compile(r'<h2 class="h3 lh-condensed">.*?href="/([^/]+/[^"]+)"', re.DOTALL)
        desc_pattern = re.compile(r'<p class="col-9 color-fg-muted my-1 pr-4">\s*(.*?)\s*</p>', re.DOTALL)

        repos = repo_pattern.findall(resp.text)
        descs = desc_pattern.findall(resp.text)

        for i, repo in enumerate(repos[:20]):
            desc = descs[i].strip() if i < len(descs) else ""
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            opportunities.append(Opportunity(
                title=f"github.com/{repo}",
                source="github",
                url=f"https://github.com/{repo}",
                description=desc[:300],
                discovered_at=datetime.now().isoformat(),
                tags=["github", "trending"],
            ))
    except Exception as e:
        logger.warning(f"GitHub survey failed: {e}")

    return opportunities


def survey_hackernews() -> List[Opportunity]:
    """Get top stories from Hacker News via public API."""
    opportunities = []
    try:
        # Get top story IDs
        resp = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10)
        if resp.status_code != 200:
            return []
        story_ids = resp.json()[:20]

        for sid in story_ids:
            try:
                story_resp = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                    timeout=10,
                )
                if story_resp.status_code == 200:
                    story = story_resp.json()
                    if story and story.get("title"):
                        opportunities.append(Opportunity(
                            title=story["title"],
                            source="hackernews",
                            url=story.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                            description=f"HN score: {story.get('score', 0)}, comments: {story.get('descendants', 0)}",
                            discovered_at=datetime.now().isoformat(),
                            tags=["hackernews"],
                        ))
            except Exception:
                continue
            time.sleep(0.2)  # rate limit
    except Exception as e:
        logger.warning(f"HackerNews survey failed: {e}")

    return opportunities


def survey_rss_feeds() -> List[Opportunity]:
    """Survey AI/ML RSS feeds for relevant articles."""
    feeds = [
        ("MIT Tech Review AI", "https://www.technologyreview.com/feed/"),
        ("The Batch (DeepLearning.AI)", "https://batch.ai/"),
        ("AI News", "https://news.artificialintelligence-news.com/feed/"),
    ]

    opportunities = []
    for name, url in feeds:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "AIOS/1.0"})
            if resp.status_code != 200:
                continue
            root = ElementTree.fromstring(resp.content)
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                desc = item.findtext("description", "")
                if title:
                    # Clean HTML from description
                    desc = re.sub(r"<[^>]+>", "", desc)[:300]
                    opportunities.append(Opportunity(
                        title=title,
                        source="rss",
                        url=link,
                        description=f"[{name}] {desc}",
                        discovered_at=datetime.now().isoformat(),
                        tags=["rss", name.lower().replace(" ", "_")],
                    ))
        except Exception as e:
            logger.warning(f"RSS feed {name} failed: {e}")

    return opportunities


def score_relevance(opportunities: List[Opportunity], interests: List[str], projects: List[str]) -> List[Opportunity]:
    """Score each opportunity's relevance to the user's interests using keyword matching.
    Splits multi-word interests into individual keywords for broader matching."""
    # Build keyword set: full phrases + individual words (excluding common words)
    stop_words = {"the", "a", "an", "and", "or", "for", "in", "of", "to", "is", "it", "with"}
    keywords = set()
    for interest in interests + projects:
        keywords.add(interest.lower())
        for word in interest.lower().split():
            if len(word) > 2 and word not in stop_words:
                keywords.add(word)

    # Add some domain-specific keywords
    keywords.update({"ai", "llm", "gpt", "python", "docker", "gpu", "rocm", "cuda", "local", "self-hosted",
                     "inference", "model", "training", "rag", "vector", "embedding", "agent", "automation"})

    for opp in opportunities:
        text = (opp.title + " " + opp.description).lower()
        matches = [kw for kw in keywords if kw in text]

        opp.relevance_score = min(len(matches) / 3.0, 1.0)  # cap at 1.0, 3 matches = full
        opp.relevance_reason = f"Matched: {', '.join(matches[:5])}" if matches else "No direct match"

    # Sort by relevance
    opportunities.sort(key=lambda o: o.relevance_score, reverse=True)
    return opportunities


def save_opportunities(opportunities: List[Opportunity]):
    """Save opportunities to JSON for the proposer to review."""
    OPPORTUNITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = [asdict(o) for o in opportunities[:50]]  # keep top 50
    OPPORTUNITIES_PATH.write_text(json.dumps(data, indent=2))
    logger.info(f"Saved {len(data)} opportunities to {OPPORTUNITIES_PATH}")


def run_survey():
    """Run the full landscape survey."""
    logger.info("Starting landscape survey...")

    interests = load_user_interests()
    projects = load_active_projects()
    logger.info(f"User interests: {interests}")
    logger.info(f"Active projects: {projects}")

    all_opportunities = []

    logger.info("Surveying GitHub trending...")
    all_opportunities.extend(survey_github_trending())

    logger.info("Surveying Hacker News...")
    all_opportunities.extend(survey_hackernews())

    logger.info("Surveying RSS feeds...")
    all_opportunities.extend(survey_rss_feeds())

    logger.info(f"Total raw opportunities: {len(all_opportunities)}")

    # Score and filter
    scored = score_relevance(all_opportunities, interests, projects)
    relevant = [o for o in scored if o.relevance_score > 0]

    logger.info(f"Relevant opportunities: {len(relevant)}")
    for opp in relevant[:10]:
        logger.info(f"  [{opp.relevance_score:.1f}] {opp.title[:60]} - {opp.relevance_reason}")

    save_opportunities(scored)
    return scored


if __name__ == "__main__":
    run_survey()
