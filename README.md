# Pysar — custom build

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-Apple_Silicon-black?logo=apple)](https://www.apple.com/mac/)

My personal, customized macOS build of [**pysar**](https://github.com/steptonite/pysar) by Pysar (MIT — see [Attribution](#attribution--license)).

Offline voice dictation: tap **Caps Lock**, speak, tap again — text is transcribed locally by whisper.cpp (Metal GPU) and pasted at your cursor in any app. No cloud, no telemetry, audio never leaves the Mac. Tuned for an **Apple M2 / 8 GB**.

---

## What I changed on top of upstream

This fork is packaged to be reproducible — it survives a clean macOS reinstall via `make setup && make app`.

- **One-command launch** — `make up` (or the `cream` alias) starts the whisper server in the background *and* the app; quitting stops both. No two-terminal dance.
- **Real `.app` in /Applications** — `make app` builds a Dock-less menu-bar agent (`LSUIElement`) with a custom icon, launchable from Spotlight/Launchpad. No Terminal window, no Dock tile, no Python rocket.
- **Language hotkeys** — hold `Ctrl+Option` + a letter to switch output language without opening the menu: `U` → 🇺🇦 Ukrainian, `R` → 🇷🇺 Russian, `E` → 🌐 any-language → English. The menu-bar icon shows the active language's flag. Default mode is `uk`.
- **VAD anti-hallucination** — the server runs with Silero VAD, so silence never reaches the model and Whisper stops inventing YouTube-style "subtitle credits" on quiet input. Also uses `--split-on-word` (no mid-word splits) and `--suppress-nst`.
- **Clean paste** — the transcriber only normalizes whitespace/newlines, so a word never lands split across a line. No content-based text filtering: real words are never dropped (the silence-hallucination problem is solved by VAD instead).

Model stays `large-v3-turbo-q5_0` — the best speed/quality fit for 8 GB of unified memory.

---

## Install

Needs `cmake`, `git`, and Python 3.10+ (`brew install cmake python@3.12`).

```bash
git clone https://github.com/steptonite/pysar-custom.git ~/code/pysar
cd ~/code/pysar
make setup    # venv + whisper.cpp (Metal) + speech model (~550 MB) + Silero VAD model
make app      # build "Pysar.app" into /Applications + install the `cream` alias
```

Then launch **Pysar** from Spotlight. On first run, grant **Input Monitoring** and **Accessibility** to *Pysar* in System Settings → Privacy & Security (macOS prompts for Microphone automatically), then relaunch it.

---

## Use

- **Dictate** — Caps Lock → speak → Caps Lock. Text pastes at the cursor.
- **Switch language** — `Ctrl+Option+U` (🇺🇦) · `Ctrl+Option+R` (🇷🇺) · `Ctrl+Option+E` (🌐 → English). The menu-bar flag shows the active mode.
- **Quit** — from the menu-bar icon; it stops the whisper server too.

More languages are available in the **🌍 Languages** submenu (15 targets + the `🌐 → English (from any)` shortcut). To change the set, edit `MODES`, `MODE_LABELS`, `MENU_MODES` and the hotkeys in [src/config.py](src/config.py).

---

## How it works

```
Caps Lock (tap)  →  🎙️ recording…
Caps Lock (tap)  →  whisper.cpp (localhost:8080, Metal)  →  clipboard  →  Cmd+V  →  clipboard restored
```

The trick behind "speak any language, get any other": Whisper's encoder produces a language-agnostic representation of the audio (meaning, not words), and the decoder writes it down in whichever language the `language` token names. Swapping that token translates — without the broken `task=translate` flag, which `large-v3-turbo` was fine-tuned without.

**Latency** ~0.3–0.5 s per 10 s of speech on Apple Silicon (slower under memory pressure on 8 GB). **Privacy:** zero network egress. **Cost:** the model downloads once, inference is free.

---

## macOS permissions

| Permission | Where | Why |
|---|---|---|
| **Input Monitoring** | Settings → Privacy → Input Monitoring | Caps Lock interception (CGEventTap) |
| **Microphone** | Settings → Privacy → Microphone | audio capture |
| **Accessibility** | Settings → Privacy → Accessibility | Cmd+V paste simulation (CGEventPost) |

Grant these to **Pysar** (the app), not Python or Terminal. macOS does not prompt for Accessibility automatically — add it manually, then relaunch the app.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Caps Lock does nothing | No Input Monitoring permission | grant it to Pysar, relaunch |
| Captured text shown in menu bar but not pasted | No Accessibility permission | grant it to Pysar, relaunch |
| `⚠️ Whisper not running` | Server isn't up | relaunch the app (it starts the server) |
| Words split mid-word, e.g. `перен осит` | Server segmenting on tokens | already fixed via `--split-on-word`; relaunch to restart the server |
| Subtitle-credit junk on silence | Whisper silence hallucination | VAD handles it; make sure the server restarted with the new flags |
| Wrong output language | Wrong mode active | switch with `Ctrl+Option+U/R/E` (check the menu-bar flag) |
| Slow (10–20 s) while Resolve/Photoshop open | 8 GB RAM under pressure (swap) | dictate when heavy apps are closed, or in shorter takes |

---

## Make targets

```
make setup        # full install: venv + whisper.cpp + speech & VAD models
make app          # install /Applications/Pysar.app + `cream` alias
make up           # run server (bg) + app from this terminal
make icon         # regenerate the app icon from scripts/make_icon.py
make whisper-vad  # (re)download the Silero VAD model
make lint / fmt / test
make distclean    # wipe venv + vendored whisper.cpp
```

Config lives in [src/config.py](src/config.py); server flags in [scripts/whisper_server.sh](scripts/whisper_server.sh) and the [Makefile](Makefile).

---

## Attribution & License

Based on [**pysar**](https://github.com/steptonite/pysar) by **Pysar** ([github.com/steptonite/pysar](https://github.com/steptonite/pysar)), used under the MIT License. The original copyright notice is retained in [LICENSE](LICENSE). This repository is an independently modified build and is **not affiliated with or endorsed by** Pysar.

Also built on [whisper.cpp](https://github.com/ggerganov/whisper.cpp) (ggerganov), [OpenAI Whisper](https://github.com/openai/whisper), [rumps](https://github.com/jaredks/rumps), and [Silero VAD](https://github.com/snakers4/silero-vad).

Licensed under MIT.
