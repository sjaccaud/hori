"""Tests for the opportunity proposer — work order quality metrics.

These tests verify the STRAT-7 quality pass:
  - Work order quality scoring (actionable, grounded, relevant, scoped)
  - Low-quality filtering
  - Deduplication in landscape survey scoring
"""
import pytest

from services.proactive_agent.opportunity_proposer import (
    score_work_order_quality,
    filter_low_quality,
)


@pytest.fixture
def good_work_order():
    """A work order that meets all quality criteria."""
    return {
        "title": "Evaluate new llama.cpp flash attention patch",
        "priority": "medium",
        "rationale": "The patch claims 15% speedup for IQ4 models, directly relevant to our inference stack.",
        "source": "github.com/ggerganov/llama.cpp",
        "first_steps": "1. Pull the PR branch. 2. Benchmark on Qwen3.6-27B. 3. Compare tokens/sec.",
    }


@pytest.fixture
def bad_work_order():
    """A work order that fails multiple quality criteria."""
    return {
        "title": "",
        "priority": "",
        "rationale": "",
        "source": "",
        "first_steps": "",
    }


@pytest.fixture
def marginal_work_order():
    """A work order that passes some but not all criteria."""
    return {
        "title": "A" * 100,  # too long, fails scoped
        "priority": "low",
        "rationale": "Might be useful",
        "source": "hackernews",
        "first_steps": "Look into it",
    }


def test_good_work_order_passes_all_criteria(good_work_order):
    """A well-formed work order should pass all quality checks."""
    quality = score_work_order_quality(good_work_order)
    assert quality["actionable"] is True
    assert quality["grounded"] is True
    assert quality["relevant"] is True
    assert quality["scoped"] is True
    assert quality["quality_score"] == 1.0
    assert "Passes all criteria" in quality["quality_reason"]


def test_bad_work_order_fails_all_criteria(bad_work_order):
    """An empty work order should fail all quality checks."""
    quality = score_work_order_quality(bad_work_order)
    assert quality["actionable"] is False
    assert quality["grounded"] is False
    assert quality["relevant"] is False
    assert quality["scoped"] is False
    assert quality["quality_score"] == 0.0
    assert "Fails" in quality["quality_reason"]


def test_marginal_work_order_partial_pass(marginal_work_order):
    """A marginal work order should pass some but not all checks."""
    quality = score_work_order_quality(marginal_work_order)
    assert quality["actionable"] is True  # "Look into it" is 13 chars, passes >10
    assert quality["grounded"] is True
    assert quality["relevant"] is True
    assert quality["scoped"] is False  # title is 100 chars
    assert 0 < quality["quality_score"] < 1.0


def test_filter_low_quality_removes_bad(bad_work_order, good_work_order):
    """filter_low_quality should remove bad work orders and keep good ones."""
    work_orders = [bad_work_order, good_work_order]
    filtered = filter_low_quality(work_orders, min_score=0.6)
    assert len(filtered) == 1
    assert filtered[0]["title"] == good_work_order["title"]


def test_filter_low_quality_adds_score(good_work_order):
    """filter_low_quality should add quality_score to each work order."""
    filtered = filter_low_quality([good_work_order], min_score=0.6)
    assert "quality_score" in filtered[0]
    assert "quality_reason" in filtered[0]
    assert filtered[0]["quality_score"] == 1.0


def test_filter_low_quality_empty_input():
    """filter_low_quality should handle empty input."""
    assert filter_low_quality([], min_score=0.6) == []


def test_filter_low_quality_all_filtered(bad_work_order):
    """filter_low_quality should return empty if all are low quality."""
    assert filter_low_quality([bad_work_order], min_score=0.6) == []


def test_quality_score_weights():
    """The quality score should weight actionable and relevant highest."""
    # Only actionable (title "Test" + priority "low" passes scoped)
    wo_actionable = {
        "title": "Test", "priority": "low",
        "rationale": "", "source": "",
        "first_steps": "1. Do thing. 2. Check result. 3. Report.",
    }
    q1 = score_work_order_quality(wo_actionable)
    assert q1["actionable"] is True
    assert q1["scoped"] is True  # "Test" < 80 chars, "low" is valid
    # actionable (0.35) + scoped (0.15) = 0.50
    assert q1["quality_score"] == 0.50

    # Only relevant (title "Test" + priority "low" passes scoped)
    wo_relevant = {
        "title": "Test", "priority": "low",
        "rationale": "This matters because it affects our inference speed.",
        "source": "", "first_steps": "",
    }
    q2 = score_work_order_quality(wo_relevant)
    assert q2["relevant"] is True
    assert q2["scoped"] is True
    # relevant (0.30) + scoped (0.15) = 0.45
    assert q2["quality_score"] == 0.45
