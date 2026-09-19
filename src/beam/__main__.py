"""Allow `python -m beam`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
