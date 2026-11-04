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
    """Holt alle Serien und deren IDs aus der ausgewählten Bibliothek"""
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
            return [{"id": item.get("Id"), "name": item.get("Name")} for item in data.get("Items", []) if item.get("Name")]
    except Exception as e:
        print(f"Fehler beim Abrufen der Serien: {e}")
    return []

def get_existing_episodes(jellyfin_url, api_key, series_id):
    """Holt alle bereits in Jellyfin vorhandenen Episoden einer spezifischen Serie"""
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
                if season is not None and number is not None:
                    existing.add(f"S{season:02d}E{number:02d}")
            return existing
    except Exception as e:
        print(f"Fehler beim Abrufen der Episoden für ID {series_id}: {e}")
    return set()

def extract_season_episode(title):
    """Versucht aus einem Release-Titel die Staffel und Episode zu extrahieren (z.B. S01E05 oder 1x05)"""
    # Sucht nach Muster wie S01E05 oder s1e5
    match = re.search(r's(\d+)\s*e(\d+)', title, re.IGNORECASE)
    if match:
        return f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"
    
    # Sucht nach Muster wie 1x05
    match_alt = re.search(r'(\d+)x(\d+)', title)
    if match_alt:
        return f"S{int(match_alt.group(1)):02d}E{int(match_alt.group(2)):02d}"
        
    return None

def perform_check():
    config = load_config()
    jellyfin_url = config.get("jellyfin_url")
    api_key = config.get("jellyfin_api_key")
    rss_url = config.get("rss_url")
    library_id = config.get("selected_library_id")
    
    print("Starte präzisen RSS-Feed Abgleich mit Jellyfin...")
    results = []
    
    if not library_id:
        return [{"text": "⚠️ Keine Jellyfin-Bibliothek ausgewählt. Bitte in den Einstellungen festlegen.", "class": "warning"}]

    local_shows = get_library_series_with_ids(jellyfin_url, api_key, library_id)
    if not local_shows:
        return [{"text": "⚠️ Keine Serien in der ausgewählten Bibliothek gefunden oder Verbindung fehlgeschlagen.", "class": "warning"}]

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

    missing_found = 0
    for show in local_shows:
        show_name = show["name"]
        series_id = show["id"]
        
        # Durchsuche den Feed nach passenden Einträgen für diese Serie
        for title in feed_items:
            if show_name.lower() in title.lower():
                ep_code = extract_season_episode(title)
                if ep_code:
                    # Prüfe, welche Episoden bereits in Jellyfin existieren
                    existing_eps = get_existing_episodes(jellyfin_url, api_key, series_id)
                    
                    # Wenn die spezifische Episode noch NICHT in Jellyfin ist -> Alarm!
                    if ep_code not in existing_eps:
                        missing_found += 1
                        results.append({
                            "text": f"🔔 '{show_name}' – {ep_code} ist im Feed erschienen und <b>fehlt noch</b> in Jellyfin! (Release: {title})",
                            "class": "missing"
                        })
                        send_pushover("Neue Episode fehlt in Jellyfin!", f"'{show_name}' ({ep_code}) ist online und fehlt noch in der Bibliothek.")

    if missing_found == 0:
        results.append({"text": f"✅ Alle Serien geprüft – Keine fehlenden Episoden im Feed entdeckt. Alles aktuell!", "class": "success"})
        
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
    <title>Jellyfin Notifier – Präziser Abgleich</title>
    <style>
        body { font-family: sans-serif; background: #121212; color: #e0e0e0; padding: 2rem; max-width: 800px; margin: auto; }
        .card { background: #1e1e1e; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 1.5rem; }
        label { display: block; margin-top: 1rem; font-weight: bold; color: #b0bec5; }
        input, select { width: 100%; padding: 0.5rem; margin-top: 0.3rem; background: #2c2c2c; border: 1px solid #444; color: #fff; border-radius: 4px; box-sizing: border-box; }
        button { background: #00bcd4; color: #000; border: none; padding: 0.75rem 1.5rem; margin-top: 1rem; font-weight: bold; border-radius: 4px; cursor: pointer; }
        button:hover { background: #26c6da; }
        .btn-test { background: #ff9800; color: #000; margin-right: 0.5rem; }
        .btn-check { background: #4caf50; color: #fff; margin-
