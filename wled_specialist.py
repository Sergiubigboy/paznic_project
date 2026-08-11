"""
wled_specialist.py — Deprecated Shim
====================================
Migrat în `agents/wled_agent.py` și `tools/wled_tools.py`.
Păstrat doar ca shim de compatibilitate.
"""

from agents.wled_agent import WLEDAgent
from tools.wled_tools import set_all_leds, set_dual_zone_leds

WLEDDispatcher = WLEDAgent
WLEDStateManager = WLEDAgent