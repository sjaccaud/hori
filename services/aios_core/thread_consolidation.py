"""Nightly thread consolidation — folds episode history back into state.

The companion to the "Sleep & Dream" memory consolidation: where that job
distills conversational memory tiers, this one maintains the workstream
layer — refreshing mechanical state.md fields, flagging threads that had
sessions but no episodes, and turning LLM observations into *proposals*
that a human reviews before they can change anything.

Trust model (Pillar VII): the LLM is a collaborator, never an authority.
call_llm output can only reach write_proposal, which hard-forces
status: pending. The mechanical writer accepts only whitelisted
frontmatter keys and preserves the state.md body byte-for-byte. All
writes stay inside the threads root or the review dir.

Traces to: .devin/prd/thread-consolidation.md, docs/manifesto.md Pillars
VI/VII.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

THREADS_ROOT = Path(
    os.environ.get("HORI_THREADS_ROOT", "~/.local/share/hori/threads")
).expanduser()
REVIEW_ROOT = Path(
    os.environ.get("HORI_REVIEW_ROOT", "~/.local/share/hori/review")
).expanduser()
SESSIONS_LOG = Path.home() / ".local/share/hori/sessions.jsonl"

# The ONLY state.md frontmatter keys the mechanical writer may touch.
MECHANICAL_KEYS = {"updated"}

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PROPOSAL_KINDS = {"state-prose", "rule"}

# cwd basename -> thread slug, for dirs that don't normalize cleanly.
CWD_MAP = {"aios": "hori", "content_factory": "content-factory"}


def _iter_threads(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        d for d in sorted(root.iterdir())
        if d.is_dir() and not d.is_symlink() and _SLUG.match(d.name)
    ]


def _split_fm(text: str) -> tuple[list[str], str]:
    """Return (frontmatter_lines, body). No fm -> ([], text)."""
    if not text.startswith("---"):
        return [], text
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return [], text
    return text[3:end].strip("\n").splitlines(), text[end + 4:]


def _episode_date(path: Path) -> Optional[str]:
    fm_lines, _ = _split_fm(path.read_text(encoding="utf-8", errors="replace"))
    for line in fm_lines:
        if line.startswith("date:"):
            raw = line.split(":", 1)[1].strip().strip('"')
            return raw[:10]
    return None


def _fm_value(lines: list[str], key: str) -> Optional[str]:
    for line in lines:
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip('"')
    return None


def plan_mechanical_update(thread_dir: Path) -> dict:
    """Plan the 'updated' refresh from the newest episode date."""
    ep_dir = thread_dir / "episodes"
    dates = [
        d for d in (_episode_date(f) for f in ep_dir.glob("*.md")) if d
    ] if ep_dir.is_dir() else []
    if not dates:
        return {}
    latest = max(dates)

    state = thread_dir / "state.md"
    if state.exists():
        fm_lines, _ = _split_fm(state.read_text(encoding="utf-8"))
    else:
        fm_lines = []
    current = _fm_value(fm_lines, "updated") or ""
    return {"updated": latest} if latest > current else {}


def apply_mechanical(thread_dir: Path, updates: dict) -> bool:
    """Apply whitelisted frontmatter updates to state.md.

    Only keys in MECHANICAL_KEYS are accepted; the body and all other
    frontmatter lines are preserved byte-for-byte.
    """
    bad = set(updates) - MECHANICAL_KEYS
    if bad:
        raise ValueError(f"non-mechanical keys rejected: {sorted(bad)}")

    state = thread_dir / "state.md"
    text = state.read_text(encoding="utf-8") if state.exists() else ""
    fm_lines, body = _split_fm(text)

    for key, value in updates.items():
        value = str(value)
        if "\n" in value or "\r" in value:
            raise ValueError("mechanical values must be single-line")
        new_line = f'{key}: "{value}"'
        replaced = False
        for i, line in enumerate(fm_lines):
            if line.startswith(f"{key}:"):
                fm_lines[i] = new_line
                replaced = True
        if not replaced:
            fm_lines.append(new_line)

    state.write_text("---\n" + "\n".join(fm_lines) + "\n---" + body, encoding="utf-8")
    return True


def _cwd_to_thread(cwd: str, cwd_map: Optional[dict] = None) -> Optional[str]:
    name = Path(cwd).name
    mapping = dict(CWD_MAP)
    if cwd_map:
        mapping.update(cwd_map)
    return mapping.get(name) or name.lower().replace("_", "-")


def detect_stale(
    root: Path,
    sessions_log: Path,
    days: int = 7,
    cwd_map: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Threads with recent sessions but no episodes inside the window."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    counts: dict[str, int] = {}
    if sessions_log.exists():
        text = sessions_log.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                ts = datetime.fromisoformat(rec.get("ts", ""))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff or not rec.get("cwd"):
                continue
            slug = _cwd_to_thread(rec["cwd"], cwd_map)
            if slug:
                counts[slug] = counts.get(slug, 0) + 1

    stale = []
    for slug, n in counts.items():
        tdir = root / slug
        if not tdir.is_dir() or tdir.is_symlink():
            continue
        ep_dir = tdir / "episodes"
        recent = any(
            (d := _episode_date(f)) and
            datetime.fromisoformat(d).replace(tzinfo=timezone.utc) >= cutoff
            for f in ep_dir.glob("*.md")
        ) if ep_dir.is_dir() else False
        if not recent:
            stale.append({"thread": slug, "sessions": n})
    return stale


def write_proposal(
    review_dir: Path,
    *,
    kind: str,
    thread: str,
    proposed: str,
    rationale: str,
    sources: list[str],
) -> dict:
    """Write a human-review proposal. Status is ALWAYS pending."""
    if kind not in _PROPOSAL_KINDS:
        raise ValueError(f"unknown proposal kind: {kind!r}")
    if not _SLUG.match(thread or ""):
        raise ValueError(f"invalid thread slug: {thread!r}")

    review_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(review_dir.glob("*.md"))) + 1
    fname = f"{n:03d}-{kind}-{thread}.md"

    fm = [
        "---",
        f"kind: {json.dumps(kind)}",
        f"thread: {json.dumps(thread)}",
        "created: " + json.dumps(
            datetime.now(timezone.utc).isoformat(timespec="seconds")),
        # Hard-forced. No caller input can set status.
        "status: pending",
        f"sources: {json.dumps(list(sources))}",
        "---",
    ]
    body = [
        "",
        "## Rationale",
        "",
        rationale.rstrip(),
        "",
        "## Proposed change",
        "",
        proposed.rstrip(),
        "",
    ]
    path = review_dir / fname
    with open(path, "x", encoding="utf-8") as fh:
        fh.write("\n".join(fm) + "\n".join(body))
    return {"ok": True, "file": fname}


def propose_state_update(
    thread_dir: Path,
    call_llm: Callable[[str, str], str],
    review_dir: Path,
) -> Optional[dict]:
    """Ask the LLM for a prose state.md update; emit as a proposal only."""
    state = (thread_dir / "state.md").read_text(encoding="utf-8", errors="replace")
    eps = sorted((thread_dir / "episodes").glob("*.md"))[-5:]
    if not eps:
        return None
    ep_text = "\n\n".join(
        f.read_text(encoding="utf-8", errors="replace") for f in eps
    )
    system = (
        "You maintain a workstream state file. Given the current state.md "
        "and recent episodes, propose ONLY the changed/added lines for "
        "state.md (not the whole file). If nothing durable changed, reply "
        "with the single word NONE."
    )
    user = f"CURRENT state.md:\n{state}\n\nRECENT EPISODES:\n{ep_text}"
    out = call_llm(system, user).strip()
    if not out or out == "NONE":
        return None
    return write_proposal(
        review_dir, kind="state-prose", thread=thread_dir.name,
        proposed=out[:4000], rationale="LLM fold of recent episodes",
        sources=[f.name for f in eps],
    )


def mine_rule_proposals(
    root: Path,
    call_llm: Callable[[str, str], str],
    review_dir: Path,
    limit: int = 3,
) -> list[dict]:
    """Mine recent episodes' open issues/failures into rule proposals."""
    snippets = []
    for tdir in _iter_threads(root):
        for f in sorted((tdir / "episodes").glob("*.md"))[-3:]:
            snippets.append(f"[{tdir.name}/{f.name}]\n"
                            + f.read_text(encoding="utf-8", errors="replace")[:2000])
    if not snippets:
        return []

    system = (
        "Read these work episodes. Identify at most {n} recurring failures, "
        "surprises, or corrections worth turning into a standing rule for "
        "the agent rules file. For each, output one line: 'THREAD | proposed "
        "rule text'. If none, reply NONE.".format(n=limit)
    )
    out = call_llm(system, "\n\n".join(snippets[-20:])).strip()
    if not out or out == "NONE":
        return []

    results = []
    for line in out.splitlines()[:limit]:
        if "|" not in line:
            continue
        thread, rule = (p.strip() for p in line.split("|", 1))
        if not _SLUG.match(thread) or not rule:
            continue
        results.append(write_proposal(
            review_dir, kind="rule", thread=thread,
            proposed=rule[:1000],
            rationale="Mined from recent episode open issues/failures",
            sources=[tdir.name for tdir in _iter_threads(root)][:5],
        ))
    return results


def run_consolidation(
    root: Path,
    review_dir: Path,
    call_llm: Optional[Callable[[str, str], str]] = None,
    sessions_log: Optional[Path] = None,
    cwd_map: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> dict:
    """One consolidation pass. LLM stages are optional and fail-open."""
    report: dict[str, Any] = {
        "threads": 0, "mechanical": {}, "stale": [],
        "proposals": [], "llm_available": call_llm is not None,
    }

    for tdir in _iter_threads(root):
        report["threads"] += 1
        plan = plan_mechanical_update(tdir)
        if plan:
            apply_mechanical(tdir, plan)
            report["mechanical"][tdir.name] = plan

    report["stale"] = detect_stale(
        root, sessions_log or SESSIONS_LOG, cwd_map=cwd_map, now=now)

    if call_llm is not None:
        for tdir in _iter_threads(root):
            try:
                p = propose_state_update(tdir, call_llm, review_dir)
                if p:
                    report["proposals"].append(p["file"])
            except Exception:
                report["llm_available"] = False
                break
        if report["llm_available"]:
            try:
                for p in mine_rule_proposals(root, call_llm, review_dir):
                    report["proposals"].append(p["file"])
            except Exception:
                report["llm_available"] = False
    return report
