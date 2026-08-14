"""
agents/chronos_agent.py — Agentul AI Principal Orchestrator (Chronos)
========================================================================
Agentul AI cu care vorbește utilizatorul. Deține registrul complet de agenți
specializați și unelte (tools). El DECIDE ce sub-agenți/tool-uri să apeleze
și le declanșează ASINCRON în fundal fără să blocheze nimic!
"""

import time
import asyncio
import logging
import threading
from datetime import datetime
from typing import List, Dict, Any

from agents.music_agent import MusicAgent
from agents.wled_agent import WLEDAgent
from agents.logger_agent import LoggerAgent
from ai_core import ask_gemini_json, ask_gemini_text
from config import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ChronosAgent:
    """
    Main Orchestrator Agent.
    Primește comenzile de la utilizator (Voce / Terminal / Web) și coordonează agenții specializați.
    """

    def __init__(self):
        # Registru Sub-Agenți Specializați
        self.music_agent = MusicAgent()
        self.wled_agent  = WLEDAgent()
        self.logger_agent = LoggerAgent()

        # Istoric conversație & rezultate
        self.conversation_history: List[tuple] = []
        self.last_result: Dict[str, Any] = {}

    def get_available_capabilities(self) -> str:
        """Returnează registrul cu toți agenții și uneltele disponibile."""
        return """
AGENTI SPECIALIZATI DISPONIBILI:
1. music_agent  — Controlează muzica pe Spotify / Google Home difuzor. Alege piese, volum, pauză.
2. wled_agent   — Controlează benzile LED WLED (culori, efecte, atmosferă vizuală dual-zone).
3. logger_agent — Salvează notițe în jurnal, target-uri personale, interoghează memoria din ChromaDB.
4. general_chat — Răspuns direct / conversație cu utilizatorul bazată pe memorie.

UNELTE (TOOLS):
- spotify_tools (send_google_command, pause_music, resume_music)
- wled_tools (set_all_leds, set_dual_zone_leds)
- memory_tools (save_journal_text, add_user_target)
"""

    def plan_and_route(self, user_text: str) -> dict:
        """
        Main AI Plan: Analizează cerința utilizatorului și decide ce agenți
        trebuie apelați simultan (pot fi 0, 1 sau mai mulți în paralel).
        """
        logger.info(f"🧠 [Chronos Agent] Planific comanda: '{user_text}'")

        system_prompt = f"""
        Ești Chronos Agent, creierul central de orchestrare al asistentului AI.
        
        {self.get_available_capabilities()}

        COMANDĂ UTILIZATOR: "{user_text}"

        Decide ce agenți/tool-uri trebuie invocați:
        - Dacă cere muzică (ex: "pune rock", "pune latina") → ["music_agent"]
        - Dacă cere lumini (ex: "pune roșu", "stinge ledurile") → ["wled_agent"]
        - Dacă cere atmosferă (ex: "atmosferă de munte", "schimbă muzica și luminile") → ["music_agent", "wled_agent"]
        - Dacă vrea să noteze în jurnal (ex: "am fost la sală") → ["logger_agent"]
        - Dacă este o întrebare generală sau discuție → ["general_chat"]

        Returnează lista de agenți ("agents") și raționamentul.
        """

        schema = {
            "type": "OBJECT",
            "properties": {
                "agents": {
                    "type": "ARRAY",
                    "items": {
                        "type": "STRING",
                        "enum": ["music_agent", "wled_agent", "logger_agent", "general_chat"]
                    }
                },
                "reasoning": {"type": "STRING"}
            },
            "required": ["agents", "reasoning"]
        }

        return ask_gemini_json(system_prompt, schema=schema, temperature=0.1)

    # Tool call → agent, pentru rutarea DIRECTĂ (fără pasul de planificare).
    # Când apelantul știe deja ce agent trebuie (ex: Gemini Live a ales
    # `control_lights`), un al doilea apel LLM care să redescopere „e despre
    # lumini" e timp și bani aruncați — plus risc de rutare greșită.
    TOOL_AGENT_MAP = {
        "control_lights": "wled_agent",
        "control_music":  "music_agent",
        "save_journal":   "logger_agent",
    }

    def route_direct(self, agent_name: str, text: str) -> bool:
        """
        Execută DIRECT un agent, sărind peste planificarea LLM.
        Folosit de calea vocală, unde tool call-ul a stabilit deja intenția.
        """
        if not text or not text.strip():
            return True

        self.conversation_history.append((time.time(), f"User: {text}"))
        logger.info(f"⚡ [Chronos Agent] Rutare DIRECTĂ (fără planificare) → {agent_name}")
        self._execute_agents([agent_name], text, reasoning="Rutare directă din tool call.")
        return True

    def process_text_command(self, text: str, sock=None) -> bool:
        """
        Metodă principală pentru intrările de TEXT BRUT (Terminal, Web), unde
        nimeni nu a stabilit încă intenția — deci planificarea LLM e necesară.
        Calea vocală folosește route_direct() (vezi TOOL_AGENT_MAP).
        """
        if not text or not text.strip():
            return True

        current_time = time.time()
        self.conversation_history.append((current_time, f"User: {text}"))

        plan = self.plan_and_route(text)
        agents_to_call = plan.get("agents", ["general_chat"]) if isinstance(plan, dict) else ["general_chat"]
        reasoning = plan.get("reasoning", "") if isinstance(plan, dict) else ""

        self._execute_agents(agents_to_call, text, reasoning)
        return True

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

    def _update_emotions(self, user_text: str, ai_text: str) -> None:
        """Analiză afectivă în fundal (thread separat) — nu blochează răspunsul."""
        try:
            from config import EMOTIONS_ENABLED, EMOTION_ANALYSIS_ENABLED
            if not EMOTIONS_ENABLED or not user_text.strip():
                return
            from core.emotions import get_state, analyze_exchange
            fn = analyze_exchange if EMOTION_ANALYSIS_ENABLED else None
            if fn:
                threading.Thread(
                    target=fn, args=(user_text, ai_text), daemon=True
                ).start()
            else:
                get_state().register_interaction()
        except Exception as e:
            logger.debug(f"[Chronos Agent] Actualizare emoții eșuată: {e}")

    def _general_chat_reply(self, text: str) -> str:
        """
        Generează un răspuns real pentru conversație (terminal + dashboard web).

        Înainte, aici era doar un placeholder care afișa primele 60 de caractere
        din memoria ChromaDB — de unde textul fără sens din terminalul web.
        Acum răspunde efectiv, în personalitatea lui Chronos, cu memoria ca
        context și cu acces la căutare web pentru informații actuale (vreme,
        evenimente, știri) — la fel ca sesiunea vocală.
        """
        memory = ""
        try:
            memory = (self.logger_agent.search_memory(text) or "")[:1200]
        except Exception as e:
            logger.debug(f"[Chronos Agent] Memorie indisponibilă: {e}")

        recent = ""
        if self.conversation_history:
            recent = "\n".join(line for _, line in self.conversation_history[-6:])

        now = datetime.now()
        zile = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]

        prompt = f"""{SYSTEM_PROMPT}

[ACUM] Este {zile[now.weekday()]}, {now.strftime('%d.%m.%Y, ora %H:%M')}.
Calculează „azi/mâine/weekend” raportat la data asta, nu ghici.

{self._emotion_block()}

[CONTEXT DIN MEMORIE — folosește-l DOAR dacă e relevant pentru ce te întreabă acum]
{memory or "(nimic relevant)"}

[ULTIMELE SCHIMBURI]
{recent or "(începutul conversației)"}

Sergiu îți scrie ACUM, din terminal: "{text}"

Răspunde-i direct, în română, scurt, în stilul tău. Text simplu pentru citit pe ecran,
fără markdown și fără să te prezinți. Dacă întrebarea ține de informații actuale
(vreme, evenimente, știri, prețuri), caută pe net și dă-i date concrete."""

        reply = ask_gemini_text(prompt, temperature=0.9, use_search=True)
        if not reply:
            logger.warning("⚠️ [Chronos Agent] general_chat n-a putut genera răspuns.")
            return "Nu am putut genera un răspuns acum, mai încearcă."

        self._update_emotions(text, reply)
        return reply

    def _execute_agents(self, agents_to_call: List[str], text: str, reasoning: str = "") -> dict:
        """Rulează agenții ceruți și salvează rezultatul în last_result."""
        actions_list = []
        reply_text = None

        for ag_name in agents_to_call:
            try:
                if ag_name == "wled_agent":
                    res = self.wled_agent.process_request(text)
                    actions_list.append({"text": f"💡 WLED: {res.get('msg')}", "status": res.get("status", "ok")})

                elif ag_name == "music_agent":
                    res = self.music_agent.process_request(text)
                    status = res.get("status", "ok")
                    msg = res.get("msg", "Muzică procesată.")
                    actions_list.append({"text": f"🎵 Spotify: {msg}", "status": status})
                    if res.get("reason"):
                        reply_text = f"DJ: {res.get('reason')}"

                elif ag_name == "logger_agent":
                    res = self.logger_agent.save_entry(text)
                    actions_list.append({"text": "📘 Salvat în jurnal.", "status": "ok"})

                elif ag_name == "general_chat":
                    reply_text = self._general_chat_reply(text)
                    actions_list.append({"text": "🧠 Răspuns generat.", "status": "ok"})

            except Exception as e:
                logger.error(f"❌ [Chronos Agent] Eroare la apelare {ag_name}: {e}", exc_info=True)
                actions_list.append({"text": f"❌ Eroare {ag_name}: {e}", "status": "error"})

        # Salvăm rezultatul pentru Web Dashboard & Terminal
        self.last_result = {
            "intents": agents_to_call,
            "reply": reply_text,
            "actions": actions_list,
            "reasoning": reasoning
        }

        if reply_text:
            self.conversation_history.append((time.time(), f"Chronos: {reply_text}"))

        return self.last_result
