# beam

Move a project folder from one laptop to another over the local network.
Pure Python standard library, no runtime dependencies, works on Windows,
macOS and Linux (Python 3.9+).

```bash
# laptop A
beam send ~/thesis

# laptop B
beam recv ab3f9c
```

## Install

Both laptops need it:

```bash
pip install beam-lan
```

`pipx install beam-lan` also works and keeps it out of your other environments.

If your shell says `beam` is not a recognised command, pip put the script in a
folder that is not on your `PATH` (pip prints a warning naming it). Either add
that folder to `PATH`, or run `python -m beam` instead of `beam`.

## Use it

On the laptop that has the project:

```bash
beam send ~/thesis
```

It prints a short one-time code. Send it to the other laptop however you like
(chat, email, read it out), then run there:

```bash
beam recv ab3f9c
```

The receiver finds the sender on the local network by itself, so no IP address
is needed. Both machines must be on the same network.

## As a library

```python
import beam

beam.send_project("~/thesis", code="ab3f9c")          # laptop A
beam.receive_project(code="ab3f9c", out="~/work")     # laptop B
```

`receive_project` also takes `host=` to skip discovery. `send_project` accepts
`port=0` to bind a free port, and an `on_ready` callback that receives the bound
host, port and code once it is listening. `beam.find_sender(code)` returns the
`(host, port, name)` of a sender without transferring anything.

## What it does

- Skips `.venv`, `__pycache__`, `.git`, `node_modules` and similar by default.
  Copying a virtualenv between machines mostly produces broken paths; rebuild
  it from `requirements.txt` instead.
- Hashes every file before sending and re-hashes after extraction, so
  corruption is reported instead of discovered weeks later.
- Streams a gzipped tar straight through the socket, so no temporary archive
  is written on either side.
- A `.beamignore` file in the project root adds per-project glob patterns, one
  per line (`#` starts a comment).

## Options

`beam send PATH`

| Flag | Effect |
| --- | --- |
| `--code CODE` | choose the code instead of a random one |
| `--port PORT` | TCP port to listen on (default 8765) |
| `--exclude GLOB` | extra pattern to skip, repeatable |
| `--all` | ignore the default exclude list |
| `--max-size MB` | skip files larger than this |
| `--no-hash` | size-check only, faster on large datasets |
| `--fast` | lower compression, starts sooner |

`beam recv CODE`

| Flag | Effect |
| --- | --- |
| `--host IP[:PORT]` | connect directly instead of searching the network |
| `--wait SEC` | how long to search for the sender (default 15) |
| `--out DIR` | where to write the folder (default: current directory) |
| `--name NAME` | rename the folder on arrival |
| `--force` | write into a non-empty destination |
| `--no-verify` | skip the checksum pass after extraction |

## Troubleshooting

**`no sender with code ... found`**

- Check both laptops are on the same Wi-Fi or wired network. Guest and office
  networks often isolate devices from each other or drop broadcasts.
- On Windows, allow Python through the firewall when it asks on the sending
  laptop. If you dismissed the prompt, allow `python.exe` for private networks
  in *Windows Defender Firewall → Allow an app*.
- Skip discovery and connect directly using the address the sender prints:
  `beam recv ab3f9c --host 192.168.1.42:8765`.

Discovery uses UDP port 8766 and the transfer uses TCP port 8765.

## Limits

The connection is plain TCP with no encryption. The code stops casual grabs on
a shared network; it is not security. Do not use it on networks you do not
trust. For repeated syncing rather than a one-time move, `rsync -av
--exclude=.venv` or a private Git remote is a better fit.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
python -m build
```

## License

MIT
