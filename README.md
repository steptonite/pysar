# Cream Typer — custom build

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-Apple_Silicon-black?logo=apple)](https://www.apple.com/mac/)

My personal, customized macOS build of [**cream-typer**](https://github.com/adjacentai/cream-typer) by NeCL (MIT — see [Attribution](#attribution--license)).

Offline voice dictation: tap **Caps Lock**, speak, tap again — text is transcribed locally by whisper.cpp (Metal GPU) and pasted at your cursor in any app. No cloud, no telemetry, audio never leaves the Mac. Tuned for an **Apple M2 / 8 GB**.

---

## What I changed on top of upstream

This fork is packaged to be reproducible — it survives a clean macOS reinstall via `make setup && make app`.

- **Streaming dictation (opt-in)** — a second mode that types each sentence into the field *as you speak* instead of one transcription at the end. Audio is cut on natural pauses (never mid-word), transcribed sentence-by-sentence by a serialized worker (so word order is preserved and whisper is never hit concurrently on 8 GB), and typed via synthetic Unicode key events — **no clipboard involved**, so your clipboard is never clobbered mid-dictation. Pick **Batch** (default) or **Streaming** in **Settings → Dictation**. See [Streaming dictation](#streaming-dictation).
- **Focus-aware buffering + status HUD** — streaming watches where the keyboard focus is. If you switch away to somewhere with no text field (Spotlight, the desktop, a window with no input), it *stops typing blind* and latches into **buffer mode**: the rest of the dictation is collected and dropped onto the clipboard in one piece at Stop, so nothing lands in the wrong place. A small floating **status pill** near the menu-bar icon shows the live state (listening / recognizing / buffering) — visible even when your dictation hotkey is a silent key with no Caps-Lock LED.
- **One-command launch** — `make up` (or the `cream` alias) starts the whisper server in the background *and* the app; quitting stops both. No two-terminal dance.
- **Real `.app` in /Applications** — `make app` builds a Dock-less menu-bar agent (`LSUIElement`) with a custom icon, launchable from Spotlight/Launchpad. No Terminal window, no Dock tile, no Python rocket.
- **Language hotkeys** — hold `Ctrl+Option` + a letter to switch output language without opening the menu: `U` → 🇺🇦 Ukrainian, `R` → 🇷🇺 Russian, `E` → 🌐 any-language → English. The menu-bar icon shows the active language's flag. Default mode is `uk`. Every shortcut (dictation toggle, language switches, profile sets) is freely **reassignable** in **Settings → Hotkeys** — captured live, no relaunch.
- **VAD anti-hallucination** — the server runs with Silero VAD, so silence never reaches the model and Whisper stops inventing YouTube-style "subtitle credits" on quiet input. Also uses `--split-on-word` (no mid-word splits) and `--suppress-nst`.
- **Clean paste** — the transcriber only normalizes whitespace/newlines, so a word never lands split across a line. No content-based text filtering: real words are never dropped (the silence-hallucination problem is solved by VAD instead).
- **Optional recording archive** — off by default (audio stays in memory). Toggle **💾 Save recordings** in the menu bar to keep the last N WAVs on disk (5/10/20, auto-pruned) in `~/Library/Application Support/Cream Typer/recordings/`, so a failed or aborted dictation can be re-transcribed instead of re-spoken. **📂 Open recordings folder** reveals them in Finder. Settings persist across launches.
- **Speech profiles** — per-language priming sentences that bias Whisper toward your jargon (tool names, slang, proper nouns) so it stops mangling them. Toggle profiles per language; the composed prompt is capped to a token budget. A built-in **"Copy AI prompt"** hands any chat model a meta-prompt that returns importable profile JSON — pasted back, the import is tolerant of smart quotes and trailing commas. There's a lot here, so it has its own guide: **[docs/speech-profiles.md](docs/speech-profiles.md)**.
- **Profile sets** — bundle several profiles into a named set and switch the whole set on with one key (`Ctrl+Option+<digit>` by default, reassignable). The Settings list shows which set is **currently active** and clears that mark the moment you hand-edit a toggle. The set editor shows a **per-language token meter** so you can see at a glance when a selection overflows Whisper's prompt budget.
- **Drill-in Settings window** — a native WebKit panel instead of a tall menu, split into focused screens: a main page (audio, recordings, theme, UI language) with drill-ins for **Speech profiles** (profile editor + sets) and **Hotkeys** (dictation, language and profile-set shortcuts). **Auto / Light / Dark** themes with a live accent.
- **Bilingual UI (🇺🇦/🌐)** — the whole interface — menu bar, status line, notifications, Settings window — switches between Ukrainian and English live, independent of the dictation output language.

Model stays `large-v3-turbo-q5_0` — the best speed/quality fit for 8 GB of unified memory.

---

## Install

Needs `cmake`, `git`, and Python 3.10+ (`brew install cmake python@3.12`).

```bash
git clone https://github.com/steptonite/cream-typer-custom.git ~/code/cream-typer
cd ~/code/cream-typer
make setup    # venv + whisper.cpp (Metal) + speech model (~550 MB) + Silero VAD model
make app      # build "Cream Typer.app" into /Applications + install the `cream` alias
```

Then launch **Cream Typer** from Spotlight. On first run, grant **Input Monitoring** and **Accessibility** to *Cream Typer* in System Settings → Privacy & Security (macOS prompts for Microphone automatically), then relaunch it.

---

## Use

- **Dictate** — Caps Lock → speak → Caps Lock. Text pastes at the cursor.
- **Streaming vs batch** — by default text appears once at the end (batch). Switch to **Settings → Dictation → Streaming** to have each sentence typed as you speak. See [Streaming dictation](#streaming-dictation).
- **Switch language** — `Ctrl+Option+U` (🇺🇦) · `Ctrl+Option+R` (🇷🇺) · `Ctrl+Option+E` (🌐 → English). The menu-bar flag shows the active mode.
- **Settings** — open from the menu bar; the main page covers audio, recordings, theme and UI language, with drill-ins for **Speech profiles** (editor + sets) and **Hotkeys** (dictation, language and profile-set shortcuts, all reassignable).
- **Profile sets** — in Speech profiles, group profiles into a set and trigger it anywhere with `Ctrl+Option+<digit>` (the active set is shown live).
- **Quit** — from the menu-bar icon; it stops the whisper server too.

More languages are available in the **🌍 Languages** submenu (17 languages + the `🌐 → English (from any)` shortcut). To change the set, edit `MODES`, `MODE_LABELS`, `MENU_MODES` and the hotkeys in [src/config.py](src/config.py).

---

## Streaming dictation

By default Cream Typer is **Batch**: record, stop, transcribe the whole clip, paste
once. **Streaming** (opt-in, **Settings → Dictation → Dictation mode**) instead
types each sentence into the field *while you keep talking*.

- **Pause-based cutting.** A segment ends on a natural pause (trailing silence)
  once enough has been said — never mid-word. A run-on with no pause at all is
  force-cut at a hard cap (~18 s) as a rare fallback. Tunable in
  [src/config.py](src/config.py) (`PAUSE_SEC`, `MIN_SEG_SEC`, `MAX_SEG_SEC`,
  `SILENCE_MARGIN`).
- **In order, never concurrent.** Segments queue to a single worker that
  transcribes and types them one at a time, so word order holds and whisper is
  never run in parallel (which would thrash 8 GB).
- **No clipboard.** Sentences are typed as synthetic Unicode key events, so your
  clipboard is left untouched the whole time.
- **Focus-aware.** Before typing each sentence it checks there's a real text field
  in focus. Switch to another text field and it keeps typing there. Switch to
  somewhere with **no** field (Spotlight, the desktop) and it latches into **buffer
  mode** for the rest of the take: nothing is typed blind — the collected text is
  placed on the clipboard in one piece at Stop, with a notification, so you can
  ⌘V it where you want.
- **Status pill.** A small overlay near the menu-bar icon shows listening /
  recognizing / buffering, so you can tell it's working even if your hotkey is a
  silent key.

Trade-off: streaming is **faster to first text** but slightly lower quality than
batch on the same audio — short segments give whisper less context. Keep **Batch**
when you want the cleanest possible transcription of a long, considered take.

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
| **Accessibility** | Settings → Privacy → Accessibility | Cmd+V paste (batch) and synthetic key typing (streaming), both via CGEventPost |

Grant these to **Cream Typer** (the app), not Python or Terminal. macOS does not prompt for Accessibility automatically — add it manually, then relaunch the app.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Caps Lock does nothing | No Input Monitoring permission | grant it to Cream Typer, relaunch |
| Captured text shown in menu bar but not pasted | No Accessibility permission | grant it to Cream Typer, relaunch |
| `⚠️ Whisper not running` | Server isn't up | relaunch the app (it starts the server) |
| Words split mid-word, e.g. `перен осит` | Server segmenting on tokens | already fixed via `--split-on-word`; relaunch to restart the server |
| Subtitle-credit junk on silence | Whisper silence hallucination | VAD handles it; make sure the server restarted with the new flags |
| Wrong output language | Wrong mode active | switch with `Ctrl+Option+U/R/E` (check the menu-bar flag) |
| Slow (10–20 s) while Resolve/Photoshop open | 8 GB RAM under pressure (swap) | dictate when heavy apps are closed, or in shorter takes |

---

## Make targets

```
make setup        # full install: venv + whisper.cpp + speech & VAD models
make app          # install /Applications/Cream Typer.app + `cream` alias
make up           # run server (bg) + app from this terminal
make icon         # regenerate the app icon from scripts/make_icon.py
make whisper-vad  # (re)download the Silero VAD model
make lint / fmt / test
make distclean    # wipe venv + vendored whisper.cpp
```

Config lives in [src/config.py](src/config.py); server flags in [scripts/whisper_server.sh](scripts/whisper_server.sh) and the [Makefile](Makefile).

---

## Attribution & License

Based on [**cream-typer**](https://github.com/adjacentai/cream-typer) by **NeCL** ([neclco.com](https://neclco.com)), used under the MIT License. The original copyright notice is retained in [LICENSE](LICENSE). This repository is an independently modified build and is **not affiliated with or endorsed by** NeCL.

Also built on [whisper.cpp](https://github.com/ggerganov/whisper.cpp) (ggerganov), [OpenAI Whisper](https://github.com/openai/whisper), [rumps](https://github.com/jaredks/rumps), and [Silero VAD](https://github.com/snakers4/silero-vad).

Licensed under MIT.
