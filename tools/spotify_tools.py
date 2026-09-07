"""
tools/spotify_tools.py — Spotify & Home Assistant Audio Tools
==============================================================
Funcții directe pentru controlul redării pe difuzor / Spotify prin Home Assistant REST API.
"""

import logging
import re

import requests
from config import HA_URL, HA_TOKEN

logger = logging.getLogger(__name__)

# Sesiune HTTP partajată: fără ea, fiecare comandă (pauză la începutul fiecărei
# sesiuni vocale, resume la final) plătea un handshake TCP nou către Home
# Assistant. Cu keep-alive, socketul rămâne cald între comenzi.
_session = requests.Session()
_session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4))
_session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=2, pool_maxsize=4))
_session.headers["Connection"] = "keep-alive"

SPEAKER_NAME = "Sergiu speaker"
_was_playing_before_pause = False


def _gazda(url: str) -> str:
    """host:port din URL, pentru mesaje de eroare pe care le intelege un om."""
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else (url or "necunoscut")


# Cat asteptam dupa Home Assistant. Scurt intentionat: e in reteaua locala,
# daca nu raspunde in cateva secunde nu e acolo, iar tu astepti in fata unui
# microfon.
_TIMEOUT = 4


def send_google_command(command_text: str) -> tuple[bool, str]:
    """
    Trimite o comanda vocala text catre Google Assistant SDK prin Home Assistant.

    Daca prima incercare esueaza cu o eroare HTTP, reincearca fara numele
    difuzorului — uneori Google nu recunoaste tinta. Daca insa esueaza
    CONEXIUNEA, nu reincercam: acelasi host inaccesibil da acelasi rezultat,
    doar dupa inca un timeout. (Inainte se reincerca oricum, deci asteptai
    dublu ca sa primesti aceeasi eroare.)
    """
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }
    gazda = _gazda(HA_URL)

    def _trimite(text: str) -> tuple[bool, str, bool]:
        """(reusit, mesaj, a_fost_problema_de_retea)"""
        try:
            r = _session.post(HA_URL, headers=headers,
                              json={"command": text}, timeout=_TIMEOUT)
            if r.status_code == 200:
                return True, "OK", False
            if r.status_code in (401, 403):
                return False, f"Home Assistant refuza tokenul ({r.status_code}).", False
            return False, f"Home Assistant a raspuns {r.status_code}.", False
        except requests.exceptions.Timeout:
            return False, f"Home Assistant ({gazda}) nu raspunde.", True
        except requests.exceptions.ConnectionError:
            return False, f"Nu ajung la Home Assistant ({gazda}).", True
        except Exception as e:
            return False, f"Eroare la Home Assistant: {type(e).__name__}", True

    # 1) cu numele difuzorului
    intreg = f"{command_text} on {SPEAKER_NAME}"
    reusit, mesaj, retea = _trimite(intreg)
    if reusit:
        logger.info(f"✅ [Spotify Tools] Trimis la Google: '{intreg}'")
        return True, "OK"

    if retea:
        # Host mort — a doua incercare ar astepta degeaba inca un timeout.
        logger.error(f"❌ [Spotify Tools] {mesaj}")
        return False, mesaj

    logger.warning(f"⚠️ [Spotify Tools] {mesaj} Incerc fara numele difuzorului...")

    # 2) fara numele difuzorului
    reusit, mesaj, _ = _trimite(command_text)
    if reusit:
        logger.info(f"✅ [Spotify Tools] Trimis la Google (fara difuzor): '{command_text}'")
        return True, "OK (fallback)"

    logger.error(f"❌ [Spotify Tools] {mesaj}")
    return False, mesaj


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
