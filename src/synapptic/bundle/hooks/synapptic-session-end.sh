#!/bin/bash
# synapptic SessionEnd hook — extracts ONLY the closed session, fully detached.

PIDFILE="/tmp/synapptic-hook.pid"
LOGFILE="/tmp/synapptic-session-end.log"

# Read session info from stdin
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
REASON=$(echo "$INPUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reason',''))" 2>/dev/null)
PROJECT=$(echo "$INPUT" | python3 -c "
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

# Only process explicit user exits
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

# Extract this session, merge, synthesize only this project + global
nohup bash -c '
    echo $$ > '"$PIDFILE"'
    echo "[$(date)] Extracting '"$SESSION_ID"' ['"$PROJECT"'] ('"$REASON"')" >> '"$LOGFILE"'
    synapptic extract -s '"$SESSION_ID"' >> '"$LOGFILE"' 2>&1
    synapptic merge >> '"$LOGFILE"' 2>&1
    synapptic synthesize -p '"$PROJECT"' >> '"$LOGFILE"' 2>&1
    echo "[$(date)] Done '"$SESSION_ID"'" >> '"$LOGFILE"'
    rm -f '"$PIDFILE"'
' </dev/null >/dev/null 2>&1 &
disown

exit 0
