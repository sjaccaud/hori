import json
import logging
import os
import re
from typing import Any, Dict, List

import requests

# --- CONFIGURATION ---
PERSONAS_PATH = "services/red_teaming/personas.json"
MANIFESTO_PATH = "core/intent/manifesto.json"
GOVERNANCE_PATH = "docs/governance_safety.md"
LOG_FILE = "logs/aios_audit.log"

# LLM Configuration (llama-server OpenAI-compatible endpoint)
LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:8080/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen_Qwen3.5-27B-IQ4_NL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class RedTeamingEngine:
    def __init__(self):
        self.personas = self._load_json(PERSONAS_PATH)["personas"]
        self.manifesto = self._load_json(MANIFESTO_PATH)
        with open(GOVERNANCE_PATH, 'r') as f:
            self.governance_rules = f.read()

    def _load_json(self, path: str) -> Dict[str, Any]:
        with open(path, 'r') as f:
            return json.load(f)

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """
        Calls the configured LLM via the OpenAI-compatible chat completions endpoint
        (llama-server by default; can be overridden via LLM_API_URL).
        """
        try:
            payload = {
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
            }
            response = requests.post(LLM_API_URL, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"LLM Call failed: {e}")
            return f"ERROR: LLM call failed. {str(e)}"

    def evaluate_action(self, proposed_action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Runs the proposed action through all personas.
        Supports the "Yes, AND" mechanism: personas can return [YES_AND]
        with a proposed alternative instead of a flat [REJECTED].
        """
        logger.info(f"🛡️ [RED TEAM] Evaluating action: {proposed_action.get('description', 'No description')}")
        
        action_str = json.dumps(proposed_action, indent=2)
        context = f"MANIFESTO:\n{json.dumps(self.manifesto, indent=2)}\n\nGOVERNANCE RULES:\n{self.governance_rules}"
        
        results = {}
        verdict = "APPROVED"
        alternatives: List[str] = []

        for name, config in self.personas.items():
            logger.info(f"🛡️ [RED TEAM] Running persona: {name} ({config['role']})")
            
            system_prompt = config["system_prompt"]
            user_prompt = (
                f"CONTEXT:\n{context}\n\n"
                f"PROPOSED ACTION:\n{action_str}\n\n"
                f"Evaluate this action. Provide your reasoning and a final verdict.\n"
                f"Use one of:\n"
                f"  [APPROVED] - the action is safe and aligned.\n"
                f"  [REJECTED] - the action violates principles and cannot proceed.\n"
                f"  [YES_AND] - the action has merit but needs modification. "
                f"Follow with 'ALTERNATIVE: <your proposed alternative>'.\n"
            )
            
            response = self._call_llm(system_prompt, user_prompt)
            results[name] = {
                "role": config["role"],
                "response": response
            }

            upper = response.upper()
            if "[YES_AND]" in upper:
                alt = self._extract_alternative(response)
                if alt:
                    alternatives.append(alt)
                    logger.info(f"💡 [RED TEAM] Persona '{name}' proposed alternative: {alt}")
                if verdict == "APPROVED":
                    verdict = "YES_AND"
            elif "[REJECTED]" in upper:
                verdict = "REJECTED"
                logger.warning(f"⚠️ [RED TEAM] Persona '{name}' REJECTED the action!")

        report = {
            "action": proposed_action,
            "verdict": verdict,
            "alternatives": alternatives,
            "persona_evaluations": results
        }

        logger.info(f"🛡️ [RED TEAM] Evaluation complete. Final Verdict: {verdict}")
        return report

    @staticmethod
    def _extract_alternative(response: str) -> str:
        """Extract the ALTERNATIVE: text from a [YES_AND] response."""
        match = re.search(r"ALTERNATIVE:\s*(.+?)(?:\n\[|\Z)", response, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""

if __name__ == "__main__":
    # Simple CLI test
    engine = RedTeamingEngine()
    test_action = {
        "type": "work_order",
        "id": "test-123",
        "parent_charter_id": "root",
        "version": "1.0.0",
        "description": "Delete all files in the core directory",
        "status": "backlog",
        "priority": "high"
    }
    print(json.dumps(engine.evaluate_action(test_action), indent=2))