"""Tests for consolidation quality metrics.

Tests the three metrics without requiring a live embedding server:
  - Consolidation fidelity (semantic overlap)
  - Promotion rate (working → project → longterm)
  - Cluster coherence (semantic tightness)
  - Quality score computation
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.consolidation_metrics import (
    ConsolidationReport,
    cosine_similarity,
    compute_quality_score,
    measure_consolidation_cycle,
    save_metrics,
    get_latest_metrics,
)


# --- cosine_similarity tests ---

def test_cosine_similarity_identical_vectors():
    """Identical vectors should have similarity 1.0."""
    v = [1.0, 2.0, 3.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    """Orthogonal vectors should have similarity 0.0."""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector():
    """Zero vectors should return 0.0 (no division by zero)."""
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0


def test_cosine_similarity_opposite_vectors():
    """Opposite vectors should have similarity -1.0."""
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0)


# --- compute_quality_score tests ---

def test_quality_score_perfect():
    """Perfect metrics should give an A grade."""
    score, grade = compute_quality_score(fidelity=1.0, coherence=1.0, promotion_rate=0.2)
    assert score == pytest.approx(1.0)
    assert grade == "A"


def test_quality_score_failing():
    """Zero metrics should give an F grade."""
    score, grade = compute_quality_score(fidelity=0.0, coherence=0.0, promotion_rate=0.0)
    assert score == 0.0
    assert grade == "F"


def test_quality_score_promotion_rate_sweet_spot():
    """Promotion rate of 0.2 should give the best promo score."""
    score_sweet, _ = compute_quality_score(0.5, 0.5, 0.2)
    score_low, _ = compute_quality_score(0.5, 0.5, 0.05)
    score_high, _ = compute_quality_score(0.5, 0.5, 0.5)
    assert score_sweet > score_low
    assert score_sweet > score_high


def test_quality_score_grades():
    """Grade boundaries should be correct."""
    # A: >= 0.8 (perfect fidelity + coherence + sweet spot promo)
    score, grade = compute_quality_score(1.0, 1.0, 0.2)
    assert grade == "A"
    # B: >= 0.7 (good fidelity + coherence + sweet spot promo)
    score, grade = compute_quality_score(0.7, 0.7, 0.2)
    # 0.7*0.4 + 0.7*0.3 + 1.0*0.3 = 0.28+0.21+0.30 = 0.79 → B
    assert grade == "B"
    # C: >= 0.6 (decent fidelity + coherence + sweet spot promo)
    score, grade = compute_quality_score(0.6, 0.6, 0.2)
    # 0.6*0.4 + 0.6*0.3 + 1.0*0.3 = 0.24+0.18+0.30 = 0.72 → B
    # Need lower values for C
    score, grade = compute_quality_score(0.5, 0.5, 0.2)
    # 0.5*0.4 + 0.5*0.3 + 1.0*0.3 = 0.20+0.15+0.30 = 0.65 → C
    assert grade == "C"
    # D: >= 0.5
    score, grade = compute_quality_score(0.4, 0.4, 0.2)
    # 0.4*0.4 + 0.4*0.3 + 1.0*0.3 = 0.16+0.12+0.30 = 0.58 → D
    assert grade == "D"
    # F: < 0.5
    score, grade = compute_quality_score(0.2, 0.2, 0.0)
    # 0.2*0.4 + 0.2*0.3 + 0.0*0.3 = 0.08+0.06+0.00 = 0.14 → F
    assert grade == "F"


# --- measure_consolidation_cycle tests ---

@patch("scripts.consolidation_metrics.get_embedding")
def test_measure_consolidation_cycle_basic(mock_embed):
    """Should compute metrics for a consolidation cycle."""
    # Mock embeddings: return simple vectors
    mock_embed.return_value = [1.0, 0.0, 0.0]

    source_points = [
        ["text about AI", "text about LLMs"],  # cluster 1
        ["text about cooking", "text about recipes"],  # cluster 2
    ]
    distilled_summaries = ["Summary about AI and LLMs", "Summary about cooking"]
    cluster_point_counts = [2, 2]
    promoted_count = 2
    total_working_points = 10

    report = measure_consolidation_cycle(
        source_points, distilled_summaries, cluster_point_counts,
        promoted_count, total_working_points
    )

    assert report.total_working_points == 10
    assert report.total_clusters == 2
    assert report.clusters_processed == 2
    assert report.promoted_count == 2
    assert report.promotion_rate == 0.2
    assert len(report.cluster_details) == 2
    assert report.quality_grade in ("A", "B", "C", "D", "F")


@patch("scripts.consolidation_metrics.get_embedding")
def test_measure_consolidation_cycle_empty(mock_embed):
    """Should handle empty input gracefully."""
    report = measure_consolidation_cycle([], [], [], 0, 0)
    assert report.total_working_points == 0
    assert report.promotion_rate == 0.0
    assert report.avg_fidelity == 0.0
    assert report.avg_cluster_coherence == 0.0


@patch("scripts.consolidation_metrics.get_embedding")
def test_measure_consolidation_cycle_fidelity_calculation(mock_embed):
    """Fidelity should be high when summary matches sources."""
    # Source and summary embeddings are identical → high fidelity
    mock_embed.return_value = [1.0, 0.0, 0.0]

    source_points = [["AI is great", "LLMs are powerful"]]
    distilled_summaries = ["AI and LLMs are great and powerful"]
    cluster_point_counts = [2]
    promoted_count = 1
    total_working_points = 5

    report = measure_consolidation_cycle(
        source_points, distilled_summaries, cluster_point_counts,
        promoted_count, total_working_points
    )

    # Identical embeddings → fidelity = 1.0
    assert report.avg_fidelity == 1.0


@patch("scripts.consolidation_metrics.get_embedding")
def test_measure_consolidation_cycle_coherence_single_item(mock_embed):
    """A single-item cluster should have coherence 1.0."""
    mock_embed.return_value = [1.0, 0.0]
    source_points = [["single text"]]
    distilled_summaries = ["summary"]
    cluster_point_counts = [1]
    promoted_count = 1
    total_working_points = 1

    report = measure_consolidation_cycle(
        source_points, distilled_summaries, cluster_point_counts,
        promoted_count, total_working_points
    )
    # Single item clusters are trivially coherent
    assert report.avg_cluster_coherence == 1.0


# --- save/get metrics tests ---

def test_save_and_get_metrics(tmp_path):
    """Should save and retrieve metrics."""
    report = ConsolidationReport(
        timestamp="2026-01-01T00:00:00",
        total_working_points=100,
        quality_score=0.85,
        quality_grade="A",
    )

    with patch("scripts.consolidation_metrics.METRICS_PATH", tmp_path / "metrics.json"):
        save_metrics(report)
        latest = get_latest_metrics()
        assert latest is not None
        assert latest["quality_score"] == 0.85
        assert latest["quality_grade"] == "A"


def test_get_latest_metrics_no_file():
    """Should return None if no metrics file exists."""
    with patch("scripts.consolidation_metrics.METRICS_PATH", Path("/nonexistent/metrics.json")):
        assert get_latest_metrics() is None
