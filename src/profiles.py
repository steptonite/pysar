"""Speech profiles — whisper initial-prompt priming, composable per language.

A *profile* is a tiny natural-sentence prompt fed to whisper before decoding.
It biases recognition toward the user's real vocabulary (names, jargon,
English terms inside Ukrainian speech, surzhyk) WITHOUT any post-hoc rewriting,
so it can never change a word the model didn't hear — unlike an LLM cleanup pass.

Empirically (tested on real dictations): a well-formed profile lifts correct
tech-term recognition markedly and cuts mangled terms, at zero RAM/latency cost.
Two craft rules that the testing surfaced and that the rest of this module bakes in:

  1. A profile prompt must read like a NATURAL SENTENCE, not a bare term list —
     a list drifts ("Олама"→"ULAMA"), a sentence primes cleanly.
  2. whisper caps the prompt at ~224 tokens (it keeps the *tail* past that), so
     composing several profiles is bounded. We pre-trim to the budget ourselves
     in selection order, so what the user sees is what whisper gets.

Profiles carry a `language` (the whisper decode language). Only profiles whose
language matches the active decode language contribute to a given transcription;
that's how "switch language" and "which profiles apply" stay coherent.
"""

import json
import re

# whisper keeps only the last n_text_ctx/2 = 224 prompt tokens. Stay under it so
# nothing the user selected gets silently dropped from the *front*.
PROMPT_TOKEN_BUDGET = 224

# The extraction prompt the user pastes into ChatGPT/Gemini/Claude — the AI that
# already knows them from chat history writes their profiles. Kept here (not just
# in the UI) so the format the importer parses and the format we ask for are one
# source of truth.
META_PROMPT = (
    "Ти допомагаєш мені створити профілі для офлайн-диктувальника на базі whisper.\n"
    "whisper приймає initial_prompt — короткий текст, що ПРАЙМИТЬ розпізнавання\n"
    "(не інструкція!). Правила: кожен профіль = ОДНЕ природне речення (НЕ список),\n"
    "щільне на мої реальні терміни/імена/жаргон, ≤55 слів, одна основна мова;\n"
    "іншомовні терміни (англ/рос) вплітай у те саме речення.\n\n"
    "ПОГАНО (список — дрейфує): React, Vite, TypeScript, ESLint, webpack, CI.\n"
    "ДОБРЕ (речення): Я пишу фронтенд на React і TypeScript, ганяю Vite та ESLint\n"
    "і налаштовую CI у GitHub Actions.\n\n"
    "Використай те, що ти про мене знаєш з нашої історії. Якщо чогось бракує —\n"
    "спитай мене 2-3 короткі питання, потім згенеруй.\n\n"
    "ФОРМАТ (суворо): видай ЛИШЕ валідний JSON-масив. БЕЗ коментарів, БЕЗ\n"
    'markdown-огорожі ```; лише ПРЯМІ ASCII-лапки " (НЕ «розумні» “ ” ’);\n'
    "без коми перед ] чи }. Точний приклад цілої відповіді:\n"
    '[{"name":"Фронтенд","language":"uk","prompt":"Я пишу веб на React і '
    'TypeScript, кажу про компоненти, стейт, пропси, Vite, ESLint і CI українською"},\n'
    ' {"name":"Я","language":"uk","prompt":"Я інженер-програміст, обговорюю код, '
    "релізи та рев'ю, вплітаю англійські терміни в українську мову\"}]\n"
    'Зроби 4-6 профілів по своїх доменах + один "Я" (склеєний з головних).'
)

# English equivalent — copied when the app language is English, so the prompt the
# user pastes into their chat AI matches the UI they're reading.
META_PROMPT_EN = (
    "Help me build profiles for an offline whisper-based dictation app.\n"
    "whisper takes an initial_prompt — a short text that PRIMES recognition\n"
    "(not an instruction!). Rules: each profile = ONE natural sentence (NOT a\n"
    "list), dense with my real terms/names/jargon, ≤55 words, one main language;\n"
    "weave foreign terms into the same sentence.\n\n"
    "BAD (a list — drifts): React, Vite, TypeScript, ESLint, webpack, CI.\n"
    "GOOD (a sentence): I write frontend in React and TypeScript, run Vite and\n"
    "ESLint and set up CI in GitHub Actions.\n\n"
    "Use what you know about me from our history. If something's missing, ask me\n"
    "2-3 short questions, then generate.\n\n"
    "FORMAT (strict): output ONLY a valid JSON array. NO comments, NO markdown\n"
    '``` fences; use only STRAIGHT ASCII quotes " (NOT smart “ ” ’); no comma\n'
    "before ] or }. Exact example of a whole reply:\n"
    '[{"name":"Frontend","language":"en","prompt":"I build web apps in React and '
    'TypeScript, talking about components, state, props, Vite, ESLint and CI"},\n'
    ' {"name":"Me","language":"en","prompt":"I am a software engineer, I discuss '
    'code, releases and reviews, weaving English terms into my speech"}]\n'
    'Make 4-6 profiles across my domains + one "Me" (merged from the main ones).'
)

_META_PROMPTS = {"uk": META_PROMPT, "en": META_PROMPT_EN}


def meta_prompt(lang: str = "uk") -> str:
    """The AI extraction prompt in the app's language (falls back to Ukrainian)."""
    return _META_PROMPTS.get(lang, META_PROMPT)


# Superseded shipped surzhyk style_prompts, kept verbatim so load_settings()
# can migrate persisted copies to the current default (exact match only — any
# user-edited text is left alone). v1 (pre-03.07.2026) read as "change nothing"
# and small models obediently echoed the input; v2 (03.07 morning) said what to
# do but lacked the explicit no-verbatim-copy rule (bench v3: lazy copies).
LEGACY_SURZHYK_STYLES = (
    "Збережи суржик, іншомовні слова та авторські звороти; виправ лише "
    "пунктуацію та розриви речень. Виведи лише виправлений текст.",
    "Відредагуй сиру диктовку: прибери слова-паразити, повтори та "
    "обірвані фальстарти; розстав пунктуацію й розбий на речення; "
    "склей розірвані думки в цілісні фрази. Лексику не замінюй: "
    "збережи суржик, англіцизми, авторські звороти й нецензурні слова "
    "як є. Нічого не вигадуй. Виведи лише відредагований текст.",
)

# Shipped starter library: general profiles + common domains, Ukrainian-first,
# surzhyk-aware. Each prompt is a single natural sentence (see craft rule #1).
DEFAULT_PROFILES: list[dict] = [
    {
        "name": "Загальна українська",
        "language": "uk",
        "prompt": "Жива розмовна українська мова, повсякденні теми, природне мовлення.",
    },
    {
        "name": "Суржик / розмова",
        "language": "uk",
        "prompt": (
            "Жива розмовна українська з суржиком та англійськими термінами, "
            "інколи російські слова й нецензурна лексика — без цензури."
        ),
        "style_prompt": (
            "Відредагуй сиру диктовку: прибери слова-паразити, повтори та "
            "обірвані фальстарти; розстав пунктуацію й розбий на речення; "
            "склей розірвані думки в цілісні фрази. Лексику не замінюй: "
            "збережи суржик, англіцизми, авторські звороти й нецензурні слова "
            "як є. Нічого не вигадуй. Дослівна копія входу — це помилка: "
            "результат мусить відрізнятися щонайменше пунктуацією та "
            "прибраними повторами. Виведи лише відредагований текст."
        ),
    },
    {
        "name": "Розробка / код",
        "language": "uk",
        "prompt": (
            "Я розробник, кажу українською про код: Git, Python, JavaScript, API, "
            "база даних, функція, змінна, реліз, баг, фікс, застосунок."
        ),
    },
    {
        "name": "Англ-терміни в укр",
        "language": "uk",
        "prompt": (
            "Українське мовлення з частими англійськими технічними термінами, "
            "назвами застосунків і брендів, які треба писати латиницею."
        ),
    },
    {
        "name": "Ділова / робоча",
        "language": "uk",
        "prompt": (
            "Робочі обговорення українською: дедлайн, таск, реліз, мітинг, "
            "пріоритет, спринт, фідбек, презентація."
        ),
        "style_prompt": (
            "Діловий тон без сленгу, чіткі речення, збережи зміст і мову. "
            "Виведи лише виправлений текст."
        ),
    },
    {
        "name": "English",
        "language": "en",
        "prompt": "Natural spoken English, everyday and technical topics.",
    },
    {
        "name": "Русский",
        "language": "ru",
        "prompt": "Живая разговорная русская речь, повседневные и технические темы.",
    },
]

# Languages we offer in the import normalizer / validator. Anything else is
# rejected so a malformed paste can't inject a bogus decode language.
_KNOWN_LANGS = {
    "auto",
    "uk",
    "en",
    "ru",
    "es",
    "de",
    "fr",
    "it",
    "pt",
    "nl",
    "pl",
    "ja",
    "zh",
    "ko",
    "tr",
    "th",
    "vi",
    "ar",
}


def estimate_tokens(text: str) -> int:
    """Rough whisper-token count. Cyrillic averages ~3 chars/token under the
    multilingual BPE, so chars/3 is a good-enough meter for the budget UI and
    the soft trim. Intentionally approximate — we only need to stay safely under
    PROMPT_TOKEN_BUDGET, not be exact."""
    if not text:
        return 0
    return max(1, round(len(text) / 3))


def active_for_language(profiles: list[dict], active_names: list[str], language: str) -> list[dict]:
    """Profiles that are toggled on AND match the active decode language,
    in their stored order (which is the order the user sees and prioritizes)."""
    names = set(active_names)
    return [p for p in profiles if p.get("name") in names and p.get("language") == language]


def compose_prompt(profiles: list[dict], active_names: list[str], language: str) -> str:
    """Concatenate the active same-language profile prompts into one whisper
    prompt, trimmed to the token budget in selection order. Whole profiles that
    would overflow are dropped (not partially cut) so each profile stays a clean
    sentence; the budget meter warns the user before it comes to that."""
    chosen = active_for_language(profiles, active_names, language)
    parts: list[str] = []
    used = 0
    for p in chosen:
        prompt = (p.get("prompt") or "").strip()
        if not prompt:
            continue
        cost = estimate_tokens(prompt)
        if used + cost > PROMPT_TOKEN_BUDGET:
            continue  # skip overflow; keep earlier (higher-priority) profiles intact
        parts.append(prompt)
        used += cost
    return " ".join(parts)


# ── Enhance styles (LLM post-processing) ─────────────────────────────────────
# A *style* is the system instruction for the enhance step (postprocessor.py) —
# unlike a whisper profile prompt it's an INSTRUCTION, and it isn't bound by the
# 224-token whisper budget. Built-in presets cover the common asks; a profile's
# optional `style_prompt` field carries the user's own voice on top.

STYLE_PRESETS: list[dict] = [
    {
        "key": "business",
        "name_uk": "Діловий",
        "name_en": "Business",
        # «Перепиши у діловому стилі» invites 4B models to COMPOSE a formal
        # document from scratch (bench v3: MamayLM hallucinated a two-page
        # «Загальні рекомендації»). Framing it as editing a work message keeps
        # them anchored to the dictation.
        "prompt": (
            "Відредагуй сиру диктовку як фрагмент робочого повідомлення: "
            "без сленгу та ненормативної лексики, чіткі повні речення, "
            "стриманий тон. Це редагування, а не твір: нічого не додавай, "
            "не складай документів і не оформлюй списків. Збережи зміст і "
            "мову оригіналу. Виведи лише відредагований текст."
        ),
    },
    {
        "key": "concise",
        "name_uk": "Коротше",
        "name_en": "Concise",
        "prompt": (
            "Стисни текст: прибери слова-паразити й повтори, але збережи "
            "кожен факт. Збережи зміст і мову оригіналу, не вигадуй фактів. "
            "Виведи лише стислий текст, без коментарів."
        ),
    },
    {
        "key": "casual",
        "name_uk": "Розмовний",
        "name_en": "Casual",
        "prompt": (
            "Перепиши текст у легкому розмовному стилі, природно; де можливо, "
            "зберігай авторські слова та звороти. Збережи зміст і мову "
            "оригіналу, не вигадуй фактів. Виведи лише переписаний текст, "
            "без коментарів."
        ),
    },
    {
        "key": "bullets",
        "name_uk": "Список тез",
        "name_en": "Bullet points",
        "prompt": (
            "Перетвори текст на маркований список ключових думок: кожен пункт "
            "з нового рядка, починай з «- ». Збережи зміст і мову оригіналу, "
            "не вигадуй фактів. Виведи лише список, без коментарів."
        ),
    },
    {
        "key": "emoji",
        "name_uk": "Емоджі",
        "name_en": "Emoji",
        "prompt": (
            "Розстав у тексті доречні за контекстом емоджі: стримано, 1–3 на "
            "короткий текст, після ключових фраз або в кінці речень. Збережи "
            "сам текст дослівно — не переписуй і не скорочуй; дозволено лише "
            "виправити пунктуацію та очевидні помилки розпізнавання мовлення. "
            "Нічого не вигадуй. Виведи лише текст з емоджі."
        ),
    },
    {
        "key": "clean",
        "name_uk": "Без матюків",
        "name_en": "No profanity",
        "prompt": (
            "Заміни ненормативну лексику на нейтральні слова; більше нічого не "
            "змінюй — ні стиль, ні структуру. Збережи зміст і мову оригіналу, "
            "не вигадуй фактів. Виведи лише виправлений текст, без коментарів."
        ),
    },
]

# ≈1000 tokens for Cyrillic (chars/3) — system-prompt sized, not whisper-budget
# sized. Soft cap so a runaway pasted style can't blow the model's context.
STYLE_PROMPT_CHAR_LIMIT = 3000

# Returned when no preset is chosen and no active profile carries a style —
# the mildest useful transform, so the toggle never silently does nothing.
FALLBACK_STYLE_PROMPT = (
    "Виправ пунктуацію та очевидні мовні помилки, збережи зміст і мову тексту. "
    "Виведи лише виправлений текст."
)


def style_preset(key: str) -> dict | None:
    """The preset dict for *key*, or None."""
    return next((p for p in STYLE_PRESETS if p["key"] == key), None)


# Few-shot for the enhance call: 4B models follow a worked example far better
# than abstract rules (bench v3, 03.07.2026 — abstract-only prompts got verbatim
# copies, instruction leaks and hallucinated documents). The input is a real
# dictation (29.06.2026, not in the bench set): it has the honest raw-dictation
# defects — a duplicated word («всіх, всіма») and a trailing false start.
STYLE_EXAMPLE_INPUT = (
    "Але я спостерігаю кожного дня, заходячи в будь-яку соцмережу, як всіх, "
    "всіма клеймлять. І оце мені не сподобалось. Але особливо мене вибішує "
    "ось така."
)

# key → rewritten example. Only the styles that earned an example so far
# (bench v4 scope: custom + business); others fall back to no few-shot.
_STYLE_EXAMPLE_OUTS = {
    "custom": (
        "Але я спостерігаю кожного дня, заходячи в будь-яку соцмережу, як усі "
        "всіх клеймлять. І оце мені не сподобалось — а особливо вибішує ось "
        "ця тема."
    ),
    "business": (
        "Щодня я бачу в соцмережах, як користувачі навішують одне на одного "
        "ярлики. Мені це не подобається, а особливо дратує саме ця тенденція."
    ),
    # Emoji: the text stays verbatim (incl. the raw-speech duplication) — the
    # example teaches restraint: a couple of context emoji, nothing rewritten.
    "emoji": (
        "Але я спостерігаю кожного дня, заходячи в будь-яку соцмережу, як "
        "всіх, всіма клеймлять 🙄. І оце мені не сподобалось 😕. Але особливо "
        "мене вибішує ось така."
    ),
}


def style_example(key: str | None) -> tuple[str, str] | None:
    """(raw, rewritten) few-shot pair for a style, or None if it has none.

    *key* is a STYLE_PRESETS key, or "custom"/None for the profile-composed
    style (the default profiles' cleanup instructions match the example).
    """
    out = _STYLE_EXAMPLE_OUTS.get(key or "custom")
    return (STYLE_EXAMPLE_INPUT, out) if out else None


def compose_style_prompt(
    profiles: list[dict],
    active_names: list[str],
    language: str,
    preset_key: str | None = None,
) -> str:
    """System prompt for the enhance step: optional preset + the non-empty
    `style_prompt` of the active same-language profiles, newline-joined.
    Soft-capped at STYLE_PROMPT_CHAR_LIMIT, dropping whole entries that would
    overflow (earlier profiles win — same rule as compose_prompt)."""
    base = ""
    if preset_key:
        preset = style_preset(preset_key)
        if preset:
            base = preset["prompt"].strip()

    parts: list[str] = [base] if base else []
    used = len(base)
    for p in active_for_language(profiles, active_names, language):
        sp = (p.get("style_prompt") or "").strip()
        if not sp:
            continue
        if used + len(sp) + 1 > STYLE_PROMPT_CHAR_LIMIT:
            continue  # skip overflow; keep earlier (higher-priority) entries intact
        parts.append(sp)
        used += len(sp) + 1

    return "\n".join(parts).strip() or FALLBACK_STYLE_PROMPT


def budget_usage(profiles: list[dict], active_names: list[str], language: str) -> tuple[int, int]:
    """(used_tokens, budget) for the active same-language selection — for the
    menu meter. used counts the *requested* selection (pre-trim), so the user
    sees when they've asked for more than fits."""
    chosen = active_for_language(profiles, active_names, language)
    used = sum(estimate_tokens((p.get("prompt") or "").strip()) for p in chosen)
    return used, PROMPT_TOKEN_BUDGET


def validate_profile(d: object) -> dict | None:
    """Coerce one parsed item into a clean profile, or None if unusable.
    Defensive: imported JSON comes from an LLM and may be ragged."""
    if not isinstance(d, dict):
        return None
    name = str(d.get("name", "")).strip()
    prompt = str(d.get("prompt", "")).strip()
    lang = str(d.get("language", "")).strip().lower()
    style_prompt = str(d.get("style_prompt", "") or "").strip()
    if not name or not prompt:
        return None
    if lang not in _KNOWN_LANGS:
        lang = "uk"  # sane default rather than dropping a useful profile
    return {"name": name, "language": lang, "prompt": prompt, "style_prompt": style_prompt}


# Typographic → ASCII, for the lenient retry below. Chat AIs (notably ChatGPT)
# love to "smart-quote" their JSON — “name” instead of "name" — which is invalid
# JSON. We only apply this when strict parsing has already failed, so a paste
# that's valid as-is (incl. legitimate curly quotes *inside* a string) is never
# touched.
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"', "＂": '"',
                               "‘": "'", "’": "'", "‚": "'", "‛": "'"})  # fmt: skip


def _loads_lenient(block: str):
    """json.loads, then — only on failure — a forgiving retry that repairs the
    two breakages chat AIs reliably emit: smart quotes and a trailing comma
    before ] or }. Returns the parsed value, or raises the *original* error."""
    try:
        return json.loads(block)
    except json.JSONDecodeError as first:
        repaired = block.translate(_SMART_QUOTES)
        repaired = re.sub(r",\s*([\]}])", r"\1", repaired)  # drop trailing commas
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            raise first from None


def _key(p: dict) -> tuple[str, str]:
    """A profile's identity: name scoped to its decode language. The SAME name
    is allowed to exist once per language (e.g. a "Я" for uk and a distinct "Я"
    for ru) — they're different profiles, not the same one re-typed."""
    return (p.get("name", ""), p.get("language", "uk"))


def parse_imported(text: str) -> tuple[list[dict], str | None]:
    """Parse the JSON the user pasted from their chat AI. Tolerant of code
    fences, surrounding prose, smart quotes and trailing commas: extract the
    first JSON array, validate items. Returns (profiles, error)."""
    if not text or not text.strip():
        return [], "Empty paste."
    # Grab the first [...] block, so ```json fences or chatty preambles are fine.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return [], "No JSON array found in the pasted text."
    try:
        raw = _loads_lenient(match.group(0))
    except json.JSONDecodeError as e:
        return [], f"Invalid JSON: {e}"
    if not isinstance(raw, list):
        return [], "Expected a JSON array of profiles."
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        prof = validate_profile(item)
        if prof and _key(prof) not in seen:
            out.append(prof)
            seen.add(_key(prof))
    if not out:
        return [], "No valid profiles in the pasted JSON."
    return out, None


def merge_profiles(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Merge imported profiles into the current set: same (name, language)
    overwrites (re-import to update), everything else appends as a new profile
    — including a name that already exists under a *different* language, which
    is a distinct profile, not a duplicate. Order: existing first, then new.

    Callers that don't want a silent overwrite should check `import_conflicts`
    first and only call this once the user has confirmed."""
    by_key = {_key(p): p for p in existing}
    order = [_key(p) for p in existing]
    for p in incoming:
        k = _key(p)
        if k not in by_key:
            order.append(k)
        by_key[k] = p
    return [by_key[k] for k in order]


def import_conflicts(existing: list[dict], incoming: list[dict]) -> list[str]:
    """Names in `incoming` that would silently overwrite a *different* existing
    profile (same name AND language, different prompt/style_prompt) if merged.
    A name that already exists only under another language is NOT a conflict —
    it becomes a separate profile in its own right.

    Pasted JSON is usually written by an AI that has no idea what the user
    already calibrated — a name collision (the built-in meta-prompt even
    suggests the generic "Я" as an example name) must never clobber a
    profile the user already tuned without the user seeing it coming first."""
    by_key = {_key(p): p for p in existing}
    out: list[str] = []
    for p in incoming:
        old = by_key.get(_key(p))
        if old is None:
            continue
        same = old.get("prompt") == p.get("prompt") and (old.get("style_prompt") or "") == (
            p.get("style_prompt") or ""
        )
        if not same:
            out.append(p["name"])
    return out


def upsert_profile(
    profiles: list[dict],
    name: str,
    language: str,
    prompt: str,
    original_name: str | None = None,
    style_prompt: str = "",
    original_language: str | None = None,
) -> tuple[list[dict], str | None]:
    """Add a new profile or edit an existing one (the Settings-window editor).

    Identity is (name, language) — the same name is fine under a different
    language (a uk "Я" and a ru "Я" are two profiles), but a clash within the
    same language is rejected so nothing gets silently shadowed.

    `original_name`/`original_language` set → edit that profile in place (lets
    the user rename or re-language it); `original_name` None → create a new
    one. Returns (profiles, error); error is a short message on bad input or a
    same-language name clash, in which case `profiles` is returned unchanged.
    """
    clean = validate_profile(
        {"name": name, "language": language, "prompt": prompt, "style_prompt": style_prompt}
    )
    if clean is None:
        return profiles, "Name and prompt can't be empty."

    out = [dict(p) for p in profiles]
    keys = {_key(p) for p in out}

    if original_name is None:
        if _key(clean) in keys:
            return profiles, f"A profile named “{clean['name']}” already exists in this language."
        out.append(clean)
        return out, None

    # Edit: locate the original by (name, language); a rename/re-language must
    # not collide with a *different* profile that already has that identity.
    orig_key = (original_name, original_language if original_language is not None else language)
    idx = next((i for i, p in enumerate(out) if _key(p) == orig_key), None)
    if idx is None:
        return profiles, f"Profile “{original_name}” not found."
    if _key(clean) != orig_key and _key(clean) in keys:
        return profiles, f"A profile named “{clean['name']}” already exists in this language."
    out[idx] = clean
    return out, None


def remove_profile(profiles: list[dict], name: str, language: str | None = None) -> list[dict]:
    """Drop the profile with this (name, language) (no-op if absent).

    `language` is optional only for backward-compat call sites; passing it is
    required to avoid removing the wrong profile when the same name exists
    under more than one language."""
    if language is None:
        return [p for p in profiles if p.get("name") != name]
    return [p for p in profiles if _key(p) != (name, language)]


def regroup_active(profiles: list[dict], members: list) -> dict[str, list[str]]:
    """Group the given set *members* by decode language → an active_profiles
    dict ({lang: [names]}). Used to activate a profile *set* as the entire
    selection at once, replacing whatever was on before.

    Each member is either `{"name": ..., "language": ...}` (exact identity —
    the format sets are saved in now) or a bare name string (legacy sets saved
    before duplicate names across languages were possible; resolved by first
    matching profile, same as always). Members with no matching profile are
    dropped."""
    by_key = {_key(p): p for p in profiles}
    by_name_first = {}
    for p in profiles:
        by_name_first.setdefault(p.get("name"), p)
    out: dict[str, list[str]] = {}
    for m in members:
        if isinstance(m, dict):
            p = by_key.get((m.get("name"), m.get("language")))
        else:
            p = by_name_first.get(m)
        if p is not None:
            out.setdefault(p.get("language", "uk"), []).append(p.get("name"))
    return out


def active_set_index(
    profile_sets: list[dict], profiles: list[dict], active_profiles: dict
) -> int | None:
    """Index of the set whose members exactly equal the current active selection
    (order-independent, ignoring empty language groups), or None if none matches.
    Lets the UI show *which* set is live — and clear that once the user hand-edits
    a toggle so the selection no longer matches any set."""
    cur = {lng: set(v) for lng, v in (active_profiles or {}).items() if v}
    for i, s in enumerate(profile_sets):
        grouped = regroup_active(profiles, s.get("members", []))
        if {lng: set(v) for lng, v in grouped.items() if v} == cur:
            return i
    return None
