#!/bin/bash
# Print the path of a CPython interpreter new enough to build Pysar's venv,
# or print nothing and exit 1.
#
# Why this exists: `command -v python3` is not a version check. macOS ships
# /usr/bin/python3 = 3.9.6, which satisfies "python3 exists" and then fails deep
# inside the build with an error that reads like a broken installer (tester's
# Mac, 17.08.2026). Everything that needs an interpreter — install.sh, the
# Makefile's venv target — asks here instead.
MIN_MINOR="${PYSAR_MIN_PY_MINOR:-10}"

ok() {
    [ -x "$1" ] || return 1
    "$1" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, $MIN_MINOR) else 1)" \
        >/dev/null 2>&1
}

# NOT newest-first: 3.12 is the version Pysar is developed and tested on, and
# PyObjC/rumps wheels lag a fresh CPython release by months — picking 3.14 the
# day it lands would trade a clear "no Python" error for an opaque build failure.
# The bare `python3` fallback is last and still version-gated by ok().
PREFERRED="python3.12 python3.13 python3.11 python3.10 python3.14 python3"

for name in $PREFERRED; do
    p="$(command -v "$name" 2>/dev/null)" || continue
    ok "$p" && { printf '%s\n' "$p"; exit 0; }
done
# Homebrew's own paths too, in case the shell's PATH predates the brew install
# that just happened in the same session.
for name in $PREFERRED; do
    ok "/opt/homebrew/bin/$name" && { printf '%s\n' "/opt/homebrew/bin/$name"; exit 0; }
done
exit 1
