"""beam - zip a project and send it to someone else's laptop, wherever it is.

From a terminal, with nothing to write:

    beam send D:/projects/my_app     # laptop A: uploads, prints a code, exits
    beam receive xEGsg-gbDICMB...    # laptop B: anywhere, later, unzips it

The zip is encrypted before it is uploaded and the key lives in the code, so
the host only ever holds unreadable bytes. Laptop A can be closed the moment
the code appears; laptop B has days to pick it up.

    beam send D:/projects/my_app --lan   # same network, both on: no upload

The same from Python:

    import beam

    code = beam.send("D:/projects/my_app")   # uploads
    beam.receive(code, extract=True)         # on the other laptop

    beam.send("D:/projects/my_app", lan=True)   # local network instead
    beam.pack("D:/projects/my_app")             # only make my_app.zip

The zip carries a start.bat that installs what the project needs and runs it.
"""

__version__ = "0.1.0"  # before the imports: _cloud reads it for its User-Agent

from ._files import DEFAULT_EXCLUDES  # noqa: E402
from ._protocol import DEFAULT_PORT, BeamError, find_sender, lan_ip  # noqa: E402
from .packer import pack  # noqa: E402
from .transfer import receive, send  # noqa: E402

__all__ = [
    "send",
    "receive",
    "pack",
    "find_sender",
    "lan_ip",
    "BeamError",
    "DEFAULT_EXCLUDES",
    "DEFAULT_PORT",
    "__version__",
]
