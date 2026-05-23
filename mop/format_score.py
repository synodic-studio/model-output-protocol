"""Format score for MOP — composite penalty for outbound message shape.

Three independent penalty terms:

  wrap_penalty       per-source-line cost when a single line wraps to multiple
                     rendered lines. Quadratic: 0, 0.5, 2, 4.5, 8 for 1..5
                     rendered lines.

  height_penalty     whole-message cost as total rendered line count climbs.
                     Free under half-budget, linear, then a cliff past the
                     screen budget.

  structure_penalty  cost for plain-prose source lines past the free
                     allowance. List items, headers, blockquotes, code
                     fences, and blank lines are exempt.

format_score = wrap_penalty + height_penalty + structure_penalty

Calibration is deliberate but unverified. Run ranking tests against real
messages before trusting absolute values; the *order* of scores between
messages is the contract, not the magnitudes.

CLI usage:
    python format_score.py "your message here"
    echo "your message" | python format_score.py -
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from mop.display_metrics import (
    MONO_LINE_WIDTH,
    PROSE_LINE_WIDTH,
    SCREEN_LINE_BUDGET,
)


PROSE_FREE_LINES = 4
HEIGHT_FREE_LINES = SCREEN_LINE_BUDGET // 2
HEIGHT_LINEAR_RATE = 0.5
HEIGHT_CLIFF_COEFFICIENT = 10.0
WRAP_QUADRATIC_COEFFICIENT = 0.5

LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)]|[a-zA-Z][.)])\s+")
HEADER_RE = re.compile(r"^\s*#{1,6}\s+")
BLOCKQUOTE_RE = re.compile(r"^\s*>")
CODE_FENCE_RE = re.compile(r"^\s*```")


def _wrapped_line_count(line: str, width: int) -> int:
    if not line:
        return 1
    return -(-len(line) // width)


def _is_structured(line: str, prev_was_list_item: bool) -> bool:
    """A line counts as structured (no prose penalty) when it has a marker,
    is a header, blockquote, code fence, blank, or is an indented continuation
    of the previous list item.
    """
    if not line.strip():
        return True
    if LIST_MARKER_RE.match(line):
        return True
    if HEADER_RE.match(line):
        return True
    if BLOCKQUOTE_RE.match(line):
        return True
    if CODE_FENCE_RE.match(line):
        return True
    if prev_was_list_item and line.startswith((" ", "\t")):
        return True
    return False


def wrap_penalty(text: str) -> float:
    if not text:
        return 0.0
    in_code = False
    total = 0.0
    for line in text.split("\n"):
        if CODE_FENCE_RE.match(line):
            in_code = not in_code
            continue
        width = MONO_LINE_WIDTH if in_code else PROSE_LINE_WIDTH
        wrapped = _wrapped_line_count(line, width)
        if wrapped > 1:
            total += WRAP_QUADRATIC_COEFFICIENT * (wrapped - 1) ** 2
    return total


def total_rendered_lines(text: str) -> int:
    if not text:
        return 0
    in_code = False
    total = 0
    for line in text.split("\n"):
        if CODE_FENCE_RE.match(line):
            in_code = not in_code
            total += 1
            continue
        width = MONO_LINE_WIDTH if in_code else PROSE_LINE_WIDTH
        total += _wrapped_line_count(line, width)
    return total


def height_penalty(text: str, budget: int = SCREEN_LINE_BUDGET) -> float:
    rendered = total_rendered_lines(text)
    if rendered <= HEIGHT_FREE_LINES:
        return 0.0
    if rendered <= budget:
        return (rendered - HEIGHT_FREE_LINES) * HEIGHT_LINEAR_RATE
    over_budget_term = HEIGHT_CLIFF_COEFFICIENT * (rendered - budget) ** 2
    cliff_base = (budget - HEIGHT_FREE_LINES) * HEIGHT_LINEAR_RATE
    return cliff_base + over_budget_term


def structure_penalty(text: str, free: int = PROSE_FREE_LINES) -> float:
    if not text:
        return 0.0
    in_code = False
    prev_was_list_item = False
    prose_count = 0
    for line in text.split("\n"):
        if CODE_FENCE_RE.match(line):
            in_code = not in_code
            prev_was_list_item = False
            continue
        if in_code:
            continue
        if _is_structured(line, prev_was_list_item):
            prev_was_list_item = bool(LIST_MARKER_RE.match(line))
            continue
        prose_count += 1
        prev_was_list_item = False
    return float(max(0, prose_count - free))


def format_score(text: str) -> dict:
    wrap = wrap_penalty(text)
    height = height_penalty(text)
    structure = structure_penalty(text)
    return {
        "wrap_penalty": round(wrap, 3),
        "height_penalty": round(height, 3),
        "structure_penalty": round(structure, 3),
        "format_score": round(wrap + height + structure, 3),
        "rendered_lines": total_rendered_lines(text),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute the composite MOP format score for a message."
    )
    parser.add_argument(
        "text",
        nargs="?",
        default=None,
        help="Message text. Pass '-' or omit to read from stdin.",
    )
    args = parser.parse_args()

    if args.text is None or args.text == "-":
        text = sys.stdin.read()
    else:
        text = args.text

    print(json.dumps(format_score(text), indent=2))


if __name__ == "__main__":
    main()
