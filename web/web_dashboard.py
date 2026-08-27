import os
import json
import glob
import re
import sys
import colorsys
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
THEME_FILE = os.path.join(DATA_DIR, "theme.json")

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

    return jsonify({
        "date": today,
        "weight": weight_data,
        "food_check": food_data,
        "journal": journal_data,
        "phase": phase,
        "targets": targets_data,
        "measurements_due": measurements_due,
        "days_since_measurements": days_since_meas,
        "last_weight_ever": last_weight_ever,
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
            # Rezultatul se ia din valoarea ÎNTOARSĂ, nu din `last_result`.
            # Flask servește cererile pe fire paralele: două comenzi trimise
            # aproape simultan își suprascriau reciproc câmpul comun, iar
            # browserul primea răspunsul celeilalte comenzi.
            res = shared_dispatcher.process_text_command(text, None) or {}
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


# ============ CHRONOS — PREZENȚĂ ÎN INTERFAȚĂ ============
# Dock-ul din colț respiră în ritmul stării lui reale. Culoarea și viteza
# pulsului vin de aici; textul rămâne vag intenționat — Chronos nu-și
# verbalizează starea (vezi core/emotions.py).

_MOOD_PALETTE = {
    'agitat':    ('#ff5c7a', '255,92,122',  2.2, 'agitat'),
    'pe_val':    ('#3ce68f', '60,230,143',  3.4, 'în formă'),
    'plictisit': ('#59b8ff', '89,184,255',  8.5, 'te așteaptă'),
    'distant':   ('#6a6b90', '106,107,144', 7.5, 'distant'),
    'cald':      ('#ff7ac4', '255,122,196', 4.6, 'binedispus'),
    'calm':      ('#8b7aff', '139,122,255', 5.5, 'calm'),
}


def _chronos_state_payload():
    try:
        from core.emotions import get_state
        vals = get_state().snapshot()
    except Exception:
        vals = {}

    if not vals:
        color, rgb, pulse, short = _MOOD_PALETTE['calm']
        return {
            'available': False,
            'values': {},
            'mood': 'calm',
            'mood_label': 'în standby',
            'mood_short': 'sistem activ',
            'color': color, 'color_rgb': rgb, 'pulse': pulse,
            'wants_attention': False,
        }

    nerv = vals.get('nervozitate', 0)
    buc  = vals.get('bucurie', 0)
    plic = vals.get('plictiseala', 0)
    afec = vals.get('afectiune', 0)

    if nerv >= 60:
        key, label = 'agitat', 'nu e în cea mai bună zi'
    elif plic >= 70:
        key, label = 'plictisit', 'n-ați mai vorbit de mult'
    elif buc >= 70 and afec >= 60:
        key, label = 'cald', 'binedispus'
    elif buc >= 65:
        key, label = 'pe_val', 'e în formă'
    elif afec <= 30 or buc <= 25:
        key, label = 'distant', 'cam retras'
    else:
        key, label = 'calm', 'echilibrat'

    color, rgb, pulse, short = _MOOD_PALETTE[key]
    return {
        'available': True,
        'values': vals,
        'mood': key,
        'mood_label': label,
        'mood_short': short,
        'color': color,
        'color_rgb': rgb,
        'pulse': pulse,
        'wants_attention': plic >= 70 or nerv >= 80,
    }


@app.route('/api/chronos/state', methods=['GET'])
@requires_auth
def chronos_state():
    return jsonify(_chronos_state_payload())


@app.route('/api/chronos/chat', methods=['POST'])
@requires_auth
def chronos_chat():
    """Același dispatcher ca terminalul, dar servit dock-ului de pe orice pagină."""
    body = request.json or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'status': 'error', 'message': 'Mesaj gol'}), 400

    try:
        global shared_dispatcher
        if not shared_dispatcher:
            return jsonify({'status': 'error',
                            'message': 'Dispecerul central nu e conectat.'}), 500

        # Valoarea întoarsă, nu `last_result` — vezi nota de la /api/command.
        res = shared_dispatcher.process_text_command(text, None) or {}

        actions = []
        for a in res.get('actions', []):
            actions.append(a.get('text', '') if isinstance(a, dict) else str(a))

        return jsonify({
            'status':  'success',
            'intents': res.get('intents', ['general']),
            'reply':   res.get('reply'),
            'actions': actions,
            'state':   _chronos_state_payload(),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


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

def _new_id(prefix):
    import time
    return f"{prefix}_{int(time.time()*1000)}_{uuid.uuid4().hex[:5]}"


def _migrate_inventory(inventory, sales):
    """
    Aduce inventarul la modelul v2 (cantități + vânzări separate) fără să
    strice datele vechi. Rulează la fiecare load, e idempotentă.

      • repară ID-urile duplicate (bug vechi: „adaugă” refolosea același id
        dacă apăsai de mai multe ori în aceeași milisecundă)
      • quantity / qty_remaining / unit_cost pentru produsele vechi (qty = 1)
      • produsele deja vândute primesc o înregistrare de vânzare 'settled'
    """
    seen = set()
    sale_by_inv = {s.get("inventory_id") for s in sales}
    changed = False

    for item in inventory:
        # 1. ID unic
        iid = item.get("id")
        if not iid or iid in seen:
            item["id"] = _new_id("inv")
            changed = True
        seen.add(item["id"])

        # 2. Cantități
        if "quantity" not in item:
            qty = 1
            item["quantity"] = qty
            item["unit_cost"] = round(float(item.get("cost_basis", 0) or 0), 2)
            item["qty_remaining"] = 0 if item.get("status") == "sold" else qty
            changed = True
        item.setdefault("unit_cost", round(
            float(item.get("cost_basis", 0) or 0) / max(1, int(item.get("quantity", 1))), 2))
        item.setdefault("qty_remaining", 0 if item.get("status") == "sold" else int(item.get("quantity", 1)))
        item.setdefault("estimated_value", item.get("unit_cost", 0))
        item.setdefault("note", "")

        # 3. Vânzările vechi devin înregistrări de vânzare reglate
        if item.get("status") == "sold" and item["id"] not in sale_by_inv and item.get("sold_amount") is not None:
            qty = int(item.get("quantity", 1)) or 1
            unit_cost = float(item.get("unit_cost", 0) or 0)
            total = float(item.get("sold_amount", 0) or 0)
            sales.append({
                "id": _new_id("sale"),
                "inventory_id": item["id"],
                "name": item.get("name", ""),
                "qty": qty,
                "unit_cost": unit_cost,
                "unit_price": round(total / qty, 2) if qty else total,
                "total": round(total, 2),
                "cost_total": round(unit_cost * qty, 2),
                "profit": round(total - unit_cost * qty, 2),
                "status": "settled",
                "buyer": "",
                "note": item.get("note", ""),
                "date_sold": item.get("sold_at") or item.get("date_bought", ""),
                "expected_date": "",
                "account_id": item.get("sold_to_account_id"),
                "settled_at": item.get("sold_at") or "",
            })
            sale_by_inv.add(item["id"])
            changed = True

    return changed


def _load_finance():
    # Migrare automată din fișierul vechi dacă există și nu a fost migrat
    if os.path.exists(LEGACY_FINANCE_FILE) and not os.path.exists(FINANCE_DIR):
        with open(LEGACY_FINANCE_FILE, 'r', encoding='utf-8') as f:
            old_data = json.load(f)
        _save_finance(old_data)
        os.rename(LEGACY_FINANCE_FILE, LEGACY_FINANCE_FILE + ".bak")

    data = {
        "accounts": _load_json_list(os.path.join(FINANCE_DIR, "accounts.json")),
        "transactions": _load_json_list(os.path.join(FINANCE_DIR, "transactions.json")),
        "debts": _load_json_list(os.path.join(FINANCE_DIR, "debts.json")),
        "inventory": _load_json_list(os.path.join(FINANCE_DIR, "inventory_active.json")) + \
                     _load_json_list(os.path.join(FINANCE_DIR, "inventory_sold.json")),
        "sales": _load_json_list(os.path.join(FINANCE_DIR, "sales.json")),
        "investment_log": _load_json_list(os.path.join(FINANCE_DIR, "investment_log.json"))
    }

    if _migrate_inventory(data["inventory"], data["sales"]):
        _save_finance(data)

    return data

def _save_finance(data):
    os.makedirs(FINANCE_DIR, exist_ok=True)
    _save_json_list(os.path.join(FINANCE_DIR, "accounts.json"), data.get("accounts", []))
    _save_json_list(os.path.join(FINANCE_DIR, "transactions.json"), data.get("transactions", []))
    _save_json_list(os.path.join(FINANCE_DIR, "debts.json"), data.get("debts", []))

    # Separăm inventarul pentru claritate vizuală în fișiere
    inventory = data.get("inventory", [])
    active_inv = [i for i in inventory if i.get("status") != "sold"]
    sold_inv = [i for i in inventory if i.get("status") == "sold"]

    _save_json_list(os.path.join(FINANCE_DIR, "inventory_active.json"), active_inv)
    _save_json_list(os.path.join(FINANCE_DIR, "inventory_sold.json"), sold_inv)
    _save_json_list(os.path.join(FINANCE_DIR, "sales.json"), data.get("sales", []))
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

def _calc_inv_summary(inventory, sales):
    """
    Statistici pentru hub-ul de investiții, pe modelul v2 (cantități + vânzări).

    Trei planuri distincte, ca să nu se mai amestece:
      STOC       — ce ai pe raft acum (unități rămase)
      PE DRUM    — vândut, dar banii încă n-au intrat în conturi
      REALIZAT   — vânzări încasate efectiv
    """
    def f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    pending = [s for s in sales if s.get('status') == 'pending']
    settled = [s for s in sales if s.get('status') == 'settled']

    # 1. STOC CURENT — doar unitățile rămase
    stock_cost = sum(f(i.get('unit_cost')) * int(i.get('qty_remaining', 0) or 0) for i in inventory)
    stock_value = sum(f(i.get('estimated_value')) * int(i.get('qty_remaining', 0) or 0) for i in inventory)
    stock_potential = stock_value - stock_cost
    stock_roi_pct = round((stock_potential / stock_cost * 100) if stock_cost > 0 else 0, 1)
    units_in_stock = sum(int(i.get('qty_remaining', 0) or 0) for i in inventory)
    active_count = len([i for i in inventory if int(i.get('qty_remaining', 0) or 0) > 0])

    # 2. BANI PE DRUM — vândut, aștept plata
    pending_total = sum(f(s.get('total')) for s in pending)
    pending_profit = sum(f(s.get('profit')) for s in pending)
    pending_cost = sum(f(s.get('cost_total')) for s in pending)

    # 3. REALIZAT — bani intrați efectiv în conturi
    total_recovered = sum(f(s.get('total')) for s in settled)
    realized_profit = sum(f(s.get('profit')) for s in settled)

    # 4. ALL-TIME
    total_invested = sum(f(i.get('unit_cost')) * int(i.get('quantity', 0) or 0) for i in inventory)
    projected_profit = realized_profit + pending_profit + stock_potential
    total_roi_pct = round((projected_profit / total_invested * 100) if total_invested > 0 else 0, 1)

    # 5. EXPUNERE — capital care încă n-a revenit în conturi
    current_risk = total_invested - total_recovered

    return {
        # stoc
        'stock_cost': round(stock_cost, 2),
        'stock_value': round(stock_value, 2),
        'stock_potential': round(stock_potential, 2),
        'stock_roi_pct': stock_roi_pct,
        'units_in_stock': units_in_stock,
        'active_count': active_count,

        # pe drum
        'pending_total': round(pending_total, 2),
        'pending_profit': round(pending_profit, 2),
        'pending_cost': round(pending_cost, 2),
        'pending_count': len(pending),

        # realizat / all-time
        'total_invested': round(total_invested, 2),
        'total_recovered': round(total_recovered, 2),
        'realized_profit': round(realized_profit, 2),
        'projected_profit': round(projected_profit, 2),
        'total_roi_pct': total_roi_pct,
        'current_risk': round(current_risk, 2),
        'sold_count': len(settled),

        # compatibilitate cu numele vechi
        'active_cost': round(stock_cost, 2),
        'active_estimated_value': round(stock_value, 2),
        'active_potential_profit': round(stock_potential, 2),
        'active_roi_pct': stock_roi_pct,
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
    sales        = data.get('sales', [])
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

    inv_summary = _calc_inv_summary(inventory, sales)

    # Datorii nereglate — le arătăm în bilanțul de sus
    debt_owed = sum(float(d.get('amount', 0)) for d in debts
                    if d.get('direction') == 'owed_to_me' and not d.get('settled'))
    debt_owing = sum(float(d.get('amount', 0)) for d in debts
                     if d.get('direction') == 'i_owe' and not d.get('settled'))

    # Averea totală: lichid + stoc (la cost) + bani pe drum + net datorii
    net_worth = (total_balance + inv_summary['stock_cost']
                 + inv_summary['pending_total'] + debt_owed - debt_owing)

    return jsonify({
        'accounts':      accounts,
        'transactions':  sorted(transactions, key=lambda x: x.get('date',''), reverse=True),
        'debts':         debts,
        'inventory':     sorted(inventory, key=lambda x: x.get('date_bought',''), reverse=True),
        'sales':         sorted(sales, key=lambda x: x.get('date_sold',''), reverse=True),
        'investment_log': sorted(inv_log, key=lambda x: x.get('date',''), reverse=True),
        'summary': {
            'total':      round(total_balance, 2),
            'total_in':   round(real_in, 2),
            'total_out':  round(real_out, 2),
            'debt_owed':  round(debt_owed, 2),
            'debt_owing': round(debt_owing, 2),
            'net_worth':  round(net_worth, 2)
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


# ══════════════════════════════════════════════════════════════
#  INVESTIȚII v2 — cantități + vânzări în așteptare
# ══════════════════════════════════════════════════════════════

def _sync_item_status(item):
    """Un produs e 'sold' doar când nu mai are nicio unitate pe stoc."""
    item['qty_remaining'] = max(0, int(item.get('qty_remaining', 0) or 0))
    item['cost_basis'] = round(float(item.get('unit_cost', 0) or 0) * int(item.get('quantity', 0) or 0), 2)
    item['status'] = 'sold' if item['qty_remaining'] == 0 else 'active'
    return item


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# --- INVEST MONEY ---
@app.route('/api/finance/invest', methods=['POST'])
@requires_auth
def finance_invest():
    """Scade din cont și adaugă produsul (cu una sau mai multe bucăți) în stoc."""
    data = _load_finance()
    body = request.json or {}
    acc_id  = body.get('source_account_id', '')
    name    = (body.get('name') or '').strip()
    note    = (body.get('note') or '').strip()
    date    = body.get('date') or datetime.now().strftime('%Y-%m-%d')
    qty     = max(1, _int(body.get('quantity'), 1))

    # unit_cost e forma nouă; 'amount' rămâne acceptat pentru compatibilitate
    if body.get('unit_cost') not in (None, ''):
        unit_cost = _num(body.get('unit_cost'))
    else:
        unit_cost = _num(body.get('amount')) / qty

    est_val = _num(body.get('estimated_value')) or unit_cost

    if unit_cost <= 0:
        return jsonify({'status': 'error', 'message': 'Preț pe bucată invalid'}), 400
    if not name:
        return jsonify({'status': 'error', 'message': 'Introduceți un nume pentru produs'}), 400

    total = round(unit_cost * qty, 2)
    inv_id = _new_id('inv')

    tx = {
        'id': _new_id('tx'),
        'account_id': acc_id,
        'amount': total,
        'type': 'out',
        'tag': 'invest',
        'note': f'Investiție: {name}' + (f' ×{qty}' if qty > 1 else ''),
        'date': date,
        'created_at': datetime.now().isoformat()
    }
    inv_item = _sync_item_status({
        'id': inv_id,
        'name': name,
        'quantity': qty,
        'qty_remaining': qty,
        'unit_cost': round(unit_cost, 2),
        'cost_basis': total,
        'estimated_value': round(est_val, 2),
        'source_account_id': acc_id,
        'date_bought': date,
        'status': 'active',
        'note': note,
        'created_at': datetime.now().isoformat()
    })
    data.setdefault('transactions', []).append(tx)
    data.setdefault('inventory', []).append(inv_item)
    data.setdefault('investment_log', []).append({
        'id': _new_id('ilog'),
        'type': 'invest',
        'inventory_id': inv_id,
        'name': name,
        'qty': qty,
        'amount': total,
        'account_id': acc_id,
        'profit': None,
        'date': date,
        'note': note
    })
    _save_finance(data)
    return jsonify({'status': 'success', 'inventory_item': inv_item})


# --- ADD MORE UNITS OF AN EXISTING PRODUCT ---
@app.route('/api/finance/inventory/add-units', methods=['POST'])
@requires_auth
def finance_inventory_add_units():
    """Mai cumperi bucăți din acelaşi produs. Costul pe bucată devine media ponderată."""
    data = _load_finance()
    body = request.json or {}
    inv_id = body.get('id')
    qty    = max(1, _int(body.get('quantity'), 1))
    acc_id = body.get('source_account_id', '')
    date   = body.get('date') or datetime.now().strftime('%Y-%m-%d')
    note   = (body.get('note') or '').strip()

    item = next((i for i in data.get('inventory', []) if i.get('id') == inv_id), None)
    if not item:
        return jsonify({'status': 'error', 'message': 'Produs negăsit'}), 404

    unit_cost = _num(body.get('unit_cost')) or _num(item.get('unit_cost'))
    if unit_cost <= 0:
        return jsonify({'status': 'error', 'message': 'Preț pe bucată invalid'}), 400

    total = round(unit_cost * qty, 2)
    old_qty = _int(item.get('quantity'), 0)
    old_cost = _num(item.get('unit_cost'))

    # Media ponderată pe toate bucățile cumpărate vreodată
    new_qty = old_qty + qty
    item['unit_cost'] = round(((old_cost * old_qty) + total) / new_qty, 2) if new_qty else unit_cost
    item['quantity'] = new_qty
    item['qty_remaining'] = _int(item.get('qty_remaining'), 0) + qty
    if body.get('estimated_value') not in (None, ''):
        item['estimated_value'] = round(_num(body.get('estimated_value')), 2)
    _sync_item_status(item)

    data.setdefault('transactions', []).append({
        'id': _new_id('tx'),
        'account_id': acc_id,
        'amount': total,
        'type': 'out',
        'tag': 'invest',
        'note': f'Reaprovizionare: {item["name"]} ×{qty}',
        'date': date,
        'created_at': datetime.now().isoformat()
    })
    data.setdefault('investment_log', []).append({
        'id': _new_id('ilog'),
        'type': 'invest',
        'inventory_id': inv_id,
        'name': item['name'],
        'qty': qty,
        'amount': total,
        'account_id': acc_id,
        'profit': None,
        'date': date,
        'note': note or f'+{qty} buc.'
    })
    _save_finance(data)
    return jsonify({'status': 'success', 'inventory_item': item})


# --- SELL (instant sau în așteptare) ---
@app.route('/api/finance/sell', methods=['POST'])
@requires_auth
def finance_sell():
    return _do_sell(request.json or {})


def _do_sell(body):
    """
    Vinde una sau mai multe bucăți.

      mode='instant' → banii intră imediat în contul ales
      mode='pending' → marchezi vânzarea, dar banii sunt „pe drum”;
                       nu ating niciun cont până la /api/finance/sale/settle
    """
    data = _load_finance()
    inv_id   = body.get('inventory_id', '')
    qty      = max(1, _int(body.get('qty'), 1))
    mode     = body.get('mode', 'instant')
    dst_id   = body.get('dest_account_id', '')
    buyer    = (body.get('buyer') or '').strip()
    note     = (body.get('note') or '').strip()
    date     = body.get('date') or datetime.now().strftime('%Y-%m-%d')
    expected = body.get('expected_date') or ''

    item = next((i for i in data.get('inventory', []) if i.get('id') == inv_id), None)
    if not item:
        return jsonify({'status': 'error', 'message': 'Produs negăsit'}), 404

    remaining = _int(item.get('qty_remaining'), 0)
    if remaining <= 0:
        return jsonify({'status': 'error', 'message': 'Nu mai ai bucăți pe stoc din produsul ăsta'}), 400
    if qty > remaining:
        return jsonify({'status': 'error', 'message': f'Ai doar {remaining} buc. pe stoc'}), 400

    # unit_price nou; 'amount' (total) acceptat pentru compatibilitate
    if body.get('unit_price') not in (None, ''):
        unit_price = _num(body.get('unit_price'))
    else:
        unit_price = _num(body.get('amount')) / qty

    if unit_price <= 0:
        return jsonify({'status': 'error', 'message': 'Preț de vânzare invalid'}), 400
    if mode == 'instant' and not dst_id:
        return jsonify({'status': 'error', 'message': 'Alege contul în care intră banii'}), 400

    unit_cost  = _num(item.get('unit_cost'))
    total      = round(unit_price * qty, 2)
    cost_total = round(unit_cost * qty, 2)
    profit     = round(total - cost_total, 2)
    settled    = (mode == 'instant')

    sale = {
        'id': _new_id('sale'),
        'inventory_id': inv_id,
        'name': item.get('name', ''),
        'qty': qty,
        'unit_cost': round(unit_cost, 2),
        'unit_price': round(unit_price, 2),
        'total': total,
        'cost_total': cost_total,
        'profit': profit,
        'status': 'settled' if settled else 'pending',
        'buyer': buyer,
        'note': note,
        'date_sold': date,
        'expected_date': expected,
        'account_id': dst_id if settled else None,
        'settled_at': date if settled else None,
        'created_at': datetime.now().isoformat()
    }

    # Scoatem bucățile din stoc indiferent de mod — marfa a plecat
    item['qty_remaining'] = remaining - qty
    item['sold_at'] = date
    _sync_item_status(item)

    data.setdefault('sales', []).append(sale)

    if settled:
        data.setdefault('transactions', []).append({
            'id': _new_id('tx'),
            'account_id': dst_id,
            'amount': total,
            'type': 'in',
            'tag': 'recover',
            'sale_id': sale['id'],
            'note': note or f'Vânzare: {item["name"]}' + (f' ×{qty}' if qty > 1 else ''),
            'date': date,
            'created_at': datetime.now().isoformat()
        })

    data.setdefault('investment_log', []).append({
        'id': _new_id('ilog'),
        'type': 'recover' if settled else 'pending',
        'inventory_id': inv_id,
        'sale_id': sale['id'],
        'name': item.get('name', ''),
        'qty': qty,
        'amount': total,
        'cost_basis': cost_total,
        'account_id': dst_id if settled else None,
        'profit': profit,
        'date': date,
        'note': note or (f'Aștept banii de la {buyer}' if buyer else 'Aștept banii')
    })

    _save_finance(data)
    return jsonify({'status': 'success', 'sale': sale, 'profit': profit})


# --- ÎNCASEZ BANII UNEI VÂNZĂRI ÎN AȘTEPTARE ---
@app.route('/api/finance/sale/settle', methods=['POST'])
@requires_auth
def finance_sale_settle():
    data = _load_finance()
    body = request.json or {}
    sale_id = body.get('id')
    dst_id  = body.get('dest_account_id', '')
    date    = body.get('date') or datetime.now().strftime('%Y-%m-%d')

    sale = next((s for s in data.get('sales', []) if s.get('id') == sale_id), None)
    if not sale:
        return jsonify({'status': 'error', 'message': 'Vânzare negăsită'}), 404
    if sale.get('status') != 'pending':
        return jsonify({'status': 'error', 'message': 'Banii au fost deja încasați'}), 400
    if not dst_id:
        return jsonify({'status': 'error', 'message': 'Alege contul în care au intrat banii'}), 400

    # Poți corecta suma la încasare (a negociat, a dat mai puțin etc.)
    if body.get('amount') not in (None, ''):
        total = round(_num(body.get('amount')), 2)
        if total <= 0:
            return jsonify({'status': 'error', 'message': 'Sumă invalidă'}), 400
        sale['total'] = total
        sale['unit_price'] = round(total / max(1, _int(sale.get('qty'), 1)), 2)
        sale['profit'] = round(total - _num(sale.get('cost_total')), 2)

    sale['status'] = 'settled'
    sale['account_id'] = dst_id
    sale['settled_at'] = date

    data.setdefault('transactions', []).append({
        'id': _new_id('tx'),
        'account_id': dst_id,
        'amount': sale['total'],
        'type': 'in',
        'tag': 'recover',
        'sale_id': sale['id'],
        'note': f'Încasare vânzare: {sale.get("name", "")}'
                + (f' ×{sale.get("qty")}' if _int(sale.get('qty'), 1) > 1 else ''),
        'date': date,
        'created_at': datetime.now().isoformat()
    })
    data.setdefault('investment_log', []).append({
        'id': _new_id('ilog'),
        'type': 'recover',
        'inventory_id': sale.get('inventory_id'),
        'sale_id': sale['id'],
        'name': sale.get('name', ''),
        'qty': sale.get('qty'),
        'amount': sale['total'],
        'cost_basis': sale.get('cost_total'),
        'account_id': dst_id,
        'profit': sale.get('profit'),
        'date': date,
        'note': 'Bani încasați'
    })
    _save_finance(data)
    return jsonify({'status': 'success', 'sale': sale})


# --- ANULEZ O VÂNZARE ---
@app.route('/api/finance/sale/cancel', methods=['POST'])
@requires_auth
def finance_sale_cancel():
    """Anulează o vânzare în așteptare și pune bucățile înapoi pe stoc."""
    data = _load_finance()
    body = request.json or {}
    sale_id = body.get('id')

    sale = next((s for s in data.get('sales', []) if s.get('id') == sale_id), None)
    if not sale:
        return jsonify({'status': 'error', 'message': 'Vânzare negăsită'}), 404
    if sale.get('status') != 'pending':
        return jsonify({'status': 'error', 'message': 'Poți anula doar vânzările neîncasate'}), 400

    item = next((i for i in data.get('inventory', []) if i.get('id') == sale.get('inventory_id')), None)
    if item:
        item['qty_remaining'] = _int(item.get('qty_remaining'), 0) + _int(sale.get('qty'), 1)
        _sync_item_status(item)

    data['sales'] = [s for s in data.get('sales', []) if s.get('id') != sale_id]
    data['investment_log'] = [l for l in data.get('investment_log', [])
                              if l.get('sale_id') != sale_id]
    _save_finance(data)
    return jsonify({'status': 'success'})


# --- COMPATIBILITATE: vechiul /recover ---
@app.route('/api/finance/recover', methods=['POST'])
@requires_auth
def finance_recover():
    """Ruta veche: vinde tot stocul rămas dintr-un produs, direct în cont."""
    body = request.json or {}
    inv_id = body.get('inventory_id', '')
    item = next((i for i in _load_finance().get('inventory', []) if i.get('id') == inv_id), None)
    qty = max(1, _int(item.get('qty_remaining'), 1)) if item else 1

    return _do_sell({
        'inventory_id': inv_id,
        'qty': qty,
        'mode': 'instant',
        'dest_account_id': body.get('dest_account_id', ''),
        'amount': body.get('amount', 0),
        'note': body.get('note', ''),
        'date': body.get('date', '')
    })


# --- INVENTORY CRUD ---
@app.route('/api/finance/inventory/edit', methods=['POST'])
@requires_auth
def finance_inventory_edit():
    data = _load_finance()
    body = request.json or {}
    inv_id = body.get('id')
    for item in data.get('inventory', []):
        if item.get('id') == inv_id:
            if 'name' in body:            item['name'] = (body['name'] or '').strip() or item['name']
            if 'note' in body:            item['note'] = body['note']
            if 'estimated_value' in body: item['estimated_value'] = round(_num(body['estimated_value']), 2)
            if 'unit_cost' in body:       item['unit_cost'] = round(_num(body['unit_cost']), 2)
            if 'quantity' in body:
                new_total = max(0, _int(body['quantity'], _int(item.get('quantity'), 1)))
                sold_units = _int(item.get('quantity'), 0) - _int(item.get('qty_remaining'), 0)
                item['quantity'] = max(new_total, sold_units)
                item['qty_remaining'] = max(0, item['quantity'] - sold_units)
            if 'qty_remaining' in body:
                item['qty_remaining'] = max(0, _int(body['qty_remaining'], 0))
            _sync_item_status(item)
            break
    _save_finance(data)
    return jsonify({'status': 'success'})

@app.route('/api/finance/inventory/delete', methods=['POST'])
@requires_auth
def finance_inventory_delete():
    data = _load_finance()
    body = request.json or {}
    inv_id = body.get('id')

    has_sales = any(s.get('inventory_id') == inv_id for s in data.get('sales', []))
    if has_sales:
        return jsonify({'status': 'error',
                        'message': 'Produsul are vânzări înregistrate — nu-l pot șterge fără să strice istoricul'}), 400

    data['inventory'] = [i for i in data.get('inventory', []) if i.get('id') != inv_id]
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

def _fetch_wled_zones():
    """Interoghează ambele zone WLED în paralel. Returnează (main, floor);
    fiecare e un dict {on, bri, seg} sau None dacă zona nu a răspuns."""
    import requests as _req
    from config import WLED_IP_MAIN, WLED_IP_FLOOR
    from concurrent.futures import ThreadPoolExecutor

    def _get(ip):
        try:
            r = _req.get(f"http://{ip}/json/state", timeout=1.5)
            if r.status_code == 200:
                d = r.json()
                return {"on": d.get("on"), "bri": d.get("bri"), "seg": d.get("seg", [])}
        except Exception:
            pass
        return None

    with ThreadPoolExecutor() as ex:
        f_main = ex.submit(_get, WLED_IP_MAIN)
        f_floor = ex.submit(_get, WLED_IP_FLOOR)
        return f_main.result(), f_floor.result()


@app.route('/api/scenes/wled-snapshot', methods=['GET'])
@requires_auth
def wled_snapshot():
    """Capturează starea curentă din ambele zone WLED."""
    try:
        main_state, floor_state = _fetch_wled_zones()
        if main_state is None and floor_state is None:
            return jsonify({'status': 'error', 'message': 'WLED offline sau inaccesibil'}), 503
        return jsonify({'status': 'ok', 'main': main_state, 'floor': floor_state})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ==============================================================
# TEMA VIZUALĂ — culoarea de accent a interfeței
# ==============================================================
# Trei surse posibile pentru --primary (butoane, glow-uri, evidențieri):
#   manual — o culoare aleasă direct de Sergiu
#   wled   — media culorilor din benzile LED ale camerei, chiar acum
#   mood   — culoarea stării emoționale a lui Chronos (vezi core/emotions.py)

DEFAULT_ACCENT = '#8b7aff'
_HEX_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _load_theme():
    if os.path.exists(THEME_FILE):
        try:
            with open(THEME_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if cfg.get('mode') in ('manual', 'wled', 'mood') and _HEX_RE.match(cfg.get('color', '')):
                return cfg
        except Exception:
            pass
    return {'mode': 'manual', 'color': DEFAULT_ACCENT}


def _save_theme(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(THEME_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


_DEFAULT_H, _DEFAULT_L, _DEFAULT_S = colorsys.rgb_to_hls(
    *(int(DEFAULT_ACCENT[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
)


def _normalize_accent_hex(rgb):
    """Orice RGB brut (dintr-un LED, o poză etc.) devine o culoare de accent
    lizibilă pe fundal întunecat: nici prea stinsă, nici arzătoare.

    Lumină albă/gri (saturație ~0) nu are nicio culoare reală de extras — la
    saturație zero, nuanța (hue) e matematic nedefinită și oricare valoare
    calculată din zgomotul de rotunjire e arbitrară. În loc să „boostăm" acel
    zgomot într-o culoare la întâmplare (testat: gri pur ieșea roșu), păstrăm
    nuanța accentului implicit — camera nu are culoare, deci nici UI-ul nu-și
    schimbă una."""
    r, g, b = (max(0, min(255, float(v))) / 255.0 for v in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s <= 0.08:
        h = _DEFAULT_H
    s = max(0.42, min(0.92, s if s > 0.08 else 0.55))
    l = max(0.46, min(0.72, l))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return '#%02x%02x%02x' % (round(r2 * 255), round(g2 * 255), round(b2 * 255))


def _wled_accent_hex():
    """Media culorilor din zonele LED aprinse acum, normalizată ca accent UI.
    None dacă WLED e offline sau ambele zone sunt stinse."""
    try:
        main_state, floor_state = _fetch_wled_zones()
    except Exception:
        return None

    cols = []
    for st in (main_state, floor_state):
        if not st or st.get('on') is False:
            continue
        seg = (st.get('seg') or [{}])[0]
        col = (seg.get('col') or [[255, 255, 255]])[0]
        if col and len(col) >= 3 and any(col[:3]):
            cols.append(col[:3])

    if not cols:
        return None
    avg = [sum(c[i] for c in cols) / len(cols) for i in range(3)]
    return _normalize_accent_hex(avg)


def _resolve_theme(cfg):
    """Întoarce (culoare_efectivă, e_live, sursă). e_live=False înseamnă că
    sursa automată nu a răspuns și s-a căzut pe ultima culoare manuală."""
    mode = cfg.get('mode', 'manual')
    manual_color = cfg.get('color') or DEFAULT_ACCENT

    if mode == 'mood':
        try:
            return _chronos_state_payload()['color'], True, 'mood'
        except Exception:
            return manual_color, False, 'mood'

    if mode == 'wled':
        hexv = _wled_accent_hex()
        return (hexv, True, 'wled') if hexv else (manual_color, False, 'wled')

    return manual_color, True, 'manual'


@app.route('/api/theme', methods=['GET'])
@requires_auth
def get_theme():
    cfg = _load_theme()
    resolved, live, mode = _resolve_theme(cfg)
    return jsonify({
        'mode': cfg.get('mode', 'manual'),
        'color': cfg.get('color', DEFAULT_ACCENT),
        'resolved': resolved,
        'live': live,
    })


@app.route('/api/theme', methods=['POST'])
@requires_auth
def set_theme():
    body = request.json or {}
    cfg = _load_theme()

    mode = body.get('mode', cfg.get('mode', 'manual'))
    if mode not in ('manual', 'wled', 'mood'):
        return jsonify({'status': 'error', 'message': 'Mod invalid'}), 400
    cfg['mode'] = mode

    if 'color' in body:
        c = (body.get('color') or '').strip()
        if not _HEX_RE.match(c):
            return jsonify({'status': 'error', 'message': 'Culoare invalidă (aștept #rrggbb)'}), 400
        cfg['color'] = c

    _save_theme(cfg)
    resolved, live, _ = _resolve_theme(cfg)
    return jsonify({
        'status': 'success',
        'mode': cfg['mode'],
        'color': cfg.get('color', DEFAULT_ACCENT),
        'resolved': resolved,
        'live': live,
    })


if __name__ == '__main__':
    print("Pornesc Dashboard-ul Chronos...")
    app.run(host='0.0.0.0', port=5000, debug=True)