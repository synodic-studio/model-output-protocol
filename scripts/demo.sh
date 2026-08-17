#!/usr/bin/env bash
#
# A guided tour of the gate, and a smoke test of an evaluator.
#
# The screen shows artifacts, not narration. You do the talking. Every
# message it puts up is a real one, read out of the counterexample corpus
# in evals/ — harvested from live agent sessions, not written for the slide.
#
#   --auto     run start to finish with no interaction, for rehearsal
#   --offline  skip the two beats that call a model
#   --model M  evaluator override, tier or provider/model
#
# Nothing needs typing; one keypress advances a beat. Evaluator settings
# come from scripts/demo.env, which is gitignored; see demo.env.example.
# Without it the model beats are skipped and the rest is unaffected.
#
# Four beats, each ending in a prompt that says what pressing the key means:
#
#   1. The rule set, then a real message checked with the rewrite turned off.
#      "Six rules, four detectors. regex, length and script are
#       deterministic — a match is a violation on its own, and no model gets
#       a vote. That's what just ran: no API key, no network, no model, two
#       tenths of a second. Half this gate runs in CI."
#      Ends on: exit 2, and the clock.
#
#   2. The same message with the judge on, then a clean one.
#      "One call judges the model-rules and repairs the deterministic hits,
#       which it's handed as confirmed violations it can't argue with. Then
#       MOP re-runs the patterns against the rewrite — a fix only counts if
#       it cleared. The verdict is derived from what changed; the model
#       never gets to declare its own message accepted. And it's not a
#       rubber stamp in either direction — that second one it left alone."
#      Ends on: exit 1, then exit 0.
#
#   3. A message that comes back rejected instead. The point of the thing.
#      "Nothing's wrong with the wording. It had the tools, the context and
#       a recommendation, and handed the decision back anyway. Rewriting
#       that would launder it. So the rule carries disposition reject: the
#       text is withheld and the reason goes back to the agent — the only
#       thing that can actually fix it, by doing the work."
#      Ends on: exit 2, and the guidance the agent gets back.
#
#   4. The flight recorder this run wrote, then the four hosts.
#      "Every verdict lands as one JSON line. mine_audit.py turns real
#       verdicts back into counterexamples — the one you just watched get
#       rejected came out of that log; the other two out of raw session
#       logs. Four hosts, one engine; three of them writing records on this
#       machine today. None of them are acting: they run a deterministic-only
#       set in log mode, which costs nothing and never touches a message.
#       Everything you just watched it do — the rewrite, the rejection —
#       is switched on inside this repo and nowhere else. A rule set earns
#       the right to change what a human sees by being right about real
#       traffic first, and that log is how it earns it."
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
  beat "the rule set, and the half with no model in it"
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
  beat "the same message, judge on"
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
  beat "what no rewrite can fix"
  show_message "doable-work/where-to-go-next-say-the-word"
  run_check "mop check --rules-dir .mop" \
    mop check --rules-dir "$RULES" \
      --file <(example "doable-work/where-to-go-next-say-the-word")
}

beat_close() {
  beat "the flight recorder, and where it runs"
  printf '%s  $ cat %s/*.jsonl%s\n\n' "$C" "${AUDIT_DIR/#$HOME/\~}" "$R"
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

printf '\n  %sMOP%s  %sthe gate between an agent and the person reading it%s\n' \
  "$B" "$R" "$D" "$R"

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
