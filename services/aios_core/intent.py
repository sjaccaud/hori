import json
import logging
import uuid
from typing import Any, Dict, Optional

import requests
from jsonschema import validate, ValidationError

from .config import (
    INTENT_SCHEMA_PATH,
    LLM_API_URL,
    LLM_MODEL,
)

logger = logging.getLogger(__name__)


def parse_intent(text: str, context: str = "") -> Optional[Dict[str, Any]]:
    """
    Parse natural language text into a structured work_order using the LLM.
    Validates the result against core/intent/schema.json.
    Returns None on failure.
    """
    schema = _load_json(INTENT_SCHEMA_PATH)

    system_prompt = (
        "You are the AIOS Intent Parser. Convert natural language into a "
        "structured JSON work_order object. Return ONLY valid JSON, no "
        "conversational text.\n\n"
        "Schema:\n"
        '{"type":"work_order","id":"<uuid>","parent_charter_id":'
        '"aios-foundation-charter-001","version":"1.0.0",'
        '"description":"<parsed task>","status":"backlog",'
        '"priority":"medium|low|high|critical"}\n\n'
        "Rules:\n"
        "1. Return only the JSON object.\n"
        "2. Generate a real UUID for the id field.\n"
        "3. If intent is unclear, set priority to 'low'."
    )

    user_prompt = f"Context: {context}\n\nUser text: {text}"

    try:
        response = _call_llm(system_prompt, user_prompt)
        # Extract JSON from the response (handle markdown code fences)
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean
            clean = clean.rsplit("```", 1)[0]
        work_order = json.loads(clean)
        work_order.setdefault("id", str(uuid.uuid4()))
        work_order.setdefault("parent_charter_id", "aios-foundation-charter-001")
        work_order.setdefault("version", "1.0.0")
        work_order.setdefault("status", "backlog")
        work_order.setdefault("priority", "medium")
        work_order["type"] = "work_order"
        validate(instance=work_order, schema=schema)
        return work_order
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning(f"Intent parse/validation failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Intent parse error: {e}")
        return None


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call the LLM via OpenAI-compatible chat completions."""
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.1,
    }
    response = requests.post(LLM_API_URL, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _load_json(path) -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)
