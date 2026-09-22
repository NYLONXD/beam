"""Internet mode: encryption, code handling, and send/receive via a fake host."""

from __future__ import annotations

import os
import shutil
import urllib.error

import pytest

import beam
from beam import _cloud
from beam._crypto import CHUNK, decrypt_file, encrypt_file
from beam._protocol import BeamError


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    """Replace the real file hosts with a folder on disk."""
    store = tmp_path / "host"
    store.mkdir()

    def upload(path, progress=None, say=None):
        file_id = f"id{len(os.listdir(store))}"
        shutil.copy(path, store / file_id)
        return "t", file_id, "3 days"

    def download(letter, file_id, dst, progress=None):
        src = store / file_id
        if letter != "t" or not src.exists():
            raise BeamError("nothing found for this code: it has expired")
        shutil.copy(src, dst)

    monkeypatch.setattr(_cloud, "upload", upload)
    monkeypatch.setattr(_cloud, "download", download)
    return store


def make_project(root):
    root.mkdir(parents=True)
    (root / "main.py").write_text("import requests\nprint('hi')\n")
    (root / "clip.mp4").write_bytes(b"video")
    return root


def test_send_and_receive_over_the_internet(tmp_path, fake_host):
    project = make_project(tmp_path / "app")
    code = beam.send(project, exclude=[".mp4"], quiet=True)

    assert code.startswith("t") and "-" in code
    secret = code.rpartition("-")[2]
    assert len(secret) == 16 and secret.isalnum()
    # what the host stores is encrypted: no readable file names or code
    stored = next(fake_host.iterdir()).read_bytes()
    assert b"main.py" not in stored and b"print('hi')" not in stored

    folder = beam.receive(code, out=tmp_path / "inbox", extract=True, quiet=True)
    assert folder == tmp_path / "inbox" / "app"
    assert (folder / "main.py").exists() and (folder / "start.bat").exists()
    assert not (folder / "clip.mp4").exists()
    assert (folder / "requirements.txt").read_text().split() == ["requests"]


def test_pasted_code_with_whitespace_still_works(tmp_path, fake_host):
    code = beam.send(make_project(tmp_path / "app"), quiet=True)
    got = beam.receive(f"  {code}\n", out=tmp_path / "inbox", quiet=True)
    assert got.name == "app.zip"


def test_wrong_secret_is_rejected(tmp_path, fake_host):
    code = beam.send(make_project(tmp_path / "app"), quiet=True)
    bad = code.rpartition("-")[0] + "-" + "A" * 16
    with pytest.raises(BeamError, match="wrong code"):
        beam.receive(bad, out=tmp_path / "inbox", quiet=True)
    assert not list((tmp_path / "inbox").iterdir())


def test_unknown_or_expired_code(tmp_path, fake_host):
    with pytest.raises(BeamError, match="expired"):
        beam.receive("tnope-" + "A" * 16, out=tmp_path, quiet=True)


def test_sending_an_existing_file(tmp_path, fake_host):
    doc = tmp_path / "report.pdf"
    doc.write_bytes(os.urandom(5000))
    code = beam.send(doc, quiet=True)
    got = beam.receive(code, out=tmp_path / "inbox", quiet=True)
    assert got.name == "report.pdf"
    assert got.read_bytes() == doc.read_bytes()


# --- encryption --------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 10, CHUNK, CHUNK + 1, 3 * CHUNK + 5])
def test_encrypt_roundtrip(tmp_path, size):
    src, enc, out = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    src.write_bytes(os.urandom(size))
    encrypt_file(src, enc, "secret", {"name": "a.zip"})
    assert decrypt_file(enc, out, "secret") == {"name": "a.zip"}
    assert out.read_bytes() == src.read_bytes()


def test_tampering_and_truncation_are_caught(tmp_path):
    src, enc, out = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    src.write_bytes(os.urandom(2 * CHUNK + 100))
    encrypt_file(src, enc, "secret", {"name": "a.zip"})
    good = enc.read_bytes()

    flipped = bytearray(good)
    flipped[len(good) // 2] ^= 1
    enc.write_bytes(bytes(flipped))
    with pytest.raises(BeamError):
        decrypt_file(enc, out, "secret")

    # drop the final chunk entirely: must not pass as a shorter, valid file
    last_start = len(good) - (5 + 100 + 16)
    enc.write_bytes(good[:last_start])
    with pytest.raises(BeamError):
        decrypt_file(enc, out, "secret")


def test_download_404_says_expired(tmp_path, monkeypatch):
    def fail(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "gone", {}, None)

    monkeypatch.setattr(_cloud.urllib.request, "urlopen", fail)
    with pytest.raises(BeamError, match="expired"):
        _cloud.download("t", "abc", tmp_path / "x")


# --- the host chain ----------------------------------------------------------


class FakeHost(_cloud._Host):
    """A host that can be told to be up, down, or small."""

    def __init__(self, letter, max_bytes=10**9, up=True):
        self.letter, self.label = letter, f"host-{letter}"
        self.max_bytes, self.keeps = max_bytes, "a while"
        self.up = up
        self.uploads = []

    def upload(self, source, progress=None):
        self.uploads.append(source)
        if not self.up:
            raise BeamError("down")
        return "id" + self.letter

    def download_request(self, file_id):
        return _cloud.urllib.request.Request(f"https://{self.label}/{file_id}.bin")


@pytest.fixture
def blob(tmp_path):
    path = tmp_path / "upload.bin"
    path.write_bytes(os.urandom(2048))
    return path


def test_upload_moves_on_when_a_host_is_down(blob, monkeypatch):
    dead, alive = FakeHost("d", up=False), FakeHost("a")
    monkeypatch.setattr(_cloud, "HOSTS", [dead, alive])

    letter, file_id, keeps = _cloud.upload(blob)
    assert (letter, file_id, keeps) == ("a", "ida", "a while")
    assert len(dead.uploads) == 1 and len(alive.uploads) == 1


def test_a_host_too_small_is_skipped_without_uploading(blob, monkeypatch):
    small, big = FakeHost("s", max_bytes=100), FakeHost("b")
    monkeypatch.setattr(_cloud, "HOSTS", [small, big])

    assert _cloud.upload(blob)[0] == "b"
    assert small.uploads == []  # not one wasted byte


def test_big_uploads_poke_the_host_before_committing(blob, monkeypatch):
    dead, alive = FakeHost("d", up=False), FakeHost("a")
    monkeypatch.setattr(_cloud, "HOSTS", [dead, alive])
    monkeypatch.setattr(_cloud, "PROBE_ABOVE", 100)  # our 2 KB blob counts as big

    assert _cloud.upload(blob)[0] == "a"
    # the dead host got the few probe bytes, never the file itself
    assert dead.uploads == [_cloud.PROBE_BYTES]
    assert alive.uploads == [_cloud.PROBE_BYTES, blob]


def test_every_host_failing_says_why(blob, monkeypatch):
    monkeypatch.setattr(
        _cloud, "HOSTS", [FakeHost("d", up=False), FakeHost("s", max_bytes=1)]
    )
    with pytest.raises(BeamError, match="upload failed on every host"):
        _cloud.upload(blob)


def test_every_host_has_its_own_letter():
    letters = [host.letter for host in _cloud.HOSTS]
    assert len(letters) == len(set(letters))
    assert set(_cloud.BY_LETTER) == set(letters)


def public_hosts():
    """The shared free hosts: everything but a relay of your own."""
    return [h for h in _cloud.HOSTS if not isinstance(h, _cloud.BeamRelay)]


@pytest.mark.parametrize(
    "url, letter, expected",
    [
        ("https://x0.at/ab3f.bin", "x", "ab3f"),
        ("https://files.catbox.moe/qcqm9r.bin", "c", "qcqm9r"),
    ],
)
def test_reply_urls_become_ids_and_back(url, letter, expected, monkeypatch):
    host = _cloud.BY_LETTER[letter]
    monkeypatch.setattr(_cloud, "_post", lambda *a, **k: url + "\n")
    file_id = host.upload("ignored")
    assert file_id == expected
    assert host.download_request(file_id).full_url == url


@pytest.mark.parametrize("junk", ["<html>down for maintenance</html>", "", "https://x"])
def test_a_junk_reply_is_an_error_not_a_bad_code(junk, monkeypatch):
    monkeypatch.setattr(_cloud, "_post", lambda *a, **k: junk)
    for host in public_hosts():
        with pytest.raises(BeamError, match="unexpected reply"):
            host.upload("ignored")


def test_your_own_relay_is_asked_before_the_shared_hosts():
    assert isinstance(_cloud.HOSTS[0], _cloud.BeamRelay)


def test_a_bytes_blob_uploads_like_a_file():
    body = _cloud._MultipartBody("file", b"hello", fields={"reqtype": "fileupload"})
    chunks = iter(lambda: body.read(1 << 16), b"")
    whole = b"".join(chunks)
    assert len(whole) == body.length
    assert b'name="reqtype"' in whole and b"fileupload" in whole
    assert whole.endswith(b"--\r\n") and b"hello" in whole
