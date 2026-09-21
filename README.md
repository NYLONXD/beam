# beam

Send a whole project to someone else's laptop, wherever they are, from the
terminal. You upload and walk away; they pick it up whenever they like. The zip
includes a `start.bat`: they double-click it and the project is set up and
running, with no manual installs.

**Laptop A** — here, now:

```bash
beam send D:/projects/my_app
#   uploading to x0.at ...
#   code : xEGsg-gbDICMBKMQ69BmvI
```

Close the laptop. Send the code however you like — WhatsApp, email, a chat
message.

**Laptop B** — another city, tomorrow:

```bash
beam receive xEGsg-gbDICMBKMQ69BmvI
#   saved to .\my_app.zip
#   unzipped into .\my_app
```

That is the whole thing. The other person never writes a line of Python and
never has to be online at the same time as you: they `pip install beam-lan`
and type the code.

Python 3.9+. Works on Windows, macOS and Linux (`start.bat` is for Windows).

## Install

On both laptops:

```bash
pip install beam-lan
```

If your shell says `beam: command not found` afterwards, Python's scripts
folder is not on your `PATH`. Use `python -m beam` instead — it is the same
command:

```bash
python -m beam send D:/projects/my_app
python -m beam receive xEGsg-gbDICMBKMQ69BmvI
```

## Send a project

```bash
beam send D:/projects/my_app                      # the project folder
beam send D:/projects/my_app -x .mp4 -x .log      # leave file types out
beam send D:/projects/my_app --main app.py        # what start.bat runs
beam send report.pdf                              # a single file, sent as it is
```

It zips the folder, encrypts it, uploads it and prints a code. That is the end
of your part — the file waits on the host until the other person fetches it.

To send a file you already have, such as a zip or a PDF, pass its path instead
of a folder. It is sent as it is.

## Receive it

```bash
beam receive <code>                     # saves my_app.zip here and unzips it
beam receive <code> -o D:/Downloads     # somewhere else
beam receive <code> --no-extract        # keep the zip, do not unzip
```

An existing `my_app.zip` is never overwritten: the new one becomes
`my_app (1).zip` unless you pass `--overwrite`.

## How long it is kept

The encrypted zip goes to the first free host that will take it. Which one you
get depends on the size and on who is up; `beam send` says which it used and
how long the code is good for.

| Host | Up to | Kept for |
| --- | --- | --- |
| [x0.at](https://x0.at) | 225 MB | at least 30 days |
| [catbox.moe](https://catbox.moe) | 200 MB | until catbox removes it |
| [uguu.se](https://uguu.se) | 128 MB | 3 hours |
| [temp.sh](https://temp.sh) | 4 GB | 3 days |

If one is down or blocked, beam moves to the next. For anything over 8 MB it
pokes each host with a few bytes first, so a host having a bad day costs a
round trip rather than your whole upload.

These are free public services with no promises attached. If they are all
unreachable, `beam pack` makes the zip and you share it another way.

## Same network? Skip the upload

When both laptops are on one local network and both are on right now, `--lan`
hands the file straight across. Nothing is uploaded, no internet is needed, and
there is no size limit.

```bash
beam send D:/projects/my_app --lan     # prints a short code, e.g. ab3f9c
beam receive ab3f9c                    # on the other laptop
```

`beam send --lan` waits until the other laptop has the file, so both people
have to be at their machines. "Same network" means the same Wi-Fi, a cable into
the same router, or a phone hotspot they have both joined.

`beam receive` tells the two kinds of code apart by itself: `--lan` codes are
short and have no dash, so you never say which one you are using. If discovery
fails on a locked-down network, use the address the sender printed:

```bash
beam receive ab3f9c --host 192.168.1.42:8765
```

## Only make the zip

```bash
beam pack D:/projects/my_app
beam pack D:/projects/my_app -o D:/share/app.zip -x .csv
```

## What goes in the zip

- **Your files**, minus things that should not travel: `.venv`, `venv`,
  `__pycache__`, `.git`, `node_modules`, `.env` (secrets) and similar caches.
  Virtual environments do not work when copied to another computer; `start.bat`
  builds a fresh one instead.
- **What you excluded.** `".mp4"`, `"mp4"` and `"*.mp4"` all skip mp4 files.
  A plain name such as `"data"` skips a file or folder with that name. A
  `.beamignore` file in the project adds more patterns, one per line.
- **`start.bat`**, which does the setup and starts the project.
- **`requirements.txt`**. Your own is used if the project has one. Otherwise
  beam reads the project's `import` lines and writes one (for example
  `import cv2` becomes `opencv-python`). Pass `requirements=["flask", "numpy"]` to
  `beam.send` to set the list yourself.

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

Turn it off with `--no-start-script`.

## Who can read it

1. The zip is encrypted on your laptop with AES-256-GCM, using a random key.
2. Only the encrypted file is uploaded. The key never leaves your laptop.
3. The code is `<where it is>-<the key>`, so the host only ever stores
   unreadable bytes — it cannot see your file names or your code.
4. `receive` downloads it, checks it has not been altered, and decrypts it.

Anyone holding the whole code can download and decrypt the file, so send the
code only to the person it is for. Treat it like a password, not a link.

The free hosts are public: the encrypted file sits there until it expires (and
on catbox, until catbox removes it). If you need it gone on a schedule, use
`--lan` or `beam pack`.

## All options

### `beam send <folder>`

| Option | Effect |
| --- | --- |
| `-x`, `--exclude PATTERN` | file type or name to leave out; repeat it, or use commas |
| `--main SCRIPT` | script that `start.bat` runs (default: auto-detected) |
| `--no-start-script` | do not add `start.bat` / `requirements.txt` |
| `--max-size-mb MB` | leave out files bigger than this |
| `--lan` | hand it straight over on the local network instead of uploading |
| `--cloud` | upload — the default, so this only spells it out |
| `-q`, `--quiet` | print nothing |

`--lan` only: `--code CODE` picks the code yourself, `--port N` sets the port
(default 8765, another free one if taken), `--timeout SECONDS` gives up if
nobody connects.

### `beam receive <code>`

| Option | Effect |
| --- | --- |
| `-o`, `--out FOLDER` | where to save (default: here) |
| `--no-extract` | keep the `.zip`, do not unzip it |
| `--overwrite` | replace what is there instead of adding ` (1)` |
| `-q`, `--quiet` | print nothing |

`--lan` codes only: `--host IP[:PORT]` skips searching the network, and
`--wait SECONDS` sets how long to search (default 15).

### `beam pack <folder>`

Same as `beam send` minus the network options, plus `-o`, `--output ZIP`.

### From Python

The command line is a thin wrapper over `beam.send`, `beam.receive` and
`beam.pack`.

```python
import beam

code = beam.send("D:/projects/my_app", exclude=[".mp4"])   # uploads
beam.receive(code, extract=True)                           # on the other laptop

beam.send("D:/projects/my_app", lan=True)                  # local network
beam.pack("D:/projects/my_app")                            # only make the zip
```

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

`beam.send` only: `lan=True` sends over the local network instead of uploading.
With `lan=True`, `code` picks your own code, `timeout` sets how many seconds to
wait for the other laptop, and `port` sets the network port.

`beam.receive(code, ...)`: `out` sets the folder to save into, `extract` also
unzips it, and `overwrite` replaces existing files. For `lan` codes, `host="IP"`
or `host="IP:port"` skips the search and `wait` sets how many seconds to
search (default 15).

Errors raise `beam.BeamError`. The command prints them and exits with `1`.

## Troubleshooting

**`beam: command not found`**: Python's scripts folder is not on your `PATH`.
Use `python -m beam ...` instead, or add the folder pip named when it installed
the package.

**`upload failed on every host`**: every host was down, blocked, or too small
for the file. The message lists what each one said. Some of these hosts are
blocked in some countries and on some office networks — a different connection
often fixes it. Otherwise use `beam pack` and share the zip another way, or
`--lan` if you are both on one network.

**`nothing found for this code ... expired`**: the file outlived its host's
retention (see the table above), or the code was not copied completely. Send it
again.

**`could not decrypt: wrong code`**: part of the code is missing or mistyped.
Codes are case-sensitive.

**`no sender with code ... found on this network`** (`--lan` only)

- Both laptops have to be running beam at the same time.
- Check they are on the same local network. Guest, office and "public" Wi-Fi
  often block devices from seeing each other; a phone hotspot both laptops join
  usually works.
- On Windows, click **Allow** when the firewall asks about Python on the
  sending laptop.
- Use the address the sender prints: `beam receive ab3f9c --host 192.168.1.42:8765`.

## Limits

- The upload depends on free third-party hosts. They can change their rules, go
  offline or block your country without notice; beam tries four of them, but it
  cannot promise any is up. This is the honest weak point of the default mode.
- Files over 4 GB, or over 225 MB when temp.sh is down, have to go by `--lan`.
- `--lan` needs both laptops on at the same time, on the same local network.
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
