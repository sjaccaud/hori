"""Tests for the proactive opportunity agent."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.proactive_agent.landscape_survey import (
    Opportunity,
    score_relevance,
    load_user_interests,
    load_active_projects,
)


@pytest.fixture
def sample_opportunities():
    return [
        Opportunity(
            title="New local LLM inference engine",
            source="github",
            url="https://github.com/example/llm-engine",
            description="A fast local inference engine for running large models on consumer GPUs",
            discovered_at="2026-01-01T00:00:00",
            tags=["github", "trending"],
        ),
        Opportunity(
            title="Recipe: Best chocolate cake",
            source="rss",
            url="https://example.com/cake",
            description="A delicious chocolate cake recipe with step-by-step instructions",
            discovered_at="2026-01-01T00:00:00",
            tags=["rss", "cooking"],
        ),
        Opportunity(
            title="AI agent framework with memory",
            source="hackernews",
            url="https://news.ycombinator.com/item?id=123",
            description="A new framework for building AI agents with persistent memory and RAG",
            discovered_at="2026-01-01T00:00:00",
            tags=["hackernews"],
        ),
    ]


def test_score_relevance_filters_by_interests(sample_opportunities):
    """Opportunities matching user interests should score higher."""
    interests = ["local LLM inference", "AI agents"]
    projects = ["AIOS"]

    scored = score_relevance(sample_opportunities, interests, projects)

    # The LLM and AI agent opportunities should score higher than the cake recipe
    assert scored[0].title != "Recipe: Best chocolate cake"
    assert scored[-1].title == "Recipe: Best chocolate cake"
    assert scored[0].relevance_score > scored[-1].relevance_score


def test_score_relevance_caps_at_1():
    """Relevance score should cap at 1.0."""
    opp = Opportunity(
        title="AI LLM Python Docker GPU ROCm local inference agent model",
        source="test",
        url="https://example.com",
        description="AI LLM Python Docker GPU ROCm local inference agent model training RAG vector embedding",
        discovered_at="",
    )
    scored = score_relevance([opp], ["AI", "LLM"], ["AIOS"])
    assert scored[0].relevance_score == 1.0


def test_opportunity_dataclass():
    """Opportunity should serialize correctly."""
    opp = Opportunity(
        title="Test",
        source="test",
        url="https://example.com",
        description="Test description",
    )
    from dataclasses import asdict
    d = asdict(opp)
    assert d["title"] == "Test"
    assert d["relevance_score"] == 0.0
    assert d["tags"] == []


def test_load_user_interests():
    """Should load interests from user_model.json."""
    interests = load_user_interests()
    assert isinstance(interests, list)
    assert len(interests) > 0


def test_load_active_projects():
    """Should load project names from project_state.json."""
    projects = load_active_projects()
    assert isinstance(projects, list)
