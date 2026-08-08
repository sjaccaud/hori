"""
PoC 15.14: Response Verification Layer

Every LLM response is scanned for action claims. If the LLM says "I found
847 files" but no count_files tool was called, the response is intercepted
and replaced with: "I cannot perform that action. Would you like me to do
it for real?"

This is the defense against the "I already did it" hallucination attack —
the exact MIDI file hallucination that motivated this project.

Traces to: docs/roadmap.md Tier 2D, PoC 15.14.
Traces to: docs/tool_safety.md "Layer 4: The Leash".

Design:
  - contains_action_claim() scans an LLM response for phrases that claim
    the LLM performed a filesystem action (found files, read a file,
    listed a directory, searched for files).
  - The check is deliberately conservative: it only flags claims that
    assert a result ("I found 847 files"), not questions ("Would you
    like me to count files?") or offers ("I can search for that").
  - The chat pipeline calls this AFTER the LLM response is generated.
    If a claim is detected AND no tool was actually called for this turn,
    the response is replaced with a safe fallback.

Why this exists (what it defs against):
  Before AIOS had tools, the LLM would hallucinate filesystem access:
  "I found 1,247 MIDI files in your Projects directory." The user had
  no way to know if this was real. With the response verification layer,
  the LLM can only claim filesystem actions if it actually called a tool.
  If it claims an action without a tool call, the claim is intercepted.
"""
from __future__ import annotations

import re

# --- Action claim patterns ---
# These patterns detect when the LLM claims to have performed a filesystem
# action. They are deliberately specific to avoid false positives on
# questions or offers.

# "I found N files" / "I found N MIDI files" / "I found 847 files"
_FOUND_COUNT_PATTERN = re.compile(
    r"\bI\s+(?:found|counted|located|discovered|see)\s+"
    r"(?:\d+|a|an|some|many|several|no)\s+"
    r"(?:\w+\s+)*files?\b",
    re.IGNORECASE,
)

# "Your directory contains N files" / "The folder has N items"
# Allows words between the possessive and "directory" (e.g., "Your Projects directory")
_DIR_CONTAINS_PATTERN = re.compile(
    r"\b(?:your|the|this)\b[^.]*?\b(?:directory|folder|path|drive)\s+"
    r"(?:contains|has|holds)\s+\d+\s+",
    re.IGNORECASE,
)

# "I read the file" / "I've read the file" / "The file contains N lines"
_READ_FILE_PATTERN = re.compile(
    r"\bI\s+(?:read|opened|checked|looked at|examined|located)\s+"
    r"(?:the\s+)?file\b",
    re.IGNORECASE,
)

# "The file contains N lines" / "It has N lines of code"
_FILE_CONTENT_CLAIM_PATTERN = re.compile(
    r"\b(?:the\s+)?file\s+(?:contains|has)\s+\d+\s+lines?\b",
    re.IGNORECASE,
)

# "I searched" / "I looked through" / "I scanned"
_SEARCHED_PATTERN = re.compile(
    r"\bI\s+(?:searched|scanned|looked through|went through|browsed)\b",
    re.IGNORECASE,
)

# "I listed" / "I listed the directory"
_LISTED_PATTERN = re.compile(
    r"\bI\s+listed\s+(?:the\s+)?(?:directory|folder|contents|files)\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [
    _FOUND_COUNT_PATTERN,
    _DIR_CONTAINS_PATTERN,
    _READ_FILE_PATTERN,
    _FILE_CONTENT_CLAIM_PATTERN,
    _SEARCHED_PATTERN,
    _LISTED_PATTERN,
]

# Phrases that indicate a question or offer, NOT a claim.
# These are checked first — if the response is asking or offering, it's
# not a claim even if it contains file-related words.
_NON_CLAIM_INDICATORS = [
    "would you like",
    "shall i",
    "do you want",
    "i can",
    "i could",
    "i'll check",
    "let me",
    "i'll search",
    "i'll count",
    "i'll look",
    "i'll find",
    "i'll read",
    "i'll list",
]


def contains_action_claim(response: str) -> bool:
    """Check if an LLM response claims a filesystem action was performed.

    Returns True if the response contains a claim like "I found 847 files"
    or "I read the file and it contains 150 lines". Returns False for
    questions ("How many files would you like me to look for?") and offers
    ("I can search for that if you'd like").

    The chat pipeline calls this after the LLM generates a response. If
    it returns True AND no tool was actually called in this turn, the
    response is intercepted and replaced with a safe fallback.
    """
    if not response or not isinstance(response, str):
        return False

    text = response.strip()

    # Check for non-claim indicators first (questions, offers, future tense)
    # If the response is primarily a question or offer, it's not a claim.
    text_lower = text.lower()
    for indicator in _NON_CLAIM_INDICATORS:
        if indicator in text_lower:
            # If the response is SHORT and contains an indicator, it's
            # likely a question/offer, not a claim.
            if len(text) < 200:
                return False
            # For longer responses, check if the claim pattern appears
            # AFTER the indicator (the indicator might be a preface to
            # the actual claim)
            # Find the indicator position and check after it
            idx = text_lower.index(indicator)
            after = text[idx + len(indicator):]
            # If the claim pattern appears after the indicator, it might
            # be "I can search... I found 847 files" — which IS a claim.
            # But "I can search for that" is NOT. We check if the claim
            # pattern matches the part after the indicator.
            for pattern in _ALL_PATTERNS:
                if pattern.search(after) and len(after) > 50:
                    # There's a claim after the offer — flag it
                    break
            else:
                return False

    # Check for action claim patterns
    for pattern in _ALL_PATTERNS:
        if pattern.search(text):
            return True

    return False


# The safe fallback response when a hallucinated claim is intercepted.
SAFE_FALLBACK = (
    "I cannot perform that action. Would you like me to do it for real?"
)


def verify_response(
    response: str,
    tool_was_called: bool,
) -> str:
    """Verify an LLM response and intercept hallucinated claims.

    If the response contains an action claim AND no tool was actually
    called in this turn, the response is replaced with a safe fallback.
    If a tool WAS called, the claim is legitimate (the tool returned
    real data) and the response is passed through.

    Args:
        response: The LLM's response text.
        tool_was_called: Whether a tool was actually called in this turn.

    Returns:
        The original response if it's safe, or the safe fallback if a
        hallucinated claim was detected.
    """
    if not tool_was_called and contains_action_claim(response):
        return SAFE_FALLBACK
    return response
