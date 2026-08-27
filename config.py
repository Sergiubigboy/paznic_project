"""
config.py — Chronos Configurare Centrală
==========================================
Citește SECRETELE din .env și PERSONALIZAREA din personalization.py.

Ierarhie setări:
    .env              → secrete (API keys, IP-uri, tokens)
    personalization.py → personalizare (voce, prompt, timeouts)
    config.py         → re-exportă totul pentru restul codului

Nu editează config.py direct. Editează:
    .env              — pentru chei și date personale
    personalization.py — pentru voce, personalitate, parametri
"""

import os
from pathlib import Path

# Încărcăm .env dacă există (python-dotenv sau manual)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        # python-dotenv nu e instalat — citim manual
        with open(_env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

# ─────────────────────────────────────────────────────────────
# CHEI API (din .env)
# ─────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI  = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SPOTIFY_DEVICE_NAME   = os.environ.get("SPOTIFY_DEVICE_NAME", "SystemVoice")

HA_URL   = os.environ.get("HA_URL", "")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

# Telegram — opțional. Fără ele, tool-ul de trimitere raportează curat că nu
# e configurat (nu crapă). Token de la @BotFather, chat id de la @userinfobot.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# Obsidian — unde se exportă oglinda Markdown a datelor (opțional).
# JSON-ul rămâne sursa de adevăr; vault-ul e doar pentru citit/răsfoit.
OBSIDIAN_VAULT_PATH = os.environ.get("OBSIDIAN_VAULT_PATH", "")

# ─────────────────────────────────────────────────────────────
# REȚEA (din .env)
# ─────────────────────────────────────────────────────────────
WLED_IP_MAIN  = os.environ.get("WLED_IP_MAIN",  "192.168.68.101")
WLED_IP_FLOOR = os.environ.get("WLED_IP_FLOOR", "192.168.68.102")
JBL_IP        = os.environ.get("JBL_IP",        "192.168.1.104")
HTTP_PORT     = int(os.environ.get("HTTP_PORT",  "8000"))

# ─────────────────────────────────────────────────────────────
# PERSONALIZARE (din personalization.py)
# ─────────────────────────────────────────────────────────────
try:
    from personalization import (
        # Voce Live
        LIVE_MODEL,
        LIVE_VOICE,
        LIVE_SAMPLE_RATE_IN,
        LIVE_SAMPLE_RATE_OUT,
        LIVE_INACTIVITY_TIMEOUT,
        LIVE_START_DELAY_MS,
        LIVE_AUDIO_QUEUE_SIZE,
        LIVE_PLAYBACK_CHUNK_BYTES,
        INTERRUPT_AMPLITUDE_THRESHOLD,
        INTERRUPT_MIN_DURATION,
        INTERRUPT_DECAY_RATE,
        INTERRUPT_ECHO_TAIL,
        INTERRUPT_CALIBRATION_MS,
        INTERRUPT_ECHO_MARGIN,
        VOICE_ACTIVITY_THRESHOLD,
        # Personalitate
        SYSTEM_PROMPT,
        SYSTEM_PROMPT_TEXT,
        SYSTEM_PROMPT_VOICE,
        CHRONOS_NAME,
        # Wake Word
        WAKE_WORD_MODEL,
        WAKE_WORD_THRESHOLD_JARVIS,
        WAKE_WORD_THRESHOLD_OTHER,
        WAKE_WORD_CONFIRMATION_FRAMES,
        WAKE_WORD_COOLDOWN,
        # TTS Fallback
        TTS_VOICE_FALLBACK,
        TTS_RATE,
        TTS_SPEAK_TERMINAL_REPLIES,
        DISPATCHER_TIMEOUT,
        # Platformă
        RASPBERRY_PI,
        # LLM Modele
        GEMINI_MODEL_DEFAULT,
        GEMINI_MODEL_LOGGER,
        GEMINI_MODEL_DJ,
        # Emoții
        EMOTIONS_ENABLED,
        EMOTION_ANALYSIS_ENABLED,
        EMOTION_BASELINE,
        EMOTION_HALFLIFE_MIN,
        BOREDOM_PER_HOUR,
        EMOTION_MAX_DELTA,
        # Profil
        PROFILE_ENABLED,
        PROFILE_REFRESH_HOURS,
        # Ziua pe Telegram
        DAY_TELEGRAM_ENABLED,
        DAY_NOTIFY_LEAD_MIN,
        # Live API avansat
        PROACTIVE_AUDIO,
        AFFECTIVE_DIALOG,
        SESSION_RESUMPTION,
        CONTEXT_COMPRESSION,
        CONTEXT_TRIGGER_TOKENS,
        CONTEXT_TARGET_TOKENS,
        VAD_SILENCE_MS,
        VAD_PREFIX_PADDING_MS,
        VAD_START_SENSITIVITY,
        VAD_END_SENSITIVITY,
    )
except ImportError as e:
    import warnings
    warnings.warn(f"personalization.py lipsă sau eroare: {e}. Folosesc defaults.")
    # ── Defaults dacă personalization.py lipsește ──
    LIVE_MODEL               = "gemini-2.5-flash-native-audio-latest"
    LIVE_VOICE               = "Charon"
    LIVE_SAMPLE_RATE_IN      = 16000
    LIVE_SAMPLE_RATE_OUT     = 24000
    LIVE_INACTIVITY_TIMEOUT  = 15.0
    LIVE_START_DELAY_MS      = 400
    LIVE_AUDIO_QUEUE_SIZE    = 500
    LIVE_PLAYBACK_CHUNK_BYTES = 2048
    INTERRUPT_AMPLITUDE_THRESHOLD = 1500
    INTERRUPT_MIN_DURATION   = 0.6
    INTERRUPT_DECAY_RATE     = 0.4
    INTERRUPT_ECHO_TAIL      = 0.35
    INTERRUPT_CALIBRATION_MS = 500
    INTERRUPT_ECHO_MARGIN    = 2.2
    VOICE_ACTIVITY_THRESHOLD = 900
    SYSTEM_PROMPT            = "Ești Chronos, asistentul AI. Răspunzi în română."
    SYSTEM_PROMPT_TEXT       = SYSTEM_PROMPT
    SYSTEM_PROMPT_VOICE      = SYSTEM_PROMPT
    CHRONOS_NAME             = "Chronos"
    WAKE_WORD_MODEL          = "hey_jarvis"
    WAKE_WORD_THRESHOLD_JARVIS = 0.75
    WAKE_WORD_THRESHOLD_OTHER  = 0.90
    WAKE_WORD_CONFIRMATION_FRAMES = 2
    WAKE_WORD_COOLDOWN       = 3.0
    TTS_VOICE_FALLBACK       = "ro-RO-EmilNeural"
    TTS_RATE                 = "+0%"
    TTS_SPEAK_TERMINAL_REPLIES = False
    DISPATCHER_TIMEOUT       = 35.0
    RASPBERRY_PI             = False
    GEMINI_MODEL_DEFAULT     = "gemini-2.5-flash"
    GEMINI_MODEL_LOGGER      = "gemini-2.5-flash"
    GEMINI_MODEL_DJ          = "gemini-2.5-flash"
    EMOTIONS_ENABLED         = True
    EMOTION_ANALYSIS_ENABLED = True
    EMOTION_BASELINE     = {"nervozitate": 15, "bucurie": 50, "plictiseala": 20, "afectiune": 55}
    EMOTION_HALFLIFE_MIN = {"nervozitate": 25, "bucurie": 90, "plictiseala": 0, "afectiune": 720}
    BOREDOM_PER_HOUR         = 12
    EMOTION_MAX_DELTA        = 30
    PROFILE_ENABLED          = True
    PROFILE_REFRESH_HOURS    = 24
    DAY_TELEGRAM_ENABLED     = True
    DAY_NOTIFY_LEAD_MIN      = 0
    PROACTIVE_AUDIO          = False
    AFFECTIVE_DIALOG         = True
    SESSION_RESUMPTION       = True
    CONTEXT_COMPRESSION      = True
    CONTEXT_TRIGGER_TOKENS   = 16000
    CONTEXT_TARGET_TOKENS    = 8000
    VAD_SILENCE_MS           = 700
    VAD_PREFIX_PADDING_MS    = 300
    VAD_START_SENSITIVITY    = "START_SENSITIVITY_LOW"
    VAD_END_SENSITIVITY      = "END_SENSITIVITY_LOW"

# ─────────────────────────────────────────────────────────────
# PARAMETRI AUDIO LEGACY (compatibilitate cu codul vechi)
# ─────────────────────────────────────────────────────────────
SAMPLE_RATE               = LIVE_SAMPLE_RATE_IN
SILENCE_THRESHOLD         = 150
SILENCE_DURATION          = 2
MIN_RECORD_SECONDS        = 1.0
MAX_RECORD_SECONDS        = 15.0
OWW_DETECTION_THRESHOLD   = WAKE_WORD_THRESHOLD_JARVIS
OWW_CONFIRMATION_FRAMES   = WAKE_WORD_CONFIRMATION_FRAMES
OWW_DETECTION_COOLDOWN    = WAKE_WORD_COOLDOWN
STT_LANGUAGE              = "ro-RO"
GEMINI_BASE_URL           = "https://generativelanguage.googleapis.com/v1beta/models"
