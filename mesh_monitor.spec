# PyInstaller spec — builds Central Finite Curve for Windows (.exe) and macOS (.app).
# Build locally:  pyinstaller mesh_monitor.spec      (CI runs this on each OS)
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("oui.txt", "."), ("assets/icon_256.png", "assets")]   # OUI table + runtime window icon
binaries = []
hiddenimports = collect_submodules("google.protobuf")

# App icon: .ico on Windows, .icns on macOS.
app_icon = ("assets/CentralFiniteCurve.ico" if sys.platform == "win32"
            else "assets/CentralFiniteCurve.icns" if sys.platform == "darwin" else None)

# Every runtime dependency, pulled in EXPLICITLY so a fresh download has all of it and
# nothing relies on PyInstaller's auto-analysis (which missed bleak/requests/urllib3 before
# — the Bluetooth-connect and map-tile features broke in the installer without them).
# meshtastic & friends also load submodules/protobufs dynamically, which collect_all covers.
for pkg in ("meshtastic", "tkintermapview", "pypubsub", "pubsub", "serial",
            "soundfile", "sounddevice", "numpy",
            "requests", "urllib3", "idna", "certifi", "charset_normalizer",  # map tiles / HTTP
            "bleak",                                                          # Bluetooth connect
            "pystray", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["mesh_widget.pyw"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="CentralFiniteCurve",
    console=False,                   # GUI app, no console window
    disable_windowed_traceback=False,
    icon=app_icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CentralFiniteCurve")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="CentralFiniteCurve.app",
        icon="assets/CentralFiniteCurve.icns",
        bundle_identifier="net.dagromflomite.centralfinitecurve",
        info_plist={"NSHighResolutionCapable": True},
    )
