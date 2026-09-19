"""beam - move a project folder between two laptops over the local network.

    import beam

    beam.send_project("~/thesis", code="ab3f9c")        # laptop A
    beam.receive_project(code="ab3f9c")                  # laptop B
"""

from ._files import DEFAULT_EXCLUDES
from ._protocol import DEFAULT_PORT, BeamError, find_sender, lan_ip
from .receiver import receive_project
from .sender import send_project

__version__ = "0.2.0"

__all__ = [
    "send_project",
    "receive_project",
    "find_sender",
    "lan_ip",
    "BeamError",
    "DEFAULT_EXCLUDES",
    "DEFAULT_PORT",
    "__version__",
]
