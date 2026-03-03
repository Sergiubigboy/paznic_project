import spotipy
from spotipy.oauth2 import SpotifyOAuth
import logging
import json
import os
import traceback
import time
from datetime import datetime
from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI, SPOTIFY_DEVICE_NAME
from ai_core import ask_gemini_json

# --- CONFIGURARE ---
DEBUG_MODE = True
STRATEGY_FILE = "dj_strategy.txt"
HISTORY_FILE = "dj_history.json"
SCOPE = "user-read-playback-state user-modify-playback-state user-read-private user-top-read user-library-read"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class MusicHandler:
    def __init__(self):
        self.sp = None
        self.strategy = self._load_text(STRATEGY_FILE)
        self.user_taste_profile = "" 
        self.play_history = self._load_history()
        self.was_playing_before_pause = False
        
        try:
            self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=SCOPE,
                open_browser=True
            ))
            print(f"✅ [DEBUG] Spotify Conectat.")
            self._analyze_user_taste()
            
        except Exception as e:
            print(f"❌ [CRITICAL] Eroare Auth Spotify: {e}")

    def _load_text(self, filename):
        if not os.path.exists(filename): return ""
        with open(filename, "r", encoding="utf-8") as f: return f.read()

    def _save_text(self, filename, text):
        with open(filename, "w", encoding="utf-8") as f: f.write(text)
        self.strategy = text

    def _load_history(self):
        if not os.path.exists(HISTORY_FILE): return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: return []

    def _add_to_history(self, track_info):
        self.play_history.append(track_info)
        if len(self.play_history) > 10: self.play_history.pop(0)
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.play_history, f, ensure_ascii=False, indent=4)
        except Exception as e:
            if DEBUG_MODE: print(f"⚠️ Nu am putut salva istoricul: {e}")

    def _get_device_id(self):
        try:
            devices = self.sp.devices()
            if not devices or 'devices' not in devices: return None
            for d in devices['devices']:
                if SPOTIFY_DEVICE_NAME.lower() in d['name'].lower(): return d['id']
            if devices['devices']: return devices['devices'][0]['id']
            return None
        except: return None

    def _analyze_user_taste(self):
        print("🧠 [AI] Analizez istoricul tău muzical...")
        try:
            short = self.sp.current_user_top_tracks(limit=5, time_range='short_term')
            short_str = ", ".join([f"{t['name']} ({t['artists'][0]['name']})" for t in short['items']])
            
            medium = self.sp.current_user_top_tracks(limit=5, time_range='medium_term')
            medium_str = ", ".join([f"{t['name']} ({t['artists'][0]['name']})" for t in medium['items']])
            
            long = self.sp.current_user_top_tracks(limit=5, time_range='long_term')
            long_str = ", ".join([f"{t['name']} ({t['artists'][0]['name']})" for t in long['items']])

            saved = self.sp.current_user_saved_tracks(limit=10)
            liked_str = ", ".join([f"{item['track']['name']} ({item['track']['artists'][0]['name']})" for item in saved['items']])

            self.user_taste_profile = f"""
            - RECENT LIKED SONGS: {liked_str}
            - CURRENT OBSESSIONS: {short_str}
            - USUAL VIBE: {medium_str}
            - ALL TIME FAVORITES: {long_str}
            """
            print("✅ [AI] Profil muzical încărcat cu succes!")
        except Exception as e:
            print(f"⚠️ Nu am putut citi istoricul: {e}")
            self.user_taste_profile = "No history available."

    def pause_playback(self):
        """Pauză temporară pentru a asculta comanda"""
        try:
            dev_id = self._get_device_id()
            if not dev_id: return
            
            # Verificăm dacă chiar cântă ceva
            current = self.sp.current_playback()
            if current and current.get('is_playing'):
                self.sp.pause_playback(device_id=dev_id)
                self.was_playing_before_pause = True # Ținem minte să o repornim
                logging.info("⏸️ Muzică pusă pe pauză pentru a asculta.")
            else:
                self.was_playing_before_pause = False
        except Exception as e:
            logging.error(f"Eroare la pauză Spotify: {e}")

    def resume_playback(self):
        """Reluăm muzica dacă era pornită înainte de pauză"""
        try:
            if hasattr(self, 'was_playing_before_pause') and self.was_playing_before_pause:
                dev_id = self._get_device_id()
                if dev_id:
                    self.sp.start_playback(device_id=dev_id)
                    logging.info("▶️ Muzică reluată.")
                self.was_playing_before_pause = False
        except Exception as e:
            logging.error(f"Eroare la reluare Spotify: {e}")

    def _get_time_context(self):
        hour = datetime.now().hour
        if 5 <= hour < 12: return "MORNING (Wake Up / Energize / Start Day)"
        elif 12 <= hour < 18: return "AFTERNOON (Focus / Vibe / Activity)"
        elif 18 <= hour < 22: return "EVENING (Chill / Social / Pre-Party)"
        else: return "LATE NIGHT (Deep / pshihedelic / Introspective / Bedroom Flow)"

    def _ask_gemini_dj(self, user_text, conversation_history):
        time_context = self._get_time_context()
        current_time = datetime.now().strftime("%H:%M")
        history_str = ", ".join(self.play_history) if self.play_history else "No recent tracks played yet."
        
        system_prompt = f"""
        ROLE: Elite Music Curator & Playback Controller.
        
        CURRENT TIME: {current_time}
        TIME VIBE: {time_context}
        
        RECENT CONVERSATION HISTORY:
        {conversation_history}
        
        USER PROFILE:
        {self.user_taste_profile}

        BANNED TRACKS (RECENTLY PLAYED):
        {history_str}

        GOLDEN RULES:
        {self.strategy}

        CURRENT REQUEST: "{user_text}"

        INSTRUCTIONS:
        1. Determine if the user wants to PLAY new music, or CONTROL the current music.
        2. If they want to pause ("pune pauză", "oprește", "taci"), return mode "pause".
        3. If they want to skip ("next", "următoarea", "alta"), return mode "next".
        4. If they want to resume ("dă-i drumul", "continuă", "play"), return mode "resume".
        5. Otherwise, pick a real song/playlist that fits the mood.
        """
        
        dj_schema = {
            "type": "OBJECT",
            "properties": {
                "mode": {"type": "STRING", "enum": ["playlist", "track", "pause", "resume", "next"]},
                "query": {"type": "STRING", "description": "Search Term (Leave empty if mode is pause, resume, or next)"},
                "reason": {"type": "STRING", "description": "Explain the decision in Romanian"}
            },
            "required": ["mode", "reason"]
        }

        # BUMP LA GEMINI 2.5 FLASH PENTRU GUSTURI MAI BUNE (Mijlocul perfect)
        return ask_gemini_json(system_prompt, schema=dj_schema, temperature=0.2, model="gemini-2.5-flash")

    def process_command(self, user_text, conversation_history=""):
        decision = self._ask_gemini_dj(user_text, conversation_history)
        if not decision: return

        mode = decision.get('mode')
        query = decision.get('query', '')
        reason = decision.get('reason')
        
        print(f"\n🧠 RAȚIONAMENT AI (2.5 Flash): {reason}")
        print(f"🤖 ACȚIUNE [{mode.upper()}]: {query if query else 'N/A'}")
        
        dev_id = self._get_device_id()
        if not dev_id: 
            print("❌ Nu găsesc boxa Spotify activă.")
            return
            
        try:
            # --- COMENZI DE CONTROL PLAYBACK ---
            if mode == 'pause':
                self.sp.pause_playback(device_id=dev_id)
                print("⏸️ Playback oprit din voce.")
                # Foarte important: anulăm reluarea automată din main.py
                self.was_playing_before_pause = False 
                return
                
            elif mode == 'next':
                self.sp.next_track(device_id=dev_id)
                print("⏭️ Piesa următoare.")
                # Dacă dai next, vrei să și cânte
                self.was_playing_before_pause = True 
                return
                
            elif mode == 'resume':
                self.sp.start_playback(device_id=dev_id)
                print("▶️ Playback reluat.")
                self.was_playing_before_pause = False
                return

            # --- COMENZI PENTRU PIESE / PLAYLISTURI NOI ---
            if mode == 'playlist':
                results = self.sp.search(q=query, type='playlist', limit=1, market='US')
                if results and results['playlists']['items']:
                    playlist = results['playlists']['items'][0]
                    print(f"▶️ Pornesc Playlist: {playlist['name']}")
                    self.sp.shuffle(True, device_id=dev_id)
                    self.sp.start_playback(device_id=dev_id, context_uri=playlist['uri'])
                    print("✅ Playlist pornit.")
                    self.was_playing_before_pause = False # Evităm resume-ul dublu
                    self._add_to_history(f"Playlist: {playlist['name']}")
                else: print("❌ Nu am găsit playlist.")

            elif mode == 'track':
                results = self.sp.search(q=query, type='track', limit=1, market='US')
                if results and results['tracks']['items']:
                    track = results['tracks']['items'][0]
                    track_fullname = f"{track['name']} - {track['artists'][0]['name']}"
                    print(f"➕ Adaug la Queue: {track_fullname}")
                    
                    self.sp.add_to_queue(track['uri'], device_id=dev_id)
                    time.sleep(0.5) 
                    self.sp.next_track(device_id=dev_id)
                    print(f"✅ Piesa a intrat! Enjoy.")
                    self.was_playing_before_pause = False # Evităm resume-ul dublu
                    self._add_to_history(track_fullname)
                else: print("❌ Nu am găsit piesa.")

        except Exception as e:
            print(f"❌ Error Spotify: {e}")
            if DEBUG_MODE: traceback.print_exc()

if __name__ == "__main__":
    dj = MusicHandler()
    while True:
        try:
            txt = input("\n🎧 Comandă: ")
            if txt.lower() in ["exit", "stop"]: break
            dj.process_command(txt)
        except KeyboardInterrupt: break