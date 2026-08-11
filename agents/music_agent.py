"""
agents/music_agent.py — Agent Specializat Muzică & DJ AI
=========================================================
Agent AI dedicat pentru curatarea muzicală (DJ) și trimiterea comenzilor pe difuzoare.
"""

import logging
import random
from datetime import datetime
from config import GEMINI_MODEL_DJ
from ai_core import ask_gemini_json
from tools.spotify_tools import send_google_command, pause_music, resume_music
from tools.spotify_api import pause_playback_api, resume_playback_api

logger = logging.getLogger(__name__)


class MusicAgent:
    """Agent AI specializat în selecție muzicală și control Spotify/Google Assistant."""

    def __init__(self):
        self.play_history = []

    def _get_time_context(self) -> str:
        hour = datetime.now().hour
        if 5 <= hour < 12: return "MORNING (Wake Up / Energize)"
        elif 12 <= hour < 18: return "AFTERNOON (Focus / Vibe)"
        elif 18 <= hour < 22: return "EVENING (Chill / Pre-Party)"
        else: return "LATE NIGHT (Deep / Psychedelic / Introspective)"

    def process_request(self, user_command: str) -> dict:
        """Alege piesa potrivită și o trimite la difuzor."""
        logger.info(f"🎧 [Music Agent] Procesez: '{user_command}'")

        time_context = self._get_time_context()
        current_time = datetime.now().strftime("%H:%M")
        history_str = ", ".join(self.play_history) if self.play_history else "Nicio piesă recentă."

        system_prompt = f"""
        ROLE: Elite Music Curator, DJ, and Assistant Audio Specialist.
        CURRENT TIME: {current_time} ({time_context})
        BANNED TRACKS: {history_str}
        USER REQUEST: "{user_command}"

        RULES:
        1. If specific track/control (volume, next, pause), translate to English command.
        2. If genre/vibe (ex: "muzică latină", "rock", "atmosferă de munte"), pick ONE SPECIFIC EXCELLENT TRACK.
        """

        schema = {
            "type": "OBJECT",
            "properties": {
                "google_command": {"type": "STRING", "description": "English command for Google Assistant (e.g. 'play Massive by Drake')"},
                "track_name_saved": {"type": "STRING"},
                "reason": {"type": "STRING", "description": "Explicație scurtă în română."}
            },
            "required": ["google_command", "reason"]
        }

        decision = ask_gemini_json(system_prompt, schema=schema, temperature=0.7, model=GEMINI_MODEL_DJ)

        if not decision or not isinstance(decision, dict):
            # Fallback
            success, err = send_google_command("play chill music")
            return {"status": "ok" if success else "error", "msg": f"Muzică transmisă ({err})."}

        cmd = decision.get("google_command", "play music")
        track_saved = decision.get("track_name_saved")
        reason = decision.get("reason", "Comandă muzică executată.")

        logger.info(f"🎧 [Music Agent] DJ Choice: {reason} → '{cmd}'")
        success, err_msg = send_google_command(cmd)

        if success and track_saved and track_saved.lower() != "none":
            self.play_history.append(track_saved)
            if len(self.play_history) > 10: self.play_history.pop(0)

        if success:
            return {"status": "success", "msg": f"Am transmis pe Spotify: {cmd}", "reason": reason}
        else:
            return {"status": "error", "msg": f"Eroare la difuzor: {err_msg}", "reason": reason}

    def pause_playback(self) -> bool:
        """Pauză REALĂ prin Spotify Web API. Fallback pe trucul cu Google
        Assistant broadcast doar dacă API-ul e indisponibil (ex: OAuth
        neautorizat încă) — ala doar "anunță" comanda vocal, nu opreste
        efectiv redarea."""
        if pause_playback_api():
            return True
        logger.debug("[Music Agent] Spotify API indisponibil pentru pauză, fallback Google Assistant.")
        return pause_music()

    def resume_playback(self) -> bool:
        """Resume REAL prin Spotify Web API, cu același fallback ca mai sus."""
        if resume_playback_api():
            return True
        logger.debug("[Music Agent] Spotify API indisponibil pentru resume, fallback Google Assistant.")
        return resume_music()
