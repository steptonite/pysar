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
LOG="/tmp/pysar-whisper.log"
STARTED_SERVER=0
SERVER_PID=""

health() { curl -s -o /dev/null "http://127.0.0.1:$PORT/"; }

cleanup() {
    if [ "$STARTED_SERVER" = "1" ] && [ -n "$SERVER_PID" ]; then
        echo "🛑 stopping whisper server (pid $SERVER_PID)…"
        # SIGKILL, not SIGTERM: whisper-server's own signal handler calls exit(),
        # which aborts inside the Metal teardown (ggml_metal_rsets_free → ggml_abort
        # → SIGABRT) and litters DiagnosticReports with a crash report on every
        # quit. A hard kill skips the handler — the server is stateless, so there's
        # nothing to flush — and dies cleanly with no crash report.
        kill -9 "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# Launch the server and wait for it to answer. Returns 1 if it died or never
# came up; the caller decides whether that's fatal or worth a CPU retry.
launch_server() {
    # A previous attempt that timed out (rather than died) still holds the port —
    # clear it before we bind again.
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -9 "$SERVER_PID" 2>/dev/null || true
        sleep 1
    fi
    # nohup so an incidental SIGHUP (terminal/login session going away on sleep or
    # logout) doesn't reach the server and tear it down mid-dictation. We still
    # stop it deliberately via the saved PID in cleanup().
    nohup bash "$ROOT/scripts/whisper_server.sh" >"$LOG" 2>&1 &
    SERVER_PID=$!
    STARTED_SERVER=1

    printf "⏳ loading model"
    for _ in $(seq 1 60); do
        if health; then echo " — ready."; return 0; fi
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo ""; return 1
        fi
        printf "."; sleep 1
    done
    echo ""
    return 1
}

if health; then
    echo "✅ whisper server already running on :$PORT"
else
    echo "🚀 starting whisper server (log: $LOG)…"
    if ! launch_server; then
        # Metal can fail at model load on some Macs ("failed to allocate buffer",
        # then SIGSEGV). Before this retry the app just disappeared at launch with
        # nothing on screen — the user had no way to know why (tester, 17.08.2026).
        # CPU decoding is slower but always works, so try it once and remember.
        if grep -qiE "failed to allocate|ggml_metal|Segmentation fault|SIGSEGV|ggml_abort" "$LOG"; then
            echo "⚠️  the GPU (Metal) backend crashed on this Mac — retrying on the CPU…"
            MARKER="$HOME/Library/Application Support/Pysar/no-gpu"
            if PYSAR_NO_GPU=1 launch_server; then
                mkdir -p "$(dirname "$MARKER")"
                printf 'Metal crashed at model load on %s — CPU decoding.\nDelete this file to try the GPU again.\n' \
                    "$(date '+%Y-%m-%d %H:%M')" > "$MARKER"
                echo "ℹ️  saved the CPU-only choice ($MARKER). Delete that file to retry the GPU."
            else
                echo "❌ server died on startup even on the CPU. Last lines of $LOG:"; tail -20 "$LOG"; exit 1
            fi
        else
            echo "❌ server didn't come up. Last lines of $LOG:"; tail -20 "$LOG"; exit 1
        fi
    fi
fi

echo "🎙  launching Pysar — Caps Lock to dictate, Ctrl+Option+U/R/E to switch language."
# Not exec'd, so the EXIT trap still fires to stop the server we started.
# When launched from the .app, PYSAR_PYTHON points at the bundled python copy
# (so NSBundle.mainBundle resolves to our .app → Dock shows "Pysar"
# + our icon, not "Python") and PYSAR_SITE feeds it the venv's packages. In dev
# (`make up`) neither is set → use the venv directly.
if [ -n "$PYSAR_PYTHON" ]; then
    "$PYSAR_PYTHON" "$ROOT/scripts/_app_main.py"
else
    . venv/bin/activate && python -m pysar
fi
