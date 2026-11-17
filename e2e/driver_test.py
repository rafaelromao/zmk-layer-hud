"""The pure parts of e2e/driver.py: what it reads of the HUD, the waiting, the socket by hand, the
reading of a platform's surface listings -- and the workflow that runs it, which must gate as it says.

The run itself needs a display, a compositor or a panel, and happens in CI (.github/workflows/e2e.yml);
nothing here starts one. Run by `make test-host`, so every platform's ordinary CI runs it."""

import json
import os
import socket
import struct
import threading
import unittest
from pathlib import Path
from unittest import mock

import driver

ROOT = Path(__file__).resolve().parent.parent


class WaitFor(unittest.TestCase):
    def clock(self):
        t = [0.0]

        def now():
            return t[0]

        def sleep(s):
            t[0] += s
        return now, sleep

    def test_returns_the_detail_of_the_first_poll_that_is_done(self):
        now, sleep = self.clock()
        polls = iter([(False, "not yet"), (False, "still not"), (True, "there"), (True, "never reached")])
        self.assertEqual(driver.wait_for(lambda: next(polls), "x", 10, interval=1, clock=now, sleep=sleep), "there")

    def test_a_timeout_says_what_it_waited_for_and_the_last_thing_seen(self):
        now, sleep = self.clock()
        with self.assertRaises(driver.DriverError) as cm:
            driver.wait_for(lambda: (False, "the pid is 7"), "a panel", 3, interval=1, clock=now, sleep=sleep)
        self.assertIn("a panel", str(cm.exception))
        self.assertIn("the pid is 7", str(cm.exception))
        self.assertIn("3 s", str(cm.exception))

    def test_polls_at_least_once_even_with_no_time(self):
        now, sleep = self.clock()
        self.assertEqual(driver.wait_for(lambda: (True, "ok"), "x", 0, clock=now, sleep=sleep), "ok")


class Status(unittest.TestCase):
    UP = {"running": True, "said": True, "ours": True, "pid": 4242, "where": "/x", "shown": True, "feed": "running",
          "stats": [["WPM", "—"], ["Avg", "—"]], "wpm": 0}

    def test_parse_status(self):
        self.assertEqual(driver.parse_status(json.dumps(self.UP) + "\n"), self.UP)

    def test_parse_status_takes_the_last_line_when_something_printed_before_it(self):
        self.assertEqual(driver.parse_status("a warning\n" + json.dumps(self.UP) + "\n"), self.UP)

    def test_parse_status_refuses_what_is_not_an_object(self):
        for text in ("", "not json", "[1, 2]", "42"):
            with self.assertRaises(driver.DriverError, msg=text):
                driver.parse_status(text)

    def test_a_panel_behind_status(self):
        self.assertEqual(driver.panel_up(self.UP, "running")[0], True)

    def test_not_up_names_what_is_missing(self):
        for key, value in (("running", False), ("said", False), ("ours", False), ("pid", None)):
            ok, why = driver.panel_up(dict(self.UP, **{key: value}), "running")
            self.assertFalse(ok, key)
            self.assertTrue(why, key)

    def test_the_feed_is_what_the_platform_calls_it(self):
        self.assertFalse(driver.panel_up(self.UP, "in the panel")[0])
        self.assertTrue(driver.panel_up(dict(self.UP, feed="in the panel"), "in the panel")[0])
        self.assertFalse(driver.panel_up(dict(self.UP, feed="stopped"), "running")[0])

    def test_the_page_has_said_once_the_stats_column_is_there(self):
        self.assertTrue(driver.page_said(self.UP)[0])
        self.assertFalse(driver.page_said(dict(self.UP, stats=[]))[0])
        self.assertFalse(driver.page_said({"running": True})[0])


class Surfaces(unittest.TestCase):
    LAYERS = {"Virtual-2": {"levels": {"0": [], "1": [], "2": [
        {"address": "0x1", "x": 400, "y": 8, "w": 598, "h": 424, "namespace": "zmkhud-layer", "pid": 9},
        {"address": "0x2", "x": 400, "y": 440, "w": 598, "h": 96, "namespace": "zmkhud-keys", "pid": 9},
        {"address": "0x3", "x": 0, "y": 0, "w": 10, "h": 10, "namespace": "waybar", "pid": 3}], "3": []}}}

    def test_both_surfaces_found_among_other_layers(self):
        ok, detail = driver.hypr_present(self.LAYERS)
        self.assertTrue(ok, detail)
        self.assertIn("598x424", detail)
        self.assertNotIn("waybar", detail)

    def test_one_missing_is_named(self):
        layers = json.loads(json.dumps(self.LAYERS))
        layers["Virtual-2"]["levels"]["2"].pop(1)
        ok, detail = driver.hypr_present(layers)
        self.assertFalse(ok)
        self.assertIn("zmkhud-keys", detail)
        self.assertNotIn("zmkhud-layer", detail)

    def test_surfaces_on_any_monitor_count(self):
        two = {"A": {"levels": {"2": [self.LAYERS["Virtual-2"]["levels"]["2"][0]]}},
               "B": {"levels": {"2": [self.LAYERS["Virtual-2"]["levels"]["2"][1]]}}}
        self.assertTrue(driver.hypr_present(two)[0])

    def test_nothing_that_is_not_a_listing(self):
        for doc in (None, [], "x", {"M": None}, {"M": {"levels": None}}, {"M": {"levels": {"0": "x"}}}):
            self.assertEqual(driver.layer_surfaces(doc), {})

    def info(self, pid, number, alpha=1.0, onscreen=True, w=598, h=480, layer=25):
        return {"kCGWindowOwnerPID": pid, "kCGWindowNumber": number, "kCGWindowAlpha": alpha, "kCGWindowIsOnscreen": onscreen,
                "kCGWindowLayer": layer, "kCGWindowBounds": {"X": 10, "Y": 20, "Width": w, "Height": h}}

    def test_windows_of_one_pid(self):
        infos = [self.info(1, 10), self.info(2, 11), self.info(2, 12, w=1, h=1)]
        self.assertEqual([w["number"] for w in driver.windows_of(2, infos)], [11, 12])
        self.assertEqual(driver.windows_of(3, infos), [])

    def test_the_panel_window_is_the_biggest(self):
        ws = driver.windows_of(2, [self.info(2, 11, w=1, h=1), self.info(2, 12)])
        self.assertEqual(driver.panel_window(ws)["number"], 12)
        self.assertIsNone(driver.panel_window([]))

    def test_shown_is_on_screen_opaque_and_a_real_size(self):
        self.assertTrue(driver.mac_shown(driver.windows_of(2, [self.info(2, 1)]))[0])
        for bad in (dict(alpha=0.0), dict(onscreen=False), dict(w=20, h=20)):
            self.assertFalse(driver.mac_shown(driver.windows_of(2, [self.info(2, 1, **bad)]))[0], bad)
        self.assertFalse(driver.mac_shown([])[0])

    def test_hidden_is_alpha_zero(self):
        self.assertTrue(driver.mac_hidden(driver.windows_of(2, [self.info(2, 1, alpha=0.0)]))[0])
        self.assertFalse(driver.mac_hidden(driver.windows_of(2, [self.info(2, 1)]))[0])
        self.assertFalse(driver.mac_hidden([])[0])


class Config(unittest.TestCase):
    def test_ble_is_turned_off_at_the_end(self):
        out = driver.config_with_ble_off("title: x\n")
        self.assertTrue(out.startswith("title: x\n"))
        self.assertTrue(out.endswith("ble:\n  enabled: false\n"))

    def test_a_config_with_a_ble_key_is_refused(self):
        with self.assertRaises(driver.DriverError):
            driver.config_with_ble_off("title: x\nble:\n  address: aa:bb\n")

    def test_the_sample_it_is_given_has_none_and_parses_with_it_off(self):
        text = (ROOT / "config" / "example-3x5.yaml").read_text(encoding="utf-8")
        out = driver.config_with_ble_off(text)
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not here")
        cfg = yaml.safe_load(out)
        self.assertEqual(cfg["ble"], {"enabled": False})
        self.assertEqual({k: v for k, v in cfg.items() if k != "ble"}, yaml.safe_load(text))


class NiriReading(unittest.TestCase):
    LAYERS = [{"namespace": "zmkhud-layer", "output": "Virtual-1", "layer": "Top", "keyboard_interactivity": "None"},
              {"namespace": "zmkhud-keys", "output": "Virtual-1", "layer": "Top", "keyboard_interactivity": "None"},
              {"namespace": "waybar", "output": "Virtual-1", "layer": "Top", "keyboard_interactivity": "None"}]

    def test_both_surfaces_found_among_others(self):
        ok, detail = driver.niri_present(self.LAYERS)
        self.assertTrue(ok, detail)
        self.assertIn("zmkhud-layer on Virtual-1 (Top)", detail)
        self.assertNotIn("waybar", detail)

    def test_one_missing_is_named(self):
        ok, detail = driver.niri_present(self.LAYERS[:1] + self.LAYERS[2:])
        self.assertFalse(ok)
        self.assertIn("zmkhud-keys", detail)

    def test_nothing_that_is_not_a_listing(self):
        for doc in (None, {}, "x", [None, 3, {"namespace": None}]):
            self.assertEqual(driver.niri_layer_surfaces(doc), {})

    SAMPLE = (
        "// Written by zmk-layer-hud on every start; edits here are lost.\n"
        "// `include optional=true \"zmk-layer-hud.kdl\"` at the end of config.kdl reads it.\n"
        "\n"
        "binds {\n"
        "    Ctrl+Alt+L repeat=false { spawn \"env\" \"ZMKHUD_STATE=/s\" \"/c\" \"toggle\"; } // ZMK HUD show/hide\n"
        "    Ctrl+Alt+Super+L repeat=false { spawn \"uwsm-app\" \"--\" \"env\" \"/c\" \"power\"; } // ZMK HUD start/stop\n"
        "}\n")

    def test_binds_of_a_kdl_file(self):
        binds = driver.kdl_binds(self.SAMPLE)
        self.assertEqual([b["key"] for b in binds], ["Ctrl+Alt+L", "Ctrl+Alt+Super+L"])
        self.assertTrue(all(b["props"] == {"repeat": "false"} for b in binds))

    def test_a_bind_that_repeats_is_seen_as_one(self):
        binds = driver.kdl_binds('binds {\n    Ctrl+Alt+L { spawn "x"; }\n    Mod+T repeat=true { spawn "y"; }\n}\n')
        self.assertEqual([b["props"] for b in binds], [{}, {"repeat": "true"}])

    def test_only_the_binds_block_and_no_comments(self):
        text = 'input { keyboard { xkb { layout "us" } } }\nbinds {\n    // Mod+X repeat=false { spawn "z"; }\n}\nlayout { gaps 8 }\n'
        self.assertEqual(driver.kdl_binds(text), [])

    def test_what_shortcuts_writes_is_what_is_read(self):
        sys_path = os.path.join(str(ROOT), "host")
        import sys
        sys.path.insert(0, sys_path)
        try:
            import shortcuts
        except ImportError:
            self.skipTest("no host/shortcuts.py")
        finally:
            sys.path.remove(sys_path)
        if not hasattr(shortcuts, "niri_conf"):
            self.skipTest("this host/shortcuts.py has no niri support")
        text = shortcuts.niri_conf({"toggle": (("ctrl", "alt"), "l"), "power": (("ctrl", "alt", "gui"), "l")}, "/c", "/s", "uwsm-app --")
        binds = driver.kdl_binds(text)
        self.assertEqual([b["key"] for b in binds], ["Ctrl+Alt+L", "Ctrl+Alt+Super+L"])
        self.assertTrue(all(b["props"].get("repeat") == "false" for b in binds))
        self.assertEqual(driver.NIRI_INCLUDE, shortcuts.NIRI_INCLUDE)

    def test_the_include_has_to_be_last(self):
        inc = driver.NIRI_INCLUDE
        self.assertTrue(driver.include_is_last("a {}\n\n// mark\n" + inc + "\n", inc))
        self.assertTrue(driver.include_is_last(inc + "\n// trailing comment\n\n", inc))
        self.assertFalse(driver.include_is_last(inc + "\nlayout { gaps 8 }\n", inc))
        self.assertFalse(driver.include_is_last("", inc))

    def test_config_loaded_events(self):
        self.assertIs(driver.config_loaded('{"ConfigLoaded":{"failed":false}}'), False)
        self.assertIs(driver.config_loaded('{"ConfigLoaded":{"failed":true}}'), True)
        for line in ('{"WorkspacesChanged":{"workspaces":[]}}', "not json", "[1]", '{"ConfigLoaded":{"failed":"no"}}', ""):
            self.assertIsNone(driver.config_loaded(line), line)

    def test_lines_as_they_arrive(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        lines = driver.Lines(r)
        os.write(w, b'one\ntw')
        self.assertEqual(lines.readline(1), "one")
        self.assertIsNone(lines.readline(0.05))          # half a line is not a line
        os.write(w, b"o\nthree\n")
        self.assertEqual(lines.readline(1), "two")
        self.assertEqual(lines.readline(1), "three")
        os.close(w)
        self.assertIsNone(lines.readline(1))            # the end of the stream


def frame(payload, opcode=1, fin=True):
    """A server's WebSocket frame."""
    first = (0x80 if fin else 0) | opcode
    n = len(payload)
    if n < 126:
        head = struct.pack("BB", first, n)
    elif n < 1 << 16:
        head = struct.pack(">BBH", first, 126, n)
    else:
        head = struct.pack(">BBQ", first, 127, n)
    return head + payload


class WebSocketByHand(unittest.TestCase):
    def test_the_accept_key_is_rfc_6455s_example(self):
        self.assertEqual(driver.ws_accept("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_the_request(self):
        req = driver.ws_request("/tok", "127.0.0.1:8766", "KEY", origin="https://x.invalid").decode()
        self.assertTrue(req.startswith("GET /tok HTTP/1.1\r\n"))
        for line in ("Host: 127.0.0.1:8766", "Upgrade: websocket", "Connection: Upgrade", "Sec-WebSocket-Key: KEY",
                     "Sec-WebSocket-Version: 13", "Origin: https://x.invalid"):
            self.assertIn(line + "\r\n", req)
        self.assertTrue(req.endswith("\r\n\r\n"))
        self.assertNotIn("Origin", driver.ws_request("/tok", "h", "K").decode())

    def test_response_head(self):
        self.assertIsNone(driver.parse_response_head(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"))
        status, headers, rest = driver.parse_response_head(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: abc\r\n\r\n\x81\x00")
        self.assertEqual((status, headers["sec-websocket-accept"], rest), (101, "abc", b"\x81\x00"))
        self.assertEqual(driver.parse_response_head(b"HTTP/1.1 403 Forbidden\r\n\r\nno")[0], 403)
        with self.assertRaises(driver.DriverError):
            driver.parse_response_head(b"SSH-2.0-OpenSSH\r\n\r\n")

    def test_frames_of_each_length_class(self):
        for n in (0, 5, 125, 126, 300, 65535, 65536, 70000):
            payload = b"x" * n
            fin, opcode, got, rest = driver.parse_frame(frame(payload) + b"tail")
            self.assertEqual((fin, opcode, got, rest), (True, 1, payload, b"tail"), n)

    def test_an_incomplete_frame_waits(self):
        whole = frame(b"y" * 300)
        for cut in (0, 1, 2, 3, 4, 100, len(whole) - 1):
            self.assertIsNone(driver.parse_frame(whole[:cut]), cut)

    def test_a_masked_server_frame_is_wrong(self):
        with self.assertRaises(driver.DriverError):
            driver.parse_frame(b"\x81\x81abcdx")

    def test_messages_across_reads_fragments_and_control_frames(self):
        m = driver.Messages()
        data = (frame(b'{"a": 1}') + frame(b"ping", opcode=9) + frame(b'{"b":', fin=False) + frame(b' 2}', opcode=0))
        out = []
        for i in range(0, len(data), 3):            # three bytes at a time
            out += m.feed(data[i:i + 3])
        self.assertEqual(out, ['{"a": 1}', '{"b": 2}'])
        self.assertFalse(m.closed)
        m.feed(frame(b"", opcode=8))
        self.assertTrue(m.closed)

    def test_a_client_frame_is_masked_and_a_server_can_read_it(self):
        raw = driver.client_frame(8, struct.pack(">H", 1000))
        self.assertEqual(raw[0], 0x88)
        self.assertTrue(raw[1] & 0x80)                       # masked
        mask, body = raw[2:6], raw[6:]
        self.assertEqual(bytes(b ^ mask[i % 4] for i, b in enumerate(body)), struct.pack(">H", 1000))
        with self.assertRaises(ValueError):
            driver.client_frame(1, b"x" * 200)

    def test_logs_say(self):
        self.assertTrue(driver.logs_say("a\nhudfeed: refused a client (wrong token)\nb", "refused a client (wrong token)"))
        self.assertFalse(driver.logs_say("a\nb", "refused"))


class FakeFeed(threading.Thread):
    """The other end of a socketpair, answering as hudfeed's Hub does: the upgrade for its token and HTTP 403
    for anything else, then the messages it replays."""

    def __init__(self, sock, token, replay, origin_ok=True):
        super().__init__(daemon=True)
        self.sock, self.token, self.replay, self.origin_ok = sock, token, replay, origin_ok
        self.request = b""

    def run(self):
        try:
            while b"\r\n\r\n" not in self.request:
                self.request += self.sock.recv(4096)
            text = self.request.decode()
            path = text.split(" ")[1]
            key = next(l.split(": ", 1)[1] for l in text.split("\r\n") if l.lower().startswith("sec-websocket-key"))
            if path.strip("/") != self.token or "Origin:" in text:
                self.sock.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            self.sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                               f"Sec-WebSocket-Accept: {driver.ws_accept(key)}\r\n\r\n").encode())
            for msg in self.replay:
                self.sock.sendall(frame(json.dumps(msg).encode()))
        finally:
            threading.Event().wait(0.2)       # the client reads before the pipe ends
            self.sock.close()


class SocketTalk(unittest.TestCase):
    def serve(self, **kw):
        try:
            client, server = socket.socketpair()
        except OSError as e:
            self.skipTest(f"no socketpair here: {e}")
        feed = FakeFeed(server, **kw)
        feed.start()
        patch = mock.patch.object(driver.socket, "create_connection", return_value=client)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(client.close)
        return feed

    def test_the_right_token_upgrades(self):
        self.serve(token="secret", replay=[])
        status, sock, rest = driver.ws_connect(8766, "/secret")
        self.assertEqual(status, 101)
        self.assertIsNotNone(sock)
        sock.close()

    def test_a_polite_close_sends_the_close_frame_and_waits_for_the_server(self):
        feed = self.serve(token="secret", replay=[])
        status, sock, _ = driver.ws_connect(8766, "/secret")
        driver.ws_close(sock)
        feed.join(3)
        self.assertEqual(sock.fileno(), -1)                  # closed

    def test_a_wrong_token_gets_403_and_no_socket(self):
        self.serve(token="secret", replay=[])
        self.assertEqual(driver.ws_connect(8766, "/wrong"), (403, None, b""))

    def test_a_foreign_origin_gets_403_with_the_right_token(self):
        self.serve(token="secret", replay=[])
        self.assertEqual(driver.ws_connect(8766, "/secret", origin=driver.FOREIGN_ORIGIN)[0], 403)

    def test_replayed_finds_the_layers_poked(self):
        self.serve(token="secret", replay=[{"kind": "keymap", "x": "y" * 70000}, {"kind": "device"},
                                           {"kind": "layers", "ids": [1], "sent": True}])
        messages, found = driver.replayed(8766, "secret", lambda m: m.get("kind") == "layers" and m.get("sent") is True)
        self.assertEqual(found["ids"], [1])
        self.assertEqual([m["kind"] for m in messages], ["keymap", "device", "layers"])

    def test_replayed_without_it_reads_what_there_is_and_finds_nothing(self):
        self.serve(token="secret", replay=[{"kind": "layers", "ids": [0]}])
        messages, found = driver.replayed(8766, "secret", lambda m: m.get("sent") is True, timeout=2)
        self.assertIsNone(found)
        self.assertEqual(len(messages), 1)

    def test_replayed_refuses_the_wrong_token(self):
        self.serve(token="secret", replay=[])
        with self.assertRaises(driver.DriverError):
            driver.replayed(8766, "nope", lambda m: True)


class Reporting(unittest.TestCase):
    def test_counts_and_verdict(self):
        r = driver.Report(echo=lambda *_: None)
        r.passed("a")
        r.skipped("b", "why")
        r.note("c")
        self.assertTrue(r.ok)
        r.check("d", False, "because")
        self.assertFalse(r.ok)
        self.assertEqual((r.count(driver.PASS), r.count(driver.FAIL), r.count(driver.SKIP)), (1, 1, 1))

    def test_a_skip_is_not_a_pass(self):
        r = driver.Report(echo=lambda *_: None)
        r.skipped("surfaces", "not visible")
        self.assertEqual(r.count(driver.PASS), 0)

    def test_summary_table(self):
        text = driver.render_summary([{"status": "pass", "name": "a|b", "detail": "x"},
                                      {"status": "fail", "name": "c", "detail": "y\nz"},
                                      {"status": "skip", "name": "d", "detail": "w"}], "End-to-end: test")
        self.assertIn("### End-to-end: test", text)
        self.assertIn("a\\|b", text)
        self.assertIn("1 passed, 1 failed, 1 skipped.", text)
        self.assertNotIn("y\nz", text)


class Workflow(unittest.TestCase):
    """.github/workflows/e2e.yml, which must gate as its comments say: a typo there runs the e2e jobs on
    every pull request, or on none, and nothing else notices."""

    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest("PyYAML is not here")
        cls.wf = yaml.safe_load((ROOT / ".github" / "workflows" / "e2e.yml").read_text(encoding="utf-8"))

    def test_runs_on_pull_requests_and_labels_only(self):
        on = self.wf.get("on", self.wf.get(True))        # YAML 1.1 reads a bare `on` as true
        self.assertEqual(list(on), ["pull_request"])
        self.assertEqual(sorted(on["pull_request"]["types"]), ["labeled", "opened", "reopened", "synchronize"])

    def test_the_jobs_and_their_limits(self):
        self.assertEqual(sorted(self.wf["jobs"]), ["hyprland", "macos", "niri"])
        for name, job in self.wf["jobs"].items():
            self.assertLessEqual(job["timeout-minutes"], 20, name)

    def test_both_jobs_gate_on_the_release_branch_or_the_label(self):
        gates = {name: " ".join(str(job["if"]).split()) for name, job in self.wf["jobs"].items()}
        self.assertEqual(len(set(gates.values())), 1, "the two jobs gate differently")
        gate = next(iter(gates.values()))
        self.assertIn("startsWith(github.head_ref, 'release-please--branches--')", gate)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", gate)
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'release-validation')", gate)

    def test_a_label_cannot_cancel_a_running_validation(self):
        cancel = str(self.wf["concurrency"]["cancel-in-progress"])
        self.assertIn("github.event.action != 'labeled'", cancel)
        self.assertIn("github.event.pull_request.number", self.wf["concurrency"]["group"])

    def test_artifacts_are_uploaded_whatever_happened(self):
        for name, job in self.wf["jobs"].items():
            uploads = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
            self.assertTrue(uploads, name)
            for s in uploads:
                self.assertEqual(str(s.get("if")).strip(), "always()", name)

    def test_nothing_here_has_a_token_that_writes(self):
        self.assertEqual(self.wf["permissions"], {"contents": "read"})
        self.assertNotIn("pull_request_target", json.dumps(self.wf))


if __name__ == "__main__":
    unittest.main()
