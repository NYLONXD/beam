"""The self-hosted relay: a stand-in Worker, served over real HTTP.

The fake below answers the same four endpoints as relay/src/worker.js, so
these tests exercise beam's actual urllib calls rather than a mocked-out
_cloud.upload.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import beam
from beam import _cloud
from beam._protocol import BeamError


class Relay:
    """State and policy for one fake relay, shared with its handler."""

    def __init__(self):
        self.blobs = {}
        self.gone = set()
        self.max_bytes = 10 * 1024**2
        self.keeps = "5 hours"
        self.up = True
        self.refuse = None  # (status, message) to answer uploads with
        self.uploads = 0
        self.dones = []


def _handler(relay: Relay):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep pytest output clean
            pass

        def _reply(self, status, payload, raw=False):
            body = payload if raw else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header(
                "Content-Type",
                "application/octet-stream" if raw else "application/json",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not relay.up:
                return self._reply(502, {"error": "down"})
            if self.path == "/v1/health":
                return self._reply(
                    200, {"ok": True, "maxBytes": relay.max_bytes, "keeps": relay.keeps}
                )
            match = re.fullmatch(r"/v1/([0-9a-f]+)", self.path)
            if not match:
                return self._reply(404, {"error": "no such endpoint"})
            blob = relay.blobs.get(match.group(1))
            if blob is None:
                return self._reply(
                    410, {"error": "this code has expired or was already collected"}
                )
            return self._reply(200, blob, raw=True)

        def do_POST(self):
            if not relay.up:
                return self._reply(502, {"error": "down"})
            size = int(self.headers.get("Content-Length") or 0)

            if self.path == "/v1/up":
                relay.uploads += 1
                if relay.refuse:
                    status, message = relay.refuse
                    return self._reply(status, {"error": message})
                if size > relay.max_bytes:
                    return self._reply(413, {"error": f"too big: {size} bytes"})
                body = self.rfile.read(size)
                if not body:
                    return self._reply(400, {"error": "nothing arrived"})
                file_id = f"{len(relay.blobs):024x}"
                relay.blobs[file_id] = body
                return self._reply(200, {"id": file_id, "keeps": relay.keeps})

            match = re.fullmatch(r"/v1/([0-9a-f]+)/done", self.path)
            if not match:
                return self._reply(404, {"error": "no such endpoint"})
            file_id = match.group(1)
            relay.dones.append(file_id)
            relay.blobs.pop(file_id, None)  # burn after reading
            relay.gone.add(file_id)
            return self._reply(200, {"ok": True})

    return Handler


class Spare(_cloud._Host):
    """Stands in for the public hosts, so no test here touches the internet."""

    letter, label = "s", "spare"
    max_bytes, keeps = 10**9, "a while"

    def __init__(self):
        self.uploads = 0

    def upload(self, source, progress=None):
        self.uploads += 1
        return "5pare"

    def download_request(self, file_id):
        raise AssertionError("the spare host should never be downloaded from")


@pytest.fixture
def relay(monkeypatch):
    state = Relay()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        _cloud.RELAY_ENV, f"http://127.0.0.1:{server.server_address[1]}"
    )
    # The real x0.at and friends are never acceptable in a test run.
    state.spare = Spare()
    monkeypatch.setattr(_cloud, "HOSTS", [_cloud.HOSTS[0], state.spare])
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def make_project(root):
    root.mkdir(parents=True)
    (root / "main.py").write_text("import requests\nprint('hi')\n")
    return root


# --- it is only there once you point it somewhere -----------------------------


def test_no_relay_configured_means_nothing_changes(monkeypatch):
    monkeypatch.delenv(_cloud.RELAY_ENV, raising=False)
    monkeypatch.setattr(_cloud.BeamRelay, "DEFAULT_URL", "")
    host = _cloud.HOSTS[0]
    assert isinstance(host, _cloud.BeamRelay)
    assert host.configured is False
    assert host.alive() is False


def test_the_relay_is_tried_first_when_it_is_set(relay, tmp_path):
    blob = tmp_path / "upload.bin"
    blob.write_bytes(b"x" * 1000)
    letter, file_id, keeps = _cloud.upload(blob)
    assert letter == "w" and keeps == "5 hours"
    assert relay.blobs[file_id] == b"x" * 1000


def test_a_baked_in_url_works_without_the_variable(monkeypatch, relay):
    url = _cloud.BeamRelay().base
    monkeypatch.delenv(_cloud.RELAY_ENV, raising=False)
    monkeypatch.setattr(_cloud.BeamRelay, "DEFAULT_URL", url)
    assert _cloud.BeamRelay().configured is True
    assert _cloud.BeamRelay().alive() is True


# --- the whole way round ------------------------------------------------------


def test_send_and_receive_through_the_relay(relay, tmp_path):
    project = make_project(tmp_path / "app")
    code = beam.send(project, quiet=True)
    assert code.startswith("w")

    # the relay only ever held ciphertext
    stored = next(iter(relay.blobs.values()))
    assert b"main.py" not in stored and b"print('hi')" not in stored

    folder = beam.receive(code, out=tmp_path / "inbox", extract=True, quiet=True)
    assert (folder / "main.py").read_text() == "import requests\nprint('hi')\n"


def test_it_is_burned_once_the_receiver_has_it(relay, tmp_path):
    code = beam.send(make_project(tmp_path / "app"), quiet=True)
    file_id = code[1:].rpartition("-")[0]
    assert relay.blobs

    beam.receive(code, out=tmp_path / "inbox", quiet=True)
    assert relay.dones == [file_id]
    assert relay.blobs == {}  # gone, without waiting for the TTL

    with pytest.raises(BeamError, match="expired"):
        beam.receive(code, out=tmp_path / "second", quiet=True)


def test_a_broken_relay_does_not_lose_the_file(relay, tmp_path):
    """If /done cannot be reached the receiver still keeps what it downloaded."""
    code = beam.send(make_project(tmp_path / "app"), quiet=True)

    def refuse(self, file_id):
        raise OSError("connection reset")

    original = _cloud.BeamRelay.after_download
    _cloud.BeamRelay.after_download = refuse
    try:
        got = beam.receive(code, out=tmp_path / "inbox", quiet=True)
    finally:
        _cloud.BeamRelay.after_download = original
    assert got.is_file() and got.name == "app.zip"


def test_a_half_finished_download_is_not_burned(relay, tmp_path):
    """A corrupted download must leave the relay's copy alone."""
    code = beam.send(make_project(tmp_path / "app"), quiet=True)
    file_id = code[1:].rpartition("-")[0]
    relay.blobs[file_id] = relay.blobs[file_id][:-20]  # truncate it in flight

    with pytest.raises(BeamError):
        beam.receive(code, out=tmp_path / "inbox", quiet=True)
    assert relay.dones == []  # decrypt failed, so nothing was confirmed


# --- when the relay says no ---------------------------------------------------


@pytest.mark.parametrize(
    "status, message, expected",
    [
        (429, "too many uploads from this address", "too many uploads"),
        (503, "the relay is full right now", "relay is full"),
        (413, "too big: 99 bytes", "too big"),
    ],
)
def test_a_refusal_falls_through_to_the_public_hosts(
    relay, tmp_path, status, message, expected
):
    relay.refuse = (status, message)
    blob = tmp_path / "upload.bin"
    blob.write_bytes(b"y" * 500)

    letter, file_id, _ = _cloud.upload(blob)

    assert relay.uploads == 1  # it was tried
    assert (letter, file_id) == ("s", "5pare")  # and moved on from


def test_a_relay_that_is_down_is_skipped_before_a_big_upload(
    relay, tmp_path, monkeypatch
):
    relay.up = False
    monkeypatch.setattr(_cloud, "HOSTS", _cloud.HOSTS[:1])  # nothing behind it
    blob = tmp_path / "upload.bin"
    blob.write_bytes(b"z" * (_cloud.PROBE_ABOVE + 1))

    with pytest.raises(BeamError, match="upload failed on every host"):
        _cloud.upload(blob)
    assert relay.uploads == 0  # health check answered first, so no wasted upload


def test_a_relay_code_without_a_relay_says_so(relay, tmp_path, monkeypatch):
    code = beam.send(make_project(tmp_path / "app"), quiet=True)
    monkeypatch.delenv(_cloud.RELAY_ENV, raising=False)
    monkeypatch.setattr(_cloud.BeamRelay, "DEFAULT_URL", "")

    with pytest.raises(BeamError, match="no relay is set here"):
        beam.receive(code, out=tmp_path / "inbox", quiet=True)


def test_junk_from_a_relay_is_an_error(relay, tmp_path, monkeypatch):
    monkeypatch.setattr(
        _cloud.BeamRelay, "keeps", "a few hours"
    )
    blob = tmp_path / "upload.bin"
    blob.write_bytes(b"q" * 100)

    class Pretend:
        status = 200

        def read(self, _n=None):
            return b'{"id": "not hex!!"}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(_cloud.urllib.request, "urlopen", lambda *a, **k: Pretend())
    with pytest.raises(BeamError, match="unexpected reply"):
        _cloud.BeamRelay().upload(blob)
