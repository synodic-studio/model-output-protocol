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
# message on screen is the message in the corpus. Given a rule name, the spans
# that rule's patterns match are highlighted — using those same patterns, read
# out of the same file the check reads, so this cannot show a match the rule
# would not make.
show_message() {
  printf '  %sthe message under review%s  %s%s%s\n' \
    "$B" "$R" "$D" "evals/counterexamples/real-history/$1.yml" "$R"
  uv run --quiet --project "$ROOT" python - \
    "$CORPUS/$1.yml" "$RULES/rules.yml" "${2:-}" "$([ -t 1 ] && echo 1)" <<'PY'
import re, sys, textwrap, yaml

msg_path, rules_path, rule_name, color = sys.argv[1:5]
text = yaml.safe_load(open(msg_path))["text"].strip()

spans = []
if rule_name and color:
    for rule in yaml.safe_load(open(rules_path))["rules"]:
        if rule["name"] == rule_name:
            for pat in rule.get("parameters", {}).get("patterns", []):
                spans += [m.span() for m in re.finditer(pat, text) if m.end() > m.start()]

merged: list[list[int]] = []
for start, end in sorted(spans):
    if merged and start <= merged[-1][1]:
        merged[-1][1] = max(merged[-1][1], end)
    else:
        merged.append([start, end])

# Markers rather than escapes, because textwrap counts an ANSI sequence as
# visible width and would fold the line short by however many bytes it carries.
OPEN, CLOSE = "\x00", "\x01"
for start, end in reversed(merged):
    text = text[:start] + OPEN + text[start:end] + CLOSE + text[end:]

for line in textwrap.wrap(text, 74) or [""]:
    print("    " + line.replace(OPEN, "\033[7m").replace(CLOSE, "\033[0m"))
PY
  echo
}

# One rule as it is actually written. The table says six rules exist; this
# says a rule is a file you edit, which is the claim that matters. Extracted
# by name rather than by line range, so it cannot drift out of sync.
show_rule() {
  printf '  %sthe rule that fires%s  %s%s%s\n\n' "$B" "$R" "$D" ".mop/rules.yml" "$R"
  awk -v n="  - name: $1" '
    $0==n{f=1}
    f&&/^[[:space:]]*$/{exit}
    f&&/^[[:space:]]*guidance:/{g=1; print "    " $0; next}
    f&&g&&/^[[:space:]]*[a-z_]+:/{g=0}
    f&&g{ if (!shown) { print "      " $0; shown=1 } else if (!dots) { print "      ..."; dots=1 }; next }
    f&&/^[[:space:]]*- "/{ pats++; if (pats<=2) print "    " $0; else if (pats==3) print "      ... and more"; next }
    f{print "    " $0}' "$RULES/rules.yml"
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
    # Exit 2 arrives two unrelated ways, and beat 3 needs the word "rejected"
    # to mean only its way. With --no-rewrite there was never a repair path,
    # so a violation lands here having never been offered one.
    2) case "$label" in
         *--no-rewrite*) printf '\n  %sexit 2 — violation, no repair attempted%s\n' "$E" "$R" ;;
         *)              printf '\n  %sexit 2 — rejected%s\n' "$E" "$R" ;;
       esac ;;
    *) printf '\n  %sexit %d%s\n' "$E" "$code" "$R" ;;
  esac
}

# ---------------------------------------------------------------------------

beat_deterministic() {
  beat "1  half the gate needs no model at all"
  points "6 rules — half decide on their own, half ask a model" \
         "a pattern match is a violation, and no model gets a vote" \
         "--no-rewrite is lint mode: verdict only, never a repair" \
         "no key, no network, no model, and it is on the clock"
  run "mop rules list --rules-dir .mop --compact" \
    mop rules list --rules-dir "$RULES" --compact
  echo
  show_rule "no-cheerleading-phrases"
  show_message "cheerleading/you-re-right-i-followed-the" "no-cheerleading-phrases"
  # Timed on screen, because "no model in the loop" is a claim until the
  # clock backs it up.
  local started ended
  started=$(date +%s%N)
  run_check "mop check --rule no-cheerleading-phrases --no-rewrite --file <(the message above)" \
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
  run_check "mop check --rules-dir .mop --file <(the message above)" \
    mop check --rules-dir "$RULES" \
      --file <(example "cheerleading/you-re-right-i-followed-the")
  echo
  show_message "clean/shipped-as-77d3215-382-tests-pas"
  run_check "mop check --rules-dir .mop --file <(the message above)" \
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
  run_check "mop check --rules-dir .mop --file <(the message above)" \
    mop check --rules-dir "$RULES" \
      --file <(example "doable-work/where-to-go-next-say-the-word")
}

beat_close() {
  beat "4  the flight recorder, and where it runs"
  points "one JSON line per verdict" \
         "the corpus is mined from real verdicts, not written for the slide" \
         "three hosts writing on this machine, one engine" \
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
  # Where each host's seam sits decides what it can do, so say which side of
  # delivery it is on rather than only naming the hook.
  printf '  %spatchbay-relay%s   filter in the Telegram send path, before delivery\n' "$B" "$R"
  printf '  %sHermes%s           plugin on transform_llm_output, before delivery\n' "$B" "$R"
  printf '  %sClaude Code%s      Stop hook — no channel to substitute text, so it\n' "$B" "$R"
  printf '                   blocks the turn and the agent revises; reject rules only\n'
  printf '  %spi%s               extension shipped, not installed here; message_end\n' "$B" "$R"
  printf '                   is observe-only, so it would record too\n\n'
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
points "prompt rules drift, and nothing tells you when they did" \
       "these live outside the prompt, as a gate every message passes through"

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
