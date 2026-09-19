"""Pulling a project folder from a sender."""

from __future__ import annotations

import json
import socket
import sys
import tarfile
from pathlib import Path

from ._files import human, safe_extract, sha256_file
from ._protocol import (
    DEFAULT_PORT,
    MANIFEST_NAME,
    PROTOCOL,
    BeamError,
    find_sender,
    recv_msg,
    send_msg,
)


def receive_project(
    host: str | None = None,
    port: int = DEFAULT_PORT,
    code: str = "",
    out=".",
    name: str | None = None,
    overwrite: bool = False,
    verify: bool = True,
    quiet: bool = False,
    wait: float = 15.0,
) -> Path:
    """Pull a project from a sender. Returns the folder it was written to.

    Leave ``host`` as None to find the sender on the LAN by its code.
    """

    def say(*args):
        if not quiet:
            print(*args, file=sys.stderr, flush=True)

    if host is None:
        if not code:
            raise BeamError("need the code shown by the sender")
        say(f"\n  looking for the sender with code {code} ...")
        host, port, _ = find_sender(code, timeout=wait)
        say(f"  found it at {host}:{port}")
    elif ":" in host and host.count(":") == 1:
        host, _, raw_port = host.partition(":")
        port = int(raw_port)

    sock = socket.create_connection((host, port), timeout=30)
    try:
        send_msg(sock, {"protocol": PROTOCOL, "code": code})
        reply = recv_msg(sock)
        if not reply.get("ok"):
            raise BeamError(f"sender refused us: {reply.get('error', 'unknown')}")

        total = reply.get("bytes", 0)
        count = reply.get("count", 0)
        dest = Path(out).expanduser().resolve() / (name or reply["name"])
        if dest.exists() and any(dest.iterdir()) and not overwrite:
            raise BeamError(
                f"{dest} already exists and is not empty "
                f"(pass overwrite=True or --force to write into it anyway)"
            )
        dest.mkdir(parents=True, exist_ok=True)

        say(f"\n  receiving {reply['name']}  ({count} files, {human(total)})")
        say(f"  into {dest}")

        sock.settimeout(None)
        manifest = None
        done = 0
        stream = sock.makefile("rb")
        with tarfile.open(fileobj=stream, mode="r|gz") as tar:
            for member in tar:
                try:
                    safe_extract(tar, member, dest)
                except ValueError as exc:
                    raise BeamError(str(exc)) from None
                if member.name == MANIFEST_NAME:
                    manifest = json.loads(
                        (dest / MANIFEST_NAME).read_text(encoding="utf-8")
                    )
                    (dest / MANIFEST_NAME).unlink()
                    continue
                done += 1
                if not quiet:
                    print(
                        f"\r  {done}/{count or '?'}   {member.name[:52]:<52}",
                        end="",
                        file=sys.stderr,
                        flush=True,
                    )
        say("")

        verified, mismatched = 0, []
        if verify and manifest:
            say("  verifying ...")
            for rel, meta in manifest["files"].items():
                target = dest / rel
                if not target.is_file():
                    mismatched.append(f"{rel} (missing)")
                elif meta["sha256"] is None:
                    if target.stat().st_size != meta["size"]:
                        mismatched.append(f"{rel} (size differs)")
                    else:
                        verified += 1
                elif sha256_file(target) != meta["sha256"]:
                    mismatched.append(f"{rel} (checksum differs)")
                else:
                    verified += 1

        try:
            send_msg(
                sock, {"ok": True, "verified": verified, "mismatched": len(mismatched)}
            )
        except OSError:
            pass

        if mismatched:
            say(f"  {len(mismatched)} file(s) did not verify:")
            for item in mismatched[:10]:
                say(f"      {item}")
            raise BeamError("transfer completed with verification failures")

        say(f"  done - {verified or done} files in {dest}")
        return dest
    finally:
        sock.close()
