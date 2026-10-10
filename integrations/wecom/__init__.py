"""Local WeCom data adapter with configuration-safe lazy loading."""

from .config import configure

__all__ = ["WeComAdapter", "configure"]


def __getattr__(name: str):
    if name == "WeComAdapter":
        from .adapter import WeComAdapter

        return WeComAdapter
    raise AttributeError(name)

