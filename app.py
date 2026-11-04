import os
import json
import threading
import time
import requests
from flask import Flask, render_template_string, request, redirect, url_for

app = Flask(__name__)
CONFIG_FILE = "/app/data/config.json"

DEFAULT_CONFIG = {
    "jellyfin_url": "http://192.168.1.100:8096",
    "jellyfin_api_key": "",
    "pushover_token": "",
    "pushover_user": "",
    "check_interval_hours": 6,
    "shows": [
        {
            "name": "Breaking Bad", 
            "source_url": "https://api.tvmaze.com/search/shows?q=Breaking+Bad"
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
        return set()
    
    headers = {"X-Embi-Token": api_key}
    try:
        # 1. Nach der Serie in Jellyfin suchen
        search_url = f"{jellyfin_url.rstrip('/')}/Items?searchTerm={requests.utils.quote(show_name)}&includeItemTypes=Series&recursive=true"
        res = requests.get(search_url, headers=headers, timeout=5).json()
        items = res.get("Items", [])
        
        if not items:
            return set()
            
        series_id = items[0]["Id"]
        
        # 2. Alle Episoden dieser Serie aus Jellyfin abrufen
        ep_url = f"{jellyfin_url.rstrip('/')}/Shows/{series_id}/Episodes"
        ep_res = requests.get(ep_url, headers=headers, timeout=5).json()
        episodes = ep_res.get("Items", [])
        
        # 3. Ein Set von vorhandenen Staffeln/Folgen erstellen (z.B. "S01E05")
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

def background_checker():
    """Hintergrund-Dienst zum Abgleich zwischen TVMaze und Jellyfin"""
    while True:
        config = load_config()
        interval_hours = float(config.get("check_interval_hours", 6))
        jellyfin_url = config.get("jellyfin_url")
        api_key = config.get("jellyfin_api_key")
        
        print("Starte automatischen Serien-Abgleich über Jellyfin-API...")
        
        for show in config.get("shows", []):
            name = show.get("name")
            source_url = show.get("source_url")
            
            if not source_url:
                continue
                
            try:
                # 1. Neueste Online-Episode über TVMaze abrufen
                res = requests.get(source_url, timeout=5).json()
                if res:
                    show_id = res[0]["show"]["id"]
                    episodes = requests.get(f"https://api.tvmaze.com/shows/{show_id}/episodes", timeout=5).json()
                    latest_ep = episodes[-1] if episodes else None
                    
                    if latest_ep:
                        season = latest_ep['season']
                        number = latest_ep['number']
                        ep_code = f"S{season:02d}E{number:02d}"
                        
                        # 2. Vorhandene Episoden direkt aus Jellyfin auslesen
                        existing_eps = get_jellyfin_episodes(jellyfin_url, api_key, name)
                        found_in_jellyfin = ep_code in existing_eps
                        
                        print(f"Geprüft: {name} -> Neueste Online: {ep_code} | In Jellyfin vorhanden: {found_in_jellyfin}")
                        
                        # 3. Wenn online da, aber in Jellyfin noch fehlt -> Benachrichtigung senden
                        if not found_in_jellyfin and existing_eps:
                            send_pushover("Neue Serienfolge!", f"{name} – {ep_code} ist erschienen und fehlt noch in Jellyfin!")
                            
            except Exception as e:
                print(f"Fehler bei Prüfung von {name}: {e}")
                
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
        input, textarea { width: 100%; padding: 0.5rem; margin-top: 0.3rem; background: #2c2c2c; border: 1px solid #444; color: #fff; border-radius: 4px; box-sizing: border-box; }
        button { background: #00bcd4; color: #000; border: none; padding: 0.75rem 1.5rem; margin-top: 1.5rem; font-weight: bold; border-radius: 4px; cursor: pointer; }
        button:hover { background: #26c6da; }
        .btn-test { background: #ff9800; color: #000; margin-left: 1rem; }
        .btn-test:hover { background: #fb8c00; }
        .btn-danger { background: #e53935; color: #fff; padding: 0.4rem 0.8rem; margin-top: 0.5rem; }
        .show-box { background: #252525; padding: 1rem; border-radius: 6px; margin-top: 1rem; border-left: 4px solid #00bcd4; }
        .success { background: #1b5e20; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; }
        .error { background: #b71c1c; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Jellyfin Notifier – Weboberfläche</h2>
        {% if msg %}
            <div class="{{ 'success' if success else 'error' }}">{{ msg }}</div>
        {% endif %}
        
        <form method="POST" action="/save">
            <h3>Allgemeine Einstellungen</h3>
            <label>Jellyfin URL (z.B. http://192.168.1.50:8096)</label>
            <input type="text" name="jellyfin_url" value="{{ config.jellyfin_url }}" required>

            <label>Jellyfin API-Key</label>
            <input type="text" name="jellyfin_api_key" value="{{ config.jellyfin_api_key }}">

            <label>Pushover App Token</label>
            <input type="text" name="pushover_token" value="{{ config.pushover_token }}">

            <label>Pushover User Key</label>
            <input type="text" name="pushover_user" value="{{ config.pushover_user }}">

            <button type="submit" name="action" value="test" class="btn-test">Pushover Test-Nachricht senden</button>

            <label style="margin-top: 1.5rem;">Prüfintervall (in Stunden)</label>
            <input type="number" step="0.1" name="check_interval_hours" value="{{ config.check_interval_hours }}" required>

            <h3 style="margin-top: 2rem;">Zu überwachende Serien</h3>
            <div id="shows-container">
                {% for show in config.shows %}
                <div class="show-box">
                    <label>Serienname (wie in Jellyfin & TVMaze):</label>
                    <input type="text" name="show_name" value="{{ show.name }}" required>
                    <label>Webseite / API URL (Abgleich, z.B. TVMaze Suche):</label>
                    <input type="text" name="show_source" value="{{ show.source_url }}">
                    <button type="button" class="btn-danger" onclick="this.parentElement.remove()">Serie entfernen</button>
                </div>
                {% endfor %}
            </div>

            <button type="button" onclick="addShow()" style="background: #4caf50; color: #fff; margin-top: 1rem;">+ Weitere Serie hinzufügen</button>
            <br>
            <button type="submit" name="action" value="save" style="width: 100%; font-size: 1.1rem; margin-top: 2rem;">Einstellungen speichern</button>
        </form>
    </div>
    <script>
    function addShow() {
        const container = document.getElementById('shows-container');
        const div = document.createElement('div');
        div.className = 'show-box';
        div.innerHTML = `
            <label>Serienname (wie in Jellyfin & TVMaze):</label>
            <input type="text" name="show_name" required>
            <label>Webseite / API URL (Abgleich, z.B. TVMaze Suche):</label>
            <input type="text" name="show_source">
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
    success = True
    if status == "saved":
        msg = "Einstellungen erfolgreich gespeichert!"
    elif status == "test_success":
        msg = "Test-Nachricht wurde erfolgreich via Pushover gesendet!"
    elif status == "test_failed":
        msg = "Fehler beim Senden! Bitte Token und User Key überprüfen."
        success = False
    return render_template_string(HTML_TEMPLATE, config=config, msg=msg, success=success)

@app.route("/save", methods=["POST"])
def save():
    action = request.form.get("action")
    
    names = request.form.getlist("show_name")
    sources = request.form.getlist("show_source")
    
    shows = []
    for i in range(len(names)):
        shows.append({
            "name": names[i],
            "source_url": sources[i]
        })
        
    new_config = {
        "jellyfin_url": request.form.get("jellyfin_url"),
        "jellyfin_api_key": request.form.get("jellyfin_api_key"),
        "pushover_token": request.form.get("pushover_token"),
        "pushover_user": request.form.get("pushover_user"),
        "check_interval_hours": float(request.form.get("check_interval_hours", 6)),
        "shows": shows
    }
    save_config(new_config)
    
    if action == "test":
        ok = send_pushover("Jellyfin Test", "Das ist eine Testnachricht von deinem Jellyfin Notifier!")
        status = "test_success" if ok else "test_failed"
        return redirect(url_for("index", status=status))
        
    return redirect(url_for("index", status="saved"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
