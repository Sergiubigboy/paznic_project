"""
tools/spotify_tools.py — Spotify & Home Assistant Audio Tools
==============================================================
Funcții directe pentru controlul redării pe difuzor / Spotify prin Home Assistant REST API.
"""

import requests
import logging
from config import HA_URL, HA_TOKEN

logger = logging.getLogger(__name__)

SPEAKER_NAME = "Sergiu speaker"
_was_playing_before_pause = False


def send_google_command(command_text: str) -> tuple[bool, str]:
    """
    Trimite o comandă vocală text către Google Assistant SDK prin Home Assistant.
    Include retry fallback automat fără numele difuzorului dacă prima încercare returnează eroare.
    """
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json"
    }

    # Încercarea 1: Cu nume difuzor
    full_command = f"{command_text} on {SPEAKER_NAME}"
    payload = {"command": full_command}

    last_error = ""
    try:
        resp = requests.post(HA_URL, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"✅ [Spotify Tools] Trimis la Google: '{full_command}'")
            return True, "OK"
        else:
            last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
            logger.warning(f"⚠️ [Spotify Tools] Eroare cu nume difuzor ({last_error}). Încerc fără nume difuzor...")
    except Exception as e:
        last_error = str(e)
        logger.warning(f"⚠️ [Spotify Tools] Conexiune eșuată ({last_error}). Încerc fără nume difuzor...")

    # Încercarea 2: Fallback fără 'on SPEAKER_NAME'
    try:
        payload_simple = {"command": command_text}
        resp2 = requests.post(HA_URL, headers=headers, json=payload_simple, timeout=10)
        if resp2.status_code == 200:
            logger.info(f"✅ [Spotify Tools] Trimis la Google (fallback simplu): '{command_text}'")
            return True, "OK (fallback)"
        else:
            last_error = f"HTTP {resp2.status_code}: {resp2.text[:100]}"
            logger.error(f"❌ [Spotify Tools] Eroare HA fallback: {last_error}")
            return False, last_error
    except Exception as e:
        logger.error(f"❌ [Spotify Tools] Conexiune eșuată fallback: {e}")
        return False, str(e)


def pause_music() -> bool:
    """Pune muzica pe pauză (utilizat când se activează wake word-ul)."""
    global _was_playing_before_pause
    success, _ = send_google_command("pause the music")
    if success:
        _was_playing_before_pause = True
        logger.info("⏸️ [Spotify Tools] Muzică pusă pe pauză.")
    return success


def resume_music() -> bool:
    """Reia muzica dacă era pornită înainte de pauză."""
    global _was_playing_before_pause
    if _was_playing_before_pause:
        success, _ = send_google_command("resume the music")
        if success:
            logger.info("▶️ [Spotify Tools] Muzică reluată.")
        _was_playing_before_pause = False
        return success
    return False
