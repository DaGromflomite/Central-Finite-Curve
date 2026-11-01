# Changelog

## 0.1.0-alpha — 2026-11-01
First public alpha by DaGromflomite.

- Live Meshtastic node dashboard — Active / History tables and a Map, with a memory- and
  data-friendly tile engine that sleeps when off-screen.
- **Drone tab** — live Remote ID flight-path tracking: drone path, pilot marker, and the drone's
  **manufacturer resolved from its MAC (IEEE OUI)**. **Save flight** exports a CSV of every ping
  plus a PNG snapshot of the map.
- **Search** — pings quiet nodes (and the drone detector) so they re-announce immediately.
- Optional phone push via ntfy; messages with delivery reports; system-tray; single-instance.
- **Cross-platform:** runs on Windows and macOS (guarded Windows-only APIs; OS-aware default sound).
- GPL-3.0; bundled IEEE OUI table for offline manufacturer lookups.

Known issues: two heavy GUI timing tests are flaky and under investigation; macOS packaging is
validated by beta testing.
