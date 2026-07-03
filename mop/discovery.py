"""Rule discovery and layered resolution for CLI/standalone use.

Built-in rules ship as package data in ``mop/rules_builtin/``. Local
rules live in a ``.mop/`` directory holding ``*.yml`` files (same
schema as the repo ``rules/`` directory), discovered by walking up
from the starting directory to the repo root — the first directory
containing ``.git``, inclusive — and never past it.

Resolution is a two-layer merge (see ``mop.rules.merge_rules``):
built-ins are the base, the discovered or explicitly-passed local set
overlays it. Same-name replaces, everything else unions, and a local
``active: false`` silences a built-in by name.
"""

from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path

from .rules import Rule, load_rules, load_rules_file, merge_rules


def load_builtin_rules() -> list[Rule]:
    """Load the rule files packaged in mop/rules_builtin/."""
    source = files("mop") / "rules_builtin"
    with as_file(source) as dir_path:
        return load_rules(Path(dir_path))


def find_local_rules_dir(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (default cwd) looking for a ``.mop/`` directory.

    Stops at — and includes — the first directory containing ``.git``.
    Returns None if no ``.mop/`` exists within the repo, or if ``start``
    is outside any git repo entirely (the walk then ends at the
    filesystem root without crossing repo boundaries it can't see).
    """
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / ".mop"
        if candidate.is_dir():
            return candidate
        if (directory / ".git").exists():
            return None
    return None


def resolve_rules(
    *,
    rules_dir: Path | None = None,
    rules_file: Path | None = None,
    start: Path | None = None,
) -> list[Rule]:
    """Resolve the active rule set: built-ins + (explicit | discovered) local layer.

    ``rules_dir``/``rules_file`` are mutually exclusive explicit overrides
    that bypass discovery. The local layer is loaded with
    ``include_inactive=True`` so ``active: false`` entries can silence
    built-ins during the merge.
    """
    if rules_dir and rules_file:
        raise ValueError("Pass at most one of rules_dir / rules_file.")
    base = load_builtin_rules()
    if rules_file:
        overlay = load_rules_file(rules_file, include_inactive=True)
    elif rules_dir:
        overlay = load_rules(rules_dir, include_inactive=True)
    else:
        discovered = find_local_rules_dir(start)
        overlay = (
            load_rules(discovered, include_inactive=True) if discovered else []
        )
    return merge_rules(base, overlay)
