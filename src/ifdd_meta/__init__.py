"""ifdd-meta: build, run and test a complete IFDD installation."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ifdd-meta")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0+unknown"
