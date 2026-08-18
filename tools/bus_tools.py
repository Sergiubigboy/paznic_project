"""
tools/bus_tools.py — Autobuze Târgu Mureș (Transport Local) pentru Chronos
================================================================================
Răspunde la "când îmi vine următorul autobuz spre X?" pentru Sergiu, care stă
pe Str. Argeșului nr. 24 (Cornișa, Târgu Mureș).

Sursa de date:
    - GTFS static  : https://new.transportlocal.ro/live/gtfs.zip  (programul oficial)
    - Poziții live : https://new.transportlocal.ro/live/journeys.json (refresh 3s)

De ce GTFS și nu live: feed-ul live dă doar vehicle_id + linie + lat/lon, FĂRĂ
trip_id, direcție sau întârziere. Nu se poate deduce dintr-o singură citire dacă
un autobuz vine spre tine sau pleacă de la tine. Deci programul GTFS e motorul
de decizie, iar live-ul e doar confirmare ("linia 23 are 3 autobuze pe traseu").

Design:
    - `build_schedule()` descarcă GTFS o dată și pre-calculează TOATE legăturile
      directe casă → destinație într-un singur JSON (chronos_data/transport/).
      Interogările ulterioare nu mai ating rețeaua și nu mai parsează 57k linii.
    - Se aleg automat între cele două stații de lângă casă (Mihai Eminescu la 22 m
      și Braseria Universității la 130 m) — ele sunt complementare: una duce spre
      spital/școală, cealaltă spre centru/Unirii/Tudor.
    - Criteriul de sortare e ORA DE SOSIRE la destinație (inclusiv mersul pe jos
      de la stația de coborâre), nu ora de plecare. Asta e "ajung cât mai repede".
    - Se compară mereu și cu varianta pe jos: pe distanțe scurte (sala, școala)
      mersul pe jos bate autobuzul dacă aștepți mult.

Testare din terminal (înainte de a fi expus ca tool AI):
    python -m tools.bus_tools build            # descarcă GTFS + scrie JSON-ul
    python -m tools.bus_tools next sala        # următoarele autobuze spre sală
    python -m tools.bus_tools next scoala --at 07:40
    python -m tools.bus_tools all              # toate destinațiile, acum
    python -m tools.bus_tools dests            # ce destinații sunt configurate
    python -m tools.bus_tools live 23          # ce autobuze de pe linia 23 rulează

Destinațiile și minutele de mers pe jos sunt editabile în
chronos_data/transport/bus_config.json (se regenerează doar dacă lipsește).
"""

import argparse
import csv
import io
import json
import logging
import math
import os
import sys
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSPORT_DIR = os.path.join(BASE_DIR, "chronos_data", "transport")
GTFS_ZIP = os.path.join(TRANSPORT_DIR, "gtfs.zip")
SCHEDULE_JSON = os.path.join(TRANSPORT_DIR, "bus_schedule.json")
CONFIG_JSON = os.path.join(TRANSPORT_DIR, "bus_config.json")

GTFS_URL = "https://new.transportlocal.ro/live/gtfs.zip"
LIVE_URL = "https://new.transportlocal.ro/live/journeys.json"

# GTFS-ul e valabil până în 2027, dar reîmprospătăm săptămânal ca să prindem
# modificările de program (feed_version crește des).
GTFS_MAX_AGE_DAYS = 7

# Mers pe jos: 4.8 km/h = 80 m/min, cu factor 1.3 pentru că drumul real nu e
# în linie dreaptă (blocuri, treceri de pietoni).
WALK_M_PER_MIN = 80.0
WALK_DETOUR = 1.3

TZ_NAME = "Europe/Bucharest"

# service_id → zilele săptămânii (0=luni) sunt citite din calendar.txt, dar
# ținem maparea asta ca fallback dacă fișierul lipsește.
_FALLBACK_SERVICE_DAYS = {"3": [0, 1, 2, 3, 4], "4": [5], "5": [6]}


# ─────────────────────────────────────────────────────────────
# CONFIG IMPLICIT — casa lui Sergiu + destinațiile lui
# ─────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict[str, Any] = {
    "_comentariu": (
        "Editează liber. 'stops' sunt stop_id din GTFS-ul Transport Local. "
        "'walk_min' se calculează automat la build dacă e null; pune o valoare "
        "manuală dacă distanța reală pe jos diferă de linia dreaptă."
    ),
    "home": {
        "address": "Str. Argeșului nr. 24, Cornișa, Târgu Mureș",
        "lat": 46.5512609,
        "lon": 24.5753226,
    },
    # Cele două stații de lângă casă.
    "origins": [
        {"stop_id": "607", "walk_min": None},   # Mihai Eminescu — 22 m
        {"stop_id": "598", "walk_min": None},   # Braseria Universității — 130 m
    ],
    "destinations": {
        "scoala": {
            "label": "Școala (UMFST, campus Medicină - Gh. Marinescu)",
            "aliases": ["umfst", "universitate", "facultate", "medicina", "cursuri"],
            "lat": 46.5565479,
            "lon": 24.5827960,
            "stops": [
                {"stop_id": "591", "walk_min": None},   # Parcul Eroilor — 218 m
                {"stop_id": "601", "walk_min": None},   # Spitalul Județean — 339 m
                {"stop_id": "249", "walk_min": None},   # SMURD — 359 m
                {"stop_id": "589", "walk_min": None},   # Clinica O.R.L. — 412 m
            ],
        },
        "sala": {
            "label": "Sala (Gold Gym, Str. Molter Károly - lângă Spitalul Județean)",
            "aliases": ["gym", "gold gym", "antrenament", "fitness"],
            "lat": 46.5610767,
            "lon": 24.5819893,
            "stops": [
                {"stop_id": "249", "walk_min": None},   # SMURD — 178 m
                {"stop_id": "601", "walk_min": None},   # Spitalul Județean — 192 m
            ],
        },
        "centru": {
            "label": "Centru (Piața Trandafirilor / Piața Teatrului)",
            "aliases": ["oras", "trandafirilor", "teatru", "piata trandafirilor"],
            "lat": 46.543753,
            "lon": 24.560285,
            "stops": [
                {"stop_id": "563", "walk_min": None},   # Piața Trandafirilor
                {"stop_id": "565", "walk_min": None},   # Piața Teatrului
            ],
        },
        "tudor": {
            "label": "Cartierul Tudor (Fortuna - intersecția mare)",
            "aliases": ["cartierul tudor", "fortuna", "tudor vladimirescu"],
            "lat": 46.535498,
            "lon": 24.582649,
            "stops": [
                {"stop_id": "581", "walk_min": None},   # Fortuna
                {"stop_id": "570", "walk_min": None},   # B-dul 1 Decembrie 1918 — 130 m
                {"stop_id": "562", "walk_min": None},   # Pandurilor — 151 m
            ],
        },
        "unirii": {
            "label": "Unirii",
            "aliases": ["piata unirii", "cartierul unirii"],
            "lat": 46.562773,
            "lon": 24.547528,
            "stops": [
                {"stop_id": "608", "walk_min": None},   # Unirii
                {"stop_id": "606", "walk_min": None},   # Piața Unirii
            ],
        },
    },
}


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _now() -> datetime:
    """Ora locală din Târgu Mureș. Cade elegant pe ora sistemului dacă tzdata
    lipsește (pe mașina lui Sergiu oricum e același fus)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(TZ_NAME))
    except Exception:
        return datetime.now()


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanță în metri între două coordonate."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _walk_minutes(meters: float) -> int:
    """Minute de mers pe jos, rotunjite în sus (minim 1 dacă distanța > 0)."""
    if meters <= 0:
        return 0
    return max(1, math.ceil(meters * WALK_DETOUR / WALK_M_PER_MIN))


def _hms_to_sec(t: str) -> int:
    """'07:20:10' → secunde de la miezul nopții. GTFS permite și 25:xx:xx."""
    parts = t.strip().split(":")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


def _sec_to_hm(sec: int) -> str:
    """Secunde → 'HH:MM' (normalizat peste 24h)."""
    sec %= 86400
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}"


def _norm(s: str) -> str:
    """Normalizare pentru potrivirea numelor: fără diacritice, minuscule."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def _load_json(path: str, default: Any) -> Any:
    """Citire tolerantă — o interogare de autobuz nu trebuie să arunce excepție."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.warning("bus_tools: nu pot citi %s (%s)", path, e)
        return default


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_config() -> dict[str, Any]:
    """Configul editabil de pe disc; îl scrie prima dată din DEFAULT_CONFIG."""
    cfg = _load_json(CONFIG_JSON, None)
    if not cfg:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # copie
        _save_json(CONFIG_JSON, cfg)
        logger.info("bus_tools: am scris configul implicit în %s", CONFIG_JSON)
    return cfg


# ─────────────────────────────────────────────────────────────
# GTFS — descărcare & parsare
# ─────────────────────────────────────────────────────────────

def download_gtfs(force: bool = False) -> str:
    """Descarcă gtfs.zip dacă lipsește sau e mai vechi de GTFS_MAX_AGE_DAYS."""
    if not force and os.path.exists(GTFS_ZIP):
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(GTFS_ZIP))
        if age < timedelta(days=GTFS_MAX_AGE_DAYS):
            return GTFS_ZIP

    import requests
    os.makedirs(TRANSPORT_DIR, exist_ok=True)
    logger.info("bus_tools: descarc GTFS de la %s", GTFS_URL)
    resp = requests.get(GTFS_URL, timeout=60)
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):
        raise RuntimeError("răspunsul de la gtfs.zip nu e o arhivă ZIP")
    with open(GTFS_ZIP, "wb") as f:
        f.write(resp.content)
    return GTFS_ZIP


def _read_table(z: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    """Un .txt din GTFS → listă de dict-uri (BOM-ul UTF-8 e strip-uit)."""
    raw = z.read(name).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


def _service_calendar(z: zipfile.ZipFile) -> dict[str, Any]:
    """calendar.txt + calendar_dates.txt → structură serializabilă în JSON."""
    days_cols = ["monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday"]
    services: dict[str, Any] = {}
    try:
        for row in _read_table(z, "calendar.txt"):
            services[row["service_id"]] = {
                "days": [i for i, c in enumerate(days_cols) if row.get(c) == "1"],
                "start_date": row.get("start_date", ""),
                "end_date": row.get("end_date", ""),
            }
    except KeyError:
        for sid, days in _FALLBACK_SERVICE_DAYS.items():
            services[sid] = {"days": days, "start_date": "", "end_date": ""}

    # exception_type 1 = serviciu ADĂUGAT în ziua respectivă, 2 = ELIMINAT.
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    try:
        for row in _read_table(z, "calendar_dates.txt"):
            bucket = added if row.get("exception_type") == "1" else removed
            bucket.setdefault(row["date"], []).append(row["service_id"])
    except KeyError:
        pass
    return {"services": services, "added": added, "removed": removed}


# ─────────────────────────────────────────────────────────────
# BUILD — pre-calculează toate legăturile directe casă → destinații
# ─────────────────────────────────────────────────────────────

def build_schedule(force_download: bool = False) -> dict[str, Any]:
    """Construiește chronos_data/transport/bus_schedule.json.

    Pentru fiecare destinație salvează TOATE plecările directe din cele două
    stații de lângă casă, cu ora de plecare, ora de sosire și stația de coborâre
    optimă (cea care minimizează sosire + mers pe jos).
    """
    cfg = load_config()
    path = download_gtfs(force=force_download)

    with zipfile.ZipFile(path) as z:
        stops_raw = _read_table(z, "stops.txt")
        routes_raw = _read_table(z, "routes.txt")
        trips_raw = _read_table(z, "trips.txt")
        stop_times_raw = _read_table(z, "stop_times.txt")
        calendar = _service_calendar(z)
        try:
            feed_version = _read_table(z, "feed_info.txt")[0].get("feed_version", "?")
        except Exception:
            feed_version = "?"

    stops = {
        s["stop_id"]: {
            "name": s["stop_name"].strip(),
            "lat": float(s["stop_lat"]),
            "lon": float(s["stop_lon"]),
        }
        for s in stops_raw
    }
    routes = {r["route_id"]: (r.get("route_short_name") or r["route_id"]).strip()
              for r in routes_raw}
    trips = {t["trip_id"]: t for t in trips_raw}

    # trip_id → secvența de opriri, ordonată după stop_sequence
    by_trip: dict[str, list[tuple[int, str, int, int]]] = {}
    for row in stop_times_raw:
        by_trip.setdefault(row["trip_id"], []).append((
            int(row["stop_sequence"]),
            row["stop_id"],
            _hms_to_sec(row["departure_time"]),
            _hms_to_sec(row["arrival_time"]),
        ))
    for seq in by_trip.values():
        seq.sort()

    home = cfg["home"]

    def _resolve(entry: dict[str, Any], ref_lat: float, ref_lon: float) -> Optional[dict]:
        """Completează numele, distanța și walk_min pentru o stație din config."""
        sid = str(entry["stop_id"])
        st = stops.get(sid)
        if not st:
            logger.warning("bus_tools: stop_id %s nu există în GTFS, îl ignor", sid)
            return None
        dist = _haversine(ref_lat, ref_lon, st["lat"], st["lon"])
        walk = entry.get("walk_min")
        return {
            "stop_id": sid,
            "name": st["name"],
            "lat": st["lat"],
            "lon": st["lon"],
            "dist_m": round(dist),
            "walk_min": int(walk) if walk is not None else _walk_minutes(dist),
        }

    origins = [o for o in (_resolve(e, home["lat"], home["lon"])
                           for e in cfg["origins"]) if o]
    origin_ids = {o["stop_id"] for o in origins}
    origin_by_id = {o["stop_id"]: o for o in origins}

    destinations: dict[str, Any] = {}
    for key, d in cfg["destinations"].items():
        resolved = [s for s in (_resolve(e, d["lat"], d["lon"])
                                for e in d["stops"]) if s]
        destinations[key] = {
            "label": d["label"],
            "aliases": d.get("aliases", []),
            "lat": d["lat"],
            "lon": d["lon"],
            "walk_from_home_min": _walk_minutes(
                _haversine(home["lat"], home["lon"], d["lat"], d["lon"])),
            "stops": resolved,
            "stop_ids": [s["stop_id"] for s in resolved],
        }

    # ── Scanare: pentru fiecare cursă care atinge o stație de plecare, caută
    #    stațiile destinație de DUPĂ ea (asta rezolvă automat și direcția).
    connections: dict[str, list[dict]] = {k: [] for k in destinations}

    for trip_id, seq in by_trip.items():
        trip = trips.get(trip_id)
        if not trip:
            continue
        # Traseele circulare (ex. 10 - Unirii - Spital - Unirii) trec de două ori
        # prin aceeași stație; fiecare trecere e o plecare reală, deci le luăm pe
        # toate, nu doar prima.
        hits = [i for i, (_, sid, _, _) in enumerate(seq) if sid in origin_ids]
        if not hits:
            continue

        line = routes.get(trip["route_id"], trip["route_id"])
        service_id = trip["service_id"]
        headsign = (trip.get("trip_headsign") or "").strip()

        for i in hits:
            o_stop = seq[i][1]
            dep_s = seq[i][2]
            o_walk = origin_by_id[o_stop]["walk_min"]

            for key, dest in destinations.items():
                wanted = set(dest["stop_ids"])
                walk_by_stop = {s["stop_id"]: s["walk_min"] for s in dest["stops"]}
                best = None
                for j in range(i + 1, len(seq)):
                    sid = seq[j][1]
                    if sid not in wanted:
                        continue
                    arr_s = seq[j][3]
                    # Criteriul: momentul în care ajung efectiv la destinație.
                    total = arr_s + walk_by_stop[sid] * 60
                    if best is None or total < best[0]:
                        best = (total, sid, arr_s)
                if best is None:
                    continue
                _, d_stop, arr_s = best
                connections[key].append({
                    "line": line,
                    "headsign": headsign,
                    "service": service_id,
                    "origin_stop": o_stop,
                    "origin_walk_min": o_walk,
                    "dep": _sec_to_hm(dep_s),
                    "dep_s": dep_s,
                    "dest_stop": d_stop,
                    "dest_walk_min": walk_by_stop[d_stop],
                    "arr": _sec_to_hm(arr_s),
                    "arr_s": arr_s,
                    "ride_min": max(1, round((arr_s - dep_s) / 60)),
                })

    # Dedup + sortare cronologică (mai multe trip_id pot descrie același autobuz).
    for key, rows in connections.items():
        seen = set()
        unique = []
        for r in sorted(rows, key=lambda x: (x["dep_s"], x["arr_s"])):
            sig = (r["line"], r["service"], r["origin_stop"], r["dep_s"], r["arr_s"])
            if sig in seen:
                continue
            seen.add(sig)
            unique.append(r)
        connections[key] = unique

    schedule = {
        "generated_at": _now().isoformat(timespec="seconds"),
        "source": GTFS_URL,
        "gtfs_feed_version": feed_version,
        "home": home,
        "origins": origins,
        "calendar": calendar,
        "destinations": destinations,
        "connections": connections,
    }
    _save_json(SCHEDULE_JSON, schedule)
    logger.info("bus_tools: schedule scris în %s", SCHEDULE_JSON)
    return schedule


def load_schedule(auto_build: bool = True) -> dict[str, Any]:
    """Programul de pe disc; îl construiește la prima folosire."""
    sched = _load_json(SCHEDULE_JSON, None)
    if not sched and auto_build:
        sched = build_schedule()
    if not sched:
        raise RuntimeError("nu există bus_schedule.json — rulează 'build'")
    return sched


# ─────────────────────────────────────────────────────────────
# INTEROGARE
# ─────────────────────────────────────────────────────────────

def _active_services(sched: dict[str, Any], day: date) -> set[str]:
    """Ce service_id-uri circulă în ziua dată (cu excepțiile din calendar_dates)."""
    cal = sched.get("calendar", {})
    ymd = day.strftime("%Y%m%d")
    active = set()
    for sid, meta in cal.get("services", {}).items():
        if day.weekday() not in meta.get("days", []):
            continue
        start, end = meta.get("start_date"), meta.get("end_date")
        if start and ymd < start:
            continue
        if end and ymd > end:
            continue
        active.add(sid)
    active |= set(cal.get("added", {}).get(ymd, []))
    active -= set(cal.get("removed", {}).get(ymd, []))
    return active


def resolve_destination(sched: dict[str, Any], query: str) -> Optional[str]:
    """Text liber → cheia destinației ('sala', 'scoala', ...). None dacă nu găsesc."""
    q = _norm(query)
    if not q:
        return None
    dests = sched["destinations"]
    if q in dests:
        return q
    # potrivire exactă pe alias sau label
    for key, d in dests.items():
        if q == _norm(key) or q in [_norm(a) for a in d.get("aliases", [])]:
            return key
    # potrivire parțială (ca să prindem "vreau la sala" / "gold gym-ul")
    for key, d in dests.items():
        cands = [key, d["label"]] + list(d.get("aliases", []))
        for c in cands:
            cn = _norm(c)
            if cn and (cn in q or q in cn):
                return key
    return None


# Cuvinte care marchează o întrebare despre transport. Fără unul dintre ele NU
# tratăm mesajul ca întrebare de autobuz — altfel „am fost la sală” ar declanșa
# tool-ul în loc să ajungă la jurnal.
_TRANSIT_WORDS = (
    "bus", "buz", "autobuz", "autobuze", "busu", "busul",
    "statie", "statia", "transport", "troleu",
)
# Cereri de tipul „și mai târziu ce am?” → întoarcem mai multe variante.
_MORE_WORDS = ("variante", "urmatoarele", "mai tarziu", "alte", "dupa", "si apoi")


def match_query(text: str) -> Optional[dict[str, Any]]:
    """Text liber → argumente pentru `next_buses`, sau None dacă nu e despre autobuz.

    Rulează pe potrivire de cuvinte, deterministic: e drumul prin care întrebările
    despre autobuz ocolesc complet planificatorul LLM.
    """
    if not text:
        return None
    t = _norm(text)
    if not any(w in t for w in _TRANSIT_WORDS):
        return None
    try:
        sched = load_schedule(auto_build=False)
    except Exception:
        return None
    dest = resolve_destination(sched, t)
    if not dest:
        return None
    return {
        "destination": dest,
        "limit": 5 if any(w in t for w in _MORE_WORDS) else 3,
    }


def answer(destination: str, limit: int = 3,
           at: Optional[datetime] = None) -> str:
    """Scurtătură: întrebare → text gata de rostit."""
    return format_answer(next_buses(destination, at=at, limit=limit))


def next_buses(
    destination: str,
    at: Optional[datetime] = None,
    limit: int = 3,
    include_live: bool = True,
    horizon_hours: int = 14,
) -> dict[str, Any]:
    """Următoarele autobuze de acasă spre `destination`.

    Întoarce un dict cu opțiunile ordonate după ORA DE SOSIRE la destinație.
    Fiecare opțiune spune din ce stație pleacă și în câte minute trebuie să ieși
    din casă. Se caută și în zilele următoare dacă azi nu mai circulă nimic.
    """
    sched = load_schedule()
    now = at or _now()

    # Feed-ul live arată vehiculele din clipa asta. Dacă interogarea simulează
    # altă oră (--at), datele live n-au nicio legătură cu ea → le ignorăm, altfel
    # apar avertismente false de tipul „linia n-are GPS activ”.
    if at is not None and abs((at - _now()).total_seconds()) > 300:
        include_live = False

    key = resolve_destination(sched, destination)
    if not key:
        return {
            "ok": False,
            "error": f"nu știu destinația '{destination}'",
            "destinatii_disponibile": sorted(sched["destinations"].keys()),
        }

    dest = sched["destinations"][key]
    conns = sched["connections"].get(key, [])
    origins = {o["stop_id"]: o for o in sched["origins"]}
    stop_names = {s["stop_id"]: s["name"] for s in dest["stops"]}

    options: list[dict[str, Any]] = []
    # Zi 0 = azi (de la ora actuală), apoi zilele următoare de la 00:00.
    for day_offset in range(0, math.ceil(horizon_hours / 24) + 1):
        day = now.date() + timedelta(days=day_offset)
        active = _active_services(sched, day)
        base = datetime.combine(day, datetime.min.time())
        if now.tzinfo:
            base = base.replace(tzinfo=now.tzinfo)

        for c in conns:
            if c["service"] not in active:
                continue
            dep_dt = base + timedelta(seconds=c["dep_s"])
            if dep_dt <= now:
                continue
            if (dep_dt - now).total_seconds() > horizon_hours * 3600:
                continue

            o = origins.get(c["origin_stop"], {})
            walk_o = c["origin_walk_min"]
            leave_dt = dep_dt - timedelta(minutes=walk_o)
            # Sosirea la STAȚIA de coborâre (asta vrea Sergiu să audă) și,
            # separat, sosirea efectivă la destinație — a doua e folosită doar
            # ca să aleg între stațiile candidate, nu se rostește.
            stop_dt = base + timedelta(seconds=c["arr_s"])
            arrive_dt = stop_dt + timedelta(minutes=c["dest_walk_min"])
            # Rotunjire ÎN JOS intenționat: mai bine să-i spun că are mai puțin
            # timp decât are, nu invers — altfel pierde autobuzul.
            leave_in = math.floor((leave_dt - now).total_seconds() / 60)

            options.append({
                "linia": c["line"],
                "spre": c["headsign"],
                # „în câte minute am bus” — numărat până la plecarea din stație.
                "in_min": math.floor((dep_dt - now).total_seconds() / 60),
                "statia_plecare": o.get("name", c["origin_stop"]),
                "statia_plecare_id": c["origin_stop"],
                "mers_pana_la_statie_min": walk_o,
                "distanta_statie_m": o.get("dist_m"),
                "plecare": dep_dt.strftime("%H:%M"),
                "ies_din_casa_in_min": leave_in,
                "ies_din_casa_la": leave_dt.strftime("%H:%M"),
                "statia_coborare": stop_names.get(c["dest_stop"], c["dest_stop"]),
                "ajung_la_statie": stop_dt.strftime("%H:%M"),
                "mers_de_la_statie_min": c["dest_walk_min"],
                "in_autobuz_min": c["ride_min"],
                "ajung_la_destinatie": arrive_dt.strftime("%H:%M"),
                "total_min": round((arrive_dt - now).total_seconds() / 60),
                "azi": day == now.date(),
                "zi": day.isoformat(),
                # Dacă leave_in < 0 autobuzul n-a plecat încă, dar trebuie să
                # alergi ca să-l prinzi.
                "la_limita": leave_in < 0,
                "_arrive": arrive_dt,
                "_dep": dep_dt,
            })

    if not options:
        return {
            "ok": False,
            "error": "nu mai circulă nimic spre această destinație în intervalul căutat",
            "destinatie": dest["label"],
            "acum": now.strftime("%H:%M"),
        }

    # Cel mai repede la destinație; la sosire egală preferăm plecarea mai târzie
    # (mai mult timp de reacție acasă).
    options.sort(key=lambda x: (x["_arrive"], -x["_dep"].timestamp()))

    # Păstrăm cea mai bună opțiune per (linie, stație de plecare) ca lista de
    # alternative să fie utilă, nu 3 autobuze de pe aceeași linie.
    top: list[dict[str, Any]] = []
    seen_pairs = set()
    for opt in options:
        pair = (opt["linia"], opt["statia_plecare_id"])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        top.append(opt)
        if len(top) >= max(1, limit):
            break
    # Dacă tot ce urmează e la limită, adăugăm și prima variantă relaxată.
    if all(o["la_limita"] for o in top):
        relaxed = next((o for o in options if not o["la_limita"]), None)
        if relaxed and relaxed not in top:
            top.append(relaxed)

    best = top[0]
    for o in top:
        o.pop("_arrive", None)
        o.pop("_dep", None)

    # Intenționat NU comparăm cu mersul pe jos și nu sugerăm „mai bine pe jos”:
    # Sergiu ia autobuzul chiar și pentru o singură stație (ex. până la O.R.L.),
    # tocmai ca să scape de mers. Răspunsul e mereu despre autobuz.
    result = {
        "ok": True,
        "destinatie": dest["label"],
        "destinatie_key": key,
        "acum": now.strftime("%H:%M"),
        "data": now.date().isoformat(),
        "recomandare": best,
        "alternative": top[1:],
        "program_generat_la": sched.get("generated_at"),
    }

    if include_live:
        result["live"] = _live_info(best["linia"],
                                   origins.get(best["statia_plecare_id"]))
    return result


# ─────────────────────────────────────────────────────────────
# LIVE — poziții vehicule (doar informativ)
# ─────────────────────────────────────────────────────────────

def fetch_live(timeout: int = 10) -> list[dict[str, Any]]:
    """Pozițiile curente ale vehiculelor. Listă goală dacă feed-ul e indisponibil."""
    try:
        import requests
        resp = requests.get(LIVE_URL, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("journeys", []) or []
    except Exception as e:
        logger.info("bus_tools: feed live indisponibil (%s)", e)
        return []


def _live_info(line: str, origin_stop: Optional[dict]) -> dict[str, Any]:
    """Câte vehicule are linia pe traseu și cât de departe e cel mai apropiat.

    Atenție: feed-ul NU dă direcția, deci distanța e doar orientativă — vehiculul
    cel mai apropiat poate merge în sens invers.
    """
    vehicles = fetch_live()
    if not vehicles:
        return {"disponibil": False}

    mine = [v for v in vehicles if str(v.get("route", "")).upper() == str(line).upper()]
    info: dict[str, Any] = {
        "disponibil": True,
        "linia": line,
        "vehicule_active": len(mine),
        "total_vehicule_oras": len(vehicles),
        "nota": "feed-ul live nu conține direcția; distanța e orientativă",
    }
    if mine and origin_stop:
        st_lat, st_lon = None, None
        sched = _load_json(SCHEDULE_JSON, {}) or {}
        for o in sched.get("origins", []):
            if o["stop_id"] == origin_stop.get("stop_id"):
                st_lat, st_lon = o.get("lat"), o.get("lon")
        if st_lat is None:
            st_lat = origin_stop.get("lat")
            st_lon = origin_stop.get("lon")
        if st_lat is not None and st_lon is not None:
            dists = [
                _haversine(st_lat, st_lon, float(v["lat"]), float(v["lon"]))
                for v in mine if v.get("lat") is not None and v.get("lon") is not None
            ]
            if dists:
                info["cel_mai_apropiat_m"] = round(min(dists))
    return info


# ─────────────────────────────────────────────────────────────
# FORMATARE PENTRU VOCE
# ─────────────────────────────────────────────────────────────

def _min_text(m: int) -> str:
    if m <= 0:
        return "chiar acum"
    if m == 1:
        return "1 minut"
    return f"{m} minute"


# Peste atâtea minute nu mai are sens „în N minute” — se spune direct ora.
_MIN_COUNT_LIMIT = 90


def _when_text(opt: dict[str, Any], today: str) -> str:
    """„în 12 minute (la 18:05)” / „la 05:20” / „mâine la 05:20”."""
    if not opt.get("azi"):
        try:
            delta = (date.fromisoformat(opt["zi"]) - date.fromisoformat(today)).days
        except Exception:
            delta = 0
        day = "mâine" if delta == 1 else f"pe {opt['zi']}"
        return f"{day} la {opt['plecare']}"
    if opt["in_min"] <= 0:
        return f"acum (la {opt['plecare']})"
    if opt["in_min"] > _MIN_COUNT_LIMIT:
        return f"la {opt['plecare']}"
    return f"în {_min_text(opt['in_min'])} (la {opt['plecare']})"


def format_answer(result: dict[str, Any]) -> str:
    """Răspuns scurt în română, gata de rostit ca atare.

    Formularea e fixă și completă tocmai ca LLM-ul să nu aibă ce recalcula: are
    deja minutele până la autobuz, linia, stația și ora de sosire.
    """
    if not result.get("ok"):
        err = result.get("error", "ceva n-a mers")
        if result.get("destinatii_disponibile"):
            return f"{err}. Știu: {', '.join(result['destinatii_disponibile'])}."
        return err.capitalize() + "."

    r = result["recomandare"]
    today = result.get("data", "")
    lines = []

    lines.append(
        f"Linia {r['linia']} {_when_text(r, today)} din {r['statia_plecare']} "
        f"— ajungi la {r['statia_coborare']} la {r['ajung_la_statie']}."
    )

    if r["la_limita"]:
        lines.append(f"Trebuie să fugi — ai {_min_text(r['mers_pana_la_statie_min'])} pe jos până în stație.")
    elif r["ies_din_casa_in_min"] <= 0:
        lines.append(f"Ieși acum din casă ({_min_text(r['mers_pana_la_statie_min'])} pe jos).")
    elif r["ies_din_casa_in_min"] > _MIN_COUNT_LIMIT:
        lines.append(f"Ieși din casă la {r['ies_din_casa_la']}.")
    else:
        lines.append(f"Ieși din casă în {_min_text(r['ies_din_casa_in_min'])}, la {r['ies_din_casa_la']}.")

    # Lipsa GPS-ului contează doar pentru o cursă iminentă; pentru un autobuz de
    # mâine dimineață e zgomot.
    live = result.get("live") or {}
    if (live.get("disponibil") and not live.get("vehicule_active")
            and r["azi"] and r["in_min"] <= 30):
        lines.append(f"Atenție: linia {live['linia']} n-are niciun GPS activ acum.")

    alts = result.get("alternative", [])
    if alts:
        parts = []
        for a in alts:
            if a["azi"] and 0 < a["in_min"] <= _MIN_COUNT_LIMIT:
                head = f"în {a['in_min']} min"
            else:
                head = f"la {a['plecare']}"
            parts.append(f"{a['linia']} {head} (ajungi {a['ajung_la_statie']})")
        # „Variante”, nu „Apoi”: o alternativă poate pleca mai devreme decât
        # recomandarea, dar să ajungă mai târziu.
        lines.append("Variante: " + " · ".join(parts) + ".")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CLI — pentru testare manuală
# ─────────────────────────────────────────────────────────────

def _parse_at(s: Optional[str]) -> Optional[datetime]:
    """'07:40' sau '2026-08-18 07:40' → datetime în fusul local."""
    if not s:
        return None
    now = _now()
    for fmt, has_date in (("%Y-%m-%d %H:%M", True), ("%Y-%m-%dT%H:%M", True),
                          ("%H:%M", False)):
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if not has_date:
            dt = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        elif now.tzinfo:
            dt = dt.replace(tzinfo=now.tzinfo)
        return dt
    raise SystemExit(f"nu pot interpreta ora '{s}' (folosește HH:MM sau YYYY-MM-DD HH:MM)")


def main(argv: Optional[list[str]] = None) -> int:
    # Consola Windows e pe cp1252 și ar arunca UnicodeEncodeError pe diacritice.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ap = argparse.ArgumentParser(
        prog="python -m tools.bus_tools",
        description="Autobuze Târgu Mureș de pe Str. Argeșului 24.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="descarcă GTFS și scrie bus_schedule.json")
    p_build.add_argument("--force", action="store_true",
                         help="re-descarcă GTFS chiar dacă e recent")

    p_next = sub.add_parser("next", help="următoarele autobuze spre o destinație")
    p_next.add_argument("destination")
    p_next.add_argument("--at", help="simulează o altă oră (HH:MM)")
    p_next.add_argument("-n", "--limit", type=int, default=3)
    p_next.add_argument("--no-live", action="store_true")
    p_next.add_argument("--json", action="store_true", help="afișează dict-ul brut")

    p_all = sub.add_parser("all", help="toate destinațiile dintr-o privire")
    p_all.add_argument("--at", help="simulează o altă oră (HH:MM)")
    p_all.add_argument("--no-live", action="store_true")

    sub.add_parser("dests", help="destinațiile configurate")

    p_live = sub.add_parser("live", help="vehicule active (opțional filtrate pe linie)")
    p_live.add_argument("line", nargs="?")

    args = ap.parse_args(argv)

    if args.cmd == "build":
        s = build_schedule(force_download=args.force)
        total = sum(len(v) for v in s["connections"].values())
        print(f"OK — GTFS {s['gtfs_feed_version']}, {total} legături directe salvate.")
        print(f"    {SCHEDULE_JSON}")
        for k, v in s["connections"].items():
            lines = sorted({c["line"] for c in v}, key=lambda x: (len(x), x))
            print(f"  {k:10} {len(v):5} plecări   linii: {', '.join(lines) or '-'}")
        print(f"\nConfig editabil: {CONFIG_JSON}")
        return 0

    if args.cmd == "dests":
        s = load_schedule()
        print(f"Casa: {s['home']['address']}")
        print("\nStații de plecare:")
        for o in s["origins"]:
            print(f"  {o['stop_id']:>5}  {o['name']:42} {o['dist_m']:4} m  "
                  f"({o['walk_min']} min pe jos)")
        print("\nDestinații:")
        for k, d in s["destinations"].items():
            print(f"\n  {k}  —  {d['label']}")
            print(f"    pe jos direct de acasă: ~{d['walk_from_home_min']} min")
            print(f"    alias: {', '.join(d['aliases']) or '-'}")
            for st in d["stops"]:
                print(f"      {st['stop_id']:>5}  {st['name']:40} {st['dist_m']:4} m "
                      f"→ {st['walk_min']} min pe jos")
        return 0

    if args.cmd == "live":
        vehicles = fetch_live()
        if not vehicles:
            print("Feed live indisponibil sau gol.")
            return 1
        if args.line:
            vehicles = [v for v in vehicles
                        if str(v.get("route", "")).upper() == args.line.upper()]
        print(f"{len(vehicles)} vehicule:")
        for v in sorted(vehicles, key=lambda x: str(x.get("route"))):
            print(f"  linia {str(v.get('route')):>4}  {v.get('vehicle_id'):>7}  "
                  f"{v.get('lat')}, {v.get('lon')}")
        return 0

    at = _parse_at(getattr(args, "at", None))

    if args.cmd == "next":
        res = next_buses(args.destination, at=at, limit=args.limit,
                         include_live=not args.no_live)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(format_answer(res))
        return 0 if res.get("ok") else 1

    if args.cmd == "all":
        s = load_schedule()
        now = at or _now()
        print(f"── {now.strftime('%A %d.%m.%Y, ora %H:%M')} ──\n")
        for k in s["destinations"]:
            res = next_buses(k, at=at, limit=2, include_live=not args.no_live)
            print(f"▸ {k.upper()}")
            print("   " + format_answer(res).replace("\n", "\n   "))
            print()
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
