#!/usr/bin/env bash
# Spike C: claude --print --resume validation.
# Run from a separate terminal (NOT inside an existing Claude Code session).
#
# Output: writes per-step logs and timings to $CLAUDE_JOB_DIR (or /tmp fallback).

set -u

OUTDIR="${1:-/tmp/spike-c-$(date +%s)}"
mkdir -p "$OUTDIR"
cd "$OUTDIR"

echo "=== Spike C output dir: $OUTDIR ==="
echo

# Generate a deterministic-but-unique UUID for this run so we can pre-seed --session-id.
SESSION_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
echo "Session ID: $SESSION_ID" | tee session-id.txt
echo

run_step() {
    local label="$1"; shift
    local out="step-${label}.json"
    local err="step-${label}.stderr"
    local timing="step-${label}.time"

    echo "--- $label ---"
    /usr/bin/env time -f '%e' -o "$timing" "$@" >"$out" 2>"$err"
    local rc=$?
    local elapsed=$(cat "$timing")
    echo "exit=$rc wall=${elapsed}s"
    if [ -s "$err" ]; then
        echo "stderr:"
        sed 's/^/  /' "$err"
    fi
    echo "stdout head:"
    head -c 400 "$out" | sed 's/^/  /'
    echo
    echo
}

# Step 1: cold start with a memorable fact, pinning our chosen session id.
run_step 01-prime \
    claude --print \
           --session-id "$SESSION_ID" \
           --output-format json \
           'Hello. Please remember the number 42 for our next exchange. Acknowledge briefly.'

# Step 2: resume the same session and ask for the number back.
run_step 02-resume \
    claude --print \
           --resume "$SESSION_ID" \
           --output-format json \
           'What number did I just ask you to remember? Answer with just the digits.'

# Step 3: warm latency burst — 10 quick resumes.
echo "--- 03-burst: 10 back-to-back resumes ---"
{
    for i in $(seq 1 10); do
        t_start=$(date +%s.%N)
        out=$(claude --print --resume "$SESSION_ID" --output-format json \
              "Burst $i. Reply with the single word OK." 2>&1)
        rc=$?
        t_end=$(date +%s.%N)
        elapsed=$(awk -v a="$t_start" -v b="$t_end" 'BEGIN{printf "%.2f", b-a}')
        echo "iter=$i rc=$rc wall=${elapsed}s"
        echo "$out" >> burst.jsonl
        echo "" >> burst.jsonl
    done
} | tee burst.log
echo

# Step 4: stream-json output shape.
run_step 04-stream-shape \
    claude --print \
           --resume "$SESSION_ID" \
           --output-format stream-json \
           --include-partial-messages \
           'Stream test. Reply with three short sentences.'

# Step 5: unknown session id — does it create or error?
UNKNOWN_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
echo "Unknown session id (never used): $UNKNOWN_ID" | tee unknown-id.txt
run_step 05-unknown-resume \
    claude --print \
           --resume "$UNKNOWN_ID" \
           --output-format json \
           'If you can hear me, reply with the word HELLO and nothing else.'

# Step 6: where does the session live on disk?
echo "--- 06-session-files ---"
find ~/.claude -maxdepth 6 -name "${SESSION_ID}*" 2>/dev/null | tee session-files.txt
echo

echo "=== Done. Artifacts in $OUTDIR ==="
ls -la "$OUTDIR"
