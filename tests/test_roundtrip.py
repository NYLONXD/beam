"""End-to-end tests: a sender thread and a receiver in the main thread."""

from __future__ import annotations

import threading

import pytest

import beam
from beam._files import excluded, human
from beam._protocol import BeamError


def make_project(root):
    (root / "src").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "__pycache__").mkdir()
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / "src" / "train.py").write_text("print('hello')\n")
    (root / "data" / "sample.bin").write_bytes(bytes(range(256)) * 400)
    (root / "README.md").write_text("notes\n")
    (root / "__pycache__" / "x.pyc").write_text("junk\n")
    (root / ".venv" / "lib" / "y.txt").write_text("junk\n")
    return root


def serve(project, code, ready, **kwargs):
    """Start send_project on a background thread; return the bound port."""
    info = {}

    def on_ready(details):
        info.update(details)
        ready.set()

    thread = threading.Thread(
        target=beam.send_project,
        args=(project,),
        kwargs=dict(port=0, code=code, quiet=True, on_ready=on_ready, **kwargs),
        daemon=True,
    )
    thread.start()
    assert ready.wait(10), "sender never started listening"
    return info, thread


def test_roundtrip_copies_files_and_applies_excludes(tmp_path):
    project = make_project(tmp_path / "thesis")
    ready = threading.Event()
    info, thread = serve(project, "test123", ready)

    dest = beam.receive_project(
        "127.0.0.1", port=info["port"], code="test123",
        out=tmp_path / "out", quiet=True,
    )
    thread.join(timeout=10)

    assert (dest / "src" / "train.py").read_text() == "print('hello')\n"
    assert (dest / "data" / "sample.bin").read_bytes() == (
        project / "data" / "sample.bin"
    ).read_bytes()
    assert not (dest / "__pycache__").exists()
    assert not (dest / ".venv").exists()
    assert not (dest / ".beam-manifest.json").exists()
    assert info["files"] == 3


def test_receiver_finds_sender_by_code_alone(tmp_path):
    project = make_project(tmp_path / "thesis")
    ready = threading.Event()
    _, thread = serve(project, "disc42", ready, discovery_port=18766)

    host, port, name = beam.find_sender("disc42", timeout=5, udp_port=18766)
    assert name == "thesis"
    dest = beam.receive_project(
        host, port=port, code="disc42", out=tmp_path / "out", quiet=True
    )
    thread.join(timeout=10)
    assert (dest / "README.md").exists()


def test_discovery_ignores_other_codes(tmp_path):
    project = make_project(tmp_path / "thesis")
    ready = threading.Event()
    serve(project, "mine", ready, discovery_port=18767)

    with pytest.raises(BeamError, match="no sender"):
        beam.find_sender("theirs", timeout=1.5, udp_port=18767)


def test_wrong_code_is_refused(tmp_path):
    project = make_project(tmp_path / "thesis")
    ready = threading.Event()
    info, _ = serve(project, "right", ready)

    with pytest.raises(BeamError, match="bad code"):
        beam.receive_project(
            "127.0.0.1", port=info["port"], code="wrong",
            out=tmp_path / "out", quiet=True,
        )

    # the sender stays up, so the correct code still works
    dest = beam.receive_project(
        "127.0.0.1", port=info["port"], code="right",
        out=tmp_path / "out", quiet=True,
    )
    assert (dest / "README.md").exists()


def test_max_size_skips_big_files(tmp_path):
    project = make_project(tmp_path / "thesis")
    ready = threading.Event()
    info, thread = serve(project, "c", ready, max_size_mb=0.05)

    dest = beam.receive_project(
        "127.0.0.1", port=info["port"], code="c",
        out=tmp_path / "out", quiet=True,
    )
    thread.join(timeout=10)

    assert not (dest / "data" / "sample.bin").exists()
    assert (dest / "src" / "train.py").exists()


def test_refuses_non_empty_destination(tmp_path):
    project = make_project(tmp_path / "thesis")
    ready = threading.Event()
    info, _ = serve(project, "c", ready)

    existing = tmp_path / "out" / "thesis"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("mine\n")

    with pytest.raises(BeamError, match="not empty"):
        beam.receive_project(
            "127.0.0.1", port=info["port"], code="c",
            out=tmp_path / "out", quiet=True,
        )


def test_empty_project_is_rejected(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BeamError, match="nothing to send"):
        beam.send_project(empty, port=0, quiet=True)


def test_missing_folder_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        beam.send_project(tmp_path / "nope", port=0, quiet=True)


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
def test_exclude_matching(path, expected):
    from pathlib import Path

    assert excluded(Path(path), beam.DEFAULT_EXCLUDES) is expected


def test_human_sizes():
    assert human(512) == "512 B"
    assert human(2048) == "2.0 KB"
