import os
import sys
import subprocess

# Automatischer Selbst-Test & Installation für MyJDownloader
try:
    import myjdapi
    import Crypto
except ImportError:
    print("📦 Installiere benötigte JDownloader-Bibliotheken automatisch...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "myjdapi", "pycryptodome"])
    import myjdapi

import json
import threading
import time
import re
import requests
import xml.etree.ElementTree as ET
from flask import Flask, render_template_string, request, redirect, url_for, Response

app = Flask(__name__)
CONFIG_FILE = "/app/data/config.json"

DEFAULT_CONFIG = {
    "jellyfin_url": "http://192.168.0.73:8096",
    "jellyfin_api_key": "",
    "pushover_token": "",
    "pushover_user": "",
    "myjd_email": "",
    "myjd_password": "",
    "myjd_device_name": "",
    "website_cookie": "",
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

def send_to_jdownloader(url, package_name):
    """Übermittelt den Hoster-Link via offizieller myjdapi-Bibliothek an JDownloader"""
    config = load_config()
    email = config.get("myjd_email")
    password = config.get("myjd_password")
    device_name = config.get("myjd_device_name")
    
    if not email or not password or not url:
        return False
        
    try:
        jd = myjdapi.Myjdapi()
        if not jd.connect(email, password):
            print("MyJDownloader: Verbindung via myjdapi fehlgeschlagen.")
            return False
            
        devices = jd.list_devices()
        if not devices:
            print("MyJDownloader: Keine Geräte gefunden.")
            return False
            
        device = None
        if device_name:
            for d in devices:
                if d.get("name") == device_name:
                    device = jd.get_device(d["name"])
                    break
        if not device:
            device = jd.get_device(devices[0]["name"])
            
        device.linkgrabber.add_links([{
            "link": url,
            "packageName": package_name,
            "autostart": False
        }])
        print(f"Link erfolgreich an JDownloader übergeben: {url}")
        return True
        
    except Exception as e:
        print(f"MyJDownloader Fehler: {e}")
        return False

def extract_hoster_link(release_page_url):
    """Ermittelt den Filer-Link mit erweiterten Fallbacks und Diagnose-Ausgaben"""
    if not release_page_url or "http" not in release_page_url:
        return None
    try:
        config = load_config()
        cookie_val = config.get("website_cookie", "")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        if cookie_val:
            headers["Cookie"] = cookie_val

        print(f"--- Prüfe Release-URL: {release_page_url} ---")
        
        # 1. Direkter Aufruf der Release-Seite
        res = requests.get(release_page_url, headers=headers, timeout=10)
        print(f"Status Code Release-Seite: {res.status_code}")
        
        if res.status_code == 200:
            html = res.text
            
            # Suche nach jeglichem Link, der filer.net enthält
            filer_match = re.search(r'https?://[^\s<>"]+filer\.net[^\s<>"]*', html, re.IGNORECASE)
            if filer_match:
                found_url = filer_match.group(0).rstrip('"\'')
                print(filer_match.group(0))
                return found_url

            # 2. Suche nach Outbound- oder Weiterleitungs-Links (z.B. /out/)
            urls = re.findall(r'href=[\'"]?(https?://[^\'">\s]+)', html)
            for u in urls:
                if any(skip in u.lower() for skip in ["google", "cloudflare", "facebook", "twitter", "css", "js", "img", "anonym.to"]):
                    continue
                
                try:
                    sub_res = requests.get(u, headers=headers, timeout=5)
                    if sub_res.status_code == 200:
                        sub_filer = re.search(r'https?://[^\s<>"]+filer\.net[^\s<>"]*', sub_res.text, re.IGNORECASE)
                        if sub_filer:
                            found_url = sub_filer.group(0).rstrip('"\'')
                            print(sub_filer.group(0))
                            return found_url
                except:
                    continue

        # 3. Fallback über die Release-ID direkt auf /out/ versuchen
        match_id = re.search(r'/releases/([a-zA-Z0-9_-]+)', release_page_url)
        if match_id:
            release_id = match_id.group(1)
            out_url = f"https://nox.to/out/{release_id}"
            print(f"Versuche direkten Outbound-Pfad: {out_url}")
            out_res = requests.get(out_url, headers=headers, allow_redirects=True, timeout=10)
            print(f"Status Code Outbound: {out_res.status_code}")
            
            if out_res.status_code == 200:
                out_filer = re.search(r'https?://[^\s<>"]+filer\.net[^\s<>"]*', out_res.text, re.IGNORECASE)
                if out_filer:
                    found_url = out_filer.group(0).rstrip('"\'')
                    print(out_filer.group(0))
                    return found_url

    except Exception as e:
        print(f"Fehler beim Extrahieren des Hoster-Links: {e}")
        
    print("⚠️ Kein Filer-Link gefunden für diese URL.")
    return None

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
    
    print("Starte präzisen Smart-Abgleich mit JDownloader-Übergabe...")
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
                link_elem = item.find("link")
                
                text_to_check = ""
                if title_elem is not None and title_elem.text:
                    text_to_check = title_elem.text
                elif desc_elem is not None and desc_elem.text:
                    text_to_check = desc_elem.text
                    
                item_link = link_elem.text if link_elem is not None else ""
                
                if text_to_check:
                    feed_items.append({"title": text_to_check, "link": item_link})
        else:
            return [{"text": f"❌ Fehler beim Laden des RSS-Feeds (Status {res.status_code})", "class": "warning"}]
    except Exception as e:
        return [{"text": f"❌ Konnte RSS-Feed nicht verarbeiten: {e}", "class": "warning"}]

    missing_found = 0
    for entry in feed_items:
        raw_title = entry["title"]
        item_link = entry["link"]
        
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
                
                jd_status_text = ""
                hoster_url = extract_hoster_link(item_link)
                if hoster_url:
                    success = send_to_jdownloader(hoster_url, f"{show_name} - {ep_str}")
                    if success:
                        jd_status_text = " | 📥 <b>An JDownloader übergeben!</b>"
                    else:
                        jd_status_text = " | ⚠️ <i>Fehler bei JDownloader-Übergabe</i>"
                else:
                    jd_status_text = " | ⚠️ <i>Kein Hoster-Link gefunden</i>"

                results.append({
                    "text": f"'{show_name}' – Episoden <b>{ep_str}</b> fehlen noch!{jd_status_text}<br><small style='color: #8c9ba5;'>Release: {raw_title}</small>",
                    "class": "missing"
                })
                send_pushover("Fehlende Episode & Download gestartet!", f"Für '{show_name}' ({ep_str}) wurde der Download an JDownloader übergeben.")

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

LOGO_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="112" fill="#0f172a"/>
  <path d="M256 96c-61.85 0-112 50.15-112 112v48.63L106.67 312A32 32 0 00133.33 360h245.34a32 32 0 0026.66-48L368 256.63V208c0-61.85-50.15-112-112-112z" fill="#06b6d4"/>
  <path d="M224 400a32 32 0 0064 0h-64z" fill="#3b82f6"/>
  <circle cx="360" cy="152" r="32" fill="#ef4444"/>
</svg>'''

HTML_TEMPLATE = """
<!doctype html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Jellyfin Notifier</title>
    <link rel="manifest" href="/manifest.json">
    <link rel="icon" type="image/svg+xml" href="/logo.svg">
    <link rel="apple-touch-icon" href="/logo.svg">
    <meta name="theme-color" content="#0f172a">
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

        .logo-img {
            width: 64px;
            height: 64px;
            margin-bottom: 0.75rem;
            filter: drop-shadow(0 4px 6px rgba(0,0,0,0.3));
        }

        header h1 {
            font-size: 1.8rem;
            margin: 0 0 0.3rem 0;
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

        .section-title {
            font-size: 1rem;
            color: var(--accent);
            margin: 1.5rem 0 0.75rem 0;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.25rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
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
            margin-top: 1.5rem;
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
            <img src="/logo.svg" alt="Logo" class="logo-img">
            <h1>Jellyfin Notifier</h1>
            <p>Automatisierter Smart-Lücken-Detektor & JDownloader-Brücke</p>
        </header>

        <div class="card">
            {% if msg %}
                <div class="alert alert-success">{{ msg }}</div>
            {% endif %}
            
            <form method="POST" action="/save">
                <div class="form-grid">
                    <div class="section-title">Jellyfin & RSS</div>
                    <div class="form-grid-2">
                        <div class="form-group">
                            <label>Jellyfin URL</label>
                            <input type="text" name="jellyfin_url" value="{{ config.jellyfin_url }}" required>
                        </div>
                        <div class="form-group">
                            <label>Jellyfin API-Key</label>
                            <input type="text" name="jellyfin_api_key" value="{{ config.jellyfin_api_key }}">
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

                    <div class="section-title">Webseiten-Zugang (für Hoster-Links)</div>
                    <div class="form-group">
                        <label>Webseiten-Cookie (Session-Cookie)</label>
                        <input type="text" name="website_cookie" value="{{ config.website_cookie }}" placeholder="cf_clearance=...">
                        <small style="color: var(--text-muted); margin-top: 0.3rem;">Dein Cloudflare-Cookie zur Umgehung von Verifizierungen.</small>
                    </div>

                    <div class="section-title">Benachrichtigungen & Download</div>
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

                    <div class="form-grid-2">
                        <div class="form-group">
                            <label>MyJDownloader E-Mail</label>
                            <input type="email" name="myjd_email" value="{{ config.myjd_email }}">
                        </div>
                        <div class="form-group">
                            <label>MyJDownloader Passwort</label>
                            <input type="password" name="myjd_password" value="{{ config.myjd_password }}">
                        </div>
                    </div>

                    <div class="form-group">
                        <label>MyJDownloader Gerätename (optional)</label>
                        <input type="text" name="myjd_device_name" value="{{ config.myjd_device_name }}" placeholder="Leer lassen = nimmt erstes Gerät">
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

@app.route("/logo.svg")
def serve_logo():
    return Response(LOGO_SVG, mimetype="image/svg+xml")

@app.route("/manifest.json")
def serve_manifest():
    manifest = {
        "name": "Jellyfin Notifier",
        "short_name": "Notifier",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": [
            {
                "src": "/logo.svg",
                "sizes": "any",
                "type": "image/svg+xml"
            }
        ]
    }
    return Response(json.dumps(manifest), mimetype="application/json")

@app.route("/save", methods=["POST"])
def save():
    new_config = {
        "jellyfin_url": request.form.get("jellyfin_url"),
        "jellyfin_api_key": request.form.get("jellyfin_api_key"),
        "pushover_token": request.form.get("pushover_token"),
        "pushover_user": request.form.get("pushover_user"),
        "myjd_email": request.form.get("myjd_email"),
        "myjd_password": request.form.get("myjd_password"),
        "myjd_device_name": request.form.get("myjd_device_name"),
        "website_cookie": request.form.get("website_cookie"),
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
