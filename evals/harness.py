"""MOP eval harness — rules vs counterexample corpus.

Loads every rule from rules/**/*.yml and every counterexample from
counterexamples/**/*.yml, then evaluates each deterministic rule against
each example and reports mismatches between expected and observed
violations.

LLM-based rules are listed in the summary but skipped. They require a
Haiku call in the loop and will be wired in when the filter pipeline
lands.

Usage:
    python harness.py                # all rules vs all counterexamples
    python harness.py --rule <name>  # one rule
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


@dataclass
class Rule:
    name: str
    detector: str
    parameters: dict
    severity: str
    source_file: str

    @property
    def is_deterministic(self) -> bool:
        return self.detector == "deterministic"


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
    """Return True if the rule fires on the text."""
    params = rule.parameters
    detector_type = params.get("type")

    if detector_type == "regex":
        for pattern in params.get("patterns", []):
            if re.search(pattern, text):
                return True
        return False

    if detector_type == "word_count":
        max_words = params.get("max", 0)
        return len(text.split()) > max_words

    raise ValueError(
        f"Unknown deterministic detector type {detector_type!r} on rule "
        f"{rule.name!r} (from {rule.source_file})"
    )


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
                # Fired but not expected. Only a mismatch if explicitly
                # listed as clean OR if any expected_violations are set
                # (i.e., this example is annotated). Otherwise we can't
                # say if it's a false positive.
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

    summary = {
        "rules_total": len(rules),
        "rules_deterministic": len(deterministic_rules),
        "rules_llm_skipped": len(llm_rules),
        "examples": len(examples),
        "correct": correct,
        "mismatches": len(mismatches),
        "untested_pairs": skipped_pairs,
    }

    if args.json:
        print(
            json.dumps(
                {"summary": summary, "mismatches": mismatches},
                indent=2,
            )
        )
        return 0 if not mismatches else 1

    print(f"Rules:               {summary['rules_total']}")
    print(f"  deterministic:     {summary['rules_deterministic']}")
    print(f"  llm (skipped):     {summary['rules_llm_skipped']}")
    print(f"Counterexamples:     {summary['examples']}")
    print(f"Correct (matched):   {summary['correct']}")
    print(f"Mismatches:          {summary['mismatches']}")
    print(f"Untested pairs:      {summary['untested_pairs']}")
    print()

    if mismatches:
        print("MISMATCHES:")
        for m in mismatches:
            print(f"  [{m['type']}] {m['rule']} vs {m['example']}")
            if args.verbose:
                example = next(e for e in examples if e.id == m["example"])
                snippet = example.text.strip().splitlines()[0][:80]
                print(f"      {snippet}")

    if llm_rules and args.verbose:
        print()
        print("LLM rules (not evaluated by harness):")
        for r in llm_rules:
            print(f"  {r.name}  ({r.source_file})")

    return 0 if not mismatches else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rule", help="Evaluate only this rule by name")
    parser.add_argument(
        "--verbose", action="store_true", help="Show example text on mismatch"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
