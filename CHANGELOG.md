# Changelog

## 0.2.0-alpha — 2026-11-07

- **Search is now a real mesh discovery, not just a hub ping.** Alongside nudging the nodes the
  radio already knows, Search now **broadcasts a node-info request** (`^all`) on your primary and
  drone channels, so **any node in range announces itself** — including one the hub has forgotten
  or never saw. Previously it could only re-ping nodes already in the node database (your hubs), so
  a quiet or dropped node (like the drone detector) couldn't be found. Targeted nudges to the drone
  node are still sent.

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
