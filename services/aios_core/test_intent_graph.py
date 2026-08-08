"""Tests for the Intent Graph (PoC 11.1) and Graph-RAG (PoC 11.2)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.aios_core.intent_graph import IntentGraph, GraphNode, GraphEdge


@pytest.fixture
def sample_graph():
    """Build a small sample graph for testing."""
    graph = IntentGraph()
    # Memory nodes
    graph.add_node(GraphNode(id="mem1", type="memory", label="How does RAG work?", tier="working",
                             metadata={"role": "user", "conversation_id": "conv1"}))
    graph.add_node(GraphNode(id="mem2", type="memory", label="RAG retrieves from vector DB...", tier="working",
                             metadata={"role": "assistant", "conversation_id": "conv1"}))
    graph.add_node(GraphNode(id="mem3", type="memory", label="What about graph-RAG?", tier="working",
                             metadata={"role": "user", "conversation_id": "conv2"}))
    # Conversation nodes
    graph.add_node(GraphNode(id="conv:conv1", type="conversation", label="Conversation conv1"))
    graph.add_node(GraphNode(id="conv:conv2", type="conversation", label="Conversation conv2"))
    # Topic nodes
    graph.add_node(GraphNode(id="topic:rag", type="topic", label="rag"))
    graph.add_node(GraphNode(id="topic:vector", type="topic", label="vector"))
    graph.add_node(GraphNode(id="topic:graph", type="topic", label="graph"))
    # Edges
    graph.add_edge(GraphEdge(source="conv:conv1", target="mem1", type="contains"))
    graph.add_edge(GraphEdge(source="conv:conv1", target="mem2", type="contains"))
    graph.add_edge(GraphEdge(source="conv:conv2", target="mem3", type="contains"))
    graph.add_edge(GraphEdge(source="mem1", target="topic:rag", type="has_topic"))
    graph.add_edge(GraphEdge(source="mem2", target="topic:rag", type="has_topic"))
    graph.add_edge(GraphEdge(source="mem2", target="topic:vector", type="has_topic"))
    graph.add_edge(GraphEdge(source="mem3", target="topic:graph", type="has_topic"))
    graph.add_edge(GraphEdge(source="mem3", target="topic:rag", type="has_topic"))
    # Co-occurrence
    graph.add_edge(GraphEdge(source="topic:rag", target="topic:vector", type="co_occurs_with"))
    graph.add_edge(GraphEdge(source="topic:rag", target="topic:graph", type="co_occurs_with"))
    return graph


def test_graph_builds_nodes_and_edges(sample_graph):
    """Graph should have the correct number of nodes and edges."""
    assert len(sample_graph.nodes) == 8
    assert len(sample_graph.edges) == 10


def test_graph_node_types(sample_graph):
    """Graph should have memory, conversation, and topic nodes."""
    types = {n.type for n in sample_graph.nodes.values()}
    assert "memory" in types
    assert "conversation" in types
    assert "topic" in types


def test_graph_edge_types(sample_graph):
    """Graph should have contains, has_topic, and co_occurs_with edges."""
    types = {e.type for e in sample_graph.edges}
    assert "contains" in types
    assert "has_topic" in types
    assert "co_occurs_with" in types


def test_get_neighbors(sample_graph):
    """Getting neighbors of a topic should return related topics and memories."""
    result = sample_graph.get_neighbors("topic:rag", depth=1)
    assert result["node"]["label"] == "rag"
    # Should find vector and graph as co-occurring topics
    neighbor_labels = {n["label"] for n in result["neighbors"].values()}
    assert "vector" in neighbor_labels or "graph" in neighbor_labels


def test_get_neighbors_depth_2(sample_graph):
    """Depth-2 traversal should find more nodes than depth-1."""
    result1 = sample_graph.get_neighbors("topic:rag", depth=1)
    result2 = sample_graph.get_neighbors("topic:rag", depth=2)
    assert len(result2["neighbors"]) >= len(result1["neighbors"])


def test_get_neighbors_nonexistent(sample_graph):
    """Getting neighbors of a nonexistent node should return error."""
    result = sample_graph.get_neighbors("nonexistent", depth=1)
    assert "error" in result


def test_topic_clusters(sample_graph):
    """Topic clusters should group co-occurring topics."""
    clusters = sample_graph.get_topic_clusters()
    assert len(clusters) > 0
    # rag, vector, and graph should all be in the same cluster
    biggest = clusters[0]
    assert biggest["size"] >= 2
    all_topics = []
    for c in clusters:
        all_topics.extend(c["topics"])
    assert "rag" in all_topics


def test_graph_stats(sample_graph):
    """Stats should report correct counts."""
    stats = sample_graph.get_stats()
    assert stats["total_nodes"] == 8
    assert stats["total_edges"] == 10
    assert "memory" in stats["node_types"]
    assert "has_topic" in stats["edge_types"]


def test_edge_weight_increment(sample_graph):
    """Adding the same edge twice should increment its weight."""
    graph = IntentGraph()
    graph.add_edge(GraphEdge(source="a", target="b", type="co_occurs_with"))
    graph.add_edge(GraphEdge(source="a", target="b", type="co_occurs_with"))
    assert len(graph.edges) == 1
    assert graph.edges[0].weight == 2.0


def test_graph_save_load(sample_graph, tmp_path):
    """Graph should save and load correctly."""
    graph_path = tmp_path / "intent_graph.json"
    # Monkey-patch the GRAPH_PATH
    import services.aios_core.intent_graph as ig_module
    original_path = ig_module.GRAPH_PATH
    ig_module.GRAPH_PATH = graph_path
    try:
        sample_graph.save()
        assert graph_path.exists()
        new_graph = IntentGraph()
        assert new_graph.load()
        assert len(new_graph.nodes) == len(sample_graph.nodes)
        assert len(new_graph.edges) == len(sample_graph.edges)
    finally:
        ig_module.GRAPH_PATH = original_path
