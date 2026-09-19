"""Free temporary file hosts that carry the encrypted zip between laptops.

Each host is tried in order. The code starts with the host's letter, so the
receiver knows where to download from.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request

from . import __version__
from ._protocol import BeamError

USER_AGENT = f"beam-lan/{__version__} (+https://pypi.org/project/beam-lan/)"
UPLOAD_NAME = "beam.bin"
BLOCK = 1 << 16


class _Host:
    letter = ""
    label = ""
    max_bytes = 0
    keeps = ""

    def upload(self, path, progress) -> str:
        """Upload ``path`` and return the host's id for it."""
        raise NotImplementedError

    def download_request(self, file_id) -> urllib.request.Request:
        raise NotImplementedError


class TempSh(_Host):
    letter, label, max_bytes, keeps = "t", "temp.sh", 4 * 1024**3, "3 days"

    def upload(self, path, progress):
        url = _post_file("https://temp.sh/upload", "file", path, progress).strip()
        # https://temp.sh/<id>/beam.bin
        parts = url.rstrip("/").split("/")
        if len(parts) < 2 or not url.startswith("https://temp.sh/"):
            raise BeamError(f"temp.sh gave an unexpected reply: {url[:100]!r}")
        return parts[-2]

    def download_request(self, file_id):
        # temp.sh serves the file itself only to a POST; a GET returns a page.
        return urllib.request.Request(
            f"https://temp.sh/{file_id}/{UPLOAD_NAME}", data=b"", method="POST"
        )


class Uguu(_Host):
    letter, label, max_bytes, keeps = "u", "uguu.se", 128 * 1024**2, "3 hours"

    def upload(self, path, progress):
        reply = _post_file("https://uguu.se/upload", "files[]", path, progress)
        try:
            url = json.loads(reply)["files"][0]["url"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise BeamError(
                f"uguu.se gave an unexpected reply: {reply[:100]!r}"
            ) from None
        # https://<server>.uguu.se/<name>.bin - the server letter varies
        prefix = "https://"
        server, _, name = url[len(prefix) :].partition(".uguu.se/")
        if not url.startswith(prefix) or not name.endswith(".bin") or "/" in name:
            raise BeamError(f"uguu.se gave an unexpected reply: {url[:100]!r}")
        return f"{server}.{name[: -len('.bin')]}"

    def download_request(self, file_id):
        server, _, name = file_id.partition(".")
        return urllib.request.Request(f"https://{server}.uguu.se/{name}.bin")


HOSTS = [TempSh(), Uguu()]
BY_LETTER = {host.letter: host for host in HOSTS}


class _MultipartBody:
    """File-like multipart/form-data body, streamed from disk with progress."""

    def __init__(self, field, path, progress):
        self.boundary = "beam" + secrets.token_hex(16)
        self.head = (
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; '
            f'filename="{UPLOAD_NAME}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        self.tail = f"\r\n--{self.boundary}--\r\n".encode()
        self.size = os.path.getsize(path)
        self.length = len(self.head) + self.size + len(self.tail)
        self.fh = open(path, "rb")
        self.stage = 0
        self.sent = 0
        self.progress = progress

    def read(self, n=-1):
        if self.stage == 0:
            self.stage = 1
            return self.head
        if self.stage == 1:
            data = self.fh.read(BLOCK if n is None or n < 0 else n)
            if data:
                self.sent += len(data)
                if self.progress:
                    self.progress(self.sent, self.size)
                return data
            self.stage = 2
            return self.tail
        return b""

    def close(self):
        self.fh.close()


def _post_file(url, field, path, progress) -> str:
    body = _MultipartBody(field, path, progress)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={body.boundary}",
            "Content-Length": str(body.length),
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return resp.read(1 << 16).decode("utf-8", "replace")
    finally:
        body.close()


def upload(path, progress=None, say=None) -> tuple[str, str, str]:
    """Upload to the first host that takes it. Returns (letter, file_id, keeps)."""
    size = os.path.getsize(path)
    errors = []
    for host in HOSTS:
        if size > host.max_bytes:
            errors.append(f"{host.label}: file is over its size limit")
            continue
        if say:
            say(f"  uploading to {host.label} ...")
        try:
            return host.letter, host.upload(path, progress), host.keeps
        except (OSError, BeamError) as exc:  # URLError is an OSError
            errors.append(f"{host.label}: {exc}")
            if say:
                say(f"\n  {host.label} failed ({exc}), trying the next one")
    raise BeamError("upload failed on every host:\n    " + "\n    ".join(errors))


def download(letter, file_id, dst, progress=None) -> None:
    host = BY_LETTER.get(letter)
    if host is None:
        raise BeamError(
            "this code is not valid (unknown host); check it was copied fully"
        )
    request = host.download_request(file_id)
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dst, "wb") as fh:
                for block in iter(lambda: resp.read(BLOCK), b""):
                    fh.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            raise BeamError(
                f"nothing found for this code on {host.label}: it has expired "
                f"(kept for {host.keeps}) or was mistyped"
            ) from None
        raise BeamError(f"download failed: {host.label} said {exc.code}") from None
    except urllib.error.URLError as exc:
        raise BeamError(
            f"could not reach {host.label} ({exc.reason}); "
            "check the internet connection"
        ) from None
