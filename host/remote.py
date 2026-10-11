"""Remote mode: a tablet's browser types into this computer over the local network (docs/touch.md).

`zmk-layer-hud remote on` asks the running HUD to serve its page on the network, at an address a
tablet on the same Wi-Fi can open: http://<this computer>:8768/#k=<the pairing key>. The tablet's
browser draws the board docked, and what its keys do is pressed here as if the panel's own page had
typed it (host/inject.py), on a backend of its own -- the macOS panel's CoreGraphics events, or on
Linux a second virtual keyboard the feed makes.

What keeps anyone else on the network from typing here:
  - The pairing key, 256 random bits, travels only in the link's fragment (#k=), which a browser
    never sends: the tablet's page reads it, keeps it, and takes it out of its address bar. It is
    kept in $STATE/remote.key until `remote unpair`.
  - The socket proves the key without sending it: the server's nonce and the tablet's, MAC'd with
    it (HMAC-SHA256), then a key for that connection alone, which MACs every report the tablet
    sends with a sequence number that only goes up. A report out of turn or with a wrong MAC ends
    the connection; MAX_FAILURES of those, or of wrong pairings, end remote mode until it is asked
    for again.
  - The page is served only to a Host header naming this address exactly (no DNS rebinding), the
    socket only to that page's own Origin, and both only from a short list of files with headers
    that let the page run nothing but its own scripts (CSP) and never be framed.
  - The address is this computer's on its default route, and only a private, link-local or
    100.64/10 (Tailscale) one -- never every interface -- unless --bind names one.
  - It stays open MINUTES after it is asked for, longer while a tablet is connected, and the tablet
    is told nothing of what the physical keyboard types: the keymap, the secure-input state and
    whether this computer can type, and that is all.

What plain HTTP leaves open, and docs/security-review.md says so: someone who can read the Wi-Fi
sees which keys the tablet presses, and someone who can change what the tablet loads while it loads
the page can take the key. Tailscale (--bind with its address) closes both.

The CLI and the server never talk directly: the CLI writes what it wants (remote.json) and the
server, polling it every second, writes what it is doing (remote.live.json). So `remote on` with
the HUD restarting in between still ends up served.

Pure where it can be -- Session, Pages, choose_address, Controller.decide -- so the protocol is
tested without a socket; the websockets glue is the rest.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import http
import ipaddress
import json
import os
import re
import secrets
import socket
import time

PORT = 8768
MINUTES = 30
PROTOCOL = "zmkhud-remote/1"
KEY_FILE, REQUEST_FILE, LIVE_FILE = "remote.key", "remote.json", "remote.live.json"
MAX_FAILURES = 5
AUTH_S = 10.0              # from connecting to proving the key
MAX_FRAMES = 60            # reports a second from the tablet; more are dropped
MAX_PENDING = 4            # connections not yet paired, at once
MAX_MESSAGE = 4096         # bytes in one message from the tablet
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
HUD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hud")
# What the tablet hears besides the replies to its pairing: the keymap, whether a secret is being
# typed here (the strip hides it), and whether this computer can type (inject.Typing).
FORWARDED = ("keymap", "secure", "typing")


class Refused(Exception):
    """A message that ends the connection. `failure`: one that counts toward MAX_FAILURES -- a
    wrong MAC, which only a party without the key sends. What was offered is never logged."""

    def __init__(self, why, failure=False):
        super().__init__(why)
        self.failure = failure


# ---------- the key ----------

def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def key_bytes(text):
    """The key as written in the link and the key file: 43 base64url characters, 32 bytes."""
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", text):
        raise ValueError("not a pairing key")
    return base64.urlsafe_b64decode(text + "=")


def kid(key):
    """Which key the server holds, for a tablet to see it was paired with another one before it
    tries (a wrong try counts). A hash of 256 random bits says nothing of them."""
    return hashlib.sha256(key).hexdigest()[:16]


def mac(key, text):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def auth_text(nonce, cnonce):
    return f"{PROTOCOL} auth {nonce} {cnonce}"


def session_key(key, nonce, cnonce):
    return hmac.new(key, f"{PROTOCOL} session {nonce} {cnonce}".encode("utf-8"), hashlib.sha256).digest()


WELCOME_TEXT = f"{PROTOCOL} welcome"


def hid_text(seq, mods, keys, consumer=()):
    return f"{PROTOCOL} hid {seq} {mods} {','.join(str(k) for k in keys)} {','.join(str(c) for c in consumer)}"


def _hex(value, n):
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{n}}}", value) is not None


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


class Session:
    """One connection, the server's side: the challenge it opens with, and what it makes of each
    message after -- the pairing first, then the tablet's reports. `take` returns ("welcome", reply)
    once the key is proved, ("hid", report) for a report to press, ("drop", None) for one to let
    pass; anything else raises Refused."""

    def __init__(self, key, nonce=None, clock=time.monotonic, max_frames=MAX_FRAMES):
        self.key, self.clock, self.max_frames = key, clock, max_frames
        self.nonce = nonce or secrets.token_hex(16)
        self.skey = None
        self.seq = 0
        self.times = []

    @property
    def paired(self):
        return self.skey is not None

    def challenge(self):
        return {"kind": "challenge", "v": 1, "kid": kid(self.key), "nonce": self.nonce}

    def take(self, raw):
        if isinstance(raw, bytes) or len(raw) > MAX_MESSAGE:
            raise Refused("not a message")
        try:
            msg = json.loads(raw)
        except ValueError:
            raise Refused("not JSON") from None
        if not isinstance(msg, dict) or msg.get("v") != 1:
            raise Refused("not a v1 message")
        if not self.paired:
            return self._pair(msg)
        if msg.get("kind") != "hid":
            return "drop", None
        return self._report(msg)

    def _pair(self, msg):
        if msg.get("kind") != "auth" or not _hex(msg.get("cnonce"), 32) or not _hex(msg.get("mac"), 64):
            raise Refused("not a pairing")
        cnonce = msg["cnonce"]
        if not hmac.compare_digest(mac(self.key, auth_text(self.nonce, cnonce)), msg["mac"]):
            raise Refused("wrong key", failure=True)
        self.skey = session_key(self.key, self.nonce, cnonce)
        return "welcome", {"kind": "welcome", "v": 1, "mac": mac(self.skey, WELCOME_TEXT)}

    def _report(self, msg):
        seq, mods, keys, consumer = msg.get("seq"), msg.get("mods", 0), msg.get("keys", []), msg.get("consumer", [])
        if not (_int(seq) and _int(mods) and isinstance(keys, list) and isinstance(consumer, list)
                and all(_int(k) for k in keys) and all(_int(c) for c in consumer) and _hex(msg.get("mac"), 64)):
            raise Refused("not a report")
        if not hmac.compare_digest(mac(self.skey, hid_text(seq, mods, keys, consumer)), msg["mac"]):
            raise Refused("wrong MAC", failure=True)
        if seq <= self.seq:
            raise Refused("a report out of turn", failure=True)
        self.seq = seq
        now = self.clock()
        self.times = [t for t in self.times if now - t < 1.0]
        if len(self.times) >= self.max_frames:
            return "drop", None
        self.times.append(now)
        out = {"kind": "hid", "v": 1, "seq": seq, "mods": mods, "keys": keys}
        if consumer:
            out["consumer"] = consumer
        return "hid", out


# ---------- the page ----------

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self' ws://{host}; manifest-src 'self'; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".html": "text/html; charset=utf-8", ".webmanifest": "application/manifest+json"}
MANIFEST = {"name": "ZMK Layer HUD", "short_name": "ZMK HUD", "start_url": "/", "display": "standalone",
            "background_color": "#1a1b26", "theme_color": "#1a1b26"}
# Where remote.html differs from index.html: marked as the remote page, installable, and with the
# script that pairs it and types through the socket.
EDITS = (('<html lang="en">', '<html lang="en" class="remote">'),
         ('<link rel="stylesheet" href="hud.css">',
          '<link rel="stylesheet" href="hud.css">\n<link rel="manifest" href="manifest.webmanifest">\n'
          '<meta name="mobile-web-app-capable" content="yes">\n<meta name="apple-mobile-web-app-capable" content="yes">\n'
          '<meta name="theme-color" content="#1a1b26">'),
         ('<script src="touch.js"></script>', '<script src="touch.js"></script>\n<script src="remote.js"></script>'))


def remote_html(index):
    """index.html as the tablet gets it. Each edit's anchor must be there: a page that changed
    shape fails here, not on a tablet."""
    for old, new in EDITS:
        if index.count(old) != 1:
            raise ValueError(f"hud/index.html no longer has {old!r} once")
        index = index.replace(old, new)
    return index


def page_files(index):
    """The files remote.html loads, as index.html names them, and remote.js: the whole list a
    tablet may fetch."""
    names = re.findall(r'<(?:script src|link rel="stylesheet" href)="([\w.-]+)"', index)
    return [n for n in names if n.endswith((".js", ".css"))] + ["remote.js"]


class Pages:
    """The HTTP side, as a function of a request: (status, headers, body) for a file, or "ws" for
    a request that may become the socket. `host` is the Host header the page is served to: this
    address and port, exactly."""

    def __init__(self, host, hud_dir=HUD_DIR):
        self.host, self.hud_dir = host, hud_dir
        with open(os.path.join(hud_dir, "index.html"), encoding="utf-8") as f:
            index = f.read()
        self.files = {"/": ("remote.html", remote_html(index).encode("utf-8")),
                      "/manifest.webmanifest": ("manifest.webmanifest", json.dumps(MANIFEST).encode("utf-8"))}
        for name in page_files(index):
            self.files["/" + name] = (name, None)

    def headers(self, kind):
        return [("Content-Type", kind), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"),
                ("Content-Security-Policy", CSP.format(host=self.host))]

    def plain(self, status, text):
        return status, self.headers("text/plain; charset=utf-8"), (text + "\n").encode("utf-8")

    def route(self, path, headers):
        if headers.get("Host") != self.host:
            return self.plain(http.HTTPStatus.MISDIRECTED_REQUEST, "not this address")
        path = path.split("?", 1)[0]
        if path == "/ws":
            if headers.get("Origin") != f"http://{self.host}":
                return self.plain(http.HTTPStatus.FORBIDDEN, "not this page")
            return "ws"
        if path not in self.files:
            return self.plain(http.HTTPStatus.NOT_FOUND, "not here")
        name, body = self.files[path]
        if body is None:
            try:
                with open(os.path.join(self.hud_dir, name), "rb") as f:
                    body = f.read()
            except OSError:
                return self.plain(http.HTTPStatus.NOT_FOUND, "not here")
        return http.HTTPStatus.OK, self.headers(TYPES[os.path.splitext(name)[1]]), body


# ---------- the address ----------

class NoAddress(Exception):
    """No address a tablet could be sent to: the sentence says why and what to do."""


def route_address():
    """This computer's address on its default route: the one a datagram socket is given when it
    connects toward a documentation address (192.0.2.1). Nothing is sent."""
    with contextlib.suppress(OSError):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    return None


def url_host(addr, port):
    return f"[{addr}]:{port}" if ":" in addr else f"{addr}:{port}"


def choose_address(bind, route):
    """(the address to listen on, the one in the link). A --bind is taken as given; 0.0.0.0 or ::
    listens everywhere and still names the default route's address. Without one, the default
    route's, when it is one only a local network reaches."""
    if bind:
        try:
            ip = ipaddress.ip_address(bind)
        except ValueError:
            raise NoAddress(f"--bind {bind}: not an IP address") from None
        if ip.is_unspecified:
            if not route:
                raise NoAddress("no network to name in the link: connect to the Wi-Fi first")
            return str(ip), route
        return str(ip), str(ip)
    if not route:
        raise NoAddress("no network: connect this computer to the Wi-Fi the tablet is on")
    ip = ipaddress.ip_address(route)
    if ip.is_loopback:
        raise NoAddress("this computer has no network but its own: connect it to the Wi-Fi the tablet is on")
    if not (ip.is_private or ip.is_link_local or ip in TAILSCALE):
        raise NoAddress(f"this computer's address ({route}) is a public one, and anyone could reach it: "
                        "--bind ADDR serves on one you choose (a Tailscale address, say)")
    return route, route


# ---------- what the CLI and the server say to each other ----------

def _path(d, name):
    return os.path.join(d, name)


def _write(path, obj):
    import panelstate
    panelstate._write_private(path, json.dumps(obj) + "\n")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def load_key(d, create=False):
    """The pairing key, or None; made the first time it is asked for with create."""
    try:
        with open(_path(d, KEY_FILE), encoding="utf-8") as f:
            text = f.read().strip()
        key_bytes(text)
        return text
    except (OSError, ValueError):
        if not create:
            return None
    import panelstate
    text = new_key()
    panelstate._write_private(_path(d, KEY_FILE), text + "\n")
    return text


def unpair(d):
    """The key gone: every tablet paired with it can no longer connect. True when there was one."""
    try:
        os.remove(_path(d, KEY_FILE))
        return True
    except FileNotFoundError:
        return False


def write_request(d, on, minutes=MINUTES, port=PORT, bind="", now=None):
    req = {"id": secrets.token_hex(4), "on": bool(on), "minutes": int(minutes), "port": int(port),
           "bind": bind or "", "until": (now if now is not None else time.time()) + int(minutes) * 60}
    _write(_path(d, REQUEST_FILE), req)
    return req


def read_request(d):
    return _read(_path(d, REQUEST_FILE))


def read_live(d):
    return _read(_path(d, LIVE_FILE))


def live_now(d):
    """What the process serving remote mode last said, while it is alive: one that crashed leaves
    its file behind."""
    live = read_live(d)
    pid = live.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return {}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {}
    except PermissionError:
        pass
    return live


# ---------- the switch ----------

class Controller:
    """Remote mode in a running HUD: reads what the CLI asked for every second, serves while it
    says on and its time is not up, and writes what it is doing. `typing` is an inject.Typing of
    remote mode's own (its tell is this controller's), or something with its start/stop/hid/release
    that hands them to the thread it lives on. `hub_said` and `tell` may be called from any thread:
    what the feed sends (the keymap, secure) and what typing says."""

    def __init__(self, state_dir, typing, log=print, clock=time.time, route=route_address, hud_dir=HUD_DIR, loop=None):
        self.d, self.typing, self.log, self.clock, self.route, self.hud_dir = (
            state_dir, typing, log, clock, route, hud_dir)
        self.req_id = None
        self.req = {}
        self.until = 0.0
        self.failures = 0
        self.why = ""               # why it is not serving, when it was asked to
        self.serving = None         # (addr, port, host) while it is
        self.client = None
        self.pending = 0
        self.cache = {}             # FORWARDED kind -> the latest
        # The loop run() will run on, when it is known before: what is said before it runs waits
        # there rather than being lost (the keymap comes first).
        self.loop = loop
        self._stop = None
        self._server_stop = None
        self._server_task = None
        self._live = None

    # -- from other threads --

    def hub_said(self, msg):
        if isinstance(msg, dict) and msg.get("kind") in ("keymap", "secure"):
            self._soon(self._forward, msg)

    def tell(self, msg):
        if isinstance(msg, dict) and msg.get("kind") == "typing":
            self._soon(self._forward, msg)

    def stop(self):
        self._soon(lambda: self._stop and self._stop.set())

    def _soon(self, fn, *args):
        if self.loop is not None:
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(fn, *args)

    # -- what to do --

    def decide(self, now):
        """"start", "stop" or None, from the request, the time and the failures. Pure but for
        reading the request file."""
        req = read_request(self.d)
        if req.get("id") != self.req_id:
            # Asked again: a new time, and the failures and the reason forgotten.
            self.req_id, self.req = req.get("id"), req
            self.until = float(req.get("until") or 0) if req.get("on") else 0.0
            self.failures, self.why = 0, ""
        if self.client is not None and self.req.get("on"):
            # A tablet connected keeps it open: its time starts again.
            self.until = max(self.until, now + int(self.req.get("minutes") or MINUTES) * 60)
        want = bool(self.req.get("on")) and now < self.until and self.failures < MAX_FAILURES
        if want and load_key(self.d) is None:
            want, self.why = False, "no pairing key: `zmk-layer-hud remote on` makes one"
        if want and self.serving is None:
            return "start"
        if not want and self.serving is not None:
            if self.req.get("on") and now >= self.until and not self.why:
                self.why = "its time was up"
            return "stop"
        return None

    def failed(self):
        self.failures += 1
        self.log(f"remote: a tablet failed to pair or to prove a report ({self.failures} of {MAX_FAILURES})")
        if self.failures >= MAX_FAILURES:
            self.why = (f"{MAX_FAILURES} failed attempts: `zmk-layer-hud remote on` opens it again, "
                        "`remote unpair` makes a new code")
            self.log("remote: off, " + self.why)

    def live(self):
        out = {"pid": os.getpid(), "id": self.req_id, "serving": self.serving is not None,
               "client": self.client is not None, "until": self.until if self.serving else None,
               "typing": (self.cache.get("typing") or {}).get("state"), "why": self.why}
        if self.serving:
            out["url"] = f"http://{self.serving[2]}/"
        return out

    def write_live(self):
        live = self.live()
        if live != self._live:
            self._live = live
            with contextlib.suppress(OSError):
                _write(_path(self.d, LIVE_FILE), live)

    # -- the loop --

    async def run(self, every=1.0):
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        try:
            while not self._stop.is_set():
                what = self.decide(self.clock())
                if what == "start":
                    await self._start()
                elif what == "stop":
                    await self._halt()
                self.write_live()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), every)
        finally:
            await self._halt()
            with contextlib.suppress(OSError):
                os.remove(_path(self.d, LIVE_FILE))

    async def _start(self):
        try:
            import websockets  # noqa: F401
        except ImportError:
            self.why = "python-websockets is missing (make venv)"
            self.req = dict(self.req, on=False)
            return
        port = PORT if self.req.get("port") is None else int(self.req["port"])
        try:
            addr, named = choose_address(self.req.get("bind") or "", self.route())
            pages = Pages(url_host(named, port), self.hud_dir)
        except (NoAddress, ValueError, OSError) as e:
            self.why = str(e)
            self.req = dict(self.req, on=False)
            self.log(f"remote: {e}")
            return
        started = self.loop.create_future()
        self._server_stop = asyncio.Event()
        self._server_task = asyncio.ensure_future(self._serve(addr, named, port, pages, started))
        port = await started
        if port:
            self.serving = (addr, port, pages.host)
            self.why = ""
            self.typing.start()
            self.log(f"remote: serving http://{pages.host}/ to a paired tablet")
        else:
            self.req = dict(self.req, on=False)     # asked again with `remote on`, not every second

    async def _halt(self):
        if self._server_task is None:
            return
        self._server_stop.set()
        with contextlib.suppress(Exception):
            await self._server_task
        self._server_task = None
        if self.serving is not None:
            self.serving = None
            self.typing.release()
            self.typing.stop()
            self.log("remote: off" + (f", {self.why}" if self.why else ""))

    def respond(self, pages, path, headers):
        """websockets' process_request: None lets the socket open, anything else is the answer."""
        from websockets.datastructures import Headers
        from websockets.http11 import Response
        what = pages.route(path, headers)
        if what == "ws":
            if self.pending < MAX_PENDING:
                return None
            what = pages.plain(http.HTTPStatus.SERVICE_UNAVAILABLE, "busy")
        status, headers, body = what
        status = http.HTTPStatus(status)
        return Response(status.value, status.phrase, Headers(headers + [("Content-Length", str(len(body)))]), body)

    async def _serve(self, addr, named, port, pages, started):
        import websockets

        def process_request(connection, request):
            return self.respond(pages, request.path, request.headers)

        try:
            async with websockets.serve(self._handler, addr, port, process_request=process_request,
                                        server_header=None, compression=None, max_size=MAX_MESSAGE) as server:
                port = server.sockets[0].getsockname()[1]      # the one taken, when asked for any (0)
                pages.host = url_host(named, port)
                started.set_result(port)
                await self._server_stop.wait()
                if self.client is not None:
                    with contextlib.suppress(Exception):
                        await self.client.close(4003, "remote mode is off")
        except OSError as e:
            self.why = f"cannot serve on {url_host(addr, port)}: {e.strerror or e}"
            self.log(f"remote: {self.why}")
        finally:
            if not started.done():
                started.set_result(None)

    async def _handler(self, ws):
        import websockets
        key = load_key(self.d)
        if key is None:
            await ws.close(4003, "not paired")
            return
        session = Session(key_bytes(key))
        self.pending += 1
        counted = True
        try:
            await ws.send(json.dumps(session.challenge()))
            what, reply = session.take(await asyncio.wait_for(ws.recv(), AUTH_S))
            self.pending -= 1
            counted = False
            await ws.send(json.dumps(reply))
            if self.client is not None:
                # Another tablet, or this one again after the Wi-Fi dropped it: the newest wins.
                old, self.client = self.client, None
                self.typing.release()
                with contextlib.suppress(Exception):
                    await old.close(4000, "another tablet connected")
            self.client = ws
            self.write_live()
            for kind in FORWARDED:
                if kind in self.cache:
                    await ws.send(json.dumps(self.cache[kind], ensure_ascii=False))
            async for raw in ws:
                what, report = session.take(raw)
                if what == "hid":
                    self.typing.hid(report)
        except Refused as e:
            if e.failure:
                self.failed()
            with contextlib.suppress(Exception):
                await ws.close(4001, "refused")
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                await ws.close(4002, "too slow to pair")
        except websockets.ConnectionClosed:
            pass
        finally:
            if counted:
                self.pending -= 1
            if self.client is ws:
                self.client = None
                self.typing.release()
                self.write_live()

    def _forward(self, msg):
        self.cache[msg["kind"]] = msg
        if self.client is not None:
            data = json.dumps(msg, ensure_ascii=False)
            asyncio.ensure_future(self._send(self.client, data))
        if msg["kind"] == "typing":
            self.write_live()

    async def _send(self, ws, data):
        with contextlib.suppress(Exception):
            await ws.send(data)

