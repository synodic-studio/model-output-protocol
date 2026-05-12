"""MOP Studio — playground, rules config, evals, system diagram.

Run:    uv run web/app.py
Env:    MOP_WEB_HOST  (default 127.0.0.1)
        MOP_WEB_PORT  (default 7731)
        MOP_RULES_DIR (default <repo>/rules — must contain active/ and pending/)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mop import (  # noqa: E402
    Accepted,
    AcceptedFailedOpen,
    Rejected,
    Rewritten,
    build_haiku_evaluator,
    collect_regex_hints,
    load_rules,
)
from mop.rules import Rule  # noqa: E402

RULES_DIR = Path(os.environ.get("MOP_RULES_DIR", REPO_ROOT / "rules"))
EVALS_DIR = Path(os.environ.get("MOP_EVALS_DIR", REPO_ROOT / "evals"))
FEEDBACK_FILE = Path(os.environ.get("MOP_FEEDBACK_FILE", REPO_ROOT / "web" / "diagram-feedback.txt"))
SUBMISSIONS_DIR = Path(os.environ.get("MOP_SUBMISSIONS_DIR", REPO_ROOT / "submissions"))

app = FastAPI(title="MOP Studio")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_rule(r: dict) -> dict:
    params = r.get("parameters", {})
    det_type = params.get("type", "regex")
    return {
        "name": r.get("name", ""),
        "description": r.get("description", ""),
        "detector": r.get("detector", "llm"),
        "guidance": (r.get("guidance") or "").strip(),
        "rationale": (r.get("rationale") or "").strip(),
        "llm_prompt": (params.get("prompt") or "").strip() if r.get("detector") == "llm" else "",
        "det_type": det_type,
        "det_patterns": params.get("patterns", []),
        "det_max_words": params.get("max"),
        "canonical_example": (r.get("canonical_example") or "").strip(),
    }


def _rule_to_yaml_dict(rule: dict) -> dict:
    r: dict = {
        "name": rule["name"],
        "detector": rule["detector"],
    }
    if rule.get("description"):
        r["description"] = rule["description"]
    if rule["detector"] == "llm":
        r["parameters"] = {"prompt": rule.get("llm_prompt", "")}
    elif rule["detector"] == "deterministic":
        det: dict = {"type": rule.get("det_type", "regex")}
        if rule.get("det_type") == "word_count":
            det["max"] = rule.get("det_max_words") or 200
        else:
            det["patterns"] = rule.get("det_patterns") or []
        r["parameters"] = det
    if rule.get("guidance"):
        r["guidance"] = rule["guidance"]
    if rule.get("rationale"):
        r["rationale"] = rule["rationale"]
    if rule.get("canonical_example"):
        r["canonical_example"] = rule["canonical_example"]
    return r


def _load_files():
    """List every *.yml file directly under rules/ (flat, one folder)."""
    if not RULES_DIR.exists():
        return []
    files = []
    for path in sorted(RULES_DIR.glob("*.yml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
            parsed_rules = [_parse_rule(r) for r in data.get("rules", [])]
        except Exception:
            parsed_rules = []
        files.append({
            "path": str(path.relative_to(REPO_ROOT)),
            "filename": path.name,
            "rules": parsed_rules,
        })
    return files


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class EvalRequest(BaseModel):
    text: str


class SaveRuleRequest(BaseModel):
    file_path: str
    original_name: str
    rule: dict


class AddRuleRequest(BaseModel):
    file_path: str
    rule: dict


class FeedbackRequest(BaseModel):
    feedback: str


class AddExampleRequest(BaseModel):
    id: str
    text: str
    expected_violations: list[str]
    expected_clean: list[str]
    rationale: str
    category: str = "behavior"
    source: str = "real-sanitized"


class SubmissionRequest(BaseModel):
    offending_text: str
    feedback: str
    verdict: Literal["reject", "rewrite", "accept"]
    desired_rule_name: str = ""


class TestOneRuleRequest(BaseModel):
    rule: dict
    text: str


class DeleteRuleRequest(BaseModel):
    file_path: str
    rule_name: str


class GeneralFeedbackRequest(BaseModel):
    feedback: str
    context: str = ""


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.post("/api/evaluate")
async def api_evaluate(req: EvalRequest):
    """Run the Haiku evaluator against the active rules.

    The playground bypasses MOP's stateful submit_message/justification
    flow — there's no pending state to track, no deliver to fire. Just
    run the evaluator once and surface the verdict shape.
    """
    rules = load_rules(RULES_DIR)
    evaluator = build_haiku_evaluator(rules=rules)
    verdict = await evaluator(req.text, collect_regex_hints(req.text, rules), None)

    result: dict[str, object]
    if isinstance(verdict, Accepted):
        result = {"action": "accept"}
    elif isinstance(verdict, Rewritten):
        result = {"action": "rewrite", "rewritten": verdict.rewritten}
    elif isinstance(verdict, Rejected):
        result = {"action": "reject", "violations": list(verdict.violations)}
    elif isinstance(verdict, AcceptedFailedOpen):
        result = {"action": "accept_failed_open", "system_note": verdict.system_note}
    else:
        result = {"action": "unknown"}
    return JSONResponse(result)


@app.get("/api/rules")
def api_rules():
    return JSONResponse(_load_files())


@app.post("/api/rules/test-one")
async def api_rules_test_one(req: TestOneRuleRequest):
    """Run the Haiku evaluator against a single rule and a piece of text.

    Lets the user sanity-check a rule in isolation — does it fire on its
    canonical example? Does it fire on text it shouldn't?
    """
    rule_dict = req.rule
    detector = rule_dict.get("detector", "llm")
    if detector == "llm":
        params = {"prompt": rule_dict.get("llm_prompt", "")}
    elif detector == "deterministic":
        dtype = rule_dict.get("det_type", "regex")
        if dtype == "word_count":
            params = {"type": "word_count", "max": rule_dict.get("det_max_words") or 200}
        else:
            params = {"type": "regex", "patterns": rule_dict.get("det_patterns") or []}
    else:
        params = {}
    one_rule = Rule(
        name=rule_dict.get("name", "test-rule"),
        detector=detector,
        parameters=params,
        guidance=rule_dict.get("guidance", ""),
        source_file="<test>",
    )
    evaluator = build_haiku_evaluator(rules=[one_rule])
    verdict = await evaluator(req.text, collect_regex_hints(req.text, [one_rule]), None)
    if isinstance(verdict, Accepted):
        return JSONResponse({"action": "accept"})
    if isinstance(verdict, Rewritten):
        return JSONResponse({"action": "rewrite", "rewritten": verdict.rewritten})
    if isinstance(verdict, Rejected):
        return JSONResponse({"action": "reject", "violations": list(verdict.violations)})
    if isinstance(verdict, AcceptedFailedOpen):
        return JSONResponse({"action": "accept_failed_open", "system_note": verdict.system_note})
    return JSONResponse({"action": "unknown"})


@app.post("/api/rules/save-rule")
def api_save_rule(req: SaveRuleRequest):
    path = REPO_ROOT / req.file_path
    path.resolve().relative_to(REPO_ROOT.resolve())
    if path.suffix != ".yml":
        raise HTTPException(400, "Only .yml files")
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}
    rules = data.get("rules", [])
    new_dict = _rule_to_yaml_dict(req.rule)
    for i, r in enumerate(rules):
        if r.get("name") == req.original_name:
            rules[i] = new_dict
            break
    else:
        rules.append(new_dict)
    data["rules"] = rules
    path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False, indent=2)
    )
    return JSONResponse({"ok": True})


@app.post("/api/rules/add-rule")
def api_add_rule(req: AddRuleRequest):
    path = REPO_ROOT / req.file_path
    path.resolve().relative_to(REPO_ROOT.resolve())
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}
    rules = data.get("rules", [])
    if any(r.get("name") == req.rule.get("name") for r in rules):
        raise HTTPException(409, f"Rule {req.rule.get('name')!r} already exists in this file")
    rules.append(_rule_to_yaml_dict(req.rule))
    data["rules"] = rules
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False, indent=2)
    )
    return JSONResponse({"ok": True})


@app.get("/api/evals")
def api_evals():
    harness = EVALS_DIR / "harness.py"
    if not harness.exists():
        raise HTTPException(404, "Eval harness not found")
    r = subprocess.run(
        [sys.executable, str(harness), "--json"],
        capture_output=True, text=True, timeout=120,
        cwd=str(EVALS_DIR),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    try:
        return JSONResponse(json.loads(r.stdout))
    except json.JSONDecodeError:
        return JSONResponse({"error": r.stderr or r.stdout or "No output"})


@app.post("/api/evals/add-example")
def api_add_example(req: AddExampleRequest):
    # category can be a single name ("voice") or a nested path ("real-history/permission-asking")
    category_path = Path(req.category)
    if category_path.is_absolute() or ".." in category_path.parts:
        raise HTTPException(400, "Invalid category")
    dest = EVALS_DIR / "counterexamples" / category_path / f"{req.id}.yml"
    if dest.exists():
        raise HTTPException(409, f"Example {req.id!r} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "id": req.id,
        "source": req.source,
        "labels": [],
        "text": req.text,
        "expected_violations": req.expected_violations,
        "expected_clean": req.expected_clean,
        "rationale": req.rationale,
    }
    dest.write_text(yaml.dump(doc, allow_unicode=True, default_flow_style=False))
    return JSONResponse({"ok": True, "path": str(dest.relative_to(REPO_ROOT))})


@app.get("/api/library")
def api_library():
    """List every counterexample on disk, grouped by category, for the playground picker."""
    root = EVALS_DIR / "counterexamples"
    by_cat: dict[str, list[dict]] = {}
    for path in sorted(root.rglob("*.yml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue
        rel = path.relative_to(root)
        category = "/".join(rel.parts[:-1]) or "uncategorized"
        text = data.get("text", "")
        preview = text.strip().splitlines()[0][:90] if text.strip() else ""
        by_cat.setdefault(category, []).append({
            "id": data.get("id", path.stem),
            "category": category,
            "preview": preview,
            "text": text,
            "expected_violations": data.get("expected_violations", []) or [],
            "source": data.get("source", "synthetic"),
            "labels": data.get("labels", []) or [],
        })
    # stable order: category name asc, id asc
    result = [{"category": cat, "examples": sorted(items, key=lambda x: x["id"])}
              for cat, items in sorted(by_cat.items())]
    return JSONResponse(result)


@app.post("/api/rules/delete-rule")
def api_delete_rule(req: DeleteRuleRequest):
    """Remove a named rule from a YAML file. The file itself is kept even
    if it becomes empty (so the category dir isn't accidentally cleaned)."""
    path = REPO_ROOT / req.file_path
    path.resolve().relative_to(REPO_ROOT.resolve())
    if not path.exists():
        raise HTTPException(404, "File not found")
    data = yaml.safe_load(path.read_text()) or {}
    rules = data.get("rules", [])
    new_rules = [r for r in rules if r.get("name") != req.rule_name]
    if len(new_rules) == len(rules):
        raise HTTPException(404, f"Rule {req.rule_name!r} not found in {req.file_path}")
    data["rules"] = new_rules
    path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False, indent=2)
    )
    return JSONResponse({"ok": True, "remaining": len(new_rules)})


@app.post("/api/feedback")
def api_general_feedback(req: GeneralFeedbackRequest):
    """General-purpose feedback box. Appended to web/feedback.txt with timestamp + context."""
    if not req.feedback.strip():
        raise HTTPException(400, "feedback required")
    dest = REPO_ROOT / "web" / "feedback.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a") as f:
        f.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---\n")
        if req.context:
            f.write(f"[context: {req.context}]\n")
        f.write(req.feedback.rstrip() + "\n")
    return JSONResponse({"ok": True, "path": str(dest.relative_to(REPO_ROOT))})


@app.post("/api/submissions")
def api_submit(req: SubmissionRequest):
    """Save a rule-candidate submission for later triage.

    A submission is a real offending response plus Adrien's verdict and what
    should have happened. Stored as a timestamped YAML in submissions/ so it
    can later be turned into either a rule, a counterexample, or both.
    """
    if not req.offending_text.strip() or not req.feedback.strip():
        raise HTTPException(400, "offending_text and feedback required")
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ts = now.strftime("%Y%m%d-%H%M%S")
    # Slug from desired rule name if given, else from feedback first words
    slug_src = req.desired_rule_name.strip() or req.feedback.strip()
    slug_parts: list[str] = []
    for token in slug_src.lower().split():
        cleaned = "".join(ch for ch in token if ch.isalnum())
        if cleaned:
            slug_parts.append(cleaned)
        if len(slug_parts) >= 5:
            break
    slug = "-".join(slug_parts)[:40] or "submission"
    filename = f"{ts}-{slug}.yml"
    dest = SUBMISSIONS_DIR / filename
    doc = {
        "id": f"{ts}-{slug}",
        "created_at": now.isoformat(timespec="seconds"),
        "verdict": req.verdict,
        "desired_rule_name": req.desired_rule_name.strip() or None,
        "offending_text": req.offending_text,
        "feedback": req.feedback,
        "status": "new",
    }
    dest.write_text(yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False))
    return JSONResponse({"ok": True, "path": str(dest.relative_to(REPO_ROOT))})


@app.post("/api/diagram-feedback")
def api_feedback(req: FeedbackRequest):
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_FILE.open("a") as f:
        f.write(f"\n--- {datetime.now().isoformat()} ---\n{req.feedback}\n")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MOP Studio</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
body{font-family:system-ui,sans-serif;background:#030712;color:#f9fafb;min-height:100vh}
textarea,input,select{font-family:ui-monospace,monospace;font-size:.8rem}
.tab-panel{display:none}.tab-panel.active{display:block}
.tab-btn{padding:5px 13px;border-radius:6px;font-size:.8rem;font-weight:500;cursor:pointer;color:#9ca3af;background:transparent;border:none}
.tab-btn:hover{color:#e5e7eb;background:#374151}
.tab-btn.active{background:#4f46e5;color:#fff}
.card{background:#111827;border:1px solid #1f2937;border-radius:8px;overflow:hidden;margin-bottom:10px}
.card.active-rule{border-color:#14532d}
.card-header{display:flex;align-items:center;gap:12px;padding:10px 14px}
.card-body{border-top:1px solid #1f2937;padding:14px;display:none}
.card-body.open{display:block}
.rule-row{border-top:1px solid #1f2937}
.rule-row-header{display:flex;align-items:center;gap:10px;padding:8px 14px;cursor:pointer;user-select:none}
.rule-row-header:hover{background:#0f172a}
.rule-row-header .chev{color:#6b7280;font-size:.75rem;transition:transform .15s;display:inline-block;width:10px}
.rule-row.open .rule-row-header .chev{transform:rotate(90deg)}
.rule-row-body{display:none;padding:12px 14px;background:#0a0f1c;border-top:1px solid #111827}
.rule-row.open .rule-row-body{display:block}
.det-pill{font-size:.6rem;padding:1px 6px;border-radius:3px;font-family:monospace;text-transform:uppercase;letter-spacing:.05em}
.det-pill.llm{background:#1e1b4b;color:#a5b4fc}
.det-pill.det{background:#1c1917;color:#fbbf24}
.seg{display:inline-flex;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:2px;gap:2px}
.seg button{background:transparent;border:none;color:#9ca3af;font-size:.75rem;padding:5px 12px;border-radius:3px;cursor:pointer;font-family:inherit}
.seg button.on{background:#4f46e5;color:#fff}
.seg button:hover:not(.on){color:#e5e7eb}
.field{margin-bottom:12px}
.field label{display:block;font-size:.7rem;color:#9ca3af;margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em}
.field input[type=text],.field textarea,.field select{width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:6px 8px;color:#f9fafb;box-sizing:border-box}
.field input[type=text]:focus,.field textarea:focus,.field select:focus{outline:none;border-color:#6366f1}
.field-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:12px}
.field-row-2{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.toggle-wrap{display:flex;align-items:center;cursor:pointer}
.toggle-wrap input{display:none}
.toggle-track{width:36px;height:20px;border-radius:9999px;background:#374151;position:relative;transition:background .2s;flex-shrink:0}
.toggle-wrap input:checked+.toggle-track{background:#166534}
.toggle-thumb{position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:9999px;background:#fff;transition:left .2s}
.toggle-wrap input:checked+.toggle-track .toggle-thumb{left:18px}
.verdict{border-radius:6px;border:1px solid;padding:14px;margin-top:12px}
.verdict.accept{background:#052e16;border-color:#166534}
.verdict.reject{background:#2d0b0b;border-color:#7f1d1d}
.verdict.edit{background:#1c1400;border-color:#713f12}
.tag-accept{color:#4ade80;font-weight:700;font-size:.8rem;text-transform:uppercase}
.tag-reject{color:#f87171;font-weight:700;font-size:.8rem;text-transform:uppercase}
.tag-edit{color:#fbbf24;font-weight:700;font-size:.8rem;text-transform:uppercase}
.badge-active{background:#052e16;color:#4ade80;font-size:.65rem;padding:2px 7px;border-radius:3px;font-family:monospace}
.badge-pending{background:#1f2937;color:#6b7280;font-size:.65rem;padding:2px 7px;border-radius:3px;font-family:monospace}
.stat{background:#1f2937;border-radius:6px;padding:12px;text-align:center}
.mismatch-row{background:#2d0b0b;border:1px solid #7f1d1d;border-radius:4px;padding:6px 10px;font-size:.75rem;margin-bottom:4px}
.btn{padding:5px 14px;border-radius:5px;font-size:.8rem;font-weight:500;cursor:pointer;border:none}
.btn-primary{background:#4f46e5;color:#fff}.btn-primary:hover{background:#4338ca}.btn-primary:disabled{opacity:.4;cursor:default}
.btn-ghost{background:transparent;color:#9ca3af}.btn-ghost:hover{color:#e5e7eb}
.btn-danger{background:#7f1d1d;color:#fca5a5}.btn-danger:hover{background:#991b1b}
select{background:#0f172a;border:1px solid #374151;border-radius:4px;padding:4px 8px;color:#f9fafb}
.det-llm{}.det-det{}
.mermaid svg{max-width:100%}
.copy-btn{display:inline-flex;align-items:center;justify-content:center;background:transparent;border:none;color:#6b7280;cursor:pointer;padding:0 4px;font-size:.85rem;line-height:1;border-radius:3px;vertical-align:middle}
.copy-btn:hover{color:#a5b4fc;background:#1f2937}
.copy-btn.copied{color:#4ade80}
.rule-name-wrap{display:inline-flex;align-items:center;gap:2px}
</style>
</head>
<body>

<header style="background:#111827;border-bottom:1px solid #1f2937;padding:10px 20px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:10">
  <span style="font-weight:700;font-size:.95rem">MOP Studio</span>
  <div style="display:flex;gap:4px;flex-wrap:wrap">
    <button class="tab-btn active" onclick="switchTab('submit')">submit</button>
    <button class="tab-btn" onclick="switchTab('rules')">rules</button>
    <button class="tab-btn" onclick="switchTab('playground')">playground</button>
    <button class="tab-btn" onclick="switchTab('evals')">evals</button>
    <button class="tab-btn" onclick="switchTab('diagram')">diagram</button>
  </div>
  <div style="margin-left:auto;font-size:.7rem;color:#6b7280">MOP Studio</div>
</header>

<main style="max-width:900px;margin:0 auto;padding:20px 16px">

  <!-- SUBMIT -->
  <div id="panel-submit" class="tab-panel active">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">
      <span style="font-weight:600">Submit a rule candidate</span>
      <span style="font-size:.7rem;color:#9ca3af">paste a bad response · describe the fix · pick a verdict · saved to <code style="color:#a5b4fc">submissions/</code> for triage</span>
    </div>

    <div class="field">
      <label>Offending response</label>
      <textarea id="sub-text" rows="10" style="width:100%;background:#1f2937;border:1px solid #374151;border-radius:6px;padding:10px;color:#f9fafb;resize:vertical;box-sizing:border-box;font-family:ui-monospace,monospace;font-size:.8rem" placeholder="Paste the actual model output that was wrong…"></textarea>
    </div>

    <div class="field">
      <label>What was wrong — and what should have happened instead</label>
      <textarea id="sub-feedback" rows="6" style="width:100%;background:#1f2937;border:1px solid #374151;border-radius:6px;padding:10px;color:#f9fafb;resize:vertical;box-sizing:border-box;font-family:ui-monospace,monospace;font-size:.8rem" placeholder="e.g. Asked permission for a doable task. Should have just done the task and reported back."></textarea>
    </div>

    <div class="field">
      <label>Verdict — what MOP should have done with this output</label>
      <div class="seg" role="tablist" id="sub-verdict-seg">
        <button type="button" id="sub-v-reject" class="on" onclick="setSubmitVerdict('reject')">Reject</button>
        <button type="button" id="sub-v-rewrite" onclick="setSubmitVerdict('rewrite')">Rewrite</button>
        <button type="button" id="sub-v-accept" onclick="setSubmitVerdict('accept')">Accept (counterexample)</button>
      </div>
      <input type="hidden" id="sub-verdict" value="reject">
      <div id="sub-verdict-help" style="font-size:.7rem;color:#6b7280;margin-top:6px">Reject = MOP should have blocked the message and demanded a justification.</div>
    </div>

    <div class="field">
      <label>Desired rule name <span style="color:#6b7280;text-transform:none;font-weight:400">— optional, kebab-case</span></label>
      <input type="text" id="sub-rule-name" placeholder="no-permission-asking-for-doable-work">
    </div>

    <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
      <button class="btn btn-primary" id="sub-submit-btn" onclick="submitCandidate()">Save submission</button>
      <button class="btn btn-ghost" onclick="clearSubmitForm()">Clear</button>
      <span id="sub-status" style="font-size:.75rem"></span>
    </div>
  </div>

  <!-- PLAYGROUND -->
  <div id="panel-playground" class="tab-panel">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">
      <span style="font-weight:600">Playground</span>
      <span style="font-size:.7rem;color:#9ca3af">runs your text through <code style="color:#a5b4fc">submit_message</code> against the active rules — Haiku evaluator</span>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
      <button class="btn btn-ghost" style="font-size:.75rem" onclick="toggleLibraryPanel()" id="pg-library-toggle">📚 Browse library <span id="pg-library-count" style="color:#6b7280;margin-left:4px"></span></button>
      <input type="search" id="pg-library-filter" placeholder="filter by id or text…" oninput="renderLibrary()" style="flex:1;min-width:200px;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:5px 10px;color:#f9fafb;font-size:.75rem;display:none">
      <button class="btn btn-ghost" style="font-size:.7rem" onclick="loadLibrary()" title="Reload from disk">↻</button>
    </div>
    <div id="pg-library-panel" style="display:none;background:#0f172a;border:1px solid #374151;border-radius:6px;padding:10px;margin-bottom:8px;max-height:380px;overflow-y:auto"></div>
    <textarea id="pg-text" rows="8" style="width:100%;background:#1f2937;border:1px solid #374151;border-radius:6px;padding:10px;color:#f9fafb;resize:vertical;box-sizing:border-box" placeholder="Paste a Claude response to test against active rules…"></textarea>
    <div style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap">
      <button class="btn btn-primary" id="pg-run-btn" onclick="runEval()">Run submit_message()</button>
      <button class="btn btn-ghost" onclick="toggleSaveForm()">Save to library</button>
      <button class="btn btn-ghost" onclick="document.getElementById('pg-text').value='';document.getElementById('pg-result').style.display='none'">Clear</button>
    </div>
    <div id="pg-result" class="verdict" style="display:none">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span id="pg-action"></span>
        <span id="pg-violations" style="font-size:.75rem;color:#9ca3af;font-family:monospace"></span>
      </div>
      <div id="pg-rewrite-wrap" style="display:none;margin-top:10px;padding-top:10px;border-top:1px solid #374151">
        <div style="font-size:.7rem;color:#fbbf24;margin-bottom:4px">Rewritten:</div>
        <pre id="pg-rewrite" style="font-size:.8rem;background:#0f172a;border-radius:4px;padding:10px;white-space:pre-wrap;margin:0;color:#f9fafb"></pre>
      </div>
      <p id="pg-system-note" style="display:none;font-size:.75rem;color:#fca5a5;margin:8px 0 0;font-style:italic"></p>
    </div>

    <!-- Save-to-library inline form (hidden by default) -->
    <div id="pg-save-form" class="card" style="display:none;padding:14px;margin-top:14px">
      <div style="font-size:.85rem;font-weight:500;margin-bottom:10px">Save current text as library example</div>
      <div class="field-row">
        <div class="field"><label>ID (kebab-case)</label><input type="text" id="pg-save-id" placeholder="describe-the-bug-fix"></div>
        <div class="field"><label>Category</label><input type="text" id="pg-save-cat" placeholder="real-history/permission-asking" value="real-history/uncategorized"></div>
        <div class="field"><label>Source</label><select id="pg-save-src" style="width:100%"><option value="real-sanitized">real-sanitized</option><option value="real-telegram">real-telegram</option><option value="synthetic">synthetic</option></select></div>
      </div>
      <div class="field-row-2">
        <div class="field"><label>Expected violations (one per line)</label><textarea id="pg-save-violations" rows="3" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:6px;color:#f9fafb;box-sizing:border-box" placeholder="no-permission-asking-for-doable-work"></textarea></div>
        <div class="field"><label>Expected clean (one per line)</label><textarea id="pg-save-clean" rows="3" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:6px;color:#f9fafb;box-sizing:border-box"></textarea></div>
      </div>
      <div class="field"><label>Rationale</label><input type="text" id="pg-save-rationale" placeholder="Why this is interesting / what it tests"></div>
      <div style="display:flex;align-items:center;gap:8px">
        <button class="btn btn-primary" onclick="saveToLibrary()">Save</button>
        <button class="btn btn-ghost" onclick="toggleSaveForm()">Cancel</button>
        <span id="pg-save-status" style="font-size:.75rem"></span>
      </div>
    </div>
  </div>

  <!-- RULES -->
  <div id="panel-rules" class="tab-panel">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
      <span style="font-weight:600">Rules</span>
      <span style="font-size:.75rem;color:#6b7280">every <code style="color:#a5b4fc">rules/*.yml</code> file is loaded at runtime</span>
      <button class="btn btn-ghost" style="margin-left:auto" onclick="loadRules()">↻ refresh</button>
    </div>
    <div id="rules-loading" style="color:#6b7280;font-size:.85rem;display:none">Loading…</div>
    <div id="rules-list"></div>
  </div>

  <!-- EVALS -->
  <div id="panel-evals" class="tab-panel">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap">
      <span style="font-weight:600">Evals</span>
      <span style="font-size:.7rem;color:#9ca3af">existing counterexamples on disk · click one to expand · use "test" to run it through active rules</span>
      <button class="btn btn-primary" style="margin-left:auto" id="evals-run-btn" onclick="runEvals()">Run harness</button>
      <button class="btn btn-ghost" style="font-size:.75rem" onclick="loadEvalsLibrary()">↻ refresh</button>
    </div>
    <div id="evals-result" style="display:none;margin-bottom:14px"></div>
    <div id="evals-cats" style="display:flex;gap:2px;flex-wrap:wrap;border-bottom:1px solid #1f2937;padding-bottom:8px;margin-bottom:12px"></div>
    <div style="margin-bottom:10px">
      <input type="search" id="evals-filter" placeholder="filter by id / text / violation…" oninput="renderEvalsLibrary()" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:6px 10px;color:#f9fafb;font-size:.8rem;box-sizing:border-box">
    </div>
    <div id="evals-loading" style="color:#6b7280;font-size:.85rem;display:none">Loading…</div>
    <div id="evals-list"></div>
  </div>

  <!-- DIAGRAM -->
  <div id="panel-diagram" class="tab-panel">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
      <span style="font-weight:600">System Diagram</span>
    </div>
    <div class="card" style="padding:16px;overflow-x:auto;margin-bottom:14px">
      <div id="mermaid-container">
        <div class="mermaid">
flowchart TD
    AGENT[Coding agent] -->|"submit_message(text)"| MCP["MOP MCP tools (in-process)"]
    RULES[(rules/*.yml)] -.->|loaded| MCP
    MCP --> EVAL["Haiku call: accept | rewrite | reject"]
    HINTS["regex hints (advisory)"] -.->|context| EVAL
    EVAL --> APPLY{"verdict"}
    APPLY -->|Accepted| DELIVER([deliver original to user])
    APPLY -->|Rewritten| DELIVER2([deliver rewritten to user])
    APPLY -->|Rejected| PEND["pending_message = text<br/>agent sees violations"]
    PEND --> JUSTIFY["submit_justification(reason)"]
    JUSTIFY --> EVAL2["Haiku re-eval with justification"]
    EVAL2 --> APPLY
    JUSTIFY -.->|"after 4 attempts"| FAILOPEN["AcceptedFailedOpen:<br/>deliver original + system_note"]
    DELIVER --> STOP["Stop hook: turn ends"]
    DELIVER2 --> STOP
    FAILOPEN --> STOP
        </div>
      </div>
    </div>
    <div class="card" style="padding:16px">
      <div style="font-size:.85rem;font-weight:500;margin-bottom:6px">Feedback</div>
      <p style="font-size:.75rem;color:#6b7280;margin:0 0 8px">Describe what to change in the diagram or the MOP library. A coding agent picks this up from web/diagram-feedback.txt.</p>
      <textarea id="diagram-feedback" rows="4" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:8px;color:#f9fafb;resize:vertical;box-sizing:border-box" placeholder="e.g. 'Add violation log writer step'"></textarea>
      <div style="display:flex;align-items:center;gap:8px;margin-top:8px">
        <button class="btn btn-primary" onclick="submitFeedback()">Submit</button>
        <span id="fb-status" style="font-size:.75rem;color:#4ade80"></span>
      </div>
    </div>
  </div>

</main>

<!-- Floating feedback widget — visible on every tab -->
<div id="fb-fab" onclick="toggleGlobalFeedback()" style="position:fixed;right:18px;bottom:18px;width:48px;height:48px;border-radius:9999px;background:#4f46e5;color:#fff;display:flex;align-items:center;justify-content:center;font-size:1.3rem;cursor:pointer;box-shadow:0 6px 16px rgba(0,0,0,.4);z-index:50" title="Send feedback to Claude">💬</div>
<div id="fb-panel" style="display:none;position:fixed;right:18px;bottom:78px;width:min(360px,92vw);background:#111827;border:1px solid #374151;border-radius:10px;padding:14px;box-shadow:0 10px 30px rgba(0,0,0,.5);z-index:50">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
    <span style="font-weight:600;font-size:.9rem">Feedback to Claude</span>
    <button class="btn btn-ghost" style="font-size:1rem;padding:2px 8px" onclick="toggleGlobalFeedback()" title="Close">×</button>
  </div>
  <p style="font-size:.7rem;color:#9ca3af;margin:0 0 8px">Anything you want to tell the coding agent about this page. Saved to <code style="color:#a5b4fc">web/feedback.txt</code>.</p>
  <textarea id="fb-text" rows="5" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:8px;color:#f9fafb;resize:vertical;box-sizing:border-box;font-family:ui-monospace,monospace;font-size:.8rem" placeholder="e.g. The evals tab is bad. Make this change instead…"></textarea>
  <div style="display:flex;align-items:center;gap:8px;margin-top:8px">
    <button class="btn btn-primary" onclick="sendGlobalFeedback()">Send</button>
    <span id="fb-global-status" style="font-size:.75rem"></span>
  </div>
</div>

<script>
// --- Tab switching ---
function switchTab(name, fromInit) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  document.querySelector('.tab-btn[onclick*=\'' + name + '\']').classList.add('active');
  if (!fromInit) localStorage.setItem('mop-tab', name);
  if (name === 'rules' && !rulesLoaded) loadRules();
  if (name === 'evals' && !evalsData.length) loadEvalsLibrary();
  if (name === 'diagram') setTimeout(renderMermaid, 50);
}
(function() {
  const saved = localStorage.getItem('mop-tab');
  if (saved && document.getElementById('panel-' + saved)) switchTab(saved, true);
})();

// --- Mermaid ---
mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' });
let mermaidDone = false;
function renderMermaid() {
  if (mermaidDone) return;
  mermaidDone = true;
  mermaid.run({ nodes: document.querySelectorAll('.mermaid') });
}

// --- Clipboard / copy-name helper ---
function copyName(btn, name) {
  // event.stopPropagation handled inline on the button click
  const fallback = () => {
    const ta = document.createElement('textarea');
    ta.value = name; ta.setAttribute('readonly','');
    ta.style.position='absolute'; ta.style.left='-9999px';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch(_) {}
    document.body.removeChild(ta);
  };
  const done = () => {
    btn.classList.add('copied');
    const orig = btn.textContent;
    btn.textContent = '✓';
    setTimeout(() => { btn.classList.remove('copied'); btn.textContent = orig; }, 1200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(name).then(done).catch(() => { fallback(); done(); });
  } else { fallback(); done(); }
}

function copyableName(name, extraStyle) {
  const safe = esc(name);
  const style = extraStyle || 'font-family:monospace;font-size:.8rem;font-weight:600';
  return `<span class="rule-name-wrap"><span style="${style}">${safe}</span>` +
    `<button class="copy-btn" title="Copy rule name" onclick="event.stopPropagation();copyName(this,'${safe.replace(/'/g, "\\'")}')">⧉</button></span>`;
}

// --- Global feedback widget ---
function toggleGlobalFeedback() {
  const p = document.getElementById('fb-panel');
  p.style.display = p.style.display === 'block' ? 'none' : 'block';
}
async function sendGlobalFeedback() {
  const text = document.getElementById('fb-text').value.trim();
  const status = document.getElementById('fb-global-status');
  if (!text) { status.textContent = 'Empty.'; status.style.color = '#f87171'; return; }
  status.textContent = 'Sending…'; status.style.color = '#9ca3af';
  // Capture current tab as context so Claude knows where the gripe came from.
  const activeTab = document.querySelector('.tab-btn.active');
  const context = activeTab ? activeTab.textContent.trim() : '';
  try {
    const r = await fetch('/api/feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feedback: text, context })
    });
    if (!r.ok) { status.textContent = 'Failed'; status.style.color = '#f87171'; return; }
    status.textContent = '✓ Sent'; status.style.color = '#4ade80';
    document.getElementById('fb-text').value = '';
    setTimeout(() => {
      status.textContent = '';
      document.getElementById('fb-panel').style.display = 'none';
    }, 1200);
  } catch(e) { status.textContent = String(e); status.style.color = '#f87171'; }
}

// --- Evals library browser (replaces the heavy add-example form) ---
let evalsData = [];
let evalsActiveCat = '__all__';

async function loadEvalsLibrary() {
  document.getElementById('evals-loading').style.display = 'block';
  try {
    const r = await fetch('/api/library');
    evalsData = await r.json();
    renderEvalsCats();
    renderEvalsLibrary();
  } catch(e) {
    document.getElementById('evals-list').innerHTML =
      `<div style="color:#f87171;font-size:.85rem">Failed to load: ${esc(String(e))}</div>`;
  } finally {
    document.getElementById('evals-loading').style.display = 'none';
  }
}

function renderEvalsCats() {
  const total = evalsData.reduce((n, g) => n + g.examples.length, 0);
  const cats = [['__all__', 'all', total], ...evalsData.map(g => [g.category, g.category, g.examples.length])];
  document.getElementById('evals-cats').innerHTML = cats.map(([key, label, n]) => `
    <button class="tab-btn ${key === evalsActiveCat ? 'active' : ''}"
            onclick="setEvalsCat('${esc(key)}')">${esc(label)} <span style="color:#6b7280;font-size:.7rem">(${n})</span></button>`).join('');
}

function setEvalsCat(key) {
  evalsActiveCat = key;
  renderEvalsCats();
  renderEvalsLibrary();
}

function renderEvalsLibrary() {
  const list = document.getElementById('evals-list');
  const q = (document.getElementById('evals-filter').value || '').trim().toLowerCase();
  const matches = (ex) => !q || ex.id.toLowerCase().includes(q) || (ex.text || '').toLowerCase().includes(q)
    || ex.expected_violations.some(v => v.toLowerCase().includes(q));
  const groups = evalsActiveCat === '__all__' ? evalsData : evalsData.filter(g => g.category === evalsActiveCat);
  if (!groups.length) { list.innerHTML = '<div style="color:#9ca3af;font-size:.85rem">No examples.</div>'; return; }
  const html = groups.map(group => {
    const items = group.examples.filter(matches);
    if (!items.length) return '';
    const cards = items.map(ex => {
      const expected = ex.expected_violations.length
        ? ex.expected_violations.map(v => `<span style="background:#2d0b0b;color:#fca5a5;font-size:.65rem;padding:1px 6px;border-radius:3px;font-family:monospace;margin-right:4px;display:inline-flex;align-items:center">${esc(v)}<button class="copy-btn" style="margin-left:2px;color:#fca5a5" onclick="event.stopPropagation();copyName(this,'${esc(v)}')">⧉</button></span>`).join('')
        : `<span style="background:#052e16;color:#4ade80;font-size:.65rem;padding:1px 6px;border-radius:3px;font-family:monospace">clean</span>`;
      const previewSrc = (ex.text || '').trim();
      const preview = previewSrc.length > 240 ? previewSrc.slice(0, 240) + '…' : previewSrc;
      return `<div style="background:#1f2937;border:1px solid #374151;border-radius:5px;padding:10px 12px;margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
          <code style="color:#a5b4fc;font-size:.75rem;display:inline-flex;align-items:center">${esc(ex.id)}<button class="copy-btn" onclick="event.stopPropagation();copyName(this,'${esc(ex.id)}')">⧉</button></code>
          <span style="background:#1f2937;color:#9ca3af;font-size:.6rem;padding:1px 5px;border-radius:3px;font-family:monospace">${esc(ex.source || 'synthetic')}</span>
          ${expected}
        </div>
        <pre style="font-size:.75rem;color:#d1d5db;line-height:1.45;background:#0f172a;padding:8px;border-radius:4px;margin:0 0 6px;white-space:pre-wrap">${esc(preview)}</pre>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn btn-ghost" style="font-size:.7rem" onclick='testEvalExample(${JSON.stringify(ex.text).replace(/'/g, "&#39;")}, ${JSON.stringify(ex.expected_violations).replace(/'/g, "&#39;")}, this)'>Test against active rules</button>
          <button class="btn btn-ghost" style="font-size:.7rem" onclick="loadEvalIntoPlayground(${JSON.stringify(ex.text).replace(/'/g, '&#39;')})">Open in playground</button>
        </div>
        <div class="verdict ex-result" style="display:none;margin-top:8px"></div>
      </div>`;
    }).join('');
    return `<div style="margin-bottom:14px">
      <div style="font-size:.65rem;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">${esc(group.category)} <span style="color:#4b5563">(${items.length}${items.length === group.examples.length ? '' : ' / ' + group.examples.length})</span></div>
      ${cards}
    </div>`;
  }).join('');
  list.innerHTML = html || '<div style="color:#9ca3af;font-size:.85rem">No matches.</div>';
}

async function testEvalExample(text, expectedVios, btn) {
  const result = btn.parentElement.parentElement.querySelector('.ex-result');
  result.style.display = 'block';
  result.className = 'verdict';
  result.innerHTML = '<span style="color:#9ca3af;font-size:.8rem">Running active rules…</span>';
  btn.disabled = true;
  try {
    const r = await fetch('/api/evaluate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
    const d = await r.json();
    const action = d.action || 'unknown';
    const cls = action === 'accept' ? 'accept' : action === 'reject' ? 'reject' : 'edit';
    const tag = action === 'accept' ? 'tag-accept' : action === 'reject' ? 'tag-reject' : 'tag-edit';
    const actualVios = d.violations || [];
    // Match = (expected empty AND actual empty AND accept) OR (expected sorted == actual sorted)
    const expSorted = [...expectedVios].sort().join(',');
    const actSorted = [...actualVios].sort().join(',');
    const match = expSorted === actSorted && (expSorted.length > 0 ? action === 'reject' : action === 'accept');
    const matchBadge = match
      ? '<span style="background:#052e16;color:#4ade80;font-size:.65rem;padding:1px 6px;border-radius:3px;font-family:monospace">match ✓</span>'
      : '<span style="background:#1c1400;color:#fbbf24;font-size:.65rem;padding:1px 6px;border-radius:3px;font-family:monospace">mismatch</span>';
    result.className = 'verdict ' + cls;
    let inner = `<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><span class="${tag}">${esc(action)}</span>${matchBadge}`;
    if (actualVios.length) inner += `<span style="font-size:.7rem;color:#9ca3af;font-family:monospace">${esc(actualVios.join(', '))}</span>`;
    inner += '</div>';
    if (expectedVios.length) inner += `<div style="font-size:.65rem;color:#6b7280;margin-top:4px">expected: ${esc(expectedVios.join(', ') || 'clean')}</div>`;
    if (d.rewritten) inner += `<pre style="font-size:.75rem;background:#0f172a;border-radius:4px;padding:8px;margin:6px 0 0;white-space:pre-wrap">${esc(d.rewritten)}</pre>`;
    result.innerHTML = inner;
  } catch(e) {
    result.className = 'verdict reject';
    result.innerHTML = `<span class="tag-reject">error</span> <span style="color:#9ca3af;font-size:.75rem">${esc(String(e))}</span>`;
  } finally { btn.disabled = false; }
}

function loadEvalIntoPlayground(text) {
  switchTab('playground');
  const ta = document.getElementById('pg-text');
  if (ta) { ta.value = text; ta.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
}

// --- Submit tab ---
const SUBMIT_VERDICT_HELP = {
  reject: 'Reject = MOP should have blocked the message and demanded a justification.',
  rewrite: 'Rewrite = MOP should have silently rewritten the response to a cleaner form.',
  accept: 'Accept = this output is actually fine and should pass — use for counterexamples / good cases.',
};
function setSubmitVerdict(v) {
  document.getElementById('sub-verdict').value = v;
  ['reject','rewrite','accept'].forEach(x => {
    const btn = document.getElementById('sub-v-' + x);
    if (btn) btn.classList.toggle('on', x === v);
  });
  const help = document.getElementById('sub-verdict-help');
  if (help) help.textContent = SUBMIT_VERDICT_HELP[v] || '';
}

function clearSubmitForm() {
  ['sub-text','sub-feedback','sub-rule-name'].forEach(i => document.getElementById(i).value = '');
  setSubmitVerdict('reject');
  const s = document.getElementById('sub-status');
  s.textContent = '';
}

async function submitCandidate() {
  const offending = document.getElementById('sub-text').value.trim();
  const feedback = document.getElementById('sub-feedback').value.trim();
  const verdict = document.getElementById('sub-verdict').value;
  const ruleName = document.getElementById('sub-rule-name').value.trim();
  const status = document.getElementById('sub-status');
  if (!offending) { status.textContent = 'Offending response is required.'; status.style.color = '#f87171'; return; }
  if (!feedback) { status.textContent = 'Feedback is required.'; status.style.color = '#f87171'; return; }
  const btn = document.getElementById('sub-submit-btn');
  btn.disabled = true; btn.textContent = 'Saving…';
  status.textContent = ''; status.style.color = '#9ca3af';
  try {
    const r = await fetch('/api/submissions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        offending_text: offending,
        feedback: feedback,
        verdict: verdict,
        desired_rule_name: ruleName,
      })
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      status.textContent = e.detail || `HTTP ${r.status}`;
      status.style.color = '#f87171';
      return;
    }
    const d = await r.json();
    status.textContent = `✓ Saved to ${d.path}`;
    status.style.color = '#4ade80';
    clearSubmitForm();
    // Re-show the success message after clearSubmitForm wiped it
    status.textContent = `✓ Saved to ${d.path}`;
    status.style.color = '#4ade80';
    setTimeout(() => { status.textContent = ''; }, 5000);
  } catch(e) {
    status.textContent = String(e);
    status.style.color = '#f87171';
  } finally {
    btn.disabled = false; btn.textContent = 'Save submission';
  }
}

// --- Library picker ---
let libraryData = [];
async function loadLibrary() {
  try {
    const r = await fetch('/api/library');
    libraryData = await r.json();
    const total = libraryData.reduce((n, g) => n + g.examples.length, 0);
    document.getElementById('pg-library-count').textContent = `(${total})`;
    renderLibrary();
  } catch(e) {
    console.error('library load failed', e);
    document.getElementById('pg-library-panel').innerHTML =
      `<div style="color:#f87171;font-size:.8rem">Failed to load library: ${esc(String(e))}</div>`;
  }
}

function toggleLibraryPanel() {
  const panel = document.getElementById('pg-library-panel');
  const filter = document.getElementById('pg-library-filter');
  const open = panel.style.display === 'block';
  panel.style.display = open ? 'none' : 'block';
  filter.style.display = open ? 'none' : 'inline-block';
  if (!open) renderLibrary();
}

function renderLibrary() {
  const panel = document.getElementById('pg-library-panel');
  const q = (document.getElementById('pg-library-filter').value || '').trim().toLowerCase();
  if (!libraryData.length) {
    panel.innerHTML = '<div style="color:#9ca3af;font-size:.8rem;padding:8px">Library is empty.</div>';
    return;
  }
  const matches = (ex) => !q || ex.id.toLowerCase().includes(q) || ex.text.toLowerCase().includes(q)
    || ex.expected_violations.some(v => v.toLowerCase().includes(q));
  const html = libraryData.map(group => {
    const items = group.examples.filter(matches);
    if (!items.length) return '';
    const cards = items.map(ex => {
      const expected = ex.expected_violations.length
        ? ex.expected_violations.map(v => `<span style="background:#2d0b0b;color:#fca5a5;font-size:.65rem;padding:1px 6px;border-radius:3px;font-family:monospace;margin-right:4px;display:inline-flex;align-items:center">${esc(v)}<button class="copy-btn" style="color:#fca5a5;margin-left:2px" onclick="event.stopPropagation();copyName(this,'${esc(v)}')">⧉</button></span>`).join('')
        : `<span style="background:#052e16;color:#4ade80;font-size:.65rem;padding:1px 6px;border-radius:3px;font-family:monospace">clean</span>`;
      const preview = (ex.text || '').trim().slice(0, 220).replace(/\s+/g, ' ');
      const more = (ex.text || '').length > 220 ? '…' : '';
      const sourceBadge = `<span style="background:#1f2937;color:#9ca3af;font-size:.6rem;padding:1px 5px;border-radius:3px;font-family:monospace">${esc(ex.source)}</span>`;
      return `<div onclick="loadLibraryExample('${esc(group.category)}','${esc(ex.id)}')" style="background:#1f2937;border:1px solid #374151;border-radius:5px;padding:8px 10px;margin-bottom:6px;cursor:pointer;transition:border-color .12s" onmouseover="this.style.borderColor='#4f46e5'" onmouseout="this.style.borderColor='#374151'">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">
          <code style="color:#a5b4fc;font-size:.75rem">${esc(ex.id)}</code>
          ${sourceBadge}
          ${expected}
        </div>
        <div style="font-size:.72rem;color:#d1d5db;line-height:1.4">${esc(preview)}${more}</div>
      </div>`;
    }).join('');
    return `<div style="margin-bottom:10px">
      <div style="font-size:.65rem;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">${esc(group.category)} <span style="color:#4b5563">(${items.length}${items.length === group.examples.length ? '' : ' / ' + group.examples.length})</span></div>
      ${cards}
    </div>`;
  }).join('');
  panel.innerHTML = html || '<div style="color:#9ca3af;font-size:.8rem;padding:8px">No matches.</div>';
}

function loadLibraryExample(cat, id) {
  const group = libraryData.find(g => g.category === cat);
  if (!group) return;
  const ex = group.examples.find(x => x.id === id);
  if (!ex) return;
  document.getElementById('pg-text').value = ex.text;
  const hint = ex.expected_violations.length
    ? `Expected violations: ${ex.expected_violations.join(', ')}`
    : 'Expected: clean';
  const result = document.getElementById('pg-result');
  result.style.display = 'block';
  result.className = 'verdict accept';
  document.getElementById('pg-action').textContent = 'loaded';
  document.getElementById('pg-action').className = 'tag-accept';
  document.getElementById('pg-violations').textContent = hint;
  document.getElementById('pg-rewrite-wrap').style.display = 'none';
  document.getElementById('pg-system-note').style.display = 'none';
  // Close the panel after loading
  document.getElementById('pg-library-panel').style.display = 'none';
  document.getElementById('pg-library-filter').style.display = 'none';
  // Scroll the textarea into view so the loaded text is obviously there
  document.getElementById('pg-text').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function toggleSaveForm() {
  const f = document.getElementById('pg-save-form');
  const showing = f.style.display === 'block';
  f.style.display = showing ? 'none' : 'block';
  if (!showing) {
    // suggest an ID slug from the first few words
    const txt = document.getElementById('pg-text').value.trim();
    if (txt && !document.getElementById('pg-save-id').value) {
      const slug = txt.toLowerCase().match(/[a-z0-9]+/g)?.slice(0, 5).join('-').slice(0, 40) || '';
      document.getElementById('pg-save-id').value = slug;
    }
  }
}

async function saveToLibrary() {
  const id = document.getElementById('pg-save-id').value.trim();
  const text = document.getElementById('pg-text').value.trim();
  const cat = document.getElementById('pg-save-cat').value.trim() || 'real-history/uncategorized';
  if (!id || !text) { alert('ID and text required.'); return; }
  const violations = document.getElementById('pg-save-violations').value.trim().split(/\n+/).filter(Boolean);
  const clean = document.getElementById('pg-save-clean').value.trim().split(/\n+/).filter(Boolean);
  const status = document.getElementById('pg-save-status');
  status.textContent = 'Saving…'; status.style.color = '#9ca3af';
  try {
    const r = await fetch('/api/evals/add-example', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id, text,
        expected_violations: violations,
        expected_clean: clean,
        rationale: document.getElementById('pg-save-rationale').value.trim(),
        category: cat,
        source: document.getElementById('pg-save-src').value,
      })
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      status.textContent = e.detail || `HTTP ${r.status}`;
      status.style.color = '#f87171';
      return;
    }
    status.textContent = '✓ Saved'; status.style.color = '#4ade80';
    ['pg-save-id','pg-save-violations','pg-save-clean','pg-save-rationale'].forEach(i => document.getElementById(i).value = '');
    await loadLibrary();
    setTimeout(() => { status.textContent = ''; document.getElementById('pg-save-form').style.display = 'none'; }, 1500);
  } catch(e) { status.textContent = String(e); status.style.color = '#f87171'; }
}

// --- Playground ---
async function runEval() {
  const text = document.getElementById('pg-text').value.trim();
  if (!text) return;
  const btn = document.getElementById('pg-run-btn');
  btn.textContent = 'Running…'; btn.disabled = true;
  try {
    const r = await fetch('/api/evaluate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
    const d = await r.json();
    const el = document.getElementById('pg-result');
    el.className = 'verdict ' + (d.action || 'accept');
    el.style.display = 'block';
    const act = document.getElementById('pg-action');
    act.textContent = d.action || '';
    act.className = 'tag-' + (d.action || 'accept');
    const violEl = document.getElementById('pg-violations');
    if (d.violations && d.violations.length) {
      violEl.innerHTML = d.violations.map(v =>
        `<span style="display:inline-flex;align-items:center;margin-right:8px">${esc(v)}<button class="copy-btn" onclick="copyName(this,'${esc(v)}')">⧉</button></span>`
      ).join('');
    } else {
      violEl.textContent = '';
    }
    const rw = document.getElementById('pg-rewrite-wrap');
    if (d.rewritten) { rw.style.display = 'block'; document.getElementById('pg-rewrite').textContent = d.rewritten; }
    else rw.style.display = 'none';
    const sn = document.getElementById('pg-system-note');
    if (d.system_note) { sn.style.display = 'block'; sn.textContent = d.system_note; }
    else sn.style.display = 'none';
  } catch(e) { alert('Eval failed: ' + e); }
  finally { btn.textContent = 'Run submit_message()'; btn.disabled = false; }
}

// --- Rules ---
let rulesData = [];
let rulesLoaded = false;

async function loadRules() {
  document.getElementById('rules-loading').style.display = 'block';
  try {
    const r = await fetch('/api/rules');
    rulesData = await r.json();
    rulesLoaded = true;
    renderRules();
  } finally { document.getElementById('rules-loading').style.display = 'none'; }
}

function renderRules() {
  const list = document.getElementById('rules-list');
  if (!rulesData.length) {
    list.innerHTML = '<div style="color:#9ca3af;font-size:.85rem">No rule files in rules/.</div>';
    return;
  }
  list.innerHTML = rulesData.map((file, fi) => {
    const ruleCount = file.rules.length;
    const ruleLabel = ruleCount === 1 ? '1 rule' : `${ruleCount} rules`;
    return `
    <div class="card active-rule" id="file-${fi}">
      <div class="card-header">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span style="font-family:monospace;font-size:.85rem">${esc(file.filename)}</span>
            <span style="font-size:.7rem;color:#6b7280">${ruleLabel}</span>
          </div>
        </div>
      </div>
      ${file.rules.map((rule, ri) => renderRuleRow(fi, ri, rule)).join('')}
      <div id="newrule-slot-${fi}"></div>
      <div style="padding:8px 14px;border-top:1px solid #1f2937">
        <button class="btn btn-ghost" style="font-size:.75rem" onclick="addRuleForm(${fi})">+ add rule</button>
      </div>
    </div>`;
  }).join('');
}

function renderRuleRow(fi, ri, rule) {
  const id = `r${fi}-${ri}`;
  const det = rule.detector || 'llm';
  const detLabel = det === 'llm' ? 'LLM' : (rule.det_type === 'word_count' ? 'WORD COUNT' : 'REGEX');
  const detClass = det === 'llm' ? 'llm' : 'det';
  return `
    <div class="rule-row" id="row-${id}">
      <div class="rule-row-header" onclick="toggleRuleBody('${id}')">
        <span class="chev">▶</span>
        ${copyableName(rule.name)}
        <span class="det-pill ${detClass}">${detLabel}</span>
        <span style="font-size:.7rem;color:#6b7280;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(rule.description || '')}</span>
      </div>
      <div class="rule-row-body" id="body-${id}">
        ${renderRuleForm(fi, ri, rule, false)}
      </div>
    </div>`;
}

function renderRuleForm(fi, ri, rule, isNew) {
  const id = isNew ? `r${fi}-new` : `r${fi}-${ri}`;
  const detIsLlm = rule.detector === 'llm' || rule.detector === undefined;
  const dtype = rule.det_type || 'regex';
  return `
      ${isNew ? `<div style="font-size:.8rem;font-weight:600;margin-bottom:12px;color:#a5b4fc">New rule</div>` : ''}
      <div class="field-row-2">
        <div class="field"><label>Name *</label><input type="text" id="${id}-name" value="${esc(rule.name||'')}" placeholder="kebab-case-id"></div>
        <div class="field"><label>Description</label><input type="text" id="${id}-desc" value="${esc(rule.description||'')}" placeholder="one-line summary"></div>
      </div>
      <div class="field">
        <label>Detection</label>
        <div class="seg" role="tablist">
          <button type="button" id="${id}-det-llm" class="${detIsLlm?'on':''}" onclick="setDetector('${id}','llm')">Ask Haiku</button>
          <button type="button" id="${id}-det-det" class="${!detIsLlm?'on':''}" onclick="setDetector('${id}','deterministic')">Pattern match</button>
        </div>
        <input type="hidden" id="${id}-det" value="${detIsLlm?'llm':'deterministic'}">
        <div style="font-size:.7rem;color:#6b7280;margin-top:6px" id="${id}-det-help">
          ${detIsLlm
            ? 'Haiku reads each message and decides accept / rewrite / reject against your prompt.'
            : 'Deterministic check — patterns hint to Haiku as advisory context. Verdict still comes from Haiku.'}
        </div>
      </div>
      <!-- LLM params -->
      <div id="${id}-llm-params" style="${detIsLlm?'':'display:none'}">
        <div class="field"><label>LLM prompt — what should Haiku flag?</label>
          <textarea id="${id}-prompt" rows="5" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:8px;color:#f9fafb;box-sizing:border-box" placeholder="Is this message asking permission for work the agent could just do?">${esc(rule.llm_prompt||'')}</textarea>
        </div>
      </div>
      <!-- Deterministic params -->
      <div id="${id}-det-params" style="${!detIsLlm?'':'display:none'}">
        <div class="field">
          <label>Pattern type</label>
          <div class="seg">
            <button type="button" id="${id}-dtype-regex" class="${dtype==='regex'?'on':''}" onclick="setDetType('${id}','regex')">Regex list</button>
            <button type="button" id="${id}-dtype-wc" class="${dtype==='word_count'?'on':''}" onclick="setDetType('${id}','word_count')">Word count</button>
          </div>
          <input type="hidden" id="${id}-dtype" value="${dtype}">
        </div>
        <div class="field" id="${id}-maxwords-wrap" style="${dtype==='word_count'?'':'display:none'}">
          <label>Max words</label>
          <input type="text" id="${id}-maxwords" value="${esc(String(rule.det_max_words||200))}">
        </div>
        <div class="field" id="${id}-patterns-wrap" style="${dtype==='word_count'?'display:none':''}">
          <label>Regex patterns (one per line)</label>
          <textarea id="${id}-patterns" rows="4" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:8px;color:#f9fafb;box-sizing:border-box" placeholder="\\b[a-f0-9]{7,}\\b">${esc((rule.det_patterns||[]).join('\n'))}</textarea>
        </div>
      </div>
      <div class="field"><label>Guidance — shown to Claude on violation</label>
        <textarea id="${id}-guidance" rows="3" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:8px;color:#f9fafb;box-sizing:border-box">${esc(rule.guidance||'')}</textarea>
      </div>
      <div class="field"><label>Rationale — internal notes (not shown to Claude)</label>
        <textarea id="${id}-rationale" rows="2" style="width:100%;background:#0f172a;border:1px solid #374151;border-radius:5px;padding:8px;color:#f9fafb;box-sizing:border-box">${esc(rule.rationale||'')}</textarea>
      </div>
      <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
        <button class="btn btn-primary" onclick="${isNew ? `saveNewRule(${fi},'${id}')` : `saveRule(${fi},${ri},'${id}')`}">Save</button>
        <button class="btn btn-ghost" onclick="${isNew ? `cancelNewRule(${fi})` : `toggleRuleBody('${id}')`}">${isNew?'Cancel':'Collapse'}</button>
        <span id="${id}-status" style="font-size:.75rem"></span>
      </div>`;
}

function toggleRuleBody(id) {
  const row = document.getElementById('row-' + id);
  if (row) row.classList.toggle('open');
}

function setDetector(id, value) {
  document.getElementById(id + '-det').value = value;
  document.getElementById(id + '-det-llm').classList.toggle('on', value === 'llm');
  document.getElementById(id + '-det-det').classList.toggle('on', value === 'deterministic');
  document.getElementById(id + '-llm-params').style.display = value === 'llm' ? '' : 'none';
  document.getElementById(id + '-det-params').style.display = value === 'deterministic' ? '' : 'none';
  const help = document.getElementById(id + '-det-help');
  if (help) help.textContent = value === 'llm'
    ? 'Haiku reads each message and decides accept / rewrite / reject against your prompt.'
    : 'Deterministic check — patterns hint to Haiku as advisory context. Verdict still comes from Haiku.';
}

function setDetType(id, value) {
  document.getElementById(id + '-dtype').value = value;
  document.getElementById(id + '-dtype-regex').classList.toggle('on', value === 'regex');
  document.getElementById(id + '-dtype-wc').classList.toggle('on', value === 'word_count');
  document.getElementById(id + '-patterns-wrap').style.display = value === 'word_count' ? 'none' : '';
  document.getElementById(id + '-maxwords-wrap').style.display = value === 'word_count' ? '' : 'none';
}

function collectRule(id) {
  const det = document.getElementById(id + '-det').value;
  const dtypeEl = document.getElementById(id + '-dtype');
  const dtype = det === 'deterministic' && dtypeEl ? dtypeEl.value : null;
  return {
    name: document.getElementById(id + '-name').value.trim(),
    description: document.getElementById(id + '-desc').value.trim(),
    detector: det,
    guidance: document.getElementById(id + '-guidance').value.trim(),
    rationale: document.getElementById(id + '-rationale').value.trim(),
    llm_prompt: det === 'llm' ? document.getElementById(id + '-prompt').value.trim() : '',
    det_type: dtype || 'regex',
    det_patterns: (dtype === 'regex' && document.getElementById(id + '-patterns'))
      ? document.getElementById(id + '-patterns').value.trim().split(/\n+/).filter(Boolean) : [],
    det_max_words: (dtype === 'word_count' && document.getElementById(id + '-maxwords'))
      ? parseInt(document.getElementById(id + '-maxwords').value) || 200 : null,
  };
}

async function saveRule(fi, ri, id) {
  const rule = collectRule(id);
  if (!rule.name) { showStatus(id, 'Name required', 'error'); return; }
  showStatus(id, 'Saving…', 'gray');
  try {
    const r = await fetch('/api/rules/save-rule', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: rulesData[fi].path, original_name: rulesData[fi].rules[ri].name, rule })
    });
    if (!r.ok) { const e = await r.json(); showStatus(id, e.detail || 'Error', 'error'); return; }
    rulesData[fi].rules[ri] = rule;
    showStatus(id, '✓ Saved', 'ok');
    setTimeout(() => { showStatus(id, '', ''); }, 3000);
  } catch(e) { showStatus(id, String(e), 'error'); }
}

function addRuleForm(fi) {
  const slot = document.getElementById('newrule-slot-' + fi);
  if (!slot) return;
  if (slot.firstChild) return; // already open
  const blankRule = { name:'', description:'', detector:'llm', guidance:'', rationale:'', llm_prompt:'', det_type:'regex', det_patterns:[], det_max_words:null };
  slot.innerHTML = `<div style="border-top:1px solid #1f2937;padding:14px;background:#0a0f1c">${renderRuleForm(fi, null, blankRule, true)}</div>`;
}

function cancelNewRule(fi) {
  const slot = document.getElementById('newrule-slot-' + fi);
  if (slot) slot.innerHTML = '';
}

async function saveNewRule(fi, id) {
  const rule = collectRule(id);
  if (!rule.name) { showStatus(id, 'Name required', 'error'); return; }
  showStatus(id, 'Saving…', 'gray');
  try {
    const r = await fetch('/api/rules/add-rule', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: rulesData[fi].path, rule })
    });
    if (!r.ok) { const e = await r.json(); showStatus(id, e.detail || 'Error', 'error'); return; }
    rulesData[fi].rules.push(rule);
    showStatus(id, '✓ Saved', 'ok');
    setTimeout(renderRules, 1500);
  } catch(e) { showStatus(id, String(e), 'error'); }
}

function showStatus(id, msg, type) {
  const el = document.getElementById(id + '-status');
  if (!el) return;
  el.textContent = msg;
  el.style.color = type === 'ok' ? '#4ade80' : type === 'error' ? '#f87171' : '#9ca3af';
}


// --- Evals ---
async function runEvals() {
  const btn = document.getElementById('evals-run-btn');
  btn.textContent = 'Running…'; btn.disabled = true;
  const res = document.getElementById('evals-result');
  res.style.display = 'none';
  try {
    const r = await fetch('/api/evals');
    const d = await r.json();
    if (d.error) {
      res.innerHTML = `<div style="color:#f87171;font-size:.85rem">${esc(d.error)}</div>`;
    } else {
      const s = d.summary || {};
      const mm = (d.mismatches || []).map(m =>
        `<div class="mismatch-row"><span style="color:#f87171;font-family:monospace">${esc(m.type)}</span>
         <span style="color:#d1d5db;margin-left:8px">${esc(m.rule)} vs ${esc(m.example)}</span></div>`).join('');
      res.innerHTML = `
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px">
          <div class="stat"><div style="font-size:1.5rem;font-weight:700;color:#4ade80">${s.correct||0}</div><div style="font-size:.7rem;color:#9ca3af;margin-top:4px">Correct</div></div>
          <div class="stat"><div style="font-size:1.5rem;font-weight:700;color:#f87171">${s.mismatches||0}</div><div style="font-size:.7rem;color:#9ca3af;margin-top:4px">Mismatches</div></div>
          <div class="stat"><div style="font-size:1.5rem;font-weight:700;color:#f9fafb">${s.examples||0}</div><div style="font-size:.7rem;color:#9ca3af;margin-top:4px">Examples</div></div>
          <div class="stat"><div style="font-size:1.5rem;font-weight:700;color:#fbbf24">${s.rules_llm_skipped||0}</div><div style="font-size:.7rem;color:#9ca3af;margin-top:4px">LLM skipped</div></div>
        </div>
        ${mm || '<div style="color:#4ade80;font-size:.85rem">✓ All deterministic evals pass</div>'}`;
    }
    res.style.display = 'block';
  } catch(e) { res.innerHTML = `<div style="color:#f87171">${esc(String(e))}</div>`; res.style.display = 'block'; }
  finally { btn.textContent = 'Run harness'; btn.disabled = false; }
}

async function addExample() {
  const id = document.getElementById('ex-id').value.trim();
  const text = document.getElementById('ex-text').value.trim();
  if (!id || !text) { alert('ID and text required.'); return; }
  const violations = document.getElementById('ex-violations').value.trim().split(/\n+/).filter(Boolean);
  const clean = document.getElementById('ex-clean').value.trim().split(/\n+/).filter(Boolean);
  const status = document.getElementById('ex-status');
  status.textContent = 'Saving…'; status.style.color = '#9ca3af';
  try {
    const r = await fetch('/api/evals/add-example', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, text, expected_violations: violations, expected_clean: clean,
        rationale: document.getElementById('ex-rationale').value.trim(),
        category: document.getElementById('ex-cat').value,
        source: document.getElementById('ex-src').value })
    });
    if (!r.ok) { const e = await r.json(); status.textContent = e.detail || 'Error'; status.style.color = '#f87171'; }
    else {
      status.textContent = '✓ Added'; status.style.color = '#4ade80';
      ['ex-id','ex-text','ex-violations','ex-clean','ex-rationale'].forEach(i => document.getElementById(i).value = '');
      setTimeout(() => status.textContent = '', 3000);
    }
  } catch(e) { status.textContent = String(e); status.style.color = '#f87171'; }
}

// --- Diagram ---
async function submitFeedback() {
  const text = document.getElementById('diagram-feedback').value.trim();
  if (!text) return;
  const status = document.getElementById('fb-status');
  status.textContent = 'Saving…'; status.style.color = '#9ca3af';
  try {
    await fetch('/api/diagram-feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feedback: text })
    });
    status.textContent = '✓ Saved to web/diagram-feedback.txt';
    document.getElementById('diagram-feedback').value = '';
    setTimeout(() => status.textContent = '', 5000);
  } catch(e) { status.textContent = String(e); }
}

// --- Util ---
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Boot
loadRules();
loadLibrary();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def root():
    # Aggressive no-cache so iPad Safari doesn't serve stale UI after edits.
    return HTMLResponse(
        HTML,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("MOP_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("MOP_WEB_PORT", "7731"))
    uvicorn.run(app, host=host, port=port, log_level="info")
