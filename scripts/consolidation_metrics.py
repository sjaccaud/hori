"""
Memory Consolidation Quality Metrics.

Measures whether the memory system is actually compounding knowledge
or just pretending to. Three metrics:

1. Consolidation Fidelity — how much information is preserved vs lost
   in distillation. Measures the semantic overlap between source points
   and the distilled summary using embedding cosine similarity.

2. Promotion Rate — the ratio of working memory points promoted to
   project/longterm tiers vs. archived. A healthy system promotes
   durable insights, not every conversation turn.

3. Cluster Coherence — how semantically coherent each cluster is.
   Low coherence means the clustering is grouping unrelated items,
   which produces poor distillations.

Usage:
    from scripts.consolidation_metrics import ConsolidationMetrics
    metrics = ConsolidationMetrics()
    report = metrics.measure_consolidation_cycle(
        source_points, distilled_summary, promoted_count, total_clusters
    )

Traces to: STRAT-8, PoC 8.3, PoC 3.3, Manifesto Pillar III.
"""
import json
import logging
import math
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

EMBED_URL = os.getenv("EMBED_URL", "http://localhost:8081/v1/embeddings")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text-v1.5.Q8_0")

METRICS_PATH = Path(__file__).resolve().parent.parent / "core" / "state" / "consolidation_metrics.json"


@dataclass
class ConsolidationReport:
    """Quality metrics for a single consolidation cycle."""
    timestamp: str = ""
    # Input stats
    total_working_points: int = 0
    total_clusters: int = 0
    clusters_processed: int = 0
    # Promotion stats
    promoted_count: int = 0
    archived_count: int = 0
    promotion_rate: float = 0.0  # promoted / total_working
    # Fidelity stats (average across clusters)
    avg_fidelity: float = 0.0  # 0-1, semantic overlap source→summary
    min_fidelity: float = 0.0
    max_fidelity: float = 0.0
    # Coherence stats
    avg_cluster_coherence: float = 0.0  # 0-1, how tight each cluster is
    # Per-cluster details
    cluster_details: List[Dict[str, Any]] = field(default_factory=list)
    # Overall quality score (weighted average)
    quality_score: float = 0.0
    quality_grade: str = ""  # A/B/C/D/F


def get_embedding(text: str) -> List[float]:
    """Get embedding vector for a text string."""
    payload = {"model": EMBED_MODEL, "input": text}
    response = requests.post(EMBED_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def measure_fidelity(source_texts: List[str], summary: str) -> float:
    """Measure how much of the source information is preserved in the summary.

    Uses embedding cosine similarity between each source point and the summary.
    The fidelity score is the average similarity — high means the summary
    captures the source content, low means information was lost.

    Returns a 0-1 score.
    """
    if not source_texts or not summary:
        return 0.0

    try:
        summary_emb = get_embedding(summary)
    except Exception as e:
        logger.warning(f"Failed to embed summary: {e}")
        return 0.0

    similarities = []
    for text in source_texts:
        try:
            source_emb = get_embedding(text)
            sim = cosine_similarity(source_emb, summary_emb)
            similarities.append(sim)
        except Exception as e:
            logger.warning(f"Failed to embed source text: {e}")
            continue

    if not similarities:
        return 0.0

    return sum(similarities) / len(similarities)


def measure_cluster_coherence(source_texts: List[str]) -> float:
    """Measure how semantically coherent a cluster is.

    Computes the average pairwise cosine similarity between all source texts.
    High coherence means the cluster is about one topic; low coherence means
    it's a grab bag of unrelated items.

    Returns a 0-1 score.
    """
    if len(source_texts) < 2:
        return 1.0  # single item is trivially coherent

    # Get embeddings for all texts
    embeddings = []
    for text in source_texts:
        try:
            emb = get_embedding(text)
            embeddings.append(emb)
        except Exception as e:
            logger.warning(f"Failed to embed text for coherence: {e}")
            continue

    if len(embeddings) < 2:
        return 0.0

    # Average pairwise similarity
    similarities = []
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            sim = cosine_similarity(embeddings[i], embeddings[j])
            similarities.append(sim)

    return sum(similarities) / len(similarities) if similarities else 0.0


def compute_quality_score(fidelity: float, coherence: float, promotion_rate: float) -> tuple:
    """Compute an overall quality score and grade.

    Weights:
    - Fidelity: 40% (most important — are we losing information?)
    - Coherence: 30% (are clusters meaningful?)
    - Promotion rate: 30% (are we promoting the right amount?)

    Promotion rate has a sweet spot: too low means we're not compounding
    knowledge, too high means we're promoting noise. The ideal is 0.1-0.3.
    """
    # Promotion rate score: peaks at 0.2, declines on either side
    if promotion_rate <= 0.2:
        promo_score = promotion_rate / 0.2  # 0→0, 0.2→1.0
    else:
        promo_score = max(0.0, 1.0 - (promotion_rate - 0.2) * 2)  # 0.2→1.0, 0.7→0.0

    score = (fidelity * 0.40 + coherence * 0.30 + promo_score * 0.30)

    if score >= 0.8:
        grade = "A"
    elif score >= 0.7:
        grade = "B"
    elif score >= 0.6:
        grade = "C"
    elif score >= 0.5:
        grade = "D"
    else:
        grade = "F"

    return round(score, 3), grade


def measure_consolidation_cycle(
    source_points: List[Any],
    distilled_summaries: List[str],
    cluster_point_counts: List[int],
    promoted_count: int,
    total_working_points: int,
) -> ConsolidationReport:
    """Measure quality metrics for a consolidation cycle.

    Args:
        source_points: List of source points per cluster (list of lists of text)
        distilled_summaries: List of distilled summaries (one per cluster)
        cluster_point_counts: Number of points in each cluster
        promoted_count: How many clusters were promoted
        total_working_points: Total working memory points before consolidation

    Returns:
        ConsolidationReport with all metrics
    """
    report = ConsolidationReport()
    report.timestamp = datetime.now().isoformat()
    report.total_working_points = total_working_points
    report.total_clusters = len(cluster_point_counts)
    report.clusters_processed = len(distilled_summaries)
    report.promoted_count = promoted_count
    report.archived_count = total_working_points - promoted_count
    report.promotion_rate = promoted_count / total_working_points if total_working_points > 0 else 0.0

    fidelities = []
    coherences = []

    for i, (sources, summary) in enumerate(zip(source_points, distilled_summaries)):
        # Extract text from source points (could be strings or objects with .payload)
        source_texts = []
        for s in sources:
            if isinstance(s, str):
                source_texts.append(s)
            elif hasattr(s, "payload"):
                source_texts.append(s.payload.get("text", ""))
            elif isinstance(s, dict):
                source_texts.append(s.get("text", ""))

        fidelity = measure_fidelity(source_texts, summary)
        coherence = measure_cluster_coherence(source_texts)

        fidelities.append(fidelity)
        coherences.append(coherence)

        report.cluster_details.append({
            "cluster_index": i,
            "point_count": len(sources),
            "fidelity": round(fidelity, 3),
            "coherence": round(coherence, 3),
            "summary_preview": summary[:100],
        })

    if fidelities:
        report.avg_fidelity = round(sum(fidelities) / len(fidelities), 3)
        report.min_fidelity = round(min(fidelities), 3)
        report.max_fidelity = round(max(fidelities), 3)
    if coherences:
        report.avg_cluster_coherence = round(sum(coherences) / len(coherences), 3)

    report.quality_score, report.quality_grade = compute_quality_score(
        report.avg_fidelity, report.avg_cluster_coherence, report.promotion_rate
    )

    return report


def save_metrics(report: ConsolidationReport):
    """Save metrics to the metrics file (appends to history)."""
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)

    history = []
    if METRICS_PATH.exists():
        try:
            history = json.loads(METRICS_PATH.read_text())
        except Exception:
            history = []

    history.append(asdict(report))
    # Keep last 90 runs (~3 months of nightly cycles)
    history = history[-90:]

    METRICS_PATH.write_text(json.dumps(history, indent=2))
    logger.info(f"Consolidation metrics saved to {METRICS_PATH}")


def get_latest_metrics() -> Optional[Dict[str, Any]]:
    """Get the most recent consolidation metrics."""
    if not METRICS_PATH.exists():
        return None
    try:
        history = json.loads(METRICS_PATH.read_text())
        return history[-1] if history else None
    except Exception:
        return None
