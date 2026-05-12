# MOP Studio

A FastAPI web UI for editing rules, testing them against real or synthetic agent output, and triaging rule candidates. Single Python file (`app.py`) with all HTML/CSS/JS inline — no build step, no node toolchain.

## Tabs

| Tab | What it does |
| --- | --- |
| **Submit** | Paste a real (offending or clean) agent response, pick the verdict MOP *should* have produced, optionally name a proposed rule. Saved to `submissions/YYYY-MM-DD-<slug>.yml` for later triage. |
| **Rules** | List every `rules/*.yml` file. Expand any rule to edit it; save writes back to disk. Add new rules from the inline form. Test any rule against its canonical example or whatever's in the playground. |
| **Playground** | Run arbitrary text through `submit_message()` against the live rule set, see the Haiku verdict (accept / rewrite / reject) and any violations. Also a counterexample browser — load any saved eval into the textarea. |
| **Evals** | Run the `evals/` corpus harness and surface mismatches between expected and actual violations. |
| **Diagram** | Mermaid system diagram with a feedback box that writes to `web/diagram-feedback.txt` for a coding agent to pick up. |

A floating chat widget on every tab writes anything you type into `web/feedback.txt`. The intended workflow: leave notes here, then run a coding agent against the repo to act on them.

## Run it

```bash
# One-shot, current shell
uv run web/app.py
```

```bash
# Or via the launchd-friendly wrapper (handles uv resolution + Anthropic key)
web/run-web.sh
```

The wrapper pulls `MOP_ANTHROPIC_API_KEY` from `pass` if available (so the Haiku playground works), binds to `MOP_WEB_HOST` (default `0.0.0.0`) and `MOP_WEB_PORT` (default `7731`).

Once running, hit `http://localhost:7731/`.

## Run as a launchd agent

`../docs/launchd-example.plist` is the template. Replace `REPO_PATH` and the label, then:

```bash
cp ../docs/launchd-example.plist ~/Library/LaunchAgents/com.<you>.mop-web.plist
launchctl load ~/Library/LaunchAgents/com.<you>.mop-web.plist
```

Reload after edits with `launchctl kickstart -k gui/$(id -u)/com.<you>.mop-web`.

## Environment

| Var | Default | Purpose |
| --- | --- | --- |
| `MOP_WEB_HOST` | `127.0.0.1` (script default: `0.0.0.0`) | Bind interface |
| `MOP_WEB_PORT` | `7731` | Listen port |
| `MOP_RULES_DIR` | `<repo>/rules` | Rules directory to load |
| `MOP_EVALS_DIR` | `<repo>/evals` | Eval corpus directory |
| `MOP_SUBMISSIONS_DIR` | `<repo>/submissions` | Where Submit-tab YAML lands |
| `MOP_FEEDBACK_FILE` | `<repo>/web/diagram-feedback.txt` | Diagram-feedback append target |
| `MOP_ANTHROPIC_API_KEY` | — | Haiku API key for `/api/evaluate` and rule tests |

## File layout

```
web/
├── app.py        # FastAPI app + inline HTML/CSS/JS for every tab
├── run-web.sh    # Launchd-friendly wrapper: resolves uv, fetches API key, exec's app.py
└── README.md     # This file
```

No auth. Bind to `127.0.0.1` (or a Tailscale interface) if you don't want it reachable on your LAN.
