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

# Unele dispozitive (difuzoare Google Cast controlate prin Assistant) nu accepta
# comenzi de playback prin Spotify Web API: raspund 403 "Restriction violated".
# Ne amintim asta ca sa nu mai pierdem un round-trip inutil la fiecare pauza —
# mergem direct pe fallback-ul care functioneaza.
_restricted_device = False


def _is_restriction_error(e) -> bool:
    txt = str(e).lower()
    return "restriction violated" in txt or "403" in txt


def playback_control_available() -> bool:
    """False daca stim deja ca dispozitivul curent refuza comenzile API."""
    return not _restricted_device


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
    global _restricted_device
    if _restricted_device:
        return False          # stim ca nu merge — lasam fallback-ul sa preia
    sp = _get_client()
    if not sp:
        return False
    try:
        sp.pause_playback(device_id=_find_target_device_id(sp))
        logger.info("⏸️ [Spotify API] Playback pus pe pauză.")
        return True
    except Exception as e:
        if _is_restriction_error(e):
            _restricted_device = True
            logger.info("ℹ️ [Spotify API] Dispozitivul nu accepta control prin API "
                        "(difuzor Cast) — trec pe Google Assistant si nu mai reincerc.")
            return False
        # Spotify răspunde adesea cu eroare dacă playerul e deja pe pauză — nu e o eroare reală.
        if "already paused" in str(e).lower() or "NO_ACTIVE_DEVICE" in str(e):
            logger.debug(f"[Spotify API] Pauză no-op: {e}")
            return True
        logger.error(f"❌ [Spotify API] Pauză eșuată: {e}")
        return False


def resume_playback_api() -> bool:
    """Resume REAL prin Spotify Web API. True dacă a reușit."""
    global _restricted_device
    if _restricted_device:
        return False
    sp = _get_client()
    if not sp:
        return False
    try:
        sp.start_playback(device_id=_find_target_device_id(sp))
        logger.info("▶️ [Spotify API] Playback reluat.")
        return True
    except Exception as e:
        if _is_restriction_error(e):
            _restricted_device = True
            logger.info("ℹ️ [Spotify API] Dispozitivul nu accepta control prin API.")
            return False
        logger.error(f"❌ [Spotify API] Resume eșuat: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# CONTROL DIRECT — instant, ZERO apeluri LLM
# ─────────────────────────────────────────────────────────────
# Doar PORNIREA unei piese anume mai trece prin Google Assistant
# (workaround ca să meargă fără Spotify deschis). Restul controlului
# se face aici, prin API: mult mai rapid și mai fiabil.

def now_playing() -> dict:
    """Ce se aude acum: piesă, artist, album, progres."""
    sp = _get_client()
    if not sp:
        return {"status": "error", "message": "Spotify API indisponibil."}
    try:
        cur = sp.current_playback()
        if not cur or not cur.get("item"):
            return {"status": "ok", "playing": False, "message": "Nu cântă nimic acum."}

        item = cur["item"]
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        prog = int((cur.get("progress_ms") or 0) / 1000)
        dur = int((item.get("duration_ms") or 0) / 1000)
        return {
            "status": "ok",
            "playing": bool(cur.get("is_playing")),
            "track": item.get("name"),
            "artist": artists,
            "album": (item.get("album") or {}).get("name"),
            "progres": f"{prog // 60}:{prog % 60:02d} / {dur // 60}:{dur % 60:02d}",
            "volum": (cur.get("device") or {}).get("volume_percent"),
        }
    except Exception as e:
        logger.error(f"❌ [Spotify API] now_playing: {e}")
        return {"status": "error", "message": str(e)}


def next_track() -> dict:
    sp = _get_client()
    if not sp:
        return {"status": "error", "message": "Spotify API indisponibil."}
    try:
        sp.next_track(device_id=_find_target_device_id(sp))
        logger.info("⏭️ [Spotify API] Piesa următoare.")
        return {"status": "ok", "message": "Am dat mai departe."}
    except Exception as e:
        logger.error(f"❌ [Spotify API] next: {e}")
        return {"status": "error", "message": str(e)}


def previous_track() -> dict:
    sp = _get_client()
    if not sp:
        return {"status": "error", "message": "Spotify API indisponibil."}
    try:
        sp.previous_track(device_id=_find_target_device_id(sp))
        logger.info("⏮️ [Spotify API] Piesa anterioară.")
        return {"status": "ok", "message": "Am dat înapoi."}
    except Exception as e:
        logger.error(f"❌ [Spotify API] previous: {e}")
        return {"status": "error", "message": str(e)}


def get_volume() -> Optional[int]:
    sp = _get_client()
    if not sp:
        return None
    try:
        cur = sp.current_playback()
        return (cur.get("device") or {}).get("volume_percent") if cur else None
    except Exception:
        return None


def set_volume(percent: int) -> dict:
    """Volum absolut, 0-100."""
    sp = _get_client()
    if not sp:
        return {"status": "error", "message": "Spotify API indisponibil."}
    percent = max(0, min(100, int(percent)))
    try:
        sp.volume(percent, device_id=_find_target_device_id(sp))
        logger.info(f"🔊 [Spotify API] Volum → {percent}%")
        return {"status": "ok", "volum": percent}
    except Exception as e:
        logger.error(f"❌ [Spotify API] set_volume: {e}")
        return {"status": "error", "message": str(e)}


def change_volume(delta: int) -> dict:
    """Volum relativ (+/- procente) față de cel curent."""
    current = get_volume()
    if current is None:
        return {"status": "error", "message": "Nu pot citi volumul curent."}
    return set_volume(current + delta)
