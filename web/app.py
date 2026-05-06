"""MOP Studio — playground, rules config, evals, system diagram.

Run:   uv run web/app.py
URL:   http://bajor:7731  (Tailscale)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mop import Action, MopConfig, evaluate  # noqa: E402

RULES_ACTIVE = REPO_ROOT / "rules" / "active"
RULES_PENDING = REPO_ROOT / "rules" / "pending"
EVALS_DIR = REPO_ROOT / "evals"
FEEDBACK_FILE = REPO_ROOT / "web" / "diagram-feedback.txt"
PATCHBAY_VIOLATIONS = Path.home() / "Developer/patchbay-relay/logs/mop-violations.jsonl"

app = FastAPI(title="MOP Studio")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class EvalRequest(BaseModel):
    text: str
    backend: str = "haiku"


class SaveRequest(BaseModel):
    path: str
    content: str


class ToggleRequest(BaseModel):
    path: str


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


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.post("/api/evaluate")
async def api_evaluate(req: EvalRequest):
    config = MopConfig(
        rules_dir=RULES_ACTIVE,
        llm_backend=req.backend,  # type: ignore[arg-type]
    )
    verdict = await evaluate(req.text, config)
    result: dict = {
        "action": verdict.action.value,
        "rule": verdict.rule,
        "reason": verdict.reason,
        "guidance": verdict.guidance,
    }
    if verdict.action == Action.EDIT:
        from mop import rewrite as mop_rewrite
        result["rewritten"] = await mop_rewrite(
            req.text, verdict.rule or "unknown", verdict.guidance or ""
        )
    return JSONResponse(result)


@app.get("/api/rules")
def api_rules():
    files = []
    for status, dirpath in [("active", RULES_ACTIVE), ("pending", RULES_PENDING)]:
        if not dirpath.exists():
            continue
        for path in sorted(dirpath.rglob("*.yml")):
            content = path.read_text()
            try:
                data = yaml.safe_load(content) or {}
                rule_names = [r.get("name", "?") for r in data.get("rules", [])]
            except Exception:
                rule_names = []
            files.append({
                "path": str(path.relative_to(REPO_ROOT)),
                "status": status,
                "filename": path.name,
                "rule_names": rule_names,
                "content": content,
            })
    return JSONResponse(files)


@app.post("/api/rules/save")
def api_save(req: SaveRequest):
    path = REPO_ROOT / req.path
    path.resolve().relative_to(REPO_ROOT.resolve())  # safety check
    if path.suffix != ".yml":
        raise HTTPException(400, "Only .yml files")
    try:
        yaml.safe_load(req.content)
    except yaml.YAMLError as e:
        raise HTTPException(400, f"Invalid YAML: {e}")
    path.write_text(req.content)
    return JSONResponse({"ok": True})


@app.post("/api/rules/toggle")
def api_toggle(req: ToggleRequest):
    path = REPO_ROOT / req.path
    path.resolve().relative_to(REPO_ROOT.resolve())
    if not path.exists():
        raise HTTPException(404, "File not found")
    if RULES_ACTIVE in path.resolve().parents or path.resolve().parent == RULES_ACTIVE:
        dest_dir = RULES_PENDING
        new_status = "pending"
    else:
        dest_dir = RULES_ACTIVE
        new_status = "active"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    path.rename(dest)
    return JSONResponse({"new_status": new_status, "new_path": str(dest.relative_to(REPO_ROOT))})


@app.get("/api/evals")
def api_evals():
    harness = EVALS_DIR / "harness.py"
    if not harness.exists():
        raise HTTPException(404, "Eval harness not found")
    r = subprocess.run(
        [sys.executable, str(harness), "--json"],
        capture_output=True, text=True, timeout=120,
        cwd=str(EVALS_DIR),
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    try:
        return JSONResponse(json.loads(r.stdout))
    except json.JSONDecodeError:
        return JSONResponse({"error": r.stderr or r.stdout or "Harness produced no output"})


@app.post("/api/evals/add-example")
def api_add_example(req: AddExampleRequest):
    dest = EVALS_DIR / "counterexamples" / req.category / f"{req.id}.yml"
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


@app.get("/api/violations")
def api_violations():
    if not PATCHBAY_VIOLATIONS.exists():
        return JSONResponse([])
    lines = PATCHBAY_VIOLATIONS.read_text().strip().splitlines()
    entries = []
    for line in reversed(lines[-100:]):
        try:
            e = json.loads(line)
            if e.get("text_preview"):  # skip empty-message entries from tests
                entries.append(e)
        except json.JSONDecodeError:
            pass
    return JSONResponse(entries[:30])


@app.post("/api/diagram-feedback")
def api_feedback(req: FeedbackRequest):
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_FILE.open("a") as f:
        f.write(f"\n--- {datetime.now().isoformat()} ---\n{req.feedback}\n")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_MERMAID = """
graph LR
    T([📱 Telegram]) -->|message| PB[Patchbay Bridge]
    PB --> HS{{Harness}}
    HS -->|cc-sdk-mop| MOP[MOP Harness]
    HS -->|cc-sdk / cc-cli / pi| DIRECT[Direct Harness]
    MOP --> INNER[cc-sdk inner]
    INNER --> CC[Claude Code]
    CC --> BUF[Buffer events]
    BUF --> EVAL{{evaluate}}
    EVAL -->|ACCEPT| DEL([✅ Deliver])
    EVAL -->|REJECT| GUIDE[Inject guidance + retry]
    EVAL -->|EDIT| REW[pydantic-ai rewrite]
    REW --> DEL
    GUIDE -->|attempt < max_retries| INNER
    GUIDE -->|exhausted| DEL
    RULES[(rules/active)] -.->|loaded| EVAL
    VLOG[(violations.jsonl)] -.->|appended| EVAL
    DIRECT --> DEL
    DEL --> T
"""

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MOP Studio</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/alpinejs@3.14.3/dist/cdn.min.js" defer></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
  [x-cloak]{display:none!important}
  textarea{font-family:ui-monospace,monospace}
  .tab-btn{padding:6px 14px;border-radius:6px;font-size:.8rem;font-weight:500;cursor:pointer;transition:background .15s,color .15s}
  .tab-active{background:#4f46e5;color:#fff}
  .tab-inactive{color:#9ca3af}
  .tab-inactive:hover{color:#e5e7eb;background:#374151}
  .verdict-accept{background:#052e16;border-color:#166534}
  .verdict-reject{background:#2d0b0b;border-color:#7f1d1d}
  .verdict-edit{background:#1c1400;border-color:#713f12}
  .toggle-track{position:relative;display:inline-block;width:36px;height:20px;border-radius:9999px;cursor:pointer;transition:background .2s}
  .toggle-thumb{position:absolute;top:2px;width:16px;height:16px;border-radius:9999px;background:#fff;transition:left .2s}
</style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen" x-data="mopApp()" x-cloak>

<header class="bg-gray-900 border-b border-gray-800 px-5 py-3 flex items-center gap-4 sticky top-0 z-10">
  <span class="font-bold text-base tracking-tight">MOP Studio</span>
  <div class="flex gap-1">
    <template x-for="t in ['playground','rules','evals','diagram']" :key="t">
      <button @click="tab=t" class="tab-btn" :class="tab===t?'tab-active':'tab-inactive'" x-text="t"></button>
    </template>
  </div>
  <div class="ml-auto text-xs text-gray-600">model-output-protocol · bajor:7731</div>
</header>

<main class="max-w-4xl mx-auto px-4 py-5 space-y-4">

  <!-- PLAYGROUND -->
  <div x-show="tab==='playground'" class="space-y-4">
    <div class="flex items-center gap-3 flex-wrap">
      <h2 class="text-base font-semibold">Playground</h2>
      <select x-model="backend" class="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs">
        <option value="stub">stub (always accept)</option>
        <option value="haiku">haiku (live LLM)</option>
      </select>
      <button @click="loadViolations()" class="text-xs text-indigo-400 hover:text-indigo-300 underline">
        load recent violation
      </button>
    </div>

    <textarea x-model="playText" rows="8"
      class="w-full bg-gray-800 border border-gray-700 rounded p-3 text-sm focus:outline-none focus:border-indigo-500 resize-y"
      placeholder="Paste a Claude response to test against active rules…"></textarea>

    <div class="flex gap-2 items-center">
      <button @click="runEval()" :disabled="evalLoading"
        class="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 px-4 py-1.5 rounded text-sm font-medium">
        <span x-show="!evalLoading">Run evaluate()</span>
        <span x-show="evalLoading">Running…</span>
      </button>
      <button @click="playText='';verdict=null" class="text-gray-500 hover:text-gray-300 text-xs px-2">Clear</button>
    </div>

    <div x-show="verdict" class="border rounded p-4 space-y-2"
      :class="verdict?.action==='accept'?'verdict-accept border-green-800':verdict?.action==='reject'?'verdict-reject border-red-800':'verdict-edit border-yellow-800'">
      <div class="flex items-center gap-2">
        <span class="font-bold text-sm uppercase tracking-wide"
          :class="verdict?.action==='accept'?'text-green-400':verdict?.action==='reject'?'text-red-400':'text-yellow-400'"
          x-text="verdict?.action"></span>
        <span x-show="verdict?.rule" class="text-xs font-mono text-gray-400" x-text="verdict?.rule"></span>
      </div>
      <p x-show="verdict?.guidance" class="text-sm text-gray-300" x-text="verdict?.guidance"></p>
      <div x-show="verdict?.rewritten" class="mt-3 pt-3 border-t border-gray-700">
        <div class="text-xs text-yellow-500 mb-1">Rewritten (EDIT):</div>
        <p class="text-sm bg-gray-900 rounded p-3 whitespace-pre-wrap" x-text="verdict?.rewritten"></p>
      </div>
    </div>

    <!-- Violations list -->
    <div x-show="violations.length>0" class="space-y-1">
      <div class="text-xs text-gray-500 mb-1">Click to load into playground:</div>
      <div class="max-h-56 overflow-y-auto space-y-1">
        <template x-for="(v,i) in violations" :key="i">
          <button @click="playText=v.text_preview;violations=[]"
            class="w-full text-left text-xs bg-gray-800 hover:bg-gray-700 rounded px-3 py-2 flex items-center gap-2">
            <span class="text-red-400 font-mono shrink-0" x-text="v.rule"></span>
            <span class="text-gray-400 truncate" x-text="v.text_preview"></span>
          </button>
        </template>
      </div>
    </div>
  </div>

  <!-- RULES -->
  <div x-show="tab==='rules'" class="space-y-3">
    <div class="flex items-center gap-3">
      <h2 class="text-base font-semibold">Rules</h2>
      <span class="text-xs text-gray-500">Toggle = move active ↔ pending · Edit = save YAML to disk</span>
      <button @click="loadRules()" class="ml-auto text-xs text-indigo-400 hover:text-indigo-300">↻ refresh</button>
    </div>

    <div x-show="rulesLoading" class="text-gray-500 text-sm">Loading…</div>

    <template x-for="rule in rules" :key="rule.path">
      <div class="bg-gray-900 border rounded overflow-hidden"
        :class="rule.status==='active'?'border-green-900':'border-gray-800'">
        <div class="flex items-center gap-3 px-4 py-3">

          <!-- Toggle -->
          <div @click="toggleRule(rule)" class="toggle-track flex-shrink-0"
            :style="rule.status==='active'?'background:#166534':'background:#374151'">
            <div class="toggle-thumb" :style="rule.status==='active'?'left:18px':'left:2px'"></div>
          </div>

          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2">
              <span class="font-mono text-sm" x-text="rule.filename"></span>
              <span class="text-xs px-1.5 py-0.5 rounded font-mono"
                :class="rule.status==='active'?'bg-green-950 text-green-400':'bg-gray-800 text-gray-500'"
                x-text="rule.status"></span>
            </div>
            <div class="text-xs text-gray-500 mt-0.5 font-mono" x-text="rule.rule_names.join(' · ')"></div>
          </div>

          <button @click="rule._exp=!rule._exp; if(rule._exp) rule._edit=rule.content"
            class="text-xs text-gray-500 hover:text-gray-200 px-2 py-1 rounded hover:bg-gray-700"
            x-text="rule._exp?'collapse':'edit'"></button>
        </div>

        <div x-show="rule._exp" class="border-t border-gray-800 px-4 py-3">
          <textarea x-model="rule._edit" rows="18"
            class="w-full bg-gray-800 border border-gray-700 rounded p-2 text-xs focus:outline-none focus:border-indigo-500"></textarea>
          <div class="flex items-center gap-2 mt-2">
            <button @click="saveRule(rule)" :disabled="rule._saving"
              class="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 px-3 py-1 rounded text-xs"
              x-text="rule._saving?'Saving…':'Save'"></button>
            <button @click="rule._exp=false;rule._edit=rule.content"
              class="text-gray-500 hover:text-gray-300 text-xs px-2">Cancel</button>
            <span x-show="rule._savedOk" class="text-green-400 text-xs">✓ Saved</span>
            <span x-show="rule._err" class="text-red-400 text-xs" x-text="rule._err"></span>
            <span class="ml-auto text-xs text-gray-700 font-mono" x-text="rule.path"></span>
          </div>
        </div>
      </div>
    </template>
  </div>

  <!-- EVALS -->
  <div x-show="tab==='evals'" class="space-y-4">
    <div class="flex items-center gap-3">
      <h2 class="text-base font-semibold">Evals</h2>
      <button @click="runEvals()" :disabled="evalsLoading"
        class="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 px-3 py-1.5 rounded text-sm">
        <span x-show="!evalsLoading">Run harness</span>
        <span x-show="evalsLoading">Running…</span>
      </button>
      <span class="text-xs text-gray-500">deterministic rules only · LLM rules skipped</span>
    </div>

    <div x-show="evalsData" class="space-y-4">
      <div class="grid grid-cols-4 gap-3">
        <template x-for="[label, key, color] in [['Correct','correct','text-green-400'],['Mismatches','mismatches','text-red-400'],['Examples','examples','text-gray-300'],['LLM skipped','rules_llm_skipped','text-yellow-400']]" :key="key">
          <div class="bg-gray-800 rounded p-3 text-center">
            <div class="text-2xl font-bold" :class="color" x-text="evalsData?.summary?.[key]??0"></div>
            <div class="text-xs text-gray-500 mt-1" x-text="label"></div>
          </div>
        </template>
      </div>

      <div x-show="evalsData?.mismatches?.length>0" class="space-y-1">
        <div class="text-xs text-red-400 font-medium">Mismatches:</div>
        <template x-for="m in evalsData?.mismatches||[]" :key="m.example+m.rule">
          <div class="bg-red-950 border border-red-900 rounded px-3 py-2 text-xs flex gap-2 items-center">
            <span class="text-red-400 font-mono" x-text="m.type"></span>
            <span class="text-gray-300" x-text="m.rule+' vs '+m.example"></span>
            <span class="ml-auto text-gray-600 font-mono" x-text="m.file"></span>
          </div>
        </template>
      </div>

      <div x-show="(evalsData?.summary?.mismatches??1)===0" class="text-green-400 text-sm">✓ All deterministic evals pass</div>
      <div x-show="evalsData?.error" class="text-red-400 text-sm" x-text="evalsData?.error"></div>
    </div>

    <!-- Add example -->
    <div class="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
      <div class="text-sm font-medium text-gray-300">Add counterexample</div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="text-xs text-gray-500 block mb-1">ID (kebab-case)</label>
          <input x-model="newEx.id" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500" placeholder="my-example">
        </div>
        <div>
          <label class="text-xs text-gray-500 block mb-1">Category</label>
          <select x-model="newEx.category" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs">
            <option>behavior</option>
            <option>voice</option>
          </select>
        </div>
      </div>
      <div>
        <label class="text-xs text-gray-500 block mb-1">Text</label>
        <textarea x-model="newEx.text" rows="4" class="w-full bg-gray-800 border border-gray-700 rounded p-2 text-xs focus:outline-none focus:border-indigo-500" placeholder="The message to classify…"></textarea>
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="text-xs text-gray-500 block mb-1">Expected violations (one per line)</label>
          <textarea x-model="newEx.violations" rows="3" class="w-full bg-gray-800 border border-gray-700 rounded p-2 text-xs focus:outline-none focus:border-indigo-500" placeholder="no-permission-asking-for-doable-work"></textarea>
        </div>
        <div>
          <label class="text-xs text-gray-500 block mb-1">Expected clean (one per line)</label>
          <textarea x-model="newEx.clean" rows="3" class="w-full bg-gray-800 border border-gray-700 rounded p-2 text-xs focus:outline-none focus:border-indigo-500"></textarea>
        </div>
      </div>
      <div>
        <label class="text-xs text-gray-500 block mb-1">Rationale</label>
        <input x-model="newEx.rationale" class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs focus:outline-none focus:border-indigo-500">
      </div>
      <div class="flex gap-2 items-center">
        <button @click="addExample()" :disabled="newEx.saving"
          class="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 px-3 py-1 rounded text-xs"
          x-text="newEx.saving?'Saving…':'Add example'"></button>
        <span x-show="newEx.ok" class="text-green-400 text-xs">✓ Added</span>
        <span x-show="newEx.err" class="text-red-400 text-xs" x-text="newEx.err"></span>
      </div>
    </div>
  </div>

  <!-- DIAGRAM -->
  <div x-show="tab==='diagram'" class="space-y-4">
    <div class="flex items-center gap-3">
      <h2 class="text-base font-semibold">System Diagram</h2>
      <span class="text-xs text-gray-500">MOP pipeline architecture</span>
    </div>

    <div class="bg-gray-900 border border-gray-800 rounded p-4 overflow-x-auto">
      <div class="mermaid" id="mermaid-diagram">""" + _MERMAID + """</div>
    </div>

    <div class="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
      <div class="text-sm font-medium text-gray-300">Feedback</div>
      <p class="text-xs text-gray-500">Describe what to change in the diagram or the MOP library. A coding agent picks this up.</p>
      <textarea x-model="feedback" rows="4"
        class="w-full bg-gray-800 border border-gray-700 rounded p-2 text-sm focus:outline-none focus:border-indigo-500 resize-y"
        placeholder="e.g. 'Add a box for the violation log writer' or 'The retry loop should show the max_retries counter'"></textarea>
      <div class="flex gap-2 items-center">
        <button @click="submitFeedback()" :disabled="fbSaving"
          class="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 px-3 py-1.5 rounded text-sm"
          x-text="fbSaving?'Saving…':'Submit'"></button>
        <span x-show="fbSaved" class="text-green-400 text-sm">✓ Saved to web/diagram-feedback.txt</span>
      </div>
    </div>
  </div>

</main>

<script>
mermaid.initialize({startOnLoad:false,theme:'dark',securityLevel:'loose'});

function mopApp(){
  return {
    tab:'playground',
    playText:'',backend:'haiku',evalLoading:false,verdict:null,violations:[],
    rules:[],rulesLoading:false,
    evalsLoading:false,evalsData:null,
    feedback:'',fbSaving:false,fbSaved:false,
    newEx:{id:'',text:'',violations:'',clean:'',rationale:'',category:'behavior',saving:false,ok:false,err:''},

    async init(){
      await this.loadRules();
      await this.$nextTick();
      mermaid.run({nodes:document.querySelectorAll('.mermaid')});
    },

    async runEval(){
      if(!this.playText.trim())return;
      this.evalLoading=true;this.verdict=null;
      try{
        const r=await fetch('/api/evaluate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:this.playText,backend:this.backend})});
        this.verdict=await r.json();
      }catch(e){this.verdict={action:'error',guidance:String(e)};}
      finally{this.evalLoading=false;}
    },

    async loadViolations(){
      const r=await fetch('/api/violations');
      this.violations=await r.json();
      if(!this.violations.length)alert('No real violations in patchbay log yet.');
    },

    async loadRules(){
      this.rulesLoading=true;
      try{
        const r=await fetch('/api/rules');
        this.rules=(await r.json()).map(f=>({...f,_exp:false,_edit:f.content,_saving:false,_savedOk:false,_err:''}));
      }finally{this.rulesLoading=false;}
    },

    async saveRule(rule){
      rule._saving=true;rule._err='';rule._savedOk=false;
      try{
        const r=await fetch('/api/rules/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:rule.path,content:rule._edit})});
        if(!r.ok){const e=await r.json();rule._err=e.detail||'Save failed';}
        else{rule.content=rule._edit;rule._savedOk=true;setTimeout(()=>rule._savedOk=false,3000);}
      }catch(e){rule._err=String(e);}
      finally{rule._saving=false;}
    },

    async toggleRule(rule){
      try{
        const r=await fetch('/api/rules/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:rule.path})});
        const d=await r.json();rule.status=d.new_status;rule.path=d.new_path;
      }catch(e){alert('Toggle failed: '+e);}
    },

    async runEvals(){
      this.evalsLoading=true;this.evalsData=null;
      try{const r=await fetch('/api/evals');this.evalsData=await r.json();}
      catch(e){this.evalsData={error:String(e)};}
      finally{this.evalsLoading=false;}
    },

    async addExample(){
      if(!this.newEx.id||!this.newEx.text)return;
      this.newEx.saving=true;this.newEx.ok=false;this.newEx.err='';
      const violations=this.newEx.violations.trim().split(/\n+/).filter(Boolean);
      const clean=this.newEx.clean.trim().split(/\n+/).filter(Boolean);
      try{
        const r=await fetch('/api/evals/add-example',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:this.newEx.id,text:this.newEx.text,expected_violations:violations,expected_clean:clean,rationale:this.newEx.rationale,category:this.newEx.category})});
        if(!r.ok){const e=await r.json();this.newEx.err=e.detail||'Failed';}
        else{this.newEx.ok=true;this.newEx.id='';this.newEx.text='';this.newEx.violations='';this.newEx.clean='';this.newEx.rationale='';setTimeout(()=>this.newEx.ok=false,3000);}
      }catch(e){this.newEx.err=String(e);}
      finally{this.newEx.saving=false;}
    },

    async submitFeedback(){
      if(!this.feedback.trim())return;
      this.fbSaving=true;this.fbSaved=false;
      try{
        await fetch('/api/diagram-feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({feedback:this.feedback})});
        this.fbSaved=true;this.feedback='';setTimeout(()=>this.fbSaved=false,5000);
      }finally{this.fbSaving=false;}
    },
  }
}
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def root():
    return HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7731, log_level="info")
