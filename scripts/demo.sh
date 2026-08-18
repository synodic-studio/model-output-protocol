#!/usr/bin/env bash
#
# A guided tour of the gate, and a smoke test of an evaluator.
#
# You do the talking; the screen carries artifacts and a few fragments to
# speak around. Fragments, never sentences — a room given prose reads it
# instead of listening. Every message it puts up is a real one, read out of
# the counterexample corpus in evals/ — harvested from live agent sessions,
# not written for the slide.
#
#   --auto     run start to finish with no interaction, for rehearsal
#   --offline  skip the two beats that call a model
#   --model M  evaluator override, tier or provider/model
#
# Nothing needs typing; one keypress advances a beat. Evaluator settings
# come from scripts/demo.env, which is gitignored; see demo.env.example.
# Without it the model beats are skipped and the rest is unaffected.
#
# Each beat opens with a few fragments on screen and ends in a prompt that
# says what pressing the key means. Those fragments are the speaking prompts;
# they live in the beat functions, not here, so there is one copy of them.
#
#   1. The rule set, then a real message checked with the rewrite turned off.
#      Ends on: exit 2, and the clock — the claim that no model ran, timed.
#
#   2. The same message with the judge on, then a clean one it leaves alone.
#      Ends on: exit 1, then exit 0.
#
#   3. A message that comes back rejected instead. The point of the thing.
#      Worth saying out loud: the reason goes back to the agent, which is the
#      only thing that can actually fix it, and it fixes it by doing the work.
#      Ends on: exit 2, and the guidance the agent gets back.
#
#   4. The flight recorder this run wrote, then the four hosts.
#      Worth saying out loud: mine_audit.py turns real verdicts back into
#      counterexamples — the rejected one came out of that log, the other two
#      out of raw session logs. A rule set earns the right to change what a
#      human sees by being right about real traffic first, in log mode, and
#      that is why it is gating in this repo and nowhere else yet.
#      Ends on: the log, and the repo URL.
#
# Before demoing on a machine for the first time:
#   1. `uv sync` in this checkout, so `uv run mop` starts instantly.
#   2. scripts/demo.env points at an evaluator this machine can reach.
#   3. Run it once with --auto, so the first live run is not the first run.
#
# Deliberately without `set -e`. A failed beat prints and the rest still runs.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RULES="$ROOT/.mop"
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
    # The header comment IS the documentation, so print it rather than a
    # duplicate usage string — extracted by shape, not by line number, so it
    # cannot drift out of sync with the beats above.
    -h|--help) awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  B=$'\033[1m'; D=$'\033[2m'; C=$'\033[36m'; G=$'\033[32m'; Y=$'\033[33m'; E=$'\033[31m'; R=$'\033[0m'
else
  B=""; D=""; C=""; G=""; Y=""; E=""; R=""
fi

beat() { printf '\n%s%s-- %s %s%s\n\n' "$D" "$B" "$1" "$(printf '%.0s-' $(seq 1 $((56 - ${#1}))))" "$R"; }
warn() { printf '%s%s%s\n' "$Y" "$1" "$R"; }

# What the beat is about, in fragments you speak around — never sentences, or
# the room reads instead of listening. Runs before the commands so it frames
# what is about to appear rather than explaining what already scrolled by.
points() { for p in "$@"; do printf '    %s·%s %s\n' "$D" "$R" "$p"; done; echo; }

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

# The repo-relative path, so anyone can open the file and check that the
# message on screen is the message in the corpus.
show_message() {
  printf '  %s%s%s\n' "$D" "evals/counterexamples/real-history/$1.yml" "$R"
  example "$1" | fold -s -w 74 | sed "s/^/    /"
  echo
}

# Print the command, then run it. No folding — this one prints a table, and
# folding a table is how you get a separator line cut in half on a projector.
run() {
  local label="$1"; shift
  printf '%s  $ %s%s\n\n' "$C" "$label" "$R"
  "$@" 2>&1 | sed 's/^/  /'
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

beat_deterministic() {
  beat "1  half the gate needs no model at all"
  points "6 rules, 4 kinds of detector" \
         "regex, length, script — a match is a violation on its own" \
         "no key, no network, no model, and it is on the clock"
  run "mop rules list --rules-dir .mop --compact" \
    mop rules list --rules-dir "$RULES" --compact
  echo
  show_message "cheerleading/you-re-right-i-followed-the"
  # Timed on screen, because "no model in the loop" is a claim until the
  # clock backs it up.
  local started ended
  started=$(date +%s%N)
  run_check "mop check --rule no-cheerleading-phrases --no-rewrite" \
    mop check --rules-dir "$RULES" --rule no-cheerleading-phrases --no-rewrite \
      --file <(example "cheerleading/you-re-right-i-followed-the")
  ended=$(date +%s%N)
  printf '  %s%d ms, no network%s\n' "$D" $(( (ended - started) / 1000000 )) "$R"
}

beat_judge() {
  beat "2  the judge, and what it is not allowed to decide"
  points "one call: judges the model-rules, repairs the rest" \
         "deterministic hits handed over as confirmed, not up for debate" \
         "patterns re-run on the rewrite — a fix only counts if it cleared" \
         "the verdict is derived from what changed, never self-declared"
  run_check "mop check --rules-dir .mop" \
    mop check --rules-dir "$RULES" \
      --file <(example "cheerleading/you-re-right-i-followed-the")
  echo
  show_message "clean/shipped-as-77d3215-382-tests-pas"
  run_check "mop check --rules-dir .mop" \
    mop check --rules-dir "$RULES" \
      --file <(example "clean/shipped-as-77d3215-382-tests-pas")
}

beat_reject() {
  beat "3  what no rewrite can fix"
  points "the wording is fine — the behavior is the violation" \
         "it had the tools, the context and a recommendation, and asked anyway" \
         "rewriting that would launder it" \
         "so the rule rejects: text withheld, reason back to the agent"
  show_message "doable-work/where-to-go-next-say-the-word"
  run_check "mop check --rules-dir .mop" \
    mop check --rules-dir "$RULES" \
      --file <(example "doable-work/where-to-go-next-say-the-word")
}

beat_close() {
  beat "4  the flight recorder, and where it runs"
  points "one JSON line per verdict" \
         "the corpus is mined from real verdicts, not written for the slide" \
         "four hosts, one engine" \
         "gating here only — everywhere else it watches and records"
  # The variable, not the expansion: a raw mktemp path under /var/folders is
  # unreadable on a projector and reads as a scratch file rather than a log.
  printf '%s  $ cat $MOP_AUDIT_LOG/*.jsonl%s\n\n' "$C" "$R"
  uv run --quiet --project "$ROOT" python - "$AUDIT_DIR" <<'PY' | sed 's/^/  /'
import glob, json, sys

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
  printf '  %spatchbay-relay%s   in-process filter on the Telegram send path\n' "$B" "$R"
  printf '  %sHermes%s           plugin on the outbound hook\n' "$B" "$R"
  printf '  %sClaude Code%s      Stop hook — no pre-delivery hook exists, so it\n' "$B" "$R"
  printf '                   observes and records rather than gates\n'
  printf '  %spi%s               extension on message_end, shells to the CLI\n\n' "$B" "$R"
  printf '  %shttps://github.com/synodic-studio/model-output-protocol%s\n' "$D" "$R"
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

printf '\n  %sMOP%s  %sthe gate between an agent and the person reading it%s\n\n' \
  "$B" "$R" "$D" "$R"
points "a system prompt is a request — it holds until it quietly stops" \
       "these rules live outside the prompt, as a gate every message passes" \
       "so the question stops being 'did it behave' and becomes 'what was blocked'"

beat_deterministic; advance "press to switch the judge on"
if [ -z "$OFFLINE" ]; then
  beat_judge;       advance "press for one it will not rewrite"
  beat_reject;      advance "press for the flight recorder"
else
  echo
  warn "  (the two model beats need an evaluator — see --offline)"
  advance "press for the flight recorder"
fi
beat_close
echo
