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
WHISPER_VAD_MODEL="${WHISPER_VAD_MODEL:-$WHISPER_DIR/models/ggml-silero-v5.1.2.bin}"

# VAD (voice-activity detection) drops silence before it reaches the model, which
# kills the "subtitle credits" hallucinations Whisper emits on quiet/empty audio.
# Only enabled when the VAD model is present.
VAD_ARGS=()
if [ -f "$WHISPER_VAD_MODEL" ]; then
    VAD_ARGS=(--vad --vad-model "$WHISPER_VAD_MODEL")
fi

# Flash attention: faster decode and lower memory on Apple-Silicon Metal — the
# single biggest free speed win, and the memory drop matters most on 8 GB.
# It's a context-init flag (set once at model load), not a per-request param.
exec "$WHISPER_SERVER" \
    --model "$WHISPER_MODEL" \
    --host 127.0.0.1 \
    --port "$WHISPER_PORT" \
    --language "$WHISPER_LANG" \
    --flash-attn \
    --split-on-word \
    --suppress-nst \
    "${VAD_ARGS[@]}"
