#!/bin/bash
# One-shot launcher: bring up the whisper.cpp server (in the background if it
# isn't already running) and then run the menu-bar app in the foreground.
#
# Quitting the app (menu → Quit, or Ctrl+C) also stops the server *if this
# script started it*. A server you launched yourself is left untouched.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${WHISPER_PORT:-8080}"
LOG="/tmp/cream-whisper.log"
STARTED_SERVER=0
SERVER_PID=""

health() { curl -s -o /dev/null "http://127.0.0.1:$PORT/"; }

cleanup() {
    if [ "$STARTED_SERVER" = "1" ] && [ -n "$SERVER_PID" ]; then
        echo "🛑 stopping whisper server (pid $SERVER_PID)…"
        kill "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if health; then
    echo "✅ whisper server already running on :$PORT"
else
    echo "🚀 starting whisper server (log: $LOG)…"
    bash "$ROOT/scripts/whisper_server.sh" >"$LOG" 2>&1 &
    SERVER_PID=$!
    STARTED_SERVER=1

    printf "⏳ loading model"
    for _ in $(seq 1 60); do
        if health; then echo " — ready."; break; fi
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo ""; echo "❌ server died on startup. Last lines of $LOG:"; tail -20 "$LOG"; exit 1
        fi
        printf "."; sleep 1
    done
    if ! health; then echo ""; echo "❌ server didn't come up in time. See $LOG"; exit 1; fi
fi

echo "🎙  launching Pysar — Caps Lock to dictate, Ctrl+Option+U/R/E to switch language."
# Not exec'd, so the EXIT trap still fires to stop the server we started.
. venv/bin/activate && python -m pysar
