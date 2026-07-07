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
async def test_check_passes_deterministic_hits_and_justification():
    """The deterministic violations + justification are forwarded to the LLM call."""
    calls = []
    det_rule = Rule(
        "too-long", "regex", {"patterns": [r"\bwords\b"]},
        "keep it short", "x.yml",
    )
    v = await check(
        "three words here", [det_rule], _fake_evaluator(Accepted(), calls),
        justification="because",
    )
    # The evaluator was told about the deterministic hit...
    assert calls[0]["hints"] == ["too-long"]
    assert calls[0]["justification"] == "because"
    # ...and because its (fake) Accepted did not clear the still-matching
    # regex, deterministic authority (D3) forces a Rejected on the residual.
    assert isinstance(v, Rejected)
    assert v.unresolved == ["too-long"]


@pytest.mark.asyncio
async def test_check_rewrite_that_clears_deterministic_is_accepted_clean():
    """A rewrite that removes the matching pattern yields a clean Rewritten."""
    calls = []
    det_rule = Rule(
        "no-bang", "regex", {"patterns": [r"!"]}, "no exclamation", "x.yml",
    )
    v = await check(
        "hi!", [det_rule], _fake_evaluator(Rewritten(rewritten="hi"), calls),
    )
    assert isinstance(v, Rewritten)
    assert v.rewritten == "hi"
    assert v.unresolved == []


@pytest.mark.asyncio
async def test_check_no_rules_is_accepted_without_calling_evaluator():
    calls = []
    v = await check("anything", [], _fake_evaluator(Accepted(), calls))
    assert isinstance(v, Accepted)
    assert calls == []  # no llm rules, no deterministic hits → no LLM call


@pytest.mark.asyncio
async def test_check_no_rewrite_flag_skips_rewrite():
    """--no-rewrite path: deterministic residual is reported, never rewritten."""
    det_rule = Rule("no-bang", "regex", {"patterns": [r"!"]}, "no bang", "x.yml")
    v = await check(
        "hi!", [det_rule], _fake_evaluator(Rewritten(rewritten="hi")),
        allow_rewrite=False,
    )
    assert isinstance(v, Rejected)
    assert v.unresolved == ["no-bang"]


# ---------------------------------------------------------------------------
# Exit codes and JSON contract
# ---------------------------------------------------------------------------


def test_accepted_exit_and_json(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Accepted())
    code = main(["check", "hello world", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"verdict": "accepted", "rewritten": None, "unresolved": []}


def test_rewritten_exit_and_json(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Rewritten(rewritten="better text"))
    code = main(["check", "worse text", "--builtins", "--json"])
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
    _patch_evaluator(monkeypatch, Rejected(unresolved=["no-hype"]))
    code = main(["check", "AMAZING!!!", "--json"])
    assert code == EXIT_REJECTED
    payload = json.loads(capsys.readouterr().out)
    assert payload["unresolved"] == [{"name": "no-hype", "guidance": "No hype words."}]


def test_rejected_human_output_shows_guidance(repo, monkeypatch, capsys):
    _patch_evaluator(monkeypatch, Rejected(unresolved=["unspecified"]))
    code = main(["check", "text", "--builtins"])
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
    code = main(["check", "--file", str(target), "--builtins"])
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


def test_audit_log_written_when_env_set(repo, monkeypatch, tmp_path):
    log_dir = tmp_path / "auditlogs"
    monkeypatch.setenv("MOP_AUDIT_LOG", str(log_dir))
    _patch_evaluator(monkeypatch, Accepted())
    code = main(["check", "hello world", "--json"])
    assert code == EXIT_ACCEPTED
    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text().strip())
    assert entry["verdict"] == "accepted"
    assert entry["original"] == "hello world"


def test_no_audit_log_when_env_unset(repo, monkeypatch, tmp_path):
    monkeypatch.delenv("MOP_AUDIT_LOG", raising=False)
    _patch_evaluator(monkeypatch, Accepted())
    assert main(["check", "hello"]) == EXIT_ACCEPTED  # no crash, no file


def test_justify_passes_justification(repo, monkeypatch):
    calls = []
    _patch_evaluator(monkeypatch, Accepted(), calls)
    main(["check", "text", "--builtins", "--justify", "the user asked for raw logs"])
    assert calls[0]["justification"] == "the user asked for raw logs"


def test_builtins_off_by_default_warns_and_accepts(repo, monkeypatch, capsys):
    """No --builtins and no local .mop → warn to stderr, accept, exit 0."""
    calls = []
    _patch_evaluator(monkeypatch, Rejected(unresolved=["x"]), calls)
    code = main(["check", "anything", "--json"])
    assert code == EXIT_ACCEPTED
    err = capsys.readouterr().err
    assert "no active rules" in err
    assert calls == []  # evaluator never called — nothing to enforce


def test_builtins_flag_loads_packaged_rules(repo, monkeypatch, capsys):
    code = main(["rules", "list", "--builtins", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    names = {r["name"] for r in payload}
    assert "no-fabricated-attribution" in names  # a packaged built-in


def test_rules_list_empty_without_builtins(repo, monkeypatch, capsys):
    code = main(["rules", "list", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert payload == []


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
    code = main(["rules", "list", "--builtins"])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "Rules" in out
    assert "no-fabricated-attribution" in out
    assert "links-for-references" in out
    # Guidance must be present in human output
    assert "Attribute quotes and claims" in out


def test_rules_list_json(repo, capsys):
    code = main(["rules", "--json", "list", "--builtins"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    names = {r["name"] for r in payload}
    assert "no-fabricated-attribution" in names
    assert "links-for-references" in names


def test_rules_show_human(repo, capsys):
    code = main(["rules", "show", "no-fabricated-attribution", "--builtins"])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "Name:" in out
    assert "no-fabricated-attribution" in out
    assert "Detector:" in out


def test_rules_show_json(repo, capsys):
    code = main(["rules", "--json", "show", "no-fabricated-attribution", "--builtins"])
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
    code = main(["rules", "--rules-file", str(explicit), "--builtins"])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "only-rule" in out
    assert "no-fabricated-attribution" in out  # builtins base layer when opted in


def test_evaluator_exception_exits_error(repo, monkeypatch, capsys):
    async def boom(text, hints, justification):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        "mop.cli.build_evaluator", lambda *, rules, model=None: boom
    )
    code = main(["check", "text", "--builtins"])
    assert code == EXIT_ERROR
    assert "provider exploded" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Bare mop rules (defaults to list) must work (spec contract)
# ---------------------------------------------------------------------------


def test_bare_mop_rules_exits_0_and_shows_rule_and_guidance(repo, capsys):
    """`mop rules --builtins` must exit 0 and include a known rule name + guidance."""
    code = main(["rules", "--builtins"])
    assert code == EXIT_ACCEPTED
    out = capsys.readouterr().out
    assert "no-fabricated-attribution" in out
    # Guidance text for no-fabricated-attribution
    assert "Attribute quotes and claims" in out


def test_bare_mop_rules_json(repo, capsys):
    """`mop rules --builtins --json` must work and emit valid JSON."""
    code = main(["rules", "--builtins", "--json"])
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
    """`mop rules list --builtins --json` must produce JSON and exit 0."""
    code = main(["rules", "list", "--builtins", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    names = {r["name"] for r in payload}
    assert "no-fabricated-attribution" in names


def test_rules_show_json_after_subcommand(repo, capsys):
    """`mop rules show <name> --builtins --json` must produce JSON and exit 0."""
    code = main(["rules", "show", "no-fabricated-attribution", "--builtins", "--json"])
    assert code == EXIT_ACCEPTED
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "no-fabricated-attribution"
    assert payload["detector"] == "llm"
