#!/bin/bash
# Двоклік: замір аудіо-шляхів зустрічі. Нічого не змінює, лише міряє.
cd "$(dirname "$0")/.." || exit 1
R="$HOME/Desktop/Писар-замір-аудіо-$(date +%Y-%m-%d_%H-%M).txt"
{ venv/bin/python tools/audio_matrix_test.py; } 2>&1 | tee "$R"
echo
echo "звіт: $R"
echo "── вікно можна закривати ──"
