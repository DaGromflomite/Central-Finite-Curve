"""Central Finite Curve - a small always-on desktop window for watching a Meshtastic node.

Connects to one node over USB (serial) or Wi-Fi (TCP). Only one program can
hold that connection, so use Pause before opening the Meshtastic app.
"""
import collections
import datetime
import gzip
import hashlib
import io
import json
import logging
import logging.handlers
import math
import platform
import random
import re
import sys
import urllib.error
import urllib.request
import webbrowser
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
try:
    import winsound  # Windows only
except ImportError:
    winsound = None  # macOS / Linux: alerts fall back to sounddevice

try:  # optional: lets the alert play on a chosen output instead of the Windows default
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    sd = sf = None
try:  # optional: tray icon instead of a taskbar button
    import pystray
except ImportError:
    pystray = None
try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = ImageDraw = None
try:  # optional: map tab
    import requests
    import tkintermapview
    import PIL.Image
    if not hasattr(PIL.Image, "ANTIALIAS"):
        # tkintermapview 1.30 still uses the old name, removed in Pillow 10; without this
        # every tile with a label overlay fails silently and the map stays white
        PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
except ImportError:
    tkintermapview = None
from pubsub import pub
import meshtastic.serial_interface
import meshtastic.tcp_interface

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
LOG_PATH = os.path.join(HERE, "messages.jsonl")
HIST_PATH = os.path.join(HERE, "nodes-history.json")
AIRSPACE_PATH = os.path.join(HERE, "airspace-cache.json")  # FAA zones near you, refreshed daily
DEBUG_LOG = os.path.join(HERE, "mesh_widget.log")  # errors and health, for debugging during testing
OUI_PATH = os.path.join(HERE, "oui.txt")           # IEEE OUI table: first 3 MAC octets -> manufacturer
FLIGHTS_DIR = os.path.join(HERE, "flights")        # saved drone flight logs + map snapshots


def asset_path(name):
    """Path to a bundled asset (icons), working from source and from a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", HERE)
    return os.path.join(base, "assets", name)
VERSION = "0.5.0-alpha"
HEALTH_SECS = 600   # how often a health line (memory, CPU, nodes) goes into the log
DEMO = False        # --demo: made-up nodes instead of a radio, with its own files (see enable_demo)
log = logging.getLogger("centralfinitecurve")
DEFAULTS = {"mode": "serial", "port": "COM16", "host": "", "on_top": False,
            "sound": True, "geometry": "440x680"}
RETRY_SECS = 10
ACTIVE_MINS = 180  # a quiet node only announces itself every ~3 h, so a shorter window hides working nodes
PUSH_GAP = 30  # seconds between phone pushes; alerts in between are merged into the next one
# Settings -> Phone alerts -> "Send to phone": which incoming messages are pushed
PUSH_MODES = (("drone", "Drone alerts only"), ("dm", "Drone alerts + direct messages"),
              ("all", "Every message received"))
CLOCK_SECS = 6 * 3600  # re-send the PC's time this often, so a radio with no GPS doesn't drift
SEARCH_GAP = 12  # seconds between manual searches, so clicking repeatedly doesn't flood the mesh
AUTO_SEARCH_SECS = 180  # how often the background discovery runs (0 = off). This is what makes the
# widget a *transient-node tracker*: every few minutes it sends a DIRECT (0-hop) node-info request so
# any node that has passed into range — even a silent one — is prompted to announce itself. Because it
# is 0-hop it only touches your local airspace and is never relayed into the wider mesh, so it is safe
# to run on a timer. 180s is the light default; override with cfg "auto_search_secs".
AUTO_SEARCH_MIN = 20  # hard floor on the interval — never let a config hammer the local airtime
if sys.platform == "darwin":
    DEFAULT_SOUND = "/System/Library/Sounds/Ping.aiff"   # macOS built-in
elif sys.platform == "win32":
    DEFAULT_SOUND = r"C:\Windows\Media\Windows Notify Messaging.wav"
else:
    DEFAULT_SOUND = ""   # Linux: pick a sound file in Settings
WORLD_VIEW = (20, 0)  # map start point when no home location is set
REPEATER_ROLES = {"ROUTER", "ROUTER_LATE", "REPEATER", "ROUTER_CLIENT"}
ONLINE_URL = "https://meshmap.net/nodes.json"  # community map fed by MQTT; drops nodes after ~8h quiet
ONLINE_REFRESH_SECS = 600
# Parses a Drone Detect alert: "Drone: <mac> RSSI:-NN https://maps.google.com/?q=<lat>,<lon> \r\n Pilot: ...q=<lat>,<lon>"
# Groups: 1 mac, 2 rssi, 3 drone lat, 4 drone lon, 5 pilot lat, 6 pilot lon.
DRONE_RE = re.compile(
    r"Drone:\s*(\S+)\s+RSSI:\s*(-?\d+).*?q=(-?\d+\.\d+),(-?\d+\.\d+)"
    r".*?Pilot:.*?q=(-?\d+\.\d+),(-?\d+\.\d+)", re.S)

_OUI = None
def oui_vendor(mac):
    """Manufacturer for a MAC's first three octets (OUI), from the bundled IEEE
    table (oui.txt). Returns None if unknown or the table is missing. Note: the
    OUI is the radio-chip/NIC maker, which may differ from the drone's brand."""
    global _OUI
    if not mac:
        return None
    key = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()[:6]
    if len(key) < 6:
        return None
    if _OUI is None:
        _OUI = {}
        try:
            with open(OUI_PATH, encoding="utf-8") as f:
                for line in f:
                    pfx, _, name = line.partition("\t")
                    if name:
                        _OUI[pfx.strip()] = name.strip()
        except OSError:
            _OUI = {}
    return _OUI.get(key)
AIRCRAFT = "#6ad7e5"  # aircraft from adsb.fi (cyan), so they never look like mesh nodes
AIRCRAFT_URL = "https://opendata.adsb.fi/api/v3/lat/{lat:.4f}/lon/{lon:.4f}/dist/{nm}"
AIRCRAFT_SECS = 15       # between fetches while a map is on screen (adsb.fi allows 1/s; we stay far below)
AIRCRAFT_BACKOFF = 120   # after a refusal or error, wait this long before asking again
AIRCRAFT_NM = 50         # default radius, nautical miles (adsb.fi allows up to 250); rural skies are sparse
FAA_ARCGIS = "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/{svc}/FeatureServer/0/query"
TFR_LIST_URL = "https://tfr.faa.gov/tfrapi/exportTfrList"
TFR_XML_URL = "https://tfr.faa.gov/download/detail_{id}.xml"
AIRSPACE_REFRESH = 86400     # airport airspace hardly changes: download it once a day
TFR_REFRESH = 1800           # temporary flight restrictions come and go: check the list every 30 min
AIRSPACE_SPAN = (0.6, 0.8)   # degrees of latitude / longitude around you (about 65 km)
AIRSPACE_CLASSES = {"CLASS_B": ("Class B", "#4f8cff"), "CLASS_C": ("Class C", "#d04fd0"),
                    "CLASS_D": ("Class D", "#4fb3ff"), "CLASS_E2": ("Class E surface", "#7aa0c8")}
TFR_COLOUR = "#ff4d4d"
AIRSPACE_REPEAT = 600        # seconds before the same drone/zone pair can warn again
DRONE_FRESH_SECS = 180   # a drone counts as "in the air now" this long after its last ping
CONFLICT_KM = 3.0        # warn when a fresh drone and a low aircraft are this close (about 1.9 mi)
CONFLICT_ALT_FT = 3500   # "low" aircraft: at or below this altitude (feet, as the aircraft reports it)
CONFLICT_REPEAT = 300    # seconds before the same drone/aircraft pair can warn again
PING_DUP_SECS = 5        # the same ping heard by two detectors within this time counts once on the track
ROUTE_URL = "https://vrs-standing-data.adsb.lol/routes/{prefix}/{callsign}.json"  # VRS standing data, CC0
ROUTE_RE = re.compile(r"^[A-Z]{3}[0-9]{1,4}[A-Z]{0,2}$")  # airline-style callsigns only (AAL100, not N682D)
# optional extras a detector may add to its alert: "ALT:123m AGL:45m SPD:8m/s ID:1581F..."
DRONE_EXTRA_RE = re.compile(r"\b(ALT|AGL|SPD|ID):\s*(-?[0-9A-Za-z.]+)")
LIMIT_AGL_M = 122        # 400 ft: the usual ceiling for hobby drones in the US
EMERGENCY_SQUAWKS = {"7500": "hijack", "7600": "radio failure", "7700": "emergency"}
ONLINE = "#b07cff"  # marker colour for internet-reported nodes, so they never look like radio-heard ones


def setup_logging():
    """Rotating log (3 x 1 MB) that also catches crashes in any thread or UI callback."""
    try:
        handler = logging.handlers.RotatingFileHandler(DEBUG_LOG, maxBytes=1_000_000, backupCount=3,
                                                       encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()  # read-only folder: run without a log rather than not at all
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG if "--debug" in sys.argv else logging.INFO)
    sys.excepthook = lambda *exc: log.critical("Crashed", exc_info=exc)
    threading.excepthook = lambda a: log.error("Thread %s crashed", getattr(a.thread, "name", "?"),
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    log.info("=== Central Finite Curve %s%s starting · Python %s · %s · Tk %s · map=%s tray=%s sound-device=%s",
             VERSION, " (DEMO)" if DEMO else "", platform.python_version(), platform.platform(),
             tk.TkVersion, bool(tkintermapview), bool(pystray), bool(sd))


_last_warned = {}


def warn_once(key, msg, *args, every=600, exc_info=False):
    """Log a warning that would otherwise repeat on every retry: at most once per `every` seconds per key."""
    now = time.time()
    if now - _last_warned.get(key, 0) >= every:
        _last_warned[key] = now
        log.warning(msg, *args, exc_info=exc_info)


def memory_mb():
    """This process's memory use (resident set), or None if the OS won't say."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes as w

            class Counters(ctypes.Structure):
                _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [
                    (n, ctypes.c_size_t) for n in ("peak", "rss", "a", "b", "c", "d", "page", "peak_page")]
            k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
            k32.GetCurrentProcess.restype = w.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return c.rss / 2 ** 20
        with open("/proc/self/statm") as f:  # Linux
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2 ** 20
    except Exception:
        try:
            import resource  # macOS: peak rather than current, in bytes
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20
        except Exception:
            return None


def km_between(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def real_pos(p):
    """True if a position dict holds a usable fix. Faulty GPS units report 0,0 ("Null Island")
    or out-of-range values, which would drag Fit all out to the whole world."""
    lat, lon = (p or {}).get("latitude"), (p or {}).get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (abs(lat) < 1e-4 and abs(lon) < 1e-4)


def precision_m(bits):
    # Meshtastic position precision: owners blur their location to a grid cell of this size
    return 23905787.925 / (2 ** bits) if bits and bits < 32 else 0


def circle(lat, lon, radius_m, points=28):
    dlat = radius_m / 111320
    dlon = radius_m / (111320 * max(math.cos(math.radians(lat)), 0.01))
    return [(lat + dlat * math.sin(2 * math.pi * i / points), lon + dlon * math.cos(2 * math.pi * i / points))
            for i in range(points)]


def fetch_online(center, radius_km):
    req = urllib.request.Request(ONLINE_URL, headers={"User-Agent": "CentralFiniteCurve/1.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"unexpected nodes.json shape: {type(data).__name__}")
    found, skipped = {}, 0
    for num, n in data.items():
        try:  # third-party data: one malformed node mustn't cost us all the others
            pos = (n["latitude"] / 1e7, n["longitude"] / 1e7)
            if not real_pos({"latitude": pos[0], "longitude": pos[1]}) or km_between(center, pos) > radius_km:
                continue
            seen = max((v for v in (n.get("seenBy") or {}).values() if isinstance(v, (int, float))), default=0)
            found[f"!{int(num) & 0xFFFFFFFF:08x}"] = {
                "name": str(n.get("longName") or n.get("shortName") or "?").strip()[:40],
                "lat": pos[0], "lon": pos[1], "role": str(n.get("role") or "CLIENT"),
                "acc": precision_m(n.get("precision") if isinstance(n.get("precision"), int) else None),
                "seen": min(seen, time.time()),  # a gateway with a fast clock mustn't put it in the future
                "gateways": len(n.get("seenBy") or {}),  # for the pin info card
                "raw": {k: v for k, v in n.items() if k not in ("seenBy", "neighbors")}}
        except (KeyError, TypeError, ValueError, AttributeError):
            skipped += 1
    if skipped:
        log.debug("meshmap.net: skipped %d malformed nodes", skipped)
    return found
MAP_FILTERS = ("All", "Nodes", "Repeaters")
# the message panels along the bottom: (key, heading, starting width px, grows with the window?)
# "chan" only ever holds what the radio could decrypt: public messages on channels this radio shares.
MSG_PANELS = (("dm", "DIRECT MESSAGES", 380, "always"), ("drone", "DRONE DETECT", 220, "never"),
              ("chan", "CHANNEL TRAFFIC", 380, "always"))
DRONE_BANNER_SECS = 300  # the red banner hides 5 min after the last drone ping
MSG_PANEL_LINES = 1000  # lines kept per panel (the full log stays in messages.jsonl)
# Map "Show" choices: which radio-heard nodes get a pin, by when we last heard them.
# "now" = the Active window (no grey pins); None = every node we've ever heard.
MAP_SEEN = (("now", "Nodes currently active"), (3600, "Nodes active in the last hour"),
            (86400, "Nodes active in the last day"), (30 * 86400, "Nodes active in the last month"),
            (None, "All recorded nodes"))
_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
MAP_STYLES = {  # name -> (base tiles, label overlay or None, max zoom); all free, no API key
    "Dark": (_ESRI.format("Canvas/World_Dark_Gray_Base"), _ESRI.format("Canvas/World_Dark_Gray_Reference"), 16),
    "Street": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", None, 19),
    "Satellite": (_ESRI.format("World_Imagery"), _ESRI.format("Reference/World_Boundaries_and_Places"), 19),
}

# Central Finite Curve — Rick & Morty "portal" palette, sampled from the app icon.
BG, PANEL, FG, DIM, ACCENT, GOOD, BAD, WARN = (
    "#0b1412", "#13201b", "#e9f2ea", "#7e968a", "#8fe04a", "#3fe0a0", "#ef5a52", "#e8c24a")


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}
    except ValueError:
        # damaged (e.g. power cut mid-write by an older version): keep it aside rather than overwrite it
        bad = f"{path}.corrupt-{int(time.time())}"
        try:
            os.replace(path, bad)
        except OSError:
            bad = "(could not move it)"
        log.error("%s was damaged and has been set aside as %s; starting it fresh", os.path.basename(path), bad)
        return {}


MAX_MESSAGES = 2000    # (old fixed cap; the log is now the chat archive, trimmed by age below)
ARCHIVE_DAYS = 90      # default for Settings -> "Keep chat archive"; 0 = forever
ARCHIVE_CHOICES = (("1 week", 7), ("1 month", 30), ("3 months", 90), ("1 year", 365), ("Forever", 0))
ARCHIVE_CAP = 100_000  # hard ceiling on archived messages (~20 MB) even on "Forever"
ARCHIVE_SHOW = 3000    # most recent matches the archive window shows
MAX_HISTORY = 5000     # nodes remembered; the longest-unheard are dropped first
HISTORY_DAYS = 365     # nodes unheard for this long are forgotten
INSTANCE_PORT = 47431  # localhost port that stops a second copy starting (demo uses the next one)


def trim_lines(path, keep):
    """Cut a line-per-record file down to its last `keep` lines once it's 25% over."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return 0
    if len(lines) <= keep * 1.25:
        return 0
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-keep:])
        os.replace(tmp, path)
    except OSError:
        return 0
    return len(lines) - keep


def trim_archive(path, keep_days, cap=ARCHIVE_CAP, now=None):
    """Drop archived messages older than keep_days (0 = keep all), and the oldest beyond `cap`.
    Streams the file and only rewrites it when something goes. Returns how many were dropped."""
    cutoff = (now or time.time()) - keep_days * 86400 if keep_days > 0 else None
    kept = collections.deque(maxlen=cap)
    total = 0
    try:
        with open(path, encoding="utf-8") as f:
            past_cutoff = cutoff is None
            for line in f:
                total += 1
                if not past_cutoff:
                    try:
                        if float(json.loads(line).get("t", 0)) < cutoff:
                            continue
                    except (ValueError, AttributeError, TypeError):
                        continue  # unreadable line: drop it with the old ones
                    past_cutoff = True  # the log is in time order: everything after this is newer
                kept.append(line)
    except OSError:
        return 0
    if len(kept) == total:
        return 0
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(kept)
        os.replace(tmp, path)
    except OSError:
        return 0
    return total - len(kept)


def tail_lines(path, count, chunk=262144):
    """The last `count` lines of a file without reading all of it (the archive can be large)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - chunk))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    if size > chunk:
        lines = lines[1:]  # the first one is probably cut in half
    return lines[-count:]


def route_plausible(route, lat, lon):
    """A callsign's usual route can be stale or reused: only trust it if the aircraft is roughly
    on the way between the two airports."""
    try:
        a, b = route["_airports"][0], route["_airports"][-1]
        o, d = (a["lat"], a["lon"]), (b["lat"], b["lon"])
    except (KeyError, IndexError, TypeError):
        return False
    direct = km_between(o, d)
    return km_between(o, (lat, lon)) + km_between((lat, lon), d) <= direct * 1.3 + 80


def drone_extras(text):
    """Optional altitude/speed/ID fields a detector can append to its alert. Missing = None."""
    out = {}
    for key, val in DRONE_EXTRA_RE.findall(text or ""):
        if key == "ID":
            out["serial"] = val[:24]
            continue
        try:
            num = float(re.sub(r"[^0-9.-]", "", val))
        except ValueError:
            continue
        out[{"ALT": "alt_m", "AGL": "agl_m", "SPD": "speed_ms"}[key]] = num
    return out


def parse_aircraft(data, now=None):
    """adsb.fi (ADSBexchange v2 format) -> [{hex, call, reg, type, desc, heli, alt, ground, gs, track,
    lat, lon, squawk, emergency, seen}]. Skips aircraft without a recent position."""
    out = []
    for a in (data or {}).get("ac") or []:
        try:
            lat, lon = float(a["lat"]), float(a["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or float(a.get("seen_pos") or 0) > 60:
            continue
        alt = a.get("alt_baro")
        ground = alt == "ground"
        try:
            alt = 0 if ground else int(alt if alt is not None else a.get("alt_geom"))
        except (TypeError, ValueError):
            alt = None
        hexid = str(a.get("hex", "")).strip().lower()
        if not re.fullmatch(r"~?[0-9a-f]{6}", hexid):
            continue
        squawk = str(a.get("squawk") or "")
        emerg = a.get("emergency")
        emerg = EMERGENCY_SQUAWKS.get(squawk) or (emerg if emerg and emerg != "none" else None)
        track = a.get("track", a.get("true_heading"))
        out.append({
            "hex": hexid, "call": (a.get("flight") or "").strip() or a.get("r") or hexid.upper(),
            "reg": a.get("r") or "", "type": a.get("t") or "", "desc": a.get("desc") or "",
            "heli": a.get("category") == "A7" or str(a.get("t", "")).startswith(("H", "EC", "AS3", "R44", "R22")),
            "alt": alt, "ground": ground, "gs": a.get("gs"),
            "track": float(track) if isinstance(track, (int, float)) else None,
            "lat": lat, "lon": lon, "squawk": squawk, "emergency": emerg,
            "seen": float(a.get("seen") or 0)})
    return out


def point_in_rings(lat, lon, rings):
    """Ray-casting point-in-polygon. rings = [outer, hole, ...], each [(lat, lon), ...]."""
    inside = False
    for ring in rings:
        n = len(ring)
        j = n - 1
        for i in range(n):
            yi, xi = ring[i]
            yj, xj = ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
            j = i
    return inside


def geojson_rings(geom):
    """GeoJSON Polygon/MultiPolygon -> list of polygons, each a list of rings of (lat, lon)."""
    if not geom:
        return []
    polys = [geom["coordinates"]] if geom.get("type") == "Polygon" else geom.get("coordinates", []) \
        if geom.get("type") == "MultiPolygon" else []
    return [[[(c[1], c[0]) for c in ring] for ring in poly] for poly in polys]


def parse_tfr_xml(text):
    """One FAA TFR (XNOTAM XML) -> {name, type, lower, upper, start, end, polys} or None.
    Outline points come as e.g. 31.568N / 110.193W."""
    def coord(v):
        v = v.strip()
        num = float(v[:-1])
        return -num if v[-1] in "SW" else num
    def tags(t, src):
        return re.findall(rf"<{t}>([^<]*)</{t}>", src)
    merged = re.findall(r"<abdMergedArea>(.*?)</abdMergedArea>", text, re.S)
    polys = []
    for area in (merged or [text]):
        lats, lons = tags("geoLat", area), tags("geoLong", area)
        try:
            pts = [(coord(a), coord(b)) for a, b in zip(lats, lons)]
        except (ValueError, IndexError):
            continue
        if len(pts) >= 3:
            polys.append([pts])
    if not polys:
        return None
    first = lambda t: (tags(t, text) or [""])[0]
    def when(v):
        try:
            return datetime.datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            return None
    return {"name": first("txtLocalName") or first("txtName"), "lower": first("valDistVerLower"),
            "upper": first("valDistVerUpper"), "start": when(first("dateEffective")),
            "end": when(first("dateExpire")), "polys": polys}


def fetch_airspace(center, old=None, zones=True, tfr=True):
    """Download FAA zones around `center`: airport airspace (Class B/C/D/E2), the drone facility-map grid
    (max authorized altitudes near airports) and active temporary flight restrictions. Keeps what's still
    valid from `old`, so a refresh downloads as little as possible."""
    import urllib.parse
    out = dict(old or {})
    out["center"] = list(center)
    lat, lon = center
    dl, dn = AIRSPACE_SPAN
    box = f"{lon - dn},{lat - dl},{lon + dn},{lat + dl}"

    def get(url, params=None, timeout=30):
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def query(svc, where, fields):
        data = json.loads(get(FAA_ARCGIS.format(svc=svc), {
            "where": where, "geometry": box, "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outSR": 4326, "outFields": fields,
            "returnGeometry": "true", "geometryPrecision": 5, "maxAllowableOffset": 0.0003, "f": "geojson"}))
        if "error" in data:
            raise IOError(f"FAA {svc}: {data['error'].get('message', 'error')}")
        return data.get("features") or []

    if zones:
        types = ",".join(f"'{t}'" for t in AIRSPACE_CLASSES)
        out["zones"] = [{"name": f["properties"].get("NAME") or "", "type": f["properties"].get("LOCAL_TYPE"),
                         "lower": f["properties"].get("LOWER_VAL"), "upper": f["properties"].get("UPPER_VAL"),
                         "state": f["properties"].get("STATE") or "", "polys": geojson_rings(f.get("geometry"))}
                        for f in query("Class_Airspace", f"LOCAL_TYPE IN ({types})", "NAME,LOCAL_TYPE,LOWER_VAL,UPPER_VAL,STATE")]
        out["grid"] = [{"ceil": f["properties"].get("CEILING"), "apt": f["properties"].get("APT1_NAME") or "",
                        "polys": geojson_rings(f.get("geometry"))}
                       for f in query("FAA_UAS_FacilityMap_Data", "1=1", "CEILING,APT1_NAME")]
        out["zones_at"] = time.time()
    if tfr:
        listing = json.loads(get(TFR_LIST_URL))
        states = {z.get("state") for z in out.get("zones") or [] if z.get("state")}
        cache = dict(out.get("tfr") or {})
        keep = {}
        for item in listing:
            nid = str(item.get("notam_id") or "")
            if not re.fullmatch(r"\d/\d{1,5}", nid):
                continue
            if states and item.get("state") and item["state"] not in states:
                continue  # another state: skip without downloading its outline
            if nid in cache:
                keep[nid] = cache[nid]
                continue
            try:
                t = parse_tfr_xml(get(TFR_XML_URL.format(id=nid.replace("/", "_"))).decode("utf-8-sig", "replace"))
            except Exception:
                t = None
            if t and not any(km_between(center, pt) < 150 for poly in t["polys"] for pt in poly[0][::max(1, len(poly[0]) // 20)]):
                t = None  # nowhere near you
            keep[nid] = t
            time.sleep(0.5)  # be gentle with the FAA site
        out["tfr"] = keep
        out["tfr_at"] = time.time()
    return out


def scrub_log(lines, names=()):
    """Remove personal details from log lines before they're shared in a feedback report:
    node IDs, node names, coordinates, MAC addresses, push links and the Windows user name."""
    out = []
    names = sorted({n for n in names if n and len(n) >= 3}, key=len, reverse=True)
    for line in lines:
        line = re.sub(r"https?://\S+", "<link>", line)
        line = re.sub(r"![0-9a-fA-F]{8}", "!<node>", line)
        line = re.sub(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b", "<mac>", line)
        line = re.sub(r"-?\d{1,3}\.\d{3,}", "<pos>", line)
        line = re.sub(r"(?i)([a-z]:\\users\\)[^\\\s]+", r"\1<user>", line)
        for n in names:
            line = line.replace(n, "<name>")
        out.append(line)
    return out


def feedback_link(title, body, limit=None):
    """A GitHub 'new issue' link with the report filled in, shortened to fit if needed.
    Returns (url, was_shortened)."""
    import urllib.parse
    limit = limit or FEEDBACK_URL_MAX
    base = REPO_URL + "/issues/new?"
    q = lambda b: base + urllib.parse.urlencode({"title": title, "body": b})
    url = q(body)
    if len(url) <= limit:
        return url, False
    note = "\n\n_(Report shortened to fit the link. Use **Copy report** in the app for the full text.)_"
    lo, hi = 0, len(body)
    while lo < hi:  # longest prefix that still fits
        mid = (lo + hi + 1) // 2
        if len(q(body[:mid] + note)) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return q(body[:lo] + note), True


def prune_history(history, now=None):
    """Forget nodes unheard for HISTORY_DAYS, then the oldest beyond MAX_HISTORY. Returns how many went."""
    now = now or time.time()
    old = [nid for nid, h in history.items() if now - (h.get("last") or 0) > HISTORY_DAYS * 86400]
    for nid in old:
        del history[nid]
    extra = sorted(history, key=lambda nid: history[nid].get("last") or 0)[:max(0, len(history) - MAX_HISTORY)]
    for nid in extra:
        del history[nid]
    return len(old) + len(extra)


def title_bar_visible(x, y, w, root=None):
    """Is the window's title bar on any monitor? A saved spot on a monitor that's now unplugged or off
    would otherwise reopen the widget where nobody can reach it."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes as wt
            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
            user32.MonitorFromPoint.restype = wt.HANDLE
            return any(user32.MonitorFromPoint(wt.POINT(px, y + 10), 0)  # 0 = null when off every monitor
                       for px in (x + 40, x + w // 2, x + w - 40))
        except Exception:
            return True
    if root is not None and sys.platform.startswith("linux"):  # X11: one virtual screen spans all monitors
        return 0 <= x + w // 2 < root.winfo_screenwidth() and 0 <= y < root.winfo_screenheight()
    return True  # macOS keeps windows reachable itself


def claim_instance(port):
    """Make sure only one copy runs. Returns a listening socket if we're first, None if another copy
    answered (it's been asked to show itself), or False if something else holds the port (run anyway)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(4)
        return s
    except OSError:
        s.close()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as c:
            c.settimeout(2)
            c.sendall(b"show\n")
            if c.recv(64).startswith(b"centralfinitecurve"):
                return None
    except OSError:
        pass
    log.warning("Port %d is in use by another program; running without the single-copy check", port)
    return False


def save_json(path, data):
    # write to a temp file then swap it in, so a crash or power cut can't leave half a file
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def save_cfg(cfg):
    save_json(CFG_PATH, cfg)


WIN_DEFAULT = "Windows default"


def tray_image(color, unread):
    """A little radio-tower icon in the status colour, with a dot when messages are unread."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=PANEL, outline=color, width=5)
    d.line((32, 24, 32, 50), fill=color, width=5)
    d.ellipse((26, 16, 38, 28), fill=color)
    d.arc((14, 6, 50, 42), 200, 340, fill=color, width=4)
    if unread:
        d.ellipse((40, 0, 64, 24), fill=ACCENT, outline=BG, width=3)
    return img


def output_devices():
    """DirectSound output names (full names, and it resamples for us)."""
    if not sd:
        return {}
    try:
        apis = sd.query_hostapis()
        return {d["name"]: i for i, d in enumerate(sd.query_devices())
                if d["max_output_channels"] > 0 and "DirectSound" in apis[d["hostapi"]]["name"]
                and d["name"] != "Primary Sound Driver"}
    except Exception:
        log.debug("Listing sound devices failed", exc_info=True)
        return {}


def cache_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "CentralFiniteCurve", "tiles")


# map budget: this is a widget, so the map gets a small fixed share of memory and sleeps when idle
TILES_IN_MEMORY = 150     # decoded tiles kept in RAM (~256 KB each, so ~38 MB at most)
PREVIEWS_IN_MEMORY = 40   # upscaled stand-ins shown while a zoom level loads
TILE_LOADERS = 4          # download threads (the library starts 25)
PRECACHE_RINGS = 1        # extra rings of tiles fetched around the view (library: 8)
DISK_CACHE_MB = 150       # downloaded tiles kept on disk so they aren't fetched twice
DISK_CACHE_DAYS = 30      # then re-fetched, so map changes still come through
REPO_URL = "https://github.com/DaGromflomite/Central-Finite-Curve"
FEEDBACK_KINDS = (("Bug", "Something's broken"), ("Idea", "Idea or request"), ("Works", "It worked!"))
FEEDBACK_URL_MAX = 7500  # GitHub refuses very long pre-filled issue links
USER_AGENT = "CentralFiniteCurve/0.1 (+https://github.com/)"  # tile servers ask apps to identify themselves

if tkintermapview:
    from PIL import ImageTk

    class SmoothMapView(tkintermapview.TkinterMapView):
        """Map view that never flashes white: dark placeholders, and while a zoom level loads
        each tile shows an upscaled piece of an already-loaded lower-zoom tile.

        Also replaces the library's tile engine, which polls with 25 threads every 10 ms and can hold
        10,000 decoded tiles (~2.5 GB for satellite): here a few threads sleep until there's work,
        memory is a small LRU cache, downloads are kept on disk, and everything stops while hidden."""

        def __init__(self, *args, on_zoom=None, **kwargs):
            self.on_zoom = on_zoom
            self.previews = {}
            self.pin_cache = {}
            # set before super().__init__, which starts the threads that use them
            self.tiles = collections.OrderedDict()  # (zoom, x, y) -> PhotoImage, oldest first
            self.tiles_lock = threading.Lock()
            self.tiles_gen = 0                      # bumped on style change so late downloads are dropped
            self.work = threading.Event()           # set when tiles are queued
            self.awake = threading.Event()          # clear while the map isn't on screen
            self.awake.set()
            self.loader_slots = TILE_LOADERS
            self.retry_at = 0                       # back off while offline
            self.disk = cache_dir()
            self.disk_writes = 0
            self.http = requests.Session()
            self.http.headers["User-Agent"] = USER_AGENT
            super().__init__(*args, **kwargs)
            dark = Image.new("RGB", (self.tile_size, self.tile_size), PANEL)
            self.empty_tile_image = ImageTk.PhotoImage(dark)
            self.not_loaded_tile_image = ImageTk.PhotoImage(dark)
            self.canvas.configure(bg=PANEL)
            threading.Thread(target=self.prune_disk, daemon=True).start()

        def set_awake(self, on):
            if on:
                self.awake.set()
                self.work.set()
            else:
                self.awake.clear()

        # ---- memory cache (the library's string keys collide, e.g. z1/x12/y3 and z11/x2/y3) ----
        def get_tile_image_from_cache(self, zoom, x, y):
            with self.tiles_lock:
                img = self.tiles.get((zoom, x, y))
                if img is None:
                    return False
                self.tiles.move_to_end((zoom, x, y))
                return img

        def keep_tile(self, key, img, gen):
            with self.tiles_lock:
                if gen != self.tiles_gen:
                    return
                self.tiles[key] = img
                self.tiles.move_to_end(key)
                while len(self.tiles) > TILES_IN_MEMORY:
                    self.tiles.popitem(last=False)

        # ---- downloads, through the disk cache ----
        def fetch(self, template, zoom, x, y):
            """Tile bytes for one layer, or None if the server has no tile there. Raises when offline."""
            url = template.replace("{x}", str(x)).replace("{y}", str(y)).replace("{z}", str(zoom))
            path = os.path.join(self.disk, hashlib.sha1(url.encode()).hexdigest())
            try:
                if time.time() - os.path.getmtime(path) < DISK_CACHE_DAYS * 86400:
                    with open(path, "rb") as f:
                        return f.read()
            except OSError:
                pass
            r = self.http.get(url, timeout=15)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            try:
                os.makedirs(self.disk, exist_ok=True)
                with open(path + ".tmp", "wb") as f:
                    f.write(r.content)
                os.replace(path + ".tmp", path)
                self.disk_writes += 1
                if self.disk_writes % 200 == 0:
                    threading.Thread(target=self.prune_disk, daemon=True).start()
            except OSError:
                pass  # no disk cache is fine; we still have the tile
            return r.content

        def prune_disk(self):
            # oldest files go first until the cache is back under its size limit
            try:
                files = [(e.stat().st_mtime, e.stat().st_size, e.path) for e in os.scandir(self.disk) if e.is_file()]
            except OSError:
                return
            total = sum(f[1] for f in files)
            for mtime, size, path in sorted(files):
                if total <= DISK_CACHE_MB * 2 ** 20:
                    break
                try:
                    os.remove(path)
                    total -= size
                except OSError:
                    pass

        def request_image(self, zoom, x, y, db_cursor=None):
            """Returns a tile image, the empty tile if none exists there, or None to retry later."""
            if time.time() < self.retry_at:
                return None
            gen = self.tiles_gen
            try:
                data = self.fetch(self.tile_server, zoom, x, y)
                if data is None:
                    self.keep_tile((zoom, x, y), self.empty_tile_image, gen)
                    return self.empty_tile_image
                image = Image.open(io.BytesIO(data))
                if self.overlay_tile_server:
                    over = self.fetch(self.overlay_tile_server, zoom, x, y)
                    if over is not None:
                        image = image.convert("RGBA")
                        over = Image.open(io.BytesIO(over)).convert("RGBA")
                        if over.size != image.size:
                            over = over.resize(image.size, Image.LANCZOS)
                        image.paste(over, (0, 0), over)
                if not self.running:
                    return self.empty_tile_image
                photo = ImageTk.PhotoImage(image)
            except requests.RequestException as e:
                warn_once("tiles", "Map tiles unreachable (%s); backing off", e)
                self.retry_at = time.time() + 15  # offline or server trouble: don't hammer it
                return None
            except Exception:
                warn_once("bad-tile", "Unreadable map tile %s/%s/%s", zoom, x, y, exc_info=True)
                return self.empty_tile_image  # corrupt tile: show blank rather than retry forever
            self.keep_tile((zoom, x, y), photo, gen)
            return photo

        # ---- threads: sleep until there's work instead of polling ----
        def load_images_background(self):
            with self.tiles_lock:
                self.loader_slots -= 1
                if self.loader_slots < 0:
                    return  # the library starts 25 of these; the rest exit straight away
            while self.running:
                if not self.awake.wait(2):
                    continue
                try:
                    (zoom, x, y), canvas_tile = self.image_load_queue_tasks.pop()
                except IndexError:
                    self.work.wait(2)  # timeout covers the rare missed wake-up
                    self.work.clear()
                    continue
                if zoom != round(self.zoom):
                    continue  # user zoomed away before we got to it
                image = self.get_tile_image_from_cache(zoom, x, y)
                if image is False:
                    image = self.request_image(zoom, x, y)
                    if image is None:
                        self.image_load_queue_tasks.insert(0, ((zoom, x, y), canvas_tile))
                        time.sleep(max(0.5, self.retry_at - time.time()))
                        continue
                self.image_load_queue_results.append(((zoom, x, y), canvas_tile, image))

        def update_canvas_tile_images(self):
            busy = bool(self.image_load_queue_results or self.image_load_queue_tasks)
            while self.image_load_queue_results and self.running:
                (zoom, x, y), canvas_tile, image = self.image_load_queue_results.pop(0)
                if zoom == round(self.zoom):
                    canvas_tile.set_image(image)
            if self.running:  # quick while tiles arrive, then a slow idle check
                self.after(20 if busy else (250 if self.awake.is_set() else 1000), self.update_canvas_tile_images)

        def pre_cache(self):
            done = None
            while self.running:
                time.sleep(0.5)
                pos, zoom = self.pre_cache_position, round(self.zoom)
                if not self.awake.is_set() or pos is None or (pos, zoom) == done or self.image_load_queue_tasks:
                    continue  # wait until the visible tiles are in before fetching extras
                done = (pos, zoom)
                r = PRECACHE_RINGS
                for x in range(pos[0] - r, pos[0] + r + 1):
                    for y in range(pos[1] - r, pos[1] + r + 1):
                        if self.running and self.get_tile_image_from_cache(zoom, x, y) is False:
                            self.request_image(zoom, x, y)

        def pin(self, fill, outline):
            """Half-size version of the library's pin: drawn 4x large then shrunk for smooth edges."""
            key = (fill, outline)
            if key not in self.pin_cache:
                s, w, h = 4, 16, 25
                img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.polygon([(1 * s, 9 * s), (8 * s, 25 * s - 1), (15 * s, 9 * s)], fill=outline)  # point
                d.ellipse((0, 0, w * s - 1, w * s - 1), fill=outline)                            # ring
                d.ellipse((3 * s, 3 * s, (w - 3) * s - 1, (w - 3) * s - 1), fill=fill)         # dot
                self.pin_cache[key] = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
            return self.pin_cache[key]

        def mouse_zoom(self, event):
            # one wheel notch = exactly one zoom level (Windows otherwise jumps ~1.2 per notch)
            step = 1 if (getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4) else -1
            self.set_zoom(round(self.zoom) + step, relative_pointer_x=event.x / self.width,
                          relative_pointer_y=event.y / self.height)

        def set_zoom(self, zoom, relative_pointer_x=0.5, relative_pointer_y=0.5):
            super().set_zoom(zoom, relative_pointer_x, relative_pointer_y)
            if self.on_zoom:
                self.on_zoom(round(self.zoom))

        def set_tile_server(self, *args, **kwargs):
            self.previews = {}
            with self.tiles_lock:
                self.tiles.clear()
                self.tiles_gen += 1
            super().set_tile_server(*args, **kwargs)

        # every redraw can queue tiles: wake the loaders
        def draw_initial_array(self):
            super().draw_initial_array()
            self.fill_previews()
            self.work.set()

        def draw_zoom(self):
            super().draw_zoom()
            self.fill_previews()
            self.work.set()

        def draw_move(self, called_after_zoom=False):
            super().draw_move(called_after_zoom)
            self.fill_previews()
            self.work.set()

        def preview_for(self, zoom, x, y):
            # look up to 3 levels out for a loaded tile covering this one, crop and enlarge it
            for k in range(1, 4):
                key = (zoom, x, y, k)
                if key in self.previews:
                    return self.previews[key]
                with self.tiles_lock:
                    parent = self.tiles.get((zoom - k, x >> k, y >> k))
                if parent is None or parent is self.empty_tile_image:
                    continue
                try:
                    src = ImageTk.getimage(parent)
                except Exception:
                    continue
                part = self.tile_size >> k
                ox, oy = (x % (1 << k)) * part, (y % (1 << k)) * part
                img = src.crop((ox, oy, ox + part, oy + part)).resize(
                    (self.tile_size, self.tile_size), Image.BILINEAR)
                photo = ImageTk.PhotoImage(img)
                if len(self.previews) > PREVIEWS_IN_MEMORY:
                    self.previews.clear()
                self.previews[key] = photo
                return photo
            return None

        def fill_previews(self):
            zoom = round(self.zoom)
            for column in self.canvas_tile_array or []:
                for tile in column:
                    if tile.image is self.not_loaded_tile_image:
                        p = self.preview_for(zoom, *tile.tile_name_position)
                        if p is not None:
                            tile.set_image(p)


def windows_sounds():
    media = r"C:\Windows\Media"
    try:
        names = sorted(f for f in os.listdir(media) if f.lower().endswith(".wav"))
    except OSError:
        names = []
    return {n[:-4]: os.path.join(media, n) for n in names}


ALERT_GAP = 3  # seconds: a busy channel gets one alert per burst, not one per message
_alert = {"last": 0.0, "lock": threading.Lock()}


def alert(cfg):
    """Play the new-message sound without blocking the window (starting an audio stream takes ~0.2 s)."""
    now = time.time()
    with _alert["lock"]:
        if now - _alert["last"] < ALERT_GAP:
            return
        _alert["last"] = now
    threading.Thread(target=play_alert, args=(cfg,), daemon=True, name="alert").start()


class PhonePush:
    """Forwards alerts to a phone through ntfy (config "push_url" = server + topic). A drone hovering nearby
    sends alert after alert, so bursts are merged: at most one push per PUSH_GAP seconds, and anything
    arriving in between rides along in the next one. That keeps ntfy.sh's daily limit out of reach."""

    def __init__(self, url, send=None):
        self.url = url
        self.waiting = []  # (time, sender, text, is_drone)
        self.last = 0
        self.lock = threading.Lock()
        self.send = send  # for tests; None = post to ntfy

    def add(self, sender, text, drone=True):
        with self.lock:
            self.waiting.append((time.time(), sender, text, drone))
        self.flush()

    def flush(self, now=None):
        now = now or time.time()
        with self.lock:
            if not self.waiting or now - self.last < PUSH_GAP:
                return
            batch, self.waiting, self.last = self.waiting, [], now
        lines = [f"{datetime.datetime.fromtimestamp(t).strftime('%H:%M')} {who}: {text}"
                 for t, who, text, _ in batch[:5]]
        if len(batch) > 5:
            lines.append(f"(+{len(batch) - 5} more, see Central Finite Curve)")
        drones = sum(1 for *_, d in batch if d)
        if drones == len(batch):
            title = "Drone alert" if drones == 1 else f"{drones} drone alerts"
        elif drones:
            title = f"{drones} drone alert{'s' if drones != 1 else ''} + {len(batch) - drones} message" \
                    + ("s" if len(batch) - drones != 1 else "")
        else:
            title = "New message" if len(batch) == 1 else f"{len(batch)} new messages"
        body = "\n".join(lines)
        if self.send:
            threading.Thread(target=self.send, args=(title, body), daemon=True, name="push").start()
        else:
            threading.Thread(target=self.post, args=(title, body, bool(drones)), daemon=True, name="push").start()

    def post(self, title, body, urgent=True):
        """Send one notification. Returns (ok, message) so Settings can show the test result."""
        headers = {"Title": title, "User-Agent": "CentralFiniteCurve/1.0"}
        if urgent:
            headers.update({"Priority": "high", "Tags": "warning"})
        req = urllib.request.Request(self.url, data=body.encode("utf-8"), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
            log.info("Pushed '%s' to phone", title)
            return True, "Sent"
        except Exception as e:
            warn_once("push", "Phone push failed: %s", e)
            return False, str(e)


def ntfy_url(text):
    """Turn what the user typed into a push URL: a bare topic means the free ntfy.sh server."""
    text = (text or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.I):
        text = "https://ntfy.sh/" + text.lstrip("/")
    return text if re.fullmatch(r"https?://[^\s/]+/[A-Za-z0-9_-]{1,64}", text) else None


def play_alert(cfg):
    wav = cfg.get("sound_file", DEFAULT_SOUND)
    dev = cfg.get("sound_device", WIN_DEFAULT)
    if dev != WIN_DEFAULT and sd and os.path.exists(wav):
        idx = output_devices().get(dev)
        if idx is not None:
            try:
                data, rate = sf.read(wav, dtype="float32")
                sd.play(data, rate, device=idx)
                return
            except Exception:
                warn_once("sound", "Playing alert on %r failed; using Windows default", dev, exc_info=True)
    if winsound:  # Windows
        if os.path.exists(wav):
            winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.MessageBeep(winsound.MB_OK)
    elif sd and sf and os.path.exists(wav):  # macOS / Linux: default output device
        try:
            data, rate = sf.read(wav, dtype="float32")
            sd.play(data, rate)
        except Exception:
            warn_once("sound", "Alert sound failed", exc_info=True)


def ago(ts):
    if not ts:
        return "-"
    s = int(time.time() - ts)
    if s < 1:
        return "now"  # also covers nodes whose clock runs ahead (their timestamps are in the future)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def when(ts):
    if not ts:
        return None
    a = ago(ts)
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") + ("  (just now)" if a == "now" else f"  ({a} ago)")


def span(secs):
    if not secs:
        return None
    d, rem = divmod(int(secs), 86400)
    return (f"{d}d " if d else "") + f"{rem // 3600}h {rem % 3600 // 60}m"


DEMO_CENTER = (40.4406, -79.9959)  # demo area when no home location is set


DEMO_EXTRA = 0  # --demo-nodes=N: this many extra plain nodes, to see how it copes with a busy city mesh


def enable_demo(folder=None, extra_nodes=0):
    """Switch to made-up nodes with separate files, so a demo never touches real history, messages or settings."""
    global DEMO, CFG_PATH, LOG_PATH, HIST_PATH, AIRSPACE_PATH, FLIGHTS_DIR, DEBUG_LOG, HEALTH_SECS, DEMO_EXTRA
    DEMO, DEMO_EXTRA = True, extra_nodes
    folder = folder or os.path.join(HERE, "demo")
    os.makedirs(folder, exist_ok=True)
    real = load_cfg()
    CFG_PATH = os.path.join(folder, "demo-config.json")
    if not os.path.exists(CFG_PATH):  # borrow the look and home location, not the connection
        save_json(CFG_PATH, {k: real[k] for k in ("geometry", "map_style", "home_lat", "home_lon", "sound",
                                                  "sound_device", "sound_file", "active_mins", "online_nodes")
                             if k in real})
    LOG_PATH = os.path.join(folder, "demo-messages.jsonl")
    HIST_PATH = os.path.join(folder, "demo-history.json")
    AIRSPACE_PATH = os.path.join(folder, "demo-airspace.json")
    FLIGHTS_DIR = os.path.join(folder, "flights")  # demo flights and reports never mix with real ones
    DEBUG_LOG = os.path.join(folder, "demo.log")
    for path in (LOG_PATH, HIST_PATH):  # every demo starts with an empty mesh
        try:
            os.remove(path)
        except OSError:
            pass
    HEALTH_SECS = 60


def offset(pos, north_km, east_km):
    return (pos[0] + north_km / 111.32,
            pos[1] + east_km / (111.32 * max(math.cos(math.radians(pos[0])), 0.01)))


class DemoInterface:
    def sendData(self, data, destinationId="^all", portNum=None, wantResponse=False, channelIndex=0,
                 hopLimit=None, **kw):
        return type("Sent", (), {"id": self.rng.randint(1, 2 ** 31)})()

    """Stands in for a radio in --demo mode, publishing packets the way the meshtastic library does.

    The made-up mesh is built to hit awkward cases: hilltop repeaters, walkers/cyclist/car that move,
    nodes that never share a location, a blurred position, a broken GPS reporting 0,0, a node that never
    sends its name, emoji/accented/very long/duplicate names, late joiners, a node that goes quiet,
    an MQTT node far away, chat with odd text, DMs, and ACKs or failures for what you send."""

    def __init__(self, center, seed=None, extra=0):
        self.rng = random.Random(seed)
        self.start = time.time()
        self.running = True
        self.my_id = "!dec0de01"
        self.nodes = {self.my_id: {"num": 0xDEC0DE01, "lastHeard": time.time(), "user": {
            "id": self.my_id, "longName": "Demo Base", "shortName": "DEMO", "hwModel": "HELTEC_V4",
            "role": "CLIENT_BASE"}, "deviceMetrics": {"batteryLevel": 101, "voltage": 4.3, "channelUtilization": 0.0}}}
        r = self.rng.uniform
        c = center
        # name, short, hw, role, start position (or None), speed m/s, extras
        roster = [
            ("Ridge Repeater", "RDGE", "RAK4631", "ROUTER", offset(c, 8, 9), 0, {"env": True, "snr": 7}),
            ("Hilltop Late", "HILL", "STATION_G2", "ROUTER_LATE", offset(c, -6, -7), 0, {"snr": 4}),
            ("County Tower ⛰", "TWR", "RAK4631", "ROUTER", offset(c, 25, 2), 0, {"hops": 1, "snr": -3}),
            ("Hidden Repeater", "HIDE", "RAK4631", "ROUTER", None, 0, {"snr": 2}),
            ("Kitchen Node", "KTCH", "T_ECHO", "CLIENT", offset(c, 0.5, 0.6), 0, {"snr": 10}),
            ("Kitchen Node", "KTC2", "HELTEC_V3", "CLIENT", offset(c, -1.2, 2.1), 0, {}),
            ("Barn Sensor", "BARN", "RAK4631", "CLIENT_MUTE", offset(c, 2, -1), 0, {"env": True}),
            ("Sam's T-Deck", "SAM", "T_DECK", "CLIENT", offset(c, 1, 1), 1.4, {}),
            ("Mobile 1 (car)", "CAR1", "TRACKER_T1000_E", "TRACKER", offset(c, -3, 4), 15, {"every": 8}),
            ("Bike Ride 🚲", "BIKE", "HELTEC_V4", "CLIENT", offset(c, 3, -3), 5, {"every": 10}),
            ("Quiet Neighbour", "QN", "T_ECHO", "CLIENT", None, 0, {}),
            ("No GPS Node", "NOGP", "HELTEC_V3", "CLIENT", None, 0, {}),
            ("", "x7q2", "UNSET", "CLIENT", None, 0, {}),
            (None, None, None, "CLIENT", offset(c, -2, -2), 0, {}),  # never sends its name
            ("Ñandú Ştëfan ✓ 日本", "ÑŞ", "HELTEC_V3", "CLIENT", offset(c, 4, 0), 0, {}),
            ("The Extremely Long Node Name Test 12345", "LONG", "T_BEAM", "CLIENT", offset(c, -4, 1), 0, {}),
            ("🌊🌊🌊", "🌊", "HELTEC_V4", "CLIENT", offset(c, 1.5, -4), 0, {}),
            ("Blurred Position", "BLUR", "T_ECHO", "CLIENT", offset(c, 5, 5), 0, {"bits": 13}),
            ("Null Island Bug", "NULL", "HELTEC_V3", "CLIENT", (0.0, 0.0), 0, {}),
            ("Late Joiner", "LATE", "HELTEC_V4", "CLIENT", offset(c, -5, -1), 0, {"join": 90}),
            ("Goes Quiet", "GONE", "T_ECHO", "CLIENT", offset(c, 0, -5), 0, {"leave": 150}),
            ("Far Away (MQTT)", "FAR", "RAK4631", "CLIENT", offset(c, 60, -50), 0, {"mqtt": True, "hops": 4}),
            ("Chatty Cathy", "CHAT", "T_DECK", "CLIENT", offset(c, -0.8, -0.4), 0, {"chat": 0.4}),
        ]
        for i in range(extra):  # plain filler nodes for scale testing, ~70% sharing a location
            pos = offset(c, r(-25, 25), r(-25, 25)) if self.rng.random() < 0.7 else None
            role = self.rng.choice(["CLIENT"] * 8 + ["CLIENT_MUTE", "ROUTER", "ROUTER_LATE", "CLIENT_BASE"])
            roster.append((f"Node {i + 1:03d}", f"N{i + 1:03d}"[-4:], "HELTEC_V3", role, pos, 0,
                           {"every": r(30, 120), "hops": self.rng.choice([0, 0, 1, 1, 2, 3]), "chat": 0.02}))
        self.sim = []
        for i, (name, short, hw, role, pos, speed, x) in enumerate(roster):
            num = 0x0DE30000 + i * 0x101 + 1
            nid = f"!{num:08x}"
            s = {"id": nid, "num": num, "name": name, "short": short, "hw": hw, "role": role, "pos": pos,
                 "speed": speed, "heading": r(0, 360), "next": self.start + r(1, 15) + x.get("join", 0),
                 "every": x.get("every", r(15, 45)), "snr": x.get("snr", r(-8, 9)), "hops": x.get("hops", 0),
                 "mqtt": x.get("mqtt", False), "chat": x.get("chat", 0.08), "bits": x.get("bits", 32),
                 "env": x.get("env", False), "join": x.get("join", 0), "leave": x.get("leave"),
                 "home": pos, "batt": self.rng.randint(30, 101), "named": False}
            self.sim.append(s)
        self.chatter = ["anyone on?", "copy, loud and clear", "testing 1 2 3", "heading out on the trail",
                        "battery low, back later", "73!", "signal check from the ridge",
                        "line one\nline two after a newline", "emoji test 📡🔋✅",
                        "a very long message " + "that goes on and on " * 8]
        threading.Thread(target=self.run, daemon=True, name="demo-mesh").start()

    # ---- what the widget calls on a real interface ----
    def getMyNodeInfo(self):
        return self.nodes[self.my_id]

    def sendText(self, text, destinationId="^all", wantAck=False, channelIndex=0, **kw):
        pid = self.rng.randint(1, 2 ** 31)
        target = next((s for s in self.sim if s["id"] == destinationId), None)
        if wantAck and target:
            def answer():
                ok = self.alive(target) and self.rng.random() < 0.8
                self.emit(target, {"portnum": "ROUTING_APP", "requestId": pid,
                                   "routing": {"errorReason": "NONE" if ok else "MAX_RETRANSMIT"}}, to_me=True)
                if ok and self.rng.random() < 0.5:
                    time.sleep(self.rng.uniform(2, 5))
                    self.emit(target, {"portnum": "TEXT_MESSAGE_APP", "text": f"got it: {text[:30]}"}, to_me=True)
            threading.Timer(self.rng.uniform(1, 4), answer).start()
        return type("Sent", (), {"id": pid})()

    def close(self):
        self.running = False

    # ---- the pretend mesh ----
    def alive(self, s):
        age = time.time() - self.start
        return age >= s["join"] and (s["leave"] is None or age < s["leave"])

    def emit(self, s, decoded, to_me=False):
        if not self.running:
            return
        now = time.time()
        snr = round(s["snr"] + self.rng.uniform(-2, 2), 2)
        node = self.nodes.setdefault(s["id"], {"num": s["num"]})
        node.update(lastHeard=now, snr=snr, hopsAway=s["hops"], viaMqtt=s["mqtt"] or None)
        packet = {"from": s["num"], "fromId": s["id"], "id": self.rng.randint(1, 2 ** 31), "rxTime": int(now),
                  "to": 0xDEC0DE01 if to_me else 0xFFFFFFFF, "toId": self.my_id if to_me else "^all",
                  "rxSnr": snr, "rxRssi": int(-120 + (snr + 20) * 2.5), "hopStart": 3, "hopLimit": 3 - s["hops"],
                  "decoded": decoded}
        if s["mqtt"]:
            packet["viaMqtt"] = True
        pub.sendMessage("meshtastic.receive", packet=packet, interface=self)

    def move(self, s, dt):
        if not s["speed"] or s["pos"] is None:
            return
        s["heading"] = (s["heading"] + self.rng.uniform(-25, 25)) % 360
        if km_between(s["pos"], s["home"]) > 6:  # turn back towards where it started
            s["heading"] = math.degrees(math.atan2(s["home"][1] - s["pos"][1], s["home"][0] - s["pos"][0])) % 360
        km = s["speed"] * dt / 1000
        h = math.radians(s["heading"])
        s["pos"] = offset(s["pos"], km * math.cos(h), km * math.sin(h))

    def beacon(self, s):
        roll = self.rng.random()
        self.nodes.setdefault(s["id"], {"num": s["num"]})  # a node can send data before its name
        if not s["named"] and s["name"] is not None:
            s["named"] = True
            user = {"id": s["id"], "longName": s["name"], "shortName": s["short"], "hwModel": s["hw"], "role": s["role"]}
            self.nodes.setdefault(s["id"], {"num": s["num"]})["user"] = user
            self.emit(s, {"portnum": "NODEINFO_APP", "user": user})
        elif s["pos"] is not None and (s["speed"] or roll < 0.4):
            lat, lon = s["pos"]
            if s["bits"] < 32:  # owner blurs their location: snap to a grid of that size
                step = precision_m(s["bits"]) / 111320
                lat, lon = round(lat / step) * step, round(lon / step) * step
            pos = {"latitude": lat, "longitude": lon, "altitude": self.rng.randint(250, 520),
                   "time": int(time.time()), "precisionBits": s["bits"]}
            self.nodes[s["id"]]["position"] = pos
            self.emit(s, {"portnum": "POSITION_APP", "position": pos})
        elif roll < 0.4 + s["chat"]:
            self.emit(s, {"portnum": "TEXT_MESSAGE_APP", "text": self.rng.choice(self.chatter)},
                      to_me=self.rng.random() < 0.25)
        else:
            s["batt"] = max(5, s["batt"] - self.rng.randint(0, 2)) if s["batt"] <= 100 else 101
            dm = {"batteryLevel": s["batt"], "voltage": round(3.3 + min(s["batt"], 100) / 100, 2),
                  "channelUtilization": round(self.rng.uniform(1, 25), 1),
                  "airUtilTx": round(self.rng.uniform(0, 3), 2), "uptimeSeconds": int(time.time() - self.start) + 3600}
            self.nodes[s["id"]]["deviceMetrics"] = dm
            tele = {"deviceMetrics": dm}
            if s["env"]:
                env = {"temperature": round(self.rng.uniform(5, 20), 1),
                       "relativeHumidity": round(self.rng.uniform(40, 90)), "barometricPressure": 1013.2}
                self.nodes[s["id"]]["environmentMetrics"] = env
                tele = {"environmentMetrics": env}
            self.emit(s, {"portnum": "TELEMETRY_APP", "telemetry": tele})

    def run(self):
        last = time.time()
        while self.running:
            time.sleep(0.5)
            now = time.time()
            for s in self.sim:
                self.move(s, now - last)
                if self.alive(s) and now >= s["next"]:
                    s["next"] = now + s["every"] * self.rng.uniform(0.5, 1.5)
                    try:
                        self.beacon(s)
                    except Exception:
                        log.exception("Demo node %s failed", s["id"])
            last = now


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_cfg()
        self.q = queue.Queue()
        self.iface = None
        self.connecting = False
        self.paused = False
        self.last_try = 0
        self.port_used = None  # the COM port we actually opened (can differ from config after a fallback)
        self.clock_at = 0      # when we last set the radio's clock
        self.untimed = {}      # node id -> name, for nodes the radio knows but never timestamped
        self.radio_nodes = []  # [(id, name)] the radio knows, newest first (top of the Message-to list)
        self.dest_names = {}   # node id -> plain name, for every entry in the Message-to list
        url = self.cfg.get("push_url")
        self.push = PhonePush(url) if url and not DEMO else None  # messages on push_channel go to the phone
        self.rssi = {}        # node id -> last RSSI we measured
        self.heard = {}       # node id -> (time, snr) of the last packet we got from it
        self.pending = {}     # packet id -> (recipient name, time sent), waiting for an ACK
        self.pending_nid = {} # packet id -> recipient node id, for follow-ups such as fetching a missing key
        self.drone_seen = {}  # drone MAC -> latest ping, pilot, detectors that heard it (see note_drone_ping)
        self.ping_log = []    # every drone ping with the detector that heard it, for reports (bounded)
        self.conflicts = []   # drone/aircraft near-misses we warned about, for reports (bounded)
        self.conflict_seen = {}  # (mac, hex) -> last warning time
        self.dm_markers = {}  # drone MAC -> marker on the main map
        self.airspace = load_json(AIRSPACE_PATH) or {}  # cached FAA zones near you (see fetch_airspace)
        self.as_shapes = []   # polygons drawn on the main map
        self.as_busy = False
        self.as_next = 0
        self.as_seen = {}     # (mac, zone) -> last warning time
        self.airspace_hits = []  # drones found inside a zone, for reports (bounded)
        self.aircraft = {}    # hex -> aircraft dict from adsb.fi (see parse_aircraft)
        self.ac_markers = {}  # (map id, hex) -> marker
        self.ac_icons = {}    # (heading bucket, colour) -> PhotoImage
        self.ac_next = 0      # earliest time for the next aircraft fetch
        self.ac_busy = False
        self.line_seq = 0     # message panels: each shown message gets a tag "m<seq>"...
        self.line_msgs = {}   # ...that maps back to its record, for the right-click menu
        self.key_asked = {}   # node id -> when we last asked it for its node info (public key)
        self.history = load_json(HIST_PATH)  # node id -> first/last seen, count, best signal
        self.hist_dirty = False
        self.seen_ids = {}    # packet ids already shown (a dict keeps them in order so the oldest can go)
        self.unread = 0
        self.health_at, self.health_cpu = time.time(), time.process_time()
        self.refresh_job = None
        self.refresh_cost = 0.0  # seconds the last batched refresh took
        self._last_search = 0    # when the Search button last pinged the mesh (rate limit)
        self._auto_after = None  # pending background-discovery timer id
        self.row_cache = {}       # list widget -> {row id: (tags, values)} as last shown

        root.title("Central Finite Curve — DEMO" if DEMO else "Central Finite Curve")
        try:
            self._icon_img = tk.PhotoImage(file=asset_path("icon_256.png"))
            root.iconphoto(True, self._icon_img)   # window + taskbar icon
        except Exception:
            pass
        root.geometry(self.cfg["geometry"])
        g = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", str(self.cfg["geometry"]))
        if g and not title_bar_visible(int(g[3]), int(g[4]), int(g[1]), root):
            log.warning("Saved window spot %s is off every screen (monitor unplugged?); moving it back",
                        self.cfg["geometry"])
            root.geometry(f"{g[1]}x{g[2]}+100+100")
        gone = prune_history(self.history)
        cut = trim_archive(LOG_PATH, self.archive_days())
        self.archive_trimmed = time.time()
        if gone or cut:
            self.hist_dirty = bool(gone)
            log.info("Housekeeping: forgot %d old nodes, trimmed %d old messages", gone, cut)
        root.configure(bg=BG)
        root.attributes("-topmost", self.cfg["on_top"])
        self.tray = None
        self.tray_color = BAD
        if pystray:
            root.attributes("-toolwindow", True)  # no taskbar button; the tray icon replaces it
            root.protocol("WM_DELETE_WINDOW", self.hide)
        else:
            root.protocol("WM_DELETE_WINDOW", self.quit)
        root.bind("<FocusIn>", lambda e: self.clear_unread())
        root.bind("<Configure>", self.on_configure)
        self.geo_job = None
        self.build_ui()
        self.load_history()
        self.replay_pings()

        pub.subscribe(self.on_receive, "meshtastic.receive")
        pub.subscribe(self.on_established, "meshtastic.connection.established")
        pub.subscribe(self.on_lost, "meshtastic.connection.lost")

        self.start_tray()
        if self.tray and self.cfg.get("hidden"):
            root.withdraw()
        self.root.after(200, self.pump)
        self.root.after(500, self.tick)
        # Start the background transient-node discovery loop (first run shortly
        # after startup, then every auto_search_secs).
        self._schedule_auto_search(15)

    # ---------- tray ----------
    def start_tray(self):
        if not pystray:
            return
        act = lambda name: (lambda: self.q.put(("tray", name)))
        menu = pystray.Menu(
            pystray.MenuItem("Show / hide", act("toggle"), default=True),
            pystray.MenuItem("Pause / resume", act("pause")),
            pystray.MenuItem("Settings", act("settings")),
            pystray.MenuItem("Clear messages", act("clear")),
            pystray.MenuItem("Send feedback", act("feedback")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Central Finite Curve", act("quit")))
        self.tray = pystray.Icon("CentralFiniteCurve", tray_image(BAD, False), "Central Finite Curve", menu)
        self.tray.run_detached()

    def update_tray(self):
        if self.tray:
            self.tray.icon = tray_image(self.tray_color, self.unread > 0)
            tip = f"Central Finite Curve — {self.status.cget('text')}"
            if self.unread:
                tip += f" · {self.unread} new"
            self.tray.title = tip[:127]

    def on_tray(self, action):
        if action == "toggle":
            self.show() if self.root.state() == "withdrawn" else self.hide()
        elif action == "pause":
            self.toggle_pause()
        elif action == "settings":
            self.show()
            self.open_settings()
        elif action == "clear":
            self.show()
            self.clear_messages()
        elif action == "feedback":
            self.show()
            self.open_feedback()
        elif action == "quit":
            self.quit()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.clear_unread()
        self.cfg["hidden"] = False
        save_cfg(self.cfg)
        self.sync_map_awake()

    def hide(self):
        self.save_geometry()
        self.root.withdraw()
        self.cfg["hidden"] = True
        save_cfg(self.cfg)
        self.sync_map_awake()

    def sync_map_awake(self, event=None):
        # the map only downloads and redraws while you can see it (Map tab open, window not hidden/minimised)
        if not self.map:
            return
        try:
            sel = self.tabs.select()
            on_map = sel == str(self.map_tab)
            on_drone = sel == str(getattr(self, "drone_tab", ""))
        except tk.TclError:
            on_map = on_drone = False
        if on_drone and not self.dmap:
            self._build_drone_map()      # lazy: first time the Drone tab is opened
        visible = self.root.state() not in ("withdrawn", "iconic")
        self.map.set_awake(on_map and visible)
        if self.dmap:
            self.dmap.set_awake(on_drone and visible)

    def _build_drone_map(self):
        """Create the Drone tab's map the first time the tab is opened (lazy — saves
        a second tile engine for anyone who never uses drone tracking)."""
        if self.dmap or not self._drone_frame:
            return
        f4 = self._drone_frame
        self.dmap = SmoothMapView(f4, width=400, height=300, corner_radius=0)
        self.dmap.set_position(*(self.home() or WORLD_VIEW))
        self.dmap.set_zoom(14 if self.home() else 2)
        self.dmap.pack(fill="both", expand=True)
        self.dmap.set_awake(False)
        dbar = tk.Frame(f4, bg=BG)
        dbar.place(relx=0.5, rely=1.0, y=-6, anchor="s")
        dbtn = dict(bg=PANEL, fg=FG, relief="flat", activebackground="#2c3a4a", font=("Segoe UI", 9))
        tk.Button(dbar, text="Fit path", command=self.fit_track, **dbtn).pack(side="left", padx=(0, 6))
        self.track_lbl = tk.Label(dbar, text="no drone pings yet", bg=BG, fg=DIM, width=42,
                                  font=("Segoe UI", 9))
        self.track_lbl.pack(side="left", padx=4)
        tk.Button(dbar, text="Save flight", command=self.save_flight, **dbtn).pack(side="left", padx=(6, 0))
        tk.Button(dbar, text="Incident report", command=self.make_report, **dbtn).pack(side="left", padx=(6, 0))
        tk.Button(dbar, text="Clear", command=self.clear_track, **dbtn).pack(side="left", padx=(6, 0))
        self.apply_map_style()          # same tiles as the main map
        self.load_track_file()          # replay today's alerts from the message log

    # ---------- UI ----------
    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, borderwidth=0, rowheight=22)
        style.configure("Treeview.Heading", background=BG, foreground=DIM, borderwidth=0)
        style.map("Treeview", background=[("selected", "#2c3a4a")])
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG,
                        arrowcolor=FG)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)],
                  selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", FG)])

        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=8, pady=(8, 4))
        # red DRONE DETECTED banner: packed right under this bar only while alerts are coming in
        self.top_bar = top
        self.banner = tk.Frame(self.root, bg=BAD)
        self.banner_lbl = tk.Label(self.banner, text="", bg=BAD, fg="#ffffff", anchor="w", justify="left",
                                   font=("Segoe UI", 10, "bold"))
        self.banner_lbl.pack(side="left", fill="x", expand=True, padx=10, pady=6)
        bbtn = dict(bg="#ffffff", fg=BAD, relief="flat", activebackground="#ffe0de", font=("Segoe UI", 9, "bold"))
        tk.Button(self.banner, text="Dismiss", command=self.hide_banner, **bbtn).pack(side="right", padx=(4, 8))
        tk.Button(self.banner, text="Show on map", command=self.banner_to_map, **bbtn).pack(side="right")
        self.banner_job = None
        self.banner_pings = 0
        self.dot = tk.Label(top, text="●", fg=BAD, bg=BG, font=("Segoe UI", 12))
        self.dot.pack(side="left")
        self.status = tk.Label(top, text="Starting…", fg=FG, bg=BG, font=("Segoe UI", 10, "bold"))
        self.status.pack(side="left", padx=4)
        for text, cmd in (("⚙", self.open_settings), ("📌", self.toggle_top)):
            tk.Button(top, text=text, command=cmd, bg=PANEL, fg=FG, relief="flat",
                      activebackground="#2c3a4a", width=3).pack(side="right", padx=2)
        self.pause_btn = tk.Button(top, text="Pause", command=self.toggle_pause, bg=PANEL,
                                   fg=FG, relief="flat", activebackground="#2c3a4a", width=7)
        self.pause_btn.pack(side="right", padx=2)
        # redraw the lists now and actively ping quiet nodes (e.g. the drone detector) so they check in
        self.search_btn = tk.Button(top, text="Search", command=self.search_now, bg=PANEL,
                                    fg=FG, relief="flat", activebackground="#2c3a4a", width=7)
        self.search_btn.pack(side="right", padx=2)
        # force a clean reconnect: for a hung/stale link, or an immediate retry after replugging
        self.reconnect_btn = tk.Button(top, text="Reconnect", command=self.reconnect, bg=PANEL,
                                       fg=FG, relief="flat", activebackground="#2c3a4a", width=9)
        self.reconnect_btn.pack(side="right", padx=2)

        self.info = tk.Label(self.root, text="", fg=DIM, bg=BG, anchor="w", justify="left",
                             font=("Segoe UI", 9))
        self.info.pack(fill="x", padx=10)

        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG, foreground=DIM, padding=(12, 4), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", FG)])
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=False, padx=8, pady=(8, 0))

        def make_tree(cols, labels, widths):
            frame = tk.Frame(self.tabs, bg=PANEL)
            tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
            for c, label, w in zip(cols, labels, widths):
                tree.heading(c, text=label)
                tree.column(c, width=w, anchor="w" if c == "name" else "center", stretch=c == "name")
            tree.pack(fill="both", expand=True)
            tree.tag_configure("me", foreground=ACCENT)
            tree.tag_configure("fresh", foreground=GOOD)
            tree.tag_configure("gone", foreground=DIM)
            tree.bind("<<TreeviewSelect>>", self.pick_dest)
            return frame, tree

        f1, self.tree = make_tree(("name", "snr", "rssi", "hops", "batt", "heard"),
                                  ("Name", "SNR", "RSSI", "Hops", "Batt", "Heard"),
                                  (150, 50, 55, 40, 45, 50))
        f2, self.hist_tree = make_tree(("name", "status", "first", "last", "count", "best"),
                                       ("Name", "Status", "First seen", "Last seen", "Heard", "Best RSSI"),
                                       (130, 60, 85, 85, 45, 65))
        self.tabs.add(f1, text="Active")
        self.tabs.add(f2, text="History")

        self.map = None
        self.markers = {}      # node id -> map marker
        self.map_fitted = False
        if tkintermapview:
            f3 = tk.Frame(self.tabs, bg=PANEL)
            self.map = SmoothMapView(f3, width=400, height=300, corner_radius=0,
                                     on_zoom=self.sync_zoom_slider)
            self.apply_map_style()
            self.map.set_position(*(self.home() or WORLD_VIEW))
            self.map.set_zoom(10 if self.home() else 2)
            self.map.pack(fill="both", expand=True)
            # zoom slider under the +/- buttons: top = closer in
            self.zoom_var = tk.IntVar(value=10)
            self.zoom_slider = tk.Scale(f3, from_=19, to=3, orient="vertical", variable=self.zoom_var,
                                        showvalue=False, length=150, width=12, sliderlength=18,
                                        bg=PANEL, troughcolor=BG, highlightthickness=0, bd=0,
                                        activebackground=ACCENT, command=self.on_zoom_slider)
            self.zoom_slider.place(x=26, y=100)
            self.slider_job = None

            # bottom bar: step through nodes on the map, optionally only repeaters
            self.map_points = {}   # node id -> (lat, lon, label, colour, is_repeater, last heard)
            self.map_idx = -1
            self.map_filter = self.cfg.get("map_filter", "All")
            bar = tk.Frame(f3, bg=BG)
            bar.place(relx=0.5, rely=1.0, y=-6, anchor="s")
            btn = dict(bg=PANEL, fg=FG, relief="flat", activebackground="#2c3a4a", font=("Segoe UI", 9))
            self.filter_btn = tk.Button(bar, text=self.map_filter, width=9, command=self.cycle_filter, **btn)
            self.filter_btn.pack(side="left", padx=(0, 6))
            tk.Button(bar, text="◀", width=3, command=lambda: self.cycle_map(-1), **btn).pack(side="left")
            self.cycle_lbl = tk.Label(bar, text="no nodes on map", bg=BG, fg=DIM, width=48,
                                      font=("Segoe UI", 9))
            self.cycle_lbl.pack(side="left", padx=4)
            self.cycle_nid = None  # node shown in the bar; clicking the bar opens its info card
            self.cycle_lbl.bind("<Button-1>", lambda e: self.cycle_nid and self.show_node_info(self.cycle_nid))
            self.info_win = None
            tk.Button(bar, text="▶", width=3, command=lambda: self.cycle_map(1), **btn).pack(side="left")
            tk.Button(f3, text="Fit all", command=self.fit_map, bg=PANEL, fg=FG, relief="flat",
                      font=("Segoe UI", 8)).place(relx=1.0, x=-6, y=6, anchor="ne")
            # internet-reported nodes from meshmap.net (purple), shown separately from radio-heard ones
            self.online = {}
            self.online_last = 0
            self.online_busy = False
            self.acc_polys = {}
            self.last_me = (None, {})
            # which radio-heard nodes get a pin: only the active ones, or everyone heard within a time frame
            self.seen_box = ttk.Combobox(f3, state="readonly", width=30, font=("Segoe UI", 9),
                                         values=[label for _, label in MAP_SEEN])
            self.seen_box.set(self.seen_label())
            self.seen_box.bind("<<ComboboxSelected>>", lambda e: self.set_map_seen(
                next(k for k, label in MAP_SEEN if label == self.seen_box.get())))
            self.seen_box.place(relx=1.0, x=-6, y=34, anchor="ne")
            # internet-reported nodes (meshmap.net, purple pins) are a separate layer you can switch off
            self.online_var = tk.BooleanVar(value=self.cfg.get("online_nodes", True))
            self.online_btn = tk.Checkbutton(f3, text="Show internet-map nodes (purple)", variable=self.online_var,
                                             command=self.toggle_online, bg=PANEL, fg=ONLINE, selectcolor=BG,
                                             activebackground=PANEL, activeforeground=ONLINE,
                                             font=("Segoe UI", 8), relief="flat", bd=0, padx=4)
            self.online_btn.place(relx=1.0, x=-6, y=62, anchor="ne")
            self.ac_var = tk.BooleanVar(value=bool(self.cfg.get("aircraft_on", False)))
            tk.Checkbutton(f3, text="Show aircraft (adsb.fi)", variable=self.ac_var, command=self.toggle_aircraft,
                           bg=PANEL, fg=AIRCRAFT, selectcolor=BG, activebackground=PANEL, activeforeground=AIRCRAFT,
                           font=("Segoe UI", 8), relief="flat", bd=0, padx=4).place(relx=1.0, x=-6, y=86, anchor="ne")
            self.as_var = tk.BooleanVar(value=bool(self.cfg.get("airspace_on", False)))
            tk.Checkbutton(f3, text="Show airspace & no-fly zones (FAA, US)", variable=self.as_var,
                           command=self.toggle_airspace, bg=PANEL, fg="#4fb3ff", selectcolor=BG,
                           activebackground=PANEL, activeforeground="#4fb3ff", font=("Segoe UI", 8), relief="flat",
                           bd=0, padx=4).place(relx=1.0, x=-6, y=110, anchor="ne")
            tk.Button(f3, text="Clear map history", command=self.clear_map_history, bg=PANEL, fg=DIM,
                      relief="flat", font=("Segoe UI", 8)).place(relx=1.0, x=-6, y=136, anchor="ne")
            if self.cfg.get("airspace_on"):
                self.root.after(1500, self.draw_airspace)
            # adsb.fi asks to be credited with a link whenever its data is shown
            self.ac_credit = tk.Label(f3, text="Aircraft data: adsb.fi", bg=PANEL, fg=AIRCRAFT, cursor="hand2",
                                      font=("Segoe UI", 8, "underline"))
            self.ac_credit.bind("<Button-1>", lambda e: webbrowser.open("https://adsb.fi"))
            if self.cfg.get("aircraft_on"):
                self.ac_credit.place(relx=0.0, rely=1.0, x=6, y=-44, anchor="sw")
            self.tabs.add(f3, text="Map")
            self.map_tab = f3
            self.tabs.bind("<<NotebookTabChanged>>", self.sync_map_awake)
            self.root.bind("<Unmap>", lambda e: e.widget is self.root and self.sync_map_awake())  # minimised
            self.root.bind("<Map>", lambda e: e.widget is self.root and self.sync_map_awake())
            self.root.after_idle(self.sync_map_awake)

        # ---- Drone tab: plots each drone-alert GPS and traces the flight path ----
        # The map here is built LAZILY the first time the tab is opened (see
        # _build_drone_map, called from sync_map_awake). That saves a second tile
        # engine (~6 threads + ~35 MB) for anyone who never uses drone tracking.
        self.dmap = None
        self.track = []            # (lat, lon, rssi, t, mac, vendor) per drone ping, in time order
        self.pilot_pos = None
        self.dpath = None
        self.d_drone_marker = None
        self.d_pilot_marker = None
        self.track_lbl = None
        self._drone_frame = None
        if tkintermapview:
            f4 = tk.Frame(self.tabs, bg=PANEL)
            self.tabs.add(f4, text="Drone")
            self.drone_tab = f4
            self._drone_frame = f4

        mhead = tk.Frame(self.root, bg=BG)
        mhead.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(mhead, text="MESSAGES", fg=DIM, bg=BG, font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Button(mhead, text="Clear", command=self.clear_messages, bg=BG, fg=DIM, relief="flat",
                  activebackground=PANEL, font=("Segoe UI", 8), bd=0).pack(side="right")
        tk.Button(mhead, text="Archive", command=self.open_archive, bg=BG, fg=DIM, relief="flat",
                  activebackground=PANEL, font=("Segoe UI", 8), bd=0).pack(side="right", padx=(0, 10))
        # three message panels side by side; drag the gaps between them to resize
        panes = tk.PanedWindow(self.root, orient="horizontal", bg=BG, sashwidth=6, bd=0, sashrelief="flat")
        panes.pack(fill="both", expand=True, padx=8)
        self.panels = {}
        for key, title, width, stretch in MSG_PANELS:
            frame = tk.Frame(panes, bg=BG)
            head = tk.Frame(frame, bg=BG)
            head.pack(fill="x")
            label = tk.Label(head, text=title, fg=DIM, bg=BG, font=("Segoe UI", 8, "bold"))
            label.pack(side="left")
            tk.Button(head, text="Open in window ↗", command=lambda k=key: self.pop_panel(k), bg=BG, fg=DIM,
                      relief="flat", activebackground=PANEL, font=("Segoe UI", 8), bd=0).pack(side="right")
            txt = self.make_msg_text(frame, height=10, width=20)
            txt.pack(fill="both", expand=True)
            self.bind_msg_menu(txt)
            panes.add(frame, minsize=120, width=width, stretch=stretch)
            self.panels[key] = {"title": title, "label": label, "texts": [txt], "win": None,
                                "lines": collections.deque(maxlen=MSG_PANEL_LINES)}
        self.msgs = self.panels["dm"]["texts"][0]

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=8, pady=8)
        self.dest = ttk.Combobox(bottom, state="readonly", width=26, values=["Everyone"])
        self.dest.set("Everyone")
        self.dest.pack(side="left")
        self.entry = tk.Entry(bottom, bg=PANEL, fg=FG, insertbackground=FG, relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, padx=6, ipady=3)
        self.entry.bind("<Return>", lambda e: self.send())
        tk.Button(bottom, text="Send", command=self.send, bg=ACCENT, fg="#000",
                  relief="flat").pack(side="right")

    def set_status(self, text, color):
        try:
            self.status.config(text=text)
            self.dot.config(fg=color)
        except tk.TclError:
            return  # the window is already closing (a last radio event during Quit)
        self.tray_color = color
        self.update_tray()

    @staticmethod
    def make_msg_text(parent, height, width):
        txt = tk.Text(parent, bg=PANEL, fg=FG, relief="flat", wrap="word", font=("Segoe UI", 9),
                      state="disabled", height=height, width=width, padx=4, pady=2)
        for tag, colour in (("time", DIM), ("who", ACCENT), ("dm", WARN), ("me", GOOD), ("bad", BAD)):
            txt.tag_configure(tag, foreground=colour)
        return txt

    def bind_msg_menu(self, txt):
        txt.bind("<Button-3>", lambda e, t=txt: self.msg_menu(t, e))

    @staticmethod
    def msg_key(m):
        return str(m.get("id") or f"{m.get('t')}|{m.get('from')}")

    def msg_peer(self, m):
        """(key, name) of the node a message is a conversation with."""
        if m.get("mine"):
            nid, name = m.get("tid"), m.get("to")
        else:
            nid, name = m.get("fid"), m.get("from")
        return (nid or f"name:{name}"), name

    def is_archived(self, m):
        arch = self.cfg.get("archived_ids") or []
        if self.msg_key(m) in arch:
            return True
        until = (self.cfg.get("archived_peers") or {}).get(self.msg_peer(m)[0])
        try:
            return until is not None and float(m.get("t", 0)) <= float(until)
        except (TypeError, ValueError):
            return False

    def msg_menu(self, txt, event):
        mtag = next((t for t in txt.tag_names(f"@{event.x},{event.y}") if t.startswith("m") and t[1:].isdigit()), None)
        m = self.line_msgs.get(mtag)
        if m is None:
            return
        peer, name = self.msg_peer(m)
        menu = tk.Menu(txt, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT, activeforeground="#000")
        menu.add_command(label="Archive this message", command=lambda: self.archive_msgs(msg=m))
        if name and name != "Everyone":
            menu.add_command(label=f"Archive whole conversation with {name}"[:60],
                             command=lambda: self.archive_msgs(peer=peer))
            if not peer.startswith("name:") and hasattr(self, "info_win"):
                menu.add_separator()
                menu.add_command(label=f"Open {name}'s card"[:60], command=lambda: self.show_node_info(peer))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def archive_msgs(self, msg=None, peer=None):
        """Hide a message, or a whole conversation up to now, from the panels. Nothing is deleted:
        it all stays searchable in the archive window. New messages from that node still show."""
        if msg is not None:
            arch = list(self.cfg.get("archived_ids") or [])
            arch.append(self.msg_key(msg))
            self.cfg["archived_ids"] = arch[-5000:]
        if peer is not None:
            peers = dict(self.cfg.get("archived_peers") or {})
            peers[peer] = time.time()
            self.cfg["archived_peers"] = peers
        save_cfg(self.cfg)
        self.rebuild_panels()

    def rebuild_panels(self):
        """Redraw every panel from its remembered lines, leaving out archived messages."""
        for panel in self.panels.values():
            keep = collections.deque(maxlen=MSG_PANEL_LINES)
            for segments, mtag in panel["lines"]:
                m = self.line_msgs.get(mtag)
                if m is None or not self.is_archived(m):
                    keep.append((segments, mtag))
            panel["lines"] = keep
            for txt in panel["texts"]:
                try:
                    txt.config(state="normal")
                    txt.delete("1.0", "end")
                    for segments, mtag in keep:
                        for text, tag in segments:
                            txt.insert("end", text, (tag, mtag) if tag else (mtag,))
                    txt.see("end")
                    txt.config(state="disabled")
                except tk.TclError:
                    pass

    def msg_kind(self, m):
        """Which panel a message belongs in: drone alerts, direct messages, or channel traffic."""
        text = m.get("text", "")
        if (DRONE_RE.search(text) or text.lstrip().startswith("Drone:") or m.get("ch") == self.cfg.get("push_channel", "DroneAlert")
                or (m.get("fid") and m.get("fid") == self.cfg.get("drone_node_id"))):
            return "drone"
        return "dm" if m.get("to") not in (None, "Everyone") else "chan"

    def panel_write(self, key, segments, m=None):
        """Add one line [(text, tag)] to a panel and to its pop-out window if that's open.
        m = the message it shows, so right-click can archive it (None for notes)."""
        panel = self.panels[key]
        self.line_seq += 1
        mtag = f"m{self.line_seq}"
        if m is not None:
            self.line_msgs[mtag] = m
            if len(self.line_msgs) > 4 * MSG_PANEL_LINES:  # forget the oldest (they've scrolled off)
                for old in list(self.line_msgs)[:MSG_PANEL_LINES]:
                    del self.line_msgs[old]
        panel["lines"].append((segments, mtag))
        alive = []
        for txt in panel["texts"]:
            try:
                txt.config(state="normal")
                for text, tag in segments:
                    txt.insert("end", text, (tag, mtag) if tag else (mtag,))  # plain text only
                excess = int(txt.index("end-1c").split(".")[0]) - MSG_PANEL_LINES
                if excess > 0:
                    txt.delete("1.0", f"{excess + 1}.0")
                txt.see("end")
                txt.config(state="disabled")
                alive.append(txt)
            except tk.TclError:
                pass  # its window was closed
        panel["texts"] = alive

    def pop_panel(self, key):
        """Show one message panel in its own resizable window (it keeps updating live)."""
        panel = self.panels[key]
        if panel["win"] is not None and panel["win"].winfo_exists():
            panel["win"].deiconify()
            panel["win"].lift()
            return
        win = panel["win"] = tk.Toplevel(self.root, bg=BG)
        win.title(f"{panel['title'].title()} - Central Finite Curve")
        win.geometry("640x420")
        win.attributes("-topmost", self.cfg.get("on_top", False))
        txt = self.make_msg_text(win, height=24, width=80)
        bar = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.config(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True, padx=(8, 0), pady=8)
        txt.config(state="normal")
        for segments, mtag in panel["lines"]:
            for text, tag in segments:
                txt.insert("end", text, (tag, mtag) if tag else (mtag,))
        txt.see("end")
        txt.config(state="disabled")
        panel["texts"].append(txt)
        self.bind_msg_menu(txt)
        self.panel_controls(key, win, txt)
        win.bind("<Escape>", lambda e: win.destroy())

    def panel_controls(self, key, win, txt):
        """The send bar (and extras) for a popped-out message window, matching what that panel is for."""
        bar = tk.Frame(win, bg=BG)
        bar.pack(side="bottom", fill="x", padx=8, pady=(0, 8), before=txt)
        btn = dict(bg=PANEL, fg=FG, relief="flat", activebackground="#2c3a4a", font=("Segoe UI", 9))
        if key == "dm":
            box = ttk.Combobox(bar, state="readonly", width=26, values=list(self.dest["values"]))
            cur = self.dest.get()
            box.set(cur if cur != "Everyone" else (box["values"][1] if len(box["values"]) > 1 else "Everyone"))
            box.pack(side="left")
            if not hasattr(self, "dest_boxes"):
                self.dest_boxes = []
            self.dest_boxes.append(box)

            def target():
                v = box.get()
                return (v.rsplit("[", 1)[1].rstrip("]"), 0) if "[" in v else ("^all", 0)
        elif key == "chan":
            chans = self.channel_list()
            box = ttk.Combobox(bar, state="readonly", width=18, values=[label for _, label in chans])
            box.set(chans[0][1])
            box.pack(side="left")

            def target():
                label = box.get()
                return "^all", next((i for i, l in chans if l == label), 0)
        else:  # drone
            tk.Button(bar, text="Show on map", command=self.banner_to_map, **btn).pack(side="left")
            drone_ch = self.cfg.get("push_channel", "DroneAlert")
            tk.Label(bar, text=f" to #{drone_ch}", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")

            def target():
                return "^all", self.channel_index(drone_ch)
        entry = tk.Entry(bar, bg=PANEL, fg=FG, insertbackground=FG, relief="flat")
        entry.pack(side="left", fill="x", expand=True, padx=6, ipady=3)

        def send(event=None):
            node_id, channel = target()
            if self.send_text(entry.get().strip(), node_id, channel=channel):
                entry.delete(0, "end")
        entry.bind("<Return>", send)
        tk.Button(bar, text="Send", command=send, bg=ACCENT, fg="#000", relief="flat").pack(side="left")
        tk.Button(bar, text="Clear all", command=self.clear_messages, bg=BG, fg=DIM, relief="flat",
                  activebackground=PANEL, font=("Segoe UI", 8), bd=0).pack(side="right", padx=(6, 0))
        entry.focus_set()

    def add_msg(self, m, notify):
        if m["id"] and m["id"] in self.seen_ids:
            return
        self.seen_ids[m["id"]] = None
        if len(self.seen_ids) > 5000:  # duplicates only arrive within minutes; no need to remember forever
            for old in list(self.seen_ids)[:1000]:
                del self.seen_ids[old]
        when = datetime.datetime.fromtimestamp(m["t"]).strftime("%m/%d %H:%M")
        kind = self.msg_kind(m)
        if self.is_archived(m):
            return  # archived from the panels by the user; still in the archive window
        line = [(f"{when}  ", "time"), (m["from"], "me" if m.get("mine") else "who")]
        if m["to"] != "Everyone":
            line.append((f" → {m['to']}", "dm"))
        elif kind == "chan" and m.get("ch"):
            line.append((f" #{m['ch']}", "time"))
        mt = DRONE_RE.search(m["text"]) if kind == "drone" else None
        if mt:
            vendor = oui_vendor(mt.group(1))
            line.append((f": {mt.group(1)}{' (' + vendor + ')' if vendor else ''}  RSSI {mt.group(2)}\n"
                         f"    drone {float(mt.group(3)):.5f}, {float(mt.group(4)):.5f}\n"
                         f"    pilot {float(mt.group(5)):.5f}, {float(mt.group(6)):.5f}\n", None))
        else:
            line.append((f": {m['text']}\n", None))
        self.panel_write(kind, line, m)
        chat = getattr(self, "card_chat", None)
        if chat and self.with_node(m, chat[1], chat[2]):
            self.chat_line(chat[0], m)
        if notify:
            if self.cfg["sound"]:
                alert(self.cfg)
            if self.root.focus_displayof() is None:
                self.unread += 1
                self.root.title(f"({self.unread}) Central Finite Curve")
                self.update_tray()

    def note(self, text, tag):
        self.panel_write("dm", [(f"        {text}\n", tag)])  # delivery reports belong with direct messages
        chat = getattr(self, "card_chat", None)
        if chat and (text.endswith(f" {chat[2]}") or f" {chat[2]} (" in text):  # delivery reports
            self.chat_note(chat[0], text, tag)

    @staticmethod
    def with_node(m, nid, name):
        """Is message m part of our conversation with node nid? (Its posts, or what we sent it.)"""
        if "fid" in m or "tid" in m:
            return m.get("fid") == nid or bool(m.get("mine") and m.get("tid") == nid)
        # older records only have names
        return (not m.get("mine") and m.get("from") == name) or bool(m.get("mine") and m.get("to") == name)

    def chat_line(self, txt, m):
        try:
            txt.config(state="normal")
            txt.delete("1.0", "placeholder.last + 1c") if txt.tag_ranges("placeholder") else None
            when = datetime.datetime.fromtimestamp(m["t"]).strftime("%m/%d %H:%M")
            txt.insert("end", f"{when}  ", "time")
            txt.insert("end", "Me" if m.get("mine") else m["from"], "me" if m.get("mine") else "who")
            if not m.get("mine") and m.get("to") == "Everyone":
                txt.insert("end", " → Everyone", "time")
            txt.insert("end", f": {m['text']}\n")  # text from strangers: plain text only
            txt.see("end")
            txt.config(state="disabled")
        except tk.TclError:
            pass  # card closed

    def chat_note(self, txt, text, tag):
        try:
            txt.config(state="normal")
            txt.insert("end", f"        {text}\n", tag)
            txt.see("end")
            txt.config(state="disabled")
        except tk.TclError:
            pass

    def on_configure(self, event):
        # save size/position shortly after a move or resize, so a shutdown doesn't lose it
        if event.widget is not self.root:
            return
        if self.geo_job:
            self.root.after_cancel(self.geo_job)
        self.geo_job = self.root.after(1500, self.save_geometry)

    def save_geometry(self):
        self.geo_job = None
        geo = self.root.geometry()
        if self.root.state() == "normal" and geo != self.cfg.get("geometry"):
            self.cfg["geometry"] = geo
            save_cfg(self.cfg)

    def clear_map_history(self):
        if not messagebox.askyesno(
                "Clear map history",
                "Remove every recorded node from the map and the History tab?\n\n"
                "Nodes still in your radio's own node list reappear straight away. "
                "(A backup is kept in nodes-history-backup.json.)", parent=self.root):
            return
        try:
            if os.path.exists(HIST_PATH):
                os.replace(HIST_PATH, os.path.join(os.path.dirname(HIST_PATH), "nodes-history-backup.json"))
        except OSError:
            log.warning("Couldn't back up the node history", exc_info=True)
        self.history.clear()
        self.heard.clear()  # otherwise the next refresh rebuilds history from this session's sightings
        self.hist_dirty = True
        save_json(HIST_PATH, self.history)
        log.info("Map history cleared by the user")
        self.map_idx = -1
        if self.iface:
            self.refresh()
        else:
            self.update_map()

    def clear_messages(self):
        if not messagebox.askyesno("Clear messages", "Clear the message panels?\n\n"
                                   "They stay in the chat archive (Archive button).", parent=self.root):
            return
        self.cfg["cleared_at"] = time.time()  # so a restart doesn't bring them back into the panels
        save_cfg(self.cfg)
        self.wipe_panels()

    def wipe_panels(self):
        for panel in self.panels.values():
            panel["lines"].clear()
            for txt in panel["texts"]:
                try:
                    txt.config(state="normal")
                    txt.delete("1.0", "end")
                    txt.config(state="disabled")
                except tk.TclError:
                    pass
        self.clear_unread()

    def clear_unread(self):
        self.unread = 0
        self.root.title("Central Finite Curve")
        self.update_tray()

    def load_history(self):
        cleared = float(self.cfg.get("cleared_at", 0) or 0)  # "Clear" hides older ones from the panels only
        for line in tail_lines(LOG_PATH, 300):
            try:
                m = json.loads(line)
                if isinstance(m, dict) and float(m.get("t", 0)) > cleared:
                    self.add_msg(m, notify=False)
            except (ValueError, TypeError, KeyError):
                pass

    def diagnostics(self):
        """Facts that make a bug report useful. Nothing personal: no node IDs, names or places."""
        info = [("App version", VERSION + (" (installed app)" if getattr(sys, "frozen", False) else " (from source)")),
                ("Windows / OS", platform.platform()),
                ("Python", platform.python_version()),
                ("Connection", "Wi-Fi" if self.cfg.get("mode") == "wifi" else "USB")]
        iface = self.iface
        if iface:
            try:
                me = iface.getMyNodeInfo() or {}
                info.append(("Radio", me.get("user", {}).get("hwModel", "unknown")))
                fw = getattr(getattr(iface, "metadata", None), "firmware_version", None)
                info.append(("Radio firmware", fw or "unknown"))
                info.append(("Nodes in radio", str(len(iface.nodes or {}))))
            except Exception:
                info.append(("Radio", "couldn't read"))
        else:
            info.append(("Radio", "not connected"))
        info.append(("Memory", f"{memory_mb():.0f} MB"))
        info.append(("Phone alerts", "on" if self.cfg.get("push_url") else "off"))
        return info

    def open_feedback(self):
        """Feedback form: builds a GitHub issue the person reviews and submits themselves.
        Nothing is sent from the app; the browser opens with the report filled in."""
        if getattr(self, "feedback_win", None) is not None and self.feedback_win.winfo_exists():
            self.feedback_win.deiconify()
            self.feedback_win.lift()
            return
        win = self.feedback_win = tk.Toplevel(self.root, bg=BG)
        win.title("Send feedback - Central Finite Curve")
        win.attributes("-topmost", self.cfg.get("on_top", False))
        win.columnconfigure(1, weight=1)
        lab = dict(bg=BG, fg=FG, font=("Segoe UI", 9))
        tk.Label(win, text="Tell us what happened. Your report opens on GitHub for you to check and submit;\n"
                           "nothing is sent from this app.", bg=BG, fg=DIM, font=("Segoe UI", 8),
                 justify="left").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4))
        kinds = {label: key for key, label in FEEDBACK_KINDS}
        kind = ttk.Combobox(win, state="readonly", width=20, values=list(kinds))
        kind.set(FEEDBACK_KINDS[0][1])
        tk.Label(win, text="Type", **lab).grid(row=1, column=0, sticky="w", padx=10, pady=4)
        kind.grid(row=1, column=1, sticky="w", padx=10, pady=4)
        title = tk.Entry(win, bg=PANEL, fg=FG, insertbackground=FG, relief="flat")
        tk.Label(win, text="Short title", **lab).grid(row=2, column=0, sticky="w", padx=10, pady=4)
        title.grid(row=2, column=1, sticky="we", padx=10, pady=4, ipady=3)
        tk.Label(win, text="What happened?\nWhat did you expect?", justify="left", **lab).grid(
            row=3, column=0, sticky="nw", padx=10, pady=4)
        desc = tk.Text(win, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", height=7, width=60,
                       wrap="word", font=("Segoe UI", 9))
        desc.grid(row=3, column=1, sticky="nsew", padx=10, pady=4)
        with_log = tk.BooleanVar(value=False)
        tk.Checkbutton(win, text="Include the last 40 lines of the error log (names, IDs, places and links "
                                 "are removed first)", variable=with_log, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG, font=("Segoe UI", 8),
                       wraplength=420, justify="left").grid(row=4, column=1, sticky="w", padx=6, pady=2)
        tk.Label(win, text="Report preview", **lab).grid(row=5, column=0, sticky="nw", padx=10, pady=4)
        preview = tk.Text(win, bg=BG, fg=DIM, relief="flat", height=10, width=60, wrap="word",
                          font=("Consolas", 8), state="disabled")
        preview.grid(row=5, column=1, sticky="nsew", padx=10, pady=4)
        win.rowconfigure(5, weight=1)
        status = tk.Label(win, text="", bg=BG, fg=DIM, font=("Segoe UI", 8))

        def build():
            body = desc.get("1.0", "end").strip() or "(no description)"
            lines = [body, "", "---", "**Details** (added automatically)", ""]
            lines += [f"- {k}: {v}" for k, v in self.diagnostics()]
            if with_log.get():
                names = [h.get("name", "") for h in self.history.values()] + list(self.dest_names.values())
                log_lines = scrub_log(tail_lines(DEBUG_LOG, 40), names)
                lines += ["", "<details><summary>Recent log (cleaned)</summary>", "", "```"] + log_lines + ["```",
                                                                                                         "</details>"]
            key = kinds.get(kind.get(), "Bug")
            t = title.get().strip() or {"Bug": "Bug report", "Idea": "Idea", "Works": "It worked"}[key]
            return f"[{key}] {t}", "\n".join(lines)

        def refresh(*_):
            t, body = build()
            preview.config(state="normal")
            preview.delete("1.0", "end")
            preview.insert("end", f"{t}\n\n{body}")
            preview.config(state="disabled")
            preview_job[0] = None

        preview_job = [None]

        def later(*_):  # refresh the preview shortly after typing stops, not on every key
            if preview_job[0]:
                win.after_cancel(preview_job[0])
            preview_job[0] = win.after(400, refresh)

        def open_github():
            t, body = build()
            url, cut = feedback_link(t, body)
            webbrowser.open(url)
            status.config(text="Opened in your browser. Check it over, then click Submit on GitHub."
                               + (" (It was long, so it was shortened; Copy report has it all.)" if cut else ""),
                          fg=GOOD)
            log.info("Feedback report opened on GitHub (%d characters)", len(body))

        def copy():
            t, body = build()
            win.clipboard_clear()
            win.clipboard_append(f"{t}\n\n{body}")
            status.config(text="Copied. Paste it into an email or message.", fg=GOOD)

        for w in (title, desc):
            w.bind("<KeyRelease>", later)
        kind.bind("<<ComboboxSelected>>", refresh)
        with_log.trace_add("write", refresh)
        bar = tk.Frame(win, bg=BG)
        bar.grid(row=6, column=0, columnspan=2, sticky="we", padx=10, pady=(4, 10))
        tk.Button(bar, text="Open on GitHub", command=open_github, bg=ACCENT, fg="#000", relief="flat").pack(side="left")
        tk.Button(bar, text="Copy report", command=copy, bg=PANEL, fg=FG, relief="flat").pack(side="left", padx=6)
        tk.Button(bar, text="Close", command=win.destroy, bg=PANEL, fg=FG, relief="flat").pack(side="right")
        status.grid(row=7, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        win.bind("<Escape>", lambda e: win.destroy())
        refresh()
        title.focus_set()

    def archive_days(self):
        try:
            return max(0, int(self.cfg.get("archive_days", ARCHIVE_DAYS)))
        except (TypeError, ValueError):
            return ARCHIVE_DAYS

    def open_archive(self):
        """Search old messages. Reads the archive file only when asked, so it costs nothing while closed."""
        if getattr(self, "archive_win", None) is not None and self.archive_win.winfo_exists():
            self.archive_win.deiconify()
            self.archive_win.lift()
            return
        win = self.archive_win = tk.Toplevel(self.root, bg=BG)
        win.title("Chat archive - Central Finite Curve")
        win.geometry("760x520")
        win.attributes("-topmost", self.cfg.get("on_top", False))
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=8, pady=8)
        tk.Label(bar, text="Search", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left")
        query = tk.Entry(bar, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", width=24)
        query.pack(side="left", padx=6, ipady=3)
        kinds = {"All messages": None, "Direct messages": "dm", "Drone detect": "drone", "Channel traffic": "chan"}
        kind = ttk.Combobox(bar, state="readonly", width=15, values=list(kinds))
        kind.set("All messages")
        kind.pack(side="left")
        spans = {"Today": 1, "Last 7 days": 7, "Last 30 days": 30, "Everything": 0}
        span = ttk.Combobox(bar, state="readonly", width=12, values=list(spans))
        span.set("Last 7 days")
        span.pack(side="left", padx=6)
        count = tk.Label(bar, text="", bg=BG, fg=DIM, font=("Segoe UI", 8))
        count.pack(side="right")
        txt = self.make_msg_text(win, height=24, width=90)
        sb = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 8), pady=(0, 8))
        txt.pack(fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        keep = self.archive_days()
        footbar = tk.Frame(win, bg=BG)
        foot = tk.Label(footbar, text=("Messages are kept for " + next((l for l, d in ARCHIVE_CHOICES if d == keep),
                                                                    f"{keep} days")
                                   + " (change in Settings)."), bg=BG, fg=DIM, font=("Segoe UI", 8))
        footbar.pack(side="bottom", fill="x", padx=8, pady=(0, 6), before=sb)
        foot.pack(side="left")

        def search(event=None):
            needle = query.get().strip().lower()
            want = kinds.get(kind.get())
            days = spans.get(span.get(), 7)
            since = time.time() - days * 86400 if days else 0
            found = collections.deque(maxlen=ARCHIVE_SHOW)
            total = 0
            try:
                with open(LOG_PATH, encoding="utf-8") as f:
                    for line in f:
                        if needle and needle not in line.lower():
                            continue  # cheap text check before decoding each line
                        try:
                            m = json.loads(line)
                            if float(m.get("t", 0)) < since or (want and self.msg_kind(m) != want):
                                continue
                        except (ValueError, AttributeError, TypeError):
                            continue
                        if needle and needle not in f"{m.get('from', '')} {m.get('to', '')} {m.get('text', '')}".lower():
                            continue
                        total += 1
                        found.append(m)
            except OSError:
                pass
            txt.config(state="normal")
            txt.delete("1.0", "end")
            for m in found:
                when = datetime.datetime.fromtimestamp(m["t"]).strftime("%Y-%m-%d %H:%M")
                txt.insert("end", f"{when}  ", "time")
                txt.insert("end", str(m.get("from", "?")), "me" if m.get("mine") else "who")
                if m.get("to") not in (None, "Everyone"):
                    txt.insert("end", f" → {m['to']}", "dm")
                elif m.get("ch"):
                    txt.insert("end", f" #{m['ch']}", "time")
                txt.insert("end", f": {m.get('text', '')}\n")  # plain text only
            if not found:
                txt.insert("end", "No messages found.\n", "time")
            txt.see("end")
            txt.config(state="disabled")
            count.config(text=f"{total} found" + (f", newest {ARCHIVE_SHOW} shown" if total > ARCHIVE_SHOW else ""))
        query.bind("<Return>", search)
        kind.bind("<<ComboboxSelected>>", search)
        span.bind("<<ComboboxSelected>>", search)
        tk.Button(bar, text="Search", command=search, bg=ACCENT, fg="#000", relief="flat").pack(side="left")

        def clear_archive():
            if not messagebox.askyesno("Clear chat archive",
                                       "Permanently delete every archived message?\n\n"
                                       "This also empties the message panels and can't be undone.", parent=win):
                return
            self.clear_archive()
            search()
        tk.Button(footbar, text="Clear archive", command=clear_archive, bg=BG, fg=BAD, relief="flat",
                  activebackground=PANEL, font=("Segoe UI", 8), bd=0).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        query.focus_set()
        search()

    def clear_archive(self):
        """Delete the whole chat archive (the message log) and empty the panels."""
        try:
            with open(LOG_PATH, "w", encoding="utf-8"):
                pass
        except OSError:
            log.warning("Couldn't clear the chat archive", exc_info=True)
            self.set_status("Couldn't clear the chat archive", BAD)
            return
        self.cfg["cleared_at"] = time.time()
        save_cfg(self.cfg)
        self.wipe_panels()
        log.info("Chat archive cleared by the user")

    def log_msg(self, m):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(m) + "\n")
        except OSError:
            pass

    # ---------- connection ----------
    def target(self):
        if DEMO:
            return "demo radio"
        return self.cfg["host"] if self.cfg["mode"] == "wifi" else (self.port_used or self.cfg["port"])

    def find_port(self, port):
        """The configured port, or the one Meshtastic radio plugged in if that port has vanished
        (Windows renumbers COM ports when you use a different USB socket). Never guesses between two.
        Returns (port, moved); a moved port must turn out to be the same radio (see check_radio)."""
        if not port:
            return port, False
        try:
            import serial.tools.list_ports
            present = {p.device.upper() for p in serial.tools.list_ports.comports()}
            if port.upper() in present:
                return port, False
            import meshtastic.util
            radios = meshtastic.util.findPorts(True)
        except Exception:
            log.debug("Port scan failed", exc_info=True)
            return port, False
        if len(radios) == 1:
            return radios[0], True
        return port, False

    def check_radio(self, iface, port, moved):
        """None if this is our radio, else why not. After a fallback only the same node (by ID) will do:
        a different radio plugged in (a detector, a friend's node) must never stand in for ours."""
        me = (iface.getMyNodeInfo() or {}).get("user", {})
        node_id, name = me.get("id"), me.get("longName") or me.get("id") or "?"
        known = self.cfg.get("node_id")
        if moved and known and node_id != known:
            return f"{self.cfg['port']} unplugged; {port} is a different radio ({name})"
        if moved:
            warn_once("port-moved", "%s not found; %s is the same radio (%s), using it", self.cfg["port"], port, name)
        if node_id and node_id != known:
            self.cfg["node_id"] = node_id  # remembered so a later fallback can tell our radio from others
            save_cfg(self.cfg)
        return None

    def set_clock(self, iface):
        # A radio without GPS or a phone never learns the time, so it timestamps nothing it hears.
        # Do what the phone app does on connect: give it the PC's time.
        try:
            iface.localNode.setTime(int(time.time()))
            self.clock_at = time.time()
            log.info("Set %s clock to PC time", self.target())
        except Exception as e:
            warn_once("set-clock", "Couldn't set %s clock: %s", self.target(), e)

    def serve_instance(self, sock):
        # answers copies started later: they ask us to show ourselves, then quit
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(2)
                    if conn.recv(16).startswith(b"show"):
                        conn.sendall(b"centralfinitecurve\n")
                        log.info("Another copy was started; showing this one instead")
                        self.q.put(("show", None))
                except OSError:
                    pass

    def connect(self):
        self.connecting = True
        self.last_try = time.time()
        self.port_used = None
        self.set_status(f"Connecting to {self.target() or '?'}…", WARN)

        def work():
            try:
                if DEMO:
                    iface = DemoInterface(self.home() or DEMO_CENTER, extra=DEMO_EXTRA)
                elif self.cfg["mode"] == "wifi":
                    iface = meshtastic.tcp_interface.TCPInterface(hostname=self.cfg["host"])
                else:
                    port = None if self.cfg["port"].lower() == "auto" else self.cfg["port"]
                    port, moved = self.find_port(port)
                    iface = meshtastic.serial_interface.SerialInterface(devPath=port)
                    self.port_used = iface.devPath  # "auto" resolves to a real port inside the library
                    wrong = self.check_radio(iface, iface.devPath, moved)
                    if wrong:
                        self.port_used = None
                        iface.close()
                        self.q.put(("fail", wrong))
                        return
                if not DEMO:
                    self.set_clock(iface)
                self.q.put(("iface", iface))
            except Exception as e:  # port busy, unplugged, wrong IP...
                self.q.put(("fail", str(e) or type(e).__name__))
        threading.Thread(target=work, daemon=True).start()

    def disconnect(self):
        iface, self.iface = self.iface, None
        if iface:
            threading.Thread(target=iface.close, daemon=True).start()

    def reconnect(self):
        """Force a clean reconnect now: for a hung or stale link, or an immediate retry after replugging."""
        if DEMO:
            return
        if self.connecting:
            self.note("Reconnect: already connecting, give it a moment…", "dm")
            return
        if self.paused:  # asking to reconnect means we want the radio back
            self.paused = False
            self.pause_btn.config(text="Pause")
        self.note("Reconnect: dropping the link and connecting again…", "me")
        self.disconnect()               # close any current connection (in a background thread)
        self.last_try = 0               # let the retry loop try immediately too
        self.set_status("Reconnecting…", WARN)
        self.root.after(1200, self._reconnect_go)  # let the old handle close first, then open fresh

    def _reconnect_go(self):
        if self.iface is None and not self.connecting and not self.paused:
            self.connect()

    def tick(self):
        if time.time() - getattr(self, "archive_trimmed", 0) > 86400:
            self.archive_trimmed = time.time()
            cut = trim_archive(LOG_PATH, self.archive_days())
            if cut:
                log.info("Chat archive: removed %d messages older than the keep setting", cut)
        if (not self.paused and self.iface is None and not self.connecting
                and time.time() - self.last_try > RETRY_SECS):
            self.connect()
        if self.iface and not DEMO and time.time() - self.clock_at > CLOCK_SECS:
            self.clock_at = time.time()  # set now so a slow radio doesn't get a second request queued
            threading.Thread(target=self.set_clock, args=(self.iface,), daemon=True).start()
        if self.push:
            self.push.flush()  # sends alerts that were held back to merge a burst
        if self.iface:
            self.refresh()
        elif self.map:
            self.update_map()  # keep internet nodes and ages current while unplugged
        if (self.map and self.cfg.get("online_nodes", True) and not self.online_busy
                and time.time() - self.online_last > ONLINE_REFRESH_SECS):
            self.refresh_online()
        self.maybe_fetch_aircraft()
        self.maybe_fetch_airspace()
        if self.dm_markers:
            self.draw_drones_main()  # drops drones that have gone quiet
        if self.hist_dirty:
            save_json(HIST_PATH, self.history)
            self.hist_dirty = False
        if time.time() - self.health_at >= HEALTH_SECS:
            self.log_health()
        for pid, (who, sent) in list(self.pending.items()):
            if time.time() - sent > 600:  # the radio always reports back well within this, even on failure
                del self.pending[pid]
                self.note(f"? no delivery report for message to {who}", "dm")
        self.root.after(5000, self.tick)

    def log_health(self):
        # one line every HEALTH_SECS so slow leaks or CPU creep show up over days of testing
        now, cpu = time.time(), time.process_time()
        load = 100 * (cpu - self.health_cpu) / max(now - self.health_at, 1)
        self.health_at, self.health_cpu = now, cpu
        mem = memory_mb()
        log.info("Health: %s MB, CPU %.2f%%, threads %d, radio %s, nodes %d, history %d, online %d, map %s, tiles %d",
                 f"{mem:.0f}" if mem else "?", load, threading.active_count(),
                 "connected" if self.iface else ("paused" if self.paused else "down"),
                 len(getattr(self.iface, "nodes", None) or {}), len(self.history), len(getattr(self, "online", {})),
                 ("awake" if self.map.awake.is_set() else "asleep") if self.map else "off",
                 len(self.map.tiles) if self.map else 0)

    # pubsub callbacks run on the library's thread; hand everything to the UI thread
    def on_established(self, interface, topic=pub.AUTO_TOPIC):
        self.q.put(("up", interface))

    def on_lost(self, interface, topic=pub.AUTO_TOPIC):
        self.q.put(("down", interface))

    def on_receive(self, packet, interface):
        self.q.put(("packet", (packet, interface)))

    def pump(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "iface":
                    self.connecting = False
                    if self.paused:
                        threading.Thread(target=data.close, daemon=True).start()
                    else:
                        self.iface = data
                        log.info("Connected to %s (%d nodes in its database)", self.target(),
                                 len(getattr(data, "nodes", None) or {}))
                        self.set_status("Connected", GOOD)
                        self.refresh()
                elif kind == "fail":
                    self.connecting = False
                    warn_once("connect", "Can't connect to %s: %s", self.target(), data)
                    low = data.lower()
                    if "denied" in low:
                        msg = "port busy (app open?)"
                    elif "filenotfound" in low or "cannot find" in low:
                        msg = f"node not plugged in ({self.target()})"
                    elif "timed out" in low or "timeout" in low:
                        msg = "node not answering"
                    elif "different radio" in low:
                        msg = data[:90]
                    else:
                        msg = data[:60]
                    self.set_status(f"No connection: {msg}", BAD)
                elif kind == "up" and data is self.iface:
                    self.set_status("Connected", GOOD)
                elif kind == "down" and data is self.iface:
                    log.warning("Connection to %s lost", self.target())
                    self.iface = None
                    self.set_status("Connection lost — retrying", BAD)
                elif kind == "packet":
                    self.handle_packet(*data)
                elif kind == "show":  # a second copy was started: bring this one forward instead
                    self.show()
                elif kind == "tray":
                    self.on_tray(data)
                elif kind == "ui":  # a background thread's small UI update, run here on the UI thread
                    data()
                elif kind == "airspace":
                    self.as_busy = False
                    if data is not None:
                        self.airspace = data
                        save_json(AIRSPACE_PATH, data)
                        self.draw_airspace()
                elif kind == "aircraft":
                    self.ac_busy = False
                    if data is not None:
                        self.aircraft = {a["hex"]: a for a in data}
                        self.draw_aircraft()
                        self.check_conflicts()
                    self.update_ac_credit(ok=data is not None)
                elif kind == "online":
                    self.online_busy = False
                    if data is not None:
                        self.online = data
                        self.update_map()
        except queue.Empty:
            pass
        self.root.after(200, self.pump)

    def handle_packet(self, packet, interface):
        if interface is not self.iface:
            return
        frm = packet.get("fromId")
        if packet.get("rxRssi") is not None and frm:
            self.rssi[frm] = packet["rxRssi"]
        my_id = (self.iface.getMyNodeInfo() or {}).get("user", {}).get("id")
        if frm and frm != my_id:
            now = time.time()
            self.heard[frm] = (now, packet.get("rxSnr"))
            h = self.history.setdefault(frm, {"first": now, "count": 0})
            h["last"] = now
            h["count"] = h.get("count", 0) + 1
            h["snr"] = packet.get("rxSnr", h.get("snr"))
            r = packet.get("rxRssi")
            if r is not None:
                h["rssi"] = r
                h["best_rssi"] = max(r, h.get("best_rssi", r))
            self.hist_dirty = True
        dec = packet.get("decoded", {})
        req = dec.get("requestId")
        if dec.get("portnum") == "ROUTING_APP" and req in self.pending:
            err = dec.get("routing", {}).get("errorReason", "NONE")
            who = self.pending.pop(req)[0]
            nid = self.pending_nid.pop(req, None)
            if err == "PKI_SEND_FAIL_PUBLIC_KEY":
                # the radio won't send a direct message until it has the node's public key
                self.note(f"✗ not sent to {who}: your radio doesn't have its encryption key yet. "
                          f"Asked it for the key; send again once it replies", "dm")
                if nid:
                    self.request_key(nid, force=True)
            else:
                self.note(f"✓ delivered to {who}" if err == "NONE" else f"✗ not delivered to {who} ({err})",
                          "me" if err == "NONE" else "dm")
        if dec.get("portnum") == "TEXT_MESSAGE_APP" and "text" in dec:
            to = "Everyone" if packet.get("to") == 0xFFFFFFFF else self.name(packet.get("toId"))
            m = {"id": packet.get("id"), "t": packet.get("rxTime") or time.time(),
                 "from": self.name(frm), "to": to, "text": dec["text"],
                 "fid": frm, "tid": None if to == "Everyone" else packet.get("toId"),
                 "ch": self.channel_name(packet.get("channel", 0))}
            self.log_msg(m)
            self.add_msg(m, notify=True)
            if self.note_drone_ping(dec["text"], m["t"], frm):  # False for the same ping from a 2nd detector
                self.add_drone_point(dec["text"], m["t"])  # plots it on the Drone track tab if it's an alert
            if self.msg_kind(m) == "drone" and dec["text"].lstrip().startswith("Drone:"):
                self.show_banner(dec["text"])
                self.draw_drones_main()
                self.check_conflicts()
                self.check_airspace()
            if self.push:
                drone = self.channel_name(packet.get("channel", 0)) == self.cfg.get("push_channel", "DroneAlert") \
                    or self.msg_kind(m) == "drone"
                mode = self.cfg.get("push_mode", "drone")
                if drone or mode == "all" or (mode == "dm" and m["to"] != "Everyone"):
                    self.push.add(m["from"], m["text"], drone=drone)
        # rebuilding the lists and map per packet costs ~10% CPU on a busy mesh: batch into one refresh a second
        if not self.refresh_job:
            # big meshes make each refresh slower, so space them out: never more than ~10% of the time
            delay = min(5000, max(1000, int(self.refresh_cost * 10_000)))
            self.refresh_job = self.root.after(delay, self.batched_refresh)

    def batched_refresh(self):
        self.refresh_job = None
        if self.iface:
            t0 = time.perf_counter()
            self.refresh()
            self.refresh_cost = time.perf_counter() - t0

    # ---------- background transient-node discovery ----------
    def _schedule_auto_search(self, delay=None):
        """(Re)arm the background discovery. [delay] seconds, else the configured
        interval (floored at AUTO_SEARCH_MIN). Cancels any pending run first, so a
        cadence change takes effect promptly and never double-schedules. 0 = off."""
        try:
            if self._auto_after is not None:
                self.root.after_cancel(self._auto_after)
        except Exception:
            pass
        self._auto_after = None
        try:
            interval = int(self.cfg.get("auto_search_secs", AUTO_SEARCH_SECS))
        except (TypeError, ValueError):
            interval = AUTO_SEARCH_SECS
        if interval > 0:
            interval = max(AUTO_SEARCH_MIN, interval)  # floor: never hammer local airtime
        when = interval if delay is None else delay
        if when and when > 0:
            self._auto_after = self.root.after(int(when) * 1000, self._auto_search)

    def _auto_search(self):
        """Timer callback: run one lightweight, mesh-safe discovery, then re-arm.
        Skips cleanly when the radio is down."""
        try:
            if self.iface:
                threading.Thread(target=self._do_discovery, args=(self.iface,),
                                 daemon=True, name="auto-search").start()
        except Exception:
            warn_once("auto-search", "Background discovery failed to start", exc_info=True)
        finally:
            self._schedule_auto_search()

    def _do_discovery(self, iface):
        """LIGHTWEIGHT, mesh-safe background discovery. Unlike the manual Search, this sends
        only DIRECT (hopLimit=0) requests: repeaters never relay them, so the wider mesh is
        untouched — only nodes physically in range ("in the area") hear and answer, which is
        exactly what a transient-node tracker wants. One small node-info request per channel,
        plus a direct nudge to the tracked drone node. No multi-hop, no ping-all."""
        if iface is not self.iface:
            return
        try:
            from meshtastic import telemetry_pb2, portnums_pb2, mesh_pb2, BROADCAST_ADDR
            uu = (iface.getMyNodeInfo() or {}).get("user", {}) or {}
            myuser = mesh_pb2.User(id=uu.get("id", ""), long_name=uu.get("longName", ""),
                                   short_name=uu.get("shortName", ""))
        except Exception:
            warn_once("auto-search", "Background discovery setup failed", exc_info=True)
            return
        dchan = self.channel_index(self.cfg.get("push_channel", "DroneAlert"))
        sent = 0
        # 0-hop broadcast node-info request: only direct neighbours answer and nothing is
        # relayed onward, so this costs the wider mesh nothing.
        for ch in sorted({0, dchan}):
            try:
                iface.sendData(myuser, destinationId=BROADCAST_ADDR,
                               portNum=portnums_pb2.PortNum.NODEINFO_APP,
                               wantResponse=True, channelIndex=ch, hopLimit=0)
                sent += 1
            except Exception as e:
                warn_once("auto-search", "Background broadcast failed: %s", e)
        # direct 0-hop nudge to the node we actually track, so it stays fresh
        dn = self.cfg.get("drone_node_id")
        if dn:
            try:
                iface.sendData(telemetry_pb2.Telemetry(), destinationId=dn,
                               portNum=portnums_pb2.PortNum.TELEMETRY_APP,
                               wantResponse=True, channelIndex=dchan, hopLimit=0)
                sent += 1
            except Exception as e:
                warn_once("auto-search", "Drone nudge failed: %s", e)
        log.info("Auto-discovery: %d direct (0-hop) request(s) sent", sent)

    # ---------- manual search ----------
    def search_now(self):
        """Refresh the window now, then actively ask quiet nodes to check in.

        The hub only hears a node when it transmits, and the drone detector beacons rarely,
        so it keeps dropping out of the Active list. This redraws immediately and pings each
        known node (asking for a response) so a live one reappears straight away."""
        if self.iface:
            self.refresh()  # instant redraw, even if we can't ping
        if not self.iface:
            self.note("Search: can't search, the radio isn't connected.", "dm")
            return
        now = time.time()
        if now - self._last_search < SEARCH_GAP:
            self.note("Search: just searched, give the nodes a few seconds to reply.", "dm")
            return
        self._last_search = now
        threading.Thread(target=self._do_search, args=(self.iface,), daemon=True, name="search").start()

    def _do_search(self, iface):
        """Ping every radio-known node (and the drone detector by name/id) so they announce
        themselves, AND broadcast a discovery so transient/unknown nodes check in. Runs off the
        UI thread; replies arrive normally and update the lists. This is the thorough MANUAL
        search (Search button); the background tracker uses the lighter 0-hop _do_discovery."""
        if iface is not self.iface:
            return
        try:
            from meshtastic import telemetry_pb2, portnums_pb2, mesh_pb2, BROADCAST_ADDR
            my_id = (iface.getMyNodeInfo() or {}).get("user", {}).get("id")
            nodes = dict(iface.nodes or {})
        except Exception:
            warn_once("search", "Couldn't read nodes to search", exc_info=True)
            return
        dchan = self.channel_index(self.cfg.get("push_channel", "DroneAlert"))
        targets = {}  # node id -> (name, channel index to reach it on)
        for nid, n in nodes.items():
            if nid == my_id:
                continue
            u = n.get("user", {}) or {}
            name = u.get("longName") or u.get("shortName") or nid
            is_drone = "drone" in name.lower() or nid == self.cfg.get("drone_node_id")
            if is_drone and self.cfg.get("drone_node_id") != nid:
                self.cfg["drone_node_id"] = nid  # remember it so we can still reach it once it's quiet
                save_cfg(self.cfg)
            targets[nid] = (name, dchan if is_drone else 0)
        dn = self.cfg.get("drone_node_id")
        if dn and dn not in targets:  # ask for the drone node even if the radio has dropped it
            targets[dn] = (self.history.get(dn, {}).get("name", "Drone Detect"), dchan)
        pinged = []
        for nid, (name, ch) in list(targets.items())[:8]:  # cap, so a big mesh can't flood the air
            try:
                iface.sendData(telemetry_pb2.Telemetry(), destinationId=nid,
                               portNum=portnums_pb2.PortNum.TELEMETRY_APP,
                               wantResponse=True, channelIndex=ch, hopLimit=3)
                pinged.append(name)
                log.info("Search: pinged %s (%s) on channel %d", name, nid, ch)
            except Exception as e:
                warn_once("search", "Search ping to %s failed: %s", nid, e)
        # Real search: also BROADCAST a node-info request on each channel we use,
        # so nodes the radio doesn't already know announce themselves — a true
        # discovery, not just re-pinging the hubs already in the node DB. Firmware
        # replies to a NodeInfo request with its own NodeInfo, so any node in
        # range (even one the hub has forgotten) checks back in.
        try:
            uu = (iface.getMyNodeInfo() or {}).get("user", {}) or {}
            myuser = mesh_pb2.User(id=uu.get("id", ""),
                                   long_name=uu.get("longName", ""),
                                   short_name=uu.get("shortName", ""))
        except Exception:
            myuser = None
        bcast = 0
        for ch in sorted({0, dchan}):
            try:
                iface.sendData(myuser if myuser is not None else b"",
                               destinationId=BROADCAST_ADDR,
                               portNum=portnums_pb2.PortNum.NODEINFO_APP,
                               wantResponse=True, channelIndex=ch, hopLimit=2)
                bcast += 1
                log.info("Search: broadcast node-info request on channel %d (2-hop)", ch)
            except Exception as e:
                warn_once("search", "Search broadcast on ch %d failed: %s", ch, e)
        if pinged and bcast:
            msg = ("Search: broadcast a discovery to the mesh + nudged "
                   + ", ".join(pinged) + "; replies appear here and in Active.")
        elif bcast:
            msg = "Search: broadcast a discovery to the mesh; any node in range will reply."
        elif pinged:
            msg = ("Search: asked " + ", ".join(pinged)
                   + " to check in; replies appear here and in Active.")
        else:
            msg = "Search: radio isn't ready to search yet."
        self.root.after(0, lambda: self.note(msg, "me"))

    # ---------- data ----------
    def channel_name(self, index):
        """The name of our radio's channel at this index ("" for the default public one)."""
        try:
            return self.iface.localNode.channels[index].settings.name
        except Exception:
            return ""

    def channel_list(self):
        """[(index, label)] for every enabled channel on our radio; the primary is always first."""
        out = []
        try:
            for i, ch in enumerate(self.iface.localNode.channels):
                if i and not getattr(ch, "role", 0):
                    continue  # role 0 = disabled slot
                name = getattr(ch.settings, "name", "")
                out.append((i, name or ("Primary" if i == 0 else f"Channel {i}")))
        except Exception:
            pass
        return out or [(0, "Primary")]

    def channel_index(self, name):
        """Index of our radio's channel with this name, or 0 (the primary) if there isn't one."""
        try:
            for i, ch in enumerate(self.iface.localNode.channels):
                if getattr(ch.settings, "name", "") == name:
                    return i
        except Exception:
            pass
        return 0

    def name(self, node_id):
        n = (self.iface.nodes or {}).get(node_id, {}) if self.iface else {}
        u = n.get("user", {})
        return u.get("longName") or u.get("shortName") or node_id or "?"

    def refresh(self):
        iface = self.iface
        try:
            me = iface.getMyNodeInfo() or {}
            nodes = dict(iface.nodes or {})
        except Exception:
            warn_once("refresh", "Reading node data from the radio failed", exc_info=True)
            return
        my_id = me.get("user", {}).get("id")
        dm = me.get("deviceMetrics", {})
        pos = me.get("position", {})
        batt = dm.get("batteryLevel")
        batt_txt = "USB power" if batt and batt > 100 else (f"{batt}%" if batt is not None else "?")
        if dm.get("voltage"):
            batt_txt += f" ({dm['voltage']:.2f} V)"
        if real_pos(pos):
            gps = f"GPS {pos['latitude']:.5f}, {pos['longitude']:.5f}"
            if pos.get("satsInView"):
                gps += f" · {pos['satsInView']} sats"
        else:
            gps = "GPS: no fix yet"
        util = dm.get("channelUtilization")
        self.status.config(text=f"{me.get('user', {}).get('longName', 'Connected')}"
                                f" ({me.get('user', {}).get('shortName', '')})")
        self.info.config(text=f"Battery {batt_txt}   ·   {gps}"
                              + (f"\nChannel busy {util:.1f}%" if util is not None else "")
                              + f"   ·   {max(len(nodes) - 1, 0)} other nodes")

        now = time.time()
        window = max(1, int(self.cfg.get("active_mins", ACTIVE_MINS))) * 60
        others = []  # (last heard, id, name, node) for every node except us
        me_node = None
        for nid, n in nodes.items():
            u = n.get("user", {})
            name = u.get("longName") or u.get("shortName") or nid
            our_t, our_snr = self.heard.get(nid, (None, None))
            # Our own saved record counts too: the radio's timestamps can't be trusted (a hub with no GPS
            # was seen keeping day-old times while we were receiving its packets), and ours survive restarts.
            saved = self.history.get(nid, {}).get("last") or 0
            last = min(max(our_t or 0, n.get("lastHeard") or 0, saved), now)  # a fast node clock mustn't show "in the future"
            n = dict(n, lastHeard=last or None, snr=our_snr if our_t else n.get("snr"))
            if nid == my_id:
                me_node = (name, n)
                continue
            others.append((last, nid, name, n))
            # keep the saved history in step with what the radio knows (names, older sightings)
            h = self.history.get(nid)
            if h is None and last:
                h = self.history[nid] = {"first": last, "last": last, "count": 0}
                self.hist_dirty = True
            if h is not None:
                if h.get("name") != name:
                    h["name"], self.hist_dirty = name, True
                p = n.get("position", {})
                if real_pos(p) and (h.get("lat"), h.get("lon")) != (p["latitude"], p["longitude"]):
                    h["lat"], h["lon"], self.hist_dirty = p["latitude"], p["longitude"], True
                role = u.get("role", "CLIENT")
                if h.get("role") != role:
                    h["role"], self.hist_dirty = role, True
                if last > h.get("last", 0):
                    h["last"], self.hist_dirty = last, True
        others.sort(key=lambda o: o[0], reverse=True)

        # Active tab: us, then everyone heard within the window
        rows = []
        if me_node:
            name, n = me_node
            rows.append((my_id, ("me",), (
                name, "-", "-", "", f"{batt}%" if batt is not None and batt <= 100 else "USB", "me")))
        # A radio whose clock wasn't set stores the nodes it hears with no time. They're still real,
        # so list them (after the timed ones, marked "?") instead of hiding them as if never heard.
        self.untimed = {nid: name for last, nid, name, n in others if not last and n.get("user")}
        for last, nid, name, n in sorted(others, key=lambda o: not o[0]):
            if nid in self.untimed:
                age = "?"
            elif not last or now - last > window:
                continue
            else:
                age = ago(last)
            b = n.get("deviceMetrics", {}).get("batteryLevel")
            rows.append((nid, ("fresh",) if last and now - last < 300 else (), (
                name,
                f"{n['snr']:.1f}" if n.get("snr") is not None else "-",
                self.rssi.get(nid, "-"),
                n.get("hopsAway", "-"),
                ("USB" if b > 100 else f"{b}%") if b is not None else "-",
                age)))
        self.sync_tree(self.tree, rows)
        active = len(rows)  # our own node counts as active while connected

        # History tab: every node we've ever picked up
        fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%m/%d %H:%M") if t else "-"
        rows = []
        for nid, h in sorted(self.history.items(), key=lambda kv: kv[1].get("last", 0), reverse=True):
            is_active = now - h.get("last", 0) <= window
            rows.append((nid, ("fresh",) if is_active else ("gone",), (
                h.get("name", nid),
                "● active" if is_active else ago(h.get("last")),
                fmt(h.get("first")), fmt(h.get("last")),
                h.get("count", 0) or "-",
                h.get("best_rssi", "-"))))
        self.sync_tree(self.hist_tree, rows)
        self.tabs.tab(0, text=f"Active ({active})")
        self.tabs.tab(1, text=f"History ({len(self.history)})")
        self.update_map(my_id, pos, now, window)

        self.radio_nodes = [(nid, name) for _, nid, name, _ in others]
        self.sync_dest()

    def sync_dest(self):
        """Message-to list: every node our radio knows (newest first), then every other node pinned on the
        map (heard before, or from the internet map). Any of them can be sent a direct message."""
        my_id = getattr(self, "last_me", (None,))[0]
        online = getattr(self, "online", {})
        names, extra = {}, []
        for nid, name in self.radio_nodes:
            names[nid] = name
        for nid, p in getattr(self, "map_points", {}).items():
            if nid == my_id or nid in names:
                continue
            o = online.get(nid) if nid not in self.history else None
            name = o["name"] if o else self.history.get(nid, {}).get("name", nid)
            extra.append((p[5] or 0, nid, name, bool(o)))
        labels = ["Everyone"] + [f"{name} [{nid}]" for nid, name in self.radio_nodes if nid != my_id]
        for _, nid, name, internet in sorted(extra, key=lambda e: e[0], reverse=True):
            names[nid] = name
            labels.append(f"{name} (internet) [{nid}]" if internet else f"{name} [{nid}]")
        cur = self.dest.get()
        cur_id = cur.rsplit("[", 1)[1].rstrip("]") if "[" in cur else None
        if cur_id and cur not in labels:
            # the chosen node dropped off the map (or was picked from History): keep it while it's chosen,
            # so a refresh never swaps the recipient to Everyone mid-message
            labels.append(cur)
            names.setdefault(cur_id, self.dest_names.get(cur_id, cur_id))
        self.dest_names = names
        if list(self.dest["values"]) != labels:
            self.dest["values"] = labels
        for box in list(getattr(self, "dest_boxes", [])):
            try:
                cur = box.get()
                want = labels + ([cur] if cur not in labels and cur != "Everyone" else [])
                if list(box["values"]) != want:
                    box["values"] = want
            except tk.TclError:
                self.dest_boxes.remove(box)  # its window was closed

    def set_dest(self, nid):
        """Point the Message-to list at a node, adding it if it isn't listed (e.g. picked from History)."""
        match = [v for v in self.dest["values"] if v.endswith(f"[{nid}]")]
        if not match:
            name = self.history.get(nid, {}).get("name") or getattr(self, "online", {}).get(nid, {}).get("name") or nid
            self.dest_names[nid] = name
            match = [f"{name} [{nid}]"]
            self.dest["values"] = list(self.dest["values"]) + match
        self.dest.set(match[0])
        self.entry.focus_set()

    def sync_tree(self, tree, rows):
        """Make a list show `rows` [(id, tags, values)] by changing only what differs. Rebuilding every row
        each second was the main cost on big meshes, and re-selecting the row fired the click handler."""
        cache = self.row_cache.setdefault(str(tree), {})
        want = {iid for iid, _, _ in rows}
        gone = [iid for iid in tree.get_children() if iid not in want]
        if gone:
            tree.delete(*gone)
        for iid in gone:
            cache.pop(iid, None)
        for i, (iid, tags, values) in enumerate(rows):
            if iid not in cache:
                tree.insert("", i, iid=iid, tags=tags, values=values)
            else:
                if cache[iid] != (tags, values):
                    tree.item(iid, tags=tags, values=values)
                if tree.index(iid) != i:
                    tree.move(iid, "", i)
            cache[iid] = (tags, values)

    def update_map(self, my_id=None, my_pos=None, now=None, window=None):
        if not self.map:
            return
        if my_pos is None:  # called without fresh radio data (online refresh / not connected)
            my_id, my_pos = self.last_me
        self.last_me = (my_id, my_pos)
        now = now or time.time()
        window = window or max(1, int(self.cfg.get("active_mins", ACTIVE_MINS))) * 60
        points = {}  # node id -> (lat, lon, label, colour, is_repeater, last heard)
        home = self.home()
        if my_id and real_pos(my_pos):
            points[my_id] = (my_pos["latitude"], my_pos["longitude"], "Me", ACCENT, False, now)
        elif my_id and home:
            # our node has no GPS (or keeps it private): show it at the home location, which never leaves this PC
            points[my_id] = (*home, "Me (home)", ACCENT, False, now)
        anchor = points[my_id][:2] if my_id in points else home
        # nodes heard by radio that never sent a location still go on the map: a hollow pin in a ring
        # around us, so every node shows up the moment it's heard
        self.unlocated = set()
        ring = []
        span = self.seen_span(window)
        for nid, h in self.history.items():
            if span is not None and now - h.get("last", 0) > span:
                continue  # older than the map's "Show" choice
            fresh = now - h.get("last", 0) <= window
            rep = h.get("role") in REPEATER_ROLES
            label = h.get("name", nid) + (" [R]" if rep else "") + ("" if fresh else f" ({ago(h.get('last'))})")
            color = GOOD if fresh else DIM
            if real_pos({"latitude": h.get("lat"), "longitude": h.get("lon")}):
                points[nid] = (h["lat"], h["lon"], label, color, rep, h.get("last", 0))
            elif nid in self.online:
                o = self.online[nid]  # it's on the internet map: borrow that location, keep our colour
                points[nid] = (o["lat"], o["lon"], label + " (location via internet)", color, rep, h.get("last", 0))
            elif anchor:
                ring.append((nid, label + " (no location)", color, rep, h.get("last", 0)))
        for nid, name in self.untimed.items():  # known to our radio but never timestamped: still on the map
            if nid in points or any(r[0] == nid for r in ring):
                continue
            label = name + " (time unknown)"
            if nid in self.online:
                o = self.online[nid]
                points[nid] = (o["lat"], o["lon"], label + " (location via internet)", DIM, False, 0)
            elif anchor:
                ring.append((nid, label, DIM, False, 0))
        if ring:
            # ~70 px from the anchor whatever the zoom, so the ring stays readable when zooming
            mpp = 156543.03 * math.cos(math.radians(anchor[0])) / 2 ** round(self.map.zoom)
            spots = circle(anchor[0], anchor[1], 70 * mpp, points=max(len(ring), 4))
            for i, (nid, label, color, rep, last) in enumerate(sorted(ring)):
                lat, lon = spots[i if len(ring) >= 4 else i + 1]  # a lone pin sits above the anchor
                points[nid] = (lat, lon, label, color, rep, last)
                self.unlocated.add(nid)
        accuracy = {}  # node id -> blur radius in metres, internet nodes only
        short = {}     # node id -> on-map text for internet nodes (full detail goes in the bottom bar)
        zoomed_in = round(self.map.zoom) >= 10
        if self.cfg.get("online_nodes", True):
            for nid, o in self.online.items():
                if nid in points:
                    continue  # our own radio heard it: trust that over the internet copy
                rep = o["role"] in REPEATER_ROLES
                acc = f" ±{o['acc'] / 1000:.1f} km" if o["acc"] >= 1000 else (f" ±{o['acc']:.0f} m" if o["acc"] else "")
                label = f"{o['name']}{' [R]' if rep else ''} (online{acc} · {ago(o['seen'])})"
                points[nid] = (o["lat"], o["lon"], label, ONLINE, rep, o["seen"])
                short[nid] = o["name"][:22] if zoomed_in else ""
                if o["acc"] >= 150:
                    accuracy[nid] = o["acc"]
        self.map_points = points
        for nid in list(self.markers):
            if nid not in points:
                self.markers.pop(nid).delete()
        # faint circle showing how much an owner has blurred their location
        for nid in list(self.acc_polys):
            poly, key = self.acc_polys[nid]
            if accuracy.get(nid) is None or key != (points[nid][:2], accuracy[nid]):
                poly.delete()
                del self.acc_polys[nid]
        for nid, acc in accuracy.items():
            if nid not in self.acc_polys:
                lat, lon = points[nid][:2]
                poly = self.map.set_polygon(circle(lat, lon, acc), outline_color=ONLINE, fill_color=None,
                                            border_width=1)
                self.acc_polys[nid] = (poly, ((lat, lon), acc))
        # De-clutter overlapping name labels: when two nodes sit close on screen
        # their text (drawn below each pin) piles on top of each other. Keep the
        # freshest node of a cluster labelled and blank its close neighbours (their
        # names are still in the pin card + the bottom bar). Screen spacing comes
        # from web-mercator metres-per-pixel at the current zoom (same formula used
        # above). LABEL_MIN_PX is the min pixel gap before a neighbour is hidden.
        LABEL_MIN_PX = 26
        crowded = set()
        if round(self.map.zoom) >= 10:
            z = 2 ** round(self.map.zoom)
            kept = []  # (lat, lon) of nodes that keep their label
            for nid in sorted(points, key=lambda n: (n != my_id, -points[n][5])):
                lat, lon = points[nid][:2]
                mpp = 156543.03 * math.cos(math.radians(lat)) / z  # metres/pixel here
                overlaps = False
                for klat, klon in kept:
                    dlat_m = (lat - klat) * 111320.0
                    dlon_m = (lon - klon) * 111320.0 * math.cos(math.radians((lat + klat) / 2))
                    if mpp > 0 and math.hypot(dlat_m, dlon_m) / mpp < LABEL_MIN_PX:
                        overlaps = True
                        break
                if overlaps:
                    crowded.add(nid)
                else:
                    kept.append((lat, lon))
        for nid, (lat, lon, label, color, rep, _) in points.items():
            label = short.get(nid, label)
            m = self.markers.get(nid)
            outline = WARN if rep else PANEL
            if nid in self.unlocated:  # hollow pin: placed for visibility, not a real location
                color, outline = PANEL, (WARN if rep else color)
            if (round(self.map.zoom) < 10 or nid in crowded) and nid != my_id:
                label = ""  # zoomed out / overlapping: hide the name (still in the bar + pin card)
            if m and (m.marker_color_circle, m.marker_color_outside) != (color, outline):
                m.delete()  # colours can't be changed in place
                m = None
            if m:
                if m.position != (lat, lon):
                    m.set_position(lat, lon)
                if m.text != label:
                    m.set_text(label)
            else:
                self.markers[nid] = self.map.set_marker(
                    lat, lon, text=label, text_color=FG, font=("Segoe UI", 8),
                    marker_color_circle=color, marker_color_outside=outline,
                    icon=self.map.pin(color, outline), icon_anchor="s",
                    command=lambda m: self.show_node_info(m.data), data=nid)
        if points and not self.map_fitted:
            self.fit_map()
            self.map_fitted = True
        if self.map_idx < 0:
            self.update_cycle_label()
        self.sync_dest()  # online refreshes add pins without a radio refresh

    def cycle_list(self):
        # newest-heard first; "Nodes" = everything that isn't a repeater
        keep = {"All": lambda p: True, "Nodes": lambda p: not p[4], "Repeaters": lambda p: p[4]}[self.map_filter]
        return [nid for nid, p in sorted(self.map_points.items(), key=lambda kv: kv[1][5], reverse=True)
                if keep(p)]

    def update_cycle_label(self, nid=None, pos=0, total=None):
        total = len(self.cycle_list()) if total is None else total
        if nid is None:
            what = {"All": "nodes", "Nodes": "nodes", "Repeaters": "repeaters"}[self.map_filter]
            text = f"{total} {what} on map" if total else f"no {what} on map"
        else:
            text = f"{self.map_points[nid][2]}  ·  {pos}/{total}  ⓘ"
        self.cycle_nid = nid
        self.cycle_lbl.config(text=text, fg=FG if nid else DIM, cursor="hand2" if nid else "")

    def cycle_map(self, step):
        ids = self.cycle_list()
        if not ids:
            self.map_idx = -1
            self.update_cycle_label()
            return
        self.map_idx = (self.map_idx + step) % len(ids) if self.map_idx >= 0 else (0 if step > 0 else len(ids) - 1)
        nid = ids[self.map_idx]
        self.map_fitted = True  # user is navigating: don't auto-fit over them later
        lat, lon = self.map_points[nid][:2]
        self.map.set_position(lat, lon)
        if round(self.map.zoom) < 13:
            self.map.set_zoom(13)
        self.update_cycle_label(nid, self.map_idx + 1, len(ids))

    def cycle_filter(self):
        self.map_filter = MAP_FILTERS[(MAP_FILTERS.index(self.map_filter) + 1) % len(MAP_FILTERS)]
        self.filter_btn.config(text=self.map_filter)
        self.cfg["map_filter"] = self.map_filter
        save_cfg(self.cfg)
        self.map_idx = -1
        self.update_cycle_label()

    def node_details(self, nid):
        """Everything we know about one node, as (title, [(section, [(label, value)])], (lat, lon))."""
        radio = (self.iface.nodes or {}).get(nid, {}) if self.iface else {}
        h = self.history.get(nid, {})
        o = self.online.get(nid, {}) if hasattr(self, "online") else {}
        raw = o.get("raw", {})
        u = radio.get("user", {})
        p = radio.get("position", {})
        dm = radio.get("deviceMetrics", {})
        env = radio.get("environmentMetrics", {})
        my_id, my_pos = getattr(self, "last_me", (None, {}))
        is_me = nid == my_id

        lat, lon = (p["latitude"], p["longitude"]) if real_pos(p) else (None, None)
        if lat is None and real_pos({"latitude": h.get("lat"), "longitude": h.get("lon")}):
            lat, lon = h["lat"], h["lon"]
        if lat is None and o:
            lat, lon = o["lat"], o["lon"]
        if radio or h:
            source = "This node (you)" if is_me else ("Heard by your radio" + (" via MQTT" if radio.get("viaMqtt") else ""))
        else:
            source = f"Internet only (meshmap.net, reported by {o.get('gateways', 0)} gateway(s))"
        name = (u.get("longName") or h.get("name") or o.get("name") or nid).strip()

        def num(v, fmt):
            return fmt.format(v) if isinstance(v, (int, float)) else None

        batt = dm.get("batteryLevel", raw.get("batteryLevel"))
        acc = precision_m(p.get("precisionBits")) or o.get("acc")
        unlocated = nid in getattr(self, "unlocated", ())
        if unlocated:
            lat = lon = None  # the ring spot is made up; don't report it as a location
        here = (my_pos["latitude"], my_pos["longitude"]) if my_pos.get("latitude") is not None else None
        dist_txt = None
        if lat is not None and (here or self.home()):
            dist = km_between(here or self.home(), (lat, lon))
            dist_txt = f"{dist:.2f} km / {dist * 0.621371:.2f} mi" + ("" if here else " from home")
        sections = [
            ("Identity", [
                ("Name", name),
                ("Short name", u.get("shortName") or raw.get("shortName")),
                ("Node ID", nid),
                ("Node number", str(int(nid[1:], 16)) if nid.startswith("!") else None),
                ("Hardware", u.get("hwModel") or raw.get("hwModel")),
                ("Role", u.get("role") or h.get("role") or o.get("role") or "CLIENT"),
                ("Firmware", raw.get("fwVersion")),
                ("Licensed (ham)", "yes" if u.get("isLicensed") else None),
                ("Public key", (u.get("publicKey") or "")[:16] + "…" if u.get("publicKey") else None),
                ("Favorite", "yes" if radio.get("isFavorite") else None),
                ("Source", source),
            ]),
            ("Location", [
                ("Latitude", num(lat, "{:.6f}")),
                ("Longitude", num(lon, "{:.6f}")),
                ("Altitude", num(p.get("altitude", raw.get("altitude")), "{} m")),
                ("Accuracy", (f"±{acc / 1000:.1f} km" if acc >= 1000 else f"±{acc:.0f} m") if acc else None),
                ("Satellites", p.get("satsInView")),
                ("Position time", when(p.get("time"))),
                ("Distance", dist_txt if not is_me else None),
            ] if lat is not None else [("Position", "not shared — hollow pin is placed near you so the node is visible"
                                        if unlocated else "not shared")]),
            ("Radio", [
                ("Last heard", when(radio.get("lastHeard") or h.get("last") or o.get("seen"))),
                ("First seen", when(h.get("first"))),
                ("Times heard", h.get("count") or None),
                ("SNR", num(radio.get("snr", h.get("snr")), "{:.2f} dB")),
                ("RSSI", num(self.rssi.get(nid, h.get("rssi")), "{} dBm")),
                ("Best RSSI", num(h.get("best_rssi"), "{} dBm")),
                ("Hops away", radio.get("hopsAway")),
                ("Region / preset", " / ".join(x for x in (raw.get("region"), raw.get("modemPreset")) if x) or None),
                ("Default channel", {True: "yes", False: "no"}.get(raw.get("hasDefaultCh"))),
                ("Nodes it hears", raw.get("onlineLocalNodes")),
            ]),
            ("Device", [
                ("Battery", ("USB power" if batt > 100 else f"{batt}%") if isinstance(batt, (int, float)) else None),
                ("Voltage", num(dm.get("voltage", raw.get("voltage")), "{:.2f} V")),
                ("Channel busy", num(dm.get("channelUtilization", raw.get("chUtil")), "{:.1f}%")),
                ("Airtime (TX)", num(dm.get("airUtilTx", raw.get("airUtilTx")), "{:.1f}%")),
                ("Uptime", span(dm.get("uptimeSeconds", raw.get("uptime")))),
            ]),
            ("Environment", [
                ("Temperature", num(env.get("temperature", raw.get("temperature")), "{:.1f} °C")),
                ("Humidity", num(env.get("relativeHumidity", raw.get("relativeHumidity")), "{:.0f}%")),
                ("Pressure", num(env.get("barometricPressure", raw.get("barometricPressure")), "{:.1f} hPa")),
            ]),
        ]
        sections = [(s, [(k, v) for k, v in rows if v not in (None, "")]) for s, rows in sections]
        return name, [(s, rows) for s, rows in sections if rows], (lat, lon) if lat is not None else None

    def show_node_info(self, nid):
        title, sections, pos = self.node_details(nid)
        if self.info_win and self.info_win.winfo_exists():
            self.info_win.destroy()  # one card at a time
        win = self.info_win = tk.Toplevel(self.root, bg=BG)
        win.title(f"{title} — node info")
        win.transient(self.root)
        win.attributes("-topmost", self.cfg.get("on_top", False))
        win.geometry(f"+{self.root.winfo_pointerx() + 12}+{max(0, self.root.winfo_pointery() - 40)}")
        body = tk.Frame(win, bg=PANEL)
        body.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        # tab stop instead of space padding: spaces don't line up in a proportional font
        txt = tk.Text(body, bg=PANEL, fg=FG, relief="flat", wrap="word", font=("Segoe UI", 9),
                      width=56, padx=10, pady=8, tabs=("110p",))
        txt.tag_configure("head", foreground=ACCENT, font=("Segoe UI", 9, "bold"), spacing1=6)
        txt.tag_configure("label", foreground=DIM)
        txt.tag_configure("value", lmargin2=147)  # wrapped values stay in their column
        txt.insert("end", title + "\n", "head")
        for section, rows in sections:
            txt.insert("end", section.upper() + "\n", "head")
            for label, value in rows:
                txt.insert("end", f"{label}\t", "label")  # node names come from strangers: insert as plain text only
                txt.insert("end", f"{value}\n", "value")
        txt.config(state="disabled")
        txt.pack(side="left", fill="both", expand=True)
        # size to what's actually shown: wrapped values and the gaps above headings take extra lines
        # (only measurable once the window is on screen; before that the text box is 1 pixel wide)
        try:
            win.wait_visibility(txt)
            shown = (txt.count("1.0", "end", "displaylines") or [0])[0] + len(sections) // 2 + 1
        except tk.TclError:  # closed before it appeared
            return
        fits = max(12, (win.winfo_screenheight() - 200) // 19)  # leave room for the buttons on small screens
        txt.config(height=min(shown, fits))
        if shown > fits:
            bar = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
            txt.config(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y", before=txt)

        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=8, pady=8)
        btn = dict(bg=PANEL, fg=FG, relief="flat", activebackground="#2c3a4a", font=("Segoe UI", 9))
        if pos:
            def copy():
                win.clipboard_clear()
                win.clipboard_append(f"{pos[0]:.6f}, {pos[1]:.6f}")
            tk.Button(bar, text="Copy location", command=copy, **btn).pack(side="left")
            tk.Button(bar, text="Google Maps", **btn, command=lambda: webbrowser.open(
                f"https://www.google.com/maps?q={pos[0]:.6f},{pos[1]:.6f}")).pack(side="left", padx=6)
        self.card_chat = None
        if nid != getattr(self, "last_me", (None,))[0] and re.fullmatch(r"![0-9a-f]{8}", nid or ""):
            def message():
                if self.card_chat:
                    self.card_entry.focus_force()
                else:
                    self.open_card_chat(win, bar, nid, title)
            tk.Button(bar, text="Message", command=message, bg=ACCENT, fg="#000", relief="flat",
                      font=("Segoe UI", 9)).pack(side="left", padx=(6, 0))
        tk.Button(bar, text="Close", command=win.destroy, **btn).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())

    def open_card_chat(self, win, bar, nid, name):
        """Message panel inside a node card: the conversation with that node, plus a box to send it a DM."""
        self.set_dest(nid)  # the main message bar follows too, so you can reply from either place
        name = self.dest_names.get(nid, name)
        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=8, pady=(8, 0), before=bar)
        tk.Label(frame, text=f"MESSAGES WITH {name.upper()}"[:60], fg=DIM, bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        txt = tk.Text(frame, bg=PANEL, fg=FG, relief="flat", wrap="word", font=("Segoe UI", 9),
                      height=7, width=56, state="disabled", padx=6, pady=4)
        for tag, colour in (("time", DIM), ("who", ACCENT), ("dm", WARN), ("me", GOOD)):
            txt.tag_configure(tag, foreground=colour)
        txt.pack(fill="both", expand=True)
        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        entry = self.card_entry = tk.Entry(row, bg=PANEL, fg=FG, insertbackground=FG, relief="flat")
        entry.pack(side="left", fill="x", expand=True, ipady=3)

        def send(event=None):
            if self.send_text(entry.get().strip(), nid, name):
                entry.delete(0, "end")
        entry.bind("<Return>", send)
        tk.Button(row, text="Send", command=send, bg=ACCENT, fg="#000", relief="flat").pack(side="left", padx=(6, 0))
        if not self.has_key(nid):
            asked = self.request_key(nid)
            tk.Label(frame, text="Your radio doesn't have this node's encryption key yet, so a direct message "
                                 "will be refused. " + ("Asking the node for it now; " if asked else "")
                                 + "it shows up once the node replies.",
                     fg=WARN, bg=BG, font=("Segoe UI", 8), wraplength=380, justify="left").pack(anchor="w", pady=(4, 0))
        # earlier conversation, from the message log
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                lines = f.readlines()[-2000:]
        except OSError:
            lines = []
        past = []
        for line in lines:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and "text" in m and "t" in m and self.with_node(m, nid, name):
                past.append(m)
        for m in past[-30:]:
            self.chat_line(txt, m)
        if not past:
            self.chat_note(txt, "No messages yet.", ("time", "placeholder"))
        self.card_chat = (txt, nid, name)

        def closed(event):
            if event.widget is win:
                self.card_chat = None
        win.bind("<Destroy>", closed, add="+")
        win.lift()
        entry.focus_force()

    # ---------- airspace & no-fly zones (FAA open data, US only) ----------
    def toggle_airspace(self):
        on = bool(self.as_var.get())
        self.cfg["airspace_on"] = on
        save_cfg(self.cfg)
        if on:
            self.as_next = 0
            if self.map_center() is None:
                self.set_status("Set a home location in Settings to see airspace", WARN)
        self.draw_airspace()

    def maybe_fetch_airspace(self):
        if self.as_busy or time.time() < self.as_next:
            return
        if not (self.cfg.get("airspace_on") or self.fresh_drones()):
            return  # only needed for the map layer or to check a drone that's flying now
        center = self.map_center()
        if center is None:
            return
        old = self.airspace or {}
        moved = old.get("center") is None or km_between(tuple(old["center"]), center) > 20
        need_zones = moved or time.time() - old.get("zones_at", 0) > AIRSPACE_REFRESH
        need_tfr = moved or time.time() - old.get("tfr_at", 0) > TFR_REFRESH
        if not (need_zones or need_tfr):
            return
        self.as_busy = True
        self.as_next = time.time() + 300

        def work():
            try:
                self.q.put(("airspace", fetch_airspace(center, None if moved else old, need_zones, need_tfr)))
            except Exception as e:
                self.as_next = time.time() + 900  # FAA busy or offline: try again later
                warn_once("airspace", "Airspace download failed: %s", e)
                self.q.put(("airspace", None))
        threading.Thread(target=work, daemon=True, name="airspace").start()

    def active_tfrs(self, now=None):
        now = now or time.time()
        return [t for t in (self.airspace.get("tfr") or {}).values()
                if t and (t.get("end") is None or t["end"] > now) and (t.get("start") is None or t["start"] < now + 86400)]

    def draw_airspace(self):
        """Outline airport airspace (by class) and temporary flight restrictions (red) on the main map."""
        if not self.map:
            return
        for shape in self.as_shapes:
            try:
                shape.delete()
            except Exception:
                pass
        self.as_shapes = []
        if not self.cfg.get("airspace_on"):
            return
        for z in self.airspace.get("zones") or []:
            label, colour = AIRSPACE_CLASSES.get(z["type"], ("Airspace", "#4fb3ff"))
            for poly in z["polys"]:
                self.as_shapes.append(self.map.set_polygon(
                    poly[0], outline_color=colour, fill_color=None, border_width=2,
                    command=lambda _p, z=z: self.show_zone_info(z)))
        now = time.time()
        for t in self.active_tfrs(now):
            for poly in t["polys"]:
                self.as_shapes.append(self.map.set_polygon(
                    poly[0], outline_color=TFR_COLOUR, fill_color=None, border_width=3,
                    command=lambda _p, t=t: self.show_zone_info(t, tfr=True)))

    def show_zone_info(self, z, tfr=False):
        fmt = lambda v: datetime.datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M") if v else "?"
        if tfr:
            text = (f"Temporary flight restriction\n{z['name']}\nFrom {fmt(z.get('start'))} to {fmt(z.get('end'))}\n"
                    f"Altitudes {z.get('lower') or 'surface'} to {z.get('upper') or '?'} ft\n\n"
                    "Drones generally may not fly here while it's active. Check the FAA TFR list for the details.")
        else:
            label = AIRSPACE_CLASSES.get(z["type"], ("Airspace", ""))[0]
            text = (f"{z['name']}\n{label} airspace, {z.get('lower', 0)} to {z.get('upper', '?')} ft\n\n"
                    "Drones need FAA authorization (usually through LAANC) to fly here.")
        messagebox.showinfo("Airspace", text, parent=self.root)

    def zones_at(self, lat, lon, now=None):
        """FAA zones a point is inside: [(key, description)]."""
        hits = []
        for t in self.active_tfrs(now):
            try:
                if float(t.get("lower") or 0) > 1000:
                    continue  # starts well above where drones fly
            except ValueError:
                pass
            if any(point_in_rings(lat, lon, poly) for poly in t["polys"]):
                hits.append(("tfr:" + t["name"], f"inside a temporary flight restriction ({t['name']})"))
        for z in self.airspace.get("zones") or []:
            if any(point_in_rings(lat, lon, poly) for poly in z["polys"]):
                label = AIRSPACE_CLASSES.get(z["type"], ("controlled", ""))[0]
                hits.append(("zone:" + z["name"], f"inside {label} airspace ({z['name']}): needs FAA authorization"))
        for c in self.airspace.get("grid") or []:
            if any(point_in_rings(lat, lon, poly) for poly in c["polys"]):
                hits.append(("grid:" + c["apt"], f"in an airport area where drones may fly no higher than "
                                                 f"{c['ceil']} ft with authorization ({c['apt']})"))
                break
        return hits

    def check_airspace(self, now=None):
        """Warn when a drone that's flying now is inside a no-fly zone or controlled airspace,
        or (when its detector reports height) above the 400 ft limit."""
        now = now or time.time()
        found = []
        for d in self.fresh_drones(now):
            zones = self.zones_at(d["lat"], d["lon"], now)
            if d.get("agl_m") is not None and d["agl_m"] > LIMIT_AGL_M:
                zones.append(("high", f"flying {d['agl_m'] * 3.281:.0f} ft above the ground, over the 400 ft limit"))
            for key, what in zones:
                found.append((d, key, what))
                k = (d["mac"], key)
                if now - self.as_seen.get(k, 0) < AIRSPACE_REPEAT:
                    continue
                self.as_seen[k] = now
                self.airspace_hits.append({"t": now, "mac": d["mac"], "what": what})
                self.airspace_hits = self.airspace_hits[-500:]
                text = f"⚠ AIRSPACE: drone {d['vendor'] or d['mac']} is {what}"
                log.warning("Drone %s %s", d["mac"], what)
                self.panel_write("drone", [(f"{datetime.datetime.now().strftime('%m/%d %H:%M')}  {text}" + chr(10), "bad")])
                if key.startswith("tfr:"):  # a no-fly zone is urgent; controlled airspace is just noted
                    if self.cfg["sound"]:
                        alert(self.cfg)
                    if self.push:
                        self.push.add("Central Finite Curve", text, drone=True)
                    self.banner_lbl.config(text=text)
                    if not self.banner.winfo_ismapped():
                        self.banner.pack(fill="x", padx=8, pady=(0, 6), after=self.top_bar)
        return found

    # ---------- aircraft (adsb.fi) ----------
    def maps_awake(self):
        return [m for m in (self.map, getattr(self, "dmap", None)) if m is not None and m.awake.is_set()]

    def maybe_fetch_aircraft(self):
        """Ask adsb.fi for nearby aircraft, but only while aircraft are switched on and a map is on screen,
        and never more often than every AIRCRAFT_SECS. Runs on a short-lived thread."""
        awake = self.maps_awake()
        if not self.cfg.get("aircraft_on") or self.ac_busy or time.time() < self.ac_next or not awake:
            return
        center, nm = self.aircraft_area(awake[0])
        if center is None:
            return
        self.ac_radius = nm
        url = AIRCRAFT_URL.format(lat=center[0], lon=center[1], nm=nm)
        self.ac_busy = True
        self.ac_next = time.time() + AIRCRAFT_SECS

        def work():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self.q.put(("aircraft", parse_aircraft(data)))
            except Exception as e:
                self.ac_next = time.time() + AIRCRAFT_BACKOFF  # refused (429) or offline: back off
                warn_once("aircraft", "Aircraft fetch failed: %s", e)
                self.q.put(("aircraft", None))
        threading.Thread(target=work, daemon=True, name="aircraft").start()

    def aircraft_area(self, m):
        """Where to look for aircraft: like a flight radar, the area the map is showing (up to adsb.fi's
        250 nm), never smaller than the radius in Settings. While a drone is flying, centre on it instead,
        so conflict checks always cover it."""
        try:
            floor = min(250, max(1, int(self.cfg.get("aircraft_nm", AIRCRAFT_NM))))
        except (TypeError, ValueError):
            floor = AIRCRAFT_NM
        drones = self.fresh_drones()
        if drones:
            d = max(drones, key=lambda x: x["t"])
            return (d["lat"], d["lon"]), floor
        try:
            lat, lon = m.get_position()
            mpp = 156543.03 * math.cos(math.radians(lat)) / 2 ** m.zoom  # metres per pixel
            view_nm = mpp * math.hypot(m.winfo_width(), m.winfo_height()) / 2 / 1852
            return (lat, lon), int(min(250, max(floor, view_nm)))
        except (tk.TclError, ValueError, TypeError, AttributeError):
            return self.map_center(), floor

    def update_ac_credit(self, ok=True):
        """The adsb.fi credit doubles as a status line, so an empty sky doesn't look like a fault."""
        if not getattr(self, "ac_credit", None):
            return
        nm = getattr(self, "ac_radius", AIRCRAFT_NM)
        when = datetime.datetime.now().strftime("%H:%M")
        n = len(self.aircraft)
        state = (f"{n} aircraft in view ({nm} nm) · {when}" if ok
                 else "adsb.fi didn't answer, trying again in 2 min")
        self.ac_credit.config(text=f"Aircraft data: adsb.fi  ·  {state}")

    def map_center(self):
        my_id, my_pos = getattr(self, "last_me", (None, {}))
        if real_pos(my_pos or {}):
            return my_pos["latitude"], my_pos["longitude"]
        return self.home()

    def toggle_aircraft(self):
        on = bool(self.ac_var.get())
        self.cfg["aircraft_on"] = on
        save_cfg(self.cfg)
        if on:
            self.ac_next = 0
            self.ac_credit.place(relx=0.0, rely=1.0, x=6, y=-44, anchor="sw")
            if self.map_center() is None:
                self.set_status("Set a home location in Settings to see aircraft", WARN)
        else:
            self.ac_credit.place_forget()
            self.aircraft = {}
            self.draw_aircraft()

    def plane_icon(self, track, colour, heli=False):
        """A top-down plane (or helicopter) silhouette turned the way it's heading, cached per 15 degrees.
        Drawn 4x large on a 22-unit grid, then shrunk for smooth edges."""
        bucket = int(round((track or 0) / 15.0)) % 24
        key = (bucket, colour, heli)
        if key not in self.ac_icons:
            k, size = 4, 22
            img = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            sc = lambda pts: [(x * k, y * k) for x, y in pts]
            edge = "#0b1412"
            if heli:
                for a, b in (((3, 4), (19, 14)), ((19, 4), (3, 14))):           # main rotor
                    d.line(sc([a, b]), fill=edge, width=int(3.2 * k))
                    d.line(sc([a, b]), fill=colour, width=int(1.6 * k))
                d.rectangle(sc([(10.3, 13), (11.7, 21)]), fill=colour, outline=edge, width=k)   # tail boom
                d.line(sc([(8.5, 20.5), (13.5, 20.5)]), fill=colour, width=int(1.4 * k))        # tail rotor
                d.ellipse(sc([(7.5, 4.5), (14.5, 15)]), fill=colour, outline=edge, width=k)     # cabin
            else:
                d.polygon(sc([(11, 7.5), (21.5, 12.5), (21.5, 14), (12.3, 12.2), (9.7, 12.2),
                              (0.5, 14), (0.5, 12.5)]), fill=colour, outline=edge)              # wings
                d.polygon(sc([(11, 16.5), (15.5, 19.8), (15.5, 21), (11, 19.8), (6.5, 21),
                              (6.5, 19.8)]), fill=colour, outline=edge)                         # tail
                d.polygon(sc([(11, 0.5), (12.3, 2.5), (12.3, 19.5), (11, 21.5), (9.7, 19.5),
                              (9.7, 2.5)]), fill=colour, outline=edge)                          # fuselage
            if track is not None:
                img = img.rotate(-bucket * 15, resample=Image.BICUBIC)
            self.ac_icons[key] = ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))
        return self.ac_icons[key]

    def ac_label(self, a):
        alt = "ground" if a["ground"] else (f"{a['alt']:,} ft" if a["alt"] is not None else "")
        tag = "EMERGENCY " if a["emergency"] else ("Heli " if a["heli"] else "")
        return f"{tag}{a['call']} {alt}".strip()

    def draw_aircraft(self):
        """Put the latest aircraft on every map that exists, updating markers in place."""
        for m in (self.map, getattr(self, "dmap", None)):
            if m is None:
                continue
            mid = id(m)
            show_labels = round(m.zoom) >= 9
            for (k_mid, hexid) in [k for k in self.ac_markers if k[0] == mid]:
                if hexid not in self.aircraft:
                    self.ac_markers.pop((k_mid, hexid)).delete()
            for hexid, a in self.aircraft.items():
                colour = BAD if a["emergency"] else AIRCRAFT
                icon = self.plane_icon(a["track"], colour, a["heli"])
                label = self.ac_label(a) if show_labels or a["emergency"] else ""
                mk = self.ac_markers.get((mid, hexid))
                if mk is not None and getattr(mk, "ac_key", None) != (a["track"] and round(a["track"] / 15), colour):
                    mk.delete()
                    mk = None
                if mk is None:
                    mk = m.set_marker(a["lat"], a["lon"], text=label, text_color=colour, font=("Segoe UI", 8),
                                      icon=icon, icon_anchor="center",
                                      command=lambda mk: self.show_aircraft_info(mk.data), data=hexid)
                    mk.ac_key = (a["track"] and round(a["track"] / 15), colour)
                    self.ac_markers[(mid, hexid)] = mk
                else:
                    if mk.position != (a["lat"], a["lon"]):
                        mk.set_position(a["lat"], a["lon"])
                    if mk.text != label:
                        mk.set_text(label)

    def lookup_route(self, a, label):
        """Fill in "From → To" for airline flights, looked up once per callsign (only when a card opens)."""
        cs = a["call"].upper().replace(" ", "")
        if not ROUTE_RE.match(cs):
            label.config(text="Route: not an airline flight (no published route)")
            return
        cache = getattr(self, "route_cache", None)
        if cache is None:
            cache = self.route_cache = {}

        def show(route):
            if not label.winfo_exists():
                return
            if not route:
                label.config(text="Route: not found for this callsign")
            elif not route_plausible(route, a["lat"], a["lon"]):
                label.config(text="Route: the listed route doesn't match where it is, so it's not shown")
            else:
                o, d = route["_airports"][0], route["_airports"][-1]
                label.config(text=f"Route: {o.get('iata') or o.get('icao')} ({o.get('location') or o.get('name')}) → "
                                  f"{d.get('iata') or d.get('icao')} ({d.get('location') or d.get('name')})", fg=FG)
        if cs in cache:
            show(cache[cs])
            return
        label.config(text="Route: looking up…")

        def work():
            route = None
            try:
                req = urllib.request.Request(ROUTE_URL.format(prefix=cs[:2], callsign=cs),
                                             headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=10) as r:
                    route = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                route = None if e.code == 404 else False
            except Exception:
                route = False
            if route is not False:
                cache[cs] = route
            self.q.put(("ui", lambda: show(route or None)))
        threading.Thread(target=work, daemon=True, name="route").start()

    def show_aircraft_info(self, hexid):
        a = self.aircraft.get(hexid)
        if not a:
            return
        if getattr(self, "ac_win", None) is not None and self.ac_win.winfo_exists():
            self.ac_win.destroy()
        win = self.ac_win = tk.Toplevel(self.root, bg=BG)
        win.title(f"{a['call']} - aircraft")
        win.transient(self.root)
        win.attributes("-topmost", self.cfg.get("on_top", False))
        win.geometry(f"+{self.root.winfo_pointerx() + 12}+{max(0, self.root.winfo_pointery() - 40)}")
        home = self.map_center()
        dist = None
        if home:
            d = km_between(home, (a["lat"], a["lon"]))
            dist = f"{d:.1f} km / {d * 0.621371:.1f} mi from you"
        rows = [("Callsign", a["call"]), ("Registration", a["reg"]), ("Type", a["desc"] or a["type"]),
                ("Altitude", "on the ground" if a["ground"] else (f"{a['alt']:,} ft" if a["alt"] is not None else "?")),
                ("Speed", f"{a['gs']:.0f} kt" if isinstance(a["gs"], (int, float)) else ""),
                ("Heading", f"{a['track']:.0f}°" if a["track"] is not None else ""),
                ("Squawk", a["squawk"]), ("Emergency", a["emergency"] or ""),
                ("Distance", dist or ""), ("ICAO hex", a["hex"].upper())]
        txt = tk.Text(win, bg=PANEL, fg=FG, relief="flat", font=("Segoe UI", 9), width=54, padx=10, pady=8,
                      tabs=("100p",), height=sum(1 for _, v in rows if v) + 2)
        txt.tag_configure("head", foreground=AIRCRAFT, font=("Segoe UI", 9, "bold"))
        txt.tag_configure("label", foreground=DIM)
        txt.tag_configure("bad", foreground=BAD)
        txt.insert("end", (("EMERGENCY · " if a["emergency"] else "") + a["call"]) + "\n",
                   "bad" if a["emergency"] else "head")
        for k, v in rows:
            if v:
                txt.insert("end", f"{k}\t", "label")
                txt.insert("end", f"{v}\n")  # plain text only
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        route_lbl = tk.Label(win, text="", bg=BG, fg=DIM, font=("Segoe UI", 9), justify="left", wraplength=320)
        route_lbl.pack(anchor="w", padx=12, pady=(6, 0))
        self.lookup_route(a, route_lbl)
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=8, pady=8)
        btn = dict(bg=PANEL, fg=FG, relief="flat", activebackground="#2c3a4a", font=("Segoe UI", 9))
        tk.Button(bar, text="Track on adsb.fi", **btn,
                  command=lambda: webbrowser.open(f"https://globe.adsb.fi/?icao={a['hex'].lstrip('~')}")).pack(side="left")
        tk.Button(bar, text="Close", command=win.destroy, **btn).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())

    def refresh_online(self):
        self.online_busy = True
        self.online_last = time.time()
        my_id, my_pos = self.last_me
        center = (my_pos["latitude"], my_pos["longitude"]) if my_pos.get("latitude") is not None else self.home()
        if center is None:
            self.online_busy = False  # nowhere to search around until GPS or a home location is set
            return
        radius = float(self.cfg.get("online_radius_km", 100))

        def work():
            t0 = time.time()
            try:
                found = fetch_online(center, radius)
                log.info("Online nodes: %d within %.0f km (%.1f s)", len(found), radius, time.time() - t0)
                self.q.put(("online", found))
            except Exception as e:
                warn_once("online", "Online node fetch failed: %s", e)
                self.q.put(("online", None))  # offline or site down: keep what we had
        threading.Thread(target=work, daemon=True).start()

    def toggle_online(self):
        self.cfg["online_nodes"] = bool(self.online_var.get())
        save_cfg(self.cfg)
        if self.cfg["online_nodes"]:
            self.online_last = 0  # fetch straight away
        self.update_map()

    def seen_label(self):
        """Label of the map's chosen node filter; anything unknown falls back to all recorded nodes."""
        v = self.cfg.get("map_seen")
        return next((label for k, label in MAP_SEEN if k == v), MAP_SEEN[-1][1])

    def seen_span(self, window):
        """Seconds back a heard node may be and still get a pin (None = no limit)."""
        v = self.cfg.get("map_seen")
        if v == "now":
            return window
        return v if isinstance(v, int) and v > 0 else None

    def set_map_seen(self, key):
        self.cfg["map_seen"] = key
        save_cfg(self.cfg)
        self.seen_box.set(self.seen_label())
        self.map_idx = -1
        self.update_map()
        if self.map_idx < 0:
            self.update_cycle_label()

    def sync_zoom_slider(self, zoom):
        if hasattr(self, "zoom_var") and self.zoom_var.get() != zoom:
            self.zoom_var.set(zoom)
            if self.map_points:
                self.map_fitted = True  # user zoomed: leave the view where they put it
        if hasattr(self, "last_me") and ((zoom >= 10) != getattr(self, "labels_shown", None)
                                         or getattr(self, "unlocated", None)):
            self.labels_shown = zoom >= 10
            self.root.after_idle(self.update_map)  # show/hide internet-node labels, resize the no-location ring

    def on_zoom_slider(self, value):
        # wait until the slider stops moving, then zoom once (each level means new tiles to fetch)
        if int(float(value)) == round(self.map.zoom):
            return  # slider was just synced to a wheel/button zoom; nothing to do
        if self.slider_job:
            self.root.after_cancel(self.slider_job)
        self.slider_job = self.root.after(150, lambda: self.map.set_zoom(self.zoom_var.get()))

    def home(self):
        """Home location from settings, used when our node has no GPS. Stays on this PC; never sent to the mesh."""
        try:
            lat, lon = float(self.cfg["home_lat"]), float(self.cfg["home_lon"])
        except (KeyError, TypeError, ValueError):
            return None
        return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None

    def apply_map_style(self):
        base, overlay, max_zoom = MAP_STYLES.get(self.cfg.get("map_style"), MAP_STYLES["Dark"])
        # overlay first: set_tile_server() clears the tile cache and redraws with whatever overlay is set
        for mv in (self.map, getattr(self, "dmap", None)):
            if mv is None:
                continue
            mv.set_overlay_tile_server(overlay)
            mv.set_tile_server(base, max_zoom=max_zoom)
        if hasattr(self, "zoom_slider"):
            self.zoom_slider.configure(from_=max_zoom)

    def fit_map(self):
        # hollow pins sit wherever the ring puts them at this zoom, so they mustn't steer the fit
        pts = [m.position for nid, m in self.markers.items() if nid not in getattr(self, "unlocated", ())]
        if not pts:
            return
        if len(pts) == 1:
            self.map.set_position(*pts[0])
            self.map.set_zoom(13)
            return
        lats, lons = [p[0] for p in pts], [p[1] for p in pts]
        pad = 0.01
        self.map.fit_bounding_box((max(lats) + pad, min(lons) - pad), (min(lats) - pad, max(lons) + pad))

    # ---------- drone track ----------
    def note_drone_ping(self, text, t=None, detector=None, live=True):
        """Remember a drone ping: latest position per drone, its pilot, and every detector that heard it.
        Returns False when it's the same ping another detector already reported (so the track isn't doubled)."""
        mt = DRONE_RE.search(text or "")
        if not mt:
            return True
        try:
            rssi = int(mt.group(2))
            dlat, dlon = float(mt.group(3)), float(mt.group(4))
            plat, plon = float(mt.group(5)), float(mt.group(6))
        except (TypeError, ValueError):
            return True
        if not (-90 <= dlat <= 90 and -180 <= dlon <= 180):
            return True
        t = float(t or time.time())
        mac = mt.group(1).lower()
        d = self.drone_seen.get(mac)
        dup = bool(d and abs(t - d["t"]) <= PING_DUP_SECS and (round(d["lat"], 5), round(d["lon"], 5)) == (
            round(dlat, 5), round(dlon, 5)))
        if d is None:
            d = self.drone_seen[mac] = {"mac": mac, "vendor": oui_vendor(mac), "first": t, "pings": 0,
                                        "detectors": {}}
        extras = drone_extras(text)
        if not dup:
            d.update(lat=dlat, lon=dlon, t=max(t, d.get("t", 0)), rssi=rssi, pings=d["pings"] + 1)
            d.update(extras)
            if -90 <= plat <= 90 and -180 <= plon <= 180 and (plat, plon) != (0.0, 0.0):
                d["pilot"] = (plat, plon)
        if detector:
            d["detectors"][detector] = (t, rssi)
        self.ping_log.append({"t": t, "mac": mac, "lat": dlat, "lon": dlon, "rssi": rssi,
                              "plat": plat, "plon": plon, "det": detector, "dup": dup,
                              "agl": extras.get("agl_m"), "alt": extras.get("alt_m")})
        if len(self.ping_log) > 20000:
            self.ping_log = self.ping_log[-15000:]
        return not dup

    def replay_pings(self):
        """Rebuild today's drone history from the message log at start-up (no alerts), so reports and
        the map know about flights from before the app was opened."""
        start = datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp()
        for line in tail_lines(LOG_PATH, 6000, chunk=4 * 1024 * 1024):
            if "Drone:" not in line:
                continue
            try:
                m = json.loads(line)
                if float(m.get("t", 0)) >= start:
                    self.note_drone_ping(m.get("text", ""), m.get("t"), m.get("fid"), live=False)
            except (ValueError, TypeError, AttributeError):
                pass

    def fresh_drones(self, now=None):
        now = now or time.time()
        return [d for d in self.drone_seen.values() if "lat" in d and now - d["t"] <= DRONE_FRESH_SECS]

    def draw_drones_main(self):
        """Drones in the air right now also show on the main map (red), so they sit beside aircraft and nodes."""
        if not self.map:
            return
        fresh = {d["mac"]: d for d in self.fresh_drones()}
        for mac in [k for k in self.dm_markers if k not in fresh]:
            self.dm_markers.pop(mac).delete()
        for mac, d in fresh.items():
            label = f"Drone {d['vendor'] or mac[-8:]}"
            mk = self.dm_markers.get(mac)
            if mk is None:
                self.dm_markers[mac] = self.map.set_marker(
                    d["lat"], d["lon"], text=label, text_color=BAD, font=("Segoe UI", 8, "bold"),
                    icon=self.map.pin("#ff4040", "#400000"), icon_anchor="s",
                    command=lambda m: self.banner_to_map())
            elif mk.position != (d["lat"], d["lon"]):
                mk.set_position(d["lat"], d["lon"])

    def check_conflicts(self, now=None):
        """Warn when a drone that's in the air now is close to a low-flying aircraft."""
        now = now or time.time()
        drones = self.fresh_drones(now)
        if not drones or not self.aircraft:
            return []
        found = []
        for d in drones:
            for a in self.aircraft.values():
                if a["ground"] or a["alt"] is None or a["alt"] > CONFLICT_ALT_FT:
                    continue
                dist = km_between((d["lat"], d["lon"]), (a["lat"], a["lon"]))
                if dist > CONFLICT_KM:
                    continue
                if d.get("alt_m") is not None and abs(a["alt"] - d["alt_m"] * 3.281) > 1500:
                    continue  # both altitudes known and far apart vertically
                found.append((d, a, dist))
                key = (d["mac"], a["hex"])
                if now - self.conflict_seen.get(key, 0) < CONFLICT_REPEAT:
                    continue
                self.conflict_seen[key] = now
                self.conflicts.append({"t": now, "mac": d["mac"], "vendor": d["vendor"], "hex": a["hex"],
                                       "call": a["call"], "desc": a["desc"] or a["type"], "heli": a["heli"],
                                       "alt": a["alt"], "km": round(dist, 2)})
                self.conflicts = self.conflicts[-500:]
                what = ("helicopter " if a["heli"] else "aircraft ") + a["call"]
                text = (f"⚠ CONFLICT: drone {d['vendor'] or d['mac']} is {dist:.1f} km ({dist * 0.621371:.1f} mi) "
                        f"from {what} at {a['alt']:,} ft")
                log.warning("Drone/aircraft conflict: %s %.2f km from %s at %s ft", d["mac"], dist, a["call"], a["alt"])
                self.panel_write("drone", [(f"{datetime.datetime.now().strftime('%m/%d %H:%M')}  {text}" + chr(10), "bad")])
                if self.cfg["sound"]:
                    alert(self.cfg)
                if self.push:
                    self.push.add("Central Finite Curve", text, drone=True)
                self.banner_lbl.config(text=text + "   ·   " + datetime.datetime.now().strftime("%H:%M:%S"))
                if not self.banner.winfo_ismapped():
                    self.banner_pings = 0
                    self.banner.pack(fill="x", padx=8, pady=(0, 6), after=self.top_bar)
                if self.banner_job:
                    self.root.after_cancel(self.banner_job)
                self.banner_job = self.root.after(DRONE_BANNER_SECS * 1000, self.hide_banner)
        return found

    def make_report(self):
        """Write an incident report for the most recent drone flight: a self-contained web page with the
        timeline, positions, detectors, aircraft conflicts and a map picture. Opens in the browser,
        where it can be printed or saved as PDF."""
        import base64
        import html as _html
        if not self.ping_log:
            self.note("Incident report: no drone pings recorded yet.", "dm")
            return None
        mac = self.ping_log[-1]["mac"]
        pings = [p for p in self.ping_log if p["mac"] == mac]
        flight = [pings[-1]]
        for p in reversed(pings[:-1]):  # walk back until a gap of 10+ minutes: that's where this flight began
            if flight[-1]["t"] - p["t"] > 600:
                break
            flight.append(p)
        flight.reverse()
        uniq = [p for p in flight if not p["dup"]]
        start, end = flight[0]["t"], flight[-1]["t"]
        fmt = lambda t: datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
        dets = {}
        for p in flight:
            if p["det"]:
                dets.setdefault(p["det"], []).append(p["rssi"])
        name = lambda nid: self.history.get(nid, {}).get("name") or (self.name(nid) if self.iface else nid)
        home = self.home()
        near = min((km_between(home, (p["lat"], p["lon"])) for p in uniq), default=None) if home else None
        pilots = sorted({(round(p["plat"], 5), round(p["plon"], 5)) for p in flight if (p["plat"], p["plon"]) != (0.0, 0.0)})
        confl = [c for c in self.conflicts if c["mac"] == mac and start - 60 <= c["t"] <= end + DRONE_FRESH_SECS]
        rssis = [p["rssi"] for p in uniq]
        e = lambda v: _html.escape(str(v), quote=True)  # everything from the air or the mesh is untrusted text
        gm = lambda la, lo: f"https://www.google.com/maps?q={la:.6f},{lo:.6f}"
        img = ""
        try:
            os.makedirs(FLIGHTS_DIR, exist_ok=True)
            tmp = os.path.join(FLIGHTS_DIR, "_report_map.png")
            if self.save_map_image(tmp):
                with open(tmp, "rb") as f:
                    img = base64.b64encode(f.read()).decode("ascii")
                os.remove(tmp)
        except OSError:
            pass
        height = lambda p: f"{p['agl'] * 3.281:.0f} ft" if p.get("agl") is not None else ""
        rows = "".join(
            f"<tr><td>{e(fmt(p['t']))}</td><td><a href='{e(gm(p['lat'], p['lon']))}'>{p['lat']:.6f}, {p['lon']:.6f}</a></td>"
            f"<td>{p['plat']:.6f}, {p['plon']:.6f}</td><td>{e(height(p))}</td>"
            f"<td>{p['rssi']}</td><td>{e(name(p['det']) if p['det'] else '')}</td></tr>"
            for p in uniq)
        crow = "".join(
            f"<tr><td>{e(fmt(c['t']))}</td><td>{e(('Helicopter ' if c['heli'] else '') + c['call'])}</td><td>{e(c['desc'])}</td>"
            f"<td>{c['alt']:,} ft</td><td>{c['km']:.2f} km ({c['km'] * 0.621371:.2f} mi)</td></tr>" for c in confl)
        summary = [
            ("Drone ID (MAC)", mac), ("Manufacturer", self.drone_seen.get(mac, {}).get("vendor") or "unknown"),
            ("Remote ID serial", self.drone_seen.get(mac, {}).get("serial") or "not reported by the detector"),
            ("Highest above ground", (f"{max(p['agl'] for p in uniq if p.get('agl') is not None) * 3.281:.0f} ft"
                                      if any(p.get("agl") is not None for p in uniq) else "not reported by the detector")),
            ("First ping", fmt(start)), ("Last ping", fmt(end)),
            ("Duration", f"{(end - start) / 60:.1f} minutes"), ("Pings", f"{len(uniq)}"),
            ("Signal (RSSI)", f"{min(rssis)} to {max(rssis)} dBm" if rssis else ""),
            ("Closest to your home location", f"{near:.2f} km ({near * 0.621371:.2f} mi)" if near is not None else "home location not set"),
            ("Pilot position(s) reported", "; ".join(f"{a:.6f}, {b:.6f}" for a, b in pilots[:10]) or "none"),
            ("Detectors that heard it", ", ".join(f"{name(d)} ({len(v)} pings, best {max(v)} dBm)" for d, v in dets.items()) or "unknown"),
            ("Aircraft conflicts", str(len(confl)) if confl else "none recorded"),
            ("Airspace", "; ".join(sorted({h["what"] for h in self.airspace_hits
                                           if h["mac"] == mac and start - 60 <= h["t"] <= end + DRONE_FRESH_SECS}))
             or "no FAA zones flagged (or airspace data not downloaded)"),
        ]
        srows = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in summary)
        page = f"""<!doctype html><meta charset="utf-8"><title>Drone incident report {e(fmt(start))}</title>
<style>body{{font:14px/1.5 Segoe UI,system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#111}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:16px;margin:24px 0 8px}}table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;border-bottom:1px solid #ddd;padding:4px 8px;vertical-align:top}}th{{width:240px;color:#444}}
.muted{{color:#666;font-size:12px}}img{{max-width:100%;border:1px solid #ccc}}</style>
<h1>Drone incident report</h1><p class="muted">Made by Central Finite Curve {e(VERSION)} on {e(fmt(time.time()))}.
Positions are what the drone broadcast over Remote ID, as received by your detectors; they are not independently verified.
Height is shown only when the detector includes it in its alerts.</p>
<h2>Summary</h2><table>{srows}</table>
{f'<h2>Map</h2><img alt="Flight map" src="data:image/png;base64,{img}">' if img else ''}
{f'<h2>Aircraft conflicts</h2><table><tr><th>Time</th><th>Aircraft</th><th>Type</th><th>Altitude</th><th>Distance from drone</th></tr>{crow}</table>' if confl else ''}
<h2>Every ping</h2><table><tr><th>Time</th><th>Drone position</th><th>Pilot position</th><th>Height</th><th>RSSI</th><th>Detector</th></tr>{rows}</table>
<h2>Where to report it</h2><p>In the US, unsafe or illegal drone flights can be reported to your local police (non-emergency line,
or 911 if someone is in danger) and to the nearest FAA Flight Standards District Office. Attach this page, or print it to PDF.</p>"""
        path = os.path.join(FLIGHTS_DIR, f"report-{time.strftime('%Y%m%d-%H%M%S', time.localtime(start))}.html")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(page)
        except OSError:
            self.note("Incident report: couldn't write the file.", "dm")
            return None
        self.note(f"Incident report saved: {os.path.basename(path)} (opening it in your browser)", "me")
        webbrowser.open("file:///" + path.replace("\\", "/"))
        return path

    def show_banner(self, text):
        """Red DRONE DETECTED banner under the top buttons; it stays while pings keep coming and
        hides itself DRONE_BANNER_SECS after the last one (or when dismissed)."""
        mt = DRONE_RE.search(text or "")
        if not self.banner.winfo_ismapped():
            self.banner_pings = 0
            self.banner.pack(fill="x", padx=8, pady=(0, 6), after=self.top_bar)
        self.banner_pings += 1
        detail = ""
        if mt:
            vendor = oui_vendor(mt.group(1))
            detail = f"   {vendor + ' · ' if vendor else ''}{mt.group(1)}   RSSI {mt.group(2)} dBm"
            seen = self.drone_seen.get(mt.group(1).lower()) or {}
            if seen.get("agl_m") is not None:
                detail += f"   {seen['agl_m'] * 3.281:.0f} ft up"
            dets = len(seen.get("detectors", {}))
            if dets > 1:
                detail += f"   · heard by {dets} detectors"
        when = datetime.datetime.now().strftime("%H:%M:%S")
        pings = f"{self.banner_pings} ping{'s' if self.banner_pings != 1 else ''}"
        self.banner_lbl.config(text=f"⚠ DRONE DETECTED{detail}   ·   {pings}, last {when}")
        if self.banner_job:
            self.root.after_cancel(self.banner_job)
        self.banner_job = self.root.after(DRONE_BANNER_SECS * 1000, self.hide_banner)

    def hide_banner(self):
        if self.banner_job:
            try:
                self.root.after_cancel(self.banner_job)
            except tk.TclError:
                pass
            self.banner_job = None
        self.banner.pack_forget()

    def banner_to_map(self):
        if getattr(self, "drone_tab", None) is not None:
            self.tabs.select(self.drone_tab)
            self.root.after(300, lambda: self.dmap and self.fit_track())

    def add_drone_point(self, text, t=None, redraw=True):
        """Parse a drone alert and append its GPS to the flight track. No-op when
        the text isn't a drone alert, so it's safe to call on every message."""
        if not self.dmap:
            return
        mt = DRONE_RE.search(text or "")
        if not mt:
            return
        try:
            rssi = int(mt.group(2))
            dlat, dlon = float(mt.group(3)), float(mt.group(4))
            plat, plon = float(mt.group(5)), float(mt.group(6))
        except (TypeError, ValueError):
            return
        if not (-90 <= dlat <= 90 and -180 <= dlon <= 180):
            return
        mac = mt.group(1)
        self.track.append((dlat, dlon, rssi, t or time.time(), mac, oui_vendor(mac)))
        if len(self.track) > 5000:
            self.track = self.track[-5000:]
        if -90 <= plat <= 90 and -180 <= plon <= 180:
            self.pilot_pos = (plat, plon)
        if redraw:
            self.redraw_track(center_first=True)

    def redraw_track(self, center_first=False):
        if not self.dmap or not self.track:
            return
        pts = [(la, lo) for la, lo, *_ in self.track]
        if self.dpath:
            try:
                self.dpath.delete()
            except Exception:
                pass
            self.dpath = None
        if len(pts) >= 2:
            self.dpath = self.dmap.set_path(pts, color="#ff8a3d", width=3)
        last = pts[-1]
        if self.d_drone_marker:
            self.d_drone_marker.set_position(*last)
        else:
            self.d_drone_marker = self.dmap.set_marker(
                last[0], last[1], text="Drone",
                icon=self.dmap.pin("#ff4040", "#400000"), icon_anchor="s")
        if self.pilot_pos:
            if self.d_pilot_marker:
                self.d_pilot_marker.set_position(*self.pilot_pos)
            else:
                self.d_pilot_marker = self.dmap.set_marker(
                    self.pilot_pos[0], self.pilot_pos[1], text="Pilot",
                    icon=self.dmap.pin("#40a0ff", "#002040"), icon_anchor="s")
        if getattr(self, "track_lbl", None):
            last_pt = self.track[-1]
            ven = last_pt[5] or "unknown mfr"
            dets = len((self.drone_seen.get(str(last_pt[4] or "").lower()) or {}).get("detectors", {}))
            self.track_lbl.configure(
                text=f"{len(self.track)} pings · {ven} · RSSI {last_pt[2]} dBm"
                     + (f" · {dets} detectors" if dets > 1 else ""))
        # centre once on the first point; after that leave the view where the user left it
        if center_first and len(pts) == 1:
            self.dmap.set_position(*last)
            if round(self.dmap.zoom) < 15:
                self.dmap.set_zoom(16)

    def fit_track(self):
        pts = [(la, lo) for la, lo, *_ in self.track]
        if self.pilot_pos:
            pts.append(self.pilot_pos)
        if not pts:
            return
        if len(pts) == 1:
            self.dmap.set_position(*pts[0])
            self.dmap.set_zoom(16)
            return
        lats, lons = [p[0] for p in pts], [p[1] for p in pts]
        pad = 0.0008
        self.dmap.fit_bounding_box((max(lats) + pad, min(lons) - pad),
                                   (min(lats) - pad, max(lons) + pad))

    def clear_track(self):
        self.track = []
        self.pilot_pos = None
        for mk in (self.d_drone_marker, self.d_pilot_marker):
            try:
                mk and mk.delete()
            except Exception:
                pass
        self.d_drone_marker = self.d_pilot_marker = None
        if self.dpath:
            try:
                self.dpath.delete()
            except Exception:
                pass
            self.dpath = None
        if getattr(self, "track_lbl", None):
            self.track_lbl.configure(text="no drone pings yet")

    def load_track_file(self):
        """Replay drone alerts from messages.jsonl so the Drone tab shows today's
        path the moment the widget opens."""
        if not self.dmap:
            return
        try:
            with open(LOG_PATH, "r", encoding="utf-8") as fh:
                lines = fh.readlines()[-4000:]
        except OSError:
            return
        for line in lines:
            try:
                m = json.loads(line)
            except (ValueError, TypeError):
                continue
            self.add_drone_point(m.get("text", ""), m.get("t"), redraw=False)
        if self.track:
            self.redraw_track()
            self.fit_track()

    def save_flight(self):
        """Save the current drone track: a CSV of every ping (time, GPS, RSSI, MAC,
        manufacturer, pilot GPS) plus a PNG snapshot of the map exactly as shown."""
        if not self.track:
            self.note("Save flight: no drone pings to save yet.", "dm")
            return
        try:
            os.makedirs(FLIGHTS_DIR, exist_ok=True)
        except OSError:
            self.note("Save flight: couldn't create the flights folder.", "dm")
            return
        ts = time.strftime("%Y%m%d-%H%M%S")
        base = os.path.join(FLIGHTS_DIR, f"flight-{ts}")
        plat, plon = self.pilot_pos if self.pilot_pos else ("", "")
        try:
            with open(base + ".csv", "w", encoding="utf-8", newline="") as w:
                w.write("time,epoch,drone_lat,drone_lon,rssi_dbm,mac,manufacturer,pilot_lat,pilot_lon\n")
                for la, lo, r, t, mac, ven in self.track:
                    iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""
                    ven_c = (ven or "").replace(",", " ")
                    w.write(f"{iso},{t or ''},{la},{lo},{r},{mac or ''},{ven_c},{plat},{plon}\n")
        except OSError:
            warn_once("saveflight", "Couldn't write flight CSV", exc_info=True)
            self.note("Save flight: couldn't write the CSV.", "dm")
            return
        got_png = self.save_map_image(base + ".png")
        extra = " + map.png" if got_png else " (map image failed)"
        self.note(f"Saved flight: {os.path.basename(base)}.csv{extra} — {len(self.track)} pings", "me")

    def save_map_image(self, path):
        """Grab the Drone map exactly as shown (tiles + path + markers) to a PNG."""
        if not self.dmap:
            return False
        try:
            from PIL import ImageGrab
            self.tabs.select(self.drone_tab)      # ensure the map is the visible tab
            self.root.lift()
            self.dmap.update()
            x, y = self.dmap.winfo_rootx(), self.dmap.winfo_rooty()
            x2 = x + self.dmap.winfo_width()
            y2 = y + self.dmap.winfo_height()
            ImageGrab.grab(bbox=(x, y, x2, y2)).save(path)
            return True
        except Exception:
            warn_once("savemap", "Couldn't grab the map image", exc_info=True)
            return False

    def pick_dest(self, event=None):
        # clicking a node in either list sets it as the reply target
        sel = event.widget.selection() if event else self.tree.selection()
        if sel and sel[0] != getattr(self, "last_me", (None,))[0] and re.fullmatch(r"![0-9a-f]{8}", sel[0]):
            self.set_dest(sel[0])
        else:
            self.dest.set("Everyone")
            self.entry.focus_set()

    def send(self):
        text = self.entry.get().strip()
        dest = self.dest.get()
        node_id = dest.rsplit("[", 1)[1].rstrip("]") if "[" in dest else "^all"
        if self.send_text(text, node_id):
            self.entry.delete(0, "end")

    def send_text(self, text, node_id, name=None, channel=0):
        """Send a channel message (node_id "^all") or a direct message. True once it's handed to the radio."""
        if not text:
            return False
        if not self.iface:
            self.set_status("Not connected: message not sent", BAD)
            return False
        try:
            pkt = self.iface.sendText(text, destinationId=node_id, wantAck=node_id != "^all",
                                      channelIndex=channel)
        except Exception as e:
            log.error("Sending to %s failed", node_id, exc_info=True)
            self.set_status(f"Send failed: {e}"[:60], BAD)
            return False
        m = {"id": getattr(pkt, "id", None), "t": time.time(), "mine": True,
             "from": "Me", "to": "Everyone" if node_id == "^all" else (name or self.dest_names.get(node_id, node_id)),
             "text": text, "tid": None if node_id == "^all" else node_id,
             "ch": self.channel_name(channel) if node_id == "^all" else None}
        if node_id != "^all" and not self.has_key(node_id):
            # firmware 2.5+ refuses a direct message without the node's public key: go and fetch it
            self.request_key(node_id)
            self.set_status(f"No key for {m['to']} yet: asked for it"[:70], WARN)
        self.log_msg(m)
        self.add_msg(m, notify=False)
        if node_id != "^all" and m["id"]:
            self.pending[m["id"]] = (m["to"], time.time())
            self.pending_nid[m["id"]] = node_id
            if len(self.pending_nid) > 500:
                self.pending_nid = {k: v for k, v in self.pending_nid.items() if k in self.pending}
        return True

    def has_key(self, nid):
        n = (getattr(self.iface, "nodes", None) or {}).get(nid) or {}
        return bool(n.get("user", {}).get("publicKey"))

    def request_key(self, nid, force=False):
        """Ask one node for its node info, which carries its public key; direct messages need that key.
        At most once a minute per node, on a short-lived thread so the UI never waits on the radio."""
        iface = self.iface
        if not iface or (not force and self.has_key(nid)):
            return False
        if time.time() - self.key_asked.get(nid, 0) < 60:
            return False
        self.key_asked[nid] = time.time()

        def work():
            try:
                from meshtastic import portnums_pb2, mesh_pb2
                uu = (iface.getMyNodeInfo() or {}).get("user", {}) or {}
                me = mesh_pb2.User(id=uu.get("id", ""), long_name=uu.get("longName", ""),
                                   short_name=uu.get("shortName", ""))
                iface.sendData(me, destinationId=nid, portNum=portnums_pb2.PortNum.NODEINFO_APP,
                               wantResponse=True, channelIndex=0)
                log.info("Asked %s for its node info (public key)", nid)
            except Exception as e:
                warn_once("keyreq", "Key request failed: %s", e)
        threading.Thread(target=work, daemon=True, name="keyreq").start()
        return True

    # ---------- controls ----------
    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.disconnect()
            self.pause_btn.config(text="Resume")
            self.set_status("Paused — connection free for the app", WARN)
        else:
            self.pause_btn.config(text="Pause")
            self.last_try = 0

    def toggle_top(self):
        self.cfg["on_top"] = not self.cfg["on_top"]
        self.root.attributes("-topmost", self.cfg["on_top"])
        save_cfg(self.cfg)

    def open_settings(self):
        win = tk.Toplevel(self.root, bg=BG)
        win.title("Central Finite Curve settings")
        win.transient(self.root)
        win.grab_set()
        mode = tk.StringVar(value=self.cfg["mode"])
        port = tk.StringVar(value=self.cfg["port"])
        host = tk.StringVar(value=self.cfg["host"])
        sound = tk.BooleanVar(value=self.cfg["sound"])

        def row(r, label, widget):
            tk.Label(win, text=label, bg=BG, fg=FG).grid(row=r, column=0, sticky="w", padx=10, pady=6)
            widget.grid(row=r, column=1, sticky="we", padx=10, pady=6)

        opts = tk.Frame(win, bg=BG)
        for val, text in (("serial", "USB cable"), ("wifi", "Wi-Fi")):
            tk.Radiobutton(opts, text=text, value=val, variable=mode, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG).pack(side="left")
        row(0, "Connect by", opts)
        row(1, "USB port (or auto)", tk.Entry(win, textvariable=port, bg=PANEL, fg=FG, insertbackground=FG))
        row(2, "Wi-Fi IP address", tk.Entry(win, textvariable=host, bg=PANEL, fg=FG, insertbackground=FG))
        row(3, "Sound on new message", tk.Checkbutton(win, variable=sound, bg=BG, selectcolor=PANEL,
                                                      activebackground=BG))
        device = tk.StringVar(value=self.cfg.get("sound_device", WIN_DEFAULT))
        devrow = tk.Frame(win, bg=BG)
        ttk.Combobox(devrow, textvariable=device, state="readonly", width=34,
                     values=[WIN_DEFAULT] + list(output_devices())).pack(side="left")
        tk.Button(devrow, text="Test", bg=PANEL, fg=FG, relief="flat",
                  command=lambda: play_alert(dict(self.cfg, sound_device=device.get()))
                  ).pack(side="left", padx=6)
        row(4, "Play sound on", devrow)

        # sound choice: Windows' built-in .wav files, plus any file you browse to
        sounds = windows_sounds()
        cur = self.cfg.get("sound_file", DEFAULT_SOUND)
        if cur not in sounds.values():
            sounds[os.path.basename(cur)] = cur
        label_for = {v: k for k, v in sounds.items()}
        sound_name = tk.StringVar(value=label_for.get(cur, os.path.basename(cur)))
        sndrow = tk.Frame(win, bg=BG)
        snd_box = ttk.Combobox(sndrow, textvariable=sound_name, state="readonly", width=34,
                               values=list(sounds))
        snd_box.pack(side="left")

        def browse():
            path = filedialog.askopenfilename(parent=win, title="Pick a sound",
                                              filetypes=[("WAV sound", "*.wav")])
            if path:
                sounds[os.path.basename(path)] = path
                snd_box["values"] = list(sounds)
                sound_name.set(os.path.basename(path))

        def test():
            play_alert(dict(self.cfg, sound_device=device.get(),
                            sound_file=sounds.get(sound_name.get(), cur)))
        tk.Button(sndrow, text="Browse…", bg=PANEL, fg=FG, relief="flat",
                  command=browse).pack(side="left", padx=6)
        row(5, "Alert sound", sndrow)
        for child in devrow.winfo_children():  # the device row's Test now plays the chosen sound too
            if isinstance(child, tk.Button):
                child.config(command=test)

        active_mins = tk.StringVar(value=str(self.cfg.get("active_mins", ACTIVE_MINS)))
        row(6, "Active = heard in last (min)",
            tk.Entry(win, textvariable=active_mins, width=6, bg=PANEL, fg=FG, insertbackground=FG))
        map_style = tk.StringVar(value=self.cfg.get("map_style", "Dark"))
        row(7, "Map style", ttk.Combobox(win, textvariable=map_style, state="readonly", width=12,
                                         values=list(MAP_STYLES)))
        online_radius = tk.StringVar(value=str(int(float(self.cfg.get("online_radius_km", 100)))))
        row(8, "Online nodes within (km)",
            tk.Entry(win, textvariable=online_radius, width=6, bg=PANEL, fg=FG, insertbackground=FG))
        home = self.home()
        home_txt = tk.StringVar(value=f"{home[0]:.5f}, {home[1]:.5f}" if home else "")
        row(9, "Home location (lat, lon)\nstays on this PC",
            tk.Entry(win, textvariable=home_txt, width=24, bg=PANEL, fg=FG, insertbackground=FG))

        # Transient-node tracking cadence. All options are 0-hop (local only), so even
        # "Real-time" only touches your own airspace — never the wider mesh.
        cad_map = {"Off": 0, "Light — 3 min (default)": 180,
                   "Active — 1 min": 60, "Real-time — 20 s": 20}
        cur_cad = int(self.cfg.get("auto_search_secs", AUTO_SEARCH_SECS))
        cad_rev = {v: k for k, v in cad_map.items()}
        cur_cad_label = cad_rev.get(cur_cad)
        if cur_cad_label is None:
            cur_cad_label = f"Custom — {cur_cad} s"
            cad_map[cur_cad_label] = cur_cad
        cadence = tk.StringVar(value=cur_cad_label)
        row(10, "Transient tracking\n(0-hop, local only)",
            ttk.Combobox(win, textvariable=cadence, state="readonly", width=22,
                         values=list(cad_map)))

        keep_now = self.archive_days()
        keep_map = dict(ARCHIVE_CHOICES)
        keep_label = next((l for l, d in ARCHIVE_CHOICES if d == keep_now), None)
        if keep_label is None:
            keep_label = f"{keep_now} days"
            keep_map[keep_label] = keep_now
        keep = tk.StringVar(value=keep_label)
        row(11, "Keep chat archive", ttk.Combobox(win, textvariable=keep, state="readonly", width=22,
                                                  values=list(keep_map)))
        ac_nm = tk.StringVar(value=str(self.cfg.get("aircraft_nm", AIRCRAFT_NM)))
        row(12, "Aircraft: at least (nautical mi)", tk.Entry(win, textvariable=ac_nm, bg=PANEL, fg=FG,
                                                          insertbackground=FG))

        # Phone alerts (ntfy): topic, what to send, and a test button
        tk.Label(win, text="PHONE ALERTS (ntfy)", bg=BG, fg=DIM, font=("Segoe UI", 8, "bold")).grid(
            row=13, column=0, sticky="w", padx=10, pady=(12, 0))
        push_topic = tk.StringVar(value=self.cfg.get("push_url", ""))
        prow = tk.Frame(win, bg=BG)
        tk.Entry(prow, textvariable=push_topic, bg=PANEL, fg=FG, insertbackground=FG, width=34).pack(
            side="left", fill="x", expand=True)

        def new_topic():
            push_topic.set("https://ntfy.sh/cfc-" + "".join(random.choice("abcdefghjkmnpqrstuvwxyz23456789")
                                                          for _ in range(16)))
        tk.Button(prow, text="New private topic", command=new_topic, bg=PANEL, fg=FG, relief="flat",
                  font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))
        row(14, "Topic or ntfy link\n(blank = off)", prow)
        push_modes = dict((label, key) for key, label in PUSH_MODES)
        push_mode = tk.StringVar(value=next((l for k, l in PUSH_MODES if k == self.cfg.get("push_mode", "drone")),
                                            PUSH_MODES[0][1]))
        row(15, "Send to phone", ttk.Combobox(win, textvariable=push_mode, state="readonly", width=30,
                                              values=list(push_modes)))
        trow = tk.Frame(win, bg=BG)
        push_result = tk.Label(trow, text="Subscribe to the same topic in the ntfy app, then test.",
                               bg=BG, fg=DIM, font=("Segoe UI", 8), wraplength=300, justify="left")

        def push_test():
            url = ntfy_url(push_topic.get())
            if not url:
                push_result.config(text="Enter a topic name (letters, numbers, - or _) or an ntfy link first.",
                                   fg=BAD)
                return
            push_result.config(text="Sending…", fg=DIM)

            def work():
                ok, msg = PhonePush(url).post("Central Finite Curve test",
                                              "If you can read this, phone alerts are working.", urgent=False)
                self.q.put(("ui", lambda: push_result.winfo_exists() and push_result.config(
                    text="Sent! Check your phone." if ok else f"Didn't send: {msg}"[:120], fg=GOOD if ok else BAD)))
            threading.Thread(target=work, daemon=True, name="push-test").start()
        tk.Button(trow, text="Send test", command=push_test, bg=PANEL, fg=FG, relief="flat",
                  font=("Segoe UI", 9)).pack(side="left")
        push_result.pack(side="left", padx=(8, 0))
        trow.grid(row=16, column=1, sticky="we", padx=10, pady=(0, 6))

        def apply():
            before = (self.cfg["mode"], self.cfg["port"], self.cfg["host"])
            url = ntfy_url(push_topic.get())
            if url is not None and url != self.cfg.get("push_url", ""):
                if url:
                    self.cfg["push_url"] = url
                else:
                    self.cfg.pop("push_url", None)
                self.push = PhonePush(url) if url and not DEMO else None
                log.info("Phone alerts %s", "turned on" if url else "turned off")
            self.cfg["push_mode"] = push_modes.get(push_mode.get(), "drone")
            try:
                nm = min(250, max(1, int(float(ac_nm.get()))))
                if nm != self.cfg.get("aircraft_nm", AIRCRAFT_NM):
                    self.cfg["aircraft_nm"] = nm
                    self.ac_next = 0  # fetch again with the new radius
            except ValueError:
                pass
            new_keep = keep_map.get(keep.get(), keep_now)
            if new_keep != keep_now:
                self.cfg["archive_days"] = new_keep
                self.archive_trimmed = 0  # apply a shorter keep time on the next tick
            try:
                self.cfg["active_mins"] = max(1, int(active_mins.get()))
            except ValueError:
                pass
            try:  # transient-tracking cadence (0 = off; floored otherwise)
                new_cad = int(cad_map.get(cadence.get(), cur_cad))
                new_cad = 0 if new_cad <= 0 else max(AUTO_SEARCH_MIN, new_cad)
                if new_cad != int(self.cfg.get("auto_search_secs", AUTO_SEARCH_SECS)):
                    self.cfg["auto_search_secs"] = new_cad
                    self._schedule_auto_search(2)  # re-arm with the new cadence promptly
            except (ValueError, TypeError):
                pass
            try:
                r = min(1000, max(5, int(online_radius.get())))
                if r != int(float(self.cfg.get("online_radius_km", 100))):
                    self.cfg["online_radius_km"] = r
                    if hasattr(self, "online_last"):
                        self.online_last = 0  # re-fetch with the new radius
            except ValueError:
                pass
            try:  # accepts "41.94, -79.98" as pasted from Google Maps; blank clears it
                parts = [float(x) for x in home_txt.get().replace(",", " ").split()]
                if not parts:
                    self.cfg.pop("home_lat", None)
                    self.cfg.pop("home_lon", None)
                elif len(parts) == 2 and -90 <= parts[0] <= 90 and -180 <= parts[1] <= 180:
                    if (parts[0], parts[1]) != self.home():
                        self.cfg["home_lat"], self.cfg["home_lon"] = parts
                        if hasattr(self, "online_last"):
                            self.online_last = 0  # re-fetch internet nodes around the new home
            except ValueError:
                pass
            if map_style.get() != self.cfg.get("map_style", "Dark"):
                self.cfg["map_style"] = map_style.get()
                if self.map:
                    self.apply_map_style()
            self.cfg.update(mode=mode.get(), port=port.get().strip() or "auto",
                            host=host.get().strip(), sound=sound.get(),
                            sound_device=device.get(),
                            sound_file=sounds.get(sound_name.get(), cur))
            save_cfg(self.cfg)
            win.destroy()
            if before == (self.cfg["mode"], self.cfg["port"], self.cfg["host"]):
                if self.iface:
                    self.refresh()
                return  # only sound/etc changed; keep the connection
            self.disconnect()
            self.last_try = 0
            if not self.paused:
                self.set_status("Settings saved — reconnecting…", WARN)

        tk.Button(win, text="Save", command=apply, bg=ACCENT, fg="#000", relief="flat",
                  width=10).grid(row=17, column=1, sticky="e", padx=10, pady=10)
        tk.Button(win, text="Send feedback…", command=self.open_feedback, bg=PANEL, fg=FG, relief="flat",
                  font=("Segoe UI", 9)).grid(row=17, column=0, sticky="w", padx=10, pady=10)
        snd_box.bind("<<ComboboxSelected>>", lambda e: test())  # preview on pick
        win.protocol("WM_DELETE_WINDOW", apply)  # closing with X keeps changes too

    def quit(self):
        self.save_geometry()
        save_json(HIST_PATH, self.history)
        if self.tray:
            self.tray.stop()
        if self.iface:
            try:
                self.iface.close()
            except Exception:
                log.debug("Closing the radio connection failed", exc_info=True)
        log.info("Quit")
        try:  # cancel timers so none fire into a destroyed window
            for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.tk.call("after", "cancel", job)  # not after_cancel(): it errors on the map's timers
        except tk.TclError:
            pass
        self.root.destroy()


if __name__ == "__main__":
    extra = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--demo-nodes=")), "0")
    if "--demo" in sys.argv or extra != "0":
        enable_demo(extra_nodes=int(extra) if extra.isdigit() else 0)
    setup_logging()
    instance = claim_instance(INSTANCE_PORT + (1 if DEMO else 0))
    if instance is None:
        log.info("Already running; asked that copy to show itself")
        sys.exit(0)
    root = tk.Tk()
    root.report_callback_exception = lambda *exc: log.error("UI callback failed", exc_info=exc)
    app = App(root)
    if instance:
        threading.Thread(target=app.serve_instance, args=(instance,), daemon=True, name="single-copy").start()
    root.mainloop()
