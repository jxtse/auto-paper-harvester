"""
High-level helpers for downloading publisher PDFs from Web of Science exports.
"""

from .clients import CrossrefClient, ElsevierClient, SpringerClient, WileyClient  # noqa: F401
from .downloader import download_from_savedrecs  # noqa: F401

__all__ = [
    "CrossrefClient",
    "ElsevierClient",
    "SpringerClient",
    "WileyClient",
    "download_from_savedrecs",
]
__version__ = "0.1.0"
