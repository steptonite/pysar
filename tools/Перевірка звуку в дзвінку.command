#!/bin/bash
# Двоклік посеред дзвінка — і Термінал сам веде по кроках. Команд набирати не треба.
cd "$HOME/code/pysar"
clear
venv/bin/python tools/call_audio_test.py
echo
echo "Готово. Можеш закривати вікно."
