"""Regression tests for Mesh Monitor.

Run all:            python tests/run_tests.py
Skip the slow GUI:  python tests/run_tests.py --quick

Everything runs in temporary folders: real settings, history, messages and the radio are never touched.
The GUI tests drive the widget in demo mode, so no radio is needed.
"""
import collections
import importlib.machinery
import importlib.util
import io
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(os.path.dirname(HERE), "mesh_widget.pyw")
_loader = importlib.machinery.SourceFileLoader("mesh_widget", SOURCE)
_spec = importlib.util.spec_from_loader("mesh_widget", _loader)
mw = importlib.util.module_from_spec(_spec)
_loader.exec_module(mw)

QUICK = "--quick" in sys.argv or os.environ.get("MESH_TESTS_QUICK") == "1"


class Captured(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def problems(self):
        return [r for r in self.records if r.levelno >= logging.ERROR]


CAPTURE = Captured()
mw.log.addHandler(CAPTURE)
mw.log.setLevel(logging.DEBUG)
mw.log.propagate = False


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- pure helpers
class Helpers(unittest.TestCase):
    def test_real_pos(self):
        self.assertTrue(mw.real_pos({"latitude": 41.9, "longitude": -79.9}))
        self.assertFalse(mw.real_pos({"latitude": 0.0, "longitude": 0.0}))   # broken GPS
        self.assertFalse(mw.real_pos({"latitude": 91, "longitude": 0.5}))    # out of range
        self.assertFalse(mw.real_pos({"latitude": "41", "longitude": -79}))  # wrong type
        self.assertFalse(mw.real_pos({}))
        self.assertFalse(mw.real_pos(None))
        self.assertTrue(mw.real_pos({"latitude": 0.0, "longitude": 10.0}))   # on the equator is fine

    def test_precision(self):
        self.assertEqual(mw.precision_m(32), 0)
        self.assertEqual(mw.precision_m(None), 0)
        self.assertAlmostEqual(mw.precision_m(13), 2918, delta=5)

    def test_distance(self):
        self.assertAlmostEqual(mw.km_between((0, 0), (0, 1)), 111.19, delta=0.1)
        self.assertEqual(mw.km_between((41.9, -79.9), (41.9, -79.9)), 0)

    def test_ago_and_when(self):
        now = time.time()
        self.assertEqual(mw.ago(None), "-")
        self.assertEqual(mw.ago(now + 300), "now")  # node clock running fast
        self.assertEqual(mw.ago(now - 30), "30s")
        self.assertEqual(mw.ago(now - 3600), "1h")
        self.assertEqual(mw.ago(now - 3 * 86400), "3d")
        self.assertIn("just now", mw.when(now + 300))
        self.assertIn("5m ago", mw.when(now - 300))
        self.assertIsNone(mw.when(None))

    def test_span(self):
        self.assertEqual(mw.span(3700), "1h 1m")
        self.assertEqual(mw.span(90000), "1d 1h 0m")
        self.assertIsNone(mw.span(0))

    def test_warn_once(self):
        before = len(CAPTURE.records)
        for _ in range(5):
            mw.warn_once("test-key-unique", "repeated problem")
        self.assertEqual(len(CAPTURE.records) - before, 1)

    def test_memory_reading(self):
        mb = mw.memory_mb()
        self.assertTrue(mb is None or 5 < mb < 4000)


class UiPieces(unittest.TestCase):
    def test_alerts_are_rate_limited_and_off_thread(self):
        played = []
        mw._alert["last"] = 0
        with mock.patch.object(mw, "play_alert", side_effect=lambda cfg: played.append(threading.current_thread().name)):
            for _ in range(10):
                mw.alert({})
            time.sleep(0.2)
        self.assertEqual(played, ["alert"], "ten messages in a burst should give one alert, off the UI thread")

    def test_sync_tree_changes_only_what_differs(self):
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        root.withdraw()
        try:
            tree = ttk.Treeview(root, columns=("a",), show="headings")
            app = types.SimpleNamespace(row_cache={})
            sync = types.MethodType(mw.App.sync_tree, app)
            sync(tree, [("!1", (), ("one",)), ("!2", (), ("two",)), ("!3", (), ("three",))])
            tree.selection_set("!2")
            root.update()  # deliver the event from our own selection_set before we start listening
            fired = []
            tree.bind("<<TreeviewSelect>>", lambda e: fired.append(1))
            with mock.patch.object(tree, "insert", wraps=tree.insert) as ins:
                sync(tree, [("!3", (), ("three!",)), ("!2", (), ("two",))])  # reorder, change, drop
                root.update()
            self.assertEqual(ins.call_count, 0, "existing rows should be updated, not re-inserted")
            self.assertEqual(tree.get_children(), ("!3", "!2"))
            self.assertEqual(tree.item("!3", "values"), ("three!",))
            self.assertEqual(tree.selection(), ("!2",), "selection kept")
            self.assertEqual(fired, [], "keeping the selection must not fire the click handler")
        finally:
            root.destroy()


# ---------------------------------------------------------------- files
class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="meshtest-")

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_json_round_trip(self):
        mw.save_json(self.path("a.json"), {"x": 1})
        self.assertEqual(mw.load_json(self.path("a.json")), {"x": 1})
        self.assertFalse(os.path.exists(self.path("a.json.tmp")))

    def test_damaged_json_is_set_aside_not_lost(self):
        with open(self.path("h.json"), "w") as f:
            f.write('{"half written": ')
        self.assertEqual(mw.load_json(self.path("h.json")), {})
        kept = [n for n in os.listdir(self.dir) if n.startswith("h.json.corrupt-")]
        self.assertEqual(len(kept), 1, "the damaged file should be kept for inspection")

    def test_non_dict_json(self):
        with open(self.path("l.json"), "w") as f:
            json.dump([1, 2], f)
        self.assertEqual(mw.load_json(self.path("l.json")), {})

    def test_missing_json(self):
        self.assertEqual(mw.load_json(self.path("nope.json")), {})

    def test_trim_lines(self):
        p = self.path("m.jsonl")
        with open(p, "w") as f:
            f.writelines(f"{i}\n" for i in range(3000))
        self.assertEqual(mw.trim_lines(p, 2000), 1000)
        with open(p) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2000)
        self.assertEqual(lines[-1], "2999\n")
        self.assertEqual(mw.trim_lines(p, 2000), 0)  # under the limit: untouched

    def test_trim_archive(self):
        p = self.path("a.jsonl")
        now = time.time()
        with open(p, "w", encoding="utf-8") as f:
            for days in (40, 35, 20, 5, 1):
                f.write(json.dumps({"t": now - days * 86400, "text": f"{days}d"}) + "\n")
        self.assertEqual(mw.trim_archive(p, 30, now=now), 2)  # 40 and 35 days old go
        self.assertEqual(mw.trim_archive(p, 30, now=now), 0)  # nothing more to do: file untouched
        self.assertEqual(mw.trim_archive(p, 0, cap=2, now=now), 1)  # "forever" still obeys the cap
        with open(p, encoding="utf-8") as f:
            self.assertEqual([json.loads(l)["text"] for l in f], ["5d", "1d"])

    def test_tail_lines(self):
        p = self.path("t.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(f"line {i}\n" for i in range(50000))
        self.assertEqual(mw.tail_lines(p, 3, chunk=4096), ["line 49997", "line 49998", "line 49999"])
        self.assertEqual(mw.tail_lines(self.path("none.jsonl"), 3), [])

    def test_scrub_log(self):
        line = ("Pinged Drone Detect (!02ed677c) at 41.948123,-79.981234 mac 8c:1e:d9:00:00:01 "
                "push https://ntfy.sh/cfc-abc path C:\\Users\\Timot\\x.log on COM17, SNR 1.5 dB")
        out = mw.scrub_log([line], ["Drone Detect"])[0]
        for secret in ("Drone Detect", "02ed677c", "41.948123", "79.981234", "8c:1e:d9", "ntfy.sh", "Timot"):
            self.assertNotIn(secret, out)
        for kept in ("COM17", "1.5 dB", "Pinged"):
            self.assertIn(kept, out)

    def test_feedback_link(self):
        url, cut = mw.feedback_link("[Bug] map", "short body")
        self.assertFalse(cut)
        self.assertTrue(url.startswith(mw.REPO_URL + "/issues/new?"))
        self.assertIn("title=%5BBug%5D+map", url)
        url, cut = mw.feedback_link("[Bug] long", "x" * 20000)
        self.assertTrue(cut)
        self.assertLessEqual(len(url), mw.FEEDBACK_URL_MAX)
        self.assertIn("shortened", url)

    def test_parse_aircraft(self):
        data = {"ac": [
            {"hex": "a90af0", "flight": "N682D   ", "r": "N682D", "t": "H25B", "category": "A2",
             "alt_baro": 2400, "gs": 120.5, "track": 90, "lat": 41.9, "lon": -79.9, "seen_pos": 2},
            {"hex": "abc123", "flight": "", "r": "N1HE", "t": "EC35", "category": "A7",
             "alt_baro": "ground", "lat": 41.8, "lon": -79.8, "seen_pos": 1, "squawk": "7700"},
            {"hex": "dead00", "lat": 41.0, "lon": -79.0, "seen_pos": 300},     # stale position: dropped
            {"hex": "<script>", "lat": 41.0, "lon": -79.0},                    # bad id: dropped
            {"hex": "beef01"}]}                                                  # no position: dropped
        out = mw.parse_aircraft(data)
        self.assertEqual([a["hex"] for a in out], ["a90af0", "abc123"])
        self.assertEqual(out[0]["call"], "N682D")
        self.assertEqual(out[0]["alt"], 2400)
        self.assertTrue(out[1]["ground"] and out[1]["heli"])
        self.assertEqual(out[1]["emergency"], "emergency")
        self.assertEqual(mw.parse_aircraft({}), [])

    def test_point_in_rings(self):
        square = [[(0, 0), (0, 10), (10, 10), (10, 0)]]
        with_hole = [square[0], [(4, 4), (4, 6), (6, 6), (6, 4)]]
        self.assertTrue(mw.point_in_rings(5, 5, square))
        self.assertFalse(mw.point_in_rings(15, 5, square))
        self.assertFalse(mw.point_in_rings(5, 5, with_hole))
        self.assertTrue(mw.point_in_rings(2, 2, with_hole))
        g = {"type": "MultiPolygon", "coordinates": [[[[0, 0], [10, 0], [10, 10], [0, 10]]]]}
        self.assertEqual(mw.geojson_rings(g)[0][0][1], (0, 10))  # GeoJSON is lon,lat; we use lat,lon

    def test_parse_tfr_xml(self):
        xml = ("<XNOTAM><txtLocalName>Wildfire near Testville</txtLocalName><valDistVerLower>0</valDistVerLower>"
               "<valDistVerUpper>8000</valDistVerUpper><dateEffective>2026-11-08T12:00:00</dateEffective>"
               "<dateExpire>2026-11-09T12:00:00</dateExpire><abdMergedArea>"
               "<Avx><geoLat>41.90N</geoLat><geoLong>080.00W</geoLong></Avx>"
               "<Avx><geoLat>42.00N</geoLat><geoLong>080.00W</geoLong></Avx>"
               "<Avx><geoLat>42.00N</geoLat><geoLong>079.90W</geoLong></Avx>"
               "<Avx><geoLat>41.90N</geoLat><geoLong>079.90W</geoLong></Avx></abdMergedArea></XNOTAM>")
        t = mw.parse_tfr_xml(xml)
        self.assertEqual(t["name"], "Wildfire near Testville")
        self.assertEqual(t["polys"][0][0][0], (41.9, -80.0))
        self.assertTrue(mw.point_in_rings(41.95, -79.95, t["polys"][0]))
        self.assertLess(t["start"], t["end"])
        self.assertIsNone(mw.parse_tfr_xml("<XNOTAM></XNOTAM>"))

    def test_drone_extras(self):
        text = ("Drone: aa:bb:cc:dd:ee:ff RSSI:-70 https://maps.google.com/?q=41.9,-79.9 "
                "ALT:412m AGL:150m SPD:12m/s ID:1581F5FJD239C00DW22E")
        self.assertEqual(mw.drone_extras(text), {"alt_m": 412.0, "agl_m": 150.0, "speed_ms": 12.0,
                                                 "serial": "1581F5FJD239C00DW22E"})
        self.assertEqual(mw.drone_extras("Drone: aa:bb:cc:dd:ee:ff RSSI:-70"), {})  # old detectors: nothing

    def test_route_plausible(self):
        route = {"_airports": [{"lat": 40.64, "lon": -73.78}, {"lat": 51.47, "lon": -0.46}]}  # JFK -> LHR
        self.assertTrue(mw.route_plausible(route, 45.0, -55.0))     # over the Atlantic, on the way
        self.assertFalse(mw.route_plausible(route, 34.0, -118.0))   # over Los Angeles: stale/reused callsign
        self.assertFalse(mw.route_plausible({}, 45.0, -55.0))
        self.assertTrue(mw.ROUTE_RE.match("AAL100") and not mw.ROUTE_RE.match("N682D"))

    def test_prune_history(self):
        now = time.time()
        h = {"!old": {"last": now - 400 * 86400}, "!new": {"last": now}, "!none": {}}
        self.assertEqual(mw.prune_history(h, now), 2)
        self.assertEqual(list(h), ["!new"])
        with mock.patch.object(mw, "MAX_HISTORY", 3):
            h = {f"!{i}": {"last": now - i} for i in range(10)}
            mw.prune_history(h, now)
            self.assertEqual(sorted(h), ["!0", "!1", "!2"])  # the most recently heard stay


# ---------------------------------------------------------------- internet nodes
class OnlineNodes(unittest.TestCase):
    def fetch(self, payload, center=(41.94, -79.98), radius=100):
        body = json.dumps(payload).encode()

        class Resp(io.BytesIO):
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(mw.urllib.request, "urlopen", return_value=Resp(body)):
            return mw.fetch_online(center, radius)

    def test_good_bad_and_far_nodes(self):
        now = int(time.time())
        found = self.fetch({
            "1": {"longName": "Good", "latitude": 419400000, "longitude": -799800000, "seenBy": {"t": now}},
            "2": {"longName": "Far", "latitude": 400000000, "longitude": -700000000},
            "3": {"longName": "Null island", "latitude": 0, "longitude": 0},
            "4": {"longName": "Text lat", "latitude": "x", "longitude": -799800000},
            "5": {"longName": "No lat"},
            "6": {"longName": "Weird seen", "latitude": 419500000, "longitude": -799800000,
                  "seenBy": {"a": "yesterday"}, "precision": "high"},
            "7": {"longName": "Future", "latitude": 419600000, "longitude": -799800000, "seenBy": {"t": now + 9999}},
            "not-a-number": {"latitude": 419400000, "longitude": -799800000},
            "8": "not even a dict",
        })
        names = {v["name"] for v in found.values()}
        self.assertEqual(names, {"Good", "Weird seen", "Future"})
        self.assertLessEqual(max(v["seen"] for v in found.values()), time.time() + 1)

    def test_wrong_shape(self):
        with self.assertRaises(ValueError):
            self.fetch([1, 2, 3])

    def test_long_name_capped(self):
        found = self.fetch({"9": {"longName": "x" * 500, "latitude": 419400000, "longitude": -799800000}})
        self.assertLessEqual(len(next(iter(found.values()))["name"]), 40)


# ---------------------------------------------------------------- single copy
class SingleCopy(unittest.TestCase):
    def test_second_copy_is_refused_and_first_is_shown(self):
        port = free_port()
        first = mw.claim_instance(port)
        self.assertTrue(first)
        shown = []
        fake = types.SimpleNamespace(q=types.SimpleNamespace(put=shown.append))
        threading.Thread(target=mw.App.serve_instance, args=(fake, first), daemon=True).start()
        try:
            self.assertIsNone(mw.claim_instance(port))
            time.sleep(0.2)
            self.assertEqual(shown, [("show", None)])
        finally:
            first.close()

    def test_port_taken_by_something_else_still_runs(self):
        port = free_port()
        other = socket.socket()
        other.bind(("127.0.0.1", port))
        other.listen(1)
        threading.Thread(target=lambda: other.accept()[0].close(), daemon=True).start()
        try:
            self.assertIs(mw.claim_instance(port), False)
        finally:
            other.close()


# ---------------------------------------------------------------- screens and ports
class ScreensAndPorts(unittest.TestCase):
    def test_title_bar_visible(self):
        self.assertTrue(mw.title_bar_visible(100, 100, 400))
        if os.name == "nt":
            self.assertFalse(mw.title_bar_visible(-60000, -60000, 400))

    def find(self, configured, present, radios):
        app = types.SimpleNamespace()
        ports = [types.SimpleNamespace(device=p) for p in present]
        with mock.patch("serial.tools.list_ports.comports", return_value=ports), \
                mock.patch("meshtastic.util.findPorts", return_value=radios):
            return mw.App.find_port(app, configured)

    def test_port_present(self):
        self.assertEqual(self.find("COM17", ["COM17", "COM18"], ["COM17", "COM18"]), ("COM17", False))

    def test_port_case_insensitive(self):
        self.assertEqual(self.find("com17", ["COM17"], ["COM17"]), ("com17", False))

    def test_port_moved_one_radio(self):
        self.assertEqual(self.find("COM17", ["COM5"], ["COM5"]), ("COM5", True))

    def test_port_moved_two_radios_never_guesses(self):
        self.assertEqual(self.find("COM17", ["COM5", "COM6"], ["COM5", "COM6"]), ("COM17", False))

    def test_auto(self):
        self.assertEqual(self.find(None, ["COM5"], ["COM5"]), (None, False))


class PhonePushes(unittest.TestCase):
    """Drone alerts to the phone: never lost, and a burst can't flood the phone or ntfy's daily limit."""

    def make(self):
        sent, done = [], threading.Event()

        def send(title, body):
            sent.append((title, body))
            done.set()
        return mw.PhonePush("https://ntfy.example/topic", send=send), sent, done

    def wait(self, done):
        self.assertTrue(done.wait(2), "push never sent")
        done.clear()

    def test_first_alert_goes_straight_away(self):
        p, sent, done = self.make()
        p.add("Drone Detect", "RID drone 41.9,-79.9")
        self.wait(done)
        self.assertEqual(sent[0][0], "Drone alert")
        self.assertIn("Drone Detect: RID drone 41.9,-79.9", sent[0][1])

    def test_message_titles(self):
        p, sent, done = self.make()
        p.add("Chatty", "hi there", drone=False)
        self.wait(done)
        self.assertEqual(sent[0][0], "New message")
        p.add("Drone Detect", "d1")
        p.add("Chatty", "m1", drone=False)
        p.add("Chatty", "m2", drone=False)
        p.flush(now=time.time() + mw.PUSH_GAP + 1)
        self.wait(done)
        self.assertEqual(sent[1][0], "1 drone alert + 2 messages")

    def test_ntfy_url(self):
        self.assertEqual(mw.ntfy_url("cfc-abc_123"), "https://ntfy.sh/cfc-abc_123")
        self.assertEqual(mw.ntfy_url(" https://ntfy.example.com/my-topic "), "https://ntfy.example.com/my-topic")
        self.assertEqual(mw.ntfy_url(""), "")
        self.assertIsNone(mw.ntfy_url("has spaces in it"))
        self.assertIsNone(mw.ntfy_url("https://ntfy.sh/"))

    def test_burst_is_merged_and_nothing_lost(self):
        p, sent, done = self.make()
        p.add("Drone Detect", "first")
        self.wait(done)
        for i in range(8):
            p.add("Drone Detect", f"burst {i}")
        time.sleep(0.2)
        self.assertEqual(len(sent), 1, "pushed again inside the gap")
        p.flush(now=time.time() + mw.PUSH_GAP + 1)
        self.wait(done)
        self.assertEqual(sent[1][0], "8 drone alerts")
        self.assertIn("burst 0", sent[1][1])
        self.assertIn("+3 more", sent[1][1])

    def test_flush_with_nothing_waiting_sends_nothing(self):
        p, sent, done = self.make()
        p.flush(now=time.time() + 999)
        time.sleep(0.1)
        self.assertEqual(sent, [])


class RadioIdentity(unittest.TestCase):
    """A fallback port must be the same radio: a detector or friend's node must never stand in for ours."""

    def app(self, known):
        cfg = {"port": "COM17", "mode": "serial"}
        if known:
            cfg["node_id"] = known
        return types.SimpleNamespace(cfg=cfg)

    def iface(self, node_id, name="Node"):
        return types.SimpleNamespace(getMyNodeInfo=lambda: {"user": {"id": node_id, "longName": name}})

    def check(self, app, iface, port, moved):
        with mock.patch.object(mw, "save_cfg") as saved:
            return mw.App.check_radio(app, iface, port, moved), saved

    def test_fallback_to_a_different_radio_is_refused(self):
        app = self.app("!7321ed96")
        why, saved = self.check(app, self.iface("!02ed677c", "Drone Detect"), "COM4", True)
        self.assertIn("different radio", why)
        self.assertIn("Drone Detect", why)
        self.assertEqual(app.cfg["node_id"], "!7321ed96")  # never overwritten by the stranger
        saved.assert_not_called()

    def test_fallback_to_the_same_radio_is_used(self):
        app = self.app("!7321ed96")
        why, saved = self.check(app, self.iface("!7321ed96"), "COM18", True)
        self.assertIsNone(why)
        saved.assert_not_called()

    def test_first_connect_remembers_the_radio(self):
        app = self.app(None)
        why, saved = self.check(app, self.iface("!7321ed96"), "COM17", False)
        self.assertIsNone(why)
        self.assertEqual(app.cfg["node_id"], "!7321ed96")
        saved.assert_called_once()

    def test_new_radio_on_the_configured_port_is_accepted(self):
        app = self.app("!7321ed96")  # the user plugged a different node into the port they chose
        why, _ = self.check(app, self.iface("!3d3e6d9f"), "COM17", False)
        self.assertIsNone(why)
        self.assertEqual(app.cfg["node_id"], "!3d3e6d9f")

    def test_clock_is_set_and_failure_only_warns(self):
        sent = []
        app = types.SimpleNamespace(target=lambda: "COM17", clock_at=0)
        ok = types.SimpleNamespace(localNode=types.SimpleNamespace(setTime=sent.append))
        mw.App.set_clock(app, ok)
        self.assertEqual(len(sent), 1)
        self.assertLess(abs(sent[0] - time.time()), 5)
        self.assertGreater(app.clock_at, 0)

        def boom(t):
            raise OSError("radio busy")
        start = len(CAPTURE.records)
        app.clock_at = 0
        mw.App.set_clock(app, types.SimpleNamespace(localNode=types.SimpleNamespace(setTime=boom)))
        self.assertEqual(app.clock_at, 0)
        self.assertEqual([r for r in CAPTURE.records[start:] if r.levelno >= logging.ERROR], [])


class Search(unittest.TestCase):
    """The Search button must ping the drone detector on its own channel and never ping ourselves."""

    def iface(self, pings):
        channels = [types.SimpleNamespace(settings=types.SimpleNamespace(name="")),       # 0 primary
                    types.SimpleNamespace(settings=types.SimpleNamespace(name="DroneAlert"))]  # 1
        nodes = {"!7321ed96": {"user": {"id": "!7321ed96", "longName": "gramflomitehub"}},
                 "!02ed677c": {"user": {"longName": "Drone Detect"}},
                 "!3d3e6d9f": {"user": {"longName": "Dagromflimte"}}}
        return types.SimpleNamespace(
            getMyNodeInfo=lambda: {"user": {"id": "!7321ed96"}},
            nodes=nodes,
            localNode=types.SimpleNamespace(channels=channels),
            sendData=lambda data, destinationId, portNum, wantResponse, channelIndex, hopLimit:
                pings.append((destinationId, channelIndex, wantResponse)))

    def app(self, iface):
        a = types.SimpleNamespace(cfg={"push_channel": "DroneAlert"}, history={}, iface=iface,
                                  root=types.SimpleNamespace(after=lambda d, fn: fn()),
                                  notes=[])
        a.note = lambda text, tag: a.notes.append(text)
        a.channel_index = lambda name: mw.App.channel_index(a, name)
        return a

    def test_channel_index_finds_the_named_channel(self):
        a = self.app(self.iface([]))
        self.assertEqual(mw.App.channel_index(a, "DroneAlert"), 1)
        self.assertEqual(mw.App.channel_index(a, "Nope"), 0)  # falls back to the primary

    def test_search_pings_drone_on_its_channel_and_skips_self(self):
        pings = []
        iface = self.iface(pings)
        a = self.app(iface)
        with mock.patch.object(mw, "save_cfg"):
            mw.App._do_search(a, iface)
        by_id = dict((nid, ch) for nid, ch, want in pings)
        self.assertNotIn("!7321ed96", by_id)            # never ping ourselves
        self.assertEqual(by_id["!02ed677c"], 1)         # drone detector on the DroneAlert channel
        self.assertEqual(by_id["!3d3e6d9f"], 0)         # a normal node on the primary
        self.assertTrue(all(want for _, _, want in pings))  # every ping asks for a reply
        self.assertEqual(a.cfg["drone_node_id"], "!02ed677c")  # learned by name, remembered
        self.assertTrue(a.notes and "Drone Detect" in a.notes[-1])

    def test_search_remembers_drone_node_even_when_quiet(self):
        pings = []
        iface = self.iface(pings)
        iface.nodes = {"!7321ed96": {"user": {"id": "!7321ed96"}}}  # radio has forgotten the drone node
        a = self.app(iface)
        a.cfg["drone_node_id"] = "!02ed677c"
        a.history = {"!02ed677c": {"name": "Drone Detect"}}
        with mock.patch.object(mw, "save_cfg"):
            mw.App._do_search(a, iface)
        by_id = dict((nid, ch) for nid, ch, want in pings)
        self.assertEqual(by_id.get("!02ed677c"), 1)  # still pinged, on its channel


# ---------------------------------------------------------------- map tile engine (no window needed)
@unittest.skipUnless(mw.tkintermapview, "map library not installed")
class TileEngine(unittest.TestCase):
    def engine(self):
        e = types.SimpleNamespace(tiles=collections.OrderedDict(), tiles_lock=threading.Lock(), tiles_gen=0,
                                  retry_at=0, running=True, disk=tempfile.mkdtemp(prefix="tiles-"),
                                  disk_writes=0, overlay_tile_server=None, tile_server="https://t/{z}/{x}/{y}",
                                  empty_tile_image="EMPTY")
        for name in ("keep_tile", "get_tile_image_from_cache", "fetch", "prune_disk", "request_image"):
            setattr(e, name, types.MethodType(getattr(mw.SmoothMapView, name), e))
        return e

    def test_lru_cap(self):
        e = self.engine()
        with mock.patch.object(mw, "TILES_IN_MEMORY", 3):
            for i in range(5):
                e.keep_tile((10, i, 0), f"img{i}", 0)
            e.get_tile_image_from_cache(10, 2, 0)  # recently used: should survive the next insert
            e.keep_tile((10, 9, 0), "img9", 0)
        self.assertEqual(list(e.tiles), [(10, 4, 0), (10, 2, 0), (10, 9, 0)])

    def test_keys_dont_collide(self):
        # the library's own "{zoom}{x}{y}" keys made z1/x12/y3 and z11/x2/y3 the same tile
        e = self.engine()
        e.keep_tile((1, 12, 3), "A", 0)
        e.keep_tile((11, 2, 3), "B", 0)
        self.assertEqual(e.get_tile_image_from_cache(1, 12, 3), "A")
        self.assertEqual(e.get_tile_image_from_cache(11, 2, 3), "B")

    def test_style_change_drops_late_tiles(self):
        e = self.engine()
        e.tiles_gen = 2
        e.keep_tile((5, 1, 1), "old style", 1)
        self.assertFalse(e.tiles)

    def test_disk_cache_used_second_time(self):
        e = self.engine()
        calls = []

        def get(url, timeout):
            calls.append(url)
            return types.SimpleNamespace(status_code=200, content=b"tile-bytes", raise_for_status=lambda: None)
        e.http = types.SimpleNamespace(get=get)
        self.assertEqual(e.fetch(e.tile_server, 5, 1, 2), b"tile-bytes")
        self.assertEqual(e.fetch(e.tile_server, 5, 1, 2), b"tile-bytes")
        self.assertEqual(len(calls), 1, "second fetch should come from disk")

    def test_missing_tile(self):
        e = self.engine()
        e.http = types.SimpleNamespace(get=lambda url, timeout: types.SimpleNamespace(status_code=404))
        self.assertIsNone(e.fetch(e.tile_server, 5, 1, 2))

    def test_offline_backs_off(self):
        e = self.engine()
        calls = []

        def get(url, timeout):
            calls.append(url)
            raise mw.requests.ConnectionError("offline")
        e.http = types.SimpleNamespace(get=get)
        self.assertIsNone(e.request_image(5, 1, 2))
        self.assertGreater(e.retry_at, time.time() + 10)
        self.assertIsNone(e.request_image(5, 1, 3))
        self.assertEqual(len(calls), 1, "should not retry while backing off")

    def test_disk_prune(self):
        e = self.engine()
        for i in range(6):
            p = os.path.join(e.disk, f"t{i}")
            with open(p, "wb") as f:
                f.write(b"x" * 400_000)
            os.utime(p, (1000 + i, 1000 + i))
        with mock.patch.object(mw, "DISK_CACHE_MB", 1):
            e.prune_disk()
        left = sorted(os.listdir(e.disk))
        self.assertEqual(left, ["t4", "t5"], "oldest files go first")


# ---------------------------------------------------------------- demo radio
class Demo(unittest.TestCase):
    def test_packets_look_like_the_library(self):
        got = []

        def on(packet, interface):
            got.append(packet)
        mw.pub.subscribe(on, "meshtastic.receive")
        d = mw.DemoInterface((41.94, -79.98), seed=1)
        try:
            for s in d.sim:
                s["next"] = 0
            time.sleep(1.5)
        finally:
            d.close()
            mw.pub.unsubscribe(on, "meshtastic.receive")
        self.assertGreater(len(got), 10)
        for p in got:
            for key in ("fromId", "id", "rxTime", "decoded"):
                self.assertIn(key, p)
        self.assertEqual(d.getMyNodeInfo()["user"]["id"], d.my_id)

    def test_scale_roster(self):
        d = mw.DemoInterface((41.94, -79.98), seed=2, extra=300)
        d.close()
        self.assertEqual(len(d.sim), 323)
        self.assertEqual(len({s["id"] for s in d.sim}), 323, "node ids must be unique")


# ---------------------------------------------------------------- the real window, in demo mode
@unittest.skipIf(QUICK, "GUI tests skipped (--quick)")
class Gui(unittest.TestCase):
    """Drives the actual widget for a minute or two. Windows will pop up briefly."""

    def run_app(self, steps, extra=0):
        import tkinter as tk
        folder = tempfile.mkdtemp(prefix="meshgui-")
        mw.enable_demo(folder=folder, extra_nodes=extra)
        root = tk.Tk()
        errors = []
        root.report_callback_exception = lambda *exc: errors.append(exc)
        app = mw.App(root)
        app.show()
        root.geometry("980x760+60+60")
        results = {}
        t = 0
        for delay, fn in steps:
            t += delay
            root.after(int(t * 1000), lambda fn=fn: fn(app, root, results))
        root.after(int((t + 1) * 1000), app.quit)
        start = len(CAPTURE.records)
        root.mainloop()
        logged = [r for r in CAPTURE.records[start:] if r.levelno >= logging.ERROR]
        self.assertEqual(errors, [], "UI callbacks raised")
        self.assertEqual(logged, [], "errors were logged: " + "; ".join(r.getMessage() for r in logged))
        return results

    def test_demo_session(self):
        def open_map(app, root, r):
            app.tabs.select(app.map_tab)
            r["cpu0"] = (time.process_time(), time.time())

        def exercise(app, root, r):
            r["idle_cpu"] = 100 * (time.process_time() - r["cpu0"][0]) / (time.time() - r["cpu0"][1])
            hidden = []
            for nid in list(app.map_points):
                app.show_node_info(nid)
                root.update()
                txt = [c for c in app.info_win.winfo_children()[0].winfo_children() if c.winfo_class() == "Text"][0]
                if txt.dlineinfo("end-2c") is None:
                    hidden.append(nid)
            app.info_win.destroy()
            r["cards_cut_off"] = hidden
            for f in mw.MAP_FILTERS:
                app.cycle_filter()
                for _ in range(len(app.cycle_list()) + 1):
                    app.cycle_map(1)
            app.fit_map()
            root.update()
            r["fit_zoom"] = round(app.map.zoom)
            r["null_island"] = [n for n, v in app.map_points.items() if abs(v[0]) < 1 and abs(v[1]) < 1]
            dm = [v for v in app.dest["values"] if "Chatty" in v]
            app.dest.set(dm[0])
            app.entry.insert(0, "regression test")
            app.send()
            r["dm_pid"] = list(app.pending)[-1] if app.pending else None
            r["sent"] = time.time()

        def untimed_node(app, root, r):
            # a radio with no clock stores what it hears with no time: it must still be listed and mapped
            app.iface.nodes["!0bad71e5"] = {"num": 0x0bad71e5, "user": {"id": "!0bad71e5", "longName": "No Clock",
                                                                     "shortName": "NOCK"}}
            app.refresh()
            root.update()
            r["untimed_listed"] = app.tree.exists("!0bad71e5") and app.tree.item("!0bad71e5")["values"][-1] == "?"
            r["untimed_mapped"] = "!0bad71e5" in app.map_points
            del app.iface.nodes["!0bad71e5"]
            # the radio says "heard a day ago" but we saved a packet from it an hour ago: we must believe ourselves
            now = time.time()
            app.iface.nodes["!05a1e000"] = {"num": 0x05a1e000, "lastHeard": int(now - 86400),
                                            "user": {"id": "!05a1e000", "longName": "Stale Hub Copy"}}
            app.history["!05a1e000"] = {"first": now - 90000, "last": now - 3600, "count": 3}
            app.refresh()
            root.update()
            r["stale_listed"] = app.tree.exists("!05a1e000") and app.tree.item("!05a1e000")["values"][-1] == "1h"
            del app.iface.nodes["!05a1e000"]
            # every pin on the map can be messaged, even ones the radio doesn't know (internet-map nodes)
            lat, lon = app.home() or (41.94, -79.98)
            app.cfg["online_nodes"] = True  # don't depend on the tester's own map settings
            app.online["!0e7e7e7e"] = {"name": "Web Only", "lat": lat + 0.05, "lon": lon + 0.05, "acc": 0,
                                       "role": "CLIENT", "seen": now - 600}
            app.update_map()
            root.update()
            listed = [v for v in app.dest["values"] if v.endswith("[!0e7e7e7e]")]
            r["online_in_dest"] = bool(listed) and "(internet)" in listed[0]
            r["all_pins_in_dest"] = [n for n in app.map_points if n != app.last_me[0]
                                     and not any(v.endswith(f"[{n}]") for v in app.dest["values"])]
            app.show_node_info("!0e7e7e7e")
            root.update()
            msg_btn = [b for b in app.info_win.winfo_children()[1].winfo_children()
                       if b.winfo_class() == "Button" and b["text"] == "Message"]
            r["card_has_message"] = bool(msg_btn)
            if msg_btn:
                msg_btn[0].invoke()  # opens a message panel on the card itself
            root.update()
            r["card_chat"] = app.card_chat is not None and app.card_chat[1] == "!0e7e7e7e"
            app.refresh()  # a refresh mustn't swap the chosen recipient back to Everyone
            root.update()
            r["dest_kept"] = app.dest.get().endswith("[!0e7e7e7e]")
            if r["card_chat"]:
                app.card_entry.insert(0, "hello web node")
                app.card_entry.event_generate("<Return>")
                root.update()
                r["card_shows_sent"] = "hello web node" in app.card_chat[0].get("1.0", "end")
            r["sent_to"] = app.msgs.get("1.0", "end")
            # the radio refuses a DM it has no key for: we must say so plainly and ask the node for its key
            pid = next((k for k, v in app.pending_nid.items() if v == "!0e7e7e7e"), None)
            app.key_asked.pop("!0e7e7e7e", None)
            if pid:
                app.handle_packet({"fromId": app.last_me[0], "decoded": {
                    "portnum": "ROUTING_APP", "requestId": pid,
                    "routing": {"errorReason": "PKI_SEND_FAIL_PUBLIC_KEY"}}}, app.iface)
            r["pki_explained"] = "encryption key" in app.msgs.get("1.0", "end")
            r["pki_key_asked"] = "!0e7e7e7e" in app.key_asked
            app.info_win.destroy()
            root.update()
            r["chat_cleared"] = app.card_chat is None
            del app.online["!0e7e7e7e"]
            app.dest.set("Everyone")
            # map Show: "Now" drops grey (not-active) pins, History time frames bring them back
            app.history["!01d0ffee"] = {"first": now - 7 * 86400, "last": now - 2 * 86400, "count": 1,
                                        "name": "Two Days Ago", "lat": lat - 0.03, "lon": lon - 0.03}
            app.set_map_seen("now")
            r["now_hides_old"] = "!01d0ffee" not in app.map_points
            app.set_map_seen(86400)
            r["day_hides_old"] = "!01d0ffee" not in app.map_points
            app.set_map_seen(30 * 86400)
            r["month_shows_old"] = "!01d0ffee" in app.map_points
            app.set_map_seen(None)
            r["all_shows_old"] = "!01d0ffee" in app.map_points
            del app.history["!01d0ffee"]
            # Clear map history: old recorded nodes go, nodes the radio still lists come back
            app.history["!01d0ffee"] = {"first": now - 9e5, "last": now - 8e5, "count": 1, "name": "Old"}
            real_ask = mw.messagebox.askyesno
            mw.messagebox.askyesno = lambda *a, **k: True
            try:
                app.clear_map_history()
            finally:
                mw.messagebox.askyesno = real_ask
            root.update()
            r["history_cleared"] = "!01d0ffee" not in app.history and "!01d0ffee" not in app.map_points
            r["radio_nodes_back"] = len(app.history) > 0
            # three message panels; each pops out into a window with its own send controls
            alert = ("Drone: 60:60:1f:00:00:01 RSSI:-71 https://maps.google.com/?q=41.95,-79.97\r\n"
                     "Pilot: https://maps.google.com/?q=41.94,-79.98\r\n")
            app.handle_packet({"fromId": "!02ed677c", "id": 4242, "to": 0xFFFFFFFF, "channel": 0,
                               "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": alert}}, app.iface)
            root.update()
            r["banner"] = app.banner.winfo_ismapped() and "DRONE DETECTED" in app.banner_lbl["text"]
            r["drone_panel"] = "60:60:1f:00:00:01" in app.panels["drone"]["texts"][0].get("1.0", "end")
            r["drone_not_chan"] = "60:60:1f" not in app.panels["chan"]["texts"][0].get("1.0", "end")
            app.hide_banner()
            root.update()
            r["banner_hidden"] = not app.banner.winfo_ismapped()
            controls = {}
            for k in ("dm", "drone", "chan"):
                app.pop_panel(k)
                root.update()
                bar = app.panels[k]["win"].winfo_children()[-1]
                controls[k] = sorted(c.winfo_class() for c in bar.winfo_children())
            r["pop_controls"] = controls
            bar = app.panels["chan"]["win"].winfo_children()[-1]
            entry = [c for c in bar.winfo_children() if c.winfo_class() == "Entry"][0]
            entry.insert(0, "hello channel from popout")
            entry.event_generate("<Return>")
            root.update()
            r["pop_sent"] = "hello channel from popout" in app.panels["chan"]["texts"][0].get("1.0", "end")
            for k in ("dm", "drone", "chan"):
                app.panels[k]["win"].destroy()
            # right-click archiving: one message, or a whole conversation, leaves the panels
            chan_txt = app.panels["chan"]["texts"][0]
            m = next(v for v in app.line_msgs.values() if v.get("text") == "hello channel from popout")
            app.archive_msgs(msg=m)
            root.update()
            r["line_archived"] = "hello channel from popout" not in chan_txt.get("1.0", "end")
            dm_txt = app.panels["dm"]["texts"][0]
            chatty = next((v for v in app.line_msgs.values() if v.get("from") == "Chatty Cathy"
                           or v.get("to") == "Chatty Cathy"), None)
            if chatty:
                app.archive_msgs(peer=app.msg_peer(chatty)[0])
                root.update()
                shown = dm_txt.get("1.0", "end")
                r["convo_archived"] = "Chatty Cathy:" not in shown and "→ Chatty Cathy:" not in shown
            else:
                r["convo_archived"] = True  # nothing from Chatty in this run
            app.add_msg({"id": 777001, "t": time.time() + 1, "from": "Chatty Cathy", "to": "Me",
                         "fid": chatty and chatty.get("fid") or chatty and chatty.get("tid"),
                         "tid": "!me", "text": "new after archive"}, False)
            root.update()
            r["new_still_shows"] = "new after archive" in dm_txt.get("1.0", "end")
            # chat archive: finds old messages by text and kind, and Clear keeps them archived
            real_ask = mw.messagebox.askyesno
            mw.messagebox.askyesno = lambda *a, **k: True
            try:
                app.clear_messages()
            finally:
                mw.messagebox.askyesno = real_ask
            r["panels_cleared"] = "hello channel from popout" not in app.panels["chan"]["texts"][0].get("1.0", "end")
            app.open_archive()
            root.update()
            aw = app.archive_win
            bar = aw.winfo_children()[0]
            query = [c for c in bar.winfo_children() if c.winfo_class() == "Entry"][0]
            query.insert(0, "popout")
            [c for c in bar.winfo_children() if c.winfo_class() == "Button" and c["text"] == "Search"][0].invoke()
            root.update()
            text = [c for c in aw.winfo_children() if c.winfo_class() == "Text"][0].get("1.0", "end")
            r["archive_finds"] = "hello channel from popout" in text and "60:60:1f" not in text
            aw.destroy()
            app.clear_archive()
            r["archive_empty"] = os.path.getsize(mw.LOG_PATH) == 0
            # phone push modes: drone-only by default, then direct messages, then everything
            pushed = []

            class FakePush:
                def add(self, sender, text, drone=True):
                    pushed.append((text, drone))
            app.push = FakePush()
            my = app.last_me[0]

            def incoming(text, to_me, pid):
                app.handle_packet({"fromId": "!0de30001", "id": pid, "toId": my if to_me else "^all",
                                   "to": 1 if to_me else 0xFFFFFFFF, "channel": 0,
                                   "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": text}}, app.iface)
            for mode, base in (("drone", 881000), ("dm", 882000), ("all", 883000)):
                app.cfg["push_mode"] = mode
                incoming(f"public {mode}", False, base + 1)
                incoming(f"private {mode}", True, base + 2)
                incoming("Drone: 60:60:1f:00:00:02 RSSI:-60 https://maps.google.com/?q=41.95,-79.97\r\n"
                         "Pilot: https://maps.google.com/?q=41.94,-79.98\r\n", False, base + 3)
            r["pushed"] = pushed
            app.push = None
            app.cfg["push_mode"] = "drone"
            # airspace: two detectors hear the same drone; a low helicopter flies close by
            now = time.time()
            app.history["!0de7ec70"] = {"name": "<script>alert(1)</script>", "first": now, "last": now, "count": 1}
            dlat, dlon = 41.95, -79.97
            alert_text = (f"Drone: 60:60:1f:aa:bb:cc RSSI:-70 https://maps.google.com/?q={dlat},{dlon}" + chr(13) + chr(10)
                          + "Pilot: https://maps.google.com/?q=41.94,-79.98" + chr(13) + chr(10))
            for pid, det in ((991001, "!0de7ec70"), (991002, "!02ed677c")):
                app.handle_packet({"fromId": det, "id": pid, "to": 0xFFFFFFFF, "channel": 0, "rxTime": now,
                                   "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": alert_text}}, app.iface)
            d = app.drone_seen["60:60:1f:aa:bb:cc"]
            r["two_detectors"] = len(d["detectors"]) == 2 and d["pings"] == 1
            r["drone_on_main_map"] = "60:60:1f:aa:bb:cc" in app.dm_markers
            app.aircraft = {a["hex"]: a for a in mw.parse_aircraft({"ac": [
                {"hex": "a1b2c3", "flight": "LIFE1", "category": "A7", "alt_baro": 1500, "track": 45,
                 "lat": dlat + 0.01, "lon": dlon + 0.01, "seen_pos": 1},
                {"hex": "a1b2c4", "flight": "HIGH1", "alt_baro": 30000, "lat": dlat, "lon": dlon, "seen_pos": 1},
                {"hex": "a1b2c5", "flight": "FAR1", "alt_baro": 1000, "lat": dlat + 1, "lon": dlon, "seen_pos": 1}]})}
            app.draw_aircraft()
            found = app.check_conflicts()
            mine = "60:60:1f:aa:bb:cc"
            r["conflicts"] = sorted({a["call"] for dd, a, _ in found if dd["mac"] == mine})
            r["conflict_shown"] = "CONFLICT" in app.banner_lbl["text"]
            app.check_conflicts()  # again straight away: must not warn a second time
            r["conflict_once"] = sum(1 for c in app.conflicts if c["mac"] == mine) == 1
            reports = []
            real_open = mw.webbrowser.open
            mw.webbrowser.open = lambda url: reports.append(url)
            try:
                path = app.make_report()
            finally:
                mw.webbrowser.open = real_open
            page = ""
            if path:
                with open(path, encoding="utf-8") as fh:
                    page = fh.read()
            r["report_ok"] = bool(path) and "LIFE1" in page and "60:60:1f:aa:bb:cc" in page and reports
            r["report_escaped"] = "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
            if path:
                os.remove(path)
            high = (f"Drone: 60:60:1f:aa:bb:dd RSSI:-66 https://maps.google.com/?q={dlat + 0.5},{dlon + 0.5} "
                    "ALT:700m AGL:200m SPD:9m/s ID:TESTSERIAL1" + chr(13) + chr(10)
                    + "Pilot: https://maps.google.com/?q=41.94,-79.98" + chr(13) + chr(10))
            app.handle_packet({"fromId": "!02ed677c", "id": 991010, "to": 0xFFFFFFFF, "channel": 0, "rxTime": now,
                               "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": high}}, app.iface)
            r["high_flagged"] = any("over the 400 ft limit" in h["what"] for h in app.airspace_hits
                                    if h["mac"] == "60:60:1f:aa:bb:dd")
            r["serial_kept"] = app.drone_seen["60:60:1f:aa:bb:dd"].get("serial") == "TESTSERIAL1"
            # airspace: the same live drone sits inside a no-fly zone and an airport's Class D
            box = [[(dlat - 0.05, dlon - 0.05), (dlat + 0.05, dlon - 0.05), (dlat + 0.05, dlon + 0.05),
                    (dlat - 0.05, dlon + 0.05)]]
            far = [[(dlat + 1, dlon + 1), (dlat + 1.1, dlon + 1), (dlat + 1.1, dlon + 1.1)]]
            app.airspace = {"zones": [{"name": "TEST CLASS D", "type": "CLASS_D", "lower": 0, "upper": 3200,
                                       "polys": [box]}],
                            "grid": [{"ceil": 100, "apt": "Test Field", "polys": [box]}],
                            "tfr": {"9/9999": {"name": "Test wildfire", "lower": "0", "upper": "8000",
                                               "start": now - 60, "end": now + 3600, "polys": [box]},
                                    "9/9998": {"name": "High TFR", "lower": "9000", "upper": "17999",
                                               "start": now - 60, "end": now + 3600, "polys": [box]},
                                    "9/9997": {"name": "Far TFR", "lower": "0", "upper": "5000",
                                               "start": now - 60, "end": now + 3600, "polys": [far]}},
                            "zones_at": now, "tfr_at": now, "center": [dlat, dlon]}
            app.cfg["airspace_on"] = True
            app.draw_airspace()
            r["airspace_drawn"] = len(app.as_shapes) >= 4
            hits = {k for dd, k, _ in app.check_airspace() if dd["mac"] == mine}
            r["airspace_hits"] = sorted(hits)
            r["airspace_banner"] = "AIRSPACE" in app.banner_lbl["text"]
            app.cfg["airspace_on"] = False
            app.draw_airspace()
            r["airspace_cleared"] = app.as_shapes == []
            app.airspace = {}
            app.aircraft = {}
            app.draw_aircraft()
            app.hide_banner()
            del app.history["!0de7ec70"]
            # feedback form: opens, builds a report with details, never sends anything by itself
            opened = []
            real_open = mw.webbrowser.open
            mw.webbrowser.open = lambda url: opened.append(url)
            try:
                app.open_feedback()
                root.update()
                fw = app.feedback_win
                entries = [c for c in fw.winfo_children() if c.winfo_class() == "Entry"]
                entries[0].insert(0, "map pins jump around")
                bar = [c for c in fw.winfo_children() if c.winfo_class() == "Frame"][-1]
                [b for b in bar.winfo_children() if b["text"] == "Open on GitHub"][0].invoke()
                root.update()
            finally:
                mw.webbrowser.open = real_open
            r["feedback_url"] = opened[0] if opened else ""
            fw.destroy()
            app.hide_banner()

        def lose_radio(app, root, r):
            # the radio's delivery report (delivered or not) was received and matched to our message
            r["delivered"] = r.get("dm_pid") is not None and r["dm_pid"] not in app.pending
            mw.pub.sendMessage("meshtastic.connection.lost", interface=app.iface)
            r["lost_at"] = time.time()

        def check_back(app, root, r):
            r["reconnected"] = app.iface is not None
            r["pins"] = len(app.map_points)

        r = self.run_app([(8, open_map), (30, exercise), (2, untimed_node), (6, lose_radio), (20, check_back)])
        self.assertLess(r["idle_cpu"], 3.0, f"busy-mesh CPU {r['idle_cpu']:.1f}%")
        self.assertEqual(r["cards_cut_off"], [])
        self.assertGreaterEqual(r["fit_zoom"], 6, "Fit all zoomed out too far (bad positions?)")
        self.assertEqual(r["null_island"], [])
        self.assertTrue(r["delivered"], "no delivery report for a direct message")
        self.assertTrue(r["untimed_listed"], "a node with no timestamp was hidden from the Active list")
        self.assertTrue(r["untimed_mapped"], "a node with no timestamp was left off the map")
        self.assertTrue(r["stale_listed"], "a node we heard an hour ago was hidden because the radio's time was stale")
        self.assertTrue(r["online_in_dest"], "an internet-map node was missing from Message-to")
        self.assertEqual(r["all_pins_in_dest"], [], "map pins missing from Message-to")
        self.assertTrue(r["card_has_message"], "node card had no Message button")
        self.assertTrue(r["dest_kept"], "a refresh reset the chosen recipient")
        self.assertIn("Web Only", r["sent_to"])
        self.assertTrue(r["card_chat"], "Message didn't open a message panel on the card")
        self.assertTrue(r["pki_explained"], "a missing-key refusal wasn't explained")
        self.assertTrue(r["pki_key_asked"], "a missing-key refusal didn't ask the node for its key")
        self.assertTrue(r["history_cleared"], "Clear map history left old nodes behind")
        self.assertTrue(r["banner"], "no red banner for a drone alert")
        self.assertTrue(r["banner_hidden"], "Dismiss didn't hide the banner")
        self.assertTrue(r["drone_panel"] and r["drone_not_chan"], "drone alert went to the wrong panel")
        for k, classes in r["pop_controls"].items():
            self.assertIn("Entry", classes, f"{k} pop-out has no message box")
            self.assertIn("Button", classes, f"{k} pop-out has no Send")
        self.assertIn("TCombobox", r["pop_controls"]["dm"], "DM pop-out has no recipient picker")
        self.assertIn("TCombobox", r["pop_controls"]["chan"], "channel pop-out has no channel picker")
        self.assertTrue(r["pop_sent"], "sending from a pop-out window didn't land in its panel")
        self.assertTrue(r["panels_cleared"], "Clear didn't clear the panels")
        self.assertTrue(r["line_archived"], "Archive this message left it in the panel")
        self.assertTrue(r["convo_archived"], "Archive conversation left messages in the panel")
        self.assertTrue(r["new_still_shows"], "a new message after archiving a conversation was hidden")
        self.assertTrue(r["archive_finds"], "the archive search didn't find a cleared message")
        self.assertTrue(r["archive_empty"], "Clear archive left messages on disk")
        self.assertTrue(r["two_detectors"], "the same ping from two detectors wasn't merged into one")
        self.assertTrue(r["drone_on_main_map"], "a live drone didn't appear on the main map")
        self.assertEqual(r["conflicts"], ["LIFE1"], "conflict check picked the wrong aircraft")
        self.assertTrue(r["conflict_shown"], "no conflict banner")
        self.assertTrue(r["conflict_once"], "the same conflict warned twice")
        self.assertTrue(r["report_ok"], "incident report missing or incomplete")
        self.assertTrue(r["report_escaped"], "incident report let a node name inject HTML")
        self.assertTrue(r["high_flagged"], "a drone reporting 650 ft above ground wasn't flagged")
        self.assertTrue(r["serial_kept"], "the drone's Remote ID serial wasn't kept")
        self.assertTrue(r["airspace_drawn"], "airspace outlines weren't drawn")
        self.assertEqual(r["airspace_hits"], ["grid:Test Field", "tfr:Test wildfire", "zone:TEST CLASS D"],
                         "airspace check found the wrong zones (high or far TFRs must not count)")
        self.assertTrue(r["airspace_banner"], "a drone in a no-fly zone didn't raise the banner")
        self.assertTrue(r["airspace_cleared"], "turning airspace off left outlines on the map")
        self.assertIn("issues/new", r["feedback_url"], "feedback didn't open a GitHub issue link")
        self.assertIn("map+pins+jump+around", r["feedback_url"])
        self.assertIn("App+version", r["feedback_url"], "feedback report is missing the app details")
        texts = [t for t, _ in r["pushed"]]
        self.assertNotIn("public drone", texts, "drone-only mode pushed a channel message")
        self.assertNotIn("private drone", texts, "drone-only mode pushed a direct message")
        self.assertIn("private dm", texts, "direct-message mode didn't push a DM")
        self.assertNotIn("public dm", texts, "direct-message mode pushed a channel message")
        self.assertIn("public all", texts)
        self.assertIn("private all", texts)
        self.assertEqual(sum(1 for t, d in r["pushed"] if d), 3, "every drone alert should push, in every mode")
        self.assertTrue(r["radio_nodes_back"], "nodes the radio still knows didn't come back after clearing")
        self.assertTrue(r.get("card_shows_sent"), "a message sent from the card didn't show on it")
        self.assertTrue(r["chat_cleared"], "closing the card left its chat hooked up")
        self.assertTrue(r["now_hides_old"] and r["day_hides_old"], "map Show filter kept old pins")
        self.assertTrue(r["month_shows_old"] and r["all_shows_old"], "map History filter hid a node")
        self.assertTrue(r["reconnected"], "did not reconnect after the radio dropped")
        self.assertGreater(r["pins"], 15)

    def test_busy_city_scale(self):
        def start(app, root, r):
            app.tabs.select(app.map_tab)

        def measure(app, root, r):
            t0 = time.perf_counter()
            app.refresh()
            r["refresh_ms"] = 1000 * (time.perf_counter() - t0)
            r["cpu0"] = (time.process_time(), time.time())

        def finish(app, root, r):
            r["cpu"] = 100 * (time.process_time() - r["cpu0"][0]) / (time.time() - r["cpu0"][1])
            r["nodes"] = len(app.iface.nodes)
            r["mem"] = mw.memory_mb()

        r = self.run_app([(5, start), (60, measure), (30, finish)], extra=300)
        print(f"\n  scale: {r['nodes']} nodes, refresh {r['refresh_ms']:.0f} ms, CPU {r['cpu']:.1f}%, "
              f"memory {r['mem']:.0f} MB", file=sys.stderr)
        self.assertGreater(r["nodes"], 150)
        self.assertLess(r["refresh_ms"], 400, "one refresh is too slow for a busy mesh")
        self.assertLess(r["cpu"], 15, "CPU too high on a busy mesh")


if __name__ == "__main__":
    unittest.main(argv=[a for a in sys.argv if a != "--quick"])
