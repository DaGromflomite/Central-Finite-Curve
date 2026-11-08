# Central Finite Curve

**A desktop dashboard for a Meshtastic mesh, with live drone Remote ID tracking.**
Created by **DaGromflomite**.

Central Finite Curve connects to a Meshtastic radio over USB or TCP and shows you the mesh at a
glance: the nodes your radio hears, where they sit on a live map, and the messages passing through.
When a Remote ID drone detector is on the mesh, it also turns those alerts into a live flight path
and identifies the drone's manufacturer from its MAC address.

> **Alpha, and Windows-first.** This is early software under active development. Windows is the
> platform it is built and tested on, and the Releases page carries a ready-to-run Windows
> installer. **macOS is not ready yet:** the code is cross-platform and should run from source, but
> it has never been launched on a Mac, so treat the macOS build as an untested, pre-alpha backup.
> There is no prebuilt macOS app at this stage.
>
> Central Finite Curve is free and open source under the GPL-3.0, and it is never for sale.

---

## Screenshots

**Drone tracking** — each Remote ID ping from the detector is plotted and joined into the drone's
flight path, alongside the pilot's location and the manufacturer resolved from the MAC:

![Drone flight tracking](docs/img/drone-track.png)

**Live mesh** — every node the radio hears, with signal, hops, battery and when it was last heard:

![Active node list](docs/img/active.png)

*(Screenshots use the built-in demo data at a neutral location.)*

---

## How it works

- Connect a **Meshtastic radio** (for example a Heltec V3) to your PC over USB. Central Finite Curve
  talks to it through the Meshtastic Python API and listens to every channel you are on.
- The nodes it hears appear in the **Active** and **History** tables and on the **Map**, updated
  live as packets arrive.
- For drone tracking, a **Remote ID detector** on the mesh — such as Colonel Panic's
  [Mesh-Detect](https://github.com/colonelpanichacks/mesh-detect) — picks up nearby drone
  broadcasts and sends one text alert per ping, carrying the drone's GPS, the pilot's GPS, the
  drone's MAC and the signal strength. Central Finite Curve reads those alerts, draws the drone's
  path on the **Drone** tab, marks the pilot, and looks up the manufacturer from the MAC's first
  three octets using the bundled IEEE registry.
- Optionally, alerts can be forwarded to your phone through [ntfy](https://ntfy.sh).

## Features

The short version is below. For every button, setting and how-to, see the
[Feature guide](#feature-guide).

- **Live node dashboard** — Active and History tables showing SNR, RSSI, hops, battery and last
  heard.
- **Map** — your radio's nodes and, if you want them, internet nodes from meshmap.net, in dark or
  satellite styles, with a tile engine that is easy on memory and data and sleeps when off-screen.
  A dropdown on the map lets you pick what you see: just the nodes that are active right now, the
  ones heard in the last hour, day or month, or every node you've ever recorded. A checkbox shows
  or hides the purple internet-map nodes, and **Clear map history** wipes the slate clean.
- **Message any node on the map** — click a pin and hit **Message**. A chat opens right there on
  the node's card, with your conversation so far and a box to reply in.
- **Three message panels** — direct messages, drone alerts and channel traffic each get their own
  panel along the bottom. Pop any of them out into its own window, with everything you need to
  reply from there.
- **Drone alert banner** — a red **DRONE DETECTED** bar drops in under the top buttons the moment
  a drone shows up, telling you who made it, how strong the signal is and how many pings you've had.
  One click takes you to it on the map.
- **Chat archive** — every message is kept for as long as you choose (a week up to forever), and
  you can search and scroll back through all of it whenever you like.
- **Drone tab** — live flight-path tracking from Remote ID alerts: the drone's path, a pilot
  marker, the manufacturer resolved from the MAC, and a running "pings · manufacturer · RSSI"
  readout.
- **Save flight** — export the current track to `flights/flight-<time>.csv` (time, drone GPS, RSSI,
  MAC, manufacturer and pilot GPS), together with a PNG snapshot of the map.
- **Spots nodes passing through** — every few minutes it checks for radios that have come into range
  and nudges them to check in, so a node moving through the area shows up on its own, without anyone
  clicking. It only reaches radios right next to you, not the wider mesh, so it never adds to everyone
  else's traffic. Change how often it runs, or turn it off, in Settings. The **Search** button does a
  bigger sweep whenever you want one.
- **Messaging** — send and receive mesh text, with delivery reports.
- **Light on your PC** — it's a widget, so it's built to sit quietly in the corner: about 100 MB
  of memory and well under 1% CPU on a normal mesh. The archive isn't even opened until you go
  looking through it.
- **Phone push** (optional) — forward alerts to your phone through ntfy.
- Runs minimised to the **system tray**, allows only a single instance, and recovers cleanly when a
  radio is unplugged and plugged back in.

## Installing

**Windows:** download the installer from the [Releases](../../releases) page and run it. A portable
zip is there too if you would rather not install. Neither needs Python.

**From source** (Windows, or macOS at your own risk):

```bash
# Python 3.10 or newer, with Tkinter.
#   Windows: the python.org installer includes Tkinter.
#   macOS:   use the python.org build, or run: brew install python-tk
pip install -r requirements.txt
python mesh_widget.pyw
```

On first run, open **Settings** (the gear icon) to choose your radio's port and set your home
location. The widget only ever talks to your radio; your location and settings stay on your PC and
are never sent over the mesh.

## Feature guide

Everything Central Finite Curve can do, where to find it, and how far along it is. Each feature
has one of these labels:

- ✅ **Working.** Used on real radios and doing its job.
- 🧪 **New, needs field testing.** Works in the built-in demo and passes the automated tests, but
  hasn't had much time on a real mesh yet. Please tell us if something's off.
- ⚠️ **Limited.** Works, with a catch that's explained next to it.
- 🚧 **Not ready.** Planned or untested.

### The window at a glance

From top to bottom: the **top bar** (connection status and buttons), the **drone banner** (only
when a drone is about), a line of **radio info**, the **tabs** (Active, History, Map, Drone), the
**message panels**, and the **send bar** at the bottom.

### Top bar

| Control | What it does | Status |
|---|---|---|
| ● status dot and text | Green when connected to your radio, amber when paused or connecting, red when disconnected. | ✅ |
| **Pause** / **Resume** | Lets go of the radio so another app (such as the Meshtastic phone or web app) can use the connection. Click again to reconnect. | ✅ |
| **Search** | A bigger sweep for nodes. It nudges every node your radio knows to check in, and asks any radio within two hops to announce itself. Replies show up in Active within a minute or so. It can only be used every 12 seconds, so it can't flood the mesh. | ✅ |
| **Reconnect** | Drops the connection and opens it again. Handy after you unplug or reflash the radio. | ✅ |
| 📌 | Keeps the window on top of everything else. | ✅ |
| ⚙ | Opens **Settings** (see below). | ✅ |

The radio info line underneath shows your radio's battery, GPS state, how busy the channel is, and
how many other nodes it knows.

### Drone banner

| What | How to use it | Status |
|---|---|---|
| Red **DRONE DETECTED** bar | Appears under the top bar the moment a drone alert arrives. It shows the drone's maker (from its MAC address), the MAC, the signal strength and how many pings have come in. **Show on map** jumps to the Drone tab; **Dismiss** hides it. It goes away by itself 5 minutes after the last ping. | 🧪 |

### Active tab

| What | How to use it | Status |
|---|---|---|
| Node list | Every node heard within the "Active" window (3 hours by default; change it in Settings). Shows name, SNR, RSSI, hops away, battery and when it was last heard. Nodes your radio knows but never timestamped show a "?" in Heard. | ✅ |
| Click a node | Sets it as the recipient in the send bar, so you can message it straight away. | ✅ |

### History tab

| What | How to use it | Status |
|---|---|---|
| Every node ever heard | Name, whether it's active now, first and last seen, how many packets, and the best signal. Kept for up to a year (5,000 nodes at most). | ✅ |
| Click a node | Sets it as the message recipient, even if it isn't on the map. | 🧪 |

### Map tab

| Control | How to use it | Status |
|---|---|---|
| Pins | **Green**: active nodes. **Grey**: heard before, but not lately. **Purple**: nodes from the internet map (meshmap.net). **Amber outline**: repeaters and routers. **Hollow pin**: a node your radio heard that never sent a location. It's placed in a ring around you so you can still click it; it is *not* its real position. | ✅ |
| Click a pin | Opens the node's **info card**: identity, hardware, location, signal and more, with **Copy location**, **Google Maps** and **Message** buttons. | ✅ |
| **Node dropdown** (top right) | Choose what's on the map: *Nodes currently active* (no grey pins), *Nodes active in the last hour*, *…last day*, *…last month*, or *All recorded nodes*. Your choice is remembered. | 🧪 |
| **Show internet-map nodes (purple)** | Tick or untick to show or hide nodes from meshmap.net. They're fetched every 10 minutes within the radius you set in Settings. | ✅ |
| **Clear map history** | Removes every recorded node from the map and the History tab, after asking first. A backup is saved as `nodes-history-backup.json`. Nodes still in your radio's own list come straight back. | 🧪 |
| **Fit all** | Zooms to fit every pin. | ✅ |
| **+ / − and the slider** | Zoom in and out. The scroll wheel works too. | ✅ |
| Bottom bar: **All / Nodes / Repeaters**, **◀ ▶** | Step through the pins one by one, optionally only repeaters or only regular nodes. Click the name in the bar to open its card. | ✅ |

⚠️ **About positions:** a pin is only as accurate as the location that node sends. Many nodes send a
blurred position on purpose (you'll see a faint circle around them), some send none at all (hollow
pins), and internet-map positions are often blurred by a kilometre or more. Your own node shows at
the **home location** from Settings if it has no GPS.

### Drone tab

| Control | How to use it | Status |
|---|---|---|
| Flight path | Each Remote ID ping from your detector adds a point. The orange line is the drone's route, the red marker its latest position, and the blue marker the pilot. Today's flight is loaded automatically when you open the tab. | ✅ |
| Readout | Total pings, the manufacturer and the latest signal strength. | ✅ |
| **Fit path** | Frames the whole flight. | ✅ |
| **Save flight** | Saves `flights/flight-<time>.csv` (time, drone position, RSSI, MAC, manufacturer, pilot position) plus a PNG picture of the map. | ✅ |
| **Clear** | Starts a fresh track. | ✅ |

To get alerts at all, your Remote ID detector needs to be powered on and on the same region,
preset and channel as your radio (by default a channel called **DroneAlert**).

### Message panels

| Panel | What goes in it | Status |
|---|---|---|
| **Direct messages** | Private messages between you and other nodes, plus delivery reports (✓ delivered, ✗ not delivered). | 🧪 |
| **Drone detect** | Drone alerts in a compact form: maker, MAC, signal, and the drone and pilot positions. | 🧪 |
| **Channel traffic** | Every public message on the channels your radio is on, with the channel name. | 🧪 |
| Drag the gaps | Resize the panels. | 🧪 |
| **Open in window ↗** | Pops a panel into its own window that keeps updating live. Each window has its own send bar: a recipient picker for direct messages, a channel picker for channel traffic, and **Show on map** plus sending on the drone channel for drone alerts. **Clear all** is there too. | 🧪 |
| **Right-click a message** | **Archive this message**, **Archive whole conversation with [name]**, or **Open [name]'s card**. Archiving only hides messages from the panels; they stay in the archive, and anything new from that node still shows. | 🧪 |
| **Clear** (above the panels) | Empties the panels. Your messages stay in the archive. | 🧪 |
| **Archive** (above the panels) | Opens the chat archive (see below). | 🧪 |

⚠️ **What you can't see:** your radio relays plenty of messages it can't read: other people's private
messages, and channels you don't have the key for. That's how Meshtastic's encryption is meant to
work, so the Channel traffic panel only shows what your radio can decrypt.

### Sending messages

| How | Steps | Status |
|---|---|---|
| Send bar (bottom) | Pick **Everyone** or a node from the dropdown, type, and press Enter or **Send**. The dropdown lists every node on the map, with the ones your radio knows at the top and internet-map nodes marked *(internet)*. | ✅ |
| From a node card | Click a pin, then **Message**. A small chat opens on the card with your conversation so far, a box to type in, and delivery reports. | 🧪 |
| From a pop-out window | Use the send bar at the bottom of the window. | 🧪 |

⚠️ **"Your radio doesn't have this node's encryption key yet."** Meshtastic encrypts direct messages
end to end, so your radio needs the other node's public key first. Nobody has to accept anything:
every radio announces its key now and then, and yours saves the ones it hears. If it hasn't heard a
node yet (common for internet-map nodes, which may be out of radio range), Central Finite Curve
asks that node for its key. If the node can hear you, the key arrives within a minute or so and you
can send again. If it never arrives, the node is out of range. You can still reach it with a public
message to Everyone.

### Chat archive

| Control | How to use it | Status |
|---|---|---|
| **Archive** button | Opens a window where you can search and scroll back through old messages. | 🧪 |
| Search box | Finds messages by text, sender or recipient. Press Enter or **Search**. | 🧪 |
| Type filter | All messages, Direct messages, Drone detect or Channel traffic. | 🧪 |
| Time filter | Today, Last 7 days, Last 30 days or Everything. Shows the newest 3,000 matches. | 🧪 |
| **Clear archive** | Permanently deletes every saved message after asking first. This can't be undone. | 🧪 |
| How long it keeps messages | **Settings → Keep chat archive**: 1 week, 1 month, 3 months (default), 1 year or Forever. "Forever" still stops at 100,000 messages so the file can't grow without limit. Old messages are cleared out once a day. | 🧪 |

### Settings (⚙)

| Setting | What it does | Status |
|---|---|---|
| **Connect by** | USB (serial) or Wi-Fi. | ✅ USB / ⚠️ Wi-Fi less tested |
| **USB port (or auto)** | Your radio's COM port, or `auto`. If the port changes, it only switches to a radio with the same node ID, so it never grabs someone else's device. | ✅ |
| **Wi-Fi IP address** | For radios connected over the network. | ⚠️ |
| **Sound on new message** | Plays a sound when a message arrives (at most one every 3 seconds). | ✅ |
| **Play sound on** | Pick a speaker or headset. | ✅ |
| **Alert sound** | Choose a sound; it plays a preview when you pick one. | ✅ |
| **Active = heard in last (min)** | How recent a node must be to count as active (default 180). | ✅ |
| **Map style** | Dark, Street or Satellite. | ✅ |
| **Online nodes within (km)** | Radius for internet-map nodes around you. | ✅ |
| **Home location** | Where your node sits on the map if it has no GPS. Paste "lat, lon" straight from Google Maps. It stays on your PC and is never sent over the mesh. | ✅ |
| **Transient tracking** | How often it checks for nodes passing through: Off, Light (3 min, default), Active (1 min) or Real-time (20 s). These checks only reach radios right next to you and are never relayed, so they don't add traffic to the wider mesh. | ✅ |
| **Keep chat archive** | How long messages are kept (see Chat archive). | 🧪 |

### System tray

| Item | What it does | Status |
|---|---|---|
| Tray icon | Its colour shows the connection status, it gets a marker when there are unread messages, and hovering over it shows the status and how many are new. Closing the window only hides it to the tray. | ✅ |
| **Show / hide**, **Pause / resume**, **Settings**, **Clear messages** | Same as the buttons in the window. | ✅ |
| **Quit Central Finite Curve** | The only way to close it completely. Use this before updating. | ✅ |

### Behind the scenes

| What | Status |
|---|---|
| Only one copy runs at a time; opening it again just brings the window forward. | ✅ |
| Reconnects by itself when the radio is unplugged and plugged back in. | ✅ |
| Sets your radio's clock from your PC when it connects (and every 6 hours), so radios without GPS keep correct times. | ✅ |
| Moves the window back on screen if it was left on a monitor that's no longer plugged in. | ✅ |
| Writes an error log to `mesh_widget.log` (kept small) to help track down problems. | ✅ |
| **Demo mode:** start it with `--demo` (for example `CentralFiniteCurve.exe --demo`, or `python mesh_widget.pyw --demo`) for a pretend mesh full of tricky nodes, without touching your radio. | ✅ |
| Phone alerts through ntfy (see below). | ✅ drone alerts / 🚧 other messages |
| macOS. | 🚧 Source only, never tested |

## Phone alerts with ntfy

Central Finite Curve can send drone alerts to your phone through [ntfy](https://ntfy.sh), a free
push-notification service. There's no account to make: you pick a private "topic" name, subscribe
to it on your phone, and tell Central Finite Curve to post to it.

**What gets sent:** right now, only messages on your drone channel (**DroneAlert** by default), which
is where your Remote ID detector posts. While a drone is about, the alerts come thick and fast, so
they're bundled together: you'll get at most one notification every 30 seconds, along the lines of
"3 drone alerts", listing the first five. That keeps you well inside ntfy.sh's free daily limit.
Pushing other messages (direct messages, or everything) is planned but not built yet. 🚧

**1. Pick a topic name.** Anyone who knows the name can read your alerts, so make it long and hard to
guess, like a password. For example: `cfc-drones-7f3k9q2xw81m`.

**2. Subscribe on your phone.** Install the **ntfy** app (iPhone App Store or Google Play), tap **+**,
and enter your topic name. Leave the server as `ntfy.sh`.

**3. Tell Central Finite Curve.** There isn't a Settings box for this yet, so you add it to the
settings file by hand:

1. Quit Central Finite Curve from the tray (**Quit Central Finite Curve**).
2. Open `config.json` in Notepad. Where it is:
   - **Installed version:** `%LOCALAPPDATA%\Programs\Central Finite Curve\_internal\config.json`
     (paste that into the File Explorer address bar).
   - **Running from source:** next to `mesh_widget.pyw`.

   The file is only created after the first run, so open and quit the app once if it's not there.
3. Add a line with your topic, just after the opening `{`:

   ```json
   "push_url": "https://ntfy.sh/cfc-drones-7f3k9q2xw81m",
   ```

   If your drone channel isn't called DroneAlert, add this line as well, with your channel's name:

   ```json
   "push_channel": "YourChannelName",
   ```

4. Save the file and start Central Finite Curve again.

**4. Test it.** From any computer, open a command prompt and run
`curl -d "test" https://ntfy.sh/cfc-drones-7f3k9q2xw81m` with your own topic name. Your phone should
buzz. If it does, the phone side is set up. The next drone alert on the mesh will arrive the same
way. If alerts reach the widget but not your phone, check `mesh_widget.log` for a "Phone push
failed" line.

**Running your own ntfy server?** Just put its address in `push_url` instead, for example
`https://ntfy.example.com/cfc-drones`.

**Privacy:** alerts go over the internet to the ntfy server, and they include the drone's and
pilot's coordinates. Keep the topic name to yourself, and don't put `config.json` anywhere public
(it's already excluded from this repository).

## Is this a fork?

No. Central Finite Curve is an original, standalone project, not a fork of anything. It is designed
to work alongside third-party detector hardware (see Credits), but it shares no code with it.

## Credits

The full list is in [CREDITS.md](CREDITS.md). In short, Central Finite Curve is built on the
**Meshtastic** project and the wider open-source Python ecosystem (tkintermapview, Pillow, requests,
sounddevice, pystray and PyPubSub), with map tiles from **OpenStreetMap** contributors and **Esri**,
manufacturer data from the **IEEE** OUI registry, and mesh node data from **meshmap.net**. It is
designed to pair with Colonel Panic's **Mesh-Detect** drone detector. Thank you to everyone whose
work it builds on.

## License

**GPL-3.0-or-later.** Copyright © 2026 DaGromflomite.

Central Finite Curve depends on the Meshtastic Python library, which is GPL-3.0, so Central Finite
Curve is GPL-3.0 as well. That keeps it free and open for good: anyone may use, modify and share it,
but forks and derivatives must stay open source under the GPL and must keep this attribution to the
original author. See [LICENSE](LICENSE) for the full text.

## Known issues (alpha)

- Direct messages to a node your radio has never heard won't go through until that node answers
  the key request (see Messaging above). Nodes that are out of range will never answer.
- The map uses online tiles, so keep the providers' terms in mind, along with your data plan on
  cellular connections.
- Tray and sound behaviour varies a little between operating systems.
- macOS is completely unvalidated — it has not been launched on a Mac. Treat the macOS source as a
  pre-alpha backup, not a working build.
