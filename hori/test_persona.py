"""Tests for hori.persona — the HORI system prompt builder."""
import pytest

from hori.persona import build_system_prompt, build_search_prompt


class TestCoreIdentity:
    def test_says_hori(self):
        prompt = build_system_prompt()
        assert "HORI" in prompt
        assert "彫り" in prompt

    def test_does_not_say_aios(self):
        prompt = build_system_prompt()
        assert "AIOS" not in prompt

    def test_says_local_first(self):
        prompt = build_system_prompt()
        assert "local-first" in prompt
        assert "NOT a cloud service" in prompt

    def test_has_web_search_capability(self):
        prompt = build_system_prompt()
        assert "web search" in prompt.lower() or "search the web" in prompt.lower()
        assert "DuckDuckGo" in prompt

    def test_has_date_time(self):
        prompt = build_system_prompt()
        assert "Current date/time:" in prompt


class TestToolsMode:
    def test_no_tools_by_default(self):
        prompt = build_system_prompt()
        assert "CANNOT browse the filesystem" in prompt
        assert "CANNOT run shell commands" in prompt

    def test_tools_enabled(self):
        prompt = build_system_prompt(tools_enabled=True)
        assert "Browse the filesystem" in prompt
        assert "CANNOT browse" not in prompt

    def test_hallucination_warning_with_tools(self):
        prompt = build_system_prompt(tools_enabled=True)
        assert "DO NOT HALLUCINATE" in prompt

    def test_hallucination_warning_without_tools(self):
        prompt = build_system_prompt(tools_enabled=False)
        assert "DO NOT HALLUCINATE" in prompt


class TestVoiceMode:
    def test_voice_guidelines_when_enabled(self):
        prompt = build_system_prompt(voice_mode=True)
        assert "spoken aloud" in prompt
        assert "Do not use markdown" in prompt
        assert "NEVER suggest terminal commands" in prompt

    def test_no_voice_guidelines_by_default(self):
        prompt = build_system_prompt(voice_mode=False)
        assert "spoken aloud" not in prompt


class TestContext:
    def test_state_context_included(self):
        prompt = build_system_prompt(state_context="System: all services running")
        assert "all services running" in prompt

    def test_memory_context_included(self):
        prompt = build_system_prompt(memory_context="User likes blue")
        assert "RELEVANT MEMORY" in prompt
        assert "User likes blue" in prompt

    def test_empty_context_ok(self):
        prompt = build_system_prompt()
        # Should not crash with empty context
        assert "RELEVANT MEMORY" not in prompt


class TestSearchPrompt:
    def test_says_hori(self):
        prompt = build_search_prompt()
        assert "HORI" in prompt
        assert "AIOS" not in prompt

    def test_has_citation_instruction(self):
        prompt = build_search_prompt()
        assert "[1]" in prompt
        assert "cite" in prompt.lower()

    def test_concise_instruction(self):
        prompt = build_search_prompt()
        assert "concise" in prompt.lower()
