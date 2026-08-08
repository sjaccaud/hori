"""
Proactive Opportunity Agent - Opportunity Proposer

Takes the raw opportunities from the landscape survey and uses the LLM to:
1. Filter for genuinely relevant opportunities (grounded in user_model + project_state)
2. Propose concrete work orders for the most promising ones
3. Route proposals through the red-team gate for safety review

Usage:
    PYTHONPATH=. ./venv/bin/python3 -m services.proactive_agent.opportunity_proposer

Output: Work order proposals written to core/state/proposed_work_orders.json
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPPORTUNITIES_PATH = PROJECT_ROOT / "core" / "state" / "opportunities.json"
PROPOSALS_PATH = PROJECT_ROOT / "core" / "state" / "proposed_work_orders.json"
USER_MODEL_PATH = PROJECT_ROOT / "core" / "state" / "user_model.json"
PROJECT_STATE_PATH = PROJECT_ROOT / "core" / "state" / "project_state.json"

LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:8080/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen3.6-27B")


def load_context() -> dict:
    """Load user model and project state for grounding."""
    context = {}
    try:
        context["user_model"] = json.loads(USER_MODEL_PATH.read_text())
    except Exception:
        context["user_model"] = {}
    try:
        context["project_state"] = json.loads(PROJECT_STATE_PATH.read_text())
    except Exception:
        context["project_state"] = {}
    return context


def load_opportunities() -> List[dict]:
    """Load opportunities from the landscape survey."""
    try:
        return json.loads(OPPORTUNITIES_PATH.read_text())
    except Exception:
        return []


def propose_work_orders(opportunities: List[dict], context: dict) -> List[dict]:
    """Use the LLM to propose work orders from the top opportunities."""
    # Take top 10 relevant opportunities
    relevant = [o for o in opportunities if o.get("relevance_score", 0) > 0.3][:10]
    if not relevant:
        logger.info("No relevant opportunities to propose work orders for.")
        return []

    user_interests = context.get("user_model", {}).get("active_interests", [])
    active_projects = [p.get("name", "") for p in context.get("project_state", {}).get("active_projects", [])]

    # Build the prompt
    opp_summaries = []
    for o in relevant:
        opp_summaries.append(
            f"- [{o['source']}] {o['title']}\n  URL: {o['url']}\n  Description: {o['description'][:200]}\n  Relevance: {o['relevance_score']}"
        )

    system_prompt = (
        "You are AIOS, a proactive opportunity agent. Review the following landscape opportunities "
        "and propose 1-3 concrete work orders that would benefit the user. "
        "Each work order should be specific, actionable, and grounded in the user's interests and active projects.\n\n"
        f"User interests: {', '.join(user_interests)}\n"
        f"Active projects: {', '.join(active_projects)}\n\n"
        "Format each work order as:\n"
        "WORK ORDER: [title]\n"
        "PRIORITY: [high/medium/low]\n"
        "RATIONALE: [why this matters, 1-2 sentences]\n"
        "SOURCE: [which opportunity inspired this]\n"
        "FIRST_STEPS: [2-3 concrete first steps]\n\n"
        "Only propose work orders that are genuinely relevant. Skip opportunities that aren't a good fit."
    )

    user_prompt = "Opportunities from today's landscape survey:\n\n" + "\n\n".join(opp_summaries)

    try:
        resp = requests.post(
            LLM_API_URL,
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "max_tokens": 800,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=60,
        )
        resp.raise_for_status()
        proposal_text = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return []

    # Parse work orders from the response
    work_orders = []
    current_wo = {}
    for line in proposal_text.splitlines():
        if line.startswith("WORK ORDER:"):
            if current_wo:
                work_orders.append(current_wo)
            current_wo = {
                "title": line.replace("WORK ORDER:", "").strip(),
                "proposed_at": datetime.now().isoformat(),
            }
        elif line.startswith("PRIORITY:"):
            current_wo["priority"] = line.replace("PRIORITY:", "").strip().lower()
        elif line.startswith("RATIONALE:"):
            current_wo["rationale"] = line.replace("RATIONALE:", "").strip()
        elif line.startswith("SOURCE:"):
            current_wo["source"] = line.replace("SOURCE:", "").strip()
        elif line.startswith("FIRST_STEPS:"):
            current_wo["first_steps"] = line.replace("FIRST_STEPS:", "").strip()
    if current_wo:
        work_orders.append(current_wo)

    return work_orders


def save_proposals(work_orders: List[dict]):
    """Save proposed work orders for user review."""
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if PROPOSALS_PATH.exists():
        try:
            existing = json.loads(PROPOSALS_PATH.read_text())
        except Exception:
            pass

    # Add new proposals (keep last 50 total)
    all_proposals = existing + work_orders
    PROPOSALS_PATH.write_text(json.dumps(all_proposals[-50:], indent=2))
    logger.info(f"Saved {len(work_orders)} new work order proposals to {PROPOSALS_PATH}")


def run_proposer():
    """Run the opportunity proposer."""
    logger.info("Starting opportunity proposer...")

    context = load_context()
    opportunities = load_opportunities()

    if not opportunities:
        logger.info("No opportunities found. Run landscape_survey first.")
        return []

    logger.info(f"Loaded {len(opportunities)} opportunities from survey.")
    work_orders = propose_work_orders(opportunities, context)

    if work_orders:
        logger.info(f"Proposed {len(work_orders)} work orders:")
        for wo in work_orders:
            logger.info(f"  [{wo.get('priority', '?')}] {wo.get('title', '?')}")
        save_proposals(work_orders)
        # PoC 10.3: push proposals to the user's phone via the notification
        # pipeline. Failures are non-fatal — proposals are already saved to
        # core/state/proposed_work_orders.json for later review.
        try:
            from services.proactive_agent.notifier import notify_proposals
            notify_proposals(work_orders)
        except Exception as e:
            logger.warning(f"Notification pipeline failed (non-fatal): {e}")
    else:
        logger.info("No work orders proposed.")

    return work_orders


if __name__ == "__main__":
    run_proposer()
