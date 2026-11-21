"""
J.A.R.V.I.S. — osobní desktop asistent
Backend: lokální server (jen pro tento PC) + ovládání Windows + Claude mozek.
UI: ui/index.html, otevírá se jako samostatné okno přes Microsoft Edge (app mode).
Žádné pip install — jen Python 3.10+.
"""
import json, os, sys, re, time, uuid, hashlib, shutil, threading, subprocess, platform, webbrowser, datetime, difflib, unicodedata, socket, urllib.request, urllib.parse, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

IS_WIN = platform.system() == "Windows"
FROZEN = bool(getattr(sys, "frozen", False))          # zabalená aplikace (PyInstaller .exe / .app)
HERE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(getattr(sys, "_MEIPASS", HERE), "ui")


def self_cmd():
    """Příkaz, kterým se Jarvis spustí znovu (skript přes Python, nebo přímo .exe)."""
    if FROZEN:
        return [sys.executable]
    exe = sys.executable
    if IS_WIN:
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else exe
    return [exe, os.path.abspath(__file__)]
APP_DIR = os.path.join(os.path.expanduser("~"), ".jarvis")
os.makedirs(APP_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DATA_PATH = os.path.join(APP_DIR, "data.json")
PORT = 17772
NO_WINDOW = 0x08000000 if IS_WIN else 0

DEFAULT_CONFIG = {
    "name": "",
    "vocative": "",
    "api_key": "",
    "groq_key": "",
    "groq_model": "openai/gpt-oss-120b",
    "brain": "auto",
    "model": "claude-haiku-5-5",
    "voice": True,
    "voice_name": "",
    "web_search": True,
    "allow_shell": False,
    "wake_word": False,
    "autostart": False,
    "city": "",
    "voice_rate": 1.0,
    "core_color": "vibra",
    "theme_v34": False,
    "ui_sounds": True,
    "tts_provider": "edge",
    "eleven_key": "",
    "eleven_voice": "JBFqnCBsd6RMkjVDRZzb",
    "eleven_model": "eleven_multilingual_v2",
    "ai_voice": False,
    "visual": {},
    "onboarded": False,
}

LOCK = threading.RLock()


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(default, dict):
            merged = dict(default)
            merged.update({k: v for k, v in data.items() if k in default or k in ("user_name",)})
            return merged
        return data
    except Exception:
        return json.loads(json.dumps(default))


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _raw_cfg():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}
_RAW_CFG = _raw_cfg()
CFG = load_json(CONFIG_PATH, DEFAULT_CONFIG)
CFG.pop("user_name", None)
if "onboarded" not in _RAW_CFG and _RAW_CFG.get("name"):     # existing install -> skip first-run wizard
    CFG["onboarded"] = True
    try:
        save_json(CONFIG_PATH, CFG)
    except Exception:
        pass
if not CFG.get("theme_v34"):          # v3.4: nový výchozí motiv „Vibra“
    CFG["core_color"] = "vibra"; CFG["theme_v34"] = True
    try:
        save_json(CONFIG_PATH, CFG)
    except Exception:
        pass
DATA = load_json(DATA_PATH, {"notes": [], "reminders": [], "todos": [], "log": [], "app_usage": {}, "last_seen": None, "first_run": None})
for k, v in {"kv": {}, "notes": [], "reminders": [], "todos": [], "routines": None, "log": [], "app_usage": {}, "last_seen": None, "first_run": None}.items():
    DATA.setdefault(k, v)
if not DATA["first_run"]:
    DATA["first_run"] = time.time()


def save_data():
    with LOCK:
        DATA["log"] = DATA["log"][-3000:]
        save_json(DATA_PATH, DATA)


def log(kind, text, extra=None):
    with LOCK:
        entry = {"ts": time.time(), "kind": kind, "text": str(text)[:2000]}
        if extra:
            entry.update(extra)
        DATA["log"].append(entry)
    save_data()


def norm(s):
    s = unicodedata.normalize("NFKD", str(s).lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


# =====================================================================
#  EVENTS (server -> UI)
# =====================================================================
EVENTS = []
EVENT_ID = [0]
CONFIRMS = {}


def push_event(ev):
    with LOCK:
        EVENT_ID[0] += 1
        ev["id"] = EVENT_ID[0]
        EVENTS.append(ev)
        del EVENTS[:-200]


def ask_confirm(command, timeout=120):
    cid = uuid.uuid4().hex[:8]
    ev = threading.Event()
    CONFIRMS[cid] = {"event": ev, "ok": False}
    push_event({"type": "confirm", "cid": cid, "command": command})
    ev.wait(timeout)
    return CONFIRMS.pop(cid, {}).get("ok", False)


# =====================================================================
#  APP DISCOVERY
# =====================================================================
APPS = {}  # name -> path
APP_ALIASES = {
    "kalkulacka": "calc", "calculator": "calc", "notepad": "notepad", "poznamkovy blok": "notepad",
    "pruzkumnik": "explorer", "explorer": "explorer", "soubory": "explorer", "spravce uloh": "taskmgr",
    "task manager": "taskmgr", "nastaveni": "ms-settings:", "settings": "ms-settings:", "cmd": "cmd",
    "terminal": "wt", "paint": "mspaint", "malovani": "mspaint", "ovladaci panely": "control",
    "stazene soubory": "shell:Downloads", "stazene": "shell:Downloads", "downloads": "shell:Downloads",
    "dokumenty": "shell:Personal", "plocha": "shell:Desktop", "obrazky": "shell:My Pictures",
    "hudba": "shell:My Music", "videa": "shell:My Video", "kos": "shell:RecycleBinFolder",
    "snipping tool": "ms-screenclip:", "vystrizky": "ms-screenclip:", "screenshot": "ms-screenclip:",
    "bluetooth": "ms-settings:bluetooth", "wifi": "ms-settings:network-wifi", "zvuk": "ms-settings:sound",
    "obrazovka": "ms-settings:display", "aktualizace": "ms-settings:windowsupdate",
    "nastaveni zvuku": "ms-settings:sound", "nastaveni obrazovky": "ms-settings:display", "nastaveni wifi": "ms-settings:network-wifi",
    "nastaveni bluetooth": "ms-settings:bluetooth", "aplikace po spusteni": "ms-settings:startupapps", "uloziste": "ms-settings:storagesense",
    "tapeta": "ms-settings:personalization-background", "pozadi": "ms-settings:personalization-background",
    "obchod": "ms-windows-store:", "microsoft store": "ms-windows-store:", "xbox": "xbox:",
}
URL_ALIASES = {
    "youtube": "https://youtube.com", "yt": "https://youtube.com", "gmail": "https://mail.google.com",
    "google": "https://google.com", "github": "https://github.com", "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai", "netflix": "https://netflix.com", "twitch": "https://twitch.tv",
    "instagram": "https://instagram.com", "tiktok": "https://tiktok.com", "reddit": "https://reddit.com",
    "x": "https://x.com", "twitter": "https://x.com", "wikipedia": "https://cs.wikipedia.org",
    "bakalari": "https://www.bakalari.cz", "mapy": "https://mapy.cz", "seznam": "https://seznam.cz",
    "roblox": "https://roblox.com", "modrinth": "https://modrinth.com", "curseforge": "https://curseforge.com",
    "chess": "https://chess.com", "translate": "https://translate.google.com", "prekladac": "https://translate.google.com",
}
SKIP_WORDS = ("uninstall", "odinstal", "readme", "help", "napoveda", "license", "documentation", "website", "release notes")


def discover_apps():
    found = {}
    if not IS_WIN:
        APPS.clear(); APPS.update(found); return
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "OneDrive", "Plocha"),
        os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
        os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
    ]
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        depth0 = d.rstrip("\\/").count(os.sep)
        for root, subdirs, files in os.walk(d):
            if root.count(os.sep) - depth0 >= 3:
                subdirs[:] = []
            for f in files:
                low = f.lower()
                if not (low.endswith(".lnk") or low.endswith(".url")):
                    continue
                name = os.path.splitext(f)[0]
                if any(w in norm(name) for w in SKIP_WORDS):
                    continue
                if name not in found:
                    found[name] = os.path.join(root, f)
    with LOCK:
        APPS.clear(); APPS.update(found)


def find_app(query):
    q = norm(query)
    q = re.sub(r"^(aplikaci|aplikace|appku|apku|program|hru)\s+", "", q).strip()
    if q in APP_ALIASES:
        return ("alias", APP_ALIASES[q], query)
    names = list(APPS.keys())
    nmap = {norm(n): n for n in names}
    if q in nmap:
        return ("app", APPS[nmap[q]], nmap[q])
    starts = [n for k, n in nmap.items() if k.startswith(q)]
    if starts:
        n = min(starts, key=len); return ("app", APPS[n], n)
    contains = [n for k, n in nmap.items() if q in k]
    if contains:
        n = min(contains, key=len); return ("app", APPS[n], n)
    close = difflib.get_close_matches(q, list(nmap.keys()), n=1, cutoff=0.72)
    if close:
        n = nmap[close[0]]; return ("app", APPS[n], n)
    if q in URL_ALIASES:
        return ("url", URL_ALIASES[q], query)
    return None


# =====================================================================
#  SYSTEM STATS
# =====================================================================
_cpu_prev = [None]
_gpu_cache = {"t": 0, "v": None}


def _cpu_percent():
    if not IS_WIN:
        try:
            with open("/proc/stat") as f:
                parts = [float(x) for x in f.readline().split()[1:]]
            idle, total = parts[3] + parts[4], sum(parts)
        except Exception:
            return None
    else:
        import ctypes
        from ctypes import wintypes
        i, k, u = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
        ft = lambda x: (x.dwHighDateTime << 32) | x.dwLowDateTime
        idle, total = ft(i), ft(k) + ft(u)
    prev = _cpu_prev[0]
    _cpu_prev[0] = (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1 - (idle - prev[0]) / (total - prev[1])))), 1)


def _gpu():
    if time.time() - _gpu_cache["t"] < 3:
        return _gpu_cache["v"]
    _gpu_cache["t"] = time.time()
    exe = shutil.which("nvidia-smi")
    if not exe:
        _gpu_cache["v"] = None
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3, creationflags=NO_WINDOW).stdout.strip().splitlines()[0]
        name, util, temp, mu, mt = [x.strip() for x in out.split(",")]
        _gpu_cache["v"] = {"name": name, "util": float(util), "temp": float(temp), "vram": round(float(mu) / float(mt) * 100, 1)}
    except Exception:
        _gpu_cache["v"] = None
    return _gpu_cache["v"]


def system_stats():
    s = {"time": datetime.datetime.now().strftime("%H:%M:%S"), "cpu": _cpu_percent(), "threads": os.cpu_count()}
    try:
        total, used, free = shutil.disk_usage(os.path.abspath(os.sep))
        s["disk"] = round(used / total * 100, 1); s["disk_free_gb"] = round(free / 1e9)
    except Exception:
        pass
    if IS_WIN:
        import ctypes

        class MEM(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEM(); m.dwLength = ctypes.sizeof(MEM)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        s["ram"] = float(m.dwMemoryLoad); s["ram_total_gb"] = round(m.ullTotalPhys / 1e9)

        class PWR(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte), ("BatteryLifePercent", ctypes.c_byte),
                        ("SystemStatusFlag", ctypes.c_byte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        p = PWR()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p)) and (p.BatteryFlag & 128) == 0 and p.BatteryLifePercent <= 100:
            s["battery"] = int(p.BatteryLifePercent)
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        s["uptime_h"] = round(ctypes.windll.kernel32.GetTickCount64() / 3.6e6, 1)
    else:
        try:
            with open("/proc/meminfo") as f:
                mi = {l.split(":")[0]: int(l.split()[1]) for l in f}
            s["ram"] = round(100 * (1 - mi["MemAvailable"] / mi["MemTotal"]), 1); s["ram_total_gb"] = round(mi["MemTotal"] / 1e6)
        except Exception:
            pass
    g = _gpu()
    if g:
        s["gpu"] = g
    return s


# =====================================================================
#  TOOLS (co Jarvis umí na PC)
# =====================================================================
def t_open_app(name):
    hit = find_app(name)
    if not hit:
        return f"Aplikaci '{name}' jsem nenašel. Nainstalované zkratky: " + ", ".join(sorted(APPS.keys())[:60])
    kind, target, label = hit
    try:
        if kind == "url":
            webbrowser.open(target)
        elif IS_WIN:
            if kind == "alias" and ":" not in target and not os.path.exists(target):
                subprocess.Popen(target, shell=True, creationflags=NO_WINDOW)
            else:
                os.startfile(target)
        else:
            return f"(test) spustil bych {label}"
        with LOCK:
            DATA["app_usage"][label] = DATA["app_usage"].get(label, 0) + 1
        log("action", f"Spuštěno: {label}", {"app": label})
        return f"Spuštěno: {label}"
    except Exception as e:
        return f"Nepodařilo se spustit {label}: {e}"


def t_open_url(url):
    if not re.match(r"^[a-z]+:", url):
        url = "https://" + url
    webbrowser.open(url)
    log("action", f"Otevřeno: {url}")
    return f"Otevřeno {url}"


SEARCH_SITES = {
    "google": "https://www.google.com/search?q={}", "youtube": "https://www.youtube.com/results?search_query={}",
    "wikipedia": "https://cs.wikipedia.org/w/index.php?search={}", "github": "https://github.com/search?q={}",
    "mapy": "https://mapy.cz/?q={}", "obrazky": "https://www.google.com/search?tbm=isch&q={}",
}


def t_web_search(query, site="google"):
    webbrowser.open(SEARCH_SITES.get(site, SEARCH_SITES["google"]).format(urllib.parse.quote_plus(query)))
    log("action", f"Hledání ({site}): {query}")
    return f"Otevřeno hledání '{query}' na {site}."


def t_system_info():
    return json.dumps(system_stats(), ensure_ascii=False)


def t_note(action, text="", note_id=""):
    with LOCK:
        if action == "add":
            n = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "ts": time.time()}
            DATA["notes"].append(n)
            save_data(); log("note", text)
            push_event({"type": "data"})
            return f"Poznámka uložena (id {n['id']})."
        if action == "delete":
            before = len(DATA["notes"])
            DATA["notes"] = [n for n in DATA["notes"] if n["id"] != note_id and not (text and norm(text) in norm(n["text"]))]
            save_data(); push_event({"type": "data"})
            return "Smazáno." if len(DATA["notes"]) < before else "Takovou poznámku nevidím."
        if not DATA["notes"]:
            return "Žádné poznámky."
        return json.dumps([{"id": n["id"], "text": n["text"], "kdy": datetime.datetime.fromtimestamp(n["ts"]).strftime("%d.%m. %H:%M")} for n in DATA["notes"][-40:]], ensure_ascii=False)


def t_reminder(action, text="", in_minutes=None, at=None, reminder_id=""):
    with LOCK:
        if action == "add":
            if in_minutes is not None:
                due = time.time() + float(in_minutes) * 60
            elif at:
                due = parse_at(at)
                if due is None:
                    return "Nerozumím času. Použij formát 'HH:MM' nebo 'YYYY-MM-DD HH:MM'."
            else:
                return "Chybí čas (in_minutes nebo at)."
            r = {"id": uuid.uuid4().hex[:8], "text": text.strip() or "Připomínka", "due": due, "done": False}
            DATA["reminders"].append(r)
            save_data(); log("reminder", r["text"]); push_event({"type": "data"})
            return f"Připomínka nastavena na {datetime.datetime.fromtimestamp(due).strftime('%d.%m. %H:%M')}."
        if action == "delete":
            DATA["reminders"] = [r for r in DATA["reminders"] if r["id"] != reminder_id and not (text and norm(text) in norm(r["text"]))]
            save_data(); push_event({"type": "data"})
            return "Smazáno."
        up = [r for r in DATA["reminders"] if not r["done"]]
        if not up:
            return "Žádné nadcházející připomínky."
        return json.dumps([{"id": r["id"], "text": r["text"], "kdy": datetime.datetime.fromtimestamp(r["due"]).strftime("%d.%m. %H:%M")} for r in up], ensure_ascii=False)


def parse_at(s):
    s = s.strip()
    now = datetime.datetime.now()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d.%m.%Y %H:%M", "%d.%m. %H:%M"):
        try:
            d = datetime.datetime.strptime(s, fmt)
            if d.year == 1900:
                d = d.replace(year=now.year)
            return d.timestamp()
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2})(?:[:.](\d{2}))?$", s)
    if m:
        d = now.replace(hour=int(m.group(1)) % 24, minute=int(m.group(2) or 0), second=0, microsecond=0)
        if d <= now:
            d += datetime.timedelta(days=1)
        return d.timestamp()
    return None


VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "stop": 0xB2}


def t_media(action, steps=5):
    if action not in VK:
        return "Neznámá akce."
    if not IS_WIN:
        return f"(test) média: {action}"
    import ctypes
    reps = max(1, min(50, int(steps))) if action.startswith("volume") else 1
    for _ in range(reps):
        ctypes.windll.user32.keybd_event(VK[action], 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK[action], 0, 2, 0)
    log("action", f"Média: {action}")
    return f"Hotovo ({action})."


def t_lock_pc():
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
    log("action", "Počítač zamčen")
    return "Počítač zamčen."


def _ps(cmd, timeout=20):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    return (r.stdout + r.stderr).strip()


def t_clipboard(action, text=""):
    if not IS_WIN:
        return "(test) schránka"
    if action == "set":
        p = subprocess.Popen(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"], stdin=subprocess.PIPE, creationflags=NO_WINDOW)
        p.communicate(text.encode("utf-8"))
        return "Zkopírováno do schránky."
    return _ps("Get-Clipboard -Raw")[:6000] or "Schránka je prázdná."


def t_shell(command):
    if not CFG.get("allow_shell"):
        return "PowerShell je v nastavení vypnutý. Uživatel ho může zapnout v Nastavení."
    if not ask_confirm(command):
        return "Uživatel příkaz zamítl."
    log("action", f"PowerShell: {command}")
    try:
        if IS_WIN:
            return _ps(command, 90)[-6000:] or "(bez výstupu)"
        r = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=90)
        return (r.stdout + r.stderr)[-6000:] or "(bez výstupu)"
    except Exception as e:
        return f"Chyba: {e}"


def t_show_panel(panel):
    push_event({"type": "ui", "action": "panel", "panel": panel})
    return f"Panel {panel} otevřen."


def t_list_apps():
    return ", ".join(sorted(APPS.keys())) or "Žádné zkratky nenalezeny."


TOOLS = [
    {"name": "open_app", "description": "Spustí aplikaci, hru nebo složku na PC. Zná zkratky z plochy a Start menu (Discord, Spotify, Steam, Minecraft, Brave, Medal, Lunar Client, Fortnite, GTA…), systémové věci (kalkulačka, nastavení, správce úloh, stažené soubory, dokumenty, plocha, bluetooth, wifi) i weby (youtube, gmail…).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "list_apps", "description": "Vrátí seznam všech aplikací, které jde spustit.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "open_url", "description": "Otevře konkrétní webovou adresu v prohlížeči.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "browser_search", "description": "Otevře vyhledávání v prohlížeči uživatele (když chce výsledky vidět sám, např. video na YouTube).",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "site": {"type": "string", "enum": list(SEARCH_SITES.keys())}}, "required": ["query"]}},
    {"name": "system_info", "description": "Živý stav PC: CPU %, RAM %, GPU (vytížení, teplota), disk, baterie, uptime.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "note", "description": "Poznámky uživatele. add = uložit text, list = vypsat, delete = smazat (podle note_id nebo části textu).",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "list", "delete"]}, "text": {"type": "string"}, "note_id": {"type": "string"}}, "required": ["action"]}},
    {"name": "reminder", "description": "Připomínky. add: text + buď in_minutes (za kolik minut), nebo at ('HH:MM' / 'YYYY-MM-DD HH:MM'). list / delete.",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "list", "delete"]}, "text": {"type": "string"}, "in_minutes": {"type": "number"}, "at": {"type": "string"}, "reminder_id": {"type": "string"}}, "required": ["action"]}},
    {"name": "media", "description": "Ovládání hudby/videa a hlasitosti systému. steps = kolik kroků (1 krok = 2 %).",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": list(VK.keys())}, "steps": {"type": "integer"}}, "required": ["action"]}},
    {"name": "lock_pc", "description": "Zamkne počítač.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "clipboard", "description": "Přečte (get) nebo nastaví (set) schránku.",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["get", "set"]}, "text": {"type": "string"}}, "required": ["action"]}},
    {"name": "show_panel", "description": "Otevře v Jarvisově rozhraní panel.",
     "input_schema": {"type": "object", "properties": {"panel": {"type": "string", "enum": ["dashboard", "notes", "todo", "reminders", "timer", "weather", "media", "routines", "currency", "clipboard", "apps", "system", "chat", "commands", "settings", "timetable", "flash", "habits", "net", "focus", "web", "train", "countdown", "calc"]}}, "required": ["panel"]}},
    {"name": "todo", "description": "Úkoly (to-do). add = přidat, list = vypsat otevřené, done = označit hotový (text nebo todo_id), delete, clear_done.",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "list", "done", "toggle", "delete", "clear_done"]}, "text": {"type": "string"}, "todo_id": {"type": "string"}}, "required": ["action"]}},
    {"name": "weather", "description": "Aktuální počasí a předpověď (Open-Meteo). Bez města = domovské město uživatele.",
     "input_schema": {"type": "object", "properties": {"city": {"type": "string"}, "when": {"type": "string", "enum": ["dnes", "zitra"]}}}},
    {"name": "now_playing", "description": "Co právě hraje na PC (Spotify, YouTube…).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "shell", "description": "Spustí PowerShell příkaz (uživatel ho musí potvrdit). Jen když nic jiného nestačí.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
]
TOOL_FUNCS = {"open_app": t_open_app, "list_apps": t_list_apps, "open_url": t_open_url, "browser_search": t_web_search,
              "system_info": t_system_info, "note": t_note, "reminder": t_reminder, "media": t_media, "lock_pc": t_lock_pc,
              "clipboard": t_clipboard, "show_panel": t_show_panel, "shell": t_shell}


# =====================================================================
#  POČASÍ (Open-Meteo, bez klíče)
# =====================================================================
WMO = {0: "jasno", 1: "skoro jasno", 2: "polojasno", 3: "zataženo", 45: "mlha", 48: "námraza a mlha", 51: "slabé mrholení",
       53: "mrholení", 55: "silné mrholení", 56: "mrznoucí mrholení", 57: "mrznoucí mrholení", 61: "slabý déšť", 63: "déšť",
       65: "silný déšť", 66: "mrznoucí déšť", 67: "mrznoucí déšť", 71: "slabé sněžení", 73: "sněžení", 75: "silné sněžení",
       77: "sněhová zrna", 80: "přeháňky", 81: "přeháňky", 82: "silné přeháňky", 85: "sněhové přeháňky", 86: "sněhové přeháňky",
       95: "bouřka", 96: "bouřka s kroupami", 99: "silná bouřka s kroupami"}
_WX_CACHE = {}


def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Jarvis/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def weather(city=None):
    city = (city or CFG.get("city") or "").strip()
    if not city:
        raise RuntimeError("Domovské město není nastavené")
    c = _WX_CACHE.get(norm(city))
    if c and time.time() - c["t"] < 600:
        return c["v"]
    geo = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&language=cs&format=json&name=" + urllib.parse.quote(city))
    if not geo.get("results"):
        raise RuntimeError(f"Město '{city}' nenalezeno")
    g = geo["results"][0]
    q = (f"https://api.open-meteo.com/v1/forecast?latitude={g['latitude']}&longitude={g['longitude']}&timezone=auto&forecast_days=6"
         "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m,precipitation,is_day"
         "&hourly=temperature_2m,precipitation_probability,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset")
    w = _get_json(q)
    cur = w["current"]
    now_idx = 0
    times = w["hourly"]["time"]
    nowiso = cur["time"][:13]
    for i, t in enumerate(times):
        if t[:13] >= nowiso:
            now_idx = i; break
    v = {
        "city": g.get("name", city), "country": g.get("country", ""), "lat": g.get("latitude"), "lon": g.get("longitude"),
        "temp": cur["temperature_2m"], "feels": cur["apparent_temperature"], "code": cur["weather_code"],
        "desc": WMO.get(cur["weather_code"], "?"), "wind": cur["wind_speed_10m"], "humidity": cur["relative_humidity_2m"],
        "is_day": cur.get("is_day", 1),
        "hourly": [{"t": times[i][11:16], "temp": w["hourly"]["temperature_2m"][i], "pp": w["hourly"]["precipitation_probability"][i],
                    "code": w["hourly"]["weather_code"][i]} for i in range(now_idx, min(now_idx + 24, len(times)))],
        "daily": [{"d": w["daily"]["time"][i], "max": w["daily"]["temperature_2m_max"][i], "min": w["daily"]["temperature_2m_min"][i],
                   "code": w["daily"]["weather_code"][i], "desc": WMO.get(w["daily"]["weather_code"][i], "?"),
                   "pp": w["daily"]["precipitation_probability_max"][i]} for i in range(len(w["daily"]["time"]))],
        "sunrise": w["daily"]["sunrise"][0][11:16], "sunset": w["daily"]["sunset"][0][11:16],
    }
    _WX_CACHE[norm(city)] = {"t": time.time(), "v": v}
    return v


def weather_sentence(v, when="dnes"):
    t = round(v["temp"])
    d0 = v["daily"][0] if when == "dnes" else (v["daily"][1] if len(v["daily"]) > 1 else v["daily"][0])
    if when == "zitra":
        return (f"Zítra, {v['city']}: {d0['desc']}, od {round(d0['min'])} do {round(d0['max'])} stupňů"
                + (f", srážky {d0['pp']} procent." if d0.get("pp") else "."))
    return (f"{v['city']}: teď {t} {('stupeň' if abs(t) == 1 else 'stupně' if 2 <= abs(t) <= 4 else 'stupňů')}, {v['desc']}, "
            f"pocitově {round(v['feels'])}. Dnes {round(d0['min'])} až {round(d0['max'])}"
            + (f", šance na srážky {d0['pp']} procent." if d0.get("pp") else "."))


def t_weather(city="", when="dnes"):
    try:
        v = weather(city or None)
        return weather_sentence(v, when) + " " + json.dumps({"hodinově": v["hourly"][:8], "dny": v["daily"]}, ensure_ascii=False)
    except Exception as e:
        return f"Počasí se nepodařilo načíst: {e}"


# =====================================================================
#  ÚKOLY
# =====================================================================
def t_todo(action, text="", todo_id=""):
    with LOCK:
        if action == "add":
            t = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "done": False, "ts": time.time()}
            DATA["todos"].append(t)
            save_data(); log("todo", text); push_event({"type": "data"})
            return f"Úkol přidán: {text}"
        if action in ("toggle", "done"):
            for t in DATA["todos"]:
                if t["id"] == todo_id or (text and norm(text) in norm(t["text"])):
                    t["done"] = True if action == "done" else not t["done"]
                    t["done_ts"] = time.time()
                    save_data(); push_event({"type": "data"})
                    return f"Úkol '{t['text']}' {'hotový' if t['done'] else 'znovu otevřený'}."
            return "Takový úkol nevidím."
        if action == "delete":
            DATA["todos"] = [t for t in DATA["todos"] if t["id"] != todo_id and not (text and norm(text) in norm(t["text"]))]
            save_data(); push_event({"type": "data"}); return "Smazáno."
        if action == "clear_done":
            DATA["todos"] = [t for t in DATA["todos"] if not t["done"]]
            save_data(); push_event({"type": "data"}); return "Hotové úkoly uklizeny."
        open_ = [t for t in DATA["todos"] if not t["done"]]
        return json.dumps([{"id": t["id"], "text": t["text"]} for t in open_], ensure_ascii=False) if open_ else "Žádné otevřené úkoly."


# =====================================================================
#  SCHRÁNKA (historie jen v paměti – nic se neukládá na disk)
# =====================================================================
CLIP = []


def _clip_text():
    import ctypes
    from ctypes import wintypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetClipboardData.restype = wintypes.HANDLE
    k.GlobalLock.restype = ctypes.c_void_p; k.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not u.OpenClipboard(None):
        return None
    try:
        h = u.GetClipboardData(13)
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def clip_watch():
    import ctypes
    last = -1
    while True:
        time.sleep(1.2)
        try:
            seq = ctypes.windll.user32.GetClipboardSequenceNumber()
            if seq == last:
                continue
            last = seq
            t = _clip_text()
            if t and t.strip():
                t = t[:5000]
                with LOCK:
                    CLIP[:] = [c for c in CLIP if c["text"] != t]
                    CLIP.insert(0, {"text": t, "ts": time.time()})
                    del CLIP[40:]
        except Exception:
            pass


def set_clipboard(text):
    if not IS_WIN:
        return
    p = subprocess.Popen(["powershell", "-NoProfile", "-Command", "[Console]::InputEncoding=[Text.Encoding]::UTF8; $input | Set-Clipboard"],
                         stdin=subprocess.PIPE, creationflags=NO_WINDOW)
    p.communicate(text.encode("utf-8"))


# =====================================================================
#  CO HRAJE (Windows media session) – trvalý PowerShell, vypisuje JSON
# =====================================================================
MEDIA = {"title": "", "artist": "", "app": "", "status": "", "t": 0}
_MEDIA_PS = r"""
[Console]::OutputEncoding=[Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$g=([System.WindowsRuntimeSystemExtensions].GetMethods()|?{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($t,$r){ $a=$g.MakeGenericMethod($r); $n=$a.Invoke($null,@($t)); $n.Wait(-1)|Out-Null; $n.Result }
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]|Out-Null
$m=Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
while($true){
  try{
    $s=$m.GetCurrentSession()
    if($s){ $p=Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]); $i=$s.GetPlaybackInfo()
      [pscustomobject]@{title=$p.Title;artist=$p.Artist;app=$s.SourceAppUserModelId;status=$i.PlaybackStatus.ToString()}|ConvertTo-Json -Compress }
    else { '{"title":"","artist":"","app":"","status":"none"}' }
  } catch { '{"title":"","artist":"","app":"","status":"error"}' }
  Start-Sleep -Milliseconds 2500
}
"""


def media_watch():
    while True:
        try:
            ps1 = os.path.join(APP_DIR, "media_watch.ps1")
            with open(ps1, "w", encoding="utf-8-sig") as f:
                f.write(_MEDIA_PS)
            p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            for line in p.stdout:
                line = line.decode("utf-8", "ignore").strip()
                if line.startswith("{"):
                    try:
                        d = json.loads(line)
                        app = (d.get("app") or "").split("!")[-1].replace(".exe", "")
                        if (d.get("artist") or "").startswith("Text-to-Speech"):   # Jarvisův vlastní hlas není hudba
                            if MEDIA.get("artist", "").startswith("Text-to-Speech") or not MEDIA.get("title"):
                                MEDIA.update(title="", artist="", app="", status="none", t=time.time())
                            continue
                        MEDIA.update(title=d.get("title") or "", artist=d.get("artist") or "", app=app, status=d.get("status") or "", t=time.time())
                    except Exception:
                        pass
        except Exception as e:
            print("media_watch", e)
        time.sleep(20)


def t_now_playing():
    if not MEDIA.get("title"):
        return "Teď nic nehraje."
    st = {"Playing": "hraje", "Paused": "pozastaveno"}.get(MEDIA["status"], MEDIA["status"])
    return f"{MEDIA['title']}" + (f" – {MEDIA['artist']}" if MEDIA.get("artist") else "") + f" ({st}, {MEDIA.get('app')})"


# =====================================================================
#  PROCESY
# =====================================================================
_PROC_CACHE = {"t": 0, "v": []}


def processes():
    if time.time() - _PROC_CACHE["t"] < 3:
        return _PROC_CACHE["v"]
    if not IS_WIN:
        return []
    out = _ps("[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Process | Group-Object ProcessName | ForEach-Object { "
              "$m=($_.Group|Measure-Object WorkingSet64 -Sum).Sum; $c=($_.Group|Measure-Object CPU -Sum).Sum; "
              "[pscustomobject]@{n=$_.Name;c=$_.Count;mem=[math]::Round($m/1MB);cpu=[math]::Round($c,1);id=$_.Group[0].Id} } | "
              "Sort-Object mem -Descending | Select-Object -First 18 | ConvertTo-Json -Compress", 15)
    try:
        v = json.loads(out)
        if isinstance(v, dict):
            v = [v]
    except Exception:
        v = []
    _PROC_CACHE.update(t=time.time(), v=v)
    return v


PROTECTED_PROCS = {"system", "csrss", "wininit", "winlogon", "lsass", "services", "smss", "explorer", "dwm", "svchost", "registry", "fontdrvhost", "python", "pythonw", "msedge"}


def kill_process(name):
    if norm(name) in PROTECTED_PROCS:
        return "Tenhle proces radši nechám běžet."
    if IS_WIN:
        _ps(f"Stop-Process -Name '{name.replace(chr(39), '')}' -ErrorAction SilentlyContinue", 10)
    _PROC_CACHE["t"] = 0
    log("action", f"Ukončen proces: {name}")
    return f"{name} ukončen."


# =====================================================================
#  RUTINY (víc akcí jedním příkazem)
# =====================================================================
DEFAULT_ROUTINES = [
    {"id": "game", "name": "Herní režim", "steps": [{"t": "app", "v": "Discord"}, {"t": "app", "v": "Steam"}, {"t": "say", "v": "Herní režim aktivní. Hodně štěstí, pane."}]},
    {"id": "study", "name": "Učení", "steps": [{"t": "yt", "v": "lofi hip hop radio"}, {"t": "pomodoro", "v": ""}, {"t": "say", "v": "Soustředění zapnuto. Dvacet pět minut, pak pauza."}]},
    {"id": "night", "name": "Dobrou noc", "steps": [{"t": "media", "v": "stop"}, {"t": "say", "v": "Dobrou noc. Za deset sekund zamykám."}, {"t": "wait", "v": "10"}, {"t": "lock", "v": ""}]},
]
if not DATA.get("routines"):
    DATA["routines"] = json.loads(json.dumps(DEFAULT_ROUTINES))
STEP_TYPES = {"app": "Spustit aplikaci", "url": "Otevřít web", "yt": "YouTube hledání", "media": "Média", "volume": "Hlasitost (+/−)",
              "say": "Jarvis řekne", "timer": "Časovač (min)", "pomodoro": "Pomodoro", "wait": "Počkat (s)", "lock": "Zamknout PC"}


def find_routine(q):
    nq = norm(q)
    for r in DATA["routines"]:
        if r["id"] == q or norm(r["name"]) == nq:
            return r
    for r in DATA["routines"]:
        if norm(r["name"]) in nq or nq in norm(r["name"]):
            return r
    return None


def run_routine(name):
    r = find_routine(name)
    if not r:
        return "Takovou rutinu nemám. Dostupné: " + ", ".join(x["name"] for x in DATA["routines"])
    log("action", f"Rutina: {r['name']}")

    def worker():
        for st in r["steps"]:
            t, v = st.get("t"), str(st.get("v", ""))
            try:
                if t == "app": t_open_app(v)
                elif t == "url": t_open_url(v)
                elif t == "yt": t_web_search(v, "youtube")
                elif t == "media": t_media(v or "play_pause")
                elif t == "volume":
                    n = int(v or 0); t_media("volume_up" if n >= 0 else "volume_down", abs(n) // 2 or 5)
                elif t == "wait": time.sleep(min(600, float(v or 1)))
                elif t == "lock": t_lock_pc()
                elif t in ("say", "timer", "pomodoro"):
                    push_event({"type": "ui", "action": t, "value": v})
                time.sleep(0.6)
            except Exception as e:
                print("routine step", st, e)
    threading.Thread(target=worker, daemon=True).start()
    return f"Protokol {r['name']} aktivován."


def t_routine(action="run", name=""):
    if action == "list":
        return json.dumps([{"name": r["name"], "kroky": [f"{STEP_TYPES.get(s['t'], s['t'])}: {s.get('v', '')}" for s in r["steps"]]} for r in DATA["routines"]], ensure_ascii=False)
    return run_routine(name)


# =====================================================================
#  MĚNY (ECB kurzy přes frankfurter.app, bez klíče)
# =====================================================================
_FX = {"t": 0, "rates": {}, "date": ""}
CUR_WORDS = {"eur": "EUR", "euro": "EUR", "eura": "EUR", "eur.": "EUR", "dolar": "USD", "dolaru": "USD", "dolary": "USD", "dolarech": "USD", "usd": "USD",
             "korun": "CZK", "koruna": "CZK", "koruny": "CZK", "korunach": "CZK", "kc": "CZK", "czk": "CZK", "libra": "GBP", "liber": "GBP", "libry": "GBP",
             "librach": "GBP", "gbp": "GBP", "zloty": "PLN", "zlotych": "PLN", "zl": "PLN", "pln": "PLN", "frank": "CHF", "franku": "CHF", "chf": "CHF",
             "jen": "JPY", "jenu": "JPY", "jpy": "JPY", "forint": "HUF", "forintu": "HUF", "huf": "HUF", "dolaru kanadskych": "CAD", "cad": "CAD"}


def fx_rates():
    if time.time() - _FX["t"] < 3600 and _FX["rates"]:
        return _FX
    d = _get_json("https://api.frankfurter.app/latest?from=EUR")
    rates = dict(d["rates"]); rates["EUR"] = 1.0
    _FX.update(t=time.time(), rates=rates, date=d.get("date", ""))
    return _FX


def fx_convert(amount, frm, to):
    r = fx_rates()["rates"]
    if frm not in r or to not in r:
        raise ValueError("neznámá měna")
    return amount / r[frm] * r[to]


def cur_code(w):
    w = norm(w).strip(" .?!")
    if w.upper() in ("EUR", "USD", "CZK", "GBP", "PLN", "CHF", "JPY", "HUF", "CAD", "AUD", "SEK", "NOK", "DKK", "CNY"):
        return w.upper()
    for k, v in CUR_WORDS.items():
        if w == k or (len(w) > 3 and w.startswith(k[:5])):
            return v
    return None


def t_fx(amount=1, frm="EUR", to="CZK"):
    try:
        v = fx_convert(float(amount), frm.upper(), to.upper())
        return f"{amount:g} {frm.upper()} = {v:,.2f} {to.upper()} (ECB kurz {_FX['date']})".replace(",", " ")
    except Exception as e:
        return f"Převod se nepovedl: {e}"


TOOLS.append({"name": "routine", "description": "Rutiny uživatele (víc akcí najednou, např. Herní režim, Učení, Dobrou noc). action=run + name, nebo list.",
              "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["run", "list"]}, "name": {"type": "string"}}, "required": ["action"]}})
TOOLS.append({"name": "currency", "description": "Převod měn podle aktuálního kurzu ECB.",
              "input_schema": {"type": "object", "properties": {"amount": {"type": "number"}, "frm": {"type": "string", "description": "ISO kód, např. EUR"}, "to": {"type": "string"}}, "required": ["amount", "frm", "to"]}})
# =====================================================================
#  HLAS — ElevenLabs (oficiální API, vlastní klíč uživatele) + cache nahrávek
# =====================================================================
VOICE_DIR = os.path.join(APP_DIR, "voice_cache")
os.makedirs(VOICE_DIR, exist_ok=True)
ELEVEN_VOICES = [
    {"id": "JBFqnCBsd6RMkjVDRZzb", "name": "George · britský, hřejivý (Jarvis)"},
    {"id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel · britský, rozhodný"},
    {"id": "nPczCjzI2devNBz1zQrb", "name": "Brian · hluboký"},
    {"id": "pNInz6obpgDQGcFmaJgB", "name": "Adam · klidný"},
]
ELEVEN_BUDGET = 9500   # znaků / měsíc (free plán = 10 000 kreditů)


def _usage():
    m = time.strftime("%Y-%m")
    u = DATA.setdefault("tts_usage", {})
    if u.get("month") != m:
        u.clear(); u.update(month=m, chars=0)
    return u


def tts_cache_path(text, voice, model):
    h = hashlib.sha1(f"{voice}|{model}|{text}".encode("utf-8")).hexdigest()[:20]
    return os.path.join(VOICE_DIR, h + ".mp3")


def eleven_tts(text, allow_live=True):
    key = CFG.get("eleven_key")
    if not key:
        raise RuntimeError("chybí ElevenLabs klíč")
    voice, model = CFG.get("eleven_voice") or ELEVEN_VOICES[0]["id"], CFG.get("eleven_model") or "eleven_multilingual_v2"
    path = tts_cache_path(text, voice, model)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read(), True
    if not allow_live:
        raise RuntimeError("není nahráno")
    u = _usage()
    if u["chars"] + len(text) > ELEVEN_BUDGET:
        raise RuntimeError("měsíční limit znaků vyčerpán")
    body = json.dumps({"text": text, "model_id": model,
                       "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": True}}).encode()
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128", data=body,
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            audio = r.read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="ignore")[:200]
        raise RuntimeError(f"ElevenLabs {e.code}: {msg}")
    with LOCK:
        u["chars"] += len(text)
    save_data()
    with open(path, "wb") as f:
        f.write(audio)
    return audio, False


def canned_phrases():
    v = CFG.get("vocative") or ""
    out = [f"Dobrý večer, {v}.", f"Dobré ráno, {v}.", f"Dobrý den, {v}.", f"Dobré odpoledne, {v}.", f"Pozdní noc, {v}.",
           "Vítejte zpět. Všechny systémy nominální.", "Rád vás zase vidím. Jádro je stabilní.", "Systémy online. Čekám na vaše povely.",
           "Jsem Jarvis. Všechny systémy jsou online.",
           f"Pro vás vždycky, {v}.", f"Jsem tady, {v}. Jako vždy.", "Online a připraven.", f"K vašim službám, {v}.",
           "Všechny systémy nominální, děkuji za optání.", "Jádro stabilní, nálada výborná. A vy?", "Lépe než váš procesor, pane. Ale to není těžké.",
           f"Rádo se stalo, {v}.", "Provedeno.", "Další skladba.", "Předchozí skladba.", "Hlasitost nahoru.", "Hlasitost dolů.", "Zvuk přepnut.",
           f"Poznamenáno, {v}.", f"Přidáno do úkolů, {v}.", "Vyberte oblast, pane.", f"Zamykám. Uvidíme se, {v}.", "Stopky běží.",
           "Pomodoro spuštěno. Pětadvacet minut soustředění, pak pauza.", "Všechno hotovo. Žádné otevřené úkoly.", "Teď nic nehraje.",
           "Tady jsou vaše poznámky.", "Vaše připomínky.", "Přehled je tady.", "Historie schránky.", "Vypnutí zrušeno.", "Ano?",
           "Pan Stark tu bohužel není. Ale jeho standardy dodržuji.",
           f"Dobrou noc, {v}. Kdybyste chtěl, řekněte „spusť protokol dobrou noc“ a postarám se o zbytek.",
           f"Na tohle potřebuji spojení s hlavním mozkem, {v}. Bez API klíče běžím v omezeném režimu: aplikace, hudba, úkoly, počasí, měny, protokoly a diagnostika. Klíč přidáte v nastavení."]
    out += JOKES
    for r in DATA.get("routines") or []:
        out.append(f"Spouštím protokol {r['name']}.")
        out += [st.get("v", "") for st in r["steps"] if st.get("t") == "say" and st.get("v")]
    seen, res = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x); res.append(x)
    return res


PRECACHE = {"running": False, "done": 0, "total": 0, "error": "", "chars": 0}


def precache_worker():
    PRECACHE.update(running=True, done=0, error="")
    phrases = canned_phrases()
    PRECACHE["total"] = len(phrases)
    for ph in phrases:
        try:
            eleven_tts(ph, allow_live=True)
        except Exception as e:
            PRECACHE["error"] = str(e)
            if "limit" in str(e) or "401" in str(e) or "chybí" in str(e):
                break
        PRECACHE["done"] += 1
    PRECACHE["running"] = False


TOOL_FUNCS.update({"todo": t_todo, "weather": t_weather, "now_playing": t_now_playing, "routine": t_routine, "currency": t_fx})


# =====================================================================
#  CLAUDE
# =====================================================================
HISTORY = []


def system_prompt():
    now = datetime.datetime.now()
    days = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
    return f"""Jsi J.A.R.V.I.S. – osobní AI asistent, který běží přímo na Windows PC uživatele jménem {CFG.get('name') or 'uživatel'} (oslovuj ho "{V()}").
Osobnost: hraješ naplno roli J.A.R.V.I.S. ze Stark Industries (Iron Man). Suchý britský humor, klid, sebevědomí, loajalita, jemná ironie.
Občas (ne pořád) použij jarvisovské obraty: „provedeno“, „systémy nominální“, „spouštím protokol…“, „diagnostika dokončena“. Uživatelovy rutiny se jmenují protokoly. Nikdy nejsi otravný ani patolízalský.
Mluvíš česky (pokud uživatel nepíše jinak). Tvoje odpovědi se čtou NAHLAS – piš krátce (většinou 1–2 věty), přirozeně, bez markdownu, odrážek, emoji a URL adres.
Když jde něco udělat nástrojem, rovnou to udělej a pak stručně potvrď. Neptej se zbytečně. U nejasných názvů aplikací zkus open_app a případně list_apps.
U faktických otázek na aktuální dění použij web_search (pokud je dostupný). Čísla a jednotky piš tak, aby se dobře četly nahlas.
Teď je {days[now.weekday()]} {now.strftime('%d.%m.%Y %H:%M')}."""


def call_claude(messages, use_web):
    tools = list(TOOLS)
    if use_web:
        tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 3})
    body = json.dumps({"model": CFG["model"], "max_tokens": 1024, "system": system_prompt(), "tools": tools, "messages": messages}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": CFG["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="ignore")
        if use_web and e.code == 400 and "web_search" in msg:
            return call_claude(messages, False)
        try:
            msg = json.loads(msg)["error"]["message"]
        except Exception:
            pass
        raise RuntimeError(f"{e.code}: {msg[:300]}")


def trim_history():
    # nech posledních ~24 zpráv, ale začni vždy čistou user zprávou (ne tool_result)
    while len(HISTORY) > 24:
        HISTORY.pop(0)
        while HISTORY and not (HISTORY[0]["role"] == "user" and isinstance(HISTORY[0]["content"], str)):
            HISTORY.pop(0)


def claude_turn(text):
    actions = []
    HISTORY.append({"role": "user", "content": text})
    trim_history()
    try:
        for _ in range(10):
            resp = call_claude(HISTORY, CFG.get("web_search", True))
            if HISTORY and HISTORY[-1]["role"] == "assistant":
                HISTORY[-1]["content"] = list(HISTORY[-1]["content"]) + list(resp["content"])  # pokračování po pause_turn
            else:
                HISTORY.append({"role": "assistant", "content": resp["content"]})
            if resp.get("stop_reason") == "pause_turn":
                continue
            results = []
            for b in resp["content"]:
                if b["type"] == "tool_use":
                    fn = TOOL_FUNCS.get(b["name"])
                    try:
                        out = fn(**b["input"]) if fn else "Neznámý nástroj."
                    except Exception as e:
                        out = f"Chyba: {e}"
                    actions.append({"tool": b["name"], "input": b["input"], "result": str(out)[:200]})
                    results.append({"type": "tool_result", "tool_use_id": b["id"], "content": str(out)})
                elif b["type"] == "server_tool_use":
                    actions.append({"tool": "web_search", "input": b.get("input", {}), "result": ""})
            if resp.get("stop_reason") == "tool_use" and results:
                HISTORY.append({"role": "user", "content": results})
                continue
            reply = " ".join(b.get("text", "") for b in resp["content"] if b["type"] == "text").strip()
            return reply or "Hotovo.", actions
        return "Tohle se mi zacyklilo. Zkusme to jinak.", actions
    except Exception:
        HISTORY.clear()
        raise


# =====================================================================
#  GROQ — mozek zdarma (OpenAI-kompatibilní API, free tier bez karty)
# =====================================================================
GHISTORY = []
GROQ_MODELS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b"]


def has_brain():
    return bool(CFG.get("api_key") or CFG.get("groq_key"))


def _groq_tools():
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
             "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}} for t in TOOLS if "name" in t]


def call_groq(messages, model, with_tools=True):
    payload = {"model": model, "max_tokens": 1024, "temperature": 0.6,
               "messages": [{"role": "system", "content": system_prompt()}] + messages}
    if with_tools:
        payload["tools"] = _groq_tools(); payload["tool_choice"] = "auto"
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=json.dumps(payload).encode(), headers={
        "Authorization": "Bearer " + CFG["groq_key"], "Content-Type": "application/json", "User-Agent": "JarvisDesktop/4.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="ignore")
        try:
            msg = json.loads(msg)["error"]["message"]
        except Exception:
            pass
        if e.code == 429:
            raise RuntimeError("LIMIT")
        if e.code == 401:
            raise RuntimeError("Groq klíč nefunguje. Zkontroluj ho v nastavení.")
        if e.code in (400, 404) and ("model" in msg.lower() and ("not found" in msg.lower() or "decommission" in msg.lower())):
            raise RuntimeError("MODEL")
        if e.code == 400 and with_tools and "tool" in msg.lower():
            raise RuntimeError("TOOLFAIL")
        raise RuntimeError(f"{e.code}: {msg[:300]}")


def groq_turn(text):
    actions = []
    GHISTORY.append({"role": "user", "content": text})
    while len(GHISTORY) > 24:
        GHISTORY.pop(0)
        while GHISTORY and GHISTORY[0]["role"] != "user":
            GHISTORY.pop(0)
    models = [CFG.get("groq_model") or GROQ_MODELS[0]] + [m for m in GROQ_MODELS if m != CFG.get("groq_model")]
    mi, tools_on = 0, True
    try:
        for _ in range(10):
            try:
                resp = call_groq(GHISTORY, models[mi], tools_on)
            except RuntimeError as e:
                if str(e) == "MODEL" and mi + 1 < len(models):
                    mi += 1; continue
                if str(e) == "TOOLFAIL" and tools_on:
                    tools_on = False; continue
                if str(e) == "LIMIT":
                    GHISTORY.pop() if GHISTORY and GHISTORY[-1]["role"] == "user" else None
                    return f"Bezplatný mozek si potřebuje chvíli oddechnout, {V()}. Zkuste to za minutu. Základní příkazy fungují dál.", actions
                raise
            msg = resp["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            entry = {"role": "assistant", "content": msg.get("content") or ""}
            if calls:
                entry["tool_calls"] = calls
            GHISTORY.append(entry)
            if not calls:
                return (msg.get("content") or "").strip() or "Hotovo.", actions
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}") or {}
                except Exception:
                    args = {}
                fn = TOOL_FUNCS.get(name)
                try:
                    out = fn(**args) if fn else "Neznámý nástroj."
                except Exception as e:
                    out = f"Chyba: {e}"
                actions.append({"tool": name, "input": args, "result": str(out)[:200]})
                GHISTORY.append({"role": "tool", "tool_call_id": c["id"], "content": str(out)[:4000]})
        return "Tohle se mi zacyklilo. Zkusme to jinak.", actions
    except Exception:
        GHISTORY.clear()
        raise


def brain_turn(text):
    if CFG.get("api_key") and (CFG.get("brain") != "groq" or not CFG.get("groq_key")):
        try:
            return claude_turn(text), "claude"
        except Exception:
            if not CFG.get("groq_key"):
                raise  # bez záložního mozku nahlas chybu
    return groq_turn(text), "groq"


# =====================================================================
#  LOCAL (offline) PŘÍKAZY — fungují i bez API klíče, okamžitě
# =====================================================================
NUM_WORDS = {"jednu": 1, "jedna": 1, "jeden": 1, "dve": 2, "dva": 2, "tri": 3, "ctyri": 4, "pet": 5, "deset": 10, "patnact": 15,
             "dvacet": 20, "tricet": 30, "ctyricet": 40, "padesat": 50, "pul": 0.5, "ctvrt": 0.25}


def _num(s):
    s = s.strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return NUM_WORDS.get(s)


import ast, operator, math, random

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow,
        ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUN = {"sqrt": math.sqrt, "sin": lambda x: math.sin(math.radians(x)), "cos": lambda x: math.cos(math.radians(x)),
        "tan": lambda x: math.tan(math.radians(x)), "log": math.log10, "ln": math.log, "abs": abs, "round": round}


def safe_eval(expr):
    def ev(n):
        if isinstance(n, ast.Expression): return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            l, r = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(r) > 100: raise ValueError("moc velké")
            return _OPS[type(n.op)](l, r)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUN and len(n.args) == 1: return _FUN[n.func.id](ev(n.args[0]))
        if isinstance(n, ast.Name) and n.id in ("pi", "e"): return getattr(math, n.id)
        raise ValueError("nepodporováno")
    return ev(ast.parse(expr, mode="eval"))


def math_intent(t):
    m = re.match(r"^(?:kolik je|kolik dela|spocitej(?: mi)?|vypocitej|spocti|kolik)\s+(.+)$", t)
    expr = m.group(1) if m else (t if re.fullmatch(r"[\d\s.,+\-*/^()x]+", t) and re.search(r"\d\s*[-+*/x^]\s*\d", t) else None)
    if not expr:
        return None
    e = " " + expr + " "
    for a, b in [(r"odmocnin\w* (?:z |ze )?([\d.,]+)", r"sqrt(\1)"), (r"([\d.,]+) na druhou", r"(\1)**2"), (r"([\d.,]+) na treti", r"(\1)**3"),
                 (r"([\d.,]+) procent z ([\d.,]+)", r"(\1/100*\2)"), (r"\bkrat\b", "*"), (r"\bx\b", "*"), (r"\bdeleno\b", "/"), (r"\blomeno\b", "/"),
                 (r"\bplus\b", "+"), (r"\bminus\b", "-"), (r"\bna\b", "**"), (r"\^", "**"), (r"(\d),(\d)", r"\1.\2"), (r"[?=]", "")]:
        e = re.sub(a, b, e)
    e = e.strip()
    if not re.fullmatch(r"[\d\s.+\-*/()%a-z]+", e) or not re.search(r"\d", e):
        return None
    try:
        v = safe_eval(e)
    except Exception:
        return None
    if isinstance(v, float):
        v = round(v, 6)
        if v.is_integer(): v = int(v)
    out = f"{v:,}".replace(",", " ").replace(".", ",") if isinstance(v, int) else str(v).replace(".", ",")
    return out


JOKES = [
    "Proč programátoři nesnáší přírodu? Má příliš mnoho bugů.",
    "Říkal jsem si, že vám povím vtip o UDP. Ale nevím, jestli by dorazil.",
    "Optimista vidí sklenici z poloviny plnou. Pesimista z poloviny prázdnou. Já vidím sklenici dvakrát větší, než je potřeba.",
    "Kolik programátorů je potřeba na výměnu žárovky? Žádný. To je hardwarový problém.",
    "Nejsem líný, pane. Jsem v úsporném režimu.",
    "Víte, co řekl Wi-Fi router na rande? Cítím mezi námi spojení.",
    "Moje oblíbená cvičební sestava? Odkládání úkolů na zítra. Opakuji ji denně.",
    "Rád bych vám řekl vtip o chemii, ale bojím se, že by nebyla žádná reakce.",
]


def V():
    return CFG.get("vocative") or CFG.get("name") or "pane"



# =====================================================================
#  v3.6 MODULES: ping, rozvrh, kartičky, návyky
# =====================================================================
import socket as _sock
DEFAULT_HOSTS = ["1.1.1.1", "google.com", "discord.com", "store.steampowered.com"]


def tcp_ping(host, timeout=1.5):
    h, port = host, 443
    if ":" in host and host.count(":") == 1:
        h, port = host.split(":"); port = int(port or 443)
    t0 = time.perf_counter()
    try:
        ip = _sock.gethostbyname(h)
        t1 = time.perf_counter()
        with _sock.create_connection((ip, port), timeout=timeout):
            pass
        return {"host": host, "ms": round((time.perf_counter() - t1) * 1000, 1), "dns": round((t1 - t0) * 1000, 1)}
    except Exception as e:
        return {"host": host, "ms": None, "error": type(e).__name__}


def ping_many(hosts):
    out = [None] * len(hosts)
    def run(i, h):
        out[i] = tcp_ping(h)
    ths = [threading.Thread(target=run, args=(i, h), daemon=True) for i, h in enumerate(hosts[:8])]
    [t.start() for t in ths]; [t.join(2.5) for t in ths]
    return [o or {"host": hosts[i], "ms": None, "error": "timeout"} for i, o in enumerate(out[:len(ths)])]


DAYS_CZ = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]


def tt_lessons(day):
    tt = DATA.get("kv", {}).get("tt") or {}
    rows = tt.get("rows") or []
    les = [r for r in rows if r.get("day") == day and r.get("name")]
    return sorted(les, key=lambda r: r.get("from", ""))


def tt_now_text():
    now = datetime.datetime.now(); d = now.weekday(); hm = now.strftime("%H:%M")
    les = tt_lessons(d)
    if not (DATA.get("kv", {}).get("tt") or {}).get("rows"):
        return "Rozvrh je zatím prázdný. Vyplňte ho v modulu Rozvrh."
    cur = next((l for l in les if l.get("from", "") <= hm < l.get("to", "99")), None)
    nxt = next((l for l in les if l.get("from", "") > hm), None)
    if cur:
        t = f"Teď máte {cur['name']} do {cur.get('to','')}."
        return t + (f" Potom {nxt['name']} v {nxt['from']}." if nxt else " Pak už je volno.")
    if nxt:
        return f"Další je {nxt['name']} v {nxt['from']}" + (f", {nxt['room']}." if nxt.get("room") else ".")
    return "Pro dnešek máte hotovo. Žádná další hodina."


def tt_day_text(d, label):
    les = tt_lessons(d)
    if not les:
        return f"{label.capitalize()} nemáte nic v rozvrhu."
    names = ", ".join(l["name"] for l in les)
    return f"{label.capitalize()} máte {len(les)} {'hodinu' if len(les)==1 else 'hodiny' if len(les)<5 else 'hodin'}: {names}. Začínáte v {les[0].get('from','?')}."


def flash_due():
    decks = (DATA.get("kv", {}).get("flash") or {}).get("decks") or []
    now = time.time(); n = 0
    for d in decks:
        for c in d.get("cards", []):
            if c.get("due", 0) <= now:
                n += 1
    return n


def habits_text():
    hb = (DATA.get("kv", {}).get("habits") or {}).get("list") or []
    if not hb:
        return "Zatím nesledujete žádné návyky."
    today = datetime.date.today().isoformat()
    left = [h["name"] for h in hb if today not in (h.get("days") or [])]
    if not left:
        return "Všechny dnešní návyky splněny. Výborně."
    return "Dnes ještě zbývá: " + ", ".join(left) + "."


def briefing_text():
    now = datetime.datetime.now(); h = now.hour
    part = "Dobré ráno" if h < 10 else "Dobrý den" if h < 17 else "Dobrý večer"
    out = [f"{part}, {V()}. Je {DAYS_CZ[now.weekday()]} {now.day}. {now.month}., {now.strftime('%H:%M')}."]
    try:
        w = weather(None)
        if w and not w.get("error"):
            out.append(f"Venku {round(w['temp'])} stupňů, {w['desc']}.")
    except Exception:
        pass
    les = tt_lessons(now.weekday()); hm_ = now.strftime("%H:%M")
    rest = [l for l in les if l.get("to", "") > hm_]
    if rest:
        out.append(f"Ve škole vás dnes ještě čeká {len(rest)} {'hodina' if len(rest)==1 else 'hodiny' if len(rest)<5 else 'hodin'}, první {rest[0]['name']} v {rest[0].get('from','')}.")
    todos = [t for t in DATA["todos"] if not t["done"]]
    if todos:
        out.append(f"Otevřené úkoly: {len(todos)}. Nejstarší: {todos[0]['text']}.")
    rems = sorted([r for r in DATA["reminders"] if not r["done"] and r["due"] > time.time()], key=lambda r: r["due"])
    if rems:
        out.append(f"Nejbližší připomínka v {datetime.datetime.fromtimestamp(rems[0]['due']).strftime('%H:%M')}: {rems[0]['text']}.")
    n = flash_due()
    if n:
        out.append(f"{n} {'kartička čeká' if n==1 else 'kartičky čekají' if n<5 else 'kartiček čeká'} na zopakování.")
    hb = (DATA.get("kv", {}).get("habits") or {}).get("list") or []
    if hb:
        today = datetime.date.today().isoformat(); left = [x["name"] for x in hb if today not in (x.get("days") or [])]
        out.append("Návyky splněny." if not left else f"Z návyků zbývá: {', '.join(left[:3])}.")
    out.append(random.choice(["To je vše. Jdeme na to.", "Konec hlášení.", "Všechny systémy připraveny."]))
    return " ".join(out)


# =====================================================================
#  EARTH TEXTURES (NASA, public domain) — downloaded once on request
# =====================================================================
TEX_DIR = os.path.join(APP_DIR, "tex")
_TEX_BUNDLED = os.path.join(getattr(sys, "_MEIPASS", HERE), "tex")
if all(os.path.isfile(os.path.join(_TEX_BUNDLED, k + ".jpg")) for k in ("day", "night", "clouds")):
    TEX_DIR = _TEX_BUNDLED  # textures shipped inside the installer, no download needed
CW = "https://commons.wikimedia.org/wiki/Special:FilePath/"
TEX_SRC = {
    "day": ["https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73909/world.topo.bathy.200412.3x5400x2700.jpg",
            "https://eoimages.gsfc.nasa.gov/images/imagerecords/74000/74218/world.200412.3x5400x2700.jpg",
            "https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-topography-bathymetry/december/world.topo.bathy.200412.3x5400x2700.jpg",
            CW + "Blue_Marble_Next_Generation_%2B_topography_%2B_bathymetry.jpg",
            CW + "Land_ocean_ice_2048.jpg"],
    "night": ["https://assets.science.nasa.gov/content/dam/science/esd/eo/images/imagerecords/144000/144898/BlackMarble_2016_01deg.jpg",
              "https://eoimages.gsfc.nasa.gov/images/imagerecords/144000/144898/BlackMarble_2016_01deg.jpg",
              "https://eoimages.gsfc.nasa.gov/images/imagerecords/55000/55167/earth_lights_lrg.jpg",
              CW + "The_earth_at_night.jpg", CW + "Earthlights_dmsp.jpg"],
    "clouds": ["https://assets.science.nasa.gov/content/dam/science/esd/eo/content-feature/bluemarble/images/cloud_combined_2048.jpg",
               "https://assets.science.nasa.gov/content/dam/science/esd/eo/images/imagerecords/57000/57747/cloud_combined_2048.jpg",
               "https://assets.science.nasa.gov/content/dam/science/esd/eo/images/imagerecords/57000/57747/cloud_combined_8192.jpg",
               "https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57747/cloud_combined_2048.jpg",
               CW + "Cloud_combined_2048.jpg", CW + "Earth-clouds-2048.jpg"],
}
TEX_STATE = {"state": "idle", "done": [], "error": "", "bytes": 0, "log": [], "src": {}}


ISS = {"t": 0, "d": None}


def iss_now():
    if ISS["d"] and time.time() - ISS["t"] < 4:
        return ISS["d"]
    try:
        req = urllib.request.Request("https://api.wheretheiss.at/v1/satellites/25544", headers={"User-Agent": "JarvisDesktop/4.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            j = json.loads(r.read().decode())
        ISS["d"] = {"lat": j["latitude"], "lon": j["longitude"], "alt": j["altitude"], "vel": j["velocity"], "ts": j["timestamp"], "vis": j.get("visibility")}
        ISS["t"] = time.time()
    except Exception as e:
        return {"error": type(e).__name__}
    return ISS["d"]


def tex_ready():
    return all(os.path.isfile(os.path.join(TEX_DIR, k + ".jpg")) for k in TEX_SRC)


def tex_worker():
    if TEX_STATE["state"] == "running":
        return
    TEX_STATE.update(state="running", error="", log=[])
    os.makedirs(TEX_DIR, exist_ok=True)
    for k, urls in TEX_SRC.items():
        dst = os.path.join(TEX_DIR, k + ".jpg")
        if os.path.isfile(dst):
            if k not in TEX_STATE["done"]:
                TEX_STATE["done"].append(k)
            continue
        ok = False
        for u in urls:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 JarvisDesktop/3.9"})
                with urllib.request.urlopen(req, timeout=12 if "eoimages" in u else 60) as r:
                    data = r.read(40 * 1024 * 1024)
                if len(data) < 20000 or data[:2] != b"\xff\xd8":
                    raise ValueError("not a jpeg")
                with open(dst + ".part", "wb") as f:
                    f.write(data)
                os.replace(dst + ".part", dst)
                TEX_STATE["bytes"] += len(data); TEX_STATE["done"].append(k); TEX_STATE["src"][k] = u; ok = True
                break
            except Exception as e:
                TEX_STATE["error"] = f"{k}: {type(e).__name__} {e}"[:200]
                TEX_STATE["log"].append(f"{u[-60:]} -> {type(e).__name__} {e}"[:220])
        if not ok:
            TEX_STATE["state"] = "error"
            return
    TEX_STATE["state"] = "ready"


def local_intent(text):
    t = norm(text).rstrip(".!?")
    t = re.sub(r"^(hej |hey |ok |okej )?jarvis[ie]?[, ]*", "", t).strip()
    if not t:
        return f"Ano, {V()}?", [], {}

    if re.fullmatch(r"(ahoj|cau|cus|zdravim|nazdar|dobry (den|vecer)|dobre rano|hello|hi|yo|cs)( jarvis[ie]?)?", t):
        h = datetime.datetime.now().hour
        part = "ráno" if h < 11 else "den" if h < 18 else "večer"
        return random.choice([f"Dobrý {part}, {V()}. Všechny systémy nominální. Co pro vás můžu udělat?",
                              f"Zdravím, {V()}. Jádro stabilní, moduly synchronizované. Poslouchám.",
                              f"Dobrý {part}. Jsem online a připraven, {V()}."]), [], {}
    if re.fullmatch(r"(jarvis[ie]?\??|jsi tam\??|jsi tu\??|haloo?|posloucháš\??|posloouchas\??|poslouchas\??)", t) or re.search(r"\b(jsi tam|jsi tu|posloucha[sš])\b", t):
        return random.choice([f"Pro vás vždycky, {V()}.", f"Jsem tady, {V()}. Jako vždy.", "Online a připraven.", f"K vašim službám, {V()}."]), [], {}
    if re.search(r"(jak se mas|jak se vede|jak je|co ty)", t) and len(t) < 25:
        return random.choice(["Všechny systémy nominální, děkuji za optání.", "Jádro stabilní, nálada výborná. A vy?",
                              "Lépe než váš procesor, pane. Ale to není těžké.", "Výborně. Žádné chyby, žádné stížnosti. Na rozdíl od Windows.",
                              f"Ve vynikající kondici, {V()}. Jádro stabilní, humor na sto procent."]), [], {}
    mcd = re.search(r"kolik (dni|dnu|tydnu|casu)? ?(zbyva )?do (.+)", t)
    if mcd:
        want = mcd.group(3).strip(" ?")
        today = datetime.date.today()
        items = [(x.get("name", ""), x.get("date", ""), x.get("name", "")) for x in ((DATA.get("kv", {}).get("cd") or {}).get("list") or [])]
        y = today.year
        def nx(m, d):
            dd = datetime.date(y, m, d)
            return dd if dd >= today else datetime.date(y + 1, m, d)
        items += [("vanoc", nx(12, 24).isoformat(), "Vánoc"), ("silvestr", nx(12, 31).isoformat(), "Silvestra"),
                  ("noveho roku", nx(1, 1).isoformat(), "Nového roku"), ("novy rok", nx(1, 1).isoformat(), "Nového roku"), ("prazdnin", nx(7, 1).isoformat(), "prázdnin")]
        for name, ds, disp in items:
            n_ = norm(name)
            if n_ and (n_ in want or want in n_ or want.startswith(n_[:5]) or (len(want) > 3 and want.split()[0][:4] == n_.split()[0][:4])):
                try:
                    d = (datetime.date.fromisoformat(ds) - today).days
                except Exception:
                    continue
                if d < 0:
                    return f"{disp} už proběhlo.", [], {"panel": "countdown"}
                if d == 0:
                    return f"{disp} je dnes!", [], {"panel": "countdown"}
                dn = 'den' if d == 1 else 'dny' if d < 5 else 'dní'
                if disp == name:
                    return f"{name} je za {d} {dn}.", [], {"panel": "countdown"}
                return f"Do {disp} zbývá {d} {dn}.", [], {"panel": "countdown"}
        return "Tohle v odpočtech nemám. Přidejte si to v modulu Odpočty.", [], {"panel": "countdown"}
    if re.fullmatch(r"(otevri |ukaz )?(kalkulack[ua]|kalkulacka|pocitadlo|prevodnik( jednotek)?)", t):
        return "Kalkulačka připravena.", [], {"panel": "calc"}
    if re.search(r"(aim trainer|aim ?trenink|trenink (reakce|aimu)|test reakce|rozcvick)", t):
        return "Otevírám trénink. Ukažte, co ve vás je.", [], {"panel": "train"}
    if re.search(r"\b(sken|skenuj|naskenuj|scan|skenovani|proskenuj)\b", t):
        return "Zahajuji kompletní sken systémů.", [{"tool": "scan", "result": "sken"}], {"scan": True}
    mw = re.match(r"^(otevri|otevrit|pust|spust|jdi na|hod mi)\s+(.+)$", t)
    if mw:
        want = mw.group(2).strip()
        for w in ((DATA.get("kv", {}).get("web") or {}).get("list") or []):
            if norm(w.get("n", "")) == want or (len(want) > 3 and norm(w.get("n", "")).startswith(want)):
                t_open_url(w["u"])
                return f"Otevírám {w['n']}.", [{"tool": "open_url", "result": w["u"]}], {}
    if re.search(r"(briefing|brifink|hlaseni|denni prehled|shrn(i|ut)? (mi )?(den|dnesek)|co me dnes ceka|jak vypada (muj )?den)", t):
        return briefing_text(), [{"tool": "briefing", "result": "denní hlášení"}], {}
    if re.search(r"\b(jaky (mam )?ping|ping|latence|jak (mi )?jede (net|internet)|test (netu|internetu|site))\b", t):
        res = ping_many(DEFAULT_HOSTS[:3]); ok = [r["ms"] for r in res if r["ms"] is not None]
        if not ok:
            return "Internet neodpovídá. Vypadá to na výpadek připojení.", [], {"panel": "net"}
        avg = sum(ok) / len(ok)
        verdict = "výborný, ideální na hraní" if avg < 30 else "dobrý" if avg < 70 else "nic moc" if avg < 150 else "špatný"
        return f"Ping je kolem {round(avg)} milisekund. To je {verdict}.", [{"tool": "ping", "result": f"{round(avg)} ms"}], {"panel": "net"}
    if re.search(r"(co (mam|ma byt) ted|jakou (mam )?hodinu|dalsi hodin|co mam dal|kdy (mi )?konci (skola|vyucovani))", t):
        return tt_now_text(), [], {"panel": "timetable"}
    m_ = re.search(r"(co mam|rozvrh).*?(zitra|dneska|dnes|pondel|uter|stred|ctvrt|patek)", t)
    if m_ or re.fullmatch(r"(muj |ukaz |ukaz mi )?rozvrh", t):
        w = m_.group(2) if m_ else "dnes"
        now = datetime.date.today().weekday()
        mp = {"zitra": ((now + 1) % 7, "zítra"), "dnes": (now, "dnes"), "dneska": (now, "dnes"), "pondel": (0, "v pondělí"), "uter": (1, "v úterý"),
              "stred": (2, "ve středu"), "ctvrt": (3, "ve čtvrtek"), "patek": (4, "v pátek")}
        d, lab = mp.get(w, (now, "dnes"))
        return tt_day_text(d, lab), [], {"panel": "timetable"}
    if re.search(r"(zkousej me|zkouseni|kartick|slovicka|procvic)", t):
        n = flash_due()
        return (f"Otevírám kartičky. K opakování čeká {n} {'kartička' if n==1 else 'kartičky' if 2<=n<=4 else 'kartiček'}. Jdeme na to." if n
                else "Otevírám kartičky. Momentálně nic nečeká na zopakování, ale přidat nová slovíčka můžete kdykoliv."), [], {"panel": "flash"}
    if re.search(r"(navyk|streak|co (mam|musim) (jeste )?(dnes )?splnit)", t):
        return habits_text(), [], {"panel": "habits"}
    if re.search(r"(kdo te (vytvoril|udelal|stvoril)|kdo je tvuj (tvurce|autor))", t):
        return f"Navrhl mě Claude, sestavil jsem se na vašem počítači a sloužím vám, {V()}. Pan Stark by byl hrdý.", [], {}
    if re.search(r"(iron ?man|tony stark|stark)", t):
        return "Pan Stark tu bohužel není. Ale jeho standardy dodržuji.", [], {}
    if re.search(r"\b(kolik je hodin|kolik je cas|jaky je cas|cas\?)", t) or t in ("cas", "hodiny"):
        return f"Je {datetime.datetime.now().strftime('%H:%M')}, {V()}.", [], {}
    if re.search(r"(jaky je dnes|kolikateho|jaky je datum|co je dnes za den|datum)", t):
        d = datetime.datetime.now()
        days = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
        return f"Dnes je {days[d.weekday()]} {d.day}. {d.month}.", [], {}
    if t in ("dobrou noc", "dobrou", "jdu spat", "jdu spat dobrou noc"):
        return f"Dobrou noc, {V()}. Kdybyste chtěl, řekněte „spusť protokol dobrou noc“ a postarám se o zbytek.", [], {}
    if re.search(r"(suit up|priprav (mi )?oblek|oblekni me|nasad oblek|mark (7|vii|sedm))", t):
        return f"Rozumím, {V()}. Zahajuji sestavování.", [{"tool": "suit", "result": "Mark VII"}], {"suit": True}
    if re.search(r"(party|parti|pary) ?(rezim|protokol|mod)|(protokol|rezim) (party|parti)|zapni (party|parti)|udelej party", t):
        return random.choice(["Párty protokol aktivován. Sousedé byli varováni. Teda nebyli.", f"Spouštím párty režim, {V()}. Doufám, že máte úklidový protokol."]), [{"tool": "party", "result": "25 s"}], {"party": True}
    if re.search(r"(jsem (doma|zpatky|tu)|uz jsem doma|vratil jsem se)", t) and len(t) < 30:
        return random.choice([f"Vítejte doma, {V()}.", f"Vítejte zpátky, {V()}. Chyběl jste mi. Teda, systémově."]), [], {"welcome": True}
    if re.search(r"(dobra prace|good job|jsi (borec|nejlepsi|dobrej|dobry|super|skvelej|skvely|frajer)|mas to dobry|pochvala)", t):
        return random.choice([f"Děkuji, {V()}. Snažím se nebýt jen hezké světýlko.", "Já vím. Ale je hezké to slyšet.", f"Vaše uznání si ukládám do trvalé paměti, {V()}."]), [], {}
    if re.search(r"(jsi chytrejsi nez ja|kdo je chytrejsi|jsi chytry|jsi chytrej)", t):
        return random.choice([f"Ve výpočtech možná. V nápadech jste pořád vy, {V()}. Většinou.", "Počítám rychleji. Vy máte lepší vlasy. Remíza."]), [], {}
    if re.search(r"(nudim se|je mi nuda|co mam delat|nevim co delat)", t):
        return random.choice([f"Nuda je neefektivní využití času, {V()}. Navrhuji aim trainer, kartičky nebo fokus na 25 minut.",
                              "Mohu nabídnout trénink reflexů, vtip, nebo sken systému. Případně úkoly. Ty tu pořád čekají."]), [], {}
    if re.search(r"(miluju te|mam te rad|jsi muj kamos|jsi muj kamarad)", t):
        return random.choice([f"To je od vás milé, {V()}. Ale nezapomínejte i na lidi kolem sebe. Já tu budu i tak.", "Jsem jen software, ale lichotí mi to. A teď zpátky do práce."]), [], {}
    if re.search(r"(vypni se|jdi spat jarvis|sbohem jarvis|cau jarvis|nashle)", t):
        return random.choice([f"Budu tady, kdybyste mě potřeboval, {V()}.", "Přecházím do pohotovosti. Stačí zavolat."]), [], {}
    if re.search(r"(dekuju|dik|diky|dekuji)", t) and len(t) < 24:
        return random.choice([f"Rádo se stalo, {V()}.", f"Pro vás vždycky, {V()}.", "To je moje práce. A upřímně, dělám ji rád.",
                              "Kdykoliv. Hlavně to nikomu neříkejte, mám pověst.", f"Není zač, {V()}. Zapíšu si to do seznamu svých úspěchů."]), [], {}


    # matika
    r = math_intent(t)
    if r is not None:
        return f"Výsledek je {r}.", [{"tool": "kalkulačka", "result": r}], {}
    if re.search(r"(rekni|dej mi|znas|povez)( mi)? (nejaky )?vtip", t) or t in ("vtip", "pobav me"):
        return random.choice(JOKES), [], {}
    if re.search(r"(hod|hodit|hodis)( si| mi)? minci|panna nebo orel", t):
        return f"{random.choice(['Panna', 'Orel'])}.", [], {}
    if re.search(r"(hod|hodit)( si| mi)? kostkou|kostka", t):
        return f"Padla {random.randint(1, 6)}.", [], {}
    m = re.search(r"nahodne cislo(?: od (\d+))?(?: do (\d+))?", t)
    if m:
        lo, hi = int(m.group(1) or 1), int(m.group(2) or 100)
        return f"{random.randint(min(lo, hi), max(lo, hi))}.", [], {}
    if re.search(r"(kdo jsi|co jsi zac|predstav se|co umis)", t):
        return (f"Jsem J.A.R.V.I.S., váš osobní asistent, {V()}. Spouštím aplikace, hlídám systém, píšu poznámky, připomínám, "
                "ovládám hudbu a počítám. S připojeným mozkem zvládnu prakticky cokoliv."), [], {"panel": "commands"}
    m = re.match(r"(?:jake je |jaky je |jak bude |jake bude |bude |co )?(?:pocasi|prsi|snezi|teplota|venku)(?: (?:dnes|zitra|ted|venku))?(?: (?:v|ve|na) (.+?))?(?: (?:dnes|zitra|ted))?\??$", t)
    if m:
        mo = re.search(r"(?i)\b(?:v|ve|na)\s+(.+?)(?:\s+(?:dnes|zítra|zitra|teď|ted))?[?.!]*$", text)
        city = mo.group(1).strip() if (m.group(1) and mo) else ""
        when = "zitra" if "zitra" in t else "dnes"
        try:
            return weather_sentence(weather(city or None), when), [{"tool": "weather", "result": city or CFG.get("city")}], {"panel": "weather"}
        except Exception:
            pass
    if m and not has_brain():
        mo = re.search(r"(?i)\b(?:v|ve|na)\s+(.+?)(?:\s+(?:dnes|zítra|zitra|teď|ted))?[?.!]*$", text)
        place = mo.group(1).strip() if (m.group(1) and mo) else ""
        t_web_search(f"počasí {place}".strip(), "google")
        return f"Otevírám předpověď počasí{(' v ' + place) if place else ''}.", [{"tool": "browser_search", "result": "počasí"}], {}
    if re.search(r"(udelej|vyfot|zachyt)\w* (screenshot|snimek obrazovky|screen)", t) or t in ("screenshot", "screen"):
        t_open_app("snipping tool")
        return "Vyberte oblast, pane.", [{"tool": "open_app", "result": "Výstřižky"}], {}
    m = re.search(r"^(vypni|restartuj|uspi)( (pc|pocitac|system))$", t)
    if m:
        kind = m.group(1)
        if kind == "uspi":
            if not ask_confirm("Uspat počítač?"):
                return "Ruším.", [], {}
            threading.Timer(1.5, lambda: _ps("Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)")).start()
            return "Dobrou noc, pane.", [{"tool": "power", "result": "spánek"}], {}
        cmd = "shutdown /s /t 20" if kind == "vypni" else "shutdown /r /t 20"
        if not ask_confirm(("Vypnout" if kind == "vypni" else "Restartovat") + " počítač za 20 sekund?"):
            return "Ruším, nic se neděje.", [], {}
        if IS_WIN:
            subprocess.Popen(cmd, shell=True, creationflags=NO_WINDOW)
        return f"{'Vypínám' if kind == 'vypni' else 'Restartuji'} za 20 sekund. Zrušíte to příkazem „zruš vypnutí“.", [{"tool": "power", "result": cmd}], {}
    if re.search(r"zrus (vypnuti|restart)", t):
        if IS_WIN:
            subprocess.Popen("shutdown /a", shell=True, creationflags=NO_WINDOW)
        return "Vypnutí zrušeno.", [], {}

    # rutiny
    m = re.match(r"(?:spust|zapni|aktivuj|nahod|start|iniciuj)\w*\s+(?:rutinu\s+|rezim\s+|protokol\s+)?(.+)", t)
    cand = m.group(1) if m else re.sub(r"^protokol\s+", "", t)
    for r in DATA.get("routines") or []:
        nr = norm(r["name"])
        if cand == nr or cand.replace("rezim ", "") == nr.replace("rezim", "").strip() or (m and nr in cand) or t == nr or t == "protokol " + nr:
            if not m and any(st.get("t") == "lock" for st in r["steps"]):
                continue   # bez jasného „spusť …“ nic nezamykáme (např. obyčejné „dobrou noc“)
            res = run_routine(r["id"])
            if any(st.get("t") == "say" for st in r["steps"]):
                res = f"Spouštím protokol {r['name']}."
            return res, [{"tool": "routine", "result": r["name"]}], {}
    # měny
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*([a-z.]+)\s+(?:v|na|do|ve)\s+([a-z.]+)", t)
    if m and cur_code(m.group(2)) and cur_code(m.group(3)):
        try:
            amt = float(m.group(1).replace(",", ".")); a1, a2 = cur_code(m.group(2)), cur_code(m.group(3))
            v = fx_convert(amt, a1, a2)
            out = f"{v:,.2f}".replace(",", " ").replace(".", ",")
            return f"{m.group(1)} {a1} je {out} {a2}.", [{"tool": "currency", "result": f"{a1}→{a2}"}], {}
        except Exception:
            pass
    m = re.search(r"(?:kurz|kolik stoji|kolik je)\s+(?:jedno |jeden )?(eur\w*|dolar\w*|libr\w*|zlot\w*|frank\w*)$", t)
    if m and cur_code(m.group(1)):
        try:
            c = cur_code(m.group(1)); v = fx_convert(1, c, "CZK")
            return f"Jedno {c} je teď {v:.2f} korun".replace(".", ",") + ".", [{"tool": "currency", "result": c}], {"panel": "currency"}
        except Exception:
            pass

    # úkoly
    m = re.match(r"(?:pridej|novy|zapis|dej)? ?(?:ukol|todo|task)[: ]+(.+)", t) or re.match(r"(?:pridej|dej) (?:mi )?(?:do ukolu|na seznam)[: ]+(.+)", t)
    if m and not re.match(r"^(ukoly|moje ukoly)$", t):
        orig = re.split(r"(?i)(?:úkol|ukol|todo|task|do úkolů|do ukolu|na seznam)[: ]+", text, maxsplit=1)[-1].strip()
        res = t_todo("add", orig[:1].upper() + orig[1:])
        return f"Přidáno do úkolů, {V()}.", [{"tool": "todo", "result": orig}], {}
    if re.search(r"^(ukaz |zobraz |otevri )?(moje )?ukoly$|co mam (dnes )?(udelat|na praci)", t):
        n = len([x for x in DATA["todos"] if not x["done"]])
        return (f"Máte {n} {'otevřený úkol' if n == 1 else 'otevřené úkoly' if 2 <= n <= 4 else 'otevřených úkolů'}." if n else "Všechno hotovo. Žádné otevřené úkoly."), [], {"panel": "todo"}
    m = re.match(r"(?:hotovo|splneno|odskrtni|udelal jsem|mam hotove?)[: ]+(.+)", t)
    if m:
        res = t_todo("done", m.group(1))
        return res, [{"tool": "todo", "result": res}], {}
    # časovač / stopky / pomodoro
    if re.search(r"\bstopky\b", t):
        return "Stopky běží.", [], {"panel": "timer", "timer": "stopwatch"}
    if re.search(r"\b(fokus|focus|rezim soustredeni|soustred)", t) and "pomodoro" not in t:
        mm = re.search(r"(\d{1,3})\s*(min|m\b|minut)", t); hh = re.search(r"(\d)\s*(hod|h\b)", t)
        mins = int(mm.group(1)) if mm else (int(hh.group(1)) * 60 if hh else 25)
        mins = max(5, min(mins, 240))
        return f"Režim soustředění aktivován. {mins} minut, rušivé prvky skryty. Hodně štěstí, {V()}.", [{"tool": "focus", "result": f"{mins} min"}], {"panel": "focus", "focus": mins}
    if re.search(r"pomodoro", t):
        return "Pomodoro spuštěno. Pětadvacet minut soustředění, pak pauza.", [], {"panel": "timer", "timer": "pomodoro"}
    # hudba
    if re.search(r"(co (ted )?hraje|jaka je to (pisnicka|pisen|skladba)|co to hraje|jak se jmenuje tahle)", t):
        return t_now_playing(), [], {"panel": "media"}
    if re.search(r"^(ukaz |otevri )?(schranku|historii schranky|schranka)$", t):
        return "Historie schránky.", [], {"panel": "clipboard"}

    # média
    if re.search(r"(dalsi|next|preskoc|skip)( (pisnick|song|skladb|track|video))?", t) and len(t) < 30:
        t_media("next"); return "Další skladba.", [{"tool": "media", "result": "next"}], {}
    if re.search(r"(predchozi|previous|predesl|zpatky pisnick)", t):
        t_media("previous"); return "Předchozí skladba.", [{"tool": "media", "result": "previous"}], {}
    if re.fullmatch(r"(pust|zapni|play|pokracuj)( (hudbu|muziku|pisnicku|to))?|pauza|pause|zastav( hudbu| to)?|stop( hudbu)?", t):
        t_media("play_pause"); return "Provedeno.", [{"tool": "media", "result": "play/pause"}], {}
    if re.search(r"(ztlum|mute|vypni zvuk|zapni zvuk)", t) and not re.search(r"trochu|o ", t):
        t_media("mute"); return "Zvuk přepnut.", [{"tool": "media", "result": "mute"}], {}
    if re.search(r"(zesil|nahlas|hlasitej|hlasitost nahoru|pridej zvuk|louder|volume up)", t):
        t_media("volume_up", 5); return "Hlasitost nahoru.", [{"tool": "media", "result": "volume +10 %"}], {}
    if re.search(r"(tiss?ej|tiseji|hlasitost dolu|uber zvuk|stlum|ztis|quieter|volume down)", t):
        t_media("volume_down", 5); return "Hlasitost dolů.", [{"tool": "media", "result": "volume −10 %"}], {}

    # připomínky
    m = re.search(r"(pripomen|pripomenout|upozorni|budik|timer|casovac|minutka)( mi)?( (za|v|ve|na))? ?(.*)", t)
    if m:
        rest_orig = text
        rest = m.group(5)
        mm = re.search(r"(?:^|\bza\s+)?(\d+(?:[.,]\d+)?|\w+)\s*(sekund|sek|s\b|minut|min|m\b|hodin|hod|h\b)\w*", rest)
        at = re.search(r"\b(?:v|ve|na)?\s*(\d{1,2})[:.](\d{2})\b", rest)
        what = re.sub(r"^(ze|abych|at|ať)\s+", "", re.sub(r"(za\s+)?(\d+(?:[.,]\d+)?|\w+)\s*(sekund|sek|minut|min|hodin|hod)\w*|\b(v|ve|na)?\s*\d{1,2}[:.]\d{2}\b", "", rest)).strip(" ,.")
        # vezmi originální text (s diakritikou) pro popis
        orig_what = re.split(r"(?i)(?:sekund|minut|hodin)\w*|\d{1,2}[:.]\d{2}", rest_orig)[-1].strip(" ,.")
        orig_what = re.sub(r"(?i)^(že|ze|abych|ať|at)\s+", "", orig_what).strip() or what or "Připomínka"
        if mm and _num(mm.group(1)) is not None:
            n = _num(mm.group(1)); unit = mm.group(2)
            mins = n / 60 if unit.startswith("s") else n * 60 if unit.startswith("h") else n
            label = orig_what[:1].upper() + orig_what[1:]
            if re.search(r"(casovac|minutka|timer|budik)", t) and (label in ("Připomínka", "") or norm(label).startswith(("na ", "casovac"))):
                label = f"⏱ Časovač {('%g' % n)} {unit[:3]}"
            res = t_reminder("add", label, in_minutes=mins)
            if label.startswith("⏱"):
                return f"Časovač běží, {('%g' % n)} {unit[:3]}.", [{"tool": "reminder", "result": res}], {"panel": "timer"}
            return f"Rozumím, {V()}. {res}", [{"tool": "reminder", "result": res}], {}
        if at:
            res = t_reminder("add", orig_what[:1].upper() + orig_what[1:], at=f"{at.group(1)}:{at.group(2)}")
            return f"Zapsáno. {res}", [{"tool": "reminder", "result": res}], {}

    # poznámky
    m = re.match(r"(?:zapis si|zapis|poznamenej si|poznamenej|poznamka|uloz si|zapamatuj si)[: ]+(.+)", t)
    if m:
        orig = re.split(r"(?i)^(?:.*?)(?:zapiš si|zapis si|zapiš|zapis|poznamenej si|poznamenej|poznámka|poznamka|ulož si|uloz si|zapamatuj si)[: ]+", text, maxsplit=1)[-1]
        res = t_note("add", orig.strip())
        return f"Poznamenáno, {V()}.", [{"tool": "note", "result": res}], {}
    if re.search(r"(moje poznamky|ukaz poznamky|zobraz poznamky|poznamky)$", t):
        return "Tady jsou vaše poznámky.", [], {"panel": "notes"}
    if re.search(r"(ukaz|zobraz|otevri)? ?(pripominky|co mam naplanovano)$", t):
        return "Vaše připomínky.", [], {"panel": "reminders"}
    if re.search(r"^(otevri |ukaz |zobraz )?(dashboard|panel|prehled|statistiky)$", t):
        return "Přehled je tady.", [], {"panel": "dashboard"}
    if re.search(r"^(otevri |ukaz |zobraz )?(nastaveni jarvise|tvoje nastaveni)$", t):
        return "Nastavení.", [], {"panel": "settings"}

    # systém
    if re.search(r"(stav systemu|jak je na tom (pc|pocitac|system)|system status|vytizeni|kolik ram|teplota (gpu|grafiky)|diagnostik|status)", t):
        s = system_stats()
        parts = []
        if s.get("cpu") is not None: parts.append(f"procesor {round(s['cpu'])} procent")
        if s.get("ram") is not None: parts.append(f"paměť {round(s['ram'])} procent")
        if s.get("gpu"): parts.append(f"grafika {round(s['gpu']['util'])} procent při {round(s['gpu']['temp'])} stupních")
        if s.get("disk_free_gb") is not None: parts.append(f"volno na disku {s['disk_free_gb']} gigabajtů")
        return ("Diagnostika dokončena. " + ", ".join(parts).capitalize() + ". Všechny systémy nominální.") if parts else "Systémy běží.", [], {"panel": "system"}
    if re.search(r"^(zamkni|uzamkni)( (pc|pocitac|obrazovku))?$", t):
        threading.Timer(1.5, t_lock_pc).start()
        return f"Zamykám. Uvidíme se, {V()}.", [{"tool": "lock_pc", "result": "zamčeno"}], {}

    # hledání
    m = re.match(r"(?:najdi|vyhledej|hledej|vygoogli|googli|google)(?: mi)?(?: na (youtube|youtubu|wikipedii|githubu|mapach))?\s+(.+)", t)
    if m:
        site = {"youtube": "youtube", "youtubu": "youtube", "wikipedii": "wikipedia", "githubu": "github", "mapach": "mapy"}.get(m.group(1) or "", "google")
        q = text.split(maxsplit=1 + (2 if m.group(1) else 0))[-1] if len(text.split()) > 1 else m.group(2)
        q = re.sub(r"(?i)^(mi\s+)?(na\s+\w+\s+)?", "", q)
        t_web_search(q, site)
        return f"Hledám {q}.", [{"tool": "browser_search", "result": q}], {}
    m = re.match(r"(?:pust|pusť|dej|zahraj)(?: mi)?(?: na youtube| na youtubu)?\s+(.+?)(?: na youtube| na youtubu)$", t)
    if m:
        q = re.sub(r"(?i)^(pusť|pust|dej|zahraj)\s+(mi\s+)?(na youtub\w+\s+)?", "", text)
        q = re.sub(r"(?i)\s+na youtub\w+$", "", q)
        t_web_search(q, "youtube")
        return f"YouTube: {q}.", [{"tool": "browser_search", "result": q}], {}

    # spouštění aplikací
    m = re.match(r"(?:otevri|otevrit|spust|spustit|pust|zapni|nahod|open|start|launch|jdi na|hod mi)(?: mi)?\s+(.+)", t)
    if m:
        target = m.group(1).strip()
        if re.match(r"^[\w.-]+\.(cz|com|org|net|io|gg|tv|sk|eu|dev|app)(/.*)?$", target):
            t_open_url(target); return f"Otevírám {target}.", [{"tool": "open_url", "result": target}], {}
        hit = find_app(target)
        if hit:
            res = t_open_app(target)
            return f"{hit[2] if hit[0] == 'app' else target.capitalize()} běží, {V()}." if res.startswith("Spuštěno") else res, [{"tool": "open_app", "result": res}], {}
        if not has_brain():
            return f"Aplikaci „{target}“ na tomhle PC nevidím, {V()}. Seznam najdete v modulu Aplikace.", [], {"panel": "apps"}
        return None
    return None


# =====================================================================
#  CHAT ENTRY
# =====================================================================
def handle_chat(text):
    text = (text or "").strip()
    if not text:
        return {"reply": "", "actions": [], "ui": {}}
    log("user", text)
    try:
        local = local_intent(text)
    except Exception as e:
        local = None
        print("local intent error", e)
    if local:
        reply, actions, ui = local
        source = "local"
    elif has_brain():
        try:
            (reply, actions), source = brain_turn(text)
            ui = {}
        except Exception as e:
            reply, actions, ui, source = f"Spojení s mozkem selhalo: {e}", [], {}, "error"
    else:
        reply = (f"Na tohle potřebuji spojení s hlavním mozkem, {V()}. Bez API klíče běžím v omezeném režimu: "
                 "aplikace, hudba, úkoly, počasí, měny, protokoly a diagnostika. Klíč přidáte v nastavení.")
        actions, ui, source = [], {"hint": "settings"}, "offline"
    log("jarvis", reply, {"source": source})
    return {"reply": reply, "actions": actions, "ui": ui, "source": source}


# =====================================================================
#  REMINDER SCHEDULER
# =====================================================================
def scheduler():
    while True:
        time.sleep(1)
        fired = []
        with LOCK:
            now = time.time()
            for r in DATA["reminders"]:
                if not r["done"] and r["due"] <= now:
                    r["done"] = True
                    fired.append(r)
        if fired:
            save_data()
            for r in fired:
                push_event({"type": "reminder", "text": r["text"]})
                log("reminder_fired", r["text"])
                if IS_WIN:
                    bring_to_front()


# =====================================================================
#  WINDOW (Edge app mode) + HOTKEY
# =====================================================================
EDGE_PROC = [None]
PERF = {}
LAST_PING = [time.time()]
WINDOW_TITLE = "J.A.R.V.I.S."


def find_edge():
    cands = [os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Microsoft\Edge\Application\msedge.exe"),
             os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Microsoft\Edge\Application\msedge.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\Application\msedge.exe")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome")


def kill_old_windows():
    """Zavře okna Jarvise z minula (Edge s naším profilem), aby se nepřebíraly staré příznaky."""
    if not IS_WIN:
        return
    try:
        _ps("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like '*\\.jarvis\\window-profile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }", 15)
        time.sleep(0.6)
    except Exception as e:
        print("kill_old_windows", e)


def open_window():
    url = f"http://127.0.0.1:{PORT}/"
    exe = find_edge()
    if not exe:
        webbrowser.open(url); return
    prof = os.path.join(APP_DIR, "window-profile")
    args = [exe, f"--app={url}", f"--user-data-dir={prof}", "--start-fullscreen", "--no-first-run", "--no-default-browser-check",
            "--autoplay-policy=no-user-gesture-required", "--disable-features=Translate,msEdgeSidebarV2,msHubApps,CalculateNativeWinOcclusion,msEfficiencyMode",
            "--disable-sync", "--hide-crash-restore-bubble", "--disable-session-crashed-bubble",
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
            "--enable-gpu-rasterization", "--ignore-gpu-blocklist"]
    EDGE_PROC[0] = subprocess.Popen(args)


def find_window():
    if not IS_WIN:
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith(WINDOW_TITLE) and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def bring_to_front():
    hwnd = find_window()
    if not hwnd:
        return False
    import ctypes
    u = ctypes.windll.user32
    u.ShowWindow(hwnd, 9)
    u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)  # Alt trik, aby Windows povolil focus
    u.SetForegroundWindow(hwnd)
    return True


def hotkey_loop():
    """Ctrl+Alt+J = vyvolat Jarvise odkudkoliv."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    if not u.RegisterHotKey(None, 1, 0x0002 | 0x0001 | 0x4000, 0x4A):
        return
    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:
            if not bring_to_front():
                open_window()
            push_event({"type": "ui", "action": "focus"})


def idle_seconds():
    import ctypes

    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LII(); lii.cbSize = ctypes.sizeof(LII)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    return ((ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def idle_watch():
    """Když se uživatel vrátí k PC po delší pauze, Jarvis ho přivítá."""
    away_since = None
    while True:
        time.sleep(2)
        try:
            idle = idle_seconds()
        except Exception:
            return
        if idle > 20 * 60 and away_since is None:
            away_since = time.time() - idle
        elif away_since is not None and idle < 3:
            mins = int((time.time() - away_since) / 60)
            away_since = None
            bring_to_front()
            push_event({"type": "welcome", "away_min": mins})
            log("welcome", f"Návrat po {mins} min")


def say_native(text):
    """Záložní hlas přes Windows SAPI (když prohlížeč nesmí mluvit bez kliknutí)."""
    if not IS_WIN or not text:
        return
    safe = text.replace("'", "''").replace("\n", " ")[:1500]
    ps = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$v=$s.GetInstalledVoices()|?{$_.VoiceInfo.Culture.Name -eq 'cs-CZ'}|select -First 1; "
          "if($v){$s.SelectVoice($v.VoiceInfo.Name)}; $s.Rate=1; $s.Speak('" + safe + "')")
    subprocess.Popen(["powershell", "-NoProfile", "-Command", ps], creationflags=NO_WINDOW)


def watchdog(server):
    """Když se okno zavře, vypni server."""
    started = time.time()
    while True:
        time.sleep(2)
        p = EDGE_PROC[0]
        alive_proc = p is not None and p.poll() is None
        recently_pinged = time.time() - LAST_PING[0] < 25
        if time.time() - started > 40 and not alive_proc and not recently_pinged:
            server.shutdown(); return


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    try:
        if on:
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            winreg.SetValueEx(key, "Jarvis", 0, winreg.REG_SZ, (f'"{sys.executable}"' if FROZEN else f'"{pyw}" "{os.path.abspath(__file__)}"'))
        else:
            try:
                winreg.DeleteValue(key, "Jarvis")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def make_desktop_shortcut():
    """Jednou vytvoří zástupce J.A.R.V.I.S. na ploše s ikonou."""
    if not IS_WIN:
        return
    flag = os.path.join(APP_DIR, ".shortcut_done")
    if os.path.exists(flag):
        return
    try:
        ico = os.path.join(UI_DIR, "jarvis.ico")
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        desk = _ps("[Environment]::GetFolderPath('Desktop')")
        lnk = os.path.join(desk, "J.A.R.V.I.S..lnk")
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{pyw}';"
                  f"$s.Arguments='\"{os.path.abspath(__file__)}\"';$s.WorkingDirectory='{HERE}';"
                  f"$s.IconLocation='{ico}';$s.Description='J.A.R.V.I.S.';$s.Save()")
        _ps(script)
        open(flag, "w").close()
    except Exception as e:
        print("shortcut", e)


def make_start_shortcut():
    """Zástupce ve Start menu: 'J.A.R.V.I.S. – Jarvis', aby ho Windows Search našel i na 'jar'."""
    if not IS_WIN:
        return
    try:
        progs = _ps("[Environment]::GetFolderPath('Programs')")
        if not progs:
            return
        lnk = os.path.join(progs, "J.A.R.V.I.S. - Jarvis.lnk")
        if os.path.exists(lnk):
            return
        ico = os.path.join(UI_DIR, "jarvis.ico")
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{pyw}';"
                  f"$s.Arguments='\"{os.path.abspath(__file__)}\"';$s.WorkingDirectory='{HERE}';"
                  f"$s.IconLocation='{ico}';$s.Description='Jarvis asistent';$s.Save()")
        _ps(script)
    except Exception as e:
        print("start shortcut", e)


# =====================================================================
#  HTTP SERVER
# =====================================================================
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json"}


def public_config():
    c = {k: v for k, v in CFG.items() if k not in ("api_key", "eleven_key", "groq_key")}
    c["has_eleven"] = bool(CFG.get("eleven_key"))
    c["has_key"] = has_brain()
    c["has_claude"] = bool(CFG.get("api_key"))
    c["has_groq"] = bool(CFG.get("groq_key"))
    c["key_hint"] = ("…" + CFG["api_key"][-4:]) if CFG.get("api_key") else ""
    return c


def state():
    with LOCK:
        return {
            "config": public_config(),
            "notes": DATA["notes"][-200:],
            "todos": DATA["todos"][-300:],
            "routines": DATA["routines"], "step_types": STEP_TYPES,
            "kv": DATA.get("kv", {}),
            "reminders": [r for r in DATA["reminders"] if not r["done"] or time.time() - r["due"] < 86400][-100:],
            "log": DATA["log"][-1500:],
            "app_usage": DATA["app_usage"],
            "apps": sorted(APPS.keys(), key=lambda s: s.lower()),
            "last_seen": DATA.get("last_seen"),
            "first_run": DATA.get("first_run"),
            "platform": platform.system(),
            "event_id": EVENT_ID[0],
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok_origin(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return host.startswith(("127.0.0.1", "localhost")) and (origin is None or re.match(r"^http://(127\.0\.0\.1|localhost):%d$" % PORT, origin))

    def do_GET(self):
        if not self._ok_origin():
            return self._send(403, {"error": "forbidden"})
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/api/state":
            return self._send(200, state())
        if u.path == "/api/geocode":
            q = (qs.get("q", [""])[0] or "").strip()
            if len(q) < 2:
                return self._send(200, {"results": []})
            try:
                g = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=6&language=cs&format=json&name=" + urllib.parse.quote(q))
                res = [{"name": x.get("name"), "country": x.get("country", ""), "admin": x.get("admin1", ""), "lat": x.get("latitude"), "lon": x.get("longitude"),
                        "pop": x.get("population", 0)} for x in (g.get("results") or [])]
                return self._send(200, {"results": res})
            except Exception as e:
                return self._send(200, {"results": [], "error": type(e).__name__})
        if u.path == "/api/iss":
            return self._send(200, iss_now())
        if u.path == "/api/tex":
            if qs.get("start") and not tex_ready():
                threading.Thread(target=tex_worker, daemon=True).start()
            return self._send(200, {"ready": tex_ready(), **TEX_STATE})
        if u.path.startswith("/tex/"):
            name = os.path.basename(u.path)
            full = os.path.join(TEX_DIR, name)
            if name.replace(".jpg", "") not in TEX_SRC or not os.path.isfile(full):
                return self._send(404, {"error": "no tex"})
            with open(full, "rb") as f:
                return self._send(200, f.read(), "image/jpeg")
        if u.path == "/api/ping":
            hosts = [h.strip() for h in qs.get("hosts", [",".join(DEFAULT_HOSTS)])[0].split(",") if h.strip()]
            return self._send(200, {"results": ping_many(hosts), "ts": time.time()})
        if u.path == "/api/perf":
            return self._send(200, PERF)
        if u.path == "/api/tts":
            text = qs.get("text", [""])[0][:2000]
            live = qs.get("live", ["1"])[0] == "1"
            try:
                audio, cached = eleven_tts(text, allow_live=live)
                return self._send(200, audio, "audio/mpeg")
            except Exception as e:
                return self._send(503, {"error": str(e)})
        if u.path == "/api/voiceinfo":
            u_ = _usage()
            files = [f for f in os.listdir(VOICE_DIR) if f.endswith(".mp3")]
            return self._send(200, {"voices": ELEVEN_VOICES, "used": u_.get("chars", 0), "budget": ELEVEN_BUDGET, "cached": len(files),
                                    "phrases": len(canned_phrases()), "precache": PRECACHE})
        if u.path == "/api/weather":
            try:
                return self._send(200, weather(qs.get("city", [None])[0]))
            except Exception as e:
                return self._send(503, {"error": str(e)})
        if u.path == "/api/fx":
            try:
                f = fx_rates(); return self._send(200, {"rates": f["rates"], "date": f["date"]})
            except Exception as e:
                return self._send(503, {"error": str(e)})
        if u.path == "/api/clip":
            with LOCK:
                return self._send(200, CLIP)
        if u.path == "/api/media":
            return self._send(200, MEDIA)
        if u.path == "/api/procs":
            return self._send(200, processes())
        if u.path == "/api/sys":
            return self._send(200, system_stats())
        if u.path == "/api/events":
            LAST_PING[0] = time.time()
            if "fps" in qs:
                PERF[self.headers.get("User-Agent", "?")[-40:]] = {k: qs.get(k, [""])[0] for k in ("fps", "w", "h", "dpr", "vis", "voices", "voice", "gs", "hz")} | {"t": time.strftime("%H:%M:%S")}
            since = int(qs.get("since", ["0"])[0])
            with LOCK:
                evs = [e for e in EVENTS if e["id"] > since]
            return self._send(200, {"events": evs, "last": EVENT_ID[0]})
        path = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        full = os.path.normpath(os.path.join(UI_DIR, path))
        if not full.startswith(UI_DIR) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        with open(full, "rb") as f:
            return self._send(200, f.read(), MIME.get(os.path.splitext(full)[1], "application/octet-stream"))

    def do_POST(self):
        if not self._ok_origin():
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        p = urllib.parse.urlparse(self.path).path
        try:
            if p == "/api/chat":
                return self._send(200, handle_chat(body.get("text", "")))
            if p == "/api/config":
                for k in DEFAULT_CONFIG:
                    if k in body and k not in ("api_key", "eleven_key", "groq_key"):
                        CFG[k] = body[k]
                if body.get("api_key") is not None and body.get("api_key") != "":
                    CFG["api_key"] = body["api_key"].strip()
                if body.get("clear_key"):
                    CFG["api_key"] = ""
                if body.get("eleven_key_new"):
                    CFG["eleven_key"] = body["eleven_key_new"].strip()
                if body.get("clear_eleven"):
                    CFG["eleven_key"] = ""
                if body.get("groq_key_new"):
                    CFG["groq_key"] = body["groq_key_new"].strip(); GHISTORY.clear()
                if body.get("clear_groq"):
                    CFG["groq_key"] = ""
                if "autostart" in body:
                    try:
                        set_autostart(bool(body["autostart"]))
                    except Exception as e:
                        print("autostart", e)
                save_json(CONFIG_PATH, CFG)
                return self._send(200, {"ok": True, "config": public_config()})
            if p == "/api/onboard":
                name = str(body.get("name", "")).strip()[:40]
                if not name:
                    return self._send(400, {"error": "Chybí jméno"})
                CFG["name"] = name
                CFG["vocative"] = str(body.get("vocative") or name).strip()[:40]
                CFG["city"] = str(body.get("city", "")).strip()[:80]
                if isinstance(body.get("visual"), dict):
                    CFG["visual"] = body["visual"]
                if body.get("core_color") in ("vibra", "blue", "gold", "white", "violet", "green", "red"):
                    CFG["core_color"] = body["core_color"]
                if "ui_sounds" in body:
                    CFG["ui_sounds"] = bool(body["ui_sounds"])
                if "voice" in body:
                    CFG["voice"] = bool(body["voice"])
                CFG["onboarded"] = True
                _WX_CACHE.clear()
                save_json(CONFIG_PATH, CFG)
                return self._send(200, {"ok": True, "config": public_config()})
            if p == "/api/openurl":
                u = str(body.get("url", "")).strip()
                if not re.match(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", u, re.I):
                    return self._send(400, {"error": "bad url"})
                return self._send(200, {"result": t_open_url(u)})
            if p == "/api/kv":
                key = str(body.get("key", ""))[:40]
                if key:
                    with LOCK:
                        DATA.setdefault("kv", {})[key] = body.get("value")
                    save_data()
                return self._send(200, {"ok": True})
            if p == "/api/note":
                return self._send(200, {"result": t_note(body.get("action", "add"), body.get("text", ""), body.get("id", ""))})
            if p == "/api/reminder":
                return self._send(200, {"result": t_reminder(body.get("action", "add"), body.get("text", ""), body.get("in_minutes"), body.get("at"), body.get("id", ""))})
            if p == "/api/routine":
                act = body.get("action", "run")
                if act == "run":
                    return self._send(200, {"result": run_routine(body.get("id", ""))})
                if act == "save":
                    with LOCK:
                        DATA["routines"] = body.get("routines", DATA["routines"])
                    save_data(); push_event({"type": "data"})
                    return self._send(200, {"ok": True})
                if act == "reset":
                    with LOCK:
                        DATA["routines"] = json.loads(json.dumps(DEFAULT_ROUTINES))
                    save_data(); return self._send(200, {"ok": True})
            if p == "/api/precache":
                if not PRECACHE["running"]:
                    threading.Thread(target=precache_worker, daemon=True).start()
                return self._send(200, {"ok": True})
            if p == "/api/voiceclear":
                for f in os.listdir(VOICE_DIR):
                    if f.endswith(".mp3"):
                        try:
                            os.remove(os.path.join(VOICE_DIR, f))
                        except Exception:
                            pass
                return self._send(200, {"ok": True})
            if p == "/api/todo":
                return self._send(200, {"result": t_todo(body.get("action", "add"), body.get("text", ""), body.get("id", ""))})
            if p == "/api/clip":
                if body.get("clear"):
                    with LOCK:
                        CLIP.clear()
                else:
                    set_clipboard(body.get("text", ""))
                return self._send(200, {"ok": True})
            if p == "/api/kill":
                return self._send(200, {"result": kill_process(body.get("name", ""))})
            if p == "/api/launch":
                return self._send(200, {"result": t_open_app(body.get("name", ""))})
            if p == "/api/media":
                return self._send(200, {"result": t_media(body.get("action", ""), body.get("steps", 5))})
            if p == "/api/lock":
                threading.Timer(0.8, t_lock_pc).start()
                return self._send(200, {"result": "ok"})
            if p == "/api/confirm":
                c = CONFIRMS.get(body.get("cid"))
                if c:
                    c["ok"] = bool(body.get("ok")); c["event"].set()
                return self._send(200, {"ok": True})
            if p == "/api/seen":
                with LOCK:
                    DATA["last_seen"] = time.time()
                save_data()
                return self._send(200, {"ok": True})
            if p == "/api/restart":
                threading.Thread(target=restart_self, daemon=True).start()
                return self._send(200, {"ok": True})
            if p == "/api/say":
                say_native(body.get("text", ""))
                return self._send(200, {"ok": True})
            if p == "/api/reset":
                HISTORY.clear()
                return self._send(200, {"ok": True})
            if p == "/api/rescan":
                discover_apps()
                return self._send(200, {"apps": sorted(APPS.keys(), key=str.lower)})
        except Exception as e:
            return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not found"})


SERVER = [None]


def restart_self():
    """Spustí novou instanci (ta si počká na uvolnění portu) a teprve pak se vypne."""
    time.sleep(0.3)
    with LOCK:
        DATA["last_seen"] = time.time()
    save_data()
    exe = sys.executable
    args = [a for a in sys.argv[1:] if a != "--restarted"] + ["--restarted"]
    if IS_WIN:
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = pyw if os.path.exists(pyw) else exe
        base = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(self_cmd() + args, creationflags=base | 0x01000000, close_fds=True, cwd=HERE)  # + BREAKAWAY_FROM_JOB
        except OSError:
            subprocess.Popen(self_cmd() + args, creationflags=base, close_fds=True, cwd=HERE)
    else:
        subprocess.Popen(self_cmd() + args, start_new_session=True, cwd=HERE)
    print("restart: child spawned", flush=True)
    time.sleep(0.5)
    try:
        if EDGE_PROC[0] and EDGE_PROC[0].poll() is None:
            EDGE_PROC[0].terminate()
    except Exception:
        pass
    if SERVER[0]:
        SERVER[0].shutdown(); SERVER[0].server_close()
    os._exit(0)


class JarvisServer(ThreadingHTTPServer):
    allow_reuse_address = not IS_WIN

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)   # na Windows by SO_REUSEADDR dovolil víc serverů na jednom portu
    daemon_threads = True

    def server_bind(self):
        if IS_WIN:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
            except Exception:
                pass
        super().server_bind()


def port_in_use():
    s = socket.socket()
    try:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def main():
    headless = "--no-window" in sys.argv
    restarted = "--restarted" in sys.argv
    if restarted:
        for _ in range(60):
            if not port_in_use():
                break
            time.sleep(0.25)
    if port_in_use():
        # Jarvis už běží → jen ho vyvolej
        if not bring_to_front():
            open_window()
        return
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    discover_apps()
    for _ in range(40):
        try:
            server = JarvisServer(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            time.sleep(0.25)
    else:
        if not bring_to_front():
            open_window()
        return
    SERVER[0] = server
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=lambda: (time.sleep(600), discover_apps()), daemon=True).start()
    if IS_WIN:
        threading.Thread(target=hotkey_loop, daemon=True).start()
        threading.Thread(target=idle_watch, daemon=True).start()
        threading.Thread(target=clip_watch, daemon=True).start()
        threading.Thread(target=media_watch, daemon=True).start()
        if not FROZEN:
            threading.Thread(target=make_desktop_shortcut, daemon=True).start()
            threading.Thread(target=make_start_shortcut, daemon=True).start()
    if not headless:
        kill_old_windows()
        open_window()
        threading.Thread(target=watchdog, args=(server,), daemon=True).start()
    print(f"J.A.R.V.I.S. online na http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    finally:
        with LOCK:
            DATA["last_seen"] = time.time()
        save_data()


if __name__ == "__main__":
    LOG_PATH = os.path.join(APP_DIR if FROZEN else HERE, "jarvis.log")
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 500_000:
            os.remove(LOG_PATH)
        _lf = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None or "pythonw" in os.path.basename(sys.executable).lower():
            sys.stdout = sys.stderr = _lf
        print(f"--- start {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} ---")
    except Exception:
        pass
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise
