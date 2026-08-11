import os
import json
import glob
import sys
from time import time
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
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.json")
MAINTENANCE_FILE = os.path.join(DATA_DIR, "maintenance.json")
SCENES_FILE = os.path.join(DATA_DIR, "scenes.json")

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

# --- DAILY TASKS ---
DAILY_TASKS_FILE = os.path.join(DATA_DIR, "daily_tasks.json")

# --- FINANCE ---
FINANCE_FILE = os.path.join(DATA_DIR, "finance.json")

# --- AUTH TRUST DEVICE ---
TRUSTED_DEVICES_FILE = os.path.join(DATA_DIR, "trusted_devices.json")

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'}

sys.path.append(BASE_DIR)

from logger_specialist import JournalCore
from wled_specialist import WLEDStateManager, WLEDDispatcher

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# Lazy-loaded journal core
class DispatcherProxy:
    def __getattr__(self, name):
        # Dacă cineva încearcă să acceseze ceva și dispecerul e gol, aruncă o eroare clară
        raise Exception("Dispecerul central nu a fost conectat din main.py!")

shared_dispatcher = DispatcherProxy()
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
            # Update last_used only every 5 minutes to avoid disk I/O on every request
            last_used = dev.get("last_used", "")
            try:
                last_dt = datetime.fromisoformat(last_used)
                if (datetime.now() - last_dt).total_seconds() > 300:
                    dev["last_used"] = datetime.now().isoformat()
                    save_trusted_devices(data)
            except:
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
def get_available_months():
    """Return sorted list of available month strings (YYYY-MM), newest first."""
    if not os.path.exists(LOGS_DIR):
        return []
    months = []
    for file_path in glob.glob(os.path.join(LOGS_DIR, "log_*.jsonl")):
        fname = os.path.basename(file_path)  # log_YYYY_MM.jsonl
        parts = fname.replace('log_', '').replace('.jsonl', '').split('_')
        if len(parts) == 2:
            months.append(f"{parts[0]}-{parts[1]}")
    return sorted(months, reverse=True)

def get_logs_for_month(year_month: str):
    """Load and return grouped log entries for a single YYYY-MM month."""
    try:
        year, month = year_month.split('-')
    except ValueError:
        return []

    file_path = os.path.join(LOGS_DIR, f"log_{year}_{month}.jsonl")
    if not os.path.exists(file_path):
        return []

    logs = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                try:
                    logs.append(json.loads(line))
                except:
                    pass

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
        except:
            continue

    # Attach journal photos to each day (only for this month prefix)
    journal_photos = {}
    if os.path.exists(JOURNAL_PHOTOS_DIR):
        for fname in os.listdir(JOURNAL_PHOTOS_DIR):
            if allowed_file(fname):
                parts = fname.split('_', 1)
                if len(parts) >= 1:
                    date_part = parts[0]  # YYYY-MM-DD
                    if date_part.startswith(year_month):
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

def get_all_logs():
    """Legacy: load all months. Used only for full search if needed."""
    all_results = []
    for month in reversed(get_available_months()):  # oldest first, then sort
        all_results.extend(get_logs_for_month(month))
    all_results.sort(key=lambda d: d['date'], reverse=True)
    return all_results

def _get_log_file_for_date(date_str):
    year, month, _ = date_str.split('-')
    return os.path.join(LOGS_DIR, f"log_{year}_{month}.jsonl")

from flask import redirect, url_for as flask_url_for

# ============ HTML ROUTES ============
@app.route('/')
@requires_auth
def home_page():
    return render_template('home.html', active_page='home')

@app.route('/journal')
@requires_auth
def index():
    return render_template('index.html', active_page='journal')

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
    return redirect('/')

@app.route('/terminal')
@requires_auth
def terminal():
    return render_template('terminal.html', active_page='terminal')

@app.route('/settings')
@requires_auth
def settings_page():
    return render_template('settings.html', active_page='settings')

@app.route('/electronics')
@requires_auth
def electronics_page():
    return render_template('electronics.html', active_page='electronics')

# ============ SETTINGS API ============
SETTINGS_ALLOWED_PATHS = None  # Lazy init

def _get_allowed_base_paths():
    """Returns list of allowed directories/files for Settings editor."""
    return [
        os.path.join(BASE_DIR, "chronos_data"),
        os.path.join(BASE_DIR, "config.py"),
    ]

def _is_safe_path(requested_path):
    """Check that requested_path is inside one of the allowed base paths."""
    real = os.path.realpath(requested_path)
    for allowed in _get_allowed_base_paths():
        allowed_real = os.path.realpath(allowed)
        if real == allowed_real or real.startswith(allowed_real + os.sep):
            return True
    return False

def _build_file_tree(directory, rel_root=None):
    """Recursively build file tree for a directory."""
    if rel_root is None:
        rel_root = BASE_DIR
    items = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: (not e.is_dir(), e.name.lower()))
        for entry in entries:
            rel_path = os.path.relpath(entry.path, BASE_DIR).replace('\\', '/')
            if entry.name.startswith('.') or entry.name == '__pycache__':
                continue
            if entry.is_dir():
                children = _build_file_tree(entry.path, rel_root)
                items.append({"type": "dir", "name": entry.name, "path": rel_path, "children": children})
            else:
                ext = entry.name.rsplit('.', 1)[-1].lower() if '.' in entry.name else ''
                if ext in ('json', 'jsonl', 'py', 'txt', 'md'):
                    items.append({
                        "type": "file", "name": entry.name,
                        "path": rel_path, "ext": ext,
                        "size": entry.stat().st_size
                    })
    except PermissionError:
        pass
    return items

@app.route('/api/settings/tree', methods=['GET'])
@requires_auth
def settings_tree():
    """Return file tree for config.py and chronos_data/."""
    config_rel = os.path.relpath(os.path.join(BASE_DIR, "config.py"), BASE_DIR).replace('\\', '/')
    config_files = [{
        "type": "file", "name": "config.py", "path": config_rel,
        "ext": "py", "size": os.path.getsize(os.path.join(BASE_DIR, "config.py"))
    }]
    data_dir = os.path.join(BASE_DIR, "chronos_data")
    data_files = _build_file_tree(data_dir)
    return jsonify({"config_files": config_files, "data_files": data_files})

@app.route('/api/settings/file', methods=['GET'])
@requires_auth
def settings_read_file():
    """Read a file content. path is relative to BASE_DIR."""
    rel_path = request.args.get('path', '').strip()
    if not rel_path:
        return jsonify({"status": "error", "message": "Path lipsă"}), 400
    abs_path = os.path.realpath(os.path.join(BASE_DIR, rel_path))
    if not _is_safe_path(abs_path):
        return jsonify({"status": "error", "message": "Acces interzis la această cale"}), 403
    if not os.path.isfile(abs_path):
        return jsonify({"status": "error", "message": "Fișierul nu există"}), 404
    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()
        ext = abs_path.rsplit('.', 1)[-1].lower() if '.' in abs_path else ''
        return jsonify({"status": "success", "content": content, "ext": ext, "path": rel_path})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/settings/file', methods=['POST'])
@requires_auth
def settings_write_file():
    """Write file content. body: {path, content}."""
    body = request.json or {}
    rel_path = body.get('path', '').strip()
    content = body.get('content', '')
    if not rel_path:
        return jsonify({"status": "error", "message": "Path lipsă"}), 400
    abs_path = os.path.realpath(os.path.join(BASE_DIR, rel_path))
    if not _is_safe_path(abs_path):
        return jsonify({"status": "error", "message": "Acces interzis la această cale"}), 403
    try:
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"status": "success", "bytes_written": len(content.encode('utf-8'))})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============ ELECTRONICS API ============
ELECTRONICS_FILE = os.path.join(DATA_DIR, "electronics_data.json")

def _load_electronics():
    if os.path.exists(ELECTRONICS_FILE):
        with open(ELECTRONICS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"components": [], "projects": [], "wishlist": []}

def _save_electronics(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ELECTRONICS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

@app.route('/api/electronics/data', methods=['GET'])
@requires_auth
def electronics_data():
    data = _load_electronics()
    # Compute available quantities
    for comp in data.get('components', []):
        reserved = sum(
            r.get('qty', 0)
            for proj in data.get('projects', [])
            for r in proj.get('reservations', [])
            if r.get('component_id') == comp['id']
        )
        comp['reserved'] = reserved
        comp['available'] = max(0, comp.get('qty', 0) - reserved)
    return jsonify(data)

@app.route('/api/electronics/component/add', methods=['POST'])
@requires_auth
def electronics_component_add():
    import time
    data = _load_electronics()
    body = request.json or {}
    name = body.get('name', '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Nume lipsă'}), 400
    comp = {
        'id': f"comp_{int(time.time()*1000)}",
        'name': name,
        'category': body.get('category', 'Altele'),
        'qty': int(body.get('qty', 0)),
        'specs': body.get('specs', ''),
        'notes': body.get('notes', ''),
        'created_at': datetime.now().isoformat()
    }
    data.setdefault('components', []).append(comp)
    _save_electronics(data)
    return jsonify({'status': 'success', 'component': comp})

@app.route('/api/electronics/component/edit', methods=['POST'])
@requires_auth
def electronics_component_edit():
    data = _load_electronics()
    body = request.json or {}
    cid = body.get('id')
    for comp in data.get('components', []):
        if comp['id'] == cid:
            for field in ['name', 'category', 'specs', 'notes']:
                if field in body: comp[field] = body[field]
            if 'qty' in body: comp['qty'] = int(body['qty'])
            comp['updated_at'] = datetime.now().isoformat()
            break
    _save_electronics(data)
    return jsonify({'status': 'success'})

@app.route('/api/electronics/component/delete', methods=['POST'])
@requires_auth
def electronics_component_delete():
    data = _load_electronics()
    body = request.json or {}
    cid = body.get('id')
    data['components'] = [c for c in data.get('components', []) if c['id'] != cid]
    # Remove reservations for this component from all projects
    for proj in data.get('projects', []):
        proj['reservations'] = [r for r in proj.get('reservations', []) if r.get('component_id') != cid]
    _save_electronics(data)
    return jsonify({'status': 'success'})

@app.route('/api/electronics/project/add', methods=['POST'])
@requires_auth
def electronics_project_add():
    import time
    data = _load_electronics()
    body = request.json or {}
    name = body.get('name', '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Nume lipsă'}), 400
    proj = {
        'id': f"proj_{int(time.time()*1000)}",
        'name': name,
        'description': body.get('description', ''),
        'status': body.get('status', 'idea'),  # idea, active, done
        'technologies': body.get('technologies', []),
        'links': body.get('links', []),
        'reservations': [],
        'devlog': [],
        'created_at': datetime.now().isoformat()
    }
    data.setdefault('projects', []).append(proj)
    _save_electronics(data)
    return jsonify({'status': 'success', 'project': proj})

@app.route('/api/electronics/project/edit', methods=['POST'])
@requires_auth
def electronics_project_edit():
    data = _load_electronics()
    body = request.json or {}
    pid = body.get('id')
    for proj in data.get('projects', []):
        if proj['id'] == pid:
            for field in ['name', 'description', 'status', 'technologies', 'links']:
                if field in body: proj[field] = body[field]
            proj['updated_at'] = datetime.now().isoformat()
            break
    _save_electronics(data)
    return jsonify({'status': 'success'})

@app.route('/api/electronics/project/delete', methods=['POST'])
@requires_auth
def electronics_project_delete():
    data = _load_electronics()
    body = request.json or {}
    pid = body.get('id')
    data['projects'] = [p for p in data.get('projects', []) if p['id'] != pid]
    _save_electronics(data)
    return jsonify({'status': 'success'})

@app.route('/api/electronics/project/devlog/add', methods=['POST'])
@requires_auth
def electronics_devlog_add():
    import time
    data = _load_electronics()
    body = request.json or {}
    pid = body.get('project_id')
    title = body.get('title', '').strip()
    text = body.get('text', '').strip()
    if not pid or not title:
        return jsonify({'status': 'error', 'message': 'Date lipsă'}), 400
    entry = {
        'id': f"dlog_{int(time.time()*1000)}",
        'date': body.get('date', datetime.now().strftime('%Y-%m-%d')),
        'title': title,
        'text': text,
        'created_at': datetime.now().isoformat()
    }
    for proj in data.get('projects', []):
        if proj['id'] == pid:
            proj.setdefault('devlog', []).append(entry)
            proj['devlog'].sort(key=lambda x: x.get('date', ''), reverse=True)
            break
    _save_electronics(data)
    return jsonify({'status': 'success', 'entry': entry})

@app.route('/api/electronics/project/devlog/edit', methods=['POST'])
@requires_auth
def electronics_devlog_edit():
    data = _load_electronics()
    body = request.json or {}
    pid = body.get('project_id')
    eid = body.get('entry_id')
    for proj in data.get('projects', []):
        if proj['id'] == pid:
            for entry in proj.get('devlog', []):
                if entry['id'] == eid:
                    for field in ['date', 'title', 'text']:
                        if field in body: entry[field] = body[field]
                    entry['updated_at'] = datetime.now().isoformat()
                    break
            proj['devlog'].sort(key=lambda x: x.get('date', ''), reverse=True)
            break
    _save_electronics(data)
    return jsonify({'status': 'success'})

@app.route('/api/electronics/project/devlog/delete', methods=['POST'])
@requires_auth
def electronics_devlog_delete():
    data = _load_electronics()
    body = request.json or {}
    pid = body.get('project_id')
    eid = body.get('entry_id')
    for proj in data.get('projects', []):
        if proj['id'] == pid:
            proj['devlog'] = [e for e in proj.get('devlog', []) if e['id'] != eid]
            break
    _save_electronics(data)
    return jsonify({'status': 'success'})

# ============ DEVLOG PHOTOS ============
DEVLOG_PHOTOS_DIR = os.path.join(DATA_DIR, 'electronics_devlog_photos')
os.makedirs(DEVLOG_PHOTOS_DIR, exist_ok=True)

@app.route('/api/electronics/devlog/photo/upload', methods=['POST'])
@requires_auth
def electronics_devlog_photo_upload():
    """Upload a photo for a devlog entry."""
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'Niciun fișier'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'Niciun fișier selectat'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'status': 'error', 'message': 'Format neacceptat'}), 400
    
    proj_id = request.form.get('project_id')
    entry_id = request.form.get('entry_id')
    
    if not proj_id or not entry_id:
        return jsonify({'status': 'error', 'message': 'Date lipsă'}), 400
    
    # Generate filename
    filename = secure_filename(f"{proj_id}_{entry_id}_{int(time.time()*1000)}_{file.filename}")
    filepath = os.path.join(DEVLOG_PHOTOS_DIR, filename)
    file.save(filepath)
    
    # Update devlog entry with photo reference
    data = _load_electronics()
    for proj in data.get('projects', []):
        if proj['id'] == proj_id:
            for entry in proj.get('devlog', []):
                if entry['id'] == entry_id:
                    entry.setdefault('photos', []).append(filename)
                    entry['updated_at'] = datetime.now().isoformat()
                    break
            break
    _save_electronics(data)
    
    return jsonify({
        'status': 'success',
        'filename': filename,
        'url': f'/api/electronics/devlog/photo/{filename}'
    })

@app.route('/api/electronics/devlog/photo/<filename>', methods=['GET'])
def electronics_devlog_photo_get(filename):
    """Serve devlog photo."""
    try:
        return send_from_directory(DEVLOG_PHOTOS_DIR, filename)
    except:
        return jsonify({'status': 'error', 'message': 'Fișier negăsit'}), 404

@app.route('/api/electronics/devlog/photo/delete', methods=['POST'])
@requires_auth
def electronics_devlog_photo_delete():
    """Delete a photo from devlog entry."""
    data = _load_electronics()
    body = request.json or {}
    proj_id = body.get('project_id')
    entry_id = body.get('entry_id')
    filename = body.get('filename')
    
    if not all([proj_id, entry_id, filename]):
        return jsonify({'status': 'error', 'message': 'Date lipsă'}), 400
    
    # Remove from devlog entry
    for proj in data.get('projects', []):
        if proj['id'] == proj_id:
            for entry in proj.get('devlog', []):
                if entry['id'] == entry_id:
                    if 'photos' in entry and filename in entry['photos']:
                        entry['photos'].remove(filename)
                        entry['updated_at'] = datetime.now().isoformat()
                    break
            break
    _save_electronics(data)
    
    # Delete file
    filepath = os.path.join(DEVLOG_PHOTOS_DIR, filename)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except:
            pass
    
    return jsonify({'status': 'success'})

@app.route('/api/electronics/reserve', methods=['POST'])
@requires_auth
def electronics_reserve():
    """Add or update a reservation of a component for a project."""
    data = _load_electronics()
    body = request.json or {}
    pid = body.get('project_id')
    cid = body.get('component_id')
    qty = int(body.get('qty', 0))

    # Find the component to check stock
    comp = next((c for c in data.get('components', []) if c['id'] == cid), None)
    if not comp:
        return jsonify({'status': 'error', 'message': 'Componentă negăsită'}), 404

    # Calculate currently reserved by OTHER projects
    other_reserved = sum(
        r.get('qty', 0)
        for proj in data.get('projects', [])
        for r in proj.get('reservations', [])
        if r.get('component_id') == cid and proj['id'] != pid
    )
    available_for_this = comp.get('qty', 0) - other_reserved
    if qty > available_for_this:
        return jsonify({'status': 'error', 'message': f'Stoc insuficient. Disponibil pentru acest proiect: {available_for_this}'}), 400

    for proj in data.get('projects', []):
        if proj['id'] == pid:
            existing = next((r for r in proj.get('reservations', []) if r.get('component_id') == cid), None)
            if existing:
                if qty == 0:
                    proj['reservations'] = [r for r in proj['reservations'] if r.get('component_id') != cid]
                else:
                    existing['qty'] = qty
                    existing['updated_at'] = datetime.now().isoformat()
            elif qty > 0:
                proj.setdefault('reservations', []).append({
                    'component_id': cid,
                    'qty': qty,
                    'reserved_at': datetime.now().isoformat()
                })
            break
    _save_electronics(data)
    return jsonify({'status': 'success'})

@app.route('/api/electronics/wishlist/add', methods=['POST'])
@requires_auth
def electronics_wishlist_add():
    import time
    data = _load_electronics()
    body = request.json or {}
    name = body.get('name', '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Nume lipsă'}), 400
    item = {
        'id': f"wish_{int(time.time()*1000)}",
        'name': name,
        'qty': int(body.get('qty', 1)),
        'priority': body.get('priority', 'normal'),  # urgent, normal, low
        'link': body.get('link', ''),
        'reason': body.get('reason', ''),
        'created_at': datetime.now().isoformat()
    }
    data.setdefault('wishlist', []).append(item)
    _save_electronics(data)
    return jsonify({'status': 'success', 'item': item})

@app.route('/api/electronics/wishlist/delete', methods=['POST'])
@requires_auth
def electronics_wishlist_delete():
    data = _load_electronics()
    body = request.json or {}
    wid = body.get('id')
    data['wishlist'] = [w for w in data.get('wishlist', []) if w['id'] != wid]
    _save_electronics(data)
    return jsonify({'status': 'success'})

@app.route('/api/electronics/wishlist/buy', methods=['POST'])
@requires_auth
def electronics_wishlist_buy():
    """Mark wishlist item as bought: remove from wishlist, add to inventory."""
    import time
    data = _load_electronics()
    body = request.json or {}
    wid = body.get('id')
    bought_qty = int(body.get('qty', 0))
    category = body.get('category', 'Altele')
    specs = body.get('specs', '')

    wish = next((w for w in data.get('wishlist', []) if w['id'] == wid), None)
    if not wish:
        return jsonify({'status': 'error', 'message': 'Item negăsit'}), 404

    # Check if component with same name already exists → update qty
    existing_comp = next((c for c in data.get('components', []) if c['name'].lower() == wish['name'].lower()), None)
    if existing_comp:
        existing_comp['qty'] = existing_comp.get('qty', 0) + bought_qty
        existing_comp['updated_at'] = datetime.now().isoformat()
        comp = existing_comp
    else:
        comp = {
            'id': f"comp_{int(time.time()*1000)}",
            'name': wish['name'],
            'category': category,
            'qty': bought_qty,
            'specs': specs,
            'notes': f"Cumpărat din wishlist ({datetime.now().strftime('%Y-%m-%d')})",
            'created_at': datetime.now().isoformat()
        }
        data.setdefault('components', []).append(comp)

    data['wishlist'] = [w for w in data['wishlist'] if w['id'] != wid]
    _save_electronics(data)
    return jsonify({'status': 'success', 'component': comp})

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
            checks = json.load(f).get("checks", [])
        today_check = next((c for c in reversed(checks) if c.get('date') == today), None)
        if today_check:
            food_data["logged"] = True
            food_data["level"] = today_check["level"]

    # --- Journal entries today ---
    journal_data = {"entries_today": 0, "entries": [], "last_scores": None}
    try:
        logfile = _get_log_file_for_date(datetime.now().strftime("%Y-%m-%d"))
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

    # --- Last weight (even if not today) + recent 7 ---
    # Read WEIGHT_FILE only once for both last_weight_ever and recent_weights
    last_weight_ever = None
    recent_weights = []
    if os.path.exists(WEIGHT_FILE):
        with open(WEIGHT_FILE, 'r', encoding='utf-8') as f:
            wl = json.load(f)
        if wl:
            last_weight_ever = wl[-1].get('weight')
            recent_weights = list(reversed(wl[-7:]))

    # --- Schedule today ---
    schedule = []
    if os.path.exists(DAY_SCHEDULE_FILE):
        with open(DAY_SCHEDULE_FILE, 'r', encoding='utf-8') as f:
            sched = json.load(f)
        schedule = sched.get(today, [])

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
    data = request.json or {}
    text = data.get('text', '').strip()
    if not text:
        return jsonify({"status": "error", "message": "Comandă goală"}), 400

    try:
        global shared_dispatcher
        
        # Apelează DISPECERUL CENTRAL (cel real, cu memorie)
        if shared_dispatcher:
            shared_dispatcher.process_text_command(text, None)
            res = getattr(shared_dispatcher, 'last_result', {})
        else:
            return jsonify({"status": "error", "message": "Dispecerul central nu este conectat."}), 500
        
        # Extragem lista de acțiuni pentru UI-ul din browser
        actions_list = []
        for a in res.get("actions", []):
            if isinstance(a, dict):
                actions_list.append(a.get("text", ""))
            else:
                actions_list.append(str(a))

        return jsonify({
            "status": "success",
            "intents": res.get("intents", ["general"]),
            "reply": res.get("reply"),
            "actions": actions_list,
            "reasoning": res.get("reasoning", "")
        })

    except Exception as e:
        import traceback
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
@app.route('/api/logs/months')
@requires_auth
def api_logs_months():
    """Return list of available months (YYYY-MM), newest first."""
    months = get_available_months()
    return jsonify({"months": months})

@app.route('/api/logs')
@requires_auth
def api_logs():
    """Return logs for a specific month. Defaults to current month.
    Query param: ?month=YYYY-MM
    """
    month = request.args.get('month', '').strip()
    if not month:
        # Default: current month
        month = datetime.now().strftime("%Y-%m")
    return jsonify(get_logs_for_month(month))

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

# ============ API REMINDERS ============
def _load_reminders():
    if os.path.exists(REMINDERS_FILE):
        with open(REMINDERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"reminders": []}

def _save_reminders(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REMINDERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

@app.route('/api/reminders', methods=['GET'])
@requires_auth
def get_reminders():
    return jsonify(_load_reminders())

@app.route('/api/reminders/add', methods=['POST'])
@requires_auth
def add_reminder():
    import time
    body = request.json or {}
    title = body.get('title', '').strip()
    if not title:
        return jsonify({'status': 'error', 'message': 'Titlu lipsă'}), 400
    rem = {
        'id': f"rem_{int(time.time()*1000)}",
        'title': title,
        'description': body.get('description', ''),
        'emoji': body.get('emoji', '📌'),
        'priority': body.get('priority', 'Med'),
        'checked': False,
        'last_checked': None,
        'created_at': datetime.now().isoformat()
    }
    data = _load_reminders()
    data.setdefault('reminders', []).append(rem)
    _save_reminders(data)
    return jsonify({'status': 'success', 'reminder': rem})

@app.route('/api/reminders/check', methods=['POST'])
@requires_auth
def check_reminder():
    body = request.json or {}
    rid = body.get('id')
    checked = body.get('checked', True)
    data = _load_reminders()
    for rem in data.get('reminders', []):
        if rem['id'] == rid:
            rem['checked'] = checked
            rem['last_checked'] = datetime.now().isoformat() if checked else rem.get('last_checked')
            break
    _save_reminders(data)
    return jsonify({'status': 'success'})

@app.route('/api/reminders/delete', methods=['POST'])
@requires_auth
def delete_reminder():
    body = request.json or {}
    rid = body.get('id')
    data = _load_reminders()
    data['reminders'] = [r for r in data.get('reminders', []) if r['id'] != rid]
    _save_reminders(data)
    return jsonify({'status': 'success'})

@app.route('/api/reminders/edit', methods=['POST'])
@requires_auth
def edit_reminder():
    body = request.json or {}
    rid = body.get('id')
    data = _load_reminders()
    for rem in data.get('reminders', []):
        if rem['id'] == rid:
            for field in ['title', 'description', 'emoji', 'priority']:
                if field in body: rem[field] = body[field]
            break
    _save_reminders(data)
    return jsonify({'status': 'success'})

# ============ API MAINTENANCE ============
def _load_maintenance():
    if os.path.exists(MAINTENANCE_FILE):
        with open(MAINTENANCE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"items": []}

def _save_maintenance(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MAINTENANCE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

@app.route('/api/maintenance', methods=['GET'])
@requires_auth
def get_maintenance():
    return jsonify(_load_maintenance())

@app.route('/api/maintenance/item/add', methods=['POST'])
@requires_auth
def add_maintenance_item():
    import time
    body = request.json or {}
    name = body.get('name', '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Nume lipsă'}), 400
    item = {
        'id': f"mnt_{int(time.time()*1000)}",
        'name': name,
        'emoji': body.get('emoji', '🔧'),
        'tasks': [],
        'created_at': datetime.now().isoformat()
    }
    data = _load_maintenance()
    data.setdefault('items', []).append(item)
    _save_maintenance(data)
    return jsonify({'status': 'success', 'item': item})

@app.route('/api/maintenance/item/delete', methods=['POST'])
@requires_auth
def delete_maintenance_item():
    body = request.json or {}
    iid = body.get('id')
    data = _load_maintenance()
    data['items'] = [i for i in data.get('items', []) if i['id'] != iid]
    _save_maintenance(data)
    return jsonify({'status': 'success'})

@app.route('/api/maintenance/task/add', methods=['POST'])
@requires_auth
def add_maintenance_task():
    import time
    body = request.json or {}
    item_id = body.get('item_id')
    task_name = body.get('name', '').strip()
    interval_days = int(body.get('interval_days', 30))
    if not item_id or not task_name:
        return jsonify({'status': 'error', 'message': 'Date lipsă'}), 400
    task = {
        'id': f"mtask_{int(time.time()*1000)}",
        'name': task_name,
        'interval_days': interval_days,
        'last_done': None,
        'notes': body.get('notes', ''),
        'created_at': datetime.now().isoformat()
    }
    data = _load_maintenance()
    for item in data.get('items', []):
        if item['id'] == item_id:
            item.setdefault('tasks', []).append(task)
            break
    _save_maintenance(data)
    return jsonify({'status': 'success', 'task': task})

@app.route('/api/maintenance/task/done', methods=['POST'])
@requires_auth
def done_maintenance_task():
    body = request.json or {}
    item_id = body.get('item_id')
    task_id = body.get('task_id')
    data = _load_maintenance()
    for item in data.get('items', []):
        if item['id'] == item_id:
            for task in item.get('tasks', []):
                if task['id'] == task_id:
                    task['last_done'] = datetime.now().strftime('%Y-%m-%d')
                    break
            break
    _save_maintenance(data)
    return jsonify({'status': 'success'})

@app.route('/api/maintenance/task/delete', methods=['POST'])
@requires_auth
def delete_maintenance_task():
    body = request.json or {}
    item_id = body.get('item_id')
    task_id = body.get('task_id')
    data = _load_maintenance()
    for item in data.get('items', []):
        if item['id'] == item_id:
            item['tasks'] = [t for t in item.get('tasks', []) if t['id'] != task_id]
            break
    _save_maintenance(data)
    return jsonify({'status': 'success'})

# ============ API HOME ALERTS ============
@app.route('/api/home/alerts', methods=['GET'])
@requires_auth
def home_alerts():
    alerts = []
    today = datetime.now().date()

    # --- Overdue targets ---
    if os.path.exists(TARGETS_FILE):
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            td = json.load(f)
        for g in td.get('goals', []):
            if g.get('deadline'):
                try:
                    dl = datetime.strptime(g['deadline'], '%Y-%m-%d').date()
                    days_left = (dl - today).days
                    if days_left < 0:
                        alerts.append({'type': 'target_overdue', 'severity': 'error',
                            'title': f"Target expirat: {g['title']}",
                            'detail': f"A expirat acum {abs(days_left)} zile",
                            'link': '/targets', 'icon': '🎯'})
                    elif days_left <= 3:
                        alerts.append({'type': 'target_soon', 'severity': 'warning',
                            'title': f"Target expiră în {days_left}z: {g['title']}",
                            'detail': f"Deadline: {g['deadline']}",
                            'link': '/targets', 'icon': '⚠️'})
                except: pass

    # --- Maintenance overdue ---
    if os.path.exists(MAINTENANCE_FILE):
        with open(MAINTENANCE_FILE, 'r', encoding='utf-8') as f:
            mnt = json.load(f)
        for item in mnt.get('items', []):
            for task in item.get('tasks', []):
                interval = task.get('interval_days', 30)
                last_done = task.get('last_done')
                if last_done:
                    try:
                        ld = datetime.strptime(last_done, '%Y-%m-%d').date()
                        days_since = (today - ld).days
                        days_left = interval - days_since
                        if days_left < 0:
                            alerts.append({'type': 'maintenance_overdue', 'severity': 'error',
                                'title': f"{item['emoji']} {item['name']}: {task['name']}",
                                'detail': f"Scadentă de {abs(days_left)} zile",
                                'link': '/targets', 'icon': '🔧'})
                        elif days_left <= 7:
                            alerts.append({'type': 'maintenance_soon', 'severity': 'warning',
                                'title': f"{item['emoji']} {item['name']}: {task['name']}",
                                'detail': f"Scadentă în {days_left} zile",
                                'link': '/targets', 'icon': '⏰'})
                    except: pass
                else:
                    # Never done
                    alerts.append({'type': 'maintenance_never', 'severity': 'info',
                        'title': f"{item['emoji']} {item['name']}: {task['name']}",
                        'detail': 'Niciodată efectuată',
                        'link': '/targets', 'icon': '📋'})

    return jsonify({'alerts': alerts})

# ============ API PROJECT PLAN (STEPS) ============
@app.route('/api/electronics/project/plan/add', methods=['POST'])
@requires_auth
def proj_plan_add():
    import time
    body = request.json or {}
    pid = body.get('project_id')
    title = body.get('title', '').strip()
    parent_path = body.get('parent_path', [])  # list of step IDs leading to parent
    if not pid or not title:
        return jsonify({'status': 'error', 'message': 'Date lipsă'}), 400
    step = {
        'id': f"step_{int(time.time()*1000)}",
        'title': title,
        'status': body.get('status', 'todo'),
        'priority': body.get('priority', 'Med'),
        'children': [],
        'created_at': datetime.now().isoformat()
    }
    data = _load_electronics()
    for proj in data.get('projects', []):
        if proj['id'] == pid:
            proj.setdefault('plan', [])
            if not parent_path:
                proj['plan'].append(step)
            else:
                # Navigate to parent
                node_list = proj['plan']
                for step_id in parent_path:
                    parent = next((s for s in node_list if s['id'] == step_id), None)
                    if parent is None: break
                    node_list = parent.setdefault('children', [])
                node_list.append(step)
            break
    _save_electronics(data)
    return jsonify({'status': 'success', 'step': step})

@app.route('/api/electronics/project/plan/update', methods=['POST'])
@requires_auth
def proj_plan_update():
    body = request.json or {}
    pid = body.get('project_id')
    step_id = body.get('step_id')
    def update_step(node_list):
        for s in node_list:
            if s['id'] == step_id:
                for field in ['title', 'status', 'priority']:
                    if field in body: s[field] = body[field]
                return True
            if update_step(s.get('children', [])): return True
        return False
    data = _load_electronics()
    for proj in data.get('projects', []):
        if proj['id'] == pid:
            update_step(proj.get('plan', []))
            break
    _save_electronics(data)
    return jsonify({'status': 'success'})

@app.route('/api/electronics/project/plan/delete', methods=['POST'])
@requires_auth
def proj_plan_delete():
    body = request.json or {}
    pid = body.get('project_id')
    step_id = body.get('step_id')
    def remove_step(node_list):
        for i, s in enumerate(node_list):
            if s['id'] == step_id:
                node_list.pop(i)
                return True
            if remove_step(s.get('children', [])): return True
        return False
    data = _load_electronics()
    for proj in data.get('projects', []):
        if proj['id'] == pid:
            remove_step(proj.get('plan', []))
            break
    _save_electronics(data)
    return jsonify({'status': 'success'})

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
            checks = json.load(f).get("checks", [])
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

# ============ DAILY TASKS API ============
def load_daily_tasks():
    if os.path.exists(DAILY_TASKS_FILE):
        with open(DAILY_TASKS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"tasks": [], "checks": {}}

def save_daily_tasks(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DAILY_TASKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

@app.route('/api/daily-tasks', methods=['GET'])
@requires_auth
def get_daily_tasks():
    data = load_daily_tasks()
    return jsonify(data)

@app.route('/api/daily-tasks/add', methods=['POST'])
@requires_auth
def add_daily_task():
    req = request.json or {}
    name = req.get('name', '').strip()
    emoji = req.get('emoji', '✅')
    if not name:
        return jsonify({"status": "error", "message": "Nume lipsă"}), 400
    import time
    data = load_daily_tasks()
    task = {
        "id": str(int(time.time() * 1000)),
        "name": name,
        "emoji": emoji,
        "created_at": datetime.now().strftime("%Y-%m-%d")
    }
    data["tasks"].append(task)
    save_daily_tasks(data)
    return jsonify({"status": "success", "task": task})

@app.route('/api/daily-tasks/delete', methods=['POST'])
@requires_auth
def delete_daily_task():
    req = request.json or {}
    task_id = req.get('id')
    if not task_id:
        return jsonify({"status": "error", "message": "ID lipsă"}), 400
    data = load_daily_tasks()
    data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
    # Also remove all checks for this task
    for date_key in data["checks"]:
        data["checks"][date_key] = [tid for tid in data["checks"][date_key] if tid != task_id]
    save_daily_tasks(data)
    return jsonify({"status": "success"})

@app.route('/api/daily-tasks/check', methods=['POST'])
@requires_auth
def check_daily_task():
    req = request.json or {}
    task_id = req.get('id')
    date = req.get('date', datetime.now().strftime("%Y-%m-%d"))
    done = req.get('done', True)  # True = bifat, False = debifat
    if not task_id:
        return jsonify({"status": "error", "message": "ID lipsă"}), 400
    data = load_daily_tasks()
    if date not in data["checks"]:
        data["checks"][date] = []
    if done:
        if task_id not in data["checks"][date]:
            data["checks"][date].append(task_id)
    else:
        data["checks"][date] = [tid for tid in data["checks"][date] if tid != task_id]
    save_daily_tasks(data)
    return jsonify({"status": "success"})

@app.route('/api/daily-tasks/history', methods=['GET'])
@requires_auth
def daily_tasks_history():
    """Returns check history for the last N days per task."""
    days = int(request.args.get('days', 30))
    data = load_daily_tasks()
    end = datetime.now()
    start = end - timedelta(days=days)
    # Build list of dates
    date_range = []
    cur = start
    while cur <= end:
        date_range.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    result = []
    for task in data["tasks"]:
        tid = task["id"]
        # Count from created_at
        created = task.get("created_at", date_range[0])
        days_active = max(1, (datetime.now() - datetime.strptime(max(created, date_range[0]), "%Y-%m-%d")).days + 1)
        checked_dates = [d for d in date_range if d >= created and tid in data["checks"].get(d, [])]
        possible_dates = [d for d in date_range if d >= created]
        result.append({
            "id": tid,
            "name": task["name"],
            "emoji": task.get("emoji", "✅"),
            "created_at": created,
            "date_range": date_range,
            "checked_dates": checked_dates,
            "days_done": len(checked_dates),
            "days_possible": len(possible_dates),
            "streak_pct": round(len(checked_dates) / max(len(possible_dates), 1) * 100)
        })
    return jsonify({"tasks": result, "checks": data["checks"]})

# ============ FINANCE / BANI ============

# --- FINANCE ---
FINANCE_DIR = os.path.join(DATA_DIR, "finance")
LEGACY_FINANCE_FILE = os.path.join(DATA_DIR, "finance.json")

def _load_json_list(filepath):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def _save_json_list(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def _load_finance():
    # Migrare automată din fișierul vechi dacă există și nu a fost migrat
    if os.path.exists(LEGACY_FINANCE_FILE) and not os.path.exists(FINANCE_DIR):
        with open(LEGACY_FINANCE_FILE, 'r', encoding='utf-8') as f:
            old_data = json.load(f)
        _save_finance(old_data)
        os.rename(LEGACY_FINANCE_FILE, LEGACY_FINANCE_FILE + ".bak")

    return {
        "accounts": _load_json_list(os.path.join(FINANCE_DIR, "accounts.json")),
        "transactions": _load_json_list(os.path.join(FINANCE_DIR, "transactions.json")),
        "debts": _load_json_list(os.path.join(FINANCE_DIR, "debts.json")),
        "inventory": _load_json_list(os.path.join(FINANCE_DIR, "inventory_active.json")) + \
                     _load_json_list(os.path.join(FINANCE_DIR, "inventory_sold.json")),
        "investment_log": _load_json_list(os.path.join(FINANCE_DIR, "investment_log.json"))
    }

def _save_finance(data):
    os.makedirs(FINANCE_DIR, exist_ok=True)
    _save_json_list(os.path.join(FINANCE_DIR, "accounts.json"), data.get("accounts", []))
    _save_json_list(os.path.join(FINANCE_DIR, "transactions.json"), data.get("transactions", []))
    _save_json_list(os.path.join(FINANCE_DIR, "debts.json"), data.get("debts", []))
    
    # Separăm inventarul pentru claritate vizuală în fișiere
    inventory = data.get("inventory", [])
    active_inv = [i for i in inventory if i.get("status") == "active"]
    sold_inv = [i for i in inventory if i.get("status") == "sold"]
    
    _save_json_list(os.path.join(FINANCE_DIR, "inventory_active.json"), active_inv)
    _save_json_list(os.path.join(FINANCE_DIR, "inventory_sold.json"), sold_inv)
    _save_json_list(os.path.join(FINANCE_DIR, "investment_log.json"), data.get("investment_log", []))

def _calc_balance(account_id, transactions):
    """Calculate current balance for one account (all tx types included)."""
    total = 0.0
    for tx in transactions:
        if tx.get('account_id') == account_id:
            if tx.get('type') == 'in':
                total += float(tx.get('amount', 0))
            else:
                total -= float(tx.get('amount', 0))
    return round(total, 2)

def _calc_inv_summary(inventory, investment_log):
    """Calculate separated investment statistics for the Investment Hub."""
    active = [i for i in inventory if i.get('status') == 'active']
    sold   = [i for i in inventory if i.get('status') == 'sold']

    # 1. STOC CURENT (Active)
    active_cost = sum(float(i.get('cost_basis', 0)) for i in active)
    active_estimated_value = sum(float(i.get('estimated_value', 0)) for i in active)
    active_potential_profit = active_estimated_value - active_cost
    active_roi_pct = round((active_potential_profit / active_cost * 100) if active_cost > 0 else 0, 1)

    # 2. PERFORMANȚĂ TOTALĂ (All-Time)
    total_invested = sum(float(i.get('cost_basis', 0)) for i in inventory)
    total_recovered = sum(float(i.get('sold_amount', 0)) for i in sold)
    
    total_cost_sold = sum(float(i.get('cost_basis', 0)) for i in sold)
    realized_profit = total_recovered - total_cost_sold
    
    total_roi_pct = round(((realized_profit + active_potential_profit) / total_invested * 100) if total_invested > 0 else 0, 1)

    # 3. EXPUNERE / RISC (Bani blocați)
    current_risk = total_invested - total_recovered

    return {
        'active_cost': round(active_cost, 2),
        'active_estimated_value': round(active_estimated_value, 2),
        'active_potential_profit': round(active_potential_profit, 2),
        'active_roi_pct': active_roi_pct,
        
        'total_invested': round(total_invested, 2),
        'total_recovered': round(total_recovered, 2),
        'realized_profit': round(realized_profit, 2),
        'total_roi_pct': total_roi_pct,
        'current_risk': round(current_risk, 2),
        
        'active_count': len(active),
        'sold_count': len(sold),
    }

@app.route('/bani')
@requires_auth
def bani_page():
    return render_template('finance.html', active_page='bani')

@app.route('/api/finance/data', methods=['GET'])
@requires_auth
def finance_data():
    data = _load_finance()
    accounts     = data.get('accounts', [])
    transactions = data.get('transactions', [])
    debts        = data.get('debts', [])
    inventory    = data.get('inventory', [])
    inv_log      = data.get('investment_log', [])

    # Attach balances to accounts
    for acc in accounts:
        acc['balance'] = _calc_balance(acc['id'], transactions)

    total_balance = sum(a['balance'] for a in accounts)

    # P&L excludes invest/recover/transfer tags so totals reflect real cash flow
    real_in  = sum(float(t['amount']) for t in transactions
                   if t.get('type') == 'in'  and t.get('tag') not in ('transfer',))
    real_out = sum(float(t['amount']) for t in transactions
                   if t.get('type') == 'out' and t.get('tag') not in ('invest', 'transfer'))

    inv_summary = _calc_inv_summary(inventory, inv_log)

    return jsonify({
        'accounts':      accounts,
        'transactions':  sorted(transactions, key=lambda x: x.get('date',''), reverse=True),
        'debts':         debts,
        'inventory':     sorted(inventory, key=lambda x: x.get('date_bought',''), reverse=True),
        'investment_log': sorted(inv_log, key=lambda x: x.get('date',''), reverse=True),
        'summary': {
            'total':     round(total_balance, 2),
            'total_in':  round(real_in, 2),
            'total_out': round(real_out, 2)
        },
        'inv_summary': inv_summary
    })

@app.route('/api/finance/history', methods=['GET'])
@requires_auth
def finance_history():
    """Return cumulative balance per day, optionally filtered by account_id."""
    account_id = request.args.get('account_id', None)
    days_back = int(request.args.get('days', 60))
    data = _load_finance()
    transactions = data.get('transactions', [])
    accounts = data.get('accounts', [])

    if account_id and account_id != 'all':
        txs = [t for t in transactions if t.get('account_id') == account_id]
    else:
        txs = transactions

    # Build day-by-day history
    today = datetime.now().date()
    start_date = today - timedelta(days=days_back)
    day_deltas = {}
    for tx in txs:
        d = tx.get('date', '')
        try:
            dt = datetime.strptime(d, '%Y-%m-%d').date()
        except:
            continue
        if dt < start_date:
            continue
        delta = float(tx.get('amount', 0)) * (1 if tx.get('type') == 'in' else -1)
        day_deltas[d] = day_deltas.get(d, 0) + delta

    # Start balance = all transactions before start_date
    start_balance = 0.0
    for tx in txs:
        try:
            dt = datetime.strptime(tx.get('date', ''), '%Y-%m-%d').date()
            if dt < start_date:
                start_balance += float(tx.get('amount', 0)) * (1 if tx.get('type') == 'in' else -1)
        except:
            pass

    labels = []
    values = []
    running = start_balance
    cur = start_date
    while cur <= today:
        d_str = cur.strftime('%Y-%m-%d')
        running += day_deltas.get(d_str, 0)
        labels.append(d_str)
        values.append(round(running, 2))
        cur += timedelta(days=1)

    return jsonify({'labels': labels, 'values': values})

@app.route('/api/finance/account/add', methods=['POST'])
@requires_auth
def finance_account_add():
    import time
    data = _load_finance()
    body = request.json or {}
    name = body.get('name', '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Nume lipsă'}), 400
    acc = {
        'id': f"acc_{int(time.time()*1000)}",
        'name': name,
        'color': body.get('color', '#7c6aff'),
        'icon': body.get('icon', '💰')
    }
    data.setdefault('accounts', []).append(acc)
    _save_finance(data)
    return jsonify({'status': 'success', 'account': acc})

@app.route('/api/finance/account/edit', methods=['POST'])
@requires_auth
def finance_account_edit():
    data = _load_finance()
    body = request.json or {}
    acc_id = body.get('id')
    for acc in data.get('accounts', []):
        if acc['id'] == acc_id:
            if 'name' in body: acc['name'] = body['name']
            if 'color' in body: acc['color'] = body['color']
            if 'icon' in body: acc['icon'] = body['icon']
            break
    _save_finance(data)
    return jsonify({'status': 'success'})

@app.route('/api/finance/account/delete', methods=['POST'])
@requires_auth
def finance_account_delete():
    data = _load_finance()
    body = request.json or {}
    acc_id = body.get('id')
    data['accounts'] = [a for a in data.get('accounts', []) if a['id'] != acc_id]
    data['transactions'] = [t for t in data.get('transactions', []) if t.get('account_id') != acc_id]
    _save_finance(data)
    return jsonify({'status': 'success'})

@app.route('/api/finance/transaction/add', methods=['POST'])
@requires_auth
def finance_transaction_add():
    import time
    data = _load_finance()
    body = request.json or {}
    account_id = body.get('account_id', '')
    amount = body.get('amount', 0)
    tx_type = body.get('type', 'in')  # 'in' or 'out'
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except:
        return jsonify({'status': 'error', 'message': 'Sumă invalidă'}), 400
    tx = {
        'id': f"tx_{int(time.time()*1000)}",
        'account_id': account_id,
        'amount': amount,
        'type': tx_type,
        'note': body.get('note', '').strip(),
        'date': body.get('date', datetime.now().strftime('%Y-%m-%d')),
        'created_at': datetime.now().isoformat()
    }
    data.setdefault('transactions', []).append(tx)
    _save_finance(data)
    # Return updated balance for this account
    new_balance = _calc_balance(account_id, data['transactions'])
    return jsonify({'status': 'success', 'transaction': tx, 'new_balance': new_balance})

@app.route('/api/finance/transaction/delete', methods=['POST'])
@requires_auth
def finance_transaction_delete():
    data = _load_finance()
    body = request.json or {}
    tx_id = body.get('id')
    data['transactions'] = [t for t in data.get('transactions', []) if t['id'] != tx_id]
    _save_finance(data)
    return jsonify({'status': 'success'})

# --- DATORII ---
@app.route('/api/finance/debt/add', methods=['POST'])
@requires_auth
def finance_debt_add():
    import time
    data = _load_finance()
    body = request.json or {}
    name = body.get('name', '').strip()
    amount = body.get('amount', 0)
    try:
        amount = float(amount)
        if amount <= 0: raise ValueError
    except:
        return jsonify({'status': 'error', 'message': 'Sumă invalidă'}), 400
    debt = {
        'id': f"debt_{int(time.time()*1000)}",
        'name': name,
        'amount': amount,
        'direction': body.get('direction', 'owed_to_me'),  # 'owed_to_me' or 'i_owe'
        'reason': body.get('reason', '').strip(),
        'date': body.get('date', datetime.now().strftime('%Y-%m-%d')),
        'settled': False,
        'created_at': datetime.now().isoformat()
    }
    data.setdefault('debts', []).append(debt)
    _save_finance(data)
    return jsonify({'status': 'success', 'debt': debt})

@app.route('/api/finance/debt/settle', methods=['POST'])
@requires_auth
def finance_debt_settle():
    data = _load_finance()
    body = request.json or {}
    debt_id = body.get('id')
    for d in data.get('debts', []):
        if d['id'] == debt_id:
            d['settled'] = not d.get('settled', False)
            d['settled_at'] = datetime.now().isoformat()
            break
    _save_finance(data)
    return jsonify({'status': 'success'})

@app.route('/api/finance/debt/delete', methods=['POST'])
@requires_auth
def finance_debt_delete():
    data = _load_finance()
    body = request.json or {}
    debt_id = body.get('id')
    data['debts'] = [d for d in data.get('debts', []) if d['id'] != debt_id]
    _save_finance(data)
    return jsonify({'status': 'success'})

# --- TRANSFER ÎNTRE CONTURI ---
@app.route('/api/finance/transfer', methods=['POST'])
@requires_auth
def finance_transfer():
    """Move money from one account to another. Creates 2 tagged transactions."""
    import time
    data = _load_finance()
    body = request.json or {}
    src_id  = body.get('source_account_id', '')
    dst_id  = body.get('dest_account_id', '')
    amount  = body.get('amount', 0)
    note    = body.get('note', '').strip()
    date    = body.get('date', datetime.now().strftime('%Y-%m-%d'))

    try:
        amount = float(amount)
        if amount <= 0: raise ValueError
    except:
        return jsonify({'status': 'error', 'message': 'Sumă invalidă'}), 400

    if src_id == dst_id:
        return jsonify({'status': 'error', 'message': 'Conturile sursă și destinație sunt identice'}), 400

    ts = int(time.time() * 1000)
    transfer_ref = f"transfer_{ts}"
    out_tx = {
        'id': f"tx_{ts}_out",
        'account_id': src_id,
        'amount': amount,
        'type': 'out',
        'tag': 'transfer',
        'note': note or f'Transfer → cont',
        'transfer_ref': transfer_ref,
        'date': date,
        'created_at': datetime.now().isoformat()
    }
    in_tx = {
        'id': f"tx_{ts}_in",
        'account_id': dst_id,
        'amount': amount,
        'type': 'in',
        'tag': 'transfer',
        'note': note or f'Transfer ← cont',
        'transfer_ref': transfer_ref,
        'date': date,
        'created_at': datetime.now().isoformat()
    }
    data.setdefault('transactions', []).extend([out_tx, in_tx])
    _save_finance(data)
    return jsonify({'status': 'success', 'transfer_ref': transfer_ref})


# --- INVEST MONEY ---
@app.route('/api/finance/invest', methods=['POST'])
@requires_auth
def finance_invest():
    """Deduct from source account and add product to inventory."""
    import time
    data = _load_finance()
    body = request.json or {}
    acc_id   = body.get('source_account_id', '')
    amount   = body.get('amount', 0)
    name     = body.get('name', '').strip()
    est_val  = body.get('estimated_value', 0)
    note     = body.get('note', '').strip()
    date     = body.get('date', datetime.now().strftime('%Y-%m-%d'))

    try:
        amount  = float(amount)
        est_val = float(est_val) if est_val else amount
        if amount <= 0: raise ValueError
    except:
        return jsonify({'status': 'error', 'message': 'Sumă invalidă'}), 400

    if not name:
        return jsonify({'status': 'error', 'message': 'Introduceți un nume pentru produs'}), 400

    ts = int(time.time() * 1000)
    # Transaction that reduces account balance
    tx = {
        'id': f"tx_{ts}",
        'account_id': acc_id,
        'amount': amount,
        'type': 'out',
        'tag': 'invest',
        'note': f'Investiție: {name}',
        'date': date,
        'created_at': datetime.now().isoformat()
    }
    inv_id = f"inv_{ts}"
    inv_item = {
        'id': inv_id,
        'name': name,
        'cost_basis': amount,
        'estimated_value': est_val,
        'source_account_id': acc_id,
        'date_bought': date,
        'status': 'active',
        'sold_at': None,
        'sold_amount': None,
        'sold_to_account_id': None,
        'note': note,
        'created_at': datetime.now().isoformat()
    }
    inv_log = {
        'id': f"ilog_{ts}",
        'type': 'invest',
        'inventory_id': inv_id,
        'amount': amount,
        'account_id': acc_id,
        'profit': None,
        'date': date,
        'note': note
    }
    data.setdefault('transactions', []).append(tx)
    data.setdefault('inventory', []).append(inv_item)
    data.setdefault('investment_log', []).append(inv_log)
    _save_finance(data)
    return jsonify({'status': 'success', 'inventory_item': inv_item})


# --- RECOVER FUNDS (SELL) ---
@app.route('/api/finance/recover', methods=['POST'])
@requires_auth
def finance_recover():
    """Mark inventory item as sold and credit dest account. Calculate profit."""
    import time
    data = _load_finance()
    body = request.json or {}
    inv_id   = body.get('inventory_id', '')
    dst_id   = body.get('dest_account_id', '')
    amount   = body.get('amount', 0)
    note     = body.get('note', '').strip()
    date     = body.get('date', datetime.now().strftime('%Y-%m-%d'))

    try:
        amount = float(amount)
        if amount <= 0: raise ValueError
    except:
        return jsonify({'status': 'error', 'message': 'Sumă invalidă'}), 400

    inv_item = next((i for i in data.get('inventory', []) if i['id'] == inv_id), None)
    if not inv_item:
        return jsonify({'status': 'error', 'message': 'Produs negăsit'}), 404
    if inv_item.get('status') == 'sold':
        return jsonify({'status': 'error', 'message': 'Produsul a fost deja vândut'}), 400

    profit = round(amount - float(inv_item.get('cost_basis', 0)), 2)
    ts = int(time.time() * 1000)

    # Update inventory
    inv_item['status']             = 'sold'
    inv_item['sold_at']            = date
    inv_item['sold_amount']        = amount
    inv_item['sold_to_account_id'] = dst_id

    # Transaction crediting dest account
    tx = {
        'id': f"tx_{ts}",
        'account_id': dst_id,
        'amount': amount,
        'type': 'in',
        'tag': 'recover',
        'note': note or f'Vânzare: {inv_item["name"]}',
        'date': date,
        'created_at': datetime.now().isoformat()
    }
    inv_log = {
        'id': f"ilog_{ts}",
        'type': 'recover',
        'inventory_id': inv_id,
        'name': inv_item['name'],
        'amount': amount,
        'cost_basis': inv_item.get('cost_basis', 0),
        'account_id': dst_id,
        'profit': profit,
        'date': date,
        'note': note
    }
    data.setdefault('transactions', []).append(tx)
    data.setdefault('investment_log', []).append(inv_log)
    _save_finance(data)
    return jsonify({'status': 'success', 'profit': profit, 'transaction': tx})


# --- INVENTORY CRUD ---
@app.route('/api/finance/inventory/edit', methods=['POST'])
@requires_auth
def finance_inventory_edit():
    data = _load_finance()
    body = request.json or {}
    inv_id = body.get('id')
    for item in data.get('inventory', []):
        if item['id'] == inv_id:
            if 'name' in body:            item['name']            = body['name']
            if 'cost_basis' in body:      item['cost_basis']      = float(body['cost_basis'])
            if 'estimated_value' in body: item['estimated_value'] = float(body['estimated_value'])
            if 'note' in body:            item['note']            = body['note']
            break
    _save_finance(data)
    return jsonify({'status': 'success'})

@app.route('/api/finance/inventory/delete', methods=['POST'])
@requires_auth
def finance_inventory_delete():
    data = _load_finance()
    body = request.json or {}
    inv_id = body.get('id')
    item = next((i for i in data.get('inventory', []) if i['id'] == inv_id), None)
    if item and item.get('status') == 'sold':
        return jsonify({'status': 'error', 'message': 'Nu poți șterge un produs vândut'}), 400
    data['inventory'] = [i for i in data.get('inventory', []) if i['id'] != inv_id]
    # Also remove associated invest log + reverse the account deduction tx
    data['investment_log'] = [l for l in data.get('investment_log', [])
                               if not (l.get('inventory_id') == inv_id and l.get('type') == 'invest')]
    _save_finance(data)
    return jsonify({'status': 'success'})


# ==============================================================
# SCENES API
# ==============================================================

def _load_scenes():
    if os.path.exists(SCENES_FILE):
        try:
            with open(SCENES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"scenes": []}

def _save_scenes(data):
    with open(SCENES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

@app.route('/api/scenes', methods=['GET'])
@requires_auth
def get_scenes():
    data = _load_scenes()
    return jsonify(data)

@app.route('/api/scenes/save', methods=['POST'])
@requires_auth
def save_scene():
    body = request.json or {}
    data = _load_scenes()
    scene_id = body.get('id')
    if scene_id:
        # Update existing
        for i, s in enumerate(data['scenes']):
            if s['id'] == scene_id:
                data['scenes'][i].update({
                    'name': body.get('name', s['name']),
                    'emoji': body.get('emoji', s.get('emoji', '🎬')),
                    'music_prompt': body.get('music_prompt', s.get('music_prompt', '')),
                    'lights': body.get('lights', s.get('lights', {})),
                    'updated_at': datetime.now().isoformat()
                })
                break
    else:
        # New scene
        import uuid as _uuid
        new_scene = {
            'id': str(_uuid.uuid4()),
            'name': body.get('name', 'Scenă nouă'),
            'emoji': body.get('emoji', '🎬'),
            'music_prompt': body.get('music_prompt', ''),
            'lights': body.get('lights', {}),
            'created_at': datetime.now().isoformat()
        }
        data['scenes'].append(new_scene)
        scene_id = new_scene['id']
    _save_scenes(data)
    return jsonify({'status': 'success', 'id': scene_id})

@app.route('/api/scenes/delete', methods=['POST'])
@requires_auth
def delete_scene():
    body = request.json or {}
    scene_id = body.get('id')
    data = _load_scenes()
    data['scenes'] = [s for s in data['scenes'] if s['id'] != scene_id]
    _save_scenes(data)
    return jsonify({'status': 'success'})

@app.route('/api/scenes/activate', methods=['POST'])
@requires_auth
def activate_scene():
    body = request.json or {}
    scene_id = body.get('id')
    data = _load_scenes()
    scene = next((s for s in data['scenes'] if s['id'] == scene_id), None)
    if not scene:
        return jsonify({'status': 'error', 'message': 'Scena nu există'}), 404

    results = {'music': None, 'lights': None}

    # --- Activate lights ---
    lights = scene.get('lights', {})
    if lights:
        try:
            import requests as _req
            from config import WLED_IP_MAIN, WLED_IP_FLOOR
            from concurrent.futures import ThreadPoolExecutor
            def _send(ip, payload):
                try:
                    _req.post(f"http://{ip}/json/state", json=payload, timeout=2.0)
                except Exception as e:
                    pass
            with ThreadPoolExecutor() as ex:
                if 'main' in lights:
                    ex.submit(_send, WLED_IP_MAIN, lights['main'])
                if 'floor' in lights:
                    ex.submit(_send, WLED_IP_FLOOR, lights['floor'])
            results['lights'] = 'ok'
        except Exception as e:
            results['lights'] = f'error: {str(e)}'

    # --- Activate music ---
    music_prompt = scene.get('music_prompt', '').strip()
    if music_prompt:
        try:
            sys.path.insert(0, BASE_DIR)
            from agents.music_agent import MusicAgent
            dj = MusicAgent()
            result = dj.process_request(music_prompt)
            results['music'] = result.get('status', 'ok') if result else 'ok'
        except Exception as e:
            results['music'] = f'error: {str(e)}'

    return jsonify({'status': 'success', 'results': results, 'scene_name': scene.get('name', '')})

@app.route('/api/scenes/wled-snapshot', methods=['GET'])
@requires_auth
def wled_snapshot():
    """Capturează starea curentă din ambele zone WLED."""
    try:
        import requests as _req
        from config import WLED_IP_MAIN, WLED_IP_FLOOR
        from concurrent.futures import ThreadPoolExecutor

        def _get(ip):
            try:
                r = _req.get(f"http://{ip}/json/state", timeout=1.5)
                if r.status_code == 200:
                    d = r.json()
                    return {"on": d.get("on"), "bri": d.get("bri"), "seg": d.get("seg", [])}
            except:
                pass
            return None

        with ThreadPoolExecutor() as ex:
            f_main  = ex.submit(_get, WLED_IP_MAIN)
            f_floor = ex.submit(_get, WLED_IP_FLOOR)
            main_state  = f_main.result()
            floor_state = f_floor.result()

        if main_state is None and floor_state is None:
            return jsonify({'status': 'error', 'message': 'WLED offline sau inaccesibil'}), 503

        return jsonify({'status': 'ok', 'main': main_state, 'floor': floor_state})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    print("Pornesc Dashboard-ul Chronos...")
    app.run(host='0.0.0.0', port=5000, debug=True)