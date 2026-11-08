# Changelog

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
