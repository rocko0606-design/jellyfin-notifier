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
        search_url = f"{jellyfin_url.rstrip('/')}/Items?searchTerm={requests.utils.quote(show_name)}&includeItemTypes=Series&recursive=true"
        res = requests.get(search_url, headers=headers, timeout=5).json()
        items = res.get("Items", [])
        
        if not items:
            return set()
            
        series_id = items[0]["Id"]
        ep_url = f"{jellyfin_url.rstrip('/')}/Shows/{series_id}/Episodes"
        ep_res = requests.get(ep_url, headers=headers, timeout=5).json()
        episodes = ep_res.get("Items", [])
        
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
    """Führt den Abgleich für alle hinterlegten Serien durch und liefert strukturierte Ergebnisse"""
    config = load_config()
    jellyfin_url = config.get("jellyfin_url")
    api_key = config.get("jellyfin_api_key")
    
    print("Starte Serien-Abgleich...")
    results = []
    
    for show in config.get("shows", []):
        name = show.get("name")
        source_url = show.get("source_url")
        
        if not source_url:
            continue
            
        try:
            res = requests.get(source_url, timeout=5).json()
            if res:
                show_id = res[0]["show"]["id"]
                episodes = requests.get(f"https://api.tvmaze.com/shows/{show_id}/episodes", timeout=5).json()
                latest_ep = episodes[-1] if episodes else None
                
                if latest_ep:
                    season = latest_ep['season']
                    number = latest_ep['number']
                    ep_code = f"S{season:02d}E{number:02d}"
                    
                    existing_eps = get_jellyfin_episodes(jellyfin_url, api_key, name)
                    found_in_jellyfin = ep_code in existing_eps
                    
                    if not existing_eps:
                        status_text = f"⚠️ Serie '{name}' wurde nicht in deiner Jellyfin-Bibliothek gefunden!"
                        type_class = "warning"
                    elif found_in_jellyfin:
                        status_text = f"✅ '{name}': Neueste Folge ({ep_code}) ist bereits in Jellyfin vorhanden. Alles aktuell!"
                        type_class = "success"
                    else:
                        status_text = f"🔔 '{name}': Neueste Folge ({ep_code}) ist online erschienen, fehlt aber noch in Jellyfin!"
                        type_class = "missing"
                        send_pushover("Neue Serienfolge!", f"{name} – {ep_code} ist erschienen und fehlt noch
