"""Tests: packing, start.bat generation, and send/receive over localhost."""

from __future__ import annotations

import threading
import zipfile
from pathlib import Path

import pytest

import beam
from beam._files import excluded, human, normalize_excludes
from beam._protocol import BeamError


def make_project(root):
    (root / "src").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "__pycache__").mkdir()
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / "main.py").write_text(
        "import os\nimport numpy\nimport cv2\nfrom src import helpers\n"
    )
    (root / "src" / "helpers.py").write_text("from PIL import Image\n")
    (root / "data" / "sample.bin").write_bytes(bytes(range(256)) * 400)
    (root / "data" / "clip.mp4").write_bytes(b"video")
    (root / "run.log").write_text("log\n")
    (root / "README.md").write_text("notes\n")
    (root / "__pycache__" / "x.pyc").write_text("junk\n")
    (root / ".venv" / "lib" / "y.txt").write_text("junk\n")
    return root


def names_in(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


def read_in(zip_path, name):
    with zipfile.ZipFile(zip_path) as zf:
        return zf.read(name).decode()


# --- pack --------------------------------------------------------------------


def test_pack_excludes_types_and_defaults(tmp_path):
    project = make_project(tmp_path / "app")
    out = beam.pack(project, exclude=[".mp4", "log"], quiet=True)

    assert out == tmp_path / "app.zip"
    names = names_in(out)
    assert {"main.py", "src/helpers.py", "data/sample.bin", "README.md"} <= names
    assert "data/clip.mp4" not in names
    assert "run.log" not in names
    assert not any(n.startswith((".venv", "__pycache__")) for n in names)


def test_pack_adds_start_bat_and_guessed_requirements(tmp_path):
    project = make_project(tmp_path / "app")
    out = beam.pack(project, output=tmp_path / "dist" / "bundle", quiet=True)

    assert out.name == "bundle.zip"
    bat = read_in(out, "start.bat")
    assert "\r\n" in bat and "\n" not in bat.replace("\r\n", "")
    assert '"%VPY%" "main.py"' in bat
    assert "-m venv .venv" in bat
    reqs = read_in(out, "requirements.txt").split()
    assert reqs == ["numpy", "opencv-python", "Pillow"]  # not os, not src


def test_existing_requirements_file_is_kept(tmp_path):
    project = make_project(tmp_path / "app")
    (project / "requirements.txt").write_bytes(b"numpy==1.26\n")
    out = beam.pack(project, quiet=True)
    assert read_in(out, "requirements.txt") == "numpy==1.26\n"


def test_main_and_requirements_can_be_given(tmp_path):
    project = make_project(tmp_path / "app")
    (project / "src" / "server.py").write_text("print('hi')\n")
    out = beam.pack(project, main="src/server.py", requirements=["flask"], quiet=True)
    assert '"%VPY%" "src\\server.py"' in read_in(out, "start.bat")
    assert read_in(out, "requirements.txt") == "flask\n"


def test_missing_main_is_an_error(tmp_path):
    project = make_project(tmp_path / "app")
    with pytest.raises(FileNotFoundError):
        beam.pack(project, main="nope.py", quiet=True)


def test_streamlit_node_and_static_projects(tmp_path):
    st = tmp_path / "dash"
    st.mkdir()
    (st / "app.py").write_text("import streamlit as st\n")
    assert "-m streamlit run" in read_in(beam.pack(st, quiet=True), "start.bat")

    node = tmp_path / "web"
    node.mkdir()
    (node / "package.json").write_text("{}")
    bat = read_in(beam.pack(node, quiet=True), "start.bat")
    assert "npm install" in bat and "npm start" in bat

    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>hi</h1>")
    assert 'start "" "index.html"' in read_in(beam.pack(site, quiet=True), "start.bat")


def test_no_start_bat_when_disabled(tmp_path):
    project = make_project(tmp_path / "app")
    out = beam.pack(project, start_script=False, quiet=True)
    assert "start.bat" not in names_in(out)
    assert "requirements.txt" not in names_in(out)


def test_empty_project_is_rejected(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BeamError, match="nothing to pack"):
        beam.pack(empty, quiet=True)


def test_missing_folder_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        beam.pack(tmp_path / "nope", quiet=True)


# --- send / receive ----------------------------------------------------------


def serve(path, code, **kwargs):
    """Start beam.send on a background thread; return its details."""
    info, ready = {}, threading.Event()

    def on_ready(details):
        info.update(details)
        ready.set()

    kwargs.setdefault("discoverable", False)
    thread = threading.Thread(
        target=beam.send,
        args=(path,),
        kwargs=dict(
            lan=True, port=0, code=code, quiet=True, on_ready=on_ready, **kwargs
        ),
        daemon=True,
    )
    thread.start()
    assert ready.wait(10), "sender never started listening"
    return info, thread


def test_send_and_receive_zip(tmp_path):
    project = make_project(tmp_path / "app")
    info, thread = serve(project, "abc123", exclude=[".mp4"])

    got = beam.receive(
        "abc123", host=f"127.0.0.1:{info['port']}", out=tmp_path / "inbox", quiet=True
    )
    thread.join(timeout=10)

    assert got == tmp_path / "inbox" / "app.zip"
    names = names_in(got)
    assert "start.bat" in names and "main.py" in names
    assert "data/clip.mp4" not in names


def test_receive_by_code_alone(tmp_path):
    project = make_project(tmp_path / "app")
    _, thread = serve(project, "disc42", discoverable=True, discovery_port=18766)

    got = beam.receive(
        "disc42", out=tmp_path / "inbox", quiet=True, wait=5, discovery_port=18766
    )
    thread.join(timeout=10)
    assert got.name == "app.zip"


def test_discovery_ignores_other_codes(tmp_path):
    project = make_project(tmp_path / "app")
    serve(project, "mine", discoverable=True, discovery_port=18767)
    with pytest.raises(BeamError, match="no sender"):
        beam.find_sender("theirs", timeout=1.5, udp_port=18767)


def test_extract_and_no_overwrite(tmp_path):
    project = make_project(tmp_path / "app")
    inbox = tmp_path / "inbox"
    for _ in range(2):
        info, thread = serve(project, "x1")
        folder = beam.receive(
            "x1",
            host="127.0.0.1",
            port=info["port"],
            out=inbox,
            extract=True,
            quiet=True,
        )
        thread.join(timeout=10)

    assert (inbox / "app" / "start.bat").exists()
    assert folder == inbox / "app (1)"
    assert (folder / "src" / "helpers.py").exists()
    assert (inbox / "app.zip").exists() and (inbox / "app (1).zip").exists()


def test_single_file_is_sent_as_is(tmp_path):
    zip_path = beam.pack(make_project(tmp_path / "app"), quiet=True)
    info, thread = serve(zip_path, "f")
    got = beam.receive(
        "f", host="127.0.0.1", port=info["port"], out=tmp_path / "inbox", quiet=True
    )
    thread.join(timeout=10)
    assert got.read_bytes() == zip_path.read_bytes()


def test_wrong_code_is_refused(tmp_path):
    project = make_project(tmp_path / "app")
    info, _ = serve(project, "right")

    with pytest.raises(BeamError, match="wrong code"):
        beam.receive(
            "wrong",
            host="127.0.0.1",
            port=info["port"],
            out=tmp_path / "inbox",
            quiet=True,
        )

    # the sender stays up, so the right code still works
    got = beam.receive(
        "right", host="127.0.0.1", port=info["port"], out=tmp_path / "inbox", quiet=True
    )
    assert got.exists()


def test_busy_port_falls_back_to_a_free_one(tmp_path):
    import socket

    blocker = socket.socket()
    blocker.bind(("0.0.0.0", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    ready, details = threading.Event(), {}
    thread = threading.Thread(
        target=beam.send,
        args=(make_project(tmp_path / "app"),),
        kwargs=dict(
            lan=True,
            port=busy,
            code="p",
            quiet=True,
            discoverable=False,
            on_ready=lambda d: (details.update(d), ready.set()),
        ),
        daemon=True,
    )
    thread.start()
    assert ready.wait(10)
    try:
        assert details["port"] != busy
        got = beam.receive(
            "p",
            host="127.0.0.1",
            port=details["port"],
            out=tmp_path / "inbox",
            quiet=True,
        )
        assert got.exists()
    finally:
        blocker.close()


def test_send_timeout(tmp_path):
    project = make_project(tmp_path / "app")
    with pytest.raises(BeamError, match="nobody connected"):
        beam.send(
            project, lan=True, port=0, timeout=0.5, quiet=True, discoverable=False
        )


# --- helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("__pycache__/x.pyc", True),
        ("src/__pycache__/x.pyc", True),
        ("src/train.py", False),
        (".venv/lib/y.txt", True),
        ("data/notes.venv.txt", False),
    ],
)
def test_default_excludes(path, expected):
    assert excluded(Path(path), beam.DEFAULT_EXCLUDES) is expected


@pytest.mark.parametrize("given", [".mp4", "mp4", "*.mp4", [".MP4".lower()]])
def test_exclude_forms(given):
    patterns = normalize_excludes(given)
    assert excluded(Path("videos/a.mp4"), patterns)
    assert not excluded(Path("videos/a.mp3"), patterns)


def test_human_sizes():
    assert human(512) == "512 B"
    assert human(2048) == "2.0 KB"
