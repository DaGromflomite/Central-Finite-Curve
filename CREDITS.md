# Credits & acknowledgements

Central Finite Curve is an original project by **DaGromflomite**, released under **GPL-3.0-or-later**.
It stands on a lot of other people's good work. Thank you to all of the below.

## Core dependency (sets the license)

- **[Meshtastic](https://meshtastic.org/) / [Meshtastic-python](https://github.com/meshtastic/python)** — GPL-3.0.
  The mesh radio API and protocol Central Finite Curve is built on. Because this library is GPL-3.0,
  Central Finite Curve is GPL-3.0 as well.

## Open-source libraries

| Library | License | Used for |
|---|---|---|
| [tkintermapview](https://github.com/TomSchimansky/TkinterMapView) | CC0 (public domain) | the map view (subclassed for the slim tile engine) |
| [Pillow](https://python-pillow.github.io/) | MIT-CMU | images, map tiles, flight-snapshot export |
| [requests](https://requests.readthedocs.io/) | Apache-2.0 | meshmap.net + map-tile downloads |
| [sounddevice](https://python-sounddevice.readthedocs.io/) | MIT | cross-platform alert sound |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause | reading alert audio |
| [NumPy](https://numpy.org/) | BSD-3-Clause | audio buffers (via sounddevice/soundfile) |
| [pystray](https://github.com/moses-palmer/pystray) | LGPL-3.0 | system-tray icon |
| [PyPubSub](https://github.com/schollii/pypubsub) | BSD | event bus used by the Meshtastic API |
| Python + Tkinter | PSF | the app itself |

## Data sources

- **Map tiles:** © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL);
  satellite/labels imagery © [Esri](https://www.esri.com/) and its data providers. Use is subject
  to each provider's terms of use.
- **MAC → manufacturer:** the **[IEEE](https://standards-oui.ieee.org/) MA-L (OUI) registry**
  (public registry data), bundled as `oui.txt` for offline lookups.
- **Internet mesh nodes:** [meshmap.net](https://meshmap.net/) (community MQTT map), shown when
  enabled.
- **Phone push:** [ntfy](https://ntfy.sh/) (optional, user-configured).
- **Aircraft:** [adsb.fi](https://adsb.fi) open data (free for personal, non-commercial use;
  credited on the map whenever aircraft are shown). Shown only when switched on.
- **Airspace and no-fly zones:** the US Federal Aviation Administration's open data: the
  [Aeronautical Data Delivery Service](https://adds-faa.opendata.arcgis.com/) (Class airspace and
  the UAS Facility Map) and [tfr.faa.gov](https://tfr.faa.gov/) (temporary flight restrictions).
  Public US government data. Shown or checked only when switched on, or while a drone is flying.
- **Airline routes:** [Virtual Radar Server standing data](https://github.com/vradarserver/standing-data)
  (CC0), served by [adsb.lol](https://github.com/adsblol/vrs-standing-data). Looked up only when you
  open an aircraft's card.

## Hardware this is designed to work with

- **Colonel Panic (colonelpanichacks)** — [Mesh-Detect](https://github.com/colonelpanichacks/mesh-detect),
  the ESP32-S3 + Meshtastic drone **Remote ID detector** whose mesh alerts Central Finite Curve receives and
  maps. Central Finite Curve shares **no code** with it — it's a separate program that reads the alerts the
  detector broadcasts — but it's built to pair with that hardware, and credit is due for it.
- Related prior art in the same space, acknowledged with thanks:
  [colonelpanichacks/drone-mesh-mapper](https://github.com/colonelpanichacks/drone-mesh-mapper)
  (a web-based RemoteID mapper), itself derived from JimZGChow's `wifi-rid-to-mesh`.

## Note on forks

Central Finite Curve is **not** a fork of any of the above. Under GPL-3.0, forks of *Central Finite Curve* must
remain open-source and must preserve attribution to the original author, **DaGromflomite**.
