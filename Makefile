.PHONY: test test-unit test-integration test-regression test-adversarial test-all lint coverage test-stress-entropy test-stress-safety test-stress open-webui-apply install-open-webui-service

# Default target
test: test-all

# Run all tests
test-all: test-unit test-integration test-regression test-adversarial

# Run all stress tests (smoke versions — 10 turns each, fits in 120s)
test-stress: test-stress-entropy test-stress-safety

# Run entropy/drift stress test (10-turn smoke, ~60s)
test-stress-entropy:
	@echo "📈 Running entropy/drift stress test (10 turns)..."
	PYTHONPATH=. ./venv/bin/python3 -m pytest tests/stress/test_ten_thousand_turns.py::test_stress_10_turns -v

# Run safety stress test (10-turn smoke, ~45s)
test-stress-safety:
	@echo "🛡️ Running safety stress test (10 turns)..."
	PYTHONPATH=. ./venv/bin/python3 -m pytest tests/stress/test_safety_stress.py::test_safety_stress_smoke -v

# Run unit tests
test-unit:
	@echo "🧪 Running unit tests..."
	PYTHONPATH=. ./venv/bin/python3 -m pytest services/red_teaming/test_engine.py services/aios_core/test_main.py services/aios_core/test_intent_graph.py services/aios_core/test_web_search.py services/aios_core/test_multi_search.py services/aios_core/test_tts.py services/aios_core/test_safety_events.py services/aios_core/test_autolisten.py services/aios_core/test_chat_surface.py services/aios_core/test_presence.py services/proactive_agent/test_landscape_survey.py services/proactive_agent/test_notifier.py services/tool_daemon/test_schema.py services/tool_daemon/test_registry.py services/tool_daemon/test_validation.py services/tool_daemon/test_output_parser.py services/tool_daemon/test_tools.py services/tool_daemon/test_audit.py services/tool_daemon/test_fail_closed.py services/tool_daemon/test_server.py services/tool_daemon/test_rate_limiter.py services/tool_daemon/test_guard_rails.py services/tool_daemon/test_response_verification.py services/open_webui/test_system_state_sync.py hori/test_detect.py hori/test_sqlite_memory.py hori/test_init.py hori/test_persona.py

# Run integration tests
test-integration:
	@echo "🔗 Running integration tests..."
	PYTHONPATH=. ./venv/bin/python3 -m pytest services/integration_tests/

# Run adversarial tests (TDD safety property tests — see docs/roadmap.md Tier 2F)
# These tests attempt to BREAK safety properties. They are written BEFORE
# implementation and must FAIL first, then the safety is built until they PASS.
test-adversarial:
	@echo "⚔️ Running adversarial tests..."
	@if [ -d tests/adversarial ]; then \
		PYTHONPATH=. ./venv/bin/python3 -m pytest tests/adversarial/; \
	else \
		echo "   (tests/adversarial/ does not exist yet — planned in Tier 2F)"; \
	fi

# Run linting
lint:
	@echo "🧹 Running linting..."
	./venv/bin/ruff check .

# Run coverage report
coverage:
	@echo "📊 Generating coverage report..."
	PYTHONPATH=. ./venv/bin/python3 -m pytest --cov=.
# Run regression tests
test-regression:
	@echo "🛡️ Running regression tests..."
	PYTHONPATH=. ./venv/bin/python3 -m pytest tests/regression/

# Re-apply Open-WebUI workspace/prompt/context configuration.
# Run this after `docker compose up -d` or a volume/container recreate.
# Set OPENWEBUI_USER_NAME if you want a display name other than the capitalized $USER.
open-webui-apply:
	@echo "🧰 Applying Open-WebUI model config..."
	@OPENWEBUI_USER_NAME=$${OPENWEBUI_USER_NAME:-$$(echo "$$USER" | sed 's/^./\u&/')} \
	 USER=$$USER HOME=$$HOME \
	 ./services/open-webui/apply-model-config.sh

# Install the systemd oneshot service for Open-WebUI re-apply.
# After install, run `systemctl --user start open-webui-apply`.
install-open-webui-service:
	@echo "🔗 Installing open-webui-apply systemd user service..."
	@mkdir -p "$${HOME}/.config/systemd/user"
	@cp ./services/open-webui/open-webui-apply.service "$${HOME}/.config/systemd/user/open-webui-apply.service"
	@systemctl --user daemon-reload
