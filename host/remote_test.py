"""Tests for remote mode (host/remote.py): the pairing and the reports as a state machine, the pages
and their headers, the address a tablet is sent to, what the CLI and the server say to each other,
and -- where a port can be opened -- a tablet's whole visit."""

import asyncio
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import remote  # noqa: E402

VECTORS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "hud", "tests", "fixtures", "remote_vectors.json")
KEY_TEXT = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"     # bytes 0..31: a test key, nobody's
KEY = bytes(range(32))

try:
    import websockets  # noqa: F401
    HAVE_WEBSOCKETS = True
except ImportError:
    HAVE_WEBSOCKETS = False


def can_listen():
    """Whether this process may open a local port at all (a sandbox may say no)."""
    import socket
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def paired(nonce="00112233445566778899aabbccddeeff", cnonce="ffeeddccbbaa99887766554433221100", **kw):
    s = remote.Session(KEY, nonce=nonce, **kw)
    what, reply = s.take(json.dumps({"kind": "auth", "v": 1, "cnonce": cnonce,
                                     "mac": remote.mac(KEY, remote.auth_text(nonce, cnonce))}))
    return s, what, reply


def report(s, seq, mods=0, keys=(), consumer=(), mac=None):
    msg = {"kind": "hid", "v": 1, "seq": seq, "mods": mods, "keys": list(keys)}
    if consumer:
        msg["consumer"] = list(consumer)
    msg["mac"] = mac or remote.mac(s.skey, remote.hid_text(seq, mods, keys, consumer))
    return json.dumps(msg)


class Key(unittest.TestCase):
    def test_43_base64url_characters_are_32_bytes(self):
        text = remote.new_key()
        self.assertEqual(32, len(remote.key_bytes(text)))
        self.assertNotEqual(text, remote.new_key())
        self.assertEqual(KEY, remote.key_bytes(KEY_TEXT))
        for bad in ("", KEY_TEXT[:-1], KEY_TEXT + "A", KEY_TEXT[:-1] + "+", None, 5):
            with self.assertRaises(ValueError):
                remote.key_bytes(bad)

    def test_the_vectors_the_page_is_tested_with_are_this_protocols(self):
        # hud/tests/remote_test.js holds remote.js to the same file.
        with open(VECTORS) as f:
            v = json.load(f)
        key = remote.key_bytes(v["key"])
        self.assertEqual(v["kid"], remote.kid(key))
        self.assertEqual(v["auth_text"], remote.auth_text(v["nonce"], v["cnonce"]))
        self.assertEqual(v["auth_mac"], remote.mac(key, v["auth_text"]))
        sk = remote.session_key(key, v["nonce"], v["cnonce"])
        self.assertEqual(v["session_key"], sk.hex())
        self.assertEqual(v["welcome_mac"], remote.mac(sk, remote.WELCOME_TEXT))
        for r in v["reports"]:
            text = remote.hid_text(r["seq"], r["mods"], r["keys"], r.get("consumer", []))
            self.assertEqual((r["text"], r["mac"]), (text, remote.mac(sk, text)))


class Pairing(unittest.TestCase):
    def test_the_key_proved_is_a_welcome_the_tablet_can_check(self):
        s = remote.Session(KEY, nonce="ab" * 16)
        self.assertEqual({"kind": "challenge", "v": 1, "kid": remote.kid(KEY), "nonce": "ab" * 16}, s.challenge())
        s, what, reply = paired()
        self.assertEqual("welcome", what)
        self.assertTrue(s.paired)
        sk = remote.session_key(KEY, "00112233445566778899aabbccddeeff", "ffeeddccbbaa99887766554433221100")
        self.assertEqual({"kind": "welcome", "v": 1, "mac": remote.mac(sk, remote.WELCOME_TEXT)}, reply)

    def test_a_wrong_key_counts_and_anything_else_is_only_refused(self):
        s = remote.Session(KEY)
        wrong = remote.mac(bytes(32), remote.auth_text(s.nonce, "11" * 16))
        with self.assertRaises(remote.Refused) as e:
            s.take(json.dumps({"kind": "auth", "v": 1, "cnonce": "11" * 16, "mac": wrong}))
        self.assertTrue(e.exception.failure)
        self.assertFalse(s.paired)
        for junk in ("not json", "[]", '{"kind":"auth"}', json.dumps({"kind": "hid", "v": 1}),
                     json.dumps({"kind": "auth", "v": 1, "cnonce": "XY" * 16, "mac": wrong}), b"{}", "x" * 5000):
            with self.assertRaises(remote.Refused) as e:
                remote.Session(KEY).take(junk)
            self.assertFalse(e.exception.failure, junk)


class Reports(unittest.TestCase):
    def test_a_proved_report_is_handed_on_clean(self):
        s, _, _ = paired()
        self.assertEqual(("hid", {"kind": "hid", "v": 1, "seq": 1, "mods": 2, "keys": [4]}), s.take(report(s, 1, 2, [4])))
        self.assertEqual(("hid", {"kind": "hid", "v": 1, "seq": 2, "mods": 0, "keys": [], "consumer": [233]}),
                         s.take(report(s, 2, consumer=[233])))
        self.assertEqual(("drop", None), s.take(json.dumps({"kind": "ping", "v": 1})))

    def test_a_wrong_mac_or_a_report_out_of_turn_ends_it_and_counts(self):
        s, _, _ = paired()
        with self.assertRaises(remote.Refused) as e:
            s.take(report(s, 1, keys=[4], mac=remote.mac(KEY, remote.hid_text(1, 0, [4]))))   # the pairing key, not the session's
        self.assertTrue(e.exception.failure)
        s.take(report(s, 5, keys=[4]))
        for seq in (5, 4):
            with self.assertRaises(remote.Refused) as e:
                s.take(report(s, seq, keys=[4]))
            self.assertTrue(e.exception.failure)
        # A report whose MAC covers other keys than it carries.
        msg = json.loads(report(s, 9, keys=[4]))
        msg["keys"] = [0x28]
        with self.assertRaises(remote.Refused):
            s.take(json.dumps(msg))

    def test_more_than_max_frames_a_second_are_dropped(self):
        now = [100.0]
        s, _, _ = paired(clock=lambda: now[0], max_frames=3)
        self.assertEqual(["hid", "hid", "hid", "drop"], [s.take(report(s, n, keys=[4]))[0] for n in range(1, 5)])
        now[0] += 1.0
        self.assertEqual("hid", s.take(report(s, 9))[0])


class PagesTest(unittest.TestCase):
    def setUp(self):
        self.pages = remote.Pages("192.168.1.5:8768")
        self.ok = {"Host": "192.168.1.5:8768"}

    def test_the_remote_page_is_index_html_marked_and_with_its_script(self):
        status, headers, body = self.pages.route("/", self.ok)
        self.assertEqual(200, status)
        html = body.decode("utf-8")
        self.assertIn('<html lang="en" class="remote">', html)
        self.assertIn('<script src="touch.js"></script>\n<script src="remote.js"></script>', html)
        self.assertIn('<link rel="manifest" href="manifest.webmanifest">', html)
        with self.assertRaises(ValueError):
            remote.remote_html("<html>")

    def test_every_file_it_loads_is_served_and_nothing_else(self):
        _, _, body = self.pages.route("/", self.ok)
        import re
        names = re.findall(r'(?:src|href)="([\w.-]+)"', body.decode("utf-8"))
        self.assertIn("remote.js", names)
        for name in names:
            status, headers, _ = self.pages.route("/" + name, self.ok)
            self.assertEqual(200, status, name)
        for path in ("/index.html", "/keys.html", "/../host/remote.py", "/%2e%2e/host/cli.py", "/tests/dom.js", "/hud.js/"):
            self.assertEqual(404, self.pages.route(path, self.ok)[0], path)
        status, headers, _ = self.pages.route("/hud.js?x=1", self.ok)
        self.assertEqual((200, "text/javascript; charset=utf-8"), (status, dict(headers)["Content-Type"]))

    def test_every_answer_runs_only_its_own_scripts_and_is_never_kept_or_framed(self):
        for path in ("/", "/hud.js", "/nothing"):
            headers = dict(self.pages.route(path, self.ok)[1])
            csp = headers["Content-Security-Policy"]
            self.assertIn("script-src 'self';", csp)
            self.assertIn("connect-src 'self' ws://192.168.1.5:8768;", csp)
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertEqual(("no-store", "nosniff", "no-referrer", "DENY"),
                             (headers["Cache-Control"], headers["X-Content-Type-Options"],
                              headers["Referrer-Policy"], headers["X-Frame-Options"]))

    def test_another_host_header_is_refused_whatever_it_asks_for(self):
        # A DNS name pointed at this address (rebinding) says its own name in Host.
        for host in ("evil.example:8768", "192.168.1.5", "192.168.1.5:80", None):
            self.assertEqual(421, self.pages.route("/", {"Host": host})[0], host)
            self.assertEqual(421, self.pages.route("/ws", {"Host": host, "Origin": "http://192.168.1.5:8768"})[0])

    def test_the_socket_is_for_the_pages_own_origin_alone(self):
        self.assertEqual("ws", self.pages.route("/ws", dict(self.ok, Origin="http://192.168.1.5:8768")))
        for origin in (None, "null", "http://evil.example", "https://192.168.1.5:8768", "http://192.168.1.5"):
            self.assertEqual(403, self.pages.route("/ws", dict(self.ok, Origin=origin))[0], origin)


class Address(unittest.TestCase):
    def test_the_default_routes_address_when_only_a_local_network_reaches_it(self):
        for route in ("192.168.1.5", "10.0.0.7", "172.20.1.1", "169.254.3.4", "100.101.102.103"):
            self.assertEqual((route, route), remote.choose_address("", route))

    def test_no_network_a_loopback_or_a_public_address_is_a_sentence(self):
        for route, said in ((None, "Wi-Fi"), ("127.0.0.1", "no network but its own"), ("8.8.8.8", "--bind")):
            with self.assertRaises(remote.NoAddress) as e:
                remote.choose_address("", route)
            self.assertIn(said, str(e.exception))

    def test_bind_is_taken_as_given(self):
        self.assertEqual(("100.64.0.9", "100.64.0.9"), remote.choose_address("100.64.0.9", "192.168.1.5"))
        self.assertEqual(("8.8.8.8", "8.8.8.8"), remote.choose_address("8.8.8.8", None))
        self.assertEqual(("0.0.0.0", "192.168.1.5"), remote.choose_address("0.0.0.0", "192.168.1.5"))
        for bind, route in (("wifi", "192.168.1.5"), ("0.0.0.0", None)):
            with self.assertRaises(remote.NoAddress):
                remote.choose_address(bind, route)
        self.assertEqual(("192.168.1.5:8768", "[fe80::1]:8768"),
                         (remote.url_host("192.168.1.5", 8768), remote.url_host("fe80::1", 8768)))


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name

    def test_the_key_is_made_once_kept_for_this_user_alone_and_unpaired(self):
        self.assertIsNone(remote.load_key(self.d))
        key = remote.load_key(self.d, create=True)
        self.assertEqual(key, remote.load_key(self.d, create=True))
        self.assertEqual(0o600, stat.S_IMODE(os.stat(os.path.join(self.d, remote.KEY_FILE)).st_mode))
        self.assertTrue(remote.unpair(self.d))
        self.assertFalse(remote.unpair(self.d))
        self.assertNotEqual(key, remote.load_key(self.d, create=True))

    def test_a_request_is_read_back_and_nothing_is_an_empty_one(self):
        self.assertEqual({}, remote.read_request(self.d))
        req = remote.write_request(self.d, True, minutes=5, port=9000, bind="100.64.0.9", now=1000.0)
        self.assertEqual(req, remote.read_request(self.d))
        self.assertEqual((True, 1300.0, 9000, "100.64.0.9"), (req["on"], req["until"], req["port"], req["bind"]))
        self.assertEqual({}, remote.read_live(self.d))


class FakeTyping:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def release(self):
        self.calls.append("release")

    def hid(self, msg):
        self.calls.append(("hid", msg))


class Decide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.c = remote.Controller(self.d, FakeTyping(), log=lambda *a: None)

    def serving(self):
        self.c.serving = ("192.168.1.5", 8768, "192.168.1.5:8768")

    def test_nothing_asked_nothing_done(self):
        self.assertIsNone(self.c.decide(1000.0))

    def test_asked_it_serves_until_its_time_is_up(self):
        remote.load_key(self.d, create=True)
        remote.write_request(self.d, True, minutes=1, now=1000.0)
        self.assertEqual("start", self.c.decide(1000.0))
        self.serving()
        self.assertIsNone(self.c.decide(1059.0))
        self.assertEqual("stop", self.c.decide(1060.0))
        self.assertEqual("its time was up", self.c.why)

    def test_a_tablet_connected_keeps_it_open(self):
        remote.load_key(self.d, create=True)
        remote.write_request(self.d, True, minutes=1, now=1000.0)
        self.c.decide(1000.0)
        self.serving()
        self.c.client = object()
        self.assertIsNone(self.c.decide(1050.0))
        self.assertIsNone(self.c.decide(1100.0))
        self.c.client = None
        self.assertIsNone(self.c.decide(1159.0))          # a minute from the last time it was renewed
        self.assertEqual("stop", self.c.decide(1160.0))

    def test_off_stops_it_and_without_a_key_it_never_starts(self):
        remote.write_request(self.d, True, now=1000.0)
        self.assertIsNone(self.c.decide(1000.0))
        self.assertIn("no pairing key", self.c.why)
        remote.load_key(self.d, create=True)
        self.assertEqual("start", self.c.decide(1001.0))
        self.serving()
        remote.write_request(self.d, False)
        self.assertEqual("stop", self.c.decide(1002.0))
        self.assertEqual("", self.c.why)

    def test_max_failures_turn_it_off_until_it_is_asked_again(self):
        remote.load_key(self.d, create=True)
        remote.write_request(self.d, True, now=1000.0)
        self.c.decide(1000.0)
        self.serving()
        for _ in range(remote.MAX_FAILURES):
            self.c.failed()
        self.assertEqual("stop", self.c.decide(1001.0))
        self.assertIn("failed attempts", self.c.why)
        self.c.serving = None
        self.assertIsNone(self.c.decide(1002.0))
        remote.write_request(self.d, True, now=1003.0)
        self.assertEqual("start", self.c.decide(1003.0))
        self.assertEqual(0, self.c.failures)

    def test_what_it_says_it_is_doing(self):
        self.serving()
        self.c.req_id = "abcd"
        self.c._forward({"kind": "typing", "state": "denied", "fix": "zmk-layer-hud typing setup"})
        live = remote.read_live(self.d)
        self.assertEqual(("abcd", True, False, "denied", "http://192.168.1.5:8768/"),
                         (live["id"], live["serving"], live["client"], live["typing"], live["url"]))
        self.assertEqual(0o600, stat.S_IMODE(os.stat(os.path.join(self.d, remote.LIVE_FILE)).st_mode))


class FakeSocket:
    """A connection as the handler sees it, with a tablet on the other end: `tablet(msg)` is what
    the tablet answers each message the server sends with (a list of raw messages, or None to hang
    up)."""

    def __init__(self, tablet):
        self.tablet, self.sent, self.closed = tablet, [], None
        self.incoming = asyncio.Queue()

    async def send(self, data):
        msg = json.loads(data)
        self.sent.append(msg)
        answer = self.tablet(msg)
        for raw in (answer if answer is not None else [None]):
            self.incoming.put_nowait(raw)

    async def recv(self):
        return await self.incoming.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        raw = await self.incoming.get()
        if raw is None:
            raise StopAsyncIteration
        return raw

    async def close(self, code=1000, reason=""):
        self.closed = code
        self.incoming.put_nowait(None)


@unittest.skipUnless(HAVE_WEBSOCKETS, "the handler needs python-websockets (the venv)")
class Handler(unittest.TestCase):
    """The socket's handler and its HTTP answers, with no socket: the pairing, the keymap after it,
    the reports pressed, and the ways a connection ends."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.key = remote.key_bytes(remote.load_key(self.d, create=True))
        self.typing = FakeTyping()
        self.c = remote.Controller(self.d, self.typing, log=lambda *a: None)
        self.c.cache = {"keymap": {"kind": "keymap", "title": "t"}, "typing": {"kind": "typing", "state": "ok"}}

    def tablet(self, key=None, reports=((1, 0, [4]), (2, 0, [])), forged=False):
        state = {}

        def answer(msg):
            if msg["kind"] == "challenge":
                state["nonce"], cnonce = msg["nonce"], "34" * 16
                state["sk"] = remote.session_key(key or self.key, msg["nonce"], cnonce)
                return [json.dumps({"kind": "auth", "v": 1, "cnonce": cnonce,
                                    "mac": remote.mac(key or self.key, remote.auth_text(msg["nonce"], cnonce))})]
            if msg["kind"] == "typing":         # the last thing it is told: its reports now
                out = []
                for seq, mods, keys in reports:
                    mac = "0" * 64 if forged else remote.mac(state["sk"], remote.hid_text(seq, mods, keys))
                    out.append(json.dumps({"kind": "hid", "v": 1, "seq": seq, "mods": mods, "keys": keys, "mac": mac}))
                return out + [None]
            return []
        return answer

    def visit(self, tablet):
        ws = FakeSocket(tablet)
        asyncio.run(self.c._handler(ws))
        return ws

    def test_paired_it_is_told_the_keymap_and_its_reports_are_pressed(self):
        ws = self.visit(self.tablet())
        self.assertEqual(["challenge", "welcome", "keymap", "typing"], [m["kind"] for m in ws.sent])
        self.assertEqual([("hid", {"kind": "hid", "v": 1, "seq": 1, "mods": 0, "keys": [4]}),
                          ("hid", {"kind": "hid", "v": 1, "seq": 2, "mods": 0, "keys": []}), "release"],
                         self.typing.calls)
        self.assertEqual((None, 0, 0), (self.c.client, self.c.pending, self.c.failures))

    def test_a_wrong_key_or_a_forged_report_is_refused_and_counted(self):
        ws = self.visit(self.tablet(key=bytes(32)))
        self.assertEqual((4001, ["challenge"]), (ws.closed, [m["kind"] for m in ws.sent]))
        ws = self.visit(self.tablet(forged=True))
        self.assertEqual(4001, ws.closed)
        self.assertEqual(["release"], self.typing.calls)          # nothing pressed, and let go on the way out
        self.assertEqual((2, 0, None), (self.c.failures, self.c.pending, self.c.client))

    def test_a_tablet_too_slow_to_pair_is_let_go_uncounted(self):
        old = remote.AUTH_S
        remote.AUTH_S = 0.01
        try:
            ws = self.visit(lambda msg: [])
        finally:
            remote.AUTH_S = old
        self.assertEqual((4002, 0, 0), (ws.closed, self.c.failures, self.c.pending))

    def test_the_http_answers(self):
        pages = remote.Pages("192.168.1.5:8768")
        ok = {"Host": "192.168.1.5:8768", "Origin": "http://192.168.1.5:8768"}
        self.assertIsNone(self.c.respond(pages, "/ws", ok))
        r = self.c.respond(pages, "/hud.css", ok)
        self.assertEqual((200, "text/css; charset=utf-8", str(len(r.body))),
                         (r.status_code, r.headers["Content-Type"], r.headers["Content-Length"]))
        self.assertEqual(404, self.c.respond(pages, "/x", ok).status_code)
        self.c.pending = remote.MAX_PENDING
        self.assertEqual(503, self.c.respond(pages, "/ws", ok).status_code)


@unittest.skipUnless(HAVE_WEBSOCKETS, "the socket needs python-websockets (the venv)")
@unittest.skipUnless(can_listen(), "this process may not open a local port")
class Visit(unittest.TestCase):
    """A tablet's whole visit, on 127.0.0.1 (--bind): the page, the pairing, a report pressed, a
    forged one refused, and remote mode turned off again."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.key = remote.key_bytes(remote.load_key(self.d, create=True))
        remote.write_request(self.d, True, port=0, bind="127.0.0.1")
        self.typing = FakeTyping()
        self.c = remote.Controller(self.d, self.typing, log=lambda *a: None)
        self.thread = threading.Thread(target=lambda: asyncio.run(self.c.run(every=0.05)), daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.c.stop)
        deadline = time.monotonic() + 5
        while not remote.read_live(self.d).get("serving") and time.monotonic() < deadline:
            time.sleep(0.02)
        self.live = remote.read_live(self.d)
        self.assertTrue(self.live.get("serving"), self.live)
        self.host = self.live["url"][len("http://"):].rstrip("/")
        self.c.hub_said({"kind": "keymap", "title": "test"})
        self.c.hub_said({"kind": "key", "chars": "secret"})          # never the tablet's

    def get(self, path, host=None):
        import http.client
        addr, port = self.host.rsplit(":", 1)
        conn = http.client.HTTPConnection(addr, int(port), timeout=5)
        conn.request("GET", path, headers={"Host": host or self.host})
        r = conn.getresponse()
        return r.status, r.read()

    def test_a_tablets_visit(self):
        from websockets.sync.client import connect
        status, body = self.get("/")
        self.assertEqual(200, status)
        self.assertIn(b'class="remote"', body)
        self.assertEqual(421, self.get("/", host="evil.example:80")[0])
        with self.assertRaises(Exception):
            connect(f"ws://{self.host}/ws", origin="http://evil.example").close()
        with connect(f"ws://{self.host}/ws", origin=f"http://{self.host}") as ws:
            challenge = json.loads(ws.recv(timeout=5))
            self.assertEqual(remote.kid(self.key), challenge["kid"])
            cnonce = "12" * 16
            ws.send(json.dumps({"kind": "auth", "v": 1, "cnonce": cnonce,
                                "mac": remote.mac(self.key, remote.auth_text(challenge["nonce"], cnonce))}))
            welcome = json.loads(ws.recv(timeout=5))
            sk = remote.session_key(self.key, challenge["nonce"], cnonce)
            self.assertEqual(remote.mac(sk, remote.WELCOME_TEXT), welcome["mac"])
            self.assertEqual("keymap", json.loads(ws.recv(timeout=5))["kind"])
            ws.send(json.dumps({"kind": "hid", "v": 1, "seq": 1, "mods": 0, "keys": [4],
                                "mac": remote.mac(sk, remote.hid_text(1, 0, [4]))}))
            ws.send(json.dumps({"kind": "hid", "v": 1, "seq": 2, "mods": 0, "keys": [5], "mac": "0" * 64}))
            with self.assertRaises(Exception):
                while True:
                    ws.recv(timeout=5)
        deadline = time.monotonic() + 5
        while "release" not in self.typing.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIn(("hid", {"kind": "hid", "v": 1, "seq": 1, "mods": 0, "keys": [4]}), self.typing.calls)
        self.assertNotIn(5, [k for c in self.typing.calls if isinstance(c, tuple) for k in c[1]["keys"]])
        self.assertEqual(1, self.c.failures)
        remote.write_request(self.d, False)
        deadline = time.monotonic() + 5
        while remote.read_live(self.d).get("serving") and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(["start", "release", "stop"], [c for c in self.typing.calls if isinstance(c, str)][:1]
                         + [c for c in self.typing.calls if isinstance(c, str)][-2:])


if __name__ == "__main__":
    unittest.main()
