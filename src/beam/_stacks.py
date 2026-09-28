"""What each language and framework leaves behind that the other laptop rebuilds.

Dependencies, virtual environments and build output are the heavy part of most
projects, and none of it needs to travel: start.bat / start.sh download or
build it again on the other laptop. Nobody has to write an ignore file for
this; the rules below are built in.

There are three kinds of rule, from safest to most careful:

* a folder whose *name* is only ever used by a tool (``node_modules``) is left
  out wherever it is;
* a folder whose *contents* give it away (a venv has ``pyvenv.cfg``) is left
  out whatever it is called;
* a folder with a common name (``target``, ``build``, ``bin``, ``vendor``) is
  left out only when the file that proves what it is sits next to it -
  ``target/`` beside a ``Cargo.toml``, never a ``target/`` on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Folders only ever made by a tool, rebuilt or reinstalled on the other laptop.
# They are skipped by name wherever they are, and listed as "left out".
REINSTALLED = (
    "node_modules",
    "__pypackages__",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    ".eggs",
    ".gradle",
    ".dart_tool",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".angular",
    ".turbo",
    ".parcel-cache",
    ".expo",
    ".vs",
    ".stack-work",
    "dist-newstyle",
    ".zig-cache",
    "zig-cache",
    ".elixir_ls",
    ".terraform",
    ".pnpm-store",
)

# Of those, the ones holding packages rather than caches: --with-deps keeps
# them, for a receiver with no internet.
DEPENDENCY_NAMES = frozenset({"node_modules", "__pypackages__"})

# The standard mark of a cache folder (https://bford.info/cachedir/); Cargo's
# target/, pytest, ruff and mypy all write one.
CACHE_TAG = "CACHEDIR.TAG"
CACHE_SIGNATURE = b"Signature: 8a477f597d28d172789f06886806bc55"


def is_rebuilt_folder(path, filenames, dirnames) -> bool:
    """True for a venv, conda env, CMake build folder or tagged cache.

    ``filenames`` and ``dirnames`` are the folder's own listing, as os.walk
    gives it, so this costs nothing unless a cache tag has to be read.
    """
    if "pyvenv.cfg" in filenames:  # python -m venv, virtualenv, uv
        return True
    if "conda-meta" in dirnames:  # a conda environment
        return True
    if "CMakeCache.txt" in filenames:  # a CMake build folder, whatever its name
        return True
    if CACHE_TAG in filenames:
        try:
            with open(os.path.join(path, CACHE_TAG), "rb") as fh:
                return fh.read(len(CACHE_SIGNATURE)) == CACHE_SIGNATURE
        except OSError:
            return False
    return False


@dataclass(frozen=True)
class Beside:
    """Paths left out only when one of ``markers`` is in the same folder."""

    markers: tuple  # file names, "*.ext" patterns, or "sub/file" paths
    skip: tuple  # folders or files next to the marker; "vendor/bundle" works too
    deps: bool = False  # packages, not build output: --with-deps keeps them


UNITY = "ProjectSettings/ProjectVersion.txt"

BESIDE = (
    Beside(("Cargo.toml", "pom.xml"), ("target",)),
    Beside(
        ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"),
        ("build", ".cxx", ".externalNativeBuild", "local.properties"),
    ),
    Beside(("*.csproj", "*.fsproj", "*.vbproj"), ("bin", "obj")),
    Beside(("composer.json",), ("vendor",), deps=True),
    Beside(("Gemfile",), ("vendor/bundle",), deps=True),
    Beside(("mix.exs",), ("deps",), deps=True),
    Beside(("mix.exs",), ("_build",)),
    Beside(
        ("pubspec.yaml",),
        (
            "build",
            ".flutter-plugins",
            ".flutter-plugins-dependencies",
            "ios/Flutter/Generated.xcconfig",
            "ios/Flutter/flutter_export_environment.sh",
        ),
    ),
    Beside(("Podfile",), ("Pods",), deps=True),
    Beside(("Package.swift",), (".build",)),
    Beside(
        ("*.uproject", "*.uplugin"),
        ("Binaries", "Intermediate", "Saved", "DerivedDataCache"),
    ),
    Beside(("project.godot",), (".godot", ".import")),
    Beside(
        (UNITY,),
        ("Library", "Temp", "Obj", "Logs", "UserSettings", "MemoryCaptures"),
    ),
)


def _has_marker(marker: str, path, filenames, dirnames) -> bool:
    if marker.startswith("*."):
        return any(name.endswith(marker[1:]) for name in filenames)
    if "/" in marker:
        first = marker.split("/", 1)[0]
        return first in dirnames and os.path.isfile(os.path.join(path, marker))
    return marker in filenames


def beside(path, filenames, dirnames, with_deps: bool = False) -> set:
    """Lower-case paths, relative to ``path``, that its marker files rule out."""
    out = set()
    for rule in BESIDE:
        if with_deps and rule.deps:
            continue
        if any(_has_marker(m, path, filenames, dirnames) for m in rule.markers):
            out.update(s.lower() for s in rule.skip)
    return out


def is_secret(name: str) -> bool:
    """.env and .env.local hold secrets; .env.example and friends do not."""
    if name == ".env":
        return True
    if not name.startswith(".env."):
        return False
    return name.rsplit(".", 1)[-1].lower() not in {
        "example", "sample", "template", "dist", "defaults",
    }
