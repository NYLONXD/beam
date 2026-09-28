"""Walking the project tree, exclude rules, hashing."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import time
from pathlib import Path

from . import _stacks

HASH_CHUNK = 1 << 20

# Things you almost never want to carry between machines: .git is better
# cloned and caches are regenerable. Dependencies, virtualenvs and build
# output (node_modules, .venv, ...) are rebuilt on the other laptop by
# start.bat, so they stay behind too; see _stacks for the full rules.
DEFAULT_EXCLUDES = [
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".ipynb_checkpoints",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "*.egg-info",
    ".DS_Store",
    "Thumbs.db",
    *_stacks.REINSTALLED,
]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def normalize_excludes(exclude) -> list[str]:
    """Turn user-friendly excludes into glob patterns.

    ".mp4", "mp4" and "*.mp4" all skip mp4 files. A bare name like "data" or
    ".cache" also skips a file or folder with exactly that name.
    """
    if exclude is None:
        return []
    if isinstance(exclude, (str, os.PathLike)):
        exclude = [exclude]
    patterns = []
    for item in exclude:
        item = str(item).strip().replace("\\", "/")
        if not item:
            continue
        if any(ch in item for ch in "*?[/"):
            patterns.append(item)
        else:
            patterns.append(item)
            patterns.append("*." + item.lstrip("."))
    return patterns


def excluded(rel: Path, patterns) -> bool:
    text = rel.as_posix()
    for pat in patterns:
        pat = pat.rstrip("/")
        if fnmatch.fnmatch(rel.name, pat):
            return True
        if any(fnmatch.fnmatch(part, pat) for part in rel.parts):
            return True
        if fnmatch.fnmatch(text, pat):
            return True
    return False


def read_ignore_file(root: Path) -> list:
    """Read .beamignore if present. Simple glob patterns, one per line."""
    path = root / ".beamignore"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(HASH_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def walk(root: Path, patterns, max_bytes=None, rules: bool = True,
         with_deps: bool = False):
    """Return (kept, skipped, left_out).

    kept      [(abs_path, rel_path, size)]
    skipped   [(rel_path, size)] - bigger than max_bytes
    left_out  [rel_path] - folders the other laptop rebuilds or reinstalls:
              node_modules, venvs, target/ beside a Cargo.toml, ...

    ``rules`` applies the built-in rules in _stacks; ``with_deps`` keeps the
    dependency folders among them (node_modules, vendor, ...).
    """
    found, skipped, left_out = [], [], []
    deep = set()  # rule paths with a slash, like vendor/bundle, from root
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel_dir = here.relative_to(root)
        if rules and rel_dir.parts and _stacks.is_rebuilt_folder(
            here, filenames, dirnames
        ):
            left_out.append(rel_dir)
            dirnames[:] = []
            continue
        near = _stacks.beside(here, filenames, dirnames, with_deps) if rules else set()
        deep.update((rel_dir / p).as_posix().lower() for p in near if "/" in p)

        kept_dirs = []
        for d in sorted(dirnames):
            rel = rel_dir / d
            if excluded(rel, patterns):
                if d in _stacks.REINSTALLED:
                    left_out.append(rel)
                continue
            if _ruled_out(rel, near, deep):
                left_out.append(rel)
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for name in sorted(filenames):
            rel = rel_dir / name
            if excluded(rel, patterns) or _ruled_out(rel, near, deep):
                continue
            abs_path = here / name
            if abs_path.is_symlink() and not abs_path.exists():
                continue  # broken symlink
            try:
                size = abs_path.stat().st_size
            except OSError:
                continue
            if max_bytes is not None and size > max_bytes:
                skipped.append((rel, size))
                continue
            found.append((abs_path, rel, size))
    return found, skipped, left_out


def _ruled_out(rel: Path, near, deep) -> bool:
    return rel.name.lower() in near or rel.as_posix().lower() in deep


def measure(folders, budget: float = 2.0):
    """[(folder, bytes, complete)], giving up after ``budget`` seconds overall.

    A big node_modules has a lot of files; the size is only for the printout,
    so it is not worth making anyone wait long for it.
    """
    deadline = time.monotonic() + budget
    out = []
    for folder in folders:
        total, complete, todo = 0, True, [str(folder)]
        while todo:
            if time.monotonic() > deadline:
                complete = False
                break
            try:
                with os.scandir(todo.pop()) as entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink() or getattr(
                                entry, "is_junction", lambda: False
                            )():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                todo.append(entry.path)
                            else:
                                total += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
            except OSError:
                pass
        out.append((folder, total, complete))
    return out
