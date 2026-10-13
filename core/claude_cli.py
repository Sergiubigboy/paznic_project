"""
core/claude_cli.py — Transport text prin Claude Code CLI (abonamentul Claude)
==============================================================================
Alternativa la Gemini REST pentru tot ce e text: planificator, DJ, WLED,
jurnal, emoții, profil, conversația din terminal. Vocea Live rămâne pe Gemini.

De ce CLI-ul și nu API-ul Anthropic:
  - CLI-ul (`claude -p`) se autentifică cu ABONAMENTUL (Pro/Max), nu cu o
    cheie API facturată la token. Apelurile intră în limitele abonamentului.
  - Pe Pi, fără browser: `claude setup-token` dă un token de lungă durată,
    pus în .env ca CLAUDE_CODE_OAUTH_TOKEN.

Cum e izolat apelul, ca să fie un simplu „model de text":
  - `--system-prompt` scurt → înlocuiește promptul uriaș de agent de cod
  - `--tools ""` (sau doar WebSearch când e nevoie de net)
  - `--setting-sources ""`, `--strict-mcp-config`, cwd într-un director gol →
    nu se încarcă setări, pluginuri, MCP sau CLAUDE.md din vreun proiect
  - `MAX_THINKING_TOKENS=0` → fără gândire, primul token vine mai repede
  - ANTHROPIC_API_KEY scos din mediu → nu poate factura accidental pe API

Test manual (fără restul lui Chronos):
    python -m core.claude_cli "Spune salut în 3 cuvinte"
    python -m core.claude_cli --search "Ce vreme e azi în Târgu Mureș?"
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Iterator, List, Optional, Set

logger = logging.getLogger(__name__)

try:
    from config import CLAUDE_CLI_PATH, CLAUDE_MAX_PARALLEL
except ImportError:
    CLAUDE_CLI_PATH = ""
    CLAUDE_MAX_PARALLEL = 2

# Fiecare apel e un proces separat (~100-200MB cât rulează). Pe Pi, mai multe
# analize de fundal pornite simultan ar mânca RAM-ul — le punem la coadă.
_slots = threading.BoundedSemaphore(max(1, int(CLAUDE_MAX_PARALLEL)))

_procs: Set[subprocess.Popen] = set()
_procs_lock = threading.Lock()

_cli_path: Optional[str] = None
_cli_lock = threading.Lock()

_WORKDIR = Path(tempfile.gettempdir()) / "chronos_claude"


# ─────────────────────────────────────────────────────────────
# GĂSIREA CLI-ULUI
# ─────────────────────────────────────────────────────────────

def _versiune(p: Path) -> tuple:
    try:
        return tuple(int(x) for x in p.parent.name.split("."))
    except ValueError:
        return ()


def find_cli() -> Optional[str]:
    """Calea către executabilul `claude`, sau None.

    Serviciul systemd nu are ~/.local/bin în PATH, de aceea căutăm explicit
    în locurile unde îl pune installer-ul nativ și aplicația desktop.
    """
    global _cli_path
    if _cli_path:
        return _cli_path
    with _cli_lock:
        if _cli_path:
            return _cli_path

        candidati: List[Path] = []
        if CLAUDE_CLI_PATH:
            candidati.append(Path(CLAUDE_CLI_PATH).expanduser())
        gasit = shutil.which("claude")
        if gasit:
            candidati.append(Path(gasit))
        home = Path.home()
        exe = "claude.exe" if os.name == "nt" else "claude"
        candidati += [home / ".local" / "bin" / exe,
                      home / ".claude" / "local" / exe]
        if os.name == "nt":
            appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
            # CLI-ul adus de aplicația desktop Claude, cea mai nouă versiune.
            desktop = sorted((appdata / "Claude" / "claude-code").glob("*/claude.exe"),
                             key=_versiune, reverse=True)
            candidati += desktop

        for c in candidati:
            if c.is_file():
                _cli_path = str(c)
                logger.info(f"🟣 [Claude CLI] Folosesc {_cli_path}")
                return _cli_path

        logger.error("❌ [Claude CLI] Executabilul `claude` nu a fost găsit. "
                     "Instalează-l (vezi INSTALL_PI.md) sau setează CLAUDE_CLI_PATH.")
        return None


def _env() -> dict:
    env = dict(os.environ)
    # Cu o cheie API în mediu, CLI-ul ar factura pe ea în loc de abonament.
    env.pop("ANTHROPIC_API_KEY", None)
    # Dacă Chronos e pornit dintr-o sesiune Claude Code, copilul nu trebuie
    # să creadă că e o sesiune imbricată.
    env.pop("CLAUDECODE", None)
    env["MAX_THINKING_TOKENS"] = "0"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def _args(cli: str, model: str, system: str, search: bool) -> List[str]:
    return [
        cli, "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json", "--verbose",
        "--include-partial-messages",
        "--model", model,
        "--system-prompt", system,
        "--tools", "WebSearch" if search else "",
        *(["--allowedTools", "WebSearch"] if search else []),
        "--no-session-persistence",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
    ]


# ─────────────────────────────────────────────────────────────
# RULARE
# ─────────────────────────────────────────────────────────────

def _run(prompt: str, system: str, model: str, search: bool,
         timeout: float) -> Iterator[str]:
    """Pornește un apel și produce bucățile de text pe măsură ce vin.

    Nu aruncă: la orice eroare loghează și se oprește, la fel ca transportul
    Gemini — apelanții tratează „zero bucăți" ca eșec.
    """
    cli = find_cli()
    if not cli:
        return

    _WORKDIR.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    with _slots:
        try:
            proc = subprocess.Popen(
                _args(cli, model, system, search),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=str(_WORKDIR), env=_env(),
                text=True, encoding="utf-8", errors="replace",
                creationflags=flags,
            )
        except OSError as e:
            logger.error(f"❌ [Claude CLI] Nu pot porni procesul: {e}")
            return

        with _procs_lock:
            _procs.add(proc)
        # Stderr citit în paralel: un buffer plin ar bloca procesul.
        err_buf: List[str] = []
        err_thread = threading.Thread(
            target=lambda: err_buf.append(proc.stderr.read()), daemon=True)
        err_thread.start()
        watchdog = threading.Timer(timeout, _kill, args=(proc, "timeout"))
        watchdog.daemon = True
        watchdog.start()

        emis = False
        separa = False
        terminat = False
        try:
            msg = {"type": "user", "message": {"role": "user", "content": prompt}}
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.close()

            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tip = ev.get("type")

                if tip == "stream_event" and ev.get("parent_tool_use_id") is None:
                    e = ev.get("event") or {}
                    if e.get("type") == "message_start" and emis:
                        # Un mesaj nou după o căutare: nu lipim textele.
                        separa = True
                    elif e.get("type") == "content_block_delta":
                        d = e.get("delta") or {}
                        if d.get("type") == "text_delta" and d.get("text"):
                            if separa:
                                yield "\n"
                                separa = False
                            emis = True
                            yield d["text"]

                elif tip == "result":
                    if ev.get("is_error"):
                        logger.error(f"❌ [Claude CLI] {ev.get('subtype')}: "
                                     f"{str(ev.get('result'))[:300]}")
                    elif not emis and ev.get("result"):
                        # Fără evenimente parțiale (versiune veche de CLI).
                        emis = True
                        yield str(ev["result"])
                    terminat = True
                    break
        except (OSError, ValueError) as e:
            logger.error(f"❌ [Claude CLI] Flux întrerupt: {type(e).__name__}: {e}")
        finally:
            watchdog.cancel()
            if not terminat and proc.poll() is None:
                # Apelantul a renunțat la mijloc (sau eroare): nu așteptăm
                # să termine de generat un răspuns pe care nu-l mai citește.
                _kill(proc, "abandonat")
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _kill(proc, "nu s-a închis")
            with _procs_lock:
                _procs.discard(proc)
            err_thread.join(timeout=1)
            if proc.returncode not in (0, None) and not emis:
                detaliu = ("".join(err_buf)).strip()[:400]
                logger.error(f"❌ [Claude CLI] Cod ieșire {proc.returncode}: {detaliu}")


def _kill(proc: subprocess.Popen, motiv: str) -> None:
    if proc.poll() is None:
        logger.warning(f"⚠️ [Claude CLI] Opresc procesul ({motiv}).")
        try:
            proc.kill()
        except OSError:
            pass


def shutdown() -> None:
    """Oprește apelurile în curs (apelat la oprirea lui Chronos)."""
    with _procs_lock:
        vii = list(_procs)
    for p in vii:
        _kill(p, "shutdown")


# ─────────────────────────────────────────────────────────────
# CURĂȚAREA RĂSPUNSURILOR CU CĂUTARE
# ─────────────────────────────────────────────────────────────

_SURSE = re.compile(r"^\s*(\*\*|#+\s*)?(sources|surse|sursă|sursa)\b.*$", re.I | re.M)
_LINK_MD = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_URL = re.compile(r"\(?https?://\S+\)?")


def curata_text_cautare(text: str) -> str:
    """Scoate lista de surse și linkurile pe care le adaugă căutarea web.

    Răspunsul e afișat pe ecran și citit de TTS: un URL rostit e zgomot.
    """
    m = _SURSE.search(text)
    if m:
        text = text[:m.start()]
    text = _LINK_MD.sub(r"\1", text)
    text = _URL.sub("", text)
    text = text.replace("**", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────
# API PUBLIC
# ─────────────────────────────────────────────────────────────

def complete(prompt: str, system: str, model: str, search: bool = False,
             timeout: float = 60.0) -> Optional[str]:
    """Răspunsul complet, dintr-o bucată. None la eșec."""
    text = "".join(_run(prompt, system, model, search, timeout)).strip()
    if search:
        text = curata_text_cautare(text)
    return text or None


def stream(prompt: str, system: str, model: str, search: bool = False,
           timeout: float = 90.0) -> Iterator[str]:
    """Bucățile de text pe măsură ce vin.

    Cu căutare web, răspunsul vine cu surse și linkuri la final; ca să nu
    ajungă în TTS, îl strângem întreg, îl curățăm și îl dăm o dată. Căutarea
    oricum domină timpul de răspuns, așa că se pierde puțin.
    """
    if search:
        text = complete(prompt, system, model, search=True, timeout=timeout)
        if text:
            yield text
        return
    yield from _run(prompt, system, model, False, timeout)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    argv = sys.argv[1:]
    cauta = "--search" in argv
    argv = [a for a in argv if a != "--search"]
    intrebare = " ".join(argv) or "Spune salut în 3 cuvinte."
    for bucata in stream(intrebare, "Răspunzi scurt, în română, text simplu.",
                         "haiku", search=cauta):
        print(bucata, end="", flush=True)
    print()
