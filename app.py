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
    "shows": [
        {
            "name": "4 Blocks"
        }
    ]
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

def get_jellyfin_episodes(jellyfin_url, api_key, show_name):
    """Durchsucht die Jellyfin-Bibliothek über die API nach vorhandenen Episoden einer Serie"""
    if not jellyfin_url or not api_key:
        print("Jellyfin URL oder API-Key fehlt in der Konfiguration.")
        return set()
    
    # Beide Authentifizierungs-Header für maximale Kompatibilität übergeben
    headers = {
        "X-Emby-Token": api_key,
        "Authorization": f'MediaBrowser Token="{api_key}"'
    }
    
    try:
        search_url = f"{jellyfin_url.rstrip('/')}/Items?searchTerm={requests.utils.quote(show_name)}&includeItemTypes=Series&recursive=true"
        res = requests.get(search_url, headers=headers, timeout=5)
        
        if res.status_code != 200:
            print(f"Jellyfin HTTP-Fehler {res.status_code} für {show_name}: {res.text[:150]}")
            return set()
            
        try:
            data = res.json()
        except Exception:
            print(f"Jellyfin lieferte kein JSON: {res.text[:150]}")
            return set()
            
        items = data.get("Items", [])
        if not items:
            print(f"Serie '{show_name}' wurde in Jellyfin nicht gefunden.")
            return set()
            
        series_id = items[0]["Id"]
        ep_url = f"{jellyfin_url.rstrip('/')}/Shows/{series_id}/Episodes"
        ep_res = requests.get(ep_url, headers=headers, timeout=5)
        
        if ep_res.status_code != 200:
            return set()
            
        ep_data = ep_res.json()
        episodes = ep_data.get("Items", [])
        
        existing_eps = set()
        for ep in episodes:
            season = ep.get("ParentIndexNumber", 0)
            number = ep.get("IndexNumber", 0)
            if season is not None and number is not None:
                existing_eps.add(f"S{season:02d}E{number:02d}")
                
        return existing_eps
    except Exception as e:
        print(f"Fehler bei Jellyfin-Abfrage für {show_name}: {e}")
        return set()

def perform_check():
    """Liest den RSS-Feed aus und gleicht gefundene Releases mit Jellyfin ab"""
    config = load_config()
    jellyfin_url = config.get("jellyfin_url")
    api_key = config.get("jellyfin_api_key")
    rss_url = config.get("rss_url")
    
    print("Starte RSS-Feed Abgleich...")
    results = []
    
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
            results.append({"text": f"❌ Fehler beim Laden des RSS-Feeds (Status {res.status_code})", "class": "warning"})
            return results
    except Exception as e:
        results.append({"text": f"❌ Konnte RSS-Feed nicht verarbeiten: {e}", "class": "warning"})
        return results

    for show in config.get("shows", []):
        name = show.get("name")
        if not name:
            continue
            
        try:
            existing_eps = get_jellyfin_episodes(jellyfin_url, api_key, name)
            
            found_releases = []
            for title in feed_items:
                if name.lower() in title.lower():
                    found_releases.append(title)
            
            if not existing_eps:
                results.append({"text": f"⚠️ Serie '{name}' wurde nicht in Jellyfin gefunden oder API-Key ungültig.", "class": "warning"})
            elif found_releases:
                results.append({"text": f"🔔 '{name}': Neue Einträge im Feed gefunden: {found_releases[0]}", "class": "missing"})
                send_pushover("Neuer Release im Feed!", f"Für '{name}' wurde ein neuer Eintrag im Feed entdeckt.")
            else:
                results.append({"text": f"✅ '{name}': Keine neuen Releases im Feed gefunden. Jellyfin ist aktuell.", "class": "success"})
                
        except Exception as e:
            err_msg = f"❌ Fehler bei Prüfung von {name}: {e}"
            print(err_msg)
            results.append({"text": err_msg, "class": "warning"})
            
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
    <title>Jellyfin Notifier</title>
    <style>
        body { font-family: sans-serif; background: #121212; color: #e0e0e0; padding: 2rem; max-width: 800px; margin: auto; }
        .card { background: #1e1e1e; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 1.5rem; }
        label { display: block; margin-top: 1rem; font-weight: bold; color: #b0bec5; }
        input { width: 100%; padding: 0.5rem; margin-top: 0.3rem; background: #2c2c2c; border: 1px solid #444; color: #fff; border-radius: 4px; box-sizing: border-box; }
        button { background: #00bcd4; color: #000; border: none; padding: 0.75rem 1.5rem; margin-top: 1rem; font-weight: bold; border-radius: 4px; cursor: pointer; }
        button:hover { background: #26c6da; }
        .btn-test { background: #ff9800; color: #000; margin-right: 0.5rem; }
        .btn-check { background: #4caf50; color: #fff; margin-right: 0.5rem; }
        .btn-danger { background: #e53935; color: #fff; padding: 0.4rem 0.8rem; margin-top: 0.5rem; }
        .show-box { background: #252525; padding: 1rem; border-radius: 6px; margin-top: 1rem; border-left: 4px solid #00bcd4; }
        .success { background: #1b5e20; color: #a5d6a7; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; }
        .missing { background: #e65100; color: #ffe0b2; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; font-weight: bold; }
        .warning { background: #b71c1c; color: #ffcdd2; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; }
        .status-box { margin-top: 1.5rem; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Jellyfin Notifier – RSS Edition</h2>
        {% if msg %}
            <div class="success">{{ msg }}</div>
        {% endif %}
        
        <form method="POST" action="/save">
            <h3>Allgemeine Einstellungen</h3>
            <label>Jellyfin URL (z.B. http://192.168.0.73:8096)</label>
            <input type="text" name="jellyfin_url" value="{{ config.jellyfin_url }}" required>

            <label>Jellyfin API-Key</label>
            <input type="text" name="jellyfin_api_key" value="{{ config.jellyfin_api_key }}">

            <label>Pushover App Token</label>
            <input type="text" name="pushover_token" value="{{ config.pushover_token }}">

            <label>Pushover User Key</label>
            <input type="text" name="pushover_user" value="{{ config.pushover_user }}">

            <label>RSS-Feed URL</label>
            <input type="text" name="rss_url" value="{{ config.rss_url }}" required>

            <div style="margin-top: 1.5rem;">
                <button type="submit" name="action" value="test" class="btn-test">Test-Nachricht senden</button>
                <button type="submit" name="action" value="run_check" class="btn-check">Jetzt prüfen (Manuell)</button>
            </div>

            <label style="margin-top: 1.5rem;">Prüfintervall (in Stunden)</label>
            <input type="number" step="0.1" name="check_interval_hours" value="{{ config.check_interval_hours }}" required>

            <h3 style="margin-top: 2rem;">Zu überwachende Serien</h3>
            <div id="shows-container">
                {% for show in config.shows %}
                <div class="show-box">
                    <label>Serienname (wie in Jellyfin):</label>
                    <input type="text" name="show_name" value="{{ show.name }}" required>
                    <button type="button" class="btn-danger" onclick="this.parentElement.remove()">Serie entfernen</button>
                </div>
                {% endfor %}
            </div>

            <button type="button" onclick="addShow()" style="background: #2196f3; color: #fff; margin-top: 1rem;">+ Weitere Serie hinzufügen</button>
            <br>
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
    <script>
    function addShop() {
        // Dummy
    }
    function addShow() {
        const container = document.getElementById('shows-container');
        const div = document.createElement('div');
        div.className = 'show-box';
        div.innerHTML = `
            <label>Serienname (wie in Jellyfin):</label>
            <input type="text" name="show_name" required>
            <button type="button" class="btn-danger" onclick="this.parentElement.remove()">Serie entfernen</button>
        `;
        container.appendChild(div);
    }
    </script>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    config = load_config()
    status = request.args.get("status")
    msg = None
    if status == "saved":
        msg = "Einstellungen erfolgreich gespeichert!"
    elif status == "test_success":
        msg = "Test-Nachricht wurde erfolgreich via Pushover gesendet!"
    elif status == "test_failed":
        msg = "Fehler beim Senden! Bitte Token und User Key überprüfen."
    
    return render_template_string(HTML_TEMPLATE, config=config, msg=msg, results=app.config.get("LAST_RESULTS"))

@app.route("/save", methods=["POST"])
def save():
    action = request.form.get("action")
    
    names = request.form.getlist("show_name")
    shows = [{"name": name} for name in names]
        
    new_config = {
        "jellyfin_url": request.form.get("jellyfin_url"),
        "jellyfin_api_key": request.form.get("jellyfin_api_key"),
        "pushover_token": request.form.get("pushover_token"),
        "pushover_user": request.form.get("pushover_user"),
        "rss_url": request.form.get("rss_url"),
        "check_interval_hours": float(request.form.get("check_interval_hours", 6)),
        "shows": shows
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
