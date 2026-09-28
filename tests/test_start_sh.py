"""Tests: start.sh and other shell scripts survive the trip from Windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from test_detect import PROJECTS
from test_skip_rules import build

import beam
from beam.transfer import _safe_unzip


def find_bash():
    """A real bash: on Windows, Git's - not the WSL launcher in System32."""
    if os.name != "nt":
        return shutil.which("bash")
    for folder in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                   os.environ.get("ProgramW6432", r"C:\Program Files")):
        candidate = Path(folder) / "Git" / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


BASH = find_bash()


def mode_of(info: zipfile.ZipInfo) -> int:
    return info.external_attr >> 16


def test_start_sh_is_executable_with_unix_line_endings(tmp_path):
    project = build(tmp_path / "proj", {"main.py": "print(1)\n"})
    with zipfile.ZipFile(beam.pack(project, quiet=True)) as zf:
        info = zf.getinfo("start.sh")
        text = zf.read("start.sh")
    assert info.create_system == 3 and mode_of(info) == 0o100755
    assert b"\r" not in text and text.startswith(b"#!/usr/bin/env bash\n")


def test_wrapper_scripts_from_windows_are_fixed_on_the_way(tmp_path):
    project = build(tmp_path / "proj", {
        "pom.xml": "<project/>",
        "mvnw": b"#!/bin/sh\r\necho hi\r\n",
        "scripts/deploy.sh": b"echo deploy\r\n",
        "run.bat": b"@echo off\r\necho hi\r\n",
    })
    with zipfile.ZipFile(beam.pack(project, quiet=True)) as zf:
        assert zf.read("mvnw") == b"#!/bin/sh\necho hi\n"
        assert zf.read("scripts/deploy.sh") == b"echo deploy\n"
        assert mode_of(zf.getinfo("mvnw")) == 0o100755
        assert zf.read("run.bat") == b"@echo off\r\necho hi\r\n"  # left alone


@pytest.mark.skipif(os.name == "nt", reason="no executable bit on Windows")
def test_unzipping_restores_the_run_permission(tmp_path):
    project = build(tmp_path / "proj", {"main.py": "print(1)\n", "gradlew": "x"})
    zip_path = beam.pack(project, quiet=True)
    _safe_unzip(zip_path, tmp_path / "out")
    assert os.access(tmp_path / "out" / "start.sh", os.X_OK)
    assert os.access(tmp_path / "out" / "gradlew", os.X_OK)
    assert not os.access(tmp_path / "out" / "main.py", os.X_OK)


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("files", [p[0] for p in PROJECTS.values()], ids=list(PROJECTS))
def test_every_start_sh_is_valid_bash(tmp_path, files):
    project = build(tmp_path / "proj", files)
    with zipfile.ZipFile(beam.pack(project, quiet=True)) as zf:
        script = tmp_path / "start.sh"
        script.write_bytes(zf.read("start.sh"))
    out = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
