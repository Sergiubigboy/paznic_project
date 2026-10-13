"""
ai_core.py — Stratul de transport pentru text (Claude sau Gemini)
==================================================================
Un singur loc prin care trec TOATE apelurile text/JSON ale lui Chronos.
Vocea Live NU trece pe aici (e în core/gemini_live.py, mereu pe Gemini).

Furnizorul se alege în personalization.py / .env cu LLM_PROVIDER:
  - "claude" → core/claude_cli.py, pe abonamentul Claude (Claude Code CLI)
  - "gemini" → Gemini REST, mai jos
Apelanții folosesc ask_llm_json / ask_llm_text / stream_llm_text și nu știu
cine răspunde. Dacă Claude pică și LLM_FALLBACK_TO_GEMINI e activ, apelul se
reîncearcă pe Gemini.

De ce transportul Gemini există în forma asta:
  - CONEXIUNI REFOLOSITE. Înainte, fiecare apel deschidea o conexiune TCP+TLS
    nouă. Handshake-ul costă 100-250ms pe fiecare request — pe un Pi, peste
    linia lui, ăsta era cel mai scump lucru din tot apelul. Acum o singură
    `Session` cu keep-alive ține conexiunea caldă între apeluri.
  - RETRY CU BACKOFF. 429/5xx sunt tranzitorii; înainte întorceau None și
    comanda lui Sergiu pica de tot.
  - CHEIA ÎN HEADER, nu în URL. În query string ajunge în orice log de proxy.
  - PLAFON DE OUTPUT + `thinkingBudget`. Apelurile structurate (planificator,
    DJ, WLED, emoții) nu au nevoie de „gândire" — pe gemini-2.5-flash aia e
    activă implicit și adaugă sute/mii de tokeni facturați per apel, degeaba.
  - STREAMING. `stream_gemini_text` livrează textul pe măsură ce vine, ca
    partea de TTS să poată începe să vorbească înainte ca modelul să termine.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Iterator, Optional

import requests
from requests.adapters import HTTPAdapter

from config import (
    GEMINI_API_KEY, GEMINI_BASE_URL,
    GEMINI_MODEL_DEFAULT, GEMINI_MODEL_LOGGER, GEMINI_MODEL_DJ,
    CLAUDE_MODEL_DEFAULT, CLAUDE_MODEL_LOGGER, CLAUDE_MODEL_DJ,
    LLM_PROVIDER, LLM_FALLBACK_TO_GEMINI,
)

logger = logging.getLogger(__name__)

# Plafoane de output. Fără ele, un model care o ia razna poate genera (și
# factura) mii de tokeni pentru o comandă de două cuvinte.
MAX_TOKENS_JSON = 768     # extracție structurată — schema e mică
MAX_TOKENS_TEXT = 1024    # conversație rostită/afișată

_TIMEOUT_JSON = (5, 30)   # (connect, read)
_TIMEOUT_TEXT = (5, 45)
_TIMEOUT_STREAM = (5, 90)

_session: Optional[requests.Session] = None
_session_lock = threading.Lock()


def _build_retry():
    """Retry pe erorile tranzitorii. Numele parametrului diferă între
    versiunile urllib3, de aceea încercarea dublă."""
    try:
        from urllib3.util.retry import Retry
    except ImportError:  # pragma: no cover - urllib3 vine cu requests
        return None

    kwargs = dict(
        total=2,
        connect=2,
        read=1,
        backoff_factor=0.4,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        raise_on_status=False,
    )
    try:
        return Retry(allowed_methods=frozenset({"GET", "POST"}), **kwargs)
    except TypeError:  # urllib3 < 1.26
        return Retry(method_whitelist=frozenset({"GET", "POST"}), **kwargs)


def get_session() -> requests.Session:
    """Sesiunea HTTP partajată (lazy, thread-safe)."""
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        s = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=4,
            pool_maxsize=8,
            max_retries=_build_retry(),
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update({
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
            # Conexiunea trebuie să rămână deschisă ca pooling-ul să conteze.
            "Connection": "keep-alive",
        })
        _session = s
        return _session


def close_session() -> None:
    """Închide sesiunea partajată și oprește apelurile Claude în curs
    (apelat la shutdown)."""
    global _session
    try:
        from core import claude_cli
        claude_cli.shutdown()
    except Exception:
        pass
    with _session_lock:
        if _session is not None:
            try:
                _session.close()
            except Exception:
                pass
            _session = None


def _url(model: str, streaming: bool = False) -> str:
    verb = "streamGenerateContent?alt=sse" if streaming else "generateContent"
    return f"{GEMINI_BASE_URL}/{model}:{verb}"


def _gen_config(
    temperature: float,
    max_tokens: int,
    schema: Optional[dict] = None,
    thinking: bool = True,
) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
    }
    if schema is not None:
        cfg["responseMimeType"] = "application/json"
        cfg["responseSchema"] = schema
    if not thinking:
        # Apelurile de extracție structurată nu câștigă nimic din „gândire",
        # dar o plătesc integral. Zero = dezactivat.
        cfg["thinkingConfig"] = {"thinkingBudget": 0}
    return cfg


def _post(url: str, payload: dict, timeout, stream: bool = False):
    try:
        resp = get_session().post(url, json=payload, timeout=timeout, stream=stream)
    except requests.RequestException as e:
        logger.error(f"❌ [AI Core] Rețea: {type(e).__name__}: {e}")
        return None

    if resp.status_code != 200:
        # Corpul erorii de la Google spune EXACT ce nu i-a plăcut — fără el,
        # un 400 e imposibil de diagnosticat.
        detail = ""
        try:
            detail = resp.text[:400]
        except Exception:
            pass
        logger.error(f"❌ [AI Core] HTTP {resp.status_code}: {detail}")
        resp.close()
        return None
    return resp


def _extract_text(data: dict) -> str:
    """Concatenează părțile text ale primului candidat. Tolerant la răspunsuri
    fără `parts` (blocate de filtre de siguranță sau tăiate de MAX_TOKENS)."""
    try:
        cand = (data.get("candidates") or [])[0]
    except IndexError:
        return ""
    parts = ((cand.get("content") or {}).get("parts")) or []
    return "".join(p.get("text", "") for p in parts)


# ─────────────────────────────────────────────────────────────
# GEMINI (REST)
# ─────────────────────────────────────────────────────────────

def _salveaza_json_taiat(raw: str) -> Optional[dict]:
    """
    Incearca sa recupereze un JSON retezat de `maxOutputTokens`.

    Cand raspunsul se opreste in mijlocul unui sir, tot ce s-a primit pana
    acolo e valid si de multe ori suficient — campurile importante sunt deja
    complete, se pierde doar coada (de obicei `reasoning`). Inainte aruncam
    tot si planul se pierdea complet.

    Taiem la ultima virgula din afara unui sir, inchidem structurile ramase
    deschise si reincercam. Daca nici asa nu iese, renuntam onest.
    """
    if not raw:
        return None

    in_sir = False
    escape = False
    stiva = []
    ultima_taiere = -1

    for i, c in enumerate(raw):
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_sir = not in_sir
            continue
        if in_sir:
            continue
        if c in "{[":
            stiva.append(c)
        elif c in "}]":
            if stiva:
                stiva.pop()
            # Un element complet s-a inchis: de aici in sus e sigur.
            ultima_taiere = i + 1
        elif c == ",":
            ultima_taiere = i

    if ultima_taiere <= 0:
        return None

    bucata = raw[:ultima_taiere].rstrip().rstrip(",")

    # Reconstituim inchiderile, in ordine inversa deschiderii.
    adancime = 0
    in_sir = False
    escape = False
    deschise = []
    for c in bucata:
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_sir = not in_sir
            continue
        if in_sir:
            continue
        if c in "{[":
            deschise.append(c)
        elif c in "}]" and deschise:
            deschise.pop()

    coada = "".join("}" if d == "{" else "]" for d in reversed(deschise))
    try:
        val = json.loads(bucata + coada)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        return None


def _gemini_json(
    system_prompt: str,
    schema: dict,
    temperature: float = 0.7,
    model: Optional[str] = None,
    max_tokens: int = MAX_TOKENS_JSON,
    thinking: bool = False,
) -> Optional[dict]:
    """Răspuns JSON validat pe schemă.

    `thinking=False` implicit: toți apelanții actuali (planificator, DJ, WLED,
    emoții, profil) fac extracție structurată, unde gândirea e cost curat.
    """
    model = model or GEMINI_MODEL_DEFAULT
    payload = {
        "contents": [{"parts": [{"text": system_prompt}]}],
        "generationConfig": _gen_config(temperature, max_tokens, schema, thinking),
    }

    resp = _post(_url(model), payload, _TIMEOUT_JSON)
    if resp is None:
        return None
    try:
        raw = _extract_text(resp.json())
    except ValueError as e:
        logger.error(f"❌ [AI Core] Răspuns non-JSON de la API ({model}): {e}")
        return None
    finally:
        resp.close()

    if not raw:
        logger.error(f"❌ [AI Core] Răspuns gol ({model}) — filtrat sau tăiat de maxOutputTokens.")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        salvat = _salveaza_json_taiat(raw)
        if salvat is not None:
            logger.warning(
                f"⚠️ [AI Core] JSON taiat de maxOutputTokens ({model}) — "
                f"recuperat {len(salvat)} campuri: {list(salvat)}"
            )
            return salvat
        logger.error(f"❌ [AI Core] JSON invalid ({model}): {e} | {raw[:200]}")
        return None


def _gemini_text(
    prompt: str,
    temperature: float = 0.8,
    model: Optional[str] = None,
    use_search: bool = False,
    max_tokens: int = MAX_TOKENS_TEXT,
    thinking: bool = False,
) -> Optional[str]:
    """Răspuns text liber, opțional cu grounding pe Google Search.

    `thinking=False` implicit: pe conversație, „gândirea" nu îmbunătățește
    răspunsul (personalitatea și contextul sunt deja în prompt), dar întârzie
    PRIMUL token cu câteva secunde bune — exact ce se simte cel mai tare.
    """
    model = model or GEMINI_MODEL_DEFAULT
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": _gen_config(temperature, max_tokens, thinking=thinking),
    }
    if use_search:
        payload["tools"] = [{"google_search": {}}]

    resp = _post(_url(model), payload, _TIMEOUT_TEXT)
    if resp is None:
        return None
    try:
        text = _extract_text(resp.json()).strip()
    except ValueError as e:
        logger.error(f"❌ [AI Core] Răspuns non-JSON ({model}): {e}")
        return None
    finally:
        resp.close()
    return text or None


def _gemini_stream(
    prompt: str,
    temperature: float = 0.8,
    model: Optional[str] = None,
    use_search: bool = False,
    max_tokens: int = MAX_TOKENS_TEXT,
    thinking: bool = False,
) -> Iterator[str]:
    """Generator peste bucățile de text, pe măsură ce modelul le produce.

    Ăsta e ce face posibilă vorbirea devreme: apelantul acumulează bucățile
    într-un buffer de propoziții și trimite la sinteză prima frază completă
    fără să aștepte finalul generării (vezi core/tts_engine.speak_stream).

    La eroare nu aruncă — pur și simplu nu mai produce nimic; apelantul
    tratează „zero bucăți" ca eșec și poate cădea pe calea non-streaming.
    """
    model = model or GEMINI_MODEL_DEFAULT
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": _gen_config(temperature, max_tokens, thinking=thinking),
    }
    if use_search:
        payload["tools"] = [{"google_search": {}}]

    resp = _post(_url(model, streaming=True), payload, _TIMEOUT_STREAM, stream=True)
    if resp is None:
        return

    # SSE vine fără charset → requests ar decoda ca ISO-8859-1 și ar strica
    # diacriticele („bunÄ" în loc de „bună").
    resp.encoding = "utf-8"
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                chunk = json.loads(body)
            except json.JSONDecodeError:
                continue
            piece = _extract_text(chunk)
            if piece:
                yield piece
    except requests.RequestException as e:
        logger.error(f"❌ [AI Core] Stream întrerupt ({model}): {type(e).__name__}: {e}")
    finally:
        resp.close()


# ─────────────────────────────────────────────────────────────
# CLAUDE (abonament, prin CLI)
# ─────────────────────────────────────────────────────────────

_TIMEOUT_CLAUDE_JSON = 60.0
_TIMEOUT_CLAUDE_TEXT = 60.0
_TIMEOUT_CLAUDE_STREAM = 90.0

_SYSTEM_CLAUDE_JSON = (
    "Ești un modul de extracție structurată. Răspunzi EXCLUSIV cu un singur "
    "obiect JSON valid care respectă schema dată — fără text înainte sau după, "
    "fără blocuri ``` și fără comentarii."
)
_SYSTEM_CLAUDE_TEXT = (
    "Urmezi întocmai personajul și instrucțiunile din mesaj. Răspunsul e "
    "afișat pe ecran și citit cu voce tare: text simplu, fără markdown, fără "
    "linkuri și fără liste de surse."
)

# Rolurile, ca un apel de fallback să ajungă pe modelul echivalent.
_ROLURI = (
    (CLAUDE_MODEL_LOGGER, GEMINI_MODEL_LOGGER),
    (CLAUDE_MODEL_DJ, GEMINI_MODEL_DJ),
    (CLAUDE_MODEL_DEFAULT, GEMINI_MODEL_DEFAULT),
)

_claude_lipsa_raportat = False


def _model_gemini(model: Optional[str]) -> str:
    if not model or model.startswith("gemini"):
        return model or GEMINI_MODEL_DEFAULT
    for claude, gemini in _ROLURI:
        if model == claude:
            return gemini
    return GEMINI_MODEL_DEFAULT


def _model_claude(model: Optional[str]) -> str:
    if not model:
        return CLAUDE_MODEL_DEFAULT
    if model.startswith("gemini"):
        for claude, gemini in _ROLURI:
            if model == gemini:
                return claude
        return CLAUDE_MODEL_DEFAULT
    return model


def _foloseste_claude() -> bool:
    """Claude e ales ȘI CLI-ul există. Altfel cădem pe Gemini, o dată anunțat."""
    global _claude_lipsa_raportat
    if LLM_PROVIDER != "claude":
        return False
    from core import claude_cli
    if claude_cli.find_cli():
        return True
    if not _claude_lipsa_raportat:
        _claude_lipsa_raportat = True
        logger.warning("⚠️ [AI Core] LLM_PROVIDER=claude, dar CLI-ul lipsește → folosesc Gemini.")
    return False


def _poate_fallback() -> bool:
    return bool(LLM_FALLBACK_TO_GEMINI and GEMINI_API_KEY)


def _schema_standard(schema: Any) -> Any:
    """Schema Gemini ("OBJECT", "STRING") → JSON Schema obișnuit ("object")."""
    if isinstance(schema, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str)
                    else _schema_standard(v))
                for k, v in schema.items()}
    if isinstance(schema, list):
        return [_schema_standard(x) for x in schema]
    return schema


def _parse_json_claude(raw: str, model: str) -> Optional[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0:
        logger.error(f"❌ [AI Core] Claude n-a dat JSON ({model}): {raw[:200]}")
        return None
    try:
        val = json.loads(text[start:end + 1])
        if isinstance(val, dict):
            return val
    except json.JSONDecodeError:
        pass
    salvat = _salveaza_json_taiat(text[start:])
    if salvat is not None:
        logger.warning(f"⚠️ [AI Core] JSON Claude incomplet ({model}) — "
                       f"recuperat {len(salvat)} campuri: {list(salvat)}")
        return salvat
    logger.error(f"❌ [AI Core] JSON invalid de la Claude ({model}): {raw[:200]}")
    return None


def _claude_json(system_prompt: str, schema: dict, model: Optional[str]) -> Optional[dict]:
    from core import claude_cli
    model = _model_claude(model)
    prompt = (
        f"{system_prompt}\n\n---\n"
        "Răspunde DOAR cu un obiect JSON care respectă schema de mai jos "
        "(JSON Schema). Câmpurile din `required` sunt obligatorii.\n"
        f"{json.dumps(_schema_standard(schema), ensure_ascii=False)}"
    )
    raw = claude_cli.complete(prompt, _SYSTEM_CLAUDE_JSON, model,
                              timeout=_TIMEOUT_CLAUDE_JSON)
    return _parse_json_claude(raw, model) if raw else None


# ─────────────────────────────────────────────────────────────
# API PUBLIC
# ─────────────────────────────────────────────────────────────

def ask_llm_json(
    system_prompt: str,
    schema: dict,
    temperature: float = 0.7,
    model: Optional[str] = None,
    max_tokens: int = MAX_TOKENS_JSON,
    thinking: bool = False,
) -> Optional[dict]:
    """Răspuns JSON pe schemă (schema în format Gemini: "OBJECT", "STRING"...).

    `temperature`, `max_tokens` și `thinking` contează doar pe Gemini; CLI-ul
    Claude nu le expune (gândirea e oricum oprită acolo).
    """
    if _foloseste_claude():
        rez = _claude_json(system_prompt, schema, model)
        if rez is not None or not _poate_fallback():
            return rez
        logger.warning("⚠️ [AI Core] Claude a picat → reîncerc pe Gemini.")
    return _gemini_json(system_prompt, schema, temperature, _model_gemini(model),
                        max_tokens, thinking)


def ask_llm_text(
    prompt: str,
    temperature: float = 0.8,
    model: Optional[str] = None,
    use_search: bool = False,
    max_tokens: int = MAX_TOKENS_TEXT,
    thinking: bool = False,
) -> Optional[str]:
    """Răspuns text liber, opțional cu căutare pe net (Google pe Gemini,
    WebSearch pe Claude)."""
    if _foloseste_claude():
        from core import claude_cli
        rez = claude_cli.complete(prompt, _SYSTEM_CLAUDE_TEXT, _model_claude(model),
                                  search=use_search, timeout=_TIMEOUT_CLAUDE_TEXT)
        if rez or not _poate_fallback():
            return rez
        logger.warning("⚠️ [AI Core] Claude a picat → reîncerc pe Gemini.")
    return _gemini_text(prompt, temperature, _model_gemini(model), use_search,
                        max_tokens, thinking)


def stream_llm_text(
    prompt: str,
    temperature: float = 0.8,
    model: Optional[str] = None,
    use_search: bool = False,
    max_tokens: int = MAX_TOKENS_TEXT,
    thinking: bool = False,
) -> Iterator[str]:
    """Generator peste bucățile de text (vezi `_gemini_stream`).

    Fallback-ul pe Gemini se face doar dacă Claude n-a produs NICIO bucată —
    altfel utilizatorul ar auzi două răspunsuri lipite.
    """
    if _foloseste_claude():
        from core import claude_cli
        emis = False
        for piece in claude_cli.stream(prompt, _SYSTEM_CLAUDE_TEXT, _model_claude(model),
                                       search=use_search, timeout=_TIMEOUT_CLAUDE_STREAM):
            emis = True
            yield piece
        if emis or not _poate_fallback():
            return
        logger.warning("⚠️ [AI Core] Claude n-a răspuns → reîncerc pe Gemini.")
    yield from _gemini_stream(prompt, temperature, _model_gemini(model), use_search,
                              max_tokens, thinking)


# Numele vechi, păstrate pentru orice apelant extern.
ask_gemini_json = ask_llm_json
ask_gemini_text = ask_llm_text
stream_gemini_text = stream_llm_text
