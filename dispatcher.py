import os
import time
import logging

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

    def classify_intent_with_gemini(self, transcription, conversation_history):
        logging.info(f"🧠 Dispatcher: Analizez intenția...")
        
        prompt_text = f"""
        Ești Dispecerul Asistentului Inteligent al camerei.
        Rutează comanda vocală a utilizatorului.
        
        ISTORIC RECENT:
        {conversation_history}
        
        COMANDĂ: "{transcription}"

        Logica de clasificare:
        - "led": lumini, culori, vizual.
        - "music": melodii, play, stop, volum, "pune muzică".
        - "journal": VREA SĂ SCRIE/ÎNREGISTREZE ceva nou în jurnal.
        - "target": Vrea să adauge un task.
        - "general": Vreme, discuții, întrebări despre memorie/trecut.
        """

        intent_schema = {
            "type": "OBJECT",
            "properties": {
                "intent": {
                    "type": "STRING", 
                    "enum": ["led", "music", "general", "journal", "target", "unknown"],
                },
                "reasoning": {"type": "STRING"}
            },
            "required": ["intent", "reasoning"]
        }

        return ask_gemini_json(prompt_text, schema=intent_schema, temperature=0.1)

    def handle_general_chat(self, user_text, short_term_history, long_term_context):
        logging.info("🧠 Generez răspuns bazat pe memorie...")
        
        prompt = f"""
        ROL: Ești Chronos, asistent AI. Răspunzi STRICT în română.
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

    def process_text_command(self, text, sock):
        """Procesează textul transcris de main.py"""
        if not text: return

        current_time = time.time()
        self.conversation_history = [msg for msg in self.conversation_history if current_time - msg[0] <= 3600]
        history_str = "\n".join([msg[1] for msg in self.conversation_history]) if self.conversation_history else "No previous context."
        
        intent = self.classify_intent_with_gemini(text, history_str)
        self.conversation_history.append((current_time, f"User: {text}"))
        
        should_restore_lights = True

        if intent and isinstance(intent, dict):
            actiune = intent.get("intent", "unknown")
            logging.info(f"📋 Intenție rutată: {actiune} | Motiv: {intent.get('reasoning')}")
            
            if actiune == "journal":
                self.jural_expert.start_journal_session(sock)
            elif actiune == "target":
                self.jural_expert.start_target_session(sock)
            else:
                action_taken = False
                if actiune == "led":
                    self.wled_expert.execute(text, history_str)
                    should_restore_lights = False 
                    action_taken = True
                    
                if actiune == "music":
                    self.music_expert.process_command(text, history_str)
                    action_taken = True
                
                if actiune == "general" and not action_taken:
                    past_context = self.memory_manager.query_memory(text, n_results=5)
                    response = self.handle_general_chat(text, history_str, past_context)
                    if response:
                        reply_text = response.get("response_text", "")
                        print(f"\n🤖 Chronos: {reply_text}\n")
                        self.conversation_history.append((time.time(), f"Chronos: {reply_text}"))
        else:
            logging.error("Eroare la parsarea intenției.")

        return should_restore_lights