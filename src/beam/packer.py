"""Zipping a project folder, with a start.bat that sets it up and runs it."""

from __future__ import annotations

import zipfile
from pathlib import Path

from ._files import DEFAULT_EXCLUDES, human, normalize_excludes, read_ignore_file, walk
from ._protocol import BeamError
from ._starter import detect, start_bat


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
    """
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a folder")

    out = Path(output).expanduser() if output else root.parent / f"{root.name}.zip"
    if out.suffix.lower() != ".zip":
        out = out.with_name(out.name + ".zip")
    out = out.resolve()

    patterns = list(DEFAULT_EXCLUDES) if use_default_excludes else []
    patterns += normalize_excludes(exclude)
    patterns += read_ignore_file(root)

    max_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb else None
    files, skipped = walk(root, patterns, max_bytes)
    files = [f for f in files if f[0].resolve() != out]
    if not files:
        raise BeamError(f"nothing to pack in {root} after applying excludes")

    extras = {}
    info = None
    if start_script:
        info = detect(files, main=main, requirements=requirements)
        if info["kind"] is not None:
            extras["start.bat"] = start_bat(root.name, info)
            if info["requirements"]:  # given, or guessed for lack of a file
                extras["requirements.txt"] = "\n".join(info["requirements"]) + "\n"
        files = [f for f in files if f[1].as_posix() not in extras]

    total = sum(size for _, _, size in files)
    _say(quiet, f"  packing  : {root.name}  ({len(files)} files, {human(total)})")
    for rel, size in skipped:
        _say(quiet, f"  skipped  : {rel} ({human(size)}, over max_size_mb)")
    if info is not None:
        if info["kind"] is None:
            _say(quiet, "  start.bat: not added (no Python, Node or HTML files found)")
        else:
            what = info["entry"] or "setup only, no main script found"
            _say(quiet, f"  start.bat: {info['kind']} -> {what}")
        if "requirements.txt" in extras:
            _say(quiet, f"  requires : {', '.join(info['requirements'])}")

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    done = 0
    try:
        with zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel
        ) as zf:
            for abs_path, rel, size in files:
                zf.write(abs_path, rel.as_posix())
                done += size
                if not quiet and total:
                    pct = 100 * done / total
                    print(f"\r  {pct:5.1f}%  {str(rel)[:56]:<56}", end="", flush=True)
            for name, text in extras.items():
                zf.writestr(name, text)
        tmp.replace(out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if not quiet and total:
        print("\r" + " " * 66 + "\r", end="", flush=True)
    _say(quiet, f"  created  : {out}  ({human(out.stat().st_size)})")
    return out
