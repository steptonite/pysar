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
    if not name or not prompt:
        return None
    if lang not in _KNOWN_LANGS:
        lang = "uk"  # sane default rather than dropping a useful profile
    return {"name": name, "language": lang, "prompt": prompt}


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
    seen: set[str] = set()
    for item in raw:
        prof = validate_profile(item)
        if prof and prof["name"] not in seen:
            out.append(prof)
            seen.add(prof["name"])
    if not out:
        return [], "No valid profiles in the pasted JSON."
    return out, None


def merge_profiles(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Merge imported profiles into the current set: same name overwrites
    (re-import to update), new names append. Order: existing first, then new."""
    by_name = {p["name"]: p for p in existing}
    order = [p["name"] for p in existing]
    for p in incoming:
        if p["name"] not in by_name:
            order.append(p["name"])
        by_name[p["name"]] = p
    return [by_name[n] for n in order]


def upsert_profile(
    profiles: list[dict],
    name: str,
    language: str,
    prompt: str,
    original_name: str | None = None,
) -> tuple[list[dict], str | None]:
    """Add a new profile or edit an existing one (the Settings-window editor).

    `original_name` set → edit that profile in place (lets the user rename it);
    None → create a new one. Returns (profiles, error); error is a short message
    on bad input or a name clash, in which case `profiles` is returned unchanged.
    """
    clean = validate_profile({"name": name, "language": language, "prompt": prompt})
    if clean is None:
        return profiles, "Name and prompt can't be empty."

    out = [dict(p) for p in profiles]
    names = {p["name"] for p in out}

    if original_name is None:
        if clean["name"] in names:
            return profiles, f"A profile named “{clean['name']}” already exists."
        out.append(clean)
        return out, None

    # Edit: locate the original; a rename must not collide with a *different* one.
    idx = next((i for i, p in enumerate(out) if p["name"] == original_name), None)
    if idx is None:
        return profiles, f"Profile “{original_name}” not found."
    if clean["name"] != original_name and clean["name"] in names:
        return profiles, f"A profile named “{clean['name']}” already exists."
    out[idx] = clean
    return out, None


def remove_profile(profiles: list[dict], name: str) -> list[dict]:
    """Drop the profile with this name (no-op if absent)."""
    return [p for p in profiles if p.get("name") != name]


def regroup_active(profiles: list[dict], member_names: list[str]) -> dict[str, list[str]]:
    """Group the given profile names by their decode language → an
    active_profiles dict ({lang: [names]}). Names with no matching profile are
    dropped. Used to activate a profile *set* as the entire selection at once,
    replacing whatever was on before."""
    by_name = {p.get("name"): p for p in profiles}
    out: dict[str, list[str]] = {}
    for n in member_names:
        p = by_name.get(n)
        if p is not None:
            out.setdefault(p.get("language", "uk"), []).append(n)
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
