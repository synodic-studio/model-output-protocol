"""MOP eval harness — rules vs counterexample corpus.

Loads every rule from rules/**/*.yml and every counterexample from
counterexamples/**/*.yml, then evaluates each deterministic rule against
each example and reports mismatches between expected and observed
violations.

LLM-based rules are skipped by default. Pass --llm to evaluate them
against the counterexample corpus using the configured LLM evaluator
(deepseek by default).

Usage:
    python harness.py                # all deterministic rules vs all examples
    python harness.py --rule <name>  # one rule (deterministic or LLM)
    python harness.py --llm          # also evaluate LLM rules
    python harness.py --verbose      # show example text on mismatch
    python harness.py --json         # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "PyYAML required. Install with `uv add pyyaml` or `pip install pyyaml`.\n"
    )
    sys.exit(1)


import os

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = Path(os.environ.get("MOP_RULES_DIR", REPO_ROOT / "rules"))
CORPUS_DIR = Path(os.environ.get("MOP_EVALS_CORPUS", Path(__file__).resolve().parent / "counterexamples"))


DETERMINISTIC_DETECTORS = ("regex", "script", "length")


@dataclass
class Rule:
    name: str
    detector: str
    parameters: dict
    severity: str
    source_file: str

    @property
    def is_deterministic(self) -> bool:
        return self.detector in DETERMINISTIC_DETECTORS


@dataclass
class Counterexample:
    id: str
    text: str
    expected_violations: list[str]
    expected_clean: list[str]
    labels: list[str]
    source: str
    rationale: str
    file_path: str


def load_rules(rules_dir: Path) -> list[Rule]:
    rules: list[Rule] = []
    for path in sorted(rules_dir.rglob("*.yml")):
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("rules", []):
            rules.append(
                Rule(
                    name=entry["name"],
                    detector=entry["detector"],
                    parameters=entry.get("parameters", {}),
                    severity=entry.get("severity", "warn"),
                    source_file=str(path.relative_to(REPO_ROOT)),
                )
            )
    return rules


def load_counterexamples(corpus_dir: Path) -> list[Counterexample]:
    examples: list[Counterexample] = []
    for path in sorted(corpus_dir.rglob("*.yml")):
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        examples.append(
            Counterexample(
                id=data["id"],
                text=data["text"],
                expected_violations=data.get("expected_violations", []) or [],
                expected_clean=data.get("expected_clean", []) or [],
                labels=data.get("labels", []) or [],
                source=data.get("source", "synthetic"),
                rationale=data.get("rationale", ""),
                file_path=str(path.relative_to(REPO_ROOT)),
            )
        )
    return examples


def evaluate_deterministic(rule: Rule, text: str) -> bool:
    """Return True if the rule fires on the text.

    Delegates to the real ``mop.rules._rule_matches`` so the harness and the
    shipped gate can never drift on detector semantics again (regex / script /
    length are all defined in one place).
    """
    from mop.rules import Rule as MopRule
    from mop.rules import _rule_matches

    return _rule_matches(
        MopRule(
            name=rule.name,
            detector=rule.detector,
            parameters=rule.parameters,
            guidance="",
            source_file=rule.source_file,
        ),
        text,
    )


def run_llm_eval(
    rules: list[Rule],
    examples: list[Counterexample],
    verbose: bool,
) -> tuple[list[dict], int]:
    """Evaluate LLM rules against counterexamples.

    Uses the real ``mop.rules.Rule`` instances (with ``guidance`` and
    ``lint`` fields) via ``mop.evaluators.build_evaluator``.

    Returns (mismatches, correct_count).
    """
    # Import the real MOP rules loader to get LLM rules with guidance
    from mop.evaluators import build_evaluator
    from mop.rules import load_rules as mop_load_rules
    from mop.types import Accepted, Rejected, Rewritten

    def _unresolved(verdict) -> list[str]:
        """Rule names a derived verdict left unresolved (Rejected or partial Rewritten)."""
        if isinstance(verdict, (Rejected, Rewritten)):
            return list(verdict.unresolved)
        return []

    # include_inactive so a freshly-drafted rule (shipped active: false) can be
    # evaluated; restrict to the caller's (possibly --rule-filtered) set.
    wanted = {r.name for r in rules}
    real_rules = mop_load_rules(RULES_DIR, include_inactive=True)
    llm_rules = [
        r for r in real_rules if r.detector == "llm" and r.name in wanted
    ]

    if not llm_rules:
        print("No LLM rules found.")
        return [], 0

    # One evaluator per rule, so a rewrite/rejection is attributable to THAT
    # rule (the evaluator only knows about it). Under the best-effort-rewrite
    # model a rule "fired" when the message was not accepted as-is — a *clean
    # rewrite* means the rule fired and the evaluator fixed it, which the old
    # "rule name in unresolved" check wrongly scored as a miss.
    evaluators = {r.name: build_evaluator(rules=[r]) for r in llm_rules}

    mismatches: list[dict] = []
    correct = 0

    print(f"\n--- LLM Eval ({len(llm_rules)} rules, {len(examples)} examples) ---")

    import asyncio

    for example in examples:
        for rr in llm_rules:
            should_violate = rr.name in example.expected_violations
            should_be_clean = rr.name in example.expected_clean
            if not should_violate and not should_be_clean:
                continue  # example doesn't annotate this rule

            try:
                verdict = asyncio.run(evaluators[rr.name](example.text, [], None))
            except Exception as exc:
                mismatches.append(
                    {
                        "type": "eval_error",
                        "rule": rr.name,
                        "example": example.id,
                        "file": example.file_path,
                        "error": str(exc),
                    }
                )
                continue

            fired = not isinstance(verdict, Accepted)  # rewrote or rejected

            if fired and should_violate:
                correct += 1
            elif not fired and should_be_clean:
                correct += 1
            elif fired and should_be_clean:
                mismatches.append(
                    {
                        "type": "false_alarm",
                        "rule": rr.name,
                        "example": example.id,
                        "file": example.file_path,
                        "llm_violations": _unresolved(verdict) or ["<rewrote>"],
                    }
                )
            elif not fired and should_violate:
                mismatches.append(
                    {
                        "type": "missed",
                        "rule": rr.name,
                        "example": example.id,
                        "file": example.file_path,
                    }
                )

    return mismatches, correct


def run(args: argparse.Namespace) -> int:
    rules = load_rules(RULES_DIR)
    examples = load_counterexamples(CORPUS_DIR)

    if args.rule:
        rules = [r for r in rules if r.name == args.rule]
        if not rules:
            sys.stderr.write(f"No rule named {args.rule!r}.\n")
            return 2

    deterministic_rules = [r for r in rules if r.is_deterministic]
    llm_rules = [r for r in rules if not r.is_deterministic]

    mismatches: list[dict] = []
    correct = 0
    skipped_pairs = 0

    for example in examples:
        for rule in deterministic_rules:
            fired = evaluate_deterministic(rule, example.text)
            should_fire = rule.name in example.expected_violations
            should_not_fire = rule.name in example.expected_clean

            if fired and not should_fire and rule.name not in example.expected_violations:
                if should_not_fire or example.expected_violations:
                    mismatches.append(
                        {
                            "type": "false_positive",
                            "rule": rule.name,
                            "example": example.id,
                            "file": example.file_path,
                        }
                    )
                else:
                    skipped_pairs += 1
            elif not fired and should_fire:
                mismatches.append(
                    {
                        "type": "false_negative",
                        "rule": rule.name,
                        "example": example.id,
                        "file": example.file_path,
                    }
                )
            elif fired and should_fire:
                correct += 1
            elif not fired and should_not_fire:
                correct += 1
            else:
                skipped_pairs += 1

    llm_mismatches: list[dict] = []
    llm_correct = 0
    if args.llm and llm_rules:
        llm_mismatches, llm_correct = run_llm_eval(llm_rules, examples, args.verbose)

    summary = {
        "rules_total": len(rules),
        "rules_deterministic": len(deterministic_rules),
        "rules_llm": len(llm_rules),
        "examples": len(examples),
        "deterministic_correct": correct,
        "deterministic_mismatches": len(mismatches),
        "deterministic_untested": skipped_pairs,
        "llm_correct": llm_correct,
        "llm_mismatches": len(llm_mismatches),
    }

    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "deterministic_mismatches": mismatches,
                    "llm_mismatches": llm_mismatches,
                },
                indent=2,
            )
        )
        return 0 if not mismatches and not llm_mismatches else 1

    print(f"Rules:               {summary['rules_total']}")
    print(f"  deterministic:     {summary['rules_deterministic']}")
    print(f"  llm:               {summary['rules_llm']}")
    print(f"Counterexamples:     {summary['examples']}")
    print(f"")
    print(f"--- Deterministic ---")
    print(f"Correct:             {summary['deterministic_correct']}")
    print(f"Mismatches:          {summary['deterministic_mismatches']}")
    print(f"Untested pairs:      {summary['deterministic_untested']}")

    if mismatches:
        print()
        print("DETERMINISTIC MISMATCHES:")
        for m in mismatches:
            print(f"  [{m['type']}] {m['rule']} vs {m['example']}")
            if args.verbose:
                example = next(e for e in examples if e.id == m["example"])
                snippet = example.text.strip().splitlines()[0][:80]
                print(f"      {snippet}")

    if args.llm:
        print()
        print(f"--- LLM Eval ---")
        print(f"Correct:             {summary['llm_correct']}")
        print(f"Mismatches:          {summary['llm_mismatches']}")

        if llm_mismatches:
            print()
            print("LLM MISMATCHES:")
            for m in llm_mismatches:
                msg = f"  [{m['type']}] {m['rule']} vs {m['example']}"
                if "llm_violations" in m:
                    msg += f" (LLM said: {', '.join(m['llm_violations'])})"
                if "error" in m:
                    msg += f" (error: {m['error']})"
                print(msg)
                if args.verbose and "file" in m:
                    example = next(e for e in examples if e.id == m["example"])
                    snippet = example.text.strip().splitlines()[0][:80]
                    print(f"      {snippet}")

    if not args.llm and llm_rules:
        print()
        print(f"LLM rules ({len(llm_rules)}) skipped. Pass --llm to evaluate.")

    return 0 if not mismatches and not llm_mismatches else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rule", help="Evaluate only this rule by name")
    parser.add_argument(
        "--verbose", action="store_true", help="Show example text on mismatch"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Also evaluate LLM rules against the counterexample corpus",
    )
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
