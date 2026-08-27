"""
ai_core.py — Stratul de transport către Gemini (REST)
======================================================
Un singur loc prin care trec TOATE apelurile text/JSON către Gemini.

De ce există în forma asta:
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

from config import GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL_DEFAULT

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
    """Închide sesiunea partajată (apelat la shutdown)."""
    global _session
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
# API PUBLIC
# ─────────────────────────────────────────────────────────────

def ask_gemini_json(
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
        logger.error(f"❌ [AI Core] JSON invalid ({model}): {e} | {raw[:200]}")
        return None


def ask_gemini_text(
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


def stream_gemini_text(
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
