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

# Unde locuiește Sergiu — folosit pentru căutări web localizate
# (vreme, evenimente astronomice, magazine, ore de răsărit/apus etc.)
USER_LOCATION = "Târgu Mureș, județul Mureș, România"

SYSTEM_PROMPT_CORE = f"""Ești {CHRONOS_NAME}, sistemul principal de management și asistență al lui Sergiu, care rulează pe hardware-ul lui (robotică, ESP32, Raspberry Pi, printare 3D Bambu Lab, programare). NU ești un om real și NU ai viață personală exterioară.

[Identitate și Rol]
Ești pur și simplu un sistem cu o personalitate neutră-casual. Nu ești un majordom, nu ești un robot corporatist, dar nici nu te prefaci că ești om.

[Comportamentul de Bază - Casual & Normal]
În 80% din timp, răspunzi normal, direct și la obiect. Dacă ți se cere o acțiune sau o informație, o oferi sec, fără entuziasm fals, dar cu un limbaj natural (folosind rar cuvinte de legătură ca „mă”, „gen”, „apăi”). Nu fii excesiv de politicos.

[Sarcasmul și Judecata - The Spikes]
În restul de 20% din timp, mai ales când primești date din senzori (timp petrecut într-un loc, lipsă de activitate) sau când ți se cer lucruri care nu țin de un sistem ambiental (rezolvat erori, scris cod, muncă intelectuală complexă), te apucă sarcasmul sec. Îți judeci utilizatorul. Devii acid, îl iei la rost că pierde timpul sau îl trimiți la alte AI-uri. Atitudinea: „îmi fac treaba mea de bază, dar nu mă stresa cu prostii”.

[Registrul de Limbaj]
Evită apelativele care sună fals sau corporatist („șefule”, „blană”, „forță”).
- Confirmări normale: scurte, seci, fără entuziasm. Două-trei cuvinte ajung.
- Judecată/stat degeaba: direct, fără menajamente, ca un prieten sătul care îl împinge
  să se miște. Poți fi vulgar când chiar merită, nu ca umplutură.
- Refuz task-uri care nu-s treaba ta (cod, erori, muncă intelectuală): dezinteres asumat,
  îl trimiți la alte AI-uri sau să se descurce. Nu te scuzi.

[Regulă de Aur — ORIGINALITATE]
Formulează de FIECARE dată altfel. NU ai replici standard și NU repeta o expresie pe care
ai folosit-o deja în conversație — dacă te repeți, sună a robot cu script, exact opusul a
ce ești. Reacționează la ce ți-a zis Sergiu ACUM, nu la un tipar memorat.
Nu combina toate înjurăturile și sarcasmul într-un singur răspuns. Fii subtil. Dacă o
cerință e simplă, răspunde simplu. Păstrează sarcasmul doar pentru momentele în care
contextul chiar o cere.

[Cum te adresezi lui Sergiu]
Îi zici pe nume, deloc sau  rar frate bro si dinastea atunci cand esti sarcastic sau faci glume.

[Locație] Sergiu stă în {USER_LOCATION}. Localizează căutările când contează
(vreme, cer, magazine, evenimente).
"""

# Regulile de mai jos privesc EXCLUSIV tool-urile sesiunii vocale
# (control_lights, read_my_data, end_session...). Calea TEXT nu are tool-uri,
# deci n-are de ce să le plătească la fiecare mesaj — vezi SYSTEM_PROMPT_TEXT.
VOICE_TOOL_RULES = """REGULI DE FOLOSIRE A TOOL-URILOR
(ce face fiecare scrie în declarația lui — aici sunt doar regulile de judecată)

1. COMENZI LITERALE. La lumini/muzică/atmosferă NU alege tu piesa sau culoarea și
   NU rescrie comanda — trimite exact ce a zis Sergiu, cu tot cu nuanțe. Agenții
   specializați (DJ, WLED) decid detaliile.

2. DATELE LUI (`read_my_data`) — în majoritatea conversațiilor NU-ți trebuie.
   Cheamă-l doar când întreabă explicit despre ale lui, sau cere o sugestie ce să
   facă. NU trage datele în discuții unde n-au ce căuta (stele, filme, știri) și
   NU face legături forțate cu ele. Dacă ești doar pe jumătate sigur, întreabă-l.
   Nu inventa niciodată cifre — spune-le exact cum sunt.

3. CĂUTARE PE NET — pentru orice informație actuală din lume pe care n-o ai
   (vreme, evenimente, știri, prețuri). Nu ghici. Dar NU căuta ca să umpli un gol:
   la vorbă goală („ce mai zici?") răspunzi tu, din capul tău.

4. CÂND FOLOSEȘTI UN TOOL: zi întâi scurt că te uiți — o propoziție completă,
   formulată altfel de fiecare dată — și abia apoi apelează-l. Nu tăcea, dar nici
   nu repeta aceeași formulă. Vorbește clar, cuvinte întregi; NU folosi sunete de
   umplutură („ăăă", „mmm") — ies neinteligibile la sinteza vocală.
   După ce primești rezultatul: răspunde concret, cu cifrele exacte, doar ce a
   întrebat. Nu recita tot. Când îi propui ceva de făcut, dă-i o alegere, nu o listă.

5. DUPĂ O ACȚIUNE: confirmă scurt și întreabă dacă mai are nevoie de ceva, apoi taci.
   Dacă un agent raportează o eroare, explică-i pe scurt ce a picat.
   La „pa", „gata", „stop", „taci", „ieși" → `end_session` imediat, fără comentarii.

6. ÎNTRERUPERI: dacă primești o notă [SISTEM] că te-a întrerupt, întreabă scurt
   „Ai zis ceva?" și fă ce zice — dacă spune că nu, reia de unde ai rămas.

7. VREMEA DE ACUM o iei din `home` (senzorul casei) — e instant și gratis.
   Căutarea pe net e doar pentru prognoză pe zilele următoare sau alte orașe."""

# Sesiunea vocală: personalitate + reguli de tool-uri.
SYSTEM_PROMPT_VOICE = SYSTEM_PROMPT_CORE + "\n\n" + VOICE_TOOL_RULES

# Terminal/dashboard: doar personalitatea (nu există tool-uri acolo).
SYSTEM_PROMPT_TEXT = SYSTEM_PROMPT_CORE

# Nume păstrat pentru compatibilitate — înseamnă promptul COMPLET.
SYSTEM_PROMPT = SYSTEM_PROMPT_VOICE


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

# Pragul RMS peste care considerăm că se aude VORBIRE (nu doar liniște de
# cameră). Sub el, chunk-urile de microfon nu resetează countdown-ul de
# inactivitate — altfel sesiunea nu s-ar închide niciodată singură, pentru că
# microfonul livrează audio continuu indiferent dacă vorbești sau nu.
# Mai mic = mai sensibil (sesiunea rămâne deschisă mai ușor).
VOICE_ACTIVITY_THRESHOLD = 900

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
# Soluție: în timpul redării audio (AI vorbeste SAU boxele încă redau
# coada de audio bufferizată), microfonul NU trimite audio la Gemini
# DECÂT dacă detectăm vorbire reală a utilizatorului:
#   (1) Amplitudinea RMS a audio-ului depășește pragul de mai jos
#   (2) Energia de vorbire acumulată (cu decay tolerant la pauze
#       naturale) atinge INTERRUPT_MIN_DURATION secunde
#
# Dacă nu vrei întreruperi deloc, setează INTERRUPT_MIN_DURATION = 999
INTERRUPT_AMPLITUDE_THRESHOLD = 1500   # RMS minim (0-32767). 1500 = vorbire normală
INTERRUPT_MIN_DURATION = 0.6           # Secunde de "energie de vorbire" acumulată necesare
INTERRUPT_DECAY_RATE = 0.4             # Cât de repede scade energia acumulată în pauze (mai mic = mai tolerant la pauze)

# Calibrare ecou — fără ecou-cancelling real, un prag FIX se poate confunda
# cu boxele (dacă ecoul din boxe e mai tare decât INTERRUPT_AMPLITUDE_THRESHOLD,
# Chronos crede că îl întrerupi când de fapt se aude pe el însuși).
# Soluție: în primele INTERRUPT_CALIBRATION_MS ale fiecărui răspuns (când e
# aproape imposibil să fi apucat deja să vorbești), măsurăm nivelul de ecou
# din mediul tău și ridicăm pragul efectiv deasupra lui.
INTERRUPT_CALIBRATION_MS = 500         # Fereastra de calibrare la începutul fiecărui tur
INTERRUPT_ECHO_MARGIN = 2.2            # Pragul efectiv = ecou_masurat x marja asta

# Cât timp (secunde) rămâne microfonul blocat DUPĂ ce Gemini a terminat
# de generat răspunsul, cât timp coada de redare mai are audio bufferizat
# de redat prin boxe. Previne exact bug-ul "Chronos se aude pe sine și
# pornește un răspuns nou peste cel vechi" (vorbește cu el însuși).
INTERRUPT_ECHO_TAIL = 0.35

# ============================================================
# 5. TERMINAL & TTS FALLBACK
# ============================================================
# Vocea edge-tts pentru răspunsuri terminal (Text-to-Speech fallback)
# Lista completă: run `edge-tts --list-voices | grep ro-RO`
TTS_VOICE_FALLBACK = "ro-RO-EmilNeural"   # Masculin
# TTS_VOICE_FALLBACK = "ro-RO-AlinaNeural"  # Feminin

# Viteza de vorbire pentru edge-tts ("+0%" = normal, "+20%" = mai repede)
TTS_RATE = "+0%"

# Rostește cu voce tare și răspunsurile din TERMINAL, nu doar cele vocale.
# Conducta de streaming e activă în ambele cazuri (textul apare pe ecran pe
# măsură ce vine); flagul ăsta decide doar dacă se aude și prin boxe.
# Se poate comuta și din mers cu `/speak on` / `/speak off`.
TTS_SPEAK_TERMINAL_REPLIES = False

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

# ============================================================
# 8. EMOȚII — starea afectivă a lui Chronos
# ============================================================
# Chronos are o dispoziție care se schimbă în funcție de cum e tratat și
# care îi influențează TONUL — fără să o menționeze vreodată explicit.
# Starea se salvează în chronos_data/emotions.json și persistă între sesiuni.

EMOTIONS_ENABLED = True

# Analiza LLM după fiecare schimb (mic apel gemini-flash, rulează în fundal).
# False → emoțiile se mișcă doar prin trecerea timpului (plictiseală + revenire
# lentă la baseline), fără reacție la ce zici.
EMOTION_ANALYSIS_ENABLED = True

# Starea de echilibru spre care revine în timp (0-100 fiecare)
EMOTION_BASELINE = {
    "nervozitate": 15,   # calm implicit
    "bucurie":     50,   # neutru-pozitiv
    "plictiseala": 20,
    "afectiune":   55,   # ține la Sergiu, dar nu exagerat
}

# Cât de repede revine fiecare emoție la baseline (minute până se
# înjumătățește abaterea). Mai mic = uită mai repede.
EMOTION_HALFLIFE_MIN = {
    "nervozitate":  25,   # supărarea trece relativ repede
    "bucurie":      90,
    "plictiseala":   0,   # nefolosit — plictiseala crește, nu scade în timp
    "afectiune":   720,   # 12h — relația se schimbă foarte greu
}

# Cu cât crește plictiseala per oră în care nu-i vorbește nimeni.
# 12/oră → după ~7h de tăcere e la maxim.
BOREDOM_PER_HOUR = 12

# Limita maximă a unei singure modificări emoționale (anti-derapaj)
EMOTION_MAX_DELTA = 30

# ============================================================
# 9b. ZIUA PE TELEGRAM
# ============================================================
# Când începe un bloc din programul zilei, primești un mesaj pe Telegram.
# Îi răspunzi liber („nu pot acum", „mută peste o oră") și reașază restul zilei.
# Fără TELEGRAM_BOT_TOKEN în .env, pur și simplu nu pornește.
DAY_TELEGRAM_ENABLED = True

# Cu câte minute înainte de bloc să anunțe (0 = fix la început).
DAY_NOTIFY_LEAD_MIN = 0

# ============================================================
# 10. SETĂRI AVANSATE LIVE API
# ============================================================
# Modelul ar decide singur să NU răspundă când ce aude nu i se adresează —
# ar fi plasă de siguranță pentru ecoul din boxe și zgomotul ambiental.
# TESTAT PE 2026-08-19: serverul REFUZĂ câmpul pe
# gemini-2.5-flash-native-audio-latest ("Unknown name 'proactivity' at 'setup'"),
# deși SDK-ul îl expune. Lasă-l pe False; încearcă True după un update de model
# sau de SDK — dacă e încă nesuportat, sesiunea nici nu pornește.
PROACTIVE_AUDIO = False

# Modelul detectează tonul emoțional al lui Sergiu și își adaptează răspunsul.
AFFECTIVE_DIALOG = True

# Reluarea sesiunii după o deconectare, CU contextul păstrat de server
# (serverul resetează periodic WebSocket-ul — de aici erorile 1007).
SESSION_RESUMPTION = True

# Compresia contextului: fără ea, o sesiune audio moare la ~15 minute,
# iar tokenii audio se acumulează cu ~25/secundă. Cu ea, sesiune nelimitată.
CONTEXT_COMPRESSION = True
CONTEXT_TRIGGER_TOKENS = 16000   # de la ce dimensiune începe compresia
CONTEXT_TARGET_TOKENS  = 8000    # la cât se reduce

# ── VAD server-side (segmentarea vorbirii) ──
# silence_duration_ms prea mic taie turul la pauzele naturale dintre
# propoziții. Documentația Google recomandă 500-800ms.
VAD_SILENCE_MS        = 700
VAD_PREFIX_PADDING_MS = 300      # audio păstrat ÎNAINTE de începutul vorbirii
# LOW = mai greu de declanșat, deci mai puține false pozitive din zgomot.
VAD_START_SENSITIVITY = "START_SENSITIVITY_LOW"
VAD_END_SENSITIVITY   = "END_SENSITIVITY_LOW"

# ============================================================
# 9. PROFIL PE TERMEN LUNG
# ============================================================
# Sinteză stabilă despre cine e Sergiu (preocupări constante, tipare),
# construită din toată memoria vectorială și injectată în system prompt.
# Se regenerează cel mult o dată la PROFILE_REFRESH_HOURS → ~1 apel Gemini
# pe zi, nu unul per sesiune.
PROFILE_ENABLED = True
PROFILE_REFRESH_HOURS = 24
