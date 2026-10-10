"""Local Chat Hub — unified SQLite index and export helpers.

Public surface::

    from hub import ChatStore, render_export

See ``TASK.md`` for the contract this package implements.
"""

from __future__ import annotations

from .exports import FORMATS, render_export
from .store import (
    MEDIA_TYPES,
    MESSAGE_TYPES,
    PLATFORMS,
    ChatStore,
)

__all__ = [
    "ChatStore",
    "render_export",
    "PLATFORMS",
    "MESSAGE_TYPES",
    "MEDIA_TYPES",
    "FORMATS",
]

__version__ = "1.0.0"
