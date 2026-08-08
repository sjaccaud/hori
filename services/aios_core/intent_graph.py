"""
Intent Graph (PoC 11.1)

Turns the edges[] in memory payloads into a real knowledge graph.
The graph connects:
- Conversations to topics (what was discussed)
- Topics to each other (co-occurrence)
- Consolidated insights to their source turns (distilled_from edges)
- Incidents to services and their resolutions
- Work orders to their source opportunities

This enables Graph-RAG retrieval: instead of just semantic similarity,
AIOS can traverse the graph to find related concepts, trace how an idea
evolved, or recall the full context behind a distilled insight.

Storage: core/state/intent_graph.json (JSON adjacency list)
API: GET /system/graph - returns the graph
     GET /system/graph/node/{id} - returns a node and its neighbors

Usage:
    PYTHONPATH=. ./venv/bin/python3 -m services.aios_core.intent_graph  # rebuild
    PYTHONPATH=. ./venv/bin/python3 -m services.aios_core.intent_graph --stats  # stats only
"""
import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from qdrant_client import QdrantClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = PROJECT_ROOT / "core" / "state" / "intent_graph.json"

QDRANT_URL = "http://localhost:6333"
COLLECTIONS = ["aios_working", "aios_project", "aios_longterm"]


@dataclass
class GraphNode:
    id: str
    type: str  # "conversation", "topic", "insight", "incident", "work_order", "memory"
    label: str
    tier: str = ""
    metadata: Dict = field(default_factory=dict)


@dataclass
class GraphEdge:
    source: str
    target: str
    type: str  # "distilled_from", "has_topic", "co_occurs_with", "relates_to", "caused_by"
    weight: float = 1.0


class IntentGraph:
    """Knowledge graph built from Qdrant memory payloads."""

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: List[GraphEdge] = []
        self._edge_set: Set[tuple] = set()  # dedup

    def add_node(self, node: GraphNode):
        if node.id not in self.nodes:
            self.nodes[node.id] = node
        else:
            # Merge metadata
            self.nodes[node.id].metadata.update(node.metadata)

    def add_edge(self, edge: GraphEdge):
        key = (edge.source, edge.target, edge.type)
        if key not in self._edge_set:
            self._edge_set.add(key)
            self.edges.append(edge)
        else:
            # Increment weight if edge already exists
            for e in self.edges:
                if (e.source, e.target, e.type) == key:
                    e.weight += 1
                    break

    def build_from_qdrant(self):
        """Scan all Qdrant points and build the graph from edges + topics."""
        client = QdrantClient(url=QDRANT_URL)
        total_points = 0

        for collection in COLLECTIONS:
            tier = collection.replace("aios_", "")
            offset = None
            while True:
                result = client.scroll(
                    collection_name=collection,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                points, next_offset = result
                if not points:
                    break

                for point in points:
                    total_points += 1
                    pid = str(point.id)
                    payload = point.payload or {}

                    # Add memory node
                    role = payload.get("role", "unknown")
                    content = payload.get("content", "")[:80]
                    conv_id = payload.get("conversation_id", "")

                    self.add_node(GraphNode(
                        id=pid,
                        type="memory",
                        label=content,
                        tier=tier,
                        metadata={
                            "role": role,
                            "conversation_id": conv_id,
                            "surface": payload.get("surface", ""),
                        },
                    ))

                    # Add conversation node and link
                    if conv_id:
                        conv_node_id = f"conv:{conv_id}"
                        self.add_node(GraphNode(
                            id=conv_node_id,
                            type="conversation",
                            label=f"Conversation {conv_id[:8]}",
                            metadata={"tier": tier},
                        ))
                        self.add_edge(GraphEdge(
                            source=conv_node_id,
                            target=pid,
                            type="contains",
                        ))

                    # Add topic nodes and link
                    topics = payload.get("topics", [])
                    for topic in topics:
                        topic_id = f"topic:{topic.lower()}"
                        self.add_node(GraphNode(
                            id=topic_id,
                            type="topic",
                            label=topic,
                        ))
                        self.add_edge(GraphEdge(
                            source=pid,
                            target=topic_id,
                            type="has_topic",
                        ))

                        # Co-occurrence: topics that appear together are related
                        for other_topic in topics:
                            if other_topic != topic:
                                other_id = f"topic:{other_topic.lower()}"
                                self.add_edge(GraphEdge(
                                    source=topic_id,
                                    target=other_id,
                                    type="co_occurs_with",
                                ))

                    # Process explicit edges from payload
                    edges = payload.get("edges", [])
                    for edge in edges:
                        target = edge.get("target", "")
                        etype = edge.get("type", "relates_to")
                        if target:
                            self.add_edge(GraphEdge(
                                source=pid,
                                target=target,
                                type=etype,
                            ))

                offset = next_offset
                if not next_offset:
                    break

        logger.info(f"Built graph: {len(self.nodes)} nodes, {len(self.edges)} edges from {total_points} points")

    def get_neighbors(self, node_id: str, depth: int = 1) -> Dict:
        """Get a node and its neighbors up to depth N."""
        if node_id not in self.nodes:
            return {"error": "Node not found"}

        visited = set()
        result_nodes = {}
        result_edges = []

        def traverse(nid: str, d: int):
            if d < 0 or nid in visited:
                return
            visited.add(nid)
            if nid in self.nodes:
                result_nodes[nid] = asdict(self.nodes[nid])

            for edge in self.edges:
                if edge.source == nid:
                    result_edges.append(asdict(edge))
                    if edge.target not in visited:
                        traverse(edge.target, d - 1)
                elif edge.target == nid:
                    result_edges.append(asdict(edge))
                    if edge.source not in visited:
                        traverse(edge.source, d - 1)

        traverse(node_id, depth)
        return {"node": asdict(self.nodes[node_id]), "neighbors": result_nodes, "edges": result_edges}

    def get_topic_clusters(self) -> List[Dict]:
        """Find clusters of co-occurring topics."""
        topic_edges = [e for e in self.edges if e.type == "co_occurs_with"]
        adjacency = defaultdict(list)
        for e in topic_edges:
            adjacency[e.source].append(e.target)
            adjacency[e.target].append(e.source)  # make it undirected

        visited = set()
        clusters = []
        for topic_id in list(adjacency.keys()):
            if topic_id in visited:
                continue
            cluster = set()
            queue = [topic_id]
            while queue:
                current = queue.pop()
                if current in visited:
                    continue
                visited.add(current)
                cluster.add(current)
                for neighbor in adjacency[current]:
                    if neighbor not in visited:
                        queue.append(neighbor)
            if len(cluster) > 1:
                clusters.append({
                    "topics": [self.nodes[t].label if t in self.nodes else t for t in cluster],
                    "size": len(cluster),
                })

        clusters.sort(key=lambda c: c["size"], reverse=True)
        return clusters

    def get_stats(self) -> Dict:
        """Return graph statistics."""
        type_counts = defaultdict(int)
        for node in self.nodes.values():
            type_counts[node.type] += 1

        edge_type_counts = defaultdict(int)
        for edge in self.edges:
            edge_type_counts[edge.type] += 1

        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "node_types": dict(type_counts),
            "edge_types": dict(edge_type_counts),
            "topic_clusters": len(self.get_topic_clusters()),
        }

    def save(self):
        """Save graph to JSON."""
        GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "built_at": datetime.now().isoformat(),
            "stats": self.get_stats(),
            "nodes": {nid: asdict(n) for nid, n in self.nodes.items()},
            "edges": [asdict(e) for e in self.edges],
        }
        GRAPH_PATH.write_text(json.dumps(data, indent=2))
        logger.info(f"Graph saved to {GRAPH_PATH}")

    def load(self) -> bool:
        """Load graph from JSON."""
        if not GRAPH_PATH.exists():
            return False
        data = json.loads(GRAPH_PATH.read_text())
        self.nodes = {nid: GraphNode(**n) for nid, n in data.get("nodes", {}).items()}
        self.edges = [GraphEdge(**e) for e in data.get("edges", [])]
        self._edge_set = {(e.source, e.target, e.type) for e in self.edges}
        logger.info(f"Loaded graph: {len(self.nodes)} nodes, {len(self.edges)} edges")
        return True


def build_graph():
    """Build and save the intent graph from Qdrant."""
    graph = IntentGraph()
    graph.build_from_qdrant()
    graph.save()

    stats = graph.get_stats()
    print(f"\nGraph Stats:")
    print(f"  Nodes: {stats['total_nodes']}")
    print(f"  Edges: {stats['total_edges']}")
    print(f"  Node types: {stats['node_types']}")
    print(f"  Edge types: {stats['edge_types']}")
    print(f"  Topic clusters: {stats['topic_clusters']}")

    clusters = graph.get_topic_clusters()[:5]
    if clusters:
        print(f"\n  Top topic clusters:")
        for c in clusters:
            print(f"    [{c['size']}] {', '.join(c['topics'][:5])}")

    return graph


if __name__ == "__main__":
    import sys
    if "--stats" in sys.argv:
        graph = IntentGraph()
        if graph.load():
            stats = graph.get_stats()
            print(json.dumps(stats, indent=2))
        else:
            print("No graph found. Run without --stats to build one.")
    else:
        build_graph()
