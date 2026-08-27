"""
tools/home_assistant.py — Home Assistant (aer condiționat, prezență, vreme, prognoză)
=============================================================================
Instanța de Home Assistant era folosită până acum pentru un singur lucru: să
strige o frază către difuzor (spotify_tools). Modulul ăsta o folosește ca ceea
ce e — sursă locală de stare și control.

De ce vremea de aici și nu de pe net: e un apel HTTP în rețeaua locală, cu
răspuns în milisecunde și ZERO tokeni. O căutare Google pentru „ce vreme e"
costă un round-trip de grounding plus tokenii rezultatului — pentru o
informație pe care senzorul din casă o are deja.

Config: HA_URL și HA_TOKEN din .env (deja setate).
"""

import logging
import re
from datetime import date, datetime
from typing import Optional

import requests

from config import HA_URL, HA_TOKEN

logger = logging.getLogger(__name__)

# HA_URL e un endpoint complet de serviciu (folosit de spotify_tools);
# ne trebuie doar originea, ca să construim celelalte rute.
BASE_URL = HA_URL.split("/api/")[0].rstrip("/") if HA_URL else ""
_TIMEOUT = 6

# Sesiune reutilizată — evită un handshake TCP nou la fiecare cerere.
_session = requests.Session()

AC_SCRIPTS = {"on": "script.ac_on", "off": "script.ac_off"}
WEATHER_ENTITY = "weather.forecast_home"

_STARI_RO = {
    "clear-night": "senin", "cloudy": "înnorat", "fog": "ceață",
    "hail": "grindină", "lightning": "descărcări electrice",
    "lightning-rainy": "furtună cu ploaie", "partlycloudy": "parțial înnorat",
    "pouring": "ploaie torențială", "rainy": "ploios", "snowy": "ninsoare",
    "snowy-rainy": "lapoviță", "sunny": "însorit", "windy": "vânt",
    "windy-variant": "vânt", "exceptional": "condiții extreme",
}


def _headers() -> dict:
    return {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def is_configured() -> bool:
    return bool(BASE_URL and HA_TOKEN)


def _get_states() -> Optional[list]:
    if not is_configured():
        return None
    try:
        r = _session.get(f"{BASE_URL}/api/states", headers=_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"❌ [HA] Nu pot citi starea: {e}")
        return None


def _get_state(entity_id: str) -> Optional[dict]:
    if not is_configured():
        return None
    try:
        r = _session.get(f"{BASE_URL}/api/states/{entity_id}",
                         headers=_headers(), timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"❌ [HA] Nu pot citi {entity_id}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# AER CONDIȚIONAT
# ─────────────────────────────────────────────────────────────

def ac_control(pornit: bool) -> dict:
    """Pornește/oprește aerul condiționat prin scripturile din HA."""
    if not is_configured():
        return {"status": "error", "message": "Home Assistant nu e configurat."}

    entity = AC_SCRIPTS["on" if pornit else "off"]
    try:
        r = _session.post(
            f"{BASE_URL}/api/services/script/turn_on",
            headers=_headers(), json={"entity_id": entity}, timeout=_TIMEOUT,
        )
        r.raise_for_status()
        logger.info(f"❄️ [HA] Aer condiționat {'PORNIT' if pornit else 'OPRIT'}.")
        return {"status": "ok",
                "message": f"Am {'pornit' if pornit else 'oprit'} aerul condiționat."}
    except Exception as e:
        logger.error(f"❌ [HA] Comanda AC a eșuat: {e}")
        return {"status": "error", "message": f"N-am putut comanda aerul: {e}"}


# ─────────────────────────────────────────────────────────────
# PREZENȚĂ
# ─────────────────────────────────────────────────────────────

def who_is_home() -> dict:
    """Cine e acasă acum, după entitățile `person` din HA."""
    states = _get_states()
    if states is None:
        return {"status": "error", "message": "Nu pot ajunge la Home Assistant."}

    acasa, plecati, necunoscuti = [], [], []
    for e in states:
        if not e["entity_id"].startswith("person."):
            continue
        nume = (e.get("attributes") or {}).get("friendly_name") or e["entity_id"].split(".")[1]
        stare = e.get("state")
        if stare == "home":
            acasa.append(nume)
        elif stare in ("not_home", "away"):
            plecati.append(nume)
        else:
            necunoscuti.append(nume)   # telefon oprit / fără locație

    if not (acasa or plecati or necunoscuti):
        return {"status": "ok", "message": "Nu am nicio persoană configurată."}

    parti = []
    if acasa:
        parti.append(("E acasă: " if len(acasa) == 1 else "Sunt acasă: ") + ", ".join(acasa))
    if plecati:
        parti.append("Plecați: " + ", ".join(plecati))
    if necunoscuti:
        parti.append("Fără locație (telefon oprit): " + ", ".join(necunoscuti))

    logger.info(f"🏠 [HA] Acasă: {acasa or 'nimeni'}")
    return {"status": "ok", "acasa": acasa, "plecati": plecati,
            "message": ". ".join(parti) + "."}


# ─────────────────────────────────────────────────────────────
# VREME LOCALĂ
# ─────────────────────────────────────────────────────────────

def local_weather() -> dict:
    """
    Vremea din senzorul casei. Instant și gratis — folosește ASTA în loc de
    căutare web pentru vremea de acum.
    """
    st = _get_state(WEATHER_ENTITY)
    if not st:
        return {"status": "error", "message": "Nu pot citi vremea din Home Assistant."}

    a = st.get("attributes") or {}
    stare = _STARI_RO.get(st.get("state"), st.get("state", "?"))
    temp = a.get("temperature")
    umid = a.get("humidity")
    vant = a.get("wind_speed")

    bucati = [f"Afară e {stare}"]
    if temp is not None:
        bucati.append(f"{temp}°C")
    if umid is not None:
        bucati.append(f"umiditate {umid}%")
    if vant is not None:
        bucati.append(f"vânt {vant} {a.get('wind_speed_unit', 'km/h')}")

    logger.info(f"🌤️ [HA] Vreme: {stare}, {temp}°C")
    return {"status": "ok", "stare": stare, "temperatura": temp,
            "umiditate": umid, "message": ", ".join(bucati) + ".",
            "nota": "Astea sunt date locale ACUM. Pentru zilele următoare "
                    "cere prognoza, tot de aici — nu căuta pe net."}


# ─────────────────────────────────────────────────────────────
# PROGNOZĂ
# ─────────────────────────────────────────────────────────────

_ZILE_RO = ("luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică")

FORECAST_DAYS_DEFAULT = 3
FORECAST_DAYS_MAX = 5


def _nume_zi(zi: date, azi: date) -> str:
    """„azi" / „mâine" / „poimâine", apoi numele zilei — cum ar zice un om."""
    delta = (zi - azi).days
    if delta == 0:
        return "azi"
    if delta == 1:
        return "mâine"
    if delta == 2:
        return "poimâine"
    return _ZILE_RO[zi.weekday()]


def _parse_moment(text: str):
    """ISO din HA → dată locală. HA trimite UTC cu offset explicit."""
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone().date()
    except Exception:
        return None


def weather_forecast(days: int = FORECAST_DAYS_DEFAULT) -> dict:
    """
    Prognoza pe zilele următoare — tot din Home Assistant, nu de pe net.

    Entitatea `weather` are deja prognoza, dar nu în atribute: se cere prin
    serviciul `weather.get_forecasts` (HA ≥ 2024.4, cu `return_response`).
    Același apel în rețeaua locală ca restul modulului — milisecunde și zero
    tokeni, în loc de un round-trip de căutare web.
    """
    if not is_configured():
        return {"status": "error", "message": "Home Assistant nu e configurat."}

    try:
        days = int(days)
    except (TypeError, ValueError):
        days = FORECAST_DAYS_DEFAULT
    days = max(1, min(days or FORECAST_DAYS_DEFAULT, FORECAST_DAYS_MAX))

    try:
        r = _session.post(
            f"{BASE_URL}/api/services/weather/get_forecasts",
            headers=_headers(), timeout=_TIMEOUT,
            params={"return_response": "true"},
            json={"entity_id": WEATHER_ENTITY, "type": "daily"},
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        logger.error(f"❌ [HA] Prognoza a eșuat: {e}")
        return {"status": "error", "message": "Nu pot citi prognoza din Home Assistant."}

    # Răspunsul vine ca {"service_response": {"<entity>": {"forecast": [...]}}}.
    raspuns = payload.get("service_response") if isinstance(payload, dict) else None
    if not isinstance(raspuns, dict):
        raspuns = payload if isinstance(payload, dict) else {}
    intrare = raspuns.get(WEATHER_ENTITY)
    if not isinstance(intrare, dict):
        intrare = next((v for v in raspuns.values() if isinstance(v, dict) and "forecast" in v), {})
    lista = intrare.get("forecast") or []
    if not lista:
        return {"status": "error", "message": "Prognoza nu e disponibilă acum."}

    azi = datetime.now().astimezone().date()
    zile, bucati = [], []
    for item in lista[:days]:
        zi = _parse_moment(item.get("datetime"))
        if zi is None:
            continue
        stare = _STARI_RO.get(item.get("condition"), item.get("condition") or "?")
        tmax, tmin = item.get("temperature"), item.get("templow")
        ploaie = item.get("precipitation") or 0
        nume = _nume_zi(zi, azi)

        zile.append({"zi": nume, "data": zi.isoformat(), "stare": stare,
                     "maxim": tmax, "minim": tmin, "precipitatii": ploaie})

        text = f"{nume} {stare}"
        if tmax is not None:
            text += f", maxim {round(tmax)} grade"
        if tmin is not None:
            text += f", minim {round(tmin)}"
        if ploaie:
            text += f", {ploaie} mm de ploaie"
        bucati.append(text)

    if not zile:
        return {"status": "error", "message": "Prognoza a venit necitibilă."}

    logger.info(f"🌦️ [HA] Prognoză {len(zile)} zile.")
    return {"status": "ok", "zile": zile,
            "message": "; ".join(bucati) + ".",
            "nota": "Prognoză locală din Home Assistant. Citește-o ca atare, "
                    "nu căuta pe net și nu inventa alte zile."}


# ─────────────────────────────────────────────────────────────
# POTRIVIRE PE TEXT (pentru calea text, care n-are function calling)
# ─────────────────────────────────────────────────────────────

# Deliberat îngust. Calea text trimite orice întrebare despre vreme la
# căutare web; scurtcircuitul ăsta o oprește DOAR când e clar o întrebare
# despre vremea de aici. La orice dubiu întoarce None și comanda merge mai
# departe pe drumul normal — o ratare costă o căutare web, o potrivire
# greșită strică o conversație.

_VREME_CUVINTE = ("vreme", "vremea", "prognoza", "prognoza pe", "meteo",
                  "ploua", "ploaie", "ninge", "ninsoare")

# „pierzi vremea", „pe vremea aia", „de vreme ce" — vorbesc despre timp, nu
# despre cer. Fără lista asta scurtcircuitul ar fura replici de conversație.
_VREME_CAPCANE = ("pierde vremea", "pierzi vremea", "pierd vremea",
                  "pierdem vremea", "pierdut vremea", "pierdeti vremea",
                  "de vreme ce", "din vreme", "pe vremea", "vremea aia",
                  "vremuri")

_VIITOR_CUVINTE = ("maine", "poimaine", "weekend", "saptamana", "zilele",
                   "urmatoarele", "prognoza", "zile", "joi", "vineri",
                   "sambata", "duminica", "luni", "marti", "miercuri")

_FARA_DIACRITICE = str.maketrans("ăâîșşțţ", "aaisstt")


def _normalizeaza(text: str) -> str:
    """Minuscule, fără diacritice — Sergiu scrie în ambele feluri."""
    return (text or "").lower().translate(_FARA_DIACRITICE)


def match_query(text: str) -> Optional[dict]:
    """Întrebare despre vreme → argumente pentru `answer`, altfel None."""
    t = _normalizeaza(text)
    if any(c in t for c in _VREME_CAPCANE):
        return None
    if not any(c in t for c in _VREME_CUVINTE):
        return None

    if not any(c in t for c in _VIITOR_CUVINTE):
        return {"kind": "vreme"}

    zile = FORECAST_DAYS_DEFAULT
    nr = re.search(r"(\d+)\s*zile", t)
    if nr:
        zile = int(nr.group(1))
    elif "saptamana" in t:
        zile = FORECAST_DAYS_MAX
    return {"kind": "prognoza", "zile": zile}


def answer(kind: str = "vreme", zile: int = FORECAST_DAYS_DEFAULT) -> str:
    """Răspuns gata de citit, pentru scurtcircuitul din calea text."""
    rezultat = weather_forecast(zile) if kind == "prognoza" else local_weather()
    return rezultat.get("message") or "Nu pot citi vremea acum."

# ─────────────────────────────────────────────────────────────
# CLI — verificare fără AI: python -m tools.home_assistant
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json as _json
    import sys

    # Consola Windows e cp1252 implicit și se îneacă la primul „ț".
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ap = argparse.ArgumentParser(description="Verificare Home Assistant din linia de comandă.")
    ap.add_argument("actiune", nargs="?", default="prognoza",
                    choices=["prognoza", "vreme", "acasa"])
    ap.add_argument("--zile", type=int, default=FORECAST_DAYS_DEFAULT)
    ap.add_argument("--json", action="store_true", help="Afișează dicționarul brut.")
    args = ap.parse_args()

    if args.actiune == "prognoza":
        rezultat = weather_forecast(args.zile)
    elif args.actiune == "vreme":
        rezultat = local_weather()
    else:
        rezultat = who_is_home()

    if args.json:
        print(_json.dumps(rezultat, ensure_ascii=False, indent=2))
    else:
        print(rezultat.get("message", rezultat))
