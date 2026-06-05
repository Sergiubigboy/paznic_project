import logging
import json
import os
import random
import requests
from datetime import datetime
from config import GEMINI_MODEL_DJ, HA_TOKEN, HA_URL
from ai_core import ask_gemini_json

# --- CONFIGURARE ---
DEBUG_MODE = True
STRATEGY_FILE = "chronos_data/dj/dj_strategy.txt"
HISTORY_FILE = "chronos_data/dj/dj_history.json"
SPEAKER_NAME = "Sergiu speaker" # Exact cum se numește boxa ta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class MusicHandler:
    def __init__(self):
        self.strategy = self._load_text(STRATEGY_FILE)
        self.play_history = self._load_history()
        self.was_playing_before_pause = False
        
    def _load_text(self, filename):
        if not os.path.exists(filename): return ""
        with open(filename, "r", encoding="utf-8") as f: return f.read()

    def _load_history(self):
        if not os.path.exists(HISTORY_FILE): return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: return []

    def _add_to_history(self, track_name):
        self.play_history.append(track_name)
        if len(self.play_history) > 10: self.play_history.pop(0)
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.play_history, f, ensure_ascii=False, indent=4)
        except Exception as e:
            if DEBUG_MODE: logging.warning(f"⚠️ Nu am putut salva istoricul: {e}")

    def send_to_google(self, command_text):
        """Trimite comanda text prin Home Assistant către Google"""
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json"
        }
        # Adăugăm numele boxei la finalul comenzii
        full_command = f"{command_text} on {SPEAKER_NAME}"
        payload = {
            "command": full_command
        }
        
        try:
            response = requests.post(HA_URL, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                logging.info(f"✅ Trimis la Google: {full_command}")
                return True
            else:
                logging.error(f"❌ Eroare HA: {response.text}")
                return False
        except Exception as e:
            logging.error(f"❌ Eroare conexiune HA: {e}")
            return False

    def pause_playback(self):
        """Pauză temporară pentru a asculta comanda (folosit de main.py)"""
        success = self.send_to_google("pause the music")
        if success:
            self.was_playing_before_pause = True
            logging.info("⏸️ Muzică pusă pe pauză pentru a asculta comanda vocală.")

    def resume_playback(self):
        """Reluăm muzica dacă era pornită înainte de pauză (folosit de main.py)"""
        if hasattr(self, 'was_playing_before_pause') and self.was_playing_before_pause:
            success = self.send_to_google("resume the music")
            if success:
                logging.info("▶️ Muzică reluată.")
            self.was_playing_before_pause = False

    def _get_time_context(self):
        hour = datetime.now().hour
        if 5 <= hour < 12: return "MORNING (Wake Up / Energize / Start Day)"
        elif 12 <= hour < 18: return "AFTERNOON (Focus / Vibe / Activity)"
        elif 18 <= hour < 22: return "EVENING (Chill / Social / Pre-Party)"
        else: return "LATE NIGHT (Deep / Psychedelic / Introspective / Bedroom Flow)"

    def _ask_gemini_dj(self, user_text, conversation_history):
        time_context = self._get_time_context()
        current_time = datetime.now().strftime("%H:%M")
        history_str = ", ".join(self.play_history) if self.play_history else "No recent tracks played yet."
        rand_seed = random.random() # Ca să nu se repete
        
        system_prompt = f"""
        ROLE: Elite Music Curator, DJ, and Translator for Google Assistant.
        
        CURRENT TIME: {current_time}
        TIME VIBE: {time_context}
        RANDOM SEED: {rand_seed}
        
        RECENT CONVERSATION HISTORY:
        {conversation_history}

        BANNED TRACKS (RECENTLY PLAYED - NEVER PICK THESE):
        {history_str}

        GOLDEN RULES:
        {self.strategy}
        
        VARIETY RULES:
        - NEVER suggest overplayed mainstream hits or anything in the BANNED list above.
        - Think like a music nerd who never repeats themselves. Surprise the user.
        
        USER REQUEST: "{user_text}"
        
        INSTRUCTIONS:
        1. If the user asks for a SPECIFIC song/artist or gives a CONTROL command (e.g., "pune piesa X", "dă mai tare", "piesa următoare", "pune pauză"), TRANSLATE it directly into a simple English command.
        2. If the user asks for a VIBE/GENRE (e.g., "pune ceva chill", "pune rap", "fă atmosferă"), USE YOUR AI BRAIN to pick ONE SPECIFIC, EXCELLENT TRACK that fits the vibe, time context, and strategy. DO NOT ask for playlists. Ask Google to play that SPECIFIC track by the artist.
        3. Generate the EXACT English text command to feed to Google Assistant. DO NOT INCLUDE THE SPEAKER NAME, just the core command.
        
        EXAMPLES OF GOOD GOOGLE COMMANDS:
        - "play Massive by Drake" (If user asks for this specifically, OR if user asks for an Ego Boost and you pick this track)
        - "play MALAMENTE by ROSALIA"
        - "next song"
        - "pause the music"
        - "set volume to 40 percent"
        """

        dj_schema = {
            "type": "OBJECT",
            "properties": {
                "google_command": {"type": "STRING", "description": "The exact English text command for Google Assistant (e.g., 'play The Hills by The Weeknd' or 'next song')"},
                "track_name_saved": {"type": "STRING", "description": "If a SPECIFIC song is going to be played, write its 'Song - Artist' here to save to history. If it's just a control command (volume, next), write 'none'."},
                "reason": {"type": "STRING", "description": "Explică în română logica din spatele piesei alese sau acțiunii."}
            },
            "required": ["google_command", "track_name_saved", "reason"]
        }

        return ask_gemini_json(system_prompt, schema=dj_schema, temperature=0.8, model=GEMINI_MODEL_DJ)

    def process_command(self, user_text, conversation_history=""):
        decision = self._ask_gemini_dj(user_text, conversation_history)
        if not decision: return None

        command = decision.get('google_command')
        track_saved = decision.get('track_name_saved')
        reason = decision.get('reason')

        print(f"\n🧠 RAȚIONAMENT AI (DJ): {reason}")
        print(f"🤖 COMANDA GOOGLE: {command} on {SPEAKER_NAME}")

        # Trimitem comanda către Home Assistant
        success = self.send_to_google(command)
        
        if success:
            # Salvăm în istoric dacă s-a cerut o piesă
            if track_saved and track_saved.lower() != 'none':
                self._add_to_history(track_saved)
            
            # Actualizăm starea de pauză internă în caz că userul zice "pune pauză" sau "continuă"
            if "pause" in command.lower() or "stop" in command.lower():
                self.was_playing_before_pause = False
            elif "play" in command.lower() or "resume" in command.lower():
                self.was_playing_before_pause = False 
                
            return {"status": "success", "msg": f"Am transmis: {command}", "reason": reason}
        else:
            return {"status": "error", "msg": "Eroare la comunicarea cu boxa.", "reason": reason}

if __name__ == "__main__":
    dj = MusicHandler()
    while True:
        try:
            txt = input("\n🎧 Comandă: ")
            if txt.lower() in ["exit", "stop"]: break
            dj.process_command(txt)
        except KeyboardInterrupt: break