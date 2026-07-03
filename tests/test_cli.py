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


def test_evaluator_exception_exits_error(repo, monkeypatch, capsys):
    async def boom(text, hints, justification):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        "mop.cli.build_evaluator", lambda *, rules, model=None: boom
    )
    code = main(["check", "text"])
    assert code == EXIT_ERROR
    assert "provider exploded" in capsys.readouterr().err
