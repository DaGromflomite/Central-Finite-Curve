# Changelog

## 0.5.0-alpha — 2026-11-08

Central Finite Curve becomes an airspace-awareness tool: drones, aircraft and no-fly zones together.

- **Aircraft on the map.** Tick *Show aircraft (adsb.fi)* on the Map tab to see planes and
  helicopters near you as arrows pointing the way they're flying, with callsign and altitude.
  Click one for its type, registration, speed, heading, squawk and distance, or to follow it on
  adsb.fi. Emergencies (squawk 7500/7600/7700) show in red. It only downloads while a map is on
  screen, every 15 seconds, and backs off if adsb.fi is busy. Radius in Settings.
- **Drone / aircraft conflict alerts.** When a drone that's flying now is within 3 km of an aircraft
  at or below 3,500 ft (a medical helicopter, a crop duster, a plane on approach), you get a red
  CONFLICT banner, a line in the Drone panel, a sound, and a phone alert if those are on. Each
  drone/aircraft pair warns at most once every 5 minutes.
- **Airspace and no-fly zones (US).** Tick *Show airspace & no-fly zones (FAA, US)* to outline airport
  airspace (Class B, C, D and E surface areas) and active temporary flight restrictions (red), from
  the FAA's own open data. Click an outline for details. A drone flying inside a no-fly zone raises
  the banner and a phone alert; a drone in controlled airspace near an airport is noted in the
  Drone panel, along with the FAA's maximum altitude for that spot. Zones are downloaded about once
  a day and kept on disk; the restriction list is checked every 30 minutes.
- **Where airline flights are going.** The aircraft card shows the flight's usual route (for example
  ORD Chicago → BOS Boston), looked up once per callsign from the free VRS route database, and only
  when the plane is actually somewhere between those two airports. Private planes have no route.
- **Drone altitude, when the detector sends it.** If a detector adds `ALT:` (above sea level),
  `AGL:` (above the ground), `SPD:` and `ID:` (the Remote ID serial) to its alerts, Central Finite
  Curve uses them: the banner shows the height, drones above 400 ft are flagged, conflicts compare
  altitudes, and the incident report gets a height column and the serial. Older detectors keep
  working exactly as before. (Mesh-Detect's stock firmware decodes these already but doesn't put
  them in its mesh messages yet; see the README.)
- **Several detectors work as one.** When more than one detector hears the same drone, the pings
  are merged into one track instead of doubling up, and the banner and Drone tab show how many
  detectors heard it.
- **Live drones on the main map.** Drones in the air right now show as red pins on the Map tab too,
  next to aircraft and nodes.
- **Incident report.** *Incident report* on the Drone tab writes a clean report of the latest
  flight: when it started and ended, the drone's ID and maker, every position, the pilot's
  reported position, how close it came to your home, which detectors heard it, any aircraft
  conflicts and airspace it entered, and a map picture. It opens in your browser, ready to print or
  save as PDF for the police or the FAA.

## 0.4.0-alpha — 2026-11-07

- **Phone alerts are now set up in Settings.** A new *Phone alerts (ntfy)* section has a topic box,
  a **New private topic** button that makes a long random topic for you, and a **Send test**
  button that tells you straight away whether it worked. No more editing `config.json` by hand.
- **Choose what reaches your phone:** drone alerts only (the default, as before), drone alerts plus
  direct messages, or every message received. Bursts are still bundled into at most one
  notification every 30 seconds. Drone alerts are sent as high priority, other messages as normal.
- **Send feedback.** A feedback form (tray menu, or the bottom of Settings) builds a GitHub issue
  with the app version, Windows version, radio model and firmware filled in, and an optional
  cleaned-up excerpt of the error log (names, node IDs, coordinates, MAC addresses and links
  removed). It opens in your browser for you to check and submit; nothing is sent from the app.
  **Copy report** is there for people without a GitHub account.

## 0.3.0-alpha — 2026-11-07

Messaging, drone alerts and the map, reworked.

- **Message any node on the map.** Every node card (click a pin, or the name in the bar under the
  map) has a **Message** button. It opens a message panel on the card itself: your conversation with
  that node, a box to type in, and delivery reports. The main **Message to** list follows the card. The
  list holds every node on the map: nodes your radio knows (newest first), then nodes heard before,
  then internet-map nodes marked *(internet)*. The chosen recipient stays put through refreshes.
- **Missing encryption keys are handled.** Firmware 2.5+ refuses a direct message
  (`PKI_SEND_FAIL_PUBLIC_KEY`) until your radio has the other node's public key. The widget now says
  so in plain words and asks that node for its node info (which carries the key), at most once a
  minute per node. Opening a card's message panel asks up front if the key is missing.
- **Three message panels.** Direct messages, Drone detect (a compact drone/pilot position list) and
  Channel traffic (every public message on the channels your radio shares). Drag the gaps to resize.
  **Open in window ↗** pops any panel into its own window that keeps updating, with the right send
  controls: a recipient picker for direct messages, a channel picker for channel traffic, and
  *Show on map* plus sending on the drone channel for Drone detect.
- **Red DRONE DETECTED banner** under the top buttons when a drone alert comes in: the maker (from the
  MAC), the MAC, RSSI and a ping count, plus *Show on map* and *Dismiss*. It hides itself 5 minutes
  after the last ping.
- **Chat archive.** The **Archive** button (next to Clear) opens a searchable, scrollable window of
  old messages, filtered by type (direct, drone, channel) and time (today, 7 days, 30 days,
  everything), with **Clear archive** to delete it all. *Settings → Keep chat archive* sets how long
  messages are kept: 1 week, 1 month, 3 months (the default), 1 year or forever, with a hard cap of
  100,000 messages. **Clear** now only empties the panels; the messages stay in the archive.
  **Right-click** any message to archive that message, or the whole conversation with that node, out
  of the panels. New messages from that node still show. The archive costs nothing while it's closed:
  the file is only read when you search, and it's trimmed once a day.
- **Map: choose which nodes to show.** A dropdown on the map: *Nodes currently active*, *active in the
  last hour / day / month*, or *All recorded nodes*. "Currently active" removes the grey pins.
  The old "Online: on/off" button is now a checkbox, **Show internet-map nodes (purple)**.
  **Clear map history** removes recorded nodes, and keeps a backup.

## 0.2.0-alpha — 2026-11-07

- **Search is now a real mesh discovery, not just a hub ping.** Alongside nudging the nodes the
  radio already knows, Search now **broadcasts a node-info request** (`^all`) on your primary and
  drone channels, so **any node in range announces itself** — including one the hub has forgotten
  or never saw. Previously it could only re-ping nodes already in the node database (your hubs), so
  a quiet or dropped node (like the drone detector) couldn't be found. Targeted nudges to the drone
  node are still sent.
- **Background auto-discovery — the widget now actively tracks transient nodes.** On a timer it
  sends a **DIRECT (0-hop) node-info request**, so a node that passes into range — even a silent one
  — is prompted to check in without anyone clicking Search. Because every background request is
  **0-hop, it only touches your local airspace and is never relayed into the wider mesh**, so it's
  safe to run continuously. Short-lived daemon thread (no polling loop); measured CPU impact ≈ 0%.
- **Tracking cadence setting** (*Settings → "Transient tracking"*). Default **Light — 3 min** keeps
  the widget feather-light; choose **Active — 1 min** or **Real-time — 20 s** for near-live tracking
  (still 0-hop / local-only), or **Off**. The interval is floored at 20 s so a config can never
  hammer the air. The **manual Search** button stays the thorough option (pings known nodes + a
  2-hop broadcast).

## 0.1.0-alpha — 2026-11-01
First public alpha by DaGromflomite.

- Live Meshtastic node dashboard — Active / History tables and a Map, with a memory- and
  data-friendly tile engine that sleeps when off-screen.
- **Drone tab** — live Remote ID flight-path tracking: drone path, pilot marker, and the drone's
  **manufacturer resolved from its MAC (IEEE OUI)**. **Save flight** exports a CSV of every ping
  plus a PNG snapshot of the map.
- **Search** — pings quiet nodes (and the drone detector) so they re-announce immediately.
- Optional phone push via ntfy; messages with delivery reports; system-tray; single-instance.
- **Windows:** tested platform; prebuilt installer + portable zip provided.
- **macOS:** cross-platform code is in place (guarded Windows-only APIs; OS-aware default sound),
  but it is **completely untested — never launched on a Mac**. Published as a pre-alpha source
  backup only; no prebuilt macOS app yet.
- GPL-3.0; bundled IEEE OUI table for offline manufacturer lookups.

Known issues: two heavy GUI timing tests are flaky and under investigation; macOS is untested and
has no prebuilt app (source-only, pre-alpha backup).
