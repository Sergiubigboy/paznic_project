import os
import time
import logging
from typing import Optional

# --- IMPORTURI MODULE SPECIALISTE ---
from wled_specialist import WLEDDispatcher, WLEDStateManager
from music_specialist import MusicHandler
from logger_specialist import JournalCore, MemoryManager
from ai_core import ask_gemini_json

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class CommandDispatcher:
    def __init__(self, music_expert, wled_mechanic):
        # Primim instanțele gata făcute din main, să nu le instanțiem de 2 ori
        self.wled_expert = WLEDDispatcher()
        self.wled_mechanic = wled_mechanic
        self.music_expert = music_expert
        self.jural_expert = JournalCore(self.wled_mechanic)
        self.memory_manager = MemoryManager()
        self.conversation_history = []
        self.last_result = {}

    def classify_intent_with_gemini(self, transcription, conversation_history):
        logging.info(f"🧠 Dispatcher: Analizez intenția...")

        prompt_text = f"""
        Ești Dispecerul Asistentului Inteligent al camerei.
        Rutează comanda vocală a utilizatorului.
        O comandă poate conține MAI MULTE intenții simultan (ex: "schimbă piesa și luminile" = ["music", "led"]).

        ISTORIC RECENT:
        {conversation_history}

        COMANDĂ: "{transcription}"

        Logica de clasificare:
        - "led": lumini, culori, vizual, schimbă ledurile.
        - "music": melodii, play, stop, volum, "pune muzică", schimbă piesa.
        - "journal": VREA SĂ SCRIE/ÎNREGISTREZE ceva nou în jurnal.
        - "target": Vrea să adauge un task/obiectiv.
        - "general": Vreme, discuții, întrebări despre memorie/trecut.

        Returnează TOATE intențiile detectate în comandă ca o listă (de obicei una, dar pot fi mai multe).
        """

        intent_schema = {
            "type": "OBJECT",
            "properties": {
                "intents": {
                    "type": "ARRAY",
                    "items": {
                        "type": "STRING",
                        "enum": ["led", "music", "general", "journal", "target", "unknown"]
                    }
                },
                "reasoning": {"type": "STRING"}
            },
            "required": ["intents", "reasoning"]
        }

        return ask_gemini_json(prompt_text, schema=intent_schema, temperature=0.1)

    def extract_memory_parameters(self, text):
            from datetime import datetime
            azi = datetime.now()
            
            prompt = f"""
            Suntem în data de: {azi.strftime('%Y-%m-%d')}
            Comanda utilizatorului: "{text}"
            
            Sarcina ta:
            1. Extrage intervalul de timp (start_date și end_date în format YYYY-MM-DD). Dacă se cere "săptămâna asta", calculează datele. Dacă nu se specifică timpul, setează has_time_filter pe false.
            2. Generează o listă de 3-5 fraze/concepte cheie (search_queries) pentru a căuta în jurnal. Fii creativ! 
            Exemplu: Dacă userul întreabă de "productivitate", generează ["productivitate", "am lucrat mult", "taskuri", "progres", "lene", "oboseală", "succes"].
            Folosește cuvinte naturale pe care userul le-ar scrie în jurnalul său zilnic.
            """
            
            schema = {
                "type": "OBJECT",
                "properties": {
                    "has_time_filter": {"type": "BOOLEAN"},
                    "start_date": {"type": "STRING"},
                    "end_date": {"type": "STRING"},
                    "search_queries": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"}
                    }
                },
                "required": ["has_time_filter", "search_queries"]
            }
            
            return ask_gemini_json(prompt, schema=schema, temperature=0.3)

    def handle_general_chat(self, user_text, short_term_history, long_term_context):
        from datetime import datetime # <-- Am adăugat importul necesar
        logging.info("🧠 Generez răspuns bazat pe memorie...")
        
        # Aflăm data și ora curentă pentru a i le da lui Gemini
        azi = datetime.now().strftime("%A, %d %B %Y, ora %H:%M")

        prompt = f"""
        ROL: Ești Chronos, asistent AI. Răspunzi STRICT în română.
        DATA ȘI ORA CURENTĂ: {azi} 
        
        REGULĂ CRITICĂ PENTRU TIMP: Dacă utilizatorul întreabă de "azi", "ieri", "săptămâna asta", compară neapărat datele din memoria pe termen lung cu DATA CURENTĂ. Dacă amintirile furnizate sunt prea vechi, spune clar că nu ai informații salvate despre perioada recentă cerută, dar poți discuta despre datele mai vechi pe care le ai.

        ISTORIC SCURT: {short_term_history}
        MEMORIE PE TERMEN LUNG: {long_term_context}
        COMANDĂ/ÎNTREBARE: "{user_text}"
        """

        schema = {
            "type": "OBJECT",
            "properties": {
                "response_text": {"type": "STRING"},
                "emotion": {"type": "STRING", "enum": ["neutral", "happy", "serious", "empathetic"]}
            },
            "required": ["response_text", "emotion"]
        }

        return ask_gemini_json(prompt, schema=schema, temperature=0.7)

    def extract_time_filter(self, text):
        """Extrage un filtru de timp (ChromaDB) din comanda utilizatorului."""
        from datetime import datetime, timedelta
        azi = datetime.now()
        
        prompt = f"""
        Suntem în data de: {azi.strftime('%Y-%m-%d')}
        Comanda utilizatorului este: "{text}"
        
        Identifică dacă utilizatorul a cerut un interval de timp (ex: "azi", "ieri", "săptămâna asta", "luna trecută").
        Dacă da, calculează 'start_date' și 'end_date' în format 'YYYY-MM-DD'.
        Dacă nu se specifică timpul (ex: "ce am făcut în general?"), returnează null.
        """
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "has_time_filter": {"type": "BOOLEAN"},
                "start_date": {"type": "STRING"}, # Format YYYY-MM-DD
                "end_date": {"type": "STRING"}    # Format YYYY-MM-DD
            },
            "required": ["has_time_filter"]
        }
        
        return ask_gemini_json(prompt, schema=schema, temperature=0.1)

    def process_text_command(self, text, sock):
        if not text:
            return True

        current_time = time.time()
        self.conversation_history = [msg for msg in self.conversation_history if current_time - msg[0] <= 3600]
        history_str = "\n".join([msg[1] for msg in self.conversation_history]) if self.conversation_history else "No previous context."

        intent_result = self.classify_intent_with_gemini(text, history_str)
        self.conversation_history.append((current_time, f"User: {text}"))

        should_restore_lights = True
        reply_text = None
        actions_list = []

        if not intent_result or not isinstance(intent_result, dict):
            logging.error("Eroare la parsarea intenției.")
            self.last_result = {"intents": [], "reply": "Eroare AI.", "actions": [], "reasoning": ""}
            return should_restore_lights

        actiuni = intent_result.get("intents") or [intent_result.get("intent", "unknown")]
        reasoning = intent_result.get("reasoning", "")

        for actiune in actiuni:
            if actiune == "journal":
                if sock is not None:
                    self.jural_expert.start_journal_session(sock) # Merge vocal
                else:
                    try:
                        self.jural_expert._process_daily_entry(text) # Salvează textul direct de pe web
                        reply_text = "Am notat textul în jurnal."
                        actions_list.append({"text": "📘 Salvat în jurnal.", "status": "ok"})
                    except Exception as e:
                        actions_list.append({"text": f"❌ Eroare jurnal: {e}", "status": "error"})
            
            elif actiune == "target":
                if sock is not None:
                    self.jural_expert.start_target_session(sock) # Merge vocal
                else:
                    try:
                        import json
                        from logger_specialist import TARGETS_FILE
                        from datetime import datetime
                        target = {
                            "id": str(int(time.time() * 1000)),
                            "title": text, "progress": 0, "created_at": datetime.now().isoformat()
                        }
                        with open(TARGETS_FILE, 'r', encoding='utf-8') as f: data = json.load(f)
                        data.setdefault('goals', []).append(target)
                        with open(TARGETS_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
                        reply_text = f"Am adăugat targetul: {text}"
                        actions_list.append({"text": "🎯 Target salvat.", "status": "ok"})
                    except Exception as e:
                        actions_list.append({"text": f"❌ Eroare target: {e}", "status": "error"})
            
            elif actiune == "led":
                self.wled_expert.execute(text, history_str)
                should_restore_lights = False
                actions_list.append({"text": "💡 Comandă WLED trimisă.", "status": "ok"})
            
            elif actiune == "music":
                music_res = self.music_expert.process_command(text, history_str)
                if music_res and isinstance(music_res, dict):
                    msg = music_res.get("msg", "Comandă procesată.")
                    status = music_res.get("status", "ok")
                    reason = music_res.get("reason", "")
                    actions_list.append({"text": f"🎵 {msg}", "status": status})
                    if reason:
                        reply_text = f"DJ Choice: {reason}"
                else:
                    actions_list.append({"text": "🎵 Comandă Spotify trimisă.", "status": "ok"})
            
            elif actiune == "general":
                # 1. Analizăm comanda pentru Timp + Cuvinte multiple (Query Expansion)
                mem_params = self.extract_memory_parameters(text)
                
                chroma_filter = None
                search_queries = [text] # Fallback la textul original în caz de eroare
                
                if mem_params:
                    # Setăm filtrele de timp (dacă există)
                    if mem_params.get("has_time_filter"):
                        start = mem_params.get("start_date")
                        end = mem_params.get("end_date")
                        if start and end:
                            # Notă: Presupunem că în logger salvezi data ca 'logical_date'
                            chroma_filter = {
                                "$and": [
                                    {"logical_date": {"$gte": start}},
                                    {"logical_date": {"$lte": end}}
                                ]
                            }
                            
                    # Extragem cuvintele cheie expandate
                    if mem_params.get("search_queries"):
                        search_queries = mem_params.get("search_queries")

                logging.info(f"🔎 Caut în memorie folosind: {search_queries} | Filtru: {chroma_filter}")

                # 2. Interogăm ChromaDB cu LISTA de cuvinte și filtrul
                past_context = self.memory_manager.query_memory(search_queries, n_results=3, where_filter=chroma_filter)
                
                # 3. Generăm răspunsul final, dându-i și data de azi ca să fie orientat în timp
                from datetime import datetime
                azi_str = datetime.now().strftime("%A, %d %B %Y")
                history_plus_time = f"[Azi e {azi_str}]\n" + history_str
                
                response = self.handle_general_chat(text, history_plus_time, past_context)
                if response:
                    reply_text = response.get("response_text", "")
                    print(f"\n🤖 Chronos: {reply_text}\n")
                    self.conversation_history.append((time.time(), f"Chronos: {reply_text}"))

        # Salvăm rezultatul pentru a fi trimis către Web Terminal
        self.last_result = {
            "intents": actiuni,
            "reply": reply_text,
            "actions": actions_list,
            "reasoning": reasoning
        }
        
        return should_restore_lights