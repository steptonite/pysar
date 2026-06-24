"""UI string tables for the two interface languages (uk default, en).

Only chrome/labels live here — never the dictated text. Keys are flat, dotted by
surface (``sec.audio``, ``hotkey.label`` …). ``strings(lang)`` returns the dict
the Settings WebView embeds as ``state["t"]`` and reads by key; ``t(lang, key,
**kw)`` is the Python-side lookup (tray menu, notices, status line) with optional
``str.format`` interpolation. Anything missing in a language falls back to uk.

Language *names* themselves (the endonyms in config.MODE_LABELS) are intentionally
NOT here — a language picker names each language in its own script regardless of
the UI language.
"""

UI_LANGS = ("uk", "en")

_UK = {
    # ── Settings · main screen ──────────────────────────────────────────────
    "settings": "Налаштування",
    "sec.audio": "Аудіо",
    "sec.recordings": "Записи",
    "sec.dictation": "Диктовка",
    "sec.system": "Система",
    "mic.label": "Мікрофон",
    "mic.help": "Пристрій вводу для диктовки",
    "save.label": "Зберігати записи",
    "save.help": "Тримати кожен WAV на диску, щоб невдалий прогін можна було відновити",
    "keep.label": "Тримати останні",
    "keep.help": "Старіші записи видаляються автоматично",
    "keep.unit": "записів",
    "folder.label": "Папка зберігання",
    "folder.open": "Відкрити",
    "dict.mode": "Режим диктовки",
    "dict.modeHelp": "Звичайний — розпізнає весь запис у кінці; пришвидшений — друкує по реченнях під час диктовки",
    "dict.batch": "Звичайний",
    "dict.streaming": "Пришвидшений (по реченнях)",
    "profiles.nav": "Профілі мовлення",
    "profiles.navHelp": "Праймінг промпту для кращого розпізнавання",
    "hotkeys.nav": "Хоткеї",
    "hotkeys.navHelp": "Диктовка, мова та набори профілів",
    "hkscreen.title": "Хоткеї",
    "hotkey.label": "Хоткей диктовки",
    "hotkey.help": "Тап — старт, ще тап — стоп",
    "hotkey.change": "Змінити…",
    "hotkey.press": "Натисни клавіші…",
    "langhk.label": "Хоткеї мови",
    "langhk.help": (
        "Перемикай мову розпізнавання будь-де, не відкриваючи меню. Будь-яка клавіша "
        "з утриманим ⌃⌥⌘⇧, або клавіша, що сама нічого не друкує (Caps Lock, "
        "модифікатор, F-клавіша)."
    ),
    "langhk.assign": "Призначити…",
    "langhk.change": "Змінити…",
    "langhk.remove": "Прибрати хоткей",
    "theme.label": "Оформлення",
    "theme.help": "Тема вікна — за macOS, або примусово світла / темна",
    "theme.auto": "Авто",
    "theme.light": "Світла",
    "theme.dark": "Темна",
    "applang.label": "Мова застосунку",
    "applang.help": "Мова інтерфейсу та скопійованого AI-промпту",
    "login.label": "Запуск при вході",
    "login.help": "Запускати Pysar автоматично при вході в систему",
    # ── Settings · profiles screen ──────────────────────────────────────────
    "back": "Налаштування",
    "pscreen.title": "Профілі мовлення",
    "pscreen.ai": (
        "Профіль — одне природне речення, що націлює whisper на твою справжню "
        "лексику: імена, жаргон, англійські терміни всередині української. Вмикай "
        "ті, що пасують до того, що диктуєш; шкала показує, скільки бюджету промпту "
        "whisper займає кожна мова."
    ),
    "psec.ai": "Збери профілі своїм AI",
    "copyai.label": "Скопіювати AI-промпт",
    "copyai.help": (
        "Копіює готовий промпт. Встав його у ChatGPT / Gemini / Claude — він знає "
        "тебе з твоїх чатів і напише профілі як JSON, який ти потім імпортуєш нижче."
    ),
    "copyai.btn": "Копіювати",
    "copyai.done": "Промпт скопійовано в буфер.",
    "import.label": "Імпорт профілів",
    "import.help": "Встав JSON, який повернув твій AI",
    "import.btn": "Імпорт…",
    "import.paste": "Встав JSON сюди",
    "import.cancel": "Скасувати",
    "import.do": "Імпортувати",
    "form.name": "Назва",
    "form.namePh": "напр. Розробка / код",
    "form.lang": "Мова",
    "form.prompt": "Промпт — одне природне речення, що праймить розпізнавання",
    "form.cancel": "Скасувати",
    "form.save": "Зберегти",
    "form.tokens": "токенів",
    "prow.edit": "Редагувати",
    "prow.delete": "Видалити",
    "prow.confirm": "Підтвердити?",
    "prow.add": "+ Додати профіль",
    "prof.count": "Профілі: {n}",
    # ── Settings · profile sets ─────────────────────────────────────────────
    "psec.sets": "Набори профілів",
    "sets.help": "Натисни ⌃⌥‹цифру›, щоб увімкнути цілий набір профілів за раз.",
    "sets.none": "Ще немає наборів — створи набір, щоб вмикати профілі однією клавішею.",
    "set.activate": "Увімкнути",
    "set.active": "Активний",
    "set.add": "+ Додати набір",
    "set.namePh": "напр. Кодинг",
    "set.members": "Профілі в наборі",
    "set.empty": "(порожній набір)",
    "psec.sethk": "Хоткеї наборів",
    "setshk.help": "Кожен набір вмикається через ⌃⌥‹цифру›. Самі набори — у Профілях мовлення.",
    "sethk.reset": "Скинути до типового",
    # ── Settings · notices (pushed from Python) ─────────────────────────────
    "notice.saved": "Профіль збережено.",
    "notice.deleted": "Видалено «{name}».",
    "notice.importFail": "Імпорт не вдався: {err}",
    "notice.imported": "Імпортовано профілів: {count}",
    # ── Tray menu ───────────────────────────────────────────────────────────
    "tray.ready": "Готово",
    "tray.hotkey": "Хоткей: {label}",
    "tray.languages": "🌍 Мови",
    "tray.profiles": "👤 Профілі",
    "tray.settings": "⚙️ Налаштування…",
    "tray.editInSettings": "Редагувати в Налаштуваннях…",
    "tray.noProfiles": "(немає профілів для {lang})",
    "tray.tokens": "Токени ({lang}): {used}/{budget}{warn}",
    "tray.overBudget": "  ⚠️ понад бюджет",
    "tray.quit": "Вийти",
    "notif.inBufferTitle": "Текст у буфері",
    "notif.inBufferMsg": "Не вдалося вставити у вікно — натисни ⌘V вручну",
    "notif.cantOpenSettings": "Не вдалося відкрити Налаштування",
    "notif.cantSaveProfile": "Не вдалося зберегти профіль",
    "notif.cantLogin": "Не вдалося ввімкнути Запуск при вході",
    "notif.cantLoginBody": (
        "Додай вручну: System Settings → General → Login Items "
        "(працює лише з встановленого застосунку, не з терміналу)."
    ),
    # ── Status line ─────────────────────────────────────────────────────────
    "st.recording": "● Запис…",
    "st.transcribing": "⏳ Розпізнаю…",
    "st.tooShort": "⚠️ Закоротко",
    "st.silence": "⚠️ Тиша",
    "st.mode": "Мова: {label}",
    "st.saving": "💾 Зберігаю записи",
    "st.memoryOnly": "Записи: лише памʼять",
    "st.keepLast": "Тримаю останні {n} записів",
    "st.profileOn": "👤 {name}: увімк",
    "st.profileOff": "👤 {name}: вимк",
    "st.mic": "🎤 {name}",
    "st.defaultMic": "🎤 Дефолтний мікрофон",
    "st.loginOn": "🚀 Запуск при вході: увімк",
    "st.loginOff": "Запуск при вході: вимк",
    "st.whisperDown": "⚠️ Whisper не запущено — `make whisper`",
    "st.whisperStarting": "⏳ Піднімаю Whisper-сервер… (говори, модель гріється)",
    "st.needMod": "⚠️ Додай ⌃⌥⌘⇧ — ця клавіша друкуватиме сама",
    "st.hotkeySet": "Хоткей: {label}",
    "st.cleared": "Прибрано хоткей для {label}",
    "st.setOn": "🎛 Набір: {name}",
    "st.inBuffer": "📋 У буфері — ⌘V вручну: {preview}",
    "st.okIn": "✓ → {app} ({dur:.1f}с): {preview}",
    "st.streaming": "✍️ {preview}",
    "st.streamDone": "✓ → {app}: надруковано {n} символів",
    "st.micError": "⚠️ Мікрофон недоступний",
    "st.dictBatch": "Диктовка: звичайний режим",
    "st.dictStreaming": "Диктовка: пришвидшений режим",
    "notif.micErrorTitle": "Мікрофон недоступний",
    "notif.micErrorMsg": "Не вдалося відкрити мікрофон — спробуй ще раз",
    "st.buffering": "📋 Нема активного поля — пишу в буфер ({n})",
    "st.bufferDone": "📋 {n} реч. у буфері — встав ⌘V",
    "notif.bufferTitle": "Текст у буфері обміну",
    "notif.bufferMsg": "Поле вводу зникло — {n} реч. лежить у буфері, встав ⌘V",
    "notif.bufferModeTitle": "Нема активного поля вводу",
    "notif.bufferModeMsg": "Друк зупинено — весь текст збираю в буфер, з'явиться разом після Стоп (⌘V)",
    "notif.serverDownTitle": "Сервер розпізнавання впав",
    "notif.serverDownMsg": "Whisper не відповідає — мова зараз НЕ записується. Зупини й перезапусти Pysar.",
    # Floating status overlay (streaming).
    "hud.listening": "Слухаю…",
    "hud.recognizing": "Розпізнаю…",
    "hud.buffering": "Нема поля → буфер ({n})",
    "hud.serverDown": "Сервер недоступний",
}

_EN = {
    # ── Settings · main screen ──────────────────────────────────────────────
    "settings": "Settings",
    "sec.audio": "Audio",
    "sec.recordings": "Recordings",
    "sec.dictation": "Dictation",
    "sec.system": "System",
    "mic.label": "Microphone",
    "mic.help": "Input device used for dictation",
    "save.label": "Save recordings",
    "save.help": "Keep each WAV on disk so a failed run is recoverable",
    "keep.label": "Keep last",
    "keep.help": "Older recordings are deleted automatically",
    "keep.unit": "recordings",
    "folder.label": "Storage folder",
    "folder.open": "Open",
    "dict.mode": "Dictation mode",
    "dict.modeHelp": "Batch transcribes the whole clip at the end; streaming types each sentence as you speak",
    "dict.batch": "Batch",
    "dict.streaming": "Streaming (by sentence)",
    "profiles.nav": "Speech profiles",
    "profiles.navHelp": "Prompt-priming for better recognition",
    "hotkeys.nav": "Hotkeys",
    "hotkeys.navHelp": "Dictation, language and profile sets",
    "hkscreen.title": "Hotkeys",
    "hotkey.label": "Dictation hotkey",
    "hotkey.help": "Tap to start, tap again to stop",
    "hotkey.change": "Change…",
    "hotkey.press": "Press keys…",
    "langhk.label": "Language shortcuts",
    "langhk.help": (
        "Switch the decode language anywhere, without opening the menu. Use any key "
        "with ⌃⌥⌘⇧ held, or a key that types nothing on its own (Caps Lock, a "
        "modifier, an F-key)."
    ),
    "langhk.assign": "Assign…",
    "langhk.change": "Change…",
    "langhk.remove": "Remove shortcut",
    "theme.label": "Appearance",
    "theme.help": "Window theme — follow macOS, or force light / dark",
    "theme.auto": "Automatic",
    "theme.light": "Light",
    "theme.dark": "Dark",
    "applang.label": "App language",
    "applang.help": "Language of the interface and the copied AI prompt",
    "login.label": "Launch at login",
    "login.help": "Start Pysar automatically when you log in",
    # ── Settings · profiles screen ──────────────────────────────────────────
    "back": "Settings",
    "pscreen.title": "Speech profiles",
    "pscreen.ai": (
        "A profile is one natural sentence that primes whisper toward your real "
        "vocabulary — names, jargon, English terms inside Ukrainian. Toggle the ones "
        "that fit what you're dictating; the meter shows how much of whisper's prompt "
        "budget each language uses."
    ),
    "psec.ai": "Build profiles with your AI",
    "copyai.label": "Copy AI prompt",
    "copyai.help": (
        "Copies a ready prompt. Paste it into ChatGPT / Gemini / Claude — it knows "
        "you from your chats and writes profiles as JSON, which you then import below."
    ),
    "copyai.btn": "Copy",
    "copyai.done": "Prompt copied to clipboard.",
    "import.label": "Import profiles",
    "import.help": "Paste the JSON your AI returned",
    "import.btn": "Import…",
    "import.paste": "Paste JSON here",
    "import.cancel": "Cancel",
    "import.do": "Import",
    "form.name": "Name",
    "form.namePh": "e.g. Розробка / код",
    "form.lang": "Language",
    "form.prompt": "Prompt — one natural sentence that primes recognition",
    "form.cancel": "Cancel",
    "form.save": "Save",
    "form.tokens": "tokens",
    "prow.edit": "Edit",
    "prow.delete": "Delete",
    "prow.confirm": "Confirm?",
    "prow.add": "+ Add profile",
    "prof.count": "Profiles: {n}",
    # ── Settings · profile sets ─────────────────────────────────────────────
    "psec.sets": "Profile sets",
    "sets.help": "Press ⌃⌥‹digit› to switch a whole set of profiles on at once.",
    "sets.none": "No sets yet — create one to switch profiles on with a single key.",
    "set.activate": "Activate",
    "set.active": "Active",
    "set.add": "+ Add set",
    "set.namePh": "e.g. Coding",
    "set.members": "Profiles in set",
    "set.empty": "(empty set)",
    "psec.sethk": "Profile-set shortcuts",
    "setshk.help": "Each set is switched on with ⌃⌥‹digit›. Manage the sets in Speech profiles.",
    "sethk.reset": "Reset to default",
    # ── Settings · notices (pushed from Python) ─────────────────────────────
    "notice.saved": "Profile saved.",
    "notice.deleted": "Deleted “{name}”.",
    "notice.importFail": "Import failed: {err}",
    "notice.imported": "Imported {count} profile(s).",
    # ── Tray menu ───────────────────────────────────────────────────────────
    "tray.ready": "Ready",
    "tray.hotkey": "Hotkey: {label}",
    "tray.languages": "🌍 Languages",
    "tray.profiles": "👤 Profiles",
    "tray.settings": "⚙️ Settings…",
    "tray.editInSettings": "Edit in Settings…",
    "tray.noProfiles": "(no profiles for {lang})",
    "tray.tokens": "Tokens ({lang}): {used}/{budget}{warn}",
    "tray.overBudget": "  ⚠️ over budget",
    "tray.quit": "Quit",
    "notif.inBufferTitle": "Text on clipboard",
    "notif.inBufferMsg": "Couldn't paste into the window — press ⌘V manually",
    "notif.cantOpenSettings": "Couldn't open Settings",
    "notif.cantSaveProfile": "Couldn't save profile",
    "notif.cantLogin": "Couldn't enable Launch at login",
    "notif.cantLoginBody": (
        "Add it manually: System Settings → General → Login Items "
        "(only works from the installed app, not a terminal run)."
    ),
    # ── Status line ─────────────────────────────────────────────────────────
    "st.recording": "● Recording…",
    "st.transcribing": "⏳ Transcribing…",
    "st.tooShort": "⚠️ Too short",
    "st.silence": "⚠️ Silence",
    "st.mode": "Mode: {label}",
    "st.saving": "💾 Saving recordings",
    "st.memoryOnly": "Recordings: memory only",
    "st.keepLast": "Keeping last {n} recordings",
    "st.profileOn": "👤 {name}: on",
    "st.profileOff": "👤 {name}: off",
    "st.mic": "🎤 {name}",
    "st.defaultMic": "🎤 Default mic",
    "st.loginOn": "🚀 Launch at login: on",
    "st.loginOff": "Launch at login: off",
    "st.whisperDown": "⚠️ Whisper not running — `make whisper`",
    "st.whisperStarting": "⏳ Starting Whisper server… (keep talking, model is loading)",
    "st.needMod": "⚠️ Add ⌃⌥⌘⇧ — that key would type on its own",
    "st.hotkeySet": "Hotkey set: {label}",
    "st.cleared": "Cleared shortcut for {label}",
    "st.setOn": "🎛 Set: {name}",
    "st.inBuffer": "📋 On clipboard — ⌘V manually: {preview}",
    "st.okIn": "✓ → {app} ({dur:.1f}s): {preview}",
    "st.streaming": "✍️ {preview}",
    "st.streamDone": "✓ → {app}: typed {n} chars",
    "st.micError": "⚠️ Microphone unavailable",
    "st.dictBatch": "Dictation: batch mode",
    "st.dictStreaming": "Dictation: streaming mode",
    "notif.micErrorTitle": "Microphone unavailable",
    "notif.micErrorMsg": "Couldn't open the microphone — try again",
    "st.buffering": "📋 No active field — buffering ({n})",
    "st.bufferDone": "📋 {n} sentence(s) on clipboard — paste with ⌘V",
    "notif.bufferTitle": "Text on clipboard",
    "notif.bufferMsg": "Input field was gone — {n} sentence(s) on the clipboard, paste with ⌘V",
    "notif.bufferModeTitle": "No active input field",
    "notif.bufferModeMsg": "Typing paused — collecting the rest into the buffer; it appears together after Stop (⌘V)",
    "notif.serverDownTitle": "Recognition server crashed",
    "notif.serverDownMsg": "Whisper isn't responding — speech is NOT being captured right now. Stop and relaunch Pysar.",
    # Floating status overlay (streaming).
    "hud.listening": "Listening…",
    "hud.recognizing": "Recognizing…",
    "hud.buffering": "No field → buffer ({n})",
    "hud.serverDown": "Server unavailable",
}

_STRINGS = {"uk": _UK, "en": _EN}


def strings(lang: str) -> dict:
    """The full string table for a UI language (falls back to uk)."""
    return _STRINGS.get(lang, _UK)


def t(ui_lang: str, key: str, **kw) -> str:
    """One localized string by key, with optional str.format interpolation.
    Missing keys fall back to uk, then to the raw key (so nothing ever blanks).

    The UI-language arg is `ui_lang` (not `lang`) so a ``{lang}`` format field —
    e.g. tray.tokens / tray.noProfiles — can be passed as a kwarg without clashing
    with this positional parameter."""
    table = _STRINGS.get(ui_lang, _UK)
    s = table.get(key) or _UK.get(key, key)
    return s.format(**kw) if kw else s
