import os
import json
import glob
import sys
import uuid
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, Response, render_template, jsonify, send_from_directory
from werkzeug.utils import secure_filename

# --- CONFIGURARE SECURITATE ---
USERNAME = "admin"
PASSWORD = "123"  # SCHIMBĂ ASTA

# --- CONFIGURARE CĂI ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
LOGS_DIR = os.path.join(BASE_DIR, "chronos_data", "logs")
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
TARGETS_FILE = os.path.join(DATA_DIR, "targets.json")
COMPLETED_FILE = os.path.join(DATA_DIR, "archive", "completed_goals.json")

# --- GYM DATA ---
GYM_DIR = os.path.join(DATA_DIR, "gym")
MEASUREMENTS_FILE = os.path.join(GYM_DIR, "measurements.json")
WEIGHT_FILE = os.path.join(GYM_DIR, "weight_log.json")
GYM_PROFILE_FILE = os.path.join(GYM_DIR, "profile.json")
PHASE_FILE = os.path.join(GYM_DIR, "phase.json")
DAILY_CHECKS_FILE = os.path.join(GYM_DIR, "daily_checks.json")
GYM_PHOTOS_DIR = os.path.join(GYM_DIR, "photos")
AESTHETIC_PHOTOS_DIR = os.path.join(GYM_DIR, "aesthetic")

# --- JOURNAL PHOTOS ---
JOURNAL_PHOTOS_DIR = os.path.join(DATA_DIR, "journal_photos")

# --- SCREEN TIME ---
SCREEN_TIME_FILE = os.path.join(DATA_DIR, "screen_time.json")

# --- DAY SCHEDULE ---
DAY_SCHEDULE_FILE = os.path.join(DATA_DIR, "day_schedule.json")

# --- AUTH TRUST DEVICE ---
TRUSTED_DEVICES_FILE = os.path.join(DATA_DIR, "trusted_devices.json")

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'}

sys.path.append(BASE_DIR)

from logger_specialist import JournalCore
from wled_specialist import WLEDStateManager

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# Lazy-loaded journal core
_journal_core = None

def get_journal():
    global _journal_core
    if _journal_core is None:
        wled = WLEDStateManager()
        _journal_core = JournalCore(wled)
    return _journal_core

# --- ENSURE DIRS EXIST ---
os.makedirs(GYM_PHOTOS_DIR, exist_ok=True)
os.makedirs(AESTHETIC_PHOTOS_DIR, exist_ok=True)
os.makedirs(JOURNAL_PHOTOS_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "archive"), exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- TRUSTED DEVICES HELPERS ---
def load_trusted_devices():
    if os.path.exists(TRUSTED_DEVICES_FILE):
        with open(TRUSTED_DEVICES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"devices": []}

def save_trusted_devices(data):
    with open(TRUSTED_DEVICES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def is_trusted_device(token):
    if not token:
        return False
    data = load_trusted_devices()
    for dev in data.get("devices", []):
        if dev.get("token") == token:
            # Update last_used
            dev["last_used"] = datetime.now().isoformat()
            save_trusted_devices(data)
            return True
    return False

# --- AUTH SISTEM ---
def check_auth(username, password):
    return username == USERNAME and password == PASSWORD

def authenticate():
    return Response(
        'Acces interzis. Te rog să te autentifici.\n', 401,
        {'WWW-Authenticate': 'Basic realm="Chronos Core Login"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check device trust token first
        device_token = request.headers.get('X-Device-Token') or request.cookies.get('device_token')
        if device_token and is_trusted_device(device_token):
            return f(*args, **kwargs)
        # Fall back to HTTP Basic
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- GYM HELPERS ---
def load_json_file(filepath, default):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def save_json_file(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- HELPERS LOGS ---
def get_all_logs():
    logs = []
    if not os.path.exists(LOGS_DIR):
        return logs

    for file_path in glob.glob(os.path.join(LOGS_DIR, "*.jsonl")):
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        logs.append(json.loads(line))
                    except: pass

    grouped_logs = defaultdict(list)
    for log in logs:
        try:
            if log.get("type") in ["daily_entry", "daily_summary"]:
                if "logical_date" in log:
                    day_string = log["logical_date"]
                else:
                    date_obj = datetime.fromisoformat(log['timestamp'])
                    shifted = date_obj - timedelta(hours=5)
                    day_string = shifted.strftime("%Y-%m-%d")
                log['display_time'] = datetime.fromisoformat(log['timestamp']).strftime("%H:%M")
                grouped_logs[day_string].append(log)
        except: continue

    # Attach journal photos to each day
    journal_photos = {}
    if os.path.exists(JOURNAL_PHOTOS_DIR):
        for fname in os.listdir(JOURNAL_PHOTOS_DIR):
            if allowed_file(fname):
                parts = fname.split('_', 1)
                if len(parts) >= 1:
                    date_part = parts[0]
                    if date_part not in journal_photos:
                        journal_photos[date_part] = []
                    journal_photos[date_part].append(fname)

    sorted_days = sorted(grouped_logs.keys(), reverse=True)
    result = []
    for day in sorted_days:
        day_logs = grouped_logs[day]
        day_logs.sort(key=lambda x: (0 if x.get("type") == "daily_summary" else 1, x['timestamp']))
        result.append({
            "date": day,
            "logs": day_logs,
            "journal_photos": journal_photos.get(day, [])
        })
    return result

def _get_log_file_for_date(date_str):
    year, month, _ = date_str.split('-')
    return os.path.join(LOGS_DIR, f"log_{year}_{month}.jsonl")

from flask import redirect, url_for as flask_url_for

# ============ HTML ROUTES ============
@app.route('/')
@requires_auth
def index():
    return render_template('index.html', active_page='journal')

@app.route('/journal')
@requires_auth
def journal_alt():
    return redirect('/')

@app.route('/targets')
@requires_auth
def targets():
    return render_template('targets.html', active_page='targets')

@app.route('/gym')
@requires_auth
def gym():
    return render_template('gym.html', active_page='gym')

@app.route('/day')
@requires_auth
def day_page():
    return render_template('day.html', active_page='day')

@app.route('/terminal')
@requires_auth
def terminal():
    return render_template('terminal.html', active_page='terminal')

# ============ DAY STATUS AGGREGATOR ============
@app.route('/api/day/status', methods=['GET'])
@requires_auth
def day_status():
    """Single endpoint returning full today status for all pages."""
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # --- Weight ---
    weight_data = {"logged": False, "value": None, "trend": None}
    if os.path.exists(WEIGHT_FILE):
        with open(WEIGHT_FILE, 'r', encoding='utf-8') as f:
            weights = json.load(f)
        today_w = next((w for w in reversed(weights) if w.get('date') == today), None)
        if today_w:
            weight_data["logged"] = True
            weight_data["value"] = today_w["weight"]
            yest_w = next((w for w in reversed(weights) if w.get('date') == yesterday), None)
            if yest_w:
                weight_data["trend"] = round(today_w["weight"] - yest_w["weight"], 1)

    # --- Screen time ---
    st_data = {"logged": False, "minutes": None}
    if os.path.exists(SCREEN_TIME_FILE):
        with open(SCREEN_TIME_FILE, 'r', encoding='utf-8') as f:
            st_entries = json.load(f)
        today_st = next((e for e in reversed(st_entries) if e.get('date') == today), None)
        if today_st:
            st_data["logged"] = True
            st_data["minutes"] = today_st["minutes"]

    # --- Food check ---
    food_data = {"logged": False, "level": None}
    if os.path.exists(DAILY_CHECKS_FILE):
        with open(DAILY_CHECKS_FILE, 'r', encoding='utf-8') as f:
            checks = json.load(f)
        today_check = next((c for c in reversed(checks) if c.get('date') == today), None)
        if today_check:
            food_data["logged"] = True
            food_data["level"] = today_check["level"]

    # --- Journal entries today ---
    journal_data = {"entries_today": 0, "entries": [], "last_scores": None}
    try:
        logfile = get_log_file_path(datetime.now().year, datetime.now().month)
        if os.path.exists(logfile):
            with open(logfile, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        entry_date = entry.get("timestamp", "")[:10]
                        if entry_date == today:
                            if entry.get("type") == "daily_summary":
                                a = entry.get("analysis", {})
                                journal_data["last_scores"] = a.get("scores")
                            elif entry.get("raw_text"):
                                journal_data["entries_today"] += 1
                                if len(journal_data["entries"]) < 4:
                                    journal_data["entries"].append({
                                        "time": entry.get("display_time", ""),
                                        "text": (entry.get("raw_text") or "")[:150]
                                    })
                    except:
                        pass
    except:
        pass

    # --- Targets ---
    targets_data = []
    if os.path.exists(TARGETS_FILE):
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            td = json.load(f)
        targets_data = td.get("goals", [])[:5]

    # --- Phase ---
    phase = "sustinere"
    if os.path.exists(PHASE_FILE):
        with open(PHASE_FILE, 'r', encoding='utf-8') as f:
            phase = json.load(f).get("current", "sustinere")

    # --- Measurements due ---
    measurements_due = False
    days_since_meas = None
    if os.path.exists(MEASUREMENTS_FILE):
        with open(MEASUREMENTS_FILE, 'r', encoding='utf-8') as f:
            meas = json.load(f)
        if meas:
            try:
                last_date = datetime.strptime(meas[-1]["date"], "%Y-%m-%d")
                days_since_meas = (datetime.now() - last_date).days
                measurements_due = days_since_meas >= 28
            except:
                pass
        else:
            measurements_due = True
    else:
        measurements_due = True

    # --- Last weight (even if not today) ---
    last_weight_ever = None
    if os.path.exists(WEIGHT_FILE):
        with open(WEIGHT_FILE, 'r', encoding='utf-8') as f:
            wl = json.load(f)
        if wl:
            last_weight_ever = wl[-1].get('weight')

    # --- Schedule today ---
    schedule = []
    if os.path.exists(DAY_SCHEDULE_FILE):
        with open(DAY_SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            sched = json.load(f)
        schedule = sched.get(today, [])

    # --- Recent weight log (last 7) ---
    recent_weights = []
    if os.path.exists(WEIGHT_FILE):
        with open(WEIGHT_FILE, 'r', encoding='utf-8') as f:
            wl = json.load(f)
        recent_weights = list(reversed(wl[-7:])) if wl else []

    return jsonify({
        "date": today,
        "weight": weight_data,
        "screen_time": st_data,
        "food_check": food_data,
        "journal": journal_data,
        "phase": phase,
        "targets": targets_data,
        "measurements_due": measurements_due,
        "days_since_measurements": days_since_meas,
        "last_weight_ever": last_weight_ever,
        "schedule": schedule,
        "recent_weights": recent_weights
    })




# ============ TERMINAL / DISPATCHER API ============
@app.route('/api/terminal/ping', methods=['GET'])
@requires_auth
def terminal_ping():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route('/api/terminal/command', methods=['POST'])
@requires_auth
def terminal_command():
    """Send a text command to the dispatcher and return the result."""
    data = request.json or {}
    text = data.get('text', '').strip()
    if not text:
        return jsonify({"status": "error", "message": "Comandă goală"}), 400

    try:
        # Import dispatcher lazily — it needs the main process context
        # We use a lightweight version: just classify intent + handle general chat
        import sys
        sys.path.insert(0, BASE_DIR)
        from ai_core import ask_gemini_json

        # Classify intent
        intent_schema = {
            "type": "OBJECT",
            "properties": {
                "intents": {
                    "type": "ARRAY",
                    "items": {"type": "STRING", "enum": ["led","music","general","journal","target","study_timer","hype_mode","unknown"]}
                },
                "reasoning": {"type": "STRING"}
            },
            "required": ["intents", "reasoning"]
        }

        prompt = f"""
        Ești Dispecerul Asistentului Inteligent. Clasifică intenția comenzii:
        - "led": lumini, culori
        - "music": melodii, muzică
        - "journal": scrie în jurnal, înregistrează
        - "target": adaugă task/obiectiv
        - "study_timer": timer pomodoro
        - "hype_mode": motivație extremă, petrecere
        - "general": întrebări, conversație

        COMANDĂ: "{text}"
        """

        intent_result = ask_gemini_json(prompt, schema=intent_schema, temperature=0.1)
        intents = intent_result.get('intents', ['general']) if intent_result else ['general']

        actions = []
        reply = None

        for intent in intents:
            if intent == 'general':
                chat_schema = {
                    "type": "OBJECT",
                    "properties": {
                        "response_text": {"type": "STRING"},
                        "emotion": {"type": "STRING"}
                    },
                    "required": ["response_text"]
                }
                chat_prompt = f"""
                Ești Chronos, asistent AI inteligent. Răspunde concis în română.
                COMANDĂ: "{text}"
                """
                chat_res = ask_gemini_json(chat_prompt, schema=chat_schema, temperature=0.7)
                if chat_res:
                    reply = chat_res.get('response_text', '')
            elif intent == 'led':
                actions.append('Comandă LED trimisă (necesită main.py activ cu WLED)')
            elif intent == 'music':
                actions.append('Comandă muzică trimisă (necesită main.py activ)')
            elif intent == 'journal':
                actions.append('Pentru jurnal, folosește pagina Jurnal din web sau microfonul')
            elif intent == 'target':
                actions.append('Pentru targeturi, folosește pagina Targeturi din web')
            elif intent == 'study_timer':
                actions.append('Timer Pomodoro pornit (necesită main.py activ)')
            elif intent == 'hype_mode':
                actions.append('🔥 HYPE MODE (necesită main.py activ cu WLED + muzică)')

        return jsonify({
            "status": "success",
            "intents": intents,
            "reply": reply,
            "actions": actions,
            "reasoning": intent_result.get('reasoning', '') if intent_result else ''
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ============ STATIC MEDIA ============
@app.route('/media/gym/photos/<filename>')
@requires_auth
def serve_gym_photo(filename):
    return send_from_directory(GYM_PHOTOS_DIR, filename)

@app.route('/media/gym/aesthetic/<filename>')
@requires_auth
def serve_aesthetic_photo(filename):
    return send_from_directory(AESTHETIC_PHOTOS_DIR, filename)

@app.route('/media/journal/<filename>')
@requires_auth
def serve_journal_photo(filename):
    return send_from_directory(JOURNAL_PHOTOS_DIR, filename)

# ============ AUTH TRUST DEVICE ============
@app.route('/api/auth/trust-device', methods=['POST'])
def trust_device():
    """Register a new trusted device. Requires initial HTTP Basic auth."""
    auth = request.authorization
    if not auth or not check_auth(auth.username, auth.password):
        return jsonify({"status": "error", "message": "Autentificare necesară"}), 401

    data = request.json or {}
    device_name = data.get('name', 'Device necunoscut')
    token = str(uuid.uuid4())

    devices_data = load_trusted_devices()
    devices_data.setdefault("devices", []).append({
        "token": token,
        "name": device_name,
        "created_at": datetime.now().isoformat(),
        "last_used": datetime.now().isoformat()
    })
    save_trusted_devices(devices_data)
    return jsonify({"status": "success", "token": token})

@app.route('/api/auth/check-token', methods=['GET'])
def check_token():
    device_token = request.headers.get('X-Device-Token') or request.cookies.get('device_token')
    if device_token and is_trusted_device(device_token):
        return jsonify({"status": "trusted"})
    return jsonify({"status": "untrusted"}), 401

@app.route('/api/auth/revoke-device', methods=['POST'])
@requires_auth
def revoke_device():
    data = request.json or {}
    token = data.get('token')
    devices_data = load_trusted_devices()
    devices_data["devices"] = [d for d in devices_data.get("devices", []) if d.get("token") != token]
    save_trusted_devices(devices_data)
    return jsonify({"status": "success"})

@app.route('/api/auth/trusted-devices', methods=['GET'])
@requires_auth
def list_trusted_devices():
    devices_data = load_trusted_devices()
    return jsonify(devices_data.get("devices", []))

# ============ API LOGS ============
@app.route('/api/logs')
@requires_auth
def api_logs():
    return jsonify(get_all_logs())

@app.route('/api/journal/entry', methods=['POST'])
@requires_auth
def add_journal_entry():
    data = request.json
    text = data.get('text', '').strip()
    if not text:
        return jsonify({"status": "error", "message": "Text gol"}), 400

    custom_date = data.get('date', '').strip()

    try:
        dt_now = datetime.now()
        if custom_date:
            logical_date = custom_date
            try:
                day_dt = datetime.strptime(custom_date, "%Y-%m-%d")
                timestamp_dt = day_dt.replace(hour=22, minute=0, second=0)
                timestamp_str = timestamp_dt.isoformat()
            except:
                timestamp_str = dt_now.isoformat()
        else:
            shifted = dt_now - timedelta(hours=5)
            logical_date = shifted.strftime("%Y-%m-%d")
            timestamp_str = dt_now.isoformat()

        entry = {
            "timestamp": timestamp_str,
            "type": "daily_entry",
            "logical_date": logical_date,
            "raw_text": text,
            "source": "web"
        }
        log_file = _get_log_file_for_date(logical_date)
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return jsonify({"status": "success", "logical_date": logical_date})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/journal/rejudge', methods=['POST'])
@requires_auth
def rejudge_entry():
    data = request.json
    logical_date = data.get('date', '').strip()
    if not logical_date:
        return jsonify({"status": "error", "message": "Data lipsă"}), 400
    try:
        journal = get_journal()
        result = journal.rejudge_day(logical_date)
        if result:
            return jsonify({"status": "success", "summary": result})
        else:
            return jsonify({"status": "error", "message": "Nu s-a putut genera judecata"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/journal/update-scores', methods=['POST'])
@requires_auth
def update_journal_scores():
    """Manually update scores in a daily_summary for a specific date."""
    data = request.json
    logical_date = data.get('date', '').strip()
    new_scores = data.get('scores', {})
    if not logical_date or not new_scores:
        return jsonify({"status": "error", "message": "Date lipsă"}), 400

    try:
        log_file = _get_log_file_for_date(logical_date)
        if not os.path.exists(log_file):
            return jsonify({"status": "error", "message": "Nu există logs pentru această zi"}), 404

        lines = []
        updated = False
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        entry = json.loads(line)
                        if (entry.get("type") == "daily_summary" and
                                entry.get("logical_date") == logical_date):
                            if "analysis" not in entry:
                                entry["analysis"] = {}
                            if "scores" not in entry["analysis"]:
                                entry["analysis"]["scores"] = {}
                            for k, v in new_scores.items():
                                entry["analysis"]["scores"][k] = v
                            entry["scores_manually_edited"] = True
                            updated = True
                        lines.append(json.dumps(entry, ensure_ascii=False))
                    except:
                        lines.append(line.strip())

        if updated:
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": "Nu am găsit un summary pentru această zi"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Upload Journal Photos
@app.route('/api/journal/photos/upload', methods=['POST'])
@requires_auth
def upload_journal_photo():
    date = request.form.get('date', '').strip()
    if not date:
        return jsonify({"status": "error", "message": "Dată lipsă"}), 400
    if 'photo' not in request.files:
        return jsonify({"status": "error", "message": "Nicio poză"}), 400

    file = request.files['photo']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Fișier invalid"}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{date}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(JOURNAL_PHOTOS_DIR, filename)
    file.save(filepath)
    return jsonify({"status": "success", "filename": filename, "url": f"/media/journal/{filename}"})

@app.route('/api/journal/photos/delete', methods=['POST'])
@requires_auth
def delete_journal_photo():
    data = request.json or {}
    filename = data.get('filename', '')
    if not filename or '..' in filename:
        return jsonify({"status": "error"}), 400
    filepath = os.path.join(JOURNAL_PHOTOS_DIR, secure_filename(filename))
    if os.path.exists(filepath):
        os.remove(filepath)
    return jsonify({"status": "success"})

# ============ API TARGETS ============
@app.route('/api/targets')
@requires_auth
def api_targets():
    if os.path.exists(TARGETS_FILE):
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({"goals": []})

@app.route('/api/targets/add', methods=['POST'])
@requires_auth
def add_target():
    data = request.json
    title = data.get('title', '').strip()
    if not title:
        return jsonify({"status": "error", "message": "Titlu lipsă"}), 400
    try:
        import time
        target = {
            "id": str(int(time.time() * 1000)),
            "title": title,
            "description": data.get('description', ''),
            "deadline": data.get('deadline', ''),
            "priority": data.get('priority', 'Med'),
            "category": data.get('category', 'General'),
            "progress": 0,
            "created_at": datetime.now().isoformat()
        }
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(TARGETS_FILE):
            with open(TARGETS_FILE, 'w', encoding='utf-8') as f: json.dump({"goals": []}, f)
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            file_data = json.load(f)
        file_data.setdefault('goals', []).append(target)
        with open(TARGETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(file_data, f, indent=4, ensure_ascii=False)
        return jsonify({"status": "success", "target": target})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/targets/update', methods=['POST'])
@requires_auth
def update_target():
    data = request.json
    try:
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            file_data = json.load(f)

        target_id = data.get('id')
        updated_goals = []
        archived_goal = None

        for goal in file_data.get('goals', []):
            if str(goal['id']) == str(target_id):
                for field in ['progress', 'title', 'description', 'deadline', 'priority', 'category']:
                    if field in data:
                        goal[field] = data[field]
                if int(goal.get('progress', 0)) >= 100:
                    goal['progress'] = 100
                    goal['completed_at'] = datetime.now().isoformat()
                    archived_goal = goal
                else:
                    updated_goals.append(goal)
            else:
                updated_goals.append(goal)

        file_data['goals'] = updated_goals
        with open(TARGETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(file_data, f, indent=4, ensure_ascii=False)

        if archived_goal:
            os.makedirs(os.path.dirname(COMPLETED_FILE), exist_ok=True)
            if not os.path.exists(COMPLETED_FILE):
                with open(COMPLETED_FILE, 'w', encoding='utf-8') as f: json.dump({"completed_history": []}, f)
            with open(COMPLETED_FILE, 'r+', encoding='utf-8') as f:
                comp_data = json.load(f)
                comp_data['completed_history'].append(archived_goal)
                f.seek(0); f.truncate()
                json.dump(comp_data, f, indent=4, ensure_ascii=False)

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/targets/delete', methods=['POST'])
@requires_auth
def delete_target():
    data = request.json
    target_id = data.get('id')
    if not target_id:
        return jsonify({"status": "error", "message": "ID lipsă"}), 400
    try:
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            file_data = json.load(f)
        file_data['goals'] = [g for g in file_data.get('goals', []) if str(g['id']) != str(target_id)]
        with open(TARGETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(file_data, f, indent=4, ensure_ascii=False)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============ GYM API ============

@app.route('/api/gym/measurements', methods=['GET'])
@requires_auth
def get_measurements():
    data = load_json_file(MEASUREMENTS_FILE, {"entries": []})
    return jsonify(data["entries"])

@app.route('/api/gym/measurements', methods=['POST'])
@requires_auth
def add_measurement():
    data = request.json
    date = data.get('date', datetime.now().strftime("%Y-%m-%d"))
    entry = {
        "date": date,
        "weight": data.get('weight'),
        "height": data.get('height'),
        "brat_relaxat": data.get('brat_relaxat'),
        "brat_incordat": data.get('brat_incordat'),
        "antebrat_incordat": data.get('antebrat_incordat'),
        "piept": data.get('piept'),
        "talie": data.get('talie'),
        "sold": data.get('sold'),
        "coapsa": data.get('coapsa'),
        "umar": data.get('umar'),
        "gat": data.get('gat'),
        "gamba": data.get('gamba'),
        "notes": data.get('notes', ''),
        "recorded_at": datetime.now().isoformat()
    }
    # Remove None values
    entry = {k: v for k, v in entry.items() if v is not None or k in ('date', 'recorded_at')}

    storage = load_json_file(MEASUREMENTS_FILE, {"entries": []})
    # Update existing entry for same date or add new
    found = False
    for i, e in enumerate(storage["entries"]):
        if e["date"] == date:
            storage["entries"][i] = {**e, **entry}
            found = True
            break
    if not found:
        storage["entries"].append(entry)
    storage["entries"].sort(key=lambda x: x["date"])
    save_json_file(MEASUREMENTS_FILE, storage)
    return jsonify({"status": "success", "entry": entry})

@app.route('/api/gym/measurements/<date>', methods=['DELETE'])
@requires_auth
def delete_measurement(date):
    storage = load_json_file(MEASUREMENTS_FILE, {"entries": []})
    storage["entries"] = [e for e in storage["entries"] if e["date"] != date]
    save_json_file(MEASUREMENTS_FILE, storage)
    return jsonify({"status": "success"})

@app.route('/api/gym/phase', methods=['GET'])
@requires_auth
def get_phase():
    data = load_json_file(PHASE_FILE, {"current": "sustinere", "set_at": None})
    return jsonify(data)

@app.route('/api/gym/phase', methods=['POST'])
@requires_auth
def set_phase():
    data = request.json
    phase = data.get('phase', 'sustinere')
    if phase not in ['bulk', 'sustinere', 'cut']:
        return jsonify({"status": "error", "message": "Fază invalidă"}), 400
    phase_data = {"current": phase, "set_at": datetime.now().isoformat()}
    save_json_file(PHASE_FILE, phase_data)
    return jsonify({"status": "success", "phase": phase_data})

@app.route('/api/gym/daily-checks', methods=['GET'])
@requires_auth
def get_daily_checks():
    data = load_json_file(DAILY_CHECKS_FILE, {"checks": []})
    return jsonify(data["checks"])

@app.route('/api/gym/daily-check', methods=['POST'])
@requires_auth
def add_daily_check():
    data = request.json
    date = data.get('date', datetime.now().strftime("%Y-%m-%d"))
    level = data.get('level')
    valid_levels = ['surplus_mare', 'mentinere', 'deficit', 'deficit_mare']
    if level not in valid_levels:
        return jsonify({"status": "error", "message": "Nivel invalid"}), 400

    storage = load_json_file(DAILY_CHECKS_FILE, {"checks": []})
    found = False
    for i, c in enumerate(storage["checks"]):
        if c["date"] == date:
            storage["checks"][i]["level"] = level
            storage["checks"][i]["updated_at"] = datetime.now().isoformat()
            found = True
            break
    if not found:
        storage["checks"].append({"date": date, "level": level, "created_at": datetime.now().isoformat()})
    storage["checks"].sort(key=lambda x: x["date"])
    save_json_file(DAILY_CHECKS_FILE, storage)
    return jsonify({"status": "success"})

# ---- GYM PHOTOS ----
@app.route('/api/gym/photos', methods=['GET'])
@requires_auth
def list_gym_photos():
    category = request.args.get('category', 'progress')
    folder = GYM_PHOTOS_DIR if category == 'progress' else AESTHETIC_PHOTOS_DIR
    photos = []
    if os.path.exists(folder):
        for fname in sorted(os.listdir(folder), reverse=True):
            if allowed_file(fname):
                # Try to extract date from filename prefix
                parts = fname.split('_', 1)
                date_str = parts[0] if len(parts[0]) == 10 else None
                photos.append({
                    "filename": fname,
                    "url": f"/media/gym/{'photos' if category == 'progress' else 'aesthetic'}/{fname}",
                    "date": date_str,
                    "category": category
                })
    return jsonify(photos)

@app.route('/api/gym/photos/upload', methods=['POST'])
@requires_auth
def upload_gym_photo():
    category = request.form.get('category', 'progress')
    date = request.form.get('date', datetime.now().strftime("%Y-%m-%d"))
    folder = GYM_PHOTOS_DIR if category == 'progress' else AESTHETIC_PHOTOS_DIR

    if 'photo' not in request.files:
        return jsonify({"status": "error", "message": "Nicio poză"}), 400

    file = request.files['photo']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Fișier invalid"}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{date}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = os.path.join(folder, filename)
    file.save(filepath)
    url = f"/media/gym/{'photos' if category == 'progress' else 'aesthetic'}/{filename}"
    return jsonify({"status": "success", "filename": filename, "url": url, "date": date})

@app.route('/api/gym/photos/delete', methods=['POST'])
@requires_auth
def delete_gym_photo():
    data = request.json or {}
    category = data.get('category', 'progress')
    filename = data.get('filename', '')
    if not filename or '..' in filename:
        return jsonify({"status": "error"}), 400
    folder = GYM_PHOTOS_DIR if category == 'progress' else AESTHETIC_PHOTOS_DIR
    filepath = os.path.join(folder, secure_filename(filename))
    if os.path.exists(filepath):
        os.remove(filepath)
    return jsonify({"status": "success"})

# ============ GYM PROFILE (height, etc.) ============
@app.route('/api/gym/profile', methods=['GET'])
@requires_auth
def get_gym_profile():
    if os.path.exists(GYM_PROFILE_FILE):
        with open(GYM_PROFILE_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({"height": None, "goal_weight": None, "dob": None})

@app.route('/api/gym/profile', methods=['POST'])
@requires_auth
def save_gym_profile():
    data = request.json or {}
    profile = {}
    if os.path.exists(GYM_PROFILE_FILE):
        with open(GYM_PROFILE_FILE, 'r', encoding='utf-8') as f:
            profile = json.load(f)
    profile.update({k: v for k, v in data.items() if v is not None})
    os.makedirs(GYM_DIR, exist_ok=True)
    with open(GYM_PROFILE_FILE, 'w', encoding='utf-8') as f:
        json.dump(profile, f, indent=4)
    return jsonify({"status": "success", "profile": profile})

# ============ DAILY WEIGHT LOG ============
@app.route('/api/gym/weight', methods=['GET'])
@requires_auth
def get_weight_log():
    if os.path.exists(WEIGHT_FILE):
        with open(WEIGHT_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route('/api/gym/weight', methods=['POST'])
@requires_auth
def log_weight():
    data = request.json or {}
    date = data.get('date', datetime.now().strftime("%Y-%m-%d"))
    weight = data.get('weight')
    note = data.get('note', '')
    if not weight:
        return jsonify({"status": "error", "message": "Greutate lipsă"}), 400
    try:
        weight = float(weight)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Valoare invalidă"}), 400
    os.makedirs(GYM_DIR, exist_ok=True)
    weights = []
    if os.path.exists(WEIGHT_FILE):
        with open(WEIGHT_FILE, 'r', encoding='utf-8') as f:
            weights = json.load(f)
    # Update or insert
    existing = next((w for w in weights if w.get('date') == date), None)
    if existing:
        existing['weight'] = weight
        existing['note'] = note
        existing['updated_at'] = datetime.now().isoformat()
    else:
        weights.append({"date": date, "weight": weight, "note": note, "logged_at": datetime.now().isoformat()})
    weights.sort(key=lambda x: x.get('date', ''))
    with open(WEIGHT_FILE, 'w', encoding='utf-8') as f:
        json.dump(weights, f, indent=4, ensure_ascii=False)
    return jsonify({"status": "success", "entry": {"date": date, "weight": weight}})

@app.route('/api/gym/weight/delete', methods=['POST'])
@requires_auth
def delete_weight_entry():
    data = request.json or {}
    date = data.get('date')
    if not date:
        return jsonify({"status": "error"}), 400
    if os.path.exists(WEIGHT_FILE):
        with open(WEIGHT_FILE, 'r', encoding='utf-8') as f:
            weights = json.load(f)
        weights = [w for w in weights if w.get('date') != date]
        with open(WEIGHT_FILE, 'w', encoding='utf-8') as f:
            json.dump(weights, f, indent=4)
    return jsonify({"status": "success"})

# ============ SCREEN TIME ============
@app.route('/api/screen-time', methods=['GET'])
@requires_auth
def get_screen_time():
    if os.path.exists(SCREEN_TIME_FILE):
        with open(SCREEN_TIME_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route('/api/screen-time', methods=['POST'])
@requires_auth
def log_screen_time():
    data = request.json or {}
    date = data.get('date', datetime.now().strftime("%Y-%m-%d"))
    minutes = data.get('minutes')
    if minutes is None:
        return jsonify({"status": "error", "message": "Minutes lipsă"}), 400
    try:
        minutes = int(minutes)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Valoare invalidă"}), 400
    entries = []
    if os.path.exists(SCREEN_TIME_FILE):
        with open(SCREEN_TIME_FILE, 'r', encoding='utf-8') as f:
            entries = json.load(f)
    existing = next((e for e in entries if e.get('date') == date), None)
    if existing:
        existing['minutes'] = minutes
        existing['note'] = data.get('note', existing.get('note', ''))
        existing['updated_at'] = datetime.now().isoformat()
    else:
        entries.append({"date": date, "minutes": minutes, "note": data.get('note', ''), "logged_at": datetime.now().isoformat()})
    entries.sort(key=lambda x: x.get('date', ''))
    with open(SCREEN_TIME_FILE, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=4, ensure_ascii=False)
    return jsonify({"status": "success"})

@app.route('/api/screen-time/delete', methods=['POST'])
@requires_auth
def delete_screen_time():
    data = request.json or {}
    date = data.get('date')
    if not date:
        return jsonify({"status": "error"}), 400
    if os.path.exists(SCREEN_TIME_FILE):
        with open(SCREEN_TIME_FILE, 'r', encoding='utf-8') as f:
            entries = json.load(f)
        entries = [e for e in entries if e.get('date') != date]
        with open(SCREEN_TIME_FILE, 'w', encoding='utf-8') as f:
            json.dump(entries, f, indent=4)
    return jsonify({"status": "success"})

# ============ DAY SCHEDULE (manual events) ============
@app.route('/api/day/schedule', methods=['GET'])
@requires_auth
def get_day_schedule():
    date = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
    schedule = {}
    if os.path.exists(DAY_SCHEDULE_FILE):
        with open(DAY_SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            schedule = json.load(f)
    return jsonify(schedule.get(date, []))

@app.route('/api/day/schedule', methods=['POST'])
@requires_auth
def save_day_schedule():
    data = request.json or {}
    date = data.get('date', datetime.now().strftime("%Y-%m-%d"))
    events = data.get('events', [])
    schedule = {}
    if os.path.exists(DAY_SCHEDULE_FILE):
        with open(DAY_SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            schedule = json.load(f)
    schedule[date] = events
    with open(DAY_SCHEDULE_FILE, 'w', encoding='utf-8') as f:
        json.dump(schedule, f, indent=4, ensure_ascii=False)
    return jsonify({"status": "success"})

# ============ AI DAILY BRIEFING ============
@app.route('/api/day/briefing', methods=['POST'])
@requires_auth
def generate_briefing():
    """Generate an AI daily briefing based on all available context."""
    data = request.json or {}
    today = datetime.now().strftime("%Y-%m-%d")
    weekday_ro = ["Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă", "Duminică"]
    weekday = weekday_ro[datetime.now().weekday()]

    # Collect context
    # Targets
    targets_ctx = []
    if os.path.exists(TARGETS_FILE):
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            td = json.load(f)
        for g in td.get('goals', [])[:5]:
            targets_ctx.append(f"- {g.get('title')} ({g.get('priority','')}, {g.get('progress',0)}%)")

    # Gym phase
    phase = "sustinere"
    if os.path.exists(PHASE_FILE):
        with open(PHASE_FILE, 'r', encoding='utf-8') as f:
            phase = json.load(f).get('current', 'sustinere')

    # Last weight
    last_weight = None
    if os.path.exists(WEIGHT_FILE):
        with open(WEIGHT_FILE, 'r', encoding='utf-8') as f:
            wl = json.load(f)
        if wl:
            last_weight = wl[-1].get('weight')

    # Last 7 days food checks
    food_checks = []
    if os.path.exists(DAILY_CHECKS_FILE):
        with open(DAILY_CHECKS_FILE, 'r', encoding='utf-8') as f:
            checks = json.load(f)
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        food_checks = [c.get('level') for c in checks if c.get('date', '') >= cutoff]

    # Screen time avg last 7 days
    st_avg = None
    if os.path.exists(SCREEN_TIME_FILE):
        with open(SCREEN_TIME_FILE, 'r', encoding='utf-8') as f:
            st_entries = json.load(f)
        cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent_st = [e.get('minutes', 0) for e in st_entries if e.get('date', '') >= cutoff]
        if recent_st:
            st_avg = round(sum(recent_st) / len(recent_st))

    # Events for today
    events_ctx = data.get('events', [])

    # Recent journal summary
    recent_summary = data.get('recent_summary', '')

    try:
        sys.path.insert(0, BASE_DIR)
        from ai_core import ask_gemini_json

        prompt = f"""
Ești Chronos, asistentul AI personal. Generează un briefing zilnic inteligent și motivant pentru utilizator.

DATA: {weekday}, {today}
FAZA FITNESS: {phase.upper()}
GREUTATE ACTUALĂ: {last_weight or 'necunoscută'} kg
FOOD CHECKS ULTIMELE 7 ZILE: {', '.join(food_checks) if food_checks else 'nedisponibil'}
SCREEN TIME MEDIU 7 ZILE: {f"{st_avg // 60}h {st_avg % 60}m" if st_avg else 'neînregistrat'}
TARGETURI ACTIVE:
{chr(10).join(targets_ctx) if targets_ctx else '- Niciun target activ'}
AGENDA AZI: {', '.join(events_ctx) if events_ctx else 'necompletată'}
CONTEXT JURNAL RECENT: {recent_summary or 'nedisponibil'}

Generează un briefing structurat, concis, în română, care include:
1. Un salut scurt adaptat zilei (ex: "Luni grea, dar ești pregătit")
2. Focus principal al zilei (1-2 fraze)
3. Recomandare screen time maxim pentru azi (în ore, bazat pe trend)
4. Sfat fitness/alimentar bazat pe faza curentă
5. Top 3 acțiuni concrete pentru azi
6. O frază motivațională scurtă la final

Fii direct, nu verbos. Vorbi-i ca unui prieten.
"""

        schema = {
            "type": "OBJECT",
            "properties": {
                "greeting": {"type": "STRING"},
                "focus": {"type": "STRING"},
                "screen_time_rec": {"type": "STRING"},
                "fitness_tip": {"type": "STRING"},
                "top3_actions": {"type": "ARRAY", "items": {"type": "STRING"}},
                "motivation": {"type": "STRING"},
                "energy_level": {"type": "STRING", "enum": ["high", "medium", "low"]},
                "mood_vibe": {"type": "STRING"}
            },
            "required": ["greeting", "focus", "top3_actions", "motivation"]
        }

        result = ask_gemini_json(prompt, schema=schema, temperature=0.75)
        if result:
            result['generated_at'] = datetime.now().isoformat()
            result['date'] = today
            return jsonify({"status": "success", "briefing": result})
        else:
            return jsonify({"status": "error", "message": "AI nu a răspuns"}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    print("🌐 Pornesc Dashboard-ul Chronos...")
    app.run(host='0.0.0.0', port=5000, debug=True)