#!/bin/bash
# synapptic SessionEnd hook — extracts ONLY the closed session, fully detached.

PIDFILE="${SYNAPPTIC_HOOK_PIDFILE:-/tmp/synapptic-hook.pid}"
LOGFILE="${SYNAPPTIC_HOOK_LOGFILE:-/tmp/synapptic-session-end.log}"

# synapptic's own `claude -p` extraction runs: never learn from them.
if [ -n "$SYNAPPTIC_INTERNAL" ]; then
    exit 0
fi

# The python3 on PATH may lack synapptic (e.g. Homebrew next to a conda install), and then the project
# slug comes out empty and synthesize rebuilds every project. Use the interpreter in synapptic's shebang.
PY=$(head -1 "$(command -v synapptic)" 2>/dev/null | sed -n 's/^#!//p')
[ -x "$PY" ] || PY=python3

# Read session info from stdin
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
REASON=$(echo "$INPUT" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('reason',''))" 2>/dev/null)
PROJECT=$(echo "$INPUT" | "$PY" -c "
import json,sys
from pathlib import Path
d = json.load(sys.stdin)
tp = d.get('transcript_path','')
if tp:
    from synapptic.state import slug_from_project_dir
    print(slug_from_project_dir(Path(tp).parent.name))
else:
    print('')
" 2>/dev/null)

if [ -z "$SESSION_ID" ]; then
    exit 0
fi

# Tell the dashboard the session closed, so it stops showing it live (fire-and-forget: it may not be running)
curl -sf -X POST http://127.0.0.1:5200/api/session-ended \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$SESSION_ID\"}" \
    --max-time 2 >/dev/null 2>&1 || true

# Only process explicit user exits for extraction pipeline
if [ "$REASON" != "prompt_input_exit" ] && [ "$REASON" != "clear" ] && [ "$REASON" != "logout" ]; then
    exit 0
fi

# Skip if another hook is still running (PID file check, not pgrep)
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[$(date)] Skipping $SESSION_ID ($REASON) - previous hook still running (PID $OLD_PID)" >> "$LOGFILE"
        exit 0
    fi
    rm -f "$PIDFILE"
fi

# Extract this session, merge, synthesize only this project + global, then write it to that project's memory
nohup bash -c '
    echo $$ > '"$PIDFILE"'
    echo "[$(date)] Extracting '"$SESSION_ID"' ['"$PROJECT"'] ('"$REASON"')" >> '"$LOGFILE"'
    synapptic checkpoint -s '"$SESSION_ID"' --flush --synthesize-after -1 >> '"$LOGFILE"' 2>&1
    synapptic merge >> '"$LOGFILE"' 2>&1
    synapptic synthesize -p '"$PROJECT"' >> '"$LOGFILE"' 2>&1
    synapptic integrate -p '"$PROJECT"' >> '"$LOGFILE"' 2>&1
    echo "[$(date)] Done '"$SESSION_ID"'" >> '"$LOGFILE"'
    rm -f '"$PIDFILE"'
' </dev/null >/dev/null 2>&1 &
disown

exit 0
