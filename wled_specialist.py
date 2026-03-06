import requests
import logging
import datetime
from concurrent.futures import ThreadPoolExecutor
from config import WLED_IP_MAIN, WLED_IP_FLOOR
from ai_core import ask_gemini_json

class WLEDDispatcher:
    def __init__(self):
        self.palettes_db = """
-- VIBRANT & HIGH ENERGY (Multicolor/Party) --
6: Party – Rainbow fără nuanțe de verde (vibe de club).
11: Rainbow – Spectrul complet (clasic, fluid).
57: Candy – Galben, magenta și albastru electric (pop-art).
68: Red Shift – Galben, albastru, magenta și roșu (extrem de dinamic).
1: Random Cycle – Se schimbă singur la fiecare câteva secunde.

-- BLUE, TEAL & AQUA (Deep Water & Cold) --
9: Ocean – Mix de albastru, turcoaz și alb (marin).
15: Breeze – Tonalități de turcoaz (teal) cu luminozitate variabilă (relaxant).
60: Semi Blue – Albastru închis cu explozii de lumină electrică.
63: Aqua Flash – Gradient aqua cu flash-uri galbene și albe.
51: Atlantica – Mix de verde marin și albastru de adâncime.
-- RED, AMBER & FIRE (Warm & Aggressive) --
35: Fire – Gradient de alb, galben și roșu (foc realist).
8: Lava – Roșu închis, galben și negru (magmă).
66: Red Flash – Roșu intens cu o explozie de alb în centru.
69: Red Tide – Valuri de galben, portocaliu și roșu (intens).
-- PINK, MAGENTA & PURPLE (Cyberpunk & Glamour) --
19: Splash – Roz vibrant și magenta (neon vibes).
61: Pink Candy – Alb, roz și mov (dulce, luminos).
40: Magenta – Alb pur cu accente de magenta și albastru.
28: Hult – Alb, magenta și turcoaz (stil retro-wave).
--  GREEN & NATURE (Earth & Zen) --
10: Forest – Nuanțe de verde și galben (organic).
50: Aurora – Verde neon pe fundal albastru închis (magic).
14: Rivendell – Verzi desaturate și pale (fantasy/misterios).
24: Departure – Mix de verde și alb care se stinge treptat.
--  CONTRAST & SPECIAL MIXES (Unique Vibes) --
44: Orange & Teal – Contrast cinematografic (Hollywood look).
30: Drywet – Contrast puternic între Galben și Albastru.
45: Tiamat – Meteorit strălucitor cu albastru, turcoaz și magenta.
56: Retro Clown – Gradient jucăuș de la galben la mov.
46: April Night – Fundal albastru închis cu "fulgi" colorați.
--  FESTIVE & HOLIDAY (Sărbători) --
48: C9 – Culori retro de Crăciun (Roșu, Chihlimbar, Verde, Albastru).
31: Jul – Mix modern de roșu și verde pastelat.
49: Sakura – Roz și alb (primăvară/japonez).
52: C9 2 – Mix festiv clasic + nuanțe de galben.
        """

        self.effects_db = """
        -- Atmosfere --
         28: Chase, 76: Meteor, 27: Android, 9: Rainbow, 43: Rain, 64: Juggle
        -- RELAXING --
         110: Flow, 115: Blends, 38: Aurora, 88: Candle, 87: Glitter, 10: Scan
        -- TRIPPY --
        57: Lightling 42: Fireworks, 184: Wavesins, 
        """

    def _get_time_context(self):
        hour = datetime.datetime.now().hour
        if 8 <= hour < 19: return "DAY (Bright allowed)"
        elif 19 <= hour < 23: return "EVENING (Cozy/Medium)"
        else: return "NIGHT (Dim/Low - DO NOT BLIND USER)"

    def _get_current_state_summary(self):
        try:
            resp = requests.get(f"http://{WLED_IP_MAIN}/json/state", timeout=0.5)
            if resp.status_code == 200:
                d = resp.json()
                return f"Main Light is {'ON' if d['on'] else 'OFF'}, Bri: {d['bri']}"
        except: pass
        return "Unknown"

    def _get_ai_dual_decision(self, user_text, conversation_history):
        state_summary = self._get_current_state_summary()
        time_context = self._get_time_context()

        system_prompt = f"""
        You are an Advanced Dual-Zone Lighting Designer.
        
        SETUP & PHYSICS (CRITICAL):
        1. "main": Ceiling/Wardrobe LEDs. 
        2. "floor": Under-desk/Floor LEDs. 
        !! Because the main lights are more powerful here is how you can use them proportionally:
           -main bri 90-100% -> floor bri 100%
           -main bri 30-80% -> floor bri 80-100%
           -main bri 1-30% -> floor bri 50-80%

        CURRENT CONTEXT:
        - Time(use dimmer lights as it gets darker): {time_context}
        - Current Status: {state_summary}
        
        RECENT CONVERSATION HISTORY:
        {conversation_history}

        AVAILABLE PALETTES (pal):
        {self.palettes_db}

        AVAILABLE EFFECTS (fx):
        {self.effects_db}

        INSTRUCTIONS:
        1. NEVER just use solid colors unless explicitly asked. ALWAYS pick an effect (fx) and a palette (pal) from the lists above to create a dynamic vibe!
        2. Explain your artistic choices in the 'reasoning' field (in Romanian). Why this palette? Why this effect?
        3. Combine the two zones logically (e.g., ocean -> blue main, teal floor).
        
        User Request: "{user_text}"
        """

        wled_schema = {
            "type": "OBJECT",
            "properties": {
                "reasoning": {"type": "STRING", "description": "Explică în română logica alegerii efectului și paletei."},
                "main": {
                    "type": "OBJECT",
                    "properties": {
                        "on": {"type": "BOOLEAN"},
                        "bri": {"type": "INTEGER"},
                        "seg": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "fx": {"type": "INTEGER"},
                                    "pal": {"type": "INTEGER"},
                                    "sx": {"type": "INTEGER"},
                                    "ix": {"type": "INTEGER"}
                                },
                                "required": ["fx", "pal", "sx", "ix"]
                            }
                        }
                    },
                    "required": ["on", "bri", "seg"]
                },
                "floor": {
                    "type": "OBJECT",
                    "properties": {
                        "on": {"type": "BOOLEAN"},
                        "bri": {"type": "INTEGER"},
                        "seg": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "fx": {"type": "INTEGER"},
                                    "pal": {"type": "INTEGER"},
                                    "sx": {"type": "INTEGER"},
                                    "ix": {"type": "INTEGER"}
                                },
                                "required": ["fx", "pal", "sx", "ix"]
                            }
                        }
                    },
                    "required": ["on", "bri", "seg"]
                }
            },
            "required": ["reasoning", "main", "floor"]
        }

        return ask_gemini_json(system_prompt, schema=wled_schema, temperature=0.85)

    def execute(self, user_text, conversation_history=""):
        logging.info(f"🎨 Dual-Zone AI: '{user_text}'")
        full_scene = self._get_ai_dual_decision(user_text, conversation_history)
        
        if not full_scene: return
        
        # AICI VOM VEDEA DE CE A ALES EFECTELE
        logging.info(f"💡 LOGICĂ WLED: {full_scene.get('reasoning', 'Fără explicație')}")

        try:
            with ThreadPoolExecutor() as executor:
                if "main" in full_scene:
                    executor.submit(self._send_request, WLED_IP_MAIN, full_scene["main"])
                if "floor" in full_scene:
                    executor.submit(self._send_request, WLED_IP_FLOOR, full_scene["floor"])
        except Exception as e:
            logging.error(f"Eroare execuție WLED: {e}")

        # SCHEMA STRICTĂ PENTRU LUMINILE WLED
        wled_schema = {
            "type": "OBJECT",
            "properties": {
                "main": {
                    "type": "OBJECT",
                    "properties": {
                        "on": {"type": "BOOLEAN"},
                        "bri": {"type": "INTEGER"},
                        "seg": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "fx": {"type": "INTEGER"},
                                    "pal": {"type": "INTEGER"},
                                    "sx": {"type": "INTEGER"},
                                    "ix": {"type": "INTEGER"}
                                },
                                "required": ["fx", "pal", "sx", "ix"]
                            }
                        }
                    },
                    "required": ["on", "bri", "seg"]
                },
                "floor": {
                    "type": "OBJECT",
                    "properties": {
                        "on": {"type": "BOOLEAN"},
                        "bri": {"type": "INTEGER"},
                        "seg": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "fx": {"type": "INTEGER"},
                                    "pal": {"type": "INTEGER"},
                                    "sx": {"type": "INTEGER"},
                                    "ix": {"type": "INTEGER"}
                                },
                                "required": ["fx", "pal", "sx", "ix"]
                            }
                        }
                    },
                    "required": ["on", "bri", "seg"]
                }
            }
        }

    def _send_request(self, ip, data):
        try:
            requests.post(f"http://{ip}/json/state", json=data, timeout=1.5)
        except Exception as e:
            logging.error(f"Eroare trimitere către {ip}: {e}")

    def execute(self, user_text, conversation_history=""):
        logging.info(f"🎨 Dual-Zone AI: '{user_text}'")
        full_scene = self._get_ai_dual_decision(user_text, conversation_history)
        
        if not full_scene: return

        try:
            with ThreadPoolExecutor() as executor:
                if "main" in full_scene:
                    bri = full_scene['main'].get('bri', 'N/A')
                    logging.info(f"Main (Top) -> Bri: {bri}/255")
                    executor.submit(self._send_request, WLED_IP_MAIN, full_scene["main"])
                
                if "floor" in full_scene:
                    bri = full_scene['floor'].get('bri', 'N/A')
                    logging.info(f"Floor (Bot) -> Bri: {bri}/255")
                    executor.submit(self._send_request, WLED_IP_FLOOR, full_scene["floor"])
                    
        except Exception as e:
            logging.error(f"Eroare execuție: {e}")


class WLEDStateManager:
    def __init__(self):
        self.saved_states = {} 

    def _get_state(self, ip):
        try:
            r = requests.get(f"http://{ip}/json/state", timeout=0.5)
            if r.status_code == 200:
                d = r.json()
                return {"on": d.get("on"), "bri": d.get("bri"), "seg": d.get("seg")}
        except: pass
        return None

    def save_state(self):
        with ThreadPoolExecutor() as executor:
            future_main = executor.submit(self._get_state, WLED_IP_MAIN)
            future_floor = executor.submit(self._get_state, WLED_IP_FLOOR)
            self.saved_states["main"] = future_main.result()
            self.saved_states["floor"] = future_floor.result()

    def start_loading_animation(self):
        def send_anim(ip, color, bri):
            payload = {
                "on": True, "bri": bri,
                "seg": [{
                    "id": 0, "fx": 10, "sx": 240, "ix": 150, "pal": 0,
                    "col": [color, [0,0,0], [0,0,0]]
                }]
            }
            try: requests.post(f"http://{ip}/json/state", json=payload, timeout=0.5)
            except: pass

        with ThreadPoolExecutor() as executor:
            executor.submit(send_anim, WLED_IP_MAIN, [255, 0, 0], 60)   
            executor.submit(send_anim, WLED_IP_FLOOR, [128, 0, 255], 200) 

    def restore_state(self):
        def restore(ip, state_key):
            if state_key in self.saved_states and self.saved_states[state_key]:
                try: requests.post(f"http://{ip}/json/state", json=self.saved_states[state_key], timeout=1)
                except: pass

        with ThreadPoolExecutor() as executor:
            executor.submit(restore, WLED_IP_MAIN, "main")
            executor.submit(restore, WLED_IP_FLOOR, "floor")


if __name__ == "__main__":
    wled = WLEDDispatcher()
    while True:
        txt = input("Comandă: ")
        if txt == "exit": break
        wled.execute(txt)