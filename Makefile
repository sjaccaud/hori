.PHONY: test test-unit test-mcp test-integration test-regression test-adversarial test-all lint coverage test-stress-entropy test-stress-safety test-stress open-webui-apply install-open-webui-service install-local-tools check-local-tools install-llama-nano check-llama-nano install-script-panel check-script-panel

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
	PYTHONPATH=. ./venv/bin/python3 -m pytest services/red_teaming/test_engine.py services/aios_core/test_main.py services/aios_core/test_intent_graph.py services/aios_core/test_web_search.py services/aios_core/test_multi_search.py services/aios_core/test_tts.py services/aios_core/test_safety_events.py services/aios_core/test_autolisten.py services/aios_core/test_chat_surface.py services/aios_core/test_presence.py services/proactive_agent/test_landscape_survey.py services/proactive_agent/test_notifier.py services/tool_daemon/test_schema.py services/tool_daemon/test_registry.py services/tool_daemon/test_validation.py services/tool_daemon/test_output_parser.py services/tool_daemon/test_tools.py services/tool_daemon/test_audit.py services/tool_daemon/test_fail_closed.py services/tool_daemon/test_server.py services/tool_daemon/test_rate_limiter.py services/tool_daemon/test_guard_rails.py services/tool_daemon/test_response_verification.py services/open_webui/test_system_state_sync.py services/open-webui/test_local_tools_server.py services/open-webui/test_project_tools_server.py services/aios_core/test_mcp_server.py hori/test_detect.py hori/test_sqlite_memory.py hori/test_init.py hori/test_persona.py services/broker/test_broker.py

# Run MCP unit + adversarial tests (fast feedback loop)
test-mcp:
	@echo "🔌 Running MCP tests..."
	PYTHONPATH=. ./venv/bin/python3 -m pytest services/aios_core/test_mcp_server.py tests/adversarial/test_mcp_unauthorized.py

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
	PYTHONPATH=. ./venv/bin/python3 -m pytest tests/regression/ $(wildcard services/open-webui/test_*.py)

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

# Install and start the Open-WebUI local-tools OpenAPI server on 127.0.0.1:8766.
install-local-tools:
	@echo "🔗 Installing local-tools systemd user service..."
	@mkdir -p "$${HOME}/.config/systemd/user"
	@cp ./services/open-webui/local-tools.service "$${HOME}/.config/systemd/user/local-tools.service"
	@systemctl --user daemon-reload
	@systemctl --user enable --now local-tools

# Quick health check for the local-tools server.
check-local-tools:
	@./venv/bin/python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8766/health').read().decode())"

# Install and start the project-creation tools server on 127.0.0.1:8769.
install-project-tools:
	@echo "🔗 Installing project-tools systemd user service..."
	@mkdir -p "$${HOME}/.config/systemd/user"
	@cp ./services/open-webui/project-tools.service "$${HOME}/.config/systemd/user/project-tools.service"
	@systemctl --user daemon-reload
	@systemctl --user enable --now project-tools

# Quick health check for the project-tools server.
check-project-tools:
	@./venv/bin/python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8769/health').read().decode())"

# Install and start the nano SLM server (Llama-3.2-3B) on 127.0.0.1:8090.
# Registered in Open-WebUI as a second OpenAI base URL; shows as "Nano Chat".
install-llama-nano:
	@echo "🔗 Installing llama-nano systemd user service..."
	@mkdir -p "$${HOME}/.config/systemd/user"
	@cp ./services/open-webui/llama-nano.service "$${HOME}/.config/systemd/user/llama-nano.service"
	@systemctl --user daemon-reload
	@systemctl --user enable --now llama-nano

# Quick health check for the nano server.
check-llama-nano:
	@./venv/bin/python3 -c "import urllib.request, json; print(json.load(urllib.request.urlopen('http://127.0.0.1:8090/v1/models')))"

# Install and start the script-panel control panel on 127.0.0.1:8770.
install-script-panel:
	@echo "🔗 Installing script-panel systemd user service..."
	@mkdir -p "$${HOME}/.config/systemd/user"
	@cp ./services/script-panel/script-panel.service "$${HOME}/.config/systemd/user/script-panel.service"
	@systemctl --user daemon-reload
	@systemctl --user enable --now script-panel

# Quick health check for the script panel.
check-script-panel:
	@./venv/bin/python3 -c "import urllib.request, json; d=json.load(urllib.request.urlopen('http://127.0.0.1:8770/api/catalog')); print(len(d['scripts']), 'scripts in catalog')"

# Generate a SearXNG secret for the local search container if one doesn't exist.
search-secret:
	@if [ ! -f services/searchrouter/.env ]; then \
		mkdir -p services/searchrouter; \
		python3 -c "import secrets; print('SEARXNG_SECRET=' + secrets.token_hex(32))" > services/searchrouter/.env; \
		chmod 600 services/searchrouter/.env; \
		echo "Generated services/searchrouter/.env"; \
	else \
		echo "services/searchrouter/.env already exists."; \
	fi

# Non-sudo health check for the whole AIOS spine.
health:
	@./venv/bin/python3 scripts/health.py

# Bring up the user-facing AIOS stack (Docker + user services).
# System inference services (aios_core, llamacpp, aios-sherpa, aios-tool-daemon)
# are normally started at boot; if not, run:
#   sudo systemctl start aios_core llamacpp aios-sherpa aios-tool-daemon
aios-up:
	@echo "Starting local-tools user service..."
	@systemctl --user start local-tools
	@echo "Starting project-tools user service..."
	@systemctl --user start project-tools
	@echo "Starting llama-nano user service..."
	@systemctl --user start llama-nano 2>/dev/null || echo "  (not installed — run 'make install-llama-nano')"
	@echo "Ensuring SearXNG secret..."
	@$(MAKE) -s search-secret
	@echo "Starting searchrouter..."
	@cd services/searchrouter && docker compose up -d
	@echo "Starting Qdrant..."
	@docker start qdrant_aios >/dev/null 2>&1 || true
	@echo "Starting Open-WebUI / embedding Docker stack..."
	@cd services/open-webui && docker compose up -d
	@echo "Use 'make health' to verify."

# Shut down the user-facing stack.
aios-down:
	@echo "Stopping Open-WebUI / embedding Docker stack..."
	@cd services/open-webui && docker compose down
	@echo "Stopping searchrouter..."
	@cd services/searchrouter && docker compose down
	@echo "Stopping Qdrant..."
	@docker stop qdrant_aios >/dev/null 2>&1 || true
	@echo "Stopping local-tools user service..."
	@systemctl --user stop local-tools
	@echo "Stopping project-tools user service..."
	@systemctl --user stop project-tools
	@echo "Stopping llama-nano user service..."
	@systemctl --user stop llama-nano 2>/dev/null || true
