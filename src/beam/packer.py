"""Zipping a project folder, with start.bat / start.sh that set it up and run it."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

from . import _stacks
from ._detect import find_apps
from ._files import (
    DEFAULT_EXCLUDES,
    human,
    measure,
    normalize_excludes,
    read_ignore_file,
    walk,
)
from ._protocol import BeamError
from ._starter import start_bat, start_sh


def _say(quiet, *args, end="\n"):
    if not quiet:
        print(*args, end=end, flush=True)


def pack(
    path,
    output=None,
    exclude=None,
    main: str | None = None,
    start_script: bool = True,
    requirements=None,
    use_default_excludes: bool = True,
    max_size_mb: float | None = None,
    with_deps: bool = False,
    compresslevel: int = 6,
    quiet: bool = False,
) -> Path:
    """Zip a project folder and return the path of the .zip.

    path        the project folder
    output      where to write the zip (default: next to the folder, NAME.zip)
    exclude     file types / names to leave out, e.g. [".mp4", ".log", "data"]
    main        script start.bat should run (default: main.py, app.py, ...)
    start_script  add a start.bat that installs what the project needs and runs it
    requirements  pip packages for start.bat to install; by default the
                  project's requirements.txt is used, or one is worked out
                  from its imports
    with_deps   keep dependency folders (node_modules, vendor, ...) for a
                receiver with no internet; venvs and build output still stay
                behind, because they do not work when moved
    """
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a folder")

    out = Path(output).expanduser() if output else root.parent / f"{root.name}.zip"
    if out.suffix.lower() != ".zip":
        out = out.with_name(out.name + ".zip")
    out = out.resolve()

    patterns = list(DEFAULT_EXCLUDES) if use_default_excludes else []
    if with_deps:
        patterns = [p for p in patterns if p not in _stacks.DEPENDENCY_NAMES]
    patterns += normalize_excludes(exclude)
    patterns += read_ignore_file(root)

    max_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb else None
    files, skipped, left_out = walk(
        root, patterns, max_bytes, rules=use_default_excludes, with_deps=with_deps
    )
    files = [f for f in files if f[0].resolve() != out]
    if not files:
        raise BeamError(f"nothing to pack in {root} after applying excludes")

    extras = {}
    apps = []
    if start_script:
        apps = find_apps(files, main=main, requirements=requirements)
        if apps:
            extras["start.bat"] = start_bat(root.name, apps)
            extras["start.sh"] = start_sh(root.name, apps)
            for app in apps:  # requirements.txt worked out for a Python app
                for name, text in app.files.items():
                    extras[f"{app.folder}/{name}" if app.folder else name] = text
    files = [f for f in files if f[1].as_posix() not in extras]

    total = sum(size for _, _, size in files)
    _say(quiet, f"  packing  : {root.name}  ({len(files)} files, {human(total)})")
    for rel, size in skipped:
        _say(quiet, f"  skipped  : {rel} ({human(size)}, over max_size_mb)")
    if left_out and not quiet:
        _report_left_out(root, left_out, bool(extras))
    secrets = [rel.as_posix() for _, rel, _ in files if _stacks.is_secret(rel.name)]
    if secrets:
        _say(quiet, f"  included : {', '.join(secrets)}  (secrets: share this "
                    "only with the person it is for)")
    if start_script:
        _describe(apps, quiet)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    done = 0
    try:
        with zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel
        ) as zf:
            for abs_path, rel, size in files:
                if _is_shell_script(rel.name):
                    _write_script(zf, rel.as_posix(), abs_path.read_bytes(),
                                  abs_path.stat().st_mtime)
                else:
                    zf.write(abs_path, rel.as_posix())
                done += size
                if not quiet and total:
                    pct = 100 * done / total
                    print(f"\r  {pct:5.1f}%  {str(rel)[:56]:<56}", end="", flush=True)
            for name, text in extras.items():
                if name == "start.sh":
                    _write_script(zf, name, text.encode(), time.time())
                else:
                    zf.writestr(name, text)
        tmp.replace(out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if not quiet and total:
        print("\r" + " " * 66 + "\r", end="", flush=True)
    _say(quiet, f"  created  : {out}  ({human(out.stat().st_size)})")
    return out


def _report_left_out(root: Path, left_out, has_start_script: bool, shown: int = 5):
    """List the biggest folders left behind, so the saving is visible."""
    sizes = sorted(
        ((rel, size, done) for (_, size, done), rel in
         zip(measure([root / rel for rel in left_out]), left_out)),
        key=lambda item: -item[1],
    )
    width = min(max(len(rel.as_posix()) for rel, _, _ in sizes[:shown]), 40)
    label = "  left out :"
    for rel, size, done in sizes[:shown]:
        print(f"{label} {rel.as_posix():<{width}}  {human(size)}{'' if done else '+'}")
        label = " " * len(label)
    if len(sizes) > shown:
        rest = sum(size for _, size, _ in sizes[shown:])
        print(f"{label} and {len(sizes) - shown} more ({human(rest)})")
    if has_start_script:
        print(f"{label} (start.bat / start.sh put these back on the other laptop)")
    else:
        print(f"{label} (the other laptop has to install these again)")


def _describe(apps, quiet: bool) -> None:
    """What start.bat will do, one line per app."""
    if not apps:
        _say(quiet, "  start    : no start.bat (nothing beam knows how to set up)")
        return
    width = max(len(app.folder) for app in apps)
    label = "  start    :"
    for app in apps:
        where = f"{app.folder or '.':<{width}}  " if width else ""
        how = app.what or ("set up only" if app.setup_bat else "open it in its editor")
        _say(quiet, f"{label} {where}{app.stack} -> {how}")
        label = " " * len(label)
    for app in apps:
        if app.requires:
            where = f"{app.folder}: " if app.folder else ""
            _say(quiet, f"  requires : {where}{', '.join(app.requires)}")


def _is_shell_script(name: str) -> bool:
    return name in ("gradlew", "mvnw") or name.endswith(".sh")


def _write_script(zf: zipfile.ZipFile, name: str, data: bytes, mtime: float) -> None:
    """A shell script, marked executable and with Unix line endings.

    Sent from Windows, gradlew and friends lose their executable bit and
    often gain CRLF line endings from git, and then refuse to run on a Mac.
    The mode is recorded the Unix way, so unzip tools and beam restore it.
    """
    info = zipfile.ZipInfo(name, date_time=time.localtime(mtime)[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix: tells unzip tools to read the mode below
    info.external_attr = 0o100755 << 16
    zf.writestr(info, data.replace(b"\r\n", b"\n"))
