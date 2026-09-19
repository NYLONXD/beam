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
