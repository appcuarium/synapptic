#!/bin/bash
# synapptic SessionEnd hook — fully detached, zero terminal lag.

PIDFILE="/tmp/synapptic-update.pid"
LOGFILE="/tmp/synapptic-session-end.log"

# Skip if already running
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
    exit 0
fi

# Fully detach: redirect ALL file descriptors, nohup, disown
nohup bash -c '
    echo $$ > '"$PIDFILE"'
    echo "[$(date)] Starting synapptic update" >> '"$LOGFILE"'
    synapptic update --model sonnet --limit 5 >> '"$LOGFILE"' 2>&1
    echo "[$(date)] Exit code: $?" >> '"$LOGFILE"'
    rm -f '"$PIDFILE"'
' </dev/null >/dev/null 2>&1 &
disown

exit 0
