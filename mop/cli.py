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
from .rules import Rule, _rule_matches
from .types import Accepted, Evaluator, Rejected, Rewritten, Verdict

EXIT_ACCEPTED = 0
EXIT_REWRITTEN = 1
EXIT_REJECTED = 2
EXIT_ERROR = 3


_DETERMINISTIC = ("regex", "script", "length")


def _deterministic_hits(text: str, rules: list[Rule]) -> list[str]:
    """Names of active deterministic rules that fire on `text` (authoritative)."""
    return [
        r.name
        for r in rules
        if r.detector in _DETERMINISTIC and _rule_matches(r, text)
    ]


def _llm_unresolved(verdict: Verdict) -> list[str]:
    """The rule names an evaluator verdict left unresolved."""
    if isinstance(verdict, (Rejected, Rewritten)):
        return list(verdict.unresolved)
    return []


async def check(
    text: str,
    rules: list[Rule],
    evaluator: Evaluator,
    *,
    justification: str | None = None,
    allow_rewrite: bool = True,
) -> Verdict:
    """One-shot check: deterministic rules are authoritative, then one LLM call.

    Two phases (D3/D4):
      1. Run every active ``regex``/``script`` rule. A match is a hard
         violation the LLM cannot wave away.
      2. One LLM call judges the ``llm`` rules and produces a best-effort
         rewrite that also removes the deterministic violations. The
         deterministic rules are re-checked against the rewrite, so a fix
         only counts if it actually cleared the pattern.

    The final verdict is DERIVED from (did-text-change?, is-unresolved-empty?).
    ``allow_rewrite=False`` (CLI ``--no-rewrite``) runs judgement but never
    applies a rewrite — verdict-only for CI/lint callers.
    """
    det_pre = _deterministic_hits(text, rules)
    has_llm = any(r.detector == "llm" for r in rules)
    if not det_pre and not has_llm:
        return Accepted()

    if not allow_rewrite:
        llm_unresolved = _llm_unresolved(
            await evaluator(text, det_pre, justification)
        ) if has_llm else []
        unresolved = _dedupe(det_pre + llm_unresolved)
        return Rejected(unresolved) if unresolved else Accepted()

    verdict = await evaluator(text, det_pre, justification)
    final_text = verdict.rewritten if isinstance(verdict, Rewritten) else text
    unresolved = _dedupe(_deterministic_hits(final_text, rules) + _llm_unresolved(verdict))
    changed = final_text.strip() != text.strip()
    if not unresolved:
        return Rewritten(final_text, unresolved=[]) if changed else Accepted()
    if changed:
        return Rewritten(final_text, unresolved=unresolved)
    return Rejected(unresolved)


def _dedupe(names: list[str]) -> list[str]:
    """Order-preserving dedupe."""
    return list(dict.fromkeys(names))


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


def _unresolved_payload(names: list[str], rules: list[Rule]) -> list[dict]:
    guidance_by_name = {r.name: r.guidance for r in rules}
    return [{"name": n, "guidance": guidance_by_name.get(n, "")} for n in names]


def _render(verdict: Verdict, rules: list[Rule], *, as_json: bool) -> int:
    if isinstance(verdict, Rewritten):
        unresolved = _unresolved_payload(verdict.unresolved, rules)
        payload = {
            "verdict": "rewritten",
            "rewritten": verdict.rewritten,
            "unresolved": unresolved,
        }
        human = f"rewritten\n\n{verdict.rewritten}"
        if unresolved:
            lines = "\n".join(f"  {v['name']}: {v['guidance']}" for v in unresolved)
            human += f"\n\nunresolved (fix these yourself):\n{lines}"
        code = EXIT_REWRITTEN
    elif isinstance(verdict, Rejected):
        unresolved = _unresolved_payload(verdict.unresolved, rules)
        payload = {"verdict": "rejected", "rewritten": None, "unresolved": unresolved}
        lines = "\n".join(f"  {v['name']}: {v['guidance']}" for v in unresolved)
        human = f"rejected\n{lines}"
        code = EXIT_REJECTED
    else:
        # Accepted. (AcceptedFailedOpen is unreachable in one-shot mode —
        # it only arises from the MCP adapter's justification budget.)
        payload = {"verdict": "accepted", "rewritten": None, "unresolved": []}
        human = "accepted"
        code = EXIT_ACCEPTED
    print(json.dumps(payload) if as_json else human)
    return code


def _run_check(args: argparse.Namespace, rules: list[Rule]) -> int:
    if not rules:
        print("no active rules — MOP enforced nothing", file=sys.stderr)
    if args.rule:
        rules = [r for r in rules if r.name == args.rule]
        if not rules:
            raise ValueError(f"No active rule named {args.rule!r}.")
    text = _read_input(args)
    evaluator = build_evaluator(rules=rules, model=args.model)
    verdict = asyncio.run(
        check(
            text,
            rules,
            evaluator,
            justification=args.justify,
            allow_rewrite=not args.no_rewrite,
        )
    )
    return _render(verdict, rules, as_json=args.as_json)


class _MOPArgumentParser(argparse.ArgumentParser):
    """Custom parser that maps usage errors to EXIT_ERROR instead of exit 2."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        args = {"prog": self.prog, "message": message}
        self.exit(EXIT_ERROR, f"%(prog)s: error: %(message)s\n" % args)


def _build_parser() -> argparse.ArgumentParser:
    parser = _MOPArgumentParser(
        prog="mop", description="MOP — check text against model-output rules."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check_p = sub.add_parser(
        "check",
        help="Evaluate text; exit 0/1/2/3.",
        description=(
            "Evaluate text against local .mop/ rules. Built-ins are OFF by "
            "default — pass --builtins to include the packaged rule set. With "
            "no rules at all, MOP warns and accepts (enforces nothing)."
        ),
    )
    check_p.add_argument("text", nargs="?", help="Text to check (or use --file/stdin)")
    check_p.add_argument("--file", type=Path, help="Read the text from a file")
    check_p.add_argument("--builtins", action="store_true", help="Include packaged built-in rules (default: off)")
    check_p.add_argument("--rules-dir", type=Path, help="Explicit rules dir (skips discovery)")
    check_p.add_argument("--rules-file", type=Path, help="Explicit rules file (skips discovery)")
    check_p.add_argument("--rule", help="Restrict evaluation to one named rule")
    check_p.add_argument("--model", help="litellm model string (default: MOP_EVALUATOR_MODEL)")
    check_p.add_argument("--justify", metavar="REASON", help="Attach a justification")
    check_p.add_argument(
        "--no-rewrite",
        action="store_true",
        help="Judge only; never apply a rewrite (verdict-only, for CI/lint)",
    )
    check_p.add_argument("--json", action="store_true", dest="as_json")

    rules_p = sub.add_parser("rules", help="List or show rules.")
    rules_p.add_argument("--builtins", action="store_true", help="Include packaged built-in rules (default: off)")
    rules_p.add_argument("--rules-dir", type=Path, help="Explicit rules dir (skips discovery)")
    rules_p.add_argument("--rules-file", type=Path, help="Explicit rules file (skips discovery)")
    rules_p.add_argument("--json", action="store_true", dest="as_json")
    rules_sub = rules_p.add_subparsers(dest="rules_command", required=False)
    rules_p.set_defaults(rules_command="list")

    list_p = rules_sub.add_parser("list", help="List all resolved rules.")
    list_p.add_argument("--builtins", action="store_true", default=argparse.SUPPRESS)
    list_p.add_argument("--rules-dir", type=Path, default=argparse.SUPPRESS)
    list_p.add_argument("--rules-file", type=Path, default=argparse.SUPPRESS)
    list_p.add_argument("--json", action="store_true", dest="as_json",
                        default=argparse.SUPPRESS)

    show_p = rules_sub.add_parser("show", help="Show details of one rule.")
    show_p.add_argument("--builtins", action="store_true", default=argparse.SUPPRESS)
    show_p.add_argument("--rules-dir", type=Path, default=argparse.SUPPRESS)
    show_p.add_argument("--rules-file", type=Path, default=argparse.SUPPRESS)
    show_p.add_argument("--json", action="store_true", dest="as_json",
                        default=argparse.SUPPRESS)
    show_p.add_argument("name", help="Rule name to show")

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
            print(f"Rules ({len(all_rules)}):")
            print("  " + "-" * 78)
            print("  " + f"{'Name':<42s} {'Status':<10s} {'Kind':<14s} Source")
            print("  " + "-" * 78)
            for r in all_rules:
                status = "active" if r.active else "inactive"
                kind = "lint" if r.lint else r.detector
                print(f"  {r.name:<42s} {status:<10s} {kind:<14s} {r.source_file}")
                guidance = (r.guidance or "").strip()
                if guidance:
                    wrapped = textwrap.fill(guidance, width=72, initial_indent="      ", subsequent_indent="      ")
                    print(wrapped)
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
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse error() → self.exit(EXIT_ERROR) raises SystemExit(EXIT_ERROR)
        if e.code == EXIT_ERROR:
            return EXIT_ERROR
        raise  # code 0 (e.g. --help) propagates normally
    try:
        if args.command == "rules":
            rules = resolve_rules(
                rules_dir=args.rules_dir,
                rules_file=args.rules_file,
                use_builtins=getattr(args, "builtins", False),
            )
            return _run_rules(args, rules)
        # check command
        rules = resolve_rules(
            rules_dir=args.rules_dir,
            rules_file=args.rules_file,
            use_builtins=args.builtins,
        )
        return _run_check(args, rules)
    except Exception as exc:
        print(f"mop: {exc}", file=sys.stderr)
        return EXIT_ERROR
