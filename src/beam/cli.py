"""Command line interface for beam."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from ._protocol import DEFAULT_PORT, BeamError
from .receiver import receive_project
from .sender import send_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beam", description="Move a project folder between two laptops."
    )
    parser.add_argument("--version", action="version", version=f"beam {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    send = sub.add_parser("send", help="serve a folder to one receiver")
    send.add_argument("path", help="project folder to send")
    send.add_argument("--port", type=int, default=DEFAULT_PORT)
    send.add_argument("--code", help="access code (random if omitted)")
    send.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="extra pattern to skip (repeatable)",
    )
    send.add_argument(
        "--all",
        action="store_true",
        help="do not apply the default excludes (.git, venv, __pycache__, ...)",
    )
    send.add_argument(
        "--max-size",
        type=float,
        metavar="MB",
        help="skip files larger than this many MB",
    )
    send.add_argument(
        "--no-hash",
        action="store_true",
        help="skip SHA-256 hashing (faster on huge datasets, size-check only)",
    )
    send.add_argument("--fast", action="store_true", help="compress less, send sooner")

    recv = sub.add_parser("recv", help="pull a folder from a sender")
    recv.add_argument("code", help="the code shown by the sender")
    recv.add_argument("--host", help="sender's IP (or IP:port); skips auto-discovery")
    recv.add_argument("--port", type=int, default=DEFAULT_PORT)
    recv.add_argument(
        "--wait",
        type=float,
        default=15,
        metavar="SEC",
        help="how long to search the network for the sender",
    )
    recv.add_argument("--out", default=".", help="where to put the folder")
    recv.add_argument("--name", help="rename the folder on arrival")
    recv.add_argument(
        "--force", action="store_true", help="write into a non-empty folder"
    )
    recv.add_argument("--no-verify", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "send":
            send_project(
                args.path,
                port=args.port,
                code=args.code,
                excludes=[] if args.all else None,
                extra_excludes=args.exclude,
                hash_files=not args.no_hash,
                max_size_mb=args.max_size,
                compresslevel=1 if args.fast else 6,
            )
        else:
            receive_project(
                args.host,
                port=args.port,
                code=args.code,
                wait=args.wait,
                out=args.out,
                name=args.name,
                overwrite=args.force,
                verify=not args.no_verify,
            )
    except (BeamError, OSError, NotADirectoryError) as exc:
        print(f"\nbeam: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
