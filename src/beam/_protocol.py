"""Wire protocol: framing, errors, and network helpers."""

from __future__ import annotations

import hashlib
import json
import socket
import struct
import threading
import time

PROTOCOL = 2
DEFAULT_PORT = 8765
DISCOVERY_PORT = 8766
MAX_HEADER = 1024 * 1024


class BeamError(RuntimeError):
    """The other side refused us, or the stream was malformed."""


def send_msg(sock: socket.socket, obj: dict) -> None:
    raw = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise BeamError("connection closed mid-message")
        buf += chunk
    return bytes(buf)


def recv_msg(sock: socket.socket) -> dict:
    (length,) = struct.unpack("!I", recv_exact(sock, 4))
    if length > MAX_HEADER:
        raise BeamError("refusing an absurdly large header")
    return json.loads(recv_exact(sock, length).decode("utf-8"))


def lan_ip() -> str:
    """Best guess at this machine's address on the local network."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # no packets are actually sent
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


# --- discovery ---------------------------------------------------------------
#
# The receiver only knows the code. It broadcasts a UDP query carrying a hash
# of the code; the sender with that code answers with its TCP port, and the
# reply's source address tells the receiver where to connect.


def code_tag(code: str) -> str:
    return hashlib.sha256(f"beam:{code}".encode()).hexdigest()[:16]


def _local_ipv4s() -> set[str]:
    ips = {lan_ip()}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return {ip for ip in ips if not ip.startswith("127.")}


def _broadcast_targets() -> list[str]:
    targets = ["255.255.255.255", "127.0.0.1"]
    # Windows often sends 255.255.255.255 out of one adapter only, so also hit
    # each adapter's subnet broadcast (assuming the usual home /24).
    for ip in _local_ipv4s():
        targets.append(ip.rsplit(".", 1)[0] + ".255")
    return targets


class DiscoveryResponder:
    """Answers discovery queries for one code until stopped."""

    def __init__(
        self, code: str, port: int, name: str, udp_port: int = DISCOVERY_PORT
    ):
        self.tag = code_tag(code)
        self.reply = json.dumps(
            {"beam": PROTOCOL, "id": self.tag, "port": port, "name": name}
        ).encode("utf-8")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", udp_port))
        self.sock.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> DiscoveryResponder:
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
                query = json.loads(data.decode("utf-8"))
            except (OSError, ValueError):  # timeout, ICMP reset, junk
                continue
            if isinstance(query, dict) and query.get("find") == self.tag:
                try:
                    self.sock.sendto(self.reply, addr)
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


def find_sender(
    code: str, timeout: float = 15.0, udp_port: int = DISCOVERY_PORT
) -> tuple[str, int, str]:
    """Locate the sender holding ``code`` on the LAN. Returns (host, port, name)."""
    tag = code_tag(code)
    query = json.dumps({"beam": PROTOCOL, "find": tag}).encode("utf-8")
    targets = _broadcast_targets()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    sock.settimeout(0.3)
    deadline = time.monotonic() + timeout
    next_ping = 0.0
    try:
        while time.monotonic() < deadline:
            if time.monotonic() >= next_ping:
                for target in targets:
                    try:
                        sock.sendto(query, (target, udp_port))
                    except OSError:
                        pass
                next_ping = time.monotonic() + 1.0
            try:
                data, addr = sock.recvfrom(2048)
                reply = json.loads(data.decode("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(reply, dict) and reply.get("id") == tag:
                return addr[0], int(reply["port"]), str(reply.get("name", ""))
    finally:
        sock.close()
    raise BeamError(
        f"no sender with code {code!r} found on this network after {timeout:.0f}s "
        "(same Wi-Fi? firewall allowing Python? or pass host=IP)"
    )
