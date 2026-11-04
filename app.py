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
            "path": "D:/Serien/Breaking Bad", 
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

def background_checker():
    while True:
        config = load_config()
        interval_hours = float(config.get("check_interval_hours", 6))
        print("Starte automatischen Serien-Abgleich im Hintergrund...")
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
        .btn-danger { background: #e53935; color: #fff; padding: 0.4rem 0.8rem; margin-top: 0.5rem; }
        .show-box { background: #252525; padding: 1rem; border-radius: 6px; margin-top: 1rem; border-left: 4px solid #00bcd4; }
        .success { background: #1b5e20; padding: 1rem; border-radius: 4px; margin-bottom: 1rem; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Jellyfin Notifier – Weboberfläche</h2>
        {% if saved %}<div class="success">Einstellungen erfolgreich gespeichert!</div>{% endif %}
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

            <label>Prüfintervall (in Stunden)</label>
            <input type="number" step="0.1" name="check_interval_hours" value="{{ config.check_interval_hours }}" required>

            <h3 style="margin-top: 2rem;">Zu überwachende Serien & Pfade</h3>
            <div id="shows-container">
                {% for show in config.shows %}
                <div class="show-box">
                    <label>Serienname:</label>
                    <input type="text" name="show_name" value="{{ show.name }}" required>
                    <label>Lokaler Pfad (Verzeichnis):</label>
                    <input type="text" name="show_path" value="{{ show.path }}">
                    <label>Webseite / API URL (Abgleich):</label>
                    <input type="text" name="show_source" value="{{ show.source_url }}">
                    <button type="button" class="btn-danger" onclick="this.parentElement.remove()">Serie entfernen</button>
                </div>
                {% endfor %}
            </div>

            <button type="button" onclick="addShow()" style="background: #4caf50; color: #fff; margin-top: 1rem;">+ Weitere Serie hinzufügen</button>
            <br>
            <button type="submit" style="width: 100%; font-size: 1.1rem; margin-top: 2rem;">Einstellungen speichern</button>
        </form>
    </div>
    <script>
    function addShow() {
        const container = document.getElementById('shows-container');
        const div = document.createElement('div');
        div.className = 'show-box';
        div.innerHTML = `
            <label>Serienname:</label>
            <input type="text" name="show_name" required>
            <label>Lokaler Pfad (Verzeichnis):</label>
            <input type="text" name="show_path">
            <label>Webseite / API URL (Abgleich):</label>
            <input type="text" name="show_source">
            <button type="button" class="btn-danger" onclick="this.parentElement.remove()">Serie entfernen</button>
        `;
        container.appendChild(div);
    }
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    config = load_config()
    saved = request.args.get("saved", "false") == "true"
    return render_template_string(HTML_TEMPLATE, config=config, saved=saved)

@app.route("/save", methods=["POST"])
def save():
    names = request.form.getlist("show_name")
    paths = request.form.getlist("show_path")
    sources = request.form.getlist("show_source")
    
    shows = []
    for i in range(len(names)):
        shows.append({
            "name": names[i],
            "path": paths[i],
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
    return redirect(url_for("index", saved="true"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
