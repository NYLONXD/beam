# beam

Send a whole project to someone else's laptop, wherever they are, from the
terminal. You upload and walk away; they pick it up whenever they like.

Only your project travels. `node_modules`, virtualenvs, Rust's `target/` and
everything else the other laptop can download or build again stay behind, with
no ignore file to write. The zip carries a `start.bat` (and a `start.sh` for
macOS and Linux) that installs what the project needs and starts it.

**Laptop A** — here, now:

```bash
beam send D:/projects/my_app
#   left out : frontend/node_modules  1.2 GB
#              backend/.venv          310.4 MB
#   start    : backend   Python -> app.py
#              frontend  Node -> npm run dev
#   uploading to x0.at ...
#   code : xEGsg-gbDICMBKMQ69BmvI
```

Close the laptop. Send the code however you like — WhatsApp, email, a chat
message.

**Laptop B** — another city, tomorrow:

```bash
beam receive xEGsg-gbDICMBKMQ69BmvI
#   unzipped into .\my_app
#   Run start.bat now to set it up and start it? [Y/n]
```

Press Enter: the packages are installed and the project starts. That is the
whole thing. The other person never writes a line of Python and never has to be
online at the same time as you: they `pip install beam-lan` and type the code.

Python 3.9+. Works on Windows, macOS and Linux.

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
beam send D:/projects/my_app --with-deps          # keep node_modules & co.
beam send report.pdf                              # a single file, sent as it is
```

It zips the folder, leaving out what the other laptop rebuilds, encrypts it,
uploads it and prints a code. That is the end of your part — the file waits on
the host until the other person fetches it.

To send a file you already have, such as a zip or a PDF, pass its path instead
of a folder. It is sent as it is.

## Receive it

```bash
beam receive <code>                     # saves my_app.zip here and unzips it
beam receive <code> -o D:/Downloads     # somewhere else
beam receive <code> --run               # and start it, without asking
beam receive <code> --no-extract        # keep the zip, do not unzip
```

Once it is unzipped, `beam receive` asks whether to run `start.bat` (`start.sh`
on macOS and Linux) there and then. Say no, and you can run it any time later:
double-click `start.bat`, or `bash start.sh`. It does not ask when nobody is at
the keyboard (in a script, or with `-q`); `--run` runs it without asking and
`--no-run` never asks.

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

## Your own relay

Depending on strangers' servers is the weak point above. `relay/` is a small
Cloudflare Worker you can deploy to your own account in about five minutes; it
keeps the encrypted zip in R2 and its expiry in Upstash Redis.

```bash
export BEAM_RELAY=https://beam-relay.<your-subdomain>.workers.dev
beam send D:/projects/my_app        # goes to your relay, not to x0.at
```

Compared with the public hosts it is:

- **yours** — nobody else's uptime, rules or country blocks
- **short-lived on purpose** — a TTL you set, 5 hours by default, to the second
- **burn-after-read** — deleted the moment the receiver confirms the zip
  decrypted intact, rather than sitting there until it expires

beam tries your relay first and falls through to the public hosts when it is
unreachable, so a relay that is down slows things rather than breaking them.
Codes from a relay start with `w`, and the receiver needs `BEAM_RELAY` set too
unless you bake the URL into your own build.

Setup, settings and the rate limits are in [`relay/README.md`](relay/README.md).

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

- **Your files**, minus what the other laptop downloads or builds again. There
  is no ignore file to write; beam knows these by itself:
  - **by name, wherever they are:** `node_modules`, `.venv`, `venv`, `.gradle`,
    `.next`, `.nuxt`, `.svelte-kit`, `.angular`, `.turbo`, `.dart_tool`, `.vs`,
    `.stack-work`, `.terraform`, `__pycache__`, `.git` and the usual caches;
  - **by what is inside, whatever they are called:** a virtualenv
    (`pyvenv.cfg`), a conda environment, a CMake build folder, a tagged cache
    folder;
  - **only next to the file that proves what they are**, because the names
    are common: `target/` beside `Cargo.toml` or `pom.xml`, `build/` beside a
    Gradle file or `pubspec.yaml`, `bin/` and `obj/` beside a `.csproj`,
    `vendor/` beside `composer.json`, `vendor/bundle/` beside a `Gemfile`,
    `deps/` and `_build/` beside `mix.exs`, `Pods/` beside a `Podfile`, and
    Unity's `Library/`, Unreal's `Intermediate/` and Godot's `.godot/`. A
    `target/` folder with no `Cargo.toml` next to it is yours, and it goes.

  `beam send` lists the biggest folders it left out and how much they
  weighed. Lock files (`package-lock.json`, `Cargo.lock`, `poetry.lock`, ...)
  always go, so the other laptop installs the same versions.
- **`.env` files.** The project usually needs them to run, and the zip is
  encrypted, so they travel; `beam send` names them so you know. Leave them
  out with `-x .env`. A zip from `beam pack` is **not** encrypted.
- **What you excluded.** `".mp4"`, `"mp4"` and `"*.mp4"` all skip mp4 files.
  A plain name such as `"data"` skips a file or folder with that name. A
  `.beamignore` file in the project adds more patterns, one per line.
- **`start.bat` and `start.sh`**, which set the project up and start it.
- **`requirements.txt`** for a Python project that has none: taken from
  `pyproject.toml` or the `Pipfile` if they list dependencies, or else worked
  out from the `import` lines (`import cv2` becomes `opencv-python`). Pass
  `requirements=["flask", "numpy"]` to `beam.send` to set the list yourself.

With `--with-deps`, dependency folders (`node_modules`, `vendor`, ...) are kept,
for a receiver with no internet. Virtualenvs and build output stay behind even
then, because they do not work once moved.

## What start.bat and start.sh do

1. **Check that the language is installed.** If it is not, `start.bat` asks
   "Install it now with winget? [Y,N]", installs it, and carries on in the
   same window. On macOS and Linux, `start.sh` says what to install and where
   to get it.
2. **Set it up**: install the packages, create the venv, do a first build.
3. **Start it.**

| Project | Set up | Start |
| --- | --- | --- |
| Python: `requirements.txt`, `pyproject.toml`, `Pipfile`, `main.py`, ... | `.venv` + pip; uv, poetry or pipenv when the project uses one and it is installed | the script; Django `migrate` + `runserver`; Flask `flask run`; FastAPI `uvicorn`; Streamlit; notebooks `jupyter notebook` |
| Node: `package.json` | npm, pnpm, yarn or bun, by lock file | the `dev` script, else `start`, else `node <main>`; Tauri `tauri dev` |
| Deno: `deno.json` | — | `deno task dev` / `start`, or `deno run main.ts` |
| Rust: `Cargo.toml` | `cargo build` | `cargo run` |
| Go: `go.mod` | `go mod download` | `go run .`, or the one `cmd/<name>` |
| Java / Kotlin: `pom.xml`, `build.gradle` | through `mvnw` / `gradlew` when there | Spring Boot, Quarkus, Gradle `run`, JavaFX; otherwise it builds |
| Java files, no build tool | — | `java Main.java` |
| .NET: `.csproj`, `.sln` | `dotnet` restores | `dotnet run` for the web or program project |
| PHP: `composer.json`, `index.php` | `composer install` | Laravel `artisan serve`, else `php -S localhost:8000` |
| Ruby: `Gemfile` | `bundle install` | Rails `db:prepare` + `server`, `rackup`, or `app.rb` |
| Elixir: `mix.exs` | `mix deps.get` | Phoenix `phx.server`, or `mix run --no-halt` |
| Flutter / Dart: `pubspec.yaml` | `pub get` | `flutter run` / `dart run` |
| Haskell, Swift | `stack` / `cabal` / `swift build` | `... run` |
| C / C++: `CMakeLists.txt` | configures and builds | — (says where the program is) |
| Unity, Unreal, Godot, Android | — | says which editor to open it in |
| Website: `index.html` | — | opens it in the browser |

**A back end and a front end in one project** (`backend/` and `frontend/`,
`apps/web/`, ...) are both found — beam looks two folders deep — and both set
up and started, back ends first. `start.bat` gives each extra program a window
of its own; `start.sh` runs them side by side and stops them all together.
Workspaces (npm, pnpm, Cargo, Maven, Gradle, a `.sln`) count as one project.
`--main` picks exactly one thing to start.

Missing languages it can install with winget: Python, Node.js, pnpm, Yarn, Bun,
Deno, Rust, Go, Java (Temurin JDK), .NET, PHP, Ruby, Erlang and Elixir, Dart,
Swift, CMake, and Microsoft's C++ Build Tools. Maven, Gradle, Composer, Flutter
and Haskell have no winget package, so it gives the download link instead.

The setup runs once; after that, the scripts go straight to starting the
project. To set a Python part up from scratch, delete its `.venv` folder.

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
| `--main SCRIPT` | the one script the start scripts run (default: auto-detected) |
| `--no-start-script` | do not add `start.bat`, `start.sh` or `requirements.txt` |
| `--max-size-mb MB` | leave out files bigger than this |
| `--with-deps` | keep dependency folders (`node_modules`, `vendor`, ...) |
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
| `--run` | run `start.bat` / `start.sh` once unzipped, without asking |
| `--no-run` | do not offer to run it |
| `--overwrite` | replace what is there instead of adding ` (1)` |
| `-q`, `--quiet` | print nothing (and do not ask) |

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
beam.receive(code, extract=True, run=True)                 # other laptop: start it

beam.send("D:/projects/my_app", lan=True)                  # local network
beam.pack("D:/projects/my_app")                            # only make the zip
```

`beam.send(path, ...)` and `beam.pack(path, ...)`

| Option | Default | Effect |
| --- | --- | --- |
| `exclude` | none | file types or names to leave out |
| `main` | auto | the one script the start scripts run |
| `start_script` | `True` | add `start.bat` and `start.sh` (and `requirements.txt` if needed) |
| `requirements` | auto | pip packages the start scripts install |
| `use_default_excludes` | `True` | leave out `.git`, `node_modules`, venvs, build output, ... |
| `with_deps` | `False` | keep dependency folders (`node_modules`, `vendor`, ...) |
| `max_size_mb` | none | skip files bigger than this |
| `quiet` | `False` | print nothing |

`beam.pack` only: `output` sets where the zip goes (default `<folder>.zip`
next to the folder).

`beam.send` only: `lan=True` sends over the local network instead of uploading.
With `lan=True`, `code` picks your own code, `timeout` sets how many seconds to
wait for the other laptop, and `port` sets the network port.

`beam.receive(code, ...)`: `out` sets the folder to save into, `extract` also
unzips it, `run=True` then runs `start.bat` / `start.sh` there, and `overwrite`
replaces existing files. For `lan` codes, `host="IP"` or `host="IP:port"` skips
the search and `wait` sets how many seconds to search (default 15).

Errors raise `beam.BeamError`. The command prints them and exits with `1`.

## Troubleshooting

**`beam: command not found`**: Python's scripts folder is not on your `PATH`.
Use `python -m beam ...` instead, or add the folder pip named when it installed
the package.

**`this code came from a beam relay, but no relay is set here`**: the sender
used their own relay. Set `BEAM_RELAY` to the same URL they used.

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

**`It is installed, but this window cannot see it yet`** (`start.bat`): winget
installed the language, but Windows only tells new windows about it. Close the
window and run `start.bat` again.

**`start.bat` stops with an error about `cl.exe`, `link.exe`, MSBuild or
node-gyp**: a package needs a C/C++ compiler to build. Install Microsoft's
C++ Build Tools (`winget install Microsoft.VisualStudio.BuildTools`, choose
"Desktop development with C++") and run `start.bat` again. Rust, Swift and
CMake projects check for them up front and offer to install them.

**`If it says venv or ensurepip is not available`** (`start.sh` on
Ubuntu/Debian): run `sudo apt install python3-venv`, then `bash start.sh`.

## Limits

- Without a relay of your own, the upload depends on free third-party hosts.
  They can change their rules, go offline or block your country without notice;
  beam tries four of them, but it cannot promise any is up. Deploying
  [`relay/`](relay/README.md) is the fix.
- Files over 4 GB, or over 225 MB when temp.sh is down, have to go by `--lan`.
- `--lan` needs both laptops on at the same time, on the same local network.
- The other laptop needs internet the first time the start script runs, to
  download what was left out. For one without, send with `--with-deps`.
- Packages that cannot be downloaded again do not come back by themselves:
  private registries, git dependencies that need a login, and local paths
  outside the project (`"file:../shared"`, `path = "../lib"`).
- `start.sh` does not install missing languages; it says what to install.
- beam works out what to start from the files it sees. For an unusual layout,
  check the `start :` lines `beam send` prints, and use `--main` or `-x`.
- PHP on Windows: the winget PHP has openssl, mbstring and friends switched
  off; `start.bat` switches them on for its own window. This is the least
  tested path.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
python -m build

cd relay && npm test        # the Worker; needs node, no account or network
```

## License

MIT
