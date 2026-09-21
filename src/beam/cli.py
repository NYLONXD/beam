"""The ``beam`` command: send and receive projects without writing Python.

    beam send D:/projects/my_app     # laptop A - uploads, prints a code, exits
    beam receive ab3f9c              # laptop B - anywhere, later, unzips it

Sending goes over the internet by default, so the other person can be in
another city and pick it up hours later; the sending laptop can be closed the
moment the code appears. ``--lan`` hands it straight to a laptop on the same
network instead: nothing is uploaded, no size limit, but both have to be
running beam at the same time.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, transfer
from ._protocol import DEFAULT_PORT, BeamError
from .packer import pack
from .transfer import receive, send

USAGE = """\
  beam send <folder>        upload a project and print a code
  beam receive <code>       get it, from anywhere, and unzip it
  beam send <folder> --lan  hand it straight over to a laptop on this network
  beam pack <folder>        only make the zip, send it yourself\
"""


def _excludes(values) -> list[str]:
    """--exclude .mp4 --exclude .log, or --exclude .mp4,.log - both work."""
    out = []
    for value in values or []:
        out += [part.strip() for part in value.split(",") if part.strip()]
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="beam",
        description="Zip a project and send it to somebody else's laptop, "
        "wherever they are.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"beam {__version__}")
    subs = parser.add_subparsers(dest="command", metavar="<command>")

    # --- send ----------------------------------------------------------------
    send_p = subs.add_parser(
        "send",
        help="zip a folder, upload it and print a code",
        description="Zip a folder, encrypt it, upload it and print a code. The "
        "other person runs 'beam receive <code>' from anywhere, whenever suits "
        "them; you can close this laptop as soon as the code appears.",
    )
    send_p.add_argument("path", help="the project folder (or a single file to send)")
    send_p.add_argument(
        "-x", "--exclude", action="append", metavar="PATTERN",
        help="file type or name to leave out, e.g. .mp4 (repeatable)",
    )
    send_p.add_argument(
        "--main", metavar="SCRIPT", help="script start.bat should run"
    )
    send_p.add_argument(
        "--no-start-script", action="store_true",
        help="do not add start.bat / requirements.txt to the zip",
    )
    send_p.add_argument(
        "--max-size-mb", type=float, metavar="MB",
        help="leave out files bigger than this",
    )
    how = send_p.add_mutually_exclusive_group()
    how.add_argument(
        "--lan", action="store_true",
        help="hand it straight to a laptop on the same network instead: nothing "
        "is uploaded and there is no size limit, but both laptops have to be "
        "running beam at the same time",
    )
    how.add_argument(
        "--cloud", action="store_true",
        help="upload it - this is the default, so the flag only spells it out",
    )
    local = send_p.add_argument_group("--lan only")
    local.add_argument(
        "--code", metavar="CODE", help="pick the code yourself instead of a random one"
    )
    local.add_argument(
        "--port", type=int, default=DEFAULT_PORT, metavar="N",
        help=f"network port to listen on (default {DEFAULT_PORT})",
    )
    local.add_argument(
        "--timeout", type=float, metavar="SECONDS",
        help="give up if nobody connects within this many seconds",
    )
    send_p.add_argument("-q", "--quiet", action="store_true", help="print nothing")
    send_p.set_defaults(func=_do_send)

    # --- receive -------------------------------------------------------------
    recv_p = subs.add_parser(
        "receive",
        aliases=["recv", "get"],
        help="download what was sent to you and unzip it",
        description="Download what was sent to you and unzip it. The code says "
        "where it is: an uploaded zip is fetched from wherever you are, and a "
        "--lan code is found on the local network. You never say which.",
    )
    recv_p.add_argument("code", help="the code the sender printed")
    recv_p.add_argument(
        "-o", "--out", default=".", metavar="FOLDER",
        help="folder to save into (default: here)",
    )
    recv_p.add_argument(
        "--no-extract", action="store_true", help="keep the .zip instead of unzipping"
    )
    recv_p.add_argument(
        "--overwrite", action="store_true",
        help="replace what is already there instead of adding ' (1)'",
    )
    recv_p.add_argument(
        "--host", metavar="IP[:PORT]",
        help="--lan codes only: the sender's address, to skip searching",
    )
    recv_p.add_argument(
        "--wait", type=float, default=15.0, metavar="SECONDS",
        help="--lan codes only: how long to search for the sender (default 15)",
    )
    recv_p.add_argument("-q", "--quiet", action="store_true", help="print nothing")
    recv_p.set_defaults(func=_do_receive)

    # --- pack ----------------------------------------------------------------
    pack_p = subs.add_parser(
        "pack",
        help="only make the zip",
        description="Zip a folder (with its start.bat) and stop there.",
    )
    pack_p.add_argument("path", help="the project folder")
    pack_p.add_argument(
        "-o", "--output", metavar="ZIP",
        help="where to write the zip (default: <folder>.zip next to it)",
    )
    pack_p.add_argument(
        "-x", "--exclude", action="append", metavar="PATTERN",
        help="file type or name to leave out (repeatable)",
    )
    pack_p.add_argument("--main", metavar="SCRIPT", help="script start.bat should run")
    pack_p.add_argument(
        "--no-start-script", action="store_true",
        help="do not add start.bat / requirements.txt to the zip",
    )
    pack_p.add_argument(
        "--max-size-mb", type=float, metavar="MB",
        help="leave out files bigger than this",
    )
    pack_p.add_argument("-q", "--quiet", action="store_true", help="print nothing")
    pack_p.set_defaults(func=_do_pack)

    return parser


def _do_send(args) -> int:
    send(
        args.path,
        exclude=_excludes(args.exclude),
        main=args.main,
        start_script=not args.no_start_script,
        max_size_mb=args.max_size_mb,
        lan=args.lan,
        code=args.code,
        port=args.port,
        timeout=args.timeout,
        quiet=args.quiet,
    )
    return 0


def _do_receive(args) -> int:
    receive(
        args.code,
        out=args.out,
        host=args.host,
        extract=not args.no_extract,
        overwrite=args.overwrite,
        wait=args.wait,
        quiet=args.quiet,
    )
    return 0


def _do_pack(args) -> int:
    pack(
        args.path,
        output=args.output,
        exclude=_excludes(args.exclude),
        main=args.main,
        start_script=not args.no_start_script,
        max_size_mb=args.max_size_mb,
        quiet=args.quiet,
    )
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 2

    transfer.HINT = "cli"  # print "beam receive X", not "beam.receive('X')"
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n  stopped", file=sys.stderr)
        return 130
    except (BeamError, OSError, ValueError) as exc:
        print(f"\nbeam: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
