#!/bin/bash
# Launch the whisper.cpp server. Alternative to `make whisper` when you want
# to run it from a launchd plist or systemd unit. Defaults match the Makefile —
# after `make setup` everything lives in vendor/whisper.cpp/.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

WHISPER_DIR="${WHISPER_DIR:-$ROOT/vendor/whisper.cpp}"
WHISPER_SERVER="${WHISPER_SERVER:-$WHISPER_DIR/build/bin/whisper-server}"
WHISPER_MODEL="${WHISPER_MODEL:-$WHISPER_DIR/models/ggml-large-v3-turbo-q5_0.bin}"
WHISPER_PORT="${WHISPER_PORT:-8080}"
WHISPER_LANG="${WHISPER_LANG:-en}"

exec "$WHISPER_SERVER" \
    --model "$WHISPER_MODEL" \
    --host 127.0.0.1 \
    --port "$WHISPER_PORT" \
    --language "$WHISPER_LANG"
