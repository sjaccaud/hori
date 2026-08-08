import json
import logging
from typing import Any, Dict, List, Optional

from .config import PROJECT_STATE_PATH, USER_MODEL_PATH

logger = logging.getLogger(__name__)

# --- Context budget constants ---
# The llama-server context window is 16,384 tokens (-c 16384 in the
# systemd service). The state block was consuming ~13K tokens (82%)
# because active_interests grew to 209 items and open_questions to 571.
# These caps keep the state block to a sane fraction of the context
# window, leaving room for conversation history + generation.
# See the 500-turn stress run analysis (entropy_500turns_20260807_020611.json)
# where recall collapsed at turns 300 and 500 due to context overflow.
MAX_CONTEXT_CHARS = 4000          # ~1000 tokens — a quarter of the window
MAX_INTERESTS = 20                # cap displayed interests
MAX_QUESTIONS_PER_PROJECT = 10    # cap displayed open questions per project
MAX_TONE_TAGS = 15                # cap displayed tone tags (was uncapped — grew to 73 with case-variant dups)

_DEFAULT_USER_MODEL = {
    "preferences": [],
    "communication_style": "",
    "tone_tags": [],
    "active_interests": [],
    "skills": [],
    "working_hours": "",
    "decision_patterns": [],
}

_DEFAULT_PROJECT_STATE = {
    "active_projects": [],
    "recent_decisions": [],
    "deferred_items": [],
}


def load_user_model() -> Dict[str, Any]:
    """Load the living user model, creating defaults if absent."""
    return _load_state(USER_MODEL_PATH, _DEFAULT_USER_MODEL)


def load_project_state() -> Dict[str, Any]:
    """Load the living project state, creating defaults if absent."""
    return _load_state(PROJECT_STATE_PATH, _DEFAULT_PROJECT_STATE)


def save_user_model(model: Dict[str, Any]):
    _save_state(USER_MODEL_PATH, model)


def save_project_state(state: Dict[str, Any]):
    _save_state(PROJECT_STATE_PATH, state)


def _dedupe_interests(interests: List[str], max_items: int = MAX_INTERESTS) -> List[str]:
    """Deduplicate near-duplicate interests and cap to max_items.

    The memory consolidation appends every new topic to active_interests
    without dedup. Over 500 turns this grew to 209 items with many
    near-duplicates ('Safety Spine', 'Safety Spine Implementation',
    'Safety Spine Architecture'). This collapses them by measuring
    word overlap: if the shorter interest's word set is >=50% contained
    in the longer one's, they're considered duplicates.

    Longer/more specific entries are kept; shorter variants are dropped
    in favor of the more descriptive one.
    """
    if not interests:
        return []

    # Normalize: strip whitespace, drop empties
    normalized = [i.strip() for i in interests if i.strip()]

    # Sort by length descending — keep the most descriptive variant
    normalized.sort(key=len, reverse=True)

    def _word_set(s: str) -> set:
        """Tokenize into lowercase words, splitting on spaces and hyphens."""
        import re
        return set(w.lower() for w in re.split(r'[\s\-]+', s) if len(w) > 1)

    kept: List[str] = []
    kept_words: List[set] = []
    for label in normalized:
        words = _word_set(label)
        is_dup = False
        for existing_words in kept_words:
            if not words or not existing_words:
                continue
            overlap = len(words & existing_words)
            shorter = min(len(words), len(existing_words))
            if shorter > 0 and overlap / shorter >= 0.5:
                is_dup = True
                break
        if not is_dup:
            kept.append(label)
            kept_words.append(words)
        if len(kept) >= max_items:
            break

    return kept


def build_context_block() -> str:
    """
    Build a compact text block from the state files for prompt injection.
    This is the anti-groundhog mechanism: every session starts with this context.

    Output is capped to MAX_CONTEXT_CHARS (~1000 tokens) to prevent
    context-window overflow. The 500-turn stress run showed that
    uncapped state (209 interests, 571 questions) consumed 82% of the
    16K-token context window, causing generation truncation at turns
    300 and 500. See entropy_500turns_20260807_020611.json.
    """
    user = load_user_model()
    project = load_project_state()

    lines = ["=== USER CONTEXT ==="]

    if user.get("communication_style"):
        lines.append(f"Communication style: {user['communication_style']}")
    if user.get("active_interests"):
        deduped = _dedupe_interests(user["active_interests"])
        lines.append(f"Active interests: {', '.join(deduped)}")
    if user.get("tone_tags"):
        # Deduplicate case-insensitively, keeping the first-seen casing.
        # The tone_tags list grew to 73 entries with case-variant duplicates
        # (analytical/Analytical, Alert/alert, etc.) consuming ~900 chars.
        seen_tones: set[str] = set()
        deduped_tones: List[str] = []
        for tag in user["tone_tags"]:
            key = tag.lower()
            if key not in seen_tones:
                seen_tones.add(key)
                deduped_tones.append(tag)
        lines.append(f"Preferred tone: {', '.join(deduped_tones[:MAX_TONE_TAGS])}")
    if user.get("skills"):
        lines.append(f"Known skills: {', '.join(user['skills'])}")

    lines.append("")
    lines.append("=== PROJECT STATE ===")

    # Track questions across all projects to avoid duplicating the same
    # question under multiple projects (e.g. AIOS 1.0 and 1.5/1.6 had
    # identical 10-question lists, wasting ~800 chars).
    seen_questions: set[str] = set()

    for p in project.get("active_projects", []):
        status = p.get("status", "unknown")
        name = p.get("name", "unnamed")
        last = p.get("last_touched", "")
        lines.append(f"- {name} [{status}] (last: {last})")
        # Cap open questions — keep the most recent ones, skip dups across projects
        questions = p.get("open_questions", [])
        shown = 0
        for q in questions[-MAX_QUESTIONS_PER_PROJECT * 2:]:  # scan more to find unique ones
            if q in seen_questions:
                continue
            seen_questions.add(q)
            lines.append(f"  ? {q}")
            shown += 1
            if shown >= MAX_QUESTIONS_PER_PROJECT:
                break
        for a in p.get("next_actions", []):
            lines.append(f"  -> {a}")

    if project.get("recent_decisions"):
        lines.append("")
        lines.append("Recent decisions:")
        for d in project["recent_decisions"][-5:]:
            lines.append(f"  - {d}")

    result = "\n".join(lines)

    # Hard cap: if we're still over budget, truncate with a note.
    # This is a safety net — the per-field caps above should prevent it.
    if len(result) > MAX_CONTEXT_CHARS:
        result = result[:MAX_CONTEXT_CHARS - 50] + "\n[... state context truncated to fit context window ...]"

    return result


def _load_state(path, defaults: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info(f"State file not found, creating defaults: {path}")
        _save_state(path, defaults)
        return defaults
    except Exception as e:
        logger.warning(f"Failed to load state {path}: {e}")
        return dict(defaults)


def _save_state(path, data: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
