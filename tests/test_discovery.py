"""Rule discovery: packaged built-ins, .mop/ walk-up, layered resolution."""

from pathlib import Path

import yaml

from mop.discovery import find_local_rules_dir, load_builtin_rules, resolve_rules

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_mop_dir(directory: Path, entries: list[dict]) -> Path:
    mop_dir = directory / ".mop"
    mop_dir.mkdir(parents=True)
    (mop_dir / "local.yml").write_text(yaml.safe_dump({"rules": entries}))
    return mop_dir


# ---------------------------------------------------------------------------
# Built-ins
# ---------------------------------------------------------------------------


def test_builtin_rules_load_nonempty():
    rules = load_builtin_rules()
    assert rules, "packaged built-in rules should not be empty"
    names = {r.name for r in rules}
    assert "no-fabricated-attribution" in names      # active in packaged core-voice.yml
    assert "links-for-references" in names           # active in packaged core-voice.yml
    assert "verify-before-asserting" not in names    # inactive, dropped by load_rules


def test_builtin_copies_stay_in_sync_with_rules_dir():
    """Guard against drift between rules/ (dev corpus) and packaged copies.

    The packaged copies may differ from the originals ONLY in their
    `active:` flags (those define the CLI's out-of-the-box defaults).
    Everything else — rule names and all other fields — must match.
    """
    for name in ("core-behavior.yml", "core-voice.yml"):
        original = yaml.safe_load((REPO_ROOT / "rules" / name).read_text())
        packaged = yaml.safe_load(
            (REPO_ROOT / "mop" / "rules_builtin" / name).read_text()
        )
        original_rules = {r["name"]: r for r in original["rules"]}
        packaged_rules = {r["name"]: r for r in packaged["rules"]}
        assert original_rules.keys() == packaged_rules.keys(), (
            f"{name}: mop/rules_builtin/ copy defines different rules than the "
            f"rules/ original — re-copy it (cp rules/{name} mop/rules_builtin/{name}) "
            "and re-apply the packaged active flags"
        )
        for rule_name, orig in original_rules.items():
            orig_no_active = {k: v for k, v in orig.items() if k != "active"}
            pkg_no_active = {
                k: v for k, v in packaged_rules[rule_name].items() if k != "active"
            }
            assert orig_no_active == pkg_no_active, (
                f"{name}: rule '{rule_name}' in mop/rules_builtin/ differs from "
                f"the rules/ original beyond the active flag — re-copy it "
                f"(cp rules/{name} mop/rules_builtin/{name}) and re-apply the "
                "packaged active flags"
            )


# ---------------------------------------------------------------------------
# Walk-up discovery
# ---------------------------------------------------------------------------


def test_finds_mop_dir_in_start_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    mop_dir = _write_mop_dir(tmp_path, [])
    assert find_local_rules_dir(tmp_path) == mop_dir


def test_walks_up_to_repo_root(tmp_path):
    (tmp_path / ".git").mkdir()
    mop_dir = _write_mop_dir(tmp_path, [])
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert find_local_rules_dir(nested) == mop_dir


def test_stops_at_repo_root(tmp_path):
    _write_mop_dir(tmp_path, [])          # .mop ABOVE the repo root
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    nested = repo / "src"
    nested.mkdir()
    assert find_local_rules_dir(nested) is None


def test_returns_none_without_mop_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    assert find_local_rules_dir(tmp_path) is None


def test_returns_none_without_git_or_mop_anywhere(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_local_rules_dir(nested) is None


# ---------------------------------------------------------------------------
# resolve_rules layering
# ---------------------------------------------------------------------------


def test_resolve_defaults_to_builtins_when_nothing_found(tmp_path):
    (tmp_path / ".git").mkdir()
    resolved = resolve_rules(start=tmp_path)
    assert {r.name for r in resolved} == {r.name for r in load_builtin_rules()}


def test_resolve_local_addition_unions(tmp_path):
    (tmp_path / ".git").mkdir()
    _write_mop_dir(
        tmp_path, [{"name": "project-rule", "detector": "llm", "guidance": "g"}]
    )
    resolved = resolve_rules(start=tmp_path)
    assert "project-rule" in {r.name for r in resolved}


def test_resolve_local_override_replaces_builtin(tmp_path):
    (tmp_path / ".git").mkdir()
    builtin_name = next(
        r.name for r in load_builtin_rules() if r.source_file != "<builtin>"
    )
    _write_mop_dir(
        tmp_path,
        [{"name": builtin_name, "detector": "llm", "guidance": "local override"}],
    )
    resolved = resolve_rules(start=tmp_path)
    match = [r for r in resolved if r.name == builtin_name]
    assert len(match) == 1
    assert match[0].guidance == "local override"


def test_resolve_local_inactive_silences_builtin(tmp_path):
    (tmp_path / ".git").mkdir()
    builtin_name = next(
        r.name for r in load_builtin_rules() if r.source_file != "<builtin>"
    )
    _write_mop_dir(
        tmp_path,
        [{"name": builtin_name, "detector": "llm", "guidance": "", "active": False}],
    )
    resolved = resolve_rules(start=tmp_path)
    assert builtin_name not in {r.name for r in resolved}


def test_resolve_explicit_rules_file_bypasses_discovery(tmp_path):
    (tmp_path / ".git").mkdir()
    _write_mop_dir(
        tmp_path, [{"name": "discovered-rule", "detector": "llm", "guidance": "g"}]
    )
    explicit = tmp_path / "explicit.yml"
    explicit.write_text(
        yaml.safe_dump(
            {"rules": [{"name": "explicit-rule", "detector": "llm", "guidance": "g"}]}
        )
    )
    resolved = resolve_rules(rules_file=explicit, start=tmp_path)
    names = {r.name for r in resolved}
    assert "explicit-rule" in names
    assert "discovered-rule" not in names


def test_resolve_rejects_both_dir_and_file(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="at most one"):
        resolve_rules(rules_dir=tmp_path, rules_file=tmp_path / "x.yml")


def test_resolve_builtin_lints_appear_once(tmp_path):
    (tmp_path / ".git").mkdir()
    _write_mop_dir(tmp_path, [{"name": "x", "detector": "llm", "guidance": "g"}])
    resolved = resolve_rules(start=tmp_path)
    lint_names = [r.name for r in resolved if r.source_file == "<builtin>"]
    assert len(lint_names) == len(set(lint_names))
