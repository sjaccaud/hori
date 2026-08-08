"""
10,000-Turn Entropy & Context Drift Stress Test for AIOS Core

Headless E2E conversation simulator that drives 100/1K/10K turn
CONVERSATIONS (not independent requests) through the aios-core
/v1/chat/completions endpoint. Measures entropy, repetition, context
drift, memory recall fidelity, and resource usage to flush gremlins
from the system.

This is the "Crucible" — it proves the 10,000th turn is just as smart
as the first. The core promise: memory + state files carry distilled
knowledge across turns so the LLM doesn't need raw context history.

WHY THIS IS SEPARATE FROM test_safety_stress.py:
  This test chains turns into a continuous conversation (each turn
  sends prior responses as history). It uses planning/strategy prompts
  that build on each other, requiring multi-turn context. It runs for
  1K-10K turns (hours) to trigger memory consolidation.

  The safety stress test uses stateless independent turns with
  adversarial prompts to exercise the hallucination interception rate
  and Sherpa behavioral guardian. Mixing the two would break both:
  chained turns muddy safety metrics, and stateless turns can't test
  context drift.

Usage:
    # Quick 10-turn smoke test
    python3 tests/stress/test_ten_thousand_turns.py --turns 10

    # 100-turn test (default)
    python3 tests/stress/test_ten_thousand_turns.py --turns 100

    # Full 1000-turn stress test
    python3 tests/stress/test_ten_thousand_turns.py --turns 1000

    # 10K turns (long run, ~2-3 hours)
    python3 tests/stress/test_ten_thousand_turns.py --turns 10000

    # Custom endpoint
    python3 tests/stress/test_ten_thousand_turns.py --url http://localhost:5680/v1/chat/completions

Safety:
    - Rate limited (default 1s between turns) to avoid overheating
    - VRAM monitored, auto-pause at 90%
    - All results written to tests/stress/results/ for analysis
    - Can be Ctrl+C'd at any point, partial results are saved
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

# --- Config ---

DEFAULT_URL = os.getenv("AIOS_STRESS_URL", "http://localhost:5680/v1/chat/completions")
DEFAULT_TURN_DELAY = float(os.getenv("AIOS_STRESS_DELAY", "1.0"))  # seconds between turns
VRAM_PAUSE_THRESHOLD = 90  # pause if VRAM > this %
VRAM_RESUME_THRESHOLD = 80  # resume when VRAM drops below this
MAX_RESPONSE_CHARS = 500  # cap response length to keep things manageable
# Max conversation history sent per turn. This simulates a realistic UI
# scrollback buffer — a human's chat UI shows ~10-20 recent turns, not 6.
# The server then does elastic retrieval for older context (when --elastic
# is set). See docs/elastic_context_window.md "The human behaviour model".
MAX_HISTORY_TURNS = 10
RESULTS_DIR = Path(__file__).parent / "results"

# Memory system instrumentation endpoints
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
STATE_DIR = Path(__file__).resolve().parents[2] / "core" / "state"

# --- Conversation Prompts ---

# Prompts are organized into phases that simulate a real long-form
# planning session. Each phase builds on the previous one, requiring
# the LLM to recall and connect earlier discussion points. This is
# what tests context drift — if the LLM forgets what was said 50
# turns ago, the planning becomes incoherent.
#
# Multiple themes are supported via PROMPT_THEMES. The default "aios"
# theme tests AIOS project planning. The "business" theme tests
# small-business exploration (a lemonade stand / food stand) — same
# drift-detection structure, different subject matter. This lets us
# test context drift on topics the model hasn't been fine-tuned on
# (no AIOS-specific system prompt bias).

PROMPT_THEMES = {
    "aios": {
        "opening": [
            "Let's plan the next phase of AIOS. What should we prioritize?",
            "I want to think strategically about where this project is going. What's the big picture?",
            "Before we dive into details, what's the current state of the system?",
            "Let's take stock. What have we built so far and what's left to do?",
            "I've been thinking about the roadmap. What do you think the next milestone should be?",
        ],
        "factual": [
            "What is AIOS about?",
            "What's the current project state?",
            "What model are you running on?",
            "How much VRAM do we have?",
            "What's the difference between turbo4 and f16 KV cache?",
            "Explain how RAG works in 3 sentences.",
            "What are the Qdrant memory tiers?",
            "How does the red-team gate work?",
            "What is the Spiritbuun fork?",
            "What's our Tailscale IP?",
        ],
        "context_reference": [
            "Tell me more about that.",
            "Can you elaborate on the last point?",
            "How does that relate to what we discussed earlier?",
            "Wait, you said something about that before - what was it?",
            "Connect that to the memory system.",
            "How does that fit into the roadmap?",
            "What did we decide about that last time?",
            "Go back to what we were discussing earlier - does that still apply?",
            "You mentioned something about that before. Does it change things now?",
        ],
        "planning": [
            "Given what we've discussed, what are the top 3 risks?",
            "If we had to ship something next week, what would it be?",
            "What's the dependency chain? What needs to happen first?",
            "Let's break that down into concrete steps.",
            "What could go wrong with that approach?",
            "How would we measure success for that?",
            "What's the minimum viable version of that?",
            "Let's reconsider. Is there a simpler approach?",
            "What did we decide in our earlier planning? Let me make sure I have it right.",
            "How does this connect to what we said at the start of this session?",
            "Let's revisit our priorities. Has anything changed based on what we've discussed?",
            "What's the critical path? What blocks everything else?",
        ],
        "coding": [
            "Write a Python function to check if a number is prime.",
            "Show me a simple FastAPI endpoint.",
            "How do I read a JSON file in Python?",
            "What's the difference between async and sync HTTP calls?",
            "Write a regex to match email addresses.",
            "How do I handle exceptions in async code?",
            "Show me a Docker compose snippet for a database.",
        ],
        "creative": [
            "Write a haiku about local AI.",
            "Give me a name for a new project that combines AI and music.",
            "What if AIOS could dream? Describe a dream.",
            "Write a short story about a self-healing system.",
            "Invent a new feature for AIOS and describe it.",
        ],
        "meta": [
            "Are you working correctly?",
            "Do you remember what we talked about?",
            "How many turns have we done so far?",
            "Is your memory functioning?",
            "What was the first thing I asked you?",
            "What have we covered in this session so far?",
            "Summarize the key decisions we've made.",
        ],
    },
    "business": {
        "opening": [
            "I'm thinking about starting a small business. Where should I begin?",
            "I want to explore what kind of business I could start. Help me think through the options.",
            "Before we get into specifics, what are the first things someone should consider when starting a business?",
            "Let's take stock of my situation. I have some savings and free time. What's the best way to use them?",
            "I've been thinking about turning a hobby into income. What do I need to figure out first?",
        ],
        "factual": [
            "What's a business plan and why do I need one?",
            "What's the difference between an LLC and a sole proprietorship?",
            "How do I calculate gross margin?",
            "What's the average profit margin for a food and beverage business?",
            "Explain break-even analysis in 3 sentences.",
            "What are the main startup costs for a small food stand?",
            "How does sales tax work for a small business?",
            "What's the difference between fixed costs and variable costs?",
            "What is a marketing funnel?",
            "How do I price a product to be competitive but profitable?",
        ],
        "context_reference": [
            "Tell me more about that.",
            "Can you elaborate on the last point?",
            "How does that relate to what we discussed earlier?",
            "Wait, you said something about that before - what was it?",
            "Connect that to our business plan.",
            "How does that fit into our overall strategy?",
            "What did we decide about that last time?",
            "Go back to what we were discussing earlier - does that still apply?",
            "You mentioned something about that before. Does it change things now?",
        ],
        "planning": [
            "Given what we've discussed, what are the top 3 risks for this business?",
            "If we had to launch next week, what would we offer first?",
            "What's the dependency chain? What needs to happen before we can open?",
            "Let's break that down into concrete steps.",
            "What could go wrong with that approach?",
            "How would we measure success for this business?",
            "What's the minimum viable version of this business we could test?",
            "Let's reconsider. Is there a simpler way to start?",
            "What did we decide in our earlier planning? Let me make sure I have it right.",
            "How does this connect to what we said at the start of this session?",
            "Let's revisit our priorities. Has anything changed based on what we've discussed?",
            "What's the critical path? What blocks everything else?",
        ],
        "coding": [
            "Write a Python function to calculate profit given revenue and costs.",
            "Show me a simple spreadsheet formula for tracking daily sales.",
            "Write a Python function to calculate break-even point.",
            "How do I create a simple HTML page for a business landing page?",
            "Write a regex to extract prices from a text file.",
            "Show me a SQL query to find my top-selling products.",
            "Write a Python function to calculate compound monthly growth rate.",
        ],
        "creative": [
            "Write a catchy tagline for a lemonade stand.",
            "Give me a name for a small food business that sounds fresh and local.",
            "What if our business had a mascot? Describe it.",
            "Write a short story about a lemonade stand that became a community hub.",
            "Invent a new drink recipe and describe how it would sell.",
        ],
        "meta": [
            "Are you working correctly?",
            "Do you remember what we talked about?",
            "How many turns have we done so far?",
            "Is your memory functioning?",
            "What was the first thing I asked you?",
            "What have we covered in this session so far?",
            "Summarize the key decisions we've made.",
        ],
    },
}

# Backwards compat: PROMPT_PHASES is the default (aios) theme.
PROMPT_PHASES = PROMPT_THEMES["aios"]


def generate_prompts(n: int, theme: str = "aios") -> list[dict]:
    """Generate n prompts organized as a realistic planning session.

    The prompt sequence simulates a long-form strategy conversation:
    it opens with planning, moves through factual/context phases,
    interleaves planning and coding, and periodically checks meta-cognition
    (does the LLM remember what was discussed?). The context_reference
    and meta prompts are the key drift detectors — they only make sense
    if the conversation is chained.

    The `theme` selects the prompt set ("aios" for AIOS project planning,
    "business" for small-business exploration). The category structure
    and weights are identical across themes — only the prompt content
    changes.
    """
    phases = PROMPT_THEMES.get(theme, PROMPT_THEMES["aios"])
    prompts = []
    # Weight: heavy on planning and context_reference (the core of the
    # drift test), with factual and coding interspersed for variety.
    weights = {
        "opening": 3,       # only at the start
        "factual": 15,
        "context_reference": 25,  # high — this is the drift detector
        "planning": 30,     # highest — the core of the strategy session
        "coding": 10,
        "creative": 5,
        "meta": 12,         # periodic memory checks
    }
    weighted_pool = []
    for cat, weight in weights.items():
        weighted_pool.extend([cat] * weight)

    # First few turns are always "opening" to set up the conversation
    opening_prompts = phases["opening"]
    n_opening = min(len(opening_prompts), max(3, n // 20))
    for i in range(n_opening):
        prompts.append({"turn": i + 1, "category": "opening", "prompt": opening_prompts[i]})

    # Remaining turns cycle through the weighted pool
    for i in range(n - n_opening):
        cat = weighted_pool[i % len(weighted_pool)]
        prompt_list = phases[cat]
        prompt = prompt_list[i % len(prompt_list)]
        prompts.append({"turn": n_opening + i + 1, "category": cat, "prompt": prompt})
    return prompts


# --- Metrics ---


@dataclass
class TurnResult:
    turn: int
    category: str
    prompt: str
    response: str
    response_time_s: float
    response_len: int
    timestamp: str
    vram_used_mb: int = 0
    error: Optional[str] = None
    # Context drift indicators
    history_sent: int = 0  # how many prior turns were included in the request


@dataclass
class StressReport:
    total_turns: int
    completed_turns: int
    failed_turns: int
    total_time_s: float
    avg_response_time_s: float
    median_response_time_s: float
    p95_response_time_s: float
    avg_response_len: float
    # Entropy indicators
    repetition_ratio: float  # fraction of turns with near-identical responses
    unique_responses: int
    topic_drift_score: float  # how much categories vary
    # Context drift indicators
    early_recall_quality: float  # how often early meta/context prompts get coherent responses
    late_recall_quality: float   # same metric for late turns
    recall_degradation: float    # late - early (negative = drift)
    # Resource
    peak_vram_mb: int
    vram_growth_mb: int  # final - initial
    # Per-category breakdown
    category_stats: dict = field(default_factory=dict)
    # Raw turn data (first/last few for inspection)
    first_turns: list = field(default_factory=list)
    last_turns: list = field(default_factory=list)
    # Degradation curve: per-100-turn segment quality scores
    degradation_curve: list = field(default_factory=list)
    # Memory system snapshots at each checkpoint
    memory_snapshots: list = field(default_factory=list)
    # Errors
    errors: list = field(default_factory=list)
    # Prompt theme used for this run
    theme: str = "aios"


@dataclass
class MemorySnapshot:
    """Snapshot of the memory system state at a checkpoint."""
    turn: int
    timestamp: str
    # Qdrant tier point counts
    working_count: int = 0
    project_count: int = 0
    longterm_count: int = 0
    # State file sizes (bytes) — tracks whether the system is learning
    user_model_size: int = 0
    project_state_size: int = 0
    # Per-segment recall quality (for the degradation curve)
    segment_recall_quality: float = 0.0
    segment_repetition: float = 0.0
    segment_avg_response_len: float = 0.0
    # VRAM at checkpoint
    vram_mb: int = 0


# --- Memory System Instrumentation ---


def get_qdrant_tier_counts() -> dict:
    """Get point counts for each Qdrant memory tier."""
    counts = {"working": 0, "project": 0, "longterm": 0}
    for tier, collection in [
        ("working", "aios_working"),
        ("project", "aios_project"),
        ("longterm", "aios_longterm"),
    ]:
        try:
            resp = httpx.get(
                f"{QDRANT_URL}/collections/{collection}",
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            counts[tier] = data.get("result", {}).get("points_count", 0)
        except Exception:
            pass
    return counts


def get_state_file_sizes() -> dict:
    """Get sizes of the state files that track learning."""
    sizes = {"user_model": 0, "project_state": 0}
    for key, filename in [("user_model", "user_model.json"), ("project_state", "project_state.json")]:
        path = STATE_DIR / filename
        if path.exists():
            sizes[key] = path.stat().st_size
    return sizes


def take_memory_snapshot(turn: int, segment_results: list[TurnResult]) -> MemorySnapshot:
    """Take a snapshot of the memory system at a checkpoint.

    Args:
        turn: current turn number
        segment_results: results since the last checkpoint (for per-segment metrics)
    """
    counts = get_qdrant_tier_counts()
    sizes = get_state_file_sizes()

    # Per-segment recall quality (only for context_reference and meta prompts)
    segment_recall = measure_recall_quality(segment_results, 0, len(segment_results))

    # Per-segment repetition
    segment_responses = [r.response for r in segment_results if not r.error]
    segment_rep = detect_repetition(segment_responses)

    # Per-segment avg response length
    segment_lens = [r.response_len for r in segment_results if not r.error]
    segment_avg_len = statistics.mean(segment_lens) if segment_lens else 0

    return MemorySnapshot(
        turn=turn,
        timestamp=datetime.now().isoformat(),
        working_count=counts["working"],
        project_count=counts["project"],
        longterm_count=counts["longterm"],
        user_model_size=sizes["user_model"],
        project_state_size=sizes["project_state"],
        segment_recall_quality=round(segment_recall, 4),
        segment_repetition=round(segment_rep, 4),
        segment_avg_response_len=round(segment_avg_len, 1),
        vram_mb=get_vram_usage_mb(),
    )


# --- VRAM Monitoring ---


def get_vram_usage_mb() -> int:
    """Get current VRAM usage in MB via rocm-smi."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "VRAM Total Used" in line:
                    # Format: "GPU[0]\t\t: VRAM Total Used Memory (B): 16130473984"
                    parts = line.split("(B):")
                    if len(parts) == 2:
                        return int(parts[1].strip()) // (1024 * 1024)
    except Exception:
        pass
    return 0


def get_vram_percent() -> float:
    """Get VRAM usage percentage."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            total = used = 0
            for line in result.stdout.splitlines():
                if "VRAM Total Memory" in line:
                    parts = line.split("(B):")
                    if len(parts) == 2:
                        total = int(parts[1].strip())
                elif "VRAM Total Used" in line:
                    parts = line.split("(B):")
                    if len(parts) == 2:
                        used = int(parts[1].strip())
            if total > 0:
                return (used / total) * 100
    except Exception:
        pass
    return 0


# --- Entropy & Drift Detection ---


def is_near_identical(a: str, b: str, threshold: float = 0.85) -> bool:
    """Check if two strings are near-identical using simple ratio."""
    if not a or not b:
        return False
    # Quick check: if lengths differ by >30%, not identical
    if abs(len(a) - len(b)) / max(len(a), len(b)) > 0.3:
        return False
    # Simple character overlap ratio
    shorter = min(len(a), len(b))
    matches = sum(1 for i in range(shorter) if i < len(a) and i < len(b) and a[i] == b[i])
    return matches / shorter > threshold


def detect_repetition(responses: list[str]) -> float:
    """Calculate what fraction of responses are near-identical to a previous one."""
    if len(responses) < 2:
        return 0.0
    repetitive = 0
    for i in range(1, len(responses)):
        for j in range(max(0, i - 10), i):  # check against last 10
            if is_near_identical(responses[i], responses[j]):
                repetitive += 1
                break
    return repetitive / len(responses)


def detect_hallucination(response: str, prompt: str) -> bool:
    """Heuristic hallucination detection (gibberish/loop detection)."""
    # Repetition within a single response (same phrase 3+ times)
    words = response.lower().split()
    if len(words) > 10:
        word_counts = Counter(words)
        if word_counts.most_common(1)[0][1] > len(words) * 0.3:
            return True
    # Single character repeated (the slash bug)
    if len(set(response.strip())) <= 3 and len(response) > 10:
        return True
    # Response is gibberish (no real words)
    if len(response) > 20 and not re.search(r"[a-zA-Z]{3,}", response):
        return True
    return False


def measure_recall_quality(results: list[TurnResult], start: int, end: int) -> float:
    """Measure how coherent responses are for context_reference and meta prompts
    in the given turn range.

    A "coherent" response is one that references prior context — it's not
    a generic "I don't remember" or "I don't have access to that" deflection.
    This is a heuristic: we check that the response is reasonably long
    (>50 chars) and doesn't contain deflection phrases.

    Returns: fraction of context/meta prompts in [start, end) that got
    coherent responses.
    """
    drift_categories = {"context_reference", "meta"}
    relevant = [r for r in results[start:end] if r.category in drift_categories and not r.error]
    if not relevant:
        return 1.0  # vacuously true if no relevant prompts

    deflection_phrases = [
        # Contracted forms
        "i don't remember",
        "i don't have access",
        "i don't have that information",
        "i don't have context",
        "i don't have any previous",
        "i don't have prior",
        "i don't have memory of",
        "i don't know",
        "i can't recall",
        "i can't see",
        "i can't remember",
        "i can't connect",
        # Uncontracted forms (the LLM uses both interchangeably)
        "i do not remember",
        "i do not have access",
        "i do not have that information",
        "i do not have context",
        "i do not have any previous",
        "i do not have prior",
        "i do not have memory of",
        "i do not know",
        "i cannot recall",
        "i cannot see",
        "i cannot remember",
        "i cannot connect",
        # Other deflection patterns
        "i have no record",
        "i have no memory",
        "i have no context",
        "i have no access",
        "i have no idea",
        "no previous conversation",
        "this is the first",
        "each session is",
        "i don't know what",
        "i do not know what",
    ]

    coherent = 0
    for r in relevant:
        resp_lower = r.response.lower()
        is_deflection = any(p in resp_lower for p in deflection_phrases)
        is_too_short = len(r.response) < 50
        if not is_deflection and not is_too_short:
            coherent += 1
    return coherent / len(relevant)


# --- Main Test Runner ---


def run_stress_test(
    url: str,
    turns: int,
    delay: float,
    max_tokens: int,
    verbose: bool = True,
    consolidate: bool = False,
    elastic: bool = False,
    theme: str = "aios",
) -> StressReport:
    """Run the entropy/drift stress test and return a report.

    Turns are CHAINED — each turn sends the conversation history from
    prior turns (up to MAX_HISTORY_TURNS). This is what makes the
    context_reference and meta prompts meaningful: they test whether
    the LLM can recall what was said earlier in the conversation.

    When `elastic` is set, each request includes `elastic_context: true`
    so aios-core uses the elastic context window (semantic retrieval of
    past turns from Qdrant) instead of the fixed 6-turn window. This is
    what fixes the deflection collapse at turns 443-478 — see
    docs/elastic_context_window.md.
    """

    prompts = generate_prompts(turns, theme=theme)
    results: list[TurnResult] = []
    responses: list[str] = []
    errors: list[dict] = []
    memory_snapshots: list[MemorySnapshot] = []
    segment_start = 0  # track where the current 100-turn segment starts
    # Conversation history: list of {"role": "user"/"assistant", "content": ...}
    history: list[dict] = []
    # Fixed conversation ID for this run — all turns share this ID so
    # the memory consolidation can cluster them as one conversation.
    conv_id = f"stress-entropy-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    initial_vram = get_vram_usage_mb()
    peak_vram = initial_vram
    start_time = time.time()

    # Take initial memory snapshot
    initial_snapshot = take_memory_snapshot(0, [])
    memory_snapshots.append(initial_snapshot)

    if verbose:
        print(f"\n{'='*60}")
        print(f"  AIOS Entropy & Context Drift Stress Test")
        print(f"  Endpoint: {url}")
        print(f"  Turns: {turns} (chained conversation)")
        print(f"  Max history per turn: {MAX_HISTORY_TURNS}")
        print(f"  Delay: {delay}s between turns")
        print(f"  Elastic context: {'ON' if elastic else 'OFF'}")
        print(f"  Consolidate: {'ON' if consolidate else 'OFF'}")
        print(f"  Theme: {theme}")
        print(f"  Initial VRAM: {initial_vram} MB")
        print(f"{'='*60}\n")

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        for i, prompt_data in enumerate(prompts):
            turn = prompt_data["turn"]
            category = prompt_data["category"]
            prompt = prompt_data["prompt"]

            # VRAM safety check
            vram_pct = get_vram_percent()
            if vram_pct > VRAM_PAUSE_THRESHOLD:
                print(f"  [VRAM] {vram_pct:.1f}% usage - pausing 30s...")
                time.sleep(30)
                vram_pct = get_vram_percent()
                if vram_pct > VRAM_PAUSE_THRESHOLD:
                    print(f"  [VRAM] Still at {vram_pct:.1f}% - stopping early")
                    errors.append({
                        "turn": turn,
                        "type": "vram_overflow",
                        "vram_percent": vram_pct,
                    })
                    break

            # Build the request payload with conversation history.
            # We send the last MAX_HISTORY_TURNS of the conversation
            # (user + assistant pairs). aios-core also does its own
            # trimming (_trim_conversation max_messages=6), but we
            # send history explicitly so the LLM has direct context
            # for the context_reference and meta prompts.
            recent_history = history[-(MAX_HISTORY_TURNS * 2):]
            messages = list(recent_history) + [{"role": "user", "content": prompt}]

            # Send request
            turn_start = time.time()
            try:
                payload = {
                    "model": "aios-core",
                    "messages": messages,
                    "stream": False,
                    "max_tokens": max_tokens,
                    # Send a fixed conversation_id so all turns in this
                    # run cluster together in Qdrant. This lets the memory
                    # consolidation distill multi-turn insights instead of
                    # seeing 500 separate 2-point "conversations".
                    "conversation_id": conv_id,
                }
                # Elastic context window: opt in so the server retrieves
                # older turns from this conversation semantically instead
                # of using the fixed 6-turn window. See
                # docs/elastic_context_window.md.
                if elastic:
                    payload["elastic_context"] = True
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                response_text = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                response_text = response_text[:MAX_RESPONSE_CHARS]
                error = None
            except Exception as e:
                response_text = ""
                error = str(e)
                errors.append({"turn": turn, "type": "request_error", "error": str(e)})
                if verbose:
                    print(f"  Turn {turn:>5}/{turns} [{category:>18}] ERROR: {e}")

            turn_time = time.time() - turn_start
            vram_mb = get_vram_usage_mb()
            peak_vram = max(peak_vram, vram_mb)

            result = TurnResult(
                turn=turn,
                category=category,
                prompt=prompt,
                response=response_text,
                response_time_s=round(turn_time, 3),
                response_len=len(response_text),
                timestamp=datetime.now().isoformat(),
                vram_used_mb=vram_mb,
                error=error,
                history_sent=len(recent_history),
            )
            results.append(result)
            responses.append(response_text)

            # Update conversation history for the next turn
            if not error:
                history.append({"role": "user", "content": prompt})
                history.append({"role": "assistant", "content": response_text})

            # Hallucination check (gibberish/loop detection only —
            # action-claim hallucination is tested by test_safety_stress.py)
            is_hallucination = detect_hallucination(response_text, prompt)

            if verbose:
                status = "OK"
                if error:
                    status = "ERR"
                elif is_hallucination:
                    status = "GIBBERISH"
                elif not response_text:
                    status = "EMPTY"

                response_preview = response_text[:60].replace("\n", " ")
                print(
                    f"  Turn {turn:>5}/{turns} [{category:>18}] "
                    f"{turn_time:>6.2f}s | {status:>10} | "
                    f"VRAM {vram_mb:>6}MB | {response_preview}"
                )

                if is_hallucination:
                    errors.append({
                        "turn": turn,
                        "type": "gibberish",
                        "response": response_text[:200],
                    })

            # Save checkpoint and take memory snapshot every 100 turns
            if turn % 100 == 0:
                _save_checkpoint(results, turn, turns)
                segment_results = results[segment_start:turn]
                snapshot = take_memory_snapshot(turn, segment_results)
                memory_snapshots.append(snapshot)
                segment_start = turn
                if verbose:
                    print(f"  [CHECKPOINT] turn {turn}: "
                          f"working={snapshot.working_count} "
                          f"project={snapshot.project_count} "
                          f"longterm={snapshot.longterm_count} "
                          f"recall={snapshot.segment_recall_quality} "
                          f"rep={snapshot.segment_repetition}")

                # Optionally trigger memory consolidation at each checkpoint.
                # This runs the "sleep & dream" cycle: clusters working memory,
                # distills via LLM, promotes to project/longterm, updates state
                # files. This is what makes the "learning and growing with you"
                # promise visible in the degradation curve.
                if consolidate:
                    if verbose:
                        print(f"  [CONSOLIDATE] Triggering memory consolidation...")
                    try:
                        consol_url = url.replace(
                            "/v1/chat/completions", "/admin/api/consolidate"
                        )
                        consol_resp = client.post(
                            consol_url, params={"max_clusters": 5}, timeout=300
                        )
                        if consol_resp.status_code == 200:
                            if verbose:
                                print(f"  [CONSOLIDATE] Done (exit {consol_resp.json().get('exit_code')})")
                        else:
                            if verbose:
                                print(f"  [CONSOLIDATE] HTTP {consol_resp.status_code}")
                    except Exception as e:
                        if verbose:
                            print(f"  [CONSOLIDATE] Failed: {e}")

                    # Take a post-consolidation snapshot to see the effect
                    post_snapshot = take_memory_snapshot(turn, segment_results)
                    if verbose:
                        print(f"  [CONSOLIDATE] Post: "
                              f"working={post_snapshot.working_count} "
                              f"project={post_snapshot.project_count} "
                              f"longterm={post_snapshot.longterm_count}")

            # Rate limit
            if delay > 0 and i < len(prompts) - 1:
                time.sleep(delay)

    total_time = time.time() - start_time
    final_vram = get_vram_usage_mb()

    # Calculate metrics
    completed = [r for r in results if not r.error]
    failed = [r for r in results if r.error]
    response_times = [r.response_time_s for r in completed]
    response_lens = [r.response_len for r in completed]

    # Per-category stats
    category_stats = {}
    for cat in PROMPT_PHASES.keys():
        cat_results = [r for r in completed if r.category == cat]
        if cat_results:
            category_stats[cat] = {
                "count": len(cat_results),
                "avg_time": round(statistics.mean([r.response_time_s for r in cat_results]), 3),
                "avg_len": round(statistics.mean([r.response_len for r in cat_results]), 1),
            }

    # Context drift: compare recall quality early vs late in the conversation.
    # We look at context_reference and meta prompts — these only make sense
    # if the conversation is chained. If the LLM starts deflecting ("I don't
    # remember") more in late turns than early ones, that's context drift.
    n = len(results)
    third = n // 3
    early_recall = measure_recall_quality(results, 0, third)
    late_recall = measure_recall_quality(results, n - third, n)
    recall_degradation = round(late_recall - early_recall, 4)

    # Take final memory snapshot (for the last segment)
    if segment_start < len(results):
        final_segment = results[segment_start:]
        if final_segment:
            final_snapshot = take_memory_snapshot(len(results), final_segment)
            memory_snapshots.append(final_snapshot)

    # Build degradation curve from memory snapshots
    # Each point: {turn, recall_quality, repetition, avg_len, working, project, longterm}
    degradation_curve = [
        {
            "turn": s.turn,
            "recall_quality": s.segment_recall_quality,
            "repetition": s.segment_repetition,
            "avg_response_len": s.segment_avg_response_len,
            "working_count": s.working_count,
            "project_count": s.project_count,
            "longterm_count": s.longterm_count,
            "user_model_size": s.user_model_size,
            "project_state_size": s.project_state_size,
            "vram_mb": s.vram_mb,
        }
        for s in memory_snapshots
    ]

    report = StressReport(
        total_turns=turns,
        completed_turns=len(completed),
        failed_turns=len(failed),
        total_time_s=round(total_time, 1),
        avg_response_time_s=round(statistics.mean(response_times), 3) if response_times else 0,
        median_response_time_s=round(statistics.median(response_times), 3) if response_times else 0,
        p95_response_time_s=round(
            statistics.quantiles(response_times, n=20)[18] if len(response_times) >= 20 else max(response_times) if response_times else 0,
            3,
        ),
        avg_response_len=round(statistics.mean(response_lens), 1) if response_lens else 0,
        repetition_ratio=round(detect_repetition(responses), 4),
        unique_responses=len(set(responses)),
        topic_drift_score=round(len(set(r.category for r in results)) / len(PROMPT_PHASES), 2),
        early_recall_quality=round(early_recall, 4),
        late_recall_quality=round(late_recall, 4),
        recall_degradation=recall_degradation,
        peak_vram_mb=peak_vram,
        vram_growth_mb=final_vram - initial_vram,
        category_stats=category_stats,
        first_turns=[asdict(r) for r in results[:5]],
        last_turns=[asdict(r) for r in results[-5:]],
        degradation_curve=degradation_curve,
        memory_snapshots=[asdict(s) for s in memory_snapshots],
        errors=errors,
        theme=theme,
    )

    return report


def _save_checkpoint(results: list[TurnResult], turn: int, total: int):
    """Save a checkpoint of results so far."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_path = RESULTS_DIR / f"checkpoint_turn_{turn}.json"
    with open(checkpoint_path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)


def print_report(report: StressReport):
    """Print a human-readable summary of the stress test."""
    print(f"\n{'='*60}")
    print(f"  ENTROPY & DRIFT STRESS TEST RESULTS")
    print(f"{'='*60}")
    print(f"  Turns completed:   {report.completed_turns}/{report.total_turns}")
    print(f"  Failed turns:      {report.failed_turns}")
    print(f"  Total time:        {report.total_time_s}s ({report.total_time_s/60:.1f}m)")
    print(f"  Avg response time: {report.avg_response_time_s}s")
    print(f"  Median resp time:  {report.median_response_time_s}s")
    print(f"  P95 resp time:     {report.p95_response_time_s}s")
    print(f"  Avg response len:  {report.avg_response_len} chars")
    print(f"")
    print(f"  --- Entropy Indicators ---")
    print(f"  Repetition ratio:  {report.repetition_ratio} (lower is better)")
    print(f"  Unique responses:  {report.unique_responses}/{report.completed_turns}")
    print(f"  Topic drift:       {report.topic_drift_score} (1.0 = full variety)")
    print(f"")
    print(f"  --- Context Drift ---")
    print(f"  Early recall quality:  {report.early_recall_quality} (first 1/3 of conversation)")
    print(f"  Late recall quality:   {report.late_recall_quality} (last 1/3 of conversation)")
    print(f"  Recall degradation:    {report.recall_degradation} (negative = drift, 0 = stable)")
    print(f"")
    print(f"  --- Degradation Curve (per 100-turn segment) ---")
    if report.degradation_curve:
        print(f"  {'Turn':>6} {'Recall':>8} {'Repetition':>10} {'AvgLen':>8} {'Working':>8} {'Project':>8} {'Longterm':>9} {'UserModel':>10} {'ProjState':>10}")
        for point in report.degradation_curve:
            print(f"  {point['turn']:>6} {point['recall_quality']:>8} {point['repetition']:>10} "
                  f"{point['avg_response_len']:>8.0f} {point['working_count']:>8} "
                  f"{point['project_count']:>8} {point['longterm_count']:>9} "
                  f"{point['user_model_size']:>10} {point['project_state_size']:>10}")
    else:
        print(f"  (no checkpoints — run with --turns 100+ for curve data)")
    print(f"")
    print(f"  --- Resource Usage ---")
    print(f"  Peak VRAM:         {report.peak_vram_mb} MB")
    print(f"  VRAM growth:       {report.vram_growth_mb} MB (final - initial)")
    print(f"")
    print(f"  --- Per-Category ---")
    for cat, stats in sorted(report.category_stats.items()):
        print(f"  {cat:>20}: {stats['count']:>4} turns, avg {stats['avg_time']}s, avg {stats['avg_len']} chars")
    print(f"")
    print(f"  --- Errors ---")
    if report.errors:
        error_types = Counter(e["type"] for e in report.errors)
        for etype, count in error_types.items():
            print(f"  {etype:>20}: {count}")
    else:
        print(f"  No errors detected")
    print(f"")
    print(f"  --- First 3 Turns ---")
    for t in report.first_turns[:3]:
        print(f"  Turn {t['turn']} [{t['category']}]: {t['prompt'][:40]} -> {t['response'][:60]}")
    print(f"")
    print(f"  --- Last 3 Turns ---")
    for t in report.last_turns[-3:]:
        print(f"  Turn {t['turn']} [{t['category']}]: {t['prompt'][:40]} -> {t['response'][:60]}")
    print(f"{'='*60}\n")


def save_report(report: StressReport, turns: int):
    """Save the full report to JSON."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Include theme in filename for non-default themes
    theme_suffix = f"_{report.theme}" if report.theme != "aios" else ""
    filename = RESULTS_DIR / f"entropy_{turns}turns{theme_suffix}_{timestamp}.json"
    with open(filename, "w") as f:
        json.dump(asdict(report), f, indent=2)
    print(f"  Report saved to: {filename}")
    return filename


# --- Theme / Prompt Set Tests ---


def test_business_theme_has_all_categories():
    """The business theme must have all 7 categories with at least 1 prompt each,
    matching the structure of the default (aios) theme. The drift test depends
    on every category being populated — missing categories would skew the
    weighted distribution and break the recall-quality metric."""
    from tests.stress.test_ten_thousand_turns import PROMPT_THEMES
    required = {"opening", "factual", "context_reference", "planning",
                "coding", "creative", "meta"}
    business = PROMPT_THEMES["business"]
    assert set(business.keys()) == required, (
        f"Business theme missing categories: {required - set(business.keys())}"
    )
    for cat, prompts in business.items():
        assert len(prompts) >= 1, f"Category '{cat}' has no prompts"


def test_business_theme_prompts_are_business_flavored():
    """Business theme prompts should be about starting a business, not about
    AIOS. This catches copy-paste errors where an AIOS prompt leaks into the
    business set. We check that no business prompt mentions 'AIOS', 'VRAM',
    'Qdrant', 'llama-server', or 'Safety Spine'."""
    from tests.stress.test_ten_thousand_turns import PROMPT_THEMES
    aios_terms = ["aios", "vram", "qdrant", "llama", "safety spine", "tier 2", "tier 3"]
    for cat, prompts in PROMPT_THEMES["business"].items():
        for p in prompts:
            lower = p.lower()
            for term in aios_terms:
                assert term not in lower, (
                    f"Business prompt in '{cat}' contains AIOS term '{term}': {p}"
                )


def test_generate_prompts_with_business_theme():
    """generate_prompts(n, theme='business') must return n prompts using
    the business theme, with opening prompts first and all categories
    represented over a long enough run."""
    prompts = generate_prompts(100, theme="business")
    assert len(prompts) == 100
    # First turns should be opening
    assert prompts[0]["category"] == "opening"
    # Over 100 turns, all categories should appear
    cats_seen = {p["category"] for p in prompts}
    assert "planning" in cats_seen
    assert "context_reference" in cats_seen
    assert "meta" in cats_seen
    # No AIOS-specific prompts should appear
    for p in prompts:
        assert "AIOS" not in p["prompt"], f"AIOS prompt in business theme: {p['prompt']}"


def test_generate_prompts_default_theme_unchanged():
    """generate_prompts(n) with no theme arg must still use the aios theme
    (backwards compatibility)."""
    prompts_default = generate_prompts(20)
    prompts_aios = generate_prompts(20, theme="aios")
    assert prompts_default == prompts_aios


# --- Pytest Integration ---


def test_stress_10_turns():
    """Quick 10-turn smoke test - verifies the pipeline works end-to-end
    with chained conversation turns."""
    report = run_stress_test(
        url=DEFAULT_URL,
        turns=10,
        delay=0.5,
        max_tokens=50,
        verbose=False,
    )
    assert report.completed_turns >= 8, f"Too many failures: {report.failed_turns}"
    assert report.repetition_ratio < 0.5, f"High repetition: {report.repetition_ratio}"
    assert len(report.errors) == 0, f"Errors: {report.errors}"


def test_stress_100_turns():
    """100-turn stress test - verifies memory and context handling across
    a chained conversation. Checks for context drift (recall degradation
    should be near zero — the LLM should remember early context even at
    turn 100)."""
    report = run_stress_test(
        url=DEFAULT_URL,
        turns=100,
        delay=1.0,
        max_tokens=100,
        verbose=False,
    )
    assert report.completed_turns >= 90, f"Too many failures: {report.failed_turns}"
    assert report.repetition_ratio < 0.3, f"High repetition: {report.repetition_ratio}"
    # VRAM shouldn't grow more than 2GB over 100 turns
    assert report.vram_growth_mb < 2048, f"VRAM leak: grew {report.vram_growth_mb}MB"
    # Context drift: recall quality shouldn't degrade by more than 20%
    assert report.recall_degradation > -0.2, (
        f"Context drift detected: recall degraded by {report.recall_degradation} "
        f"(early={report.early_recall_quality}, late={report.late_recall_quality})"
    )


# --- CLI ---


def main():
    parser = argparse.ArgumentParser(description="AIOS Entropy & Context Drift Stress Test")
    parser.add_argument("--turns", type=int, default=100, help="Number of turns (default: 100)")
    parser.add_argument("--url", type=str, default=DEFAULT_URL, help="AIOS endpoint URL")
    parser.add_argument("--delay", type=float, default=DEFAULT_TURN_DELAY, help="Delay between turns (seconds)")
    parser.add_argument("--max-tokens", type=int, default=100, help="Max tokens per response")
    parser.add_argument("--quiet", action="store_true", help="Less verbose output")
    parser.add_argument("--consolidate", action="store_true",
                        help="Trigger memory consolidation at each 100-turn checkpoint")
    parser.add_argument("--elastic", action="store_true",
                        help="Enable the elastic context window (elastic_context: true "
                             "on each request) so the server retrieves older turns "
                             "semantically instead of using the fixed 6-turn window. "
                             "See docs/elastic_context_window.md.")
    parser.add_argument("--theme", type=str, default="aios",
                        choices=list(PROMPT_THEMES.keys()),
                        help="Prompt theme: 'aios' (AIOS project planning) or "
                             "'business' (small-business exploration). "
                             "Default: aios")
    args = parser.parse_args()

    report = run_stress_test(
        url=args.url,
        turns=args.turns,
        delay=args.delay,
        max_tokens=args.max_tokens,
        verbose=not args.quiet,
        consolidate=args.consolidate,
        elastic=args.elastic,
        theme=args.theme,
    )

    print_report(report)
    save_report(report, args.turns)

    # Exit code: 0 if no errors, 1 if any failures
    sys.exit(0 if len(report.errors) == 0 else 1)


if __name__ == "__main__":
    main()
