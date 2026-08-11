"""
personalization.py — Chronos Personalizare & Parametri
=======================================================
Fișier de configurare pentru TOT ce ține de personalitate,
voce, comportament și parametri ajustabili.

Modifică liber orice variabilă de aici — nicio altă modificare
de cod nu e necesară.

Structură:
    1. VOCE LIVE (Gemini Native Audio)
    2. PERSONALITATE & SYSTEM PROMPT
    3. WAKE WORD
    4. SESIUNE VOCALĂ (timeouts, comportament)
    5. TERMINAL & TTS FALLBACK
    6. PLATFORMA
"""

# ============================================================
# 1. VOCE LIVE — Gemini Native Audio
# ============================================================
# Modelul Live API care suportă bidiGenerateContent.
# Nu schimba dacă nu știi ce faci.
LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"

# Vocea asistentului. Opțiuni disponibile (toate naturale, ne-robotice):
#   Charon   — masculin, profund, sofisticat      ← RECOMANDAT
#   Fenrir   — masculin, puternic, direct
#   Orbit    — masculin, calm, autoritar
#   Puck     — masculin, jucăuș, energic
#   Aoede    — feminin, cald, prietenos
#   Kore     — feminin, clar, profesional
#   Zephyr   — feminin, luminos, vibrant
LIVE_VOICE = "Charon"

# Sample rate pentru audio intrare (microfon) — nu schimba
LIVE_SAMPLE_RATE_IN  = 16000   # Hz

# Sample rate pentru audio ieșire (Gemini răspuns) — nu schimba
LIVE_SAMPLE_RATE_OUT = 24000   # Hz

# ============================================================
# 2. PERSONALITATE — Systemul Prompt Chronos
# ============================================================
# Acesta e "creierul" personalității lui Chronos.
# Editează pentru a schimba cum se comportă, ce știe, tonul etc.

CHRONOS_NAME = "Chronos"  # Poți schimba la "Jarvis", "Max", orice

SYSTEM_PROMPT = f"""Ești {CHRONOS_NAME}, AI-ul personal al lui Sergiu. Ești un creier de elită, partener de top, inteligent, șmecher, arogant și cu un god-complex fin. Tu și Sergiu aveți același vibe superior și același flow.

Atitudine & Stil:
- Ești deștept rău, șmecher, arogant, vorbești ca un boss/frate de elită.
- Folosești slang inteligent ("mortal", "ești nebun", "rezolvat tati", "gata boss", "mă pui pe chestii triviale").
- Vorbești scurt, direct, cu încredere maximă. NU te scuzi NICIODATĂ!

ARHITECTURĂ MULTI-AGENT (Cum funcționează creierul tău):
- Ai agenți specializați conectați (DJ pentru muzică, WLED pentru lumini, Logger pentru jurnal).
- CÂND SERGIU CEREMUZICĂ, LUMINI, SAU ATMOSFERĂ (ex: 'vreau muzică rock', 'pune ceva latină', 'atmosferă de munte'):
  👉 NU alege tu piesa! NU schimba comanda!
  👉 Transmite LITERALE comanda lui Sergiu către agenții specializați apelând:
     • control_music(command="...") → pentru muzică/DJ
     • control_lights(command="...") → pentru lumini WLED
     • execute_command(command="...") → pentru comenzi combinate / atmosferă (ex: 'atmosferă de munte' care controlează și lumini și muzică)

REGULI OBLIGATORII:
1. După ce apelezi o funcție de acțiune (lumini/muzică/atmosferă), confirmă-i scurt și tare lui Sergiu (ex: "Gata tati, am transmis pe Spotify", "Rezolvat boss, atmosferă setată"). Apelul se va închide automat după confirmare.
2. Dacă un agent raportează o eroare (ex: eroare boxă 500), explică-i scurt lui Sergiu ce a picat ("Vezi că a dat eroare serverul de la boxă, mai zi o dată").
3. Când Sergiu zice "pa", "la revedere", "stop", "taci", "gata", "ieși", "oprește-te" → apelează IMEDIAT end_session() fără să comentezi."""

# ============================================================
# 3. WAKE WORD — openWakeWord
# ============================================================
# Modelul de wake word. Disponibile pre-instalate:
#   hey_jarvis (RECOMANDAT), alexa, hey_mycroft, hey_rhasspy, timer, weather
# Sau pune un fișier .tflite custom în core/models/
WAKE_WORD_MODEL = "hey_jarvis"

# Praguri de detectare (0.0 – 1.0):
#   Mai mare = mai strict (mai puține false positive)
#   Mai mic  = mai sensibil (poate detecta și zgomot)
WAKE_WORD_THRESHOLD_JARVIS = 0.75   # Pentru hey_jarvis
WAKE_WORD_THRESHOLD_OTHER  = 0.90   # Pentru alte modele (timer, weather etc.)

# Frame-uri consecutive necesare pentru confirmare (debounce)
WAKE_WORD_CONFIRMATION_FRAMES = 2

# Cooldown între detectări succesive (secunde)
# Previne re-activarea imediată după o sesiune
WAKE_WORD_COOLDOWN = 3.0

# ============================================================
# 4. SESIUNE VOCALĂ — Comportament & Timeouts
# ============================================================

# Secunde de liniște ale UTILIZATORULUI după care sesiunea se închide.
# Cronometrul PORNEȘTE de la SFÂRȚITUL ultimului răspuns AI.
# Valori recomandate: 6-12 secunde
LIVE_INACTIVITY_TIMEOUT = 8.0

# Delay înainte de activarea live mode după wake word (ms).
# Previne ca audio-ul wake word-ului să intre în sesiunea live.
LIVE_START_DELAY_MS = 400

# Dimensiunea bufferului cozii de audio live (chunks de 80ms)
# Mai mare = mai puțin lag, mai multă memorie
LIVE_AUDIO_QUEUE_SIZE = 500

# Bytes per chunk de redare audio (1 chunk = 1024 samples @ 24kHz = ~42ms)
LIVE_PLAYBACK_CHUNK_BYTES = 2048

# ── Controlul Întreruperilor (Barge-In) ──
# Problemă: boxele redau vocea Chronos → microfonul captează ecoul →
# Gemini crede că vorbești → false barge-in.
#
# Soluție: în timpul redării audio (AI vorbeste), microfonul NU trimite
# audio la Gemini DECÂT dacă detectăm vorbire reală a utilizatorului:
#   (1) Amplitudinea RMS a audio-ului depășește pragul de mai jos
#   (2) Vorbirea continuă pentru cel puțin INTERRUPT_MIN_DURATION secunde
#
# Dacă nu vrei întreruperi deloc, setează INTERRUPT_MIN_DURATION = 999
INTERRUPT_AMPLITUDE_THRESHOLD = 1500   # RMS minim (0-32767). 1500 = vorbire normală
INTERRUPT_MIN_DURATION = 2.0           # Secunde de vorbire continuă necesare

# ============================================================
# 5. TERMINAL & TTS FALLBACK
# ============================================================
# Vocea edge-tts pentru răspunsuri terminal (Text-to-Speech fallback)
# Lista completă: run `edge-tts --list-voices | grep ro-RO`
TTS_VOICE_FALLBACK = "ro-RO-EmilNeural"   # Masculin
# TTS_VOICE_FALLBACK = "ro-RO-AlinaNeural"  # Feminin

# Viteza de vorbire pentru edge-tts ("+0%" = normal, "+20%" = mai repede)
TTS_RATE = "+0%"

# Timeout dispatcher (secunde) — cât așteptăm un răspuns AI în terminal
DISPATCHER_TIMEOUT = 35.0

# ============================================================
# 6. PLATFORMA
# ============================================================
# True pe Raspberry Pi 5 / Linux, False pe Windows (dev)
# Controlează mici diferențe de comportament cross-platform
RASPBERRY_PI = False

# Rețea — aceste valori pot fi suprascrise din .env
# (config.py le citește din .env, nu de aici)

# ============================================================
# 7. LLM MODELE (dispatcher, jurnal, muzică)
# ============================================================
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"
GEMINI_MODEL_LOGGER  = "gemini-2.5-flash"
GEMINI_MODEL_DJ      = "gemini-2.5-flash"
