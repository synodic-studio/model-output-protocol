"""Display metrics for MOP — pure-function measurements of an outbound message.

Metrics feed deterministic rule detectors and serve as standalone telemetry
for audit logs. No LLM calls, no opinions; just measurements.

CLI usage:
    python display_metrics.py "your message here"
    echo "your message" | python display_metrics.py -

Returns JSON with all available metrics.
"""

from __future__ import annotations

import argparse
import json
import re
import sys


CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
LIST_ITEM_RE = re.compile(
    r"^\s*(?:[-*+]|\d+[.)]|[a-zA-Z][.)])\s+", re.MULTILINE
)
EM_DASH_RE = re.compile(r"—")
URL_RE = re.compile(r"https?://\S+")

# Telegram iOS rendering norms, calibrated against Adrien's setup at 85% text size.
# At 100% iOS scale: ~33 chars per line in both monospace and proportional (x-width baseline).
# At 85% scale: ~39 x-width units per line. 90% safety margin gives the working norms below.
# These can be tuned per-channel; values here are for the chat-display path.
PROSE_LINE_WIDTH = 35
MONO_LINE_WIDTH = 37
SCREEN_LINE_BUDGET = 32


def word_count(text: str) -> int:
    return len(text.split())


def char_count(text: str) -> int:
    return len(text)


def line_count(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def max_line_width(text: str) -> int:
    return max((len(line) for line in text.splitlines()), default=0)


def rendered_line_count(
    text: str,
    prose_width: int = PROSE_LINE_WIDTH,
    mono_width: int = MONO_LINE_WIDTH,
) -> int:
    """Estimate rendered height after wrapping.

    Each newline is one line. Long lines wrap at prose_width, or mono_width
    inside ``` fences. Empty lines count as 1.
    """
    if not text:
        return 0
    in_code = False
    total = 0
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            total += 1
            continue
        width = mono_width if in_code else prose_width
        if not line:
            total += 1
        else:
            total += -(-len(line) // width)
    return total


def overflows_screen(text: str, budget: int = SCREEN_LINE_BUDGET) -> bool:
    return rendered_line_count(text) > budget


def list_item_count(text: str) -> int:
    return len(LIST_ITEM_RE.findall(text))


def code_block_chars(text: str) -> int:
    return sum(len(m) for m in CODE_BLOCK_RE.findall(text))


def code_block_ratio(text: str) -> float:
    if not text:
        return 0.0
    return code_block_chars(text) / len(text)


def em_dash_count(text: str) -> int:
    return len(EM_DASH_RE.findall(text))


def url_count(text: str) -> int:
    return len(URL_RE.findall(text))


def compute_all(text: str) -> dict:
    """Return every metric in a single dict."""
    return {
        "word_count": word_count(text),
        "char_count": char_count(text),
        "line_count": line_count(text),
        "max_line_width": max_line_width(text),
        "rendered_line_count": rendered_line_count(text),
        "overflows_screen": overflows_screen(text),
        "list_item_count": list_item_count(text),
        "code_block_chars": code_block_chars(text),
        "code_block_ratio": round(code_block_ratio(text), 4),
        "em_dash_count": em_dash_count(text),
        "url_count": url_count(text),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute display metrics for an outbound MOP message."
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

    print(json.dumps(compute_all(text), indent=2))


if __name__ == "__main__":
    main()
