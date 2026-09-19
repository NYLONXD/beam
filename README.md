# beam

Move a project folder from one laptop to another over the local network.
Standard library only, no runtime dependencies.

## Install

```bash
pip install beam-lan
```

Or from a checkout, while you are still changing the code:

```bash
pip install -e ".[dev]"
```

## Use it

On the laptop that has the project:

```bash
beam send ~/thesis
```

It prints a short one-time code. Send it to the other laptop however you
like (chat, email, read it out), then run there:

```bash
beam recv ab3f9c
```

The receiver finds the sender on the local network by itself, so no IP
address is needed. Both machines must be on the same network. If discovery is
blocked (some office or guest Wi-Fi drops broadcasts), pass the address the
sender prints: `beam recv ab3f9c --host 192.168.1.42:8765`.

`python -m beam` works too.

## As a library

```python
import beam

beam.send_project("~/thesis", code="ab3f9c")               # laptop A
beam.receive_project(code="ab3f9c", out="~/work")         # laptop B
```

`send_project` accepts `port=0` to bind a free port, and an `on_ready`
callback that receives the bound host, port and code once it is listening.

## What it does

- Skips `.venv`, `__pycache__`, `.git`, `node_modules` and similar by default.
  Copying a virtualenv between machines mostly produces broken paths; rebuild
  it from `requirements.txt` instead.
- Hashes every file before sending and re-hashes after extraction, so
  corruption is reported instead of discovered weeks later.
- Streams a gzipped tar straight through the socket, so no temporary archive
  is written on either side.
- `--max-size 200` skips files above 200 MB. A `.beamignore` file in the
  project root adds per-project glob patterns, one per line.

## Options

| Flag | Effect |
| --- | --- |
| `--exclude GLOB` | extra pattern to skip, repeatable |
| `--all` | ignore the default exclude list |
| `--max-size MB` | skip files larger than this |
| `--no-hash` | size-check only, faster on large datasets |
| `--fast` | lower compression, starts sooner |
| `--host IP[:PORT]` | connect directly instead of searching the network |
| `--wait SEC` | how long the receiver searches (default 15) |
| `--out DIR` | where the receiver writes the folder |
| `--name NAME` | rename the folder on arrival |
| `--force` | write into a non-empty destination |

## Limits

The connection is plain TCP with no encryption. The code stops casual grabs on
a shared network; it is not security. For repeated syncing rather than a
one-time move, `rsync -av --exclude=.venv` or a private Git remote is a better
fit.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
python -m build
```

## License

MIT
