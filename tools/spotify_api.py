"""
tools/spotify_api.py — Spotify Web API (control REAL de playback)
====================================================================
Pauză/resume REALE prin Spotify Web API — spre deosebire de trucul din
spotify_tools.py (comandă vocală broadcast către Google Assistant), care
doar "anunță" fraza către difuzor și cauzează un scurt "ducking" de volum,
NU o pauză reală de playback.

Autorizare: OAuth Authorization Code Flow prin spotipy.SpotifyOAuth.
La prima folosire se deschide un browser pentru login Spotify + aprobarea
scope-urilor; tokenul (+ refresh token) se cache-uiește local în
chronos_data/.spotify_token_cache (gitignored) și se reînnoiește
automat după aceea — fără să mai fie nevoie de login din nou.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent.parent / "chronos_data" / ".spotify_token_cache"
_SCOPE = "user-modify-playback-state user-read-playback-state"

_sp = None
_sp_init_failed = False


def _get_client():
    """Lazy singleton — inițializează clientul Spotify (+ OAuth) la prima utilizare."""
    global _sp, _sp_init_failed
    if _sp is not None:
        return _sp
    if _sp_init_failed:
        return None

    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
        from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI

        if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET and SPOTIFY_REDIRECT_URI):
            logger.warning("⚠️ [Spotify API] Credențiale lipsă în .env — control direct dezactivat.")
            _sp_init_failed = True
            return None

        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=_SCOPE,
            cache_path=str(_CACHE_PATH),
            open_browser=True,
        )
        _sp = spotipy.Spotify(auth_manager=auth_manager)
        logger.info("✅ [Spotify API] Client inițializat.")
        return _sp
    except Exception as e:
        logger.error(f"❌ [Spotify API] Inițializare eșuată: {e}")
        _sp_init_failed = True
        return None


def _find_target_device_id(sp) -> Optional[str]:
    """Caută device-ul configurat (SPOTIFY_DEVICE_NAME) printre cele active la Spotify."""
    try:
        from config import SPOTIFY_DEVICE_NAME
        devices = (sp.devices() or {}).get("devices", [])
        if not devices:
            return None

        if SPOTIFY_DEVICE_NAME:
            for d in devices:
                if SPOTIFY_DEVICE_NAME.lower() in (d.get("name") or "").lower():
                    return d["id"]

        # Fallback: device-ul activ curent, altfel primul disponibil
        for d in devices:
            if d.get("is_active"):
                return d["id"]
        return devices[0]["id"]
    except Exception as e:
        logger.debug(f"[Spotify API] Nu am putut lista device-urile: {e}")
        return None


def pause_playback_api() -> bool:
    """Pauză REALĂ prin Spotify Web API. True dacă a reușit (sau era deja pe pauză)."""
    sp = _get_client()
    if not sp:
        return False
    try:
        sp.pause_playback(device_id=_find_target_device_id(sp))
        logger.info("⏸️ [Spotify API] Playback pus pe pauză.")
        return True
    except Exception as e:
        # Spotify răspunde adesea cu eroare dacă playerul e deja pe pauză — nu e o eroare reală.
        if "already paused" in str(e).lower() or "NO_ACTIVE_DEVICE" in str(e):
            logger.debug(f"[Spotify API] Pauză no-op: {e}")
            return True
        logger.error(f"❌ [Spotify API] Pauză eșuată: {e}")
        return False


def resume_playback_api() -> bool:
    """Resume REAL prin Spotify Web API. True dacă a reușit."""
    sp = _get_client()
    if not sp:
        return False
    try:
        sp.start_playback(device_id=_find_target_device_id(sp))
        logger.info("▶️ [Spotify API] Playback reluat.")
        return True
    except Exception as e:
        logger.error(f"❌ [Spotify API] Resume eșuat: {e}")
        return False
