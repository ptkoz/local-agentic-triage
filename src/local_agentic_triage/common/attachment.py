"""Domain-neutral file content delivered alongside a notification."""
from dataclasses import dataclass

__all__ = ('Attachment',)


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: str
