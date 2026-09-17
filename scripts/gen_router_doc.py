#!/usr/bin/env python3
"""Generate the live router-alias table for docs/operations.md.

The "which alias / ctx / KV / spec mode is live" table in operations.md is
hand-maintained prose and has repeatedly drifted behind router.ini
(retired aliases still listed, ctx bumps never propagated). This script
parses the preset file llama-server actually loads and renders the table
mechanically.

Usage:
    gen_router_doc.py            # update the marked block in operations.md in place
    gen_router_doc.py --check    # exit 1 if the doc block != generated (drift detector)
    gen_router_doc.py --print    # print the table to stdout, no file writes
    gen_router_doc.py --ini PATH # different router.ini

operations.md must contain the markers:
    <!-- BEGIN GENERATED: router-aliases -->
    <!-- END GENERATED: router-aliases -->

Traces to: hardening sprint 2026-09-17 (knowledge-doc drift findings).
"""

from __future__ import annotations

import argparse
import configparser
import sys
from datetime import date
from pathlib import Path

DEFAULT_INI = Path.home() / "llama-builds/mainline-adaptive-mtp-10/router.ini"
DEFAULT_DOC = Path(__file__).resolve().parent.parent / "docs/operations.md"
BEGIN = "<!-- BEGIN GENERATED: router-aliases -->"
END = "<!-- END GENERATED: router-aliases -->"


def parse_presets(ini_path: Path) -> list[dict[str, str]]:
    parser = configparser.ConfigParser(
        interpolation=None, comment_prefixes=(";",), inline_comment_prefixes=(";",)
    )
    parser.optionxform = str
    parser.read(ini_path)
    presets = []
    for section in parser.sections():
        if section == "*":
            continue
        p = dict(parser.items(section))
        presets.append(p)
    return presets


def render_table(presets: list[dict[str, str]], ini_path: Path) -> str:
    lines = [
        BEGIN,
        f"_Generated {date.today().isoformat()} from "
        f"`{str(ini_path).replace(str(Path.home()), '~')}` by "
        "`scripts/gen_router_doc.py` — do not hand-edit._",
        "",
        "| Alias | Weights | ctx | KV | Reasoning | Speculative decode |",
        "|---|---|---|---|---|---|",
    ]
    for p in presets:
        alias = p.get("alias", "?")
        model = Path(p.get("model", "")).name
        ctx = p.get("c", "?")
        kv = f"{p.get('ctk', '?')}/{p.get('ctv', '?')}"
        reasoning = p.get("reasoning", "?")
        if "chat-template-kwargs" in p:
            reasoning += f" {p['chat-template-kwargs']}"
        spec = p.get("spec-type", "none")
        if spec != "none":
            n_max = p.get("spec-draft-n-max", "")
            spec = f"{spec} n≤{n_max}" if n_max else spec
        lines.append(f"| `{alias}` | {model} | {ctx} | {kv} | {reasoning} | {spec} |")
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ini", type=Path, default=DEFAULT_INI)
    ap.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--print", action="store_true", dest="print_only")
    args = ap.parse_args()

    if not args.ini.is_file():
        print(f"error: {args.ini} not found", file=sys.stderr)
        return 2

    table = render_table(parse_presets(args.ini), args.ini)

    if args.print_only:
        print(table)
        return 0

    doc = args.doc.read_text(encoding="utf-8")
    if BEGIN not in doc or END not in doc:
        print(f"error: markers not found in {args.doc}", file=sys.stderr)
        return 2

    head, rest = doc.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    new_doc = head + table + tail

    if args.check:
        if new_doc == doc:
            return 0
        print(f"DRIFT: {args.doc} alias table is stale vs {args.ini}", file=sys.stderr)
        print("run: scripts/gen_router_doc.py", file=sys.stderr)
        return 1

    if new_doc != doc:
        args.doc.write_text(new_doc, encoding="utf-8")
        print(f"updated {args.doc}")
    else:
        print("already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
