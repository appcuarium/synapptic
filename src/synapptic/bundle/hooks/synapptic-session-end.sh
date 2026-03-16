#!/bin/bash
# synapptic SessionEnd hook — extracts ONLY the closed session, fully detached.

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

# Skip if another extraction is running
if pgrep -f "synapptic extract" >/dev/null 2>&1; then
    echo "[$(date)] Skipping $SESSION_ID ($REASON) - another extraction in progress" >> "$LOGFILE"
    exit 0
fi

# Extract this session, merge, synthesize only this project + global
nohup bash -c '
    echo "[$(date)] Extracting '"$SESSION_ID"' ['"$PROJECT"'] ('"$REASON"')" >> '"$LOGFILE"'
    synapptic extract -s '"$SESSION_ID"' >> '"$LOGFILE"' 2>&1
    synapptic merge >> '"$LOGFILE"' 2>&1
    synapptic synthesize -p '"$PROJECT"' >> '"$LOGFILE"' 2>&1
    echo "[$(date)] Done '"$SESSION_ID"'" >> '"$LOGFILE"'
' </dev/null >/dev/null 2>&1 &
disown

exit 0
