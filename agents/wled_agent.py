"""
agents/wled_agent.py — Agent Specializat Lumini WLED
=====================================================
Agent AI dedicat pentru ambianță vizuală, efecte și culori pe benzile LED WLED.
"""

import logging
from tools.wled_tools import set_all_leds, set_dual_zone_leds
from ai_core import ask_gemini_json

logger = logging.getLogger(__name__)


class WLEDAgent:
    """Agent AI specializat în atmosfera vizuală WLED."""

    def __init__(self):
        self.palettes_db = """
-- VIBRANT & HIGH ENERGY --
6: Party, 11: Rainbow, 57: Candy, 68: Red Shift, 1: Random Cycle
-- BLUE, TEAL & AQUA (Water & Chill) --
9: Ocean, 15: Breeze, 60: Semi Blue, 63: Aqua Flash, 51: Atlantica
-- RED, AMBER & FIRE (Warm) --
35: Fire, 8: Lava, 66: Red Flash, 69: Red Tide
-- PINK & PURPLE (Cyberpunk) --
19: Splash, 61: Pink Candy, 40: Magenta, 28: Hult
-- GREEN & NATURE (Earth) --
10: Forest, 50: Aurora, 14: Rivendell, 24: Departure
"""
        self.effects_db = """
28: Chase, 76: Meteor, 27: Android, 9: Rainbow, 43: Rain, 64: Juggle,
110: Flow, 115: Blends, 38: Aurora, 88: Candle, 87: Glitter, 10: Scan
"""

    @staticmethod
    def _normalize_rgb(col) -> list:
        """Garantează un triplet RGB valid (0-255). Completează/taie dacă modelul
        returnează un array greșit ca lungime (ex: un ID de paletă confundat cu culoarea)."""
        vals = list(col) if isinstance(col, (list, tuple)) else []
        vals = (vals + [255, 255, 255])[:3]
        return [max(0, min(255, int(v))) for v in vals]

    def process_request(self, user_command: str) -> dict:
        """Procesează comanda și setează starea benzilor WLED."""
        logger.info(f"🎨 [WLED Agent] Procesez: '{user_command}'")

        system_prompt = f"""
        Ești WLED Agent, AI-ul responsabil de luminile din cameră.
        Avem 2 zone:
        - Main (Top)
        - Floor (Bot)

        DB Palete (main_pal / floor_pal): {self.palettes_db}
        DB Efecte (main_fx / floor_fx): {self.effects_db}

        COMANDĂ UTILIZATOR: "{user_command}"

        Generează setările JSON pentru ambele zone.
        - main_col/floor_col: culoarea RGB de bază [R,G,B] (0-255 fiecare). Alege-o mereu,
          chiar și când folosești o paletă — e fallback-ul dacă paleta nu se aplică.
        - main_pal/floor_pal: ID-ul paletei din DB Palete DACĂ atmosfera cere una
          (ex: "atmosferă", "vibe", "party"). Pune 0 dacă vrei doar culoare solidă simplă.
        - main_fx/floor_fx: ID-ul efectului din DB Efecte DACĂ comanda cere mișcare/animație
          (ex: "curgere", "pulsare", "petrecere"). Pune 0 (Solid) dacă nu vrei animație.
        NU pune niciodată un ID de paletă/efect în main_col/floor_col — sunt câmpuri separate!
        """

        schema = {
            "type": "OBJECT",
            "properties": {
                "turn_off": {"type": "BOOLEAN"},
                "main_bri": {"type": "INTEGER"},
                "main_col": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                "main_pal": {"type": "INTEGER", "description": "ID paletă (0 = fără paletă, culoare solidă)"},
                "main_fx": {"type": "INTEGER", "description": "ID efect (0 = Solid, fără animație)"},
                "floor_bri": {"type": "INTEGER"},
                "floor_col": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                "floor_pal": {"type": "INTEGER", "description": "ID paletă (0 = fără paletă, culoare solidă)"},
                "floor_fx": {"type": "INTEGER", "description": "ID efect (0 = Solid, fără animație)"},
                "reasoning": {"type": "STRING"}
            },
            "required": [
                "turn_off", "main_bri", "main_col", "main_pal", "main_fx",
                "floor_bri", "floor_col", "floor_pal", "floor_fx", "reasoning"
            ]
        }

        decision = ask_gemini_json(system_prompt, schema=schema, temperature=0.2)

        if not decision or not isinstance(decision, dict):
            # Fallback direct
            set_all_leds(255, 100, 50, 150)
            return {"status": "ok", "msg": "WLED setat pe fallback."}

        if decision.get("turn_off"):
            set_all_leds(0, 0, 0, turn_off=True)
            return {"status": "ok", "msg": "Luminile au fost stinse."}

        main_payload = {
            "on": True,
            "bri": max(0, min(255, decision.get("main_bri", 150))),
            "seg": [{
                "col": [self._normalize_rgb(decision.get("main_col"))],
                "pal": max(0, int(decision.get("main_pal") or 0)),
                "fx":  max(0, int(decision.get("main_fx") or 0)),
            }]
        }
        floor_payload = {
            "on": True,
            "bri": max(0, min(255, decision.get("floor_bri", 150))),
            "seg": [{
                "col": [self._normalize_rgb(decision.get("floor_col"))],
                "pal": max(0, int(decision.get("floor_pal") or 0)),
                "fx":  max(0, int(decision.get("floor_fx") or 0)),
            }]
        }

        res = set_dual_zone_leds(main_payload, floor_payload)
        reason = decision.get("reasoning", "Luminile au fost ajustate.")
        logger.info(f"🎨 [WLED Agent] Rezultat: {reason}")
        return {"status": "ok", "msg": reason, "details": res}
