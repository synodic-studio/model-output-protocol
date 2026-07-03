"""CLI adapter: exit codes, output contract, input-source validation."""

import json

import pytest
import yaml

from mop.cli import EXIT_ACCEPTED, EXIT_ERROR, EXIT_REJECTED, EXIT_REWRITTEN, check, main
from mop.rules import Rule
from mop.types import Accepted, Rejected, Rewritten


def _fake_evaluator(verdict, calls=None):
    async def evaluate(text, hints, justification):
        if calls is not None:
            calls.append({"text": text, "hints": hints, "justification": justification})
        return verdict

    return evaluate


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Isolated fake repo root so discovery never picks up real .mop dirs."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _patch_evaluator(monkeypatch, verdict, calls=None):
    monkeypatch.setattr(
        "mop.cli.build_evaluator",
        lambda *, rules, model=None: _fake_evaluator(verdict, calls),
    )


# ---------------------------------------------------------------------------
# check() core function
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_passes_lint_hints_and_justification():
    calls = []
    lint_rule = Rule(
        "too-long", "deterministic", {"type": "word_count", "max": 2},
        "keep it short", "x.yml", lint=True,
    )
    v = await check(
        "three words here", [lint_rule], _fake_evaluator(Accepted(), calls),
        justification="because",
    )
    assert isinstance(v, Accepted)
    assert calls[0]["hints"] == ["too-long"]
    assert calls[0]["justification"] == "because"


# ---------------------------------------------------------------------------
# Exit codes and JSON contract
# ---------------------------------------------------------------------------


def test_accepted_exit_and_json(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Accepted())
    code = main(["check", "hello world", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"verdict": "accepted", "rewritten": None, "violations": []}


def test_rewritten_exit_and_json(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Rewritten(rewritten="better text"))
    code = main(["check", "worse text", "--json"])
    assert code == EXIT_REWRITTEN
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "rewritten"
    assert payload["rewritten"] == "better text"


def test_rejected_json_resolves_guidance(repo, monkeypatch, capsys):
    """Rejected carries names only; the CLI must look guidance up in the rule set."""
    mop_dir = repo / ".mop"
    mop_dir.mkdir()
    (mop_dir / "local.yml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {"name": "no-hype", "detector": "llm", "guidance": "No hype words."}
                ]
            }
        )
    )
    _patch_evaluator(monkeypatch, Rejected(violations=["no-hype"]))
    code = main(["check", "AMAZING!!!", "--json"])
    assert code == EXIT_REJECTED
    payload = json.loads(capsys.readouterr().out)
    assert payload["violations"] == [{"name": "no-hype", "guidance": "No hype words."}]


def test_rejected_human_output_shows_guidance(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Rejected(violations=["unspecified"]))
    code = main(["check", "text"])
    assert code == EXIT_REJECTED
    out = capsys.readouterr().out
    assert "rejected" in out
    assert "unspecified" in out


# ---------------------------------------------------------------------------
# Input sources
# ---------------------------------------------------------------------------


def test_file_input(repo, monkeypatch, capsys, tmp_path):
    target = tmp_path / "msg.txt"
    target.write_text("from a file")
    calls = []
    _patch_evaluator(monkeypatch, Accepted(), calls)
    code = main(["check", "--file", str(target)])
    assert code == EXIT_ACCEPTED
    assert calls[0]["text"] == "from a file"


def test_text_and_file_together_errors(repo, monkeypatch, capsys, tmp_path):
    target = tmp_path / "msg.txt"
    target.write_text("x")
    _patch_evaluator(monkeypatch, Accepted())
    code = main(["check", "positional", "--file", str(target)])
    assert code == EXIT_ERROR


def test_stdin_input(repo, monkeypatch, capsys):
    import io

    _patch_evaluator(monkeypatch, Accepted())
    monkeypatch.setattr("sys.stdin", io.StringIO("piped text"))
    code = main(["check", "--json"])
    assert code == EXIT_ACCEPTED


def test_empty_input_errors(repo, monkeypatch, capsys):
    import io

    _patch_evaluator(monkeypatch, Accepted())
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = main(["check"])
    assert code == EXIT_ERROR


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_justify_passes_justification(repo, monkeypatch):
    calls = []
    _patch_evaluator(monkeypatch, Accepted(), calls)
    main(["check", "text", "--justify", "the user asked for raw logs"])
    assert calls[0]["justification"] == "the user asked for raw logs"


def test_rule_filter_restricts_rule_set(repo, monkeypatch):
    """--rule NAME evaluates against only that rule."""
    captured_rules = []

    def fake_builder(*, rules, model=None):
        captured_rules.extend(rules)
        return _fake_evaluator(Accepted())

    monkeypatch.setattr("mop.cli.build_evaluator", fake_builder)
    mop_dir = repo / ".mop"
    mop_dir.mkdir()
    (mop_dir / "local.yml").write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {"name": "only-this", "detector": "llm", "guidance": "g"},
                    {"name": "not-this", "detector": "llm", "guidance": "g"},
                ]
            }
        )
    )
    code = main(["check", "text", "--rule", "only-this"])
    assert code == EXIT_ACCEPTED
    assert [r.name for r in captured_rules] == ["only-this"]


def test_rule_filter_unknown_name_errors(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Accepted())
    code = main(["check", "text", "--rule", "does-not-exist"])
    assert code == EXIT_ERROR
    assert "does-not-exist" in capsys.readouterr().err


def test_model_flag_reaches_builder(repo, monkeypatch):
    seen = {}

    def fake_builder(*, rules, model=None):
        seen["model"] = model
        return _fake_evaluator(Accepted())

    monkeypatch.setattr("mop.cli.build_evaluator", fake_builder)
    main(["check", "text", "--model", "openai/gpt-4o-mini"])
    assert seen["model"] == "openai/gpt-4o-mini"


# ---------------------------------------------------------------------------
# mop rules subcommand
# ---------------------------------------------------------------------------


def test_rules_list_human(repo, capsys):
    code = main(["rules", "list"])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "Rules" in out
    assert "no-fabricated-attribution" in out
    assert "links-for-references" in out
    # Guidance must be present in human output
    assert "Attribute quotes and claims" in out


def test_rules_list_json(repo, capsys):
    code = main(["rules", "--json", "list"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    names = {r["name"] for r in payload}
    assert "no-fabricated-attribution" in names
    assert "links-for-references" in names


def test_rules_show_human(repo, capsys):
    code = main(["rules", "show", "no-fabricated-attribution"])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "Name:" in out
    assert "no-fabricated-attribution" in out
    assert "Detector:" in out


def test_rules_show_json(repo, capsys):
    code = main(["rules", "--json", "show", "no-fabricated-attribution"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "no-fabricated-attribution"
    assert payload["detector"] == "llm"


def test_rules_show_unknown_name(repo, capsys):
    code = main(["rules", "show", "does-not-exist"])
    assert code == EXIT_ERROR
    assert "does-not-exist" in capsys.readouterr().err


def test_rules_list_with_explicit_file(repo, capsys, tmp_path):
    explicit = tmp_path / "explicit.yml"
    explicit.write_text(
        yaml.safe_dump(
            {"rules": [{"name": "only-rule", "detector": "llm", "guidance": "g"}]}
        )
    )
    code = main(["rules", "--rules-file", str(explicit)])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "only-rule" in out
    assert "no-fabricated-attribution" in out  # builtins still present as base layer


def test_evaluator_exception_exits_error(repo, monkeypatch, capsys):
    async def boom(text, hints, justification):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        "mop.cli.build_evaluator", lambda *, rules, model=None: boom
    )
    code = main(["check", "text"])
    assert code == EXIT_ERROR
    assert "provider exploded" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Bare mop rules (defaults to list) must work (spec contract)
# ---------------------------------------------------------------------------


def test_bare_mop_rules_exits_0_and_shows_rule_and_guidance(repo, capsys):
    """Bare `mop rules` must exit 0 and include a known rule name + guidance."""
    code = main(["rules"])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "no-fabricated-attribution" in out
    # Guidance text for no-fabricated-attribution
    assert "Attribute quotes and claims" in out


def test_bare_mop_rules_json(repo, capsys):
    """Bare `mop rules --json` must work and emit valid JSON."""
    code = main(["rules", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    names = {r["name"] for r in payload}
    assert "no-fabricated-attribution" in names
    assert "links-for-references" in names


def test_bare_mop_rules_with_rules_dir(repo, capsys, tmp_path):
    """Bare `mop rules --rules-dir X` must work."""
    alt = tmp_path / "alt_rules"
    alt.mkdir()
    (alt / "alt.yml").write_text(
        yaml.safe_dump(
            {"rules": [{"name": "alt-rule", "detector": "llm", "guidance": "Alt guidance."}]}
        )
    )
    code = main(["rules", "--rules-dir", str(alt)])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "alt-rule" in out
    assert "Alt guidance." in out


# ---------------------------------------------------------------------------
# Usage errors must exit 3 (task 1), --json after subcommand (task 2)
# ---------------------------------------------------------------------------


def test_bad_flag_exits_error(repo, capsys):
    """Typo'd flag must exit EXIT_ERROR (3), not argparse-native exit 2."""
    code = main(["check", "--bogus"])
    assert code == EXIT_ERROR
    # argparse must still print its usage message on stderr
    err = capsys.readouterr().err
    assert "mop check" in err or "usage:" in err


def test_unknown_subcommand_exits_error(repo, capsys):
    """Unknown subcommand must exit EXIT_ERROR (3), not exit 2."""
    code = main(["nonsense"])
    assert code == EXIT_ERROR
    err = capsys.readouterr().err
    assert "nonsense" in err or "usage:" in err


def test_rules_list_json_after_subcommand(repo, capsys):
    """`mop rules list --json` must produce JSON and exit 0."""
    code = main(["rules", "list", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    names = {r["name"] for r in payload}
    assert "no-fabricated-attribution" in names


def test_rules_show_json_after_subcommand(repo, capsys):
    """`mop rules show <name> --json` must produce JSON and exit 0."""
    code = main(["rules", "show", "no-fabricated-attribution", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "no-fabricated-attribution"
    assert payload["detector"] == "llm"
