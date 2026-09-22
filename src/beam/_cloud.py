"""Free temporary file hosts that carry the encrypted zip between laptops.

The zip is uploaded to the first host that will take it, and the code starts
with that host's letter so the receiver knows where to download from. Hosts go
down, block whole countries and change their rules without notice, so there
are several and beam moves on to the next one when one fails.

Before a big upload each candidate is poked with a few bytes first: without
that, a host that is down costs a whole upload before it says so.
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

# Poking a host costs a round trip, so only bother when a wasted upload would
# cost more than that.
PROBE_ABOVE = 8 * 1024**2
PROBE_BYTES = b"beam health check\n"

#: Points beam at a relay you deployed yourself; see relay/ in the repository.
RELAY_ENV = "BEAM_RELAY"


class _Host:
    letter = ""
    label = ""
    max_bytes = 0
    keeps = ""
    #: False for a host that needs setting up first, such as a relay with no
    #: URL yet. Unconfigured hosts are passed over as if they were not listed.
    configured = True

    def upload(self, source, progress=None) -> str:
        """Upload a file path or a bytes blob; return the host's id for it."""
        raise NotImplementedError

    def download_request(self, file_id) -> urllib.request.Request:
        raise NotImplementedError

    def alive(self) -> bool:
        """Is the host taking uploads right now? A few bytes to find out."""
        try:
            self.upload(PROBE_BYTES)
        except (OSError, BeamError):
            return False
        return True

    def after_download(self, file_id) -> None:
        """The file arrived intact. Hosts that can delete it now, do."""


class BeamRelay(_Host):
    """A relay you deployed yourself. See ``relay/`` in the repository.

    Unlike the public hosts this one is yours: it holds the zip for hours
    rather than weeks, and deletes it the moment the other laptop confirms it
    arrived intact, rather than waiting for the clock. It is skipped entirely
    until ``DEFAULT_URL`` is filled in or ``BEAM_RELAY`` is set, so a plain
    install behaves exactly as it did before.
    """

    letter, label = "w", "your relay"
    max_bytes, keeps = 10 * 1024**2, "a few hours"

    #: Paste your deployed Worker's URL here to bake it into your own build.
    #: The BEAM_RELAY environment variable overrides it.
    DEFAULT_URL = ""

    @property
    def base(self) -> str:
        return (os.environ.get(RELAY_ENV) or self.DEFAULT_URL).strip().rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.base)

    def alive(self) -> bool:
        # A relay has a health endpoint, so unlike the public hosts it can be
        # asked without leaving a stray upload behind.
        if not self.configured:
            return False
        try:
            with urllib.request.urlopen(self._ask(f"{self.base}/v1/health"),
                                        timeout=15) as resp:
                return resp.status == 200
        except OSError:
            return False

    def _ask(self, url, data=None, method="GET") -> urllib.request.Request:
        return urllib.request.Request(
            url, data=data, method=method, headers={"User-Agent": USER_AGENT}
        )

    def upload(self, source, progress=None):
        if not self.configured:
            raise BeamError("no relay configured")
        body, size = _stream(source, progress)
        request = urllib.request.Request(
            f"{self.base}/v1/up",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as resp:
                reply = json.loads(resp.read(1 << 16).decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise BeamError(f"{self.label} refused it: {_said(exc)}") from None
        except ValueError:
            raise BeamError(f"{self.label} gave an unexpected reply") from None
        finally:
            body.close()

        file_id = str(reply.get("id", ""))
        if not file_id.isalnum():
            raise BeamError(f"{self.label} gave an unexpected reply: {reply!r}")
        # The relay decides its own retention, so let it tell us what to print.
        self.keeps = str(reply.get("keeps") or type(self).keeps)
        return file_id

    def download_request(self, file_id):
        if not self.configured:
            raise BeamError(
                "this code came from a beam relay, but no relay is set here. "
                f"Set {RELAY_ENV} to its URL and try again."
            )
        return self._ask(f"{self.base}/v1/{file_id}")

    def after_download(self, file_id):
        """Burn it now that the receiver has it, rather than waiting for TTL."""
        if not self.configured:
            return
        request = self._ask(f"{self.base}/v1/{file_id}/done", data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=30):
            pass


class X0(_Host):
    # Retention scales down with size: about a month for a small zip.
    letter, label = "x", "x0.at"
    max_bytes, keeps = 225 * 1024**2, "at least 30 days"

    def upload(self, source, progress=None):
        url = _post("https://x0.at", "file", source, progress).strip()
        return _tail_id(url, "https://x0.at/", self.label)

    def download_request(self, file_id):
        return urllib.request.Request(f"https://x0.at/{file_id}.bin")


class Catbox(_Host):
    letter, label = "c", "catbox.moe"
    max_bytes, keeps = 200 * 1024**2, "as long as catbox keeps it"

    def upload(self, source, progress=None):
        url = _post(
            "https://catbox.moe/user/api.php",
            "fileToUpload",
            source,
            progress,
            fields={"reqtype": "fileupload"},
        ).strip()
        return _tail_id(url, "https://files.catbox.moe/", self.label)

    def download_request(self, file_id):
        return urllib.request.Request(f"https://files.catbox.moe/{file_id}.bin")


class Uguu(_Host):
    letter, label, max_bytes, keeps = "u", "uguu.se", 128 * 1024**2, "3 hours"

    def upload(self, source, progress=None):
        reply = _post("https://uguu.se/upload", "files[]", source, progress)
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


class TempSh(_Host):
    # The only one here that takes really big files, so it stays on the list
    # even when it is having a bad day - nothing else will hold 4 GB.
    letter, label, max_bytes, keeps = "t", "temp.sh", 4 * 1024**3, "3 days"

    def upload(self, source, progress=None):
        url = _post("https://temp.sh/upload", "file", source, progress).strip()
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


# Your own relay first when there is one, because it is the only host here
# that answers to you. Then whichever public host keeps a small zip longest;
# anything over 225 MB only temp.sh will hold, so it is the big-file fallback.
HOSTS = [BeamRelay(), X0(), Catbox(), Uguu(), TempSh()]
BY_LETTER = {host.letter: host for host in HOSTS}


def _tail_id(url: str, prefix: str, label: str) -> str:
    """'https://x0.at/ab3f.bin' -> 'ab3f', with the shape checked."""
    name = url[len(prefix) :]
    if not url.startswith(prefix) or not name.endswith(".bin") or "/" in name:
        raise BeamError(f"{label} gave an unexpected reply: {url[:100]!r}")
    return name[: -len(".bin")]


def _said(exc: urllib.error.HTTPError) -> str:
    """The message a relay put in its JSON error body, or the bare status."""
    try:
        body = exc.read(4096).decode("utf-8", "replace")
        return str(json.loads(body).get("error") or body)[:200]
    except (ValueError, OSError, AttributeError):
        return f"HTTP {exc.code}"


def _stream(source, progress=None):
    """A readable body for urllib and its length, from a path or a blob."""
    if isinstance(source, (bytes, bytearray)):
        return _BytesReader(bytes(source)), len(source)
    return _ProgressFile(source, progress), os.path.getsize(source)


class _ProgressFile:
    """A file for urllib to stream, reporting how far it has got."""

    def __init__(self, path, progress=None):
        self.fh = open(path, "rb")
        self.size = os.path.getsize(path)
        self.sent = 0
        self.progress = progress

    def read(self, n=-1):
        data = self.fh.read(BLOCK if n is None or n < 0 else n)
        if data:
            self.sent += len(data)
            if self.progress:
                self.progress(self.sent, self.size)
        return data

    def close(self):
        self.fh.close()


class _BytesReader:
    """Just enough of a file to stream a blob through _MultipartBody."""

    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def read(self, n=-1):
        end = len(self.data) if n is None or n < 0 else self.pos + n
        chunk = self.data[self.pos : end]
        self.pos += len(chunk)
        return chunk

    def close(self):
        pass


class _MultipartBody:
    """File-like multipart/form-data body, streamed from disk with progress."""

    def __init__(self, field, source, progress=None, fields=None):
        self.boundary = "beam" + secrets.token_hex(16)
        head = ""
        for name, value in (fields or {}).items():
            head += (
                f"--{self.boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            )
        head += (
            f"--{self.boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; '
            f'filename="{UPLOAD_NAME}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
        self.head = head.encode()
        self.tail = f"\r\n--{self.boundary}--\r\n".encode()
        if isinstance(source, (bytes, bytearray)):
            self.fh = _BytesReader(bytes(source))
            self.size = len(source)
        else:
            self.fh = open(source, "rb")
            self.size = os.path.getsize(source)
        self.length = len(self.head) + self.size + len(self.tail)
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


def _post(url, field, source, progress=None, fields=None) -> str:
    body = _MultipartBody(field, source, progress, fields)
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
    probe_first = size > PROBE_ABOVE
    errors = []
    for host in HOSTS:
        if not host.configured:  # a relay nobody has pointed anywhere yet
            continue
        if size > host.max_bytes:
            errors.append(f"{host.label}: over its {host.max_bytes // 1024**2} MB cap")
            continue
        if probe_first and not host.alive():
            errors.append(f"{host.label}: not answering")
            if say:
                say(f"  {host.label} is not answering, trying the next one")
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


def finished(letter, file_id) -> None:
    """Say the file arrived intact, so a host that can delete it now does.

    Best effort on purpose: if the relay cannot be reached the TTL still
    clears the file, and the receiver already has what they came for.
    """
    host = BY_LETTER.get(letter)
    if host is None:
        return
    try:
        host.after_download(file_id)
    except (OSError, BeamError):
        pass


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
