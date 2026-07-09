# Pysar

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-Apple_Silicon-black?logo=apple)](https://www.apple.com/mac/)

**Pysar** (Ukrainian for *scribe*) is my personal macOS build, grown out of [**cream-typer**](https://github.com/adjacentai/cream-typer) by NeCL (MIT — see [Attribution](#attribution--license)).

Offline voice dictation: tap **Caps Lock**, speak, tap again — text is transcribed locally by whisper.cpp (Metal GPU) and pasted at your cursor in any app. No cloud, no telemetry, audio never leaves the Mac. Tuned for an **Apple M2 / 8 GB**.

---

## What I changed on top of upstream

This fork is packaged to be reproducible — it survives a clean macOS reinstall via `make setup && make app`.

- **Streaming dictation (opt-in)** — a second mode that types each sentence into the field *as you speak* instead of one transcription at the end. Audio is cut on natural pauses (never mid-word), transcribed sentence-by-sentence by a serialized worker (so word order is preserved and whisper is never hit concurrently on 8 GB), and typed via synthetic Unicode key events — **no clipboard involved**, so your clipboard is never clobbered mid-dictation. Pick **Batch** (default) or **Streaming** in **Settings → Dictation**. See [Streaming dictation](#streaming-dictation).
- **Focus-aware buffering + status HUD** — streaming watches where the keyboard focus is. If you switch away to somewhere with no text field (Spotlight, the desktop, a window with no input), it *stops typing blind* and latches into **buffer mode**: the rest of the dictation is collected and dropped onto the clipboard in one piece at Stop, so nothing lands in the wrong place. A small floating **status pill** near the menu-bar icon shows the live state (listening / recognizing / buffering) — visible even when your dictation hotkey is a silent key with no Caps-Lock LED.
- **One-command launch** — `make up` (or the `pysar` alias) starts the whisper server in the background *and* the app; quitting stops both. No two-terminal dance.
- **Real `.app` in /Applications** — `make app` builds a Dock-less menu-bar agent (`LSUIElement`) with a custom icon, launchable from Spotlight/Launchpad. No Terminal window, no Dock tile, no Python rocket.
- **Language hotkeys** — hold `Ctrl+Option` + a letter to switch output language without opening the menu: `U` → 🇺🇦 Ukrainian, `R` → 🇷🇺 Russian, `E` → 🌐 any-language → English. The menu-bar icon shows the active language's flag. Default mode is `uk`. Every shortcut (dictation toggle, language switches, profile sets) is freely **reassignable** in **Settings → Hotkeys** — captured live, no relaunch.
- **VAD anti-hallucination** — the server runs with Silero VAD, so silence never reaches the model and Whisper stops inventing YouTube-style "subtitle credits" on quiet input. Also uses `--split-on-word` (no mid-word splits) and `--suppress-nst`.
- **Clean paste** — the transcriber only normalizes whitespace/newlines, so a word never lands split across a line. No content-based text filtering: real words are never dropped (the silence-hallucination problem is solved by VAD instead).
- **Optional recording archive** — off by default (audio stays in memory). Toggle **💾 Save recordings** in the menu bar to keep the last N WAVs on disk (5/10/20, auto-pruned) in `~/Library/Application Support/Pysar/recordings/`, so a failed or aborted dictation can be re-transcribed instead of re-spoken. **📂 Open recordings folder** reveals them in Finder. Settings persist across launches.
- **Speech profiles** — per-language priming sentences that bias Whisper toward your jargon (tool names, slang, proper nouns) so it stops mangling them. Toggle profiles per language; the composed prompt is capped to a token budget. A built-in **"Copy AI prompt"** hands any chat model a meta-prompt that returns importable profile JSON — pasted back, the import is tolerant of smart quotes and trailing commas. There's a lot here, so it has its own guide: **[docs/speech-profiles.md](docs/speech-profiles.md)**.
- **Profile sets** — bundle several profiles into a named set and switch the whole set on with one key (`Ctrl+Option+<digit>` by default, reassignable). The Settings list shows which set is **currently active** and clears that mark the moment you hand-edit a toggle. The set editor shows a **per-language token meter** so you can see at a glance when a selection overflows Whisper's prompt budget.
- **Drill-in Settings window** — a native WebKit panel instead of a tall menu, split into focused screens: a main page (audio, recordings, theme, UI language) with drill-ins for **Speech profiles** (profile editor + sets), **Hotkeys** (dictation, language and profile-set shortcuts) and **Transcribe everything**. **Auto / Light / Dark** themes with a live accent.
- **Bilingual UI (🇺🇦/🌐)** — the whole interface — menu bar, status line, notifications, Settings window — switches between Ukrainian and English live, independent of the dictation output language.
- **Transcribe everything (meeting mode)** — a separate on/off capture, independent of dictation: system audio *and* mic are transcribed live into a floating **liquid-glass island** (real macOS 26 `NSGlassEffectView`, draggable, resizable, adjustable transparency) that stays on top of everything, even fullscreen video. Optionally saved as a timestamped `.md` transcript. See [Transcribe everything](#transcribe-everything).

Model stays `large-v3-turbo-q5_0` — the best speed/quality fit for 8 GB of unified memory.

---

## Install

**One command** (Apple Silicon Mac). It installs the prerequisites (Homebrew,
`cmake`, `git`, Python) if they're missing, clones the repo, builds whisper.cpp,
downloads the models, and installs the menu-bar app:

```bash
curl -fsSL https://raw.githubusercontent.com/steptonite/pysar/main/install.sh | bash
```

Already have the repo cloned? Run the same script from inside it, or use the
Makefile directly:

```bash
./install.sh        # bootstrap (deps + setup + app), idempotent
# …or…
make all            # setup + app, assuming cmake/git/python are already present
```

`make setup` (venv + whisper.cpp + ~550 MB speech model + Silero VAD) and
`make app` (build `Pysar.app` into /Applications + the `pysar` alias) can
still be run separately.

Then launch **Pysar** from Spotlight. On first run, grant **Input Monitoring** and **Accessibility** to *Pysar* in System Settings → Privacy & Security (macOS prompts for Microphone automatically), then relaunch it.

---

## Use

- **Dictate** — Caps Lock → speak → Caps Lock. Text pastes at the cursor.
- **Streaming vs batch** — by default text appears once at the end (batch). Switch to **Settings → Dictation → Streaming** to have each sentence typed as you speak. See [Streaming dictation](#streaming-dictation).
- **Switch language** — `Ctrl+Option+U` (🇺🇦) · `Ctrl+Option+R` (🇷🇺) · `Ctrl+Option+E` (🌐 → English). The menu-bar flag shows the active mode.
- **Settings** — open from the menu bar; the main page covers audio, recordings, theme and UI language, with drill-ins for **Speech profiles** (editor + sets) and **Hotkeys** (dictation, language and profile-set shortcuts, all reassignable).
- **Profile sets** — in Speech profiles, group profiles into a set and trigger it anywhere with `Ctrl+Option+<digit>` (the active set is shown live).
- **Transcribe everything** — `🎧 Transcribe everything` in the menu bar starts/stops a live floating transcript of system audio + mic, independent of dictation. See [Transcribe everything](#transcribe-everything).
- **Quit** — from the menu-bar icon; it stops the whisper server too.

More languages are available in the **🌍 Languages** submenu (17 languages + the `🌐 → English (from any)` shortcut). To change the set, edit `MODES`, `MODE_LABELS`, `MENU_MODES` and the hotkeys in [src/config.py](src/config.py).

---

## Streaming dictation

By default Pysar is **Batch**: record, stop, transcribe the whole clip, paste
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

## Transcribe everything

A separate, independent capture mode for meetings/calls/streams: start it from
**🎧 Transcribe everything** in the menu bar (or the hotkey) and both **system
audio and your mic** are transcribed live, side by side with the person's or
your own dictation.

- **Floating island.** A borderless, real **Liquid Glass** panel (macOS 26
  `NSGlassEffectView`, with a `NSVisualEffectView` fallback on older macOS)
  shows the transcript as it's recognized, each line stamped `Source · HH:MM`.
  It floats above everything — including other apps' fullscreen video — is
  freely draggable/resizable (down to a compact strip), and remembers its
  position and size across launches.
- **Adjustable glassiness.** **Settings → Transcribe everything → Island
  transparency** goes from a solid themed panel to near-full glass — the tint
  underlay is a separate layer *below* the text, so turning up the
  transparency never fades the text itself.
- **Speaker separation.** Off (single mixed stream) / Fast (dominant source by
  loudness) / Smart (system and mic decoded separately through the same
  whisper.cpp instance, more accurate, a bit slower).
- **Context hint.** A custom priming sentence for the session (same
  token-budget meter as Speech profiles), or inherit the active dictation
  profiles for the transcription language.
- **Saved to disk (optional).** **Save transcript to file** writes a
  timestamped Markdown file per session to
  `~/Library/Application Support/Pysar/transcripts/`, reachable via **Open
  folder**. **Record without the window** keeps the file but skips showing the
  island entirely, for a fully out-of-sight capture.

Full design/process log: [docs/meeting-mode-settings.md](docs/meeting-mode-settings.md).

---

## Text enhancement (LLM styling via Ollama)

Optional post-dictation pass: the transcribed text is rewritten by a small
**local** LLM before pasting — like Telegram's "rephrase", but offline and in
*your* voice. Turn it on in **Settings → Text enhancement**.

- **Requires [Ollama](https://ollama.com)** running locally (`127.0.0.1:11434`).
  Pysar never starts or stops it; if it's down, dictation just pastes the raw
  text. An easy way to install/manage Ollama and models is **KobzarAI**.
- **Recommended model:** `hf.co/INSAIT-Institute/MamayLM-Gemma-3-4B-IT-v1.0-GGUF:Q4_K_M`
  (~2.5 GB) — winner of a blind bench on real Ukrainian/surzhyk dictations
  (02.07.2026; runner-up `gemma3:4b`). Any Ollama model can be selected in
  Settings; on 8 GB machines stay at ≤4B q4.
- **Styles.** Built-in presets (Business, Concise, Casual, Bullet points,
  No profanity) or **My style** — composed from the *style* field of your
  active Speech profiles, so the rewrite keeps your own voice.
- **Never blocks dictation.** Any LLM error or timeout falls back to the raw
  transcript; the model is kept warm only briefly (`keep_alive 5m`) so it
  doesn't pin your RAM.

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
| **Screen Recording** | Settings → Privacy → Screen Recording | needed by ScreenCaptureKit to capture system audio for **Transcribe everything** — only if you use that feature |

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
make app          # install /Applications/Pysar.app + `pysar` alias
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
