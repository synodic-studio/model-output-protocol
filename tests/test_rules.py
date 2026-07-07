"""Rule loading and regex hint collection."""

from pathlib import Path

from mop.rules import (
    Rule,
    collect_lint_hints,
    collect_regex_hints,
    load_rules,
    load_rules_file,
    merge_rules,
)


def test_load_rules_from_yaml(tmp_path: Path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "style.yml").write_text(
        """
rules:
  - name: no-emojis
    detector: regex
    parameters:
      patterns:
        - "[\U0001f600-\U0001f64f]"
    guidance: "No emojis."
  - name: brevity
    detector: llm
    parameters:
      prompt: "Is this message under 100 words and on-topic?"
    guidance: "Be concise."
"""
    )
    rules = load_rules(rules_dir)
    assert len(rules) == 3  # 2 from yaml + 1 built-in lint
    assert rules[0].name == "no-emojis"
    assert rules[0].detector == "regex"
    assert rules[1].name == "brevity"
    assert rules[1].detector == "llm"
    assert rules[2].name == "format-score-too-high"
    assert rules[2].detector == "script"
    assert rules[2].lint is True


def test_load_rules_ignores_severity_and_on_violation(tmp_path: Path):
    """Legacy `severity` / `on_violation` fields on a rule are silently dropped."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "legacy.yml").write_text(
        """
rules:
  - name: legacy-rule
    detector: regex
    parameters:
      type: regex
      patterns: ["foo"]
    severity: warn
    on_violation: reject
    guidance: "old fields ignored"
"""
    )
    rules = load_rules(rules_dir)
    assert len(rules) == 2  # 1 from yaml + 1 built-in lint
    file_rules = [r for r in rules if r.source_file != "<builtin>"]
    assert len(file_rules) == 1
    # severity / on_violation are NOT attributes on the Rule dataclass
    assert not hasattr(file_rules[0], "severity")
    assert not hasattr(file_rules[0], "on_violation")


def test_collect_regex_hints_returns_matched_rule_names():
    rules = [
        Rule(
            name="no-emojis",
            detector="regex",
            parameters={"patterns": [r"\U0001f600"]},
            guidance="No emojis.",
            source_file="style.yml",
        ),
        Rule(
            name="too-long-word",
            detector="regex",
            parameters={"patterns": [r"\blong\b"]},
            guidance="Avoid the word 'long'.",
            source_file="style.yml",
        ),
    ]
    hints = collect_regex_hints("hello \U0001f600 world this is too long", rules)
    assert "no-emojis" in hints
    assert "too-long-word" in hints


def test_load_rules_filters_inactive_by_default(tmp_path: Path):
    """`active: false` rules are excluded from the loaded set by default."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "mixed.yml").write_text(
        """
rules:
  - name: live-rule
    detector: regex
    parameters:
      type: regex
      patterns: ["foo"]
    guidance: "loaded"
  - name: dormant-rule
    active: false
    detector: regex
    parameters:
      type: regex
      patterns: ["bar"]
    guidance: "filtered out"
  - name: explicit-active-rule
    active: true
    detector: regex
    parameters:
      type: regex
      patterns: ["baz"]
    guidance: "also loaded"
"""
    )
    rules = load_rules(rules_dir)
    names = {r.name for r in rules}
    assert names == {"live-rule", "explicit-active-rule", "format-score-too-high"}


def test_load_rules_include_inactive_returns_everything(tmp_path: Path):
    """`include_inactive=True` surfaces every rule for Studio-style UIs."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "mixed.yml").write_text(
        """
rules:
  - name: live-rule
    detector: regex
    parameters:
      type: regex
      patterns: ["foo"]
  - name: dormant-rule
    active: false
    detector: regex
    parameters:
      type: regex
      patterns: ["bar"]
"""
    )
    all_rules = load_rules(rules_dir, include_inactive=True)
    assert {r.name for r in all_rules} == {"live-rule", "dormant-rule", "format-score-too-high"}


def test_rule_defaults_lint_to_false():
    """The `lint` field defaults to False for backward compat."""
    r = Rule(
        name="test",
        detector="regex",
        parameters={"type": "regex", "patterns": ["foo"]},
        guidance="",
        source_file="x.yml",
    )
    assert r.lint is False


def test_load_rules_parses_lint_flag_from_yaml(tmp_path: Path):
    """A YAML entry with `lint: true` produces a Rule with lint=True."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "mixed.yml").write_text(
        """
rules:
  - name: inline-code
    lint: true
    detector: regex
    parameters:
      patterns:
        - hello\n  - name: no-permission
    detector: llm
    parameters:
      prompt: "Is this asking permission for doable work?"
    guidance: "Just do it"
"""
    )
    rules = load_rules(rules_dir)
    assert len(rules) == 3  # 2 from yaml + 1 built-in lint
    lint_rule = next(r for r in rules if r.name == "inline-code")
    llm_rule = next(r for r in rules if r.name == "no-permission")
    builtin = next(r for r in rules if r.name == "format-score-too-high")
    assert lint_rule.lint is True
    assert lint_rule.detector == "regex"
    assert llm_rule.lint is False
    assert llm_rule.detector == "llm"
    assert builtin.lint is True
    assert builtin.source_file == "<builtin>"


def test_collect_lint_hints_returns_only_lint_matches():
    """`collect_lint_hints` only checks entries where lint=True.

    LLM rules (lint=False) are skipped even if they carry regex patterns.
    """
    rules = [
        Rule(
            name="no-emojis",
            detector="regex",
            lint=True,
            parameters={"patterns": [r"\U0001f600"]},
            guidance="No emojis.",
            source_file="style.yml",
        ),
        Rule(
            name="too-long-word",
            detector="regex",
            lint=True,
            parameters={"patterns": [r"\blong\b"]},
            guidance="Avoid the word 'long'.",
            source_file="style.yml",
        ),
        Rule(
            name="llm-prose-rule",
            detector="llm",
            lint=False,
            parameters={"prompt": "Is the message clear?"},
            guidance="...",
            source_file="x.yml",
        ),
    ]
    hints = collect_lint_hints("hello \U0001f600 world this is too long", rules)
    assert "no-emojis" in hints
    assert "too-long-word" in hints
    assert "llm-prose-rule" not in hints


def test_collect_regex_hints_still_works_for_legacy_deterministic():
    """Legacy deterministic rules without `lint: true` are still collected
    by `collect_regex_hints` for backward compatibility."""
    rules = [
        Rule(
            name="old-detect",
            detector="regex",
            lint=False,
            parameters={"type": "regex", "patterns": [r"hello"]},
            guidance="Old style.",
            source_file="legacy.yml",
        ),
    ]
    hints = collect_regex_hints("hello world", rules)
    assert "old-detect" in hints


def test_builtin_format_lint_collected_by_load_rules(tmp_path: Path):
    """The format-score-too-high built-in lint appears in load_rules()."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "dummy.yml").write_text(
        """
rules:
  - name: silence
    detector: deterministic
    parameters:
      type: regex
      patterns: ["silence"]
    guidance: "..."
"""
    )
    rules = load_rules(rules_dir)
    builtin = next(r for r in rules if r.name == "format-score-too-high")
    assert builtin.detector == "script"
    assert builtin.lint is True
    assert builtin.source_file == "<builtin>"


def test_format_lint_hint_fires_on_long_message():
    """A long prose-heavy message triggers the format-score-too-high hint."""
    from mop.rules import _rule_matches

    rule = Rule(
        name="format-score-too-high",
        detector="script",
        parameters={},
        guidance="...",
        source_file="<builtin>",
        lint=True,
    )
    # 50 lines of prose should trigger 33+ prose-structure penalty alone
    long_msg = "\n".join(["this is a very long prose line that just keeps going" for _ in range(50)])
    assert _rule_matches(rule, long_msg) is True


def test_format_lint_hint_does_not_fire_on_short_message():
    from mop.rules import _rule_matches

    rule = Rule(
        name="format-score-too-high",
        detector="script",
        parameters={},
        guidance="...",
        source_file="<builtin>",
        lint=True,
    )
    assert _rule_matches(rule, "short message") is False


def test_format_lint_collected_by_collect_lint_hints():
    """collect_lint_hints picks up the format-score-too-high built-in."""
    from mop import load_rules, collect_lint_hints
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        rules_dir = Path(td)
        (rules_dir / "dummy.yml").write_text(
            """
rules:
  - name: silence
    detector: deterministic
    parameters:
      type: regex
      patterns: ["silence"]
    guidance: "..."
"""
        )
        rules = load_rules(rules_dir)
        # Short message should NOT trigger the format lint
        hints = collect_lint_hints("hello world", rules)
        assert "format-score-too-high" not in hints

        # Long prose-heavy message SHOULD trigger
        long_msg = "\n".join(["this is a very long prose line that just keeps going" for _ in range(50)])
        hints = collect_lint_hints(long_msg, rules)
        assert "format-score-too-high" in hints


def test_collect_regex_hints_ignores_llm_rules():
    rules = [
        Rule(
            name="prose-rule",
            detector="llm",
            parameters={"prompt": "..."},
            guidance="...",
            source_file="x.yml",
        ),
    ]
    hints = collect_regex_hints("anything", rules)
    assert hints == []


# ---------------------------------------------------------------------------
# Rule.active field, single-file loading, layered merge
# ---------------------------------------------------------------------------


def _write_rules_yml(path, entries):
    import yaml

    path.write_text(yaml.safe_dump({"rules": entries}))


def test_load_rules_populates_active_field(tmp_path):
    _write_rules_yml(
        tmp_path / "r.yml",
        [
            {"name": "on-rule", "detector": "llm", "guidance": "g"},
            {"name": "off-rule", "detector": "llm", "guidance": "g", "active": False},
        ],
    )
    rules = load_rules(tmp_path, include_inactive=True)
    by_name = {r.name: r for r in rules}
    assert by_name["on-rule"].active is True
    assert by_name["off-rule"].active is False


def test_load_rules_file_reads_single_file(tmp_path):
    target = tmp_path / "solo.yml"
    _write_rules_yml(target, [{"name": "solo-rule", "detector": "llm", "guidance": "g"}])
    # A sibling file that must NOT be picked up:
    _write_rules_yml(
        tmp_path / "other.yml", [{"name": "other-rule", "detector": "llm", "guidance": "g"}]
    )
    rules = load_rules_file(target)
    names = [r.name for r in rules]
    assert "solo-rule" in names
    assert "other-rule" not in names


def test_load_rules_file_does_not_append_builtin_lints(tmp_path):
    target = tmp_path / "solo.yml"
    _write_rules_yml(target, [{"name": "solo-rule", "detector": "llm", "guidance": "g"}])
    rules = load_rules_file(target)
    assert all(r.source_file != "<builtin>" for r in rules)


def test_merge_overlay_replaces_same_name():
    base = [Rule("a", "llm", {}, "base guidance", "base.yml")]
    overlay = [Rule("a", "llm", {}, "local guidance", "local.yml")]
    merged = merge_rules(base, overlay)
    assert len(merged) == 1
    assert merged[0].guidance == "local guidance"


def test_merge_unions_distinct_names():
    base = [Rule("a", "llm", {}, "g", "base.yml")]
    overlay = [Rule("b", "llm", {}, "g", "local.yml")]
    merged = merge_rules(base, overlay)
    assert [r.name for r in merged] == ["a", "b"]


def test_merge_local_inactive_silences_builtin():
    base = [Rule("a", "llm", {}, "g", "base.yml")]
    overlay = [Rule("a", "llm", {}, "g", "local.yml", active=False)]
    merged = merge_rules(base, overlay)
    assert merged == []


def test_merge_dedupes_builtin_lints():
    """load_rules appends registered builtin lints per call; merge dedupes them."""
    lint = Rule(
        "format-score-too-high", "script", {},
        "g", "<builtin>", lint=True,
    )
    merged = merge_rules([lint], [lint])
    assert len(merged) == 1


# ---------------------------------------------------------------------------
# script detector — external command (stdin in, exit code = verdict)
# ---------------------------------------------------------------------------


def _write_script(path: Path, body: str) -> Path:
    import os
    import stat

    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_script_detector_fires_on_nonzero_exit(tmp_path: Path):
    """A `script` rule fires when its command exits non-zero, reading stdin."""
    from mop.rules import _rule_matches

    checker = _write_script(
        tmp_path / "check.sh",
        "#!/bin/sh\nif grep -q BAD; then exit 1; else exit 0; fi\n",
    )
    rule = Rule(
        name="no-bad-word",
        detector="script",
        parameters={"command": str(checker)},
        guidance="Remove BAD.",
        source_file="local.yml",
    )
    assert _rule_matches(rule, "this is BAD") is True
    assert _rule_matches(rule, "this is fine") is False


def test_script_detector_raises_when_command_missing(tmp_path: Path):
    """A command that can't run raises rather than silently passing."""
    import pytest

    from mop.rules import _rule_matches

    rule = Rule(
        name="broken",
        detector="script",
        parameters={"command": str(tmp_path / "does-not-exist.sh")},
        guidance="",
        source_file="local.yml",
    )
    with pytest.raises(RuntimeError, match="failed to run"):
        _rule_matches(rule, "anything")
