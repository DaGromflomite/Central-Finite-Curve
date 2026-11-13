# Central Finite Curve

**A desktop dashboard for a Meshtastic mesh, with live drone Remote ID tracking.**
Created by **DaGromflomite**.

Central Finite Curve connects to a Meshtastic radio over USB, Bluetooth or Wi-Fi and shows you the
mesh at a glance: the nodes your radio hears, where they sit on a live map, and the messages passing
through. When a Remote ID drone detector is on the mesh, it also turns those alerts into a live
flight path and identifies the drone's manufacturer from its MAC address. It even keeps itself up to
date — once it's installed, it can check for new versions and install them for you.

> **Beta, and Windows-first.** It's in daily use and solid enough to lean on, with plenty still
> planned. Windows is the platform it's built and tested on, and the Releases page carries a
> ready-to-run Windows installer that updates itself from then on. **macOS isn't ready yet:** the
> code is cross-platform and should run from source, but it has never been launched on a Mac, so
> treat the macOS build as an untested, pre-alpha backup. There's no prebuilt macOS app yet.
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

- Connect a **Meshtastic radio** (for example a Heltec V3) to your PC over USB, Bluetooth or Wi-Fi.
  Central Finite Curve talks to it through the Meshtastic Python API and listens to every channel
  you are on.
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

- **Connect by USB, Bluetooth or Wi-Fi** — plug the radio in, or pair it over Bluetooth and go
  wireless. Pick the one you want in Settings; for Bluetooth there's a **Scan** button that finds
  nearby radios for you.
- **Updates itself** — once installed, it checks GitHub for new versions and can download and
  install them for you, from Settings or the tray. No more hunting for the latest download.
- **Live node dashboard** — Active and History tables showing SNR, RSSI, hops, battery and last
  heard. The Active list drops a node once it goes quiet, so it only ever shows what's current.
- **Map** — your radio's nodes and, if you want them, internet nodes from meshmap.net, in dark or
  satellite styles, with a tile engine that is easy on memory and data and sleeps when off-screen.
  A neat bar of controls sits **above the map**: toggles for internet nodes, aircraft, airspace and
  names, a dropdown to choose how far back to show nodes (active now, last hour, day, month, or
  everything), **Fit all**, and **Clear history**. The internet nodes also follow wherever you pan,
  so you can explore other areas, not just home.
- **Full screen** — a button up top, **F11** to toggle, **Esc** to come back, and an option to
  start in full screen every time.
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
- **Aircraft on the map** — planes and helicopters near you, live from adsb.fi.
- **Drone / aircraft conflict alerts** — a warning when a drone is close to a low-flying aircraft.
- **Airspace and no-fly zones (US)** — airport airspace and temporary flight restrictions from the
  FAA, with a warning when a drone flies into one.
- **Incident report** — a printable report of a drone flight, ready for the police or the FAA.
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
zip is there too if you would rather not install. Neither needs Python. After that, the app keeps
itself up to date — it'll tell you when there's a new version and can install it for you.

> **First run:** the app isn't code-signed yet, so Windows SmartScreen shows a blue "Windows
> protected your PC" warning the first time. That's normal for a new indie app — click **More
> info → Run anyway**.

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

From top to bottom: the **header** (logo and name), the **top bar** (connection status and buttons),
the **drone banner** (only when a drone is about), a line of **radio info**, the **tabs** (Active,
History, Map, Drone), the **message panels**, and the **send bar** at the bottom. Cards and dialogs
— a node's info, an aircraft, Settings, Feedback, the archive — slide out from the right edge of the
window rather than opening as separate pop-up windows.

### Top bar

| Control | What it does | Status |
|---|---|---|
| ● status dot and text | Green when connected to your radio, amber when paused or connecting, red when disconnected. | ✅ |
| **Pause** / **Resume** | Lets go of the radio so another app (such as the Meshtastic phone or web app) can use the connection. Click again to reconnect. | ✅ |
| **Search** | A bigger sweep for nodes. It nudges every node your radio knows to check in, and asks any radio within two hops to announce itself. Replies show up in Active within a minute or so. It can only be used every 12 seconds, so it can't flood the mesh. | ✅ |
| **Reconnect** | Drops the connection and opens it again. Handy after you unplug or reflash the radio. | ✅ |
| ⛶ | Full screen on or off. **F11** does the same, and **Esc** leaves full screen. | ✅ |
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
| Node list | Every node heard within the "Active" window (3 hours by default; change it in Settings). Shows name, SNR, RSSI, hops away, battery and when it was last heard. Once a node goes quiet past that window it drops out of Active (it's still in History and on the map), so this list only ever shows what's current. | ✅ |
| Click a node | Sets it as the recipient in the send bar, so you can message it straight away. | ✅ |
| Right-click a node | **Keep on clear** pins it with a ★ so it survives a history clear (handy for a node you're testing), or open its card. | ✅ |

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
| Controls bar (above the map) | The toggles and buttons live in a tidy bar across the top of the map, rather than floating on top of it: **Fit all**, the time-range dropdown, **Clear history**, and the layer toggles **Internet / Aircraft / Airspace / Names**. | ✅ |
| **Time range** dropdown | Choose what's on the map: *Nodes currently active* (no grey pins), *active in the last hour*, *…last day*, *…last month*, or *All recorded nodes*. Your choice is remembered. | ✅ |
| **Internet** | Show or hide the purple nodes from meshmap.net. They're fetched every 10 minutes within the radius you set in Settings, and follow wherever you pan the map, so you can look at other areas too. | ✅ |
| **Aircraft** | Planes and helicopters near you, drawn as little plane/helicopter icons pointing the way they're flying, labelled with callsign and altitude when you're zoomed in. Emergencies are red. Click one for its type, registration, speed, distance and, for airline flights, a **ROUTE** panel showing where it departs and arrives; there's a **Show on map** button to jump to it, or follow it on adsb.fi. Updates every 15 seconds while the map is on screen. | 🧪 |
| **Airspace** | Outlines airport airspace (Class B, C, D and E surface areas, in blues and purple) and active temporary flight restrictions (red), US/FAA data. Click an outline for its name, altitudes and times. | 🧪 |
| **Names** | Show or hide the text labels on every pin, to declutter a busy map. Names are still there on the pins' cards. | ✅ |
| Red drone pins | Drones flying right now (heard in the last 3 minutes), so you can see them beside aircraft and nodes. Click one to jump to the Drone tab. | 🧪 |
| **Clear history…** | Clears the History tab and the map, after asking first. It keeps your own node and any you've pinned with **Keep on clear** (★), and can also wipe the radio's own node memory so cleared nodes don't come straight back. A backup is saved as `nodes-history-backup.json`. | ✅ |
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
| **Incident report** | Writes a report of the latest flight and opens it in your browser: start and end, drone ID and maker, every position, the pilot's reported position, closest distance to your home, the detectors that heard it, aircraft conflicts, airspace it entered, and a map picture. Print it or save it as PDF. Saved in the `flights` folder. | 🧪 |

### Airspace awareness

| What | How it works | Status |
|---|---|---|
| **Conflict alerts** | Needs *Show aircraft* switched on. When a drone that's flying now is within 3 km (about 2 miles) of an aircraft at or below 3,500 ft, you get a red CONFLICT banner, a line in the Drone panel, a sound and a phone alert (if those are on). Each drone and aircraft pair warns at most once every 5 minutes. | 🧪 |
| **No-fly zone alerts** | When a drone flies inside an active temporary flight restriction that reaches down to drone heights, you get the banner, a sound and a phone alert. Restrictions that start high up (say, 9,000 ft) are ignored. | 🧪 |
| **Controlled airspace notes** | When a drone flies in airspace near an airport where drones need FAA authorization, the Drone panel says so, along with the FAA's maximum altitude for that exact spot (from the UAS Facility Map). | 🧪 |
| **Several detectors** | If two or more detectors hear the same drone, their pings are merged into one track, and the banner and Drone tab show how many heard it. | 🧪 |

⚠️ **Drone altitude:** Mesh-Detect's stock alerts don't include the drone's altitude, so conflicts
are judged by distance and the aircraft's altitude, and "above 400 ft" can't be checked. The
detector does decode altitude, though, so a small firmware change fixes this: add
` ALT:<metres>m AGL:<metres>m SPD:<m/s>m/s ID:<serial>` to the end of its `Drone:` line, and
Central Finite Curve picks the extras up automatically. Older detectors keep working as before.

⚠️ **Other limits:**
Airspace data is for the **United States only**. Aircraft only show where adsb.fi's volunteer
receivers have coverage, and not every aircraft broadcasts its position. Routes are a callsign's
usual route, not a filed flight plan, and only exist for airline flights. Treat all of this as
awareness, never as an official airspace or safety tool.

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
| **Right-click a message** | **Reply to [name]** (points the send bar straight at whoever sent it), **Delete this message** (gone for good, from the panels and the archive), **Archive this message**, **Archive whole conversation with [name]**, or **Open [name]'s card**. Archiving only hides messages from the panels — they stay in the archive and anything new still shows; deleting is permanent. | ✅ |
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
| **Connect by** | USB cable, Wi-Fi, or **Bluetooth**. | ✅ USB / ⚠️ Wi-Fi less tested / 🧪 Bluetooth |
| **USB port (or auto)** | Your radio's COM port, or `auto`. If the port changes, it only switches to a radio with the same node ID, so it never grabs someone else's device. | ✅ |
| **Wi-Fi IP address** | For radios connected over the network. | ⚠️ |
| **Bluetooth radio + Scan** | When Connect by is Bluetooth: click **Scan** to list nearby radios and pick yours, or leave it blank for the nearest. First-time pairing uses the Meshtastic PIN shown on the radio (or the fixed PIN if you've set one). | 🧪 |
| **Sound on new message** | Plays a sound when a message arrives (at most one every 3 seconds). | ✅ |
| **Play sound on** | Pick a speaker or headset. | ✅ |
| **Alert sound** | Choose a sound; it plays a preview when you pick one. | ✅ |
| **Active = heard in last (min)** | How recent a node must be to count as active (default 180). | ✅ |
| **Map style** | Dark, Street or Satellite. | ✅ |
| **Online nodes within (km)** | Radius for internet-map nodes around you. | ✅ |
| **Aircraft: at least (nautical mi)** | How far out to show aircraft (default 50, up to 250). | 🧪 |
| **Home location** | Where your node sits on the map if it has no GPS. Paste "lat, lon" straight from Google Maps. It stays on your PC and is never sent over the mesh. | ✅ |
| **Transient tracking** | How often it checks for nodes passing through: Off, Light (3 min, default), Active (1 min) or Real-time (20 s). These checks only reach radios right next to you and are never relayed, so they don't add traffic to the wider mesh. | ✅ |
| **Keep chat archive** | How long messages are kept (see Chat archive). | 🧪 |
| **Phone alerts: topic** | Your ntfy topic or link; **New private topic** makes a random one. Blank turns phone alerts off. | 🧪 |
| **Phone alerts: Send to phone** | Drone alerts only, drone alerts + direct messages, or every message. | 🧪 |
| **Phone alerts: Send test** | Sends a test notification so you know it works. | 🧪 |
| **Window: Full screen** | Go full screen now, and/or **Start in full screen every launch** (off by default). | ✅ |
| **Updates** | Shows the version you're on, a button to **check for updates** (or **download & install** when one's waiting), and a switch to check automatically once a day. | 🧪 |
| **Send feedback…** (bottom left) | Opens the feedback form (see Feedback below). | 🧪 |

### System tray

| Item | What it does | Status |
|---|---|---|
| Tray icon | Its colour shows the connection status, it gets a marker when there are unread messages, and hovering over it shows the status and how many are new. Closing the window only hides it to the tray. | ✅ |
| **Show / hide**, **Pause / resume**, **Settings**, **Clear messages** | Same as the buttons in the window. | ✅ |
| **Check for updates** | Looks for a newer version and, if there's one, offers to download and install it. | 🧪 |
| **Send feedback** | Opens the feedback form. | 🧪 |
| **Quit Central Finite Curve** | The only way to close it completely. | ✅ |

### Behind the scenes

| What | Status |
|---|---|
| Only one copy runs at a time; opening it again just brings the window forward. | ✅ |
| Reconnects by itself when the radio is unplugged and plugged back in. | ✅ |
| Sets your radio's clock from your PC when it connects (and every 6 hours), so radios without GPS keep correct times. | ✅ |
| Moves the window back on screen if it was left on a monitor that's no longer plugged in. | ✅ |
| Keeps itself up to date: checks GitHub about once a day and can download and install new versions for you (turn it off in Settings). | 🧪 |
| Stores your settings, history and messages in your user folder, so updating the app never wipes them. | ✅ |
| Writes an error log to `mesh_widget.log` (kept small) to help track down problems. | ✅ |
| **Demo mode:** start it with `--demo` (for example `CentralFiniteCurve.exe --demo`, or `python mesh_widget.pyw --demo`) for a pretend mesh full of tricky nodes, without touching your radio. | ✅ |
| Phone alerts through ntfy (see below). | ✅ drone alerts / 🧪 messages and the Settings box |
| macOS. | 🚧 Source only, never tested |

## Phone alerts with ntfy

Central Finite Curve can send alerts to your phone through [ntfy](https://ntfy.sh), a free
push-notification service. There's no account to make: you pick a private "topic" name, subscribe
to it on your phone, and tell Central Finite Curve to post to it.

**Setting it up (about two minutes):**

1. **Install the ntfy app** on your phone (iPhone App Store or Google Play).
2. In Central Finite Curve, open **Settings (⚙) → Phone alerts** and click **New private topic**.
   It fills in a long, random topic name, something like `https://ntfy.sh/cfc-k7m2q9xw4hpr3tna`.
   Anyone who knows the name can read your alerts, so treat it like a password. You can also type
   your own topic name, or a link to your own ntfy server.
3. In the ntfy app, tap **+** and enter the same topic name (the part after `ntfy.sh/`). Leave the
   server as `ntfy.sh` unless you run your own.
4. Back in Settings, click **Send test**. Your phone should buzz within a few seconds. If it says
   "Didn't send", check your internet connection and the topic name.
5. Choose what to send under **Send to phone**, then click **Save**.

**What gets sent (Send to phone):**

| Choice | You'll get |
|---|---|
| **Drone alerts only** (default) | Every drone alert from your detector, marked urgent. |
| **Drone alerts + direct messages** | The above, plus private messages sent to you. |
| **Every message received** | The above, plus every public channel message. Can be chatty on a busy mesh. |

While a drone is about, alerts come thick and fast, so they're bundled together: you'll get at
most one notification every 30 seconds, like "3 drone alerts" or "1 drone alert + 2 messages",
listing the first five. That keeps you well inside ntfy.sh's free daily limit. Drone alerts arrive
as high priority; ordinary messages as normal priority.

**To turn it off,** clear the topic box in Settings and click Save.

**Troubleshooting:** if alerts show up in the widget but never on your phone, look in
`mesh_widget.log` for a "Phone push failed" line; it says why. Your drone channel needs to be called
**DroneAlert** for drone alerts to be recognised. If yours has another name, quit the app and add
`"push_channel": "YourChannelName",` to `config.json` (installed version:
`%LOCALAPPDATA%\Central Finite Curve\config.json`).

**Privacy:** alerts travel over the internet to the ntfy server, and drone alerts include the
drone's and pilot's coordinates. Keep your topic name to yourself. It's saved only in `config.json`
on your PC, which is never uploaded to this repository.

## Feedback

Found a bug, got an idea, or just want to say it worked? Open **Send feedback** from the tray menu
or from the bottom of Settings.

1. Pick what it is (something's broken, an idea, or "it worked!"), give it a short title, and say
   what happened.
2. The form adds the useful details for you: app version, Windows version, radio model and
   firmware, and how you're connected. There's a preview, so you see exactly what will be shared.
3. If something broke, you can tick the box to include the last 40 lines of the error log. Names,
   node IDs, coordinates, MAC addresses and links are removed before you ever see it.
4. Click **Open on GitHub**. Your browser opens a new issue with everything filled in; check it over
   and click **Submit**. Nothing is sent from the app itself.

No GitHub account? Click **Copy report** and send it however suits you.

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

## Known issues (beta)

- Direct messages to a node your radio has never heard won't go through until that node answers
  the key request (see Messaging above). Nodes that are out of range will never answer.
- Bluetooth: connecting over BLE can be slow the first time, and disconnecting can lag — the window
  stays responsive, but give a Bluetooth reconnect a moment.
- The map uses online tiles, so keep the providers' terms in mind, along with your data plan on
  cellular connections.
- Tray and sound behaviour varies a little between operating systems.
- macOS is completely unvalidated — it has not been launched on a Mac. Treat the macOS source as a
  pre-alpha backup, not a working build.
