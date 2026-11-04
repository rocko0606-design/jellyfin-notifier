import os
import json
import threading
import time
import re
import requests
import xml.etree.ElementTree as ET
from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)
CONFIG_FILE = "/app/data/config.json"

DEFAULT_CONFIG = {
    "jellyfin_url": "http://192.168.0.73:8096",
    "jellyfin_api_key": "",
    "pushover_token": "",
    "pushover_user": "",
    "check_interval_hours": 6,
    "rss_url": "https://nox.to/releases?type=series",
    "selected_library_id": ""
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except:
            pass
    return DEFAULT_CONFIG

def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def send_pushover(title, message):
    config = load_config()
    token = config.get("pushover_token")
    user = config.get("pushover_user")
    if not token or not user:
        return False
    try:
        res = requests.post("https://api.pushover.net/1/messages.json", data={
            "token": token,
            "user": user,
            "title": title,
            "message": message
        }, timeout=5)
        return res.status_code == 200
    except Exception as e:
        print(f"Pushover-Fehler: {e}")
        return False

def get_jellyfin_libraries(jellyfin_url, api_key):
    if not jellyfin_url or not api_key:
        return []
    
    headers = {
        "X-Emby-Token": api_key,
        "Authorization": f'MediaBrowser Token="{api_key}"'
    }
    try:
        url = f"{jellyfin_url.rstrip('/')}/Library/VirtualFolders"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"Fehler beim Laden der Bibliotheken: {e}")
    return []

def get_library_series_with_ids(jellyfin_url, api_key, library_id):
    if not jellyfin_url or not api_key or not library_id:
        return []
    
    headers = {
        "X-Emby-Token": api_key,
        "Authorization": f'MediaBrowser Token="{api_key}"'
    }
    try:
        url = f"{jellyfin_url.rstrip('/')}/Items?ParentId={library_id}&includeItemTypes=Series&recursive=true"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            shows = [{"id": item.get("Id"), "name": item.get("Name")} for item in data.get("Items", []) if item.get("Name")]
            return shows
    except Exception as e:
        print(f"Fehler beim Abrufen der Serien: {e}")
    return []

def get_existing_episodes(jellyfin_url, api_key, series_id):
    headers = {
        "X-Emby-Token": api_key,
        "Authorization": f'MediaBrowser Token="{api_key}"'
    }
    try:
        url = f"{jellyfin_url.rstrip('/')}/Shows/{series_id}/Episodes"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            existing = set()
            for ep in data.get("Items", []):
                season = ep.get("ParentIndexNumber")
                number = ep.get("IndexNumber")
                runtime = ep.get("RunTimeTicks") or 0
                
                if season is not None and number is not None and runtime > 0:
                    existing.add(f"S{season:02d}E{number:02d}")
            return existing
    except Exception as e:
        print(f"Fehler beim Abrufen der Episoden für ID {series_id}: {e}")
    return set()

def normalize_name(name):
    if not name:
        return ""
    n = name.lower().strip()
    
    if "ncis origins" in n or "ncis: origins" in n:
        n = "navy cis: origins"
    elif n == "ncis":
        n = "navy cis"
        
    n = re.sub(r'\b(19|20)\d{2}\b', '', n)
    return re.sub(r'[^a-z0-9]', '', n)

def clean_feed_title(raw_title):
    if "—" in raw_title:
        base_text = raw_title.split("—")[0].strip()
    else:
        base_text = raw_title

    multi_match = re.search(r's(\d+)\s*e(\d+)\s*-\s*(?:e)?(\d+)', raw_title, re.IGNORECASE)
    
    ep_codes = []
    if multi_match:
        season = int(multi_match.group(1))
        start_ep = int(multi_match.group(2))
        end_ep = int(multi_match.group(3))
        for ep_num in range(start_ep, end_ep + 1):
            ep_codes.append(f"S{season:02d}E{ep_num:02d}")
        if "—" not in raw_title:
            base_text = raw_title[:multi_match.start()]
    else:
        match_s = re.search(r's(\d+)\s*e(\d+)', raw_title, re.IGNORECASE)
        match_x = re.search(r'(\d+)x(\d+)', raw_title)
        
        if match_s:
            ep_codes.append(f"S{int(match_s.group(1)):02d}E{int(match_s.group(2)):02d}")
            if "—" not in raw_title:
                base_text = raw_title[:match_s.start()]
        elif match_x:
            ep_codes.append(f"S{int(match_x.group(1)):02d}E{int(match_x.group(2)):02d}")
            if "—" not in raw_title:
                base_text = raw_title[:match_x.start()]

    if not base_text or "http" in base_text or len(base_text) < 2:
        match_any = re.search(r's\d+e\d+|\d+x\d+', raw_title, re.IGNORECASE)
        if match_any:
            base_text = raw_title[:match_any.start()]

    clean_name = base_text.replace('.', ' ').replace('_', ' ').strip()
    return clean_name, ep_codes

def perform_check():
    config = load_config()
    jellyfin_url = config.get("jellyfin_url")
    api_key = config.get("jellyfin_api_key")
    rss_url = config.get("rss_url")
    library_id = config.get("selected_library_id")
    
    print("Starte präzisen Smart-Abgleich...")
    results = []
    
    if not library_id:
        return [{"text": "⚠️ Keine Jellyfin-Bibliothek ausgewählt. Bitte in den Einstellungen festlegen.", "class": "warning"}]

    local_shows = get_library_series_with_ids(jellyfin_url, api_key, library_id)
    if not local_shows:
        return [{"text": "⚠️ Keine Serien in der ausgewählten Bibliothek gefunden oder Verbindung fehlgeschlagen.", "class": "warning"}]

    shows_dict = {}
    for show in local_shows:
        norm_key = normalize_name(show["name"])
        shows_dict[norm_key] = show

    feed_items = []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(rss_url, headers=headers, timeout=10)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title_elem = item.find("title")
                desc_elem = item.find("description")
                
                text_to_check = ""
                if title_elem is not None and title_elem.text:
                    text_to_check = title_elem.text
                elif desc_elem is not None and desc_elem.text:
                    text_to_check = desc_elem.text
                    
                if text_to_check:
                    feed_items.append(text_to_check)
        else:
            return [{"text": f"❌ Fehler beim Laden des RSS-Feeds (Status {res.status_code})", "class": "warning"}]
    except Exception as e:
        return [{"text": f"❌ Konnte RSS-Feed nicht verarbeiten: {e}", "class": "warning"}]

    missing_found = 0
    for raw_title in feed_items:
        feed_show_name, ep_codes = clean_feed_title(raw_title)
        
        if not ep_codes or not feed_show_name:
            continue
            
        norm_feed_name = normalize_name(feed_show_name)
        matched_show = shows_dict.get(norm_feed_name)
                
        if matched_show:
            show_name = matched_show["name"]
            series_id = matched_show["id"]
            
            existing_eps = get_existing_episodes(jellyfin_url, api_key, series_id)
            missing_in_release = [ep for ep in ep_codes if ep not in existing_eps]
            
            if missing_in_release:
                missing_found += 1
                ep_str = ", ".join(missing_in_release)
                results.append({
                    "text": f"'{show_name}' – Episoden <b>{ep_str}</b> fehlen noch in deiner Bibliothek!<br><small style='color: #8c9ba5;'>Release: {raw_title}</small>",
                    "class": "missing"
                })
                send_pushover("Fehlende Episoden entdeckt!", f"Für '{show_name}' fehlen noch Episoden ({ep_str}) aus dem Release.")

    if missing_found == 0:
        results.append({"text": "✅ Alle Feed-Einträge geprüft – Deine Serien sind aktuell, keine neuen Lücken gefunden.", "class": "success"})
        
    return results

def background_checker():
    while True:
        config = load_config()
        interval_hours = float(config.get("check_interval_hours", 6))
        perform_check()
        time.sleep(interval_hours * 3600)

threading.Thread(target=background_checker, daemon=True).start()

HTML_TEMPLATE = """
<!doctype html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Jellyfin Notifier – Dashboard</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --border-color: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #06b6d4;
            --accent-hover: #0891b2;
            --success-bg: rgba(22, 101, 52, 0.2);
            --success-border: #166534;
            --success-text: #4ade80;
            --missing-bg: rgba(194, 65, 12, 0.2);
            --missing-border: #c2410c;
            --missing-text: #fb923c;
            --warning-bg: rgba(153, 27, 27, 0.2);
            --warning-border: #991b1b;
            --warning-text: #f87171;
        }

        body {
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 2rem 1rem;
            display: flex;
            justify-content: center;
        }

        .container {
            width: 100%;
            max-width: 850px;
        }

        header {
            text-align: center;
            margin-bottom: 2rem;
        }

        header h1 {
            font-size: 1.8rem;
            margin: 0 0 0.5rem 0;
            color: #fff;
            letter-spacing: -0.025em;
        }

        header p {
            color: var(--text-muted);
            margin: 0;
            font-size: 0.95rem;
        }

        .card {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 2rem;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3), 0 4px 6px -4px rgba(0, 0, 0, 0.3);
            margin-bottom: 2rem;
        }

        .alert {
            padding: 1rem 1.25rem;
            border-radius: 8px;
            margin-bottom: 1.5rem;
            font-size: 0.95rem;
            font-weight: 500;
            border: 1px solid transparent;
        }

        .alert-success {
            background-color: var(--success-bg);
            border-color: var(--success-border);
            color: var(--success-text);
        }

        .form-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 1.25rem;
        }

        @media (min-width: 640px) {
            .form-grid-2 {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 1rem;
            }
        }

        .form-group {
            display: flex;
            flex-direction: column;
        }

        label {
            font-size: 0.875rem;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 0.4rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        input, select {
            width: 100%;
            padding: 0.75rem 1rem;
            background-color: #0f172a;
            border: 1px solid var(--border-color);
            color: #fff;
            border-radius: 8px;
            font-size: 0.95rem;
            box-sizing: border-box;
            transition: border-color 0.2s, box-shadow 0.2s;
        }

        input:focus, select:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(6, 182, 212, 0.15);
        }

        .btn-group {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            margin-top: 1.5rem;
        }

        button {
            padding: 0.75rem 1.25rem;
            font-size: 0.95rem;
            font-weight: 600;
            border-radius: 8px;
            border: none;
            cursor: pointer;
            transition: background-color 0.2s, transform 0.1s;
        }

        button:active {
            transform: translateY(1px);
        }

        .btn-primary {
            background-color: var(--accent);
            color: #0f172a;
            width: 100%;
            margin-top: 1rem;
        }
        .btn-primary:hover {
            background-color: var(--accent-hover);
        }

        .btn-test {
            background-color: #3b82f6;
            color: #fff;
        }
        .btn-test:hover { background-color: #2563eb; }

        .btn-check {
            background-color: #10b981;
            color: #fff;
        }
        .btn-check:hover { background-color: #059669; }

        .btn-clear {
            background-color: #ef4444;
            color: #fff;
            padding: 0.4rem 0.8rem;
            font-size: 0.85rem;
        }
        .btn-clear:hover { background-color: #dc2626; }

        .status-box {
            margin-top: 2rem;
            border-top: 1px solid var(--border-color);
            padding-top: 1.5rem;
        }

        .header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }

        .header-row h3 {
            margin: 0;
            font-size: 1.1rem;
            color: #fff;
        }

        .result-item {
            padding: 1rem 1.25rem;
            border-radius: 8px;
            margin-bottom: 0.75rem;
            font-size: 0.95rem;
            border: 1px solid transparent;
            line-height: 1.5;
        }

        .result-item.missing {
            background-color: var(--missing-bg);
            border-color: var(--missing-border);
            color: var(--missing-text);
        }

        .result-item.success {
            background-color: var(--success-bg);
            border-color: var(--success-border);
            color: var(--success-text);
        }

        .result-item.warning {
            background-color: var(--warning-bg);
            border-color: var(--warning-border);
            color: var(--warning-text);
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🎬 Jellyfin Notifier</h1>
            <p>Automatisierter Smart-Lücken-Detektor für deine Medienbibliothek</p>
        </header>

        <div class="card">
            {% if msg %}
                <div class="alert alert-success">{{ msg }}</div>
            {% endif %}
            
            <form method="POST" action="/save">
                <div class="form-grid">
                    <div class="form-group">
                        <label>Jellyfin URL</label>
                        <input type="text" name="jellyfin_url" value="{{ config.jellyfin_url }}" required>
                    </div>

                    <div class="form-group">
                        <label>Jellyfin API-Key</label>
                        <input type="text" name="jellyfin_api_key" value="{{ config.jellyfin_api_key }}">
                    </div>

                    <div class="form-grid-2">
                        <div class="form-group">
                            <label>Pushover App Token</label>
                            <input type="text" name="pushover_token" value="{{ config.pushover_token }}">
                        </div>
                        <div class="form-group">
                            <label>Pushover User Key</label>
                            <input type="text" name="pushover_user" value="{{ config.pushover_user }}">
                        </div>
                    </div>

                    <div class="form-group">
                        <label>RSS-Feed URL</label>
                        <input type="text" name="rss_url" value="{{ config.rss_url }}" required>
                    </div>

                    <div class="form-group">
                        <label>Zu überwachende Jellyfin-Bibliothek</label>
                        <select name="selected_library_id">
                            <option value="">-- Bitte wählen --</option>
                            {% for lib in libraries %}
                                <option value="{{ lib.ItemId }}" {% if config.selected_library_id == lib.ItemId %}selected{% endif %}>{{ lib.Name }}</option>
                            {% endfor %}
                        </select>
                    </div>

                    <div class="form-group">
                        <label>Prüfintervall (in Stunden)</label>
                        <input type="number" step="0.1" name="check_interval_hours" value="{{ config.check_interval_hours }}" required>
                    </div>
                </div>

                <div class="btn-group">
                    <button type="submit" name="action" value="test" class="btn-test">Test-Nachricht</button>
                    <button type="submit" name="action" value="run_check" class="btn-check">Jetzt prüfen</button>
                </div>

                <button type="submit" name="action" value="save" class="btn-primary">Einstellungen speichern</button>
            </form>

            {% if results %}
                <div class="status-box">
                    <div class="header-row">
                        <h3>Ergebnis des Abgleichs</h3>
                        <form method="POST" action="/clear" style="margin: 0;">
                            <button type="submit" class="btn-clear">Löschen</button>
                        </form>
                    </div>
                    {% for r in results %}
                        <div class="result-item {{ r.class }}">{{ r.text|safe }}</div>
                    {% endfor %}
                </div>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    config = load_config()
    libraries = get_jellyfin_libraries(config.get("jellyfin_url"), config.get("jellyfin_api_key"))
    status = request.args.get("status")
    msg = None
    if status == "saved":
        msg = "Einstellungen & Bibliotheken erfolgreich aktualisiert!"
    elif status == "test_success":
        msg = "Test-Nachricht wurde erfolgreich via Pushover gesendet!"
    elif status == "test_failed":
        msg = "Fehler beim Senden! Bitte Token und User Key überprüfen."
    
    return render_template_string(HTML_TEMPLATE, config=config, libraries=libraries, msg=msg, results=app.config.get("LAST_RESULTS"))

@app.route("/save", methods=["POST"])
def save():
    new_config = {
        "jellyfin_url": request.form.get("jellyfin_url"),
        "jellyfin_api_key": request.form.get("jellyfin_api_key"),
        "pushover_token": request.form.get("pushover_token"),
        "pushover_user": request.form.get("pushover_user"),
        "rss_url": request.form.get("rss_url"),
        "check_interval_hours": float(request.form.get("check_interval_hours", 6)),
        "selected_library_id": request.form.get("selected_library_id")
    }
    save_config(new_config)
    
    action_val = request.form.get("action")
    if action_val == "test":
        ok = send_pushover("Jellyfin Test", "Das ist eine Testnachricht von deinem Jellyfin Notifier!")
        status = "test_success" if ok else "test_failed"
        return redirect(url_for("index", status=status))
        
    elif action_val == "run_check":
        results = perform_check()
        app.config["LAST_RESULTS"] = results
        return redirect(url_for("index"))
        
    return redirect(url_for("index", status="saved"))

@app.route("/clear", methods=["POST"])
def clear_results():
    app.config["LAST_RESULTS"] = None
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
