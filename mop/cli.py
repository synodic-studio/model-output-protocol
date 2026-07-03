"""MOP CLI adapter — stateless one-shot checks over the shared core.

``mop check`` evaluates one piece of text against the resolved rule set
(packaged built-ins + discovered/explicit local layer) and reports the
verdict. Unlike the stateful MCP adapter, there is no pending message
and no justification-attempt budget: ``--justify`` attaches a
justification to the single evaluation call.

``mop rules list`` shows the resolved rule set.
``mop rules show <name>`` shows details of one rule.

Exit codes: 0 accepted, 1 rewritten, 2 rejected, 3 usage/runtime error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

from .discovery import resolve_rules
from .evaluators import build_evaluator
from .rules import Rule, collect_lint_hints
from .types import Accepted, Evaluator, Rejected, Rewritten, Verdict

EXIT_ACCEPTED = 0
EXIT_REWRITTEN = 1
EXIT_REJECTED = 2
EXIT_ERROR = 3


async def check(
    text: str,
    rules: list[Rule],
    evaluator: Evaluator,
    *,
    justification: str | None = None,
) -> Verdict:
    """Pure one-shot check: lint hints + one evaluator call, raw Verdict out.

    ``rules`` must be the same resolved set the evaluator was built with —
    evaluators bake rules into their prompt; this parameter feeds lint-hint
    collection (and, in the CLI renderer, guidance lookup).
    """
    hints = collect_lint_hints(text, rules)
    return await evaluator(text, hints, justification)


def _read_input(args: argparse.Namespace) -> str:
    if args.text is not None and args.file is not None:
        raise ValueError("Pass TEXT or --file, not both.")
    if args.text is not None:
        return args.text
    if args.file is not None:
        return Path(args.file).read_text()
    if sys.stdin.isatty():
        raise ValueError("No input: pass TEXT, --file, or pipe text on stdin.")
    text = sys.stdin.read()
    if not text.strip():
        raise ValueError("Empty input.")
    return text


def _violations_payload(names: list[str], rules: list[Rule]) -> list[dict]:
    guidance_by_name = {r.name: r.guidance for r in rules}
    return [{"name": n, "guidance": guidance_by_name.get(n, "")} for n in names]


def _render(verdict: Verdict, rules: list[Rule], *, as_json: bool) -> int:
    if isinstance(verdict, Rewritten):
        payload = {
            "verdict": "rewritten",
            "rewritten": verdict.rewritten,
            "violations": [],
        }
        human = f"rewritten\n\n{verdict.rewritten}"
        code = EXIT_REWRITTEN
    elif isinstance(verdict, Rejected):
        violations = _violations_payload(verdict.violations, rules)
        payload = {"verdict": "rejected", "rewritten": None, "violations": violations}
        lines = "\n".join(f"  {v['name']}: {v['guidance']}" for v in violations)
        human = f"rejected\n{lines}"
        code = EXIT_REJECTED
    else:
        # Accepted. (AcceptedFailedOpen is unreachable in one-shot mode —
        # it only arises from the MCP adapter's justification budget.)
        payload = {"verdict": "accepted", "rewritten": None, "violations": []}
        human = "accepted"
        code = EXIT_ACCEPTED
    print(json.dumps(payload) if as_json else human)
    return code


def _run_check(args: argparse.Namespace, rules: list[Rule]) -> int:
    if args.rule:
        rules = [r for r in rules if r.name == args.rule]
        if not rules:
            raise ValueError(f"No active rule named {args.rule!r}.")
    text = _read_input(args)
    evaluator = build_evaluator(rules=rules, model=args.model)
    verdict = asyncio.run(
        check(text, rules, evaluator, justification=args.justify)
    )
    return _render(verdict, rules, as_json=args.as_json)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mop", description="MOP — check text against model-output rules."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser("check", help="Evaluate text; exit 0/1/2/3.")
    check_p.add_argument("text", nargs="?", help="Text to check (or use --file/stdin)")
    check_p.add_argument("--file", type=Path, help="Read the text from a file")
    check_p.add_argument("--rules-dir", type=Path, help="Explicit rules dir (skips discovery)")
    check_p.add_argument("--rules-file", type=Path, help="Explicit rules file (skips discovery)")
    check_p.add_argument("--rule", help="Restrict evaluation to one named rule")
    check_p.add_argument("--model", help="litellm model string (default: MOP_EVALUATOR_MODEL)")
    check_p.add_argument("--justify", metavar="REASON", help="Attach a justification")
    check_p.add_argument("--json", action="store_true", dest="as_json")

    rules_p = sub.add_parser("rules", help="List or show rules.")
    rules_sub = rules_p.add_subparsers(dest="rules_command", required=True)

    list_p = rules_sub.add_parser("list", help="List all resolved rules.")
    list_p.add_argument("--rules-dir", type=Path, help="Explicit rules dir (skips discovery)")
    list_p.add_argument("--rules-file", type=Path, help="Explicit rules file (skips discovery)")
    list_p.add_argument("--json", action="store_true", dest="as_json")

    show_p = rules_sub.add_parser("show", help="Show details of one rule.")
    show_p.add_argument("name", help="Rule name to show")
    show_p.add_argument("--rules-dir", type=Path, help="Explicit rules dir (skips discovery)")
    show_p.add_argument("--rules-file", type=Path, help="Explicit rules file (skips discovery)")
    show_p.add_argument("--json", action="store_true", dest="as_json")

    return parser


def _run_rules(args: argparse.Namespace, all_rules: list[Rule]) -> int:
    if args.rules_command == "list":
        if args.as_json:
            payload = [
                {
                    "name": r.name,
                    "detector": r.detector,
                    "lint": r.lint,
                    "active": r.active,
                    "source_file": r.source_file,
                    "guidance": r.guidance,
                }
                for r in all_rules
            ]
            print(json.dumps(payload))
        else:
            lines = []
            for r in all_rules:
                status = "active" if r.active else "inactive"
                kind = "lint" if r.lint else r.detector
                lines.append(f"  {r.name:<42s} {status:<10s} {kind:<14s} {r.source_file}")
            print(f"Rules ({len(all_rules)}):")
            print("  " + "-" * 78)
            print("  " + f"{'Name':<42s} {'Status':<10s} {'Kind':<14s} Source")
            print("  " + "-" * 78)
            for line in lines:
                print(line)
        return EXIT_ACCEPTED

    # rules show
    matches = [r for r in all_rules if r.name == args.name]
    if not matches:
        print(f"mop: no rule named {args.name!r}", file=sys.stderr)
        return EXIT_ERROR
    rule = matches[0]
    if args.as_json:
        print(json.dumps(asdict(rule)))
    else:
        wrapped = textwrap.fill(rule.guidance, width=72, initial_indent="  ", subsequent_indent="  ")
        print(f"  Name:        {rule.name}")
        print(f"  Detector:    {rule.detector}")
        print(f"  Lint:        {rule.lint}")
        print(f"  Active:      {rule.active}")
        print(f"  Source:      {rule.source_file}")
        print(f"  Parameters:  {json.dumps(rule.parameters)}")
        print(f"  Guidance:\n{wrapped}")
    return EXIT_ACCEPTED


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "rules":
            rules = resolve_rules(rules_dir=args.rules_dir, rules_file=args.rules_file)
            return _run_rules(args, rules)
        # check command
        rules = resolve_rules(rules_dir=args.rules_dir, rules_file=args.rules_file)
        return _run_check(args, rules)
    except Exception as exc:  # argparse errors exit(2) on their own before this
        print(f"mop: {exc}", file=sys.stderr)
        return EXIT_ERROR
