"""eivlhole: wormhole-style file transfer over SSH."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("eivlhole")
except PackageNotFoundError:  # pragma: no cover - only when run from a bare checkout
    __version__ = "0.0.0"
