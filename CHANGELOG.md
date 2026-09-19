# Changelog

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
