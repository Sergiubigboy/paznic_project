import spotipy
from spotipy.oauth2 import SpotifyOAuth
import logging
import requests
import json
import os
import traceback
import time
from datetime import datetime
from config import (
    SPOTIFY_CLIENT_ID, 
    SPOTIFY_CLIENT_SECRET, 
    SPOTIFY_REDIRECT_URI, 
    SPOTIFY_DEVICE_NAME,
    GEMINI_API_KEY
)

# --- CONFIGURARE ---
DEBUG_MODE = True
STRATEGY_FILE = "dj_strategy.txt"
HISTORY_FILE = "dj_history.json" # FIȘIER NOU PENTRU ISTORIC
# Scope extins pentru a citi istoricul și melodiile salvate (Liked Songs)
SCOPE = "user-read-playback-state user-modify-playback-state user-read-private user-top-read user-library-read"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class MusicHandler:
    def __init__(self):
        self.sp = None
        self.gemini_key = GEMINI_API_KEY
        self.strategy = self._load_text(STRATEGY_FILE)
        self.user_taste_profile = "" 
        self.play_history = self._load_history() # ÎNCĂRCĂM ISTORICUL
        
        try:
            self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=SCOPE,
                open_browser=True
            ))
            print(f"✅ [DEBUG] Spotify Conectat.")
            
            # POPULAM PROFILUL UTILIZATORULUI LA PORNIRE
            self._analyze_user_taste()
            
        except Exception as e:
            print(f"❌ [CRITICAL] Eroare Auth Spotify: {e}")

    def _load_text(self, filename):
        if not os.path.exists(filename): return ""
        with open(filename, "r", encoding="utf-8") as f: return f.read()

    def _save_text(self, filename, text):
        with open(filename, "w", encoding="utf-8") as f: f.write(text)
        self.strategy = text

    # --- MANAGEMENT ISTORIC PIESE (EVITĂ REPETIȚIA) ---
    def _load_history(self):
        if not os.path.exists(HISTORY_FILE): return []
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    def _add_to_history(self, track_info):
        self.play_history.append(track_info)
        # Păstrăm doar ultimele 10 piese
        if len(self.play_history) > 10:
            self.play_history.pop(0)
        
        # Salvăm în fișier
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

    # --- NOUL MOTOR DE ANALIZĂ A GUSTURILOR ---
    def _analyze_user_taste(self):
        print("🧠 [AI] Analizez istoricul tău muzical (Recent, Mediu, All-Time, Liked Songs)...")
        try:
            # 1. Short Term (Ultima luna)
            short = self.sp.current_user_top_tracks(limit=5, time_range='short_term')
            short_str = ", ".join([f"{t['name']} ({t['artists'][0]['name']})" for t in short['items']])
            
            # 2. Medium Term (Ultimele 6 luni)
            medium = self.sp.current_user_top_tracks(limit=5, time_range='medium_term')
            medium_str = ", ".join([f"{t['name']} ({t['artists'][0]['name']})" for t in medium['items']])
            
            # 3. Long Term (All time)
            long = self.sp.current_user_top_tracks(limit=5, time_range='long_term')
            long_str = ", ".join([f"{t['name']} ({t['artists'][0]['name']})" for t in long['items']])

            # 4. LIKED SONGS (Cele mai noi 10 melodii salvate)
            saved = self.sp.current_user_saved_tracks(limit=10)
            liked_str = ", ".join([f"{item['track']['name']} ({item['track']['artists'][0]['name']})" for item in saved['items']])

            self.user_taste_profile = f"""
            - RECENT LIKED SONGS (Use as strong reference): {liked_str}
            - CURRENT OBSESSIONS (Last 4 weeks): {short_str}
            - USUAL VIBE (Last 6 months): {medium_str}
            - ALL TIME FAVORITES: {long_str}
            """
            print("✅ [AI] Profil muzical încărcat cu succes (inclusiv Liked Songs)!")
            
        except Exception as e:
            print(f"⚠️ Nu am putut citi istoricul: {e}")
            self.user_taste_profile = "No history available."

    # --- CALCULARE CONTEXT TEMPORAL ---
    def _get_time_context(self):
        now = datetime.now()
        hour = now.hour
        
        if 5 <= hour < 12:
            return "MORNING (Wake Up / Energize / Start Day)"
        elif 12 <= hour < 18:
            return "AFTERNOON (Focus / Vibe / Activity)"
        elif 18 <= hour < 22:
            return "EVENING (Chill / Social / Pre-Party)"
        else:
            return "LATE NIGHT (Deep / pshihedelic / Introspective / Bedroom Flow)"

    # --- GEMINI: REVENIRE LA 2.0 FLASH + TEMPERATURE STRICTĂ (0.2) ---
    def _ask_gemini_dj(self, user_text):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_key}"
        headers = {'Content-Type': 'application/json'}
        
        time_context = self._get_time_context()
        current_time = datetime.now().strftime("%H:%M")
        
        # Formatăm istoricul recent pentru prompt
        history_str = ", ".join(self.play_history) if self.play_history else "No recent tracks played yet."
        
        system_prompt = f"""
        ROLE: Elite Music Curator (Strict Fact-Checker & Taste Expert).
        
        --- REAL-TIME CONTEXT ---
        CURRENT TIME: {current_time}
        TIME VIBE: {time_context}
        
        --- USER PROFILE (DATA FROM SPOTIFY) ---
        {self.user_taste_profile}

        --- RECENTLY PLAYED BY YOU (CRITICAL: DO NOT REPEAT THESE) ---
        {history_str}

        --- GOLDEN RULES (USER MANIFESTO) ---
        1. **HATE**: Generic Radio Pop, Top 50 Global, "Happy-Clappy" music, Cheap EDM, Childish sounds, nights-Frank Ocean.
        2. **LOVE**: Hip-Hop, Rap, Trap, R&B, Classic Rock (Pink Floyd vibe).
        3. **SOUND**: Heavy Bass, Atmospheric, Dark, Cinematic.
        4. **CONTEXT - "ROMANTIC"**: Means Drake, Future, Frank Ocean type "Toxic/Love Songs". NOT pop ballads.
        5. **CONTEXT - "NIGHT/BED"**: If Time is LATE NIGHT -> Continuous flow, deep vibes, NO sudden volume spikes.
        6. **CONTEXT - "EGO BOOST"**: Attitude, Swagger, Money, Power, Aggressive but smooth.
        7. **HIDDEN GEMS**: Try to find the "hidden gems" in the artists I like. Don't just pick their hits.

        --- CURRENT REQUEST ---
        "{user_text}"

        --- INSTRUCTIONS ---
        1. **ANALYZE INTENT**:
           - **Time-Based**: If user says "play something", look at TIME VIBE.
           - **Explicit Genre**: If user asks for "Pop", give them GOOD Pop (The Weeknd, MJ), not generic trash.
        
        2. **DECIDE MODE**:
           - **"playlist"**: ONLY if user specifically asks for a "playlist", "mix", "collection".
           - **"track"**: Default. Pick ONE specific song.
        
        3. **ANTI-HALLUCINATION & ANTI-REPETITION (CRITICAL)**:
           - You MUST pick a REAL song that exists on Spotify.
           - **DO NOT** pick any song listed in the "RECENTLY PLAYED BY YOU" section above.
           - If unsure about a niche remix, pick the original or a guaranteed hit.
        
        4. **FORMAT (JSON ONLY)**:
        {{
            "mode": "playlist" | "track",
            "query": "Exact Search Term (Artist - Song) OR (Playlist Name)",
            "reason": "Explain how this fits the GOLDEN RULES, TIME ({current_time}), and REQUEST. Mention that you avoided recently played tracks."
        }}
        """
        
        # CONFIGURARE STRICTĂ (TEMPERATURA 0.2)
        payload = {
            "contents": [{"parts": [{"text": system_prompt}]}],
            "generationConfig": {
                "temperature": 0.2,       # SECRETUL ANTI-HALUCINATII
                "topK": 40,
                "topP": 0.95,
                "response_mime_type": "application/json"
            }
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code != 200: 
                print(f"❌ API Error: {response.text}")
                return None
            
            clean_json = response.json()['candidates'][0]['content']['parts'][0]['text'].replace("```json", "").replace("```", "").strip()
            return json.loads(clean_json)
        except Exception as e:
            if DEBUG_MODE: print(f"❌ [DEBUG] Eroare Gemini: {e}")
            return None

    def process_command(self, user_text):
        decision = self._ask_gemini_dj(user_text)
        if not decision: return

        mode = decision.get('mode')
        query = decision.get('query')
        reason = decision.get('reason')
        
        print(f"\n🧠 RAȚIONAMENT AI: {reason}")
        print(f"🤖 ACȚIUNE [{mode.upper()}]: {query}")
        
        dev_id = self._get_device_id()
        if not dev_id: 
            print("❌ Nu găsesc boxa.")
            return

        try:
            # --- MOD 1: PLAYLIST (Shuffle + Play) ---
            if mode == 'playlist':
                results = self.sp.search(q=query, type='playlist', limit=1, market='US')
                
                if results and results['playlists']['items']:
                    playlist = results['playlists']['items'][0]
                    print(f"▶️ Pornesc Playlist: {playlist['name']}")
                    self.sp.shuffle(True, device_id=dev_id)
                    self.sp.start_playback(device_id=dev_id, context_uri=playlist['uri'])
                    print("✅ Playlist pornit.")
                    # Salvăm intenția de playlist în istoric (opțional)
                    self._add_to_history(f"Playlist: {playlist['name']}")
                else:
                    print("❌ Nu am găsit playlist.")

            # --- MOD 2: TRACK (Queue + Skip) ---
            else:
                # Căutăm cu market US pentru precizie maximă
                results = self.sp.search(q=query, type='track', limit=1, market='US')
                
                if results and results['tracks']['items']:
                    track = results['tracks']['items'][0]
                    track_fullname = f"{track['name']} - {track['artists'][0]['name']}"
                    print(f"➕ Adaug la Queue: {track_fullname}")
                    
                    self.sp.add_to_queue(track['uri'], device_id=dev_id)
                    time.sleep(0.5) 
                    self.sp.next_track(device_id=dev_id)
                    
                    print(f"✅ Piesa a intrat! Enjoy.")
                    
                    # SALVĂM PIESA ÎN ISTORIC CA SĂ NU O MAI REPETE
                    self._add_to_history(track_fullname)
                else:
                    print("❌ Nu am găsit piesa.")

        except Exception as e:
            print(f"❌ Error: {e}")
            if DEBUG_MODE: traceback.print_exc()

if __name__ == "__main__":
    dj = MusicHandler()
    while True:
        try:
            txt = input("\n🎧 Comandă: ")
            if txt.lower() in ["exit", "stop"]: break
            dj.process_command(txt)
        except KeyboardInterrupt: break