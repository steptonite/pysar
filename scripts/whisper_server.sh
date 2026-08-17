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

# GPU (Metal) is the default and is what makes dictation fast. Some machines
# can't have it: on one M4 the Metal backend died at model load with
# "failed to allocate buffer" → SIGSEGV, so the app launched and vanished with
# no window and no message (tester, 17.08.2026). start.sh detects that crash and
# re-launches us with PYSAR_NO_GPU=1; the marker file makes the choice stick
# across restarts. Flash attention is a Metal-side optimisation, so it goes with it.
GPU_ARGS=(--flash-attn)
NO_GPU_MARKER="${PYSAR_NO_GPU_MARKER:-$HOME/Library/Application Support/Pysar/no-gpu}"
if [ "${PYSAR_NO_GPU:-0}" = "1" ] || [ -f "$NO_GPU_MARKER" ]; then
    echo "⚠️  Metal disabled (CPU decoding) — remove \"$NO_GPU_MARKER\" to try the GPU again."
    GPU_ARGS=(--no-gpu)
fi

exec "$WHISPER_SERVER" \
    --model "$WHISPER_MODEL" \
    --host 127.0.0.1 \
    --port "$WHISPER_PORT" \
    --language "$WHISPER_LANG" \
    "${GPU_ARGS[@]}" \
    --split-on-word \
    --suppress-nst \
    "${VAD_ARGS[@]}"
