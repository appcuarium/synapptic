#!/bin/bash
# synapptic Stop / PreCompact hook: extracts what this session added since its last checkpoint, fully detached.
# Stop: only once enough new conversation exists (the threshold lives in `synapptic checkpoint`).
# PreCompact: everything so far (--flush), before the context is summarised.

LOGFILE="${SYNAPPTIC_HOOK_LOGFILE:-/tmp/synapptic-checkpoint.log}"

# synapptic's own `claude -p` extraction runs: never checkpoint them (each would start another extraction).
if [ -n "$SYNAPPTIC_INTERNAL" ]; then
    exit 0
fi

# The python3 on PATH may lack synapptic (e.g. Homebrew next to a conda install). Use the interpreter in synapptic's shebang.
PY=$(head -1 "$(command -v synapptic)" 2>/dev/null | sed -n 's/^#!//p')
[ -x "$PY" ] || PY=python3

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
EVENT=$(echo "$INPUT" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null)

if [ -z "$SESSION_ID" ]; then
    exit 0
fi

FLAG=""
if [ "$EVENT" = "PreCompact" ]; then
    FLAG="--flush"
fi

nohup bash -c '
    echo "[$(date)] '"$EVENT"' '"$SESSION_ID"'" >> '"$LOGFILE"'
    synapptic checkpoint -s '"$SESSION_ID"' '"$FLAG"' >> '"$LOGFILE"' 2>&1
' </dev/null >/dev/null 2>&1 &
disown

exit 0
