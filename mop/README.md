# `mop/` — library internals

The public surface lives in [`__init__.py`](__init__.py). Hosts import everything from `mop` (not from these submodules). The split here is so the implementation stays focused per file.

| File | Role |
| --- | --- |
| [`__init__.py`](__init__.py) | Re-exports the public API (`MOP`, `JsonlAuditor`, `build_evaluator`, `build_litellm_evaluator`, `protocol_prompt`, `stop`, the verdict types). |
| [`protocol.py`](protocol.py) | The `MOP` class. One instance per agent session. Holds rules, evaluator, deliver, auditor; routes every `submit_message` / `submit_justification` through `_apply`. |
| [`types.py`](types.py) | Verdict union (`Accepted` / `Rewritten` / `Rejected` / `AcceptedFailedOpen`), the `Gate` shape returned by the Stop hook, and Protocol aliases (`Evaluator`, `Deliver`). |
| [`rules.py`](rules.py) | `load_rules(dir)` reads `rules/*.yml`. `collect_regex_hints(text, rules)` runs deterministic detectors as advisory context for the LLM evaluator. |
| [`evaluators.py`](evaluators.py) | `build_litellm_evaluator(rules)` returns a provider-agnostic `Evaluator` callable backed by litellm. `build_evaluator(rules)` is the backward-compatible factory. |
| [`hooks.py`](hooks.py) | `protocol_prompt(rules)` builds the system-prompt snippet that tells the agent the MOP tools exist. `stop(mop)` is the body of the Stop hook — returns `Block` if the agent tried to end a turn without sending. |
| [`mcp.py`](mcp.py) | `build_mcp_server(mop)` wraps the `MOP` instance in an in-process MCP server exposing `submit_message`, `submit_justification`, `get_rules`, `get_status`. |
| [`audit.py`](audit.py) | `Auditor` Protocol + default `JsonlAuditor`. Called once per verdict with the original text, the verdict payload, the rule names active at evaluation time, the attempt counter, and any justification. |
| [`format_score.py`](format_score.py) | Heuristic scoring for message shape (line length, paragraph breaks, etc.). Backs the `format-score-too-high` built-in check and the eval corpus. |
| [`display_metrics.py`](display_metrics.py) | Low-level shape metrics (word/char/line counts, max line width) that `format_score.py` aggregates. |

## Reading order

If you're new and want to understand the runtime path, read in this order: `types.py` → `protocol.py` → `mcp.py` → `hooks.py`. That covers every line that executes during a real turn. `rules.py`, `evaluators.py`, and `audit.py` are pluggable adjuncts the host wires in.
