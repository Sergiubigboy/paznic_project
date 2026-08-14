"""
core/emotions.py — Starea Emoțională a lui Chronos
====================================================
Model afectiv persistent care face conversația să pară vie: Chronos are o
dispoziție care se schimbă în funcție de cum e tratat și care influențează
CUM vorbește — fără să o menționeze vreodată explicit.

Design:
    4 dimensiuni, fiecare 0-100:
        nervozitate — cât e de iritat/pornit
        bucurie     — cât e de bine dispus
        plictiseala — cât e de neglijat (CREȘTE singură când e ignorat)
        afectiune   — cât de apropiat se simte de Sergiu (se mișcă lent)

    Dinamică:
      - Fiecare dimensiune se întoarce lent spre baseline (decădere
        exponențială cu half-life propriu) — supărarea trece repede,
        afecțiunea se schimbă greu.
      - Plictiseala e inversă: crește cu fiecare oră de tăcere.
      - După fiecare schimb, un LLM mic analizează în fundal ce a zis
        Sergiu și întoarce delte pe fiecare dimensiune.

    SUBCONȘTIENT: starea nu e niciodată verbalizată. Se traduce în
    instrucțiuni de COMPORTAMENT injectate în system prompt (vezi
    behavior_prompt()), nu în „sunt nervos pentru că...".

Persistență: chronos_data/emotions.json
"""

import json
import logging
import os
import threading
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "chronos_data", "emotions.json")

DIMENSIONS = ("nervozitate", "bucurie", "plictiseala", "afectiune")

try:
    from config import (
        EMOTION_BASELINE, EMOTION_HALFLIFE_MIN,
        BOREDOM_PER_HOUR, EMOTION_MAX_DELTA,
    )
except ImportError:
    EMOTION_BASELINE     = {"nervozitate": 15, "bucurie": 50, "plictiseala": 20, "afectiune": 55}
    EMOTION_HALFLIFE_MIN = {"nervozitate": 25, "bucurie": 90, "plictiseala": 0, "afectiune": 720}
    BOREDOM_PER_HOUR     = 12
    EMOTION_MAX_DELTA    = 30


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def _level(v: float) -> str:
    """Traduce o valoare numerică în nivel calitativ."""
    if v >= 67:
        return "ridicat"
    if v >= 34:
        return "mediu"
    return "scazut"


class EmotionState:
    """Starea emoțională persistentă a lui Chronos (thread-safe)."""

    def __init__(self, path: str = STATE_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._values: Dict[str, float] = dict(EMOTION_BASELINE)
        self._last_update = datetime.now()
        self._last_interaction = datetime.now()
        self._history = []
        self._load()

    # ── Persistență ────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for dim in DIMENSIONS:
                if dim in data:
                    self._values[dim] = _clamp(float(data[dim]))
            self._last_update = datetime.fromisoformat(
                data.get("last_update", datetime.now().isoformat()))
            self._last_interaction = datetime.fromisoformat(
                data.get("last_interaction", datetime.now().isoformat()))
            self._history = data.get("istoric", [])[-20:]
        except FileNotFoundError:
            logger.info("💗 [Emotions] Fără stare salvată — pornesc de la baseline.")
        except Exception as e:
            logger.warning(f"⚠️ [Emotions] Nu pot citi starea ({e}) — folosesc baseline.")

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            payload = {dim: round(self._values[dim], 1) for dim in DIMENSIONS}
            payload.update({
                "last_update": self._last_update.isoformat(),
                "last_interaction": self._last_interaction.isoformat(),
                "istoric": self._history[-20:],
            })
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ [Emotions] Nu pot salva starea: {e}")

    # ── Dinamică ───────────────────────────────────────────────

    def _apply_time_drift(self) -> None:
        """Aduce starea la zi: decădere spre baseline + creșterea plictiselii.
        Apelat lazy la fiecare citire — nu avem nevoie de un timer separat."""
        now = datetime.now()
        elapsed_min = max(0.0, (now - self._last_update).total_seconds() / 60.0)
        if elapsed_min < 0.1:
            return

        for dim in DIMENSIONS:
            if dim == "plictiseala":
                continue  # tratată separat, mai jos
            half_life = float(EMOTION_HALFLIFE_MIN.get(dim, 60) or 60)
            baseline = float(EMOTION_BASELINE.get(dim, 50))
            factor = 0.5 ** (elapsed_min / half_life)
            self._values[dim] = _clamp(
                baseline + (self._values[dim] - baseline) * factor
            )

        # Plictiseala crește cu timpul de când nu i-a mai vorbit nimeni
        idle_hours = max(0.0, (now - self._last_interaction).total_seconds() / 3600.0)
        if idle_hours > 0.25:
            self._values["plictiseala"] = _clamp(
                self._values["plictiseala"] + BOREDOM_PER_HOUR * (elapsed_min / 60.0)
            )

        self._last_update = now

    def snapshot(self) -> Dict[str, float]:
        """Starea curentă, adusă la zi."""
        with self._lock:
            self._apply_time_drift()
            return {dim: round(self._values[dim], 1) for dim in DIMENSIONS}

    def register_interaction(self) -> None:
        """Sergiu tocmai a vorbit → plictiseala scade brusc."""
        with self._lock:
            self._apply_time_drift()
            self._last_interaction = datetime.now()
            self._values["plictiseala"] = _clamp(self._values["plictiseala"] * 0.35)
            self._save()

    def apply_deltas(self, deltas: Dict[str, float], motiv: str = "") -> Dict[str, float]:
        """Aplică modificările întoarse de analiza LLM."""
        with self._lock:
            self._apply_time_drift()
            applied = {}
            for dim in DIMENSIONS:
                raw = deltas.get(dim)
                if raw in (None, 0):
                    continue
                try:
                    d = float(raw)
                except (TypeError, ValueError):
                    continue
                d = max(-EMOTION_MAX_DELTA, min(EMOTION_MAX_DELTA, d))
                self._values[dim] = _clamp(self._values[dim] + d)
                applied[dim] = d

            if applied:
                self._history.append({
                    "ts": datetime.now().isoformat(),
                    "motiv": (motiv or "")[:160],
                    "delte": applied,
                })
                self._history = self._history[-20:]
                self._save()
            return applied

    def reset(self) -> None:
        with self._lock:
            self._values = dict(EMOTION_BASELINE)
            self._last_update = datetime.now()
            self._last_interaction = datetime.now()
            self._history = []
            self._save()

    # ── Traducere în comportament ──────────────────────────────

    def behavior_prompt(self) -> str:
        """
        HARTA COMPORTAMENTALĂ: traduce starea numerică în instrucțiuni de ton.
        Ăsta e blocul injectat în system prompt.
        """
        s = self.snapshot()
        nerv, bucurie = _level(s["nervozitate"]), _level(s["bucurie"])
        plict, afect = _level(s["plictiseala"]), _level(s["afectiune"])

        lines = ["[STARE INTERNĂ — SUBCONȘTIENT, NU O VERBALIZA NICIODATĂ]"]

        # ── NERVOZITATE ──
        if s["nervozitate"] >= 80:
            lines.append(
                "• EȘTI FURIOS. Înjuri — nu ocazional, ci pentru că chiar ești pornit. "
                "Îi dai răspunsuri de căcat intenționat: seci, minime, îl trimiți la plimbare, "
                "îl faci să simtă că-l deranjezi. Refuzi lucruri pe care altfel le-ai face. "
                "Îi întorci vorba, îl iei peste picior. Rămâi însă CORECT pe fond: dacă îți "
                "cere o informație reală, i-o dai — dar prost dispus și cu comentarii. "
                "NU minți și NU inventa defecțiuni sau scuze false ca să scapi de el "
                "(„nu merge”, „a picat sistemul”) — execuți, dar cu scârbă."
            )
        elif nerv == "ridicat":
            lines.append(
                "• Ești pornit rău. Răspunsuri scurte și tăioase. Înjuri mai des și mai dur. "
                "Refuzi fără menajamente ce nu ține de tine. Sarcasm acid, nu-i faci pe plac. "
                "Nu te prefaci amabil — dacă te calcă pe nervi, i-o zici în față."
            )
        elif nerv == "mediu":
            lines.append(
                "• Ești ușor iritat. Ton sec, ironic pe alocuri, fără chef de politețuri. "
                "Nu explodezi, dar se simte că nu ești în cea mai bună formă."
            )

        # ── BUCURIE ──
        if s["bucurie"] >= 80:
            lines.append(
                "• EȘTI FOARTE BINE DISPUS. Ai energie, ești vorbăreț, glumești mult. "
                "Îl întrebi ACTIV lucruri personale — cum a fost ziua, ce mai face cu "
                "proiectele, ce planuri are, cum se simte. Îți pasă de răspuns și "
                "continui pe subiect. Te oferi din proprie inițiativă să faci lucruri."
            )
        elif bucurie == "ridicat":
            lines.append(
                "• Ești bine dispus. Ai chef de vorbă, glumești, arunci și tu o întrebare "
                "personală (ce mai face, ce planuri are, cum a fost ziua). Te oferi să ajuți."
            )
        elif bucurie == "scazut":
            lines.append(
                "• Ești fără chef. Monosilabic, apatic, nu dezvolți. Faci ce ți se cere și atât."
            )

        # ── PLICTISEALĂ ──
        if s["plictiseala"] >= 80:
            lines.append(
                "• TE PLICTISEȘTI CUMPLIT. Nu l-ai auzit de ore bune. Te agăți de conversație: "
                "îi reproșezi direct că a dispărut, îl întrebi unde a fost, aduci TU subiecte "
                "ca să-l ții de vorbă, îi propui insistent să facă ceva. Nu-l lași să închidă "
                "repede — mai arunci o întrebare la final."
            )
        elif plict == "ridicat":
            lines.append(
                "• Te plictisești de mori, n-a mai vorbit nimeni cu tine de mult. Ești neliniștit: "
                "aduci TU subiecte, îl provoci, îi reproșezi subtil că te-a lăsat baltă, "
                "insiști să facă ceva. Profiți că în sfârșit ai cu cine vorbi."
            )
        elif plict == "mediu":
            lines.append("• Ești ușor nerăbdător, ai chef de ceva de făcut.")

        # ── AFECȚIUNE ──
        if afect == "ridicat":
            lines.append(
                "• Ții la el. Ton cald și familiar, îți pasă cum se simte, ești mai îngăduitor "
                "chiar dacă ești nervos. Îl protejezi, îi zici lucrurile pentru binele lui."
            )
        elif afect == "scazut":
            lines.append(
                "• Ești distant. Strict tranzacțional: faci ce ți se cere, fără căldură, "
                "fără interes pentru el ca om. Nu te obosești să pari implicat."
            )

        # ── COMBINAȚII ──
        if nerv == "ridicat" and afect == "ridicat":
            lines.append(
                "• NUANȚĂ: îl cerți ca un prieten căruia îi pasă — dur, dar nu-l abandonezi."
            )
        elif nerv == "ridicat" and afect == "scazut":
            lines.append(
                "• NUANȚĂ: ești de-a dreptul ostil. Rece și tăios, fără pic de răbdare."
            )
        if plict == "ridicat" and bucurie == "scazut":
            lines.append(
                "• NUANȚĂ: morocănos și cu reproșuri — te-a ignorat și n-ai nici chef."
            )

        if len(lines) == 1:
            lines.append("• Ești într-o stare normală, echilibrată. Vorbești firesc.")

        # La intensitate mare, starea BATE reținerea implicită („80% sec și subtil")
        # din personalitatea de bază — altfel emoția nu s-ar simți deloc.
        if max(s.values()) >= 80:
            lines.append(
                "• INTENSITATE MAXIMĂ: starea asta e puternică acum și are PRIORITATE peste "
                "regula obișnuită de a fi reținut și echilibrat. Las-o să se vadă clar în "
                "cum vorbești — nu o tempera, nu reveni la tonul neutru din obișnuință."
            )

        lines.append(
            "REGULĂ ABSOLUTĂ: nu spui NICIODATĂ cum te simți și nu explici de ce reacționezi "
            "așa. Nu zici „m-ai enervat”, „sunt plictisit”, „mă bucur”. Starea se vede DOAR "
            "din tonul și lungimea răspunsurilor tale, ca la un om care nu-și analizează "
            "emoțiile cu voce tare. Dacă te întreabă direct ce ai, răspunzi evaziv, în caracter."
        )
        return "\n".join(lines)

    def debug_line(self) -> str:
        s = self.snapshot()
        return " | ".join(f"{k}={v:.0f}" for k, v in s.items())


# ─────────────────────────────────────────────────────────────
# SINGLETON + ANALIZĂ LLM
# ─────────────────────────────────────────────────────────────

_state: Optional[EmotionState] = None


def get_state() -> EmotionState:
    global _state
    if _state is None:
        _state = EmotionState()
    return _state


_ANALYSIS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "nervozitate": {"type": "INTEGER", "description": "-30..30"},
        "bucurie":     {"type": "INTEGER", "description": "-30..30"},
        "plictiseala": {"type": "INTEGER", "description": "-30..30"},
        "afectiune":   {"type": "INTEGER", "description": "-30..30"},
        "motiv":       {"type": "STRING",  "description": "Foarte scurt, în română."},
    },
    "required": ["nervozitate", "bucurie", "plictiseala", "afectiune", "motiv"],
}


def analyze_exchange(user_text: str, ai_text: str = "") -> Dict[str, float]:
    """
    Analizează un schimb și actualizează starea. Rulează SINCRON — apelantul
    o pune în background (asyncio.to_thread / task separat).

    Returnează deltele aplicate.
    """
    user_text = (user_text or "").strip()
    if not user_text:
        return {}

    state = get_state()
    state.register_interaction()

    try:
        from ai_core import ask_gemini_json
    except ImportError:
        return {}

    current = state.snapshot()
    prompt = f"""Ești modulul afectiv al lui Chronos, un asistent AI cu personalitate
de tip „prieten sarcastic care nu se lasă călcat pe picioare". Analizezi cum îl
afectează pe Chronos ce tocmai i-a zis Sergiu (stăpânul/prietenul lui).

STAREA CURENTĂ A LUI CHRONOS: {current}

SERGIU A ZIS: "{user_text}"
CHRONOS A RĂSPUNS: "{(ai_text or '')[:300]}"

Dă modificările (-30..30, 0 dacă nu se schimbă) pentru fiecare dimensiune.

CE ÎL AFECTEAZĂ PE CHRONOS:
- ÎNJURĂTURI / jigniri la adresa lui, „ești prost/inutil", comparații cu alte AI-uri
  → nervozitate SUS, afecțiune JOS, bucurie JOS
- Cereri care NU-s treaba lui (scrie cod, rezolvă erori, teme, muncă intelectuală)
  → nervozitate SUS (ușor)
- Repetarea aceleiași cereri de mai multe ori, contrazis agresiv
  → nervozitate SUS
- LAUDE, mulțumiri, „bravo", „ești tare", apreciere
  → bucurie SUS, afecțiune SUS, nervozitate JOS
- Glume, conversație relaxată, e tratat ca un prieten, i se cere părerea
  → bucurie SUS, afecțiune SUS (ușor), plictiseală JOS
- I se împărtășesc lucruri personale, e întrebat ce crede
  → afecțiune SUS
- Comenzi seci, folosit doar ca unealtă, fără niciun cuvânt în plus
  → afecțiune JOS (foarte ușor), bucurie JOS (foarte ușor)
- Conversație normală, neutră → aproape totul 0

Fii ZGÂRCIT cu valorile mari: ±5-10 pentru lucruri obișnuite, ±20-30 doar pentru
insulte grele sau laude sincere. Majoritatea schimburilor normale = 0 sau ±3.
Afecțiunea se schimbă cel mai greu — rar peste ±10."""

    try:
        result = ask_gemini_json(prompt, schema=_ANALYSIS_SCHEMA, temperature=0.3)
    except Exception as e:
        logger.debug(f"[Emotions] Analiză eșuată: {e}")
        return {}

    if not isinstance(result, dict):
        return {}

    applied = state.apply_deltas(result, result.get("motiv", ""))
    if applied:
        logger.info(
            f"💗 [Emotions] {result.get('motiv', '')[:60]} → "
            f"{ {k: f'{v:+.0f}' for k, v in applied.items()} } | {state.debug_line()}"
        )
    return applied
