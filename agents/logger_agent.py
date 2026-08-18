"""
agents/logger_agent.py — Agent Specializat Jurnal & Memorie
===========================================================
Agent AI dedicat pentru salvarea notițelor și interogarea memoriei pe termen lung.
"""

import time
import logging
from datetime import datetime

from tools.memory_tools import save_journal_text, add_user_target
from logger_specialist import MemoryManager

logger = logging.getLogger(__name__)


class LoggerAgent:
    """Agent AI specializat în memorie și jurnale."""

    def __init__(self):
        self.memory_manager = MemoryManager()

    def save_entry(self, text: str) -> dict:
        """Salvează o notă în jurnal."""
        return save_journal_text(text)

    def add_target(self, target_text: str) -> dict:
        """Adaugă un target personal."""
        return add_user_target(target_text)

    def search_memory(self, query: str) -> str:
        """Interoghează memoria pe termen lung din ChromaDB."""
        logger.info(f"🔎 [Logger Agent] Caut în memorie: '{query}'")
        results = self.memory_manager.query_memory([query], n_results=3)
        return results if results else "Nicio amintire găsită."

    # ── Memorie conversațională (sesiuni vocale Gemini Live) ──

    def save_conversation_turn(self, user_text: str, ai_text: str) -> None:
        """
        Salvează un schimb (user↔Chronos) din sesiunea vocală live în ChromaDB,
        ca Chronos să poată face referire la el în sesiuni viitoare.
        """
        user_text = (user_text or "").strip()
        ai_text   = (ai_text or "").strip()
        if not user_text and not ai_text:
            return

        doc = f"Sergiu: {user_text}\nChronos: {ai_text}".strip()
        mem_id = f"conv_{int(time.time() * 1000)}"
        meta = {
            "type": "conversation",
            "timestamp": datetime.now().isoformat(),
        }
        try:
            self.memory_manager.add_memory(mem_id, doc, meta)
        except Exception as e:
            logger.error(f"❌ [Logger Agent] Eroare salvare conversație: {e}")

    def get_recent_conversations(self, n: int = 3, max_chars: int = 220) -> str:
        """
        Recap text al ultimelor N conversații — injectat în system prompt-ul
        unei sesiuni noi. Se plătește la FIECARE tur al sesiunii, deci ținem
        puține și scurte: scopul e continuitatea firului, nu arhiva completă.
        """
        try:
            docs = self.memory_manager.get_recent(n=n, where_filter={"type": "conversation"})
            if not docs:
                return ""
            scurte = []
            for d in docs:
                d = " ".join(d.split())          # colapsăm spațiile/newline-urile
                if len(d) > max_chars:
                    d = d[:max_chars].rsplit(" ", 1)[0] + "…"
                scurte.append(d)
            return "\n".join(scurte)
        except Exception as e:
            logger.error(f"❌ [Logger Agent] Eroare recap conversații: {e}")
            return ""
