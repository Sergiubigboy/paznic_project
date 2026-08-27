"""
agents/chronos_agent.py — Agentul AI Principal Orchestrator (Chronos)
========================================================================
Agentul cu care vorbește utilizatorul pe calea TEXT (terminal + dashboard).
Deține registrul de agenți specializați, decide care trebuie invocați și
adună rezultatele.

Note de proiectare
------------------
REZULTATUL SE ÎNTOARCE, NU SE CITEȘTE DINTR-UN CÂMP COMUN.
    `process_text_command()` întorcea `True`, iar apelanții citeau apoi
    `agent.last_result`. Dashboard-ul web rulează pe fire Flask, iar terminalul
    lansează fiecare comandă ca task separat — două comenzi suprapuse își
    suprascriau reciproc `last_result`, deci răspunsul uneia ajungea la
    cealaltă. Acum rezultatul e întors direct; `last_result` rămâne doar ca
    ultimă valoare cunoscută, pentru compatibilitate.

PLANIFICAREA E SEPARATĂ DE EXECUȚIE.
    `plan()` / `run_agents()` / `build_chat_prompt()` sunt pași distincți, ca
    stratul de voce să poată face streaming pe răspunsul de conversație
    (vezi core/llm_router.py) fără să dubleze logica de aici.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from agents.music_agent import MusicAgent
from agents.wled_agent import WLEDAgent
from agents.logger_agent import LoggerAgent
from ai_core import ask_gemini_json, ask_gemini_text, stream_gemini_text
# Calea text nu are tool-uri, deci foloseste promptul FARA regulile de
# tool-uri vocale — vezi personalization.SYSTEM_PROMPT_TEXT.
from config import SYSTEM_PROMPT_TEXT

logger = logging.getLogger(__name__)

# Istoricul e mărginit: pe un sistem care rulează cu lunile, o listă care
# crește la fiecare replică e o scurgere de memorie lentă, dar sigură.
# În prompt intră oricum doar ultimele câteva schimburi.
HISTORY_MAXLEN = 40
HISTORY_IN_PROMPT = 6

# Plafoane de context. Fiecare caracter de aici se plătește la FIECARE apel.
MEMORY_BUDGET_CHARS = 900


class ChronosAgent:
    """Orchestratorul principal: primește comenzi text și coordonează agenții."""

    __slots__ = (
        "music_agent", "wled_agent", "logger_agent",
        "conversation_history", "last_result",
    )

    # Tool call → agent, pentru rutarea DIRECTĂ (fără pasul de planificare).
    # Când apelantul știe deja ce agent trebuie (ex: Gemini Live a ales
    # `control_lights`), un al doilea apel LLM care să redescopere „e despre
    # lumini" e timp și bani aruncați — plus risc de rutare greșită.
    TOOL_AGENT_MAP = {
        "control_lights": "wled_agent",
        "control_music": "music_agent",
        "save_journal": "logger_agent",
    }

    def __init__(self):
        self.music_agent = MusicAgent()
        self.wled_agent = WLEDAgent()
        self.logger_agent = LoggerAgent()

        self.conversation_history: deque = deque(maxlen=HISTORY_MAXLEN)
        self.last_result: Dict[str, Any] = {}

    # ─────────────────────────────────────────────────────────────────────
    # PLANIFICARE
    # ─────────────────────────────────────────────────────────────────────

    # Promptul de planificare pleacă la FIECARE comandă text, deci e scris cât
    # se poate de scurt. Descrierile lungi ale agenților au dispărut: numele
    # din enum-ul schemei spun deja ce fac, iar exemplele contează mai mult
    # decât proza. Modelul primește instrucțiuni, nu un manual.
    _PLAN_RULES = (
        "Rutezi comanda lui Sergiu către agenți.\n"
        "music_agent=muzică/Spotify. wled_agent=lumini LED. "
        "logger_agent=SCRIE ceva în jurnal („am fost la sală”, „notează că…”). "
        "general_chat=orice altceva.\n"
        # „Ce am de făcut azi?" ajungea la logger_agent, adică se SALVA ca notă
        # în loc să primească răspuns. A întreba despre datele tale nu e
        # același lucru cu a scrie în ele.
        "ÎNTREBĂRILE despre datele lui („ce am azi”, „cât am cheltuit”, „cum "
        "stau cu obiectivele”) → general_chat + data_categories, NU logger_agent.\n"
        # Fără rândul ăsta, „pune ceva chill" ajungea și la lumini: modelul
        # citea orice cuvânt de dispoziție drept „atmosferă". Vibe-ul e tot
        # o cerere de muzică — luminile intră doar dacă le cere.
        "DOAR music_agent la orice cerere de muzică, inclusiv pe vibe "
        "(„ceva chill”, „de energie”, „ceva linistit”).\n"
        "AMBII (music+wled) doar dacă cere explicit și partea vizuală "
        "(„atmosferă de munte”, „schimbă muzica și luminile”).\n\n"
        "data_categories = datele lui personale STRICT necesare, de obicei una:\n"
        "bani/investit→finante; ce am azi/programat→azi; obiective→targeturi;\n"
        "proiect→proiecte; greutate→sport; tranzacții→tranzactii (doar explicit);\n"
        "vândut→vanzari (doar explicit). Gol la conversație obișnuită sau "
        "subiecte despre lume.\n\n"
        "needs_web = true DOAR dacă răspunsul cere informații actuale din lume "
        "(vreme, știri, prețuri, evenimente, program magazine). false la vorbă "
        "obișnuită, la datele lui personale și la comenzi de lumini/muzică.\n\n"
        'COMANDĂ: "{text}"'
    )

    _PLAN_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            "agents": {
                "type": "ARRAY",
                "items": {
                    "type": "STRING",
                    "enum": ["music_agent", "wled_agent", "logger_agent", "general_chat"],
                },
            },
            # Extindem schema apelului EXISTENT în loc să facem unul nou:
            # planificatorul decide și ce date personale sunt necesare, deci
            # calea text ajunge la aceleași informații ca vocea, fără niciun
            # request Gemini în plus.
            "data_categories": {
                "type": "ARRAY",
                "items": {
                    "type": "STRING",
                    "enum": ["azi", "finante", "tranzactii", "vanzari", "targeturi",
                             "remindere", "proiecte", "sport", "obiceiuri"],
                },
            },
            # Decis în ACELAȘI apel, deci gratis. Înainte, căutarea web era
            # atașată la fiecare conversație, inclusiv la „ce mai zici?":
            # ~0.75s în plus și tokeni de grounding pentru nimic.
            "needs_web": {"type": "BOOLEAN"},
            "reasoning": {"type": "STRING"},
        },
        "required": ["agents", "reasoning"],
    }

    def plan(self, user_text: str) -> dict:
        """Decide ce agenți se invocă și ce date personale trebuie citite."""
        logger.info(f"🧠 [Chronos Agent] Planific: '{user_text}'")
        result = ask_gemini_json(
            self._PLAN_RULES.format(text=user_text),
            schema=self._PLAN_SCHEMA,
            temperature=0.1,
        )
        if not isinstance(result, dict):
            # Planificarea a picat (rețea/filtru) → nu pierdem comanda,
            # o tratăm ca discuție normală.
            logger.warning("⚠️ [Chronos Agent] Planificare eșuată → general_chat.")
            # Fără plan nu știm dacă are nevoie de web → îl lăsăm pornit, ca să
            # nu pierdem capacitatea de a răspunde la ceva actual.
            return {"agents": ["general_chat"], "data_categories": [],
                    "needs_web": True, "reasoning": ""}
        result.setdefault("agents", ["general_chat"])
        result.setdefault("data_categories", [])
        result.setdefault("needs_web", False)
        result.setdefault("reasoning", "")
        return result

    # Păstrat sub numele vechi pentru orice apelant extern.
    plan_and_route = plan

    # ─────────────────────────────────────────────────────────────────────
    # PUNCTE DE INTRARE
    # ─────────────────────────────────────────────────────────────────────

    def route_direct(self, agent_name: str, text: str) -> dict:
        """Execută DIRECT un agent, sărind peste planificarea LLM.
        Folosit de calea vocală, unde tool call-ul a stabilit deja intenția."""
        if not text or not text.strip():
            return {}
        self._remember(f"User: {text}")
        logger.info(f"⚡ [Chronos Agent] Rutare DIRECTĂ → {agent_name}")
        return self.run_agents([agent_name], text, reasoning="Rutare directă din tool call.")

    def process_text_command(self, text: str, sock=None) -> dict:
        """Intrare pentru TEXT BRUT (terminal, dashboard web).

        Returns:
            Dict-ul de rezultat: {"intents", "reply", "actions", "reasoning"}.
            Folosește valoarea întoarsă, nu `self.last_result` — al doilea e
            partajat între apeluri concurente.
        """
        if not text or not text.strip():
            return {}

        self._remember(f"User: {text}")

        # ── Scurtcircuit autobuze: ZERO apeluri LLM ──
        # Orarul e determinist (stație + ceas + GTFS local), deci n-are rost
        # nici planificarea, nici generarea răspunsului.
        bus_result = self._try_bus(text)
        if bus_result is not None:
            return bus_result

        # ── Scurtcircuit vreme: ZERO apeluri LLM, ZERO căutare web ──
        # Home Assistant are și starea de acum, și prognoza, în rețeaua
        # locală. Planificatorul ar marca asta `needs_web=true` și ar plăti
        # un grounding pentru ceva ce avem în casă.
        weather_result = self._try_weather(text)
        if weather_result is not None:
            return weather_result

        plan = self.plan(text)
        return self.run_agents(
            plan["agents"], text, plan["reasoning"], plan["data_categories"],
            needs_web=plan["needs_web"],
        )

    def prepare(self, text: str) -> dict:
        """Faza 1: scurtcircuit determinist SAU planificare.

        Separată de execuție ca apelantul asincron (LLMRouter) să poată decide
        dacă merge pe calea cu streaming — nu are cum să afle asta fără să
        știe întâi ce agenți intră în joc.

        Returns:
            {"done": True,  "result": {...}}  — răspuns gata (ex: orar autobuz)
            {"done": False, "plan":   {...}}  — planul de execuție
        """
        if not text or not text.strip():
            return {"done": True, "result": {}}
        self._remember(f"User: {text}")
        bus_result = self._try_bus(text)
        if bus_result is not None:
            return {"done": True, "result": bus_result}
        weather_result = self._try_weather(text)
        if weather_result is not None:
            return {"done": True, "result": weather_result}
        return {"done": False, "plan": self.plan(text)}

    def _try_bus(self, text: str) -> Optional[dict]:
        bus_args = self._match_bus(text)
        if not bus_args:
            return None
        from tools import bus_tools
        reply = bus_tools.answer(**bus_args)
        logger.info(f"🚌 [Chronos Agent] Orar autobuz (fără LLM): {bus_args}")
        result = {
            "intents": ["bus"],
            "reply": reply,
            "actions": [{"text": "🚌 Orar autobuz.", "status": "ok"}],
            "reasoning": "Întrebare despre autobuz — răspuns determinist din GTFS.",
        }
        self._remember(f"Chronos: {reply}")
        self.last_result = result
        return result

    def _try_weather(self, text: str) -> Optional[dict]:
        """Întrebare despre vreme → răspuns direct din Home Assistant.

        Tolerant la erori ca și autobuzul: dacă HA e picat sau potrivirea
        crapă, întoarce None și comanda își vede de drumul normal (unde
        există oricum căutarea web ca plasă de siguranță)."""
        try:
            from tools import home_assistant as HA
            args = HA.match_query(text)
            if not args:
                return None
            reply = HA.answer(**args)
        except Exception as e:
            logger.debug(f"[Chronos Agent] Potrivire vreme indisponibilă: {e}")
            return None

        logger.info(f"🌦️ [Chronos Agent] Vreme din HA (fără LLM): {args}")
        result = {
            "intents": ["weather"],
            "reply": reply,
            "actions": [{"text": "🌦️ Vremea din Home Assistant.", "status": "ok"}],
            "reasoning": "Întrebare despre vreme — răspuns local din Home Assistant.",
        }
        self._remember(f"Chronos: {reply}")
        self.last_result = result
        return result

    @staticmethod
    def _match_bus(text: str) -> Optional[dict]:
        """Întrebare despre autobuz → argumente, altfel None (calea normală).
        Tolerant la erori: dacă orarul nu e generat încă, comanda merge mai
        departe prin planificator ca înainte."""
        try:
            from tools import bus_tools
            return bus_tools.match_query(text)
        except Exception as e:
            logger.debug(f"[Chronos Agent] Potrivire autobuz indisponibilă: {e}")
            return None

    # ─────────────────────────────────────────────────────────────────────
    # EXECUȚIA AGENȚILOR
    # ─────────────────────────────────────────────────────────────────────

    def run_agents(
        self,
        agents_to_call: List[str],
        text: str,
        reasoning: str = "",
        data_cats: Optional[List[str]] = None,
        skip_chat: bool = False,
        needs_web: bool = True,
    ) -> dict:
        """Rulează agenții ceruți și întoarce rezultatul.

        `skip_chat=True` execută tot în afară de general_chat — folosit de calea
        cu streaming, unde răspunsul de conversație e generat separat, token cu
        token, ca să poată începe redarea vocală mai devreme.
        """
        actions_list: List[dict] = []
        reply_text: Optional[str] = None

        for ag_name in agents_to_call:
            try:
                if ag_name == "wled_agent":
                    res = self.wled_agent.process_request(text)
                    actions_list.append({
                        "text": f"💡 WLED: {res.get('msg')}",
                        "status": res.get("status", "ok"),
                    })

                elif ag_name == "music_agent":
                    res = self.music_agent.process_request(text)
                    actions_list.append({
                        "text": f"🎵 Spotify: {res.get('msg', 'Muzică procesată.')}",
                        "status": res.get("status", "ok"),
                    })
                    if res.get("reason"):
                        reply_text = f"DJ: {res['reason']}"

                elif ag_name == "logger_agent":
                    self.logger_agent.save_entry(text)
                    actions_list.append({"text": "📘 Salvat în jurnal.", "status": "ok"})

                elif ag_name == "general_chat":
                    if skip_chat:
                        continue
                    reply_text = self.general_chat_reply(text, data_cats, needs_web)
                    actions_list.append({"text": "🧠 Răspuns generat.", "status": "ok"})

            except Exception as e:
                logger.error(f"❌ [Chronos Agent] Eroare la {ag_name}: {e}", exc_info=True)
                actions_list.append({"text": f"❌ Eroare {ag_name}: {e}", "status": "error"})

        result = {
            "intents": list(agents_to_call),
            "reply": reply_text,
            "actions": actions_list,
            "reasoning": reasoning,
        }
        if reply_text:
            self._remember(f"Chronos: {reply_text}")
        self.last_result = result
        return result

    # Numele vechi, păstrat pentru compatibilitate.
    _execute_agents = run_agents

    # ─────────────────────────────────────────────────────────────────────
    # CONVERSAȚIE
    # ─────────────────────────────────────────────────────────────────────

    def build_chat_prompt(self, text: str, data_cats: Optional[List[str]] = None) -> str:
        """Construiește promptul de conversație. Pur — niciun apel LLM.

        Separat de generare special ca stratul de voce să poată porni
        streamingul fără să reimplementeze asamblarea contextului.
        """
        memory = ""
        try:
            memory = (self.logger_agent.search_memory(text) or "")[:MEMORY_BUDGET_CHARS]
        except Exception as e:
            logger.debug(f"[Chronos Agent] Memorie indisponibilă: {e}")

        # Datele personale cerute de planificator (decise în apelul LLM
        # existent, deci fără request suplimentar).
        date_reale = ""
        if data_cats:
            try:
                from tools.context_tools import read_context
                date_reale = read_context(data_cats)
                logger.info(f"📂 [Chronos Agent] Date injectate: {data_cats}")
            except Exception as e:
                logger.warning(f"⚠️ [Chronos Agent] Nu pot citi datele {data_cats}: {e}")

        recent = "\n".join(list(self.conversation_history)[-HISTORY_IN_PROMPT:])

        now = datetime.now()
        zile = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]

        parts = [
            SYSTEM_PROMPT_TEXT,
            f"\n[ACUM] {zile[now.weekday()]}, {now.strftime('%d.%m.%Y, ora %H:%M')}. "
            f"Calculează „azi/mâine/weekend” de la data asta.",
        ]
        emo = self._emotion_block()
        if emo:
            parts.append("\n" + emo)
        if date_reale:
            parts.append(f"\n[DATELE TALE REALE — cifrele EXACTE de aici, nu inventa]\n{date_reale}")
        if memory:
            parts.append(f"\n[MEMORIE — folosește doar dacă e relevant]\n{memory}")
        if recent:
            parts.append(f"\n[ULTIMELE SCHIMBURI]\n{recent}")
        parts.append(
            f'\nSergiu îți scrie ACUM: "{text}"\n'
            "Răspunde-i direct, în română, scurt, în stilul tău. Text simplu, "
            "fără markdown, fără să te prezinți. Dacă ține de informații actuale "
            "(vreme, evenimente, știri, prețuri), caută pe net și dă cifre concrete."
        )
        return "\n".join(parts)

    def general_chat_reply(self, text: str, data_cats: Optional[List[str]] = None,
                           needs_web: bool = True) -> str:
        """Răspuns de conversație, dintr-o bucată (calea sincronă)."""
        prompt = self.build_chat_prompt(text, data_cats)
        reply = ask_gemini_text(prompt, temperature=0.9, use_search=needs_web)
        if not reply:
            logger.warning("⚠️ [Chronos Agent] general_chat n-a generat răspuns.")
            return "Nu am putut genera un răspuns acum, mai încearcă."
        self._update_emotions(text, reply)
        return reply

    def stream_chat_reply(
        self, text: str, data_cats: Optional[List[str]] = None,
        needs_web: bool = True,
    ) -> Iterator[str]:
        """Răspuns de conversație în bucăți, pe măsură ce modelul îl produce.

        Consumat de LLMRouter, care îl trimite mai departe în conducta TTS —
        de aici vine faptul că Chronos începe să vorbească înainte de a fi
        terminat de gândit răspunsul.
        """
        prompt = self.build_chat_prompt(text, data_cats)
        collected: List[str] = []
        for piece in stream_gemini_text(prompt, temperature=0.9, use_search=needs_web):
            collected.append(piece)
            yield piece

        reply = "".join(collected).strip()
        if reply:
            self._remember(f"Chronos: {reply}")
            self._update_emotions(text, reply)

    # Numele vechi (privat), păstrat ca alias.
    _general_chat_reply = general_chat_reply

    # ─────────────────────────────────────────────────────────────────────
    # STARE INTERNĂ
    # ─────────────────────────────────────────────────────────────────────

    def _remember(self, line: str) -> None:
        self.conversation_history.append(line)

    @staticmethod
    def _emotion_block() -> str:
        """Harta comportamentală derivată din starea afectivă curentă."""
        try:
            from config import EMOTIONS_ENABLED
            if not EMOTIONS_ENABLED:
                return ""
            from core.emotions import get_state
            return get_state().behavior_prompt()
        except Exception as e:
            logger.debug(f"[Chronos Agent] Bloc emoții indisponibil: {e}")
            return ""

    @staticmethod
    def _update_emotions(user_text: str, ai_text: str) -> None:
        """Analiză afectivă în fundal — nu blochează niciodată răspunsul."""
        try:
            from config import EMOTIONS_ENABLED, EMOTION_ANALYSIS_ENABLED
            if not EMOTIONS_ENABLED or not user_text.strip():
                return
            from core.emotions import get_state, analyze_exchange
            if EMOTION_ANALYSIS_ENABLED:
                threading.Thread(
                    target=analyze_exchange, args=(user_text, ai_text), daemon=True
                ).start()
            else:
                get_state().register_interaction()
        except Exception as e:
            logger.debug(f"[Chronos Agent] Actualizare emoții eșuată: {e}")

    # Registrul de capabilități era injectat în promptul de planificare la
    # fiecare comandă. Numele agenților din enum-ul schemei transmit deja
    # aceeași informație, gratis — deci textul a ieșit din prompt. Metoda
    # rămâne pentru dashboard/diagnostic.
    @staticmethod
    def get_available_capabilities() -> str:
        return (
            "music_agent — Spotify / difuzor Google (piese, volum, pauză)\n"
            "wled_agent  — benzi LED WLED (culori, efecte, dual-zone)\n"
            "logger_agent— jurnal, targeturi, memorie ChromaDB\n"
            "general_chat— conversație cu memorie + căutare web\n"
            "vreme       — starea de acum și prognoza, local din Home Assistant"
        )
