"""
dispatcher.py — Deprecated / Backwards-Compatibility Proxy
===========================================================
Acest fișier este păstrat pentru compatibilitate înapoi.
Toată logica de orchestrare a fost migrată în modulul `agents/chronos_agent.py`.
"""

from agents.chronos_agent import ChronosAgent

# Instanțiere proxy pentru compatibilitate înapoi
CommandDispatcher = ChronosAgent