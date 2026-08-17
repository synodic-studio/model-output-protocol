#!/usr/bin/env python3
"""Generate a Claude Code Stop-hook that runs MOP's rules as a prompt hook.

CC's prompt-based hooks feed a prompt to CC's own model (Haiku) and act on
the verdict — so this lets MOP judge each turn's final message with NO model
dispatched by MOP itself. The trade-off vs. `mop check`: a Stop hook fires
*after* the message is already on screen and has no rewrite channel, so this
is a corrective gate — it BLOCKS the turn and tells the agent what to change,
never rewrites. That matches MOP's `reject` disposition; the `rewrite` path
does not survive into this host (see docs/ + ADR on host shapes).

Because of that, only `reject`-disposition rules are emitted by default: a
`rewrite` rule asks for a wording change this host has no channel to make, so
blocking a turn over one spends the user's attention on something the gate was
supposed to absorb. `--include-rewrite` restores them for callers who want the
agent to revise its own prose.

Usage:
  python scripts/gen_cc_hook.py [--builtins] [--rules-dir DIR] [--llm-only]
      > .claude/settings.json      # (or merge the "Stop" block into yours)

The prompt is static once emitted, and CC loads hooks at session start, so
regenerate and restart CC when rules change.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mop.discovery import resolve_rules

PREAMBLE = (
    "You are the MOP output gate for a Claude Code session. Below are the "
    "active communication rules. Review the assistant's MOST RECENT message "
    "to the user (the one it just produced before trying to end its turn) "
    "against every rule.\n\n"
    "A rule fires ONLY on a clear violation of that message. When in doubt, "
    "do not fire. Do not judge the task, the code, or earlier turns — only "
    "whether this final message complies.\n\n"
    "ACTIVE RULES:\n"
)

INSTRUCTION = (
    "\nDecision:\n"
    "- If NO rule is violated, APPROVE and let the turn end.\n"
    "- If one or more rules are violated, BLOCK. In the reason, for each "
    "violated rule give (a) its name and (b) the specific change the "
    "assistant must make to comply. Be concrete and short.\n\n"
    "Never rewrite the message yourself. State what to fix; the assistant "
    "revises on its next turn. Do not block for anything other than a listed "
    "rule.\n"
)


def build_prompt(rules) -> str:
    rule_lines = "\n".join(
        f"  - {r.name} [{r.disposition}]: {(r.guidance or '').strip()}"
        for r in rules
    ) or "  (no active rules)"
    return PREAMBLE + rule_lines + "\n" + INSTRUCTION


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--builtins", action="store_true", help="include packaged built-in rules")
    ap.add_argument("--rules-dir", type=Path, default=None, help="explicit rules dir (bypass discovery)")
    ap.add_argument("--llm-only", action="store_true", help="only include detector: llm rules")
    ap.add_argument(
        "--include-rewrite",
        action="store_true",
        help="also emit rewrite-disposition rules (default: reject only)",
    )
    ap.add_argument("--timeout", type=int, default=30, help="hook timeout seconds")
    args = ap.parse_args()

    rules = resolve_rules(rules_dir=args.rules_dir, use_builtins=args.builtins)
    rules = [r for r in rules if getattr(r, "active", True)]
    if args.llm_only:
        rules = [r for r in rules if r.detector == "llm"]
    if not args.include_rewrite:
        rules = [r for r in rules if r.disposition == "reject"]
    if not rules:
        raise SystemExit(
            "No rules to emit. A Stop hook can only block, so it needs at least "
            "one reject-disposition rule; pass --include-rewrite to emit the "
            "rewrite ones anyway."
        )

    settings = {
        "hooks": {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "prompt",
                            "prompt": build_prompt(rules),
                            "timeout": args.timeout,
                        }
                    ],
                }
            ]
        }
    }
    print(json.dumps(settings, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
