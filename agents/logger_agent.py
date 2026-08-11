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

    def get_recent_conversations(self, n: int = 5) -> str:
        """
        Recap text al ultimelor N conversații (indiferent de subiect) —
        injectat în system prompt-ul unei sesiuni vocale noi, ca Chronos
        să știe ce s-a discutat înainte cu el.
        """
        try:
            docs = self.memory_manager.get_recent(n=n, where_filter={"type": "conversation"})
            return "\n---\n".join(docs) if docs else ""
        except Exception as e:
            logger.error(f"❌ [Logger Agent] Eroare recap conversații: {e}")
            return ""
