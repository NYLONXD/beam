"""beam - zip a project and send it to another laptop, anywhere.

    import beam

    code = beam.send("D:/projects/my_app", exclude=[".mp4", ".log"])  # laptop A
    beam.receive(code)                                               # laptop B

    beam.send("D:/projects/my_app", lan=True)   # direct, same Wi-Fi only
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
