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
from typing import List, Dict, Any

from agents.music_agent import MusicAgent
from agents.wled_agent import WLEDAgent
from agents.logger_agent import LoggerAgent
from ai_core import ask_gemini_json

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
                    past_mem = self.logger_agent.search_memory(text)
                    reply_text = f"Chronos: Răspuns bazat pe memorie ({past_mem[:60]}...)"
                    actions_list.append({"text": "🧠 Răspuns general creat.", "status": "ok"})

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
