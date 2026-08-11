"""
tools/memory_tools.py — Vector Memory & Journal Database Tools
================================================================
Funcții directe pentru salvarea și interogarea datelor în ChromaDB & Fișiere Jurnal.
"""

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
TARGETS_FILE = os.path.join(DATA_DIR, "targets.json")

os.makedirs(LOGS_DIR, exist_ok=True)


def save_journal_text(entry_text: str) -> dict:
    """Salvează o notă în fișierul jurnal al zilei curente."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(LOGS_DIR, f"{today}.txt")

    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted_entry = f"\n[{timestamp}] {entry_text.strip()}\n"

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(formatted_entry)
        logger.info(f"📘 [Memory Tools] Salvat în jurnal {log_path}: '{entry_text[:40]}...'")
        return {"status": "ok", "file": log_path, "entry": entry_text}
    except Exception as e:
        logger.error(f"❌ [Memory Tools] Eroare salvare jurnal: {e}")
        return {"status": "error", "message": str(e)}


def add_user_target(target_title: str) -> dict:
    """Adaugă un obiectiv / target personal în fișierul de scopuri."""
    try:
        if not os.path.exists(TARGETS_FILE):
            data = {"goals": []}
        else:
            with open(TARGETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

        new_target = {
            "id": str(int(datetime.now().timestamp() * 1000)),
            "title": target_title.strip(),
            "progress": 0,
            "created_at": datetime.now().isoformat()
        }
        data.setdefault("goals", []).append(new_target)

        with open(TARGETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        logger.info(f"🎯 [Memory Tools] Target salvat: '{target_title}'")
        return {"status": "ok", "target": new_target}
    except Exception as e:
        logger.error(f"❌ [Memory Tools] Eroare salvare target: {e}")
        return {"status": "error", "message": str(e)}
