# beam

Zip a project and send it to anyone, anywhere, from Python. The zip includes
a `start.bat`: the other person double-clicks it and the project is set up
and running, with no manual installs.

```python
import beam

beam.send("D:/projects/my_app", exclude=[".mp4", ".log"])
# code : tqWFGE-RX2oLb5xjRDX8d0M
```

The other person, on any network, any time in the next 3 days:

```python
import beam

beam.receive("tqWFGE-RX2oLb5xjRDX8d0M")
```

Python 3.9+. Works on Windows, macOS and Linux (`start.bat` is for Windows).

## Install

On both laptops:

```bash
pip install beam-lan
```

## Send a project

```python
import beam

code = beam.send(
    "D:/projects/my_app",        # the project folder
    exclude=[".mp4", ".log"],    # file types to leave out (optional)
    main="app.py",               # what start.bat runs (optional, auto-detected)
)
```

It zips the folder, encrypts it, uploads it, and prints a code. Send the code
to the other person however you like: WhatsApp, email, chat. You can close
your laptop straight after; the other person can download it any time within
3 days.

To send a file you already have, such as a zip or a PDF, pass its path instead
of a folder. It is sent as it is.

## Receive it

```python
import beam

beam.receive("tqWFGE-RX2oLb5xjRDX8d0M")                      # saves my_app.zip here
beam.receive("tqWFGE-RX2oLb5xjRDX8d0M", out="D:/Downloads")  # or somewhere else
beam.receive("tqWFGE-RX2oLb5xjRDX8d0M", extract=True)        # also unzip it
```

An existing `my_app.zip` is never overwritten: the new one becomes
`my_app (1).zip` unless you pass `overwrite=True`.

## Same Wi-Fi, direct

For big folders on the same network, skip the internet:

```python
beam.send("D:/projects/my_app", lan=True)   # prints a short code, e.g. ab3f9c
beam.receive("ab3f9c")                       # on the other laptop, same Wi-Fi
```

This mode has no size limit and uploads nothing, and the receiver finds the
sender by itself. Both laptops must be on at the same time, and `send` waits
until the other laptop has the file.

## Only make the zip

```python
beam.pack("D:/projects/my_app")                         # -> D:/projects/my_app.zip
beam.pack("D:/projects/my_app", output="D:/share/app.zip", exclude=[".csv"])
```

## What goes in the zip

- **Your files**, minus things that should not travel: `.venv`, `venv`,
  `__pycache__`, `.git`, `node_modules`, `.env` (secrets) and similar caches.
  Virtual environments do not work when copied to another computer; `start.bat`
  builds a fresh one instead.
- **Your `exclude` list.** `".mp4"`, `"mp4"` and `"*.mp4"` all skip mp4 files.
  A plain name such as `"data"` skips a file or folder with that name. A
  `.beamignore` file in the project adds more patterns, one per line.
- **`start.bat`**, which does the setup and starts the project.
- **`requirements.txt`**. Your own is used if the project has one. Otherwise
  beam reads the project's `import` lines and writes one (for example
  `import cv2` becomes `opencv-python`). Pass `requirements=["flask", "numpy"]`
  to set the list yourself.

## What start.bat does

| Project | start.bat |
| --- | --- |
| Python (`main.py`, `app.py`, `run.py`, ... or `main=`) | creates `.venv`, installs `requirements.txt`, runs the script |
| Streamlit app | same setup, then `streamlit run app.py` |
| Django (`manage.py`) | same setup, then `manage.py runserver` |
| Node (`package.json`) | `npm install`, then `npm start` |
| Website (`index.html`) | opens it in the browser |

The setup runs once. After that, `start.bat` goes straight to starting the
project. If Python or Node.js is missing, it says where to download it. To
start over, delete the `.venv` folder.

Turn it off with `start_script=False`.

## How the internet transfer works

1. The zip is encrypted on your laptop with AES-256-GCM, using a random key.
2. The encrypted file is uploaded to a free temporary file host:
   [temp.sh](https://temp.sh) (up to 4 GB, kept 3 days), or
   [uguu.se](https://uguu.se) (up to 128 MB, kept 3 hours) if temp.sh is down.
3. The code is `<where it is>-<the key>`. The key never goes to the host, so
   the host only ever stores unreadable data.
4. `receive` downloads it, checks it has not been changed, and decrypts it.

Anyone who has the whole code can download the file, so send it only to the
person it is for.

## All options

`beam.send(path, ...)` and `beam.pack(path, ...)`

| Option | Default | Effect |
| --- | --- | --- |
| `exclude` | none | file types or names to leave out |
| `main` | auto | script that `start.bat` runs |
| `start_script` | `True` | add `start.bat` (and `requirements.txt` if needed) |
| `requirements` | auto | pip packages `start.bat` installs |
| `use_default_excludes` | `True` | skip `.venv`, `.git`, `__pycache__`, ... |
| `max_size_mb` | none | skip files bigger than this |
| `quiet` | `False` | print nothing |

`beam.pack` only: `output` sets where the zip goes (default `<folder>.zip`
next to the folder).

`beam.send` only: `lan=True` sends directly over the same Wi-Fi. With
`lan=True`, `code` picks your own code, `timeout` sets how many seconds to
wait for the other laptop, and `port` sets the network port (default 8765,
another free port is used if it is taken).

`beam.receive(code, ...)`: `out` sets the folder to save into, `extract` also
unzips it, and `overwrite` replaces existing files. For LAN codes, `host="IP"`
or `host="IP:port"` skips the search and `wait` sets how many seconds to
search (default 15).

Errors raise `beam.BeamError`.

## Troubleshooting

**`nothing found for this code ... expired`**: the file is older than 3 days
(3 hours if it went to uguu.se), or the code was not copied completely.
Send it again.

**`could not decrypt: wrong code`**: part of the code is missing or mistyped.

**`upload failed on every host`**: check the internet connection. If the hosts
are down, use `beam.pack` and share the zip another way.

**LAN mode: `no sender with code ... found`**

- Check both laptops are on the same Wi-Fi. Guest and office networks often
  block devices from seeing each other.
- On Windows, click **Allow** when the firewall asks about Python on the
  sending laptop.
- Use the address the sender prints: `beam.receive("ab3f9c", host="192.168.1.42:8765")`.

## Limits

- Internet mode depends on free third-party hosts. They can change their rules
  or go offline; beam falls back from temp.sh to uguu.se, but it cannot promise
  either is up. Files over 4 GB need `lan=True`.
- `start.bat` is for Windows. On macOS or Linux, unzip the file and install
  `requirements.txt` yourself.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
python -m build
```

## License

MIT
