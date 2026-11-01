# Central Finite Curve

**A desktop dashboard for a Meshtastic mesh — with live drone-detection tracking.**
Created by **DaGromflomite**.

Central Finite Curve is a lightweight Windows/macOS desktop widget that connects to a Meshtastic
radio over USB (or TCP) and shows the nodes it hears, a live map, and messages. It also turns
drone **Remote ID** alerts relayed over the mesh into a **live flight path on a map**, with the
drone's manufacturer looked up from its MAC address.

> ⚠️ **ALPHA.** This is early software under active development. Expect rough edges, and please
> report issues. It is **free, open-source (GPL-3.0)** community software — never for sale.

---

## What it looks like

**Drone tracking** — every Remote ID ping its detector broadcasts is plotted and connected into
the drone's flight path, with the pilot location and the manufacturer from the MAC:

![Drone flight tracking](docs/img/drone-track.png)

**Live central finite curve** — every node the radio hears, with signal, hops, battery and last-heard:

![Active node list](docs/img/active.png)

*(Screenshots use built-in demo data at a neutral location.)*

---

## How it works

- You connect a **Meshtastic radio** (e.g. a Heltec V3) to your PC by USB. Central Finite Curve talks to
  it with the Meshtastic Python API and listens to everything on your channels.
- Nodes it hears appear in **Active** / **History** and on the **Map**, updated live.
- For drones: a **Remote ID detector** on the mesh (such as Colonel Panic's
  [Mesh-Detect](https://github.com/colonelpanichacks/mesh-detect)) sniffs nearby drone Remote ID
  broadcasts and sends a text alert over the mesh for each ping, containing the drone's GPS, the
  pilot's GPS, the drone MAC and RSSI. Central Finite Curve receives those alerts and, on the **Drone**
  tab, draws the drone's path, marks the pilot, and resolves the MAC's first three octets (OUI)
  to a manufacturer using the bundled IEEE registry.
- Optional **phone push**: matching alerts can be pushed to your phone via [ntfy](https://ntfy.sh).

## Features

- **Live node dashboard** — Active / History tables (SNR, RSSI, hops, battery, last heard).
- **Map** — radio-heard nodes and (optionally) meshmap.net internet nodes, dark/satellite styles,
  a memory- and data-friendly tile engine that sleeps when off-screen.
- **Drone tab** — live flight-path tracking from Remote ID alerts: drone path, pilot marker,
  **manufacturer from MAC OUI**, and a running "N pings · maker · RSSI" readout.
- **Save flight** — export the current track to `flights/flight-<time>.csv` (time, drone GPS,
  RSSI, MAC, manufacturer, pilot GPS) **plus a PNG snapshot** of the map as drawn.
- **Search** — ping quiet nodes (and the drone detector) so they re-announce immediately.
- **Phone push** (optional) — forward drone alerts to your phone via ntfy.
- **Messages** — send/receive mesh text, with delivery reports.
- Runs minimised to the **system tray**; single-instance; survives dropped/replugged radios.

## Install

**Run from source (Windows & macOS):**

```bash
# 1) Python 3.10+ with Tkinter
#    Windows: the python.org installer includes Tkinter.
#    macOS:   use the python.org build, or `brew install python-tk`.
# 2) Get the code, then:
pip install -r requirements.txt
python mesh_widget.pyw
```

Prebuilt packages (a Windows installer and a macOS app) are attached to each
[Release](../../releases) once CI has built them — no Python needed to run those.

First run: open **Settings** (gear) to pick your radio's port and set your home location. The
widget talks to the radio; your location and config stay on your PC and are never sent to the mesh.

## Using the Drone tab

1. Make sure your Remote ID detector is powered and on the same Meshtastic channel/region as your
   PC's radio. Its alerts arrive as mesh messages.
2. Open the **Drone** tab. As pings arrive, the **orange path** grows along the drone's route; the
   **red** marker is the latest drone position, the **blue** marker is the pilot.
3. The label shows total pings, the drone's **manufacturer**, and the latest RSSI.
4. **Fit path** frames the whole flight; **Save flight** writes the CSV + map PNG to `flights/`;
   **Clear** resets the track.

## Is this a fork?

**No.** Central Finite Curve is an original, standalone project — not a fork of anything. It is *designed
to work with* third-party detector hardware (see Credits) but shares no code with it.

## Credits

Full details in [CREDITS.md](CREDITS.md). In short: built on the **Meshtastic** project and the
open-source Python ecosystem (tkintermapview, Pillow, requests, sounddevice, pystray, PyPubSub),
map tiles from **OpenStreetMap** contributors and **Esri**, manufacturer data from the **IEEE**
OUI registry, mesh node data from **meshmap.net**, and designed to pair with Colonel Panic's
**Mesh-Detect** drone detector. Thank you to everyone whose work this builds on.

## License

**GPL-3.0-or-later.** Copyright © 2026 DaGromflomite.

Central Finite Curve uses the Meshtastic Python library, which is GPL-3.0, so Central Finite Curve is GPL-3.0 too.
That keeps it free and open forever: anyone may use, modify and redistribute it, but **forks and
derivatives must stay open-source under the GPL and must keep this attribution** to the original
author. See [LICENSE](LICENSE).

## Known issues (alpha)

- Two heavy GUI timing tests (`test_demo_session`, `test_busy_city_scale`) are currently flaky —
  being investigated; core functionality tests pass.
- The map uses online tiles; mind the providers' terms, and your data plan on cellular.
- Tray/sound behaviour varies a little by OS; macOS packaging is validated by beta testing.
