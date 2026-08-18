"""
tools/music_memory.py — Profilul de Gust Muzical (cu limite dure)
===================================================================
DJ-ul învață singur ce-i place lui Sergiu, din SEMNALE IMPLICITE — fără să
fie nevoie să declare preferințe la început (declarațiile explicite creează
fixații; observația tăcută nu).

Semnale, de la cel mai slab la cel mai puternic:
    finish        (+1)  a lăsat piesa să meargă
    skip târziu   (-1)  a sărit după > jumătate
    skip devreme  (-3)  a sărit repede — cel mai onest semnal de „nu-mi place"
    like explicit (+4)  „asta e tare"
    dislike expl. (-5)  „scoate asta"

CONTROLUL DIMENSIUNII (cerința: să nu ajungă la 100k tokeni în 100 de zile):
    1. Scorurile DECAD în timp (half-life ~45 zile) — gusturile vechi se sting
       singure, deci profilul reflectă ce asculți ACUM, nu acum un an.
    2. Fiecare secțiune are un plafon FIX de intrări (vezi MAX_*). La depășire
       se taie cele mai slabe/vechi.
    3. Blocul injectat în promptul DJ-ului e plafonat separat (PROMPT_MAX_*),
       deci chiar dacă fișierul crește, ce se trimite la model rămâne mic.
    Rezultat: fișierul se stabilizează la câțiva KB, promptul la ~200 tokeni,
    indiferent de câte luni trec.

Fișier: chronos_data/music_taste.json
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASTE_FILE = os.path.join(BASE_DIR, "chronos_data", "music_taste.json")

# ── Plafoane pe DISC ──
MAX_ARTISTS = 30      # artiști urmăriți
MAX_GENRES = 15       # genuri/vibe-uri
MAX_RECENT = 25       # istoric anti-repetiție
MAX_DISLIKED = 20     # respinse explicit
MAX_NOTES = 5         # observații libere

# ── Plafoane în PROMPT (ce ajunge efectiv la model) ──
PROMPT_MAX_LIKED = 8
PROMPT_MAX_DISLIKED = 6
PROMPT_MAX_GENRES = 6
PROMPT_MAX_RECENT = 12

HALF_LIFE_DAYS = 45.0
EARLY_SKIP_RATIO = 0.5    # sub 50% din piesă = skip „devreme"

_SCORES = {"finish": 1.0, "skip_late": -1.0, "skip_early": -3.0,
           "like": 4.0, "dislike": -5.0}


def _now() -> str:
    return datetime.now().isoformat()


def _age_days(ts: str) -> float:
    try:
        return max(0.0, (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


class MusicMemory:
    """Profil de gust persistent, cu decădere și plafoane. Thread-safe."""

    def __init__(self, path: str = TASTE_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._data = {"artists": {}, "genres": {}, "recent": [],
                      "disliked": [], "notes": []}
        self._load()

    # ── Persistență ──

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for k in self._data:
                if k in loaded:
                    self._data[k] = loaded[k]
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"⚠️ [Music Memory] Fișier ilizibil ({e}) — pornesc gol.")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ [Music Memory] Nu pot salva: {e}")

    # ── Decădere + curățare (rulează la fiecare scriere) ──

    def _decayed(self, entry: dict) -> float:
        """Scorul ajustat cu vechimea — preferințele vechi se estompează."""
        score = float(entry.get("score", 0.0))
        return score * (0.5 ** (_age_days(entry.get("last", _now())) / HALF_LIFE_DAYS))

    def _prune(self) -> None:
        # Artiști/genuri: păstrăm cei mai relevanți (scor absolut mare, recent)
        for key, cap in (("artists", MAX_ARTISTS), ("genres", MAX_GENRES)):
            d = self._data.get(key, {})
            if len(d) > cap:
                ordonat = sorted(d.items(), key=lambda kv: abs(self._decayed(kv[1])), reverse=True)
                self._data[key] = dict(ordonat[:cap])
            # Scăpăm de cele care au decăzut la aproape zero
            self._data[key] = {
                k: v for k, v in self._data[key].items()
                if abs(self._decayed(v)) >= 0.25 or v.get("plays", 0) >= 3
            }

        self._data["recent"] = self._data.get("recent", [])[-MAX_RECENT:]
        self._data["disliked"] = self._data.get("disliked", [])[-MAX_DISLIKED:]
        self._data["notes"] = self._data.get("notes", [])[-MAX_NOTES:]

    # ── Înregistrare semnale ──

    def _bump(self, bucket: str, name: str, delta: float, **extra) -> None:
        if not name:
            return
        d = self._data.setdefault(bucket, {})
        key = _norm(name)
        entry = d.get(key) or {"nume": name, "score": 0.0, "plays": 0, "skips": 0,
                               "last": _now()}
        # Aplicăm decăderea acumulată ÎNAINTE de a adăuga semnalul nou
        entry["score"] = round(self._decayed(entry) + delta, 2)
        entry["last"] = _now()
        for k, v in extra.items():
            entry[k] = entry.get(k, 0) + v
        d[key] = entry

    def _genre_of(self, track: str) -> str:
        """Genul cu care a fost pusă piesa — ca skip-ul să penalizeze și zona,
        nu doar artistul."""
        for r in reversed(self._data.get("recent", [])):
            if _norm(r.get("track", "")) == _norm(track):
                return r.get("genre", "")
        return ""

    def record_play(self, track: str, artist: str = "", genre: str = "") -> None:
        """O piesă tocmai a pornit. Genul se notează NEUTRU — abia reacția lui
        (o termină sau sare) decide dacă zona aia îi place sau nu."""
        with self._lock:
            if track:
                self._data.setdefault("recent", []).append(
                    {"track": track, "artist": artist, "genre": genre, "ts": _now()})
            if artist:
                self._bump("artists", artist, 0.0, plays=1)
            if genre:
                self._bump("genres", genre, 0.0, plays=1)
            self._prune()
            self._save()
        logger.debug(f"🎵 [Music Memory] Redare: {track} — {artist} ({genre})")

    def record_skip(self, track: str, artist: str = "", progress_ratio: float = 0.0) -> None:
        """Sergiu a sărit peste. Cu cât mai devreme, cu atât mai clar că nu-i place."""
        devreme = progress_ratio < EARLY_SKIP_RATIO
        delta = _SCORES["skip_early"] if devreme else _SCORES["skip_late"]
        with self._lock:
            genre = self._genre_of(track)
            if artist:
                self._bump("artists", artist, delta, skips=1)
            if genre:
                # Jumătate din penalizare merge pe zonă: un skip izolat nu
                # condamnă genul, dar skip după skip îl scoate din preferințe.
                self._bump("genres", genre, delta / 2.0, skips=1)
            if devreme and track:
                self._data.setdefault("disliked", []).append(
                    {"track": track, "artist": artist, "motiv": "sărită repede", "ts": _now()})
            self._prune()
            self._save()
        logger.info(f"⏭️ [Music Memory] Skip {'devreme' if devreme else 'târziu'}: "
                    f"{track} — {artist} ({delta:+g})")

    def record_finish(self, track: str, artist: str = "") -> None:
        with self._lock:
            genre = self._genre_of(track)
            if artist:
                self._bump("artists", artist, _SCORES["finish"])
            if genre:
                self._bump("genres", genre, _SCORES["finish"] / 2.0)
            self._prune()
            self._save()

    def record_feedback(self, positive: bool, track: str = "", artist: str = "",
                        note: str = "") -> None:
        """Reacție explicită: „asta e tare" / „scoate asta"."""
        delta = _SCORES["like"] if positive else _SCORES["dislike"]
        with self._lock:
            if artist:
                self._bump("artists", artist, delta)
            if not positive and track:
                self._data.setdefault("disliked", []).append(
                    {"track": track, "artist": artist, "motiv": note or "respinsă", "ts": _now()})
            if note:
                self._data.setdefault("notes", []).append({"text": note[:120], "ts": _now()})
            self._prune()
            self._save()
        logger.info(f"{'👍' if positive else '👎'} [Music Memory] Feedback: {track} — {artist}")

    # ── Ce vede DJ-ul ──

    def _recent_unique(self, n: int) -> list:
        """Ultimele piese distincte (fără duplicate) — pentru anti-repetiție."""
        vazute, out = set(), []
        for r in reversed(self._data.get("recent", [])):
            t = r.get("track")
            if t and _norm(t) not in vazute:
                vazute.add(_norm(t))
                out.append(t)
            if len(out) >= n:
                break
        return list(reversed(out))

    def recent_tracks(self, n: int = PROMPT_MAX_RECENT) -> list:
        with self._lock:
            return self._recent_unique(n)

    def prompt_block(self) -> str:
        """
        Profilul comprimat pentru promptul DJ-ului. Plafonat ca lungime —
        crește fișierul, nu și ce trimitem la model.
        """
        with self._lock:
            artists = [(v.get("nume"), self._decayed(v)) for v in self._data.get("artists", {}).values()]
            genres = [(v.get("nume"), self._decayed(v)) for v in self._data.get("genres", {}).values()]
            disliked = self._data.get("disliked", [])[-PROMPT_MAX_DISLIKED:]
            notes = self._data.get("notes", [])[-MAX_NOTES:]
            recent = self._recent_unique(PROMPT_MAX_RECENT)

        placute = sorted([a for a in artists if a[1] > 0.5], key=lambda x: -x[1])[:PROMPT_MAX_LIKED]
        respinse = sorted([a for a in artists if a[1] < -0.5], key=lambda x: x[1])[:PROMPT_MAX_DISLIKED]
        gen_top = sorted([g for g in genres if g[1] > 0.5], key=lambda x: -x[1])[:PROMPT_MAX_GENRES]

        if not any((placute, respinse, gen_top, disliked, recent)):
            return ("PROFIL: încă nu știi ce-i place. Explorează larg și fii atent "
                    "la ce sare peste.")

        out = ["PROFIL DE GUST (dedus din ce ascultă, nu declarat de el):"]
        if placute:
            out.append("  Merge bine: " + ", ".join(n for n, _ in placute))
        if gen_top:
            out.append("  Zone preferate: " + ", ".join(n for n, _ in gen_top))
        if respinse:
            out.append("  EVITĂ (a sărit peste repetat): " + ", ".join(n for n, _ in respinse))
        if disliked:
            out.append("  Piese respinse: " + ", ".join(
                f"{d.get('track')}" for d in disliked if d.get("track")))
        if notes:
            out.append("  Observații: " + "; ".join(n.get("text", "") for n in notes))
        if recent:
            out.append("  Puse recent (NU le repeta): " + ", ".join(recent))
        return "\n".join(out)

    def stats(self) -> dict:
        with self._lock:
            return {
                "artisti": len(self._data.get("artists", {})),
                "genuri": len(self._data.get("genres", {})),
                "recente": len(self._data.get("recent", [])),
                "respinse": len(self._data.get("disliked", [])),
                "note": len(self._data.get("notes", [])),
                "marime_fisier_kb": round(os.path.getsize(self._path) / 1024, 1)
                if os.path.exists(self._path) else 0,
            }


_memory: Optional[MusicMemory] = None


def get_memory() -> MusicMemory:
    global _memory
    if _memory is None:
        _memory = MusicMemory()
    return _memory
