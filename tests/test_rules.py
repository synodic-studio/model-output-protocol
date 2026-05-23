"""Rule loading and regex hint collection."""

from pathlib import Path

from mop.rules import Rule, load_rules, collect_regex_hints, collect_lint_hints


def test_load_rules_from_yaml(tmp_path: Path):
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "style.yml").write_text(
        """
rules:
  - name: no-emojis
    detector: regex
    parameters:
      type: regex
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
    assert len(rules) == 2
    assert rules[0].name == "no-emojis"
    assert rules[0].detector == "regex"
    assert rules[1].name == "brevity"
    assert rules[1].detector == "llm"


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
    assert len(rules) == 1
    # severity / on_violation are NOT attributes on the Rule dataclass
    assert not hasattr(rules[0], "severity")
    assert not hasattr(rules[0], "on_violation")


def test_collect_regex_hints_returns_matched_rule_names():
    rules = [
        Rule(
            name="no-emojis",
            detector="regex",
            parameters={"type": "regex", "patterns": [r"\U0001f600"]},
            guidance="No emojis.",
            source_file="style.yml",
        ),
        Rule(
            name="word-cap",
            detector="regex",
            parameters={"type": "word_count", "max": 5},
            guidance="Max 5 words.",
            source_file="style.yml",
        ),
    ]
    hints = collect_regex_hints("hello \U0001f600 world this is too long", rules)
    assert "no-emojis" in hints
    assert "word-cap" in hints


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
    assert names == {"live-rule", "explicit-active-rule"}


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
    assert {r.name for r in all_rules} == {"live-rule", "dormant-rule"}


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
    detector: deterministic
    parameters:
      type: regex
      patterns:
        - hello\n  - name: no-permission
    detector: llm
    parameters:
      prompt: "Is this asking permission for doable work?"
    guidance: "Just do it"
"""
    )
    rules = load_rules(rules_dir)
    assert len(rules) == 2
    lint_rule = next(r for r in rules if r.name == "inline-code")
    llm_rule = next(r for r in rules if r.name == "no-permission")
    assert lint_rule.lint is True
    assert lint_rule.detector == "deterministic"
    assert llm_rule.lint is False
    assert llm_rule.detector == "llm"


def test_collect_lint_hints_returns_only_lint_matches():
    """`collect_lint_hints` only checks entries where lint=True.

    LLM rules (lint=False) are skipped even if they carry regex patterns.
    """
    rules = [
        Rule(
            name="no-emojis",
            detector="deterministic",
            lint=True,
            parameters={"type": "regex", "patterns": [r"\U0001f600"]},
            guidance="No emojis.",
            source_file="style.yml",
        ),
        Rule(
            name="word-cap",
            detector="deterministic",
            lint=True,
            parameters={"type": "word_count", "max": 5},
            guidance="Max 5 words.",
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
    assert "word-cap" in hints
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
