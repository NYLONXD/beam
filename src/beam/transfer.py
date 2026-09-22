"""Sending a zipped project to another laptop, over the internet or the LAN.

Internet: the zip is encrypted, uploaded to a free temporary file host, and
the code is ``<host letter><file id>-<secret>``. The secret is the key and
never leaves the two laptops.

LAN: the sender listens, the receiver finds it by broadcast. Codes have no
dash, which is how ``receive`` tells the two apart.
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
import socket
import string
import tempfile
import zipfile
from pathlib import Path

from . import _cloud
from ._crypto import decrypt_file, encrypt_file
from ._files import HASH_CHUNK, human, sha256_file
from ._protocol import (
    DEFAULT_PORT,
    DISCOVERY_PORT,
    PROTOCOL,
    BeamError,
    DiscoveryResponder,
    find_sender,
    lan_ip,
    recv_exact,
    recv_msg,
    send_msg,
)
from .packer import pack

# How the other laptop is told to fetch the file. The ``beam`` command sets
# this to "cli"; the library leaves it alone and prints Python.
HINT = "python"


def _receive_hint(code: str, host: str | None = None) -> str:
    if HINT == "cli":
        return f"beam receive {code}" + (f" --host {host}" if host else "")
    where = f', host="{host}"' if host else ""
    return f'beam.receive("{code}"{where})'


def _say(quiet, *args):
    if not quiet:
        print(*args, flush=True)


def _progress(quiet, done, total, label):
    if not quiet:
        pct = 100 * done / total if total else 100.0
        print(f"\r  {pct:5.1f}%  {human(done)} / {human(total)}  {label}", end="",
              flush=True)


def send(
    path,
    exclude=None,
    main: str | None = None,
    start_script: bool = True,
    requirements=None,
    lan: bool = False,
    code: str | None = None,
    port: int = DEFAULT_PORT,
    timeout: float | None = None,
    quiet: bool = False,
    on_ready=None,
    discoverable: bool = True,
    discovery_port: int = DISCOVERY_PORT,
    **pack_options,
) -> str:
    """Zip a project and send it to another laptop. Returns the code.

    By default it goes over the internet: the zip is encrypted and uploaded,
    and the returned code works from anywhere (for 3 days) with
    ``beam.receive(code)``. The sender does not need to stay online.

    With ``lan=True`` it goes directly to a laptop on the same network: no
    upload and no size limit, but both must be online at the same time, and
    this call blocks until the other laptop has received it.

    path     project folder (zipped first, with start.bat) or any single file,
             such as a zip you already made, which is sent as it is
    exclude  file types / names to leave out, e.g. [".mp4", ".log"]
    main     script start.bat should run (default: main.py, app.py, ...)
    code, port, timeout, discoverable   LAN only
    Other keyword arguments (``max_size_mb``, ``use_default_excludes``, ...)
    go to :func:`beam.pack`.
    """
    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"{src} does not exist")

    tmpdir = Path(tempfile.mkdtemp(prefix="beam-"))
    try:
        if src.is_dir():
            zip_path = pack(
                src,
                output=tmpdir / f"{src.name}.zip",
                exclude=exclude,
                main=main,
                start_script=start_script,
                requirements=requirements,
                quiet=quiet,
                **pack_options,
            )
        else:
            zip_path = src
        if not lan:
            return _send_online(zip_path, tmpdir, quiet, on_ready)
        return _serve(zip_path, code, port, timeout, quiet, on_ready,
                      discoverable, discovery_port)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


SECRET_ALPHABET = string.ascii_letters + string.digits
SECRET_LENGTH = 16  # ~95 bits


def _send_online(zip_path: Path, workdir: Path, quiet, on_ready) -> str:
    secret = "".join(secrets.choice(SECRET_ALPHABET) for _ in range(SECRET_LENGTH))
    encrypted = workdir / "upload.bin"
    _say(quiet, "  encrypting ...")
    encrypt_file(zip_path, encrypted, secret, {"name": zip_path.name})

    def progress(done, total):
        _progress(quiet, done, total, "uploaded")

    letter, file_id, keeps = _cloud.upload(
        encrypted, progress=progress, say=lambda msg: _say(quiet, msg)
    )
    _say(quiet, "")
    code = f"{letter}{file_id}-{secret}"
    if on_ready is not None:
        on_ready({"code": code, "file": str(zip_path), "keeps": keeps})

    _say(quiet, f"\n  code : {code}\n")
    _say(quiet, "  send this code to the other person. They run:\n")
    _say(quiet, f"      {_receive_hint(code)}\n")
    _say(quiet, f"  it works from anywhere for {keeps}. Anyone with the code can")
    _say(quiet, "  download it, so share it only with the person it is for.")
    return code


def _listen(port: int) -> socket.socket:
    """Listen on ``port``, or on any free port if another program has it."""
    for candidate in (port, 0) if port else (0,):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # On Windows SO_REUSEADDR would let two senders share one port.
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", candidate))
        except OSError:
            server.close()
            if candidate == 0:
                raise
            continue
        server.listen(1)
        return server
    raise AssertionError("unreachable")


def _serve(file_path,code, port, timeout, quiet, on_ready, discoverable,
           discovery_port) -> str:
    size = file_path.stat().st_size
    digest = sha256_file(file_path)
    code = code or secrets.token_hex(3)

    server = _listen(port)
    server.settimeout(timeout)
    bound_port = server.getsockname()[1]

    responder = None
    if discoverable:
        try:
            responder = DiscoveryResponder(
                code, bound_port, file_path.name, discovery_port
            ).start()
        except OSError as exc:
            _say(quiet, f"  (auto-discovery unavailable: {exc})")

    if on_ready is not None:
        on_ready({"host": lan_ip(), "port": bound_port, "code": code,
                  "file": str(file_path), "bytes": size})

    _say(quiet, f"\n  code : {code}\n")
    _say(quiet, "  on the other laptop run:\n")
    address = f"{lan_ip()}:{bound_port}"
    if responder is not None:
        _say(quiet, f"      {_receive_hint(code)}\n")
        _say(quiet, f"  (if it is not found: {_receive_hint(code, address)})")
    else:
        _say(quiet, f"      {_receive_hint(code, address)}\n")
    _say(quiet, "  waiting for the other laptop ...")

    try:
        while True:
            try:
                conn, addr = server.accept()
            except socket.timeout:
                raise BeamError(f"nobody connected within {timeout:g}s") from None
            try:
                conn.settimeout(30)
                hello = recv_msg(conn)
                if hello.get("protocol") != PROTOCOL:
                    send_msg(conn, {"ok": False, "error": "beam version mismatch "
                                    "(install the same beam-lan version on both)"})
                    continue
                if not secrets.compare_digest(str(hello.get("code", "")), code):
                    send_msg(conn, {"ok": False, "error": "wrong code"})
                    _say(quiet, f"  refused {addr[0]} (wrong code)")
                    continue

                _say(quiet, f"  sending to {addr[0]} ...")
                send_msg(conn, {"ok": True, "filename": file_path.name,
                                "bytes": size, "sha256": digest})
                conn.settimeout(None)
                sent = 0
                with file_path.open("rb") as fh:
                    for block in iter(lambda: fh.read(HASH_CHUNK), b""):
                        conn.sendall(block)
                        sent += len(block)
                        _progress(quiet, sent, size, file_path.name)
                _say(quiet, "")

                conn.settimeout(600)
                try:
                    ack = recv_msg(conn)
                except (BeamError, OSError):
                    ack = {}
                if ack.get("ok"):
                    _say(quiet, "  done - the other laptop received it")
                elif ack:
                    raise BeamError(f"receiver reported: {ack.get('error')}")
                else:
                    _say(quiet, "  sent (no confirmation from the other laptop)")
                return code
            finally:
                conn.close()
    finally:
        if responder is not None:
            responder.stop()
        server.close()


def _free_path(path: Path) -> Path:
    """path, or 'name (1).ext', 'name (2).ext', ... if it is already taken."""
    if not path.exists():
        return path
    for n in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise BeamError(f"too many copies of {path.name} in {path.parent}")


def receive(
    code: str,
    out=".",
    host: str | None = None,
    port: int = DEFAULT_PORT,
    extract: bool = False,
    overwrite: bool = False,
    wait: float = 15.0,
    quiet: bool = False,
    discovery_port: int = DISCOVERY_PORT,
) -> Path:
    """Receive what the other laptop sent with ``beam.send``.

    code      the code the sender printed
    out       folder to save into (default: current folder)
    extract   also unzip it into a folder; returns that folder
    overwrite replace an existing file/folder instead of adding " (1)"
    host, port, wait   LAN only: the sender's IP or "IP:port" to skip
              searching the network, and how many seconds to search
    Returns the path of the saved zip (or the extracted folder).
    """
    code = "".join(str(code).split())  # tolerate spaces/newlines from pasting
    if not code:
        raise BeamError("need the code shown by the sender")

    out_dir = Path(out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if "-" in code:
        target = _receive_online(code, out_dir, overwrite, quiet)
    else:
        target = _receive_lan(code, out_dir, host, port, overwrite, wait, quiet,
                              discovery_port)

    _say(quiet, f"  saved to {target}")
    if extract and zipfile.is_zipfile(target):
        folder = out_dir / target.stem
        if folder.exists() and any(folder.iterdir()) and not overwrite:
            folder = _free_path(folder)
        _safe_unzip(target, folder)
        _say(quiet, f"  unzipped into {folder}")
        if (folder / "start.bat").exists():
            _say(quiet, "  double-click start.bat there to set it up and run it")
        return folder
    return target


def _receive_online(code, out_dir: Path, overwrite, quiet) -> Path:
    head, _, secret = code.rpartition("-")
    if len(head) < 2 or not secret:
        raise BeamError("this code is not valid; check it was copied fully")
    letter, file_id = head[0], head[1:]

    workdir = Path(tempfile.mkdtemp(prefix="beam-"))
    try:
        encrypted, plain = workdir / "download.bin", workdir / "file"
        _say(quiet, "  downloading ...")
        _cloud.download(
            letter, file_id, encrypted,
            progress=lambda done, total: _progress(quiet, done, total, "downloaded"),
        )
        _say(quiet, "")
        _say(quiet, "  decrypting ...")
        meta = decrypt_file(encrypted, plain, secret)
        # Only now, with the tag verified, is it safe to let the host drop it:
        # a half-finished download must never destroy the only copy.
        _cloud.finished(letter, file_id)

        name = Path(str(meta.get("name", "")).replace("\\", "/")).name or "received"
        target = out_dir / name
        if not overwrite:
            target = _free_path(target)
        shutil.move(str(plain), str(target))
        return target
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _receive_lan(code, out_dir: Path, host, port, overwrite, wait, quiet,
                 discovery_port) -> Path:
    if host is None:
        _say(quiet, f"  looking for the sender with code {code} ...")
        host, port, _ = find_sender(code, timeout=wait, udp_port=discovery_port)
        _say(quiet, f"  found it at {host}:{port}")
    elif host.count(":") == 1:
        host, _, raw_port = host.partition(":")
        port = int(raw_port)

    sock =socket.create_connection((host, port), timeout=30)
    try:
        send_msg(sock, {"protocol": PROTOCOL, "code": code})
        reply = recv_msg(sock)
        if not reply.get("ok"):
            raise BeamError(f"sender refused: {reply.get('error', 'unknown')}")

        name = Path(str(reply["filename"]).replace("\\", "/")).name or "received"
        size = int(reply["bytes"])
        target = out_dir / name
        if not overwrite:
            target = _free_path(target)
        part = target.with_name(target.name + ".part")

        _say(quiet, f"  receiving {name} ({human(size)}) ...")
        sock.settimeout(120)
        digest = hashlib.sha256()
        got = 0
        try:
            with part.open("wb") as fh:
                while got < size:
                    chunk = recv_exact(sock, min(HASH_CHUNK, size - got))
                    fh.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    _progress(quiet, got, size, name)
            _say(quiet, "")
            if digest.hexdigest() != reply["sha256"]:
                raise BeamError("file arrived damaged (checksum mismatch); try again")
            part.replace(target)
        except BaseException as exc:
            part.unlink(missing_ok=True)
            try:
                send_msg(sock, {"ok": False, "error": str(exc) or type(exc).__name__})
            except OSError:
                pass
            raise

        try:
            send_msg(sock, {"ok": True})
        except OSError:
            pass
    finally:
        sock.close()
    return target


def _safe_unzip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (root / member.filename).resolve()
            if target != root and root not in target.parents:
                raise BeamError(f"unsafe path in zip: {member.filename!r}")
        zf.extractall(root)
