# Changelog

## 0.2.0

- `beam recv CODE` finds the sender on the local network by itself; no IP
  address is needed. Use `--host IP[:PORT]` to connect directly.
- New `--wait SEC` option for how long the receiver searches.
- New `beam.find_sender(code)` library function. `receive_project` takes
  `host=None` to discover the sender.
- Fix: sending crashed on Python 3.9–3.11 (the receiver saw an empty stream).
- Breaking: `beam recv HOST --code CODE` is now `beam recv CODE --host HOST`.

## 0.1.0

- First release: `beam send` / `beam recv` with excludes, `.beamignore`,
  SHA-256 verification and streamed gzip transfer.
