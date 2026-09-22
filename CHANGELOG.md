# Changelog

## Unreleased

- A relay of your own, in `relay/`: a Cloudflare Worker that keeps the
  encrypted zip in R2 and its expiry in Upstash Redis, so beam stops depending
  on whichever free file host is up. Set `BEAM_RELAY` (or bake the URL into
  `BeamRelay.DEFAULT_URL`) and it is tried before the public hosts, falling
  back to them when it is unreachable. Codes from a relay start with `w`.
- Relay transfers are burned as soon as the receiver confirms the zip decrypted
  intact, rather than waiting for the TTL. The confirmation fires only after
  the AES-GCM tag verifies, so a half-finished download can never destroy the
  only copy.
- A `beam` command, so neither side has to write Python. `beam send <folder>`
  uploads a project and prints a code; `beam receive <code>` on the other
  laptop downloads it and unzips it, from any network, hours or days later.
  `beam pack` makes the zip only. `python -m beam` does the same if the
  scripts folder is not on `PATH`.
- `beam send` uploads by default, so distance does not matter and the sending
  laptop can be closed as soon as the code appears. `--lan` hands the file
  straight to a laptop on the same network instead, with nothing uploaded and
  no size limit, when both are on at the same time.
- `beam receive` works out from the code alone whether to download it or look
  for the sender on the local network. It unzips by default; `--no-extract`
  keeps just the zip.
- Two more upload hosts, x0.at and catbox.moe, both verified working where
  temp.sh currently is not. Small zips now land on x0.at and are kept at least
  30 days, instead of falling through to uguu.se's 3 hours.
- Uploads over 8 MB poke each host with a few bytes before committing, so a
  host that is down costs a round trip instead of the whole upload.
- The sender now prints the command the other person should type, rather than
  a line of Python, when it was started from the command line.
- Clearer message when a `--lan` sender cannot be found: any local network
  works (Wi-Fi, a cable, a phone hotspot), not only Wi-Fi.


## 0.1.0

First release.

- `beam.send(folder)` zips a project, encrypts it (AES-256-GCM) and uploads it
  to a free temporary file host. It returns a code that works from anywhere
  for 3 days with `beam.receive(code)`. The key is part of the code and never
  reaches the host.
- `beam.send(folder, lan=True)` sends directly to a laptop on the same network
  instead. The receiver finds the sender by the code alone.
- `beam.pack(folder)` only makes the zip.
- `exclude=[".mp4", ".log"]` leaves file types out. `.venv`, `.git`,
  `__pycache__`, `node_modules`, `.env` and similar are skipped by default.
- The zip includes a `start.bat` that creates a virtual environment, installs
  requirements (read from the project's imports when it has no
  `requirements.txt`) and runs the project. It handles plain Python,
  Streamlit, Django, Node and static-HTML projects.
