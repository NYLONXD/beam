"""Walking the project tree, exclude rules, hashing."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path

HASH_CHUNK = 1 << 20

# Things you almost never want to carry between machines: virtualenvs are
# platform-specific, caches are regenerable, .git is better cloned, .env
# usually holds secrets.
DEFAULT_EXCLUDES = [
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".venv",
    "venv",
    "env",
    ".env",
    ".ipynb_checkpoints",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "*.egg-info",
    "node_modules",
    ".DS_Store",
    "Thumbs.db",
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


def walk(root: Path, patterns, max_bytes=None):
    """Return ([(abs_path, rel_path, size)], [(rel_path, size)]) - kept, skipped."""
    found, skipped = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel_dir = here.relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if not excluded(rel_dir / d, patterns))
        for name in sorted(filenames):
            rel = rel_dir / name
            if excluded(rel, patterns):
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
    return found, skipped
