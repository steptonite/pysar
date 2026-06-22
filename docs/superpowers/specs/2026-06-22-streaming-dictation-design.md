# Streaming Dictation — Design

**Date:** 2026-06-22
**Status:** Approved for planning
**Topic:** Speed up long dictation by transcribing and inserting sentence-by-sentence instead of one batch at the end.

## Problem

Today dictation is **batch**: Caps Lock starts recording, the second tap stops it, the *entire* clip is sent to whisper.cpp as one WAV, and the full text is pasted once. For a long dictation (minutes) the user speaks, then waits while the whole clip transcribes, and sees nothing until the end. Goal: see text appear as you speak, faster results, **without losing transcription quality**.

## Goals / Non-goals

**Goals**
- Insert text into the focused field progressively, **sentence by sentence**, while the user keeps talking.
- Cut on **natural pauses (sentence boundaries)** — never mid-word.
- Preserve punctuation and sentence structure (quality must match batch).
- Keep the existing batch mode, selectable via a Settings switch (batch = default).
- Be robust: a single failed segment or a mic glitch must not kill the session silently.

**Non-goals**
- True low-latency word-by-word streaming (whisper.cpp partial hypotheses) — unstable, would require deleting/retyping text. Rejected.
- Fixed-window chunking with overlap+dedup — cuts mid-word, messy punctuation. Rejected.
- Any change to the AX insertion path (AX is dead for Claude/Electron; confirmed).

## Approach (chosen: pause-segmented + incremental direct-type)

Record continuously. A pure **Segmenter** watches the audio energy (RMS) and emits a segment whenever a sentence-sized utterance is followed by a pause. Each emitted segment is transcribed independently (a complete utterance → whisper punctuates it correctly) and **typed directly into the focused field** via synthetic Unicode key events — no clipboard involved.

Rejected alternatives: see Non-goals.

## Components (isolated, independently testable)

### 1. `Segmenter` (new, pure logic — `src/segmenter.py`)
No I/O, no audio library. Fed normalized audio blocks (or precomputed RMS + frame bytes); decides segment boundaries.

- **Input:** `feed(block: np.ndarray) -> bytes | None` — returns a finished segment's raw float32 bytes when a boundary is hit, else None. `flush() -> bytes | None` — returns the trailing buffered segment at stop.
- **State machine:**
  - Accumulate incoming blocks. Track trailing-silence duration from per-block RMS vs an adaptive threshold (noise floor + margin).
  - **Emit** when: trailing silence ≥ `PAUSE_SEC` **and** buffered *voiced* audio ≥ `MIN_SEG_SEC`. The emitted segment is the buffered audio up to (and including a short tail of) the pause; reset buffer.
  - **Safety cap:** if buffered audio reaches `MAX_SEG_SEC` without a qualifying pause, force-emit (prevents a run-on monologue from never flushing). This is the only place a cut may not align to a sentence; acceptable as a rare fallback.
  - Drop leading silence so segments start on speech.
- **Adaptive threshold:** seed the noise floor from the first ~300 ms, update slowly; avoids hard-coded RMS that breaks on a hot/quiet mic.
- **Testable** with synthetic block sequences: assert emit on pause, no emit below MIN_SEG, forced emit at MAX_SEG, correct flush.

### 2. `AudioRecorder` — streaming mode (`src/recorder.py`, extended)
- New optional `on_segment: Callable[[bytes], None]` passed to `start()`. When set, the `_record` loop feeds each block to a `Segmenter` and calls `on_segment(seg_wav)` for every emitted segment (wrapped to WAV via the existing `_to_wav` logic, refactored to a module helper `pcm_to_wav(float32_bytes)`).
- On `stop()`: flush the Segmenter; emit the final segment through `on_segment`; still return the **full-session WAV** (unchanged) so the existing "save audio for recoverability" behavior is preserved.
- **Mic robustness (fixes the PaErrorCode -9986 we hit):** if `sd.InputStream` open fails, retry once after a short delay; if it still fails, call an `on_error` callback so the app surfaces a visible status/notification instead of silently producing nothing.

### 3. `TypeWriter` insertion (`src/backend/_macos.py`, new `Paster.type_text`)
- Inserts a string by posting synthetic Unicode key events (`CGEventKeyboardSetUnicodeString` on a keydown/keyup pair) using a **private event source** (same isolation lesson as the Cmd+V fix). No clipboard, no Command modifier.
- Long strings are split into small slices per event (≤ ~16 code units) to avoid event-string truncation.
- Used by streaming mode for each segment. Batch mode keeps the existing `paste_text` (Cmd+V) path unchanged.

### 4. `StreamingDictation` worker (`src/app.py`, new path in the toggle handler)
- A single serialized worker (thread + queue) so segments are transcribed and inserted **in order**, never concurrently (preserves order, avoids hammering whisper on 8 GB).
- For each queued segment: `transcribe(seg, mode, prompt)` → on success `type_text(prefix + text)` where `prefix` is `""` for the first inserted segment of the session and `" "` thereafter (whisper supplies in-segment punctuation). On transcription error: log + skip, keep the session alive.
- Re-entry: the session owns `_busy` from start until stop **and** queue drain.

### 5. Settings: dictation mode switch
- `config.py` default `dictation_mode: "batch"`. Values: `"batch"` | `"streaming"`.
- i18n labels (uk + en): e.g. "Режим диктовки" with options "Звичайний" / "Пришвидшений (по реченнях)".
- `settings_window.py`: a segmented/select control wired through the existing settings message bridge; persists via `save_settings`.
- `app.py._on_toggle` branches on the mode: `streaming` → segmenter path; otherwise the current batch path.

## Data flow (streaming)

```
Caps Lock ✊ (start)
  → capture_target()              # focused app/field, as today
  → recorder.start(on_segment)    # continuous capture + Segmenter

… user speaks …
  block → Segmenter.feed()
        → (pause detected) segment → queue → transcribe → type_text(" " + text)   # appears in field
  … repeats per sentence …

Caps Lock ✊ (stop)
  → recorder.stop()               # flush() final segment → queue; returns full WAV
  → save full WAV (recoverability, unchanged)
  → wait for queue drain
  → done; clear _busy
```

No clipboard is touched at any point in streaming mode.

## Error handling

- **Mic open failure (-9986):** retry once; on persistent failure, visible notification ("Мікрофон недоступний — спробуйте ще раз"); session aborts cleanly, `_busy` cleared.
- **Segment transcription error / whisper down:** log, skip that segment, continue; if whisper is down for the whole session, the first failure notifies once.
- **Focus lost mid-stream:** `type_text` goes to whatever is focused (same semantics as the user typing). The Spotlight/overlay concern is far smaller than with Cmd+V because there's no clipboard residue; if an overlay is detected at insert time we skip that segment's insert and notify once (do not lose the text — it is logged), rather than typing into Spotlight. (Minimal handling; revisit only if it bites.)
- **Very long run-on:** `MAX_SEG_SEC` cap forces a flush.

## Parameters (tunable, `config.py`)

| Name | Start value | Meaning |
|------|-------------|---------|
| `PAUSE_SEC` | 0.6 | trailing silence that ends a segment |
| `MIN_SEG_SEC` | 0.8 | min voiced audio before a pause can cut |
| `MAX_SEG_SEC` | 12 | hard cap; force-emit without a pause |
| `SILENCE_MARGIN` | adaptive | RMS margin above noise floor = "voiced" |

## Testing

- **Segmenter** (pure): synthetic RMS/block sequences → assert boundaries (pause cut, MIN_SEG suppression, MAX_SEG force, flush, leading-silence drop, adaptive threshold). No hardware.
- **StreamingDictation worker:** fake `transcribe` + fake `type_text` → assert in-order insertion, prefix spacing (first vs subsequent), error-skip keeps session alive.
- **`pcm_to_wav` helper:** round-trip sanity (header, sample rate, mono).
- Existing 107 tests stay green; batch path untouched.

## Out of scope (explicit)

- LLM punctuation/cleanup layer (separate effort — see project memory `issue_cream_typer_llm_cleanup`).
- Spotlight "smart capture" (dictating *into* Spotlight) — deferred per user.
- Tuning UI for the parameters above — start with config constants; expose later only if needed.
