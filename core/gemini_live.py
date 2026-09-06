"""
core/gemini_live.py — Chronos Gemini Live Voice Session v6
============================================================
Schimbări v6:

1. ECHO GUARD LEGAT DE REDAREA REALĂ, NU DE turn_complete (fix "vorbește cu el însuși")
   - Bug v5: _ai_is_speaking se punea pe False la turn_complete — adică
     imediat ce Gemini termină de GENERAT răspunsul. Dar coada de redare
     (_audio_out_queue) mai avea audio bufferizat de REDAT prin boxe încă
     multe sute de ms / câteva secunde. În acel interval microfonul se
     redeschidea, capta ecoul propriei voci a lui Chronos din boxe și îl
     retrimitea la Gemini ca "vorbire user" → Gemini pornea un răspuns nou
     peste cel care încă se reda → efectul de "doi Chronos care vorbesc
     unul peste altul / se ceartă între ei".
   - Fix: _ai_audio_active() = _ai_is_speaking SAU "am scris audio către
     boxe acum mai puțin de INTERRUPT_ECHO_TAIL secunde" (_last_audio_write_ts,
     actualizat de _playback_loop la fiecare scriere reală către stream).
     Microfonul rămâne blocat cât timp boxele CHIAR redau ceva, nu doar
     cât Gemini generează.

2. INTERRUPT "LEAKY" ÎN LOC DE STRICT CONTINUU (fix "nu mai pot întrerupe deloc")
   - Bug v5: pragul cerea INTERRUPT_MIN_DURATION secunde de RMS>prag
     NEÎNTRERUPTE — orice pauză naturală din vorbire (dintre silabe/cuvinte)
     reseta contorul la 0, deci practic nu se acumula niciodată.
   - Fix: _interrupt_energy se acumulează cât timp RMS>prag și scade lent
     (INTERRUPT_DECAY_RATE) în pauze, în loc să se reseteze brusc — vorbirea
     reală întrerupe fiabil, zgomotul de fundal scurt nu.

3. TIMER INACTIVITATE
   - _last_turn_end: când AI a terminat de vorbit (reset la turn_complete)
   - Countdown pornește DUPĂ ce AI termină, nu în timp ce vorbește
   - Dacă user vorbește → reset timer
   - Dacă AI vorbește (_ai_audio_active) → timer pauzat (nu se numără)

4. TOOL CALLING PRIN DISPATCHER
   - control_lights(command) → dispatcher.process_text_command → wled_specialist
   - control_music(command)  → dispatcher.process_text_command → music_specialist
   - save_journal(entry)     → dispatcher.process_text_command → logger
   - end_session()           → închide sesiunea imediat
"""

import asyncio
import json
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from config import (
        LIVE_MODEL, LIVE_VOICE,
        LIVE_SAMPLE_RATE_IN, LIVE_SAMPLE_RATE_OUT,
        LIVE_INACTIVITY_TIMEOUT, LIVE_START_DELAY_MS,
        LIVE_PLAYBACK_CHUNK_BYTES, SYSTEM_PROMPT_VOICE, GEMINI_API_KEY,
        INTERRUPT_AMPLITUDE_THRESHOLD, INTERRUPT_MIN_DURATION,
        INTERRUPT_DECAY_RATE, INTERRUPT_ECHO_TAIL,
        INTERRUPT_CALIBRATION_MS, INTERRUPT_ECHO_MARGIN,
        VOICE_ACTIVITY_THRESHOLD,
        PROACTIVE_AUDIO, AFFECTIVE_DIALOG, SESSION_RESUMPTION,
        CONTEXT_COMPRESSION, CONTEXT_TRIGGER_TOKENS, CONTEXT_TARGET_TOKENS,
        VAD_SILENCE_MS, VAD_PREFIX_PADDING_MS,
        VAD_START_SENSITIVITY, VAD_END_SENSITIVITY,
    )
except ImportError:
    LIVE_MODEL                    = "gemini-2.5-flash-native-audio-latest"
    LIVE_VOICE                    = "Charon"
    LIVE_SAMPLE_RATE_IN           = 16000
    LIVE_SAMPLE_RATE_OUT          = 24000
    LIVE_INACTIVITY_TIMEOUT       = 8.0
    LIVE_START_DELAY_MS           = 300
    LIVE_PLAYBACK_CHUNK_BYTES     = 2048
    SYSTEM_PROMPT_VOICE           = "Ești Chronos. Răspunzi în română."
    GEMINI_API_KEY                = ""
    INTERRUPT_AMPLITUDE_THRESHOLD = 1500
    INTERRUPT_MIN_DURATION        = 0.6
    INTERRUPT_DECAY_RATE          = 0.4
    INTERRUPT_ECHO_TAIL           = 0.35
    INTERRUPT_CALIBRATION_MS      = 500
    INTERRUPT_ECHO_MARGIN         = 2.2
    VOICE_ACTIVITY_THRESHOLD      = 900
    PROACTIVE_AUDIO               = False
    AFFECTIVE_DIALOG              = True
    SESSION_RESUMPTION            = True
    CONTEXT_COMPRESSION           = True
    CONTEXT_TRIGGER_TOKENS        = 16000
    CONTEXT_TARGET_TOKENS         = 8000
    VAD_SILENCE_MS                = 700
    VAD_PREFIX_PADDING_MS         = 300
    VAD_START_SENSITIVITY         = "START_SENSITIVITY_LOW"
    VAD_END_SENSITIVITY           = "END_SENSITIVITY_LOW"

def _pcm_bytes(chunk, np) -> bytes:
    """Chunk-ul de microfon ca bytes PCM 16-bit.

    AudioInterface livrează deja int16, dar `astype("int16")` copia vectorul
    din nou la FIECARE chunk (de ~12 ori pe secundă, cât ține sesiunea)
    doar ca să ajungă la același dtype. Conversia rămâne ca plasă de
    siguranță dacă vreodată sursa se schimbă.
    """
    if chunk.dtype != np.int16:
        chunk = chunk.astype(np.int16)
    return chunk.tobytes()


def _rms(chunk, np) -> float:
    """Amplitudinea RMS a unui chunk (0-32767)."""
    return float(np.sqrt(np.mean(np.square(chunk, dtype=np.float32))))


_FLUSH_SENTINEL = object()
_END_SENTINEL   = object()

# Reconectare automată când API-ul Live pică singur mid-conversație
# (ex: eroarea 1007 CONTENT_TYPE_AUDIO, care apare intermitent pe modelele
# native-audio). Fără asta, Chronos se oprea pur și simplu în mijlocul frazei.
MAX_RECONNECT_ATTEMPTS = 2
RECONNECT_DELAY        = 0.4   # secunde

# Fereastra pe care urmarim anvelopa ecoului (chunks de 80ms).
# 25 x 80ms = 2s: destul de lunga incat o interventie scurta a lui Sergiu
# sa nu miste percentila, destul de scurta incat sa urmareasca dinamica vocii.
ECHO_WINDOW_CHUNKS = 25
ECHO_PERCENTILE    = 75

# Ce face Chronos după ce a executat o acțiune. Înainte închideam sesiunea
# automat, ceea ce suna abrupt (mai ales când tocmai pusese el o întrebare).
# Acum confirmă și lasă ușa deschisă; dacă Sergiu nu mai zice nimic, sesiunea
# se închide singură prin timeout-ul de inactivitate.
_DUPA_ACTIUNE = (
    "Confirmă-i lui Sergiu scurt și sec ce ai făcut, apoi întreabă-l printr-o "
    "formulare SCURTĂ dacă mai are nevoie de ceva (variaz-o de fiecare dată, "
    "nu folosi mereu aceleași cuvinte). Apoi TACI și așteaptă. "
    "Totul într-o singură replică scurtă — nu repeta confirmarea de două ori."
)


class GeminiLiveSession:
    """
    Sesiune vocală Gemini Native Audio.
    Tool-urile sunt rutate prin dispatcher-ul existent (wled_specialist, etc.)
    """

    def __init__(self, event_bus, dispatcher=None, audio_interface=None):
        self.bus        = event_bus
        self.dispatcher = dispatcher
        # Injectat de LLMRouter — necesar pentru a arma wake-interrupt în focus mode.
        self.audio_interface = audio_interface

        self._client = None
        self._types  = None
        self._sd     = None
        self._np     = None

        self._initialized        = False
        self._session_active     = False
        self._ai_is_speaking     = False

        # Timer inactivitate: moment când AI a terminat ultimul turn
        # (countdown porneste de ATUNCI, nu din timpul AI speech)
        self._last_turn_end      = 0.0

        # Interrupt detection (echo prevention) — energie acumulată cu decay,
        # tolerantă la pauze scurte naturale din vorbire (vezi _ai_audio_active)
        self._interrupt_energy   = 0.0

        # Calibrare ecou per-tur: în primele INTERRUPT_CALIBRATION_MS ale
        # fiecărui răspuns măsurăm nivelul de ecou din boxe (fără input real
        # din partea lui Sergiu, statistic), apoi ridicăm pragul de interrupt
        # deasupra lui — altfel boxele se pot confunda cu o întrerupere reală.
        self._turn_speech_start_ts = 0.0
        self._echo_calib_samples   = []
        self._echo_baseline_rms    = 0.0

        # Timestamp-ul ultimei scrieri REALE de audio către boxe.
        # Folosit pentru a ține microfonul blocat cât timp coada de redare
        # mai are audio bufferizat, chiar dacă Gemini a terminat deja de
        # GENERAT răspunsul (turn_complete ajunge mult înainte ca boxele
        # să termine de REDAT audio-ul bufferizat).
        self._last_audio_write_ts = 0.0

        # Transcript VERBATIM al vorbirii utilizatorului (ASR server-side,
        # NU parafrazarea pe care Gemini o pune în argumentul "command" al
        # tool call-ului). Folosit ca sursă de adevăr pentru comanda trimisă
        # către agenții specializați — vezi _handle_tool_call.
        self._current_user_transcript = ""

        # Transcript al vocii lui Chronos pentru turul curent (ce a răspuns
        # el, ca text) — împreună cu _current_user_transcript, formează
        # perechea salvată în memoria conversațională la finalul turului.
        self._current_ai_transcript = ""

        # ── FOCUS MODE (al doilea mod de barge-in) ──
        # False → mod GENERAL: poți vorbi peste Chronos oricând (barge-in pe RMS).
        # True  → mod FOCUS: Chronos livrează ceva important (răspuns bazat pe
        #         tool-uri/date). Barge-in-ul pe voce e DEZACTIVAT ca zgomotul
        #         ambiental să nu taie răspunsul; singura cale de a-l întrerupe
        #         e să spui din nou wake word-ul (vezi _handle_wake_interrupt).
        self._focus_mode = False

        # Handle-ul de reluare a sesiunii, trimis periodic de server. Cu el,
        # o reconectare continua EXACT de unde a ramas, cu tot contextul —
        # spre deosebire de repovestirea manuala din _seed_after_reconnect.
        # Valabil 2 ore de la ultima terminare a sesiunii.
        self._resumption_handle = None

        # Reconectare: distingem „legătura a picat" de „sesiunea s-a încheiat
        # cum trebuie" (end_session / inactivitate / auto-close / stop).
        # Reluăm conexiunea doar în primul caz.
        self._connection_lost       = False
        self._ended_intentionally   = False

        # Playback
        self._audio_out_queue    = asyncio.Queue(maxsize=400)
        self._stop_playback      = threading.Event()
        self._close_after_turn   = False

        # Thread dedicat scrierii către boxe. Nu pool-ul default: acolo stau
        # Flask, agenții și analiza de emoții, iar redarea nu are voie să
        # aștepte după ele (se aude ca sacadare în voce).
        self._play_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="live-play"
        )

        # Pool MĂRGINIT pentru tool-urile lansate „și gata" (DJ-ul, scenele).
        # Înainte fiecare apel pornea un `threading.Thread` nou: la comenzi
        # date în rafală, threadurile se acumulau nelimitat, fiecare cu un apel
        # LLM în desfășurare, fără nimeni să le raporteze erorile.
        self._tool_pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="live-tool"
        )

        # Referințe tari la task-urile de fundal. Fără ele, asyncio poate
        # colecta un task în plină execuție — salvarea conversației sau
        # actualizarea emoțiilor dispăreau în tăcere.
        self._bg_tasks: set = set()

        self._ET = None
        try:
            from core.event_bus import EventType
            self._ET = EventType
        except ImportError:
            pass

    # ─────────────────────────────────────────────────────────
    # INIȚIALIZARE
    # ─────────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        logger.info("🔗 [GeminiLive] Inițializare...")
        try:
            from google import genai
            from google.genai import types
            self._client = genai.Client(api_key=GEMINI_API_KEY)
            self._types  = types
        except ImportError:
            logger.error("❌ [GeminiLive] google-genai lipsă!")
            return False
        try:
            import sounddevice as sd
            import numpy as np
            self._sd = sd
            self._np = np
        except ImportError:
            logger.error("❌ [GeminiLive] sounddevice lipsă!")
            return False

        self._initialized = True
        logger.info(
            f"✅ [GeminiLive] Gata.\n"
            f"   Model: {LIVE_MODEL} | Voce: {LIVE_VOICE}\n"
            f"   Timeout: {LIVE_INACTIVITY_TIMEOUT}s | "
            f"Interrupt: RMS>{INTERRUPT_AMPLITUDE_THRESHOLD} × {INTERRUPT_MIN_DURATION}s"
        )
        return True

    # ─────────────────────────────────────────────────────────
    # TOOL DECLARATIONS (pentru Gemini Live API)
    # ─────────────────────────────────────────────────────────

    def _build_tools(self) -> list:
        """
        Declară funcțiile pe care Gemini le poate apela.

        CONSOLIDAT: tool-urile înrudite sunt grupate sub un parametru `action`
        / `kind` în loc să fie declarate separat. Declarațiile se retrimit la
        FIECARE tur, deci fiecare descriere în plus se plătește de fiecare
        dată — 17 tool-uri costau ~2250 tokeni/tur, 8 costă ~jumătate, la
        aceeași funcționalitate.
        """
        types = self._types
        try:
            return [
                # Căutare web nativă (grounding Google) — informații actuale
                # din lume. Modelul își localizează singur interogările.
                types.Tool(google_search=types.GoogleSearch()),

                types.Tool(function_declarations=[

                types.FunctionDeclaration(
                    name="control_lights",
                    description=(
                        "Lumini LED WLED. Trimite comanda LITERAL în română, cum a zis-o "
                        "Sergiu (ex: 'vreau o atmosferă roșie faină'). Nu o rescrie."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                ),

                types.FunctionDeclaration(
                    name="music",
                    description=(
                        "Muzică. action='play' PORNEȘTE ceva nou — pune în `query` comanda "
                        "LITERALĂ a lui Sergiu (gen/vibe/piesă), un DJ alege piesa. "
                        "Celelalte acțiuni controlează instant redarea curentă. "
                        "'like'/'dislike' când își dă cu părerea despre ce cântă "
                        "('asta e tare' / 'scoate-o') — DJ-ul învață din asta."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": [
                                "play", "pause", "resume", "next", "previous",
                                "volume_up", "volume_down", "set_volume", "now_playing",
                                "like", "dislike"]},
                            "query": {"type": "string", "description": "Doar pentru play."},
                            "value": {"type": "integer", "description": "Procent volum."},
                        },
                        "required": ["action"],
                    },
                ),

                types.FunctionDeclaration(
                    name="scene",
                    description=(
                        "Scene salvate (lumini+muzică) și anulare. action='activate' cu "
                        "`name` ('Cozy Night', 'Energie Mobtrap'); action='undo' readuce "
                        "luminile cum erau ('nu, anulează', 'pune-le înapoi')."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["activate", "undo"]},
                            "name": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                ),

                types.FunctionDeclaration(
                    name="execute_command",
                    description=(
                        "Comandă combinată de atmosferă, care atinge și lumini, și muzică "
                        "(ex: 'atmosferă de munte'). Trimite textul literal."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                ),

                types.FunctionDeclaration(
                    name="read_my_data",
                    description=(
                        "Citește datele REALE ale lui Sergiu. DOAR când întreabă explicit "
                        "despre ale LUI (banii lui, ce are de făcut, progresul lui) sau cere "
                        "o sugestie ce să facă. NU în conversație obișnuită sau la subiecte "
                        "despre lume. Ia STRICT ce trebuie, de obicei O categorie. "
                        "'tranzactii'/'vanzari' doar la cerere explicită, nu la 'câți bani am'."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "categories": {
                                "type": "array",
                                "items": {"type": "string", "enum": [
                                    "azi", "finante", "tranzactii", "vanzari", "targeturi",
                                    "remindere", "proiecte", "sport", "obiceiuri"]},
                            },
                        },
                        "required": ["categories"],
                    },
                ),

                types.FunctionDeclaration(
                    name="bus",
                    description=(
                        "Când îi vine lui Sergiu următorul autobuz de acasă (Str. Argeșului 24) "
                        "spre una din destinațiile lui. Apelează la 'când am bus spre X', "
                        "'în cât timp am autobuz', 'cu ce ajung la X'. "
                        "`variante`=true doar dacă cere explicit și cursele de mai târziu. "
                        "Răspunsul vine gata calculat — citește-l ca atare, nu recalcula ore."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "destination": {"type": "string", "enum": [
                                "scoala", "sala", "centru", "tudor", "unirii"]},
                            "variante": {"type": "boolean"},
                        },
                        "required": ["destination"],
                    },
                ),

                types.FunctionDeclaration(
                    name="save_data",
                    description=(
                        "Notează în datele lui Sergiu. Alege `kind`: "
                        "cheltuiala/incasare (`value`=suma, `text`=pe ce, `extra`=contul); "
                        "reminder (`text`=ce); reminder_gata (`text`=ce a terminat — merge "
                        "și pentru mentenanță, ex 'am curățat imprimanta'); "
                        "target (`text`=obiectiv); target_progres (`text`=care, `value`=0-100); "
                        "greutate (`value`=kg); proiect_pas (`text`=pasul terminat); "
                        "proiect_devlog (`text`=titlu, `extra`=detalii); "
                        "obicei (`text`=care); jurnal (`text`=gândul)."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": [
                                "cheltuiala", "incasare", "reminder", "reminder_gata",
                                "target", "target_progres", "greutate", "proiect_pas",
                                "proiect_devlog", "obicei", "jurnal"]},
                            "text": {"type": "string"},
                            "value": {"type": "number"},
                            "extra": {"type": "string"},
                        },
                        "required": ["kind"],
                    },
                ),

                types.FunctionDeclaration(
                    name="home",
                    description=(
                        "Casa (Home Assistant). 'ac_on'/'ac_off' = aer conditionat. "
                        "'cine_e_acasa' = cine e prezent. "
                        "'vreme' = cum e ACUM afara; 'prognoza' = zilele urmatoare "
                        "(`zile`, implicit 3, maxim 5). Amandoua vin din casa: "
                        "instant si gratis, deci NU cauta vremea pe web."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": [
                                "ac_on", "ac_off", "cine_e_acasa", "vreme", "prognoza"]},
                            "zile": {"type": "integer", "description": "Doar pentru prognoza."},
                        },
                        "required": ["action"],
                    },
                ),

                types.FunctionDeclaration(
                    name="timer",
                    description=(
                        "Timere si alarme. 'set' = timer relativ (da `hours`/`minutes`/"
                        "`seconds`); 'alarm' = ora fixa (da `hour` si `minute`); "
                        "'list' = ce e activ; 'cancel' = anuleaza (`label` sau gol pentru tot). "
                        "`label` = pentru ce e ('paste', 'trezire')."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["set", "alarm", "list", "cancel"]},
                            "hours": {"type": "number"},
                            "minutes": {"type": "number"},
                            "seconds": {"type": "number"},
                            "hour": {"type": "integer", "description": "Ora 0-23, pentru alarm."},
                            "minute": {"type": "integer", "description": "Minutul 0-59."},
                            "label": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                ),

                types.FunctionDeclaration(
                    name="ziua",
                    description=(
                        "Ce are Sergiu de facut azi.\n"
                        "'plan' = noteaza ce vrea sa faca (da `iteme`). ASTA E MODUL "
                        "NORMAL: notezi intentiile, NU faci orar, NU intrebi de "
                        "intensitate, NU dai ore. Poti chema din nou peste zi ca sa "
                        "adaugi lucruri — se adauga, nu se sterge ce era.\n"
                        "'program' = ABIA CAND CERE EL EXPLICIT un orar pe ore. Doar "
                        "atunci intreaba-l ce intensitate vrea (relaxat/normal/full) "
                        "si daca iese undeva (`ocupat`).\n"
                        "'azi' = ce mai are de facut; 'gata' = a terminat ceva (`text`); "
                        "'sari' = renunta azi la ceva (`text`).\n"
                        "Doar DUPA ce exista un orar: 'amana' (`minute` SAU `ora`), "
                        "'acum', 'replan'. `text` gol = lucrul curent.\n"
                        "'somn' = programul de somn (`trezire`, `culcare`, `mod`).\n"
                        "Estimeaza TU duratele; intreaba doar la lucruri complet noi. "
                        "Pentru proiecte pune `proiect` (numele) si, daca e ceva nou, "
                        "`pasi` — se creeaza automat in proiect."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "action": {"type": "string",
                                       "enum": ["plan", "program", "azi", "gata",
                                                "sari", "amana", "acum", "replan",
                                                "somn"]},
                            "intensitate": {"type": "string",
                                            "enum": ["relaxat", "normal", "full"]},
                            "iteme": {
                                "type": "array",
                                "description": "Ce vrea sa faca azi (pentru 'plan').",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "titlu": {"type": "string"},
                                        "minute": {"type": "integer"},
                                        "proiect": {"type": "string",
                                                    "description": "Daca tine de un proiect."},
                                        "pasi": {"type": "array", "items": {"type": "string"},
                                                 "description": "Subpasi noi de creat in proiect."},
                                    },
                                    "required": ["titlu"],
                                },
                            },
                            "ocupat": {
                                "type": "array",
                                "description": "Intervale in care nu e liber.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "start": {"type": "string", "description": "HH:MM"},
                                        "end": {"type": "string", "description": "HH:MM"},
                                        "title": {"type": "string"},
                                    },
                                    "required": ["start", "end"],
                                },
                            },
                            "text": {"type": "string",
                                     "description": "La ce se refera (gata/amana/sari/acum). Gol = blocul curent."},
                            "minute": {"type": "integer", "description": "Cu cate minute amana."},
                            "ora": {"type": "string", "description": "HH:MM, daca cere o ora anume."},
                            "trezire": {"type": "string", "description": "HH:MM"},
                            "culcare": {"type": "string", "description": "HH:MM"},
                            "mod": {"type": "string", "description": "vacanta / scoala"},
                        },
                        "required": ["action"],
                    },
                ),

                types.FunctionDeclaration(
                    name="send_telegram",
                    description="Mesaj pe telefonul lui Sergiu. DOAR la cerere explicită.",
                    parameters={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),

                types.FunctionDeclaration(
                    name="end_session",
                    description=(
                        "Închide sesiunea. Apelează IMEDIAT la 'pa', 'gata', 'stop', "
                        "'taci', 'ieși' sau orice rămas-bun."
                    ),
                    parameters={"type": "object", "properties": {}, "required": []},
                ),

                ]),
            ]
        except Exception as e:
            logger.warning(f"⚠️ [GeminiLive] Tool declarations eșuate: {e}")
            return []

    # ─────────────────────────────────────────────────────────
    # TOOL EXECUTION — prin dispatcher
    # ─────────────────────────────────────────────────────────

    async def _handle_tool_call(self, session, tool_call) -> bool:
        """
        Primim un tool_call de la Gemini → executăm → trimitem FunctionResponse.

        Returns:
            True  → sesiunea continuă
            False → sesiunea trebuie închisă (end_session apelat)
        """
        fcs = getattr(tool_call, "function_calls", [])
        if not fcs:
            return True

        responses = []
        should_close = False
        # Gemini trimite uneori ACELAȘI apel de două ori în același batch.
        # Fără dedup, acțiunea se execută de două ori (două remindere identice)
        # și modelul confirmă de două ori, ca și cum ar vorbi două voci.
        deja_executate = {}

        for fc in fcs:
            name  = getattr(fc, "name", "unknown")
            args  = dict(fc.args) if getattr(fc, "args", None) else {}
            fc_id = getattr(fc, "id", None)

            amprenta = (name, json.dumps(args, sort_keys=True, default=str))
            if amprenta in deja_executate:
                logger.warning(f"⚠️ [GeminiLive] Tool call DUPLICAT ignorat: {name}({args})")
                responses.append(self._types.FunctionResponse(
                    id=fc_id, name=name, response=deja_executate[amprenta]
                ))
                continue

            logger.info(f"🔧 [GeminiLive] Tool call: {name}({args})")

            # ── end_session: închide sesiunea ──
            if name == "end_session":
                logger.info("👋 [GeminiLive] end_session apelat → sesiune terminată.")
                should_close = True
                result = {"status": "ok", "message": "Sesiune terminată."}

            # ── read_my_data: citire SINCRONĂ a datelor personale ──
            # Spre deosebire de tool-urile de acțiune (fire-and-forget), aici
            # avem NEVOIE de rezultat: el devine baza răspunsului rostit.
            elif name == "read_my_data":
                result = await self._read_my_data(args.get("categories") or [])
                # Răspunsul care urmează e important → intrăm în focus mode și
                # NU auto-închidem sesiunea (conversația continuă natural).
                self._enter_focus_mode()

            # ── bus: orarul autobuzelor, calculat local ──
            # Ca și read_my_data, rezultatul E răspunsul — deci sincron.
            elif name == "bus":
                result = await self._bus(
                    args.get("destination") or "",
                    bool(args.get("variante")),
                )
                self._enter_focus_mode()

            # ── Tool-uri LOCALE: rezultat imediat, zero apeluri LLM ──
            elif name in self.LOCAL_TOOLS:
                try:
                    result = await asyncio.to_thread(self._run_local_tool, name, args)
                except Exception as e:
                    logger.error(f"❌ [GeminiLive] Tool local '{name}': {e}", exc_info=True)
                    result = {"status": "error", "message": str(e)}
                logger.info(f"⚡ [GeminiLive] {name} → {result.get('message', result.get('status'))}")
                # NU mai închidem sesiunea după acțiune — confirmă și întreabă
                # dacă mai are nevoie de ceva. Dacă nu răspunde, se închide
                # singură prin timeout-ul de inactivitate.
                result["urmatorul_pas"] = _DUPA_ACTIUNE
                self._enter_focus_mode()

            # ── Dispatcher tools (agenți cu propriul LLM) ──
            elif name in ("control_lights", "execute_command"):
                # Preferăm transcriptul VERBATIM (ASR server-side) al ce a zis
                # Sergiu, nu argumentul din tool call — Gemini tinde să
                # rescrie/scurteze comanda (ex: "vreau o atmosferă romantică
                # roșie faină" → "pune luminile roșii"), pierzând nuanța pe
                # care agentul specializat (WLED/Music) ar folosi-o. Fallback
                # pe argumentul modelului doar dacă transcrierea a lipsit.
                raw_transcript = self._current_user_transcript.strip()
                command = raw_transcript or args.get("command") or args.get("entry") or ""
                if raw_transcript:
                    logger.info(f"🎙️ [GeminiLive] Comandă RAW (transcript): '{raw_transcript}'")
                result  = await self._dispatch(name, command)
                # NU resetăm _current_user_transcript aici — rămâne până la
                # turn_complete, unde e folosit și pentru memoria conversațională
                # (vezi _save_conversation_turn). Dacă vine un al doilea tool
                # call în același tur, va refolosi același transcript — corect,
                # e tot ce a zis Sergiu în turul ăsta.
                self._enter_focus_mode()

            else:
                result = {"status": "error", "message": f"Tool necunoscut: {name}"}

            deja_executate[amprenta] = result
            responses.append(self._types.FunctionResponse(
                id=fc_id, name=name, response=result
            ))

        # Trimitem răspunsul la Gemini (va genera un audio de confirmare)
        try:
            await session.send_tool_response(function_responses=responses)
            logger.info(f"✅ [GeminiLive] Tool response trimis ({len(responses)} funcții).")
        except Exception as e:
            logger.error(f"❌ [GeminiLive] send_tool_response: {e}")

        return not should_close

    async def _dispatch(self, tool_name: str, command: str) -> dict:
        """
        Rutează comanda prin dispatcher-ul existent în fundal (asincron).
        Dispatcher → intent detection → specialist (wled, music, logger, etc.)
        """
        if not self.dispatcher:
            logger.warning("[GeminiLive] Dispatcher indisponibil.")
            return {"status": "error", "executed": False, "message": "Dispatcher indisponibil."}

        # ── Rutare DIRECTĂ către agentul specializat ──
        # Tool call-ul a stabilit deja intenția (`control_lights` = lumini), deci
        # sărim peste pasul de planificare LLM din ChronosAgent: economisim ~1s
        # și un apel Gemini per comandă, și eliminăm riscul ca planificatorul să
        # rateze intenția (caz în care comanda nu s-ar executa deloc, deși
        # Chronos deja a confirmat vocal că a rezolvat).
        # Excepție: `execute_command` (atmosferă combinată) — acolo chiar vrem
        # planificarea, ca să lanseze mai mulți agenți în paralel.
        agent_name = getattr(self.dispatcher, "TOOL_AGENT_MAP", {}).get(tool_name)

        try:
            # Lansăm comanda în background fără să blocăm răspunsul vocal!
            # Metodele dispatcher-ului sunt sincrone → asyncio.to_thread.
            if agent_name and hasattr(self.dispatcher, "route_direct"):
                logger.info(f"⚡ [GeminiLive] Dispatch DIRECT: {tool_name} → {agent_name} | '{command}'")
                work = asyncio.to_thread(self.dispatcher.route_direct, agent_name, command)
            else:
                logger.info(f"🔄 [GeminiLive] Dispatch cu planificare: {tool_name} → '{command}'")
                work = asyncio.to_thread(self.dispatcher.process_text_command, command, "voice_live")
            self._spawn(work, f"dispatch_{tool_name}")

            return {
                "status": "success",
                "executed": True,
                "info": "Comanda a fost transmisă agenților specializați. " + _DUPA_ACTIUNE
            }
        except Exception as e:
            logger.error(f"❌ [GeminiLive] Dispatch error: {e}", exc_info=True)
            return {"status": "error", "executed": False, "error_details": str(e)}

    # ─────────────────────────────────────────────────────────
    # CITIRE DATE PERSONALE
    # ─────────────────────────────────────────────────────────

    # Tool-uri executate LOCAL: scriu/citesc direct din fișiere sau lovesc un
    # API (Spotify/Telegram/WLED). Niciunul nu implică un apel LLM — modelul
    # vocal a extras deja argumentele structurate prin function calling.
    LOCAL_TOOLS = frozenset({"music", "scene", "save_data", "send_telegram",
                             "home", "timer", "ziua"})

    def _run_local_tool(self, name: str, args: dict) -> dict:
        """Execuție sincronă a tool-urilor locale (rulată într-un thread)."""
        from tools import data_write_tools as W

        # ── MUZICĂ ──
        if name == "music":
            agent = getattr(self.dispatcher, "music_agent", None)
            if not agent:
                return {"status": "error", "message": "Agentul de muzică lipsește."}
            action = args.get("action", "")
            if action == "play":
                # Selecție de piesă → DJ (singurul care mai face un apel LLM).
                # Rulăm în fundal ca să nu blocăm confirmarea vocală.
                query = args.get("query") or self._current_user_transcript.strip()
                self._tool_pool.submit(agent.process_request, query)
                return {"status": "ok", "message": "Am dat comanda la DJ."}
            return agent.control(action, args.get("value"))

        # ── SCENE + UNDO ──
        if name == "scene":
            if args.get("action") == "undo":
                from tools.scene_tools import undo_lights
                return undo_lights()
            from tools.scene_tools import activate_scene
            res = activate_scene(args.get("name", ""))
            prompt = res.pop("music_prompt", "") if res.get("status") == "ok" else ""
            if prompt:
                agent = getattr(self.dispatcher, "music_agent", None)
                if agent:
                    self._tool_pool.submit(agent.process_request, prompt)
                    res["message"] += " Pornesc și muzica."
            return res

        # ── SCRIERE DATE — un singur tool, rutat pe `kind` ──
        if name == "save_data":
            kind = (args.get("kind") or "").strip()
            text = args.get("text") or ""
            val = args.get("value")
            extra = args.get("extra") or ""

            if kind in ("cheltuiala", "incasare"):
                return W.add_transaction(val, "in" if kind == "incasare" else "out",
                                         text, extra)
            if kind == "reminder":
                return W.add_reminder(text, extra or "Med")
            if kind == "reminder_gata":
                return W.complete_reminder(text)
            if kind == "target":
                return W.add_target(text, deadline=extra)
            if kind == "target_progres":
                return W.update_target_progress(text, val or 0)
            if kind == "greutate":
                return W.log_weight(val, text)
            if kind == "proiect_pas":
                return W.complete_project_step(text, extra)
            if kind == "proiect_devlog":
                return W.add_devlog(text, extra)
            if kind == "obicei":
                return W.check_habit(text)
            if kind == "jurnal":
                return W.quick_capture(text, "jurnal")
            return {"status": "error", "message": f"Tip necunoscut: {kind}"}

        # ── CASA ──
        if name == "home":
            from tools import home_assistant as HA
            a = args.get("action", "")
            if a in ("ac_on", "ac_off"):
                return HA.ac_control(a == "ac_on")
            if a == "cine_e_acasa":
                return HA.who_is_home()
            if a == "vreme":
                return HA.local_weather()
            if a == "prognoza":
                return HA.weather_forecast(args.get("zile") or HA.FORECAST_DAYS_DEFAULT)
            return {"status": "error", "message": f"Actiune necunoscuta: {a}"}

        # ── TIMERE / ALARME ──
        if name == "timer":
            from tools import timers as TM
            a = args.get("action", "")
            if a == "set":
                return TM.set_timer(args.get("minutes") or 0, args.get("seconds") or 0,
                                    args.get("hours") or 0, args.get("label", ""))
            if a == "alarm":
                return TM.set_alarm(args.get("hour"), args.get("minute") or 0,
                                    args.get("label", ""))
            if a == "list":
                return TM.list_timers()
            if a == "cancel":
                return TM.cancel_timer(args.get("label", ""))
            return {"status": "error", "message": f"Actiune necunoscuta: {a}"}

        # ── ZIUA ──
        if name == "ziua":
            from tools import day_planner as DP
            a = args.get("action", "")
            if a == "plan":
                # Fara orar, decat daca a cerut-o el explicit in aceeasi fraza.
                return DP.plan_day(args.get("iteme") or [],
                                   args.get("intensitate", "normal"),
                                   args.get("ocupat") or [],
                                   cu_program=False)
            if a == "program":
                return DP.fa_program(args.get("intensitate", ""),
                                     args.get("ocupat") or [])
            if a == "azi":
                return DP.today_summary()
            if a == "gata":
                return DP.complete(args.get("text", ""))
            if a in ("amana", "sari", "acum", "replan"):
                # Acelasi motor ca pe Telegram — vocea nu e cu nimic mai saraca.
                rez = DP.reschedule(a, args.get("text", ""),
                                    int(args.get("minute") or 0),
                                    args.get("ora", "") or "")
                if rez.get("status") == "ok":
                    rez["info"] = ("Spune-i pe scurt ce s-a schimbat si ce urmeaza, "
                                   "nu insira toata lista.")
                return rez
            if a == "somn":
                return DP.set_sleep(args.get("mod", ""), args.get("trezire", ""),
                                    args.get("culcare", ""))
            return {"status": "error", "message": f"Actiune necunoscuta: {a}"}

        if name == "send_telegram":
            from tools.telegram_tools import send_telegram
            return send_telegram(args.get("text", ""))

        return {"status": "error", "message": f"Tool local necunoscut: {name}"}

    async def _read_my_data(self, categories: list) -> dict:
        """
        Citește contextul cerut din chronos_data (sincron — rezultatul devine
        baza răspunsului rostit, deci nu poate fi fire-and-forget).
        """
        logger.info(f"📂 [GeminiLive] Citesc datele: {categories}")
        try:
            from tools.context_tools import read_context
            data = await asyncio.to_thread(read_context, categories)
            logger.debug(f"[GeminiLive] Context citit ({len(data)} caractere).")
            return {
                "status": "ok",
                "categories": list(categories),
                "data": data,
                "info": (
                    "Astea sunt datele REALE. Răspunde-i lui Sergiu pe baza lor, "
                    "concret și cu cifrele exacte. Nu enumera tot mecanic — "
                    "dă-i DOAR ce a întrebat, apoi cel mult o observație scurtă. "
                    "Ignoră complet categoriile care nu au legătură cu întrebarea lui."
                ),
            }
        except Exception as e:
            logger.error(f"❌ [GeminiLive] Citire date eșuată: {e}", exc_info=True)
            return {
                "status": "error",
                "message": f"Nu am putut citi datele: {e}",
                "info": "Spune-i scurt lui Sergiu că datele nu sunt accesibile acum.",
            }

    async def _bus(self, destination: str, variante: bool = False) -> dict:
        """Următorul autobuz spre `destination` — calcul local, fără rețea.

        Textul e deja formulat complet de bus_tools; modelul nu trebuie decât
        să-l rostească. Orice recalculare din partea lui ar strica orele, de
        aceea `info` insistă pe citire ca atare.
        """
        logger.info(f"🚌 [GeminiLive] Orar autobuz: {destination}")
        try:
            from tools import bus_tools
            text = await asyncio.to_thread(
                bus_tools.answer, destination, 5 if variante else 3
            )
            return {
                "status": "ok",
                "raspuns": text,
                "info": (
                    "Spune-i lui Sergiu exact asta, natural, fără să schimbi "
                    "orele, liniile sau numele stațiilor. Nu adăuga calcule "
                    "proprii și nu-l sfătui să meargă pe jos."
                ),
            }
        except Exception as e:
            logger.error(f"❌ [GeminiLive] Orar autobuz eșuat: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "info": "Spune-i scurt că nu ai orarul autobuzelor acum.",
            }

    # ─────────────────────────────────────────────────────────
    # FOCUS MODE — al doilea mod de barge-in
    # ─────────────────────────────────────────────────────────

    def _enter_focus_mode(self) -> None:
        """
        Intră în focus mode: barge-in-ul pe voce se oprește (ca zgomotul sau
        propriul ecou să nu taie un răspuns important) și se armează wake
        word-ul ca unică metodă de întrerupere.
        """
        if self._focus_mode:
            return
        self._focus_mode = True
        self._interrupt_energy = 0.0
        if self.audio_interface:
            try:
                self.audio_interface.arm_wake_interrupt()
            except Exception as e:
                logger.debug(f"[GeminiLive] arm_wake_interrupt: {e}")
        logger.info("🎯 [GeminiLive] FOCUS MODE — barge-in pe voce OPRIT, spune 'Jarvis' ca să întrerupi.")

    def _exit_focus_mode(self) -> None:
        """Revine la modul general: barge-in normal pe voce."""
        if not self._focus_mode:
            return
        self._focus_mode = False
        self._interrupt_energy = 0.0
        if self.audio_interface:
            try:
                self.audio_interface.disarm_wake_interrupt()
            except Exception as e:
                logger.debug(f"[GeminiLive] disarm_wake_interrupt: {e}")
        logger.info("💬 [GeminiLive] Mod GENERAL — poți vorbi peste Chronos oricând.")

    async def _wake_interrupt_listener(self, session) -> None:
        """Ascultă WAKE_WORD_INTERRUPT cât timp sesiunea e activă."""
        try:
            async for _ in self.bus.subscribe(self._ET.WAKE_WORD_INTERRUPT):
                if not self._session_active:
                    return
                await self._handle_wake_interrupt(session)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ [GeminiLive] wake_interrupt_listener: {e}", exc_info=True)

    async def _handle_wake_interrupt(self, session) -> None:
        """
        Sergiu a spus wake word-ul în timp ce Chronos vorbea în focus mode.
        Oprim redarea instant și îi cerem lui Gemini să întrebe scurt
        „Ai zis ceva?", păstrând contextul a ce spunea — ca să poată relua
        exact de unde a rămas dacă Sergiu zice că nu voia nimic.
        """
        logger.info("🖐️ [GeminiLive] ÎNTRERUPERE prin wake word → opresc redarea.")

        # 1. Tăiem audio-ul în curs
        self._stop_playback.set()
        while not self._audio_out_queue.empty():
            try:
                item = self._audio_out_queue.get_nowait()
                if item is _END_SENTINEL:
                    await self._audio_out_queue.put(_END_SENTINEL)
                    break
            except asyncio.QueueEmpty:
                break
        self._ai_is_speaking      = False
        self._last_audio_write_ts = 0.0
        await asyncio.sleep(0.05)
        self._stop_playback.clear()

        # 2. Ieșim din focus mode — de aici conversația e din nou normală
        self._exit_focus_mode()
        self._close_after_turn = False   # întreruperea anulează auto-close-ul
        self._last_turn_end    = time.time()

        # 3. Îi spunem lui Gemini ce s-a întâmplat + ce era pe cale să spună
        partial = self._current_ai_transcript.strip()
        note = (
            "[SISTEM] Sergiu te-a întrerupt spunând wake word-ul"
            + (f", cât timp spuneai: \"{partial}\"." if partial else ".")
            + " Oprește-te din ce spuneai. Întreabă-l scurt, o singură propoziție:"
            " \"Ai zis ceva?\" și apoi așteaptă."
            " Dacă îți zice că nu / să continui → reia exact de unde ai rămas."
            " Dacă îți cere altceva → lasă complet ce spuneai și fă ce cere."
        )
        try:
            await session.send_client_content(
                turns=self._types.Content(
                    role="user", parts=[self._types.Part(text=note)]
                ),
                turn_complete=True,
            )
        except Exception as e:
            logger.error(f"❌ [GeminiLive] Nu am putut trimite nota de întrerupere: {e}")

    # ─────────────────────────────────────────────────────────
    # MEMORIE CONVERSAȚIONALĂ
    # ─────────────────────────────────────────────────────────

    async def _build_system_prompt(self) -> str:
        """
        Injectează un recap al conversațiilor recente (orice sesiune live
        anterioară) în system prompt, ca Chronos să știe ce am mai vorbit cu
        el — nu doar ce se întâmplă în sesiunea curentă.
        """
        # Data/ora reală — altfel „mâine", „azi", „weekend" sunt ghicite greșit,
        # mai ales când caută pe net evenimente cu dată fixă.
        from datetime import datetime
        now  = datetime.now()
        zile = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]
        prompt = (
            f"{SYSTEM_PROMPT_VOICE}\n\n"
            f"[ACUM] Este {zile[now.weekday()]}, {now.strftime('%d.%m.%Y, ora %H:%M')}. "
            f"Calculează „azi/mâine/weekend” raportat la data asta, nu ghici."
        )

        # Starea emoțională curentă → instrucțiuni de ton (niciodată verbalizate)
        prompt += "\n\n" + self._emotion_block()

        logger_agent = getattr(self.dispatcher, "logger_agent", None) if self.dispatcher else None
        if not logger_agent:
            return prompt

        try:
            recap = await asyncio.to_thread(logger_agent.get_recent_conversations, 3)
        except Exception as e:
            logger.debug(f"[GeminiLive] Recap memorie indisponibil: {e}")
            return prompt

        # Profil stabil (cache-uit, se regenerează cel mult o dată pe zi)
        try:
            from core.user_profile import get_profile_block, refresh_in_background
            # DOAR din cache aici: regenerarea e un apel LLM de ~9s si ar
            # intarzia cu tot atat momentul in care Chronos incepe sa asculte.
            profil = await asyncio.to_thread(get_profile_block, None, False)
            if profil:
                prompt += "\n\n" + profil
            refresh_in_background(logger_agent)   # improspatare pt sesiunea urmatoare
        except Exception as e:
            logger.debug(f"[GeminiLive] Profil indisponibil: {e}")

        if not recap:
            return prompt

        return (
            f"{prompt}\n\n"
            f"[MEMORIE — CE AM MAI VORBIT CU SERGIU ÎN SESIUNI ANTERIOARE]\n"
            f"Ai deja context din conversații trecute. Folosește-l NATURAL, doar dacă "
            f"e relevant pentru ce zice Sergiu acum — nu-l recita, nu forța referiri.\n"
            f"{recap}"
        )

    @staticmethod
    def _emotion_block() -> str:
        """Harta comportamentală derivată din starea afectivă curentă."""
        try:
            from config import EMOTIONS_ENABLED
            if not EMOTIONS_ENABLED:
                return ""
            from core.emotions import get_state
            state = get_state()
            logger.info(f"💗 [GeminiLive] Stare afectivă: {state.debug_line()}")
            return state.behavior_prompt()
        except Exception as e:
            logger.debug(f"[GeminiLive] Bloc emoții indisponibil: {e}")
            return ""

    async def _save_conversation_turn(self) -> None:
        """
        Închiderea unui tur: salvăm schimbul în memorie ȘI actualizăm starea
        emoțională — ambele în fundal, ca să nu întârzie conversația.
        """
        user_text = self._current_user_transcript.strip()
        ai_text   = self._current_ai_transcript.strip()
        if not user_text and not ai_text:
            return

        logger_agent = getattr(self.dispatcher, "logger_agent", None) if self.dispatcher else None
        if logger_agent:
            self._spawn(
                asyncio.to_thread(logger_agent.save_conversation_turn, user_text, ai_text),
                "save_turn",
            )

        # Cum l-a afectat pe Chronos ce tocmai i-a zis Sergiu
        if user_text:
            self._spawn(self._update_emotions(user_text, ai_text), "update_emotions")

    async def _update_emotions(self, user_text: str, ai_text: str) -> None:
        """Analiză afectivă în fundal — nu blochează niciodată răspunsul vocal."""
        try:
            from config import EMOTIONS_ENABLED, EMOTION_ANALYSIS_ENABLED
            if not EMOTIONS_ENABLED:
                return
            from core.emotions import get_state, analyze_exchange
            if EMOTION_ANALYSIS_ENABLED:
                await asyncio.to_thread(analyze_exchange, user_text, ai_text)
            else:
                await asyncio.to_thread(get_state().register_interaction)
        except Exception as e:
            logger.debug(f"[GeminiLive] Actualizare emoții eșuată: {e}")

    # ─────────────────────────────────────────────────────────
    # SESIUNE PRINCIPALĂ
    # ─────────────────────────────────────────────────────────

    async def run_session(self, mic_queue: asyncio.Queue) -> None:
        if not self._initialized:
            logger.error("❌ [GeminiLive] Neinițializat!")
            return

        self._session_active   = True
        self._ai_is_speaking   = False
        self._close_after_turn = False
        self._last_turn_end    = time.time()   # Countdown porneste imediat
        self._interrupt_energy    = 0.0
        self._last_audio_write_ts = 0.0
        self._current_user_transcript = ""
        self._current_ai_transcript   = ""
        self._focus_mode = False
        self._connection_lost     = False
        self._ended_intentionally = False
        self._turn_speech_start_ts = 0.0
        self._echo_calib_samples   = []
        self._echo_baseline_rms    = 0.0
        self._resumption_handle    = None   # sesiune noua = fara istoric de reluat
        self._stop_playback.clear()

        # Flush audio vechi
        while not self._audio_out_queue.empty():
            try: self._audio_out_queue.get_nowait()
            except asyncio.QueueEmpty: break

        # Delay wake word
        if LIVE_START_DELAY_MS > 0:
            deadline = asyncio.get_event_loop().time() + LIVE_START_DELAY_MS / 1000.0
            while asyncio.get_event_loop().time() < deadline:
                try: mic_queue.get_nowait()
                except asyncio.QueueEmpty: await asyncio.sleep(0.04)

        # Config cu tools
        tools = self._build_tools()
        cfg_kw = dict(
            response_modalities=["AUDIO"],
            speech_config=self._types.SpeechConfig(
                voice_config=self._types.VoiceConfig(
                    prebuilt_voice_config=self._types.PrebuiltVoiceConfig(
                        voice_name=LIVE_VOICE
                    )
                )
            ),
            system_instruction=await self._build_system_prompt(),
            # Transcriere server-side a vorbirii — a utilizatorului (folosită
            # ca sursă de adevăr pentru comanda RAW trimisă agenților
            # specializați, vezi _handle_tool_call) și a lui Chronos (folosită
            # pentru memoria conversațională, vezi _save_conversation_turn).
            input_audio_transcription=self._types.AudioTranscriptionConfig(),
            output_audio_transcription=self._types.AudioTranscriptionConfig(),
        )
        if tools:
            cfg_kw["tools"] = tools

        T = self._types

        # ── PROACTIVE AUDIO ──
        # Modelul decide singur sa NU raspunda cand ce aude nu i se adreseaza.
        # Exact ce ne trebuie cand ecoul din boxe sau zgomotul ambiental trec
        # de filtrele noastre: in loc sa balbaie un raspuns, tace.
        if PROACTIVE_AUDIO and hasattr(T, "ProactivityConfig"):
            cfg_kw["proactivity"] = T.ProactivityConfig(proactive_audio=True)

        # ── AFFECTIVE DIALOG ──
        # Modelul detecteaza tonul emotional al lui Sergiu si isi adapteaza
        # raspunsul. Se leaga natural cu starea afectiva proprie (core/emotions).
        if AFFECTIVE_DIALOG:
            cfg_kw["enable_affective_dialog"] = True

        # ── SESSION RESUMPTION ──
        # Serverul reseteaza periodic WebSocket-ul (de aici erorile 1007 si
        # taierile in mijlocul frazei). Cu un handle de reluare, reconectarea
        # pastreaza CONTEXTUL, nu doar firul povestit inapoi modelului.
        if SESSION_RESUMPTION and hasattr(T, "SessionResumptionConfig"):
            cfg_kw["session_resumption"] = T.SessionResumptionConfig(
                handle=self._resumption_handle          # None la prima conectare
            )

        # ── CONTEXT WINDOW COMPRESSION ──
        # Fara ea, o sesiune audio moare la ~15 minute, iar tokenii audio se
        # acumuleaza cu ~25/secunda. Cu ea, sesiunea poate tine oricat, iar
        # contextul e taiat automat cand trece de prag.
        if CONTEXT_COMPRESSION and hasattr(T, "ContextWindowCompressionConfig"):
            cfg_kw["context_window_compression"] = T.ContextWindowCompressionConfig(
                trigger_tokens=CONTEXT_TRIGGER_TOKENS,
                sliding_window=T.SlidingWindow(target_tokens=CONTEXT_TARGET_TOKENS),
            )

        # ── VAD (detectia de vorbire, server-side) ──
        # silence_duration_ms mai mare = nu mai taie turul la pauzele naturale
        # dintre propozitii (una din cauzele pentru "sare propozitii").
        if hasattr(T, "RealtimeInputConfig") and hasattr(T, "AutomaticActivityDetection"):
            cfg_kw["realtime_input_config"] = T.RealtimeInputConfig(
                automatic_activity_detection=T.AutomaticActivityDetection(
                    silence_duration_ms=VAD_SILENCE_MS,
                    prefix_padding_ms=VAD_PREFIX_PADDING_MS,
                    start_of_speech_sensitivity=getattr(
                        T.StartSensitivity, VAD_START_SENSITIVITY,
                        T.StartSensitivity.START_SENSITIVITY_LOW),
                    end_of_speech_sensitivity=getattr(
                        T.EndSensitivity, VAD_END_SENSITIVITY,
                        T.EndSensitivity.END_SENSITIVITY_LOW),
                )
            )

        config = self._types.LiveConnectConfig(**cfg_kw)

        # ── Buclă de reconectare ──
        # API-ul Live pică uneori de la sine mid-conversație (ex: 1007
        # CONTENT_TYPE_AUDIO), tăind-l pe Chronos în mijlocul frazei. Nu putem
        # preveni asta din client, dar putem relua conexiunea transparent în
        # loc să aruncăm utilizatorul înapoi în wake-word mode.
        attempt = 0
        while True:
            self._connection_lost = False
            try:
                await self._run_connection(config, mic_queue, resumed=(attempt > 0))
            except Exception as e:
                logger.error(f"❌ [GeminiLive] WebSocket: {type(e).__name__}: {e}")
                self._connection_lost = True

            # Reluăm DOAR dacă legătura a picat singură, nu dacă sesiunea s-a
            # încheiat intenționat (end_session, inactivitate, auto-close, stop).
            if not self._connection_lost or self._ended_intentionally:
                break
            if attempt >= MAX_RECONNECT_ATTEMPTS:
                logger.error(
                    f"❌ [GeminiLive] Reconectare eșuată după "
                    f"{MAX_RECONNECT_ATTEMPTS} încercări — închid sesiunea."
                )
                break

            attempt += 1
            logger.warning(
                f"🔄 [GeminiLive] Conexiune pierdută → reconectare "
                f"{attempt}/{MAX_RECONNECT_ATTEMPTS}..."
            )
            self._session_active = True
            self._ai_is_speaking = False
            self._exit_focus_mode()
            self._stop_playback.clear()
            while not self._audio_out_queue.empty():
                try: self._audio_out_queue.get_nowait()
                except asyncio.QueueEmpty: break
            await asyncio.sleep(RECONNECT_DELAY)

        self._session_active = False
        self._ai_is_speaking = False
        self._exit_focus_mode()   # dezarmează wake-interrupt dacă a rămas armat
        if self._ET:
            await self.bus.publish(
                self._ET.AUDIO_RESPONSE_END, {"completed": True}
            )
        print("\n✅ Sesiune vocală încheiată. Spune 'Jarvis' pentru a relua.")

    async def _run_connection(self, config, mic_queue: asyncio.Queue,
                              resumed: bool = False) -> None:
        """O singură conexiune WebSocket. Se întoarce când sesiunea s-a
        încheiat (intenționat sau prin pierderea legăturii)."""
        async with self._client.aio.live.connect(
            model=LIVE_MODEL, config=config
        ) as session:
            logger.info("🔗 [GeminiLive] Sesiune WebSocket deschisă.")
            if resumed:
                if self._resumption_handle:
                    # Serverul a restaurat singur contextul din handle —
                    # nu mai repovestim nimic, ar fi redundant si confuz.
                    logger.info("🔄 [GeminiLive] Sesiune reluata cu context pastrat.")
                else:
                    # Fara handle: ii dam inapoi firul, ca sa nu para amnezic.
                    await self._seed_after_reconnect(session)
            else:
                print(
                    f"\n🎙️  Chronos ascultă!\n"
                    f"   Timeout {LIVE_INACTIVITY_TIMEOUT}s | "
                    f"Spune 'pa' pentru a închide"
                )

            if self._ET:
                await self.bus.publish(
                    self._ET.AUDIO_LISTENING_START, {"source": "gemini_live"}
                )

            playback_task = asyncio.create_task(
                self._playback_loop(), name="live_playback"
            )
            send_task = asyncio.create_task(
                self._send_mic_loop(session, mic_queue), name="live_send"
            )
            receive_task = asyncio.create_task(
                self._receive_loop(session), name="live_receive"
            )
            wake_task = None
            if self._ET:
                wake_task = asyncio.create_task(
                    self._wake_interrupt_listener(session), name="live_wake_interrupt"
                )

            # send_task controlează durata sesiunii
            try:
                await send_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"❌ [GeminiLive] send_task: {type(e).__name__}: {e}")
            finally:
                self._session_active = False
                if wake_task and not wake_task.done():
                    wake_task.cancel()
                    try: await asyncio.wait_for(wake_task, timeout=1.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError): pass
                if not receive_task.done():
                    receive_task.cancel()
                    try: await asyncio.wait_for(receive_task, timeout=1.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError): pass

                self._stop_playback.set()
                await self._audio_out_queue.put(_END_SENTINEL)
                try: await asyncio.wait_for(playback_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    playback_task.cancel()

    async def _seed_after_reconnect(self, session) -> None:
        """După o reconectare, îi dăm modelului firul conversației înapoi."""
        user_txt = self._current_user_transcript.strip()
        ai_txt   = self._current_ai_transcript.strip()
        note = (
            "[SISTEM] Legătura a picat o clipă și s-a reluat automat. "
            "NU-i pomeni lui Sergiu nimic tehnic despre asta."
        )
        if ai_txt:
            note += f" Erai în mijlocul frazei: \"{ai_txt}\". Continuă de acolo, natural."
        elif user_txt:
            note += f" Sergiu tocmai spusese: \"{user_txt}\". Răspunde-i."
        else:
            note += " Așteaptă în tăcere să spună ceva."
        try:
            await session.send_client_content(
                turns=self._types.Content(
                    role="user", parts=[self._types.Part(text=note)]
                ),
                turn_complete=bool(ai_txt or user_txt),
            )
            logger.info("🔄 [GeminiLive] Context re-injectat după reconectare.")
        except Exception as e:
            logger.warning(f"⚠️ [GeminiLive] Seed după reconectare eșuat: {e}")

    # ─────────────────────────────────────────────────────────
    # ECHO GUARD — audio-ul e "activ" (boxele redau ceva) cât timp
    # Gemini generează SAU cât timp coada de redare bufferizată nu s-a
    # golit încă (+ un mic tail de gardă pentru latența driverului audio).
    # ─────────────────────────────────────────────────────────

    def _ai_audio_active(self) -> bool:
        if self._ai_is_speaking:
            return True
        return (time.time() - self._last_audio_write_ts) < INTERRUPT_ECHO_TAIL

    # ─────────────────────────────────────────────────────────
    # SEND MIC → GEMINI (cu echo prevention + inactivity fix)
    # ─────────────────────────────────────────────────────────

    async def _send_mic_loop(self, session, mic_queue: asyncio.Queue) -> None:
        """
        Timer inactivitate CORECT:
        ┌──────────────────────────────────────────────────────────┐
        │ AI VORBESTE  → timer PAUZAT (nu se numără, nu se resetează) │
        │ AI terminat  → _last_turn_end = now (countdown porneste)   │
        │ User vorbeste → _last_turn_end = now (reset countdown)     │
        │ Tăcere > TIMEOUT (după AI response) → sesiune terminată    │
        └──────────────────────────────────────────────────────────┘
        """
        logger.debug("[GeminiLive:send] Loop pornit.")
        chunks_sent   = 0
        warned_close  = False
        np            = self._np

        # Ceasul de inactivitate pornește ABIA ACUM, cand chiar incepem sa
        # ascultam. Daca ar porni de la wake word, tot ce se intampla intre
        # timp (pauza muzica, handshake WebSocket, construirea promptului) s-ar
        # scadea din timpul lui Sergiu — iar la un setup mai lent decat
        # LIVE_INACTIVITY_TIMEOUT sesiunea se inchidea instant, cu 0 chunks.
        self._last_turn_end = time.time()

        while self._session_active:
            # ── VERIFICARE INACTIVITATE — rulează la FIECARE iterație ──
            # Bug vechi: verificarea stătea în ramura `except TimeoutError`, dar
            # microfonul livrează chunk-uri CONTINUU (la ~80ms), deci
            # `mic_queue.get()` nu expira niciodată → codul ăsta nu rula deloc și
            # sesiunea nu se închidea singură decât dacă ziceai „pa".
            if not self._ai_audio_active():
                elapsed   = time.time() - self._last_turn_end
                remaining = LIVE_INACTIVITY_TIMEOUT - elapsed

                if 0 < remaining <= 3.0 and not warned_close:
                    warned_close = True
                    print(f"\n⏰ Sesiunea se închide în ~{int(remaining)}s...")

                if elapsed > LIVE_INACTIVITY_TIMEOUT:
                    logger.info(
                        f"⏰ [GeminiLive] {elapsed:.0f}s fără vorbire reală "
                        f"(prag={LIVE_INACTIVITY_TIMEOUT}s) → sesiune terminată."
                    )
                    self._ended_intentionally = True
                    self._session_active = False
                    break

            try:
                chunk = await asyncio.wait_for(mic_queue.get(), timeout=0.3)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [GeminiLive:send] mic_queue: {e}")
                break

            # ══ Avem un chunk audio din microfon ══

            # ── ECHO PREVENTION: AI vorbeste SAU boxele încă redau coada → nu trimitem ──
            if self._ai_audio_active():
                # FOCUS MODE: livrează ceva important → barge-in-ul pe voce e
                # complet dezactivat. Întreruperea se face DOAR prin wake word
                # (vezi _handle_wake_interrupt), ca zgomotul ambiental sau ecoul
                # din boxe să nu taie răspunsul la jumătate.
                if self._focus_mode:
                    continue

                rms = _rms(chunk, np)
                chunk_dur = len(chunk) / float(LIVE_SAMPLE_RATE_IN)

                # ── CALIBRARE ECOU: primele INTERRUPT_CALIBRATION_MS ale turului ──
                # E statistic imposibil ca Sergiu să fi apucat deja să te
                # întrerupă la câteva zeci de ms de la primul sunet — deci ce
                # măsurăm acum e nivelul de ecou din boxele lui. Nu acumulăm
                # energie de interrupt pe durata asta, doar calibrăm pragul.
                # ── BASELINE DE ECOU: percentila pe fereastra glisanta ──
                # O calibrare doar pe primele 500ms nu ajunge — vorbirea are
                # dinamica mare, iar daca raspunsul incepe incet, pragul ramane
                # jos si mai tarziu propriul ecou al lui Chronos il depaseste,
                # deci se autoîntrerupe ("sare propozitii").
                # Nici "actualizeaza doar sub prag" nu merge: pragul si-ar bloca
                # propria crestere. Solutia: urmarim anvelopa ultimelor ~2s si
                # luam o percentila inalta. Ecoul, fiind continuu, ridica
                # percentila odata cu el; o interventie scurta a lui Sergiu
                # (0.6s din 2s de fereastra) NU apuca sa o miste, deci trece
                # de prag si intrerupe normal.
                # Cat timp NU banuim o intrerupere, fereastra urmareste ecoul.
                # Din momentul in care energia incepe sa se acumuleze, INGHETAM
                # baseline-ul: altfel vocea lui Sergiu ar intra in fereastra,
                # ar ridica pragul peste ea insasi si intreruperea n-ar mai
                # trece niciodata.
                if self._interrupt_energy <= 0.0:
                    self._echo_calib_samples.append(rms)
                    if len(self._echo_calib_samples) > ECHO_WINDOW_CHUNKS:
                        self._echo_calib_samples = self._echo_calib_samples[-ECHO_WINDOW_CHUNKS:]

                elapsed_turn = time.time() - self._turn_speech_start_ts
                if elapsed_turn < (INTERRUPT_CALIBRATION_MS / 1000.0):
                    # Fereastra de bootstrap: doar ascultam, nu acuzam pe nimeni
                    self._echo_baseline_rms = max(self._echo_calib_samples)
                    continue

                self._echo_baseline_rms = float(
                    np.percentile(self._echo_calib_samples, ECHO_PERCENTILE)
                )
                effective_threshold = max(
                    INTERRUPT_AMPLITUDE_THRESHOLD,
                    self._echo_baseline_rms * INTERRUPT_ECHO_MARGIN
                )

                if rms > effective_threshold:
                    # Acumulăm energie de vorbire. Nu resetăm la zero la orice
                    # scădere - pauzele naturale din vorbire nu trebuie să
                    # anuleze un interrupt real aflat deja în desfășurare.
                    self._interrupt_energy = min(
                        INTERRUPT_MIN_DURATION,
                        self._interrupt_energy + chunk_dur
                    )
                    logger.debug(
                        f"[GeminiLive:send] 🗣️ Energie interrupt="
                        f"{self._interrupt_energy:.2f}/{INTERRUPT_MIN_DURATION:.2f}s "
                        f"RMS={rms:.0f} (prag efectiv={effective_threshold:.0f}, "
                        f"ecou={self._echo_baseline_rms:.0f})"
                    )
                else:
                    self._interrupt_energy = max(
                        0.0,
                        self._interrupt_energy - chunk_dur * INTERRUPT_DECAY_RATE
                    )

                if self._interrupt_energy >= INTERRUPT_MIN_DURATION:
                    # Interrupt REAL
                    logger.info(
                        f"🗣️ [GeminiLive] INTERRUPT confirmat. RMS={rms:.0f} "
                        f"(prag efectiv={effective_threshold:.0f})"
                    )
                    self._stop_playback.set()
                    while not self._audio_out_queue.empty():
                        try:
                            item = self._audio_out_queue.get_nowait()
                            if item is _END_SENTINEL:
                                await self._audio_out_queue.put(_END_SENTINEL)
                                break
                        except asyncio.QueueEmpty:
                            break
                    self._ai_is_speaking = False
                    self._last_audio_write_ts = 0.0  # eliberăm imediat echo guard-ul
                    await asyncio.sleep(0.05)
                    self._stop_playback.clear()
                    self._interrupt_energy = 0.0
                    # Trimitem chunk-ul ACUM că am întrerupt
                    try:
                        await session.send_realtime_input(
                            audio=self._types.Blob(
                                data=_pcm_bytes(chunk, np),
                                mime_type=f"audio/pcm;rate={LIVE_SAMPLE_RATE_IN}"
                            )
                        )
                        chunks_sent += 1
                        self._last_turn_end = time.time()
                        warned_close = False
                    except Exception as e:
                        logger.error(f"❌ [GeminiLive:send] post-interrupt: {e}")
                        self._session_active = False
                        break

                continue  # Skip trimitere normală cât timp AI vorbeste

            # ── NORMAL: AI nu vorbeste, trimitem audio ──
            self._interrupt_energy = 0.0

            # Trimitem TOT audio-ul (VAD-ul serverului are nevoie de stream
            # continuu), dar countdown-ul de inactivitate îl resetăm DOAR când
            # chiar se aude cineva vorbind. Altfel liniștea din cameră ținea
            # sesiunea deschisă la nesfârșit, pentru că microfonul livrează
            # chunk-uri non-stop indiferent dacă vorbești sau nu.
            rms_now = _rms(chunk, np)

            try:
                await session.send_realtime_input(
                    audio=self._types.Blob(
                        data=_pcm_bytes(chunk, np),
                        mime_type=f"audio/pcm;rate={LIVE_SAMPLE_RATE_IN}"
                    )
                )
                chunks_sent += 1
                if rms_now > VOICE_ACTIVITY_THRESHOLD:
                    self._last_turn_end = time.time()   # se aude vorbire → reset
                    warned_close = False
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [GeminiLive:send] trimitere: {type(e).__name__}: {e}")
                self._connection_lost = True   # legătura a picat → merită reconectare
                self._session_active = False
                break

        logger.info(f"[GeminiLive:send] Terminat. Chunks: {chunks_sent}")

    # ─────────────────────────────────────────────────────────
    # RECEIVE ← GEMINI (re-entrant multi-turn)
    # ─────────────────────────────────────────────────────────

    async def _receive_loop(self, session) -> None:
        """
        Re-entrant receive: session.receive() se termină după fiecare turn_complete.
        while loop re-intră pentru turnul următor.
        """
        logger.debug("[GeminiLive:recv] Loop pornit.")
        total  = 0
        turns  = 0

        while self._session_active:
            try:
                async for response in session.receive():
                    if not self._session_active:
                        return

                    total += 1

                    # Handle nou de reluare — il pastram pentru o eventuala
                    # reconectare (nu il logam la fiecare update, vine des).
                    sru = getattr(response, "session_resumption_update", None)
                    if sru is not None and getattr(sru, "resumable", False):
                        h = getattr(sru, "new_handle", None)
                        if h:
                            self._resumption_handle = h

                    sc = getattr(response, "server_content", None)
                    tc = getattr(response, "tool_call", None)

                    # ── Tool Call ──
                    if tc:
                        continue_session = await self._handle_tool_call(session, tc)
                        if not continue_session:
                            logger.info("[GeminiLive:recv] end_session → opresc sesiunea.")
                            self._ended_intentionally = True
                            self._session_active = False
                            return
                        continue

                    if not sc:
                        continue

                    interrupted    = getattr(sc, "interrupted",   False)
                    turn_complete  = getattr(sc, "turn_complete", False)
                    mt             = getattr(sc, "model_turn",    None)
                    in_transcript  = getattr(sc, "input_transcription",  None)
                    out_transcript = getattr(sc, "output_transcription", None)

                    # ── Transcript VERBATIM al utilizatorului (ASR) ──
                    # Acumulăm deltas-urile de text pe măsură ce vin — vor fi
                    # folosite ca și comandă RAW în _handle_tool_call, în loc
                    # de argumentul (posibil parafrazat) din tool call.
                    if in_transcript and getattr(in_transcript, "text", None):
                        self._current_user_transcript += in_transcript.text
                        # ASR-ul serverului a recunoscut CUVINTE reale (nu zgomot)
                        # → cel mai sigur semnal că Sergiu chiar vorbește.
                        self._last_turn_end = time.time()

                    # ── Transcript al vocii lui Chronos (ASR pe audio-ul redat) ──
                    # NU folosim mt.parts[].text pentru asta — acela e text de
                    # "thinking"/instrumentare internă, nu ce a spus efectiv.
                    if out_transcript and getattr(out_transcript, "text", None):
                        self._current_ai_transcript += out_transcript.text

                    # Barge-in server-side (rar cu echo prevention)
                    if interrupted:
                        logger.info("⛔ [GeminiLive] Barge-in (interrupted). Anulez auto-close pentru că utilizatorul dorește altceva.")
                        self._ai_is_speaking   = False
                        self._close_after_turn = False  # Întreruperea anulează auto-close!
                        self._last_turn_end    = time.time()
                        self._exit_focus_mode()
                        self._current_user_transcript = ""  # turul e anulat, transcriptul e stale
                        self._current_ai_transcript   = ""
                        continue

                    # Audio / text de la model
                    if mt:
                        if not self._ai_is_speaking:
                            # Începe un tur nou de vorbire → recalibrăm ecoul
                            self._turn_speech_start_ts = time.time()
                            self._echo_calib_samples = []
                        self._ai_is_speaking = True
                        for part in (mt.parts or []):
                            id_ = getattr(part, "inline_data", None)
                            if id_ and getattr(id_, "data", None):
                                await self._audio_out_queue.put(id_.data)
                            txt = getattr(part, "text", None)
                            if txt and txt.strip():
                                logger.debug(f"[GeminiLive:recv] Text: {txt.strip()[:80]!r}")

                    # Turn complet
                    if turn_complete:
                        turns += 1
                        self._ai_is_speaking = False
                        self._last_turn_end  = time.time()
                        # Răspunsul important s-a livrat → barge-in normal înapoi
                        self._exit_focus_mode()
                        await self._save_conversation_turn()
                        self._current_user_transcript = ""  # gata cu turul asta, nu se scurge în urmatorul
                        self._current_ai_transcript   = ""
                        await self._audio_out_queue.put(_FLUSH_SENTINEL)
                        logger.info(
                            f"✅ [GeminiLive] Turn #{turns} complet. "
                            f"Countdown {LIVE_INACTIVITY_TIMEOUT}s pornit."
                        )

                        # Auto-close automat după ce comanda de acțiune a fost confirmată
                        if self._close_after_turn:
                            logger.info("👋 [GeminiLive] Auto-close activat după executarea acțiunii → Închid sesiunea.")
                            self._close_after_turn = False
                            self._ended_intentionally = True
                            self._session_active = False
                            return

                # session.receive() s-a terminat → re-intrăm pentru turnul următor
                if self._session_active:
                    await asyncio.sleep(0.05)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.info(f"ℹ️ [GeminiLive:recv] Conexiune încheiată ({type(e).__name__}): {e}")
                self._connection_lost = True   # legătura a picat → merită reconectare
                self._session_active = False
                break

        logger.info(f"[GeminiLive:recv] Terminat. {total} răspunsuri, {turns} turnuri.")
        self._ai_is_speaking = False

    # ─────────────────────────────────────────────────────────
    # PLAYBACK
    # ─────────────────────────────────────────────────────────

    async def _playback_loop(self) -> None:
        buffer = bytearray()
        # Definit ÎNAINTE de try: dacă RawOutputStream aruncă, blocul `finally`
        # atingea `stream` nedefinit și ridica UnboundLocalError peste eroarea
        # reală, ascunzând-o complet.
        stream = None
        loop = asyncio.get_running_loop()
        try:
            stream = self._sd.RawOutputStream(
                samplerate=LIVE_SAMPLE_RATE_OUT,
                channels=1,
                dtype="int16",
                blocksize=1024,
            )
            stream.start()
            logger.debug("[GeminiLive:play] Stream pornit.")

            if self._ET:
                await self.bus.publish(self._ET.AUDIO_RESPONSE_START, {})

            async def _write(data_bytes: bytes) -> None:
                # Marchează momentul scrierii REALE către boxe — folosit de
                # _ai_audio_active() ca "echo guard" cât timp mai iese sunet.
                #
                # Thread PROPRIU, nu asyncio.to_thread: `stream.write` blochează
                # până când placa de sunet acceptă datele, iar pool-ul default e
                # partajat cu Flask, cu apelurile agenților și cu analiza de
                # emoții. Sub încărcare, redarea ajungea să aștepte un worker
                # liber — adică exact întreruperi audibile în vocea lui Chronos.
                await loop.run_in_executor(self._play_pool, stream.write, data_bytes)
                self._last_audio_write_ts = time.time()

            while True:
                try:
                    data = await asyncio.wait_for(
                        self._audio_out_queue.get(), timeout=0.2
                    )
                except asyncio.TimeoutError:
                    if buffer and not self._stop_playback.is_set():
                        try: await _write(bytes(buffer))
                        except Exception: pass
                        buffer.clear()
                    continue

                if data is _END_SENTINEL:
                    if buffer and not self._stop_playback.is_set():
                        try: await _write(bytes(buffer))
                        except Exception: pass
                    break

                if data is _FLUSH_SENTINEL:
                    if buffer and not self._stop_playback.is_set():
                        try: await _write(bytes(buffer))
                        except Exception: pass
                        buffer.clear()
                    continue

                if self._stop_playback.is_set():
                    buffer.clear()
                    while not self._audio_out_queue.empty():
                        try:
                            item = self._audio_out_queue.get_nowait()
                            if item is _END_SENTINEL:
                                await self._audio_out_queue.put(_END_SENTINEL)
                                break
                            if item is _FLUSH_SENTINEL:
                                break
                        except asyncio.QueueEmpty:
                            break
                    continue

                buffer.extend(data)
                while len(buffer) >= LIVE_PLAYBACK_CHUNK_BYTES:
                    if self._stop_playback.is_set():
                        buffer.clear()
                        break
                    chunk = bytes(buffer[:LIVE_PLAYBACK_CHUNK_BYTES])
                    buffer = bytearray(buffer[LIVE_PLAYBACK_CHUNK_BYTES:])
                    try: await _write(chunk)
                    except Exception as e:
                        logger.error(f"❌ [GeminiLive:play] Write: {e}")
                        break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ [GeminiLive:play] {e}", exc_info=True)
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception as e:
                    logger.debug(f"[GeminiLive:play] Închidere stream: {e}")
            self._ai_is_speaking = False
            logger.debug("[GeminiLive:play] Stream închis.")

    # ─────────────────────────────────────────────────────────
    # CONTROL PUBLIC
    # ─────────────────────────────────────────────────────────

    def _spawn(self, coro, name: str = "") -> asyncio.Task:
        """Lansează un task de fundal și îi păstrează referința.

        `asyncio.create_task` păstrează doar o referință SLABĂ. Un task fără
        referință tare poate fi colectat de GC în plină execuție — salvarea
        turului în memorie sau comanda trimisă agenților dispăreau pur și
        simplu, fără nicio urmă în loguri.
        """
        task = asyncio.create_task(coro, name=name or None)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def shutdown(self) -> None:
        """Eliberează threadurile și task-urile deținute de sesiune."""
        self.stop_session()
        for task in tuple(self._bg_tasks):
            if not task.done():
                task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()
        self._play_pool.shutdown(wait=False)
        self._tool_pool.shutdown(wait=False)

    def interrupt(self) -> None:
        self._stop_playback.set()
        self._ai_is_speaking = False
        self._last_audio_write_ts = 0.0

    def stop_session(self) -> None:
        self._ended_intentionally = True   # oprire cerută explicit → fără reconectare
        self._session_active = False
        self._stop_playback.set()

    @property
    def is_active(self) -> bool:
        return self._session_active

    @property
    def is_initialized(self) -> bool:
        return self._initialized
