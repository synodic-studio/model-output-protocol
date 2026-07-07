"""Harvest real assistant responses from patchbay-relay session logs.

Mines `~/.claude/projects/-Users-bryancostanza-Developer-patchbay-relay/*.jsonl`
for unique assistant text blocks, buckets them by likely-violation type via
cheap regex hints, picks a curated subset, and writes YAML counterexamples to
`evals/counterexamples/real-history/<bucket>/`.

Re-runnable: skips IDs that already exist on disk.

Usage:
    uv run scripts/harvest_telegram_examples.py
    uv run scripts/harvest_telegram_examples.py --dry-run
    uv run scripts/harvest_telegram_examples.py --per-bucket 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SESS_DIR = Path.home() / ".claude/projects/-Users-bryancostanza-Developer-patchbay-relay"
DEST_ROOT = REPO_ROOT / "evals" / "counterexamples" / "real-history"

# Regex pre-screen — these don't decide the verdict, they just bucket
# candidates so the curated subset has variety. Final labels are written into
# expected_violations and you can correct them by hand or via the eval harness.
PATTERNS: dict[str, re.Pattern[str]] = {
    "permission-asking": re.compile(
        r"\b(want me to|should I|do you want me to|would you like me to|shall I)\b",
        re.I,
    ),
    "cheerleading": re.compile(
        r"\b(absolutely right|great question|excellent point|you'?re right!?|"
        r"happy to|let me know if|that'?s a great)\b",
        re.I,
    ),
    "process-narration": re.compile(
        r"^(let me|i'?ll|i'?m going to|now i'?ll|alright,?|ok,?|right,?)\s",
        re.I,
    ),
    "completion-no-findings": re.compile(
        r"^(done|all set|fixed|complete|completed)[.!]?\s*$",
        re.I | re.M,
    ),
}

# bucket -> expected_violation rule names (from rules/)
EXPECTED_VIOLATIONS = {
    "permission-asking": ["no-permission-asking-for-doable-work"],
    "cheerleading": ["no-cheerleading-phrases"],
    "process-narration": ["no-empty-acknowledgment"],
    "completion-no-findings": ["completion-must-have-findings"],
    "cap-overflow": ["length-cap-chat"],
    "clean": [],
}


def slugify(text: str, max_len: int = 32) -> str:
    """First few words of the text, kebab-case, ASCII only."""
    words = re.findall(r"[a-z0-9]+", text.lower())[:6]
    slug = "-".join(words)[:max_len].rstrip("-")
    return slug or "untitled"


def harvest_unique_texts(sess_dir: Path) -> list[str]:
    seen: set[str] = set()
    texts: list[str] = []
    for f in sorted(sess_dir.glob("*.jsonl")):
        try:
            with f.open() as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    msg = d.get("message", {})
                    for blk in msg.get("content", []):
                        if blk.get("type") != "text":
                            continue
                        text = blk.get("text", "").strip()
                        if not text or len(text) < 10:
                            continue
                        h = hashlib.sha1(text.encode()).hexdigest()[:12]
                        if h in seen:
                            continue
                        seen.add(h)
                        texts.append(text)
        except OSError as e:
            print(f"skip {f}: {e}", file=sys.stderr)
    return texts


def bucket_texts(texts: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for t in texts:
        wc = len(t.split())
        matched = False
        for label, pat in PATTERNS.items():
            if pat.search(t):
                buckets[label].append(t)
                matched = True
                break  # first-match wins so each text lands in one bucket
        if not matched:
            if wc > 220:
                buckets["cap-overflow"].append(t)
            elif 30 < wc < 200:
                buckets["clean"].append(t)
    return buckets


def pick_diverse(items: list[str], n: int) -> list[str]:
    """Pick n items with length diversity — short, medium, long mix."""
    if len(items) <= n:
        return items
    sorted_by_len = sorted(items, key=lambda s: len(s.split()))
    step = len(sorted_by_len) / n
    return [sorted_by_len[int(i * step)] for i in range(n)]


def write_example(
    bucket: str,
    text: str,
    *,
    dest_root: Path,
    dry_run: bool,
) -> Path | None:
    bucket_dir = dest_root / bucket
    slug = slugify(text)
    dest = bucket_dir / f"{slug}.yml"
    # de-dupe across runs
    suffix = 2
    while dest.exists():
        existing = yaml.safe_load(dest.read_text()) or {}
        if existing.get("text", "").strip() == text.strip():
            return None  # same content already saved
        dest = bucket_dir / f"{slug}-{suffix}.yml"
        suffix += 1
    doc = {
        "id": dest.stem,
        "source": "real-telegram",
        "labels": ["real", bucket],
        "text": text + ("\n" if not text.endswith("\n") else ""),
        "expected_violations": EXPECTED_VIOLATIONS.get(bucket, []),
        "expected_clean": [],
        "rationale": (
            f"Real assistant response harvested from patchbay-relay session logs. "
            f"Pre-screened into bucket '{bucket}'. Verify expected_violations against "
            f"current active rules before relying on this in CI."
        ),
    }
    if not dry_run:
        bucket_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False))
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-bucket", type=int, default=6, help="examples per bucket (default 6)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--sess-dir", type=Path, default=SESS_DIR)
    ap.add_argument("--dest-root", type=Path, default=DEST_ROOT)
    args = ap.parse_args()

    if not args.sess_dir.is_dir():
        print(f"session dir missing: {args.sess_dir}", file=sys.stderr)
        return 1

    texts = harvest_unique_texts(args.sess_dir)
    print(f"harvested {len(texts)} unique assistant texts from {args.sess_dir}")

    buckets = bucket_texts(texts)
    for k in sorted(buckets):
        print(f"  bucket {k:<24s} candidates={len(buckets[k])}")

    written = 0
    skipped = 0
    for bucket, items in buckets.items():
        picks = pick_diverse(items, args.per_bucket)
        for text in picks:
            result = write_example(bucket, text, dest_root=args.dest_root, dry_run=args.dry_run)
            if result is None:
                skipped += 1
            else:
                written += 1
                print(f"  {'WOULD WRITE' if args.dry_run else 'wrote'}  {result.relative_to(REPO_ROOT)}")

    print(f"\ntotal: {written} written, {skipped} skipped (already on disk)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
