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
        import threading
        self.study_timer_thread: Optional[threading.Thread] = None
        self.study_timer_stop_event = threading.Event()

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
        - "study_timer": Vrea să pornească, să seteze sau să oprească timer-ul pomodoro / de studiu.
        - "hype_mode": Vrea să fie trezit / motivat extrem / petrecere / hype.
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
                        "enum": ["led", "music", "general", "journal", "target", "study_timer", "hype_mode", "unknown"]
                    }
                },
                "reasoning": {"type": "STRING"}
            },
            "required": ["intents", "reasoning"]
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

    def parse_study_timer_command(self, text):
        prompt = f"""
        Analizează comanda utilizatorului legată de timerul de studiu (Pomodoro).
        Extrage intenția (start sau stop). Dacă e start, extrage minutele de focus și minutele de pauză.
        Dacă nu sunt specificate explicit, folosește valorile implicite: 25 pentru focus, 5 pentru pauză.

        COMANDĂ: "{text}"
        """
        schema = {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "enum": ["start", "stop"]},
                "focus_minutes": {"type": "INTEGER"},
                "break_minutes": {"type": "INTEGER"}
            },
            "required": ["action", "focus_minutes", "break_minutes"]
        }
        return ask_gemini_json(prompt, schema=schema, temperature=0.1)

    def handle_study_timer(self, text):
        import threading
        timer_info = self.parse_study_timer_command(text)
        action = timer_info.get("action", "start")

        if action == "stop":
            if self.study_timer_thread and self.study_timer_thread.is_alive():
                self.study_timer_stop_event.set()
                print("\n🤖 Chronos: Timer de studiu oprit. Spor în continuare!\n")
            else:
                print("\n🤖 Chronos: Nu există niciun timer de studiu activ momentan.\n")
            return

        focus_min = timer_info.get("focus_minutes", 25)
        break_min = timer_info.get("break_minutes", 5)

        if self.study_timer_thread and self.study_timer_thread.is_alive():
            self.study_timer_stop_event.set()
            self.study_timer_thread.join(timeout=1.0)

        self.study_timer_stop_event.clear()

        def timer_thread(stop_event, focus_m, break_m):
            logging.info(f"⏳ Pomodoro: Start {focus_m} minute FOCUS!")
            print(f"\n🤖 Chronos: Timer pornit! {focus_m} de minute de focus maxim.\n")
            self.wled_mechanic.pulse_color([0, 255, 0], duration=4)

            if stop_event.wait(focus_m * 60):
                return

            logging.info(f"⏳ Pomodoro: Pauză {break_m} minute!")
            print(f"\n🤖 Chronos: Pauză! Relaxează-te {break_m} minute.\n")
            self.wled_mechanic.pulse_color([0, 150, 255], duration=4)

            if stop_event.wait(break_m * 60):
                return

            logging.info("⏳ Pomodoro: Final.")
            print("\n🤖 Chronos: Sesiunea de studiu s-a terminat.\n")
            self.wled_mechanic.pulse_color([255, 100, 0], duration=4)

        self.study_timer_thread = threading.Thread(
            target=timer_thread,
            args=(self.study_timer_stop_event, focus_min, break_min),
            daemon=True
        )
        self.study_timer_thread.start()

    def start_hype_mode(self):
        logging.info("🔥 HYPE MODE ACTIVAT!")
        self.wled_mechanic.trigger_hype_mode()
        print("\n🤖 Chronos: AM BĂGAT HYPE MODE! Haidee!\n")
        self.music_expert.process_command("baga muzica super hype rapida motivanta bass boosted pt petrecere", "")

    def process_text_command(self, text, sock):
        """Procesează textul transcris de main.py"""
        if not text:
            return True

        current_time = time.time()
        self.conversation_history = [msg for msg in self.conversation_history if current_time - msg[0] <= 3600]
        history_str = "\n".join([msg[1] for msg in self.conversation_history]) if self.conversation_history else "No previous context."

        intent_result = self.classify_intent_with_gemini(text, history_str)
        self.conversation_history.append((current_time, f"User: {text}"))

        should_restore_lights = True

        if not intent_result or not isinstance(intent_result, dict):
            logging.error("Eroare la parsarea intenției.")
            return should_restore_lights

        # Suportă atât formatul nou (intents=[...]) cât și cel vechi (intent="...")
        actiuni = intent_result.get("intents") or [intent_result.get("intent", "unknown")]
        logging.info(f"📋 Intenții rutate: {actiuni} | Motiv: {intent_result.get('reasoning')}")

        for actiune in actiuni:
            if actiune == "journal":
                self.jural_expert.start_journal_session(sock)
            elif actiune == "target":
                self.jural_expert.start_target_session(sock)
            elif actiune == "study_timer":
                self.handle_study_timer(text)
                should_restore_lights = False
            elif actiune == "hype_mode":
                self.start_hype_mode()
                should_restore_lights = False
            elif actiune == "led":
                self.wled_expert.execute(text, history_str)
                should_restore_lights = False
            elif actiune == "music":
                self.music_expert.process_command(text, history_str)
            elif actiune == "general":
                past_context = self.memory_manager.query_memory(text, n_results=5)
                response = self.handle_general_chat(text, history_str, past_context)
                if response:
                    reply_text = response.get("response_text", "")
                    print(f"\n🤖 Chronos: {reply_text}\n")
                    self.conversation_history.append((time.time(), f"Chronos: {reply_text}"))

        return should_restore_lights