#!/bin/bash
# synapptic SessionEnd hook — fully detached, zero terminal lag.

PIDFILE="/tmp/synapptic-update.pid"
LOGFILE="/tmp/synapptic-session-end.log"

# Skip if ANY synapptic update is already running (hook or manual)
if pgrep -f "synapptic update" >/dev/null 2>&1; then
    exit 0
fi

# Fully detach: redirect ALL file descriptors, nohup, disown
nohup bash -c '
    echo $$ > '"$PIDFILE"'
    echo "[$(date)] Starting synapptic update" >> '"$LOGFILE"'
    synapptic update --limit 5 >> '"$LOGFILE"' 2>&1
    echo "[$(date)] Exit code: $?" >> '"$LOGFILE"'
    rm -f '"$PIDFILE"'
' </dev/null >/dev/null 2>&1 &
disown

exit 0
