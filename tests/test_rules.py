"""Rule loading and regex hint collection."""

from pathlib import Path

from mop.rules import Rule, load_rules, collect_regex_hints


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
