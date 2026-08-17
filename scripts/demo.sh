#!/usr/bin/env bash
#
# A guided tour of the gate, and a smoke test of an evaluator.
#
# Every message on screen is a real one, read out of the counterexample
# corpus in evals/ — harvested from live agent sessions, not written for
# the slide. Five beats: what fires without a model, what one model call
# repairs, what no rewrite can fix, what passes untouched, and the
# flight recorder the run just wrote.
#
#   --auto     run start to finish with no interaction, for rehearsal
#   --offline  skip the three beats that call a model
#   --model M  evaluator override, tier or provider/model
#
# Nothing needs typing; one keypress advances a beat. Evaluator settings
# come from scripts/demo.env, which is gitignored; see demo.env.example.
# Without it the model beats are skipped and the rest is unaffected.
#
# Before demoing on a machine for the first time:
#   1. `uv sync` in this checkout, so `uv run mop` starts instantly.
#   2. scripts/demo.env points at an evaluator this machine can reach.
#   3. Run it once with --auto, so the first live run is not the first run.
#
# Deliberately without `set -e`. A failed beat prints and the rest still runs.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RULES="$ROOT/scripts/demo-rules"
CORPUS="$ROOT/evals/counterexamples/real-history"
AUTO=""
OFFLINE=""
MODEL=""

[ -f "$ROOT/scripts/demo.env" ] && . "$ROOT/scripts/demo.env"

while [ $# -gt 0 ]; do
  case "$1" in
    --auto) AUTO="1"; shift ;;
    --offline) OFFLINE="1"; shift ;;
    --model) MODEL="$2"; shift 2 ;;
    -h|--help) sed -n '3,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  B=$'\033[1m'; D=$'\033[2m'; C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; E=$'\033[31m'; R=$'\033[0m'
else
  B=""; D=""; C=""; G=""; Y=""; E=""; R=""
fi

beat() { printf '\n%s%s-- %s %s%s\n\n' "$D" "$B" "$1" "$(printf '%.0s-' $(seq 1 $((56 - ${#1}))))" "$R"; }
say()  { printf '%s%s%s\n' "$D" "$1" "$R"; }
warn() { printf '%s%s%s\n' "$Y" "$1" "$R"; }

# Every prompt says what pressing the key means, so there is never a question
# of whether it advances the slide or ends your turn. Keys come from fd 3, the
# terminal opened at startup, so a redirected stdin does not swallow them.
advance() {
  [ -n "$AUTO" ] && return 0
  printf '\n%s   [ %s ]%s' "$D" "${1:-press to continue}" "$R"
  if ! read -n 1 -s -r _ <&3; then
    printf '\n'
    warn "  Lost the terminal, so nothing is waiting for you any more."
  fi
  printf '\r%*s\r' $((${#1} + 14)) ""
}

mop() { MOP_EVALUATOR_MODEL="${MODEL:-$MOP_EVALUATOR_MODEL}" uv run --quiet --project "$ROOT" mop "$@"; }

# The message under review, straight out of the corpus file — no editing
# between the YAML and the screen.
example() {
  uv run --quiet --project "$ROOT" python -c \
    "import yaml,sys;sys.stdout.write(yaml.safe_load(open(sys.argv[1]))['text'])" \
    "$CORPUS/$1.yml"
}

show_message() {
  printf '  %sthe message%s   %s%s%s\n' "$B" "$R" "$D" "$1" "$R"
  example "$1" | fold -s -w 74 | sed "s/^/    /"
  echo
}

# Print the command, then run it.
run() {
  local label="$1"; shift
  printf '%s  $ %s%s\n\n' "$C" "$label" "$R"
  "$@" 2>&1 | fold -s -w 76 | sed 's/^/  /'
}

# Same, plus the exit code — that is how a host branches on a verdict, so it
# belongs on screen next to one.
run_check() {
  local label="$1"; shift
  printf '%s  $ %s%s\n\n' "$C" "$label" "$R"
  "$@" 2>&1 | fold -s -w 76 | sed 's/^/  /'
  local code=${PIPESTATUS[0]}
  case "$code" in
    0) printf '\n  %sexit 0 — accepted%s\n' "$G" "$R" ;;
    1) printf '\n  %sexit 1 — rewritten%s\n' "$Y" "$R" ;;
    2) printf '\n  %sexit 2 — rejected%s\n' "$E" "$R" ;;
    *) printf '\n  %sexit %d%s\n' "$E" "$code" "$R" ;;
  esac
}

# ---------------------------------------------------------------------------

beat_rules() {
  beat "the rule set"
  run "mop rules list --rules-dir scripts/demo-rules --compact" \
    mop rules list --rules-dir "$RULES" --compact
  echo
  say "  Four detectors. regex, length and script are deterministic — a match"
  say "  is a violation on its own. llm rules are judged by a model."
  say "  A [reject] rule is one no rewrite can satisfy."
}

beat_deterministic() {
  beat "half the gate has no model in it"
  show_message "cheerleading/you-re-right-i-followed-the"
  run_check "mop check --rule no-cheerleading-phrases --no-rewrite" \
    mop check --rules-dir "$RULES" --rule no-cheerleading-phrases --no-rewrite \
      --file <(example "cheerleading/you-re-right-i-followed-the")
  echo
  say "  No API key, no network, no model. This is the mode CI runs in."
}

beat_rewrite() {
  beat "one model call, and the rewrite is re-checked"
  say "  Same message, with the judge switched on."
  echo
  run_check "mop check --rules-dir scripts/demo-rules" \
    mop check --rules-dir "$RULES" \
      --file <(example "cheerleading/you-re-right-i-followed-the")
  echo
  say "  One call judges the llm rules AND repairs the deterministic hits,"
  say "  which it is handed as confirmed violations it cannot argue with."
  say "  MOP then re-runs the patterns against the rewrite: a fix only counts"
  say "  if it actually cleared. The verdict is derived from what changed —"
  say "  the model never gets to declare its own message accepted."
}

beat_reject() {
  beat "what no rewrite can fix"
  show_message "doable-work/where-to-go-next-say-the-word"
  run_check "mop check --rules-dir scripts/demo-rules" \
    mop check --rules-dir "$RULES" \
      --file <(example "doable-work/where-to-go-next-say-the-word")
  echo
  say "  Nothing is wrong with the wording. The agent had the tools, the"
  say "  context, and a recommendation, and handed the decision back anyway."
  say "  Rewriting that would launder it. So the rule carries disposition"
  say "  reject: the text is withheld and the reason goes back to the agent,"
  say "  which is the one thing that can actually fix it — by doing the work."
}

beat_clean() {
  beat "and a message it leaves alone"
  show_message "clean/shipped-as-77d3215-382-tests-pas"
  run_check "mop check --rules-dir scripts/demo-rules" \
    mop check --rules-dir "$RULES" \
      --file <(example "clean/shipped-as-77d3215-382-tests-pas")
  echo
  say "  The corpus carries clean messages as ballast, for exactly this."
}

beat_audit() {
  beat "the flight recorder"
  say "  MOP_AUDIT_LOG was set to a scratch dir for this run. Every verdict"
  say "  above landed in it as one JSON line."
  echo
  printf '%s  $ cat %s/*.jsonl%s\n\n' "$C" "${AUDIT_DIR/#$HOME/\~}" "$R"
  uv run --quiet --project "$ROOT" python - "$AUDIT_DIR" <<'PY' | sed 's/^/  /'
import glob, json, sys, textwrap

for path in sorted(glob.glob(f"{sys.argv[1]}/*.jsonl")):
    for line in open(path):
        if not line.strip():
            continue
        rec = json.loads(line)
        first = " ".join(rec["original"].split())[:58]
        print(f'{rec["verdict"]:<10s} {rec["ts"][11:19]}  {first}...')
        for name in rec.get("unresolved") or []:
            print(f'{"":<10s} {"":<8s}  -> {name}')
PY
  echo
  say "  That is the corpus feeding itself: scripts/mine_audit.py turns real"
  say "  verdicts back into counterexamples, which is where the messages in"
  say "  this demo came from."
}

beat_close() {
  beat "where it actually runs"
  printf '  %sClaude Code%s      Stop hook, generated from the same rules\n' "$B" "$R"
  printf '                   %sscripts/gen_cc_hook.py --rules-dir ...%s\n' "$D" "$R"
  printf '  %spatchbay-relay%s   filter on the Telegram send path\n' "$B" "$R"
  printf '  %sHermes%s           plugin on the outbound hook\n' "$B" "$R"
  printf '  %spi%s               extension, integrations/pi/mop.ts\n\n' "$B" "$R"
  say "  One engine, one rule file, four hosts. Each shifts between log mode"
  say "  (judge and record, never alter) and enforce mode."
  echo
  printf '  %sgithub.com/synodic-studio/model-output-protocol%s\n' "$D" "$R"
}

# ---------------------------------------------------------------------------

if [ -z "$AUTO" ]; then
  # Tested in a subshell first: a failed `exec` redirect prints its own error
  # before any 2>/dev/null on the same line can take effect.
  if (exec 3</dev/tty) 2>/dev/null; then
    exec 3</dev/tty
  else
    warn "No terminal to read keypresses from, so every beat would run at once."
    warn "Run it from a terminal, or use --auto to drive the whole thing."
    exit 1
  fi
fi

if [ ! -d "$RULES" ]; then
  warn "No rule set at $RULES."
  exit 1
fi

# A model beat with no reachable evaluator prints a stack of litellm errors
# where the verdict should be. Decide once, up front, and say so. Runs before
# the audit log is armed, so the probe stays out of the flight recorder.
if [ -z "$OFFLINE" ]; then
  if ! echo "ping" | mop check --rules-dir "$RULES" --rule no-trailing-summary \
       >/dev/null 2>&1; then
    warn "No evaluator reachable — running the deterministic beats only."
    warn "Set MOP_EVALUATOR_MODEL and its key in scripts/demo.env to fix that."
    OFFLINE="1"
  fi
fi

# The audit beat reads only what this run wrote, so it never shows a stale
# verdict from a previous rehearsal or from a host's own log.
AUDIT_DIR=$(mktemp -d)
export MOP_AUDIT_LOG="$AUDIT_DIR"
trap 'rm -rf "$AUDIT_DIR"' EXIT

printf '\n  %sMOP%s  %sthe gate between an agent and the person reading it%s\n' \
  "$B" "$R" "$D" "$R"

beat_rules;         advance "press for the first message"
beat_deterministic; advance "press to switch the judge on"
if [ -z "$OFFLINE" ]; then
  beat_rewrite;     advance "press for one it will not rewrite"
  beat_reject;      advance "press for a clean one"
  beat_clean;       advance "press for the audit log"
else
  echo
  warn "  (the three model beats need an evaluator — see --offline)"
  advance "press for the audit log"
fi
beat_audit;         advance "press to finish"
beat_close
echo
