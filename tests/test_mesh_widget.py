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

        def lose_radio(app, root, r):
            r["delivered"] = "delivered to Chatty" in app.msgs.get("1.0", "end") or \
                             "not delivered to Chatty" in app.msgs.get("1.0", "end")
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
