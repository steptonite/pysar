#!/bin/bash
# Писар — збирач діагностики для Каті.
# НІЧОГО не змінює і не видаляє: тільки читає й кладе архів на робочий стіл.
# Аудіо НЕ копіюється (важке й приватне) — лише його розміри та тривалість.
cd "$(dirname "$0")" 2>/dev/null
P="$HOME/Library/Application Support/Pysar"
STAMP="$(date +%Y-%m-%d_%H-%M)"
OUT="$HOME/Desktop/Писар-діагностика-$STAMP"
mkdir -p "$OUT"

{
echo "════ ПИСАР — ДІАГНОСТИКА $STAMP ════"
echo "мак: $(sw_vers -productName) $(sw_vers -productVersion) · $(uname -m)"
echo

echo "──── 1. ЯКА ВЕРСІЯ ЗАСТОСУНКУ ────"
APP=""
for c in "/Applications/Pysar.app" "$HOME/Applications/Pysar.app" "/Applications/Писар.app"; do
  [ -d "$c" ] && APP="$c" && break
done
if [ -n "$APP" ]; then
  echo "застосунок: $APP"
  echo "версія:     $(defaults read "$APP/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo '—')"
  echo "зібрано:    $(stat -f '%Sm' -t '%Y-%m-%d %H:%M' "$APP" 2>/dev/null)"
  echo "запущений:  $(pgrep -f 'Pysar|Писар' >/dev/null && echo так || echo ні)"
else
  echo "🔴 Pysar.app не знайдено у /Applications"
fi
echo

echo "──── 2. ЛОГ (тут видно, чому розділення голосів не стартувало) ────"
if [ -f "$P/pysar.log" ]; then
  cp "$P/pysar.log" "$OUT/pysar.log" 2>/dev/null
  [ -f "$P/pysar.log.1" ] && cp "$P/pysar.log.1" "$OUT/pysar.log.1" 2>/dev/null
  echo "лог скопійовано ($(wc -c <"$P/pysar.log" | tr -d ' ') байт)"
  echo "— рядки про розділення голосів і помилки:"
  grep -n "diariz\|спікер\|speaker\|Traceback\|Error\|error" "$P/pysar.log" | tail -25

  echo
  echo "— 🌡 СТОРОЖ ПЕРЕГРІВУ (чи він паузив роботу і на яких градусах):"
  if grep -q "🌡" "$P/pysar.log"; then
    grep -n "🌡" "$P/pysar.log" | tail -25
  else
    echo "   сторож у лозі не озивався жодного разу"
  fi

  echo
  echo "— 🔁 ОБРИВИ ЗАХОПЛЕННЯ ЗВУКУ (з контекстом: що було за мить до обриву):"
  N=$(grep -c "capture stopped\|capture recover" "$P/pysar.log" 2>/dev/null || echo 0)
  echo "   всього обривів/перезапусків у лозі: $N"
  grep -n -B4 "capture stopped" "$P/pysar.log" | tail -40

  echo
  echo "— 🧹 ЩО ВИКИНУВ ФІЛЬТР ЕХО (дублі динаміків у мікрофоні):"
  echo "   спрацювань усього: $(grep -c "meeting filter" "$P/pysar.log" 2>/dev/null || echo 0)"
  grep -o "meeting filter \[[^]]*\]" "$P/pysar.log" 2>/dev/null | sort | uniq -c | sort -rn | head
  echo "   останні 12 викинутих рядків:"
  grep -n "meeting filter" "$P/pysar.log" | tail -12
else
  echo "🔴 лога немає: $P/pysar.log"
fi
echo

echo "──── 3. НАЛАШТУВАННЯ ────"
if [ -f "$P/settings.json" ]; then
  cp "$P/settings.json" "$OUT/settings.json"
  echo "скопійовано. Ключове:"
  grep -o '"\(diar[a-z_]*\|meeting[a-zA-Z_]*\|thermal[a-z_]*\|transcripts_dir\|mode\)"[[:space:]]*:[[:space:]]*[^,}]*' "$P/settings.json"
else
  echo "🔴 settings.json немає"
fi
echo

echo "──── 4. МОДЕЛІ РОЗДІЛЕННЯ ГОЛОСІВ ────"
if [ -d "$P/models/diar" ]; then
  find "$P/models/diar" -type f -exec ls -lh {} \; | awk '{print $5"\t"$9}'
else
  echo "🔴 теки моделей немає — розділення голосів НЕ встановлене"
fi
echo

echo "──── 5. ОСТАННІ ЗАПИСИ І ЩО ПОРУЧ ІЗ НИМИ ────"
TDIR="$P/transcripts"
if [ -f "$P/settings.json" ]; then
  CUSTOM=$(grep -o '"transcripts_dir"[[:space:]]*:[[:space:]]*"[^"]*"' "$P/settings.json" | sed 's/.*:[[:space:]]*"//; s/"$//')
  [ -n "$CUSTOM" ] && TDIR="$CUSTOM"
fi
echo "тека транскриптів: $TDIR"
ls -lt "$TDIR" 2>/dev/null | head -25
echo
echo "— ⏱ РОЗМІТКА ЧАСУ останнього запису (сайдкар .сегменти.jsonl):"
SIDE=$(ls -t "$TDIR"/*.сегменти.jsonl 2>/dev/null | head -1)
if [ -n "$SIDE" ]; then
  echo "файл: $SIDE  ($(wc -l <"$SIDE" | tr -d ' ') рядків)"
  cp "$SIDE" "$OUT/$(basename "$SIDE")"
  head -6 "$SIDE"
else
  echo "🔴 сайдкара немає — без нього розділення голосів не має де різати"
fi
echo
echo "— останній транскрипт цілком:"
LAST=$(ls -t "$TDIR"/*.md 2>/dev/null | head -1)
[ -n "$LAST" ] && cp "$LAST" "$OUT/$(basename "$LAST")" && echo "скопійовано: $(basename "$LAST")"
echo

echo "— 🔑 ЧИ ВЗАГАЛІ БУЛО РОЗДІЛЕННЯ ГОЛОСІВ:"
echo "   (з 11.09 розділення вписується В САМ транскрипт, а сира версія"
echo "    віспера лягає в теку whisper-originals. Є там копія — розділення"
echo "    відпрацювало; немає — не запускалось узагалі.)"
if [ -d "$P/whisper-originals" ]; then
  ls -lt "$P/whisper-originals" | head -15
  ORIG=$(ls -t "$P/whisper-originals"/*.md 2>/dev/null | head -1)
  if [ -n "$ORIG" ]; then
    mkdir -p "$OUT/whisper-originals"
    cp "$ORIG" "$OUT/whisper-originals/" 2>/dev/null
    echo "   ↳ скопійовано для порівняння: $(basename "$ORIG")"
  fi
else
  echo "   🔴 теки whisper-originals НЕМАЄ ⇒ розділення голосів жодного разу не доходило до кінця"
fi
echo
echo "──── 6. ДОРІЖКИ ЗУСТРІЧІ (мікрофон проти звуку з мака) ────"
echo "аудіо НЕ копіюється — лише розміри:"
find "$P" -name "*.wav" -o -name "*.ogg" 2>/dev/null | head -20 | while read -r f; do
  printf '%8s  %s\n' "$(du -h "$f" | cut -f1)" "${f#$P/}"
done
echo

echo "──── 7. АУДІО-ПРИСТРОЇ (чому мікрофон пише те саме, що й мак) ────"
system_profiler SPAudioDataType 2>/dev/null | sed -n '1,60p'
echo

echo "──── 8. ПОВНЕ ДЕРЕВО ТЕКИ ЗАСТОСУНКУ ────"
find "$P" -maxdepth 3 2>/dev/null | sed "s|$P|…/Pysar|" | head -60
echo
echo "════ ГОТОВО ════"
} > "$OUT/00-звіт.txt" 2>&1

cd "$HOME/Desktop" && zip -qr "Писар-діагностика-$STAMP.zip" "$(basename "$OUT")" && rm -rf "$OUT"
open -R "$HOME/Desktop/Писар-діагностика-$STAMP.zip"
echo ""
echo "  ✅ Готово."
echo "  Архів «Писар-діагностика-$STAMP.zip» лежить на робочому столі — скинь його Льоші."
echo "  Аудіо всередині немає, тільки текст і службові дані."
echo ""
echo "  (Вікно можна закрити: ⌘W)"
