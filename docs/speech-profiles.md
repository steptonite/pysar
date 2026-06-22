# Speech profiles — a practical guide

Whisper guesses words from sound. On your real vocabulary — tool names, jargon,
English terms dropped into Ukrainian, surzhyk, proper nouns — it guesses wrong a
lot: `Ollama` → `Олама`, `ESLint` → `і слінт`. **Speech profiles** fix this by
*priming* the model with a short sentence before it decodes, so it expects your
words. No rewriting happens after the fact — a profile can only nudge recognition
toward terms you actually said, never invent or swap a word. Zero RAM or latency
cost.

This is the one feature with the most moving parts, so here is the whole picture.

---

## 1. What a profile is

A profile is **one natural sentence** that names the things you talk about, tagged
with a **language**:

```json
{ "name": "Розробка / код",
  "language": "uk",
  "prompt": "Я розробник, кажу українською про код: Git, Python, API, реліз, баг, фікс." }
```

That `prompt` is fed to whisper as its `initial_prompt`. It is **priming, not an
instruction** — whisper doesn't "obey" it, it just leans toward that vocabulary.

**Two craft rules** (learned from real testing, and enforced by the design):

1. **Write a sentence, not a list.** A bare list (`React, Vite, ESLint, CI`)
   *drifts* — whisper starts mangling the very terms you listed. The same terms
   inside a sentence (`I write frontend in React and Vite, lint with ESLint, run
   CI`) prime cleanly.
2. **One main language per profile.** Weave foreign terms *into* that language's
   sentence; don't mix two languages as the base.

---

## 2. Profiles are grouped by language

A profile only applies when its `language` matches the **decode language you're
dictating in right now** (the menu-bar flag / `Ctrl+Option+U/R/E`). Ukrainian
profiles prime Ukrainian dictation; English profiles prime English; they never
cross. So you can keep a rich set for each language switched on at once — only the
relevant group is ever sent to whisper.

You toggle profiles on/off per language. The on-set for each language is
remembered independently.

---

## 3. Two ways to create profiles

### A. Let your chat AI write them (fastest)

Your everyday ChatGPT / Gemini / Claude already knows your domains from your chat
history. Use that:

1. **Settings → Speech profiles → Copy AI prompt** (pick the language first). This
   copies a ready meta-prompt to your clipboard.
2. Paste it into your chat AI. It returns 4–6 profiles as a JSON array (and may
   ask you 2–3 quick questions first).
3. Copy that JSON back, **Settings → Speech profiles → Import**, paste, Import.

The importer is forgiving: it ignores ```` ``` ```` code fences and surrounding
prose, repairs "smart quotes" (`"name"` → `"name"`) and trailing commas, and
de-duplicates by name. Re-importing a profile with the same name **updates** it.

### B. Write one by hand

**Settings → Speech profiles → + Add profile.** Give it a name, pick the language,
write one sentence. Edit or delete any profile from the same list.

---

## 4. The token budget

Whisper only keeps about **224 prompt tokens** (it drops everything before that).
So you can't switch on unlimited profiles for one language at once.

- The **token meter** (in the menu's Profiles submenu and in the set editor) shows
  `used / 224` for the current language and warns when you go over.
- When composing, profiles are added **in their listed order** until the budget is
  full; a profile that would overflow is **skipped whole** (never cut mid-sentence,
  so it stays a clean priming sentence). Order = priority — put your most important
  profiles higher.

Cyrillic averages ~3 characters per token, so a ~55-word sentence is roughly
40–70 tokens. Three or four focused profiles per language fit comfortably.

---

## 5. Profile sets — switch many at once

A **set** bundles several profiles under a name so you can flip the whole bundle
on with one key.

- Create/edit sets in **Settings → Speech profiles → Profile sets**.
- Activating a set **replaces** the entire active selection (across all languages)
  with that set's members.
- Each set gets a shortcut **`Ctrl+Option+<digit>`** by default (reassignable in
  **Settings → Hotkeys**), so you can switch context anywhere without opening a
  menu — e.g. `⌃⌥1` = Coding, `⌃⌥2` = Writing.
- The Settings list shows which set is **currently active**, and clears that mark
  the moment you hand-edit a single toggle (your selection no longer matches any
  set).
- The set editor shows the **per-language token meter** so you see at a glance when
  a set overflows the budget.

---

## 6. Examples

**Ukrainian, software work:**

> Я розробник, кажу українською про код: Git, Python, JavaScript, API, база даних,
> функція, змінна, реліз, баг, фікс, застосунок.

**English terms inside Ukrainian:**

> Українське мовлення з частими англійськими технічними термінами, назвами
> застосунків і брендів, які треба писати латиницею.

**A personal "Me" profile** (merge your main domains into one):

> Я інженер-програміст, обговорюю код, релізи та рев'ю, вплітаю англійські терміни
> в українську мову.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| A term is still mangled | No profile names it, or wrong language group is active | Add it to a sentence in the right language's profile; check the menu-bar flag |
| Import says "No JSON array found" | The AI wrapped prose around no array, or refused | Re-run the Copy-AI-prompt; make sure the reply actually contains `[ ... ]` |
| Profiles seem ignored | Token budget overflowed and yours got skipped | Check the meter; move important profiles up, switch off ones you don't need |
| Terms drift *worse* after adding a profile | The prompt is a bare list | Rewrite it as one natural sentence |

---

See also: [README](../README.md) · the priming logic lives in
[src/profiles.py](../src/profiles.py).
