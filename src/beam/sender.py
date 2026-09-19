"""Serving a project folder to one receiver."""

from __future__ import annotations

import gzip
import json
import secrets
import socket
import sys
import tarfile
import time
from pathlib import Path

from ._files import (
    DEFAULT_EXCLUDES,
    BytesReader,
    human,
    read_ignore_file,
    sha256_file,
    walk,
)
from ._protocol import (
    DEFAULT_PORT,
    DISCOVERY_PORT,
    MANIFEST_NAME,
    PROTOCOL,
    BeamError,
    DiscoveryResponder,
    lan_ip,
    recv_msg,
    send_msg,
)


class SocketWriter:
    """Minimal write-only file object so tarfile can stream into a socket."""

    def __init__(self, sock: socket.socket):
        self.sock = sock

    def write(self, data: bytes) -> int:
        self.sock.sendall(data)
        return len(data)

    def flush(self) -> None:
        pass


def send_project(
    path,
    port: int = DEFAULT_PORT,
    code: str | None = None,
    excludes=None,
    extra_excludes=(),
    hash_files: bool = True,
    max_size_mb: float | None = None,
    compresslevel: int = 6,
    quiet: bool = False,
    on_ready=None,
    discoverable: bool = True,
    discovery_port: int = DISCOVERY_PORT,
) -> str:
    """Serve one folder to one receiver, then stop.

    Pass ``port=0`` to bind an ephemeral port; ``on_ready`` is then called with
    ``{"host", "port", "code", "files", "bytes"}`` once the socket is listening.
    With ``discoverable`` the sender answers LAN broadcasts, so the receiver
    only needs the code.
    Returns the access code.
    """
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")

    patterns = list(DEFAULT_EXCLUDES if excludes is None else excludes)
    patterns += list(extra_excludes)
    patterns += read_ignore_file(root)

    max_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb else None
    files, skipped = walk(root, patterns, max_bytes)
    if not files:
        raise BeamError(f"nothing to send from {root} after applying excludes")

    total = sum(size for _, _, size in files)
    code = code or secrets.token_hex(3)

    def say(*args):
        if not quiet:
            print(*args, file=sys.stderr, flush=True)

    say(f"\n  project : {root.name}  ({len(files)} files, {human(total)})")
    biggest = sorted(files, key=lambda f: -f[2])[:3]
    if biggest and biggest[0][2] > 10 * 1024 * 1024:
        say("  largest : " + ", ".join(f"{r} ({human(s)})" for _, r, s in biggest))
    for rel, size in skipped:
        say(f"  skipped : {rel} ({human(size)}, over --max-size)")

    manifest = {"name": root.name, "protocol": PROTOCOL, "files": {}}
    if hash_files:
        say("  hashing ...")
        for abs_path, rel, size in files:
            manifest["files"][rel.as_posix()] = {
                "size": size,
                "sha256": sha256_file(abs_path),
            }
    else:
        for _, rel, size in files:
            manifest["files"][rel.as_posix()] = {"size": size, "sha256": None}

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(1)
    bound_port = server.getsockname()[1]

    responder = None
    if discoverable:
        try:
            responder = DiscoveryResponder(
                code, bound_port, root.name, discovery_port
            ).start()
        except OSError as exc:
            say(f"  (auto-discovery unavailable: {exc}; receiver needs --host)")

    if on_ready is not None:
        on_ready(
            {
                "host": lan_ip(),
                "port": bound_port,
                "code": code,
                "files": len(files),
                "bytes": total,
            }
        )

    say(f"\n  code    : {code}")
    say("\n  on the other laptop, run:\n")
    if responder is not None:
        say(f"      beam recv {code}\n")
        say(f"  (if it is not found: --host {lan_ip()}:{bound_port})\n")
    else:
        say(f"      beam recv {code} --host {lan_ip()}:{bound_port}\n")
    say("  waiting (Ctrl-C to cancel) ...")

    try:
        while True:
            conn, addr = server.accept()
            try:
                conn.settimeout(30)
                hello = recv_msg(conn)
                if hello.get("protocol") != PROTOCOL:
                    send_msg(conn, {"ok": False, "error": "protocol mismatch"})
                    continue
                if not secrets.compare_digest(str(hello.get("code", "")), code):
                    send_msg(conn, {"ok": False, "error": "bad code"})
                    say(f"  refused {addr[0]} (wrong code)")
                    continue

                say(f"  sending to {addr[0]} ...")
                send_msg(
                    conn,
                    {
                        "ok": True,
                        "name": root.name,
                        "count": len(files),
                        "bytes": total,
                        "hashed": hash_files,
                    },
                )

                conn.settimeout(None)
                _stream(conn, manifest, files, total, compresslevel, quiet)

                conn.shutdown(socket.SHUT_WR)
                conn.settimeout(600)
                try:
                    ack = recv_msg(conn)
                    bad = ack.get("mismatched", 0)
                    if bad:
                        say(f"  done, but {bad} file(s) failed verification")
                    else:
                        say(f"  done - {ack.get('verified', len(files))} files landed")
                except (BeamError, OSError):
                    say("  stream finished (no confirmation from the receiver)")
                return code
            finally:
                conn.close()
    except KeyboardInterrupt:
        say("\n  cancelled")
        return code
    finally:
        if responder is not None:
            responder.stop()
        server.close()


def _stream(conn, manifest, files, total, compresslevel, quiet):
    sent = 0
    writer = SocketWriter(conn)
    # tarfile's "w|gz" only accepts compresslevel from 3.12, so gzip ourselves.
    with gzip.GzipFile(
        fileobj=writer, mode="wb", compresslevel=compresslevel, mtime=0
    ) as gz, tarfile.open(fileobj=gz, mode="w|") as tar:
        blob = json.dumps(manifest).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size = len(blob)
        info.mtime = int(time.time())
        tar.addfile(info, BytesReader(blob))

        for abs_path, rel, size in files:
            tar.add(abs_path, arcname=rel.as_posix(), recursive=False)
            sent += size
            if not quiet:
                pct = 100 * sent / total if total else 100
                print(
                    f"\r  {pct:5.1f}%  {human(sent)} / {human(total)}"
                    f"   {str(rel)[:48]:<48}",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
    if not quiet:
        print("", file=sys.stderr, flush=True)
