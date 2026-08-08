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
        "You are HORI, a proactive opportunity agent. Review the following landscape opportunities "
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
        "Quality criteria — each work order MUST meet ALL of:\n"
        "1. Actionable: the first steps are concrete things the user can do today\n"
        "2. Grounded: references a specific opportunity from the list, not a generic idea\n"
        "3. Relevant: connects to at least one user interest or active project\n"
        "4. Non-obvious: the user wouldn't have thought of this themselves\n"
        "5. Scoped: can be completed in 1-3 days, not a multi-week project\n\n"
        "Only propose work orders that meet ALL criteria. Skip opportunities that aren't a good fit. "
        "It is better to propose 0 work orders than to propose low-quality ones."
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


def score_work_order_quality(wo: dict) -> dict:
    """Score a proposed work order on quality criteria.

    Returns a dict with:
    - actionable: bool — does it have concrete first steps?
    - grounded: bool — does it reference a source?
    - relevant: bool — does it have a rationale?
    - scoped: bool — is the title concise (not a multi-week project)?
    - quality_score: float — 0-1, weighted average
    - quality_reason: str — explanation
    """
    title = wo.get("title", "")
    rationale = wo.get("rationale", "")
    source = wo.get("source", "")
    first_steps = wo.get("first_steps", "")
    priority = wo.get("priority", "")

    actionable = bool(first_steps and len(first_steps) > 10)
    grounded = bool(source and len(source) > 3)
    relevant = bool(rationale and len(rationale) > 10)
    # Scoped: title under 80 chars, has a priority
    scoped = bool(len(title) < 80 and priority in ("high", "medium", "low"))

    # Weighted score
    weights = {"actionable": 0.35, "grounded": 0.20, "relevant": 0.30, "scoped": 0.15}
    quality_score = sum(
        weights[k] for k, v in
        {"actionable": actionable, "grounded": grounded,
         "relevant": relevant, "scoped": scoped}.items()
        if v
    )

    failed = [k for k, v in
              {"actionable": actionable, "grounded": grounded,
               "relevant": relevant, "scoped": scoped}.items()
              if not v]
    quality_reason = "Passes all criteria" if not failed else f"Fails: {', '.join(failed)}"

    return {
        "actionable": actionable,
        "grounded": grounded,
        "relevant": relevant,
        "scoped": scoped,
        "quality_score": round(quality_score, 3),
        "quality_reason": quality_reason,
    }


def filter_low_quality(work_orders: List[dict], min_score: float = 0.6) -> List[dict]:
    """Filter out work orders that don't meet quality threshold.

    Adds quality_score and quality_reason to each work order.
    """
    scored = []
    for wo in work_orders:
        quality = score_work_order_quality(wo)
        wo["quality_score"] = quality["quality_score"]
        wo["quality_reason"] = quality["quality_reason"]
        if quality["quality_score"] >= min_score:
            scored.append(wo)
        else:
            logger.info(f"  Filtered low-quality WO: {wo.get('title', '?')} "
                       f"(score={quality['quality_score']:.2f}, {quality['quality_reason']})")
    return scored


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

    # Filter low-quality work orders (STRAT-7 quality pass)
    work_orders = filter_low_quality(work_orders, min_score=0.6)

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
