"""
tests/diagnostic.py — De ce nu pornește Chronos?
==================================================
Verifică TOT ce trebuie ca serviciul să meargă, în ordinea în care contează,
și spune exact ce comandă rezolvă fiecare problemă.

    python tests/diagnostic.py

Nu atinge nicio dată și nu trimite nimic — doar citește și raportează.
Merge și pe Windows, și pe Pi.
"""
import os
import platform
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, WARN, ERR = [], [], []
DESCARCA_MODELE = 'python -c "import openwakeword.utils as u; u.download_models()"'


def ok(msg):
    OK.append(msg)
    print("  [OK]  " + msg)


def warn(msg, fix=""):
    WARN.append((msg, fix))
    print("  [~~]  " + msg + (f"\n         -> {fix}" if fix else ""))


def err(msg, fix=""):
    ERR.append((msg, fix))
    print("  [!!]  " + msg + (f"\n         -> {fix}" if fix else ""))


def sectiune(titlu):
    print(f"\n{'-' * 62}\n{titlu}\n{'-' * 62}")


PE_PI = platform.machine().lower() in ("aarch64", "armv7l", "armv6l")

print("=" * 62)
print("  DIAGNOSTIC CHRONOS")
print("=" * 62)
print(f"  Sistem : {platform.system()} {platform.release()} ({platform.machine()})")
print(f"  Python : {sys.version.split()[0]}  ->  {sys.executable}")
print(f"  Proiect: {BASE}")
print(f"  Raspberry Pi: {'DA' if PE_PI else 'nu'}")

# ── 1. MEDIU ───────────────────────────────────────────────────────────
sectiune("1. MEDIU")

if sys.version_info < (3, 10):
    err(f"Python {sys.version_info.major}.{sys.version_info.minor} e prea vechi (min 3.10).")
else:
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")

if sys.prefix != sys.base_prefix:
    ok("Rulez intr-un mediu virtual.")
else:
    warn("NU esti in venv — pachetele pot lipsi sau fi altele.",
         "source venv/bin/activate")

# ── 2. PACHETE ─────────────────────────────────────────────────────────
sectiune("2. PACHETE PYTHON")

PACHETE = [
    ("google.genai",   "google-genai",  True,  "vocea Gemini Live"),
    ("sounddevice",    "sounddevice",   True,  "microfon + redare"),
    ("numpy",          "numpy",         True,  "procesare audio"),
    ("openwakeword",   "openwakeword",  True,  "wake word 'Jarvis'"),
    ("onnxruntime",    "onnxruntime",   True,  "backend wake word"),
    ("edge_tts",       "edge-tts",      True,  "vocea de rezerva"),
    ("miniaudio",      "miniaudio",     False, "decodare MP3"),
    ("chromadb",       "chromadb",      True,  "memoria vectoriala"),
    ("flask",          "Flask",         True,  "dashboard web"),
    ("requests",       "requests",      True,  "API-uri HTTP"),
    ("dotenv",         "python-dotenv", True,  "citirea .env"),
    ("spotipy",        "spotipy",       False, "Spotify"),
    ("scipy",          "scipy",         True,  "dependinta openwakeword"),
    ("sklearn",        "scikit-learn",  True,  "dependinta openwakeword"),
]

lipsa_critice = []
for modul, pachet, critic, la_ce in PACHETE:
    try:
        __import__(modul)
        try:
            from importlib.metadata import version
            v = version(pachet)
        except Exception:
            v = "?"
        ok(f"{pachet:<16} {v:<12} — {la_ce}")
    except ImportError:
        if critic:
            lipsa_critice.append(pachet)
            err(f"{pachet:<16} LIPSESTE    — {la_ce}")
        else:
            warn(f"{pachet:<16} lipseste    — {la_ce}")

if lipsa_critice:
    err(f"{len(lipsa_critice)} pachete critice lipsesc.",
        "pip install -r requirements.txt && pip install --no-deps openwakeword")

try:
    import tflite_runtime  # noqa: F401
    ok("tflite-runtime prezent.")
except ImportError:
    ok("tflite-runtime absent — normal, se foloseste onnxruntime.")

# ── 3. AUDIO ───────────────────────────────────────────────────────────
sectiune("3. AUDIO")

try:
    import sounddevice as sd
    try:
        devices = sd.query_devices()
        intrari = [(i, d) for i, d in enumerate(devices)
                   if (d.get("max_input_channels") or 0) > 0]
        iesiri = [(i, d) for i, d in enumerate(devices)
                  if (d.get("max_output_channels") or 0) > 0]

        if intrari:
            ok(f"{len(intrari)} dispozitive de INTRARE:")
            for i, d in intrari[:6]:
                print(f"           [{i}] {d.get('name')}")
            if len(intrari) > 6:
                print(f"           ... si inca {len(intrari) - 6}")
        else:
            err("NICIUN microfon gasit.",
                "arecord -l   |   sudo usermod -aG audio $USER   (apoi reboot)")

        if iesiri:
            ok(f"{len(iesiri)} dispozitive de IESIRE.")
        else:
            warn("Niciun dispozitiv de iesire — Chronos nu va putea vorbi.")

        if intrari:
            try:
                from config import AUDIO_INPUT_DEVICE
            except Exception:
                AUDIO_INPUT_DEVICE = ""
            idx = intrari[0][0]
            if AUDIO_INPUT_DEVICE:
                t = str(AUDIO_INPUT_DEVICE).strip()
                m = ([x for x in intrari if x[0] == int(t)] if t.isdigit()
                     else [x for x in intrari
                           if t.lower() in (x[1].get("name") or "").lower()])
                if m:
                    idx = m[0][0]
                    ok(f"AUDIO_INPUT_DEVICE='{t}' -> [{idx}] {m[0][1].get('name')}")
                else:
                    warn(f"AUDIO_INPUT_DEVICE='{t}' nu se potriveste cu niciun microfon.")
            try:
                with sd.InputStream(device=idx, channels=1, samplerate=16000,
                                    dtype="int16", blocksize=1280):
                    pass
                ok(f"Microfonul [{idx}] se deschide la 16kHz.")
            except Exception as e:
                err(f"Microfonul [{idx}] NU se deschide: {e}",
                    "Alta aplicatie il tine ocupat? Incearca AUDIO_INPUT_DEVICE in .env")
    except Exception as e:
        err(f"Nu pot lista dispozitivele audio: {e}",
            "sudo apt install -y portaudio19-dev libportaudio2")
except ImportError:
    err("sounddevice lipseste.", "pip install sounddevice")

# ── 4. WAKE WORD ───────────────────────────────────────────────────────
sectiune("4. WAKE WORD")

try:
    import openwakeword
    mdir = Path(openwakeword.__file__).parent / "resources" / "models"
    if mdir.exists():
        onnx = list(mdir.glob("*.onnx"))
        tfl = list(mdir.glob("*.tflite"))
        jarvis = [f for f in onnx if "jarvis" in f.name.lower()]
        if jarvis:
            ok(f"Model 'hey_jarvis' prezent ({len(onnx)} .onnx, {len(tfl)} .tflite).")
        elif onnx or tfl:
            warn(f"Modele gasite ({len(onnx)} .onnx) dar fara 'hey_jarvis'.",
                 DESCARCA_MODELE)
        else:
            err("Niciun model de wake word descarcat.", DESCARCA_MODELE)
    else:
        err(f"Directorul de modele lipseste: {mdir}", DESCARCA_MODELE)
except ImportError:
    err("openwakeword lipseste.", "pip install --no-deps openwakeword")

# ── 5. CONFIGURARE ─────────────────────────────────────────────────────
sectiune("5. CONFIGURARE (.env)")

env_file = BASE / ".env"
if env_file.exists():
    ok(f".env exista ({env_file.stat().st_size} octeti).")
else:
    err(".env LIPSESTE — fara el nu merge nimic.",
        "Copiaza-l de pe Windows sau vezi INSTALL_PI.md pasul 4")

try:
    import config
    ok("config.py se importa corect.")

    CHEI = [
        ("GEMINI_API_KEY",        True,  "vocea si tot ce tine de AI"),
        ("TELEGRAM_BOT_TOKEN",    False, "notificari pe telefon"),
        ("TELEGRAM_CHAT_ID",      False, "notificari pe telefon"),
        ("HA_URL",                False, "Home Assistant"),
        ("HA_TOKEN",              False, "Home Assistant"),
        ("SPOTIFY_CLIENT_ID",     False, "muzica"),
        ("SPOTIFY_CLIENT_SECRET", False, "muzica"),
    ]
    for cheie, critic, la_ce in CHEI:
        val = getattr(config, cheie, "")
        if val:
            ok(f"{cheie:<22} setat ({len(str(val))} car.) — {la_ce}")
        elif critic:
            err(f"{cheie:<22} LIPSESTE — {la_ce}")
        else:
            warn(f"{cheie:<22} nesetat — {la_ce} nu va merge")
except Exception as e:
    err(f"config.py nu se importa: {type(e).__name__}: {e}",
        "git pull   (config.py trebuie sa vina din git)")

# ── 6. MODULELE PROIECTULUI ────────────────────────────────────────────
sectiune("6. MODULELE PROIECTULUI")

MODULE = [
    "personalization", "ai_core", "core.event_bus", "core.audio_interface",
    "core.gemini_live", "core.llm_router", "core.tts_engine", "core.emotions",
    "core.user_profile", "core.day_runner", "agents.chronos_agent",
    "agents.music_agent", "agents.wled_agent", "agents.logger_agent",
    "tools.day_planner", "tools.timers", "tools.telegram_tools",
    "tools.home_assistant", "tools.spotify_api", "tools.wled_tools",
    "tools.bus_tools", "tools.context_tools", "tools.data_write_tools",
    "tools.music_memory", "tools.scene_tools", "web.web_dashboard",
]
stricate = 0
for m in MODULE:
    try:
        __import__(m)
    except Exception as e:
        stricate += 1
        err(f"{m}: {type(e).__name__}: {e}")
if stricate == 0:
    ok(f"Toate cele {len(MODULE)} module se importa.")

# ── 7. DATE ────────────────────────────────────────────────────────────
sectiune("7. DATE")

ddir = BASE / "chronos_data"
if ddir.exists():
    import json
    jsoane = sorted(ddir.rglob("*.json"))
    stricate_json = []
    for f in jsoane:
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            stricate_json.append((f.relative_to(BASE), str(e)[:60]))
    if stricate_json:
        for f, e in stricate_json:
            err(f"JSON corupt: {f} — {e}")
    else:
        ok(f"{len(jsoane)} fisiere JSON, toate valide.")
    total = sum(f.stat().st_size for f in ddir.rglob("*") if f.is_file())
    ok(f"chronos_data/ = {total/1024/1024:.1f} MB")
else:
    warn("chronos_data/ nu exista — se creeaza la prima rulare.")

# ── 8. RETEA ───────────────────────────────────────────────────────────
sectiune("8. RETEA")

try:
    import requests

    def verifica(nume, url, headers=None, timeout=6, critic=True, nota=""):
        """critic=False -> doar avertisment; Chronos porneste si fara."""
        raporteaza = err if critic else warn
        try:
            r = requests.get(url, headers=headers or {}, timeout=timeout)
            if r.status_code in (401, 403):
                raporteaza(f"{nume}: refuzat ({r.status_code}) — cheie gresita sau expirata.")
            else:
                # Orice raspuns HTTP inseamna ca serverul e acolo. Un 404 pe
                # radacina unui API e normal, nu o problema de retea.
                ok(f"{nume}: raspunde ({r.status_code}).")
        except Exception as e:
            raporteaza(f"{nume}: inaccesibil — {type(e).__name__}", nota)

    verifica("Internet", "https://generativelanguage.googleapis.com/")

    import config as C
    if getattr(C, "GEMINI_API_KEY", ""):
        verifica("Gemini API",
                 "https://generativelanguage.googleapis.com/v1beta/models"
                 f"?key={C.GEMINI_API_KEY}")
    if getattr(C, "TELEGRAM_BOT_TOKEN", ""):
        verifica("Telegram",
                 f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/getMe")
    # Dispozitivele din reteaua locala: daca lipsesc, Chronos porneste normal
    # si doar tool-urile lor raporteaza esec. Nu blocheaza nimic.
    RETEA_LOCALA = "Esti pe alta retea? Nu blocheaza pornirea."
    if getattr(C, "HA_URL", "") and getattr(C, "HA_TOKEN", ""):
        verifica("Home Assistant", C.HA_URL.rstrip("/") + "/api/",
                 {"Authorization": f"Bearer {C.HA_TOKEN}"},
                 critic=False, nota=RETEA_LOCALA)
    for nume, ip in (("WLED principal", getattr(C, "WLED_IP_MAIN", "")),
                     ("WLED podea", getattr(C, "WLED_IP_FLOOR", ""))):
        if ip:
            verifica(nume, f"http://{ip}/json/info", timeout=3,
                     critic=False, nota=RETEA_LOCALA)
except ImportError:
    err("requests lipseste — nu pot testa reteaua.")

# ── 9. SERVICIU ────────────────────────────────────────────────────────
sectiune("9. SERVICIU SYSTEMD")

svc = Path("/etc/systemd/system/chronos.service")
if not PE_PI and not svc.exists():
    ok("(Nu esti pe Pi — serviciul nu se aplica.)")
elif svc.exists():
    try:
        import re
        txt = svc.read_text()
        ok("chronos.service exista.")
        for camp in ("User", "WorkingDirectory", "ExecStart"):
            m = re.search(rf"^{camp}=(.+)$", txt, re.M)
            if m:
                print(f"           {camp}={m.group(1).strip()}")
        m = re.search(r"^ExecStart=(\S+)", txt, re.M)
        if m and not Path(m.group(1)).exists():
            err(f"ExecStart arata spre un python inexistent: {m.group(1)}",
                "Corecteaza calea in /etc/systemd/system/chronos.service")
        m = re.search(r"^User=(.+)$", txt, re.M)
        if m:
            user = m.group(1).strip()
            try:
                import grp
                if user in grp.getgrnam("audio").gr_mem:
                    ok(f"Userul '{user}' e in grupul 'audio'.")
                else:
                    err(f"Userul '{user}' NU e in grupul 'audio' — "
                        "serviciul n-o sa auda nimic.",
                        f"sudo usermod -aG audio {user}   (apoi reboot)")
            except Exception:
                pass
    except PermissionError:
        warn("Nu pot citi chronos.service (nevoie de sudo).")
else:
    warn("chronos.service nu exista inca.", "Vezi INSTALL_PI.md pasul 7")

# ── REZUMAT ────────────────────────────────────────────────────────────
print("\n" + "=" * 62)
print(f"  REZUMAT:  {len(OK)} ok  |  {len(WARN)} avertismente  |  {len(ERR)} erori")
print("=" * 62)

if ERR:
    print("\nDE REPARAT (in ordine):\n")
    for i, (msg, fix) in enumerate(ERR, 1):
        print(f"  {i}. {msg}")
        if fix:
            print(f"     $ {fix}")
    print("\n  -> Chronos NU va porni complet pana nu rezolvi astea.")
elif WARN:
    print("\nOPTIONALE (Chronos porneste si fara):\n")
    for msg, fix in WARN:
        print(f"  - {msg}")
        if fix:
            print(f"    $ {fix}")
    print("\n  -> Nucleul e in regula. Poti porni: python main_async.py")
else:
    print("\n  Totul e in regula. Porneste cu: python main_async.py")

sys.exit(1 if ERR else 0)
