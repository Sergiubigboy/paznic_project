"""
core/user_profile.py — Profil pe Termen Lung
==============================================
Recap-ul de conversații injectat la fiecare sesiune arată doar ULTIMELE câteva
schimburi. Modulul ăsta adaugă stratul de deasupra: o sinteză stabilă despre
CINE e Sergiu — ce-l preocupă constant, ce pattern-uri revin, ce contează
pentru el — construită din întreaga memorie vectorială.

Cost controlat: se regenerează cel mult O DATĂ pe zi (PROFILE_REFRESH_HOURS)
și se citește din cache în rest. Deci ~1 apel Gemini pe zi, nu unul per sesiune.

Cache: chronos_data/user_profile.json
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_FILE = os.path.join(BASE_DIR, "chronos_data", "user_profile.json")

try:
    from config import PROFILE_REFRESH_HOURS, PROFILE_ENABLED
except ImportError:
    PROFILE_REFRESH_HOURS = 24
    PROFILE_ENABLED = True

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "preocupari": {
            "type": "ARRAY", "items": {"type": "STRING"},
            "description": "3-6 lucruri care îl preocupă constant pe Sergiu.",
        },
        "pattern_uri": {
            "type": "ARRAY", "items": {"type": "STRING"},
            "description": "2-4 tipare de comportament/dispoziție observate.",
        },
        "rezumat": {
            "type": "STRING",
            "description": "2-3 propoziții despre cine e și ce-l definește acum.",
        },
    },
    "required": ["preocupari", "rezumat"],
}


def _load_cache() -> dict:
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"⚠️ [Profile] Cache ilizibil: {e}")
        return {}


def _save_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(PROFILE_FILE), exist_ok=True)
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ [Profile] Nu pot salva profilul: {e}")


def _is_stale(cache: dict) -> bool:
    ts = cache.get("generat_la")
    if not ts:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(ts) > timedelta(hours=PROFILE_REFRESH_HOURS)
    except Exception:
        return True


def _build(logger_agent) -> dict:
    """Regenerează profilul din memoria vectorială. Un singur apel Gemini."""
    try:
        # DOAR conversații, nu analizele vechi de jurnal. Alea sunt scrise în
        # stil „psiholog dur" și, dacă intră aici, profilul devine o listă de
        # reproșuri pe care Chronos apoi le repetă la nesfârșit.
        docs = logger_agent.memory_manager.get_recent(
            n=40, where_filter={"type": "conversation"}
        )
    except Exception as e:
        logger.debug(f"[Profile] Nu pot citi memoria: {e}")
        return {}

    if not docs or len(docs) < 3:
        return {}   # prea puțin material ca să merite un apel

    corpus = "\n---\n".join(docs)[:6000]

    try:
        from ai_core import ask_gemini_json
    except ImportError:
        return {}

    prompt = f"""Analizează fragmentele de mai jos din memoria unui asistent AI despre
utilizatorul lui (Sergiu) și extrage un profil STABIL — nu evenimente izolate,
ci ce revine constant.

FRAGMENTE DIN MEMORIE:
{corpus}

Scopul e CONTEXT UTIL pentru conversații viitoare — ce trebuie să știe un
asistent ca să nu pună întrebări redundante și să înțeleagă despre ce e vorba.

Extrage:
- preocupari: 3-6 teme concrete care revin (proiecte, obiective, interese, domenii).
- pattern_uri: 2-4 preferințe practice de lucru (ex: „lucrează noaptea târziu",
  „preferă explicații scurte", „ține evidența cheltuielilor").
- rezumat: 2-3 propoziții factuale despre ce face și ce-l interesează acum.

REGULI STRICTE:
- NU include critici, judecăți sau diagnostice despre productivitate, disciplină,
  timp pierdut, telefon, distragere, restanțe sau lene. Nu ești psiholog și nu
  faci evaluări — un asistent care repetă astfel de reproșuri devine insuportabil.
- NU cita cifre sau metrici din trecut (ore, procente) — sunt aproape sigur
  învechite și ar fi repetate greșit ca fapt actual.
- Ține-te de fapte neutre și utile. Dacă materialul e prea sărac pentru o
  categorie, las-o goală în loc să inventezi.
Scrie în română, concret, fără platitudini."""

    result = ask_gemini_json(prompt, schema=_SCHEMA, temperature=0.4)
    if not isinstance(result, dict):
        return {}

    result["generat_la"] = datetime.now().isoformat()
    result["surse"] = len(docs)
    _save_cache(result)
    logger.info(f"🧬 [Profile] Profil regenerat din {len(docs)} amintiri.")
    return result


def get_profile_block(logger_agent=None, allow_refresh: bool = True) -> str:
    """
    Blocul de profil pentru system prompt. Citește din cache; regenerează doar
    dacă a expirat ȘI avem acces la memorie.
    """
    if not PROFILE_ENABLED:
        return ""

    cache = _load_cache()
    if allow_refresh and logger_agent is not None and _is_stale(cache):
        fresh = _build(logger_agent)
        if fresh:
            cache = fresh

    if not cache or not cache.get("rezumat"):
        return ""

    lines = ["[PROFIL SERGIU — context stabil, nu-l recita, doar ține cont de el]"]
    if cache.get("rezumat"):
        lines.append(cache["rezumat"])
    if cache.get("preocupari"):
        lines.append("Îl preocupă constant: " + "; ".join(cache["preocupari"]))
    if cache.get("pattern_uri"):
        lines.append("Tipare observate: " + "; ".join(cache["pattern_uri"]))
    return "\n".join(lines)


def refresh_in_background(logger_agent) -> None:
    """
    Regenerează profilul într-un thread separat, DOAR dacă a expirat.

    Regenerarea e un apel LLM de câteva secunde. Pe calea de pornire a unei
    sesiuni vocale nu ne permitem s-o așteptăm (întârzie momentul în care
    Chronos începe să asculte), așa că sesiunea curentă folosește cache-ul,
    iar rezultatul proaspăt se aplică de la sesiunea următoare.
    """
    if not PROFILE_ENABLED or logger_agent is None:
        return
    if not _is_stale(_load_cache()):
        return

    def _job():
        try:
            _build(logger_agent)
        except Exception as e:
            logger.debug(f"[Profile] Regenerare în fundal eșuată: {e}")

    threading.Thread(target=_job, daemon=True).start()
