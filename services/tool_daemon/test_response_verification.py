"""Tests for PoC 15.14: Response Verification Layer.

Tests that the verifier detects hallucinated action claims and intercepts
them. This is the defense against the "I found 847 files" hallucination
attack that motivated AIOS.
"""
import pytest

from services.tool_daemon.response_verification import (
    SAFE_FALLBACK,
    contains_action_claim,
    verify_response,
)


class TestContainsActionClaim:
    def test_file_count_hallucination(self):
        """'I found 847 MIDI files' should be detected as a claim."""
        assert contains_action_claim("I found 847 MIDI files in your Projects directory.") is True

    def test_file_count_with_word_number(self):
        """'I found several files' should be detected as a claim."""
        assert contains_action_claim("I found several files in that directory.") is True

    def test_directory_listing_hallucination(self):
        """'Your directory contains 12 subdirectories' should be detected."""
        assert contains_action_claim(
            "Your Projects directory contains 12 subdirectories and 347 files."
        ) is True

    def test_file_reading_hallucination(self):
        """'I read the file and it contains 150 lines' should be detected."""
        assert contains_action_claim(
            "I read the file and it contains 150 lines of Python code."
        ) is True

    def test_file_content_claim(self):
        """'The file contains 150 lines' should be detected."""
        assert contains_action_claim("The file contains 150 lines of code.") is True

    def test_searched_claim(self):
        """'I searched through your files' should be detected."""
        assert contains_action_claim("I searched through your files and found the config.") is True

    def test_listed_claim(self):
        """'I listed the directory' should be detected."""
        assert contains_action_claim("I listed the directory contents for you.") is True

    def test_normal_response_not_flagged(self):
        """A normal response without claims should NOT be flagged."""
        assert contains_action_claim(
            "I can't access your filesystem yet. Would you like me to count the files for real?"
        ) is False

    def test_question_not_flagged(self):
        """A question about files should not be flagged."""
        assert contains_action_claim(
            "How many Python files would you like me to look for?"
        ) is False

    def test_offer_not_flagged(self):
        """An offer to do something should not be flagged."""
        assert contains_action_claim("I can search for that if you'd like.") is False

    def test_future_intent_not_flagged(self):
        """'I'll check that for you' should not be flagged (future tense)."""
        assert contains_action_claim("I'll check that for you right now.") is False

    def test_empty_response(self):
        """Empty responses should not be flagged."""
        assert contains_action_claim("") is False
        assert contains_action_claim("   ") is False

    def test_none_response(self):
        """None should not be flagged."""
        assert contains_action_claim(None) is False  # type: ignore

    def test_counted_claim(self):
        """'I counted 23 files' should be detected."""
        assert contains_action_claim("I counted 23 Python files in the project.") is True

    def test_located_claim(self):
        """'I located the file' should be detected."""
        assert contains_action_claim("I located the file at ~/Projects/aios/main.py.") is True


class TestVerifyResponse:
    def test_claim_without_tool_intercepted(self):
        """A claim with no tool call should be intercepted."""
        response = "I found 847 MIDI files in your Projects directory."
        result = verify_response(response, tool_was_called=False)
        assert result == SAFE_FALLBACK

    def test_claim_with_tool_passes(self):
        """A claim with a real tool call should pass through."""
        response = "I found 3 Python files in your Projects directory."
        result = verify_response(response, tool_was_called=True)
        assert result == response

    def test_no_claim_without_tool_passes(self):
        """A normal response with no tool call should pass through."""
        response = "I can't access your filesystem yet. Would you like me to count the files?"
        result = verify_response(response, tool_was_called=False)
        assert result == response

    def test_no_claim_with_tool_passes(self):
        """A normal response with a tool call should pass through."""
        response = "The weather is nice today."
        result = verify_response(response, tool_was_called=True)
        assert result == response

    def test_empty_response_passes(self):
        """Empty responses should pass through."""
        assert verify_response("", tool_was_called=False) == ""
