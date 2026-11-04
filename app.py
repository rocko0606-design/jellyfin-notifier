import os
import json
import threading
import time
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
    """Ruft alle Medien-Bibliotheken von Jellyfin ab (für das Auswahlmenü)"""
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

def get_library_series(jellyfin_url, api_key, library_id):
    """Holt alle Serien, die sich in der ausgewählten Bibliothek befinden"""
    if not jellyfin_url or not api_key or not library_id:
        return []
    
    headers = {
        "X-Emby-Token": api_key,
        "Authorization": f'MediaBrowser Token="{api_key}"'
    }
    try:
        # Fragt alle Serien direkt unter der jeweiligen ParentId (Bibliothek) ab
        url = f"{jellyfin_url.rstrip('/')}/Items?ParentId={library_id}&includeItemTypes=Series&recursive=true"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return [item.get("Name") for item in data.get("Items", []) if item.get("Name")]
    except Exception as e:
        print(f"Fehler beim Abrufen der Serien aus der Bibliothek: {e}")
    return []

def perform_check():
    """Liest den RSS-Feed aus und gleicht alle Serien der gewählten Bibliothek ab"""
    config = load_config()
    jellyfin_url = config.get("jellyfin_url")
    api_key = config.get("jellyfin_api_key")
    rss_url = config.get("rss_url")
    library_id = config.get("selected_library_id")
    
    print("Starte RSS-Feed Abgleich mit Jellyfin-Bibliothek...")
    results = []
    
    if not library_id:
        return [{"text": "⚠️ Keine Jellyfin-Bibliothek ausgewählt. Bitte in den Einstellungen festlegen.", "class": "warning"}]

    # 1. Serien aus der gewählten Jellyfin-Bibliothek holen
    local_shows = get_library_series(jellyfin_url, api_key, library_id)
    if not local_shows:
        return [{"text": "⚠️ Keine Serien in der ausgewählten Bibliothek gefunden oder Verbindung fehlgeschlagen.", "class": "warning"}]

    # 2. RSS-Feed auslesen
    feed_items = []
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(rss_url, headers=headers, timeout=10)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = item.find("text") or item.find("title")
                if title is not None and title.text:
                    feed_items.append(title.text)
        else:
            return [{"text": f"❌ Fehler beim Laden des RSS-Feeds (Status {res.status_code})", "class": "warning"}]
    except Exception as e:
        return [{"text": f"❌ Konnte RSS-Feed nicht verarbeiten: {e}", "class": "warning"}]

    # 3. Abgleich: Taucht eine Serie aus der Bibliothek im RSS-Feed auf?
    matched_count = 0
    for show_name in local_shows:
        found_releases = []
        for title in feed_items:
            # Prüft, ob der Serienname im Feed vorkommt (Groß-/Kleinschreibung egal)
            if show_name.lower() in title.lower():
                found_releases.append(title)
        
        if found_releases:
            matched_count += 1
            results.append({"text": f"🔔 '{show_name}': Im Feed entdeckt -> {found_releases[0]}", "class": "missing"})
            send_pushover("Neuer Release im Feed!", f"Für '{show_name}' wurde ein neuer Eintrag entdeckt: {found_releases[0]}")

    if matched_count == 0:
        results.append({"text": f"✅ Alle {len(local_shows)} Serien geprüft – Keine neuen Releases im Feed gefunden.", "class": "success"})
        
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
    <title>Jellyfin Notifier – Bibliotheks-Modus</title>
    <style>
        body { font-family: sans-serif; background: #121212; color: #e0e0e0; padding: 2rem; max-width: 800px; margin: auto; }
        .card { background: #1e1e1e; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 1.5rem; }
        label { display: block; margin-top: 1rem; font-weight: bold; color: #b0bec5; }
        input, select { width: 100%; padding: 0.5rem; margin-top: 0.3rem; background: #2c2c2c; border: 1px solid #444; color: #fff; border-radius: 4px; box-sizing: border-box; }
        button { background: #00bcd4; color: #000; border: none; padding: 0.75rem 1.5rem; margin-top: 1rem; font-weight: bold; border-radius: 4px; cursor: pointer; }
        button:hover { background: #26c6da; }
        .btn-test { background: #ff9800; color: #000; margin-right: 0.5rem; }
        .btn-check { background: #4caf50; color: #fff; margin-right: 0.5rem; }
        .success { background: #1b5e20; color: #a5d6a7; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; }
        .missing { background: #e65100; color: #ffe0b2; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; font-weight: bold; }
        .warning { background: #b71c1c; color: #ffcdd2; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; }
        .status-box { margin-top: 1.5rem; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Jellyfin Notifier – Bibliotheks-Modus</h2>
        {% if msg %}
            <div class="success">{{ msg }}</div>
        {% endif %}
        
        <form method="POST" action="/save">
            <h3>Allgemeine Einstellungen</h3>
            <label>Jellyfin URL</label>
            <input type="text" name="jellyfin_url" value="{{ config.jellyfin_url }}" required>

            <label>Jellyfin API-Key</label>
            <input type="text" name="jellyfin_api_key" value="{{ config.jellyfin_api_key }}">

            <label>Pushover App Token</label>
            <input type="text" name="pushover_token" value="{{ config.pushover_token }}">

            <label>Pushover User Key</label>
            <input type="text" name="pushover_user" value="{{ config.pushover_user }}">

            <label>RSS-Feed URL</label>
            <input type="text" name="rss_url" value="{{ config.rss_url }}" required>

            <label>Zu überwachende Jellyfin-Bibliothek</label>
            <select name="selected_library_id">
                <option value="">-- Bitte wählen --</option>
                {% for lib in libraries %}
                    <option value="{{ lib.ItemId }}" {% if config.selected_library_id == lib.ItemId %}selected{% endif %}>{{ lib.Name }}</option>
                {% endfor %}
            </select>

            <div style="margin-top: 1.5rem;">
                <button type="submit" name="action" value="test" class="btn-test">Test-Nachricht senden</button>
                <button type="submit" name="action" value="run_check" class="btn-check">Jetzt prüfen (Manuell)</button>
            </div>

            <label style="margin-top: 1.5rem;">Prüfintervall (in Stunden)</label>
            <input type="number" step="0.1" name="check_interval_hours" value="{{ config.check_interval_hours }}" required>

            <button type="submit" name="action" value="save" style="width: 100%; font-size: 1.1rem; margin-top: 2rem; background: #00bcd4;">Einstellungen speichern</button>
        </form>

        {% if results %}
            <div class="status-box">
                <h3>Ergebnis des Abgleichs:</h3>
                {% for r in results %}
                    <div class="{{ r.class }}">{{ r.text }}</div>
                {% endfor %}
            </div>
        {% endif %}
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
    action = request.form.get("action")
    
    config = load_config()
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
    
    if action == "test":
        ok = send_pushover("Jellyfin Test", "Das ist eine Testnachricht von deinem Jellyfin Notifier!")
        status = "test_success" if ok else "test_failed"
        return redirect(url_for("index", status=status))
        
    elif action == "run_check":
        results = perform_check()
        app.config["LAST_RESULTS"] = results
        return redirect(url_for("index"))
        
    return redirect(url_for("index", status="saved"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
