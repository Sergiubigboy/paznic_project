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
from tools.spotify_api import (
    pause_playback_api, resume_playback_api,
    now_playing, next_track, previous_track, set_volume, change_volume,
)
from tools.music_memory import get_memory

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

        # Profilul învățat din ce ascultă efectiv (skip-uri, piese terminate,
        # reacții) — plafonat ca lungime, vezi tools/music_memory.py
        taste = get_memory()
        profil = taste.prompt_block()

        system_prompt = f"""
        ROLE: Elite Music Curator, DJ, and Assistant Audio Specialist.
        CURRENT TIME: {current_time} ({time_context})
        USER REQUEST: "{user_command}"

        {profil}

        RULES:
        1. If specific track/control (volume, next, pause), translate to English command.
        2. If genre/vibe (ex: "muzică latină", "rock", "atmosferă de munte"), pick ONE SPECIFIC EXCELLENT TRACK.
        3. DIVERSITATE: lui Sergiu îi place să asculte variat. NU repeta piesele
           puse recent și nu te închista într-un singur artist sau subgen —
           chiar și în interiorul unui gen cerut, variază. Excepție: dacă a cerut
           EXPLICIT o piesă sau un artist anume, dă-i exact aia.
        4. Profilul de mai sus e ORIENTATIV, nu o cușcă: evită ce a respins clar,
           dar nu servi mereu aceleași lucruri „sigure". Riscă lucruri noi apropiate
           de ce merge, ca să continue să descoperi ce-i place.
        5. `genre` = eticheta scurtă a zonei muzicale alese (ex: "trap romanesc",
           "jazz", "phonk"), ca să pot învăța din reacția lui.
        """

        schema = {
            "type": "OBJECT",
            "properties": {
                "google_command": {"type": "STRING", "description": "English command for Google Assistant (e.g. 'play Massive by Drake')"},
                "track_name_saved": {"type": "STRING"},
                "artist": {"type": "STRING", "description": "Artistul piesei alese."},
                "genre": {"type": "STRING", "description": "Eticheta scurtă a zonei muzicale."},
                "reason": {"type": "STRING", "description": "Explicație scurtă în română."}
            },
            "required": ["google_command", "reason"]
        }

        decision = ask_gemini_json(system_prompt, schema=schema, temperature=0.9, model=GEMINI_MODEL_DJ)

        if not decision or not isinstance(decision, dict):
            # Fallback
            success, err = send_google_command("play chill music")
            return {"status": "ok" if success else "error", "msg": f"Muzică transmisă ({err})."}

        cmd = decision.get("google_command", "play music")
        track_saved = decision.get("track_name_saved")
        artist = decision.get("artist", "")
        genre = decision.get("genre", "")
        reason = decision.get("reason", "Comandă muzică executată.")

        logger.info(f"🎧 [Music Agent] DJ Choice: {reason} → '{cmd}'")
        success, err_msg = send_google_command(cmd)

        if success and track_saved and track_saved.lower() != "none":
            self.play_history.append(track_saved)
            if len(self.play_history) > 10: self.play_history.pop(0)
            # Memorăm ce am pus, ca să nu repetăm și ca să putem lega
            # skip-ul următor de piesa asta.
            taste.record_play(track_saved, artist, genre)

        if success:
            return {"status": "success", "msg": f"Am transmis pe Spotify: {cmd}", "reason": reason}
        else:
            return {"status": "error", "msg": f"Eroare la difuzor: {err_msg}", "reason": reason}

    # ── CONTROL DIRECT — instant, ZERO apeluri LLM ──
    # process_request() (DJ-ul) face un apel Gemini ca să aleagă piesa.
    # Pentru pauză/next/volum aia ar fi risipă: aici mergem direct pe API.

    def control(self, action: str, value=None) -> dict:
        """Comenzi de playback fără selecție de piesă → fără LLM."""
        action = (action or "").strip().lower()
        logger.info(f"🎛️ [Music Agent] Control direct: {action}"
                    + (f" ({value})" if value is not None else ""))

        if action == "pause":
            ok = self.pause_playback()
            return {"status": "ok" if ok else "error", "msg": "Am pus pauză." if ok else "N-am putut opri."}
        if action in ("resume", "play"):
            ok = self.resume_playback()
            return {"status": "ok" if ok else "error", "msg": "Am dat drumul." if ok else "N-am putut porni."}
        if action == "next":
            # SEMNALUL CEL MAI VALOROS: ce rula și cât de departe era. O piesă
            # sărită în primele secunde spune mult mai clar „nu-mi place" decât
            # orice ar declara Sergiu explicit.
            self._record_skip_of_current()
            return next_track()
        if action in ("previous", "prev"):
            return previous_track()
        if action in ("like", "dislike"):
            cur = now_playing()
            get_memory().record_feedback(
                positive=(action == "like"),
                track=cur.get("track", ""), artist=cur.get("artist", ""),
            )
            piesa = cur.get("track") or "piesa asta"
            return {"status": "ok",
                    "msg": f"Am reținut că {'îți place' if action == 'like' else 'nu-ți place'} {piesa}."}
        if action == "volume_up":
            return change_volume(int(value) if value else 15)
        if action == "volume_down":
            return change_volume(-(int(value) if value else 15))
        if action == "set_volume":
            if value is None:
                return {"status": "error", "msg": "Lipsește valoarea volumului."}
            return set_volume(int(value))
        if action in ("now_playing", "what_is_playing"):
            return now_playing()

        return {"status": "error", "msg": f"Acțiune necunoscută: {action}"}

    def _record_skip_of_current(self) -> None:
        """Citește ce rulează ACUM și înregistrează skip-ul cu progresul lui."""
        try:
            cur = now_playing()
            if cur.get("status") != "ok" or not cur.get("track"):
                return
            # "2:26 / 2:58" → cât la sută a ascultat
            ratio = 1.0
            progres = cur.get("progres", "")
            if "/" in progres:
                def secunde(t):
                    m, s = t.strip().split(":")
                    return int(m) * 60 + int(s)
                trecut, total = (secunde(x) for x in progres.split("/"))
                ratio = (trecut / total) if total else 1.0
            get_memory().record_skip(cur.get("track", ""), cur.get("artist", ""), ratio)
        except Exception as e:
            logger.debug(f"[Music Agent] Nu pot înregistra skip-ul: {e}")

    def what_is_playing(self) -> dict:
        return now_playing()

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


# ─────────────────────────────────────────────────────────────
# HARNESS DE TEST
# ─────────────────────────────────────────────────────────────
# ATENȚIE la modul de rulare: fișierul face parte dintr-un pachet și importă
# `config` din rădăcina proiectului. `python agents/music_agent.py` pune pe
# sys.path folderul `agents/`, NU rădăcina → ModuleNotFoundError: config.
# Rulează-l ca MODUL, din rădăcina proiectului:
#
#     python -m agents.music_agent                  → profil de gust + ce cântă
#     python -m agents.music_agent "pune trap"      → cere DJ-ului o piesă
#     python -m agents.music_agent next             → skip (înregistrează semnalul)
#     python -m agents.music_agent like | dislike   → feedback explicit

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    agent = MusicAgent()
    mem = get_memory()
    arg = " ".join(sys.argv[1:]).strip()

    if arg in ("next", "like", "dislike", "pause", "resume", "now_playing"):
        print(agent.control(arg))
    elif arg:
        print(agent.process_request(arg))
    else:
        cur = agent.what_is_playing()
        print("── ACUM ────────────────────────────────────────────")
        if cur.get("track"):
            print(f"   {cur['track']} — {cur.get('artist', '?')}"
                  f"   [{cur.get('progres', '?')}]  vol {cur.get('volum', '?')}%")
        else:
            print(f"   {cur.get('message', 'nimic')}")
        print()
        print("── PROFIL DE GUST ──────────────────────────────────")
        print(mem.prompt_block())
        print()
        print("── DIMENSIUNE MEMORIE ──────────────────────────────")
        for k, v in mem.stats().items():
            print(f"   {k:<18}{v}")
