import os
import json
import glob
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, Response, render_template, jsonify

# --- CONFIGURARE SECURITATE ---
USERNAME = "admin"
PASSWORD = "123" # SCHIMBĂ ASTA

# --- CONFIGURARE CĂI ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
LOGS_DIR = os.path.join(BASE_DIR, "chronos_data", "logs")
TARGETS_FILE = os.path.join(BASE_DIR, "chronos_data", "targets.json")
COMPLETED_FILE = os.path.join(BASE_DIR, "chronos_data", "archive", "completed_goals.json")

app = Flask(__name__)

# --- SISTEM DE AUTENTIFICARE ---
def check_auth(username, password):
    return username == USERNAME and password == PASSWORD

def authenticate():
    return Response(
        'Acces interzis. Te rog să te autentifici cu user și parolă.\n', 401,
        {'WWW-Authenticate': 'Basic realm="Chronos Core Login"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- API JURNAL ---
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
            # Ne interesează doar logurile RAW și Rezumatele
            if log.get("type") in ["daily_entry", "daily_summary"]:
                # Folosim STRICT logical_date pentru grupare. Dacă nu există, o creăm.
                if "logical_date" in log:
                    day_string = log["logical_date"]
                else:
                    date_obj = datetime.fromisoformat(log['timestamp'])
                    # Logica de noapte (ora 00:00 - 05:00 trece la ziua precedentă)
                    shifted = date_obj - timedelta(hours=5)
                    day_string = shifted.strftime("%Y-%m-%d")
                
                log['display_time'] = datetime.fromisoformat(log['timestamp']).strftime("%H:%M")
                grouped_logs[day_string].append(log)
        except Exception: continue

    sorted_days = sorted(grouped_logs.keys(), reverse=True)
    
    result = []
    for day in sorted_days:
        day_logs = grouped_logs[day]
        # Sortăm în interiorul zilei cronologic, dar ne asigurăm că 'daily_summary' e primul.
        # Pentru asta, dăm un "scor" tipului de log: 0 pt summary (apare primul), 1 pt entry.
        day_logs.sort(key=lambda x: (0 if x.get("type") == "daily_summary" else 1, x['timestamp']), reverse=False)
        result.append({"date": day, "logs": day_logs})
        
    return result
# --- RUTE HTML ---
@app.route('/')
@requires_auth
def index():
    return render_template('index.html')

@app.route('/targets')
@requires_auth
def targets():
    return render_template('targets.html')

# --- RUTE API ---
@app.route('/api/logs')
@requires_auth
def api_logs():
    return jsonify(get_all_logs())

@app.route('/api/targets')
@requires_auth
def api_targets():
    if os.path.exists(TARGETS_FILE):
        with open(TARGETS_FILE, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({"goals": []})

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
            if goal['id'] == target_id:
                goal.update(data) # Actualizăm progresul
                if int(goal.get('progress', 0)) >= 100:
                    goal['progress'] = 100
                    goal['completed_at'] = datetime.now().isoformat()
                    archived_goal = goal
                else:
                    updated_goals.append(goal)
            else:
                updated_goals.append(goal)
        
        # Salvăm noile targeturi active
        file_data['goals'] = updated_goals
        with open(TARGETS_FILE, 'w', encoding='utf-8') as f:
            json.dump(file_data, f, indent=4, ensure_ascii=False)
            
        # Dacă targetul a atins 100%, îl mutăm în arhivă
        if archived_goal:
            if not os.path.exists(os.path.dirname(COMPLETED_FILE)):
                os.makedirs(os.path.dirname(COMPLETED_FILE), exist_ok=True)
            if not os.path.exists(COMPLETED_FILE):
                with open(COMPLETED_FILE, 'w', encoding='utf-8') as f: json.dump({"completed_history": []}, f)
            
            with open(COMPLETED_FILE, 'r+', encoding='utf-8') as f:
                comp_data = json.load(f)
                comp_data['completed_history'].append(archived_goal)
                f.seek(0)
                f.truncate()
                json.dump(comp_data, f, indent=4, ensure_ascii=False)
        
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    print("🌐 Pornesc Dashboard-ul Chronos...")
    app.run(host='0.0.0.0', port=5000, debug=True)