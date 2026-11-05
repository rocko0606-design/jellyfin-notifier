import os
import sys
import subprocess
import json
import threading
import time
import re
import requests
import sqlite3
import xml.etree.ElementTree as ET
from flask import Flask, render_template_string, request, redirect, url_for, Response

# Automatischer Selbst-Test & Installation für benötigte Bibliotheken
try:
    import flask
    import myjdapi
    import Crypto
    from playwright.sync_api import sync_playwright
except ImportError:
    print("📦 Installiere benötigte Bibliotheken (Flask, Playwright, etc.) automatisch...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "myjdapi", "pycryptodome", "playwright"])
    try:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    except:
        pass
    import flask
    import myjdapi
    import from playwright.sync_api import sync_playwright

app = Flask(__name__)
CONFIG_FILE = "/app/data/config.json"
DB_FILE = "/app/data/download_history.db"  # Lokale SQLite-Datenbank im persistenten Ordner
TARGET_DIR = "/mnt/downloads/extracted"    # Windows-Freigabe Ordner für fertige Downloads

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

# --- Datenbank- und Ordner-Prüflogik ---
def init_db():
    """Initialisiert die lokale SQLite-Datenbank."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS history (episode_key TEXT PRIMARY KEY)''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ DB-Fehler bei Initialisierung: {e}")

def is_in_db(episode_key):
    """Prüft, ob die Episode bereits in der lokalen Datenbank steht."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM history WHERE episode_key = ?', (episode_key,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    except:
        return False

def add_to_db(episode_key):
    """Fügt eine verarbeitete Episode der lokalen Datenbank hinzu."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO history (episode_key) VALUES (?)', (episode_key,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ DB-Fehler beim Hinzufügen: {e}")

def check_folder(show_name, episode_code):
    """Durchsucht den extracted-Ordner auf der Windows-Freigabe nach der Datei."""
    if not os.path.exists(TARGET_DIR):
        return False
    try:
        for root, dirs, files in os.walk(TARGET_DIR):
            for file in files:
                # Prüft, ob sowohl der Serienname (oder Teile davon) als auch der Episodencode (z.B. S01E02) im Dateinamen vorkommen
                if episode_code.lower() in file.lower():
                    return True
    except Exception as e:
        print(f"⚠️ Fehler beim Durchsuchen des Ordners: {e}")
    return False

# Initialisiere die DB beim Start
init_db()

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
    """Übermittelt den Hoster-Link via myjdapi an JDownloader"""
    config = load_config()
    email = config.get("myjd_email")
    password = config.get("myjd_password")
    device_name = config.get("myjd_device_name")
    
    if not email or not password or not url:
        print("⚠️ MyJDownloader: E-Mail, Passwort oder URL fehlt.")
        return False
        
    try:
        import myjdapi
        jd = myjdapi.Myjdapi()
        jd.set_app_key("JellyfinNotifier")
        if not jd.connect(email, password):
            print("❌ MyJDownloader: Verbindung fehlgeschlagen.")
            return False
            
        devices = jd.list_devices()
        if not devices:
            print("❌ MyJDownloader: Keine Geräte gefunden.")
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
        print(f"📥 Link erfolgreich an JDownloader übergeben: {url}")
        return True
    except Exception as e:
        print(f"❌ MyJDownloader Fehler: {e}")
        return False

def extract_hoster_link(release_page_url):
    """Öffnet Release-Seite, fischt den /go/-Token-Link heraus und klickt auf der Zwischenseite alle Elemente durch"""
    if not release_page_url or "http" not in release_page_url:
        return None
        
    config = load_config()
    cookie_val = config.get("website_cookie", "").strip()
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            if cookie_val:
                cookie_parts = cookie_val.split("=")
                c_name = cookie_parts[0].strip()
                c_value = cookie_parts[1].strip() if len(cookie_parts) > 1 else ""
                context.add_cookies([{
                    "name": c_name,
                    "value": c_value,
                    "domain": ".nox.to",
                    "path": "/"
                }])

            page = context.new_page()
            
            final_filer_link = []
            page.on("request", lambda req: final_filer_link.append(req.url) if "filer.net" in req.url else None)

            print(f"🌐 Öffne Release-Seite: {release_page_url}")
            page.goto(release_page_url, timeout=15000)
            page.wait_for_timeout(2000)
            
            go_link = page.locator("a[href*='/go/']").first
            if go_link.count() > 0:
                go_href = go_link.get_attribute("href")
                if go_href.startswith("/"):
                    go_href = "https://nox.to" + go_href
                
                print(f"🔗 Gefundener Token-Link, öffne Zwischenseite: {go_href}")
                page.goto(go_href, timeout=15000)
                page.wait_for_timeout(2500)
                
                if "filer.net" in page.url:
                    print(f"✅ Filer-Link direkt über URL erreicht: {page.url}")
                    browser.close()
                    return page.url

                buttons = page.locator("a, button").all()
                for btn in buttons:
                    try:
                        if btn.is_visible():
                            btn.click(timeout=1000)
                            page.wait_for_timeout(2000)
                            
                            if "filer.net" in page.url:
                                target = page.url
                                print(f"✅ Filer-Link erreicht: {target}")
                                browser.close()
                                return target
                            if final_filer_link:
                                target = final_filer_link[0]
                                print(f"✅ Filer-Link über Netzwerk abgefangen: {target}")
                                browser.close()
                                return target
                    except:
                        pass

            if final_filer_link:
                print(f"✅ Filer-Link über Netzwerk abgefangen: {final_filer_link[0]}")
                browser.close()
                return final_filer_link[0]

            content = page.content()
            browser.close()
            filer_match = re.search(r'https?://[^\s<>"]+filer\.net/(?:folder|f)/[a-zA-Z0-9]+', content, re.IGNORECASE)
            if filer_match:
                found_url = filer_match.group(0).rstrip('"\'')
                print(f"✅ Filer-Link im DOM gefunden: {found_url}")
                return found_url

            print("⚠️ Konnte keinen Filer-Link ermitteln.")
    except Exception as e:
        print(f"❌ Playwright Fehler: {e}")
        
    return None

def get_jellyfin_libraries(jellyfin_url, api_key):
    if not jellyfin_url or not api_key:
        return []
    headers = {"X-Emby-Token": api_key, "Authorization": f'MediaBrowser Token="{api_key}"'}
    try:
        url = f"{jellyfin_url.rstrip('/')}/Library/VirtualFolders"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            return res.json()
    except:
        pass
    return []

def get_library_series_with_ids(jellyfin_url, api_key, library_id):
    if not jellyfin_url or not api_key or not library_id:
        return []
    headers = {"X-Emby-Token": api_key, "Authorization": f'MediaBrowser Token="{api_key}"'}
    try:
        url = f"{jellyfin_url.rstrip('/')}/Items?ParentId={library_id}&includeItemTypes=Series&recursive=true"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return [{"id": item.get("Id"), "name": item.get("Name")} for item in data.get("Items", []) if item.get("Name")]
    except:
        pass
    return []

def get_existing_episodes(jellyfin_url, api_key, series_id):
    headers = {"X-Emby-Token": api_key, "Authorization": f'MediaBrowser Token="{api_key}"'}
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
    except:
        pass
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
    base_text = raw_title.split("—")[0].strip() if "—" in raw_title else raw_title
    multi_match = re.search(r's(\d+)\s*e(\d+)\s*-\s*(?:e)?(\d+)', raw_title, re.IGNORECASE)
    ep_codes = []
    if multi_match:
        season = int(multi_match.group(1))
        for ep_num in range(int(multi_match.group(2)), int(multi_match.group(3)) + 1):
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
    clean_name = base_text.replace('.', ' ').replace('_', ' ').strip()
    return clean_name, ep_codes

def perform_check():
    config = load_config()
    jellyfin_url = config.get("jellyfin_url")
    api_key = config.get("jellyfin_api_key")
    rss_url = config.get("rss_url")
    library_id = config.get("selected_library_id")
    
    results = []
    if not library_id:
        return [{"text": "⚠️ Keine Jellyfin-Bibliothek ausgewählt.", "class": "warning"}]

    local_shows = get_library_series_with_ids(jellyfin_url, api_key, library_id)
    if not local_shows:
        return [{"text": "⚠️ Keine Serien in der Bibliothek gefunden.", "class": "warning"}]

    shows_dict = {normalize_name(show["name"]): show for show in local_shows}

    feed_items = []
    try:
        res = requests.get(rss_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title_elem = item.find("title")
                link_elem = item.find("link")
                text_to_check = title_elem.text if title_elem is not None else ""
                item_link = link_elem.text if link_elem is not None else ""
                if text_to_check:
                    feed_items.append({"title": text_to_check, "link": item_link})
    except:
        pass

    missing_found = 0
    for entry in feed_items:
        feed_show_name, ep_codes = clean_feed_title(entry["title"])
        matched_show = shows_dict.get(normalize_name(feed_show_name))
        
        if matched_show and ep_codes:
            existing_eps = get_existing_episodes(jellyfin_url, api_key, matched_show["id"])
            
            # Filterung: 
            # 1. Noch nicht in Jellyfin
            # 2. Nicht in lokaler SQLite-Datenbank
            # 3. Nicht physisch im extracted-Ordner vorhanden
            truly_missing = []
            for ep in ep_codes:
                if ep in existing_eps:
                    continue
                
                db_key = f"{matched_show['name']} - {ep}"
                if is_in_db(db_key):
                    print(f"[{db_key}] Bereits in lokaler Datenbank erfasst. Überspringe.")
                    continue
                
                if check_folder(matched_show['name'], ep):
                    print(f"[{db_key}] Im extracted-Ordner gefunden. Trage in DB ein und überspringe.")
                    add_to_db(db_key)
                    continue
                
                truly_missing.append(ep)
            
            if truly_missing:
                missing_found += 1
                ep_str = ", ".join(truly_missing)
                hoster_url = extract_hoster_link(entry["link"])
                
                jd_status = ""
                if hoster_url:
                    if send_to_jdownloader(hoster_url, f"{matched_show['name']} - {ep_str}"):
                        jd_status = " | 📥 <b>An JDownloader übergeben!</b>"
                        send_pushover("Download gestartet!", f"Für '{matched_show['name']}' ({ep_str}) wurde der Download an JDownloader übergeben.")
                        # Nach erfolgreicher Übergabe in die DB eintragen
                        for ep in truly_missing:
                            add_to_db(f"{matched_show['name']} - {ep}")
                    else:
                        jd_status = " | ⚠️ <i>JDownloader Fehler</i>"
                else:
                    jd_status = " | ⚠️ <i>Kein Link gefunden</i>"

                results.append({
                    "text": f"'{matched_show['name']}' – Episoden <b>{ep_str}</b> fehlen!{jd_status}<br><small style='color: #8c9ba5;'>Release: {entry['title']}</small>",
                    "class": "missing"
                })

    if missing_found == 0:
        results.append({"text": "✅ Alle Feed-Einträge geprüft – keine Lücken gefunden (oder bereits lokal/im Ordner vorhanden).", "class": "success"})
    return results

def background_checker():
    while True:
        config = load_config()
        time.sleep(float(config.get("check_interval_hours", 6)) * 3600)
        perform_check()

threading.Thread(target=background_checker, daemon=True).start()

LOGO_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="112" fill="#0f172a"/>
  <path d="M256 96c-61.85 0-112 50.15-112 112v48.63L106.67 312A32 32 0 00133.33 360h245.34a32 32 0 0026.66-48L368 256.63V208c0-61.85-50.15-112-112-112z" fill="#06b6d4"/>
</svg>'''

HTML_TEMPLATE = """
<!doctype html>
<html lang="de">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>🔔 Jellyfin Notifier</title>
    <link rel="icon" type="image/svg+xml" href="/logo.svg">
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
        }
        body {
            font-family: system-ui, -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 2rem 1rem;
            display: flex;
            justify-content: center;
        }
        .container { width: 100%; max-width: 850px; }
        .card {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 2rem;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
            margin-bottom: 2rem;
        }
        .form-group { display: flex; flex-direction: column; margin-bottom: 1rem; }
        label { font-size: 0.875rem; font-weight: 600; color: var(--text-muted); margin-bottom: 0.4rem; text-transform: uppercase; }
        input, select {
            width: 100%; padding: 0.75rem 1rem; background-color: #0f172a;
            border: 1px solid var(--border-color); color: #fff; border-radius: 8px; font-size: 0.95rem; box-sizing: border-box;
        }
        button {
            padding: 0.75rem 1.25rem; font-size: 0.95rem; font-weight: 600; border-radius: 8px; border: none; cursor: pointer;
        }
        .btn-primary { background-color: var(--accent); color: #0f172a; width: 100%; margin-top: 1rem; }
        .btn-primary:hover { background-color: var(--accent-hover); }
        .btn-check { background-color: #10b981; color: #fff; margin-right: 0.5rem; }
        .btn-check:hover { background-color: #059669; }
        .btn-test { background-color: #3b82f6; color: #fff; }
        .btn-test:hover { background-color: #2563eb; }
        .result-item { padding: 1rem; border-radius: 8px; margin-top: 0.75rem; border: 1px solid transparent; }
        .result-item.missing { background-color: var(--missing-bg); border-color: var(--missing-border); color: var(--missing-text); }
        .result-item.success { background-color: var(--success-bg); border-color: var(--success-border); color: var(--success-text); }
    </style>
</head>
<body>
    <div class="container">
        <header style="text-align: center; margin-bottom: 2rem;">
            <h1>🔔 Jellyfin Notifier</h1>
            <p style="color: var(--text-muted);">Smart-Lücken-Detektor & Playwright JDownloader-Brücke</p>
        </header>
        <div class="card">
            {% if msg %}<div style="padding: 1rem; background: rgba(22,101,52,0.2); border: 1px solid #166534; color: #4ade80; border-radius: 8px; margin-bottom: 1rem;">{{ msg }}</div>{% endif %}
            <form method="POST" action="/save">
                <div class="form-group">
                    <label>Jellyfin URL</label>
                    <input type="text" name="jellyfin_url" value="{{ config.jellyfin_url }}" required>
                </div>
                <div class="form-group">
                    <label>Jellyfin API-Key</label>
                    <input type="text" name="jellyfin_api_key" value="{{ config.jellyfin_api_key }}">
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
                    <label>Webseiten-Cookie (Session/Cloudflare)</label>
                    <input type="text" name="website_cookie" value="{{ config.website_cookie }}" placeholder="cf_clearance=...">
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                    <div class="form-group">
                        <label>Pushover App Token</label>
                        <input type="text" name="pushover_token" value="{{ config.pushover_token }}">
                    </div>
                    <div class="form-group">
                        <label>Pushover User Key</label>
                        <input type="text" name="pushover_user" value="{{ config.pushover_user }}">
                    </div>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
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
                    <input type="text" name="myjd_device_name" value="{{ config.myjd_device_name }}" placeholder="Leer lassen = erstes Gerät">
                </div>
                <div class="form-group">
                    <label>Prüfintervall (in Stunden)</label>
                    <input type="number" step="0.1" name="check_interval_hours" value="{{ config.check_interval_hours }}" required>
                </div>
                <div style="display: flex; gap: 0.5rem; margin-top: 1.5rem; flex-wrap: wrap;">
                    <button type="submit" name="action" value="run_check" class="btn-check">Jetzt prüfen</button>
                    <button type="submit" name="action" value="test" class="btn-test">Test-Nachricht</button>
                </div>
                <button type="submit" name="action" value="save" class="btn-primary">Einstellungen speichern</button>
            </form>
            {% if results %}
                <div style="margin-top: 2rem; border-top: 1px solid var(--border-color); padding-top: 1.5rem;">
                    <h3>Ergebnis des Abgleichs</h3>
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
    if status == "saved":
        msg = "Einstellungen erfolgreich gespeichert!"
    elif status == "test_ok":
        msg = "Test-Nachricht via Pushover erfolgreich gesendet!"
    elif status == "test_fail":
        msg = "Pushover Test fehlgeschlagen! Token oder User Key prüfen."
    else:
        msg = None
    return render_template_string(HTML_TEMPLATE, config=config, libraries=libraries, msg=msg, results=app.config.get("LAST_RESULTS"))

@app.route("/logo.svg")
def serve_logo():
    return Response(LOGO_SVG, mimetype="image/svg+xml")

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
    
    action = request.form.get("action")
    if action == "test":
        success = send_pushover("Jellyfin Test", "Das ist eine Testnachricht von deinem Notifier!")
        return redirect(url_for("index", status="test_ok" if success else "test_fail"))
    elif action == "run_check":
        app.config["LAST_RESULTS"] = perform_check()
        return redirect(url_for("index"))
        
    return redirect(url_for("index", status="saved"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
